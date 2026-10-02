"""Offline exact candidate-pool optimizer. No accounts or game-service calls.

Power/reward arithmetic follows the archived bdon-linked reference. AP is a
chart-time reference, not a full native frame replay. Its objective uses the
lowest grade among all 120 skill orders, NOT floor(expected CP). Skip uses the
reference's enumeration-order end frame and all source-valid note types.
"""
from __future__ import annotations

from collections import Counter, OrderedDict
from copy import deepcopy
from functools import lru_cache
import hashlib
import itertools
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent
RESEARCH = ROOT / "research"
SNAPSHOT = RESEARCH / "2026-10-01"
sys.path.insert(0, str(RESEARCH))
import card_power as cp
import deck_power as dp
import event_bonus as eb
import manual_skill_contract as sk
import manual_score_reference as ms
import reward_preview as rw
from search_cache import digest
import score_intervals
from performance_trace import Trace

VERSION = "0.2.5"
ORDERS = tuple(itertools.permutations(range(5)))
RANKS = ("D", "C", "B", "A", "S", "SS")
# Memory budget for matching states, never a limit on the candidate pool.
# If reached, switch to a streaming exhaustive search of the same full pool.
MAX_MATCH_STATES = 50000
MAX_SCORE_CACHE = 2048


class InputError(ValueError):
    pass


class Cancelled(Exception):
    pass


def integer(value, label, low=0, high=2**31 - 1):
    if type(value) is not int or not low <= value <= high:
        raise InputError(f"{label}请填写 {low}～{high} 的整数。")
    return value


def read_json(path):
    return json.loads(Path(path).read_bytes())


def demo_profile():
    receipt = RESEARCH / "player_growth_observations.json"
    profile = (read_json(receipt)["player_profile_partial"] if receipt.exists() else
               {"schema_version": 1, "inventory": {"members": [], "snaps": []}, "facilities": [],
                "character_ranks": [], "character_total_rank": None, "tgw_card_rank": None})
    has_sample = receipt.exists()
    return {"name": "截图校准示例" if has_sample else "我的卡库", "profile": profile,
            "candidate_member_ids": [59, 11, 55, 26, 3] if has_sample else [],
            "candidate_snap_ids": [33, 37, 52, 61, 3] if has_sample else [],
            "settings": {"boost_budget": 20, "boost_per_live": 4, "starting_cp": 0,
                         "challenge_cp": 200,
                         "normal": {"song_id": 100109, "difficulty": "expert", "method": "ap",
                                    "sheets": [{"song_id": i, "difficulty": "expert"} for i in (100056, 100063, 100109)]},
                         "challenge": {"song_id": 100109, "difficulty": "expert", "method": "ap",
                                       "sheets": [{"song_id": i, "difficulty": "expert"} for i in (100056, 100063, 100109)]}},
            "schema_version": 1, "is_demo": has_sample}


class Data:
    """Verify source bytes once per calculation; use immutable rows afterwards."""
    def __init__(self, snapshot=SNAPSHOT):
        self.snapshot = Path(snapshot)
        self.manifest, self.tables = dp._snapshot(self.snapshot)
        self.skill_inputs = sk._load(self.snapshot.resolve())
        # The skill loader verifies formation groups. Additional cumulative and
        # static skill-category metadata for leader targets are verified here.
        self.tables.update(self.skill_inputs[0])
        import hashlib
        entries = {r["local_path"]: r for r in self.manifest["files"]}
        for name in ("MasterSkillCumulativeCondition", "MasterLiveSkill", "MasterGekisouSkill"):
            path = f"raw/{name}.json"
            entry = entries.get(path)
            if entry is None:
                raise InputError("队长技能来源记录缺失，请恢复随附数据。")
            try:
                raw = (self.snapshot / path).read_bytes()
            except OSError:
                raise InputError("队长技能数据文件缺失或无法读取，请恢复随附数据。") from None
            if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                raise InputError("队长技能数据校验失败，请恢复随附数据。")
            self.tables[name] = json.loads(raw)["_allData"]
        self.index = {name: {r["_id"]: r for r in rows} for name, rows in self.tables.items()}
        self.text = {r["_id"]: r["_japanese"] for r in self.tables["MasterText"]}
        for name in ("MasterMemoryMemberLevel", "MasterMemorySupportLevel", "MasterMemoryMusicBonus"):
            if self.tables[name]:
                raise InputError("此版本尚未覆盖回忆加成，不能按零加成计算。")
        # Build event reward data from the verified raw tables, rather than trust
        # a mutable normalized copy. This also validates its exact equivalence.
        self.event = read_json(self.snapshot / "normalized/event_1.json")
        self._check_rewards()
        self.reward_context = rw.RewardContext(self.snapshot.resolve(), ms._freeze(self.event), self.manifest["data_commit"])
        self._score_inputs = None
        report_path = self.snapshot / "validation/public_chart_conversion.json"
        self.conversion_report = read_json(report_path if report_path.exists() else self.snapshot / "validation/manual_chart_conversion.json")
        self._fingerprint = None

    @property
    def score_inputs(self):
        if self._score_inputs is None:
            self._score_inputs = ms.verify_score_inputs(self.snapshot)
        return self._score_inputs

    def fingerprint(self):
        """Key actual snapshot/model bytes, independent of temporary EXE paths."""
        if self._fingerprint is None:
            h = hashlib.sha256(b"planner-sheet-cache-v1")
            for path in sorted(p for p in self.snapshot.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
                h.update(path.relative_to(self.snapshot).as_posix().encode("utf-8"))
                h.update(hashlib.sha256(path.read_bytes()).digest())
            sources = ([Path(sys.executable)] if getattr(sys, "frozen", False) else
                       [Path(__file__), ROOT / "search_cache.py", ROOT / "solver_search.py", ROOT / "score_bounds.py"] +
                       [Path(m.__file__) for m in (cp, dp, eb, sk, ms, rw, score_intervals)] +
                       [ROOT / "performance_trace.py"])
            for path in sources:
                h.update(hashlib.sha256(path.read_bytes()).digest())
            self._fingerprint = h.hexdigest()
        return self._fingerprint

    def _check_rewards(self):
        import hashlib
        entries = {r["local_path"]: r for r in self.manifest["files"]}
        names = ("MasterLiveEventPoint", "MasterLiveEventReward", "MasterLiveChallengePoint",
                 "MasterLiveMusicBoostBonus", "MasterChallengeMusicBoostBonus",
                 "MasterChallengeLiveEventPoint", "MasterChallengeLiveEventReward")
        for name in names:
            p = f"raw/{name}.json"
            raw = (self.snapshot / p).read_bytes()
            if hashlib.sha256(raw).hexdigest() != entries[p]["sha256"]:
                raise InputError(f"收益数据校验失败：{name}")
            self.tables[name] = json.loads(raw)["_allData"]
        # The existing reward preview consumes the normalized file; re-build and
        # check its selected bases against raw rows before reusing that API.
        for challenge in (False, True):
            prefix = "MasterChallengeLive" if challenge else "MasterLive"
            event = self.index["MasterEvent"][1]
            point_group = event["_challengeLiveEventPointGroup" if challenge else "_liveEventPointGroup"]
            reward_group = event["_challengeLiveEventRewardGroup" if challenge else "_liveEventRewardGroup"]
            for base in self.event["challenge_base_rewards" if challenge else "ordinary_base_rewards"]:
                grade = RANKS.index(base["rank"]) + 2
                points = [r for r in self.tables[prefix + "EventPoint"] if r["_group"] == point_group and r["_scoreRank"] == grade]
                if len(points) != 1 or points[0]["_value"] != base["event_pt_base"]:
                    raise InputError("活动 PT 基础数据与原始表不一致。")
                raw_rewards = [r for r in self.tables[prefix + "EventReward"] if r["_eventGroup"] == reward_group and r["_scoreRank"] == grade]
                if raw_rewards != base["shop_reward_rows"]:
                    raise InputError("商店 PT 基础数据与原始表不一致。")
                if not challenge:
                    rows = [r for r in self.tables["MasterLiveChallengePoint"] if r["_scoreRank"] == grade]
                    if len(rows) != 1 or rows[0]["_value"] != base["cp_base"]:
                        raise InputError("普通演出 CP 基础数据与原始表不一致。")
        for key, table in (("ordinary_boost_rows_raw", "MasterLiveMusicBoostBonus"),
                           ("challenge_cp_consumption_rows_raw", "MasterChallengeMusicBoostBonus")):
            # Normalized rows are filtered to this event's selected groups.
            ids = {r["_id"] for r in self.event[key]}
            if [r for r in self.tables[table] if r["_id"] in ids] != self.event[key]:
                raise InputError("资源消耗倍率与原始表不一致。")

    def catalog(self):
        members, snaps = [], []
        for r in self.tables["MasterMemberCard"]:
            char = self.index["MasterCharacter"][r["_characterID"]]
            caps = [x["_limitLevel"] for rank in range(1, 6) for x in self.tables["MasterMemberCardLevelLimit"]
                    if x["_rarity"] == r["_rarity"] and x["_awakeCount"] == rank]
            members.append({"id": r["_id"], "name": self.text[r["_nameTextID"]],
                            "title": self.text[r["_subtitleTextID"]], "character_id": char["_id"],
                            "band_id": char["_bandID"], "rarity": r["_rarity"], "type": r["_cardType"],
                            "caps": caps, "character_ids": [char["_id"]], "band_ids": [char["_bandID"]],
                            "thumbnail": f"/card-images/members-{r['_id']}.webp"})
        for r in self.tables["MasterSupportCard"]:
            caps = [x["_limitLevel"] for rank in range(1, 6) for x in self.tables["MasterSupportCardRank"]
                    if x["_group"] == r["_supportCardRankGroup"] and x["_rank"] == rank]
            snaps.append({"id": r["_id"], "name": self.text[r["_nameTextID"]],
                          "title": self.text[r["_descriptionTextID"]], "rarity": r["_rarity"],
                          "type": r["_cardType"], "caps": caps,
                          "character_ids": r["_characterIDs"],
                          "band_ids": sorted({self.index["MasterCharacter"][cid]["_bandID"] for cid in r["_characterIDs"]}),
                          "thumbnail": f"/card-images/snaps-{r['_id']}.webp"})
        converted = self.conversion_report["charts"]
        song_ids = sorted({c["music_id"] for c in converted})
        songs = []
        for sid in song_ids:
            r = self.index["MasterLiveMusic"][sid]
            sheets = []
            for diff in ("easy", "normal", "hard", "expert"):
                sheet = self.index.get("MasterLiveMusicScore", {}).get(r[f"_{diff}ID"])
                # Loaded by the score layer, not needed by the power layer.
                if sheet is None:
                    table = read_json(self.snapshot / "raw/MasterLiveMusicScore.json")["_allData"]
                    sheet = next(x for x in table if x["_id"] == r[f"_{diff}ID"])
                sheets.append({"difficulty": diff, "level": sheet["_musicScoreLevel"], "notes": sheet["_fullComboCount"]})
            songs.append({"id": sid, "title": self.text[r["_titleTextID"]], "sheets": sheets,
                          "challenge": any(c["_eventId"] == 1 and c["_liveMusicId"] == sid for c in self.tables["MasterChallengeMusic"])})
        characters = [{"id": r["_id"], "name": self.text[r["_nameTextID"]], "band_id": r["_bandID"]}
                      for r in self.tables["MasterCharacter"]]
        bands = [{"id": r["_id"], "name": self.text[r["_nameTextID"]]} for r in self.tables["MasterBand"]]
        facilities = [{"id": r["_id"], "band_id": r["_bandId"], "name": self.text[r["_nameTextId"]],
                       "max_level": max(x["_level"] for x in self.tables["MasterBandItemLevel"] if x["_bandItemId"] == r["_id"])}
                      for r in self.tables["MasterBandItem"]]
        return {"version": VERSION, "members": members, "snaps": snaps, "songs": songs,
                "characters": characters, "bands": bands, "facilities": facilities,
                "source": {"provider": "bdon / Moenotes", "snapshot": "2026-10-01 JP 1.0.0.300",
                           "data_commit": self.manifest["data_commit"],
                           "model_commit": self.manifest["bdon_linked_model_commit"]},
                "limits": {"ap_bindings": None, "skip_sets": None,
                           "scope": "per_sheet", "batch_size_sheets": 1}}


def leader_rates(data, leader, own, cards, characters, music_type):
    """bonus.rs formation rules; only the current positive source conditions.

    Leader check_condition flattens its sets with AND, unlike live skill groups.
    Zero-type effects are explicit no-ops. Unknown rules fail, never grant bonus.
    """
    tables = data.tables
    effects = [r for r in tables["MasterLeaderSkillEffect"]
               if r["_leaderSkillID"] == leader["_leaderSkillID"] and r["_level"] == own["leader_skill_level"]]
    if not effects:
        raise InputError("队长没有对应的养成技能数据。")
    out = [dp.ZERO] * 5
    def targets(ids):
        return [data.index["MasterSkillTarget"][i] for i in ids]
    def matched(card, char, ts):
        def target_matches(target):
            # bonus.rs::is_target_member checks selectors with OR. Skill
            # categories / Gekisou mission type are static skill metadata;
            # they do not require a live replay or an assumed skill level.
            if target["_judgement"] != -1 or target["_liveMusicType"] != 0:
                raise InputError(f"尚未覆盖的队长成员目标 {target['_id']}。")
            if any((
                    target["_bandID"] > 0 and target["_bandID"] == char["_bandID"],
                    target["_characterID"] > 0 and target["_characterID"] == card["_characterID"],
                    target["_cardType"] != 0 and target["_cardType"] == card["_cardType"],
                    target["_tagID"] > 0 and target["_tagID"] in card["_bestMusicTagIDs"],
            )):
                return True
            if target["_liveSkillCategories"]:
                live = data.index["MasterLiveSkill"].get(card["_liveSkillID"])
                if live is None:
                    raise InputError("队长目标需要的演出技能类别数据缺失。")
                if set(target["_liveSkillCategories"]) & set(live["_skillCategories"]):
                    return True
            if target["_gekisouSkillCategories"] or target["_gekisouMissionType"]:
                gekisou = data.index["MasterGekisouSkill"].get(card["_gekisouSkillID"])
                if gekisou is None:
                    raise InputError("队长目标需要的击奏技能类别数据缺失。")
                if set(target["_gekisouSkillCategories"]) & set(gekisou["_skillCategories"]):
                    return True
                if (target["_gekisouMissionType"] != 0
                        and target["_gekisouMissionType"] == gekisou["_gekisouMissionType"]):
                    return True
            return False
        return any(target_matches(target) for target in ts)
    for e in effects:
        if e["_skillEffectType"] == 0:
            continue
        if e["_effectExecuteLimitCount"] or e["_effectExecuteLimitResetConditionGroup"]:
            raise InputError("此队长有未覆盖的执行次数限制。")
        group = e["_skillConditionGroup"]
        conditions = [data.index["MasterSkillCondition"][cid] for cs in tables["MasterSkillConditionSet"]
                      if cs["_group"] == group for cid in cs["_conditionIds"]] if group else []
        if group and not conditions:
            raise InputError("队长条件数据缺失。")
        answers = []
        for c in conditions:
            if not c["_isPositive"]:
                raise InputError("尚未覆盖此队长的反向编成条件。")
            ts = targets(c["_conditionTargetIDs"])
            kind = c["_conditionType"]
            if not ts or kind == 0:
                answer = True
            elif kind == 3000:
                answer = any(matched(a, b, ts) for a, b in zip(cards, characters))
            elif kind == 3001:
                answer = all(matched(a, b, ts) for a, b in zip(cards, characters))
            elif kind == 4012:
                answer = any(t["_liveMusicType"] != 0 and t["_liveMusicType"] == music_type for t in ts)
            else:
                raise InputError(f"未覆盖的队长条件类型 {kind}。")
            answers.append(answer)
        if not all(answers):
            continue
        kind, value = e["_skillEffectType"], e["_effectValue"]
        if 1500 <= kind <= 1503:
            cum = data.index["MasterSkillCumulativeCondition"].get(e["_skillCumulativeConditionID"])
            if cum is None:
                raise InputError("队长累积条件数据缺失。")
            ctype = cum["_skillCumulativeConditionType"]
            ts = targets(cum["_conditionTargetIDs"])
            li = cards.index(leader)
            band = characters[li]["_bandID"]
            if ctype == 3000:
                count = sum(matched(a, b, ts) for a, b in zip(cards, characters))
            elif ctype == 3001:
                count = sum(matched(a, b, ts) for i, (a, b) in enumerate(zip(cards, characters)) if i != li)
            elif ctype == 3002:
                count = sum(c["_bandID"] == band for c in characters)
            elif ctype == 3003:
                count = sum(c["_bandID"] != band for c in characters)
            elif ctype == 3004:
                count = len({c["_bandID"] for c in characters})
            elif ctype == 3005:
                count = len({c["_cardType"] for c in cards})
            else:
                raise InputError(f"未覆盖的队长累积类型 {ctype}。")
            cap = cum["_maxCumulativeCount"]
            value = dp._wrap32(value * (min(cap, count) if cap >= 1 else count))
            kind -= 500
        rate = dp._effect_rates({**e, "_skillEffectType": kind, "_effectValue": value})
        ts = targets(e["_skillTargetIDs"])
        for i, (card, char) in enumerate(zip(cards, characters)):
            if not ts or matched(card, char, ts):
                out[i] = dp._add(out[i], rate)
    return out


def growth_issues(request, data):
    """Collect every missing/invalid required value without assuming max growth."""
    profile = request.get("profile")
    if not isinstance(profile, dict):
        raise InputError("请先录入或导入个人卡库。")
    issues, cards, chars = [], [], []
    require_live = any(request.get("settings", {}).get(mode, {}).get("method") == "ap"
                       for mode in ("normal", "challenge"))

    def check(value, label, low, high, target, **extra):
        if type(value) is not int or not low <= value <= high:
            issues.append({"label": label, "reason": "未填写" if value is None else f"请填写 {low}～{high} 的整数",
                           "target": target, **extra})
            return False
        return True

    for kind, table, title in (("members", "MasterMemberCard", "_subtitleTextID"),
                               ("snaps", "MasterSupportCard", "_descriptionTextID")):
        owned = cp._inventory(profile, kind)
        ids = request.get("candidate_member_ids" if kind == "members" else "candidate_snap_ids", [])
        cp._selection(ids, owned, kind)
        for identifier in ids:
            if identifier not in data.index[table]:
                raise InputError(f"候选卡 #{identifier} 不在当前数据快照中。")
            card, row = data.index[table][identifier], owned[identifier]
            name = data.text[card[title]] + " · " + data.text[card["_nameTextID"]]
            fields = [("level", "等级", 1, 90)]
            if kind == "members":
                cards.append(card)
                chars.append(data.index["MasterCharacter"][card["_characterID"]])
                fields += [("training_count", "特训阶段", 0, 4), ("awakening_count", "觉醒次数", 0, 4)]
                if require_live:
                    fields.append(("live_skill_level", "Live 技能等级", 1, 5))
            else:
                fields.append(("limit_break_count", "突破次数", 0, 4))
            for field, label, low, high in fields:
                check(row.get(field), f"{name} · {label}", low, high, "card", kind=kind, id=identifier, field=field)
            stage = row.get("training_count" if kind == "members" else "limit_break_count")
            level = row.get("level")
            if type(stage) is int and 0 <= stage <= 4 and type(level) is int and 1 <= level <= 90:
                cap = (dp._unique(data.tables["MasterMemberCardLevelLimit"], "member cap", _rarity=card["_rarity"], _awakeCount=stage+1)["_limitLevel"]
                       if kind == "members" else dp._unique(data.tables["MasterSupportCardRank"], "snap cap", _group=card["_supportCardRankGroup"], _rank=stage+1)["_limitLevel"])
                if level > cap:
                    issues.append({"label": f"{name} · 等级", "reason": f"{level} 超过实际阶段上限 {cap}",
                                   "target": "card", "kind": kind, "id": identifier, "field": "level"})
    ranks = dp._indexed_player_rows(profile, "character_ranks", "character_id")
    for cid in sorted({c["_id"] for c in chars}):
        check(ranks.get(cid, {}).get("rank"), f"{data.text[data.index['MasterCharacter'][cid]['_nameTextID']]} · 角色等级",
              1, 1000, "rank", id=cid)
    total = profile.get("character_total_rank")
    if check(total, "总角色等级", 1, 25000, "global", field="character_total_rank"):
        entered_sum = sum(r["rank"] for r in ranks.values() if type(r.get("rank")) is int and 1 <= r["rank"] <= 1000)
        if total < entered_sum:
            issues.append({"label": "总角色等级", "reason": f"小于已填写角色等级之和 {entered_sum}",
                           "target": "global", "field": "character_total_rank"})
    check(profile.get("tgw_card_rank"), "T.G.W CARD 等级", 1, 21, "global", field="tgw_card_rank")
    facilities = dp._indexed_player_rows(profile, "facilities", "id")
    required = set()
    for effect in data.tables["MasterBandItemSkillEffect"]:
        targets = [data.index["MasterSkillTarget"][i] for i in effect["_skillTargetIDs"]]
        if any(dp._facility_matches(t, card, char) for t in targets for card, char in zip(cards, chars)):
            required.add(effect["_bandItemId"])
    for identifier in sorted(required):
        facility = data.index["MasterBandItem"][identifier]
        cap = max(r["_level"] for r in data.tables["MasterBandItemLevel"] if r["_bandItemId"] == identifier)
        check(facilities.get(identifier, {}).get("level"), f"{data.text[facility['_nameTextId']]} · 道具等级",
              1, cap, "facility", id=identifier)
    return {"issues": issues, "complete": not issues, "live_skill_required": require_live,
            "required_character_ids": sorted({c["_id"] for c in chars}), "required_facility_ids": sorted(required)}


class PowerModel:
    def __init__(self, data, profile, mids, sids):
        self.data, self.profile = data, profile
        self.mids, self.sids = mids, sids
        tables = data.tables
        # UI blanks are unknown. Diagnose selected cards by name, while leaving
        # unrelated, unfinished inventory rows available for later editing.
        for kind, selected, table, title_key, fields in (
            ("members", mids, "MasterMemberCard", "_subtitleTextID", (("level", "等级", 1, 90), ("training_count", "特训阶段", 0, 4), ("awakening_count", "觉醒次数", 0, 4))),
            ("snaps", sids, "MasterSupportCard", "_descriptionTextID", (("level", "等级", 1, 90), ("limit_break_count", "突破次数", 0, 4))),
        ):
            owned = cp._inventory(profile, kind)
            for identifier in selected:
                card = data.index[table][identifier]
                name = data.text[card[title_key]] + " · " + data.text[card["_nameTextID"]]
                for field, label, low, high in fields:
                    if owned[identifier].get(field) is None:
                        raise InputError(f"请在「我的卡库」填写「{name}」的{label}。")
                    integer(owned[identifier].get(field), f"「{name}」的{label}", low, high)
        own = cp.calculate_selected_card_power(data.snapshot, profile, mids, sids)
        bonus = eb.calculate_deck_bonuses(data.snapshot, profile, mids, sids)
        self.own = {r["id"]: r for r in own["members"]}
        self.support = {r["id"]: r for r in own["snaps"]}
        self.mb = {r["id"]: r for r in bonus["members"]}
        self.sb = {r["id"]: r for r in bonus["snaps"]}
        self.cards = {i: data.index["MasterMemberCard"][i] for i in mids}
        self.snaps = {i: data.index["MasterSupportCard"][i] for i in sids}
        self.chars = {i: data.index["MasterCharacter"][self.cards[i]["_characterID"]] for i in mids}
        rank_entries = dp._indexed_player_rows(profile, "character_ranks", "character_id")
        total = integer(profile.get("character_total_rank"), "总角色等级", 1, 25000)
        if total < sum(integer(r["rank"], "角色等级", 1, 1000) for r in rank_entries.values() if r.get("rank") is not None):
            raise InputError("总角色等级小于已录入角色等级之和。")
        tr = dp._total_rank_row(tables, total)["_bonus"]
        vr = integer(profile.get("tgw_card_rank"), "T.G.W CARD 等级", 1, 21)
        self.vip = 0 if vr == 1 else dp._unique(tables["MasterVipRankBonus"], "VIP", _vipRank=vr, _vipBonusType=7)["_value"]
        facilities = dp._indexed_player_rows(profile, "facilities", "id")
        required = set()
        for effect in tables["MasterBandItemSkillEffect"]:
            targets = [data.index["MasterSkillTarget"][i] for i in effect["_skillTargetIDs"]]
            if any(dp._facility_matches(t, self.cards[m], self.chars[m]) for t in targets for m in mids):
                required.add(effect["_bandItemId"])
        for identifier in sorted(required):
            facility = data.index["MasterBandItem"][identifier]
            max_level = max(r["_level"] for r in tables["MasterBandItemLevel"] if r["_bandItemId"] == identifier)
            integer(facilities.get(identifier, {}).get("level"), f"道具「{data.text[facility['_nameTextId']]}」的等级", 1, max_level)
        calculation_profile = {**profile, "facilities": [r for r in facilities.values() if r.get("level") is not None]}
        rates, _ = dp._band_rates(tables, calculation_profile, list(self.cards.values()), list(self.chars.values()))
        self.band = dict(zip(mids, rates))
        self.flat, self.rank = {}, {}
        for mid in mids:
            cid = self.chars[mid]["_id"]
            if cid not in rank_entries:
                raise InputError(f"请填写角色 {data.text[self.chars[mid]['_nameTextID']]} 的实际等级。")
            rank = integer(rank_entries[cid].get("rank"), "角色等级", 1, 1000)
            r = dp._unique(tables["MasterCharacterRank"], "character rank", _rank=rank)
            self.flat[mid] = ((r["_bonus"] + tr) * 10000,) * 3
            self.rank[mid] = dp._unique(tables["MasterMemberCardRank"], "member rank",
                                      _group=self.cards[mid]["_memberCardRankGroup"], _rank=self.own[mid]["raw_rank"])
        self.link = dp._parameter(tables, "type_link_base_bonus_rate")
        self.type_base = dp._parameter(tables, "music_type_base_bonus_rate")
        self.tag_base = dp._parameter(tables, "music_tag_base_bonus_rate")
        self._power_contexts = {}

    def _power_context(self, challenge):
        # MasterText Help_SubCategory_Description_140002: parameter bonuses
        # apply only in challenge lives. Currency bonuses remain independent.
        if challenge in self._power_contexts:
            return self._power_contexts[challenge]
        base, b, pair = {}, {}, {}
        snap_ranks = {sid: dp._unique(self.data.tables["MasterSupportCardRank"], "support rank",
                         _group=self.snaps[sid]["_supportCardRankGroup"], _rank=self.support[sid]["raw_rank"])
                      for sid in self.sids}
        for mid in self.mids:
            own_bp = dp._bp(self.own[mid]["parameters"])
            member_rate = self.mb[mid]["parameter_bonus_10000"] if challenge else 0
            base[mid] = dp._add(own_bp, dp._mul_floor(own_bp, (member_rate,) * 3))
            b[mid] = dp._add(base[mid], self.flat[mid])
            for sid in self.sids:
                snap = self.snaps[sid]
                r = snap_ranks[sid]
                snap_rate = self.sb[sid]["parameter_bonus_10000"] if challenge else 0
                sr = tuple(self.support[sid]["support_rates_10000"][name] + snap_rate for name in dp.PARAMETERS)
                lr = self.link + r["_cardTypeLinkBonusRate"] if self.cards[mid]["_cardType"] == snap["_cardType"] else 0
                pair[mid, sid] = dp._add(dp._mul_floor(b[mid], sr), dp._mul_floor(b[mid], (lr,) * 3))
        self._power_contexts[challenge] = base, b, pair
        return base, b, pair

    def music(self, sid, challenge):
        song = self.data.index["MasterLiveMusic"][sid]
        typ = song["_musicType"]
        if challenge:
            row = dp._unique(self.data.tables["MasterChallengeMusic"], "challenge music", _eventId=1, _liveMusicId=sid)
            typ = row["_musicType"] or typ
        self.base, self.b, self.pair = self._power_context(challenge)
        self.music_type = typ
        self.static = {}
        for mid in self.mids:
            card, r, b = self.cards[mid], self.rank[mid], self.b[mid]
            type_rate = self.type_base + r["_musicTypeBonusRate"] if (card["_cardType"] == 99 or typ == 99 or card["_cardType"] == typ) else 0
            tag_rate = self.tag_base + r["_musicTagBonusRate"] if set(card["_bestMusicTagIDs"]) & set(song["_bestMusicTagIDs"]) else 0
            self.static[mid] = dp._add(self.base[mid], self.flat[mid], dp._mul_floor(b, self.band[mid]),
                                      dp._mul_floor(b, (type_rate,) * 3), dp._mul_floor(b, (tag_rate,) * 3),
                                      dp._mul_floor(b, (self.vip,) * 3))

    def best_leader(self, mids):
        cards, chars = [self.cards[i] for i in mids], [self.chars[i] for i in mids]
        candidates = []
        for mid in mids:
            rates = leader_rates(self.data, self.cards[mid], self.own[mid], cards, chars, self.music_type)
            vectors = [dp._mul_floor(self.b[m], rate) for m, rate in zip(mids, rates)]
            candidates.append((sum(sum(v) for v in vectors), -mid, vectors))
        value, negmid, vectors = max(candidates, key=lambda v: (v[0], v[1]))
        return -negmid, vectors

    def total(self, mids, sids, leader_vectors):
        return sum(sum(dp._add(self.static[m], self.pair[m, s], v)) for m, s, v in zip(mids, sids, leader_vectors)) // 10000

    def bonuses(self, mids, sids):
        cards = [self.mb[m] for m in mids] + [self.sb[s] for s in sids]
        return {name: sum(c[f"{name}_bonus_10000"] for c in cards) for name in ("event_pt", "shop_pt")}


class Scores:
    def __init__(self, data, spec, challenge, metrics=None):
        self.metrics = metrics or Trace(False)
        begin = time.perf_counter()
        self.method = spec.get("method")
        if self.method not in ("ap", "skip"):
            raise InputError("请选择手动 AP 参考或跳过参考。")
        song = integer(spec.get("song_id"), "歌曲 ID", 1)
        try:
            self.chart = ms.prepare_ap_chart(data.snapshot, song, spec.get("difficulty"), 1, ordinary=not challenge,
                                            _conversion_report=data.conversion_report, _verified_inputs=data.score_inputs)
        except ValueError as e:
            raise InputError(f"此歌曲或谱面暂未接入：{e}") from e
        self.cache, self.score_cache, self.contract_cache = self.metrics.signatures, OrderedDict(), {}
        self.signature_namespace = (challenge, song, spec.get("difficulty"), self.method)
        self.bound_cache = OrderedDict()
        p = self.chart
        _, tables, _ = data.score_inputs.values
        self.note_index = score_intervals.note_index(p) if self.method == "ap" else ()
        self.use_intervals = len(p["notes"]) >= 128
        great = next(r["_scorePercent"] for r in tables["MasterLiveJudgementParameter"] if r["_noteSimulateJudgement"] == 4)
        self.great = great
        valid = set(p["note_percents"]) - {120}
        notes = [(t, i, op) for t, i, op in p["enumerated_notes"] if op in valid]
        get_frame = lambda t: math.ceil(cp._float32(cp._float32(t) / cp._float32(40)))
        last = get_frame(notes[-1][0])
        self.skip_counts = Counter(p["note_percents"][op] for t, _, op in notes if get_frame(t) <= last)
        base = ms.combo_bases([(0, 0, 1)], tables["MasterLiveComboScoreBonus"])[0]
        self.skip_combo_base = base
        if self.metrics.enabled:
            self.metrics.seconds["sheet_preparation"] += time.perf_counter() - begin
            self.metrics.calls["sheet_preparation"] += 1

    def skip_score(self, power):
        p = self.chart
        return sum(count * ms.note_score(power, p["level"], p["denominator"], p["adjustment"], pct,
                                         self.great, self.skip_combo_base, cp._float32(1))
                   for pct, count in self.skip_counts.items())

    @staticmethod
    def skill_key(slots):
        # All 120 orders are evaluated: relabelling performers cannot change
        # min/max. Keep effect order within each performer (f32 ties matter).
        return tuple(sorted((s["extension_ms"], tuple((e["skill_effect_type"], tuple(e.get("judgement_targets", [])), e["effect_value"], e["activation_ms"])
                                                     for e in s["active_live_effects"])) for s in slots))

    def order_signature(self, slots, order):
        p = self.chart
        active_commands = 2 * sum(1 if effect["skill_effect_type"] == 2000 else
                                sum(target == 5 for target in effect.get("judgement_targets", []))
                                for slot in slots for effect in slot["active_live_effects"])
        if self.use_intervals and len(self.note_index) * max(1, active_commands) <= 2 * len(p["notes"]):
            self.metrics.count("interval_signatures")
            return score_intervals.signature(p, slots, order, self.note_index)
        self.metrics.count("scan_signatures")
        counts = Counter()
        for op, base, factor in ms.ap_factor_samples(p, slots, order):
            counts[p["note_percents"][op], base, factor] += 1
        return tuple(counts.items())

    def rank_upper_bound(self, power, slots):
        """min(all 120 scores) <= score(one order), with exactly the same f32/floors."""
        key = self.skill_key(slots)
        if key not in self.bound_cache:
            if len(self.bound_cache) >= 512:
                self.bound_cache.popitem(last=False)
            ordered = sorted(slots, key=lambda s: self.skill_key([s])[0])
            self.bound_cache[key] = self.order_signature(ordered, tuple(range(5)))
        p = self.chart
        score = sum(n * ms.note_score(power, p["level"], p["denominator"], p["adjustment"], pct,
                                      p["perfect_percent"], base, factor)
                    for (pct, base, factor), n in self.bound_cache[key])
        return ms.rank_for_score(score, p["rank_thresholds"])

    def signatures(self, slots):
        """Group equal f32 inputs; multiplicity preserves every per-note floor."""
        key = (self.signature_namespace, self.skill_key(slots))
        if key in self.cache:
            self.cache.move_to_end(key)
            self.metrics.count("signature_cache_hits")
            return self.cache[key]
        p = self.chart
        signatures = []
        slots = sorted(slots, key=lambda s: self.skill_key([s])[0])
        seen_orders = set()
        for order in ORDERS:
            order_key = tuple(self.skill_key([slots[i]])[0] for i in order)
            if order_key in seen_orders:
                continue
            seen_orders.add(order_key)
            signatures.append(self.order_signature(slots, order))
        # Bounded cache: huge public pools must not retain every order matrix.
        if len(self.cache) >= 512:
            self.cache.popitem(last=False)
        self.cache[key] = signatures
        return signatures

    def evaluate(self, power, slots=None):
        with self.metrics.measure("exact_scoring"):
            return self._evaluate(power, slots)

    def _evaluate(self, power, slots=None):
        key = (power, self.method, None if slots is None else self.skill_key(slots))
        if key in self.score_cache:
            self.score_cache.move_to_end(key)
            self.metrics.count("score_cache_hits")
            return self.score_cache[key]
        if self.method == "skip":
            low = high = self.skip_score(power)
            count = 1
        else:
            p = self.chart
            @lru_cache(maxsize=2048)
            def point(pct, base, factor):
                return ms.note_score(power, p["level"], p["denominator"], p["adjustment"], pct,
                                     p["perfect_percent"], base, factor)
            scores = [sum(n * point(*sig) for sig, n in counts) for counts in self.signatures(slots)]
            low, high, count = min(scores), max(scores), 120
        if high > ms.INT32_MAX:
            raise InputError("估算得分超出已覆盖的整数范围。")
        result = {"method": self.method, "minimum_score": low, "maximum_score": high,
                  "rank": ms.rank_for_score(low, self.chart["rank_thresholds"]),
                  "maximum_rank": ms.rank_for_score(high, self.chart["rank_thresholds"]),
                  "skill_orders": count, "status": "model_estimate"}
        if len(self.score_cache) >= MAX_SCORE_CACHE:
            self.score_cache.popitem(last=False)
        self.score_cache[key] = result
        return result


def strongest_assignment(model, mids, sids):
    """Exact 5x5 assignment, skip ignores all skills. No greedy pairing."""
    # Only 120 permutations; the integer terms are precomputed.
    return max(itertools.permutations(sids), key=lambda ss: (sum(sum(model.pair[m, s]) for m, s in zip(mids, ss)), tuple(-s for s in ss)))


def _team_count(groups):
    coefficients = [1, 0, 0, 0, 0, 0]
    for cards in groups.values():
        for size in range(5, 0, -1):
            coefficients[size] += coefficients[size-1] * len(cards)
    return coefficients[5]


class MemberTeams:
    """Lexicographic legal teams, generated lazily; resume skips whole subtrees."""
    def __init__(self, grouped):
        self.ids = sorted(m for cards in grouped.values() for m in cards)
        self.character = {m: c for c, cards in grouped.items() for m in cards}
        self.count = _team_count(grouped)

    def __len__(self):
        return self.count

    def __iter__(self):
        return self.iter_from(0)

    def iter_from(self, offset):
        remaining = offset
        def walk(start, selected, used):
            nonlocal remaining
            need = 5 - len(selected)
            if not need:
                if remaining:
                    remaining -= 1
                else:
                    yield selected
                return
            for i in range(start, len(self.ids) - need + 1):
                mid = self.ids[i]
                char = self.character[mid]
                if char in used:
                    continue
                next_used = used | {char}
                if remaining and need > 1:
                    groups = Counter(self.character[m] for m in self.ids[i+1:]
                                     if self.character[m] not in next_used)
                    counts = [1] + [0] * (need-1)
                    for n in groups.values():
                        for k in range(need-1, 0, -1):
                            counts[k] += counts[k-1] * n
                    if remaining >= counts[-1]:
                        remaining -= counts[-1]
                        continue
                yield from walk(i+1, selected + (mid,), next_used)
        return walk(0, (), set())


class SnapSets:
    def __init__(self, ids):
        self.ids = tuple(ids)
        self.count = math.comb(len(ids), 5)

    def __len__(self):
        return self.count

    def __iter__(self):
        return itertools.combinations(self.ids, 5)


class MatchingOverflow(Exception):
    """Internal request to continue with exact streaming, never reject input."""


def matching_outcomes(model, members, snap_ids, skill_tokens=None,
                      pulse=lambda **kwargs: None, cancelled=lambda: False):
    """Exact matching DP, with bonus sums and the entire AP skill multiset.

    Processing a Snap once in descending mask size makes reuse impossible.
    At a fixed input prefix, equal keys have exactly the same future choices.
    Keep maximal integer pair power: all supported note factors are nonnegative,
    so each f32 operation/per-note floor and hence minimum rank is monotone.
    Only identical skill multisets merge; effect order inside a slot is retained.
    """
    layers = [{(0, 0, 0, ()): (0, (0,)*5)}, {}, {}, {}, {}, {}]
    states, transitions = 1, 0
    for index, sid in enumerate(snap_ids):
        event = model.sb[sid]["event_pt_bonus_10000"]
        shop = model.sb[sid]["shop_pt_bonus_10000"]
        weights = [sum(model.pair[m, sid]) for m in members]
        for size in range(4, -1, -1):
            source, target = layers[size], layers[size+1]
            for (mask, ep, sp, skills), (power, assigned) in source.items():
                for slot, mid in enumerate(members):
                    if mask & (1 << slot):
                        continue
                    transitions += 1
                    if transitions % 256 == 0 and cancelled():
                        raise Cancelled()
                    signature = (tuple(sorted(skills + (skill_tokens[mid, sid],)))
                                 if skill_tokens is not None else ())
                    key = (mask | (1 << slot), ep+event, sp+shop, signature)
                    value = (power+weights[slot], assigned[:slot] + (sid,) + assigned[slot+1:])
                    previous = target.get(key)
                    if previous is None:
                        target[key] = value
                        states += 1
                        if states > MAX_MATCH_STATES:
                            raise MatchingOverflow()
                    elif value[0] > previous[0] or (value[0] == previous[0] and value[1] < previous[1]):
                        target[key] = value
        if cancelled():
            raise Cancelled()
        pulse(snap_index=index+1, snap_count=len(snap_ids), matching_states=states)
    # Strongest candidates establish incumbents early for exact rank bounds.
    return sorted(layers[5].values(), key=lambda v: (-v[0], v[1]))


def cycle(normal, challenge, budget, boost_each, starting_cp, challenge_cp):
    plays, boost_left = divmod(budget, boost_each)
    earned = normal["cp"] * plays
    count, left = divmod(starting_cp + earned, challenge_cp)
    return {"normal_plays": plays, "challenge_plays": count, "boost_budget": budget,
            "boost_spent": plays * boost_each, "boost_remaining": boost_left,
            "starting_cp": starting_cp, "cp_gained": earned, "cp_spent": count * challenge_cp,
            "cp_remaining": left, "event_pt": normal["event_pt"] * plays + challenge["event_pt"] * count,
            "shop_pt": normal["shop_pt"] * plays + challenge["shop_pt"] * count,
            "ordinary_event_pt": normal["event_pt"] * plays, "ordinary_shop_pt": normal["shop_pt"] * plays,
            "challenge_event_pt": challenge["event_pt"] * count, "challenge_shop_pt": challenge["shop_pt"] * count}


def _select_ids(request, profile, key, kind):
    ids = request.get(key)
    owned = cp._inventory(profile, kind)
    cp._selection(ids, owned, kind)
    if len(ids) < 5:
        raise InputError(f"请勾选至少 5 张{'成员卡' if kind == 'members' else 'Snap'}作为候选。")
    return sorted(ids)


def _song_specs(settings, mode, data):
    selection = settings[mode]
    sheets = selection.get("sheets", [{"song_id": selection.get("song_id"), "difficulty": selection.get("difficulty")}])
    if not isinstance(sheets, list) or not sheets:
        raise InputError(f"请至少勾选一个{'普通演出' if mode == 'normal' else '挑战'}谱面。")
    available = {(c["music_id"], c["difficulty"]) for c in data.conversion_report["charts"]}
    seen, specs = set(), []
    for sheet in sheets:
        if not isinstance(sheet, dict):
            raise InputError("歌曲选择格式不正确。")
        sid = integer(sheet.get("song_id"), "歌曲 ID", 1)
        diff = sheet.get("difficulty")
        if not isinstance(diff, str) or (sid, diff) not in available:
            raise InputError("勾选的歌曲或难度尚未接入。")
        if mode == "challenge" and not any(r["_eventId"] == 1 and r["_liveMusicId"] == sid for r in data.tables["MasterChallengeMusic"]):
            raise InputError("这首歌不在本活动的挑战乐曲列表内。")
        if (sid, diff) in seen:
            raise InputError("同一个谱面不能重复选择。")
        seen.add((sid, diff))
        specs.append({"song_id": sid, "difficulty": diff, "method": selection.get("method")})
    return specs


def check_playability(request, specs, data):
    prefs = request.get("playability", {})
    if not isinstance(prefs, dict):
        raise InputError("个人可打歌曲设置格式不正确。")
    limits = prefs.get("max_levels", {})
    if not isinstance(limits, dict):
        raise InputError("可打难度上限格式不正确。")
    for diff, value in limits.items():
        if diff not in ("easy", "normal", "hard", "expert"):
            raise InputError("未知的个人难度设置。")
        if value is not None:
            integer(value, "可打谱面等级上限", 1, 50)
    known = {r["_id"] for r in data.tables["MasterLiveMusic"]}
    for field in ("excluded_song_ids", "favorite_song_ids"):
        ids = prefs.get(field, [])
        if not isinstance(ids, list) or any(type(i) is not int or i not in known for i in ids) or len(ids) != len(set(ids)):
            raise InputError("排除或收藏的歌曲 ID 不正确。")
    if prefs.get("range", "all") not in ("all", "favorites"):
        raise InputError("请选择全部可用乐曲或仅收藏乐曲。")
    score_table = read_json(data.snapshot / "raw/MasterLiveMusicScore.json")["_allData"]
    score_index = {r["_id"]: r for r in score_table}
    for mode, sheets in specs.items():
        for spec in sheets:
            sid, diff = spec["song_id"], spec["difficulty"]
            music = data.index["MasterLiveMusic"][sid]
            name = data.text[music["_titleTextID"]]
            if sid in prefs.get("excluded_song_ids", []):
                raise InputError(f"「{name}」已被个人设置排除，请调整歌曲选择。")
            if prefs.get("range") == "favorites" and sid not in prefs.get("favorite_song_ids", []):
                raise InputError(f"「{name}」不在收藏范围内，请调整歌曲选择。")
            level = score_index[music[f"_{diff}ID"]]["_musicScoreLevel"]
            cap = limits.get(diff)
            if spec["method"] == "ap" and cap is not None and level > cap:
                raise InputError(f"「{name}」{diff.upper()} Lv.{level} 超过个人上限 {cap}，请调整歌曲选择。")


def _sheet_cache_key(data, profile, mids, sids, spec, mode):
    # Budget, CP balance, consumption and other sheets are intentionally absent.
    # Conservative profile hashing also invalidates after any growth edit.
    canonical = deepcopy(profile)
    for kind in ("members", "snaps"):
        canonical["inventory"][kind] = sorted(canonical["inventory"][kind], key=lambda r: r["id"])
    for key, identifier in (("character_ranks", "character_id"), ("facilities", "id")):
        canonical[key] = sorted(canonical.get(key, []), key=lambda r: r[identifier])
    return digest({"model": data.fingerprint(), "profile": canonical, "members": mids, "snaps": sids,
                   "spec": spec, "mode": mode})


def _reward_rows(data, rows, mode, consumed):
    # Compress only after applying this run's consumption. Rounding can merge
    # different bonus rates at one consumption and distinguish them at another.
    best = {}
    preview = rw.preview_challenge_rewards if mode == "challenge" else rw.preview_normal_rewards
    for raw in rows:
        row = {**raw, "per_live": preview(data.snapshot, raw["score"]["rank"], consumed,
                                          raw["bonuses_10000"]["event_pt"], raw["bonuses_10000"]["shop_pt"],
                                          _context=data.reward_context)["gained"]}
        gains = row["per_live"]
        key = (gains["cp"], gains["event_pt"], gains["shop_pt"])
        if key not in best or row["power"] > best[key]["power"]:
            best[key] = row
    return list(best.values())


def optimize(request, progress=lambda **kwargs: None, cancelled=lambda: False, data=None, cache=None, run_id=None,
             diagnostics=True):
    metrics = Trace(diagnostics)
    with metrics.measure("complete_search"):
        result = _optimize(request, progress, cancelled, data, cache, run_id, metrics)
    if metrics.enabled:
        result["search"]["diagnostics"] = metrics.report()
    return result


def _optimize(request, progress, cancelled, data, cache, run_id, metrics):
    begin = time.monotonic()
    if not isinstance(request, dict) or not isinstance(request.get("profile"), dict):
        raise InputError("请先录入或导入个人卡库。")
    profile = request["profile"]
    data = data or Data()
    mids = _select_ids(request, profile, "candidate_member_ids", "members")
    sids = _select_ids(request, profile, "candidate_snap_ids", "snaps")
    settings = request.get("settings", {})
    budget = integer(settings.get("boost_budget"), "Boost 总预算", 0, 2000)
    boost = integer(settings.get("boost_per_live"), "普通演出每次 Boost", 1, 10)
    starting = integer(settings.get("starting_cp"), "已有 CP", 0, 100000)
    cost = integer(settings.get("challenge_cp"), "每次挑战消耗 CP", 1)
    if cost not in (200, 400, 800, 1600):
        raise InputError("挑战 CP 消耗请选择 200、400、800 或 1600。")
    if not isinstance(settings.get("normal"), dict) or not isinstance(settings.get("challenge"), dict):
        raise InputError("请设置普通演出和挑战歌曲。")
    grouped = {}
    for mid in mids:
        if mid not in data.index["MasterMemberCard"]:
            raise InputError(f"成员卡 #{mid} 不在当前数据快照中。")
        grouped.setdefault(data.index["MasterMemberCard"][mid]["_characterID"], []).append(mid)
    if any(s not in data.index["MasterSupportCard"] for s in sids):
        raise InputError("候选 Snap 不在当前数据快照中。")
    member_count = _team_count(grouped)
    if not member_count:
        raise InputError("需要至少 5 位不同角色；同一角色的不同卡不能同时上场。")
    total_sets = member_count * math.comb(len(sids), 5)
    song_specs = {mode: _song_specs(settings, mode, data) for mode in ("normal", "challenge")}
    for label in ("normal", "challenge"):
        method = settings[label].get("method")
        if method not in ("ap", "skip"):
            raise InputError("请选择手动 AP 参考或跳过参考。")
    check_playability(request, song_specs, data)
    requirements = growth_issues(request, data)
    if requirements["issues"]:
        items = requirements["issues"]
        raise InputError(f"本次搜索有 {len(items)} 项养成待补或不正确：" + "；".join(f"{i['label']}（{i['reason']}）" for i in items[:3]) + "。请查看养成检查清单。")
    with metrics.measure("verified_score_inputs"):
        # New request = new trusted epoch, including callers reusing Data on disk.
        verified = ms.verify_score_inputs(data.snapshot)
        if data._score_inputs is not None and verified.values[2] != data._score_inputs.values[2]:
            raise InputError("评分数据已改变，请重新加载完整数据后计算。")
        data._score_inputs = verified
    sets, snap_sets = MemberTeams(grouped), SnapSets(sids)
    if any(settings[mode]["method"] == "ap" for mode in ("normal", "challenge")):
        owned = cp._inventory(profile, "members")
        for mid in mids:
            name = data.text[data.index["MasterMemberCard"][mid]["_nameTextID"]]
            integer(owned[mid].get("live_skill_level"), f"成员卡 #{mid}「{name}」的 Live 技能等级", 1, 5)
    with metrics.measure("power_preparation"):
        model = PowerModel(data, profile, mids, sids)
    model.metrics = metrics
    import solver_search
    if solver_search.should_use_solver(member_count, len(sids), song_specs):
        try:
            return solver_search.optimize(request, data, model, song_specs, member_count,
                                          progress, cancelled, cache, run_id, begin)
        except solver_search.UnsupportedModel:
            # Preserve the full pool and original arithmetic when a future skill
            # cannot be represented by the integer solver. No greedy fallback.
            progress(strategy="matching", stage="采用完整匹配搜索", phase="保留全部候选卡与原始计算条件")
    evaluations, valid_counts = {}, {}
    pair_cache = {}
    total_sheets = sum(len(v) for v in song_specs.values())
    global_work = sum(total_sets * (120 if settings[mode]["method"] == "ap" else 1) * len(song_specs[mode]) for mode in song_specs)
    finished_work, finished_sheets, completed_keys, hits = 0, 0, [], 0
    if cache:
        cache.checkpoint(request, job_id=run_id, status="running", keys=[], total_sheets=total_sheets, completed_sheets=0)
    for mode in ("normal", "challenge"):
        matching_cache = OrderedDict()
        evaluations[mode] = []
        total_evaluated, computed, cached_sheets = 0, 0, 0
        mode_stats = Counter()
        for spec in song_specs[mode]:
            if cancelled():
                raise Cancelled()
            key = _sheet_cache_key(data, profile, mids, sids, spec, mode) if cache else None
            saved = cache.get(key) if cache else None
            def sheet_progress(**changes):
                changes.update(done=finished_work + changes.get("done", 0), total=global_work,
                               completed_sheets=finished_sheets, total_sheets=total_sheets,
                               cached_sheets=hits, current_sheet=finished_sheets+1)
                changes.update(done_exact=str(changes["done"]), total_exact=str(global_work),
                               elapsed_seconds=round(time.monotonic()-begin, 1))
                progress(**changes)
            if saved is not None:
                rows, visited = saved["rows"], saved["visited"]
                cached_sheets += 1
                hits += 1
            else:
                partial = cache.get_partial(key) if cache else None
                per_team = len(snap_sets) * (120 if spec["method"] == "ap" else 1)
                if partial and (partial["next_member"] > member_count or partial["visited"] != partial["next_member"]*per_team):
                    partial = None
                stats = {}
                def save_partial(value):
                    if cache:
                        cache.put_partial(key, value)
                        cache.checkpoint(request, job_id=run_id, status="running", keys=completed_keys,
                                         total_sheets=total_sheets, completed_sheets=finished_sheets,
                                         partial_key=key, saved_member_teams=value["next_member"],
                                         total_member_teams=member_count)
                rows, visited = evaluate_song(data, model, profile, spec, mode, sets, snap_sets,
                                              pair_cache, sheet_progress, cancelled, partial, save_partial, stats,
                                              matching_cache=matching_cache)
                computed += visited - (partial["visited"] if partial else 0)
                mode_stats.update(stats)
                if cache:
                    cache.put(key, rows, visited)
                    cache.drop_partial(key)
            finished_work += visited
            finished_sheets += 1
            if cache:
                completed_keys.append(key)
                cache.checkpoint(request, job_id=run_id, status="running", keys=completed_keys,
                                 total_sheets=total_sheets, completed_sheets=finished_sheets)
            progress(stage=f"已完成 {finished_sheets} / {total_sheets} 张谱面 · 复用 {hits} 张",
                     done=finished_work, total=global_work, completed_sheets=finished_sheets,
                     total_sheets=total_sheets, cached_sheets=hits, current_sheet=finished_sheets,
                     done_exact=str(finished_work), total_exact=str(global_work), phase="",
                     elapsed_seconds=round(time.monotonic()-begin, 1))
            evaluations[mode].extend(_reward_rows(data, rows, mode, cost if mode == "challenge" else boost))
            total_evaluated += visited
        valid_counts[mode] = {"evaluated_bindings": total_evaluated, "song_sheets": len(song_specs[mode]),
                              "computed_bindings": computed, "cached_sheets": cached_sheets,
                              "reward_outcomes": len(evaluations[mode]), "completed": True,
                              "optimization": dict(mode_stats)}
    if cancelled():
        raise Cancelled()
    result = combine_plans(evaluations, budget, boost, starting, cost, request, data, valid_counts, time.monotonic() - begin)
    result["search"].update(batch_size_sheets=1, total_sheets=total_sheets, cached_sheets=hits,
                           computed_sheets=total_sheets-hits, persistent_cache=cache is not None,
                           candidate_limit=None, algorithm="exact_matching_dp_with_streaming_fallback",
                           binding_counts="full logical coverage including exactly merged states")
    if cache:
        cache.checkpoint(request, job_id=run_id, status="complete", keys=completed_keys,
                         total_sheets=total_sheets, completed_sheets=finished_sheets)
    return result


def evaluate_song(data, model, profile, spec, mode, sets, snap_sets, pair_cache, progress, cancelled,
                  resume=None, checkpoint=lambda value: None, stats=None, matching_cache=None):
    challenge = mode == "challenge"
    metrics = getattr(model, "metrics", Trace(False))
    scores = Scores(data, spec, challenge, metrics)
    model.music(spec["song_id"], challenge)
    per_team = len(snap_sets) * (120 if scores.method == "ap" else 1)
    total_work = len(sets) * per_team
    start = resume["next_member"] if resume else 0
    visited = start * per_team
    best_by_rewards = {(r["score"]["rank"], r["bonuses_10000"]["event_pt"], r["bonuses_10000"]["shop_pt"]): r
                       for r in (resume["rows"] if resume else [])}
    counters = Counter(resume.get("stats", {}) if resume else {})
    song = data.index["MasterLiveMusic"][spec["song_id"]]
    stage = ("普通演出 · " if not challenge else "挑战 · ") + data.text[song["_titleTextID"]] + " / " + spec["difficulty"].upper()
    last_progress, last_save = 0, time.monotonic()
    completed = start
    committed_rows, committed_stats = list(best_by_rewards.values()), dict(counters)

    def emit(force=False, **changes):
        nonlocal last_progress
        now = time.monotonic()
        if force or now-last_progress >= .3:
            progress(stage=stage, done=visited, total=total_work, completed_member_teams=completed,
                     total_member_teams=len(sets), **changes)
            last_progress = now

    def save():
        checkpoint({"next_member": completed, "visited": completed*per_team,
                    "rows": committed_rows, "stats": committed_stats})

    emit(True, phase="精确匹配")
    try:
        for index, members in enumerate(sets.iter_from(start), start):
            if cancelled():
                raise Cancelled()
            lead, vectors = model.best_leader(members)
            tokens = None
            if scores.method == "ap":
                tokens, token_ids = {}, {}
                # The audited contract requires a legal five-person deck. Cyclic
                # matchings cover every pair once without altering that contract.
                for offset in range(len(snap_sets.ids)):
                    if cancelled():
                        raise Cancelled()
                    binding = tuple(snap_sets.ids[(offset+j) % len(snap_sets.ids)] for j in range(5))
                    if any((m,s) not in pair_cache for m,s in zip(members,binding)):
                        try:
                            contract = sk.derive_ap_skill_contract(data.snapshot, profile, list(members), list(binding),
                                                                  _verified_inputs=data.skill_inputs)
                        except ValueError as e:
                            raise InputError(f"当前 AP 模型尚未覆盖这组技能：{e}。可选择跳过参考，或调整候选卡。不会把该技能当作零效果。") from e
                        for slot in contract["slots"]:
                            pair_cache[slot["member_id"], slot["snap_id"]] = {"extension_ms": slot["extension_ms"],
                                                                           "active_live_effects": slot["active_live_effects"]}
                for mid in members:
                    for sid in snap_sets.ids:
                        signature = Scores.skill_key([pair_cache[mid, sid]])[0]
                        tokens[mid, sid] = token_ids.setdefault(signature, len(token_ids))

            def consider(snaps):
                if cancelled():
                    raise Cancelled()
                power = model.total(members, snaps, vectors)
                slots = [pair_cache[m, s] for m, s in zip(members, snaps)] if tokens is not None else None
                bonuses = model.bonuses(members, snaps)
                if slots is not None:
                    incumbent = next((rank for rank in reversed(RANKS)
                                      if (rank, bonuses["event_pt"], bonuses["shop_pt"]) in best_by_rewards
                                      and best_by_rewards[rank, bonuses["event_pt"], bonuses["shop_pt"]]["power"] >= power), None)
                    if incumbent is not None:
                        counters["rank_bound_evaluations"] += 1
                        upper = scores.rank_upper_bound(power, slots)
                        if RANKS.index(incumbent) >= RANKS.index(upper):
                            counters["rank_bound_pruned"] += 1
                            return
                estimate = scores.evaluate(power, slots)
                counters["score_evaluations"] += 1
                row = {"member_ids": list(members), "snap_ids": list(snaps), "leader_member_id": lead,
                       "song_id": spec["song_id"], "difficulty": spec["difficulty"], "power": power,
                       "score": estimate, "bonuses_10000": bonuses}
                rk = (estimate["rank"], bonuses["event_pt"], bonuses["shop_pt"])
                old = best_by_rewards.get(rk)
                if old is None or power > old["power"]:
                    best_by_rewards[rk] = row

            try:
                match_key = (members, scores.method)
                if matching_cache is not None and match_key in matching_cache:
                    outcomes = matching_cache[match_key]
                    matching_cache.move_to_end(match_key)
                    metrics.count("cross_sheet_matching_hits")
                else:
                    with metrics.measure("matching"):
                        outcomes = matching_outcomes(model, members, snap_sets.ids, tokens,
                                                     pulse=lambda **v: emit(phase="精确匹配", **v), cancelled=cancelled)
                    if matching_cache is not None:
                        matching_cache[match_key] = outcomes
                        while sum(map(len, matching_cache.values())) > MAX_MATCH_STATES:
                            matching_cache.popitem(last=False)
            except MatchingOverflow:
                counters["streaming_teams"] += 1
                covered = 0
                for snapset in snap_sets:
                    assignments = itertools.permutations(snapset) if tokens is not None else [strongest_assignment(model, members, snapset)]
                    for snaps in assignments:
                        consider(snaps)
                        covered += 1
                        visited = index*per_team + covered
                        emit(phase="分段穷举（继续完整搜索）")
            else:
                counters["dp_teams"] += 1
                for _, snaps in outcomes:
                    consider(snaps)
                    emit(phase="比较精确匹配结果")
            completed = index+1
            visited = completed*per_team
            committed_rows, committed_stats = list(best_by_rewards.values()), dict(counters)
            if time.monotonic()-last_save >= 2:
                save()
                last_save = time.monotonic()
            emit(True, phase="已完成成员队伍")
    except Cancelled:
        save()
        raise
    if stats is not None:
        stats.update(counters)
    return list(best_by_rewards.values()), visited


def _top_distinct(rows, key, category=lambda row: None):
    """Top three songs per conditional class; stable first witness on ties."""
    buckets = {}
    for row in rows:
        ranked = buckets.setdefault(category(row), [])
        previous = next((i for i, old in enumerate(ranked) if old["song_id"] == row["song_id"]), None)
        if previous is None:
            ranked.append(row)
        elif key(row) > key(ranked[previous]):
            ranked[previous] = row
        ranked.sort(key=key, reverse=True)
        del ranked[3:]
    identities = {id(row) for ranked in buckets.values() for row in ranked}
    return [row for row in rows if id(row) in identities]


def _cycle_summary(evaluations, budget, boost, objective):
    other = "shop_pt" if objective == "event_pt" else "event_pt"
    power_key = lambda row: (row["power"], -row["song_id"])
    reward_key = lambda row: (row["per_live"][objective], row["per_live"][other], *power_key(row))
    if budget // boost:
        normal = _top_distinct(evaluations["normal"], reward_key, lambda row: row["per_live"]["cp"])
    else:
        normal = _top_distinct(evaluations["normal"], power_key)
    selected = {id(row) for row in _top_distinct(evaluations["challenge"], reward_key)
                + _top_distinct(evaluations["challenge"], power_key)}
    return {"normal": normal, "challenge": [row for row in evaluations["challenge"] if id(row) in selected]}


def combine_plans(evaluations, budget, boost, starting, cost, request=None, data=None, counts=None, elapsed=0):
    plans, top3 = {}, {}
    for objective in ("event_pt", "shop_pt"):
        candidates = _cycle_summary(evaluations, budget, boost, objective)
        other = "shop_pt" if objective == "event_pt" else "event_pt"
        key = lambda r: (r["totals"][objective], r["totals"][other], r["totals"]["cp_remaining"],
                         r["normal"]["power"], r["challenge"]["power"], -r["normal"]["song_id"], -r["challenge"]["song_id"])
        challenge = max(candidates["challenge"], key=lambda r: (r["per_live"][objective], r["per_live"][other], r["power"], -r["song_id"]))
        zero_play_challenge = max(candidates["challenge"], key=lambda r: (r["power"], -r["song_id"]))
        options = []
        for normal in candidates["normal"]:
            selected_challenge = (challenge if starting + (budget // boost) * normal["per_live"]["cp"] >= cost
                                  else zero_play_challenge)
            totals = cycle(normal["per_live"], selected_challenge["per_live"], budget, boost, starting, cost)
            options.append({"normal": normal, "challenge": selected_challenge, "totals": totals})
        plans[objective] = max(options, key=key)
        def distinct_songs(options, mode):
            best = {}
            for option in options:
                sid = option[mode]["song_id"]
                if sid not in best or key(option) > key(best[sid]):
                    best[sid] = option
            return sorted(best.values(), key=key, reverse=True)[:3]
        challenge_options = ({"normal": normal, "challenge": ch,
                              "totals": cycle(normal["per_live"], ch["per_live"], budget, boost, starting, cost)}
                             for ch in candidates["challenge"] for normal in candidates["normal"])
        top3[objective] = {"normal": distinct_songs(options, "normal"),
                           "challenge": distinct_songs(challenge_options, "challenge")}
    activity, shop = plans["event_pt"]["totals"], plans["shop_pt"]["totals"]
    result = {"version": VERSION, "status": "complete_model_search", "plans": plans, "top3": top3,
              "difference": {"event_pt_lost_with_shop_plan": activity["event_pt"] - shop["event_pt"],
                             "shop_pt_lost_with_event_plan": shop["shop_pt"] - activity["shop_pt"],
                             "identical_totals": (activity["event_pt"], activity["shop_pt"]) == (shop["event_pt"], shop["shop_pt"])},
              "search": {"complete": True, "scope": "selected_candidate_pool", "counts": counts,
                         "elapsed_seconds": round(elapsed, 2),
                         "normal_challenge_decks_independent": True,
                         "repeat_strategy": "one fixed deck and song per phase; no mixing between normal plays or challenge plays",
                         "song_ranking": "full-cycle objective at equal budget; re-optimize decks per sheet, one entry per song",
                         "leader_search": "all five; greatest power dominates because leader does not affect live skills",
                         "skip_assignment": "exact matching across all selected Snaps, preserving both bonus sums; skills do not affect skip",
                         "ap_order_policy": "minimum reference grade over all 120 orders, deterministic scenario; not actual-play guarantee"},
              "assumptions": ["普通演出为单人自由演出，撃奏关闭；尚未模拟协力房间与撃奏。",
                              "活动参数加成仅用于挑战综合力；普通与挑战分别计算综合力，活动 PT / 商店 PT 加成按各自结算保留。",
                              "手动 AP 参考假设每个原始判定都是 PERFECT、无辅助；按 120 种技能顺序中的最低参考评级计算每次收益。",
                              "AP 得分采用谱面时间，未验证实机逐帧回放；跳过得分也属于模型估算。",
                              "固定每场 Boost / CP 消耗，使用全部可用场次；不足一次的 Boost 和 CP 保留。",
                              "每种方案内，普通场次重复同一队伍与歌曲，挑战场次也重复同一队伍与歌曲；尚未搜索逐场轮换多队或多歌。",
                              "预算为已可使用的 Boost 和已有 CP；不计自然恢复、升级奖励、额外 CP 来源、CP 上限或过期。",
                              "不计跳过券和时间限制；使用跳过参考时请自行保证跳过券足够。",
                              "最优结论仅覆盖当前勾选的候选池与这些计算条件，养成费用不计入预算。"]}
    if data:
        result["source"] = {"provider": "bdon / Moenotes", "data_commit": data.manifest["data_commit"],
                            "model_commit": data.manifest["bdon_linked_model_commit"], "snapshot": "2026-10-01"}
    if request:
        result["inputs"] = {"candidate_member_ids": request["candidate_member_ids"],
                            "candidate_snap_ids": request["candidate_snap_ids"], "settings": request["settings"]}
    return result


def calibration(data=None):
    data = data or Data()
    demo = demo_profile()
    power_matches = None
    if demo["is_demo"]:
        members, snaps = [59, 11, 55, 26, 3], [33, 37, 52, 61, 3]
        power = dp.calculate_selected_deck_power(data.snapshot, demo["profile"], members, snaps, 55, 100109)
        bonuses = power["event_bonuses"]["total"]
        power_matches = power["total"]["total"] == 812225 and bonuses["event_pt_bonus_10000"] == 10800 and bonuses["shop_pt_bonus_10000"] == 14000
    # The shareable package retains historical validation numbers, but does not
    # contain the owner's actual inventory or growth profile. It rechecks public
    # reward arithmetic only; never claim a fresh power check without the fixture.
    challenge = rw.preview_challenge_rewards(data.snapshot, "B", 200, 10800, 14000)
    ordinary = rw.preview_normal_rewards(data.snapshot, "B", 4, 10600, 15000)
    passed = power_matches is not False and challenge["gained"] == {"cp": 0, "event_pt": 5304, "shop_pt": 8160} and ordinary["gained"] == {"cp": 100, "event_pt": 1442, "shop_pt": 2100}
    if not passed:
        raise InputError("截图校准未通过，已停止计算。")
    return {"passed": True, "power_recomputed": power_matches is True, "actual": {"power": 812225, "challenge_manual_score": 4278210,
                                       "challenge_rank": "B", "challenge_event_pt": 5304, "challenge_shop_pt": 8160,
                                       "challenge_cp_gained": 0, "normal_event_pt": 1442, "normal_shop_pt": 2100,
                                       "normal_cp": 100},
            "notes": ["综合力与挑战收益匹配已核对的截图。挑战实测为手动 AP。",
                      "200 CP 是与 1 倍收益相符的模型输入，实际扣费未由结算截图直接显示。",
                      "普通演出截图为另一组未知队伍；只用于核对收益，不能当作当前队伍的普通演出实测。",
                      "后续搜索结果均为模型估算；截图示例不等于你的完整卡库。"]}

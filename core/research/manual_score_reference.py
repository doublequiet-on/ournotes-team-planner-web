"""Bounded AP challenge reference using the pinned bdon-linked score model.

Every operation follows the source's binary32 grouping. Notes are Perfect at
chart times, same-time notes read the same preceding combo, and factor commands
are applied once. This is not a full frame replay or an exact real-play score.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from types import MappingProxyType
import hashlib
import itertools
import json
import math
from pathlib import Path

from card_power import _float32 as f32
from reward_preview import preview_challenge_rewards


MODEL_COMMIT = "dbd9cf01a4854808dceb6aedcd4041af372faeee"
DATA_COMMIT = "0f8644dd85b7c3d21794fb3f718e48773cd1cc60"
TABLES = ("MasterLiveMusic", "MasterLiveMusicScore", "MasterLiveScoreRank",
          "MasterChallengeMusic", "MasterLiveSettings", "MasterLiveNoteParameter",
          "MasterLiveJudgementParameter", "MasterLiveComboScoreBonus")
SOURCES = tuple("model_reference/src/" + path for path in (
    "live/score.rs", "live/model.rs", "live/skill.rs", "live/skip.rs", "num.rs"))
UNJUDGED = frozenset((0, 80, 82, 100, 103, 121, 122, 123))
RANKS = {2: "D", 3: "C", 4: "B", 5: "A", 6: "S", 7: "SS"}
INT32_MAX = 2 ** 31 - 1


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class VerifiedScoreInputs:
    snapshot: Path
    values: tuple


def verify_score_inputs(snapshot):
    snapshot = Path(snapshot).resolve()
    return VerifiedScoreInputs(snapshot, tuple(_freeze(value) for value in _checked_inputs(snapshot)))


def _json(path: Path) -> dict:
    return json.loads(path.read_bytes())


def _integer(value: int, label: str, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= INT32_MAX:
        raise ValueError(f"{label} must be an integer in [{minimum}, {INT32_MAX}]")
    return value


def _unique(rows: list[dict], **criteria: int) -> dict:
    matches = [row for row in rows if all(row.get(key) == value for key, value in criteria.items())]
    if len(matches) != 1:
        raise ValueError(f"Expected one raw row: {criteria}")
    return matches[0]


def _checked_inputs(snapshot: Path) -> tuple[dict, dict, dict]:
    primary = _json(snapshot / "source_manifest.json")
    manual = _json(snapshot / "manual_model_source_manifest.json")
    if primary["data_commit"] != DATA_COMMIT or manual["model_commit"] != MODEL_COMMIT:
        raise ValueError("This reference requires the reviewed fixed Master/model versions")
    entries = {row["local_path"]: row for row in primary["files"] + manual["files"]}
    tables, hashes = {}, {}
    for relative in tuple(f"raw/{name}.json" for name in TABLES) + SOURCES:
        entry = entries[relative]
        path = (snapshot / relative).resolve()
        if not path.is_relative_to(snapshot.resolve()):
            raise ValueError("Source path leaves snapshot")
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != entry["sha256"]:
            raise ValueError(f"Source checksum mismatch: {relative}")
        hashes[relative] = digest
        if relative in SOURCES and entry["commit"] != MODEL_COMMIT:
            raise ValueError(f"Unreviewed model version: {relative}")
        if relative.startswith("raw/"):
            if entry["commit"] != DATA_COMMIT:
                raise ValueError(f"Unreviewed Master version: {relative}")
            rows = json.loads(payload)["_allData"]
            if len({row["_id"] for row in rows}) != len(rows):
                raise ValueError(f"Duplicate raw table ID: {relative}")
            tables[Path(relative).stem] = rows
    return primary, tables, hashes


def combo_bases(notes: list[tuple[int, int, int]], rows: list[dict]) -> list[float]:
    """notes are sorted (time, id, op); shared chart time reads prior combo."""
    if notes != sorted(notes) or len({note[1] for note in notes}) != len(notes):
        raise ValueError("Notes must be sorted with unique IDs")
    rows = sorted((row for row in rows if row["_comboBonusType"] == 0),
                  key=lambda row: row["_requiredComboCount"])
    if len({row["_requiredComboCount"] for row in rows}) != len(rows):
        raise ValueError("Duplicate combo threshold")
    table, cumulative = [], f32(0)
    for row in rows:
        cumulative = f32(cumulative + f32(row["_bonusFactor"]))
        table.append((row["_requiredComboCount"], cumulative))
    bases, previous, before, cursor, bonus = [], None, 0, 0, f32(0)
    for count, (time, _id, _op) in enumerate(notes):
        if time != previous:
            previous, before = time, count
        while cursor < len(table) and table[cursor][0] <= before:
            bonus = table[cursor][1]
            cursor += 1
        bases.append(f32(min(bonus, f32(1)) + f32(1)))
    return bases


def note_score(power: int, level: int, denominator: int, adjustment: float,
               note_percent: int, judge_percent: int, combo_base: float,
               note_score_up: float) -> int:
    """score.rs::note_score_core, restricted to positive life and Gekisou off."""
    difficulty = f32(f32(f32(level - 5) * f32(0.005)) + f32(1))
    t = f32(f32(f32(adjustment) * f32(power)) * difficulty)
    a = f32(f32(f32(note_percent) / f32(100)) * t)
    b = f32(f32(f32(judge_percent) / f32(100)) * a)
    combo_factor = f32(f32(1) * f32(f32(0) + combo_base))
    score_up = f32(note_score_up + f32(0))
    c = f32(f32(b * combo_factor) * score_up)
    d = f32(f32(f32(100) / f32(100)) * c)
    x = f32(d / f32(denominator))
    if not math.isfinite(x) or not 0 <= x <= INT32_MAX:
        raise ValueError("Score is outside the bounded reference integer range")
    y = f32(math.floor(x))
    z = f32(f32(1) * f32(f32(1) * f32(y * f32(1))))
    return math.floor(z)


def factor_commands(skill_times: list[int], skills: list[dict], order: tuple[int, ...],
                    music_length_ms: int, *, channels: bool = False) -> list[tuple]:
    _integer(music_length_ms, "music_length_ms", 1)
    for time in skill_times:
        _integer(time, "skill_time_ms")
    if (any(type(slot) is not int for slot in order) or len(skill_times) != len(skills)
            or sorted(order) != list(range(len(skills)))):
        raise ValueError("Order must permute exactly one performer per chart skill event")
    commands = []
    for event_index, slot in enumerate(order):
        start = skill_times[event_index]
        for row in skills[slot]["active_live_effects"]:
            kind = row["skill_effect_type"]
            if kind not in (2000, 2004) or (kind == 2004 and not channels):
                raise ValueError("Only plain note-score commands or explicit judgement channels are supported")
            targets = [0] if kind == 2000 else row.get("judgement_targets")
            if (not isinstance(targets, list) or not targets
                    or (kind == 2004 and any(type(t) is not int or t not in (3, 4, 5, 6) for t in targets))):
                raise ValueError("Judgement score effects require covered explicit targets")
            duration = f32(f32(row["activation_ms"]) + f32(skills[slot]["extension_ms"]))
            end = min(start + math.ceil(duration), music_length_ms)
            if duration <= 0 or end <= start:
                raise ValueError("Expected a positive skill window within this chart")
            factor = f32(f32(row["effect_value"]) / f32(10000))
            scaled = f32(factor * f32(100000))
            mill = math.floor(scaled) if kind == 2000 else round(scaled)
            owner = event_index * 100 + 1
            for target in targets:
                if target not in (0, 5):
                    continue  # Proven inactive on raw PERFECT; not an unknown effect.
                suffix = (target,) if channels else ()
                commands.extend(((start, owner, mill, *suffix), (end, owner, -mill, *suffix)))
    return sorted(commands, key=lambda command: (command[0], command[1]))


def ap_factor_samples(prepared: dict, skills: list[dict], order: tuple[int, ...]):
    """Preserve two binary32 accumulators and the final score.rs addition.

    Type 2000 starts at 1; type 2004 PERFECT starts at 0 and rounds ties to
    even. Merging their commands changes scores at integer/rank boundaries.
    """
    commands = factor_commands(prepared["skill_time_ms"], skills, order,
                               prepared["music_length_ms"], channels=True)
    cursor, note, perfect = 0, f32(1), f32(0)
    for (time, _id, op), base in zip(prepared["notes"], prepared["combo_bases"], strict=True):
        while cursor < len(commands) and commands[cursor][0] <= time:
            command = commands[cursor]
            amount = f32(f32(command[2]) / f32(100000))
            if command[3] == 0:
                if amount != 0:
                    note = f32(note + amount)
            else:
                perfect = f32(perfect + amount)
            cursor += 1
        yield op, base, f32(note + perfect)


def score_order(prepared: dict, skills: list[dict], order: tuple[int, ...]) -> int:
    total = 0
    # Nonnegative chart times give monotonic ceil(time/40) score buckets;
    # (time, owner) command sorting is identical to (bucket, time, owner).
    for op, base, factor in ap_factor_samples(prepared, skills, order):
        total += note_score(prepared["power"], prepared["level"], prepared["denominator"],
                            prepared["adjustment"], prepared["note_percents"][op],
                            prepared["perfect_percent"], base, factor)
        if total > INT32_MAX:
            raise ValueError("Total score exceeds the bounded reference integer range")
    return total


def rank_for_score(score: int, thresholds: list[dict]) -> str:
    _integer(score, "score")
    applicable = [row for row in thresholds if row["_requiredScore"] <= score]
    if not applicable:
        raise ValueError("No score rank applies")
    return RANKS[max(applicable, key=lambda row: row["_requiredScore"])["_liveScoreRank"]]


def prepare_ap_chart(snapshot: Path, song_id: int, difficulty: str, power: int,
                     event_id: int = 1, *, ordinary: bool = False,
                     _conversion_report: dict | None = None, _verified_inputs=None) -> dict:
    _integer(power, "power", 1)
    _integer(song_id, "song_id", 1)
    _integer(event_id, "event_id", 1)
    if difficulty not in {"easy", "normal", "hard", "expert"}:
        raise ValueError("Unrecognized difficulty")
    if _verified_inputs is None:
        manifest, tables, hashes = _checked_inputs(snapshot)
    else:
        if type(_verified_inputs) is not VerifiedScoreInputs or _verified_inputs.snapshot != Path(snapshot).resolve():
            raise ValueError("Invalid internal verified score context")
        manifest, tables, hashes = _verified_inputs.values
    if not ordinary:
        challenge = _unique(tables["MasterChallengeMusic"], _eventId=event_id, _liveMusicId=song_id)
        if any(challenge[f"_gekisouMission{i}"] for i in (1, 2, 3)):
            raise ValueError("This reference requires a challenge with Gekisou disabled")
    music = _unique(tables["MasterLiveMusic"], _id=song_id)
    sheet = _unique(tables["MasterLiveMusicScore"], _id=music[f"_{difficulty}ID"])
    report = (_json(snapshot / "validation/manual_chart_conversion.json") if _conversion_report is None
              else _conversion_report)
    if report["status"] != "verified" or report["errors"]:
        raise ValueError("Formal chart conversion has not passed")
    conversion = _unique(report["charts"], music_id=song_id, difficulty=difficulty)
    chart_path = (snapshot / conversion["saved_path"]).resolve()
    if not chart_path.is_relative_to(snapshot.resolve()):
        raise ValueError("Converted chart path leaves snapshot")
    raw = chart_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != conversion["converted_record_sha256"]:
        raise ValueError("Converted chart checksum mismatch")
    chart = json.loads(raw)
    if chart["scoreId"] != sheet["_id"]:
        raise ValueError("Chart score ID mismatch")
    arrays = chart["notes"]
    lengths = {len(arrays[name]) for name in ("id", "op", "timeMs", "judgementType")}
    if len(lengths) != 1 or len(set(arrays["id"])) != len(arrays["id"]):
        raise ValueError("Invalid converted note arrays")
    if any(type(t) is not int or t < 0 for t in arrays["timeMs"]):
        raise ValueError("Nonnegative integer chart times are required")
    factors = {row["_noteOperateType"]: row["_scorePercent"] for row in tables["MasterLiveNoteParameter"]}
    if len(factors) != len(tables["MasterLiveNoteParameter"]):
        raise ValueError("Duplicate note factor type")
    weighted = 0
    for op in arrays["op"]:
        weighted = (weighted + factors.get(op, 0) + 2 ** 31) % 2 ** 32 - 2 ** 31
    denominator = math.ceil(f32(f32(weighted) / f32(100)))
    if denominator <= 0 or denominator != conversion["score_denominator"]:
        raise ValueError("Converted chart divisor mismatch")
    notes = sorted((t, i, op) for t, i, op in zip(arrays["timeMs"], arrays["id"], arrays["op"], strict=True)
                   if op not in UNJUDGED)
    if len(notes) != sheet["_fullComboCount"] or any(op not in factors for _, _, op in notes):
        raise ValueError("Judged note count or factor mismatch")
    settings = {row["_key"]: row["_value"] for row in tables["MasterLiveSettings"]}
    perfect = _unique(tables["MasterLiveJudgementParameter"], _noteSimulateJudgement=5)
    if perfect["_damage"] != 0 or int(settings["life_base"]) != 1000:
        raise ValueError("This bounded AP life contract no longer applies")
    times = chart["skillEvents"]["timeMs"]
    # The source retains chart enumeration order: position is the event index,
    # not chronological order (data.rs DataChart::skill_events). Do not sort or
    # reject legitimate out-of-order events. Factor commands sort by time later.
    if len(times) != 5 or len(set(times)) != 5 or any(type(t) is not int or t < 0 for t in times):
        raise ValueError("Exactly five distinct nonnegative skill events are required")
    thresholds = [dict(row) for row in tables["MasterLiveScoreRank"] if row["_group"] == music["_liveScoreRankGroup"]]
    if len(thresholds) != 6 or len({row["_liveScoreRank"] for row in thresholds}) != 6:
        raise ValueError("Expected the six exact score rank thresholds")
    return {"song_id": song_id, "score_id": sheet["_id"], "difficulty": difficulty,
            "mode": "free_live" if ordinary else "challenge",
            "enumerated_notes": list(zip(arrays["timeMs"], arrays["id"], arrays["op"], strict=True)),
            "power": power, "level": sheet["_musicScoreLevel"], "denominator": denominator,
            "notes": notes, "combo_bases": combo_bases(notes, tables["MasterLiveComboScoreBonus"]),
            "adjustment": f32(float(settings["note_score_adjustment_factor"])),
            "perfect_percent": perfect["_scorePercent"], "note_percents": factors,
            "skill_time_ms": times, "music_length_ms": max(arrays["timeMs"]) + 1000,
            "rank_thresholds": thresholds, "source_hashes": dict(hashes),
            "data_commit": manifest["data_commit"], "chart_conversion": conversion}


def evaluate_ap_orders(snapshot: Path, prepared: dict, skill_contract: dict,
                       cp_consumed: int, pt_bonus: int, shop_bonus: int,
                       event_id: int = 1, observed_score: int | None = None) -> dict:
    skills = skill_contract["slots"]
    if len(skills) != 5:
        raise ValueError("Five actual member/Snap bindings are required")
    outcomes = []
    rewards = {}
    for order in itertools.permutations(range(5)):
        score = score_order(prepared, skills, order)
        rank = rank_for_score(score, prepared["rank_thresholds"])
        if rank not in rewards:
            rewards[rank] = preview_challenge_rewards(snapshot, rank, cp_consumed, pt_bonus, shop_bonus, event_id)["gained"]
        outcomes.append({"slot_order": list(order), "member_order": [skills[slot]["member_id"] for slot in order],
                         "score": score, "rank": rank, "rewards": rewards[rank]})
    low, high = min(outcomes, key=lambda row: row["score"]), max(outcomes, key=lambda row: row["score"])
    comparison = None
    if observed_score is not None:
        _integer(observed_score, "observed_score")
        closest = min(outcomes, key=lambda row: abs(row["score"] - observed_score))
        comparison = {"observed_score": observed_score,
                      "within_reference_order_range": low["score"] <= observed_score <= high["score"],
                      "observed_rank": rank_for_score(observed_score, prepared["rank_thresholds"]),
                      "nearest_reference_score": closest["score"],
                      "nearest_absolute_difference": abs(closest["score"] - observed_score),
                      "exact_reference_matches": sum(row["score"] == observed_score for row in outcomes),
                      "matching_reference_member_orders": [row["member_order"] for row in outcomes if row["score"] == observed_score],
                      "actual_order_identified": False, "real_play_reproduced": False}
    return {"schema_version": 1, "status": "bounded_ap_skill_order_reference_computed",
            "model_commit": MODEL_COMMIT, "data_commit": prepared["data_commit"],
            "selection": {"song_id": prepared["song_id"], "score_id": prepared["score_id"],
                          "difficulty": prepared["difficulty"], "power": prepared["power"],
                          "cp_selection": cp_consumed, "event_pt_bonus_10000": pt_bonus,
                          "shop_pt_bonus_10000": shop_bonus},
            "chart": {"judged_notes": len(prepared["notes"]), "score_denominator": prepared["denominator"],
                      "skill_time_ms": prepared["skill_time_ms"],
                      "converted_record_sha256": prepared["chart_conversion"]["converted_record_sha256"]},
            "assumptions": {"mode": "challenge", "gekisou_enabled": False,
                            "judgement_before_support_conversion": "Perfect on every judged note",
                            "judgement_times": "Exact converted chart times; shared time reads prior combo",
                            "assist_enabled": False, "life_conditions": "Initial 1000, no damage; always >=700",
                            "factor_processing": "Every command once, same-time command before note; no frame rollback",
                            "order_weighting": "All 120 permutations receive equal weight for the reference mean; actual distribution unverified"},
            "skill_contract": skill_contract, "orders_evaluated": len(outcomes),
            "score_range": {"minimum": low["score"], "maximum": high["score"],
                            "uniform_order_mean": sum(row["score"] for row in outcomes) / len(outcomes)},
            "minimum_order": low, "maximum_order": high,
            "rank_counts": dict(Counter(row["rank"] for row in outcomes)),
            "reward_summary": {name: {"minimum": min(row["rewards"][name] for row in outcomes),
                                      "maximum": max(row["rewards"][name] for row in outcomes),
                                      "uniform_order_mean": sum(row["rewards"][name] for row in outcomes) / len(outcomes)}
                               for name in ("cp", "event_pt", "shop_pt")},
            "observed_score_comparison": comparison,
            "source_hashes": prepared["source_hashes"], "outcomes": outcomes,
            "scope": "Reference AP challenge model including verified Snap extensions. Exact in-game judgement frames, pre-conversion inputs, skill order, native frame rollback and full simulator parity are unverified. Range agreement is not exact score calibration. No inventory search or optimal-team claim."}


def main() -> None:
    from deck_power import calculate_selected_deck_power
    from manual_skill_contract import derive_ap_skill_contract

    research = Path(__file__).resolve().parent
    snapshot = research / "2026-10-01"
    growth = _json(research / "player_growth_observations.json")
    receipt = _json(research / "challenge_receipt_observations.json")
    members, snaps = growth["selected_member_ids"], growth["selected_snap_ids"]
    profile = growth["player_profile_partial"]
    power = calculate_selected_deck_power(snapshot, profile, members, snaps, growth["leader_member_id"],
                                          growth["song_id"], growth["event_id"])
    if power["total"]["total"] != receipt["party_binding"]["latest_observed_power"]:
        raise ValueError("Receipt and independently calculated power do not agree")
    skills = derive_ap_skill_contract(snapshot, profile, members, snaps)
    observed = receipt["score_observation"]
    if observed["play_method"] != "manual" or not observed["all_perfect_displayed"]:
        raise ValueError("The observation is not the confirmed manual AP sample")
    prepared = prepare_ap_chart(snapshot, observed["song_id"], observed["difficulty"], power["total"]["total"], growth["event_id"])
    bonuses = power["event_bonuses"]["total"]
    result = evaluate_ap_orders(snapshot, prepared, skills,
                               receipt["cp_consumption_context"]["selected_in_prior_setup"],
                               bonuses["event_pt_bonus_10000"], bonuses["shop_pt_bonus_10000"],
                               growth["event_id"], observed["score"])
    result["private_input_file"] = "../../player_growth_observations.json"
    result["private_input_sha256"] = hashlib.sha256((research / "player_growth_observations.json").read_bytes()).hexdigest()
    result["independent_power"] = {"total": power["total"], "source": power["source"]}
    target = snapshot / "validation/manual_ap_score_reference.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    contract_path = snapshot / "manual_score_contract.json"
    contract = _json(contract_path)
    contract.update({"status": "bounded_ap_skill_order_reference_computed; real_play_reproduction_unverified",
                     "required_before_prediction": [],
                     "bounded_reference": {"file": "validation/manual_ap_score_reference.json",
                                           "entry_point": "../manual_score_reference.py",
                                           "score_range": result["score_range"],
                                           "orders_evaluated": result["orders_evaluated"],
                                           "rank_counts": result["rank_counts"],
                                           "reward_summary": result["reward_summary"],
                                           "assumptions": result["assumptions"],
                                           "real_play_reproduced": False},
                     "reference_assumptions_to_declare": [
                         "Every pre-conversion judgement is Perfect; this does not establish the actual raw input sequence.",
                         "Every judgement uses its converted chart time, with factor commands applied once; actual frame replay is not reproduced.",
                         "All 120 member skill orders are evaluated; equal weights for reference means remain an unverified distribution assumption."],
                     "scope": result["scope"]})
    contract_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    comparison = result["observed_score_comparison"]
    if comparison["nearest_absolute_difference"] == 0:
        match_text = (f"有{comparison['exact_reference_matches']}种参考顺序得到完全相同的{observed['score']:,}分。"
                      "这说明当前参考模型可以解释这份样本；实际技能顺序和判定时间未记录，不能据此识别真实顺序或声称完整实机重放。")
    else:
        match_text = (f"最接近的参考结果仍差{comparison['nearest_absolute_difference']:,}分；"
                      "没有识别真实技能顺序或精确复现实际演出。")
    text = f"""Our Notes：手动 AP 得分参考

当前已核对队伍，综合力{power['total']['total']:,}，夢我夢中 EXPERT Lv26。
使用固定 bdon 数据、正式转换谱面和已记录的真实养成，不从实战分数调整模型。
759个判定音符，分数公式除数525；两者含义不同。

假定：全部音符转换前即为PERFECT、在谱面时刻判定、无辅助模式、关闭击奏。
Snap按原表提供技能延时；满生命时选择对应生命分支。
逐音符计算使用模型的32位浮点与逐音符取整。

枚举120种技能发动顺序：
最低参考分数：{result['score_range']['minimum']:,}。
最高参考分数：{result['score_range']['maximum']:,}。
等权平均：{result['score_range']['uniform_order_mean']:,.2f}（各顺序等概率为参考假设）。
评级分布：{result['rank_counts']}。
本次实机：{observed['score']:,}，是否落在参考区间：{comparison['within_reference_order_range']}。
{match_text}

每种顺序先判评级、再算奖励；不把平均分的评级当成平均收益。
选择{result['selection']['cp_selection']}CP、活动加成108%、商店加成140%：
活动PT参考范围：{result['reward_summary']['event_pt']['minimum']}～{result['reward_summary']['event_pt']['maximum']}。
商店PT参考范围：{result['reward_summary']['shop_pt']['minimum']}～{result['reward_summary']['shop_pt']['maximum']}。
挑战不获得CP。所选200CP与1倍奖励相符，实际扣减仍未直接观察。

边界：未模拟客户端完整帧处理和回算；未知实际判定时间和转换前输入。
落在区间仅说明当前参考假设与样本相容，不代表任意队伍都已通过得分校准。
公共配队程序仍需普通演出模型、完整持有卡库和搜索，再同时比较活动PT／商店PT最优方案。
"""
    (snapshot / "手动得分参考.txt").write_text(text, encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("status", "orders_evaluated", "score_range", "rank_counts", "reward_summary", "observed_score_comparison")}, ensure_ascii=False))


if __name__ == "__main__":
    main()

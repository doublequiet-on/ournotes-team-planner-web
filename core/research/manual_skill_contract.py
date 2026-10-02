"""Derive the selected live/Snap skills for a narrowly stated manual AP baseline.

Raw PERFECT judgements, initial life 1000, no assist and Gekisou off are required
assumptions of this research API. Life recovery can over-heal to 2000; it is not
discarded as though life stayed constant. Instead, every supported life predicate
must have the same answer across that whole interval, and the score life factor
only distinguishes positive life from zero. Unknown effects/conditions fail.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import struct


DATA_COMMIT = "0f8644dd85b7c3d21794fb3f718e48773cd1cc60"
MODEL_COMMIT = "dbd9cf01a4854808dceb6aedcd4041af372faeee"
TABLES = (
    "MasterMemberCard", "MasterSupportCard", "MasterSupportCardRank",
    "MasterLiveSkillEffect", "MasterSupportSkillEffect", "MasterSkillConditionSet",
    "MasterSkillCondition", "MasterSkillTarget", "MasterCharacter",
    "MasterLiveJudgementParameter", "MasterLiveSettings",
)
MODEL_PATHS = (
    "src/live/skill.rs", "src/live/score.rs", "src/live/full/mod.rs",
    "src/live/full/engine.rs", "src/live/full/conditions.rs",
    "src/live/full/life.rs", "src/live/full/convert.rs", "src/num.rs",
)
UNSUPPORTED_STATE_FIELDS = (
    "_skillReleaseConditionGroup", "_maxEffectValue", "_skillCumulativeConditionID",
    "_effectExecuteLimitCount", "_effectExecuteLimitResetConditionGroup",
)
BASE_LIFE = 1000
OVERHEAL_CAP = 2000
PERFECT = 5


def _integer(value: object, label: str, minimum: int = 0, maximum: int = (1 << 31) - 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} requires an actual integer in {minimum}..{maximum}")
    return value


class IndexedRows(tuple):
    """Verified run-local rows; compound indices retain every duplicate match."""
    def __new__(cls, rows):
        value = super().__new__(cls, rows)
        value.indices = {}
        return value

    def matching(self, criteria):
        fields = tuple(criteria)
        values = tuple(criteria[field] for field in fields)
        try:
            hash(values)
        except TypeError:
            return [row for row in self if all(row.get(k) == v for k, v in criteria.items())]
        if fields not in self.indices:
            index = {}
            for row in self:
                key = tuple(row.get(field) for field in fields)
                try:
                    hash(key)
                except TypeError:
                    return [r for r in self if all(r.get(k) == v for k, v in criteria.items())]
                index.setdefault(key, []).append(row)
            self.indices[fields] = index
        return self.indices[fields].get(values, [])


def _matching(rows, **criteria):
    if isinstance(rows, IndexedRows):
        return rows.matching(criteria)
    return [r for r in rows if all(r.get(key) == value for key, value in criteria.items())]


def _unique(rows: list[dict], label: str, **criteria) -> dict:
    matches = _matching(rows, **criteria)
    if len(matches) != 1:
        raise ValueError(f"{label}: expected one raw row, found {len(matches)}")
    return matches[0]


def _f32(value: int | float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _checked_bytes(snapshot: Path, entry: dict) -> bytes:
    path = (snapshot / entry["local_path"]).resolve()
    if not path.is_relative_to(snapshot):
        raise ValueError("Source path leaves snapshot")
    raw = path.read_bytes()
    if len(raw) != entry["bytes"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
        raise ValueError(f"Source SHA/size mismatch: {entry['local_path']}")
    blob = b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw
    if hashlib.sha1(blob).hexdigest() != entry["git_blob_sha"]:
        raise ValueError(f"Source Git blob mismatch: {entry['local_path']}")
    return raw


def _load(snapshot: Path) -> tuple[dict[str, list[dict]], list[dict]]:
    primary = json.loads((snapshot / "source_manifest.json").read_text(encoding="utf-8"))
    manual = json.loads((snapshot / "manual_model_source_manifest.json").read_text(encoding="utf-8"))
    if primary["data_commit"] != DATA_COMMIT or manual["model_commit"] != MODEL_COMMIT:
        raise ValueError("Skill contract requires the reviewed fixed Master/model versions")
    entries = {r["local_path"]: r for r in primary["files"] + manual["files"]}
    paths = [f"raw/{name}.json" for name in TABLES]
    paths += [f"model_reference/{name}" for name in MODEL_PATHS]
    tables, sources = {}, []
    for local_path in paths:
        entry = entries[local_path]
        expected_commit = DATA_COMMIT if local_path.startswith("raw/") else MODEL_COMMIT
        if entry["commit"] != expected_commit:
            raise ValueError(f"Unreviewed source commit: {local_path}")
        raw = _checked_bytes(snapshot, entry)
        if local_path.startswith("raw/"):
            rows = json.loads(raw)["_allData"]
            if len({r["_id"] for r in rows}) != len(rows):
                raise ValueError(f"Duplicate raw row IDs: {local_path}")
            tables[Path(local_path).stem] = IndexedRows(rows)
        sources.append({k: entry[k] for k in (
            "local_path", "repository", "commit", "git_blob_sha", "sha256")})
    return tables, sources


def _inventory(profile: dict, kind: str) -> dict[int, dict]:
    try:
        rows = profile["inventory"][kind]
    except (KeyError, TypeError):
        raise ValueError(f"Actual inventory.{kind} is required") from None
    if not isinstance(rows, list):
        raise ValueError(f"inventory.{kind} must be an array")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"inventory.{kind} entry is not an object")
        identifier = _integer(row.get("id"), f"inventory.{kind}.id", 1)
        if identifier in result:
            raise ValueError(f"Duplicate owned {kind} ID {identifier}")
        result[identifier] = row
    return result


def _selection(ids: list[int], owned: dict[int, dict], kind: str) -> None:
    if not isinstance(ids, list) or len(ids) != 5:
        raise ValueError(f"Exactly five explicitly selected {kind} are required")
    if len(set(ids)) != len(ids):
        raise ValueError(f"Duplicate selected {kind}")
    for identifier in ids:
        _integer(identifier, f"Selected {kind} ID", 1)
        if identifier not in owned:
            raise ValueError(f"Selected {kind} {identifier} is not owned")


def _band_target(target: dict, band_id: int) -> dict:
    # The full model uses an OR of member-target fields, not the power predicate.
    # This limited contract accepts the current pure band targets only.
    if (target["_skillTargetType"] != 3 or target["_bandID"] <= 0
            or any(target[key] != 0 for key in (
                "_characterID", "_cardType", "_tagID", "_liveMusicType", "_gekisouMissionType"))
            or target["_liveSkillCategories"] or target["_gekisouSkillCategories"]
            or target["_judgement"] != -1):
        raise ValueError(f"Unsupported member target {target['_id']}; only pure band targets are covered")
    return {"raw_target": target, "bound_member_band_id": band_id,
            "matched": target["_bandID"] == band_id,
            "basis": "full/mod.rs::Performer::matches_skill_target band field alternative"}


def _condition_group(tables: dict, group: int, band_id: int, purpose: str) -> dict:
    if group == 0:
        if purpose == "trigger":
            raise ValueError("Support trigger without an explicit own-member live event is unsupported")
        return {"group": 0, "matched": True, "sets": [], "basis": "No condition"}
    rows = _matching(tables["MasterSkillConditionSet"], _group=group)
    if not rows or any(not r["_conditionIds"] for r in rows):
        raise ValueError(f"Unsupported missing/empty condition group {group}")
    sets = []
    for row in rows:
        conditions = []
        for cid in row["_conditionIds"]:
            condition = _unique(tables["MasterSkillCondition"], f"condition {cid}", _id=cid)
            kind, values, tids = (condition["_conditionType"],
                                  condition["_conditionValues"], condition["_conditionTargetIDs"])
            positive = condition["_isPositive"]
            if type(positive) is not bool:
                raise ValueError(f"Condition {cid} polarity is not boolean")
            details = {}
            if kind == 4010 and purpose == "trigger":
                if values or tids or not positive:
                    raise ValueError(f"Unsupported same-member trigger condition {cid}")
                answer = True
                details = {"basis": "conditions.rs::SameMemberLiveSkill: event index equals this bound member",
                           "applies_only_at_bound_member_live_event": True}
            elif kind == 5000 and purpose != "trigger":
                if values or not tids:
                    raise ValueError(f"Unsupported member condition {cid}")
                matches = [_band_target(_unique(tables["MasterSkillTarget"], f"target {tid}", _id=tid), band_id)
                           for tid in tids]
                answer = any(t["matched"] for t in matches)
                details = {"member_targets": matches, "basis": "conditions.rs::Factory type5000: OR of bound performer targets"}
            elif kind in (2001, 2003) and purpose != "trigger":
                if len(values) != 1 or tids:
                    raise ValueError(f"Unsupported life condition {cid}")
                threshold = _integer(values[0], f"Condition {cid} life threshold")
                answers = [life >= threshold if kind == 2001 else life <= threshold
                           for life in (BASE_LIFE, OVERHEAL_CAP)]
                if answers[0] != answers[1]:
                    raise ValueError(f"Life condition {cid} changes during possible AP over-heal")
                answer = answers[0]
                details = {"life_test_interval": [BASE_LIFE, OVERHEAL_CAP],
                           "threshold": threshold, "interval_answer_is_constant": True,
                           "basis": "conditions.rs::LifeAtLeast/AtMost and polarity; life.rs recovery may over-heal"}
            else:
                raise ValueError(f"Unsupported condition type {kind} in {purpose} group {group}")
            matched = answer if positive else not answer
            conditions.append({"raw_condition": condition, "unnegated_matched": answer,
                               "matched": matched, **details})
        sets.append({"raw_condition_set": row,
                     "matched": all(c["matched"] for c in conditions), "conditions": conditions})
    return {"group": group, "matched": any(s["matched"] for s in sets), "sets": sets,
            "basis": "conditions.rs::Factory::group: OR of sets, each AND of conditions"}


def _check_state_fields(row: dict, allow_conversion_limit: bool = False) -> None:
    if any(row[field] != 0 for field in UNSUPPORTED_STATE_FIELDS):
        raise ValueError(f"Effect {row['_id']} has an unsupported release/cumulative/execute-limit state")
    limit = _integer(row["_effectLimitCount"], f"Effect {row['_id']} conversion limit")
    if limit and not allow_conversion_limit:
        raise ValueError(f"Effect {row['_id']} has an unsupported effect limit")


def _base_activation_ms(row: dict) -> float:
    seconds = row["_activationTimeSecond"]
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
        raise ValueError(f"Effect {row['_id']} activation duration is invalid")
    duration = _f32(_f32(seconds) * _f32(1000))
    if not 0 <= duration <= (1 << 31) - 1:
        raise ValueError(f"Effect {row['_id']} duration is outside the covered finite range")
    return duration


def derive_ap_skill_contract(snapshot: Path | str, profile: dict,
                             member_ids: list[int], snap_ids: list[int], *,
                             _verified_inputs: tuple | None = None) -> dict:
    """Return five bound slots' Master-derived AP effects and their raw proofs.

    `active_live_effects[*].activation_ms` is the basic duration, without Snap
    extension. `extension_ms` is separate, for the score caller to add exactly
    once. `live_effects[*].effective_activation_ms` includes that extension.
    This function reads no chart or actual replay and does not predict scores.
    """
    owned_members, owned_snaps = _inventory(profile, "members"), _inventory(profile, "snaps")
    _selection(member_ids, owned_members, "members")
    _selection(snap_ids, owned_snaps, "snaps")
    # Batch callers may reuse a snapshot that _load already verified in this run.
    # This private argument is never accepted from the HTTP/profile input.
    tables, sources = (_load(Path(snapshot).resolve()) if _verified_inputs is None
                       else _verified_inputs)
    base_life = _unique(tables["MasterLiveSettings"], "life_base", _key="life_base")
    perfect = _unique(tables["MasterLiveJudgementParameter"], "raw Perfect", _noteSimulateJudgement=PERFECT)
    if base_life["_value"] != str(BASE_LIFE) or perfect["_damage"] != 0:
        raise ValueError("Fixed positive-life AP baseline is not supported by this Master")
    slots = []
    for index, (member_id, snap_id) in enumerate(zip(member_ids, snap_ids)):
        member = _unique(tables["MasterMemberCard"], f"member {member_id}", _id=member_id)
        snap = _unique(tables["MasterSupportCard"], f"Snap {snap_id}", _id=snap_id)
        character = _unique(tables["MasterCharacter"], f"member {member_id} character", _id=member["_characterID"])
        band_id = character["_bandID"]
        skill_id = _integer(member["_liveSkillID"], f"member {member_id} live skill", 1)
        skill_level = _integer(owned_members[member_id].get("live_skill_level"),
                               f"member {member_id} actual live skill level", 1, 5)
        snap_owned = owned_snaps[snap_id]
        count = _integer(snap_owned.get("limit_break_count"), f"Snap {snap_id} actual limit break", 0, 4)
        rank = _unique(tables["MasterSupportCardRank"], f"Snap {snap_id} rank",
                       _group=snap["_supportCardRankGroup"], _rank=count + 1)
        snap_level = _integer(snap_owned.get("level"), f"Snap {snap_id} actual level", 1)
        if snap_level > rank["_limitLevel"]:
            raise ValueError(f"Snap {snap_id} level exceeds its actual limit-break cap")
        support_skills, extension = [], _f32(0)
        for suffix in ("01", "02"):
            support_id = snap[f"_supportSkillId{suffix}"]
            if support_id == 0:
                continue
            support_level = _integer(rank[f"_supportSkill{suffix}Level"],
                                     f"Snap {snap_id} support {suffix} level", 1, 5)
            rows = sorted(_matching(tables["MasterSupportSkillEffect"], _supportSkillID=support_id, _level=support_level),
                          key=lambda r: r["_id"])
            if not rows:
                raise ValueError(f"No raw effects for Snap {snap_id} support {support_id}/{support_level}")
            checks = []
            for row in rows:
                kind = row["_skillEffectType"]
                if kind not in (15000, 3001, 12006):
                    raise ValueError(f"Unsupported support effect type {kind} in row {row['_id']}")
                _check_state_fields(row, allow_conversion_limit=kind == 12006)
                if row["_skillTriggerType"] != 1:
                    raise ValueError(f"Support effect {row['_id']} is not the covered one-shot trigger")
                value = _integer(row["_effectValue"], f"Effect {row['_id']} value")
                duration = _base_activation_ms(row)
                trigger = _condition_group(tables, row["_skillTriggerConditionGroup"], band_id, "trigger")
                condition = _condition_group(tables, row["_skillConditionGroup"], band_id, "support")
                applicable = trigger["matched"] and condition["matched"]
                targets = []
                if kind in (15000, 3001):
                    if row["_skillTargetIDs"] or duration != 0:
                        raise ValueError(f"Unsupported timed/targeted support row {row['_id']}")
                    if kind == 15000 and applicable:
                        extension = _f32(extension + _f32(value))
                    simplification = (
                        "Extend this bound member's running live effects only; milliseconds are added in float32"
                        if kind == 15000 else
                        "Recovery allows over-heal to 2000; raw Perfect has zero damage. Life stays positive and all supported life predicates are invariant on 1000..2000, so score factors do not change")
                else:
                    if value != PERFECT or duration <= 0 or not row["_skillTargetIDs"]:
                        raise ValueError(f"Unsupported conversion destination/window in row {row['_id']}")
                    for tid in row["_skillTargetIDs"]:
                        target = _unique(tables["MasterSkillTarget"], f"conversion target {tid}", _id=tid)
                        if target["_skillTargetType"] != 4 or target["_judgement"] not in (3, 4):
                            raise ValueError(f"Conversion row {row['_id']} has a target outside raw Good/Great")
                        targets.append(target)
                    simplification = "Only raw Good/Great targets convert to Perfect; every raw judgement in this AP baseline is already Perfect, so no conversion or limit consumption occurs"
                checks.append({"effect_row_id": row["_id"], "skill_effect_type": kind,
                               "raw_effect": row, "applicable": applicable,
                               "trigger_proof": trigger, "condition_proof": condition,
                               "raw_targets": targets, "ap_simplification": simplification})
            support_skills.append({"support_skill_id": support_id, "support_skill_level": support_level,
                                   "effect_checks": checks})
        rows = sorted(_matching(tables["MasterLiveSkillEffect"], _liveSkillID=skill_id, _level=skill_level), key=lambda r: r["_id"])
        if not rows:
            raise ValueError(f"No raw live effects for member {member_id}: {skill_id}/{skill_level}")
        active, live_checks = [], []
        for row in rows:
            kind = row["_skillEffectType"]
            if kind not in (2000, 2004):
                raise ValueError(f"Unsupported live effect type {row['_skillEffectType']} in row {row['_id']}")
            _check_state_fields(row)
            if kind == 2000 and row["_skillTargetIDs"]:
                raise ValueError(f"Unsupported targeted live score effect {row['_id']}")
            targets = []
            if kind == 2004:
                if not row["_skillTargetIDs"]:
                    raise ValueError(f"Judgement score effect {row['_id']} requires explicit targets")
                for tid in row["_skillTargetIDs"]:
                    target = _unique(tables["MasterSkillTarget"], f"judgement target {tid}", _id=tid)
                    if target["_skillTargetType"] != 4 or target["_judgement"] not in (3, 4, 5, 6):
                        raise ValueError(f"Unsupported judgement score target {tid} in row {row['_id']}")
                    targets.append(target)
            value = _integer(row["_effectValue"], f"Live effect {row['_id']} value")
            duration = _base_activation_ms(row)
            if duration <= 0:
                raise ValueError(f"Live score effect {row['_id']} has no positive window")
            proof = _condition_group(tables, row["_skillConditionGroup"], band_id, "live")
            live_checks.append({"effect_row_id": row["_id"], "raw_effect": row,
                                "applicable": proof["matched"], "condition_proof": proof,
                                "raw_targets": targets,
                                "ap_target_count": sum(t["_judgement"] == PERFECT for t in targets),
                                "target_basis": "skill.rs::apply_factor: separate PERFECT accumulator, raw AP judgement=5"})
            if proof["matched"]:
                active.append({"effect_row_id": row["_id"], "skill_effect_type": kind,
                               "effect_value": value, "activation_ms": duration,
                               **({"judgement_targets": [t["_judgement"] for t in targets]} if kind == 2004 else {})})
        if not active:
            raise ValueError(f"Member {member_id} has no active supported AP score effect")
        slots.append({
            "slot_index": index, "member_id": member_id, "snap_id": snap_id,
            "character_id": character["_id"], "band_id": band_id,
            "live_skill_id": skill_id, "live_skill_level": skill_level,
            "extension_ms": extension, "active_live_effects": active,
            "live_effects": [{**r, "effective_activation_ms": math.ceil(_f32(r["activation_ms"] + extension))}
                             for r in active],
            "live_effect_checks": live_checks, "support_skills": support_skills,
            "raw_member_card": member, "raw_snap_card": snap, "raw_snap_rank": rank,
        })
    return {
        "scope": "Selected AP skill contract only; no score, frame, random-order or replay prediction",
        "baseline": {"initial_life": BASE_LIFE, "possible_life_interval": [BASE_LIFE, OVERHEAL_CAP],
                     "life_is_constant": False, "raw_judgement": PERFECT,
                     "raw_judgement_name": "PERFECT", "gekisou_enabled": False, "assist": False,
                     "raw_life_base": base_life, "raw_perfect_parameter": perfect,
                     "score_life_factor_proof": "score.rs::note_score_core uses 1 when current_life > 0; 3001 over-heal changes life but not this factor"},
        "data_commit": DATA_COMMIT, "model_commit": MODEL_COMMIT,
        "sources": sources, "slots": slots,
    }

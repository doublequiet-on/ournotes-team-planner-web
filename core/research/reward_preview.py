"""Local research previews, not calibrated score predictions or server awards."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RewardContext:
    snapshot: Path
    event: dict
    commit: str


MODEL_URL = "https://github.com/empty-sekai/ournotes-deck/blob/dbd9cf01a4854808dceb6aedcd4041af372faeee/src/event.rs"
INT32_MAX = (1 << 31) - 1


def _integer(value: int, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _amount(ordered_factors: list[int]) -> int:
    product = 1
    for factor in ordered_factors:
        product *= factor
        if product > INT32_MAX:
            raise ValueError("Reference-model integer range exceeded; server behavior has not been calibrated")
    return product // 10000


def _event(snapshot_dir: Path, event_id: int) -> tuple[dict, str]:
    _integer(event_id, "event_id", 1)
    event = json.loads((Path(snapshot_dir) / "normalized" / f"event_{event_id}.json").read_bytes())
    manifest = json.loads((Path(snapshot_dir) / "source_manifest.json").read_bytes())
    if event["event_id"] != event_id:
        raise ValueError("Normalized event ID mismatch")
    return event, manifest["data_commit"]


def _preview(snapshot_dir: Path, event_id: int, rank: str, consumed: int,
             event_pt_bonus_10000: int, shop_pt_bonus_10000: int, challenge: bool, _context=None) -> dict:
    _integer(consumed, "consumption", 1)
    _integer(event_pt_bonus_10000, "event_pt_bonus_10000")
    _integer(shop_pt_bonus_10000, "shop_pt_bonus_10000")
    if _context is None:
        event, commit = _event(snapshot_dir, event_id)
    else:
        _integer(event_id, "event_id", 1)
        if (type(_context) is not RewardContext or _context.snapshot != Path(snapshot_dir).resolve()
                or _context.event["event_id"] != event_id):
            raise ValueError("Invalid internal reward context")
        event, commit = _context.event, _context.commit
    reward_key = "challenge_base_rewards" if challenge else "ordinary_base_rewards"
    base = next((row for row in event[reward_key] if row["rank"] == rank), None)
    if base is None:
        raise ValueError(f"No exact reward base for rank {rank!r}")
    rates_key = "challenge_cp_consumption_rows_raw" if challenge else "ordinary_boost_rows_raw"
    consumed_key = "_consumedChallengePointCount" if challenge else "_consumedLiveBoostCount"
    rates = next((row for row in event[rates_key] if row[consumed_key] == consumed), None)
    if rates is None:
        raise ValueError("Consumption must have a raw Boost/CP row; zero Boost is not yet calibrated")
    shop_rows = base["shop_reward_rows"]
    if not shop_rows or any(row["_probability"] != 10000 for row in shop_rows):
        raise ValueError("Random reward selection is not supported by this guaranteed-reward preview")
    point_factors = ([base["event_pt_base"], rates["_eventPointRate"], 10000 + event_pt_bonus_10000]
                     if challenge else
                     [10000 + event_pt_bonus_10000, rates["_eventPointRate"], base["event_pt_base"]])
    shop_parts = [{"reward_row_id": row["_id"], "base": row["_resourceCount"],
                   "amount": _amount([row["_resourceCount"], 10000 + shop_pt_bonus_10000, rates["_liveMusicRewardRate"]])}
                  for row in shop_rows]
    cp_gained = 0 if challenge else base["cp_base"] * rates["_eventPointRate"]
    return {
        "event_id": event_id, "data_commit": commit,
        "mode": "challenge" if challenge else "ordinary",
        "rank_given_by_caller": rank,
        "consumed": {"boost": 0 if challenge else consumed, "cp": consumed if challenge else 0},
        "gained": {"cp": cp_gained, "event_pt": _amount(point_factors), "shop_pt": sum(row["amount"] for row in shop_parts)},
        "cp_delta": -consumed if challenge else cp_gained,
        "exact_bonuses_10000": {"event_pt": event_pt_bonus_10000, "shop_pt": shop_pt_bonus_10000},
        "multipliers": {"event_pt_and_cp": rates["_eventPointRate"], "shop_pt": rates["_liveMusicRewardRate"]},
        "shop_reward_parts": shop_parts,
        "validation": {
            "status": "bdon_linked_client_model_preview",
            "source": MODEL_URL + "#L315",
            "scope": "Exact raw reward bases and bonus integers; client-reference arithmetic within its signed int32 range. One ordinary receipt and one known-party B challenge receipt at +108%/+140% with a 1x rate match. Other ranks/rates and fractional rounding order are not independently server-verified. Rank is supplied by the caller; this preview does not predict it.",
            "predicts_score_or_rank": False,
            "checks_owned_deck_or_cp_balance": False,
        },
    }


def preview_normal_rewards(snapshot_dir: Path, rank: str, boost: int,
                           event_pt_bonus_10000: int, shop_pt_bonus_10000: int,
                           event_id: int = 1, *, _context=None) -> dict:
    return _preview(snapshot_dir, event_id, rank, boost, event_pt_bonus_10000, shop_pt_bonus_10000, False, _context)


def preview_challenge_rewards(snapshot_dir: Path, rank: str, cp_consumed: int,
                              event_pt_bonus_10000: int, shop_pt_bonus_10000: int,
                              event_id: int = 1, *, _context=None) -> dict:
    return _preview(snapshot_dir, event_id, rank, cp_consumed, event_pt_bonus_10000, shop_pt_bonus_10000, True, _context)


def preview_reward_cycle(normal: dict, challenge: dict, normal_plays: int, starting_cp: int) -> dict:
    """Repeat explicit rank/reward assumptions and spend all affordable challenge batches."""
    _integer(normal_plays, "normal_plays")
    _integer(starting_cp, "starting_cp")
    if normal["mode"] != "ordinary" or challenge["mode"] != "challenge":
        raise ValueError("The two previews must be ordinary and challenge respectively")
    if (normal["event_id"], normal["data_commit"]) != (challenge["event_id"], challenge["data_commit"]):
        raise ValueError("Cycle previews must use the same event and data snapshot")
    spent_each = _integer(challenge["consumed"]["cp"], "challenge_cp", 1)
    earned_cp = normal["gained"]["cp"] * normal_plays
    challenge_plays, remaining_cp = divmod(starting_cp + earned_cp, spent_each)
    return {
        "event_id": normal["event_id"], "data_commit": normal["data_commit"],
        "starting_cp": starting_cp, "normal_plays": normal_plays, "challenge_plays": challenge_plays,
        "boost_consumed": normal["consumed"]["boost"] * normal_plays,
        "cp_gained": earned_cp, "cp_spent": spent_each * challenge_plays, "cp_remaining": remaining_cp,
        "total_event_pt": normal["gained"]["event_pt"] * normal_plays + challenge["gained"]["event_pt"] * challenge_plays,
        "total_shop_pt": normal["gained"]["shop_pt"] * normal_plays + challenge["gained"]["shop_pt"] * challenge_plays,
        "validation": "Deterministic research scenario using the two supplied reward previews; distinct normal/challenge bonuses are allowed. It assumes every play attains the given ranks, no CP cap/expiry/other sources, and all affordable challenge batches are played. This is not a best-team recommendation.",
    }

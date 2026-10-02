"""Measure the production route with isolated, explicitly synthetic growth."""
from collections import Counter
import copy
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import planner_core as p
import solver_search as ss
from test_search_optimization import expanded_request


def full_request(data, maximum=False):
    request = copy.deepcopy(p.demo_profile())
    catalog = data.catalog()
    rank = max(r["_rank"] for r in data.tables["MasterCharacterRank"]) if maximum else 5
    request["profile"] = {"schema_version": 1, "inventory": {
        "members": [{"id": c["id"], "level": c["caps"][4] if maximum else min(20+c["id"]*7 % 17, c["caps"][0]),
                     "training_count": 4 if maximum else 0, "awakening_count": 4 if maximum else 0} for c in catalog["members"]],
        "snaps": [{"id": c["id"], "level": c["caps"][4] if maximum else min(20+c["id"]*3 % 17, c["caps"][0]),
                   "limit_break_count": 4 if maximum else 0} for c in catalog["snaps"]]},
        "facilities": [{"id": c["id"], "level": c["max_level"] if maximum else min(3, c["max_level"])} for c in catalog["facilities"]],
        "character_ranks": [{"character_id": c["id"], "rank": rank} for c in catalog["characters"]],
        "character_total_rank": len(catalog["characters"])*rank, "tgw_card_rank": 21 if maximum else 1}
    request["candidate_member_ids"] = [c["id"] for c in catalog["members"]]
    request["candidate_snap_ids"] = [c["id"] for c in catalog["snaps"]]
    for mode in ("normal", "challenge"):
        request["settings"][mode].update(method="skip", sheets=[{"song_id": 100109, "difficulty": "expert"}])
    return request


def plan_key(row, objective):
    other = "shop_pt" if objective == "event_pt" else "event_pt"
    return (row["totals"][objective], row["totals"][other], row["totals"]["cp_remaining"],
            row["normal"]["power"], row["challenge"]["power"], -row["normal"]["song_id"], -row["challenge"]["song_id"])


def main():
    data, reports = p.Data(), []
    path = ROOT / "validation/solver-release-benchmark.json"
    request = expanded_request(data, 10)
    variants = {}
    for use_solver in (True, False):
        start = time.monotonic()
        with patch.object(ss, "should_use_solver", return_value=use_solver):
            result = p.optimize(request, data=data)
        variants["solver" if use_solver else "matching"] = {
            "seconds": time.monotonic()-start, "keys": {o: plan_key(result["plans"][o], o) for o in result["plans"]}}
    assert variants["solver"]["keys"] == variants["matching"]["keys"]
    reports.append({"case": "AP 5 members / 10 Snaps", "synthetic_extra_growth": True, "comparison": variants})
    for maximum in (False, True):
        request = full_request(data, maximum)
        begin = time.monotonic()
        result = p.optimize(request, data=data)
        assert result["search"]["optimality_proven"]
        assert result["search"]["algorithm"] == "adaptive_cp_sat_with_exact_score_oracle"
        reports.append({"case": "full 63 members / 64 Snaps", "synthetic_growth": "maximum" if maximum else "mixed_low",
                        "seconds": time.monotonic()-begin, "result": result})
        path.write_text(json.dumps({"passed": True, "version": p.VERSION, "cases": reports}, ensure_ascii=False, indent=2), "utf-8")
        print("Production full pool proved", reports[-1]["synthetic_growth"], round(reports[-1]["seconds"], 2), flush=True)


if __name__ == "__main__":
    main()

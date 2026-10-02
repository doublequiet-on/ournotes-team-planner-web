"""Recompute browser-selected decks' mode-specific power and scores."""
from pathlib import Path
import json
import sys
from baseline import load_baseline

DEST = Path(sys.argv[1]).resolve()
p = load_baseline(DEST / "native-score-oracle")
data = p.Data()
seen = {}
checks = 0
for name in ("sample-ap", "all-expert-skip", "solver-ap", "solver-mixed-rounding", "solver-judgement-ap"):
    output = json.loads((DEST / f"{name}-browser.json").read_text("utf-8"))
    request, result = output["actual_inputs"], output["result"]
    assert result["version"] == p.VERSION
    for objective in ("event_pt", "shop_pt"):
        for mode in ("normal", "challenge"):
            for plan in result["top3"][objective][mode]:
                row = plan[mode]
                method = request["settings"][mode]["method"]
                key = json.dumps([name, mode, row], sort_keys=True)
                if key in seen:
                    continue
                spec = {"song_id": row["song_id"], "difficulty": row["difficulty"], "method": method}
                model = p.PowerModel(data, request["profile"], row["member_ids"], row["snap_ids"])
                model.music(row["song_id"], mode == "challenge")
                leader = row["leader_member_id"]
                rates = p.leader_rates(data, model.cards[leader], model.own[leader],
                    [model.cards[m] for m in row["member_ids"]], [model.chars[m] for m in row["member_ids"]], model.music_type)
                vectors = [p.dp._mul_floor(model.b[m], rate) for m, rate in zip(row["member_ids"], rates)]
                power = model.total(row["member_ids"], row["snap_ids"], vectors)
                if power != row["power"]:
                    raise AssertionError(f"Browser power differs for live mode: {name}/{objective}/{mode}")
                scorer = p.Scores(data, spec, mode == "challenge")
                slots = None
                if method == "ap":
                    slots = p.sk.derive_ap_skill_contract(data.snapshot, request["profile"], row["member_ids"],
                        row["snap_ids"], _verified_inputs=data.skill_inputs)["slots"]
                native = scorer.evaluate(power, slots)
                if native != row["score"]:
                    raise AssertionError(f"Browser score differs from native oracle: {name}/{objective}/{mode}")
                seen[key] = True
                checks += 1
(DEST / "score-oracle-report.json").write_text(json.dumps({"passed": True, "core_version": p.VERSION,
    "unique_reported_decks_checked": checks, "mode_specific_power_recomputed": True,
    "includes_judgement_target_type_2004": True}, indent=2), "utf-8")
print(f"Native recomputation agrees with {checks} browser deck scores", flush=True)

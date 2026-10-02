"""v0.2.4 judgement-target AP skill, using only synthetic growth values."""
from pathlib import Path
import json
import sys
from baseline import load_baseline

DEST = Path(sys.argv[1]).resolve()
p = load_baseline(DEST / "judgement-oracle")
from test_search_optimization import expanded_request
data = p.Data()
request = expanded_request(data, 10)
request["name"] = "2004 判定目标技能验收（测试养成）"
request["candidate_member_ids"] = [59, 11, 55, 26, 28]
request["profile"]["inventory"]["members"] = [row for row in request["profile"]["inventory"]["members"] if row["id"] != 3]
request["profile"]["inventory"]["members"].append({"id": 28, "level": 20, "training_count": 0,
    "awakening_count": 0, "live_skill_level": 3, "gekisou_skill_level": 1})
assert p.growth_issues(request, data)["complete"]
result = p.optimize(request, data=data)
assert result["search"]["optimality_proven"]
(DEST / "solver-judgement-ap.json").write_text(json.dumps({"request": request, "expected": result}, ensure_ascii=False), "utf-8")
print("v0.2.4 judgement-target AP oracle ready", flush=True)

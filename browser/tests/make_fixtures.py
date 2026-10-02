"""Write private oracle fixtures to work/, never into the published website."""
from pathlib import Path
import copy
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
DEST = Path(sys.argv[1]).resolve()
DEST.mkdir(parents=True, exist_ok=True)
from baseline import load_baseline
p = load_baseline(DEST)
from test_search_optimization import expanded_request
data = p.Data()
cases = []
sample = p.demo_profile()
skip = copy.deepcopy(sample)
for mode in ("normal", "challenge"):
    skip["settings"][mode]["method"] = "skip"
skip["settings"]["normal"]["sheets"] = [{"song_id": song["id"], "difficulty": "expert"} for song in data.catalog()["songs"]]
cases.extend([("sample-ap", sample), ("all-expert-skip", skip)])
large = expanded_request(data, 10)
cases.append(("solver-ap", large))
rounding = copy.deepcopy(large)
rounding["settings"].update(boost_budget=17, starting_cp=199, boost_per_live=4, challenge_cp=200)
rounding["settings"]["normal"]["method"] = "skip"
cases.append(("solver-mixed-rounding", rounding))
summary = []
for name, request in cases:
    begin = time.monotonic()
    result = p.optimize(request, data=data)
    (DEST / f"{name}.json").write_text(json.dumps({"request": request, "expected": result}, ensure_ascii=False), "utf-8")
    summary.append({"case": name, "native_seconds": round(time.monotonic() - begin, 3), "algorithm": result["search"].get("algorithm", "matching")})
    print(summary[-1], flush=True)
(DEST / "native-report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")

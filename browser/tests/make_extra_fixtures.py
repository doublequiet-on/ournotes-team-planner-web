"""Private desktop oracles for browser resume and full catalog acceptance."""
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
from benchmark_solver_release import full_request

data = p.Data()
multi = expanded_request(data, 10)
for mode in ("normal", "challenge"):
    multi["settings"][mode]["sheets"] = [{"song_id": sid, "difficulty": "expert"} for sid in (100056, 100063, 100109)]
for name, request in (("solver-top3-resume", multi), ("full-catalog-skip", full_request(data))):
    begin = time.monotonic()
    result = p.optimize(request, data=data)
    (DEST / f"{name}.json").write_text(json.dumps({"request": request, "expected": result}, ensure_ascii=False), "utf-8")
    print({"case": name, "native_seconds": round(time.monotonic() - begin, 3),
           "members": len(request["candidate_member_ids"]), "snaps": len(request["candidate_snap_ids"])}, flush=True)

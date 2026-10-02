"""Serial old/new preparation comparison with identical browser model protos."""
from pathlib import Path
import argparse
import copy
import hashlib
import importlib.util
import json
import statistics
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--child", choices=["baseline", "optimized"])
parser.add_argument("--rounds", type=int, default=5)
args = parser.parse_args()
destination = ROOT / "work/performance"
destination.mkdir(parents=True, exist_ok=True)

if args.child:
    sys.path.insert(0, str(ROOT / "browser/tests"))
    from baseline import load_baseline
    p = load_baseline(destination / args.child, optimized=args.child == "optimized")
    import solver_search as ss
    from test_search_optimization import expanded_request
    data = p.Data()
    request = expanded_request(data, 10)
    request["settings"]["normal"]["sheets"] = [{"song_id": s["id"], "difficulty": "expert"} for s in data.catalog()["songs"]]
    request["settings"]["challenge"]["sheets"] = [{"song_id": i, "difficulty": "expert"} for i in [100056, 100063, 100109]]
    specs = {mode: p._song_specs(request["settings"], mode, data) for mode in ss.MODES}
    model = p.PowerModel(data, request["profile"], request["candidate_member_ids"], request["candidate_snap_ids"])
    search = ss.Search(request, data, model, specs, 1, lambda **kw: None, lambda: False, None, None, time.monotonic())
    spec = importlib.util.spec_from_file_location("browser_cp_benchmark", ROOT / "browser/cp_model.py")
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    if args.child == "baseline":
        # Original builder semantics, not the new single-pass helper.
        adapter.LinearExpr.sum = staticmethod(lambda values: sum(values))
    search.cp = adapter
    with patch.object(p.ms, "_checked_inputs", wraps=p.ms._checked_inputs) as check:
        begin = time.perf_counter()
        search.build()
        elapsed = time.perf_counter() - begin
        checks = check.call_count
    proto = json.dumps(search.base.model, sort_keys=True, separators=(",", ":"))
    print(json.dumps({"variant": args.child, "seconds": elapsed, "base_table_checks": checks,
                      "model_sha256": hashlib.sha256(proto.encode()).hexdigest(),
                      "variables": len(search.base.model["variables"]), "constraints": len(search.base.model["constraints"]),
                      "scope": "CPython preparation with browser modeling adapter; no WASM solving or IndexedDB"}))
else:
    samples = []
    for round_index in range(args.rounds):
        for variant in (["baseline", "optimized"] if round_index % 2 == 0 else ["optimized", "baseline"]):
            raw = subprocess.check_output([sys.executable, "-B", __file__, "--child", variant], cwd=ROOT, text=True)
            row = json.loads(raw)
            samples.append(row)
            print(json.dumps(row), flush=True)
    if len({row["model_sha256"] for row in samples}) != 1:
        raise RuntimeError("Old/new models differ")
    report = {"passed": True, "rounds": args.rounds, "samples": samples,
              "medians": {variant: statistics.median(r["seconds"] for r in samples if r["variant"] == variant)
                          for variant in ["baseline", "optimized"]}}
    (destination / "preparation.json").write_text(json.dumps(report, indent=2), "utf-8")
    print(json.dumps(report["medians"]), flush=True)

"""Run the unchanged planner oracle in a private browser Python worker."""
import hashlib
import json
from pathlib import Path
import sys
import types
import time
import sqlite3

import cp_model as browser_cp
from js import browser_cancelled, browser_progress, browser_persist

for name in ("ortools", "ortools.sat", "ortools.sat.python"):
    module = types.ModuleType(name)
    module.__path__ = []
    sys.modules[name] = module
sys.modules["ortools.sat.python.cp_model"] = browser_cp
sys.modules["ortools.sat.python"].cp_model = browser_cp

import planner_core as p
import solver_search as ss
from search_cache import SearchCache, digest


class BrowserData(p.Data):
    def fingerprint(self):
        # Different adapters/solver builds cannot reuse each other's proofs.
        native = super().fingerprint()
        bridge = [hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  hashlib.sha256(Path(browser_cp.__file__).read_bytes()).hexdigest()]
        return digest({"native": native, "browser": bridge, "solver": "or-tools-wasm-0.9.1"})


class BrowserCache(SearchCache):
    def put(self, key, rows, visited, **metadata):
        saved = super().put(key, rows, visited, **metadata)
        if saved:
            browser_persist(bool(metadata.get("proven")))
        return saved

    def checkpoint(self, request, **metadata):
        super().checkpoint(request, **metadata)
        progress = (metadata.get("completed_steps", 0), metadata.get("completed_sheets", 0))
        force = progress != getattr(self, "_last_saved_progress", None)
        self._last_saved_progress = progress
        browser_persist(force)

    def mark_status(self, job_id, status):
        super().mark_status(job_id, status)
        browser_persist(True)


def native_solve(self, cm):
    """Same strict proof contract; browser workers provide cancellation/compute."""
    self.check()
    self.add_hint(cm)
    self.save()
    solver = self.cp.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 19471
    with self.metrics.measure("solver_bridge"):
        status = solver.solve(cm)
    self.metrics.record_solver(self.solve_context, status=int(status), **solver.diagnostics)
    for name, milliseconds in solver.diagnostics["worker_timings"].items():
        self.metrics.seconds["wasm_" + name.removesuffix("_ms")] += milliseconds / 1000
        self.metrics.calls["wasm_" + name.removesuffix("_ms")] += 1
    for name in ("model_json_bytes", "protobuf_bytes", "variables", "constraints"):
        self.metrics.count("solver_total_" + name, solver.diagnostics[name])
    self.check()
    self.stats["solver_calls"] += 1
    if status != self.cp.OPTIMAL:
        raise p.InputError("浏览器求解器未能完成最优证明，已完成步骤会保留。请恢复输入继续计算。")
    self.hint = self.selected_hint(solver)
    return solver


ss.Search.native_solve = native_solve
data = BrowserData()
cache = BrowserCache("/state")


def restore_cache():
    """Discard an unreadable computation cache, preserving the UI's inventory."""
    global cache
    try:
        restored = BrowserCache("/state")
        with restored.connection() as db:
            if db.execute("PRAGMA quick_check(1)").fetchall() != [("ok",)]:
                raise sqlite3.DatabaseError("Invalid resume cache")
        cache = restored
        return False
    except sqlite3.DatabaseError:
        for name in ("search-v1.sqlite3", "search-v1.sqlite3-journal"):
            (Path("/state") / name).unlink(missing_ok=True)
        cache = BrowserCache("/state")
        return True


def bootstrap():
    checkpoint = cache.last_checkpoint()
    if checkpoint:
        checkpoint["active_job_id"] = None
        if checkpoint["status"] == "complete":
            checkpoint = None
        elif checkpoint["status"] == "running":
            checkpoint["status"] = "interrupted"
    catalog = data.catalog()
    for kind in ("members", "snaps"):
        for card in catalog[kind]:
            if isinstance(card.get("thumbnail"), str):
                card["thumbnail"] = card["thumbnail"].lstrip("/")
    return {"catalog": catalog, "demo": p.demo_profile(), "calibration": p.calibration(data),
            "token": "browser-local", "resume": checkpoint}


def invoke(method, raw, job_id=None):
    body = json.loads(raw) if raw else {}
    if method == "bootstrap":
        return json.dumps(bootstrap(), ensure_ascii=False)
    if method == "check-growth":
        return json.dumps(p.growth_issues(body, data), ensure_ascii=False)
    if method != "optimize":
        raise p.InputError("找不到此操作。")
    begin = time.monotonic()

    def progress(**changes):
        changes.setdefault("elapsed_seconds", round(time.monotonic() - begin, 1))
        browser_progress(json.dumps(changes, ensure_ascii=False))

    try:
        result = p.optimize(body, progress=progress, cancelled=lambda: bool(browser_cancelled()),
                            data=data, cache=cache, run_id=job_id)
        if browser_cancelled():
            raise p.Cancelled()
        cache.mark_status(job_id, "complete")
        return json.dumps({"status": "complete", "result": result}, ensure_ascii=False, allow_nan=False)
    except p.Cancelled:
        cache.mark_status(job_id, "cancelled")
        return json.dumps({"status": "cancelled"})
    except Exception:
        cache.mark_status(job_id, "error")
        raise

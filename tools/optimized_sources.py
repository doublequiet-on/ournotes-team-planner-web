"""Explicit, reviewable production overrides of the immutable model archive."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = ("planner_core.py", "solver_search.py", "score_bounds.py", "score_intervals.py",
                "performance_trace.py", "research/manual_score_reference.py", "research/reward_preview.py",
                "research/manual_skill_contract.py")


def sources():
    directory = ROOT / "core"
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob("*.py")}
    if actual != set(SOURCE_FILES):
        raise ValueError(f"Unexpected or missing production overrides: {actual ^ set(SOURCE_FILES)}")
    return {name: (directory / name).read_bytes() for name in SOURCE_FILES}

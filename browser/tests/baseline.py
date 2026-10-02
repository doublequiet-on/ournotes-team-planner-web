"""Keep desktop oracle generation on the same immutable public source as web."""
from pathlib import Path
import hashlib
import json
import sys
import zipfile


def load_baseline(destination):
    here = Path(__file__).resolve().parents[1]
    config = json.loads((here / "upstream.json").read_text("utf-8"))
    source = here / "upstream" / config["archive"]
    if not source.is_file():
        source = here.parent / config["archive"]
    if hashlib.sha256(source.read_bytes()).hexdigest() != config["sha256"]:
        raise ValueError("Oracle public baseline archive changed")
    native = Path(destination).resolve() / "native-baseline"
    native.mkdir(parents=True, exist_ok=True)
    prefix = f"OurNotes-配队程序-v{config['version']}/"
    with zipfile.ZipFile(source) as archive:
        for info in archive.infolist():
            relative = info.filename.removeprefix(prefix)
            if relative == info.filename or ".." in Path(relative).parts:
                raise ValueError("Invalid baseline path")
            if relative in ("planner_core.py", "solver_search.py", "score_bounds.py", "search_cache.py") or relative.startswith("research/"):
                target = native / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(info))
    sys.path[:0] = [str(native), str(here.parent / "tests")]
    import planner_core as p
    if p.VERSION != config["version"]:
        raise ValueError("Wrong oracle module loaded")
    # Public synthetic values: source-package tests require no owner's profile.
    data = p.Data()
    catalog = data.catalog()
    request = p.demo_profile()
    mids, sids = [59, 11, 55, 26, 3], [33, 37, 52, 61, 3]
    by_mid = {c["id"]: c for c in catalog["members"]}
    by_sid = {c["id"]: c for c in catalog["snaps"]}
    request.update(name="浏览器验证卡库（仅含测试养成）", is_demo=False,
                   candidate_member_ids=mids, candidate_snap_ids=sids)
    request["profile"] = {"schema_version": 1, "inventory": {
        "members": [{"id": i, "level": min(20, by_mid[i]["caps"][0]), "training_count": 0,
                     "awakening_count": 0, "live_skill_level": 1, "gekisou_skill_level": 1} for i in mids],
        "snaps": [{"id": i, "level": min(20, by_sid[i]["caps"][0]), "limit_break_count": 0} for i in sids]},
        "character_ranks": [{"character_id": c["id"], "rank": 5} for c in catalog["characters"]],
        "character_total_rank": 5 * len(catalog["characters"]), "tgw_card_rank": 1,
        "facilities": [{"id": c["id"], "level": min(3, c["max_level"])} for c in catalog["facilities"]]}
    from copy import deepcopy
    p.demo_profile = lambda: deepcopy(request)
    return p

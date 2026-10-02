"""Independent reference comparisons for interval, summary and cache changes."""
import ast
from collections import Counter
import copy
import itertools
import json
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch
import zipfile
import tempfile

import planner_core as p
import score_intervals
from score_bounds import power_thresholds

ROOT = Path(__file__).resolve().parents[1]


class IntervalTests(unittest.TestCase):
    def test_original_arithmetic_and_scan_oracles_are_unchanged(self):
        config = json.loads((ROOT / "browser/upstream.json").read_text("utf-8"))
        with zipfile.ZipFile(ROOT / "browser/upstream" / config["archive"]) as archive:
            raw = archive.read(f"OurNotes-配队程序-v{config['version']}/research/manual_score_reference.py")
        original = {node.name: node for node in ast.parse(raw).body if isinstance(node, ast.FunctionDef)}
        current = {node.name: node for node in ast.parse((ROOT / "core/research/manual_score_reference.py").read_text("utf-8")).body
                   if isinstance(node, ast.FunctionDef)}
        for name in ["note_score", "factor_commands", "ap_factor_samples", "score_order", "rank_for_score"]:
            self.assertEqual(ast.dump(original[name]), ast.dump(current[name]), name)

    def test_all_orders_match_original_scan_with_overlaps_ties_and_two_channels(self):
        rng = random.Random(21002)
        for case in range(12):
            times = [0, 30, 60, 100, 150]
            if case % 2:
                times.reverse()  # original event index remains independent of time
            note_times = sorted([0, 30, 40, 60, 100, 150, 200, 249] + [rng.randrange(250) for _ in range(42)])
            chart = {"skill_time_ms": times, "music_length_ms": 250,
                     "notes": [(t, i, (i % 3) + 1) for i, t in enumerate(note_times)],
                     "combo_bases": [p.ms.f32(1 + (i // 8) * .07) for i in range(len(note_times))],
                     "note_percents": {1: 100, 2: 50, 3: 200}}
            slots = []
            for i in range(5):
                effects = [{"skill_effect_type": 2000, "effect_value": rng.choice([0, 1000, 3000, 6000, 16000]),
                            "activation_ms": rng.choice([10, 30, 100])}]
                if (i + case) % 2:
                    effects.append({"skill_effect_type": 2004, "effect_value": rng.randrange(20000),
                                    "activation_ms": 80, "judgement_targets": [3, 5, 5, 6]})
                slots.append({"extension_ms": case % 3, "active_live_effects": effects})
            index = score_intervals.note_index(chart)
            for order in p.ORDERS:
                expected = Counter((chart["note_percents"][op], base, factor)
                                   for op, base, factor in p.ms.ap_factor_samples(chart, slots, order))
                actual = Counter(dict(score_intervals.signature(chart, slots, order, index)))
                self.assertEqual(actual, expected, (case, order))

    def test_nonoverlapping_float32_residue_regression(self):
        times = [0, 10000, 20000, 30000, 40000]
        notes = [(t + dt, i * 2 + j, 1) for i, t in enumerate(times) for j, dt in enumerate([500, 1500])]
        chart = {"skill_time_ms": times, "music_length_ms": 50000, "notes": notes,
                 "combo_bases": [p.ms.f32(1)] * 10, "note_percents": {1: 100}}
        slots = [{"extension_ms": 0, "active_live_effects": [{"skill_effect_type": 2000,
                  "effect_value": v, "activation_ms": 1000}]} for v in [1000, 3000, 6000, 12000, 16000]]
        values = []
        for order in p.ORDERS:
            signature = score_intervals.signature(chart, slots, order, score_intervals.note_index(chart))
            values.append(sum(n * p.ms.note_score(100000, 25, 1000, p.ms.f32(1), pct, 100, base, factor)
                              for (pct, base, factor), n in signature))
        self.assertEqual((min(values), max(values)), (1509, 1516))


class SummaryTests(unittest.TestCase):
    def test_summary_matches_immutable_old_combine_and_full_cartesian(self):
        config = json.loads((ROOT / "browser/upstream.json").read_text("utf-8"))
        with zipfile.ZipFile(ROOT / "browser/upstream" / config["archive"]) as archive:
            tree = ast.parse(archive.read(f"OurNotes-配队程序-v{config['version']}/planner_core.py"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "combine_plans")
        namespace = dict(vars(p))
        exec(compile(ast.Module(body=[function], type_ignores=[]), "immutable-old-combine", "exec"), namespace)
        old_combine = namespace["combine_plans"]
        rng = random.Random(83101)
        for case in range(300):
            rows = {mode: [{"song_id": rng.randrange(1, 10), "difficulty": rng.choice(["hard", "expert"]),
                           "power": rng.randrange(1, 20), "per_live": {"event_pt": rng.randrange(20),
                           "shop_pt": rng.randrange(20), "cp": rng.choice([0, 50, 99, 100, 150, 200]) if mode == "normal" else 0}}
                          for _ in range(rng.randrange(1, 45))] for mode in ["normal", "challenge"]}
            args = [rows, rng.choice([0, 1, 4, 20]), 4, rng.choice([0, 199, 200, 399]), 200]
            actual, old = p.combine_plans(*args), old_combine(*args)
            self.assertEqual(actual["plans"], old["plans"], case)
            self.assertEqual(actual["top3"], old["top3"], case)
            for objective in ["event_pt", "shop_pt"]:
                other = "shop_pt" if objective == "event_pt" else "event_pt"
                key = lambda plan: (plan["totals"][objective], plan["totals"][other], plan["totals"]["cp_remaining"],
                                    plan["normal"]["power"], plan["challenge"]["power"],
                                    -plan["normal"]["song_id"], -plan["challenge"]["song_id"])
                full = [{"normal": n, "challenge": c, "totals": p.cycle(n["per_live"], c["per_live"], *args[1:])}
                        for n, c in itertools.product(rows["normal"], rows["challenge"])]
                self.assertEqual(key(actual["plans"][objective]), max(map(key, full)))


class PreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = p.Data()

    def test_shared_score_tables_verified_once_but_bad_chart_still_rejected(self):
        data = p.Data()
        with patch.object(p.ms, "_checked_inputs", wraps=p.ms._checked_inputs) as check:
            for mode in [False, True]:
                p.Scores(data, {"song_id": 100109, "difficulty": "expert", "method": "ap"}, mode)
            self.assertEqual(check.call_count, 1)
        with self.assertRaises(TypeError):
            data.score_inputs.values[1]["MasterLiveSettings"][0]["_value"] = "0"
        report = copy.deepcopy(data.conversion_report)
        report["errors"] = ["synthetic corruption"]
        with self.assertRaises(ValueError):
            p.ms.prepare_ap_chart(data.snapshot, 100109, "expert", 1, ordinary=True,
                                  _conversion_report=report, _verified_inputs=data.score_inputs)
        with self.assertRaises(ValueError):
            p.ms.prepare_ap_chart(data.snapshot, 100109, "expert", 1, ordinary=True, _verified_inputs={"verified": True})

    def test_threshold_probes_reused_and_unreachable_domain_preserved(self):
        chart = {"rank_thresholds": [{"_liveScoreRank": r, "_requiredScore": r * 11} for r in p.ms.RANKS]}
        called = []
        values = power_thresholds(chart, lambda power: called.append(power) or power * 2, 30)
        self.assertEqual(values, [11, 17, 22, 28, 31, 31])
        self.assertEqual(len(called), len(set(called)))
        expanded = power_thresholds(chart, lambda power: power * 2, 60)
        self.assertEqual(expanded, [11, 17, 22, 28, 33, 39])

    def test_compound_index_keeps_duplicate_match_errors(self):
        rows = p.sk.IndexedRows([{"_id": 1, "_group": 3, "_rank": 2}, {"_id": 2, "_group": 3, "_rank": 2}])
        with self.assertRaisesRegex(ValueError, "found 2"):
            p.sk._unique(rows, "duplicate compound key", _group=3, _rank=2)
        self.assertEqual(p.sk._unique(rows, "id", _id=2)["_id"], 2)

    def test_signature_byte_budget_eviction_only_recomputes(self):
        from performance_trace import SignatureCache
        trace = p.Trace()
        trace.signatures = SignatureCache(1)
        scorer = p.Scores(self.data, {"song_id": 100109, "difficulty": "expert", "method": "ap"}, True, trace)
        request = p.demo_profile()
        slots = p.sk.derive_ap_skill_contract(self.data.snapshot, request["profile"], request["candidate_member_ids"],
                    request["candidate_snap_ids"], _verified_inputs=self.data.skill_inputs)["slots"]
        first = scorer.evaluate(700000, slots)
        scorer.score_cache.clear()
        self.assertEqual(scorer.evaluate(700000, slots), first)
        self.assertEqual(trace.signatures.bytes, 0)

    def test_complete_cache_skips_build_and_budget_change_reproves(self):
        import solver_search as ss
        from search_cache import SearchCache
        from test_search_optimization import expanded_request
        request = expanded_request(self.data, 10)
        with tempfile.TemporaryDirectory() as directory:
            cache = SearchCache(directory)
            first = p.optimize(request, data=self.data, cache=cache)
            with patch.object(ss.Search, "build", side_effect=AssertionError("complete hit must skip build")):
                second = p.optimize(request, data=self.data, cache=cache)
            self.assertEqual(first["plans"], second["plans"])
            self.assertEqual(first["top3"], second["top3"])
            self.assertTrue(second["search"]["cached_complete_result"])
            changed = copy.deepcopy(request)
            changed["settings"]["boost_budget"] += 1
            with patch.object(ss.Search, "build", side_effect=RuntimeError("new request builds")):
                with self.assertRaisesRegex(RuntimeError, "new request builds"):
                    p.optimize(changed, data=self.data, cache=cache)

    def test_large_pool_seed_rows_are_legal_and_recomputed_before_bounds(self):
        import solver_search as ss
        import time
        from benchmark_solver_release import full_request
        request = full_request(self.data)
        model = p.PowerModel(self.data, request["profile"], request["candidate_member_ids"], request["candidate_snap_ids"])
        specs = {mode: p._song_specs(request["settings"], mode, self.data) for mode in ss.MODES}
        search = ss.Search(request, self.data, model, specs, 1, lambda **kw: None, lambda: False,
                           None, None, time.monotonic())
        search.build()
        self.assertEqual(len(search.seed_plans), 9)
        for plan in search.seed_plans:
            for mode in ss.MODES:
                row = plan[mode]
                self.assertEqual(len({self.data.index["MasterMemberCard"][m]["_characterID"] for m in row["member_ids"]}), 5)
                self.assertEqual(len(set(row["snap_ids"])), 5)
                scorer = p.Scores(self.data, specs[mode][0], mode == "challenge")
                self.assertEqual(scorer.evaluate(row["power"]), row["score"])
                preview = p.rw.preview_challenge_rewards if mode == "challenge" else p.rw.preview_normal_rewards
                gained = preview(self.data.snapshot, row["score"]["rank"], search.zones[mode]["consumed"],
                                 row["bonuses_10000"]["event_pt"], row["bonuses_10000"]["shop_pt"])["gained"]
                self.assertEqual(gained, row["per_live"])
            self.assertEqual(plan["totals"], p.cycle(plan["normal"]["per_live"], plan["challenge"]["per_live"],
                                  request["settings"]["boost_budget"], request["settings"]["boost_per_live"],
                                  request["settings"]["starting_cp"], request["settings"]["challenge_cp"]))

    def test_diagnostics_do_not_change_business_results_or_matching_resume(self):
        request = p.demo_profile()
        on = p.optimize(request, data=self.data)
        off = p.optimize(request, data=self.data, diagnostics=False)
        for field in ["plans", "top3", "difference"]:
            self.assertEqual(on[field], off[field])
        self.assertIn("diagnostics", on["search"])
        self.assertNotIn("diagnostics", off["search"])
        self.assertGreater(on["search"]["diagnostics"]["counters"].get("cross_sheet_matching_hits", 0), 0)


class LinearSumTests(unittest.TestCase):
    def test_proto_matches_original_sum_without_mutating_operands(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("test_browser_cp", ROOT / "browser/cp_model.py")
        cp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cp)
        rng = random.Random(731)
        for _ in range(300):
            model = cp.CpModel()
            variables = [model.new_bool_var(str(i)) for i in range(7)]
            terms = [rng.choice(variables) * rng.choice([-9007199254740997, -2, 0, 3, 9007199254740997])
                     + rng.randrange(-20, 20) for _ in range(rng.randrange(30))] + [True, False, -7]
            before = [copy.deepcopy(t.proto()) for t in terms if isinstance(t, cp.LinearExpr)]
            expected = cp.LinearExpr.cast(sum(terms)).proto()
            self.assertEqual(cp.LinearExpr.sum(iter(terms)).proto(), expected)
            self.assertEqual([t.proto() for t in terms if isinstance(t, cp.LinearExpr)], before)
        self.assertEqual(cp.LinearExpr.sum([]).proto(), cp.LinearExpr.cast(0).proto())

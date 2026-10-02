"""Independent exhaustive oracles, large-pool acceptance and exact resume."""
import copy
import itertools
from pathlib import Path
import random
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import planner_core as p
import solver_search
from search_cache import SearchCache


def expanded_request(data, count=10):
    request = copy.deepcopy(p.demo_profile())
    existing = set(request["candidate_snap_ids"])
    for card in data.catalog()["snaps"]:
        sid = card["id"]
        if sid in existing:
            continue
        # Fabricated test growth only; never replace the user's real card library.
        row = {"id": sid, "level": min(20, card["caps"][0]), "limit_break_count": 0}
        request["profile"]["inventory"]["snaps"].append(row)
        try:
            for slot in range(5):
                others=iter(request["candidate_snap_ids"][:4])
                snaps=[sid if i==slot else next(others) for i in range(5)]
                p.sk.derive_ap_skill_contract(data.snapshot, request["profile"], request["candidate_member_ids"], snaps,
                                              _verified_inputs=data.skill_inputs)
        except ValueError:
            request["profile"]["inventory"]["snaps"].pop()
            continue
        request["candidate_snap_ids"].append(sid)
        existing.add(sid)
        if len(existing) >= count:
            break
    if len(existing) < count:
        raise AssertionError("not enough supported fixture Snaps")
    for mode in ("normal", "challenge"):
        request["settings"][mode]["sheets"] = [{"song_id": 100109, "difficulty": "expert"}]
    return request


class MatchingOracleTests(unittest.TestCase):
    def test_dp_matches_all_bijections_with_bonus_tradeoffs_and_pair_dependent_skills(self):
        rng = random.Random(8419)
        members = tuple(range(1, 6))
        for use_skills in (False, True):
            for _ in range(6):
                snaps = tuple(range(1, rng.randrange(6, 9)))
                model = SimpleNamespace(
                    pair={(m,s): (rng.randrange(30), rng.randrange(30), 0) for m in members for s in snaps},
                    sb={s: {"event_pt_bonus_10000": rng.randrange(3), "shop_pt_bonus_10000": rng.randrange(3)} for s in snaps})
                tokens = {(m,s): rng.randrange(3) for m in members for s in snaps} if use_skills else None
                def key(binding):
                    return (sum(model.sb[s]["event_pt_bonus_10000"] for s in binding),
                            sum(model.sb[s]["shop_pt_bonus_10000"] for s in binding),
                            tuple(sorted(tokens[m,s] for m,s in zip(members,binding))) if tokens else ())
                oracle = {}
                for binding in itertools.permutations(snaps, 5):
                    raw = sum(sum(model.pair[m,s]) for m,s in zip(members,binding))
                    k = key(binding)
                    if k not in oracle or raw > oracle[k][0] or (raw == oracle[k][0] and binding < oracle[k][1]):
                        oracle[k] = (raw, binding)
                actual = {key(binding): (raw,binding) for raw,binding in p.matching_outcomes(model,members,snaps,tokens)}
                self.assertEqual(actual, oracle)
                self.assertTrue(all(len(set(b)) == 5 for _,b in actual.values()))

    def test_lazy_member_order_and_subtree_resume_match_combinations_oracle(self):
        groups = {1:[1,9], 2:[2,8], 3:[3,7], 4:[4], 5:[5], 6:[6]}
        teams = p.MemberTeams(groups)
        chars = teams.character
        expected = [v for v in itertools.combinations(sorted(chars),5) if len({chars[m] for m in v}) == 5]
        self.assertEqual(list(teams),expected)
        self.assertEqual(len(teams),len(expected))
        for offset in range(len(expected)+1):
            self.assertEqual(list(teams.iter_from(offset)),expected[offset:])

    def test_dp_checks_cancel_inside_transition_loop(self):
        model=SimpleNamespace(pair={(m,s):(1,0,0) for m in range(5) for s in range(20)},
                              sb={s:{"event_pt_bonus_10000":s,"shop_pt_bonus_10000":s} for s in range(20)})
        with self.assertRaises(p.Cancelled):
            p.matching_outcomes(model,tuple(range(5)),tuple(range(20)),cancelled=lambda:True)


class LargePoolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = p.Data()

    def test_ap_above_old_cap_finishes_and_conserves_both_currencies(self):
        request = expanded_request(self.data, 10)
        output = p.optimize(request,data=self.data)
        self.assertTrue(output["search"]["complete"])
        self.assertIsNone(output["search"]["candidate_limit"])
        for mode in ("normal","challenge"):
            counts = output["search"]["counts"][mode]
            self.assertEqual(counts["evaluated_bindings"],30240)
            self.assertLess(counts["optimization"]["score_evaluations"],30240)
        for objective in ("event_pt","shop_pt"):
            plan=output["plans"][objective]
            self.assertEqual(plan["totals"],p.cycle(plan["normal"]["per_live"],plan["challenge"]["per_live"],20,4,0,200))

    def test_skip_all_64_snaps_finishes_above_old_70000_cap(self):
        request=copy.deepcopy(p.demo_profile())
        request["candidate_snap_ids"]=list(self.data.index["MasterSupportCard"])
        owned=p.cp._inventory(request["profile"],"snaps")
        request["profile"]["inventory"]["snaps"]=[owned.get(c["id"],{"id":c["id"],"level":min(20,c["caps"][0]),"limit_break_count":0}) for c in self.data.catalog()["snaps"]]
        for mode in ("normal","challenge"):
            request["settings"][mode].update(method="skip",sheets=[{"song_id":100109,"difficulty":"expert"}])
        out=p.optimize(request,data=self.data)
        self.assertTrue(out["search"]["complete"])
        self.assertEqual(out["search"]["counts"]["normal"]["evaluated_bindings"],7624512)
        self.assertLess(out["search"]["counts"]["normal"]["optimization"]["score_evaluations"],1000)

    def test_full_63_member_64_snap_pool_starts_lazily_and_cancels_with_a_cursor(self):
        request=copy.deepcopy(p.demo_profile())
        catalog=self.data.catalog()
        request["profile"]={"schema_version":1,
            "inventory":{"members":[{"id":c["id"],"level":1,"training_count":0,"awakening_count":0} for c in catalog["members"]],
                         "snaps":[{"id":c["id"],"level":1,"limit_break_count":0} for c in catalog["snaps"]]},
            "facilities":[{"id":c["id"],"level":1} for c in catalog["facilities"]],
            "character_ranks":[{"character_id":c["id"],"rank":1} for c in catalog["characters"]],
            "character_total_rank":len(catalog["characters"]),"tgw_card_rank":1}
        request["candidate_member_ids"]=[c["id"] for c in catalog["members"]]
        request["candidate_snap_ids"]=[c["id"] for c in catalog["snaps"]]
        for mode in ("normal","challenge"):
            request["settings"][mode].update(method="skip",sheets=[{"song_id":100109,"difficulty":"expert"}])
        stop=[False]
        progress_rows=[]
        def progress(**row):
            progress_rows.append(row)
            if row.get("completed_member_teams")==1:
                stop[0]=True
        with tempfile.TemporaryDirectory() as directory:
            cache=SearchCache(directory)
            # Independently retain coverage of the legacy streaming engine; the
            # new solver's cancellation is exercised in test_solver_search.
            with patch.object(solver_search,"should_use_solver",return_value=False), self.assertRaises(p.Cancelled):
                p.optimize(request,data=self.data,cache=cache,progress=progress,cancelled=lambda:stop[0])
            saved=cache.last_checkpoint()
            self.assertEqual(saved["saved_member_teams"],1)
            self.assertEqual(saved["saved_sheets"],0)
            self.assertGreater(progress_rows[0]["total"],70000)

    def test_memory_budget_falls_back_to_exact_full_pool_in_both_methods(self):
        for method in ("ap","skip"):
            request=copy.deepcopy(p.demo_profile())
            for mode in ("normal","challenge"):
                request["settings"][mode].update(method=method,sheets=[{"song_id":100109,"difficulty":"expert"}])
            normal=p.optimize(request,data=self.data)
            with patch.object(p,"MAX_MATCH_STATES",1):
                exhaustive=p.optimize(request,data=self.data)
            for objective in ("event_pt","shop_pt"):
                self.assertEqual(normal["plans"][objective]["totals"],exhaustive["plans"][objective]["totals"])
                for mode in ("normal","challenge"):
                    self.assertEqual(normal["plans"][objective][mode]["power"],exhaustive["plans"][objective][mode]["power"])
                    self.assertEqual(normal["plans"][objective][mode]["score"]["rank"],exhaustive["plans"][objective][mode]["score"]["rank"])
            self.assertEqual(exhaustive["search"]["counts"]["normal"]["optimization"]["streaming_teams"],1)

    def test_cancel_and_reopen_resumes_inside_unfinished_sheet(self):
        request=copy.deepcopy(p.demo_profile())
        # Add a sixth actual fixture member; use its existing recorded growth.
        other=next((r for r in request["profile"]["inventory"]["members"] if r["id"] not in request["candidate_member_ids"]),None)
        if other is None:
            base=request["profile"]["inventory"]["members"][0]
            known_chars={self.data.index["MasterMemberCard"][m]["_characterID"] for m in request["candidate_member_ids"]}
            cid=next(m for m,c in self.data.index["MasterMemberCard"].items() if c["_characterID"] in known_chars and m not in request["candidate_member_ids"])
            other=dict(base,id=cid,level=1,training_count=0,awakening_count=0)
            request["profile"]["inventory"]["members"].append(other)
        request["candidate_member_ids"].append(other["id"])
        for mode in ("normal","challenge"):
            request["settings"][mode].update(method="skip",sheets=[{"song_id":100109,"difficulty":"expert"}])
        with tempfile.TemporaryDirectory() as directory:
            cache=SearchCache(directory)
            stop=[False]
            def progress(**v):
                if v.get("completed_member_teams")==1:
                    stop[0]=True
            with self.assertRaises(p.Cancelled):
                p.optimize(request,data=self.data,cache=cache,progress=progress,cancelled=lambda:stop[0])
            checkpoint=SearchCache(directory).last_checkpoint()
            self.assertEqual(checkpoint["saved_sheets"],0)
            self.assertEqual(checkpoint["saved_member_teams"],1)
            seen=[]
            def matching(*args,**kwargs):
                seen.append(args[1])
                return original(*args,**kwargs)
            original=p.matching_outcomes
            with patch.object(p,"matching_outcomes",side_effect=matching):
                resumed=p.optimize(request,data=self.data,cache=SearchCache(directory))
            fresh=p.optimize(request,data=self.data)
            self.assertEqual(resumed["plans"],fresh["plans"])
            self.assertEqual(resumed["top3"],fresh["top3"])
            self.assertEqual(resumed["search"]["counts"]["normal"]["computed_bindings"],fresh["search"]["counts"]["normal"]["evaluated_bindings"]-1)

    def test_score_cache_stays_bounded(self):
        scores=p.Scores(self.data,{"song_id":100109,"difficulty":"expert","method":"skip"},False)
        with patch.object(p,"MAX_SCORE_CACHE",4):
            for power in range(700000,700011):
                scores.evaluate(power)
                self.assertLessEqual(len(scores.score_cache),4)

    def test_rank_bound_is_an_exact_order_and_never_below_independent_minimum(self):
        request=p.demo_profile()
        slots=p.sk.derive_ap_skill_contract(self.data.snapshot,request["profile"],request["candidate_member_ids"],request["candidate_snap_ids"],_verified_inputs=self.data.skill_inputs)["slots"]
        for song, diff, power in ((100109,"expert",812225),(100063,"hard",700001)):
            scores=p.Scores(self.data,{"song_id":song,"difficulty":diff,"method":"ap"},True)
            direct=[p.ms.score_order(dict(scores.chart,power=power),slots,order) for order in p.ORDERS]
            upper=scores.rank_upper_bound(power,slots)
            ordered=sorted(slots,key=lambda s:p.Scores.skill_key([s])[0])
            single=p.ms.score_order(dict(scores.chart,power=power),ordered,tuple(range(5)))
            self.assertEqual(upper,p.ms.rank_for_score(single,scores.chart["rank_thresholds"]))
            self.assertGreaterEqual(p.RANKS.index(upper),p.RANKS.index(p.ms.rank_for_score(min(direct),scores.chart["rank_thresholds"])))


if __name__ == "__main__":
    unittest.main()

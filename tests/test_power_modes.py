"""Mode-specific power rules; all player values here are synthetic.

The pinned game's Help_SubCategory_Description_140002 limits parameter
bonuses to challenge lives. Currency bonuses still apply in both modes.
"""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import planner_core as p


class PowerModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = p.Data()
        cls.mids, cls.sids = [59, 11, 55, 26, 3], [33, 37, 52, 61, 3]
        catalog = cls.data.catalog()
        cls.profile = {"schema_version": 1, "inventory": {
            "members": [{"id": i, "level": 20, "training_count": 0,
                         "awakening_count": 0, "live_skill_level": 1} for i in cls.mids],
            "snaps": [{"id": i, "level": 20, "limit_break_count": 0} for i in cls.sids]},
            "character_ranks": [{"character_id": c["id"], "rank": 5} for c in catalog["characters"]],
            "character_total_rank": 5 * len(catalog["characters"]), "tgw_card_rank": 1,
            "facilities": [{"id": c["id"], "level": min(3, c["max_level"])} for c in catalog["facilities"]]}

    def model(self):
        return p.PowerModel(self.data, self.profile, self.mids, self.sids)

    def fixed_power(self, model, challenge, song=100109, snaps=None, leader=55):
        model.music(song, challenge)
        rates = p.leader_rates(self.data, model.cards[leader], model.own[leader],
                               [model.cards[m] for m in self.mids],
                               [model.chars[m] for m in self.mids], model.music_type)
        vectors = [p.dp._mul_floor(model.b[m], rate) for m, rate in zip(self.mids, rates)]
        return model.total(self.mids, self.sids if snaps is None else snaps, vectors)

    def test_pinned_game_help_limits_parameter_bonus_to_challenge(self):
        self.assertIn("パラメータボーナス」はチャレンジライブのみで発動します",
                      self.data.text["Help_SubCategory_Description_140002"])

    def test_same_song_team_and_leader_change_power_but_keep_currency_bonuses(self):
        model = self.model()
        normal = self.fixed_power(model, False)
        normal_type, bonuses = model.music_type, model.bonuses(self.mids, self.sids)
        challenge = self.fixed_power(model, True)
        self.assertEqual(model.music_type, normal_type)  # Isolate event parameters.
        self.assertLess(normal, challenge)
        self.assertEqual(model.bonuses(self.mids, self.sids), bonuses)
        self.assertGreater(bonuses["event_pt"], 0)
        self.assertGreater(bonuses["shop_pt"], 0)
        # Search and solver alternate modes on the same model instance.
        self.assertEqual(self.fixed_power(model, False), normal)
        self.assertEqual(self.fixed_power(model, True), challenge)

    def test_ordinary_matches_challenge_with_only_parameter_bonuses_removed(self):
        bonuses = deepcopy(p.eb.calculate_deck_bonuses(self.data.snapshot, self.profile, self.mids, self.sids))
        for kind in ("members", "snaps"):
            for row in bonuses[kind]:
                row["parameter_bonus_10000"] = 0
        normal = self.fixed_power(self.model(), False)
        with patch.object(p.eb, "calculate_deck_bonuses", return_value=bonuses):
            expected = self.fixed_power(self.model(), True)
        self.assertEqual(normal, expected)

    def test_fast_power_matches_detailed_calculator_in_both_modes(self):
        model = self.model()
        for challenge in (False, True, False):
            for song in (100056, 100063, 100109):
                for leader in (55, 59, 3):
                    for snaps in (self.sids, list(reversed(self.sids))):
                        with self.subTest(challenge=challenge, song=song, leader=leader, snaps=snaps):
                            actual = self.fixed_power(model, challenge, song, snaps, leader)
                            detailed = p.dp.calculate_selected_deck_power(
                                self.data.snapshot, self.profile, self.mids, snaps, leader, song,
                                ordinary=not challenge)
                            self.assertEqual(actual, detailed["total"]["total"])

    def test_search_and_solver_report_power_for_the_actual_live_mode(self):
        import solver_search
        request = {"profile": deepcopy(self.profile), "candidate_member_ids": self.mids,
                   "candidate_snap_ids": self.sids, "settings": {
                       "boost_budget": 20, "boost_per_live": 4, "starting_cp": 200, "challenge_cp": 200,
                       **{mode: {"method": "skip", "sheets": [{"song_id": 100109, "difficulty": "expert"}]}
                          for mode in ("normal", "challenge")}}}
        for solver in (False, True):
            with self.subTest(solver=solver), patch.object(solver_search, "should_use_solver", return_value=solver):
                result = p.optimize(request, data=self.data)
                for objective in ("event_pt", "shop_pt"):
                    for mode in ("normal", "challenge"):
                        row = result["plans"][objective][mode]
                        detailed = p.dp.calculate_selected_deck_power(
                            self.data.snapshot, self.profile, row["member_ids"], row["snap_ids"],
                            row["leader_member_id"], row["song_id"], ordinary=mode == "normal")
                        self.assertEqual(row["power"], detailed["total"]["total"])


if __name__ == "__main__":
    unittest.main()

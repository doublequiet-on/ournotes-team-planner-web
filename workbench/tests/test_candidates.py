"""Independent small-pool enumeration and request/lifecycle regressions."""
from copy import deepcopy
import itertools
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import team_candidates as t
from app import Cache, sample


def fixture(data, snaps=7):
    request = sample(data)
    request['candidate_snap_ids'] = request['candidate_snap_ids'][:snaps]
    request['team_settings']['count'] = 3
    return request


def oracle(search):
    """Enumerate all legal card sets, leaders and bindings without CP variables."""
    outcomes = {}
    for mids in itertools.combinations(search.mids, 5):
        if len({search.model.cards[m]['_characterID'] for m in mids}) != 5:
            continue
        if not set(search.settings['required_member_ids']) <= set(mids):
            continue
        if search.settings['pure_type'] and len({search.model.cards[m]['_cardType'] for m in mids}) != 1:
            continue
        for sids in itertools.combinations(search.sids, 5):
            if not set(search.settings['required_snap_ids']) <= set(sids):
                continue
            variants = []
            for leader in mids:
                if search.settings['required_leader_id'] and leader != search.settings['required_leader_id']:
                    continue
                rates = t.p.leader_rates(search.data, search.model.cards[leader], search.model.own[leader],
                       [search.model.cards[m] for m in mids], [search.model.chars[m] for m in mids], search.model.music_type)
                vectors = [t.p.dp._mul_floor(search.model.b[m], r) for m, r in zip(mids, rates)]
                for binding in itertools.permutations(sids):
                    mapping = dict(zip(mids, binding))
                    if any(mapping[pair['member_id']] != pair['snap_id'] for pair in search.settings['required_bindings']):
                        continue
                    bonuses = search.model.bonuses(mids, binding)
                    if (bonuses['event_pt'] < search.settings['min_event_bonus_10000']
                            or bonuses['shop_pt'] < search.settings['min_shop_bonus_10000']):
                        continue
                    power = search.model.total(mids, binding, vectors)
                    area = 0
                    for m, s in zip(mids, binding):
                        slot = search.pairs[m, s]
                        for e in slot['active_live_effects']:
                            count = 1 if e['skill_effect_type'] == 2000 else sum(x == 5 for x in e.get('judgement_targets', []))
                            import math
                            duration = math.ceil(t.p.cp._float32(t.p.cp._float32(e['activation_ms']) + t.p.cp._float32(slot['extension_ms'])))
                            area += count * duration * e['effect_value']
                    variants.append((power, area, search.model.bonuses(mids, binding)))
            if variants:
                outcomes[mids, sids] = variants
    return outcomes


def objective(row, kind):
    power, area, bonus = row
    proxy = power * (t.KINDS[kind][1] * 1000 * 10000 + area)
    return ((bonus['event_pt'], proxy) if kind == 'event' else
            (bonus['shop_pt'], proxy) if kind == 'shop' else
            (power, proxy) if kind == 'power' else
            (area, power) if kind == 'skill' else (proxy,))


class CandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = t.p.Data()

    def test_leader_binding_and_bonus_floor_match_exhaustive_oracle(self):
        request = fixture(self.data, 6)
        request['team_settings'].update(required_leader_id=59,
            required_bindings=[{'member_id': 59, 'snap_id': 33}],
            min_event_bonus_10000=100, min_shop_bonus_10000=100)
        search = t.Search(t.parse_request(request, self.data), self.data)
        gold = oracle(search)
        self.assertTrue(gold)
        for kind in t.KINDS:
            row = search.solve_next(kind)
            self.assertEqual(row['leader_id'], 59)
            self.assertEqual(dict(zip(row['member_ids'], row['snap_ids']))[59], 33)
            self.assertEqual(tuple(row['verified_selection_objective']),
                             max(objective(v, kind) for values in gold.values() for v in values))
        request['team_settings']['min_event_bonus_10000'] = 1000000
        result = t.optimize(request, self.data)
        self.assertEqual(result['teams'], [])
        self.assertTrue(result['search']['exhausted'])

    def test_fixed_evaluation_retains_bindings_and_ignores_search_filters(self):
        request = fixture(self.data, 5)
        request['fixed_team'] = {'member_ids': request['candidate_member_ids'],
                                 'snap_ids': request['candidate_snap_ids'], 'leader_id': 59}
        request['team_settings'].update(member_min_rarity=10, min_event_bonus_10000=1000000,
                                        required_leader_id=11, required_bindings=[{'member_id': 11, 'snap_id': 37}])
        with patch.object(t, 'Search', side_effect=AssertionError('fixed evaluation must not search')):
            row = t.evaluate(request, self.data)['teams'][0]
        self.assertEqual(row['member_ids'], request['fixed_team']['member_ids'])
        self.assertEqual(row['snap_ids'], request['fixed_team']['snap_ids'])
        self.assertEqual(row['leader_id'], 59)
        model = t.p.PowerModel(self.data, request['profile'], row['member_ids'], row['snap_ids'])
        t.set_power_context(model, request['team_settings'])
        mids = row['member_ids']
        rates = t.p.leader_rates(self.data, model.cards[59], model.own[59],
                 [model.cards[m] for m in mids], [model.chars[m] for m in mids], 0)
        vectors = [t.p.dp._mul_floor(model.b[m], r) for m, r in zip(mids, rates)]
        self.assertEqual(row['power'], model.total(mids, row['snap_ids'], vectors))

    def test_filter_growth_parity_and_conflicting_locks(self):
        request = fixture(self.data, 6)
        selected = request['candidate_member_ids']
        chars = [self.data.index['MasterMemberCard'][m]['_characterID'] for m in selected]
        request['team_settings']['allowed_character_ids'] = chars
        request['team_settings']['allowed_band_ids'] = sorted({self.data.index['MasterCharacter'][c]['_bandID'] for c in chars})
        self.assertTrue(t.growth_check(request, self.data)['complete'])
        self.assertEqual(t.parse_request(request, self.data)['candidate_member_ids'], sorted(selected))
        request['team_settings']['required_bindings'] = [{'member_id': 59, 'snap_id': 33}, {'member_id': 11, 'snap_id': 33}]
        with self.assertRaises(t.p.InputError):
            t.parse_request(request, self.data)

    def test_every_new_constraint_changes_cache_identity(self):
        request = fixture(self.data, 6)
        original = t.request_key(t.parse_request(request, self.data), self.data)
        for key, value in {'member_min_rarity': 2, 'snap_min_rarity': 2,
                'required_leader_id': 59, 'required_bindings': [{'member_id': 59, 'snap_id': 33}],
                'min_event_bonus_10000': 100, 'min_shop_bonus_10000': 100,
                'allowed_character_ids': [self.data.index['MasterMemberCard'][m]['_characterID'] for m in request['candidate_member_ids']]}.items():
            variant = deepcopy(request)
            variant['team_settings'][key] = value
            self.assertNotEqual(t.request_key(t.parse_request(variant, self.data), self.data), original, key)

    def test_all_seven_objectives_match_exhaustive_binding_and_leader_oracle(self):
        request = fixture(self.data, 6)
        search = t.Search(t.parse_request(request, self.data), self.data)
        gold = oracle(search)
        for kind in t.KINDS:
            with self.subTest(kind=kind):
                row = search.solve_next(kind)
                expected = max(objective(v, kind) for values in gold.values() for v in values)
                self.assertEqual(tuple(row['verified_selection_objective']), expected)
                self.assertEqual(len(set(row['member_ids'])), 5)
                self.assertEqual(len(set(row['snap_ids'])), 5)

    def test_portfolio_remaining_set_optima_and_resume(self):
        request = fixture(self.data, 6)
        request['team_settings']['count'] = 5
        search = t.Search(t.parse_request(request, self.data), self.data)
        gold = oracle(search)
        saved = []
        output = search.run(save=lambda value: saved.append(deepcopy(value)))
        for row in output['teams']:
            expected = max(objective(v, row['selected_for']) for values in gold.values() for v in values)
            self.assertEqual(tuple(row['verified_selection_objective']), expected)
            del gold[tuple(row['member_ids']), tuple(sorted(row['snap_ids']))]
        resumed = t.Search(t.parse_request(request, self.data), self.data).run(saved[1])
        self.assertEqual(resumed['teams'][:2], output['teams'][:2])
        self.assertEqual(resumed['search']['cached_teams'], 2)
        self.assertEqual(len(resumed['teams']), 5)

    def test_never_calls_chart_or_song_scoring(self):
        with patch.object(t.p, 'Scores', side_effect=AssertionError('chart access')), \
             patch.object(t.p.ms, 'prepare_ap_chart', side_effect=AssertionError('chart access')), \
             patch.object(t.p.PowerModel, 'music', side_effect=AssertionError('song context')):
            output = t.optimize(fixture(self.data, 5), self.data)
        self.assertEqual(output['search']['chart_count'], 0)
        self.assertEqual(len(output['teams']), 1)
        self.assertTrue(output['search']['exhausted'])

    def test_skill_strength_and_duration_can_outweigh_power(self):
        a = (800000, 0, {'event_pt': 0, 'shop_pt': 0})
        b = (760000, 100000000, {'event_pt': 0, 'shop_pt': 0})
        self.assertGreater(objective(a, 'power'), objective(b, 'power'))
        self.assertGreater(objective(b, 'balanced'), objective(a, 'balanced'))
        slot = {'extension_ms': 1000, 'active_live_effects': [
            {'skill_effect_type': 2000, 'effect_value': 10000, 'activation_ms': 5000},
            {'skill_effect_type': 2004, 'effect_value': 5000, 'activation_ms': 3000, 'judgement_targets': [3, 5]}]}
        self.assertEqual(t.skill_area(slot), 80000000)

    def test_filters_required_cards_and_missing_growth(self):
        request = fixture(self.data, 6)
        request['team_settings']['required_snap_ids'] = [3]
        output = t.optimize(request, self.data)
        self.assertTrue(all(3 in row['snap_ids'] for row in output['teams']))
        request['team_settings']['member_type'] = 1
        with self.assertRaisesRegex(ValueError, '筛选后'):
            t.parse_request(request, self.data)
        request = fixture(self.data, 5)
        request['profile']['inventory']['members'][0]['live_skill_level'] = None
        with self.assertRaisesRegex(ValueError, 'Live'):
            t.parse_request(request, self.data)

    def test_cache_identity_covers_profile_mode_strategy_filters_and_locks(self):
        raw = fixture(self.data, 5)
        key = t.request_key(t.parse_request(raw, self.data), self.data)
        for name, value in [('mode', 'challenge'), ('strategy', 'skill'), ('count', 2),
                            ('music_attribute', 1), ('required_snap_ids', [3])]:
            changed = deepcopy(raw)
            changed['team_settings'][name] = value
            self.assertNotEqual(key, t.request_key(t.parse_request(changed, self.data), self.data))
        changed = deepcopy(raw)
        changed['profile']['inventory']['members'][0]['live_skill_level'] = 2
        self.assertNotEqual(key, t.request_key(t.parse_request(changed, self.data), self.data))
        with tempfile.TemporaryDirectory() as folder:
            cache = Cache(Path(folder) / 'state.sqlite3')
            original = t.optimize(raw, self.data, cache=cache)
            with patch.object(t.Search, 'build', side_effect=AssertionError('rebuild')):
                hit = t.optimize(raw, self.data, cache=cache)
            self.assertEqual(original['teams'], hit['teams'])
            self.assertTrue(hit['search']['cached_complete_result'])
            cache.db.close()

    def test_cancelled_partial_is_saved_and_not_published(self):
        raw = fixture(self.data, 6)
        checkpoint, stop = [], [False]
        search = t.Search(t.parse_request(raw, self.data), self.data, cancelled=lambda: stop[0])
        def save(value):
            checkpoint.append(deepcopy(value))
            stop[0] = True
        with self.assertRaises(t.p.Cancelled):
            search.run(save=save)
        self.assertEqual(len(checkpoint[0]['rows']), 1)
        self.assertFalse(checkpoint[0]['complete'])
        resumed = t.Search(t.parse_request(raw, self.data), self.data).run(checkpoint[0])
        self.assertEqual(resumed['search']['cached_teams'], 1)
        self.assertEqual(len(resumed['teams']), 3)

    def test_conditional_leader_is_checked_by_exact_formation_oracle(self):
        raw = fixture(self.data, 5)
        old = self.data.tables['MasterLeaderSkillEffect']
        table = deepcopy(old)
        conditions = [c for c in self.data.tables['MasterSkillCondition'] if c['_conditionType'] == 3001]
        group = next(s['_group'] for s in self.data.tables['MasterSkillConditionSet'] if conditions[0]['_id'] in s['_conditionIds'])
        leader = self.data.index['MasterMemberCard'][59]['_leaderSkillID']
        for row in table:
            if row['_leaderSkillID'] == leader:
                row['_skillConditionGroup'] = group
        self.data.tables['MasterLeaderSkillEffect'] = table
        try:
            search = t.Search(t.parse_request(raw, self.data), self.data)
            gold = oracle(search)
            actual = search.solve_next('balanced')
            self.assertEqual(tuple(actual['verified_selection_objective']), max(objective(v, 'balanced') for vals in gold.values() for v in vals))
        finally:
            self.data.tables['MasterLeaderSkillEffect'] = old


if __name__ == '__main__':
    unittest.main(verbosity=2)

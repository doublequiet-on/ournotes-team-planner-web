"""Song-independent portfolios. Exact optimization of declared proxies, not scores."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import itertools
import json
import math
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'runtime'))
import planner_core as p
import solver_search as upstream_solver
from ortools.sat.python import cp_model

VERSION = '0.3.0'
SCHEMA = 1
BDON = 'https://bdon.moe/tools/chart-data'
KINDS = {
    'short': ('技能覆盖优先', 60),
    'balanced': ('综合出分潜力', 120),
    'long': ('基础力权重较高', 180),
    'event': ('活动加成优先', 120),
    'shop': ('商店加成优先', 120),
    'power': ('综合力优先', 120),
    'skill': ('技能积分优先', 120),
}
DEFAULT = {'mode': 'normal', 'strategy': 'portfolio', 'count': 15,
           'member_type': 0, 'snap_type': 0, 'member_band': 0,
           'music_attribute': 0, 'pure_type': False,
           'required_member_ids': [], 'required_snap_ids': [],
           'member_min_rarity': 0, 'snap_min_rarity': 0,
           'allowed_band_ids': [], 'allowed_character_ids': [],
           'required_leader_id': 0, 'required_bindings': [],
           'min_event_bonus_10000': 0, 'min_shop_bonus_10000': 0}


def card_allowed(data, card, kind, settings):
    typ = settings['member_type' if kind == 'members' else 'snap_type']
    rarity = settings['member_min_rarity' if kind == 'members' else 'snap_min_rarity']
    if (typ and card['_cardType'] != typ) or card['_rarity'] < rarity:
        return False
    if kind == 'members':
        char = data.index['MasterCharacter'][card['_characterID']]
        if settings['member_band'] and char['_bandID'] != settings['member_band']:
            return False
        if settings['allowed_band_ids'] and char['_bandID'] not in settings['allowed_band_ids']:
            return False
        if settings['allowed_character_ids'] and card['_characterID'] not in settings['allowed_character_ids']:
            return False
    return True


def parse_request(raw, data):
    if not isinstance(raw, dict):
        raise p.InputError('请导入有效的个人卡库。')
    raw = raw.get('input', raw)
    if not isinstance(raw, dict) or raw.get('schema_version') != 1:
        raise p.InputError('卡库需要 schema_version 1。')
    settings = {**deepcopy(DEFAULT), **deepcopy(raw.get('team_settings', {}))}
    if settings['mode'] not in ('normal', 'challenge'):
        raise p.InputError('请选择普通或挑战配队。')
    if settings['strategy'] not in ('portfolio', *KINDS):
        raise p.InputError('推荐类型不正确。')
    for name, high in (('count', 15), ('member_type', 5), ('snap_type', 5),
                       ('music_attribute', 5), ('member_band', 100)):
        p.integer(settings[name], name, 1 if name == 'count' else 0, high)
    if type(settings['pure_type']) is not bool:
        raise p.InputError('纯属性条件格式不正确。')
    for key in ('member_min_rarity', 'snap_min_rarity'):
        p.integer(settings[key], key, 0, 10)
    for key in ('min_event_bonus_10000', 'min_shop_bonus_10000'):
        p.integer(settings[key], key, 0, 1000000)
    known_bands = {c['_bandID'] for c in data.tables['MasterCharacter']}
    for key, known in (('allowed_band_ids', known_bands),
                       ('allowed_character_ids', set(data.index['MasterCharacter']))):
        values = settings[key]
        if (not isinstance(values, list) or any(type(v) is not int for v in values)
                or len(set(values)) != len(values) or not set(values) <= known):
            raise p.InputError('乐队或角色筛选包含未知、重复或格式不正确的 ID。')
    profile = raw.get('profile')
    if not isinstance(profile, dict):
        raise p.InputError('请先导入或填写卡库。')
    mids, sids = [], []
    for kind, key, table, output in (
        ('members', 'candidate_member_ids', 'MasterMemberCard', mids),
        ('snaps', 'candidate_snap_ids', 'MasterSupportCard', sids),
    ):
        owned = p.cp._inventory(profile, kind)
        ids = raw.get(key, [])
        p.cp._selection(ids, owned, kind)
        for identifier in ids:
            card = data.index[table].get(identifier)
            if card is None:
                raise p.InputError(f'候选卡 #{identifier} 不在当前快照。')
            if not card_allowed(data, card, kind, settings):
                continue
            output.append(identifier)
        required = settings['required_member_ids' if kind == 'members' else 'required_snap_ids']
        if (not isinstance(required, list) or any(type(i) is not int for i in required)
                or len(required) != len(set(required)) or not set(required) <= set(output)):
            raise p.InputError('必带卡必须已拥有、已勾选，并符合本次筛选条件。')
        if len(required) > 5:
            raise p.InputError('每种卡最多必带 5 张。')
    leader = settings['required_leader_id']
    if type(leader) is not int or (leader and leader not in mids):
        raise p.InputError('指定队长必须已拥有、已勾选，并符合本次筛选条件。')
    bindings = settings['required_bindings']
    if not isinstance(bindings, list) or len(bindings) > 5:
        raise p.InputError('最多固定 5 组成员与留影绑定。')
    bound_m, bound_s = set(), set()
    for pair in bindings:
        if (not isinstance(pair, dict) or type(pair.get('member_id')) is not int
                or type(pair.get('snap_id')) is not int or pair['member_id'] not in mids
                or pair['snap_id'] not in sids):
            raise p.InputError('固定绑定的成员和留影必须已拥有、已勾选，并符合筛选条件。')
        if pair['member_id'] in bound_m or pair['snap_id'] in bound_s:
            raise p.InputError('固定绑定不能重复使用同一张成员卡或留影。')
        bound_m.add(pair['member_id'])
        bound_s.add(pair['snap_id'])
    settings['required_member_ids'] = sorted(set(settings['required_member_ids']) | bound_m | ({leader} if leader else set()))
    settings['required_snap_ids'] = sorted(set(settings['required_snap_ids']) | bound_s)
    if len(settings['required_member_ids']) > 5 or len(settings['required_snap_ids']) > 5:
        raise p.InputError('必带、队长和固定绑定合计每种卡不能超过 5 张。')
    chars = {i: data.index['MasterMemberCard'][i]['_characterID'] for i in mids}
    if len(set(chars.values())) < 5 or len(sids) < 5:
        raise p.InputError('筛选后至少需要 5 位不同角色的成员卡和 5 张 Snap。')
    required_chars = [chars[i] for i in settings['required_member_ids']]
    if len(set(required_chars)) != len(required_chars):
        raise p.InputError('必带成员卡不能属于同一角色。')
    if settings['pure_type'] and len({data.index['MasterMemberCard'][i]['_cardType'] for i in settings['required_member_ids']}) > 1:
        raise p.InputError('要求同属性时，必带成员的属性必须一致。')
    request = {'schema_version': 1, 'name': raw.get('name', '我的卡库'), 'profile': profile,
               'candidate_member_ids': sorted(mids), 'candidate_snap_ids': sorted(sids),
               'team_settings': settings}
    # The legacy validator checks growth only; no chart is prepared or selected.
    check = p.growth_issues({**request, 'settings': {'normal': {'method': 'ap'}}}, data)
    if not check['complete']:
        labels = '；'.join(i['label'] + '：' + i['reason'] for i in check['issues'][:8])
        raise p.InputError('请补齐筛选后候选卡的实际养成。' + labels)
    return request


def growth_check(raw, data):
    # Keep the full field-by-field validator for the local UI.
    temp = deepcopy(raw.get('input', raw))
    settings = {**DEFAULT, **temp.get('team_settings', {})}
    for key, table, typ in (('candidate_member_ids', 'MasterMemberCard', 'member_type'),
                            ('candidate_snap_ids', 'MasterSupportCard', 'snap_type')):
        selected = []
        for i in temp.get(key, []):
            card = data.index[table].get(i)
            if not card:
                raise p.InputError(f'未知卡牌 #{i}。')
            if not card_allowed(data, card, 'members' if table == 'MasterMemberCard' else 'snaps', settings):
                continue
            selected.append(i)
        temp[key] = selected
    temp['settings'] = {'normal': {'method': 'ap'}}
    return p.growth_issues(temp, data)


def set_power_context(model, settings):
    """Explicit optional attribute scenario, no song id or tag assumptions."""
    model.base, model.b, model.pair = model._power_context(settings['mode'] == 'challenge')
    model.music_type = settings['music_attribute']
    model.static = {}
    for mid in model.mids:
        card, rank, b = model.cards[mid], model.rank[mid], model.b[mid]
        typ = model.music_type
        type_rate = (model.type_base + rank['_musicTypeBonusRate']) if (
            typ and (card['_cardType'] == 99 or card['_cardType'] == typ)) else 0
        model.static[mid] = p.dp._add(
            model.base[mid], model.flat[mid], p.dp._mul_floor(b, model.band[mid]),
            p.dp._mul_floor(b, (type_rate,) * 3), p.dp._mul_floor(b, (model.vip,) * 3))


def skill_area(slot):
    """Raw AP increment x final window milliseconds; explicitly a proxy."""
    total = 0
    for effect in slot['active_live_effects']:
        targets = 1 if effect['skill_effect_type'] == 2000 else effect.get('judgement_targets', []).count(5)
        duration = math.ceil(p.cp._float32(p.cp._float32(effect['activation_ms'])
                                         + p.cp._float32(slot['extension_ms'])))
        total += effect['effect_value'] * duration * targets
    return total


def request_key(request, data):
    raw = json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    return hashlib.sha256(raw + data.fingerprint().encode() + Path(__file__).read_bytes()).hexdigest()


def workbench_catalog(data):
    """Explain the verified ON event rules without borrowing Bandori rules."""
    catalog = data.catalog()
    catalog.pop('songs', None)
    event = data.index['MasterEvent'][1]
    effects = [r for r in data.tables['MasterEventEffect'] if r['_eventId'] == 1]
    names = {0: 'event_pt', 1: 'shop_pt', 2: 'parameter'}
    rules = {}
    for effect in effects:
        kind = 'members' if effect['_resourceTypeConstraint'] == 2 else 'snaps'
        constraints = {k: effect[k] for k in ('_characterId', '_bandId', '_cardType',
                                            '_tagId', '_memberCardId', '_supportCardId')}
        key = (kind, *constraints.values())
        rule = rules.setdefault(key, {'kind': kind, 'constraints': constraints, 'rates': {}})
        rule['rates'][names[effect['_eventBonusType']]] = [effect[f'_rank{i}EffectValue'] for i in range(1, 6)]
    for kind, table in (('members', 'MasterMemberCard'), ('snaps', 'MasterSupportCard')):
        for card in catalog[kind]:
            raw = data.index[table][card['id']]
            matches = [effect for effect in effects if (
                p.eb._matches_member(effect, raw, data.index['MasterCharacter'][raw['_characterID']])
                if kind == 'members' else p.eb._matches_snap(effect, raw, data.index['MasterCharacter']))]
            card['event_bonuses_by_stage'] = [
                {name: sum(e[f'_rank{rank}EffectValue'] for e in matches if names[e['_eventBonusType']] == name)
                 for name in names.values()} for rank in range(1, 6)]
            if kind == 'members':
                live = data.index['MasterLiveSkill'][raw['_liveSkillID']]
                card['live_skill_name'] = data.text[live['_nameTextID']]
    catalog['event'] = {'id': 1, 'name': data.text[event['_nameTextId']],
                        'start_at': event['_startAt'], 'end_at': event['_endAt'],
                        'snapshot': '2026-10-01', 'rules': list(rules.values()),
                        'parameter_bonus_applies_to': 'challenge_only'}
    return catalog


def team_row(mids, binding, leader, power, bonuses, contract, kind='balanced', seconds=120):
    area = sum(skill_area(slot) for slot in contract['slots'])
    return {'member_ids': list(mids), 'snap_ids': list(binding), 'leader_id': leader,
            'power': power, 'bonuses_10000': bonuses,
            'skill_area_raw': area, 'skill_percent_seconds': area / 100000,
            'potential': {str(t): power * (t * 1000 * 10000 + area) / (t * 1000 * 10000) for t in (60, 120, 180)},
            'selected_for': kind, 'reason': KINDS[kind][0] if kind in KINDS else '指定队伍验算',
            'reference_seconds': seconds,
            'slots': [{k: slot[k] for k in ('member_id', 'snap_id', 'live_skill_level',
                                          'extension_ms', 'live_effects', 'support_skills')}
                      for slot in contract['slots']]}


def evaluate(raw, data):
    """Evaluate exactly the supplied five bindings; never start a team search."""
    started = time.monotonic()
    if not isinstance(raw, dict):
        raise p.InputError('指定队伍格式不正确。')
    source = raw.get('input', raw)
    if not isinstance(source, dict):
        raise p.InputError('指定队伍格式不正确。')
    fixed = source.get('fixed_team', {})
    if not isinstance(fixed, dict):
        raise p.InputError('指定队伍格式不正确。')
    mids, binding = fixed.get('member_ids', []), fixed.get('snap_ids', [])
    for values, label in ((mids, '成员'), (binding, '留影')):
        if (not isinstance(values, list) or len(values) != 5
                or any(type(v) is not int or v <= 0 for v in values) or len(set(values)) != 5):
            raise p.InputError(f'指定队伍需要 5 张不重复的{label}卡。')
    context = {**DEFAULT, **source.get('team_settings', {}), 'count': 1, 'strategy': 'balanced',
               'member_type': 0, 'snap_type': 0, 'member_band': 0, 'pure_type': False,
               'required_member_ids': [], 'required_snap_ids': [],
               'member_min_rarity': 0, 'snap_min_rarity': 0,
               'allowed_band_ids': [], 'allowed_character_ids': [],
               'required_leader_id': 0, 'required_bindings': [],
               'min_event_bonus_10000': 0, 'min_shop_bonus_10000': 0}
    request = parse_request({**source, 'candidate_member_ids': mids,
                             'candidate_snap_ids': binding, 'team_settings': context}, data)
    leader = fixed.get('leader_id', 0)
    if type(leader) is not int or (leader and leader not in mids):
        raise p.InputError('队长必须是指定队伍中的成员，或选择自动比较队长。')
    model = p.PowerModel(data, request['profile'], sorted(mids), sorted(binding))
    set_power_context(model, context)
    if leader:
        rates = p.leader_rates(data, model.cards[leader], model.own[leader],
                              [model.cards[m] for m in mids], [model.chars[m] for m in mids], model.music_type)
        vectors = [p.dp._mul_floor(model.b[m], rate) for m, rate in zip(mids, rates)]
    else:
        leader, vectors = model.best_leader(tuple(mids))
    power = model.total(mids, binding, vectors)
    contract = p.sk.derive_ap_skill_contract(data.snapshot, request['profile'], mids, binding,
                                             _verified_inputs=data.skill_inputs)
    row = team_row(mids, binding, leader, power, model.bonuses(mids, binding), contract, 'fixed')
    return {'version': VERSION, 'status': 'complete_fixed_team', 'teams': [row],
            'settings': context, 'bdon_url': BDON,
            'search': {'complete': True, 'solver_calls': 0, 'cached_teams': 0,
                       'candidate_member_count': 5, 'candidate_snap_count': 5,
                       'requested_count': 1, 'exhausted': False,
                       'elapsed_seconds': round(time.monotonic() - started, 3),
                       'algorithm': 'fixed_bindings_direct_evaluation', 'song_count': 0, 'chart_count': 0},
            'assumptions': {'raw_judgement': 'PERFECT', 'assist': False, 'gekisou': False,
                            'each_member_activates_once': True, 'uniform_note_exposure': True,
                            'window_truncation_and_chart_combo': 'not modeled', 'music_tags': 'not included',
                            'fixed_binding_order_is_skill_order': False}}


class Search:
    def __init__(self, request, data, progress=lambda **kw: None, cancelled=lambda: False):
        self.request, self.data = request, data
        self.settings = request['team_settings']
        self.mids = request['candidate_member_ids']
        self.sids = request['candidate_snap_ids']
        self.progress, self.cancelled = progress, cancelled
        self.begin = time.monotonic()
        self.model = p.PowerModel(data, request['profile'], self.mids, self.sids)
        set_power_context(self.model, self.settings)
        self.calls, self.facts, self.exclusions = 0, {}, []
        self.prepare_pairs()
        self.build()
        self.seeds = self.prepare_seeds()

    def check(self):
        if self.cancelled():
            raise p.Cancelled()

    def emit(self, stage, **kw):
        self.check()
        self.progress(stage=stage, elapsed_seconds=round(time.monotonic() - self.begin, 2), **kw)

    def prepare_pairs(self):
        self.emit('核对成员技能、留影延长与适用条件')
        helper = SimpleNamespace(
            mids=self.mids, sids=self.sids, model=self.model, data=self.data,
            request=self.request, specs={'normal': [{'method': 'ap'}]},
            check=self.check, emit=lambda: self.check())
        upstream_solver.Search.prepare_pairs(helper)
        self.pairs = helper.pairs
        self.areas = {pair: skill_area(slot) for pair, slot in self.pairs.items()}

    def leader_bounds(self):
        """Overestimate only positive formation predicates; oracle fixes real teams."""
        result, dependent = {}, False
        for leader in self.mids:
            card, own = self.model.cards[leader], self.model.own[leader]
            rows = [e for e in self.data.tables['MasterLeaderSkillEffect']
                    if e['_leaderSkillID'] == card['_leaderSkillID'] and e['_level'] == own['leader_skill_level']]
            relaxed = []
            for row in rows:
                e = dict(row)
                if e['_skillEffectType'] == 0:
                    relaxed.append(e)
                    continue
                if e['_effectValue'] < 0:
                    raise p.InputError('尚未覆盖负值队长加成。')
                if e['_skillConditionGroup'] or e['_skillCumulativeConditionID']:
                    dependent = True
                if 1500 <= e['_skillEffectType'] <= 1503:
                    cum = self.data.index['MasterSkillCumulativeCondition'][e['_skillCumulativeConditionID']]
                    e['_effectValue'] *= min(5, cum['_maxCumulativeCount']) if cum['_maxCumulativeCount'] >= 1 else 5
                    e['_skillEffectType'] -= 500
                e['_skillConditionGroup'] = e['_skillCumulativeConditionID'] = 0
                relaxed.append(e)
            fake = SimpleNamespace(tables={**self.data.tables, 'MasterLeaderSkillEffect': relaxed}, index=self.data.index)
            for m in self.mids:
                rates = p.leader_rates(fake, card, own, [card] + [self.model.cards[m]] * 4,
                                      [self.model.chars[leader]] + [self.model.chars[m]] * 4,
                                      self.model.music_type)[1]
                result[leader, m] = upstream_solver._integer_weight(p.dp._mul_floor(self.model.b[m], rates))
        return result, dependent

    def build(self):
        self.emit('准备联合配队模型')
        self.base = cm = cp_model.CpModel()
        self.x = {m: cm.new_bool_var('member_' + str(m)) for m in self.mids}
        self.y = {s: cm.new_bool_var('snap_' + str(s)) for s in self.sids}
        self.z = {(m, s): cm.new_bool_var(f'bind_{m}_{s}') for m in self.mids for s in self.sids}
        self.leaders = {m: cm.new_bool_var('leader_' + str(m)) for m in self.mids}
        cm.add(sum(self.x.values()) == 5)
        cm.add(sum(self.y.values()) == 5)
        cm.add(sum(self.leaders.values()) == 1)
        groups = defaultdict(list)
        for m in self.mids:
            groups[self.model.cards[m]['_characterID']].append(self.x[m])
            cm.add(sum(self.z[m, s] for s in self.sids) == self.x[m])
            cm.add(self.leaders[m] <= self.x[m])
        for values in groups.values():
            cm.add(sum(values) <= 1)
        for s in self.sids:
            cm.add(sum(self.z[m, s] for m in self.mids) == self.y[s])
        for m in self.settings['required_member_ids']:
            cm.add(self.x[m] == 1)
        for s in self.settings['required_snap_ids']:
            cm.add(self.y[s] == 1)
        if self.settings['required_leader_id']:
            cm.add(self.leaders[self.settings['required_leader_id']] == 1)
        for pair in self.settings['required_bindings']:
            cm.add(self.z[pair['member_id'], pair['snap_id']] == 1)
        if self.settings['pure_type']:
            flags = []
            for typ in sorted({c['_cardType'] for c in self.model.cards.values()}):
                flag = cm.new_bool_var('pure_' + str(typ))
                cm.add(sum(self.x[m] for m in self.mids if self.model.cards[m]['_cardType'] == typ) == 5).only_enforce_if(flag)
                flags.append(flag)
            cm.add(sum(flags) == 1)
        bounds, self.dependent = self.leader_bounds()
        self.leadparts = {}
        for m in self.mids:
            upper = max(bounds[l, m] for l in self.mids)
            part = self.leadparts[m] = cm.new_int_var(0, upper, 'leader_part_' + str(m))
            expr = cp_model.LinearExpr.sum([bounds[l, m] * self.leaders[l] for l in self.mids])
            cm.add(part == 0).only_enforce_if(self.x[m].Not())
            if self.dependent:
                cm.add(part <= expr)
            else:
                cm.add(part == expr).only_enforce_if(self.x[m])
        static = {m: upstream_solver._integer_weight(self.model.static[m]) for m in self.mids}
        pair = {key: upstream_solver._integer_weight(value) for key, value in self.model.pair.items()}
        upper = sum(sorted((static[m] + max(pair[m, s] for s in self.sids)
                            + max(bounds[l, m] for l in self.mids) for m in self.mids), reverse=True)[:5])
        self.power = cm.new_int_var(1, upper, 'power')
        cm.add(self.power == cp_model.LinearExpr.sum([static[m] * self.x[m] for m in self.mids]
                   + [pair[key] * flag for key, flag in self.z.items()] + list(self.leadparts.values())))
        area_upper = sum(sorted((max(self.areas[m, s] for s in self.sids) for m in self.mids), reverse=True)[:5])
        self.area = cm.new_int_var(0, area_upper, 'skill_area')
        cm.add(self.area == cp_model.LinearExpr.sum([self.areas[key] * flag for key, flag in self.z.items()]))
        self.bonuses = {}
        for name in ('event_pt', 'shop_pt'):
            key = name + '_bonus_10000'
            maximum = sum(sorted((self.model.mb[m][key] for m in self.mids), reverse=True)[:5]) + sum(sorted((self.model.sb[s][key] for s in self.sids), reverse=True)[:5])
            value = self.bonuses[name] = cm.new_int_var(0, maximum, name)
            cm.add(value == cp_model.LinearExpr.sum([self.model.mb[m][key] * self.x[m] for m in self.mids]
                         + [self.model.sb[s][key] * self.y[s] for s in self.sids]))
            cm.add(value >= self.settings['min_event_bonus_10000' if name == 'event_pt' else 'min_shop_bonus_10000'])
        self.potential = {}
        for seconds in (60, 120, 180):
            offset = seconds * 1000 * 10000
            limit = upper * (offset + area_upper)
            if limit >= 1 << 62:
                raise p.InputError('配队参考指标超出整数范围。')
            value = self.potential[seconds] = cm.new_int_var(0, limit, 'potential_' + str(seconds))
            cm.add_multiplication_equality(value, [self.power, self.area + offset])

    def native_solve(self, model):
        self.check()
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 4
        solver.parameters.random_seed = 19471
        stop = threading.Event()
        def watch():
            while not stop.wait(.04):
                if self.cancelled():
                    solver.stop_search()
                    return
        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            status = solver.solve(model)
        finally:
            stop.set()
            watcher.join()
        self.check()
        self.calls += 1
        if status not in (cp_model.OPTIMAL, cp_model.INFEASIBLE):
            raise p.InputError('配队搜索未完成，请重试；本次不发布未完成结果。')
        return status, solver

    def prepare_seeds(self):
        """Legal hints only; every candidate and all proxy proofs remain available."""
        self.emit('准备合法初始配队')
        required_m = set(self.settings['required_member_ids'])
        required_s = set(self.settings['required_snap_ids'])
        proposals, seen = [], set()
        for direction in ('balanced', 'short', 'event', 'shop', 'power', 'skill'):
            def mkey(m):
                power = sum(self.model.static[m]) + max(sum(self.model.pair[m, s]) for s in self.sids)
                area = max(self.areas[m, s] for s in self.sids)
                proxy = power * (120 * 1000 * 10000 + 5 * area)
                if direction in ('event', 'shop'):
                    return (self.model.mb[m][('event_pt' if direction == 'event' else 'shop_pt') + '_bonus_10000'], proxy)
                return (area, power) if direction == 'skill' else (power, area) if direction == 'power' else (proxy, power)
            selected = sorted(required_m)
            used = {self.model.cards[m]['_characterID'] for m in selected}
            for m in sorted(self.mids, key=lambda m: (mkey(m), -m), reverse=True):
                if len(selected) == 5:
                    break
                if self.model.cards[m]['_characterID'] in used:
                    continue
                if self.settings['pure_type'] and selected and self.model.cards[m]['_cardType'] != self.model.cards[selected[0]]['_cardType']:
                    continue
                selected.append(m)
                used.add(self.model.cards[m]['_characterID'])
            if len(selected) != 5:
                continue
            mids = tuple(sorted(selected))
            leader = self.settings['required_leader_id']
            if leader:
                rates = p.leader_rates(self.data, self.model.cards[leader], self.model.own[leader],
                                      [self.model.cards[m] for m in mids], [self.model.chars[m] for m in mids],
                                      self.model.music_type)
                vectors = [p.dp._mul_floor(self.model.b[m], r) for m, r in zip(mids, rates)]
            else:
                leader, vectors = self.model.best_leader(mids)
            def skey(s):
                power = max(sum(self.model.pair[m, s]) for m in mids)
                area = max(self.areas[m, s] for m in mids)
                proxy = power * (120 * 1000 * 10000 + 5 * area)
                if direction in ('event', 'shop'):
                    return (self.model.sb[s][('event_pt' if direction == 'event' else 'shop_pt') + '_bonus_10000'], proxy)
                return (area, power) if direction == 'skill' else (power, area) if direction == 'power' else (proxy, power)
            ordered = sorted((s for s in self.sids if s not in required_s), key=lambda s: (skey(s), -s), reverse=True)
            take = 5 - len(required_s)
            snapsets = [tuple(sorted(required_s)) + tuple(ordered[:take])]
            if take:
                snapsets += [tuple(sorted(required_s)) + tuple(ordered[:take-1]) + (s,) for s in ordered[take:take+16]]
            for snapset in snapsets:
                identity = mids, tuple(sorted(snapset))
                if len(snapset) != 5 or identity in seen:
                    continue
                seen.add(identity)
                best = None
                for binding in itertools.permutations(snapset):
                    mapping = dict(zip(mids, binding))
                    if any(mapping[pair['member_id']] != pair['snap_id'] for pair in self.settings['required_bindings']):
                        continue
                    power = self.model.total(mids, binding, vectors)
                    area = sum(self.areas[m, s] for m, s in zip(mids, binding))
                    bonuses = self.model.bonuses(mids, binding)
                    if (bonuses['event_pt'] < self.settings['min_event_bonus_10000']
                            or bonuses['shop_pt'] < self.settings['min_shop_bonus_10000']):
                        continue
                    proxy = power * (120 * 1000 * 10000 + area)
                    key = ((bonuses['event_pt'], proxy) if direction == 'event' else
                           (bonuses['shop_pt'], proxy) if direction == 'shop' else
                           (area, power) if direction == 'skill' else
                           (power, area) if direction == 'power' else (proxy, power))
                    if best is None or key > best[0]:
                        best = (key, {'mids': mids, 'binding': binding, 'leader': leader,
                                     'vectors': vectors, 'power': power, 'area': area, 'bonuses': bonuses})
                if best is not None:
                    proposals.append(best[1])
        return proposals

    def hint(self, cm, kind):
        options = [row for row in self.seeds if (row['mids'], tuple(sorted(row['binding']))) not in self.exclusions]
        if not options:
            return
        def key(row):
            proxy = row['power'] * (KINDS[kind][1] * 1000 * 10000 + row['area'])
            return ((row['bonuses']['event_pt'], proxy) if kind == 'event' else
                    (row['bonuses']['shop_pt'], proxy) if kind == 'shop' else
                    (row['power'], proxy) if kind == 'power' else
                    (row['area'], row['power']) if kind == 'skill' else (proxy,))
        row = max(options, key=key)
        binding = dict(zip(row['mids'], row['binding']))
        for m, var in self.x.items():
            cm.add_hint(var, int(m in binding))
        for s, var in self.y.items():
            cm.add_hint(var, int(s in row['binding']))
        for (m, s), var in self.z.items():
            cm.add_hint(var, int(binding.get(m) == s))
        for m, var in self.leaders.items():
            cm.add_hint(var, int(m == row['leader']))
        vectors = dict(zip(row['mids'], row['vectors']))
        for m, var in self.leadparts.items():
            cm.add_hint(var, upstream_solver._integer_weight(vectors[m]) if m in vectors else 0)
        cm.add_hint(self.power, row['power'])
        cm.add_hint(self.area, row['area'])
        for name, var in self.bonuses.items():
            cm.add_hint(var, row['bonuses'][name])
        for seconds, var in self.potential.items():
            cm.add_hint(var, row['power'] * (seconds * 1000 * 10000 + row['area']))

    def identify(self, solver):
        mids = tuple(m for m in self.mids if solver.value(self.x[m]))
        binding = tuple(next(s for s in self.sids if solver.value(self.z[m, s])) for m in mids)
        leader = next(m for m in mids if solver.value(self.leaders[m]))
        vectors = [p.dp._mul_floor(self.model.b[m], rate) for m, rate in zip(mids,
            p.leader_rates(self.data, self.model.cards[leader], self.model.own[leader],
                [self.model.cards[m] for m in mids], [self.model.chars[m] for m in mids], self.model.music_type))]
        power = self.model.total(mids, binding, vectors)
        return mids, binding, leader, vectors, power

    def add_fact(self, mids):
        if mids in self.facts:
            return
        for leader in mids:
            rates = p.leader_rates(self.data, self.model.cards[leader], self.model.own[leader],
                [self.model.cards[m] for m in mids], [self.model.chars[m] for m in mids], self.model.music_type)
            conditions = [self.x[m] for m in mids] + [self.leaders[leader]]
            for m, rate in zip(mids, rates):
                value = upstream_solver._integer_weight(p.dp._mul_floor(self.model.b[m], rate))
                self.base.add(self.leadparts[m] == value).only_enforce_if(conditions)
        self.facts[mids] = True

    def exclude(self, row):
        mids, sids = row['member_ids'], row['snap_ids']
        self.base.add(sum(self.x[m] for m in mids) + sum(self.y[s] for s in sids) <= 9)
        self.exclusions.append((tuple(mids), tuple(sorted(sids))))

    def solve_next(self, kind):
        label, seconds = KINDS[kind]
        proxy = self.potential[seconds]
        objectives = ([self.bonuses['event_pt'], proxy] if kind == 'event' else
                      [self.bonuses['shop_pt'], proxy] if kind == 'shop' else
                      [self.power, proxy] if kind == 'power' else
                      [self.area, self.power] if kind == 'skill' else [proxy])
        while True:
            self.emit('正在寻找：' + label)
            cm = self.base.clone()
            self.hint(cm, kind)
            solver = None
            for objective in objectives:
                cm.maximize(objective)
                status, solver = self.native_solve(cm)
                if status == cp_model.INFEASIBLE:
                    return None
                cm.add(objective == solver.value(objective))
                cm.clear_hints()
                for index in range(len(cm.proto.variables)):
                    var = cm.get_int_var_from_proto_index(index)
                    cm.add_hint(var, solver.value(var))
            mids, binding, leader, vectors, power = self.identify(solver)
            if power != solver.value(self.power):
                self.add_fact(mids)
                continue
            contract = p.sk.derive_ap_skill_contract(self.data.snapshot, self.request['profile'], list(mids), list(binding), _verified_inputs=self.data.skill_inputs)
            area = sum(skill_area(slot) for slot in contract['slots'])
            if area != solver.value(self.area):
                raise RuntimeError('Skill proxy differs from verified contract')
            bonuses = self.model.bonuses(mids, binding)
            return {**team_row(mids, binding, leader, power, bonuses, contract, kind, seconds),
                    'verified_selection_objective': [solver.value(o) for o in objectives]}

    def run(self, checkpoint=None, save=lambda value: None):
        rows = deepcopy(checkpoint.get('rows', [])) if checkpoint else []
        for row in rows:
            self.exclude(row)
        cached = len(rows)
        count, strategy = self.settings['count'], self.settings['strategy']
        sequence = ['short', 'balanced', 'long', 'event', 'shop'] if strategy == 'portfolio' else [strategy]
        exhausted = False
        while len(rows) < count:
            self.emit(f'已找到 {len(rows)} / {count} 套不重复卡组', done=len(rows), total=count)
            row = self.solve_next(sequence[len(rows) % len(sequence)])
            if row is None:
                exhausted = True
                break
            rows.append(row)
            self.exclude(row)
            save({'rows': rows, 'complete': False})
        self.check()
        result = {'version': VERSION, 'status': 'complete_candidate_portfolio', 'teams': rows,
                  'settings': self.settings, 'bdon_url': BDON,
                  'search': {'complete': True, 'solver_calls': self.calls, 'cached_teams': cached,
                             'candidate_member_count': len(self.mids), 'candidate_snap_count': len(self.sids),
                             'requested_count': count, 'exhausted': exhausted,
                             'elapsed_seconds': round(time.monotonic() - self.begin, 3),
                             'algorithm': 'cp_sat_song_independent_proxy_portfolio',
                             'song_count': 0, 'chart_count': 0,
                             'proof_scope': 'each declared proxy among remaining distinct card sets; no actual-song optimum claim'},
                  'assumptions': {'raw_judgement': 'PERFECT', 'assist': False, 'gekisou': False,
                                  'each_member_activates_once': True, 'uniform_note_exposure': True,
                                  'window_truncation_and_chart_combo': 'not modeled',
                                  'music_tags': 'not included',
                                  'formula': 'power * (1 + sum(AP increment * effective milliseconds) / reference milliseconds)',
                                  'portfolio_order': sequence,
                                  'deduplication': 'member card set plus Snap card set; one objective-specific binding per set'}}
        save({'rows': rows, 'complete': True, 'result': result})
        return result


def optimize(raw, data, progress=lambda **kw: None, cancelled=lambda: False, cache=None):
    request = parse_request(raw, data)
    key = request_key(request, data)
    checkpoint = cache.get(key) if cache else None
    if cancelled():
        raise p.Cancelled()
    if checkpoint and checkpoint.get('complete'):
        result = deepcopy(checkpoint['result'])
        result['search'].update(cached_complete_result=True, cached_teams=len(result['teams']),
                                source_solver_calls=result['search']['solver_calls'], solver_calls=0, elapsed_seconds=0)
        return result
    search = Search(request, data, progress, cancelled)
    return search.run(checkpoint, (lambda value: cache.put(key, value)) if cache else lambda value: None)

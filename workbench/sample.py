"""Synthetic demonstration only; no personal inventory."""
import team_candidates as teams


def sample(data):
    catalog = data.catalog()
    mids, sids = [59, 11, 55, 26, 3], [33, 37, 52, 61, 3, 1, 2, 5, 6, 7]
    members = {x['id']: x for x in catalog['members']}
    snaps = {x['id']: x for x in catalog['snaps']}
    return {'schema_version': 1, 'name': '合成测试卡库（请勿当作实际养成）', 'is_demo': True,
            'candidate_member_ids': mids, 'candidate_snap_ids': sids,
            'team_settings': dict(teams.DEFAULT),
            'profile': {'schema_version': 1, 'inventory': {
                'members': [{'id': i, 'level': min(20, members[i]['caps'][0]), 'training_count': 0,
                             'awakening_count': 0, 'live_skill_level': 1, 'gekisou_skill_level': 1} for i in mids],
                'snaps': [{'id': i, 'level': min(20, snaps[i]['caps'][0]), 'limit_break_count': 0} for i in sids]},
                'character_ranks': [{'character_id': x['id'], 'rank': 5} for x in catalog['characters']],
                'character_total_rank': 5 * len(catalog['characters']), 'tgw_card_rank': 1,
                'facilities': [{'id': x['id'], 'level': min(3, x['max_level'])} for x in catalog['facilities']]}}


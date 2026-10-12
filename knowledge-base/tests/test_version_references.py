from copy import deepcopy

import pytest

from food_kb.common import KBError
from food_kb.versions.references import expand_references


EVIDENCE = [{'source_ref': 'synthetic:reference', 'locator_quality': 'synthetic'}]


def ref(rid, start, target, nature='normative'):
    return {'reference_id': rid, 'from_version_id': start, 'target': {'version_ids': [target]},
            'nature': {'value': nature, 'verification_state': 'verified', 'evidence': EVIDENCE},
            'scope': {'value': 'clause', 'refs': ['SYNTHETIC-2'], 'verification_state': 'verified', 'evidence': EVIDENCE},
            'binding': {'datedness': 'dated', 'designation': f'SYNTH {target}-2020', 'version_ids': [target], 'verification_state': 'verified', 'evidence': EVIDENCE},
            'condition': {'state': 'unconditional', 'ast': None, 'evidence': EVIDENCE},
            'evidence': EVIDENCE, 'review_state': 'approved'}


def snapshot(*refs):
    return {'snapshot_id': 'SYNTHETIC', 'versions': [{'version_id': k, 'designation': f'SYNTH {k}-2020'} for k in ['A', 'B', 'C', 'D']],
            'references': list(refs), 'gaps': []}


def test_sourced_dependency_chain_retains_all_three_dimensions_and_path():
    data = snapshot(ref('1', 'A', 'B'), ref('2', 'B', 'C')); before = deepcopy(data)
    result = expand_references(data, ['A'])
    assert result['visited_version_ids'] == ['A', 'B', 'C'] and result['complete']
    assert result['trace'][1]['path'] == ['A', 'B'] and result['selected_reference_ids'] == ['1', '2']
    assert result['executable'] is False and data == before


def test_cycle_terminates_and_diamond_is_not_false_cycle():
    result = expand_references(snapshot(ref('1', 'A', 'B'), ref('2', 'B', 'A')), ['A'])
    assert result['cycles'][0]['path'] == ['A', 'B', 'A'] and not result['complete']
    diamond = snapshot(ref('1', 'A', 'B'), ref('2', 'A', 'C'), ref('3', 'B', 'D'), ref('4', 'C', 'D'))
    result = expand_references(diamond, ['A'])
    assert set(result['visited_version_ids']) == {'A', 'B', 'C', 'D'} and result['cycles'] == [] and result['complete']


@pytest.mark.parametrize('budget,reason', [('max_depth', 'DEPTH_BUDGET_EXHAUSTED'), ('max_nodes', 'NODE_BUDGET_EXHAUSTED'), ('max_edges', 'EDGE_BUDGET_EXHAUSTED')])
def test_each_resource_budget_leaves_explicit_omitted_targets(budget, reason):
    data = snapshot(ref('1', 'A', 'B'), ref('2', 'B', 'C'))
    result = expand_references(data, ['A'], **{budget: 0 if budget == 'max_depth' else 1})
    assert not result['complete'] and any(u['reason'] == reason for u in result['unexpanded'])


@pytest.mark.parametrize('nature', ['mention', 'informative', 'replaces', 'amends'])
def test_mentions_and_version_events_do_not_become_normative_dependencies(nature):
    result = expand_references(snapshot(ref('1', 'A', 'B', nature)), ['A'])
    assert result['visited_version_ids'] == ['A'] and result['selected_reference_ids'] == []
    assert result['trace'][0]['state'] == 'not_dependency'


@pytest.mark.parametrize('dimension', ['nature', 'scope', 'binding'])
def test_any_unverified_dimension_remains_unknown_not_selected(dimension):
    relation = ref('1', 'A', 'B'); relation[dimension]['verification_state'] = 'unverified'
    result = expand_references(snapshot(relation), ['A'])
    assert result['trace'][0]['state'] == 'unknown' and not result['complete']
    assert result['visited_version_ids'] == ['A']


def test_explicit_candidate_exploration_does_not_close_dependency_evidence():
    relation = ref('1', 'A', 'B', 'mention'); relation['nature']['verification_state'] = 'unverified'
    result = expand_references(snapshot(relation), ['A'], mode='candidates')
    assert result['visited_version_ids'] == ['A', 'B'] and not result['complete'] and result['candidate_only']


def test_undated_reference_retains_versions_and_does_not_pick_latest():
    relation = ref('1', 'A', 'B'); relation['binding']['datedness'] = 'undated'; relation['binding']['version_ids'] = ['B', 'C']
    result = expand_references(snapshot(relation), ['A'])
    assert result['visited_version_ids'] == ['A'] and len(result['unexpanded']) == 2 and not result['complete']


@pytest.mark.parametrize('change,code', [
    ('missing_datedness', 'REFERENCE_DATEDNESS_UNKNOWN'),
    ('dated_without_year', 'DATED_REFERENCE_DESIGNATION_MISSING'),
    ('dated_wrong_version', 'DATED_REFERENCE_VERSION_MISMATCH'),
    ('empty_clause', 'REFERENCE_SCOPE_TARGET_MISSING'),
    ('false_evidence', 'REFERENCE_SCOPE_UNVERIFIED'),
    ('undated_no_context', 'UNDATED_REFERENCE_CONTEXT_MISSING'),
])
def test_dimension_claims_cannot_bypass_target_and_context_checks(change, code):
    relation = ref('1', 'A', 'B')
    if change == 'missing_datedness':
        relation['binding'].pop('datedness')
    elif change == 'dated_without_year':
        relation['binding']['designation'] = 'SYNTH B'
    elif change == 'dated_wrong_version':
        relation['binding']['designation'] = 'SYNTH B-2021'
    elif change == 'empty_clause':
        relation['scope']['refs'] = []
    elif change == 'false_evidence':
        relation['scope']['evidence'] = ['synthetic-not-an-anchor']
    else:
        relation['binding']['datedness'] = 'undated'
    result = expand_references(snapshot(relation), ['A'])
    assert result['visited_version_ids'] == ['A'] and not result['complete']
    assert code in result['trace'][0]['reason_codes']


def test_missing_node_and_missing_target_leave_gap_not_complete_empty_result():
    assert not expand_references(snapshot(), ['missing'])['complete']
    relation = ref('1', 'A', 'B'); relation['binding']['version_ids'] = []
    result = expand_references(snapshot(relation), ['A'])
    assert result['selected_reference_ids'] == [] and not result['complete']


def test_undated_binding_cannot_select_a_different_standard_family():
    relation = ref('1', 'A', 'B'); relation['binding'].update({'datedness': 'undated', 'designation': 'SYNTH C', 'time_context': {}})
    result = expand_references(snapshot(relation), ['A'])
    assert 'UNDATED_REFERENCE_FAMILY_MISMATCH' in result['trace'][0]['reason_codes']
    assert result['visited_version_ids'] == ['A'] and not result['complete']


@pytest.mark.parametrize('kwargs', [{'max_depth': True}, {'max_nodes': 0}, {'max_edges': 50001}, {'max_depth': -1}, {'mode': 'latest'}])
def test_invalid_budget_or_mode_rejected(kwargs):
    with pytest.raises(KBError):
        expand_references(snapshot(), ['A'], **kwargs)

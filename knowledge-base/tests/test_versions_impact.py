"""Synthetic incremental-history proofs; no rule or business task is invented."""
from copy import deepcopy

import pytest

from food_kb.versions.impact import impact_snapshots


def snapshot(identifier='old', **members):
    return {'snapshot_id': identifier, 'versions': [{'version_id': 'v1', 'designation': 'SYNTHETIC-v1'}], **members}


def dependency(source_kind, source_id, kind, identifier, evidence=True):
    return {'depends_on': {'kind': source_kind, 'id': source_id},
            'dependent': {'kind': kind, 'id': identifier},
            'evidence': [{'source_id': 'SYNTHETIC_DEPENDENCY'}] if evidence else []}


def refs(result):
    return {(item['kind'], item['id']) for item in result['affected']}


def test_new_version_preserves_old_release_and_traces_only_explicit_consumers():
    old = snapshot(dependencies=[dependency('version', 'v1', 'classification', 'cat1'),
                                dependency('classification', 'cat1', 'requirement', 'req1'),
                                dependency('requirement', 'req1', 'rule', 'rule1'),
                                dependency('rule', 'rule1', 'task', 'task1')])
    new = deepcopy(old); new['snapshot_id'] = 'new'; new['versions'][0]['body_assets'] = [{'sha256': 'NEW_BYTES'}]
    before = deepcopy((old, new))
    result = impact_snapshots(old, new)
    assert refs(result) == {('version', 'v1'), ('classification', 'cat1'), ('requirement', 'req1'),
                            ('rule', 'rule1'), ('task', 'task1')}
    assert (old, new) == before and result == impact_snapshots(old, new)
    assert result['history_unchanged'] and not result['executable'] and not result['scope_complete']
    assert result['traversal_complete']


def test_identical_business_content_ignores_only_derived_snapshot_fields():
    old = snapshot(); new = deepcopy(old)
    new['snapshot_id'] = 'new'; new['input_hash'] = 'other'
    new['versions'][0]['created_at'] = 'new storage time'
    result = impact_snapshots(old, new)
    assert result['changes'] == [] and result['affected'] == []


def test_removing_version_retains_old_consumers_and_does_not_invent_unrelated_tasks():
    old = snapshot(dependencies=[dependency('version', 'v1', 'requirement', 'known-req')])
    new = snapshot('new', versions=[])
    result = impact_snapshots(old, new)
    assert result['changes'][0]['action'] == 'removed'
    assert refs(result) == {('version', 'v1'), ('requirement', 'known-req')}


def test_changed_reference_marks_consuming_version_and_its_categories():
    old = snapshot(references=[{'reference_id': 'r1', 'from_version_id': 'v1', 'nature': 'unknown'}],
                   dependencies=[dependency('version', 'v1', 'classification', 'cat1')])
    new = deepcopy(old); new['references'][0]['nature'] = 'normative'
    result = impact_snapshots(old, new)
    assert refs(result) == {('reference', 'r1'), ('version', 'v1'), ('classification', 'cat1')}
    assert result['changes'][0]['kind'] == 'reference'


def test_amendment_event_changes_mark_explicit_target_not_replace_body():
    old = snapshot(events=[])
    new = snapshot('new', events=[{'event_id': 'amend1', 'target_version_id': 'v1', 'kind': 'amends',
                                 'verification_state': 'unverified'}])
    result = impact_snapshots(old, new)
    assert refs(result) == {('event', 'amend1'), ('version', 'v1')}
    assert old['versions'] == new['versions']
    assert all(item['action'] == 'recheck' for item in result['affected'])


def test_notice_version_ids_contract_marks_explicit_consumers():
    old = snapshot(events=[])
    new = snapshot('new', events=[{'event_id': 'notice1', 'version_ids': ['v1'], 'kind': 'notice'}])
    assert refs(impact_snapshots(old, new)) == {('event', 'notice1'), ('version', 'v1')}


def test_removing_gap_rechecks_its_old_explicitly_affected_objects():
    old = snapshot(gaps=[{'gap_id': 'g1', 'kind': 'missing_body', 'target': {'kind': 'version', 'id': 'v1'},
                         'affected_refs': [{'kind': 'requirement', 'id': 'known-req'}]}])
    new = snapshot('new', gaps=[])
    assert refs(impact_snapshots(old, new)) == {('gap', 'g1'), ('version', 'v1'), ('requirement', 'known-req')}


@pytest.mark.parametrize('evidence', [[None], ['unanchored text'], [{}]])
def test_malformed_dependency_evidence_is_visible_and_cannot_seed_consumer(evidence):
    dep = dependency('version', 'v1', 'rule', 'bad-rule'); dep['evidence'] = evidence
    old = snapshot(dependencies=[dep]); new = deepcopy(old); new['versions'][0]['designation'] = 'new'
    result = impact_snapshots(old, new)
    assert refs(result) == {('version', 'v1')}
    assert result['unresolved'][0]['code'] == 'DEPENDENCY_UNRESOLVED'


@pytest.mark.parametrize(('collection', 'id_key', 'kind'), [('classifications', 'id', 'classification'),
    ('requirements', 'id', 'requirement'), ('gaps', 'gap_id', 'gap')])
def test_exact_changed_object_is_located(collection, id_key, kind):
    old = snapshot(**{collection: [{id_key: 'x', 'value': '1'}]})
    new = deepcopy(old); new[collection][0]['value'] = '2'
    result = impact_snapshots(old, new)
    assert refs(result) == {(kind, 'x')}
    assert result['changes'][0]['before_hash'] != result['changes'][0]['after_hash']


def test_unsourced_dependency_is_gap_and_never_confirmed_impact():
    old = snapshot(dependencies=[dependency('version', 'v1', 'rule', 'unsourced-rule', evidence=False)])
    new = deepcopy(old); new['versions'][0]['designation'] = 'changed'
    result = impact_snapshots(old, new)
    assert refs(result) == {('version', 'v1')}
    assert result['unresolved'][0]['code'] == 'DEPENDENCY_UNRESOLVED'
    assert not result['traversal_complete']


def test_dependency_cycle_terminates_once_per_object():
    old = snapshot(dependencies=[dependency('version', 'v1', 'requirement', 'r1'),
                                dependency('requirement', 'r1', 'version', 'v1')])
    new = deepcopy(old); new['versions'][0]['designation'] = 'new'
    result = impact_snapshots(old, new)
    assert len(result['affected']) == 2 and len(result['trace']) == 2


def test_node_and_edge_budgets_leave_visible_unexpanded_work():
    old = snapshot(dependencies=[dependency('version', 'v1', 'requirement', 'r1'),
                                dependency('requirement', 'r1', 'rule', 'rule1')])
    new = deepcopy(old); new['versions'][0]['designation'] = 'new'
    nodes = impact_snapshots(old, new, max_nodes=1)
    assert nodes['unresolved'][0]['code'] == 'IMPACT_NODE_BUDGET'
    assert nodes['unresolved'][0]['unexpanded'] == [{'kind': 'requirement', 'id': 'r1'}]
    edges = impact_snapshots(old, new, max_edges=1)
    assert edges['unresolved'][0]['code'] == 'IMPACT_EDGE_BUDGET'


@pytest.mark.parametrize('budget', [0, -1, True, 100001, '10'])
def test_invalid_resource_budget_rejected(budget):
    with pytest.raises(ValueError, match='budgets'):
        impact_snapshots(snapshot(), snapshot('new'), max_nodes=budget)


def test_duplicate_id_rejected_instead_of_silently_overwriting_history():
    old = snapshot(versions=[{'version_id': 'v1'}, {'version_id': 'v1'}])
    with pytest.raises(ValueError, match='unique'):
        impact_snapshots(old, snapshot('new'))

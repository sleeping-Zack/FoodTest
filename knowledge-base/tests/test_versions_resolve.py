"""Synthetic policy proofs, independent of real unverified source candidates."""
from copy import deepcopy

import pytest

from food_kb.versions.resolve import resolve_versions

EVIDENCE = [{'source_id': 'SYNTHETIC_POLICY', 'quote': 'Synthetic date policy; not a food standard.'}]


def date_record(value='2021-08-10', role='sampling', state='verified'):
    return {'value': value, 'raw_value': value, 'role': role,
            'source_ref': 'SYNTHETIC_INPUT', 'verification_state': state}


def endpoint(value, inclusive=True):
    return dict(date_record(value), inclusive=inclusive)


def condition(ast=None):
    return {'state': 'structured' if ast else 'unconditional', 'ast': ast,
            'raw': 'SYNTHETIC', 'evidence': deepcopy(EVIDENCE), 'required_facts': []}


def policy(start=None, end=None, role='sampling', ast=None):
    return {'policy_id': 'SYNTHETIC_POLICY', 'date_role': role,
            'from': endpoint(start) if start else None, 'until': endpoint(end) if end else None,
            'condition': condition(ast), 'evidence': deepcopy(EVIDENCE),
            'source_ref': 'SYNTHETIC_POLICY', 'verification_state': 'verified'}


def version(identifier='old', policies=None, status='deprecated'):
    return {'version_id': identifier, 'document_id': 'SYNTHETIC_DOCUMENT',
            'designation': 'SYNTHETIC ' + identifier, 'date_claims': [],
            'status_claims': [{'value': status, 'verification_state': 'unverified'}],
            'policies': [policy(end='2022-01-01')] if policies is None else policies,
            'identity_state': 'unverified', 'review_state': 'review_pending',
            'body_assets': [{'asset_id': 'SYNTHETIC_BODY', 'sha256': 'SYNTHETIC'}]}


def snapshot(*versions):
    return {'snapshot_id': 'SYNTHETIC_SNAPSHOT', 'versions': list(versions)}


def fact(value, state='verified'):
    return {'raw_value': value, 'normalized_value': value, 'type': 'string',
            'role': 'sample_fact', 'source_ref': 'SYNTHETIC_SAMPLE', 'verification_state': state}


def test_historical_sample_keeps_old_deprecated_version_and_ignores_export():
    old = version(); old['policies'][0]['until']['inclusive'] = False
    new = version('new', [policy(start='2022-01-01')], status='current')
    sources = snapshot(old, new)
    context = {'dates': [date_record(), date_record('2026-10-12', 'export')]}
    before = deepcopy((sources, context))
    result = resolve_versions(sources, context)
    assert result['selected_version_id'] == 'old'
    assert result['status'] == 'applicable'
    assert [item['status'] for item in result['candidates']] == ['applicable', 'not_applicable']
    assert not result['executable'] and not result['scope_complete']
    assert all(not candidate['executable'] for candidate in result['candidates'])
    assert (sources, context) == before
    assert result == resolve_versions(sources, context)
    context['dates'][0] = date_record('2022-01-01')
    assert resolve_versions(sources, context)['selected_version_id'] == 'new'


@pytest.mark.parametrize(('value', 'lower', 'upper', 'expected'), [
    ('2021-01-01', True, True, 'applicable'), ('2021-01-01', False, True, 'not_applicable'),
    ('2021-12-31', True, True, 'applicable'), ('2021-12-31', True, False, 'not_applicable'),
    ('2020-12-31', True, True, 'not_applicable'), ('2022-01-01', True, True, 'not_applicable')])
def test_explicit_boundary_inclusion(value, lower, upper, expected):
    p = policy('2021-01-01', '2021-12-31')
    p['from']['inclusive'] = lower; p['until']['inclusive'] = upper
    assert resolve_versions(snapshot(version(policies=[p])), {'dates': [date_record(value)]})['status'] == expected


@pytest.mark.parametrize('role', ['production', 'testing', 'task', 'publication', 'implementation', 'export', None, ''])
def test_different_or_missing_date_role_never_substitutes_sampling(role):
    result = resolve_versions(snapshot(version()), {'dates': [date_record(role=role)]})
    assert result['status'] == 'unknown' and result['selected_version_id'] is None


@pytest.mark.parametrize('removed', ['raw_value', 'source_ref', 'verification_state', 'value'])
def test_date_requires_complete_verified_evidence(removed):
    record = date_record(); record.pop(removed)
    assert resolve_versions(snapshot(version()), {'dates': [record]})['status'] == 'unknown'


@pytest.mark.parametrize('value', ['2021', '20210810', '2021-02-30', '2021-08-10T00:00:00', 2021, None])
def test_date_value_is_strict_iso_date(value):
    assert resolve_versions(snapshot(version()), {'dates': [date_record(value)]})['status'] == 'unknown'


def test_implementation_claim_never_becomes_business_policy():
    v = version(policies=[])
    v['date_claims'] = [date_record('2021-01-01', 'implementation')]
    result = resolve_versions(snapshot(v), {'dates': [date_record()]})
    assert result['status'] == 'unknown'
    assert result['candidates'][0]['gaps'][0]['code'] == 'APPLICABILITY_POLICY_MISSING'


@pytest.mark.parametrize('state', ['unverified', 'unknown', 'source_claim'])
def test_unverified_date_and_policy_do_not_choose_version(state):
    assert resolve_versions(snapshot(version()), {'dates': [date_record(state=state)]})['status'] == 'unknown'
    p = policy(); p['verification_state'] = state
    assert resolve_versions(snapshot(version(policies=[p])), {'dates': [date_record()]})['status'] == 'unknown'


def test_verified_conflicting_business_dates_and_effective_sources_remain_conflicting():
    context = {'dates': [date_record(), date_record('2022-08-10')]}
    assert resolve_versions(snapshot(version()), context)['status'] == 'conflicting'
    v = version(); v['date_claims'] = [date_record('2021-01-01', 'implementation'),
                                      date_record('2021-03-01', 'implementation')]
    result = resolve_versions(snapshot(v), {'dates': [date_record()]})
    assert result['status'] == 'conflicting'
    assert result['candidates'][0]['date_claim_conflicts'][0]['role'] == 'implementation'


def test_missing_role_and_fixed_version_id_only_keep_candidate():
    result = resolve_versions(snapshot(version(), version('new')), {'dates': []}, version_id='old')
    assert len(result['candidates']) == 1 and result['status'] == 'unknown'
    assert result['selected_version_id'] is None
    no_match = resolve_versions(snapshot(version()), {}, version_id='not-registered')
    assert no_match['issues'][0]['code'] == 'VERSION_NOT_FOUND'


@pytest.mark.parametrize('removed', ['date_role', 'from', 'until', 'condition', 'source_ref', 'evidence'])
def test_policy_is_explicit_and_source_bound(removed):
    p = policy(); del p[removed]
    assert resolve_versions(snapshot(version(policies=[p])), {'dates': [date_record()]})['status'] == 'unknown'


def test_policy_cannot_use_publication_or_implementation_as_business_date():
    p = policy(role='implementation')
    assert resolve_versions(snapshot(version(policies=[p])), {'dates': [date_record(role='implementation')]})['status'] == 'unknown'


def test_policy_window_must_be_consistent_and_inclusion_explicit():
    p = policy('2022-01-01', '2021-01-01')
    result = resolve_versions(snapshot(version(policies=[p])), {'dates': [date_record()]})
    assert result['status'] == 'unknown'
    assert result['candidates'][0]['policies_checked'][0]['issues'][0]['code'] == 'POLICY_WINDOW_INVALID'
    p = policy('2021-01-01'); del p['from']['inclusive']
    assert resolve_versions(snapshot(version(policies=[p])), {'dates': [date_record()]})['status'] == 'unknown'


@pytest.mark.parametrize(('value', 'state'), [(None, 'unknown'), ('熟制', 'applicable'),
                                            ('生鲜', 'not_applicable'), ('冲突', 'conflicting')])
def test_sourced_transition_condition_has_four_states(value, state):
    p = policy(ast={'op': 'eq', 'field': 'process', 'value': '熟制'})
    facts = {'process': fact(value, 'conflicting' if value == '冲突' else 'verified')} if value else {}
    assert resolve_versions(snapshot(version(policies=[p])), {'dates': [date_record()]}, facts=facts)['status'] == state


def test_unknown_transition_condition_outside_known_window_is_inapplicable():
    p = policy(end='2020-01-01', ast={'op': 'eq', 'field': 'process', 'value': '熟制'})
    assert resolve_versions(snapshot(version(policies=[p])), {'dates': [date_record()]})['status'] == 'not_applicable'


@pytest.mark.parametrize('condition_value', [None, {'state': 'structured', 'ast': {'op': 'exec', 'code': 'raise'}},
    {'op': 'eq', 'field': 'process', 'value': '熟制'},
    {'state': 'unconditional', 'ast': None, 'evidence': []},
    {'state': 'unresolved', 'raw': 'follow latest standard'}])
def test_unsupported_prose_and_code_do_not_execute_or_close_policy(condition_value):
    p = policy(); p['condition'] = condition_value
    assert resolve_versions(snapshot(version(policies=[p])), {'dates': [date_record()]})['status'] == 'unknown'


def test_overlapping_versions_conflict_and_unknown_competitor_prevents_selection():
    context = {'dates': [date_record()]}
    assert resolve_versions(snapshot(version(policies=[policy()]), version('new', [policy()])), context)['status'] == 'conflicting'
    result = resolve_versions(snapshot(version(policies=[policy()]), version('new', [])), context)
    assert result['status'] == 'unknown' and result['selected_version_id'] is None


def test_explicit_production_transition_does_not_use_later_testing_date():
    v = version(policies=[policy(end='2022-01-01', role='production')])
    dates = [date_record('2021-12-01', 'production'), date_record('2022-06-01', 'testing')]
    assert resolve_versions(snapshot(v), {'dates': dates})['selected_version_id'] == 'old'


def test_unverified_bound_and_source_policy_conflict_are_not_suppressed():
    p = policy('2021-01-01'); p['from']['verification_state'] = 'unverified'
    assert resolve_versions(snapshot(version(policies=[p])), {'dates': [date_record()]})['status'] == 'unknown'
    p['verification_state'] = 'conflicting'
    assert resolve_versions(snapshot(version(policies=[p])), {'dates': [date_record()]})['status'] == 'conflicting'


@pytest.mark.parametrize('evidence', [[], [None], ['unanchored text'], [{}]])
def test_policy_evidence_must_be_explicit_records(evidence):
    p = policy(); p['evidence'] = evidence
    assert resolve_versions(snapshot(version(policies=[p])), {'dates': [date_record()]})['status'] == 'unknown'


@pytest.mark.parametrize('extra', [None, '2026-10-12', date_record(role=None)])
def test_mixed_malformed_or_roleless_dates_block_selection(extra):
    result = resolve_versions(snapshot(version()), {'dates': [date_record(), extra]})
    assert result['status'] == 'unknown' and result['selected_version_id'] is None
    assert result['time_context']['dates'][1] == extra


@pytest.mark.parametrize('block', ['amendment', 'draft', 'missing_body'])
def test_amendment_draft_or_missing_body_cannot_select_formal_subject(block):
    v = version()
    if block == 'amendment': v['is_amendment'] = True
    elif block == 'draft': v['status_claims'] = [{'value': 'draft'}]
    else: v['body_assets'] = []
    result = resolve_versions(snapshot(v), {'dates': [date_record()]})
    candidate = result['candidates'][0]
    assert candidate['temporal_status'] == 'applicable'
    assert result['status'] == 'unknown' and result['selected_version_id'] is None
    assert not candidate['eligibility']['execution_ready'] and candidate['gaps']


def test_real_candidate_identity_and_review_never_inherit_policy_verification():
    candidate = resolve_versions(snapshot(version()), {'dates': [date_record()]})['candidates'][0]
    assert candidate['eligibility']['identity_state'] == 'unverified'
    assert candidate['eligibility']['review_state'] == 'review_pending'
    assert not candidate['eligibility']['execution_ready'] and not candidate['executable']


def test_false_transition_condition_cannot_hide_missing_role_or_invalid_window():
    p = policy(ast={'op': 'eq', 'field': 'process', 'value': '熟制'})
    facts = {'process': fact('生鲜')}
    assert resolve_versions(snapshot(version(policies=[p])), {'dates': []}, facts=facts)['status'] == 'unknown'
    p['from'] = endpoint('2022-01-01'); p['until'] = endpoint('2021-01-01')
    assert resolve_versions(snapshot(version(policies=[p])), {'dates': [date_record()]}, facts=facts)['status'] == 'unknown'


def test_conflicting_bound_is_preserved_even_with_incomplete_inclusion_flag():
    p = policy('2021-01-01'); p['from']['verification_state'] = 'conflicting'; del p['from']['inclusive']
    assert resolve_versions(snapshot(version(policies=[p])), {'dates': [date_record()]})['status'] == 'conflicting'


@pytest.mark.parametrize('field', ['is_draft', 'identity_state'])
def test_real_source_draft_flags_block_subject_selection(field):
    v = version(); v[field] = True if field == 'is_draft' else 'draft'
    assert resolve_versions(snapshot(v), {'dates': [date_record()]})['selected_version_id'] is None


@pytest.mark.parametrize('kind', ['missing_body', 'missing_pages', 'unreadable_text', 'source_conflict'])
def test_related_source_gap_freezes_only_affected_version_selection(kind):
    s = snapshot(version())
    s['gaps'] = [{'gap_id': 'SYNTHETIC_GAP', 'kind': kind, 'target': {'kind': 'version', 'id': 'old'},
                 'affected_refs': [{'kind': 'requirement', 'id': 'synthetic-req'}]}]
    result = resolve_versions(s, {'dates': [date_record()]})
    assert result['status'] == 'unknown' and result['selected_version_id'] is None
    assert result['candidates'][0]['gaps'][0]['gap_id'] == 'SYNTHETIC_GAP'
    s['gaps'][0]['target']['id'] = 'unrelated'
    assert resolve_versions(s, {'dates': [date_record()]})['selected_version_id'] == 'old'

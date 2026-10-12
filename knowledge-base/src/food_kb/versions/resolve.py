"""Read-only version selection from sourced business-date policies.

Publication and implementation claims are displayed, never converted into an
implicit business-date policy. This module does not approve or publish knowledge.
"""
from copy import deepcopy
from datetime import date

from food_kb.taxonomy.conditions import combine_states, evaluate_condition

ALGORITHM_VERSION = 'version-resolve-1'
BUSINESS_ROLES = {'production', 'sampling', 'testing', 'task'}
DATE_ROLES = BUSINESS_ROLES | {'publication', 'implementation', 'export'}


def _issue(code, message):
    return {'code': code, 'message': message}


def _iso(value):
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError('Expected YYYY-MM-DD.')
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError('Expected YYYY-MM-DD.')
    return parsed


def _date_result(records):
    values = []; incomplete = not records
    for record in records:
        if record.get('verification_state') == 'conflicting':
            return {'status': 'conflicting', 'value': None, 'records': deepcopy(records)}
        try:
            value = _iso(record.get('value'))
        except (ValueError, TypeError):
            incomplete = True
            continue
        if (record.get('verification_state') != 'verified' or not record.get('source_ref') or
                'raw_value' not in record):
            incomplete = True
        else:
            values.append(value)
    state = ('conflicting' if len(set(values)) > 1 else
             'unknown' if incomplete or not values else 'applicable')
    return {'status': state, 'value': values[0].isoformat() if state == 'applicable' else None,
            'records': deepcopy(records)}


def _policy_result(policy, dates, facts):
    result = {'policy_id': policy.get('policy_id'), 'date_role': policy.get('date_role'),
              'status': 'unknown', 'evidence': deepcopy(policy.get('evidence', [])),
              'source_ref': policy.get('source_ref'), 'bounds': {}, 'issues': []}
    if policy.get('verification_state') == 'conflicting':
        result['status'] = 'conflicting'
        result['issues'].append(_issue('POLICY_SOURCE_CONFLICT', '适用制度的来源依据冲突。'))
        return result
    if (policy.get('verification_state') != 'verified' or not policy.get('source_ref') or
            not isinstance(policy.get('evidence'), list) or not policy['evidence'] or
            any(not isinstance(item, dict) or not item for item in policy['evidence']) or
            policy.get('date_role') not in BUSINESS_ROLES or
            not {'from', 'until', 'condition'} <= policy.keys() or
            not isinstance(policy.get('condition'), dict) or 'op' in policy['condition']):
        result['issues'].append(_issue('POLICY_UNRESOLVED', '缺少已核实、明确业务日期角色的适用制度。'))
        return result
    result['date_result'] = _date_result([item for item in dates if item.get('role') == policy['date_role']])
    bounds = {}; bound_states = []
    for key in ('from', 'until'):
        bound = policy[key]
        # null is an explicit unbounded endpoint in this sourced policy.
        if bound is None:
            bounds[key] = None
            continue
        checked = _date_result([bound] if isinstance(bound, dict) else [])
        if not isinstance(bound, dict) or type(bound.get('inclusive')) is not bool:
            if checked['status'] != 'conflicting':
                checked['status'] = 'unknown'
        bounds[key] = deepcopy(bound)
        bound_states.append(checked['status'])
    result['bounds'] = bounds
    result['condition_result'] = evaluate_condition(policy['condition'], facts)
    states = [result['date_result']['status'], result['condition_result']['status']] + bound_states
    if not any(state in {'unknown', 'conflicting'} for state in [result['date_result']['status']] + bound_states):
        actual = _iso(result['date_result']['value'])
        lower = _iso(policy['from']['value']) if policy['from'] is not None else None
        upper = _iso(policy['until']['value']) if policy['until'] is not None else None
        if lower is not None and upper is not None and (lower > upper or
                lower == upper and not (policy['from']['inclusive'] and policy['until']['inclusive'])):
            states.append('unknown')
            result['issues'].append(_issue('POLICY_WINDOW_INVALID', '适用窗口为空或上下界颠倒。'))
        else:
            inside = ((lower is None or actual > lower or actual == lower and policy['from']['inclusive']) and
                      (upper is None or actual < upper or actual == upper and policy['until']['inclusive']))
            states.append('applicable' if inside else 'not_applicable')
    result['status'] = ('conflicting' if 'conflicting' in states else
                        'unknown' if result['date_result']['status'] == 'unknown' or
                        'unknown' in bound_states or result['issues'] else combine_states(states))
    if result['date_result']['status'] == 'unknown':
        result['issues'].append(_issue('BUSINESS_DATE_UNRESOLVED', '所需业务日期值、角色或核实依据缺失。'))
    return result


def resolve_versions(snapshot, time_context, document_id=None, version_id=None, facts=None):
    """Return four-state candidates without assuming today, latest or an export date.

    ``dates`` contain role/value/raw_value/source_ref/verification_state. Each
    policy declares its business date_role, explicit from/until endpoints and a
    sourced restricted condition. ``selected_version_id`` requires all competing
    candidates to be known inapplicable. A selected temporal candidate is still
    non-executable and must pass separate evidence and knowledge governance.
    """
    context = deepcopy(time_context) if isinstance(time_context, dict) else {}
    raw_dates = context.get('dates', [])
    dates = [item for item in raw_dates if isinstance(item, dict)] if isinstance(raw_dates, list) else []
    result = {'schema_version': 'version-resolution-1', 'algorithm_version': ALGORITHM_VERSION,
              'snapshot_id': snapshot.get('snapshot_id'), 'time_context': context,
              'candidates': [], 'status': 'unknown', 'selected_version_id': None,
              'issues': [], 'scope_complete': False, 'executable': False}
    if not isinstance(raw_dates, list) or len(dates) != len(raw_dates):
        result['issues'].append(_issue('DATE_SCHEMA', '日期上下文必须是明确角色的日期记录列表。'))
    if any(item.get('role') not in DATE_ROLES for item in dates):
        result['issues'].append(_issue('DATE_ROLE_MISSING', '日期角色缺失或不受支持，不能按年份猜测。'))
    gaps_by_version = {}
    for gap in snapshot.get('gaps', []):
        targets = [gap.get('target', {})] + gap.get('affected_refs', [])
        for identifier in {target.get('id') for target in targets if target.get('kind') == 'version'}:
            gaps_by_version.setdefault(identifier, []).append(gap)
    for version in snapshot.get('versions', []):
        if document_id is not None and version.get('document_id') != document_id:
            continue
        if version_id is not None and version.get('version_id') != version_id:
            continue
        candidate = {key: deepcopy(version.get(key)) for key in (
            'version_id', 'document_id', 'designation', 'identity_state', 'review_state',
            'status_claims', 'date_claims', 'is_amendment', 'is_draft', 'body_assets', 'evidence')}
        claims = version.get('date_claims', [])
        conflicts = []
        for role in ('publication', 'implementation'):
            members = [claim for claim in claims if isinstance(claim, dict) and claim.get('role') == role]
            values = {claim.get('value') for claim in members if claim.get('source_ref') and
                      isinstance(claim.get('value'), str)}
            if len(values) > 1 or any(claim.get('verification_state') == 'conflicting' for claim in members):
                conflicts.append({'role': role, 'claims': deepcopy(members)})
        checked = [_policy_result(policy, dates, facts or {}) for policy in version.get('policies', [])
                   if isinstance(policy, dict)]
        candidate['policies_checked'] = checked
        candidate['date_claim_conflicts'] = conflicts
        candidate['status'] = ('conflicting' if conflicts else
                               combine_states([item['status'] for item in checked], 'any') if checked else 'unknown')
        candidate['temporal_status'] = candidate['status']
        candidate['gaps'] = deepcopy(version.get('gaps', []) + gaps_by_version.get(version.get('version_id'), []))
        if not checked:
            candidate['gaps'].append(_issue('APPLICABILITY_POLICY_MISSING', '尚无有依据的业务日期适用制度，不能默认现行版。'))
        if conflicts:
            candidate['gaps'].append(_issue('VERSION_DATE_SOURCE_CONFLICT', '版本日期来源冲突，相关判断保持待核。'))
        draft = (version.get('is_draft') or version.get('identity_state') == 'draft' or
                 version.get('document_kind') == 'draft' or any(
            isinstance(claim, dict) and claim.get('value') in {'draft', '报批稿', '草案'}
            for claim in version.get('status_claims', [])))
        candidate['eligibility'] = {'document_kind': 'amendment' if version.get('is_amendment') else 'draft' if draft else 'body',
                                    'body_state': 'available_candidate' if version.get('body_assets') else 'missing',
                                    'identity_state': version.get('identity_state', 'unverified'),
                                    'review_state': version.get('review_state', 'review_pending'), 'execution_ready': False}
        blocked = []
        if version.get('is_amendment'):
            blocked.append(_issue('AMENDMENT_NOT_BODY', '修改单只能关联主体，不能作为主体标准版本。'))
        if draft:
            blocked.append(_issue('DRAFT_NOT_FORMAL_BODY', '草案不能替代正式主体标准。'))
        if not version.get('body_assets'):
            blocked.append(_issue('BODY_ORIGINAL_MISSING', '主体全文资产缺失，不能闭合版本选择。'))
        if any(gap.get('kind') in {'missing_body', 'missing_pages', 'unreadable_text', 'source_conflict'}
               for gap in candidate['gaps']):
            blocked.append(_issue('VERSION_SOURCE_GAP', '主体证据缺失、不可读或冲突，相关版本选择保持待核。'))
        candidate['gaps'].extend(blocked)
        if blocked and candidate['status'] == 'applicable':
            candidate['status'] = 'unknown'
        candidate['executable'] = False
        result['candidates'].append(candidate)
    states = [item['status'] for item in result['candidates']]
    applicable = [item for item in result['candidates'] if item['status'] == 'applicable']
    if 'conflicting' in states or len(applicable) > 1:
        result['status'] = 'conflicting'
    elif 'unknown' in states or not states:
        result['status'] = 'unknown'
    elif len(applicable) == 1:
        result['status'] = 'applicable'
        result['selected_version_id'] = applicable[0]['version_id']
    else:
        result['status'] = 'not_applicable'
    if not result['candidates']:
        result['issues'].append(_issue('VERSION_NOT_FOUND', '固定快照中没有所请求的版本候选。'))
    if any(issue['code'] in {'DATE_SCHEMA', 'DATE_ROLE_MISSING'} for issue in result['issues']):
        if result['status'] != 'conflicting':
            result['status'] = 'unknown'
        result['selected_version_id'] = None
    return result

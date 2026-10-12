"""Bounded, snapshot-fixed exact candidate queries; no applicability approval."""
from copy import deepcopy

from food_kb.common import KBError
from food_kb.taxonomy.conditions import evaluate_condition
from food_kb.taxonomy.inheritance import check_exclusions
from .normalize import normalize_filter_value

ALGORITHM_VERSION = 'retrieval-engine-1'
FILTER_KEYS = {'record_id', 'designation', 'family', 'year', 'version_id', 'node_id',
               'project_id', 'project', 'clause', 'record_type', 'review_state',
               'requirement_type', 'namespace', 'method_standard', 'judgment_standard'}


def prepare_filters(filters):
    if filters is None:
        return {}
    if not isinstance(filters, dict) or set(filters) - FILTER_KEYS:
        raise KBError('QUERY_FILTER', '精确查询含未支持的字段。')
    result = {}
    for key, value in filters.items():
        values = value if isinstance(value, list) else [value]
        if not values or len(values) > 100:
            raise KBError('QUERY_FILTER', '同字段候选值必须为1至100项。')
        result[key] = sorted({normalize_filter_value(key, item) for item in values})
    return result


def validate_page(limit, offset):
    if type(limit) is not int or not 1 <= limit <= 1000 or type(offset) is not int or offset < 0:
        raise KBError('QUERY_PAGE', 'limit须为1至1000整数，offset须为非负整数。')


def field_values(record, key):
    value = record.get('record_id') if key == 'record_id' else record.get('exact', {}).get(key)
    if value is None and key in {'record_type', 'review_state'}:
        value = record.get(key)
    values = value if isinstance(value, list) else [value]
    return [item for item in values if item is not None and item != '']


def filter_record(record, filters):
    matched = []; missing = []
    for key, expected in filters.items():
        values = field_values(record, key)
        if not values:
            missing.append(key)
            continue
        actual = {normalize_filter_value(key, item) for item in values}
        common = sorted(actual.intersection(expected))
        if not common:
            return False, matched, missing
        matched.extend({'field': key, 'term': term, 'raw_values': deepcopy(values),
                        'match_kind': 'exact_metadata'} for term in common)
    # Unknown metadata is not an exact match. Business condition unknowns are
    # handled separately, retaining candidates rather than faking equality.
    return not missing, matched, missing


def condition_checks(record, facts):
    conditions = record.get('conditions') or []
    if isinstance(conditions, dict):
        conditions = [conditions]
    checks = [dict(evaluate_condition(condition, facts or {}), check_kind='condition')
              for condition in conditions]
    if not checks:
        checks = [{'status': 'unknown', 'check_kind': 'condition', 'required_facts': [],
                   'missing_facts': [], 'issues': [{'code': 'CONDITION_NOT_RESOLVED',
                    'message': '检索资料没有闭合业务适用条件。'}]}]
    for result in check_exclusions(record.get('exclusions', []), facts or {}):
        checks.append(dict(result, check_kind='exclusion', predicate_status=result['status'],
                      status={'applicable': 'not_applicable', 'not_applicable': 'applicable',
                              'unknown': 'unknown', 'conflicting': 'conflicting'}[result['status']]))
    return checks


def condition_excludes(checks):
    return (any(check['status'] == 'not_applicable' for check in checks) and
            not any(check['status'] == 'conflicting' for check in checks))


def candidate_records(corpus, candidate_ids):
    records = corpus.get('records', [])
    if not isinstance(records, list):
        raise KBError('QUERY_CORPUS', '检索代次records必须为列表。')
    if candidate_ids is None:
        return records
    ids = set(candidate_ids)
    return [record for record in records if record['record_id'] in ids]


def make_hit(corpus, record, matches, checks, score):
    gap_ids = deepcopy(record.get('gap_ids', []))
    lookup = corpus.get('gap_lookup', {})
    return {'record_id': record['record_id'], 'record_type': record.get('record_type'),
            'logical_id': record.get('logical_id'), 'source_channel': record.get('source_channel'),
            'score': score, 'score_meaning': 'retrieval_order_not_business_confidence',
            'exact': deepcopy(record.get('exact', {})),
            'display_fields': {key: deepcopy(value) for key, value in record.get('fields', {}).items()
                               if key in {'name', 'project', 'title'}},
            'review_state': record.get('review_state', record.get('exact', {}).get('review_state', 'unknown')),
            'applicability': 'unknown', 'executable': False,
            'conditions': deepcopy(record.get('conditions', [])), 'condition_checks': checks,
            'exclusions': deepcopy(record.get('exclusions', [])),
            'unknown_conditions': [check for check in checks if check['status'] in {'unknown', 'conflicting'}],
            'gap_ids': gap_ids, 'gaps': deepcopy(record.get('gaps', [])) +
            [deepcopy(lookup[identifier]) for identifier in gap_ids if identifier in lookup],
            'sources': deepcopy(record.get('sources', [])), 'matches': matches}


def result_page(corpus, query, channel, hits, limit, offset, trace):
    count = len(hits)
    page = hits[offset:offset + limit]
    for rank, hit in enumerate(page, offset + 1):
        hit['rank'] = rank
    return {'schema_version': 'retrieval-result-1', 'algorithm_version': ALGORITHM_VERSION,
            'generation_id': corpus.get('generation_id'), 'channel': channel,
            'status': 'success' if count else 'empty', 'query': query,
            'hits': page, 'total_count': count, 'limit': limit, 'offset': offset,
            'next_offset': offset + limit if offset + limit < count else None,
            'scope_complete': False, 'executable': False,
            'unknown_conditions': sum(bool(hit['unknown_conditions']) for hit in hits), 'trace': trace}


def exact_search(corpus, filters=None, *, limit=50, offset=0, confirmed_conditions=None,
                 candidate_ids=None):
    validate_page(limit, offset)
    prepared = prepare_filters(filters)
    if confirmed_conditions is not None and not isinstance(confirmed_conditions, dict):
        raise KBError('QUERY_FACTS', '已确认业务事实必须按事实契约传入对象。')
    records = candidate_records(corpus, candidate_ids)
    hits = []; filter_rejected = 0; condition_excluded = 0; metadata_unknown = {}
    for record in records:
        matched, matches, missing = filter_record(record, prepared)
        for key in missing:
            metadata_unknown[key] = metadata_unknown.get(key, 0) + 1
        if not matched:
            filter_rejected += 1
            continue
        checks = condition_checks(record, confirmed_conditions)
        if condition_excludes(checks):
            condition_excluded += 1
            continue
        hits.append(make_hit(corpus, record, matches, checks, 1.0))
    hits.sort(key=lambda hit: hit['record_id'])
    result = result_page(corpus, {'filters': deepcopy(filters or {}), 'normalized_filters': prepared},
                         'exact', hits, limit, offset,
                         {'corpus_count': len(corpus.get('records', [])), 'candidate_count': len(records),
                          'filter_rejected': filter_rejected, 'condition_excluded': condition_excluded,
                          'metadata_unknown': metadata_unknown})
    if not hits:
        result['empty_reason'] = 'no_records' if not corpus.get('records') else 'filter_mismatch'
    return result

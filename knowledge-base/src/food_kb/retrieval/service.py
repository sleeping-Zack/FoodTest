"""PostgreSQL candidate retrieval with explicit index degradation and failures."""
import time

import psycopg
from psycopg.types.json import Jsonb

from ..common import KBError
from .exact import ALGORITHM_VERSION, exact_search, prepare_filters, validate_page
from .lexical import MODES, TOKENIZER_VERSION, lexical_search, query_tokens, validate_query
from .normalize import NORMALIZER_VERSION, normalize_text
from .repository import checked_records, read_header


def _index_health(conn, name, column):
    row = conn.execute('''SELECT i.indisvalid,i.indisready,i.indislive,
        i.indpred IS NULL AND i.indexprs IS NULL AND i.indnkeyatts=1 AS complete_column,
        i.indrelid=to_regclass('retrieval_record') AS correct_table,
        a.amname,pg_get_indexdef(i.indexrelid) AS definition
        FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_am a ON a.oid=c.relam
        WHERE i.indexrelid=to_regclass(%s)''', (name,)).fetchone()
    return bool(row and row['indisvalid'] and row['indisready'] and row['indislive'] and
        row['correct_table'] and row['complete_column'] and row['amname'] == 'gin' and '(' + column + ')' in row['definition'])


def _query_rows(conn, generation_id, filters, *, query=None, mode=None, tokens=None):
    conditions = ['generation_id=%s']; params = [generation_id]
    for key, values in filters.items():
        conditions.append('(' + ' OR '.join('exact_terms @> %s::jsonb' for _ in values) + ')')
        params.extend(Jsonb({key: [value]}) for value in values)
    if query is not None:
        if tokens:
            # Every lexeme is quoted; query/source text is never SQL or tsquery syntax.
            expression = ' & '.join("'" + token.replace('\\', '\\\\').replace("'", "''") + "'" for token in tokens)
            conditions.append('tokens @@ %s::tsquery'); params.append(expression)
        else:
            field = 'raw_text' if mode == 'literal_raw' else 'normalized_text'
            conditions.append('position(%s in ' + field + ')>0')
            params.append(query if mode == 'literal_raw' else normalize_text(query))
    return conn.execute('SELECT record_id,payload,payload_hash FROM retrieval_record WHERE ' +
        ' AND '.join(conditions) + ' ORDER BY record_id', params).fetchall()


def _failure(generation_id, mode, error, started):
    return {'schema_version': 'retrieval-result-1', 'algorithm_version': 'retrieval-engine-1',
        'generation_id': str(generation_id), 'channel': 'exact' if mode == 'exact' else 'lexical',
        'status': 'failed', 'hits': [], 'total_count': None, 'scope_complete': False,
        'executable': False, 'empty_reason': 'retrieval_unavailable_not_no_requirements',
        'faults': [error], 'performance': {'elapsed_ms': round((time.perf_counter() - started) * 1000, 3)}}


def search(conn, generation_id, query=None, *, mode='zh_ngram_phrase', filters=None,
           limit=50, offset=0, confirmed_conditions=None):
    started = time.perf_counter()
    prepared = prepare_filters(filters); validate_page(limit, offset)
    if mode != 'exact':
        if mode not in MODES:
            raise KBError('QUERY_MODE', 'Unknown retrieval mode.')
        validate_query(query)
    faults = []; missing = []; tokens = None
    try:
        header, _ = read_header(conn, generation_id)
        current = {'algorithm_version': ALGORITHM_VERSION, 'normalizer_version': NORMALIZER_VERSION,
                   'tokenizer_version': TOKENIZER_VERSION, 'alias_expansion': 'disabled'}
        if header.get('retrieval_config') != current:
            return _failure(generation_id, mode, {'code': 'UNSUPPORTED_RETRIEVAL_CONFIG',
                'message': 'Current search engine cannot silently reinterpret an older index configuration.'}, started)
        index_name, column = (('retrieval_record_exact_gin', 'exact_terms') if mode == 'exact'
                              else ('retrieval_record_tokens_gin', 'tokens'))
        if mode in {'exact', 'zh_ngram_phrase'} and not _index_health(conn, index_name, column):
            missing.append('physical_exact_gin' if mode == 'exact' else 'physical_lexical_gin')
            faults.append({'code': 'RETRIEVAL_INDEX_UNAVAILABLE', 'message': 'Expected GIN index is missing or invalid; fallback is explicit.'})
        if mode == 'zh_ngram_phrase' and not missing:
            tokens = query_tokens(query)
            if not tokens:
                missing.append('indexed_tokens_for_query')
                faults.append({'code': 'QUERY_NOT_TOKENIZABLE', 'message': 'This query uses characters outside the indexed token scheme.'})
        effective_mode = 'normalized_phrase' if mode == 'zh_ngram_phrase' and missing else mode
        try:
            # A failed index query is rolled back before attempting literal fallback.
            with conn.transaction():
                rows = _query_rows(conn, generation_id, prepared, query=None if mode == 'exact' else query,
                    mode=effective_mode, tokens=tokens if not missing else None)
        except psycopg.Error as exc:
            faults.append({'code': 'RETRIEVAL_INDEX_QUERY_FAILED', 'sqlstate': exc.sqlstate,
                           'message': 'Index query failed; no error text or connection credentials are returned.'})
            missing.append('index_query')
            effective_mode = 'normalized_phrase' if mode != 'exact' else 'exact'
            with conn.transaction():
                rows = _query_rows(conn, generation_id, prepared, query=None if mode == 'exact' else query,
                    mode=effective_mode, tokens=None)
        corpus = {k: v for k, v in header.items() if k != 'record_manifest'}
        corpus['records'] = checked_records(header, rows)
        if mode == 'exact':
            result = exact_search(corpus, filters or {}, limit=limit, offset=offset,
                confirmed_conditions=confirmed_conditions)
        else:
            result = lexical_search(corpus, query, mode=effective_mode, filters=filters, limit=limit,
                offset=offset, confirmed_conditions=confirmed_conditions)
        result['requested_mode'] = mode; result['effective_mode'] = effective_mode
        result['source_snapshots'] = header['source_snapshots']
        result['retrieval_config'] = header['retrieval_config']
        result['coverage'] = {key: header['summary'][key] for key in (
            'records', 'accepted_body_versions', 'accepted_body_chunks', 'standard_bodies_not_indexed',
            'review_state_basis')}
        result['limitations'] = header['limitations']
        result['missing_capabilities'] = sorted(set(missing)); result['faults'] = faults
        if missing:
            result['fallback_result_state'] = result['status']; result['status'] = 'degraded'
            if not result['hits']:
                result['empty_reason'] = 'fallback_empty_with_index_fault_not_normal_empty'
        result['trace']['postgres_candidates'] = {'stage': 'postgres_candidates', 'candidate_count': len(rows),
            'index': index_name if mode in {'exact', 'zh_ngram_phrase'} and not missing else None,
            'physical_index_checked': mode in {'exact', 'zh_ngram_phrase'},
            'known_metadata_filtered_in_database': list(prepared),
            'conditions_evaluated_after_retrieval': True,
            'whole_corpus_python_scan': False}
        result['trace']['generation_record_count'] = header['summary']['records']
        result['trace']['corpus_count'] = header['summary']['records']
        if not result['hits'] and not missing:
            result['empty_reason'] = ('generation_empty' if not header['summary']['records'] else
                'metadata_filters_no_match' if prepared and not rows else
                'confirmed_conditions_excluded' if result['trace'].get('condition_excluded') else 'no_literal_match')
        result['performance'] = {'elapsed_ms': round((time.perf_counter() - started) * 1000, 3),
                                 'database_candidate_rows': len(rows)}
        return result
    except psycopg.Error as exc:
        return _failure(generation_id, mode, {'code': 'RETRIEVAL_DATABASE_FAILED', 'sqlstate': exc.sqlstate,
            'message': 'Database retrieval failed; this is not an empty knowledge result.'}, started)
    except KBError as exc:
        if exc.code in {'RETRIEVAL_GENERATION_MISSING', 'RETRIEVAL_GENERATION_CORRUPT', 'RETRIEVAL_GENERATION_INVALID'}:
            return _failure(generation_id, mode, {'code': exc.code, 'message': str(exc)}, started)
        raise

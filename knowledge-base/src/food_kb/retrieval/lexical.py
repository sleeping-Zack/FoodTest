"""Chinese character index candidates with literal evidence recheck.

No English stemmer, unreviewed synonym expansion or semantic classification is
used. GIN lexemes are generated here; phrase and entity explanations use raw
field text, never an index-generated reconstruction.
"""
import re

from food_kb.common import KBError
from .exact import (candidate_records, condition_checks, condition_excludes, filter_record, make_hit,
                    prepare_filters, result_page, validate_page)
from .normalize import normalize_text, normalized_spans

MODES = {'literal_raw', 'normalized_phrase', 'zh_ngram_phrase'}
TOKENIZER_VERSION = 'zh-character-2gram-1'
_PARTS = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002ffff]+|'
                    r'[A-Za-z0-9_]+(?:[./-][A-Za-z0-9_]+)*')
_CJK = re.compile(r'^[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002ffff]+$')
_NAME_FIELDS = {'name', 'project'}
_WEIGHTS = {'name': 100, 'project': 100, 'title': 60, 'definition': 45,
            'scope': 35, 'table': 30, 'body': 20}


def text_fields(record):
    for field, value in record.get('fields', {}).items():
        values = value if isinstance(value, list) else [value]
        for number, text in enumerate(values):
            if isinstance(text, str) and text:
                yield field, text, number if isinstance(value, list) else None


def _tokens(text, include_single):
    tokens = set()
    for match in _PARTS.finditer(normalize_text(text)):
        part = match.group()
        if _CJK.fullmatch(part):
            if len(part) == 1 or include_single:
                tokens.update('c:' + char for char in part)
            tokens.update('g:' + part[index:index + 2] for index in range(len(part) - 1))
        else:
            # A bounded lexeme avoids PostgreSQL's oversized-lexeme error. Long
            # identifiers are still reachable through exact/literal channels.
            if len(part.encode('utf-8')) <= 1000:
                tokens.add('w:' + part)
            if len(part) == 1 or include_single:
                tokens.update('a:' + char for char in part)
            tokens.update('b:' + part[index:index + 2] for index in range(len(part) - 1))
    return sorted(tokens)


def index_tokens(record):
    return sorted({token for _, text, _ in text_fields(record) for token in _tokens(text, True)})


def query_tokens(query):
    validate_query(query)
    # Whole ASCII words are useful index metadata, but a literal substring
    # (e.g. GB within GB1886.1) cannot require a whole-word lexeme.
    return [token for token in _tokens(query, False) if not token.startswith('w:')]


def validate_query(query):
    if not isinstance(query, str) or '\x00' in query or not normalize_text(query) or len(query) > 256:
        raise KBError('QUERY_TEXT', '全文查询须为1至256字符的非空文本。')


def _raw_spans(text, query):
    spans = []; start = 0
    while len(spans) < 20:
        position = text.find(query, start)
        if position < 0:
            break
        spans.append({'span': [position, position + len(query)], 'matched_text': query,
                      'span_precision': 'exact'})
        start = position + len(query)
    return spans


def _matches(record, query, normalized, mode):
    matches = []
    for field, text, number in text_fields(record):
        spans = _raw_spans(text, query) if mode == 'literal_raw' else normalized_spans(text, normalized)
        if not spans:
            continue
        equal = normalize_text(text) == normalized
        kind = ('exact_name' if equal else 'related_name') if field in _NAME_FIELDS else 'phrase_evidence'
        for span in spans:
            item = {'field': field, 'term': normalized, 'raw_term': query,
                    'match_kind': kind, **span}
            if number is not None:
                item['field_index'] = number
            matches.append(item)
    return matches


def lexical_search(corpus, query, *, mode='zh_ngram_phrase', filters=None, limit=50, offset=0,
                   candidate_ids=None, confirmed_conditions=None):
    validate_query(query); validate_page(limit, offset)
    if mode not in MODES:
        raise KBError('QUERY_MODE', '未知全文查询模式。')
    if confirmed_conditions is not None and not isinstance(confirmed_conditions, dict):
        raise KBError('QUERY_FACTS', '已确认业务事实必须按事实契约传入对象。')
    normalized = normalize_text(query); tokens = query_tokens(query)
    prepared = prepare_filters(filters)
    records = candidate_records(corpus, candidate_ids)
    hits = []; index_rejected = 0; phrase_rejected = 0; filter_rejected = 0; condition_excluded = 0
    for record in records:
        if mode == 'zh_ngram_phrase' and not set(tokens).issubset(index_tokens(record)):
            index_rejected += 1
            continue
        matched, _, _ = filter_record(record, prepared)
        if not matched:
            filter_rejected += 1
            continue
        matches = _matches(record, query, normalized, mode)
        if not matches:
            phrase_rejected += 1
            continue
        checks = condition_checks(record, confirmed_conditions)
        if condition_excludes(checks):
            condition_excluded += 1
            continue
        score = max(_WEIGHTS.get(match['field'], 10) +
                    (1000 if match['match_kind'] == 'exact_name' else 0) for match in matches)
        hits.append(make_hit(corpus, record, matches, checks, score))
    hits.sort(key=lambda hit: (-hit['score'], hit['record_id']))
    result = result_page(corpus, {'raw': query, 'normalized': normalized, 'mode': mode,
                                 'tokens': tokens, 'filters': filters or {}, 'normalized_filters': prepared},
                         'lexical', hits, limit, offset,
                         {'tokenizer_version': TOKENIZER_VERSION, 'phrase_recheck': True,
                          'dictionary_expansion': 'disabled_unreviewed_aliases',
                          'corpus_count': len(corpus.get('records', [])), 'candidate_count': len(records),
                          'index_rejected': index_rejected, 'phrase_rejected': phrase_rejected,
                          'filter_rejected': filter_rejected, 'condition_excluded': condition_excluded})
    if not hits:
        result['empty_reason'] = ('no_records' if not corpus.get('records') else
                                  'filter_mismatch' if filter_rejected or condition_excluded else 'no_literal_match')
    return result

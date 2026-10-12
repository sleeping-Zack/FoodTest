"""Controlled engine examples, not expert-labelled food applicability claims."""
from copy import deepcopy

import pytest

from food_kb.common import KBError
from food_kb.retrieval.exact import exact_search, prepare_filters
from food_kb.retrieval.lexical import index_tokens, lexical_search, query_tokens
from food_kb.retrieval.normalize import normalize_standard, normalize_text, normalized_spans


def record(identifier, *, name='碳酸钠', designation='GB1886.1-2015', year='2015',
           body=None, conditions=None, **exact):
    body = body or f'本标准规定{name}的技术要求。'
    return {'record_id': identifier, 'record_type': 'chunk', 'review_state': 'review_pending',
            'fields': {'name': name, 'body': body},
            'exact': {'designation': designation, 'family': designation.rsplit('-', 1)[0],
                      'year': year, 'version_id': 'v-' + identifier, 'record_type': 'chunk',
                      'review_state': 'review_pending', **exact},
            'conditions': conditions or [], 'gap_ids': ['SYNTHETIC_GAP'],
            'sources': [{'asset_id': 'SYNTHETIC_ASSET', 'locator': {'page': 1},
                         'quote': body, 'synthetic': True}]}


def corpus(*records):
    return {'generation_id': 'SYNTHETIC_GENERATION', 'records': list(records),
            'gap_lookup': {'SYNTHETIC_GAP': {'kind': 'review_pending', 'synthetic': True}}}


def ids(result):
    return [hit['record_id'] for hit in result['hits']]


def fact(value, state='verified'):
    return {'type': 'string', 'raw_value': value, 'normalized_value': value,
            'source_ref': 'SYNTHETIC_FACT', 'role': 'sample_fact', 'verification_state': state}


def part_condition(value='根'):
    return {'state': 'structured', 'ast': {'op': 'eq', 'field': 'food_part', 'value': value},
            'evidence': [{'source_ref': 'SYNTHETIC_CONDITION', 'quote': '仅根部'}]}


@pytest.mark.parametrize('raw,expected', [
    ('ＧＢ　１８８６．１－２０１５', 'GB1886.1-2015'),
    ('gb / t 5009.12 — 2023', 'GB/T5009.12-2023'),
    ('GB T 5009.12-2023', 'GB/T5009.12-2023'),
    ('GB 5009.12-2023', 'GB5009.12-2023'),
    ('NY/T 761—2008', 'NY/T761-2008'),
    ('mg/kg', 'mg/kg'),
    ('Mg/kg', 'Mg/kg'),
])
def test_standard_normalization_preserves_t_year_and_nonstandard_case(raw, expected):
    assert normalize_standard(raw) == expected


def test_fullwidth_designation_and_specified_year_are_exact():
    data = corpus(record('old'), record('new', designation='GB1886.1-2021', year='2021'),
                  record('unknown', year=None))
    result = exact_search(data, {'designation': 'ＧＢ　１８８６．１－２０１５', 'year': 2015})
    assert ids(result) == ['old']
    assert result['trace']['metadata_unknown'] == {'year': 1}
    unknown_result = exact_search(data, {'year': '2015'})
    assert ids(unknown_result) == ['old']
    assert unknown_result['trace']['metadata_unknown'] == {'year': 1}


def test_gb_gbt_and_year_families_do_not_collapse():
    data = corpus(record('gb', designation='GB5009.1-2023', year='2023'),
                  record('gbt', designation='GB/T5009.1-2023', year='2023'))
    assert ids(exact_search(data, {'family': 'GB5009.1'})) == ['gb']
    assert ids(exact_search(data, {'family': 'GB/T5009.1'})) == ['gbt']
    assert not ids(exact_search(data, {'designation': 'GB/T5009.1-2022'}))


def test_chinese_project_exact_is_full_name_not_prefix_or_pb_basis():
    data = corpus(record('lead', project='铅'), record('salt', project='重金属（以Pb计）'),
                  record('water', project='水不溶物'))
    assert ids(exact_search(data, {'project': '水　不溶物'})) == ['water']
    assert not ids(exact_search(data, {'project': 'Pb'}))
    assert not ids(exact_search(data, {'project': '水不溶'}))


def test_method_and_judgment_fields_filter_independently():
    data = corpus(record('dual', method_standard=['GB/T5009.12-2017', 'GB5009.12-2023'],
                         judgment_standard='GB2762-2022'))
    assert ids(exact_search(data, {'method_standard': 'ＧＢ／Ｔ　5009.12—2017'})) == ['dual']
    assert ids(exact_search(data, {'judgment_standard': 'GB2762-2022'})) == ['dual']
    assert not ids(exact_search(data, {'method_standard': 'GB2762-2022'}))


def test_ids_clause_record_and_review_filters_are_and_of_or_lists():
    data = corpus(record('one', node_id='N:1', project_id='P1', clause='A.3.2', namespace='GB:2015'),
                  record('two', node_id='N:2', project_id='P2', clause='A.3.3', namespace='GB:2015'))
    result = exact_search(data, {'node_id': ['N:1', 'N:2'], 'project_id': 'P1', 'clause': 'Ａ．３．２',
                                'record_type': 'chunk', 'review_state': 'review_pending',
                                'namespace': 'GB:2015', 'record_id': 'one'})
    assert ids(result) == ['one']
    assert not ids(exact_search(data, {'review_state': 'approved'}))


def test_requirement_type_is_distinct_from_record_type():
    data = corpus(record('upper', requirement_type='upper_limit'),
                  record('lower', requirement_type='lower_limit'))
    assert ids(exact_search(data, {'record_type': 'chunk', 'requirement_type': 'upper_limit'})) == ['upper']
    assert not ids(exact_search(data, {'record_type': 'upper_limit'}))


@pytest.mark.parametrize('filters', [{'project': ''}, {'year': True}, {'year': '15'},
                                     {'unknown_field': 'x'}, {'year': []}, ['year']])
def test_invalid_filters_are_controlled_failure(filters):
    with pytest.raises(KBError):
        prepare_filters(filters)


def test_business_unknown_unverified_and_conflicting_are_not_exclusion_filters():
    data = corpus(record('root', conditions=[part_condition()]))
    assert ids(exact_search(data, confirmed_conditions={})) == ['root']
    assert ids(exact_search(data, confirmed_conditions={'food_part': fact('叶', 'unverified')})) == ['root']
    assert ids(exact_search(data, confirmed_conditions={'food_part': fact('叶', 'conflicting')})) == ['root']
    assert not ids(exact_search(data, confirmed_conditions={'food_part': fact('叶')}))
    accepted = exact_search(data, confirmed_conditions={'food_part': fact('根')})['hits'][0]
    assert accepted['condition_checks'][0]['status'] == 'applicable'
    assert accepted['applicability'] == 'unknown' and not accepted['executable']


def test_conflicting_condition_keeps_branch_even_with_other_false_condition():
    second = {'state': 'structured', 'ast': {'op': 'eq', 'field': 'food_name', 'value': '萝卜'},
              'evidence': [{'source_ref': 'SYNTHETIC', 'quote': '萝卜'}]}
    data = corpus(record('root', conditions=[part_condition(), second]))
    result = exact_search(data, confirmed_conditions={'food_part': fact('叶'),
                          'food_name': fact('未知', 'conflicting')})
    assert ids(result) == ['root']
    assert result['unknown_conditions'] == 1


def test_partial_conditions_satisfaction_is_still_unknown_but_known_false_excludes():
    condition = part_condition(); condition['state'] = 'partial'
    data = corpus(record('root', conditions=[condition]))
    kept = lexical_search(data, '碳酸钠', confirmed_conditions={'food_part': fact('根')})
    assert kept['hits'][0]['condition_checks'][0]['status'] == 'unknown'
    assert not ids(lexical_search(data, '碳酸钠', confirmed_conditions={'food_part': fact('叶')}))


def test_exclusion_predicate_direction_is_preserved_and_unknown_is_retained():
    item = record('root'); item['exclusions'] = [part_condition('叶')]
    data = corpus(item)
    assert ids(exact_search(data)) == ['root']
    assert not ids(exact_search(data, confirmed_conditions={'food_part': fact('叶')}))
    kept = exact_search(data, confirmed_conditions={'food_part': fact('根')})
    assert ids(kept) == ['root']
    check = next(check for check in kept['hits'][0]['condition_checks'] if check['check_kind'] == 'exclusion')
    assert check['check_kind'] == 'exclusion' and check['predicate_status'] == 'not_applicable'
    assert check['status'] == 'applicable'
    assert kept['hits'][0]['exclusions'] == item['exclusions']
    assert ids(exact_search(data, confirmed_conditions={'food_part': fact('叶', 'conflicting')})) == ['root']


def test_source_explicit_name_exclusion_is_checked_without_promoting_category_hint():
    item = record('fresh', name='新鲜水果')
    item['exclusions'] = [{'state': 'explicit_names', 'names': ['苹果汁'], 'ast': None,
                           'evidence': [{'source_ref': 'SYNTHETIC', 'quote': '不包括苹果汁'}]}]
    data = corpus(item)
    assert not ids(exact_search(data, confirmed_conditions={'food_name': fact('苹果汁')}))
    assert ids(exact_search(data, confirmed_conditions={'food_name': fact('苹果')})) == ['fresh']
    assert ids(exact_search(data, confirmed_conditions={'category_id': fact('果汁')})) == ['fresh']


def test_full_name_precedes_compound_related_names_without_entity_equivalence():
    data = corpus(record('juice', name='苹果汁'), record('apple', name='苹果'),
                  record('mention', name='果汁饮料', body='配料可能含苹果汁。'))
    result = lexical_search(data, '苹果')
    assert ids(result)[0] == 'apple'
    juice = next(hit for hit in result['hits'] if hit['record_id'] == 'juice')
    assert juice['display_fields']['name'] == '苹果汁'
    assert next(match for match in juice['matches'] if match['field'] == 'name')['match_kind'] == 'related_name'
    assert all(not hit['executable'] and hit['applicability'] == 'unknown' for hit in result['hits'])
    assert ids(exact_search(data, {'project': '苹果'})) == []


def test_ngram_candidate_rechecks_discontiguous_and_cross_field_false_positives():
    real = record('real', name='水不溶物')
    false = record('false', name='水不干扰；不溶于甲液；溶物不能单独判断')
    split = record('split', name='水不溶'); split['fields']['table'] = '溶物'
    data = corpus(real, false, split)
    tokens = set(query_tokens('水不溶物'))
    assert tokens.issubset(index_tokens(false))
    assert tokens.issubset(index_tokens(split))
    result = lexical_search(data, '水不溶物')
    assert ids(result) == ['real']
    assert result['trace']['phrase_rejected'] == 2


def test_normalized_phrase_improves_raw_linebreak_fullwidth_and_ngrams_agree():
    raw = 'ＧＢ　１８８６．１－２０１５规定水 不\n溶物项目。'
    data = corpus(record('real', name='项目', body=raw))
    assert not ids(lexical_search(data, '水不溶物', mode='literal_raw'))
    assert ids(lexical_search(data, '水不溶物', mode='normalized_phrase')) == ['real']
    assert ids(lexical_search(data, '水不溶物', mode='zh_ngram_phrase')) == ['real']
    hit = lexical_search(data, '水不溶物')['hits'][0]
    match = hit['matches'][0]
    assert raw[slice(*match['span'])] == match['matched_text'] == '水 不\n溶物'
    assert normalize_text(match['matched_text']) == match['term']
    assert hit['sources'][0]['quote'] == raw


def test_unit_symbol_case_is_preserved_in_all_channels():
    data = corpus(record('lower', name='mg/kg'), record('capital', name='Mg/kg'),
                  record('pb', name='Pb'), record('pb-lower', name='pb'))
    for mode in ('literal_raw', 'normalized_phrase', 'zh_ngram_phrase'):
        assert ids(lexical_search(data, 'mg/kg', mode=mode)) == ['lower']
        assert ids(lexical_search(data, 'Pb', mode=mode)) == ['pb']


def test_single_cjk_character_and_latin_substring_are_retrievable():
    data = corpus(record('lead', name='铅'), record('gb', name='GB1886.1-2015'))
    assert ids(lexical_search(data, '铅')) == ['lead']
    assert ids(lexical_search(data, 'GB')) == ['gb']
    assert set(query_tokens('GB')).issubset(index_tokens(data['records'][1]))


def test_candidate_tokens_do_not_lose_contiguous_original_phrases():
    # This checks the candidate channel's recall invariant independently of its
    # ranking, including cross-script and punctuation-bearing identifiers.
    text = 'ＧＢ／Ｔ 5009.12—2023 铅（Pb） 水 不\n溶物 mg/kg'
    item = record('original', name='范围', body=text)
    token_set = set(index_tokens(item))
    normalized = normalize_text(text)
    for start in range(len(normalized)):
        for size in (1, 2, 3, 6):
            phrase = normalized[start:start + size]
            if phrase:
                assert set(query_tokens(phrase)).issubset(token_set), phrase
                assert ids(lexical_search(corpus(item), phrase)) == ['original']


def test_alias_dictionary_is_not_automatically_approved_or_expanded():
    data = corpus(record('soda'))
    data['dictionary'] = [{'term': '纯碱', 'target': '碳酸钠', 'review_state': 'review_pending'}]
    assert not ids(lexical_search(data, '纯碱'))
    assert lexical_search(data, '碳酸钠')['trace']['dictionary_expansion'] == 'disabled_unreviewed_aliases'


def test_raw_spans_for_ligature_and_unicode_composition_are_honest():
    assert normalized_spans('项目ﬁ值', 'fi')[0]['matched_text'] == 'ﬁ'
    match = normalized_spans('e\u0301检验', 'é')[0]
    assert match['span_precision'] == 'field'
    assert match['matched_text'] == 'e\u0301检验'
    assert 'é' in normalize_text(match['matched_text'])


def test_generation_pagination_candidate_filter_gap_and_immutability():
    data = corpus(*(record(str(index), project='铅') for index in range(7)))
    before = deepcopy(data)
    result = exact_search(data, {'project': '铅'}, limit=2, offset=2)
    assert ids(result) == ['2', '3']
    assert result['total_count'] == 7 and result['next_offset'] == 4
    assert [hit['rank'] for hit in result['hits']] == [3, 4]
    assert result['generation_id'] == 'SYNTHETIC_GENERATION'
    assert result['hits'][0]['gaps'] == [{'kind': 'review_pending', 'synthetic': True}]
    assert not result['scope_complete']
    assert ids(lexical_search(data, '碳酸钠', candidate_ids=['5'])) == ['5']
    assert data == before


@pytest.mark.parametrize('arguments', [{'query': ''}, {'query': ' '}, {'query': '\x00'},
                                       {'query': '铅', 'mode': 'english'},
                                       {'query': '铅', 'limit': 0},
                                       {'query': '铅', 'offset': True},
                                       {'query': '铅', 'confirmed_conditions': '叶'}])
def test_invalid_fulltext_inputs_do_not_return_normal_empty_list(arguments):
    with pytest.raises(KBError):
        lexical_search(corpus(record('lead')), **arguments)


def test_empty_diagnostics_distinguish_records_filters_and_literal():
    assert exact_search(corpus())['empty_reason'] == 'no_records'
    data = corpus(record('one'))
    assert exact_search(data, {'project': '铅'})['empty_reason'] == 'filter_mismatch'
    assert lexical_search(data, '纯碱')['empty_reason'] == 'no_literal_match'
    assert lexical_search(data, '碳酸钠', filters={'review_state': 'approved'})['empty_reason'] == 'filter_mismatch'

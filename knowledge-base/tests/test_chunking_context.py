from copy import deepcopy

import pytest

from food_kb.chunking.context import expand_context
from food_kb.chunking.validate import validate_bundle


def location(line=1):
    return {'line_start': line, 'line_end': line, 'line_ranges': [[line, line]], 'pdf_pages': [1]}


def block(identifier, text, parent=None, line=1, metadata=None):
    return {'block_id': identifier, 'key': identifier, 'parent_id': parent, 'kind': 'text',
            'title': identifier, 'text_raw': text, 'source': location(line), 'metadata': metadata or {}}


def bundle():
    return {'source': {'asset_id': 'source', 'markdown_asset_id': 'markdown',
                       'page_count': 2, 'markdown_line_count': 20},
            'parse_id': 'parse-1', 'strategy_version': 'test-1',
            'blocks': [block('root', 'SYNTHETIC standard'), block('scope', 'Scope', 'root', 2),
                       block('method', 'Method', 'root', 3), block('step', 'Method detail', 'method', 4),
                       block('other', 'Unrelated sibling', 'root', 5), block('row', 'limit 1', 'root', 6)],
            'chunks': [{'chunk_id': 'chunk', 'anchor': 'row', 'kind': 'text', 'title': 'Limit',
                        'raw_markdown': 'limit 1', 'retrieval_text': 'limit 1', 'parent_block_id': 'root',
                        'block_ids': ['row'], 'source': location(6),
                        'quality': {'status': 'candidate', 'issues': []}, 'executable': False}],
            'links': [{'chunk_id': 'chunk', 'target_block_id': 'scope', 'role': 'scope'},
                      {'chunk_id': 'chunk', 'target_block_id': 'method', 'role': 'method'}],
            'coverage': {'expected_lines': [6], 'covered_lines': [6]}}


def codes(value):
    return {item['code'] for item in validate_bundle(value)['issues']}


def test_expansion_includes_scope_method_subtree_but_not_parent_siblings():
    value = bundle(); before = deepcopy(value)
    result = expand_context(value, 'chunk')
    assert [b['block_id'] for b in result['blocks']] == ['row', 'root', 'scope', 'method', 'step']
    assert result['complete'] and result['omitted'] == []
    assert result['characters'] == sum(len(b['text_raw']) for b in result['blocks'])
    result['blocks'][0]['text_raw'] = 'Modified returned copy'
    assert value == before
    assert validate_bundle(value)['passed']


@pytest.mark.parametrize('role', ['method', 'submethod', 'reference'])
def test_complete_target_subtrees_and_deduplication(role):
    value = bundle()
    value['links'] = [{'chunk_id': 'chunk', 'target_block_id': 'method', 'role': role}] * 2
    result = expand_context(value, 'chunk', roles=[role])
    assert [b['block_id'] for b in result['blocks']] == ['row', 'method', 'step']
    assert result['complete']


def test_parent_cycle_and_dangling_context_are_explicit():
    value = bundle(); value['blocks'][0]['parent_id'] = 'row'
    value['links'].append({'chunk_id': 'chunk', 'target_block_id': 'missing', 'role': 'scope'})
    result = expand_context(value, 'chunk')
    assert not result['complete'] and {'root', 'missing'} <= set(result['omitted'])
    assert {'PARENT_CYCLE', 'CONTEXT_ORPHAN'} <= codes(value)


def test_subtree_cycle_terminates_and_reports_incomplete():
    value = bundle(); value['blocks'][2]['parent_id'] = 'step'
    result = expand_context(value, 'chunk', roles=['method'])
    assert not result['complete'] and 'method' in result['omitted']


def test_budgets_never_truncate_blocks_or_hide_omissions():
    value = bundle()
    result = expand_context(value, 'chunk', max_blocks=2, max_chars=7)
    assert [b['block_id'] for b in result['blocks']] == ['row']
    assert result['blocks'][0]['text_raw'] == 'limit 1'
    assert not result['complete'] and set(result['omitted']) == {'root', 'scope', 'method', 'step'}
    assert not expand_context(value, 'chunk', max_blocks=0)['complete']


def test_invalid_requests_do_not_select_a_different_chunk():
    with pytest.raises(ValueError):
        expand_context(bundle(), 'unknown')
    with pytest.raises(ValueError):
        expand_context(bundle(), 'chunk', max_chars=-1)


def test_footnote_requires_matching_link_not_just_present_block():
    value = bundle(); value['chunks'][0]['raw_markdown'] = 'limit 1[^a]'
    value['blocks'].append(block('footnote-a', '[^a]: Applies only to dry matter', 'root', 7))
    assert 'FOOTNOTE_MISSING' in codes(value)
    value['links'].append({'chunk_id': 'chunk', 'target_block_id': 'footnote-a', 'role': 'footnote'})
    assert validate_bundle(value)['passed']
    value['blocks'][-1]['text_raw'] = '[^b]: A different qualification'
    assert 'FOOTNOTE_MISSING' in codes(value)


def test_table_rows_keep_headers_and_do_not_repeat_them_as_data():
    value = bundle(); chunk = value['chunks'][0]; chunk['kind'] = 'table_row'
    assert 'TABLE_HEADER_MISSING' in codes(value)
    chunk['table'] = {'headers': ['Item', 'Limit'], 'row': ['Lead', '1']}
    assert validate_bundle(value)['passed']
    chunk['table']['row'] = ['Item', 'Limit']
    assert 'DUPLICATE_HEADER_ROW' in codes(value)


def test_table_physical_cells_preserve_comparison_values():
    value = bundle(); chunk = value['chunks'][0]; chunk['kind'] = 'table_row'
    chunk['table'] = {'headers': ['项目', '限值'],
                      'row': {'cells': [{'value': '总碱（以Na2CO3计）≥'}, {'value': '99.2'}]}}
    assert validate_bundle(value)['passed']
    chunk['table']['row']['cells'][1]['value'] = ''
    assert 'COMPARISON_VALUE_MISSING' in codes(value)
    chunk['table']['row']['cells'] = [{'value': '项目'}, {'value': '限值'}]
    assert 'DUPLICATE_HEADER_ROW' in codes(value)


def test_noncontiguous_source_ranges_do_not_claim_the_gap():
    value = bundle()
    value['chunks'][0]['source'] = {'line_start': 6, 'line_end': 12,
                                    'line_ranges': [[6, 7], [11, 12]], 'pdf_pages': [1, 2]}
    value['coverage'] = {'expected_lines': [6, 7, 11, 12], 'covered_lines': [6, 7, 11, 12]}
    assert validate_bundle(value)['passed']
    value['coverage']['covered_lines'].append(9)
    assert 'COVERAGE_UNSUPPORTED' in codes(value)
    value['coverage']['expected_lines'].append(9)
    assert 'COVERAGE_MISSING' in codes(value)


def test_numbered_formula_needs_variables_in_its_own_context():
    value = bundle(); value['chunks'][0]['raw_markdown'] = '$$ w=m/M \\tag{A.1} $$'
    value['blocks'][4]['text_raw'] = '式中：unrelated sibling explanation'
    assert 'FORMULA_VARIABLES_MISSING' in codes(value)
    value['blocks'][3]['text_raw'] = '式中：m is sample mass; M is total mass'
    assert validate_bundle(value)['passed']
    value['chunks'][0]['raw_markdown'] = '$$ w=m/M \\tag{A.1}'
    assert 'FORMULA_UNBALANCED' in codes(value)


@pytest.mark.parametrize('fault,expected', [
    ('page', 'LOCATION_INVALID'), ('line', 'LOCATION_INVALID'), ('ranges', 'LOCATION_INVALID'),
    ('coverage', 'COVERAGE_MISSING'), ('parse', 'CROSS_PARSE'), ('source', 'CROSS_SOURCE'),
    ('executable', 'UNREVIEWED_EXECUTABLE'), ('approval', 'UNREVIEWED_APPROVAL'),
    ('comparison', 'COMPARISON_VALUE_MISSING'), ('block_missing', 'CHUNK_SOURCE_MISSING'),
])
def test_source_and_candidate_guards(fault, expected):
    value = bundle(); chunk = value['chunks'][0]
    if fault == 'page': chunk['source']['pdf_pages'] = [3]
    elif fault == 'line': chunk['source']['line_end'] = 21
    elif fault == 'ranges': chunk['source']['line_ranges'] = [[6, 7]]
    elif fault == 'coverage': value['coverage']['expected_lines'].append(7)
    elif fault == 'parse': value['blocks'][0]['parse_id'] = 'other-parse'
    elif fault == 'source': chunk['source']['asset_id'] = 'other-source'
    elif fault == 'executable': chunk['executable'] = True
    elif fault == 'approval': chunk['quality']['status'] = 'approved'
    elif fault == 'comparison': chunk['raw_markdown'] = '≤'
    elif fault == 'block_missing': chunk['block_ids'] = ['missing']
    assert expected in codes(value)

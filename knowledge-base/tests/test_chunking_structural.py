"""Read-only real-manual regressions; these tests never connect to a database."""
from copy import deepcopy
from pathlib import Path
import re

import pytest

from food_kb.chunking import manual as manual_reader
from food_kb.chunking.context import expand_context
from food_kb.chunking.structural import STRATEGY_CONFIG, build_bundle
from food_kb.common import KBError, digest, identity


MANUAL_DIRECTORY = Path(__file__).resolve().parents[2] / 'docs/knowledge-base/manual/GB1886.1-2015'
pytestmark = pytest.mark.skipif(not (MANUAL_DIRECTORY / 'source-map.json').is_file(),
                               reason='Private manual source bundle is not distributed with public code.')


@pytest.fixture(scope='module')
def real_manual():
    return manual_reader.load_manual(MANUAL_DIRECTORY)


@pytest.fixture(scope='module')
def real_bundle(real_manual):
    mapping = real_manual['mapping']
    return build_bundle(real_manual, identity('test-manual-parse', mapping['source_sha256'],
                                              mapping['markdown_sha256']))


def chunks_by_anchor(value):
    return {chunk['anchor']: chunk for chunk in value['chunks']}


def source_lines(value):
    return {line for start, end in value['source']['line_ranges'] for line in range(start, end + 1)}


def test_real_manual_keeps_all_headings_and_nonblank_source_lines(real_manual, real_bundle):
    headings = real_manual['mapping']['headings']
    chunks = chunks_by_anchor(real_bundle)
    assert len(headings) == 107
    assert len({h['id'] for h in headings}) == 107
    for heading in headings:
        chunk = chunks[heading['id']]
        assert chunk['title'] == heading['title']
        assert chunk['raw_markdown'].splitlines()[0] == (
            '#' * heading['heading_level'] + ' ' + heading['title'])
        assert heading['markdown_start_line'] in source_lines(chunk)
        assert chunk['executable'] is False
    expected = {n for n, line in enumerate(real_manual['lines'], 1)
                if n in real_manual['page_for_line'] and line.strip() and
                not line.lstrip().startswith('<!--')}
    assert len(expected) == 273
    assert set(real_bundle['coverage']['expected_lines']) == expected
    assert set(real_bundle['coverage']['covered_lines']) == expected
    assert set().union(*(source_lines(block) for block in real_bundle['blocks'])) == expected
    assert real_bundle['validation']['passed'], real_bundle['validation']['issues']
    assert real_bundle['source']['business_review_status'] == 'not_expert_approved'


def test_three_continuations_keep_full_text_and_both_pages(real_manual, real_bundle):
    chunks = chunks_by_anchor(real_bundle)
    continuations = real_manual['mapping']['cross_page_continuations']
    assert len(continuations) == 3
    assert {item['clause'] for item in continuations} == {'A.5.5', 'A.6.1.2.5', 'A.8.3.1'}
    for continuation in continuations:
        chunk = chunks[continuation['clause']]
        heading = next(h for h in real_manual['mapping']['headings']
                       if h['id'] == continuation['clause'])
        assert chunk['source']['pdf_pages'] == continuation['pages']
        for line_number in range(heading['markdown_start_line'], heading['markdown_content_end_line'] + 1):
            line = real_manual['lines'][line_number - 1]
            if line.strip() and not line.lstrip().startswith('<!--'):
                assert line in chunk['raw_markdown']
                assert line_number in source_lines(chunk)


def test_all_eight_formulas_remain_whole_with_variable_definitions(real_manual, real_bundle):
    chunks = [c for c in real_bundle['chunks'] if c['kind'] == 'section']
    formulas = real_manual['mapping']['formulas']
    assert [formula['id'] for formula in formulas] == [f'A.{n}' for n in range(1, 9)]
    found = []
    for formula in formulas:
        token = '\\tag{' + formula['id'] + '}'
        owners = [chunk for chunk in chunks if token in chunk['raw_markdown']]
        assert len(owners) == 1
        chunk = owners[0]
        raw = chunk['raw_markdown']
        assert raw.count('$$') % 2 == 0
        assert '式中' in raw[raw.index(token):]
        assert formula['markdown_line'] in source_lines(chunk)
        assert formula['pdf_page'] in chunk['source']['pdf_pages']
        found.extend(re.findall(r'\\tag\{(A\.\d+)\}', raw))
    assert set(found) == {f'A.{n}' for n in range(1, 9)}
    # A.4's concentration and titrant variables continue after the page boundary.
    definition = chunks_by_anchor(real_bundle)['A.6.1.2.5']['raw_markdown']
    assert 'c_1=\\frac' in definition and '$c_2$' in definition and '$V_4$' in definition


def test_html_table_inheritance_keeps_exact_real_row_and_cell_locations(real_bundle):
    chunks = chunks_by_anchor(real_bundle)
    first = chunks['table-1:row:1']; second = chunks['table-1:row:2']
    for chunk, line_number in ((first, 76), (second, 77)):
        assert chunk['source']['line_ranges'] == [[line_number, line_number]]
        assert chunk['source']['pdf_pages'] == [3]
        assert chunk['table']['pdf_page'] == 3
        assert chunk['table']['line_start'] == chunk['table']['line_end'] == line_number
        assert chunk['raw_markdown'].strip().startswith('<tr>')
        assert chunk['raw_markdown'].strip().endswith('</tr>')
        for cell in chunk['table']['cells'][:2]:
            assert cell['line_start'] == cell['line_end'] == line_number
    original = first['table']['cells'][2]
    inherited = second['table']['cells'][2]
    assert first['table']['row'][:2] == ['色泽', '白色']
    assert second['table']['row'][:2] == ['状态', '结晶粉末']
    assert inherited['inherited'] is True
    assert inherited['source_cell'] == original['source_cell']
    assert inherited['value'] == original['value']
    assert inherited['line_start'] == inherited['line_end'] == 76
    assert inherited['value'] in second['retrieval_text']


def test_iron_row_keeps_comparator_unit_literal_value_and_complete_method(real_bundle):
    chunk = chunks_by_anchor(real_bundle)['table-2:row:4']
    assert chunk['table']['headers'] == ['项目', '指标', '检验方法']
    assert chunk['table']['comparator'] == '≤'
    assert chunk['table']['unit'] == '%'
    assert chunk['table']['value'] == '0.003 5'
    assert chunk['table']['method'] == '附录 A 中 A.7'
    for text in ('铁（Fe）', '以干基计', '≤', '%', '0.003 5', 'A.7'):
        assert text in chunk['retrieval_text']
    assert chunk['source']['line_ranges'] == [[92, 92]]
    assert chunk['source']['pdf_pages'] == [3]
    context = expand_context(real_bundle, chunk['chunk_id'])
    keys = {block['key'] for block in context['blocks']}
    assert context['complete'] and context['omitted'] == []
    expected_method = {block['key'] for block in real_bundle['blocks']
                       if block['key'] == 'A.7' or block['key'].startswith('A.7.')}
    assert expected_method <= keys
    assert '1' in keys and 'table-2' in keys
    assert 'A.8.3.1' not in keys


def test_ids_are_repeatable_and_change_with_parse_or_strategy(real_manual, real_bundle):
    repeat = build_bundle(real_manual, real_bundle['parse_id'])
    assert repeat == real_bundle
    new_strategy = build_bundle(real_manual, real_bundle['parse_id'], strategy_version='test-next-strategy')
    new_parse = build_bundle(real_manual, identity('test-manual-parse', 'different-parser-version'))
    for other in (new_strategy, new_parse):
        assert other['run_id'] != real_bundle['run_id']
        assert {c['chunk_id'] for c in other['chunks']}.isdisjoint(
            c['chunk_id'] for c in real_bundle['chunks'])
        assert {c['anchor'] for c in other['chunks']} == {c['anchor'] for c in real_bundle['chunks']}
        assert other['validation']['passed']
    assert [b['block_id'] for b in new_strategy['blocks']] == [b['block_id'] for b in real_bundle['blocks']]
    assert {b['block_id'] for b in new_parse['blocks']}.isdisjoint(b['block_id'] for b in real_bundle['blocks'])


def changed_map(monkeypatch, mutate):
    original = manual_reader.read_json

    def read(path):
        value = deepcopy(original(path))
        if Path(path).name == 'source-map.json':
            mutate(value)
        return value

    monkeypatch.setattr(manual_reader, 'read_json', read)


def test_manual_rejects_changed_hash(monkeypatch):
    changed_map(monkeypatch, lambda mapping: mapping.update(markdown_sha256='0' * 64))
    with pytest.raises(KBError) as error:
        manual_reader.load_manual(MANUAL_DIRECTORY)
    assert error.value.code == 'MANUAL_HASH_MISMATCH'


@pytest.mark.parametrize('target,field,value,code', [
    ('page', 'markdown_end_line', 1000000, 'MANUAL_PAGE_MISMATCH'),
    ('page', 'markdown_start_line', 1, 'MANUAL_PAGE_MISMATCH'),
    ('heading', 'markdown_content_end_line', 1000000, 'MANUAL_HEADING_MISMATCH'),
    ('heading', 'parent_id', 'missing-parent', 'MANUAL_HEADING_MISMATCH'),
    ('heading', 'parent_id', 'preface', 'MANUAL_HEADING_MISMATCH'),
    ('heading', 'markdown_content_end_line', 376, 'MANUAL_HEADING_MISMATCH'),
])
def test_manual_rejects_invalid_source_map_boundaries_and_hierarchy(monkeypatch, target, field, value, code):
    def mutate(mapping):
        entity = mapping['pages'][-1] if target == 'page' else next(
            heading for heading in mapping['headings'] if heading['id'] == 'A.7')
        entity[field] = value

    changed_map(monkeypatch, mutate)
    with pytest.raises(KBError) as error:
        manual_reader.load_manual(MANUAL_DIRECTORY)
    assert error.value.code == code


def synthetic_manual(text):
    """Construct the explicit in-memory contract, with no PDF or database I/O."""
    lines = text.splitlines(); headings = []; stack = []; page_for_line = {}; pages = []
    for line_number, line in enumerate(lines, 1):
        marker = re.search(r'pdf_page=(\d+)', line)
        if marker:
            pages.append({'pdf_page': int(marker[1]), 'printed_page': marker[1]})
        page_for_line[line_number] = len(pages)
        match = re.match(r'^(#{1,6}) (.+)$', line)
        if not match:
            continue
        level = len(match[1]); title = match[2]
        while stack and stack[-1]['heading_level'] >= level:
            stack.pop()
        heading = {'id': 'document' if level == 1 else title.split()[0], 'title': title,
                   'heading_level': level, 'parent_id': stack[-1]['id'] if stack else None,
                   'markdown_start_line': line_number}
        headings.append(heading); stack.append(heading)
    for index, heading in enumerate(headings):
        following = headings[index + 1:]
        heading['markdown_content_end_line'] = following[0]['markdown_start_line'] - 1 if following else len(lines)
        next_sibling = next((h for h in following if h['heading_level'] <= heading['heading_level']), None)
        heading['markdown_section_end_line'] = next_sibling['markdown_start_line'] - 1 if next_sibling else len(lines)
    return {'lines': lines, 'page_for_line': page_for_line, 'page_count': len(pages),
            'mapping': {'source_sha256': digest(['synthetic-pdf', text]), 'markdown_sha256': digest(text),
                        'markdown': 'SYNTHETIC.md', 'headings': headings, 'pages': pages}}


CONTINUATION_SAMPLE = '''<!-- source: pdf_page=1 -->
# SYNTHETIC 食品限量示例
## 1 范围
本虚构夹具仅适用于食品甲与食品乙。
## 2 要求
**表 1 两种食品限量[^t]**
| 食品 | 项目 | 限量（mg/kg）[^u] | 检验方法 |
|---|---|---|---|
| 食品甲 | 指标 X | ≤ 1[^a] | A.3 |

<!-- source: pdf_page=2 -->
**表 1（续）**
| 食品 | 项目 | 限量（mg/kg）[^u] | 检验方法 |
|---|---|---|---|
| 食品 | 项目 | 限量（mg/kg）[^u] | 检验方法 |
| 食品乙 | 指标 X | ≤ 2[^b] | A.3 |

[^a]: 食品甲限可食部分。
[^b]: 食品乙按干基计。
[^t]: 本表仅用于虚构样例。
[^u]: 限量单位为 mg/kg。
注：表中数值不构成真实食品要求。
## A.1 一般规定
本节是虚构通用规定。
## A.2 安全提示
本节是虚构安全提示。
## A.3 指标 X 的测定
使用方法正文。
### A.3.1 操作步骤
保留完整方法步骤。
'''


def build_synthetic(text):
    return build_bundle(synthetic_manual(text), identity('synthetic-chunk-parse', digest(text)))


def test_continuation_bundle_keeps_distinct_food_rows_units_and_scoped_footnotes():
    value = build_synthetic(CONTINUATION_SAMPLE)
    assert value['validation']['passed'], value['validation']['issues']
    table, = [chunk for chunk in value['chunks'] if chunk['kind'] == 'table']
    rows = [chunk for chunk in value['chunks'] if chunk['kind'] == 'table_row']
    assert len(table['table']['physical_fragments']) == 2
    assert len(table['table']['repeated_header_rows']) == 1
    assert [row['table']['row'][:3] for row in rows] == [
        ['食品甲', '指标 X', '≤ 1[^a]'], ['食品乙', '指标 X', '≤ 2[^b]']]
    assert [row['source']['pdf_pages'] for row in rows] == [[1], [2]]
    for row, required, excluded in ((rows[0], 'a', 'b'), (rows[1], 'b', 'a')):
        context = expand_context(value, row['chunk_id'])
        assert context['complete'] and context['omitted'] == []
        keys = {block['key'] for block in context['blocks']}
        assert {'1', '2', 'table-1', 'A.1', 'A.2', 'A.3', 'A.3.1'} <= keys
        assert 'mg/kg' in row['retrieval_text']
        notes = '\n'.join(row['table']['footnotes'])
        for marker in (required, 't', 'u'):
            assert f'[^{marker}]:' in notes
            assert f'[^{marker}]:' in row['retrieval_text']
        assert f'[^{excluded}]:' not in notes
        assert '表中数值不构成真实食品要求' in notes
        own_links = [link['target_block_id'] for link in value['links']
                     if link['chunk_id'] == row['chunk_id'] and link['role'] == 'footnote']
        linked_labels = {block['metadata'].get('footnote_label') for block in context['blocks']
                         if block['block_id'] in own_links}
        assert {required, 't', 'u', '注'} <= linked_labels
        assert excluded not in linked_labels
        assert row['executable'] is False


def test_missing_continuation_footnote_is_incomplete_and_keeps_both_physical_fragments():
    value = build_synthetic(CONTINUATION_SAMPLE.replace('[^a]: 食品甲限可食部分。\n', ''))
    table, = [chunk for chunk in value['chunks'] if chunk['kind'] == 'table']
    row = chunks_by_anchor(value)['table-1:row:1']
    assert not value['validation']['passed']
    assert len(table['table']['physical_fragments']) == 2
    assert any(issue['code'] == 'MISSING_FOOTNOTE' for issue in row['quality']['issues'])
    assert row['quality']['status'] == 'context_incomplete'
    assert row['executable'] is False
    assert '食品甲' in row['raw_markdown'] and '≤ 1[^a]' in row['raw_markdown']


def test_ambiguous_continuation_remains_three_physical_tables_with_unshared_values():
    prefix = '<!-- source: pdf_page=1 -->\n# SYNTHETIC 歧义续表\n## 1 范围\n虚构范围。\n## 2 要求\n'
    table = '**表 1 基表**\n| 食品 | 限量（mg/kg） |\n|---|---|\n| {food} | {value} |\n\n'
    text = prefix + table.format(food='食品甲', value='1') + table.format(food='食品乙', value='2')
    text += '<!-- source: pdf_page=2 -->\n' + table.replace('基表', '（续）').format(food='食品丙', value='3')
    value = build_synthetic(text)
    tables = [chunk for chunk in value['chunks'] if chunk['kind'] == 'table']
    rows = [chunk for chunk in value['chunks'] if chunk['kind'] == 'table_row']
    assert len(tables) == 3 and all(len(chunk['table']['physical_fragments']) == 1 for chunk in tables)
    assert [row['table']['row'] for row in rows] == [['食品甲', '1'], ['食品乙', '2'], ['食品丙', '3']]
    assert not value['validation']['passed']
    assert rows[-1]['quality']['status'] == 'context_incomplete'
    assert rows[-1]['executable'] is False
    assert any(issue['code'] == 'AMBIGUOUS_CONTINUATION' for issue in rows[-1]['quality']['issues'])


def test_oversize_formula_clause_is_preserved_whole_and_marked_for_review():
    long_text = '仅供虚构夹具' * (STRATEGY_CONFIG['max_chars_warning'] // len('仅供虚构夹具') + 1)
    formula = '$$\ny=x+1 \\tag{A.99}\n$$\n式中：\n$x$——虚构变量。'
    text = '<!-- source: pdf_page=1 -->\n# SYNTHETIC 长公式\n## A.3 结果计算\n' + long_text + '\n\n' + formula
    value = build_synthetic(text)
    chunk = chunks_by_anchor(value)['A.3']
    assert len(chunk['raw_markdown']) > STRATEGY_CONFIG['max_chars_warning']
    assert long_text in chunk['raw_markdown'] and formula in chunk['raw_markdown']
    assert chunk['raw_markdown'].count('$$') == 2
    assert chunk['raw_markdown'].endswith('$x$——虚构变量。')
    assert chunk['quality']['status'] == 'context_incomplete'
    assert any(issue['code'] == 'OVERSIZE_ATOMIC_SECTION' for issue in chunk['quality']['issues'])
    assert chunk['executable'] is False
    context = expand_context(value, chunk['chunk_id'])
    assert context['complete']
    own = next(block for block in context['blocks'] if block['key'] == 'A.3')
    assert own['text_raw'] == chunk['raw_markdown']

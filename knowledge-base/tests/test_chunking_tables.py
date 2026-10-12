from food_kb.chunking.tables import extract_tables, merge_continuations


def test_markdown_preserves_blank_values_and_escaped_pipes():
    source = '**表 2 指标**\n\n| 食品 | 值 | 条件 |\n|---|---|---|\n| A | 1 | x \\| y |\n| B | | `a|b` |'
    table, = extract_tables(source.splitlines(), start_line=10)
    assert table['caption'] == '表 2 指标'
    assert table['line_start'] == 12
    assert table['headers'] == ['食品', '值', '条件']
    assert table['rows'][0]['cells'][2]['value'] == 'x | y'
    empty = table['rows'][1]['cells'][1]
    assert empty['value'] == empty['text_raw'] == ''
    assert not empty['inherited']
    assert empty['source_cell'] == {'row': 3, 'column': 2}
    assert table['rows'][1]['cells'][2]['value'] == '`a|b`'


def test_html_rowspan_colspan_and_header_paths_have_source_cells():
    source = '''**表 1 范围**
<table>
<thead><tr><th rowspan="2">食品</th><th colspan="2">指标</th></tr>
<tr><th>上限</th><th>单位</th></tr></thead>
<tbody><tr><td>A</td><td rowspan="2">1</td><td>mg/kg</td></tr>
<tr><td>B</td><td></td></tr></tbody>
</table>'''
    table, = extract_tables(source.splitlines())
    assert table['header_paths'] == [['食品'], ['指标', '上限'], ['指标', '单位']]
    first, second = table['rows']
    assert first['cells'][1]['rowspan'] == 2
    derived = second['cells'][1]
    assert derived['text_raw'] == '' and derived['value'] == '1'
    assert derived['inherited'] and derived['from_cell'] == {'row': 3, 'column': 2}
    assert second['cells'][2]['value'] == '' and not second['cells'][2]['inherited']
    assert (first['line_start'], first['line_end']) == (5, 5)
    assert (second['line_start'], second['line_end']) == (6, 6)
    assert (first['cells'][0]['line_start'], first['cells'][0]['line_end']) == (5, 5)
    assert table['issues'] == []


def test_html_line_offset_is_not_changed_by_htmlparser_column_tracking():
    source = '<table>\n  <tr><th>A</th><th>B</th></tr>\n  <tr><td>x</td><td>y</td></tr>\n</table>'
    table, = extract_tables(source.splitlines(), start_line=71)
    row, = table['rows']
    assert (row['line_start'], row['line_end']) == (73, 73)
    assert all((cell['line_start'], cell['line_end']) == (73, 73) for cell in row['cells'])


def test_physical_continuation_merges_only_same_explicit_number_and_headers():
    source = '''**表 1 食品限量**
| 食品 | 值 |
|---|---|
| A | 1 |

<!-- source: pdf_page=2 -->
**表 1（续）**
| 食品 | 值 |
|---|---|
| 食品 | 值 |
| B | 2 |

**表 2 其他食品**
| 食品 | 值 |
|---|---|
| C | 3 |'''
    physical = extract_tables(source.splitlines())
    merged = merge_continuations(physical, lambda line: 1 if line < 6 else 2)
    assert len(physical) == 3 and len(merged) == 2
    assert [row['cells'][0]['value'] for row in merged[0]['rows']] == ['A', 'B']
    assert len(merged[0]['physical_fragments']) == 2
    assert merged[0]['rows'][1]['row_index'] == 2
    assert merged[0]['rows'][1]['physical_row_index'] == 3
    assert merged[0]['rows'][1]['pdf_page'] == 2
    assert merged[0]['rows'][1]['cells'][0]['source_cell']['fragment_key'] == 'table-2'
    assert len(merged[0]['repeated_header_rows']) == 1
    assert 'physical_fragments' not in physical[0]


def test_missing_footnote_is_detected_after_deletion():
    source = '**表 1 指标**\n| 食品 | 值[^a] |\n|---|---|\n| A | 1 |\n\n[^a]: 以干基计。'
    table, = extract_tables(source.splitlines())
    assert table['footnotes'][0]['text'] == '以干基计。'
    assert table['issues'] == []
    broken, = extract_tables(source.splitlines()[:-1])
    assert not broken['footnotes']
    assert any(issue['code'] == 'MISSING_FOOTNOTE' for issue in broken['issues'])


def test_adjacent_notes_have_explicit_scope_and_do_not_guess_markers():
    source = '| 食品 | 值<sup>a</sup> |\n|---|---|\n| A | 1 |\n\n注：此表为虚构样例。\na 以可食部分计。\nb 无引用范围。'
    table, = extract_tables(source.splitlines())
    assert [note['relation'] for note in table['footnotes']] == ['table_note', 'explicit_reference', 'unlinked_marker']
    assert [issue['code'] for issue in table['issues']] == ['UNLINKED_FOOTNOTE']


def test_header_mismatch_and_ambiguous_bases_do_not_merge():
    base = '**表 1 基表**\n| 食品 | 值 |\n|---|---|\n| A | 1 |\n\n'
    wrong = '**表 1（续）**\n| 食品 | 单位 |\n|---|---|\n| B | mg |'
    mismatch = merge_continuations(extract_tables((base + wrong).splitlines()))
    assert len(mismatch) == 2
    assert mismatch[1]['issues'][-1]['code'] == 'CONTINUATION_HEADER_MISMATCH'
    ambiguous = merge_continuations(extract_tables((base + base + wrong.replace('单位', '值')).splitlines()))
    assert len(ambiguous) == 3
    assert ambiguous[-1]['issues'][-1]['code'] == 'AMBIGUOUS_CONTINUATION'


def test_unmarked_adjacent_food_tables_are_never_merged():
    source = '| 食品 | 值 |\n|---|---|\n| A | 1 |\n\n| 食品 | 值 |\n|---|---|\n| B | 2 |'
    assert len(merge_continuations(extract_tables(source.splitlines()))) == 2


def test_malformed_short_rows_and_span_overflow_are_explicit():
    table, = extract_tables('| A | B |\n|---|---|\n| x |'.splitlines())
    assert table['rows'][0]['cells'][1]['missing']
    assert table['issues'][0]['code'] == 'MISSING_PHYSICAL_CELL'
    table, = extract_tables('<table><tr><th>A</th></tr><tr><td rowspan="3">x</td></tr></table>'.splitlines())
    assert table['issues'][0]['code'] == 'SPAN_BEYOND_TABLE'


def test_fenced_examples_are_not_claimed_as_real_tables():
    source = '```md\n| A | B |\n|---|---|\n| x | y |\n```'
    assert extract_tables(source.splitlines()) == []


def test_caption_footnote_and_continuation_note_keep_logical_scope():
    source = '''**表 1 指标[^a]**
| 食品 | 值 |
|---|---|
| A | 1 |

**表 1（续）**
| 食品 | 值 |
|---|---|
| B | 2 |

[^a]: 以干基计。'''
    table, = merge_continuations(extract_tables(source.splitlines()))
    assert table['issues'] == []
    assert len(table['footnotes']) == 1
    assert table['footnotes'][0]['relation'] == 'explicit_reference'


def test_unresolved_fragment_prevents_jumping_to_an_earlier_base():
    source = '''**表 1 基表**
| 食品 | 值 |
|---|---|
| A | 1 |

**表 1（续）**
| 食品 | 单位 |
|---|---|
| B | mg/kg |

**表 1（续）**
| 食品 | 值 |
|---|---|
| C | 3 |'''
    tables = merge_continuations(extract_tables(source.splitlines()))
    assert len(tables) == 3
    assert tables[-1]['issues'][-1]['code'] == 'AMBIGUOUS_CONTINUATION'

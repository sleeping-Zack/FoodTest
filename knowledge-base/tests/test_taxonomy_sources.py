from copy import deepcopy
import json
from pathlib import Path

import pytest

from food_kb.common import KBError, file_hash, identity
from food_kb.taxonomy.sources import (_source_names, _necessary_condition, build_standard,
                                      _qualified_tables, build_guide, condition, node, validate_snapshot)


def record(raw):
    return {'source_key': 'references/taxonomy.json', 'index_asset_id': '1' * 64,
            'legacy_id': identity('synthetic', 'taxonomy'), 'raw_payload': raw}


def table_source(namespace='GB_2763-2026'):
    return {'namespace': namespace, 'standard': 'SYNTHETIC', 'sha256': '2' * 64,
            'coded_nodes': [], 'tables': [{'id': 'SYNTHETIC-T1', 'pdf_page': 1, 'bbox': [0, 0, 10, 10],
             'cells': [['食品类别', '类别说明', '测定部位'],
                       ['水果（仁果类）', '苹果 梨 山楂等\n、 、', '全果（去柄）'],
                       ['蔬菜（根茎类）', '根茎类\n萝卜 胡萝卜等\n、', '去除顶部叶及叶柄'],
                       ['蔬菜（叶菜类）', '绿叶类\n萝卜叶 菠菜等\n、', '整棵，去根']]}]}


@pytest.mark.parametrize('raw', ['不包括苹果汁', '水果（不包括苹果汁）',
                                '产品是以苹果为原料加工而成', '食品（包括苹果但以其为原料加工）'])
def test_negative_and_ingredient_clauses_do_not_create_positive_examples(raw):
    values = _source_names(raw, [{'synthetic': True}])
    assert not any(item['kind'] == 'explicit_example' for item in values)


def test_explicit_species_keep_food_and_measurement_part_separate():
    ns = build_standard(record(table_source()), identity('synthetic', 'version'), 'unverified')
    apples = [n for n in ns['nodes'] if any(x['value'] == '苹果' for x in n['names'])]
    assert len(apples) == 1 and apples[0]['measurement_part'] == '全果（去柄）'
    radish = [n for n in ns['nodes'] if any(x['value'] == '萝卜' for x in n['names'])]
    leaves = [n for n in ns['nodes'] if any(x['value'] == '萝卜叶' for x in n['names'])]
    assert len(radish) == len(leaves) == 1 and radish[0]['id'] != leaves[0]['id']
    assert all(x['verification_state'] == 'source_claim' for n in ns['nodes'] for x in n['names'])


def test_same_label_and_code_in_two_standards_do_not_share_identity():
    raw = {'namespace': 'A', 'standard': 'SYNTHETIC', 'sha256': '2' * 64, 'tables': [],
           'coded_nodes': [{'id': 'source-1', 'classification_code': '01', 'label': '乳制品',
                            'label_status': '原文类别', 'pdf_page': 1}]}
    first = build_standard(record(raw), identity('synthetic', 'one'), 'unverified')
    second = build_standard(record(raw | {'namespace': 'B'}), identity('synthetic', 'two'), 'unverified')
    assert first['nodes'][0]['code'] == second['nodes'][0]['code'] == '01'
    assert first['nodes'][0]['id'] != second['nodes'][0]['id']


def test_missing_parent_is_a_gap_not_an_invented_relation():
    raw = {'namespace': 'A', 'standard': 'SYNTHETIC', 'sha256': '2' * 64, 'tables': [],
           'coded_nodes': [{'id': 'child', 'parent_id': 'absent', 'classification_code': '01.1',
                           'label': '乳制品', 'label_status': '原文类别', 'pdf_page': 1}]}
    ns = build_standard(record(raw), identity('synthetic', 'one'), 'unverified')
    assert not ns['relations'] and ns['gaps'][0]['kind'] == 'missing_parent'


def test_necessary_heat_condition_has_a_final_category_and_keeps_source_only_clauses():
    ev = [{'synthetic': True}]
    candidate = node('A', 'one', '巴氏杀菌乳', ev)
    raw = '巴氏杀菌乳指仅以生牛（羊）乳为原料，经巴氏杀菌等工序制得的液体产品，不包括高温杀菌乳。'
    _necessary_condition(candidate, {'statement': ({'quote': raw}, ev)})
    assert candidate['condition']['state'] == 'partial'
    assert candidate['condition']['ast'] == {'op': 'eq', 'field': 'primary_heat_treatment', 'value': '巴氏杀菌'}
    assert candidate['condition']['necessary_only'] and candidate['condition']['raw'] == raw
    assert 'process' not in candidate['condition']['required_facts']


def test_percentage_binds_the_named_milk_material_instead_of_any_ingredient():
    ev = [{'synthetic': True}]
    candidate = node('A', 'one', '调制乳', ev)
    raw = '调制乳指以不低于 80%的生牛（羊）乳或复原乳为主要原料，添加其他原料制成的液体产品。'
    _necessary_condition(candidate, {'statement': ({'quote': raw}, ev)})
    assert candidate['condition']['ast'] == {'op': 'ge', 'field': 'milk_ingredient_proportion_percent', 'value': '80', 'value_type': 'number'}


def test_required_facts_use_the_registered_names():
    result = condition('以某原料经加工，用于某用途，按指定部位取样并包装', [{'synthetic': True}])
    assert set(result['required_facts']) == {'ingredients', 'process', 'intended_use', 'food_part', 'packaging'}


def snapshot():
    ns = build_standard(record(table_source()), identity('synthetic', 'version'), 'unverified')
    return {'schema_version': 'taxonomy-1', 'executable': False, 'review_state': 'review_pending', 'namespaces': [ns]}


def test_graph_rejects_cross_namespace_and_cyclic_links():
    payload = snapshot(); ns = payload['namespaces'][0]
    link = ns['relations'][0]
    ns['relations'].append(link | {'id': 'cycle', 'child_id': link['parent_id'], 'parent_id': link['child_id']})
    with pytest.raises(KBError, match='cycle'):
        validate_snapshot(payload)
    payload = snapshot(); payload['namespaces'][0]['relations'][0]['parent_id'] = 'foreign-node'
    with pytest.raises(KBError, match='crosses namespace'):
        validate_snapshot(payload)


def test_namespaces_and_candidate_review_boundary_are_checked():
    payload = snapshot(); payload['namespaces'].append(deepcopy(payload['namespaces'][0]))
    with pytest.raises(KBError, match='Namespace repeated'):
        validate_snapshot(payload)
    payload = snapshot(); payload['executable'] = True
    with pytest.raises(KBError, match='non-executable'):
        validate_snapshot(payload)


def guide_fixture():
    quote = '米粉制品包括年糕、米线。'
    source = {'source': 'references/SYNTHETIC.txt', 'sha256': '2' * 64}
    guide = source | {'sections': [{'id': 'S01', 'name': '米粉制品', 'scope': 'SYNTHETIC 抽检范围', 'product_types': quote}],
        'tables': [{'id': 'T1', 'section_id': 'S01', 'caption': '表1-1米粉制品检验项目', 'pdf_page': 1,
                    'kind': 'inspection_items', 'notes': ['SYNTHETIC 有条件适用注']}],
        'inspection_rows': [{'id': 'R1', 'section_id': 'S01', 'caption': '表1-1米粉制品检验项目',
            'item_raw': 'SYNTHETIC 项目', 'basis_raw': 'SYNTHETIC 依据', 'method_raw': 'SYNTHETIC 方法',
            'raw_cells': ['1', 'SYNTHETIC 项目', 'SYNTHETIC 依据', 'SYNTHETIC 方法'], 'category_raw': None,
            'pdf_page': 1, 'source_row': 1}]}
    definition = {'source_sha256': source['sha256'], 'definitions': [{'section_id': 'S01', 'name': '米粉制品',
        'raw_text': quote, 'pdf_page': 1, 'pdf_end_page': 1,
        'statements': [{'id': 'P1', 'quote': quote, 'pdf_page': 1, 'pdf_end_page': 1, 'location_status': 'paragraph_span'}],
        'relationships': [{'name': name, 'path': ['米粉制品', name], 'statement_id': 'P1', 'relation': '原文列举'}
                          for name in ['年糕', '米线']], 'exclusions': []}]}
    pages = source | {'pages': [{'pdf_page': 1, 'text': quote}]}
    bridges = {'bridges': [{'section_id': 'S01', 'name': '米线', 'targets': ['米粉制品'], 'anchor': '米粉制品包括年糕、米线'}]}
    return [record(value) for value in (guide, definition, pages, bridges)]


def test_variety_has_an_evidenced_complete_path_to_the_parent_table_without_becoming_required():
    ns = build_guide(*guide_fixture(), identity('synthetic', 'guide-version'), 'unverified')
    variety = next(n for n in ns['nodes'] if n['name'] == '年糕')
    parent = next(n for n in ns['nodes'] if n['name'] == '米粉制品' and n['kind'] != 'inspection_table')
    table = next(n for n in ns['nodes'] if n['kind'] == 'inspection_table')
    assert any(r['child_id'] == variety['id'] and r['parent_id'] == parent['id'] for r in ns['relations'])
    assert any(r['child_id'] == parent['id'] and r['parent_id'] == table['id'] for r in ns['relations'])
    assert any(r['kind'] == 'manual_bridge' for r in ns['relations'])
    assert ns['requirements'][0]['node_id'] == table['id'] and ns['requirements'][0]['task_required'] == 'unknown'
    assert ns['requirements'][0]['condition']['state'] == 'unresolved'
    assert '有条件适用注' in ns['requirements'][0]['condition']['raw']


@pytest.mark.parametrize('mutation', ['different_raw', 'wrong_page', 'invented_name', 'missing_bridge_anchor', 'wrong_bridge_target'])
def test_guide_source_clauses_and_bridges_are_checked_before_candidates(mutation):
    inputs = guide_fixture()
    definition = inputs[1]['raw_payload']['definitions'][0]
    if mutation == 'different_raw':
        definition['raw_text'] = 'SYNTHETIC 改过的定义'
    elif mutation == 'wrong_page':
        definition['statements'][0]['pdf_page'] = definition['statements'][0]['pdf_end_page'] = 2
    elif mutation == 'invented_name':
        definition['relationships'][0]['name'] = '巧克力年糕'
    elif mutation == 'missing_bridge_anchor':
        inputs[3]['raw_payload']['bridges'][0]['anchor'] = 'SYNTHETIC 原文没有的锚点'
    else:
        inputs[3]['raw_payload']['bridges'][0]['targets'] = ['另一张不存在的表']
    with pytest.raises(KBError):
        build_guide(*inputs, identity('synthetic', 'guide-version'), 'unverified')


@pytest.mark.parametrize('caption', ['其他父类（巴氏杀菌乳）', '液体乳（不包括巴氏杀菌乳）',
                                   '液体乳（仅限巴氏杀菌乳）', '液体乳（巴氏杀菌乳、灭菌乳）'])
def test_qualified_caption_does_not_guess_parent_exclusions_or_multiple_children(caption):
    ev = [{'synthetic': True}]
    child = node('A', 'one', '巴氏杀菌乳', ev)
    paths = {('液体乳', '巴氏杀菌乳'): child}
    table = node('A', 'table', caption, ev, kind='inspection_table')
    assert not _qualified_tables('液体乳', paths, {'table': table})


def test_qualified_caption_requires_one_unique_sourced_descendant():
    ev = [{'synthetic': True}]
    child = node('A', 'one', '巴氏杀菌乳', ev)
    another = node('A', 'two', '巴氏杀菌乳', ev)
    paths = {('液体乳', '巴氏杀菌乳'): child, ('液体乳', '歧义支系', '巴氏杀菌乳'): another}
    table = node('A', 'table', '液体乳（巴氏杀菌乳）', ev, kind='inspection_table')
    assert not _qualified_tables('液体乳', paths, {'table': table})


def test_real_liquid_milk_source_associates_only_each_qualified_table_and_keeps_notes():
    root = Path(__file__).resolve().parents[2]
    paths = ['references/食品标准资料库/索引/食品项目标准方法关联.json',
             'references/食品标准资料库/索引/产品种类定义表.json',
             'food-classifier/resources/guide-pages.json', 'food-classifier/resources/inspection-bridges.json']
    if any(not (root / key).is_file() for key in paths):
        pytest.skip('Private source indexes are not distributed with public code.')
    inputs = []
    for key in paths:
        path = root / key
        item = record(json.loads(path.read_text(encoding='utf-8-sig')))
        item.update(source_key=key, index_asset_id=file_hash(path))
        inputs.append(item)
    ns = build_guide(*inputs, identity('synthetic', 'real-source-engineering-check'), 'unverified')
    section_root = next(n for n in ns['nodes'] if n['name'] == '液体乳' and n['kind'] != 'inspection_table')
    table_nodes = {n['id']: n for n in ns['nodes'] if n['kind'] == 'inspection_table'}
    assert not any(r['child_id'] == section_root['id'] and r['parent_id'] in table_nodes for r in ns['relations'])
    for name, expected, page in [('灭菌乳', 9, 71), ('巴氏杀菌乳', 10, 71), ('调制乳', 7, 72)]:
        child = next(n for n in ns['nodes'] if n['name'] == name and n['kind'] != 'inspection_table')
        links = [r for r in ns['relations'] if r['child_id'] == child['id'] and r['parent_id'] in table_nodes]
        assert len(links) == 1
        table = table_nodes[links[0]['parent_id']]
        assert table['name'] == f'液体乳（{name}）'
        assert table['evidence'][0]['locator']['pdf_page'] == page
        rows = [r for r in ns['requirements'] if r['node_id'] == table['id']]
        assert len(rows) == expected and all(r['task_required'] == 'unknown' for r in rows)
        if name == '调制乳':
            assert all('非灭菌工艺' in r['condition']['raw'] for r in rows)

"""Replay candidate classification and preserve evidence; never approve knowledge."""
import argparse
import csv
from pathlib import Path
import shutil

from food_kb.common import digest, file_hash, read_json, write_json
from food_kb.config import Config
from food_kb.db.session import connect, migrate
from food_kb.taxonomy.classify import classify_food
from food_kb.taxonomy.repository import build_snapshot, read_snapshot
from food_kb.taxonomy.viewer import write_viewer
from verify_kb06 import fingerprints


GUIDE = 'inspection-guide-2026-upper'


def fact(value, name, kind='string'):
    return {'raw_value': value, 'normalized_value': value, 'type': kind,
            'source_ref': 'synthetic:kb08-demo:' + name, 'role': 'sample_fact',
            'verification_state': 'verified', 'synthetic': True}


def examples(snapshot):
    cases = [('rice-cake', '年糕', {}, '具体品种到上级表'),
             ('apple', '苹果', {}, '水果与果汁边界'),
             ('apple-juice', '苹果汁', {}, '果汁独立查询'),
             ('radish', '萝卜', {}, '不同标准分别分类'),
             ('radish-leaf', '萝卜叶', {}, '部位名称不能混用'),
             ('milk-unknown', '牛奶', {}, '缺工艺保留分支'),
             ('milk-pasteurized', '牛奶', {'primary_heat_treatment': fact('巴氏杀菌', 'primary_heat_treatment')}, '补充最终热处理类别'),
             ('milk-process-fragment', '牛奶', {'process': fact('巴氏杀菌', 'process')}, '普通工序片段不能排分支'),
             ('milk-conflict', '牛奶', {'primary_heat_treatment': [fact('巴氏杀菌', 'heat-one'), fact('灭菌', 'heat-two')]}, '相互冲突事实'),
             ('milk-ratio', '牛奶', {'milk_ingredient_proportion_percent': fact('79', 'milk-proportion', 'number')}, '明确乳原料配方比例不足'),
             ('milk-generic-ratio', '牛奶', {'ingredient_proportion': fact('79', 'generic-proportion', 'number')}, '未说明配料对象不能借用比例'),
             ('milk-invalid-ratio', '牛奶', {'milk_ingredient_proportion_percent': fact('101', 'invalid-proportion', 'number')}, '比例越界返回未知'),
             ('unknown', '火星糖块', {}, '陌生名称保留缺口')]
    results = []
    for identifier, name, other, label in cases:
        facts = {'food_name': fact(name, identifier), **other}
        result = classify_food(snapshot, facts)
        assert result == classify_food(snapshot, facts)
        assert result['executable'] is False and result['mandatory_for_task'] is False
        assert all(not g['executable'] and not g['mandatory_for_task'] for g in result['namespaces'])
        results.append({'id': identifier, 'food_name': name, 'title': label,
                        'scenario_label': '真实知识来源＋合成工程事实', 'synthetic_input': True,
                        'note': '名称及工艺等事实为受控工程输入，非真实样品或专家标签；知识仍待审，示例不证明总体分类准确率。',
                        'result': result})
    by_id = {e['id']: e['result'] for e in results}
    group = lambda identifier: next(g for g in by_id[identifier]['namespaces'] if g['namespace'] == GUIDE)
    milk = lambda identifier, label: next(b for b in group(identifier)['branches'] if b['label'] == label)
    rice = next(b for b in group('rice-cake')['branches'] if b['label'] == '年糕')
    assert any(p['label'] == '米粉制品' for p in rice['path'])
    assert len(rice['requirements']) == 8 and rice['association_count'] == 8
    assert {r['semantic']['project'] for r in rice['requirements']} == {
        '苯甲酸及其钠盐\n（以苯甲酸计）','山梨酸及其钾盐\n（以山梨酸计）',
        '脱氢乙酸及其钠盐\n（以脱氢乙酸计）','二氧化硫残留量a',
        '合成着色剂（柠檬黄、日落\n黄、喹啉黄、亮蓝、靛蓝）\nb',
        '甜蜜素（以环己基氨基磺酸\n计）b','菌落总数c','大肠菌群c'}
    assert not any('苹果汁' in m['value'] for g in by_id['apple']['namespaces'] for b in g['branches'] for m in b['matched_names'])
    assert not any(m['value'] == '萝卜' for g in by_id['radish-leaf']['namespaces'] for b in g['branches'] for m in b['matched_names'])
    assert len([g for g in by_id['radish']['namespaces'] if g['branches']]) >= 2
    assert all(milk('milk-unknown', label)['status'] == 'unknown' for label in ('巴氏杀菌乳', '灭菌乳', '调制乳'))
    assert milk('milk-pasteurized', '灭菌乳')['status'] == 'not_applicable'
    assert milk('milk-pasteurized', '巴氏杀菌乳')['status'] == 'unknown'
    assert all(milk('milk-unknown', label)['association_count'] == count
               for label, count in (('灭菌乳', 9), ('巴氏杀菌乳', 10), ('调制乳', 7)))
    assert all(len(milk('milk-unknown', label)['requirements']) == count
               for label, count in (('灭菌乳', 9), ('巴氏杀菌乳', 10), ('调制乳', 7)))
    assert milk('milk-process-fragment', '灭菌乳')['status'] == 'unknown'
    assert all(milk('milk-conflict', label)['status'] == 'conflicting' for label in ('巴氏杀菌乳', '灭菌乳'))
    assert milk('milk-ratio', '调制乳')['status'] == 'not_applicable'
    assert milk('milk-generic-ratio', '调制乳')['status'] == 'unknown'
    assert milk('milk-invalid-ratio', '调制乳')['status'] == 'unknown'
    assert all(g['association_count'] is None for g in by_id['unknown']['namespaces'])
    hint = next(n for n in snapshot['namespaces'] if n['namespace'] == GUIDE)
    wrong_category = next(n for n in hint['nodes'] if n['name'] == '巴氏杀菌乳' and n['kind'] != 'inspection_table')
    facts = {'food_name':fact('苹果','name'), 'category_id':fact(wrong_category['id'],'declared-category'),
             'category_namespace':fact(GUIDE,'declared-namespace')}
    result = classify_food(snapshot, facts)
    branch = next(b for g in result['namespaces'] for b in g['branches'] if b['category_id'] == wrong_category['id'])
    assert branch['status'] == 'unknown' and 'self_reported_category_hint' in branch['candidate_reasons']
    results.append({'id':'category-claim','food_name':'苹果','title':'错报乳类别仅作线索',
                    'scenario_label':'真实知识来源＋合成工程事实', 'synthetic_input':True,
                    'note':'故意填错类别声明；不能用自报类别完成独立分类。','result':result})
    return results


def verify(config, output):
    out = Path(output)
    if (out / 'result.json').exists():
        raise ValueError('Use a fresh evidence directory for a new verification run.')
    out.mkdir(parents=True, exist_ok=True)
    before = read_json(out / 'database-before-fingerprints.json')
    with connect(config) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        write_json(out / 'migrations.json', migrate(conn))
        first = build_snapshot(conn, config)
        snapshot = read_snapshot(conn, first['snapshot_id'])
        tables = [r['table_name'] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name")]
        between = fingerprints(conn, tables)
        repeated = build_snapshot(conn, config)
        assert repeated['status'] == 'duplicate' and repeated['snapshot_id'] == first['snapshot_id']
        assert between == fingerprints(conn, tables)
        assert all(set(rows) <= set(between[name]) for name, rows in before.items())
        mutable = {'source_asset','source_registration','schema_migration',
                   'taxonomy_snapshot','taxonomy_snapshot_source'}
        assert all(before[name] == between[name] for name in before if name not in mutable)
        cases = examples(snapshot)
        assert between == fingerprints(conn, tables)
        write_json(out / 'database-after.json', {name:{'rows':len(rows),'sha256':digest(rows)} for name, rows in between.items()})
        server_version = conn.info.server_version
    for relative, expected in read_json(out / 'source-before.json').items():
        assert file_hash(config.source_root / relative) == expected
    for source in snapshot['sources']:
        destination = out / 'source' / source['source_key']
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(config.output_root / source['storage_key'], destination)
        assert file_hash(destination) == source['sha256']
    write_json(out / 'taxonomy.json', snapshot)
    write_json(out / '分类查询样例.json', cases)
    for case in cases:
        write_json(out / 'queries' / (case['id'] + '.json'), case['result'])
    write_viewer(snapshot, cases, out / '分类与适用结果.html', out / 'source')
    with (out / '候选与待补事实.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['场景','体系','类别','状态','已关联项目','待补事实','审核状态','本次必检'])
        for case in cases:
            for group in case['result']['namespaces']:
                for branch in group['branches']:
                    writer.writerow([case['id'],group['namespace'],branch['label'],branch['status'],
                                     branch['association_count'], '、'.join(branch['missing_facts']),branch['review_state'],'未确定'])
    checks = {'old_rows_retained':True,'old_domain_tables_unchanged':True,'repeat_zero_increment':True,
              'classification_queries_read_only':True,'source_files_and_kb07_unchanged':True,
              'ten_namespaces_separate':len(snapshot['namespaces']) == 10,
              'rice_cake_eight_rows_with_source_path':True,'apple_juice_name_boundary':True,
              'radish_leaf_boundary':True,'missing_milk_process_multiple_unknown':True,
              'primary_heat_condition_excludes_only_proved_mismatch':True,'process_fragment_no_exclusion':True,
              'milk_caption_links_only_own_nine_ten_seven_rows':True,
              'fact_conflicts_retained':True,'milk_ratio_basis_separate':True,
              'declared_category_is_hint':True,'no_match_count_unknown':True,
              'real_expert_approval_or_release_created':False}
    assert checks['ten_namespaces_separate']
    result = {'status':'engineering_verification_passed','first':first,'repeat':repeated,
              'database_server_version':server_version,'examples':len(cases),'summary':snapshot['summary'],
              'algorithm_version':cases[0]['result'].get('algorithm_version'), 'checks':checks,
              'knowledge_review_state':'review_pending','executable':False,'scope_complete':False}
    write_json(out / 'checks.json', checks)
    write_json(out / 'result.json', result)
    print(result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    verify(Config.load(args.config), args.output)

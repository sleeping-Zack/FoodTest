"""Replay source-bound extraction in the configured local registry, without approvals."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import shutil

from food_kb.common import digest, file_hash, write_json
from food_kb.config import Config
from food_kb.db.session import connect, migrate
from food_kb.knowledge.service import extract_knowledge, read_extraction, review_queue
from food_kb.knowledge.facts import export_query_examples
from food_kb.knowledge.viewer import write_viewer
from verify_kb06 import fingerprints


def safe_csv(value):
    value = str(value) if value is not None else ''
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value


def verify(config, chunk_run_id, out):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    if (out / 'result.json').exists():
        raise ValueError('Use a fresh evidence directory.')
    with connect(config) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        tables = [r['table_name'] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name")]
        before = fingerprints(conn, tables)
        write_json(out / 'database-before.json', {k: {'rows': len(v), 'sha256': digest(v)} for k, v in before.items()})
        write_json(out / 'migrations.json', migrate(conn))
        tables = [r['table_name'] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name")]
        first = extract_knowledge(conn, config, chunk_run_id)
        report = read_extraction(conn, config, first['extraction_id'])
        between = fingerprints(conn, tables)
        repeated = extract_knowledge(conn, config, chunk_run_id)
        after = fingerprints(conn, tables)
        assert between == after and repeated['status'] == 'duplicate'
        assert first['extraction_id'] == repeated['extraction_id']
        assert all(set(rows) <= set(after[name]) for name, rows in before.items())
        assert all(before[name] == after[name] for name in ('reviewer', 'review_record', 'kb_release', 'release_knowledge', 'rule_binding'))
        write_json(out / 'database-after.json', {k: {'rows': len(v), 'sha256': digest(v)} for k, v in after.items()})
        queue = review_queue(conn, first['extraction_id'])
        fields = conn.execute('''SELECT fe.record_id,fe.field_name,e.* FROM knowledge_field_evidence fe
            JOIN evidence_anchor e USING(evidence_id) JOIN record_extraction re USING(record_id)
            WHERE re.extraction_id=%s ORDER BY fe.record_id,fe.field_name,e.evidence_id''', (first['extraction_id'],)).fetchall()
        catalog = conn.execute('''SELECT c.* FROM standard_scope_catalog c JOIN record_extraction r ON c.scope_record_id=r.record_id
            WHERE r.extraction_id=%s''', (first['extraction_id'],)).fetchall()
        server_version = conn.info.server_version
    records = report['records']; by_key = {r['candidate']['logical_key'].split(':', 1)[1]: r for r in records}
    assert len(records) == 21 and Counter(r['candidate']['record_type'] for r in records) == {'requirement': 9, 'method': 9, 'definition': 2, 'standard_scope_catalog': 1}
    assert report['validation']['passed'] and all(r['validation']['passed'] for r in records)
    assert all(r['review_state'] == 'review_pending' and r['candidate']['executable'] is False for r in records)
    assert all(r['validation']['publication_blockers'] for r in records)
    assert all(r['review_state'] == 'review_pending' for r in queue)
    iron = by_key['requirement:table-2:row:4']['candidate']['fields']
    assert iron['project']['value'] == '铁（Fe）'
    assert iron['value']['raw'] == '0.003 5' and iron['value']['value'] == '0.0035'
    assert iron['unit']['value'] == '%' and iron['basis']['value'] == '以干基计'
    for row, value, basis in [(1, '99.2', '以干基计'), (2, '97.9', '以湿基计')]:
        fields_row = by_key[f'requirement:table-2:row:{row}']['candidate']['fields']
        assert fields_row['value']['value'] == value and fields_row['basis']['value'] == basis
        assert fields_row['requirement_type']['value'] == 'lower_limit'
        assert fields_row['expressed_as']['value'] == '以 Na₂CO₃ 计'
    assert by_key['requirement:table-2:row:5']['candidate']['fields']['expressed_as']['value'] == '以 Pb 计'
    inherited = by_key['requirement:table-1:row:2']['candidate']['fields']['method_reference']['evidence'][0]
    assert inherited['cell']['inherited'] and inherited['cell']['source_cell']['row'] == 2
    blocks = {b['block_id']: b for b in report['blocks']}
    assert blocks[inherited['block_id']]['key'] == 'table-1:row:1'
    formulas = {x['anchor']: blocks[x['block_id']]['text_raw'] for r in records if r['candidate']['record_type'] == 'method'
                for x in r['candidate']['fields']['formulas']['value'] or []}
    assert sum(text.count('\\tag{') for text in formulas.values()) == 8
    assert not [c for c in report['coverage'] if c['status'] == 'not_covered']
    assert len(catalog) == 1 and catalog[0]['payload']['fields']['coverage_state']['value'] == 'partial'
    assert all(f['locator_quality'] == 'page_claim' for f in fields)
    preserved = out / 'source'
    for name, asset in report['source']['preserved_files'].items():
        destination = preserved / name; destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(config.output_root / asset['storage_key'], destination)
        assert file_hash(destination) == asset['asset_id']
        original = config.source_root / 'docs/knowledge-base/manual/GB1886.1-2015' / name
        assert file_hash(original) == asset['asset_id']
    write_json(out / 'knowledge.json', report)
    examples = export_query_examples(report)
    write_json(out / '事实查询样例.json', examples)
    write_json(out / 'field-evidence.json', fields)
    write_json(out / 'review-queue.json', queue)
    write_json(out / 'standard-scope-catalog.json', catalog)
    write_json(out / 'quality-report.json', {'validation': report['validation'], 'records': [
        {'record_id': r['record_id'], 'title': r['candidate']['title'], 'validation': r['validation']} for r in records]})
    with (out / '待审核清单.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream); writer.writerow(['记录ID', '类型', '标题', '修订', '审核状态', '待解决项'])
        for row in queue:
            writer.writerow([safe_csv(x) for x in [row['record_id'], row['record_type'], row['payload']['title'], row['revision'],
                row['review_state'], '；'.join(x['code'] for x in row['validation']['publication_blockers'])]])
    md = ['# GB1886.1-2015 精简事实与原文引用', '', '> 全部真实知识待审核；事实查询不确认样品适用。完整正文见 source/GB1886.1-2015.md。', '',
          '| 项目 | 要求 | 单位 | 基准 | 以何物计 | 检验方法 |', '|---|---|---|---|---|---|']
    for record in records:
        c = record['candidate']
        if c['record_type'] == 'requirement':
            f = c['fields']; value = lambda name: f[name]['value'] or '原条款未单列'
            md.append('| ' + ' | '.join([value('project'), (f['comparator']['value'] or '') + value('value'),
                value('unit'), value('basis'), value('expressed_as'), value('method_reference')]).replace('\n', ' ') + ' |')
    md.extend(['', '## 方法与来源', '', '原文关联不等于完整语义抽取；实验条件、公式复算和业务适用仍待后续检查。', ''])
    for record in records:
        c = record['candidate']
        if c['record_type'] != 'method':
            continue
        md.extend([f"### {c['title']}", '', f"记录：{record['record_id']}；状态：review_pending", ''])
        for p in c['fields']['parameters']['value']:
            md.append(f"- {p['label']}：{p['comparator']} {p['value']}{p['unit']}；原文：{p['raw']}")
        for d in c['fields']['calculation_dependencies']['value']:
            md.append(f"- 计算依赖：{d['source_method']} 的 {d['symbol']} 引用 {d['target_method']}；式 {', '.join(d['formula_ids'])}")
        md.append('- 子方法：' + ('；'.join(x['title'] for x in c['fields']['submethods']['value'] or []) or '原条款未单列'))
        for ref in c['fields']['sections']['value']:
            location = blocks[ref['block_id']]['source']
            md.append(f"- 原文引用：{ref['title']}；PDF页 {location['pdf_pages']}；MD行 {location['line_ranges']}")
        md.append('')
    (out / '知识抽取结果.md').write_text('\n'.join(md), encoding='utf8')
    write_viewer(report, out / '知识抽取结果.html', preserved)
    result = {'status': 'engineering_passed', 'database_server_version': server_version, 'first': first, 'repeat': repeated,
        'records': len(records), 'types': dict(Counter(r['candidate']['record_type'] for r in records)),
        'field_evidence_links': len(fields), 'formula_count': 8, 'source_chunk_coverage': dict(Counter(c['status'] for c in report['coverage'])),
        'fact_examples': len(examples), 'method_semantics': 'partial_source_linked',
        'scope_catalog_coverage': 'partial_selected_document', 'real_expert_approvals': 0, 'real_published_records': 0,
        'checks': {'old_rows_retained': True, 'repeat_has_no_database_changes': True,
            'source_unchanged': True, 'all_21_machine_passed': True, 'all_21_review_pending': True,
            'project_names_normalized': True, 'method_text_expands_from_source': True,
            'exact_decimal_and_basis_retained': True, 'component_basis_retained': True,
            'inherited_cell_source_retained': True, 'all_8_formulas_preserved': True,
            'version_and_field_locator_gates_block_publication': True, 'no_reviewer_created': True,
            'no_rule_binding_or_index_created': True}}
    write_json(out / 'result.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True); parser.add_argument('--chunk-run-id', required=True); parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(verify(Config.load(args.config), args.chunk_run_id, args.output))

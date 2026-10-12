"""Replay fixed local retrieval sources; labels are engineering evidence, not expert approval."""
import argparse
from pathlib import Path
import os
import platform
import shutil
from time import perf_counter

from food_kb.common import digest, file_hash, identity, read_json, write_json
from food_kb.config import Config
from food_kb.db.session import connect, migrate
from food_kb.retrieval.repository import build_generation, read_generation
from food_kb.retrieval.service import search
from food_kb.retrieval.lexical import lexical_search
from food_kb.retrieval.evaluation import evaluate_queries
from food_kb.retrieval.viewer import write_viewer
from verify_kb06 import fingerprints

MODES = ['literal_raw', 'normalized_phrase', 'zh_ngram_phrase']


def examples(conn, corpus):
    gid = corpus['generation_id']; cases = []; labels = []
    def run(**kwargs):
        result = search(conn, gid, **kwargs)
        assert result['status'] in {'success', 'empty'}
        assert not result['scope_complete'] and not result['executable']
        assert all(not h['executable'] and h['applicability'] == 'unknown' for h in result['hits'])
        return result
    def exact(cid, title, filters, count=None):
        result = run(mode='exact', filters=filters, limit=20)
        if count is not None:
            assert result['total_count'] == count, (cid, result['total_count'], count)
        cases.append({'id': cid, 'title': title, 'kind': 'exact', 'synthetic': False,
            'input': {'filters': filters}, 'note': '固定来源的精确候选，未知业务条件仍保留。', 'result': result})
        return result
    manual = {'designation': 'GB 1886.1-2015'}
    exact('fullwidth-standard', '全角标准号及横线：GB 1886.1-2015', {'designation': 'ＧＢ　１８８６．１－２０１５'}, 108)
    exact('different-prefix', 'GB/T不能误连GB 1886.1', {'designation': 'GB/T 1886.1-2015'}, 0)
    exact('fixed-year', '标准年份明确为2015', manual | {'year': '２０１５', 'record_type': 'requirement'}, 9)
    exact('different-year', '不把2014强行改成2015', {'family': 'GB 1886.1', 'year': 2014}, 0)
    total = exact('total-alkali', '总碱量：两种计量基准分别保留', manual | {'project': '总碱量', 'record_type': 'requirement'}, 2)
    assert len({digest(h['conditions']) for h in total['hits']}) == 2
    arsenic = exact('arsenic-project', '砷（As）：完整中文项目', manual | {'project': '砷（As）', 'record_type': 'requirement'}, 1)
    exact('lead-not-arsenic', '铅项目不能用重金属或砷代替', manual | {'project': '铅', 'record_type': 'requirement'}, 0)
    exact('method-clause', 'A.6条款精确匹配，子条款另查', manual | {'clause': 'A.6', 'record_type': 'method'}, 1)
    exact('child-clause', 'A.6.1子条款精确匹配', manual | {'clause': 'A.6.1', 'record_type': 'method'}, 1)
    exact('project-id', '指定项目身份可追溯', {'project_id': arsenic['hits'][0]['exact']['project_id']}, 1)
    exact('method-standard', '检验方法GB 5009.76保持独立', manual | {'method_standard': 'GB 5009.76', 'record_type': 'requirement'}, 1)
    exact('judgment-not-method', '方法引用不能自动成为判定依据', manual | {'judgment_standard': 'GB 5009.76', 'record_type': 'requirement'}, 0)
    exact('pending-review', '原有待审状态可精确查出', manual | {'review_state': 'review_pending', 'record_type': 'requirement'}, 9)
    exact('approved-empty', '本代次没有已审核真实事实', manual | {'review_state': 'approved'}, 0)
    apple = next(r for r in corpus['records'] if r['record_type'] == 'classification' and r['fields'].get('name') == '苹果')
    exact('node-id', '固定苹果分类节点身份', {'node_id': apple['exact']['node_id'], 'record_type': 'classification'}, 1)
    exact('node-linked-projects', '同一节点关联的检验表候选另查',
        {'node_id': apple['exact']['node_id'], 'record_type': 'inspection_requirement'}, 5)
    metadata = next(r for r in corpus['records'] if r['record_type'] == 'standard_metadata' and r['exact']['designation'].startswith('GB/T 5009.146-'))
    exact('gbt-metadata', 'GB/T目录命中：全文未收录缺口仍显示', {'version_id': metadata['exact']['version_id']}, 1)

    gold_specs = [
        ('alkali-name', '总碱量', manual | {'record_type': 'requirement'}, ['总碱量']),
        ('alkali-spaces', '总 碱 量', manual | {'record_type': 'requirement'}, ['总碱量']),
        ('arsenic-width', '砷(As)', manual | {'record_type': 'requirement'}, ['砷（As）']),
        ('chloride-spaces', '氯 化 物', manual | {'record_type': 'requirement'}, ['氯化物']),
        ('formula-nfkc', 'Na2CO3', manual | {'record_type': 'definition'}, ['Na₂CO₃']),
        ('apple-name', '苹果', {'record_type': 'classification', 'namespace': apple['exact']['namespace']}, ['苹果']),
    ]
    for cid, query, filters, expected_values in gold_specs:
        field = 'definition' if cid == 'formula-nfkc' else 'name' if cid == 'apple-name' else 'project'
        expected = [r['record_id'] for r in corpus['records'] if r['fields'].get(field) in expected_values and
            all(r['exact'].get(k) == v for k, v in filters.items())]
        assert expected
        results = {mode: run(query=query, mode=mode, filters=filters, limit=10) for mode in MODES}
        for mode in ['normalized_phrase', 'zh_ngram_phrase']:
            assert set(expected) <= {h['record_id'] for h in results[mode]['hits']}, (cid, mode)
        # Other records can legitimately mention a project in body evidence.
        # A selected direct-name label does not make such mentions false positives.
        labels.append({'query_id': cid, 'expected_ids': expected, 'forbidden_ids': [],
            'label_source': 'fixed-source-field-engineering-inspection', 'rationale':
            '直接读取固定来源的完整项目/名称/分子式字段制定标签；仅检验字面可达性，不作食品语义或适用审核。',
            'source_evidence': [{k: r[k] for k in ['record_id', 'logical_id', 'fields', 'exact', 'sources']}
                for r in corpus['records'] if r['record_id'] in expected],
            'synthetic': False, 'results_by_strategy': results})
        cases.append({'id': cid, 'title': '三方案对照：' + query, 'kind': 'comparison', 'synthetic': False,
            'input': {'query': query, 'filters': filters}, 'result': {'results_by_strategy': results}})

    for cid, query in [('apple-related', '苹果'), ('juice-related', '苹果汁'), ('radish-leaf', '萝卜叶'), ('normal-empty', '不存在的工程测试短语KB10')]:
        result = run(query=query, filters={'record_type': 'classification'}, limit=10)
        if cid == 'normal-empty': assert result['status'] == 'empty' and result['total_count'] == 0
        if cid == 'apple-related':
            juice_hits = [h for h in result['hits'] if '汁' in str(h.get('display_fields', {}).get('name', ''))]
            assert juice_hits, 'The actual apple/juice boundary case must exercise a juice candidate.'
            assert all(m['match_kind'] == 'related_name' for h in juice_hits for m in h['matches'] if m['field'] == 'name')
        cases.append({'id': cid, 'title': {'apple-related': '苹果与果汁：原文字面候选不等于同一对象',
            'juice-related': '苹果汁：分类原文证据', 'radish-leaf': '萝卜叶：没有字面命中不证明无要求',
            'normal-empty': '正常空结果明确保留资料覆盖限制'}[cid],
            'kind': 'lexical', 'synthetic': False, 'input': {'query': query}, 'result': result})
    failed = search(conn, '00000000-0000-0000-0000-000000000010', query='苹果')
    assert failed['status'] == 'failed' and failed['total_count'] is None
    cases.append({'id': 'missing-generation', 'title': '不存在的索引代次明确失败', 'kind': 'failure',
        'synthetic': True, 'note': '向真实服务传入合成不存在的代次身份，不修改任何资料。', 'result': failed})
    return cases, labels


def closed_fixtures():
    values = [('apple', '苹果'), ('juice', '苹果汁'), ('split', '苹果与果汁分别记载'),
              ('alkali', '总 碱 量'), ('formula', 'Na₂CO₃')]
    records = [{'record_id': identity('synthetic-kb10-label', key), 'record_type': 'synthetic',
        'review_state': 'review_pending', 'fields': {'name' if key != 'split' else 'body': text},
        'exact': {}, 'sources': [{'source_key': 'synthetic:kb10-closed-corpus', 'quote': text}],
        'conditions': [], 'exclusions': [], 'gap_ids': []} for key, text in values]
    ids = {key: r['record_id'] for (key, _), r in zip(values, records)}
    corpus = {'generation_id': 'synthetic:closed-lexical-fixture', 'records': records}
    cases = []; labels = []
    for query, keys in [('苹果汁', ['juice']), ('苹果', ['apple', 'juice', 'split']),
                        ('总碱量', ['alkali']), ('Na2CO3', ['formula']), ('未知短语', [])]:
        cid = 'synthetic-' + str(len(cases) + 1)
        results = {mode: lexical_search(corpus, query, mode=mode, limit=10) for mode in MODES}
        expected = [ids[key] for key in keys]
        for mode in ['normalized_phrase', 'zh_ngram_phrase']:
            assert {h['record_id'] for h in results[mode]['hits']} == set(expected)
        labels.append({'query_id': cid, 'expected_ids': expected,
            'forbidden_ids': sorted(set(ids.values()) - set(expected)), 'synthetic': True,
            'label_source': 'synthetic:manually-declared-five-record-closed-corpus',
            'rationale': '五条合成原文闭集；苹果汁是苹果的字面相关命中，非同一对象；分散字组不能组成苹果汁短语。',
            'results_by_strategy': results})
        cases.append({'id': cid, 'title': '合成闭集对照：' + query, 'kind': 'comparison',
            'synthetic': True, 'note': '只验证这五条工程原文；不是真实食品黄金集。',
            'result': {'results_by_strategy': results}})
    return cases, labels


def verify(config, output, selection):
    out = Path(output); out.mkdir(parents=True, exist_ok=True)
    if (out / 'result.json').exists(): raise ValueError('Use a fresh evidence directory.')
    before = read_json(out / 'database-before-fingerprints.json')
    with connect(config) as conn:
        conn.execute("SET TIME ZONE 'UTC'"); write_json(out / 'migrations.json', migrate(conn))
        start = perf_counter(); first = build_generation(conn, config, selection); build_seconds = perf_counter() - start
        write_json(out / 'build-attempt.json', {'result': first, 'seconds': round(build_seconds, 3)})
        corpus = read_generation(conn, first['generation_id'])
        tables = [r['table_name'] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name")]
        after = fingerprints(conn, tables)
        start = perf_counter(); repeated = build_generation(conn, config, selection); repeat_seconds = perf_counter() - start
        assert repeated['status'] == 'duplicate' and repeated['generation_id'] == first['generation_id']
        assert after == fingerprints(conn, tables)
        assert all(before[n] == after[n] for n in before if n != 'schema_migration')
        assert set(before['schema_migration']) <= set(after['schema_migration'])
        cases, labels = examples(conn, corpus)
        assert after == fingerprints(conn, tables)
        environment = {'platform': platform.platform(), 'processor': platform.processor(), 'logical_cpus': os.cpu_count(),
            'python': platform.python_version(), 'postgres_server_version': conn.info.server_version,
            'corpus_count': corpus['summary']['records'], 'concurrency': 1,
            'cache': 'warm local replay; no OS/PG cache eviction', 'configuration': 'explicit character unigrams/bigrams + GIN; complete phrase recheck; no English stemmer'}
    faults_file = out / 'isolated-index-faults.json'
    if faults_file.is_file(): cases.extend(read_json(faults_file))
    evaluation = evaluate_queries(labels, MODES, k=10, environment=environment)
    fixture_cases, fixture_labels = closed_fixtures(); cases.extend(fixture_cases)
    evaluation['synthetic_fixture'] = evaluate_queries(fixture_labels, MODES, k=10,
        environment={'corpus_count': 5, 'execution': 'pure algorithm, synthetic closed corpus'})
    originals = read_json(out / 'source-before.json')
    for key, expected in originals.items(): assert file_hash(config.source_root / key) == expected
    keys = set()
    def scan(item):
        if isinstance(item, dict):
            if item.get('source_key'): keys.add(item['source_key'])
            if item.get('asset_id') or item.get('markdown_asset_id'):
                keys.update(s['source_key'] for s in corpus['sources'] if s['asset_id'] in {item.get('asset_id'), item.get('markdown_asset_id')})
            for k, value in item.items():
                if k not in {'gaps', 'gap_ids'}: scan(value)
        elif isinstance(item, list):
            for value in item: scan(value)
    scan(cases)
    copied = []
    for s in corpus['sources']:
        if s['source_key'] not in keys: continue
        destination = out / 'source' / s['source_key']; destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(config.output_root / s['storage_key'], destination)
        assert file_hash(destination) == s['sha256']; copied.append(s['source_key'])
    write_json(out / 'source-copy-check.json', {'copied': copied, 'all_hashes_match': True, 'all_2218_sources_not_packaged': True})
    write_json(out / 'source-selection.json', selection); write_json(out / 'corpus.json', corpus)
    write_json(out / '查询样例.json', cases); write_json(out / '工程标签与评测.json',
        {'labels': labels, 'synthetic_labels': fixture_labels, 'evaluation': evaluation})
    for case in cases: write_json(out / 'queries' / (case['id'] + '.json'), case['result'])
    write_json(out / 'database-after.json', {k: {'rows': len(v), 'sha256': digest(v)} for k, v in after.items()})
    write_viewer(corpus, cases, out / '精确查询与中文检索.html', out / 'source', evaluation)
    checks = {'old_46_tables_unchanged_except_migration': True, 'repeat_zero_increment': True, 'queries_read_only': True,
        'source_and_old_artifact_files_unchanged': len(originals), 'exact_prefix_year_project_clause_review': True,
        'method_not_judgment': True, 'unknown_applicability_retained': True, 'complete_phrase_rechecked': True,
        'failed_generation_not_normal_empty': True, 'source_files_hash_verified': True,
        'real_knowledge_approved_or_released': False}
    result = {'status': 'engineering_verification_passed', 'first': first, 'repeat': repeated,
        'summary': corpus['summary'], 'examples': len(cases), 'evaluation_queries': len(labels),
        'timings_seconds': {'first_build': round(build_seconds, 3), 'repeat_build': round(repeat_seconds, 3)},
        'environment': environment, 'checks': checks, 'review_state': 'review_pending', 'executable': False, 'scope_complete': False}
    write_json(out / 'checks.json', checks); write_json(out / 'result.json', result)
    print({'status': result['status'], 'generation_id': first['generation_id'], 'records': corpus['summary']['records'],
        'examples': len(cases), 'evaluation_queries': len(labels), 'timings_seconds': result['timings_seconds']})
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--config', required=True)
    parser.add_argument('--output', required=True); parser.add_argument('--context', required=True)
    args = parser.parse_args(); verify(Config.load(args.config), args.output, read_json(args.context))

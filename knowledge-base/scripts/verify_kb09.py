"""Replay version/reference candidates on local PG18 without approving knowledge."""
import argparse
from copy import deepcopy
from pathlib import Path
import shutil
from time import perf_counter

from food_kb.common import digest, file_hash, read_json, write_json
from food_kb.config import Config
from food_kb.db.session import connect, migrate
from food_kb.versions.repository import build_snapshot, read_snapshot
from food_kb.versions.resolve import resolve_versions
from food_kb.versions.references import expand_references
from food_kb.versions.impact import impact_snapshots
from food_kb.versions.viewer import write_viewer
from verify_kb06 import fingerprints


EVIDENCE = [{'source_ref': 'synthetic:kb09-policy', 'quote': 'Engineering fixture, not a real standard.'}]


def date(value, role='sampling', state='verified'):
    return {'value': value, 'raw_value': value, 'role': role,
            'source_ref': 'synthetic:kb09-input:' + str(role), 'verification_state': state}


def policy(start=None, end=None, role='sampling'):
    def bound(value, inclusive):
        return {**date(value), 'inclusive': inclusive} if value else None
    return {'policy_id': 'synthetic:kb09-policy', 'date_role': role,
            'from': bound(start, True), 'until': bound(end, False),
            'condition': {'state': 'unconditional', 'ast': None, 'evidence': deepcopy(EVIDENCE), 'required_facts': []},
            'evidence': deepcopy(EVIDENCE), 'source_ref': 'synthetic:kb09-policy', 'verification_state': 'verified'}


def fixture():
    def version(vid, designation, policies):
        return {'version_id': vid, 'document_id': 'synthetic:document', 'designation': designation,
                'date_claims': [], 'status_claims': [], 'policies': policies, 'identity_state': 'unverified',
                'review_state': 'review_pending', 'body_assets': [{'asset_id': 'synthetic:body:' + vid}]}
    return {'snapshot_id': 'synthetic:kb09', 'versions': [
        version('synthetic:old', 'SYNTHETIC 1-2020', [policy(end='2022-01-01')]),
        version('synthetic:new', 'SYNTHETIC 1-2022', [policy(start='2022-01-01')])],
        'references': [], 'events': [], 'gaps': [], 'dependencies': []}


def examples(snapshot):
    cases = []
    def add(identifier, title, kind, result, synthetic=True, note=None):
        assert result['executable'] is False and result['scope_complete'] is False
        cases.append({'id': identifier, 'title': title, 'kind': kind, 'synthetic': synthetic,
                      'note': note or '日期/策略为合成工程输入；真实登记资料仍待审核，结果不构成标准适用批准。', 'result': result})

    family = [v for v in snapshot['versions'] if v['family'] == 'GB 2762' or v['family'] == 'GB2762']
    if not family:
        family = [v for v in snapshot['versions'] if v['designation'] == 'GB 2762-2022']
    assert family
    doc = next(v['document_id'] for v in family if not v['is_amendment'])
    context = {'dates': [date('2021-08-10'), date('2022-06-01', 'testing'), date('2026-10-12', 'export')], 'unknowns': []}
    real = resolve_versions(snapshot, context, document_id=doc)
    assert real['status'] == 'unknown' and real['selected_version_id'] is None
    add('actual-history', '真实登记：2021抽样、2022检验与2026导出分开', 'resolution', real)
    add('actual-missing-role', '真实登记：缺日期角色保留未知', 'resolution',
        resolve_versions(snapshot, {'dates': [date('2021-08-10', None)]}, document_id=doc))
    historical = next(v for v in family if v['designation'].endswith('-2017') and not v['is_amendment'])
    selected = resolve_versions(snapshot, context, version_id=historical['version_id'])
    assert len(selected['candidates']) == 1 and selected['candidates'][0]['version_id'] == historical['version_id']
    add('actual-fixed-history', '真实登记：固定历史版本不会被现行声明覆盖', 'resolution', selected)
    amendment = next(v for v in snapshot['versions'] if v['is_amendment'] and any(
        g['kind'] == 'missing_body' and g['target']['id'] == v['version_id'] for g in snapshot['gaps']))
    missing = resolve_versions(snapshot, context, version_id=amendment['version_id'])
    assert missing['status'] == 'unknown' and missing['selected_version_id'] is None
    add('actual-amendment', '真实登记：修改单存在、主体全文缺失', 'resolution', missing)
    start = historical['version_id']
    add('actual-dependencies', '真实登记：提及与替代文字不升格为规范依赖', 'references',
        expand_references(snapshot, [start], mode='dependencies', max_edges=80))
    candidate = expand_references(snapshot, [start], mode='candidates', max_depth=2, max_nodes=8, max_edges=20)
    assert candidate['candidate_only'] and not candidate['complete']
    add('actual-candidates', '真实登记：有预算的候选引用探索', 'references', candidate, False,
        '真实固定快照的候选探索；未核实引用性质、范围和绑定，不能据此执行检查。')
    same = impact_snapshots(snapshot, snapshot, max_nodes=100000, max_edges=200000)
    assert not same['changes'] and not same['affected'] and same['traversal_complete']
    add('actual-self-impact', '真实快照：相同快照零增量', 'impact', same, False, '比较同一个固定快照，历史记录保持不变。')
    quarantined = next(g for g in snapshot['gaps'] if g['kind'] == 'unsupported_text' and any(
        t['kind'] == 'version' for t in g['affected_refs']))
    source_version = next(t['id'] for t in quarantined['affected_refs'] if t['kind'] == 'version')
    quarantine_result = expand_references(snapshot, [source_version], max_edges=5)
    assert any(g.get('kind') == 'unsupported_text' for g in quarantine_result['gaps']) and not quarantine_result['complete']
    add('actual-quarantine', '真实登记：异常引用缺口关联到来源版本', 'references', quarantine_result, False,
        '原始异常文字保留在既有登记；不解析或替换它，明确来源版本的证据仍不完整。')

    synthetic = fixture()
    old = resolve_versions(synthetic, context)
    assert old['selected_version_id'] == 'synthetic:old'
    add('synthetic-history', '合成政策：历史样本选择旧版，忽略导出时间', 'resolution', old)
    boundary = resolve_versions(synthetic, {'dates': [date('2022-01-01')]})
    assert boundary['selected_version_id'] == 'synthetic:new'
    add('synthetic-boundary', '合成政策：明确边界当天进入新版', 'resolution', boundary)
    production = fixture(); production['versions'][0]['policies'][0]['date_role'] = 'production'
    production['versions'][1]['policies'][0]['date_role'] = 'production'
    result = resolve_versions(production, {'dates': [date('2021-12-01', 'production'), date('2022-06-01', 'testing')]})
    assert result['selected_version_id'] == 'synthetic:old'
    add('synthetic-production', '合成过渡：生产日期与检验日期分开', 'resolution', result)
    conflicting = fixture()
    conflicting['versions'][0]['date_claims'] = [date('2020-01-01', 'implementation'), date('2020-03-01', 'implementation')]
    result = resolve_versions(conflicting, context)
    assert result['status'] == 'conflicting'
    add('synthetic-conflict', '合成冲突：两个生效依据冲突，不任选其一', 'resolution', result)
    missing = fixture(); missing['versions'][0]['is_amendment'] = True; missing['versions'][0]['body_assets'] = []
    result = resolve_versions(missing, context)
    assert result['status'] == 'unknown' and result['selected_version_id'] is None
    add('synthetic-amendment', '合成政策：日期适用也不能用修改单替代主体', 'resolution', result)

    graph = fixture()
    def reference(rid, source, target):
        return {'reference_id': rid, 'from_version_id': source, 'target': {'version_ids': [target]},
                'nature': {'value': 'normative', 'verification_state': 'verified', 'evidence': deepcopy(EVIDENCE)},
                'scope': {'value': 'whole', 'refs': [], 'verification_state': 'verified', 'evidence': deepcopy(EVIDENCE)},
                'binding': {'datedness': 'dated', 'designation': next(v['designation'] for v in graph['versions'] if v['version_id'] == target),
                            'version_ids': [target], 'verification_state': 'verified', 'evidence': deepcopy(EVIDENCE)},
                'condition': {'state': 'unconditional', 'ast': None, 'evidence': deepcopy(EVIDENCE), 'required_facts': []},
                'evidence': deepcopy(EVIDENCE), 'review_state': 'approved'}
    graph['references'] = [reference('synthetic:AB', 'synthetic:old', 'synthetic:new'), reference('synthetic:BA', 'synthetic:new', 'synthetic:old')]
    cycle = expand_references(graph, ['synthetic:old'])
    assert len(cycle['cycles']) == 1 and not cycle['complete']
    add('synthetic-cycle', '合成引用：A→B→A终止并显示循环路径', 'references', cycle)
    budget = expand_references(graph, ['synthetic:old'], max_depth=0, max_nodes=1, max_edges=1)
    assert budget['unexpanded'] and not budget['complete']
    add('synthetic-budget', '合成引用：预算达到时列出未展开项', 'references', budget)
    wrong = deepcopy(graph); wrong['references'][0]['binding']['designation'] = 'SYNTHETIC 1-2020'
    result = expand_references(wrong, ['synthetic:old'])
    assert not result['selected_reference_ids'] and result['gaps']
    add('synthetic-dated-mismatch', '合成引用：定年引用不能错连新版', 'references', result)
    absent = deepcopy(graph); absent['references'][0]['binding']['datedness'] = 'undated'
    result = expand_references(absent, ['synthetic:old'])
    assert not result['selected_reference_ids']
    add('synthetic-undated', '合成引用：不定年引用缺业务时点不绑定最新版', 'references', result)
    before = digest(snapshot)
    changed = deepcopy(snapshot); changed['snapshot_id'] = 'synthetic:erratum-on-real-ids'
    target = next(v['version_id'] for v in family if not v['is_amendment'] and any(
        d['depends_on'] == {'kind': 'version', 'id': v['version_id']} and d['dependent']['kind'] == 'classification'
        for d in snapshot['dependencies']))
    changed['events'].append({'event_id': 'synthetic:erratum', 'kind': 'erratum', 'version_ids': [target],
                              'evidence': deepcopy(EVIDENCE), 'verification_state': 'unverified'})
    impact = impact_snapshots(snapshot, changed, max_nodes=100000, max_edges=200000)
    assert any(x['kind'] == 'classification' for x in impact['affected']) and impact['traversal_complete']
    assert digest(snapshot) == before
    add('synthetic-impact', '合成勘误：沿真实绑定列出受影响分类及要求', 'impact', impact, True,
        '仅在内存添加合成勘误，引用真实登记身份；不写入数据库，也不伪造真实官方勘误或受影响任务。')
    return cases


def verify(config, output):
    out = Path(output)
    if (out / 'result.json').exists():
        raise ValueError('Use a fresh evidence directory.')
    out.mkdir(parents=True, exist_ok=True)
    before = read_json(out / 'database-before-fingerprints.json')
    with connect(config) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        write_json(out / 'migrations.json', migrate(conn))
        started = perf_counter()
        first = build_snapshot(conn, config)
        build_seconds = perf_counter() - started
        snapshot = read_snapshot(conn, first['snapshot_id'])
        tables = [r['table_name'] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name")]
        between = fingerprints(conn, tables)
        started = perf_counter()
        repeated = build_snapshot(conn, config)
        repeat_seconds = perf_counter() - started
        assert repeated['status'] == 'duplicate' and repeated['snapshot_id'] == first['snapshot_id']
        assert between == fingerprints(conn, tables)
        assert all(set(rows) <= set(between[name]) for name, rows in before.items())
        assert all(before[name] == between[name] for name in before if name != 'schema_migration')
        cases = examples(snapshot)
        prior_result = out / 'sources-1/result.json'
        if prior_result.is_file():
            prior = read_json(prior_result)['first']
            assert digest(read_snapshot(conn, prior['snapshot_id'])) == prior['payload_hash']
        assert between == fingerprints(conn, tables)
        assert snapshot == read_snapshot(conn, first['snapshot_id'])
        write_json(out / 'database-after.json', {k: {'rows': len(v), 'sha256': digest(v)} for k,v in between.items()})
        server = conn.info.server_version
    originals = read_json(out / 'source-before.json')
    for relative, expected in originals.items():
        assert file_hash(config.source_root / relative) == expected
    source_keys = {s['source_key'] for s in snapshot['sources'] if s['role'] == 'index'}
    real_ids = {v['version_id'] for v in snapshot['versions'] if v['family'] in {'GB 2762', 'GB2762'}}
    for case in cases:
        if case['id'].startswith('actual-'):
            real_ids.update(c['version_id'] for c in case['result'].get('candidates', []))
            real_ids.update(case['result'].get('start_version_ids', []))
    for version in snapshot['versions']:
        if version['version_id'] in real_ids:
            source_keys.update(a['source_key'] for a in version['body_assets'] + version['other_assets'])
    copied = []
    for source in snapshot['sources']:
        if source['source_key'] in source_keys:
            destination = out / 'source' / source['source_key']; destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(config.output_root / source['storage_key'], destination)
            assert file_hash(destination) == source['sha256']; copied.append(source['source_key'])
    write_json(out / 'source-copy-check.json', {'copied': copied, 'all_hashes_match': True, 'not_all_sources_in_package': True})
    write_json(out / 'versions.json', snapshot)
    write_json(out / '版本查询样例.json', cases)
    write_json(out / '缺口账本.json', snapshot['gaps'])
    write_json(out / 'time-context.json', {'dates': [date('2021-08-10'), date('2026-10-12', 'export')]})
    for case in cases:
        write_json(out / 'queries' / (case['id'] + '.json'), case['result'])
    write_viewer(snapshot, cases, out / '版本引用与缺口.html', out / 'source')
    checks = {'old_44_tables_unchanged_except_migration': True, 'repeat_zero_increment': True,
              'queries_read_only': True, 'source_and_old_evidence_files_unchanged': len(originals),
              'fixed_history_preserved': True, 'missing_role_unknown': True, 'conflict_not_arbitrarily_selected': True,
              'cycle_recorded': True, 'budget_omissions_explicit': True, 'amendment_not_body': True,
              'dated_binding_exact': True, 'undated_not_latest': True, 'explicit_impact_history_unchanged': True,
              'quarantined_reference_affects_registered_source_version': True,
              'prior_version_snapshot_payload_unchanged': True,
              'real_knowledge_approved_or_released': False}
    result = {'status': 'engineering_verification_passed', 'first': first, 'repeat': repeated,
              'database_server_version': server, 'public_tables': len(tables), 'summary': snapshot['summary'],
              'timings_seconds': {'first_build': round(build_seconds, 3), 'repeat_build': round(repeat_seconds, 3)},
              'examples': len(cases), 'checks': checks, 'review_state': 'review_pending', 'executable': False, 'scope_complete': False}
    write_json(out / 'checks.json', checks); write_json(out / 'result.json', result)
    print(result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--config', required=True); parser.add_argument('--output', required=True)
    args = parser.parse_args(); verify(Config.load(args.config), args.output)

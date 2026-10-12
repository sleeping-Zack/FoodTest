"""Run the user-selected manual sample, save actual chunks and source comparisons."""
import argparse
from collections import Counter
from pathlib import Path
import shutil

from psycopg import sql

from food_kb.common import digest, file_hash, write_json
from food_kb.config import Config
from food_kb.db.session import connect, migrate
from food_kb.chunking.context import expand_context
from food_kb.chunking.manual import load_manual
from food_kb.chunking.service import chunk_manual, read_run
from food_kb.chunking.viewer import write_viewer


def fingerprints(conn, tables):
    result = {}
    for name in tables:
        # Existing lossless legacy JSON can contain escaped NUL; do not coerce it to jsonb.
        rows = conn.execute(sql.SQL('SELECT row_to_json(t)::text AS row FROM {} t').format(sql.Identifier(name))).fetchall()
        result[name] = sorted(digest(row['row']) for row in rows)
    return result


def verify(config, source, out):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    if (out / 'result.json').exists():
        raise ValueError('Use a fresh evidence directory.')
    original = load_manual(config.source(source))
    hashes = original['file_hashes']
    with connect(config) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        tables = [r['table_name'] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name")]
        before = fingerprints(conn, tables)
        write_json(out / 'database-before.json', {k: {'rows': len(v), 'sha256': digest(v)} for k, v in before.items()})
        write_json(out / 'migrations.json', migrate(conn))
        first = chunk_manual(conn, config, source)
        bundle = read_run(conn, config, first['run_id'])
        between = fingerprints(conn, tables + ['chunk_run'])
        repeated = chunk_manual(conn, config, source)
        after = fingerprints(conn, tables + ['chunk_run'])
        assert between == after and repeated['status'] == 'duplicate'
        assert first['run_id'] == repeated['run_id']
        assert all(set(values) <= set(after[name]) for name, values in before.items())
        write_json(out / 'database-after.json', {k: {'rows': len(v), 'sha256': digest(v)} for k, v in after.items()})
        version = conn.info.server_version
    chunks = {c['anchor']: c for c in bundle['chunks']}
    examples = []
    for anchor, pages in [('A.5.5', [5, 6]), ('A.6.1.2.5', [6, 7]), ('A.8.3.1', [9, 10])]:
        chunk = chunks[anchor]
        assert chunk['source']['pdf_pages'] == pages
        examples.append({'anchor': anchor, 'chunk_id': chunk['chunk_id'], 'source': chunk['source'],
                         'raw_markdown': chunk['raw_markdown']})
    iron = chunks['table-2:row:4']
    assert all(s in iron['retrieval_text'] for s in ['以干基计', '≤', '%', '0.003 5', 'A.7'])
    iron_context = expand_context(bundle, iron['chunk_id'])
    assert iron_context['complete'] and any(b['key'] == 'A.7.5' for b in iron_context['blocks'])
    assert any(b['key'] == '1' for b in iron_context['blocks'])
    row = chunks['table-1:row:2']
    assert row['source']['pdf_pages'] == [3] and row['source']['line_start'] == row['source']['line_end'] == 77
    assert row['table']['cells'][2]['inherited'] and row['table']['cells'][2]['text_raw'] == ''
    assert row['table']['cells'][2]['source_cell']['row'] == 2
    rows = [c for c in bundle['chunks'] if c['kind'] == 'table_row']
    assert len(rows) == 9 and len({c['chunk_id'] for c in rows}) == 9
    assert sum(c['raw_markdown'].count('\\tag{') for c in bundle['chunks']) == 8
    assert all(c['executable'] is False for c in bundle['chunks'])
    assert bundle['validation']['passed']
    assert all(file_hash(original['directory'] / name) == sha for name, sha in hashes.items())
    assert file_hash(config.source(original['source_metadata']['source'])) == original['mapping']['source_sha256']
    write_json(out / 'chunks.json', bundle)
    write_json(out / 'context-links.json', bundle['links'])
    write_json(out / 'quality-report.json', bundle['validation'])
    write_json(out / 'cross-page-examples.json', examples)
    write_json(out / 'iron-row-context.json', iron_context)
    small = expand_context(bundle, iron['chunk_id'], max_blocks=1, max_chars=1000)
    assert not small['complete'] and small['omitted']
    write_json(out / 'budget-example.json', small)
    preserved_source = out / 'source'
    for name, asset in bundle['source']['preserved_files'].items():
        destination = preserved_source / name; destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(config.output_root / asset['storage_key'], destination)
        assert file_hash(destination) == asset['asset_id']
    md = ['# GB1886.1-2015 切分结果', '', '> 全部为未审核候选；标题与范围只用于恢复上下文。', '']
    for chunk in bundle['chunks']:
        md.extend([f"## {chunk['anchor']} · {chunk['title']}", '',
            f"- 类型：{chunk['kind']}；检索：{chunk['retrieval_enabled']}；片段 ID：{chunk['chunk_id']}",
            f"- PDF 实际页：{chunk['source']['pdf_pages']}；Markdown 行：{chunk['source']['line_ranges']}", '',
            chunk['raw_markdown'].replace('](assets/', '](source/assets/'), '', '### 携带上下文的检索文本', '',
            chunk['retrieval_text'].replace('](assets/', '](source/assets/'), '', '---', ''])
    (out / '切分结果.md').write_text('\n'.join(md), encoding='utf8')
    write_viewer(bundle, out / '切分结果.html', preserved_source)
    result = {'status': 'engineering_passed', 'scope': 'User-selected GB1886.1-2015 manual Markdown, 11 PDF pages. Cross-page table/footnote cases are synthetic tests, not present in this real PDF.',
              'database_server_version': version, 'first': first, 'repeat': repeated,
              'total_chunks': len(bundle['chunks']), 'retrieval_chunks': sum(c['retrieval_enabled'] for c in bundle['chunks']),
              'kinds': dict(Counter(c['kind'] for c in bundle['chunks'])), 'context_links': len(bundle['links']),
              'formula_count': 8, 'cross_page_clauses': 3,
              'source_content_lines': len(bundle['coverage']['expected_lines']),
              'checks': {'all_original_rows_retained': True, 'repeat_has_no_database_changes': True,
                         'source_package_and_original_unchanged': True, 'all_chunks_non_executable': True,
                         'exact_table_row_locations': True, 'span_provenance_retained': True,
                         'whole_cross_page_clauses': True, 'scope_and_method_expansion': True,
                         'budget_truncation_explicit': True, 'source_coverage_complete': True}}
    write_json(out / 'result.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True); parser.add_argument('--source', required=True); parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(verify(Config.load(args.config), args.source, args.output))

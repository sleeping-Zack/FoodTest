"""Register immutable manual source parses and repeatable structural chunk runs."""
import hashlib
import os
from pathlib import Path
import tempfile

from ..common import KBError, canonical, digest, file_hash, identity, read_json
from ..db.repository import register_parse
from ..db.session import insert
from ..storage.assets import preserve
from .manual import load_manual
from .structural import STRATEGY_VERSION, build_bundle


MANUAL_VERSION = 'manual-markdown-1'


def _path(config, key):
    path = (config.output_root / key).resolve()
    if not path.is_relative_to(config.output_root.resolve()):
        raise KBError('PATH_BOUNDARY', 'Chunk artifact is outside the configured output directory.')
    return path


def read_run(conn, config, run_id):
    row = conn.execute('SELECT * FROM chunk_run WHERE run_id=%s', (run_id,)).fetchone()
    if not row:
        raise KBError('NOT_FOUND', 'Chunk run does not exist.')
    path = _path(config, row['report_key'])
    if not path.is_file() or file_hash(path) != row['report_sha256']:
        raise KBError('CHUNK_REPORT_CHANGED', 'Stored chunk report is missing or changed.')
    for asset in row['source_manifest'].values():
        preserved = _path(config, asset['storage_key'])
        if not preserved.is_file() or file_hash(preserved) != asset['asset_id']:
            raise KBError('CHUNK_SOURCE_CHANGED', 'A preserved source file is missing or changed.')
    bundle = read_json(path)
    if digest(bundle) != row['output_hash']:
        raise KBError('CHUNK_REPORT_CHANGED', 'Chunk report fingerprint differs from its registration.')
    return bundle


def compare_runs(conn, config, left_id, right_id):
    left = read_run(conn, config, left_id); right = read_run(conn, config, right_id)
    old = {c['anchor']: c for c in left['chunks']}; new = {c['anchor']: c for c in right['chunks']}
    shared = old.keys() & new.keys()
    return {'left_run_id': left_id, 'right_run_id': right_id,
            'same_parse': left['parse_id'] == right['parse_id'],
            'strategies': [left['strategy_version'], right['strategy_version']],
            'added': sorted(new.keys() - old.keys()), 'removed': sorted(old.keys() - new.keys()),
            'content_changed': sorted(k for k in shared if old[k]['content_hash'] != new[k]['content_hash']),
            'identity_changed': sorted(k for k in shared if old[k]['chunk_id'] != new[k]['chunk_id'])}


def _store(config, bundle):
    content = (canonical(bundle) + '\n').encode('utf8')
    sha = hashlib.sha256(content).hexdigest(); key = f'chunk-runs/{bundle["run_id"]}/{sha}.json'
    target = _path(config, key); target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix='.chunk-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if file_hash(target) != sha:
                raise KBError('CHUNK_REPORT_CHANGED', 'Existing chunk artifact differs; no overwrite performed.')
    finally:
        Path(temporary).unlink(missing_ok=True)
    return key, sha


def chunk_manual(conn, config, relative_directory, *, strategy_version=STRATEGY_VERSION):
    manual = load_manual(config.source(relative_directory))
    manifest = {}
    for path in manual['files']:
        # Recheck bytes during preservation, not only when loading the source map.
        expected = manual['file_hashes'][path.relative_to(manual['directory']).as_posix()]
        manifest[path.relative_to(manual['directory']).as_posix()] = preserve(config, path.relative_to(config.source_root), expected)
    parse_config = {'source_files': {name: item['asset_id'] for name, item in manifest.items()},
                    'locator_quality': 'manual_page_and_line', 'business_review_status': 'not_expert_approved'}
    parse_hash = digest(parse_config)
    parse_id = identity('parse', manual['mapping']['source_sha256'], MANUAL_VERSION, parse_hash)
    bundle = build_bundle(manual, parse_id, strategy_version)
    candidate_issues = {'CANDIDATE_CONTEXT_INCOMPLETE', 'FOOTNOTE_MISSING', 'TABLE_HEADER_MISSING', 'DUPLICATE_HEADER_ROW',
                        'FORMULA_UNBALANCED', 'FORMULA_VARIABLES_MISSING', 'COMPARISON_VALUE_MISSING'}
    fatal = [issue for issue in bundle['validation']['issues'] if issue['code'] not in candidate_issues]
    if fatal:
        raise KBError('CHUNK_VALIDATION_FAILED', canonical(fatal))
    bundle['source']['preserved_files'] = manifest
    output_hash = digest(bundle)
    report_key, report_sha = _store(config, bundle)
    with conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(608042006)')
        for asset in manifest.values():
            insert(conn, 'source_asset', asset)
        registered = register_parse(conn, manual['mapping']['source_sha256'], MANUAL_VERSION,
                                    parse_hash, digest({'mapping': manual['mapping'], 'source': manual['source_metadata']}), 'partial')
        assert registered == parse_id
        old = conn.execute('SELECT output_hash FROM chunk_run WHERE run_id=%s', (bundle['run_id'],)).fetchone()
        if old:
            if old['output_hash'] != output_hash:
                raise KBError('CHUNK_OUTPUT_CONFLICT', 'Same parse/strategy produced different output; use a new strategy version.')
            read_run(conn, config, bundle['run_id'])
            return {'status': 'duplicate', 'run_id': bundle['run_id'], 'parse_id': parse_id,
                    'chunks': len(bundle['chunks']), 'report': str(_path(config, report_key))}
        for ordinal, block in enumerate(bundle['blocks']):
            values = {'block_id': block['block_id'], 'parse_id': parse_id, 'structural_key': block['key'],
                      'parent_id': block['parent_id'], 'kind': block['kind'], 'ordinal': ordinal,
                      'text_raw': block['text_raw'], 'text_search': block['text_raw'], 'locator': block['source']}
            if insert(conn, 'content_block', values) is None:
                previous = conn.execute('SELECT * FROM content_block WHERE block_id=%s', (block['block_id'],)).fetchone()
                if any((str(previous[key]) if key in {'block_id','parse_id','parent_id'} and previous[key] is not None else previous[key]) != value
                       for key, value in values.items()):
                    raise KBError('MANUAL_BLOCK_CONFLICT', 'An existing manual source block differs from its immutable text or location.')
        insert(conn, 'chunk_run', {'run_id': bundle['run_id'], 'parse_id': parse_id,
               'strategy_version': strategy_version, 'config_hash': bundle['config_hash'],
               'configuration': bundle['strategy_config'], 'output_hash': output_hash,
               'report_key': report_key, 'report_sha256': report_sha, 'source_manifest': manifest})
        for chunk in bundle['chunks']:
            insert(conn, 'chunk', {'chunk_id': chunk['chunk_id'], 'parse_id': parse_id,
                   'chunk_run_id': bundle['run_id'], 'strategy_version': strategy_version,
                   'content_hash': chunk['content_hash'], 'retrieval_text': chunk['retrieval_text'],
                   'parent_block_id': chunk['parent_block_id'], 'token_count': None, 'structure': chunk})
            for block_id in chunk['block_ids']:
                evidence_id = identity('manual-chunk-evidence', parse_id, block_id)
                quote = chunk['raw_markdown']
                insert(conn, 'evidence_anchor', {'evidence_id': evidence_id,
                       'asset_id': manual['mapping']['source_sha256'], 'parse_id': parse_id, 'block_id': block_id,
                       'locator': chunk['source'], 'quote': quote,
                       'quote_hash': hashlib.sha256(quote.encode('utf8')).hexdigest(), 'locator_quality': 'page_claim'})
                insert(conn, 'chunk_evidence', {'chunk_id': chunk['chunk_id'], 'evidence_id': evidence_id})
        for link in bundle['links']:
            insert(conn, 'chunk_context_link', link)
    return {'status': 'completed' if bundle['validation']['passed'] else 'completed_with_issues', 'run_id': bundle['run_id'], 'parse_id': parse_id,
            'chunks': len(bundle['chunks']), 'report': str(_path(config, report_key))}

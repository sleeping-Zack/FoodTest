"""Append-only retrieval generations and database candidate rows."""
from copy import deepcopy

from psycopg.types.json import Jsonb

from ..common import KBError, digest
from ..db.session import insert
from .sources import PROCESSOR_VERSION, collect_payload


_INDEX_HASH = "encode(sha256(convert_to(jsonb_build_object('exact_terms',exact_terms,'raw_text',raw_text,'normalized_text',normalized_text,'tokens',tokens::text)::text,'UTF8')),'hex')"


def _validate(payload):
    if not isinstance(payload, dict) or payload.get('schema_version') != 'retrieval-corpus-1' or payload.get('review_state') != 'review_pending' or payload.get('scope_complete') is not False or payload.get('executable') is not False:
        raise KBError('RETRIEVAL_GENERATION_INVALID', 'Retrieval generation must retain its candidate-only boundary.')
    for name in ('sources', 'record_manifest'):
        if not isinstance(payload.get(name), list):
            raise KBError('RETRIEVAL_GENERATION_INVALID', 'A fixed source and record manifest is required.')
    if not payload['sources'] or len({s['source_key'] for s in payload['sources']}) != len(payload['sources']):
        raise KBError('RETRIEVAL_GENERATION_INVALID', 'Sources must be nonempty with unique source keys.')
    if len({m[0] for m in payload['record_manifest']}) != len(payload['record_manifest']):
        raise KBError('RETRIEVAL_GENERATION_INVALID', 'A retrieval record identity is repeated.')


def read_header(conn, generation_id):
    row = conn.execute('SELECT * FROM retrieval_generation WHERE generation_id=%s', (generation_id,)).fetchone()
    if row is None:
        raise KBError('RETRIEVAL_GENERATION_MISSING', 'Requested fixed retrieval generation does not exist.')
    header = row['payload']; _validate(header)
    if header['generation_id'] != str(generation_id) or digest(header) != row['payload_hash']:
        raise KBError('RETRIEVAL_GENERATION_CORRUPT', 'Fixed retrieval header fingerprint differs.')
    actual = sorted((r['source_key'], r['asset_id']) for r in conn.execute(
        'SELECT source_key,asset_id FROM retrieval_generation_source WHERE generation_id=%s', (generation_id,)).fetchall())
    if actual != sorted((s['source_key'], s['asset_id']) for s in header['sources']):
        raise KBError('RETRIEVAL_GENERATION_CORRUPT', 'Fixed retrieval source membership differs.')
    indexes = conn.execute('SELECT record_id,payload_hash,index_hash,' + _INDEX_HASH +
        ' AS actual_index_hash FROM retrieval_record WHERE generation_id=%s ORDER BY record_id', (generation_id,)).fetchall()
    actual_manifest = [[str(r['record_id']), r['payload_hash'], r['index_hash']] for r in indexes]
    if actual_manifest != header['record_manifest'] or any(r['index_hash'] != r['actual_index_hash'] for r in indexes):
        raise KBError('RETRIEVAL_GENERATION_CORRUPT', 'Fixed retrieval record or derived index fingerprint differs.')
    return header, row['corpus_hash']


def checked_records(header, rows):
    manifest = {m[0]: m[1] for m in header['record_manifest']}; result = []
    for r in rows:
        rid = str(r['record_id']); item = r['payload']
        if item.get('record_id') != rid or digest(item) != r['payload_hash'] or manifest.get(rid) != r['payload_hash']:
            raise KBError('RETRIEVAL_GENERATION_CORRUPT', 'A retrieval row differs from its frozen record manifest.')
        result.append(item)
    return result


def read_generation(conn, generation_id):
    header, content_hash = read_header(conn, generation_id)
    rows = conn.execute('SELECT record_id,payload,payload_hash FROM retrieval_record WHERE generation_id=%s ORDER BY record_id', (generation_id,)).fetchall()
    corpus = {k: v for k, v in header.items() if k != 'record_manifest'}
    corpus['records'] = checked_records(header, rows)
    if digest(corpus) != content_hash:
        raise KBError('RETRIEVAL_GENERATION_CORRUPT', 'Reconstructed retrieval corpus fingerprint differs.')
    return corpus


def persist_generation(conn, corpus):
    """Persist a composed corpus; the public builder selects registered sources."""
    from .lexical import index_tokens
    from .normalize import normalize_text, normalize_filter_value
    corpus = deepcopy(corpus)
    records = corpus['records'] = sorted(corpus['records'], key=lambda r: r['record_id'])
    derived = []
    for r in records:
        exact = r['exact'] | {'record_id': r['record_id']}
        terms = {k: [normalize_filter_value(k, x) for x in (v if isinstance(v, list) else [v])]
            for k, v in exact.items()}
        fields = [v for value in r['fields'].values() for v in (value if isinstance(value, list) else [value]) if isinstance(v, str)]
        derived.append({'record_id': r['record_id'], 'exact_terms': terms, 'raw_text': '\n'.join(fields),
            'normalized_text': '\n'.join(normalize_text(v) for v in fields), 'tokens': index_tokens(r)})
    # PostgreSQL canonical JSON and tsvector representations are fingerprinted
    # in one bounded call; Python does not emulate PostgreSQL JSON ordering.
    indexes = conn.execute('''SELECT record_id,'''+_INDEX_HASH+''' AS index_hash FROM (
        SELECT d->>'record_id' AS record_id,d->'exact_terms' AS exact_terms,d->>'raw_text' AS raw_text,
          d->>'normalized_text' AS normalized_text,
          array_to_tsvector(ARRAY(SELECT jsonb_array_elements_text(d->'tokens'))) AS tokens
        FROM jsonb_array_elements(%s::jsonb) d) prepared''', (Jsonb(derived),)).fetchall()
    index_hashes = {r['record_id']: r['index_hash'] for r in indexes}
    header = {k: deepcopy(v) for k, v in corpus.items() if k != 'records'}
    header['record_manifest'] = [[r['record_id'], digest(r), index_hashes[r['record_id']]] for r in records]
    _validate(header)
    gid = corpus['generation_id']; content_hash = digest(corpus); header_hash = digest(header)
    with conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(608042010)')
        old = conn.execute('SELECT corpus_hash FROM retrieval_generation WHERE generation_id=%s', (gid,)).fetchone()
        if old:
            if old['corpus_hash'] != content_hash:
                raise KBError('RETRIEVAL_GENERATION_CONFLICT', 'Same source/processor identity changed; append a new processor version.')
            read_generation(conn, gid)
            return {'generation_id': gid, 'status': 'duplicate', 'payload_hash': content_hash, 'summary': corpus['summary']}
        insert(conn, 'retrieval_generation', {'generation_id': gid, 'processor_version': PROCESSOR_VERSION,
            'input_hash': corpus['input_hash'], 'payload_hash': header_hash, 'corpus_hash': content_hash,
            **corpus['source_snapshots'], 'payload': header, 'source_count': len(header['sources']), 'record_count': len(records)})
        for s in corpus['sources']:
            insert(conn, 'retrieval_generation_source', {'generation_id': gid, 'source_key': s['source_key'], 'asset_id': s['asset_id']})
        for r, d in zip(records, derived):
            conn.execute('''INSERT INTO retrieval_record(generation_id,record_id,record_type,payload,payload_hash,index_hash,
                exact_terms,raw_text,normalized_text,tokens) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,array_to_tsvector(%s::text[]))''',
                (gid, r['record_id'], r['record_type'], Jsonb(r), digest(r), index_hashes[r['record_id']],
                 Jsonb(d['exact_terms']), d['raw_text'], d['normalized_text'], d['tokens']))
    return {'generation_id': gid, 'status': 'success', 'payload_hash': content_hash, 'summary': corpus['summary']}


def build_generation(conn, config, source_snapshots):
    return persist_generation(conn, collect_payload(conn, config, source_snapshots))

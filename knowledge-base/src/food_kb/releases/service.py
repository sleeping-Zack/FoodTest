"""Publish a knowledge snapshot without switching indexes or creating bindings."""
from psycopg.types.json import Jsonb

from ..common import KBError, digest, identity
from ..db.session import insert
from ..review.service import publication_check, require_actor


def publish(conn, record_ids, actor, parent_release_id=None, synthetic=False):
    """Pin exact revisions and approval IDs in an append-only knowledge snapshot."""
    if not record_ids or len(record_ids) != len(set(map(str, record_ids))):
        raise KBError('EXPLICIT_RECORDS_REQUIRED', 'A release requires a nonempty list of distinct knowledge revision IDs.')
    record_ids = sorted(map(str, record_ids))
    with conn.transaction():
        require_actor(conn, actor, 'knowledge_publisher', synthetic)
        if parent_release_id:
            parent = conn.execute('SELECT synthetic FROM kb_release WHERE release_id=%s', (parent_release_id,)).fetchone()
            if not parent or parent['synthetic'] is not synthetic:
                raise KBError('RELEASE_PARENT_MISMATCH', 'The parent release must exist in the same publication domain.')
        snapshots = []
        for rid in record_ids:
            record = conn.execute('SELECT * FROM knowledge_record WHERE record_id=%s FOR UPDATE', (rid,)).fetchone()
            if not record:
                raise KBError('KNOWLEDGE_NOT_FOUND', 'A requested knowledge revision does not exist.')
            if record['synthetic'] is not synthetic:
                raise KBError('RELEASE_DOMAIN_MISMATCH', 'Real and synthetic knowledge cannot be mixed in a release.')
            validation, _ = publication_check(conn, record)
            review = conn.execute('SELECT * FROM review_record WHERE record_id=%s ORDER BY event_sequence DESC LIMIT 1', (rid,)).fetchone()
            if not review or review['action'] != 'approved':
                raise KBError('APPROVED_REVIEW_REQUIRED', 'Every revision must have an explicit latest approval.')
            require_actor(conn, review['actor'], 'knowledge_reviewer', synthetic)
            snapshots.append({'record_id': rid, 'review_id': str(review['review_id']),
                'payload_hash': digest(record['payload']), 'validation_hash': digest(validation['report']),
                'version_id': str(record['version_id'])})
        manifest = {'knowledge': record_ids, 'bindings': [], 'knowledge_snapshot': snapshots,
            'scope': 'knowledge_snapshot', 'publisher': actor,
            'parent_release_id': str(parent_release_id) if parent_release_id else None,
            'executable': False, 'index_readiness': {'full_text': False, 'semantic': False}}
        manifest_hash = conn.execute("SELECT encode(sha256(convert_to(%s::jsonb::text,'UTF8')),'hex') AS h", (Jsonb(manifest),)).fetchone()['h']
        release_id = identity('knowledge-release', manifest_hash)
        existing = conn.execute('SELECT * FROM kb_release WHERE release_id=%s', (release_id,)).fetchone()
        if existing:
            return dict(existing) | {'duplicate': True}
        release = insert(conn, 'kb_release', {'release_id': release_id, 'manifest_hash': manifest_hash,
            'parent_release': parent_release_id, 'synthetic': synthetic, 'state': 'published',
            'index_readiness': manifest['index_readiness'], 'manifest': manifest})
        insert(conn, 'release_authorization', {'release_id': release_id, 'actor': actor})
        for snapshot in snapshots:
            insert(conn, 'release_knowledge', {'release_id': release_id, 'record_id': snapshot['record_id'],
                'review_id': snapshot['review_id']})
        return dict(release) | {'duplicate': False}

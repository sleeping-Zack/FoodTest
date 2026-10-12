from importlib.resources import files

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb, Json

from ..common import KBError, digest


def connect(config):
    return psycopg.connect(**config.connection_args(), row_factory=dict_row, autocommit=True)


def migrate(conn):
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(608042001)")
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migration(version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())")
        for path in sorted(files('food_kb.db').joinpath('migrations').iterdir(), key=lambda p: p.name):
            if not path.name.endswith('.sql'):
                continue
            sql = path.read_text(encoding='utf-8')
            checksum = digest(sql)
            previous = conn.execute("SELECT checksum FROM schema_migration WHERE version=%s", (path.name,)).fetchone()
            if previous:
                if previous['checksum'] != checksum:
                    raise KBError('MIGRATION_CHANGED', 'Applied migration checksum differs; append a migration instead.')
                continue
            conn.execute(sql)
            conn.execute("INSERT INTO schema_migration(version,checksum) VALUES(%s,%s)", (path.name, checksum))
    return conn.execute("SELECT version,checksum FROM schema_migration ORDER BY version").fetchall()


def insert(conn, table, values):
    from psycopg import sql
    columns = list(values)
    query = sql.SQL('INSERT INTO {} ({}) VALUES ({}) ON CONFLICT DO NOTHING RETURNING *').format(
        sql.Identifier(table), sql.SQL(',').join(map(sql.Identifier, columns)),
        sql.SQL(',').join(sql.Placeholder() for _ in columns))
    params = [(Json(v) if table=='legacy_record' and k=='raw_payload' else Jsonb(v))
              if isinstance(v, (dict, list)) else v for k,v in values.items()]
    return conn.execute(query, params).fetchone()

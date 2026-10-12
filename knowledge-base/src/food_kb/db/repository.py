"""Identity-sensitive operations whose retries must compare immutable contents."""
from ..common import KBError, identity
from .session import insert


def register_parse(conn,asset_id,parser_version,config_hash,output_hash,quality):
    parse_id=identity('parse',asset_id,parser_version,config_hash)
    row=insert(conn,'parse_run',{'parse_id':parse_id,'asset_id':asset_id,'parser_version':parser_version,
        'config_hash':config_hash,'output_hash':output_hash,'quality':quality})
    if row is None:
        previous=conn.execute('SELECT output_hash,quality FROM parse_run WHERE parse_id=%s',(parse_id,)).fetchone()
        if previous['output_hash']!=output_hash or previous['quality']!=quality:
            raise KBError('PARSE_OUTPUT_CONFLICT','Same parser/configuration produced different immutable output.')
    return parse_id

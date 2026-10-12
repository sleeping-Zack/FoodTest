from .records import Record

FIELDS = set('id code family title categories publication_date implementation_date official_status time_status detail_path detail_url attachments catalog_source as_of is_amendment card_path evidence cover_identifiers filename_declared_code identity_checks identity_conflict identity_review is_draft'.split())


def records(source, data):
    if not isinstance(data,list):raise ValueError('Catalog must be a list')
    for i,raw in enumerate(data):
        if not isinstance(raw,dict):raise ValueError('Catalog row must be an object')
        yield Record(source,f'/{i}',str(raw.get('id',i)),'catalog',raw,unknown_fields=sorted(raw.keys()-FIELDS))

from .records import Record

GROUPS = {'chapters':'guide_chapter','sections':'guide_section','tables':'guide_table','inspection_rows':'inspection_item'}


def records(source,data):
    if not isinstance(data,dict) or not set(GROUPS)<=data.keys():raise ValueError('Guide structure missing')
    yield Record(source,'','metadata','guide_metadata',data,data)
    for field,kind in GROUPS.items():
        for i,raw in enumerate(data[field]):
            yield Record(source,f'/{field}/{i}',str(raw.get('id',i)),kind,raw,{'source':data['source'],'sha256':data['sha256']})

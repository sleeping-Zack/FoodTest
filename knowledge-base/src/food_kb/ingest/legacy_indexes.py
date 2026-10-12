from .records import Record

LISTS={'标准逐层引用关系.json':'reference_candidate','检验方法与条件入口.json':'method_entry',
       '公告解读勘误关联.json':'announcement','非标准号依据与方法关联.json':'nonstandard_basis'}
GROUPS={'产品种类定义表.json':('definitions','definition_export'),
        'guide-pages.json':('pages','guide_page_cache'),'inspection-bridges.json':('bridges','definition_bridge')}


def records(source,data):
    name=source.rsplit('/',1)[-1]
    if name in LISTS:
        if not isinstance(data,list):raise ValueError('Expected list')
        for i,raw in enumerate(data):
            yield Record(source,f'/{i}',str(raw.get('id',raw.get('standard_id',i))),LISTS[name],raw)
    elif name in GROUPS:
        field,kind=GROUPS[name]
        if not isinstance(data,dict) or not isinstance(data.get(field),list):raise ValueError('Expected grouped records')
        context={k:v for k,v in data.items() if k!=field}
        yield Record(source,'','metadata',kind+'_metadata',data,context)
        for i,raw in enumerate(data[field]):
            yield Record(source,f'/{field}/{i}',str(raw.get('id',raw.get('section_id',i))),kind,raw,context)
    else:
        if not isinstance(data,(dict,list)):raise ValueError('Expected legacy rule object')
        if isinstance(data,dict):
            yield Record(source,'','legacy','legacy_rule_candidate',data)
        else:
            for i,raw in enumerate(data):yield Record(source,f'/{i}',str(i),'legacy_rule_candidate',raw)

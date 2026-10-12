from .records import Record


def records(source,data):
    if not isinstance(data,dict) or not {'namespace','standard','coded_nodes','tables','texts','source','sha256'}<=data.keys():
        raise ValueError('Taxonomy structure missing')
    context={k:data[k] for k in ('namespace','standard','source','sha256')}
    yield Record(source,'','metadata','taxonomy_metadata',data,context)
    for field,kind in [('coded_nodes','category'),('tables','classification_table'),('texts','classification_text')]:
        entries=list(enumerate(data[field]))
        invalid=set()
        if field=='coded_nodes':
            ordered=[];done=set();pending=entries
            while pending:
                ready=[(i,r) for i,r in pending if not r.get('parent_id') or r['parent_id'] in done]
                if not ready:
                    invalid={i for i,r in pending};ordered.extend(pending);break
                ordered.extend(ready);done.update(r['id'] for i,r in ready)
                emitted={i for i,r in ready};pending=[(i,r) for i,r in pending if i not in emitted]
            entries=ordered
        for i,raw in entries:
            if i in invalid:
                yield Record(source,f'/{field}/{i}',str(raw.get('id',i)),kind,raw,context|{'invalid_parent':True})
                continue
            yield Record(source,f'/{field}/{i}',str(raw.get('id',i)),kind,raw,context)

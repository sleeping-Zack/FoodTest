from collections import Counter, defaultdict
import csv
import json
from pathlib import Path

import pymupdf

from ..common import KBError, digest, file_hash, read_json, write_json
from ..storage.assets import inspect_file

LIBRARY = "references/食品标准资料库"
CATALOG = LIBRARY + "/索引/标准总目录.json"
GUIDE = LIBRARY + "/索引/食品项目标准方法关联.json"


def cover_quality(path):
    """Identity signals only; this is not KB-05 OCR/layout acceptance."""
    try:
        with pymupdf.open(path) as pdf:
            text = pdf[0].get_text() if len(pdf) else ''
            compact = ''.join(text.split())
            return {"page_count": len(pdf), "cover_text_chars": len(compact),
                    "cover_draft_signal": any(x in compact for x in ('报批稿','征求意见稿','送审稿')),
                    "cover_needs_ocr": len(compact) < 20}
    except (RuntimeError, ValueError):
        return {"pdf_error": "PDF_UNREADABLE"}


def inventory(config, output, previous=None):
    output = Path(output)
    if output.exists():
        raise KBError('OUTPUT_EXISTS', 'Use a fresh inventory directory to preserve earlier evidence.')
    catalog = read_json(config.source(CATALOG))
    registrations = defaultdict(list)
    for i, record in enumerate(catalog):
        for j, asset in enumerate(record.get('attachments', [])):
            if asset.get('path'):
                registrations[asset['path']].append({'catalog_pointer': f'/{i}/attachments/{j}',
                    'legacy_key': record['id'], 'code': record['code'], 'title': record['title'],
                    'is_draft': record.get('is_draft', False), 'identity_conflict': record.get('identity_conflict', False),
                    'expected_sha256': asset.get('sha256'), 'source_url': asset.get('url'),
                    'extraction_status_claim': asset.get('extraction_status'),
                    'empty_pages_claim': asset.get('empty_text_pages', []),
                    'unreliable_pages_claim': asset.get('unreliable_text_pages', [])})
    paths = set(); invalid_references=[]
    for allowed in config.allowed_sources:
        if allowed.is_file(): paths.add(allowed)
        elif allowed.is_dir(): paths.update(p for p in allowed.rglob('*') if p.is_file())
    for relative in registrations:
        try: paths.add(config.source(relative))
        except KBError:
            invalid_references.append({'relative_path':relative,'file_status':'path_boundary','identity_status':'unverified','quality_status':'not_extracted'})
    old = {}
    if previous:
        old = {x['relative_path']: x for x in (json.loads(line) for line in Path(previous).read_text(encoding='utf8').splitlines())}
    output.mkdir(parents=True)
    counts = Counter(); gaps = invalid_references; json_shapes = []; manifest = []
    with (output/'inventory.jsonl').open('w',encoding='utf8') as stream, (output/'source_quality.csv').open('w',encoding='utf-8-sig',newline='') as quality:
        writer = csv.DictWriter(quality,fieldnames=['relative_path','sha256','size','media_type','file_status','identity_status','quality_status'])
        writer.writeheader()
        for path in sorted(paths):
            relative = path.relative_to(config.source_root).as_posix()
            row = {'relative_path': relative, 'registrations': registrations.get(relative, []), 'identity_status':'unverified', 'quality_status':'not_extracted'}
            try:
                info = inspect_file(config.source(relative)); row.update(info); row['file_status']='present'
                registered = row['registrations']
                if any(x['expected_sha256'] and x['expected_sha256'] != info['sha256'] for x in registered):
                    row['file_status']='hash_mismatch'
                if any(x['is_draft'] for x in registered):row['identity_status']='draft'
                if any(x['identity_conflict'] for x in registered):row['identity_status']='conflicting'
                if any(x['unreliable_pages_claim'] for x in registered):row['quality_status']='partial'
                elif any(x['empty_pages_claim'] for x in registered):row['quality_status']='needs_ocr'
                if info['media_type']=='application/pdf':
                    row['cover_check']=cover_quality(path)
                    if row['cover_check'].get('cover_draft_signal'):row['identity_status']='draft'
                    if row['cover_check'].get('pdf_error'):row['quality_status']='unreadable'
                    elif row['cover_check'].get('cover_needs_ocr'):row['quality_status']='needs_ocr'
                if path.suffix.lower()=='.json':
                    try:
                        data=read_json(path)
                        shape={'path':relative,'type':type(data).__name__,'count':len(data) if isinstance(data,(dict,list)) else None}
                        if isinstance(data,dict):shape['fields']={k: {'type':type(v).__name__,'count':len(v) if isinstance(v,(dict,list)) else None} for k,v in data.items()}
                        json_shapes.append(shape)
                    except (ValueError, UnicodeError):
                        row['file_status']='invalid_json'
                if relative in old and old[relative].get('sha256') != info['sha256']:
                    row['previous_sha256']=old[relative].get('sha256');row['change']='changed'
            except (KBError,OSError) as exc:
                row['file_status']=exc.code.lower() if isinstance(exc,KBError) else 'read_failed'
            if row['file_status']!='present' or row['identity_status'] in ('draft','conflicting') or row['quality_status']!='not_extracted' or row.get('change'):
                gaps.append({k:row.get(k) for k in ('relative_path','file_status','identity_status','quality_status','change','previous_sha256')})
            counts[row['file_status']]+=1
            manifest.append((relative,row.get('sha256'),row['file_status']))
            stream.write(json.dumps(row,ensure_ascii=False)+'\n')
            writer.writerow({k:row.get(k) for k in writer.fieldnames})
    current={r[0] for r in manifest}
    gaps.extend({'relative_path':p,'file_status':'removed_since_baseline'} for p in old.keys()-current)
    guide=read_json(config.source(GUIDE))
    logical={(r['section_id'],r['caption']) for r in guide['inspection_rows']}
    summary={'files':len(manifest),'counts':dict(counts),'manifest_hash':digest(manifest),
             'catalog_records':len(catalog),'chapters':len(guide['chapters']),'sections':len(guide['sections']),
             'logical_tables':len(logical),'physical_table_fragments':len(guide['tables']),'inspection_rows':len(guide['inspection_rows']),
             'quality_scope':'All bytes hashed; PDF covers inspected; page-quality metadata is inherited claim, not OCR acceptance.'}
    write_json(output/'gaps.json',gaps);write_json(output/'json-shapes.json',json_shapes);write_json(output/'summary.json',summary)
    return summary

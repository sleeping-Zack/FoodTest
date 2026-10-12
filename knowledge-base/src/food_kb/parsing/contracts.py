"""Validate page and structural locations before storing parsed source blocks."""
import math

from ..common import KBError

CONTRACT_VERSION='kb-layout-1'
PAGE_STATES={'usable','partial','needs_ocr','unreadable','failed'}


def _validate_document(document,asset_id,requested=None):
    total=document['page_count'];pages=document['pages']
    if total is not None and (type(total) is not int or total<1):
        raise KBError('PARSE_CONTRACT','Invalid actual PDF page count.')
    expected=set(requested or range(1,total+1)) if total is not None else {None}
    if total is not None and any(type(p) is not int or not 1<=p<=total for p in expected):
        raise KBError('PARSE_CONTRACT','Requested page does not exist in the actual PDF.')
    seen=set();keys=set()
    for page in pages:
        number=page['page']
        if number not in expected or number in seen or page['status'] not in PAGE_STATES:
            raise KBError('PARSE_CONTRACT','Unexpected or repeated page, or invalid page status.')
        seen.add(number)
        if page['status'] in {'failed','needs_ocr','unreadable'} and not page['issues']:
            raise KBError('PARSE_CONTRACT','Unusable pages must include a diagnostic.')
        if page['status']=='usable' and not any(b['text_raw'].strip() for b in page['blocks']):
            raise KBError('PARSE_CONTRACT','Empty text cannot establish that a page has no requirements.')
        for block in page['blocks']:
            key=block['structural_key'];parent=block.get('parent_key');locator=block['locator']
            if key in keys or (parent is not None and parent not in keys):
                raise KBError('PARSE_CONTRACT','Duplicate block or missing structural parent.')
            keys.add(key)
            if type(block['ordinal']) is not int or block['ordinal']<0 or not isinstance(block['text_raw'],str):
                raise KBError('PARSE_CONTRACT','Invalid block content or reading order.')
            if '\x00' in block['text_raw']:
                raise KBError('PARSE_CONTRACT','NUL text cannot enter PostgreSQL text; preserve the input and diagnose it.')
            if locator.get('asset_id')!=asset_id:
                raise KBError('PARSE_CONTRACT','Block refers to a different source asset.')
            if number is not None:
                if locator.get('pdf_page')!=number:
                    raise KBError('PARSE_CONTRACT','Block actual page differs from its page result.')
                box=locator.get('bbox')
                if box is not None:
                    if locator.get('coordinate_system')!='pdf_points_top_left' or len(box)!=4:
                        raise KBError('PARSE_CONTRACT','Unknown coordinate system.')
                    if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in box):
                        raise KBError('PARSE_CONTRACT','Nonfinite block coordinates.')
                    x0,y0,x1,y1=box
                    if not (0<=x0<=x1<=page['width']+0.1 and 0<=y0<=y1<=page['height']+0.1):
                        raise KBError('PARSE_CONTRACT','Block lies outside the actual page.')
                elif page['status']=='usable':
                    raise KBError('PARSE_CONTRACT','Page-only text must declare degraded location quality.')
            elif 'pdf_page' in locator or not any(k in locator for k in ('paragraph','table','body_index')):
                raise KBError('PARSE_CONTRACT','Word blocks require structural locations, never invented PDF pages.')
            confidence=block.get('confidence')
            if confidence is not None and not (isinstance(confidence,(int,float)) and math.isfinite(confidence) and 0<=confidence<=1):
                raise KBError('PARSE_CONTRACT','Invalid recognition confidence.')
    if seen!=expected:
        raise KBError('PARSE_CONTRACT','Missing requested pages must have explicit failure results.')
    return document


def validate_document(document,asset_id,requested=None):
    try:
        return _validate_document(document,asset_id,requested)
    except (KeyError,TypeError,AttributeError) as exc:
        raise KBError('PARSE_CONTRACT','Parser output is missing a required field or has an invalid field type.') from exc


def isolate_invalid_pages(document,asset_id,requested=None):
    """Keep valid pages and turn malformed/missing pages into independently retryable failures."""
    total=document['page_count']
    if total is not None and (type(total) is not int or total<1):
        raise KBError('PARSE_CONTRACT','Invalid actual PDF page count.')
    numbers=list(requested or range(1,total+1)) if total is not None else [None]
    if total is not None and any(type(p) is not int or not 1<=p<=total for p in numbers):
        raise KBError('PARSE_CONTRACT','Requested page does not exist in the actual PDF.')
    indexed={}
    for page in document['pages']:
        number=page.get('page')
        if number not in numbers:raise KBError('PARSE_CONTRACT','Adapter returned an unrequested or nonexistent page.')
        indexed.setdefault(number,[]).append(page)
    result=[];seen_keys=set()
    for number in numbers:
        choices=indexed.get(number,[])
        try:
            if len(choices)!=1:raise KBError('PARSE_CONTRACT','Requested page is missing or returned more than once.')
            page=choices[0]
            validate_document({'page_count':total,'pages':[page]},asset_id,[number] if total is not None else None)
            keys={b['structural_key'] for b in page['blocks']}
            if keys&seen_keys:raise KBError('PARSE_CONTRACT','Structural block keys repeat across pages.')
            seen_keys.update(keys);result.append(page)
        except KBError as exc:
            result.append({'page':number,'width':None,'height':None,'printed_page':None,'status':'failed',
                'method':'contract_validation','blocks':[],'image':None,
                'issues':[{'code':exc.code,'message':str(exc)}]})
    return document|{'pages':result}


def quality(document):
    states={p['status'] for p in document['pages']}
    if not states:return 'unreadable'
    if states=={'usable'}:
        return 'usable' if document['page_count'] is None or len(document['pages'])==document['page_count'] else 'partial'
    if states=={'needs_ocr'}:return 'needs_ocr'
    if states<={'unreadable','failed'}:return 'unreadable'
    return 'partial'

"""Refresh the small, source-hashed guide page index. Originals remain untouched."""
import hashlib
import json
from pathlib import Path
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
guide = json.loads((ROOT / 'references/食品标准资料库/索引/食品项目标准方法关联.json').read_text(encoding='utf8'))
source = ROOT / guide['source']
digest = hashlib.sha256(source.read_bytes()).hexdigest()
if digest != guide['sha256']:
    raise SystemExit('Source hash differs from the guide index. Reconcile the library first.')
reader = PdfReader(source)
pages = [{'pdf_page': i + 1, 'text': page.extract_text() or ''} for i, page in enumerate(reader.pages)]
dest = Path(__file__).resolve().parent / 'resources/guide-pages.json'
dest.parent.mkdir(exist_ok=True)
dest.write_text(json.dumps({'source': guide['source'], 'sha256': digest, 'pages': pages}, ensure_ascii=False), encoding='utf8')
print(f'Indexed {len(pages)} original pages.')

"""Local-only full-stack server. Python standard library; optional PDFium preview."""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import mimetypes
import re
import sqlite3
import tempfile
import threading
import uuid
import zipfile
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from engine import Library, ROOT, SNAPSHOT, VERSION

APP = Path(__file__).resolve().parent
DATA = APP / 'data'
PDF_LOCK = threading.Lock()


def now():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec='seconds')


class Store:
    def __init__(self, directory=DATA):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.database = self.directory / 'classification.sqlite3'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS queries (id TEXT PRIMARY KEY, created_at TEXT, food TEXT, status TEXT, result TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY, query_id TEXT, created_at TEXT, actor TEXT, action TEXT, note TEXT)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, result):
        result = dict(result, id=str(uuid.uuid4()), created_at=now())
        with self.connect() as db:
            db.execute('INSERT INTO queries VALUES(?,?,?,?,?)', (result['id'], result['created_at'], result['query'], result['status'], json.dumps(result, ensure_ascii=False)))
        return result

    def get(self, query_id):
        with self.connect() as db:
            row = db.execute('SELECT result FROM queries WHERE id=?', (query_id,)).fetchone()
        if not row:
            raise KeyError(query_id)
        result = json.loads(row[0])
        result['reviews'] = self.reviews(query_id)
        return result

    def history(self):
        with self.connect() as db:
            rows = db.execute('SELECT id,created_at,food,status FROM queries ORDER BY created_at DESC,rowid DESC LIMIT 60').fetchall()
        return [dict(zip(['id', 'created_at', 'food', 'status'], row)) for row in rows]

    def reviews(self, query_id):
        with self.connect() as db:
            rows = db.execute('SELECT id,created_at,actor,action,note FROM reviews WHERE query_id=? ORDER BY rowid', (query_id,)).fetchall()
        return [dict(zip(['id', 'created_at', 'actor', 'action', 'note'], row)) for row in rows]

    def add_review(self, query_id, payload):
        self.get(query_id)
        actor = str(payload.get('actor', '')).strip()
        action = str(payload.get('action', ''))
        note = str(payload.get('note', '')).strip()
        if not actor or len(actor) > 60:
            raise ValueError('请填写处理人，最多 60 字。')
        if action not in ['确认候选', '驳回候选', '补充资料', '提交专家']:
            raise ValueError('处理动作无效。')
        if not note or len(note) > 2000:
            raise ValueError('请填写处理依据或待补充内容，最多 2000 字。')
        with self.connect() as db:
            db.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?)', (str(uuid.uuid4()), query_id, now(), actor, action, note))
        return self.reviews(query_id)


def bundle_files(library, result, include_history=False):
    found, seen = [], set()
    standards = list(result['standards'])
    # Independent classification sources are included with their own version labels.
    standards += [t['version'] for t in result['taxonomies'] if t['candidates']]
    for std in standards:
        if not std['national']:
            continue
        for ver in std['versions']:
            if ver['excluded']:
                continue
            if std['dated_reference']:
                if ver['code'] != std['requested_code']:
                    continue
            elif not include_history and not ver['current_candidate']:
                continue
            for file in ver['files']:
                key = file.get('sha256') or file['id']
                if key not in seen:
                    seen.add(key)
                    found.append({'standard': ver['code'], 'status': ver['status'], 'file': file,
                                  'relation': sorted({u['role'] for u in std['uses']}) or ['分类依据']})
    return found


class Handler(BaseHTTPRequestHandler):
    server_version = 'FoodClassifier/1.0'

    def log_message(self, fmt, *args):
        # Avoid logging input food names or paths containing user supplied text.
        if args and isinstance(args[0], str):
            print(f'[{now()}] {self.command} {urlsplit(self.path).path} {args[1] if len(args)>1 else ""}', flush=True)

    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'SAMEORIGIN')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-src 'self'; object-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'")
        super().end_headers()

    @property
    def library(self):
        return self.server.library

    @property
    def store(self):
        return self.server.store

    def check_request(self):
        host = self.headers.get('Host', '')
        expected = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        if host not in expected:
            raise PermissionError('仅接受本机访问。')
        origin = self.headers.get('Origin')
        if origin and origin not in {'http://' + h for h in expected}:
            raise PermissionError('不接受跨站请求。')
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            raise PermissionError('不接受跨站请求。')

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def send_path(self, path, content_type=None, download=False, filename=None):
        path = Path(path)
        size = path.stat().st_size
        start, end = 0, size - 1
        range_header = self.headers.get('Range')
        partial = False
        if range_header:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header)
            if not match or not any(match.groups()):
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            left, right = match.groups()
            if left:
                start, end = int(left), min(int(right) if right else size - 1, size - 1)
            else:
                start = max(0, size - int(right))
            if start > end or start >= size:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            partial = True
        self.send_response(206 if partial else 200)
        self.send_header('Content-Type', content_type or mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Cache-Control', 'no-cache' if path.is_relative_to(APP / 'web') else 'private, max-age=3600')
        if partial:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        if download:
            self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(filename or path.name))
        self.send_header('Content-Length', str(end - start + 1))
        self.end_headers()
        if self.command == 'HEAD':
            return
        with path.open('rb') as source:
            source.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                block = source.read(min(1024 * 256, remaining))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)

    def read_payload(self):
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            raise ValueError('请求格式应为 JSON。')
        length = int(self.headers.get('Content-Length', 0))
        if not 0 < length <= 16384:
            raise ValueError('请求内容为空或过长。')
        result = json.loads(self.rfile.read(length))
        if not isinstance(result, dict):
            raise ValueError('请求内容应为对象。')
        return result

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self.dispatch(False)

    def do_POST(self):
        self.dispatch(True)

    def dispatch(self, post):
        try:
            self.check_request()
            self.route(post)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except PermissionError as exc:
            self.send_json({'error': str(exc)}, 403)
        except KeyError:
            self.send_json({'error': '未找到对应记录或文件。'}, 404)
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({'error': str(exc)}, 400)
        except Exception as exc:
            print(f'Internal error: {type(exc).__name__}: {exc}', flush=True)
            self.send_json({'error': '处理失败，请查看本机服务日志后重试。'}, 500)

    def route(self, post):
        url = urlsplit(self.path)
        path, args = url.path, parse_qs(url.query)
        getarg = lambda key, default='': args.get(key, [default])[0]
        if post:
            payload = self.read_payload()
            if path == '/api/classify':
                self.send_json(self.store.save(self.library.classify(payload)), 201)
                return
            match = re.fullmatch(r'/api/queries/([\w-]+)/reviews', path)
            if match:
                self.send_json({'reviews': self.store.add_review(match[1], payload)}, 201)
                return
            raise KeyError(path)
        if path == '/api/health':
            self.send_json({'ok': True, 'app': 'food-classifier', 'version': VERSION})
        elif path == '/api/meta':
            self.send_json(self.library.metadata())
        elif path == '/api/catalog':
            self.send_json(self.library.catalog(getarg('q'), getarg('chapter')))
        elif path == '/api/definitions':
            self.send_json(self.library.definition_catalog(getarg('q'), getarg('chapter')))
        elif match := re.fullmatch(r'/api/inspection-options/([\w-]+)', path):
            self.send_json(self.library.inspection_preview(match[1]))
        elif path == '/api/history':
            self.send_json({'queries': self.store.history()})
        elif match := re.fullmatch(r'/api/queries/([\w-]+)(/bundle|/export)?', path):
            result = self.store.get(match[1])
            if match[2] == '/bundle':
                self.send_bundle(result, getarg('history') == '1')
            elif match[2] == '/export':
                content = json.dumps(result, ensure_ascii=False, indent=2).encode('utf8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(result['query'] + '-分类与依据.json'))
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(content)
            else:
                self.send_json(result)
        elif match := re.fullmatch(r'/api/standards/([\w-]+)', path):
            self.send_json(self.library.standard_detail(match[1]))
        elif match := re.fullmatch(r'/api/files/([a-f0-9]{24})(/preview)?', path):
            file = self.library.files.get(match[1])
            if not file:
                raise KeyError(path)
            resolved = file['path'].resolve()
            if not resolved.is_relative_to(self.library.root / 'references') or not resolved.is_file():
                raise KeyError(path)
            if match[2]:
                self.preview(file, int(getarg('page', '1')))
            else:
                self.send_path(resolved, download=getarg('download') == '1')
        elif path in ['/', '/app.js', '/styles.css', '/favicon.svg']:
            self.send_path(APP / 'web' / ('index.html' if path == '/' else path[1:]))
        else:
            raise KeyError(path)

    def preview(self, file, page):
        if file['path'].suffix.lower() != '.pdf':
            raise ValueError('Word 文件请下载后核对段落，不能按 PDF 页码定位。')
        if not 1 <= page <= 3000:
            raise ValueError('页码超出范围。')
        try:
            import pypdfium2 as pdfium
        except ImportError:
            self.send_json({'error': '本机未安装页面预览组件，可通过“打开原文”查看。'}, 503)
            return
        cache = self.store.directory / 'preview'
        cache.mkdir(exist_ok=True)
        cached = cache / f'{file["id"]}-{page}.jpg'
        with PDF_LOCK:
            if not cached.exists():
                document = pdfium.PdfDocument(str(file['path']))
                try:
                    if page > len(document):
                        raise ValueError('页码超过文件总页数。')
                    pdf_page = document[page - 1]
                    bitmap = pdf_page.render(scale=min(1.6, 1200 / pdf_page.get_width()))
                    bitmap.to_pil().convert('RGB').save(cached, quality=86)
                    bitmap.close()
                    pdf_page.close()
                finally:
                    document.close()
        self.send_path(cached, 'image/jpeg')

    def send_bundle(self, result, history):
        files = bundle_files(self.library, result, history)
        if sum(f['file']['size'] for f in files) > 800 * 1024 * 1024:
            raise ValueError('关联文件超过 800 MB，请按标准分别下载。')
        # A temporary on-disk archive avoids holding large standard collections in RAM.
        with tempfile.TemporaryDirectory(prefix='bundle-', dir=self.store.directory) as tmp:
            archive = Path(tmp) / 'standards.zip'
            manifest = {'query': result['query'], 'snapshot': SNAPSHOT, 'engine_version': VERSION,
                        'query_id': result['id'], 'data_fingerprint': result['data_fingerprint'],
                        'policy': '包含国标正式原文；有年号保留指定版，无年号默认仅取快照现行候选。报批稿和身份冲突文件不进入文件包。',
                        'include_history': history, 'files': files,
                        'gaps': [s['requested_code'] for s in result['standards'] if s['national'] and not s['available']],
                        'not_packaged': [{'code': s['requested_code'], 'reason': '缺正式原文或不符合本次版本打包选项；请查看原查询的版本明细。'}
                            for s in result['standards'] if s['national'] and not any(
                                any(v['code'] == f['standard'] for v in s['versions']) for f in files)],
                        'limitations': result['limitations']}
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_STORED, allowZip64=True) as z:
                z.writestr('文件清单与缺口.json', json.dumps(manifest, ensure_ascii=False, indent=2))
                z.writestr('分类结果与原文依据.json', json.dumps(result, ensure_ascii=False, indent=2))
                z.writestr('阅读说明.txt', '这是本次分类候选的关联资料包，不等于已确认的必检标准清单。\n请阅读文件清单中的版本状态、关系和缺口；草案与身份冲突文件已排除。\n部分分节引用可能只适用于同节的其他子类，须核对原文。\n')
                for item in files:
                    file = self.library.files[item['file']['id']]
                    safe_name = re.sub(r'[<>:"/\\|?*\r\n]', '_', file['name'])
                    z.write(file['path'], '国标原文/' + file['id'][:6] + '_' + safe_name)
            self.send_path(archive, 'application/zip', True, result['query'] + '-关联国标资料.zip')


def make_server(port=8011, data=DATA):
    library = Library()
    store = Store(data)
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.library = library
    server.store = store
    return server


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='食品分类与国标依据工作台（仅限本机）')
    parser.add_argument('--port', type=int, default=8011)
    parser.add_argument('--data-dir', type=Path, default=DATA)
    args = parser.parse_args()
    server = make_server(args.port, args.data_dir)
    print(f'食品分类与国标依据工作台：http://127.0.0.1:{server.server_port} （资料快照 {SNAPSHOT}）', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

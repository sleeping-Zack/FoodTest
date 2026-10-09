"""Food classifier HTTP service; public mode runs behind an HTTPS reverse proxy."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import tempfile
import threading
import uuid
import zipfile
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
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
            if 'owner' not in {row[1] for row in db.execute('PRAGMA table_info(queries)')}:
                db.execute("ALTER TABLE queries ADD COLUMN owner TEXT NOT NULL DEFAULT ''")
            db.execute('CREATE INDEX IF NOT EXISTS queries_owner_created ON queries(owner, created_at)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, result, owner=''):
        result = dict(result, id=str(uuid.uuid4()), created_at=now())
        with self.connect() as db:
            db.execute('INSERT INTO queries(id,created_at,food,status,result,owner) VALUES(?,?,?,?,?,?)', (result['id'], result['created_at'], result['query'], result['status'], json.dumps(result, ensure_ascii=False), owner))
        return result

    def get(self, query_id, owner=''):
        with self.connect() as db:
            row = db.execute('SELECT result FROM queries WHERE id=? AND owner=?', (query_id, owner)).fetchone()
        if not row:
            raise KeyError(query_id)
        result = json.loads(row[0])
        result['reviews'] = self.reviews(query_id, owner)
        return result

    def history(self, owner=''):
        with self.connect() as db:
            rows = db.execute('SELECT id,created_at,food,status FROM queries WHERE owner=? ORDER BY created_at DESC,rowid DESC LIMIT 60', (owner,)).fetchall()
        return [dict(zip(['id', 'created_at', 'food', 'status'], row)) for row in rows]

    def reviews(self, query_id, owner=''):
        with self.connect() as db:
            rows = db.execute('SELECT r.id,r.created_at,r.actor,r.action,r.note FROM reviews r JOIN queries q ON q.id=r.query_id WHERE r.query_id=? AND q.owner=? ORDER BY r.rowid', (query_id, owner)).fetchall()
        return [dict(zip(['id', 'created_at', 'actor', 'action', 'note'], row)) for row in rows]

    def add_review(self, query_id, payload, owner=''):
        self.get(query_id, owner)
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
        return self.reviews(query_id, owner)


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
        if getattr(self, 'session_cookie', None):
            self.send_header('Set-Cookie', self.session_cookie)
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
        public_origin = self.server.public_origin
        local_hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        # Container health checks may use localhost, but never access visitor data.
        health = self.command in {'GET', 'HEAD'} and self.path == '/api/health' and host in local_hosts
        expected = {urlsplit(public_origin).netloc} if public_origin else local_hosts
        if health:
            expected = expected | local_hosts
        if host not in expected:
            raise PermissionError('访问地址未配置。' if public_origin else '仅接受本机访问。')
        origin = self.headers.get('Origin')
        origins = {public_origin} if public_origin else {'http://' + h for h in expected}
        if origin and origin not in origins:
            raise PermissionError('不接受跨站请求。')
        external_navigation = (public_origin and self.command in {'GET', 'HEAD'}
                               and self.headers.get('Sec-Fetch-Mode') == 'navigate'
                               and self.headers.get('Sec-Fetch-Dest') == 'document')
        if self.headers.get('Sec-Fetch-Site') == 'cross-site' and not external_navigation:
            raise PermissionError('不接受跨站请求。')

    def prepare_session(self):
        self.owner = ''
        self.session_cookie = None
        if not self.server.public_origin or self.path == '/api/health':
            return
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get('Cookie', ''))
        except Exception:
            cookies = SimpleCookie()
        item = cookies.get('__Host-food_session')
        token = item.value if item else ''
        if not re.fullmatch(r'[a-f0-9]{64}', token):
            token = secrets.token_hex(32)
            self.session_cookie = f'__Host-food_session={token}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=31536000'
        self.owner = hashlib.sha256(token.encode('ascii')).hexdigest()

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
            self.prepare_session()
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
            self.send_json({'error': '处理失败，请稍后重试或联系网站维护人员。'}, 500)

    def route(self, post):
        url = urlsplit(self.path)
        path, args = url.path, parse_qs(url.query)
        base = self.server.base_path
        if base and path == base and not post:
            self.send_response(308)
            self.send_header('Location', base + '/' + ('?' + url.query if url.query else ''))
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if base and path.startswith(base + '/'):
            path = path[len(base):]
        elif base and path != '/api/health':
            raise KeyError(path)
        getarg = lambda key, default='': args.get(key, [default])[0]
        if post:
            payload = self.read_payload()
            if path == '/api/classify':
                self.send_json(self.store.save(self.library.classify(payload), self.owner), 201)
                return
            match = re.fullmatch(r'/api/queries/([\w-]+)/reviews', path)
            if match:
                self.send_json({'reviews': self.store.add_review(match[1], payload, self.owner)}, 201)
                return
            raise KeyError(path)
        if path == '/api/health':
            self.send_json({'ok': True, 'app': 'food-classifier', 'version': VERSION})
        elif path == '/api/meta':
            self.send_json(dict(self.library.metadata(), deployment_mode='shared' if self.server.public_origin else 'local'))
        elif path == '/api/catalog':
            self.send_json(self.library.catalog(getarg('q'), getarg('chapter')))
        elif path == '/api/definitions':
            self.send_json(self.library.definition_catalog(getarg('q'), getarg('chapter')))
        elif match := re.fullmatch(r'/api/inspection-options/([\w-]+)', path):
            self.send_json(self.library.inspection_preview(match[1]))
        elif path == '/api/history':
            self.send_json({'queries': self.store.history(self.owner)})
        elif match := re.fullmatch(r'/api/queries/([\w-]+)(/bundle|/export)?', path):
            result = self.store.get(match[1], self.owner)
            if match[2] == '/bundle':
                self.send_bundle(result, getarg('history') == '1')
            elif match[2] == '/export':
                content = json.dumps(result, ensure_ascii=False, indent=2).encode('utf8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Cache-Control', 'private, no-store')
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
            self.send_json({'error': '服务未安装页面预览组件，可通过“打开原文”查看。'}, 503)
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
            # Deployment archives may normalize source mtimes to Unix epoch.
            # ZIP dates start in 1980; clamp metadata without changing document bytes.
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_STORED, allowZip64=True, strict_timestamps=False) as z:
                z.writestr('文件清单与缺口.json', json.dumps(manifest, ensure_ascii=False, indent=2))
                z.writestr('分类结果与原文依据.json', json.dumps(result, ensure_ascii=False, indent=2))
                z.writestr('阅读说明.txt', '这是本次分类候选的关联资料包，不等于已确认的必检标准清单。\n请阅读文件清单中的版本状态、关系和缺口；草案与身份冲突文件已排除。\n部分分节引用可能只适用于同节的其他子类，须核对原文。\n')
                for item in files:
                    file = self.library.files[item['file']['id']]
                    safe_name = re.sub(r'[<>:"/\\|?*\r\n]', '_', file['name'])
                    z.write(file['path'], '国标原文/' + file['id'][:6] + '_' + safe_name)
            self.send_path(archive, 'application/zip', True, result['query'] + '-关联国标资料.zip')


class LimitedHTTPServer(ThreadingHTTPServer):
    """Bound connection count and idle reads behind the reverse proxy."""
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(24)
        super().__init__(*args, **kwargs)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(30)
        return request, address

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            try:
                request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\nRetry-After: 5\r\n\r\n')
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def make_server(port=8011, data=DATA, bind='127.0.0.1', public_origin='', base_path=''):
    base_path = base_path.rstrip('/')
    if base_path and not re.fullmatch(r'(?:/[A-Za-z0-9_-]+)+', base_path):
        raise ValueError('网站路径只能包含字母、数字、下划线和短横线。')
    if public_origin:
        origin = urlsplit(public_origin)
        if origin.scheme != 'https' or not origin.hostname or origin.username or origin.password or origin.path not in {'', '/'} or origin.query or origin.fragment:
            raise ValueError('PUBLIC_ORIGIN 必须是网站的 HTTPS 根地址，例如 https://food.example.com。')
        public_origin = public_origin.rstrip('/')
    elif bind not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('对外监听必须配置 PUBLIC_ORIGIN，并置于 HTTPS 代理之后。')
    library = Library(base_path=base_path)
    store = Store(data)
    server = LimitedHTTPServer((bind, port), Handler)
    server.library = library
    server.store = store
    server.public_origin = public_origin
    server.base_path = base_path
    return server


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='食品分类与国标依据工作台')
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', '8011')))
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('FOOD_CLASSIFIER_DATA_DIR', str(DATA))))
    parser.add_argument('--bind', default=os.environ.get('FOOD_CLASSIFIER_BIND', '127.0.0.1'))
    parser.add_argument('--public-origin', default=os.environ.get('PUBLIC_ORIGIN', ''))
    parser.add_argument('--base-path', default=os.environ.get('FOOD_CLASSIFIER_BASE_PATH', ''))
    args = parser.parse_args()
    server = make_server(args.port, args.data_dir, args.bind, args.public_origin, args.base_path)
    address = (server.public_origin or f'http://127.0.0.1:{server.server_port}') + server.base_path + '/'
    print(f'食品分类与国标依据工作台：{address} （资料快照 {SNAPSHOT}）', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

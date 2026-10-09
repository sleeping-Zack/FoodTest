"""Shared-site isolation, origin validation and legacy database migration."""
import http.client
import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import threading
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from server import Handler, Store, make_server


class SharedSiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.server = make_server(0, cls.temp.name, public_origin='https://food.example.com')
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def request(self, path, payload=None, cookie=None, headers=None):
        con = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=60)
        h = {'Host': 'food.example.com'}
        if payload is not None:
            h.update({'Content-Type': 'application/json', 'Origin': 'https://food.example.com'})
        if cookie:
            h['Cookie'] = cookie
        h.update(headers or {})
        con.request('POST' if payload is not None else 'GET', path,
                    body=json.dumps(payload).encode() if payload is not None else None, headers=h)
        response = con.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        con.close()
        return result

    def new_visitor(self):
        status, _, headers = self.request('/')
        self.assertEqual(status, 200)
        return headers['Set-Cookie'].split(';')[0]

    def test_cookie_and_history_are_private_to_each_visitor(self):
        a, b = self.new_visitor(), self.new_visitor()
        self.assertNotEqual(a, b)
        status, body, _ = self.request('/api/classify', {'food': '年糕'}, cookie=a)
        self.assertEqual(status, 201)
        result = json.loads(body)
        self.assertEqual(result['counts']['items'], 8)
        query_id = result['id']
        self.assertIn(query_id, self.request('/api/history', cookie=a)[1].decode())
        self.assertNotIn(query_id, self.request('/api/history', cookie=b)[1].decode())
        for suffix in ['', '/export', '/bundle']:
            self.assertEqual(self.request('/api/queries/' + query_id + suffix, cookie=b)[0], 404)
        self.assertEqual(self.request('/api/queries/' + query_id)[0], 404)
        status, body, _ = self.request('/api/queries/' + query_id + '/export', cookie=a)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['query'], '年糕')

    def test_foreign_visitor_cannot_add_or_read_reviews(self):
        a, b = self.new_visitor(), self.new_visitor()
        _, body, _ = self.request('/api/classify', {'food': '苹果'}, cookie=a)
        query_id = json.loads(body)['id']
        payload = {'actor': '测试人员', 'action': '补充资料', 'note': '核对加工状态'}
        url = '/api/queries/' + query_id
        self.assertEqual(self.request(url + '/reviews', payload, cookie=b)[0], 404)
        self.assertEqual(self.request(url + '/reviews', payload, cookie=a)[0], 201)
        self.assertEqual(len(json.loads(self.request(url, cookie=a)[1])['reviews']), 1)
        self.assertEqual(self.request(url, cookie=b)[0], 404)

    def test_origin_host_checks_and_external_link_navigation(self):
        self.assertEqual(self.request('/api/history', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('/api/history', headers={'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('/api/classify', {'food': '苹果'}, headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(self.request('/', headers={'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document'})[0], 200)
        local_host = f'127.0.0.1:{self.server.server_port}'
        self.assertEqual(self.request('/api/health', headers={'Host': local_host})[0], 200)
        self.assertEqual(self.request('/api/history', headers={'Host': local_host})[0], 403)

    def test_session_cookie_security_and_invalid_cookie_replacement(self):
        _, _, headers = self.request('/', cookie='__Host-food_session=invalid')
        cookie = headers['Set-Cookie']
        for flag in ['__Host-food_session=', 'Path=/', 'Secure', 'HttpOnly', 'SameSite=Lax']:
            self.assertIn(flag, cookie)
        _, _, existing = self.request('/api/history', cookie=cookie.split(';')[0])
        self.assertNotIn('Set-Cookie', existing)
        self.assertEqual(json.loads(self.request('/api/meta')[1])['deployment_mode'], 'shared')

    def test_public_documents_remain_accessible_with_exact_page_and_range(self):
        fid = self.server.library.guide_file
        status, body, _ = self.request('/api/files/' + fid, headers={'Range': 'bytes=0-7'})
        self.assertEqual(status, 206)
        self.assertEqual(len(body), 8)
        self.assertTrue(body.startswith(b'%PDF'))
        status, body, _ = self.request('/api/files/' + fid + '/preview?page=17')
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b'\xff\xd8'))

    def test_connection_limit_returns_retryable_busy_response(self):
        slots = self.server.slots
        acquired = 0
        try:
            while slots.acquire(blocking=False):
                acquired += 1
            status, _, headers = self.request('/api/health')
            self.assertEqual(status, 503)
            self.assertEqual(headers['Retry-After'], '5')
        finally:
            for _ in range(acquired):
                slots.release()


class DeploymentConfigurationTests(unittest.TestCase):
    def test_download_bundle_preserves_document_with_epoch_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            original = root / 'standard.pdf'
            content = b'%PDF-test-reference-content'
            original.write_bytes(content)
            os.utime(original, (0, 0))
            entry = {'id': 'a' * 24, 'name': original.name, 'size': len(content)}
            files = [{'standard': 'GB TEST', 'file': entry}]
            result = {'query': '部署验证', 'id': 'test', 'data_fingerprint': 'fixture',
                      'standards': [], 'limitations': []}
            handler = object.__new__(Handler)
            handler.server = SimpleNamespace(library=SimpleNamespace(files={entry['id']: dict(entry, path=original)}),
                                             store=SimpleNamespace(directory=root))
            captured = []
            def inspect_archive(archive, *args):
                with zipfile.ZipFile(archive) as z:
                    pdf = next(n for n in z.namelist() if n.endswith('.pdf'))
                    self.assertEqual(z.read(pdf), content)
                    self.assertEqual(z.getinfo(pdf).date_time[:3], (1980, 1, 1))
                    self.assertIsNone(z.testzip())
                    captured.append(pdf)
            handler.send_path = inspect_archive
            with patch('server.bundle_files', return_value=files):
                handler.send_bundle(result, False)
            self.assertEqual(len(captured), 1)

    def test_public_binding_requires_explicit_https_origin(self):
        with self.assertRaises(ValueError):
            make_server(0, bind='0.0.0.0')
        for origin in ['http://food.example.com', 'https://food.example.com/api', 'https://name:password@food.example.com', 'https://food.example.com?x=1']:
            with self.assertRaises(ValueError):
                make_server(0, public_origin=origin)

    def test_legacy_records_are_preserved_but_never_shared(self):
        with tempfile.TemporaryDirectory() as directory:
            db = sqlite3.connect(str(pathlib.Path(directory) / 'classification.sqlite3'))
            db.execute('CREATE TABLE queries(id TEXT PRIMARY KEY,created_at TEXT,food TEXT,status TEXT,result TEXT)')
            db.execute('INSERT INTO queries VALUES(?,?,?,?,?)', ('legacy', '2026-10-09', '苹果', 'name_match', '{"id":"legacy"}'))
            db.commit()
            db.close()
            store = Store(directory)
            self.assertEqual(store.get('legacy')['id'], 'legacy')
            self.assertEqual(store.history('a-new-visitor'), [])
            with self.assertRaises(KeyError):
                store.get('legacy', 'a-new-visitor')
            self.assertEqual(Store(directory).get('legacy')['id'], 'legacy')


class SubpathTests(unittest.TestCase):
    request = SharedSiteTests.request
    tearDownClass = classmethod(SharedSiteTests.tearDownClass.__func__)

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.server = make_server(0, cls.temp.name, public_origin='https://food.example.com', base_path='/foodtest')
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    def test_site_routing_does_not_claim_parent_website(self):
        status, _, headers = self.request('/foodtest?food=rice')
        self.assertEqual(status, 308)
        self.assertEqual(headers['Location'], '/foodtest/?food=rice')
        self.assertEqual(self.request('/foodtest/')[0], 200)
        self.assertEqual(self.request('/foodtest/app.js')[0], 200)
        self.assertEqual(self.request('/foodtest/styles.css')[0], 200)
        for path in ['/', '/api/meta', '/foodtest-other/api/meta']:
            self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request('/api/health', headers={'Host': f'127.0.0.1:{self.server.server_port}'})[0], 200)

    def test_classification_downloads_and_history_work_under_prefix(self):
        _, _, headers = self.request('/foodtest/')
        cookie = headers['Set-Cookie'].split(';')[0]
        status, body, _ = self.request('/foodtest/api/classify', {'food': '年糕'}, cookie=cookie)
        self.assertEqual(status, 201)
        result = json.loads(body)
        self.assertEqual(result['counts']['items'], 8)
        file = next(f for standard in result['standards'] for version in standard['versions'] for f in version['files'])
        self.assertTrue(file['url'].startswith('/foodtest/api/files/'))
        self.assertTrue(file['download_url'].startswith('/foodtest/api/files/'))
        status, body, _ = self.request(file['download_url'], headers={'Range': 'bytes=0-7'})
        self.assertEqual(status, 206)
        self.assertEqual(len(body), 8)
        query_url = '/foodtest/api/queries/' + result['id']
        self.assertEqual(self.request(query_url + '/export', cookie=cookie)[0], 200)
        self.assertEqual(self.request(query_url)[0], 404)
        self.assertIn(result['id'], self.request('/foodtest/api/history', cookie=cookie)[1].decode())

    def test_unsafe_prefixes_fail_before_startup(self):
        for prefix in ['/../other', '/path?x=1', '/path name', '//other']:
            with self.assertRaises(ValueError):
                make_server(0, base_path=prefix)


if __name__ == '__main__':
    unittest.main()

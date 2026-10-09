import hashlib
import http.client
import io
import json
import pathlib
import sys
import tempfile
import threading
import unittest
import zipfile

APP = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from engine import Library, VERSION, norm
from server import Store, make_server, bundle_files


class ClassificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.library = Library()

    def query(self, food, process='', **more):
        return self.library.classify(dict(food=food, process=process, **more))

    def test_all_product_definitions_preserve_original_and_page_spans(self):
        definitions = self.library.definitions
        self.assertEqual(len(definitions), 102)
        for section in self.library.guide['sections']:
            d = definitions[section['id']]
            self.assertEqual(d['raw_text'], section['product_types'])
            for statement in d['statements']:
                self.assertEqual(statement['location_status'], 'paragraph_span')
                first, last = statement['pdf_page'], statement['pdf_end_page']
                self.assertLessEqual(first, last)
                self.assertGreaterEqual(first, section['pdf_page'])
                self.assertLessEqual(last, section['pdf_end_page'])
                text = '\n'.join(p['text'] for p in self.library.guide_pages[first-1:last])
                # PDF folios interrupt paragraphs at page boundaries.
                import re
                text = '\n'.join(line for line in text.splitlines() if not re.fullmatch(r'\s*\d+\s*', line))
                self.assertIn(norm(statement['quote']), norm(text))

    def test_wheat_types_have_source_paths_and_candidate_conditions(self):
        for food, parent in [('全麦粉','通用小麦粉'), ('饺子用小麦粉','专用小麦粉')]:
            r = self.query(food)
            self.assertEqual(r['status'], 'definition_match')
            self.assertTrue(r['inspection_rows'])
            hit = r['definition_matches'][0]
            self.assertEqual(hit['path'], ['小麦粉', parent, food])
            self.assertEqual(hit['evidence']['pdf_page'], 5)
            self.assertIn(food, norm(hit['evidence']['quote']))
            self.assertEqual(r['candidates'][0]['label'], '小麦粉')
            confirmed = self.query(food, selected_id=r['candidates'][0]['id'])
            self.assertEqual(confirmed['product_definition']['section_id'], 'C01-S01')
            self.assertTrue(confirmed['inspection_rows'])
            self.assertEqual(len(confirmed['taxonomies']), 9)

    def test_descendant_queries_automatically_use_nearest_inspection_table(self):
        cases = [('年糕','米粉制品',8), ('糍粑','米粉制品',8), ('燕麦片','其他谷物碾磨加工品',3),
                 ('意大利面','其他谷物粉类制成品',7), ('鸭蛋','其他禽蛋',4),
                 ('小龙虾','淡水虾',11), ('核桃','生干坚果',4), ('板栗','生干坚果',4),
                 ('辅食营养素补充片','辅食营养补充品',25)]
        for food, label, count in cases:
            with self.subTest(food=food):
                r = self.query(food)
                self.assertIsNotNone(r['selected'])
                self.assertEqual(r['selected']['label'], label)
                table = self.library.entries[r['selected']['id']]
                self.assertEqual(r['counts']['items'], len(table['rows']))
                if food != '辅食营养素补充片':
                    self.assertEqual(r['counts']['items'], count)
                self.assertEqual([x['id'] for x in r['inspection_rows']], [x['id'] for x in table['rows']])
                self.assertTrue(r['inspection_resolution']['automatic'])
                self.assertEqual(r['inspection_resolution']['food'], food)
                self.assertTrue(r['standards'])
                self.assertTrue(r['inspection_resolution']['links'])
                for link in r['inspection_resolution']['links']:
                    self.assertEqual(link['table_evidence']['file_id'], self.library.guide_file)

    def test_rice_cake_inherits_only_rice_products_not_the_whole_grain_section(self):
        cake = self.query('年糕')
        parent = self.query('米粉制品')
        self.assertEqual(cake['inspection_rows'], parent['inspection_rows'])
        self.assertEqual(cake['standards'], parent['standards'])
        self.assertEqual(cake['counts']['items'], 8)
        self.assertEqual(cake['inspection_resolution']['links'][0]['definition_evidence']['pdf_page'], 12)
        self.assertEqual(cake['inspection_resolution']['links'][0]['table_evidence']['pdf_page'], 17)
        self.assertEqual({r['evidence']['pdf_page'] for r in cake['inspection_rows']}, {18})

    def test_ambiguous_branches_expose_options_and_do_not_report_zero(self):
        for food, labels in [('白虾',{'淡水虾','海水虾'}), ('整翅',{'鸡肉','鸭肉','其他禽肉'}),
                             ('谷物粉类制成品',{'生湿面制品','发酵面制品','米粉制品','其他谷物粉类制成品'})]:
            r = self.query(food)
            self.assertIsNone(r['selected'])
            self.assertIsNone(r['counts']['items'])
            self.assertFalse(r['inspection_rows'])
            self.assertEqual(r['inspection_resolution']['state'], 'ambiguous')
            options = r['inspection_resolution']['options']
            self.assertEqual({c['label'] for c in options}, labels)
            for option in options:
                preview = self.library.inspection_preview(option['id'])
                self.assertEqual(len(preview['rows']), option['row_count'])
                picked = self.query(food, selected_id=option['id'])
                self.assertEqual(picked['query'], food)
                self.assertEqual(picked['counts']['items'], option['row_count'])

    def test_missing_relation_is_not_a_zero_or_a_whole_section_union(self):
        r = self.query('芸薹属类蔬菜')
        self.assertEqual(r['inspection_resolution']['state'], 'unmapped')
        self.assertIsNone(r['counts']['items'])
        self.assertIsNone(r['selected'])
        self.assertTrue(r['inspection_resolution']['unresolved'])
        self.assertTrue(r['inspection_resolution']['options'])
        self.assertFalse(r['inspection_rows'])
        unknown = self.query('从未收录的火星食物9876')
        self.assertEqual(unknown['inspection_resolution']['state'], 'not_covered')
        self.assertIsNone(unknown['counts']['items'])

    def test_definition_paths_never_link_to_aggregate_section_rows(self):
        for d in self.library.definitions.values():
            for relation in d['relationships']:
                self.assertIn(relation['link_state'], ['unique', 'multiple', 'unmapped'])
                for eid in relation['entry_ids']:
                    entry = self.library.entries[eid]
                    self.assertEqual(entry['kind'], 'leaf')
                    self.assertEqual(entry['section_id'], d['section_id'])
                    self.assertTrue(entry['rows'])
        self.assertFalse(self.library.definition_matches('蔬菜'))  # 方便调料原料不得成为方便面后代

    def test_child_exclusion_does_not_exclude_its_sibling_inspection_table(self):
        r = self.query('高温杀菌乳')
        self.assertIsNotNone(r['selected'])
        self.assertIn('高温杀菌乳', r['selected']['label'])
        self.assertTrue(r['inspection_rows'])
        exclusion = r['definition_exclusions'][0]
        self.assertEqual(exclusion['scope_label'], '巴氏杀菌乳')
        self.assertNotIn(r['selected']['id'], exclusion['blocked_entry_ids'])
        wrong = self.query('巴氏杀菌乳')['selected']['id']
        with self.assertRaises(ValueError):
            self.query('高温杀菌乳', selected_id=wrong)

    def test_definition_exclusions_and_ingredients_are_not_positive_members(self):
        r = self.query('黑米')
        self.assertEqual(r['candidates'][0]['label'], '谷物加工品')
        self.assertNotIn('C01-S02', [x['section_id'] for x in r['candidates']])
        self.assertEqual(r['definition_exclusions'][0]['section_name'], '大米')
        self.assertIn('不包括黑米', norm(r['definition_exclusions'][0]['evidence']['quote']))
        with self.assertRaises(ValueError):
            self.query('黑米', selected_id='C01-S02')
        self.assertNotIn('C01-S03', [x['section_id'] for x in self.query('鸡蛋', 'fresh')['candidates']])
        self.assertNotIn('挂面', [x['name'] for x in self.library.definition_matches('小麦粉')])

    def test_definition_aquatic_membership_keeps_multiple_habitats(self):
        r = self.query('小龙虾', 'fresh')
        self.assertEqual(r['candidates'][0]['label'], '淡水虾')
        ambiguous = self.query('白虾', 'fresh')
        self.assertEqual(ambiguous['status'], 'needs_details')
        self.assertIsNone(ambiguous['selected'])
        self.assertEqual({x['label'] for x in ambiguous['candidates']}, {'淡水虾','海水虾'})
        processed = self.query('小龙虾', 'cooked')
        self.assertFalse(any(c['chapter_id']=='C35' for c in processed['candidates']))

    def test_definition_parser_preserves_names_and_rejects_ingredient_phrases(self):
        names = {r['name'] for d in self.library.definitions.values() for r in d['relationships']}
        self.assertIn('食用植物调和油', names)
        self.assertNotIn('食用植物调', names)
        self.assertIn('复（混）合茶饮料', names)
        self.assertNotIn('主体', names)
        self.assertNotIn('氯化钠', names)
        self.assertEqual(self.library.definition_matches('氯化钠'), [])
        path = next(h['path'] for h in self.library.definition_matches('饺子皮'))
        self.assertIn('谷物粉类制成品', path)

    def test_multiple_food_families_have_evidence(self):
        for food, process in [('苹果','fresh'), ('鸡肉','fresh'), ('巴氏杀菌乳',''), ('小麦粉',''), ('酱油',''), ('蜂蜜',''), ('面包',''), ('鸡蛋','fresh')]:
            with self.subTest(food=food):
                r = self.query(food, process)
                self.assertEqual(r['status'], 'name_match')
                self.assertTrue(r['inspection_rows'])
                self.assertTrue(r['selected']['evidence']['file_id'])
                for row in r['inspection_rows']:
                    self.assertGreater(row['evidence']['pdf_page'], 0)
                self.assertTrue(r['standards'])

    def test_radish_is_not_carrot_or_radish_leaf(self):
        r = self.query('白萝卜', 'fresh')
        self.assertEqual(r['selected']['label'], '萝卜')
        self.assertEqual(r['counts']['items'], 8)
        self.assertEqual(r['selected']['classification']['label'], '根茎类和薯芋类蔬菜')
        taxonomy = {t['standard']: t for t in r['taxonomies']}
        self.assertIn('块根和块茎蔬菜', taxonomy['GB 2762-2025']['candidates'][0]['label'])
        self.assertIn('根茎类', taxonomy['GB 2763-2026']['candidates'][0]['label'])
        self.assertFalse(any('叶菜' in n['label'] for n in taxonomy['GB 2763-2026']['candidates']))

    def test_apple_does_not_inherit_juice_or_dried_taxonomy(self):
        r = self.query('苹果','fresh')
        pesticide = next(t for t in r['taxonomies'] if t['standard']=='GB 2763-2026')
        self.assertTrue(pesticide['candidates'])
        self.assertTrue(all('仁果' in c['label'] for c in pesticide['candidates']))
        for query, process in [('苹果汁',''), ('苹果','drink'), ('苹果干','dried'), ('萝卜','pickled'), ('烤鸡','')]:
            r = self.query(query, process)
            self.assertIsNone(r['selected'])
            self.assertEqual(r['status'],'needs_details')
            self.assertTrue(r['candidates'])
            self.assertTrue(all(c['chapter_id'] not in {'C33','C34','C36'} for c in r['candidates']))

    def test_ambiguous_and_unknown_names_are_not_forced(self):
        for name in ['牛奶','纯牛奶','酸奶','三文鱼','面粉']:
            r = self.query(name)
            self.assertEqual(r['status'],'needs_details')
            self.assertIsNone(r['selected'])
        r = self.query('从未收录的火星食物9876')
        self.assertEqual(r['status'],'not_covered')
        self.assertIsNone(r['selected'])
        self.assertFalse(r['standards'])

    def test_missing_processing_status_is_visible(self):
        r = self.query('苹果')
        self.assertEqual(r['status'],'needs_details')
        self.assertTrue(r['questions'])
        self.assertTrue(r['selected'])

    def test_different_namespaces_keep_their_own_locators(self):
        r = self.query('小麦粉')
        nodes = [t for t in r['taxonomies'] if t['candidates']]
        self.assertGreaterEqual(len(nodes), 5)
        self.assertEqual(len({t['namespace'] for t in r['taxonomies']}), 9)
        self.assertGreater(len({t['evidence']['file_id'] for t in nodes}), 3)
        self.assertEqual(next(t for t in nodes if t['standard']=='GB 2763-2026')['candidates'][0]['evidence']['pdf_page'], 413)

    def test_all_catalog_entries_expand(self):
        leaves = [e for e in self.library.entries.values() if e['kind']=='leaf']
        self.assertEqual(len(leaves), 272)
        for entry in leaves:
            self.assertTrue(entry['rows'], entry['label'])
            self.assertIn(entry['evidence']['file_id'], self.library.files)
            self.assertGreater(entry['evidence']['pdf_page'], 0)
            self.assertTrue(self.library.standards_for(entry), entry['label'])

    def test_standard_gap_and_citation_roles_are_preserved(self):
        r = self.query('苹果','fresh')
        self.assertGreater(r['counts']['missing'], 0)
        roles = {u['role'] for s in r['standards'] for u in s['uses']}
        self.assertIn('检验方法', roles)
        self.assertIn('所在分节引用 · 适用性待核', roles)
        self.assertTrue(any(not s['national'] for s in r['standards']))

    def test_continued_method_citation_points_to_actual_continuation_page(self):
        r = self.query('白萝卜', 'fresh')
        ref = next(s for s in r['standards'] if s['requested_code'] == 'NY/T 1379')
        use = next(u for u in ref['uses'] if '氧乐果' in u['item'])
        self.assertEqual(use['evidence']['pdf_page'], 372)
        self.assertIn('1379', use['evidence']['quote'])

    def test_section_reference_quotes_the_standard_number(self):
        r = self.query('巴氏杀菌乳')
        section_refs = [s for s in r['standards'] if s['uses'][0]['role'] == '所在分节引用 · 适用性待核']
        self.assertTrue(section_refs)
        for s in section_refs:
            quote = ''.join(s['uses'][0]['evidence']['quote'].split())
            self.assertIn(''.join(s['requested_code'].split()), quote)

    def test_dated_reference_and_drafts_are_not_silently_replaced(self):
        s = self.library.resolve_standard('GB 5009.268-2016')
        self.assertTrue(s['dated_reference'])
        result = {'standards':[s], 'taxonomies':[]}
        files = bundle_files(self.library,result)
        self.assertTrue(files)
        self.assertTrue(all(f['standard']=='GB 5009.268-2016' for f in files))
        for std in self.query('面包')['standards']:
            for record in std['versions']:
                if record['excluded']:
                    self.assertFalse(record['current_candidate'])

    def test_current_only_bundle_excludes_future_withdrawn_and_draft(self):
        r = self.query('小麦粉')
        files = bundle_files(self.library,r)
        self.assertTrue(files)
        allowed_codes = {s['requested_code'] for s in r['standards'] if s['dated_reference']}
        allowed_codes.update(t['standard'] for t in r['taxonomies'] if t['candidates'])
        self.assertTrue(all(f['status']=='现行' or f['standard'] in allowed_codes for f in files))
        self.assertFalse(any('稿' in f['status'] or '冲突' in f['status'] for f in files))

    def test_invalid_and_contradictory_input(self):
        for payload in [{'food':''}, {'food':'a'*101}, {'food':'苹果','process':'invalid'}, {'food':'苹果','selected_id':'not-real'}]:
            with self.assertRaises(ValueError): self.library.classify(payload)
        raw = self.query('苹果','fresh')['selected']['id']
        with self.assertRaises(ValueError): self.query('苹果','drink',selected_id=raw)
        self.assertIsNone(self.query('苹果、牛奶')['selected'])

    def test_file_allowlist_rejects_other_workspace_files(self):
        self.assertIsNone(self.library.register_file('README.md'))
        self.assertIsNone(self.library.register_file('../private.pdf'))
        for file in self.library.files.values():
            self.assertTrue(file['path'].is_relative_to(self.library.root/'references'))


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (APP / 'tests/artifacts').mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(dir=APP / 'tests/artifacts')
        cls.server = make_server(0, pathlib.Path(cls.temp.name))
        cls.thread = threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(); cls.temp.cleanup()

    def request(self, path, payload=None, headers=None, method=None):
        con = http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=60)
        data = json.dumps(payload).encode() if payload is not None else None
        h = {'Content-Type':'application/json'} if payload is not None else {}
        h.update(headers or {})
        con.request(method or ('POST' if payload is not None else 'GET'),path,body=data,headers=h)
        response = con.getresponse(); status=response.status; body=response.read(); rh=dict(response.getheaders()); con.close()
        return status,body,rh

    def test_roundtrip_query_and_persistent_review(self):
        code,body,_=self.request('/api/classify',{'food':'小麦粉'})
        self.assertEqual(code,201)
        r=json.loads(body)
        code,body,_=self.request('/api/queries/'+r['id']+'/reviews',{'actor':'测试审核人','action':'补充资料','note':'需核对产品标签中的专用用途。'})
        self.assertEqual(code,201)
        saved=self.server.store.get(r['id'])
        self.assertEqual(saved['reviews'][0]['actor'],'测试审核人')
        self.assertEqual(saved['engine_version'], VERSION)
        code,body,_=self.request('/api/history')
        self.assertIn(r['id'],body.decode())

    def test_file_range_download_preview_and_bounds(self):
        fid=self.server.library.guide_file
        code,body,h=self.request('/api/files/'+fid,headers={'Range':'bytes=0-7'})
        self.assertEqual(code,206); self.assertTrue(body.startswith(b'%PDF')); self.assertEqual(len(body),8)
        code,_,h=self.request('/api/files/'+fid+'?download=1',method='HEAD')
        self.assertEqual(code,200); self.assertIn('attachment',h['Content-Disposition'])
        code,body,_=self.request('/api/files/'+fid+'/preview?page=344')
        self.assertEqual(code,200); self.assertTrue(body.startswith(b'\xff\xd8'))
        self.assertEqual(self.request('/api/files/'+fid+'/preview?page=9999')[0],400)
        self.assertEqual(self.request('/api/files/'+fid,headers={'Range':'bytes=99999999999-'})[0],416)

    def test_bundle_contains_audit_manifest_and_original_pdf(self):
        _,body,_=self.request('/api/classify',{'food':'小麦粉'})
        r=json.loads(body)
        code,body,_=self.request('/api/queries/'+r['id']+'/bundle')
        self.assertEqual(code,200)
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            self.assertIn('文件清单与缺口.json',z.namelist())
            manifest=json.loads(z.read('文件清单与缺口.json'))
            self.assertEqual(manifest['query_id'],r['id'])
            self.assertTrue(manifest['files'])
            pdf=next(n for n in z.namelist() if n.endswith('.pdf'))
            self.assertTrue(z.read(pdf).startswith(b'%PDF'))

    def test_descendant_api_and_branch_preview_keep_food_name_and_provenance(self):
        code, body, _ = self.request('/api/classify', {'food':'年糕'})
        self.assertEqual(code,201)
        r = json.loads(body)
        self.assertEqual(r['counts']['items'],8)
        self.assertEqual(r['query'],'年糕')
        self.assertEqual(r['selected']['label'],'米粉制品')
        code, body, _ = self.request('/api/queries/'+r['id']+'/export')
        self.assertEqual(code,200)
        self.assertTrue(json.loads(body)['inspection_resolution']['links'])
        code, body, _ = self.request('/api/inspection-options/'+r['selected']['id'])
        self.assertEqual(code,200)
        self.assertEqual(len(json.loads(body)['rows']),8)
        self.assertEqual(self.request('/api/inspection-options/C01-S04')[0],404)
        self.assertEqual(self.request('/api/inspection-options/missing')[0],404)

    def test_security_origin_paths_validation_and_script_input(self):
        self.assertEqual(self.request('/api/meta',headers={'Origin':'https://other.example'})[0],403)
        self.assertEqual(self.request('/api/meta',headers={'Host':'other.example'})[0],403)
        self.assertEqual(self.request('/api/files/../../README.md')[0],404)
        self.assertEqual(self.request('/../README.md')[0],404)
        self.assertEqual(self.request('/api/classify',{'food':''})[0],400)
        code,body,_=self.request('/api/classify',{'food':'<script>alert(1)</script>'})
        self.assertEqual(code,201); self.assertIsNone(json.loads(body)['selected'])
        code,body,h=self.request('/')
        self.assertEqual(code,200); self.assertIn('Content-Security-Policy',h)


if __name__=='__main__':
    unittest.main(verbosity=2)

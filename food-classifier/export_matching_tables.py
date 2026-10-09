"""Export the classifier's actual runtime tables for source inspection, without changing rules."""
import datetime
import hashlib
import html
import json
import os
import pathlib
import urllib.parse
import urllib.request

from engine import Library, ALIASES, AMBIGUOUS, VERSION

APP = pathlib.Path(__file__).resolve().parent
library = Library()
dest = library.lib / '当前系统实际匹配表.html'
json_dest = library.lib / '索引/当前系统实际匹配表.json'
e = lambda value: html.escape(str(value or ''), quote=True)


def link(path):
    return urllib.parse.quote(os.path.relpath(path, dest.parent).replace('\\', '/'), safe='/')


def source(ev):
    if not ev or not ev.get('file_id'):
        return '原文位置待补'
    f = library.files[ev['file_id']]
    page = ev.get('pdf_page')
    return f'<a href="{link(f["path"])}{("#page=" + str(page)) if page else ""}" target="_blank">原始 PDF 第 {page} 页 ↗</a>'


runtime = {'status': '未核对运行服务'}
try:
    with urllib.request.urlopen('http://127.0.0.1:8011/api/meta', timeout=3) as response:
        current = json.load(response)
    runtime = {'status': '与 8011 运行服务资料指纹一致' if current['fingerprint'] == library.fingerprint else '与运行服务资料指纹不同',
               'service_fingerprint': current['fingerprint'], 'service_version': current['version']}
except Exception as exc:
    runtime['error'] = type(exc).__name__

entries = []
for entry in library.entries.values():
    entries.append({k: entry.get(k) for k in ['id','kind','label','chapter','section_name','classification','scope','product_types','evidence']} |
                   {'match_terms': sorted(entry['aliases']), 'row_count': len(entry['rows'])})
snapshot = {
    'generated_at': datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),
    'engine_version': VERSION, 'engine_sha256': hashlib.sha256((APP/'engine.py').read_bytes()).hexdigest(),
    'data_fingerprint': library.fingerprint, 'runtime_check': runtime,
    'primary_input': 'references/食品标准资料库/索引/食品项目标准方法关联.json',
    'guide_source': library.guide['source'], 'guide_source_sha256': library.guide['sha256'],
    'entries': entries,
    'product_definition_index': library.definition_catalog(),
    'definition_extractor_sha256': hashlib.sha256((APP/'product_definitions.py').read_bytes()).hexdigest(),
    'taxonomies': [{'namespace': tax['namespace'], 'standard': tax['standard'], 'title': tax['title'],
                    'source': tax['source'], 'source_sha256': tax['sha256'],
                    'match_nodes': library.taxonomy_nodes[tax['namespace']]} for tax in library.taxonomies],
    'handwritten_name_aliases': ALIASES, 'handwritten_ambiguous_names': AMBIGUOUS,
    'note': '这是从当前代码直接导出的匹配数据快照，不是政府发布的统一分类表；匹配词拆分与手写条件不是专家审定结论。'
}
json_dest.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf8')

rows = []
for entry in entries:
    query = ' '.join([entry['chapter'],entry['section_name'],entry['label'],*entry['match_terms']])
    rows.append(f'''<tr class="food-row" data-q="{e(query)}" data-kind="{entry['kind']}">
<td>{e(entry['id'])}<small>{'分节说明' if entry['kind']=='section' else '检验项目表标题'}</small></td>
<td>{e(entry['chapter'])}<small>{e(entry['section_name'])}</small></td>
<td><b>{e(entry['label'])}</b>{('<small>原表明确归类：'+e(entry['classification']['label'])+'</small>') if entry['classification'] else ''}</td>
<td>{' '.join('<span class="term">'+e(term)+'</span>' for term in entry['match_terms'])}</td>
<td>{source(entry['classification']['evidence'] if entry['classification'] else entry['evidence'])}<small>{e((entry['classification']['evidence'] if entry['classification'] else entry['evidence'])['locator'])}</small>
<details><summary>查看范围、产品种类与引用原句</summary><pre>{e(entry['scope'])}\n{e(entry['product_types'])}</pre><blockquote>{e(entry['evidence']['quote'])}</blockquote>{source(entry['evidence'])}</details></td></tr>''')

tax_rows = []
tax_panels = []
for tax in snapshot['taxonomies']:
    namespace = tax['namespace']
    raw = library.lib/'分类体系'/f'{namespace}.json'
    readable = raw.with_suffix('.html')
    tax_rows.append(f'<tr><td><b>{e(tax["standard"])}</b></td><td>{e(tax["title"])}</td><td>{len(tax["match_nodes"])}</td><td><a href="{link(readable)}">完整分类表 ↗</a> · <a href="{link(raw)}">实际输入 JSON ↗</a> · <a href="{link(library.root/tax["source"])}">PDF 原件 ↗</a></td></tr>')
    items=[]
    for node in tax['match_nodes']:
        items.append(f'<tr><td>{e(node.get("code"))}</td><td>{e(node.get("group"))}<br><b>{e(node["label"])}</b></td><td><pre>{e(node.get("terms",node["label"]))}</pre></td><td>{source(node["evidence"])}<small>{e(node["evidence"]["locator"])}</small><details><summary>原文摘录</summary><pre>{e(node["evidence"]["quote"])}</pre></details></td></tr>')
    tax_panels.append(f'<details class="tax-panel"><summary>{e(tax["standard"])} · 程序实际生成的 {len(items)} 个检索条目</summary><p>这是 <code>build_taxonomy_nodes()</code> 生成的匹配条目。拆行、继承表头和词语拆分不保证等同于正式分类层级；兽残条目还混合动物、组织和药物条件，应对照原表核查。</p><table><thead><tr><th>分类代码</th><th>程序类别标签</th><th>被匹配的文本</th><th>原文位置</th></tr></thead><tbody>{"".join(items)}</tbody></table></details>')

alias_rows=''.join(f'<tr><td>{e(k)}</td><td>{e(v)}</td><td>手写名称提示；不是官方分类表原文</td></tr>' for k,v in ALIASES.items())
ambiguous_rows=''.join(f'<tr><td>{e(k)}</td><td>{e("、".join(v))}</td></tr>' for k,v in AMBIGUOUS.items())
css='''body{font:15px/1.7 "Microsoft YaHei",system-ui,sans-serif;background:#f5f7f1;color:#223f32;margin:0}main{max-width:1400px;margin:auto;padding:30px}h1{font-size:28px}h2{font-size:21px;margin-top:40px}a{color:#226044}small{display:block;color:#819076;font-size:11px;margin-top:5px}section,.tax-panel{padding:22px;border:1px solid #dce3d4;border-radius:12px;background:white;margin:18px 0}table{border-collapse:collapse;width:100%;font-size:12px}td,th{text-align:left;padding:12px;border:1px solid #e0e5d9;vertical-align:top;overflow-wrap:anywhere}th{background:#edf3e5}td:first-child{min-width:120px}pre{white-space:pre-wrap;font:12px/1.8 inherit;overflow-wrap:anywhere;max-height:400px;overflow:auto}summary{cursor:pointer;color:#497039}blockquote{margin:12px 0;border-left:3px solid #abc295;padding:10px;background:#f2f6ec}.term{display:inline-block;border:1px solid #d3dfc7;border-radius:4px;padding:2px 6px;margin:2px;background:#f6f9f0;font-size:11px}.notice{background:#fff5dc;padding:17px;border-radius:9px;color:#795a2c}.toolbar{position:sticky;top:0;background:#f5f7f1ee;padding:15px 0;z-index:2;display:flex;gap:14px;align-items:center;flex-wrap:wrap}input,select{padding:10px;border:1px solid #c7d7ba;border-radius:6px;font:inherit}input{min-width:300px}.table-scroll{overflow:auto}.muted{color:#829274;font-size:12px}.tax-panel>summary{font-size:16px;font-weight:600}.tax-panel table{margin-top:18px}[hidden]{display:none!important}nav{display:flex;gap:25px;flex-wrap:wrap}code{font-size:12px} @media(max-width:700px){main{padding:16px}section{padding:13px}input{min-width:0;width:100%}}'''
page=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>当前系统实际使用的匹配表</title><style>{css}</style><main>
<p><a href="index.html">食品标准资料库</a> / 分类实现核对</p><h1>系统到底通过哪张表匹配？</h1>
<p>本页直接导出当前 <code>engine.py</code> 加载并生成的匹配数据。<b>{e(runtime['status'])}</b>。</p>
<p class="muted">生成时间 {e(snapshot['generated_at'])} · 引擎 {e(VERSION)} · 资料指纹 {library.fingerprint}</p>
<div class="notice"><b>v1.2 已将产品种类逐层关联到检验项目表。</b>先看 <a href="产品种类定义表.html">102 个分节的产品种类定义表 ↗</a>：唯一明确关系可直接展开上级项目；多分支分别查看；排除条款只阻止对应类别。原料、工艺、比例等仍须核对。<a href="索引/食品层级检验表关联核验.json">全目录关联核验及缺口 ↗</a>。下表保留名称候选；各国标继续分别检索自身分类表。自动关联资料不等于专家确认适用。</div>
<nav><a href="#primary">① 实际食品名称匹配表</a><a href="#standards">② 各国标分类表</a><a href="#rules">③ 手写名称规则</a><a href="{link(json_dest)}">下载完整匹配快照 JSON</a></nav>
<h2 id="primary">① 第一层：抽检食品名称匹配表</h2>
<p>真实输入文件：<a href="{link(library.lib/'索引/食品项目标准方法关联.json')}">食品项目标准方法关联.json</a>。原件：<a href="{link(library.root/library.guide['source'])}">《国家食品安全抽检实施细则（2026 年版 上册）》</a>。</p>
<p>共 102 个分节条目和 272 个项目表条目。下面的“实际匹配词”逐项取自程序内存，并非另外编写的演示表。部分词由类别名称按括号、“和”“或”等拆出，<b>拆出的词不能保证单独成立为正式食品类别</b>。</p>
<div class="toolbar"><input id="search" placeholder="搜索实际匹配词，例如：萝卜、牛奶、小麦粉" aria-label="搜索实际匹配表"><select id="kind"><option value="">全部条目</option><option value="leaf">只看 272 个项目表条目</option><option value="section">只看 102 个分节条目</option></select><span id="count"></span></div>
<div class="table-scroll"><table><thead><tr><th>条目 ID / 类型</th><th>所属大类 / 分节</th><th>程序使用的食品标签</th><th>实际匹配词（拆分后）</th><th>原文来源与位置</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<h2 id="standards">② 第二层：各国标自己的分类表</h2>
<p>真实输入目录：<code>references/食品标准资料库/分类体系/*.json</code>。下面这些文件逐个被读取；彼此分类名称和代码不通用。“完整分类表”是原表的可读整理，“实际检索条目”展示程序再次加工后的内容。</p>
<div class="table-scroll"><table><thead><tr><th>标准</th><th>分类表位置 / 用途</th><th>程序检索条目数</th><th>直接查看</th></tr></thead><tbody>{''.join(tax_rows)}</tbody></table></div>
{''.join(tax_panels)}
<h2 id="rules">③ 手写名称规则：与官方分类表分开</h2>
<section><h3>俗称 / 简称转换</h3><p>以下来自代码中的 <code>ALIASES</code>，只用于找到候选，没有逐条附官方同义词认定依据。</p><table><thead><tr><th>输入名</th><th>转换后的检索词</th><th>性质</th></tr></thead><tbody>{alias_rows}</tbody></table></section>
<section><h3>预设歧义名称</h3><table><thead><tr><th>名称</th><th>手写候选提示</th></tr></thead><tbody>{ambiguous_rows}</tbody></table></section>
<section><h3>还有哪些判断来自代码？</h3><p>加工词（汁、干、烤、卤、腌等）到候选目录的映射，以及“新鲜水果”等父类别到不同国标同名条目的映射，也来自手写条件。位置分别是 <code>engine.py → processing_candidates()</code> 与 <code>taxonomy_results()</code>，不是标准原文中的现成映射表。匹配权重同样由代码设定。</p><p>源码：<a href="{link(APP/'engine.py')}">查看当前 engine.py</a> · 源码 SHA-256：{snapshot['engine_sha256']}</p></section>
</main><script>const rows=[...document.querySelectorAll('.food-row')];function filter(){{const q=document.querySelector('#search').value.trim().replace(/\\s/g,'').toLowerCase();const k=document.querySelector('#kind').value;let n=0;for(const row of rows){{row.hidden=!!((k&&row.dataset.kind!==k)||(q&&!row.dataset.q.replace(/\\s/g,'').toLowerCase().includes(q)));if(!row.hidden)n++;}}document.querySelector('#count').textContent='显示 '+n+' / '+rows.length+' 条';}}document.querySelector('#search').addEventListener('input',filter);document.querySelector('#kind').addEventListener('change',filter);filter();</script></html>'''
dest.write_text(page, encoding='utf8')
print(json.dumps({'html':str(dest),'json':str(json_dest),'entries':len(entries),'taxonomies':len(tax_rows),'runtime':runtime},ensure_ascii=False))

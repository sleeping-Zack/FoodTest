"""Export the exact runtime product-definition index for offline review."""
import html
import json
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from engine import Library, VERSION

library = Library()
destination = library.lib / '产品种类定义表.html'
data_path = library.lib / '索引/产品种类定义表.json'
data = library.definition_catalog()
data.update(generated_at=datetime.now(timezone(timedelta(hours=8))).isoformat(),
            source_path=library.guide['source'], source_sha256=library.guide['sha256'])
data_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')
e = lambda value: html.escape(str(value or ''), quote=True)
pdf = '../' + quote(library.root.joinpath(library.guide['source']).name)
def source(ev):
    first, last = ev['pdf_page'], ev.get('pdf_end_page', ev['pdf_page'])
    pages = str(first) + (f'–{last}' if last > first else '')
    return f'<a href="{pdf}#page={first}" target="_blank">PDF 第 {pages} 页 ↗</a>'

cards=[]
for d in data['definitions']:
    paragraphs=''.join(f'<div class="clause {"condition" if s["is_condition"] else ""}"><p>{e(s["quote"])}</p>{source(s["evidence"])}</div>' for s in d['statements'])
    relations=[]
    for r in d['relationships']:
        tables=''.join(f'<div>{e(library.entries[eid]["label"])} · {len(library.entries[eid]["rows"])} 项 {source(library.entries[eid]["evidence"])}</div>' for eid in r['entry_ids']) or '关联待补（不代表 0 项）'
        state={'unique':'已关联检验表','multiple':'多个分支，分别查看','unmapped':'关联待核'}[r['link_state']]
        relations.append(f'<tr><td>{e(" → ".join(r["path"]))}<small>{e(state)}</small></td><td>{tables}</td><td>{source(r["evidence"])}</td></tr>')
    cards.append(f'<details class="definition" data-q="{e(d["chapter"]+d["name"]+d["readable_text"])}" {"open" if d["section_id"]=="C01-S01" else ""}><summary><b>{e(d["chapter"])} / {e(d["name"])}</b><span>{source(d["evidence"])}</span></summary><p class="scope">适用范围：{e(d["scope"])}</p>{paragraphs}<details class="relations"><summary>品种关系与检验项目表关联（需核对原句条件）· {len(d["relationships"])} 条</summary><table><thead><tr><th>原文列举 / 小标题关系</th><th>关联检验表与项目数</th><th>分类出处</th></tr></thead><tbody>{"".join(relations)}</tbody></table></details></details>')
page=r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>产品种类定义表 · 食分</title><style>
body{margin:0;background:#f4f6ef;color:#263b2d;font:15px/1.85 "Microsoft YaHei",system-ui,sans-serif}main{max-width:1120px;margin:auto;padding:36px 24px}h1{font-size:30px;line-height:1.4;margin:12px 0}h2{font-size:18px}a{color:#32643a}small,.muted{color:#697363;font-size:12px}nav{display:flex;gap:24px;flex-wrap:wrap}.notice{background:#fff5dc;border-radius:12px;padding:18px}.toolbar{position:sticky;top:0;padding:16px 0;background:#f4f6eff5;display:flex;gap:20px;align-items:center}input{font:inherit;padding:12px 16px;border:1px solid #bacbad;border-radius:8px;flex:1;min-width:0}.definition{background:white;padding:22px;border:1px solid #dce3d3;border-radius:12px;margin:16px 0}.definition>summary{display:flex;justify-content:space-between;gap:20px;cursor:pointer;flex-wrap:wrap}.definition>summary span{font-size:12px}.scope{color:#66755e}.clause{border-left:3px solid #d2dec6;padding:8px 16px;margin:12px 0;overflow-wrap:anywhere}.clause p{margin:0;white-space:pre-wrap}.clause a{font-size:12px}.condition{background:#fcf7e9;border-color:#c3a75f}.relations{margin-top:20px;font-size:13px}summary{cursor:pointer}table{border-collapse:collapse;width:100%;margin-top:12px}th,td{text-align:left;border:1px solid #dce3d3;padding:10px;overflow-wrap:anywhere}td:last-child{white-space:nowrap}[hidden]{display:none!important}@media(max-width:600px){main{padding:20px 14px}.definition{padding:16px}.clause{padding:8px}.toolbar{gap:10px}}</style></head><body><main>
<nav><a href="index.html">食品标准资料库</a><a href="当前系统实际匹配表.html">查看其他匹配表</a><a href="http://127.0.0.1:8011/#definitions">打开食品分类系统</a></nav>
<p class="muted">2026 年抽检细则 / 分类的原文依据</p><h1>先看产品种类，再判断食品类别。</h1><p>按原文逐节提取 <b>102 个分节</b>，保留完整定义、列举品种、原料与工艺，以及排除条件。每段可以回到原始 PDF 核对。</p>
<div class="notice"><b>“产品种类”作为抽检分类的主依据。</b>先从名称找到候选，再核对定义条件，最后关联具体项目表。自动抽取的品种关系尚未逐条专家审定，不能把原料提及或排除项当作归类结论；各国标仍须按其各自分类表分别判断。</div>
<p class="muted">ENGINE_VERSION · GENERATED · 原件 SHA-256：SOURCE_SHA</p><p><a href="索引/产品种类定义表.json">完整定义数据 JSON ↗</a></p><div class="toolbar"><input id="q" placeholder="全文检索：小麦粉、黑米、小龙虾…" aria-label="搜索产品种类"><span id="count">102 / 102 个分节</span></div><p class="muted">全文命中可能是配料、条件或排除项，请展开看原句。浅黄色段落保留了范围、例外或条件提示。</p>
CARDS
</main><script>const cards=[...document.querySelectorAll('.definition')];document.querySelector('#q').addEventListener('input',function(){const q=this.value.replace(/\s/g,'').toLowerCase();let n=0;for(const c of cards){c.hidden=!!q&&!c.dataset.q.replace(/\s/g,'').toLowerCase().includes(q);if(!c.hidden)n++;}document.querySelector('#count').textContent=n+' / 102 个分节';});</script></body></html>'''
page=page.replace('ENGINE_VERSION',e(VERSION)).replace('GENERATED',e(data['generated_at'])).replace('SOURCE_SHA',e(data['source_sha256'])).replace('CARDS',''.join(cards))
destination.write_text(page, encoding='utf8')
print(json.dumps({'html':str(destination),'json':str(data_path),'sections':len(data['definitions']),
    'relationships':sum(len(d['relationships']) for d in data['definitions']),
    'paragraphs':sum(len(d['statements']) for d in data['definitions'])},ensure_ascii=False))

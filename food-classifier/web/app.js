'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const state = {result:null, tab:'classification', filter:'direct', standardSearch:'', catalog:null, evidence:new Map(), evidenceSeq:0, busy:false};
const statuses = {name_match:'名称匹配 · 待核范围', definition_match:'已关联上级检验表 · 待核范围', needs_details:'待补充信息', user_selected:'用户选择 · 待复核', not_covered:'暂未找到可靠分类'};
const bytes = n => n > 1024 * 1024 ? (n / 1024 / 1024).toFixed(1) + ' MB' : Math.round((n || 0) / 1024) + ' KB';
const external = url => /^https?:\/\//i.test(url || '') ? esc(url) : '';

async function api(path, payload) {
  const response = await fetch(path, payload === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求失败，请重试。');
  return data;
}

function toast(message) {
  const el = $('#toast'); el.textContent = message; el.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { el.hidden = true; }, 4200);
}

function error(message) { $('#error').textContent = message; $('#error').hidden = !message; }

function evidenceButton(ev, label = '查看原文 ↗') {
  if (!ev?.file_id) return '<span class="subtle">原文文件暂不可用</span>';
  const id = String(++state.evidenceSeq); state.evidence.set(id, ev);
  return `<button type="button" class="source-btn" data-evidence="${id}">${esc(label)}</button>`;
}

function position(ev) {
  if (!ev) return '';
  return `${ev.pdf_page ? 'PDF 第 ' + ev.pdf_page + (ev.pdf_end_page > ev.pdf_page ? '–' + ev.pdf_end_page : '') + ' 页' : 'Word 正文 / 段落'}${ev.printed_page != null ? ' · 正文第 ' + ev.printed_page + ' 页' : ''}`;
}

function setView(view) {
  for (const name of ['search','catalog','definitions','history']) $('#' + name + '-view').hidden = name !== view;
  $$('.nav').forEach(el => el.classList.toggle('active', el.dataset.view === view));
  $('#page-label').textContent = {search:'食品分类',catalog:'食品目录',definitions:'产品种类定义',history:'查询记录'}[view];
  if (view === 'catalog') loadCatalog();
  if (view === 'history') loadHistory();
  if (view === 'definitions') loadDefinitions();
}

function formValue() {
  return {food:$('#food-input').value.trim(),process:$('#process').value,packaging:$('#packaging').value,notes:$('#notes').value.trim()};
}

async function classify(payload = formValue()) {
  if (state.busy) return;
  if (!payload.food) { $('#food-input').focus(); return; }
  state.busy = true; error(''); $('#loading').hidden = false; $('#welcome').hidden = true;
  $('#results').hidden = true; $('#search-button').disabled = true; setView('search');
  try {
    state.result = await api('/api/classify', payload);
    state.tab = 'classification'; state.filter = 'direct'; state.standardSearch = '';
    state.evidence.clear(); renderResult();
    $('#results').scrollIntoView({behavior:'smooth',block:'start'});
  } catch (err) { error(err.message); $('#welcome').hidden = false; }
  finally { state.busy = false; $('#loading').hidden = true; $('#search-button').disabled = false; }
}

function candidateHtml(c) {
  return `<button class="candidate" data-select="${esc(c.id)}"><b>${esc(c.label)} <span aria-hidden="true">↗</span></b><small>${esc(c.chapter)} / ${esc(c.section_name)}</small><small>${esc(c.reason || '')} · ${c.row_count} 个项目</small></button>`;
}

function renderResult() {
  const r = state.result, s = r.selected;
  const primaryEvidence = s?.classification?.evidence || (r.definition_matches || []).find(h=>h.entry_id===s?.id)?.evidence || r.product_definition?.evidence || s?.evidence;
  const candidates = r.status === 'needs_details' && !s ? r.candidates : [];
  const itemCount = s ? r.counts.items : r.inspection_resolution?.state === 'ambiguous' ? '按分支' : '待关联';
  const standardCount = s ? r.counts.national : '待确认';
  $('#results').hidden = false;
  $('#results').innerHTML = `
    <div class="result-head"><div><h2>${esc(r.query)}</h2><span class="badge ${r.status === 'name_match' ? '' : 'amber'}">${statuses[r.status] || esc(r.status)}</span><p>${esc(r.reason)}</p></div><div class="result-actions"><a class="small-action" href="/api/queries/${esc(r.id)}/export" download>导出查询结果 ↓</a></div></div>
    ${r.questions.length ? `<div class="notice">${r.questions.map(q=>`<p>• ${esc(q)}</p>`).join('')}</div>` : ''}
    ${candidates.length ? `<div class="section-heading"><div><h3>选择与实物相符的食品类型</h3><p>候选按原文名称匹配排列；补充加工状态后可重新查询。</p></div></div><div class="candidate-grid">${candidates.map(candidateHtml).join('')}</div>` : ''}
    ${s ? `<div class="result-summary"><div><div class="trail">2026 年抽检实施细则 / ${esc(s.chapter)} / ${esc(s.section_name)}</div><h3>${esc(s.classification?.label || s.label)}</h3>${evidenceButton(primaryEvidence, (s.classification ? s.label + ' · ' : '') + position(primaryEvidence) + ' · 查看分类原文 ↗')}</div><div class="summary-counts"><div><b>${r.counts.items}</b><span>细则检验项目</span></div><div><b>${r.counts.national}</b><span>关联国标编号</span></div><div><b>${r.counts.missing}</b><span>缺正式原文</span></div></div></div>` : ''}
    ${!s && !candidates.length ? `<div class="empty-state"><h3>暂时无法给出有依据的类别</h3><p>${esc(r.reason)}</p><button class="small-action" data-go-catalog>去食品目录逐层查找 ↗</button></div>` : ''}
    <div class="tabs" role="tablist" aria-label="查询结果"><button class="tab ${state.tab==='classification'?'active':''}" role="tab" aria-selected="${state.tab==='classification'}" data-tab="classification">分类依据 <span class="tab-count">9</span></button><button class="tab ${state.tab==='items'?'active':''}" role="tab" aria-selected="${state.tab==='items'}" data-tab="items">检验项目 <span class="tab-count">${itemCount}</span></button><button class="tab ${state.tab==='standards'?'active':''}" role="tab" aria-selected="${state.tab==='standards'}" data-tab="standards">关联国标 <span class="tab-count">${standardCount}</span></button><button class="tab ${state.tab==='review'?'active':''}" role="tab" aria-selected="${state.tab==='review'}" data-tab="review">人工复核 <span class="tab-count">${(r.reviews || []).length}</span></button></div>
    <div id="result-panel" role="tabpanel"></div>`;
  renderPanel();
}

function renderPanel() {
  const panel = $('#result-panel');
  if (!panel) return;
  ({classification:renderClassification,items:renderItems,standards:renderStandards,review:renderReview}[state.tab] || renderClassification)(panel);
}

function taxCandidate(candidate) {
  return `<div class="tax-match"><b>${candidate.code ? `<span class="code">${esc(candidate.code)}</span>` : ''}${esc(candidate.group ? candidate.group + ' / ' : '')}${esc(candidate.label)}</b>${candidate.part ? `<p>测定部位原文：${esc(candidate.part)}</p>` : ''}${evidenceButton(candidate.evidence, position(candidate.evidence) + ' · 分类原文 ↗')}</div>`;
}

function renderClassification(panel) {
  const r = state.result, s = r.selected;
  panel.innerHTML = `${(r.definition_exclusions || []).map(x=>`<div class="notice"><b>“${esc(x.scope_label || x.section_name)}”的排除条款</b><p>${esc(x.quote)}</p>${evidenceButton(x.evidence,position(x.evidence)+' · 核对排除依据 ↗')}</div>`).join('')}
    ${(r.definition_matches || []).length ? `<article class="definition-hit"><div class="eyebrow">名称找到候选，定义决定适用范围</div><h3>产品种类中的直接列举</h3>${[...new Map(r.definition_matches.map(h=>[h.section_id+h.statement_id+h.name,h])).values()].map(h=>`<div class="definition-route"><b>${esc(h.path.join(' → '))}</b><p>${esc(h.evidence.quote)}</p>${evidenceButton(h.evidence,position(h.evidence)+' · 查看这句原文 ↗')}</div>`).join('')}<p class="subtle">以上是原文列举关系候选，尚未核实样品的原料、工艺及用途条件。请按实物信息确认候选类别。</p></article>` : ''}
    ${r.product_definition ? definitionHtml(r.product_definition, true) : s ? `<details class="scope-card"><summary>此历史记录中的适用范围与产品种类</summary><p>${esc(s.scope)}</p><p>${esc(s.product_types)}</p>${evidenceButton(s.evidence)}</details>` : ''}
    <div class="section-heading"><div><h3>按不同国家标准，分别核对分类</h3><p>以下均为分类依据候选；同一食品在不同标准中的名称、层级和适用条件可能不同。</p></div></div>
    <div class="taxonomy-grid">${r.taxonomies.map(t=>`<article class="taxonomy-card"><div class="taxonomy-top"><span class="taxonomy-code">${esc(t.standard)}</span><span class="badge ${t.candidates.length?'amber':'gray'}">${t.candidates.length?'分类候选':'映射待补'}</span></div><h3>${esc(t.title.split(' · ')[0])}</h3>${t.candidates.slice(0,1).map(taxCandidate).join('')}${t.candidates.length>1?`<details class="tax-more"><summary>还有 ${t.candidates.length-1} 个原文候选</summary>${t.candidates.slice(1).map(taxCandidate).join('')}</details>`:''}${!t.candidates.length?`<div class="subtle">当前输入尚未建立可靠映射</div>`:''}<p>${esc(t.note)}</p><details class="tax-more"><summary>标准使用说明与版本</summary><p>${esc(t.source_note)}</p><p>${esc(t.version.selection_note)}</p>${t.version.versions.filter(v=>v.code===t.standard).map(versionHtml).join('')}${evidenceButton(t.evidence, position(t.evidence) + ' · 分类表入口 ↗')}</details></article>`).join('')}</div>
    <div class="coverage-note"><b>依据边界</b><p>分类表说明食品属于哪些范围；具体限值、禁限用规定、测定部位和例外条件，还须核对对应条款。未命中原文不代表该标准不适用。</p></div>`;
}

function definitionHtml(d, expanded = false) {
  const body = `<p class="subtle">适用范围：${esc(d.scope)}</p><div class="definition-prose">${d.statements.map(s=>`<div class="definition-paragraph ${s.is_condition?'has-condition':''}"><p>${esc(s.quote)}</p>${evidenceButton(s.evidence,position(s.evidence)+' · 原文 ↗')}</div>`).join('')}</div><p class="subtle">核对顺序：成品定义 → 原料与加工方式 → 用途/比例等条件 → 不包括或例外。缺少样品信息时，分类仍待确认。</p>${d.relationships.length?`<details class="tax-more"><summary>查看从本节抽取的品种关系候选 · ${d.relationships.length} 条</summary><div class="definition-names">${d.relationships.map(r=>`<button class="chip" type="button" data-definition-food="${esc(r.name)}" title="${esc(r.path.join(' → '))}">${esc(r.name)}</button>`).join('')}</div><p class="subtle">${esc(d.extraction_note)}</p></details>`:''}<p>${evidenceButton(d.evidence,'打开本节产品种类原文 ↗')}</p>`;
  return expanded ? `<article class="definition-card"><div class="section-heading"><div><span class="eyebrow">第一层分类依据 · 产品种类</span><h3>${esc(d.chapter)} / ${esc(d.name)}</h3></div><span class="badge amber">样品条件待核</span></div>${body}</article>` : `<details class="definition-card"><summary><b>${esc(d.chapter)} / ${esc(d.name)}</b><span>${position(d.evidence)}</span></summary>${body}</details>`;
}

async function loadDefinitions() {
  try {
    if (!state.definitions) state.definitions = (await api('/api/definitions')).definitions;
    renderDefinitions();
  } catch(err) { $('#definition-list').textContent=err.message; }
}

function renderDefinitions() {
  const q=$('#definition-search').value.trim().replace(/\s/g,'').toLowerCase();
  const matches=(state.definitions || []).filter(d=>!q || (d.chapter+d.name+d.readable_text).replace(/\s/g,'').toLowerCase().includes(q));
  $('#definition-count').textContent=`${matches.length} / 102 个分节`;
  $('#definition-list').innerHTML=matches.map(d=>definitionHtml(d)).join('') || '<div class="empty">未找到原文，请尝试规范名称。</div>';
}

function renderItems(panel) {
  const r = state.result;
  if (!r.selected) { panel.innerHTML = pendingInspectionHtml(r); return; }
  panel.innerHTML = `${inspectionLinkHtml(r)}<div class="section-heading"><div><h3>${esc(r.query)} · 检验项目</h3><p>采用“${esc(r.selected.label)}”项目表。实际任务可增加项目，条件项目仍需核对脚注。</p></div><span class="badge gray">${r.inspection_rows.length} 项</span></div>
    ${r.selected.notes?.length?`<div class="notice">${r.selected.notes.map(n=>`<p>${esc(n)}</p>`).join('')}</div>`:''}
    ${inspectionTableHtml(r.inspection_rows)}`;
}

function inspectionTableHtml(rows) {
  return `<div class="table-wrap"><table><thead><tr><th>序号</th><th>检验项目</th><th>判定 / 限量依据</th><th>检验方法</th><th>原文位置</th></tr></thead><tbody>${rows.map(row=>`<tr><td>${esc(row.number)}</td><td>${esc(row.item_raw)}${(row.cell_inheritance||[]).some(x=>x.requires_review)?'<small>跨页继承内容待复核</small>':''}</td><td><div class="method">${esc(row.basis_raw)}</div></td><td><div class="method">${esc(row.method_raw)}</div></td><td>${evidenceButton(row.evidence,position(row.evidence)+' ↗')}${(row.continuation_fragments||[]).map(f=>`<small>续表：PDF 第 ${f.pdf_page} 页</small>`).join('')}</td></tr>`).join('')}</tbody></table></div>`;
}

function inspectionLinkHtml(r) {
  const resolution=r.inspection_resolution;
  if (!resolution || !r.selected) return '';
  const links=[...new Map(resolution.links.map(x=>[x.path.join('/'),x])).values()];
  return `<article class="inspection-link definition-hit"><h3>检验项目从哪里关联过来？</h3><p>查询食品：<b>${esc(r.query)}</b>　关联检验表：<b>${esc(r.selected.label)}</b></p>${links.map(x=>`<p class="inspection-path">${esc(x.path.join(' → '))}</p><p>${evidenceButton(x.definition_evidence,'产品归类原句 · '+position(x.definition_evidence)+' ↗')} ${x.bridge_evidence?evidenceButton(x.bridge_evidence,'定义与表名对应依据 ↗'):''}</p>`).join('')}<p>${evidenceButton(r.selected.evidence,'检验项目表 · '+position(r.selected.evidence)+' ↗')}</p><p class="subtle">系统已沿原文关联到项目表，保留食品名称与来源。分类条件、项目脚注和本次任务范围仍需核对。</p></article>`;
}

function pendingInspectionHtml(r) {
  const resolution=r.inspection_resolution;
  const options=resolution?.options || r.candidates.filter(c=>c.kind==='leaf');
  const note=resolution?.state==='unmapped'?'已找到定义，但项目表关联尚待核对。下面列出同分节检验表供查看，不能视为已适用。':options.length?'存在多个可能的检验分支，可以分别查看项目，再按实物条件选择。各分支不合并为一份必检清单。':'尚未找到可靠的项目表关联；这不表示该食品有 0 个检验项目。';
  return `<div class="notice inspection-pending">${esc(note)}</div>${options.map(c=>`<article class="inspection-option definition-card"><h3>${esc(c.label)} <span class="badge gray">${c.row_count} 项</span></h3><p>${esc(c.reason || c.section_name)}</p>${evidenceButton(c.evidence,'项目表原文 · '+position(c.evidence)+' ↗')}<div class="result-actions"><button class="small-action" data-preview-items="${esc(c.id)}">展开本分支检验项目</button><button class="small-action" data-select="${esc(c.id)}">按此类别查看项目与国标 ↗</button></div><div class="inspection-preview" hidden></div></article>`).join('')}`;
}

function versionHtml(v) {
  const badge = v.current_candidate ? '' : v.excluded ? 'red' : 'gray';
  return `<div class="version"><div><strong class="version-name">${esc(v.code)} ${esc(v.title)}</strong><p><span class="badge ${badge}">${esc(v.status)}</span> ${v.implementation_date?'实施 '+esc(v.implementation_date):'实施日期待核'}${v.is_amendment?' · 修改单':''}</p>${v.files.some(f=>(f.unreliable_text_pages||[]).length)?'<p>部分文字层异常，请核对原文图像。</p>':''}</div><div class="version-buttons"><button class="text-link" data-standard="${esc(v.id)}">范围与引用</button>${v.files.length?v.files.map(f=>`<a class="small-action" href="${esc(f.url)}" target="_blank" rel="noopener">${esc(f.format.toUpperCase())} 原文 ↗</a><a class="small-action" href="${esc(f.download_url)}" download title="${bytes(f.size)}">下载 ↓</a>`).join(''):'<span class="badge amber">缺原文</span>'}</div></div>`;
}

function stdCard(s) {
  const relevant = s.versions.filter(v=> !v.excluded && (!s.dated_reference || v.code===s.requested_code));
  const primary = relevant.find(v=>v.current_candidate && v.files.length) || relevant.find(v=>v.files.length) || relevant[0];
  const other = s.versions.filter(v=>v!==primary);
  const roles = [...new Set(s.uses.map(u=>u.role))];
  const items = [...new Set(s.uses.map(u=>u.item).filter(Boolean))];
  return `<article class="file-card"><div class="file-heading"><div><h3>${esc(s.requested_code)}</h3><p>${esc(primary?.title || '该编号尚缺可核对的正式标准原文')}</p><div class="roles">${roles.map(role=>`<span class="role">${esc(role)}</span>`).join('')}</div></div><span class="badge ${s.current_available?'':s.available?'gray':'amber'}">${esc(s.status)}</span></div>${items.length?`<p class="subtle">关联项目：${esc(items.join('、'))}</p>`:''}<details class="relation-details"><summary>为什么关联此标准 · ${s.uses.length} 处依据</summary>${s.uses.map(u=>`<div class="relation-item">${esc(u.role)}${u.item?' / '+esc(u.item):''}　${evidenceButton(u.evidence,position(u.evidence)+' ↗')}</div>`).join('')}</details><div class="versions">${primary?versionHtml(primary):'<p class="subtle">尚未取得正式原文。编号仍保留在缺口清单中。</p>'}</div>${other.length?`<details class="relation-details"><summary>其他版本 / 修改单 / 待核资料（${other.length}）</summary><p>${esc(s.selection_note)}</p>${other.map(versionHtml).join('')}</details>`:''}</article>`;
}

function standardMatches(s) {
  if (state.filter === 'direct' && (!s.national || !s.uses.some(u=>u.role!=='所在分节引用 · 适用性待核'))) return false;
  if (state.filter === 'all' && !s.national) return false;
  if (state.filter === 'missing' && (!s.national || s.available)) return false;
  if (state.filter === 'section' && (!s.national || s.uses.some(u=>u.role!=='所在分节引用 · 适用性待核'))) return false;
  if (state.filter === 'other' && s.national) return false;
  const haystack = [s.requested_code, ...s.versions.map(v=>v.title), ...s.uses.map(u=>u.item)].join(' ').replace(/\s/g,'').toLowerCase();
  return !state.standardSearch || haystack.includes(state.standardSearch.replace(/\s/g,'').toLowerCase());
}

function renderStandards(panel) {
  const r = state.result;
  if (!r.selected) { panel.innerHTML = pendingInspectionHtml(r); return; }
  panel.innerHTML = `<div class="downloads-bar"><div><b>把本次关联的国标原文带走</b><p>默认含指定年号版与现行候选版，附来源、版本及缺口清单；包含分节引用候选。</p></div><div class="download-options"><label><input type="checkbox" id="include-history"> 含其他正式版本</label><a class="primary" id="bundle-link" href="/api/queries/${esc(r.id)}/bundle" download>下载国标资料包 ↓</a></div></div><div class="filters"><input id="standard-search" aria-label="筛选标准或检验项目" placeholder="筛选标准号、名称或检验项目" value="${esc(state.standardSearch)}">${[['direct','项目直接引用'],['all','全部国标'],['section','分节其他引用'],['missing','缺原文 '+r.counts.missing],['other','行业 / 补充依据']].map(([key,name])=>`<button class="filter ${state.filter===key?'active':''}" data-filter="${key}">${esc(name)}</button>`).join('')}</div><div id="standard-list"></div>`;
  renderStandardList();
}

function renderStandardList() {
  const standards = state.result.standards.filter(standardMatches);
  $('#standard-list').innerHTML = standards.length ? standards.map(stdCard).join('') : '<div class="empty">当前筛选条件下没有记录。</div>';
}

function renderReview(panel) {
  const r = state.result;
  panel.innerHTML = `<div class="section-heading"><div><h3>保留本次分类的人工处理记录</h3><p>处理动作不会覆盖系统原始结果，也不会把用户选择自动升级为专家确认。</p></div></div><form id="review-form" class="review-form"><div class="review-fields"><label>处理人<input id="review-actor" maxlength="60" required autocomplete="name" placeholder="填写姓名"></label><label>处理动作<select id="review-action"><option>确认候选</option><option>驳回候选</option><option>补充资料</option><option>提交专家</option></select></label></div><label>依据与处理说明<textarea id="review-note" rows="4" maxlength="2000" required placeholder="写明确认哪套标准中的哪个类别、依据位置，或需要补充的产品信息。"></textarea></label><button class="primary" type="submit">保存处理记录</button><p class="subtle">本机版本的处理人由用户填写，尚未接入账号与角色认证。</p></form><div id="review-history">${(r.reviews||[]).length ? r.reviews.map(item=>`<article class="review-log"><b>${esc(item.action)}</b><small>${esc(item.actor)}</small><p>${esc(item.note)}</p><time>${esc(item.created_at.replace('T',' '))}</time></article>`).join(''):'<div class="empty">本次查询还没有人工处理记录。</div>'}</div>`;
}

async function loadCatalog() {
  try {
    if (!state.catalog) {
      state.catalog = await api('/api/catalog');
      $('#chapter-filter').innerHTML = '<option value="">全部食品大类</option>' + state.catalog.chapters.map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('');
    }
    renderCatalog();
  } catch (err) { $('#catalog-list').textContent = err.message; }
}

function renderCatalog() {
  const q = $('#catalog-search').value.trim(), chapter = $('#chapter-filter').value;
  const entries = state.catalog.entries.filter(e=>(!chapter || e.chapter_id===chapter) && (!q || [e.label,e.chapter,e.section_name].join(' ').includes(q)));
  const groups = new Map();
  for (const entry of entries.filter(e=>e.kind==='leaf')) {
    if (!groups.has(entry.chapter)) groups.set(entry.chapter, []);
    groups.get(entry.chapter).push(entry);
  }
  $('#catalog-list').innerHTML = groups.size ? [...groups.entries()].map(([name,list])=>`<section class="catalog-group"><h2>${esc(name)} <span class="badge gray">${list.length} 类</span></h2><div class="catalog-items">${list.map(e=>`<button class="catalog-item" data-catalog-select="${esc(e.id)}"><b>${esc(e.label)} ↗</b><span>${esc(e.section_name)} · ${e.row_count} 项</span></button>`).join('')}</div></section>`).join('') : '<div class="empty">未找到对应类别，可尝试规范名称或其他关键词。</div>';
}

async function loadHistory() {
  try {
    const data = await api('/api/history');
    $('#history-list').innerHTML = data.queries.length ? data.queries.map(r=>`<article class="history-row"><div><h3>${esc(r.food)} <span class="badge gray">${esc(statuses[r.status] || r.status)}</span></h3><p>${esc(r.created_at.replace('T',' '))} · 保留当次资料版本</p></div><button class="small-action" data-history="${esc(r.id)}">查看记录 ↗</button></article>`).join('') : '<div class="empty">还没有查询记录。输入一种食品开始。</div>';
  } catch (err) { $('#history-list').textContent = err.message; }
}

function openEvidence(ev) {
  if (!ev) return;
  const dialog = $('#evidence-dialog');
  $('#evidence-title').textContent = ev.file?.name || '原文依据';
  const link = ev.file?.url + (ev.pdf_page ? '#page=' + ev.pdf_page : '');
  $('#evidence-content').innerHTML = `<div class="dialog-body"><div class="evidence-tools"><span>${esc(position(ev))}</span><a class="small-action" target="_blank" rel="noopener" href="${esc(link)}">打开完整原文 ↗</a><a class="small-action" href="${esc(ev.file?.download_url)}" download>下载文件 ↓</a></div><p class="evidence-locator">${esc(ev.locator)}${ev.bbox?' · 已定位到该页表格区域':''}</p>${ev.quote?`<blockquote class="evidence-quote">${esc(ev.quote)}</blockquote>`:''}${ev.file?.format==='pdf' && ev.pdf_page ? `<div class="page-placeholder" id="preview-status">正在读取原文第 ${ev.pdf_page} 页…</div><img class="page-image" id="source-preview" alt="${esc(ev.file.name)}，PDF 第 ${ev.pdf_page} 页" src="/api/files/${esc(ev.file_id)}/preview?page=${ev.pdf_page}" hidden>`:'<p class="muted">此文件为 Word 原文，请下载核对正文段落。</p>'}<p class="subtle">来源文件 SHA-256：${esc(ev.file?.sha256 || '见资料库下载记录')}</p></div>`;
  if (!dialog.open) dialog.showModal();
  const img = $('#source-preview');
  if (img) {
    img.addEventListener('load',()=>{img.hidden=false;$('#preview-status').hidden=true;});
    img.addEventListener('error',()=>{$('#preview-status').textContent='页面预览暂不可用，请点击“打开完整原文”。';});
  }
}

async function openStandard(id) {
  const dialog = $('#standard-dialog');
  $('#standard-content').innerHTML = '<div class="loading"><span class="spinner"></span>正在读取标准范围与逐层引用…</div>';
  if (!dialog.open) dialog.showModal();
  try {
    const data = await api('/api/standards/' + encodeURIComponent(id));
    const r = data.record;
    $('#standard-content').innerHTML = `<div class="dialog-body"><h2>${esc(r.code)}</h2><p class="muted">${esc(r.title)}</p>${versionHtml(r)}${external(r.detail_url)?`<p><a class="text-link" href="${external(r.detail_url)}" target="_blank" rel="noopener">官方来源页面 ↗</a></p>`:''}<div class="section-heading"><h3>范围与条件原文入口</h3></div>${data.evidence.length?data.evidence.map(ev=>`<details class="standard-evidence"><summary>${esc(ev.locator)} · ${esc(position(ev))}</summary><p>${esc(ev.quote)}</p>${evidenceButton(ev)}</details>`).join(''):'<div class="notice">当前原文尚无可靠的范围文字提取，请打开 PDF 图像核对。</div>'}<div class="section-heading"><h3>继续追溯正文中的引用 <span class="badge gray">${data.references.length}</span></h3></div><div class="notice">${esc(data.note)}</div>${data.references.map(ref=>`<details class="relation-details"><summary>${esc(ref.code)} · ${esc(ref.relation)} · ${esc(position(ref.evidence))}</summary><p>${esc(ref.evidence.quote)}</p>${evidenceButton(ref.evidence)}${ref.resolution.versions.map(versionHtml).join('')}${!ref.resolution.versions.length?'<p class="muted">此编号的原文尚未收录。</p>':''}</details>`).join('')}</div>`;
  } catch (err) { $('#standard-content').innerHTML = `<div class="dialog-body error">${esc(err.message)}</div>`; }
}

document.addEventListener('click', async event => {
  const button = event.target.closest('button,a'); if (!button) return;
  if (button.dataset.view) { setView(button.dataset.view); return; }
  if (button.dataset.previewItems) {
    const panel=$('.inspection-preview',button.closest('.inspection-option'));
    if(panel.dataset.loaded){panel.hidden=!panel.hidden;return;}
    button.disabled=true;
    try {const data=await api('/api/inspection-options/'+encodeURIComponent(button.dataset.previewItems));panel.innerHTML=`<p class="subtle">${esc(data.note)}</p>${data.notes.map(n=>`<p>${esc(n)}</p>`).join('')}${inspectionTableHtml(data.rows)}`;panel.dataset.loaded='true';panel.hidden=false;}
    catch(err){toast(err.message);}finally{button.disabled=false;}
    return;
  }
  if (button.dataset.definitionFood) {
    $('#food-input').value=button.dataset.definitionFood; $('#process').value=''; $('#packaging').value=''; $('#notes').value='';
    classify(); return;
  }
  if (button.dataset.food) {
    $('#food-input').value = button.dataset.food; $('#process').value = button.dataset.process || ''; $('#packaging').value = button.dataset.package || ''; $('#notes').value = '';
    classify(); return;
  }
  if (button.dataset.evidence) { openEvidence(state.evidence.get(button.dataset.evidence)); return; }
  if (button.dataset.standard) { openStandard(button.dataset.standard); return; }
  if (button.classList.contains('close-dialog')) { button.closest('dialog').close(); return; }
  if (button.dataset.select) { classify({...state.result.input, selected_id:button.dataset.select}); return; }
  if (button.dataset.tab) { state.tab = button.dataset.tab; renderResult(); return; }
  if (button.dataset.filter) { state.filter = button.dataset.filter; renderPanel(); return; }
  if (button.hasAttribute('data-go-catalog')) { setView('catalog'); return; }
  if (button.dataset.catalogSelect) {
    const e = state.catalog.entries.find(x=>x.id===button.dataset.catalogSelect);
    $('#food-input').value = e.label; $('#process').value=''; $('#packaging').value=''; $('#notes').value='';
    classify({...formValue(),selected_id:e.id}); return;
  }
  if (button.dataset.history) {
    try {
      state.result=await api('/api/queries/'+encodeURIComponent(button.dataset.history)); state.tab='classification';
      $('#food-input').value=state.result.query; $('#process').value=state.result.input.process; $('#packaging').value=state.result.input.packaging; $('#notes').value=state.result.input.notes;
      setView('search'); $('#welcome').hidden=true; error(''); renderResult(); window.scrollTo({top:0,behavior:'smooth'});
    } catch (err) { toast(err.message); }
  }
  if (button.id==='bundle-link') toast('正在打包国标原文，文件较多时请稍候。');
});

document.addEventListener('submit', async event => {
  if (event.target.id==='search-form') { event.preventDefault(); classify(); }
  if (event.target.id==='review-form') {
    event.preventDefault(); const submit=$('button[type=submit]',event.target); submit.disabled=true;
    try {
      const data=await api('/api/queries/'+state.result.id+'/reviews',{actor:$('#review-actor').value,action:$('#review-action').value,note:$('#review-note').value});
      state.result.reviews=data.reviews; renderResult(); toast('处理记录已保存。');
    } catch (err) { toast(err.message); submit.disabled=false; }
  }
});

document.addEventListener('input', event=>{
  if (event.target.id==='standard-search') { state.standardSearch=event.target.value; renderStandardList(); }
  if (event.target.id==='catalog-search') renderCatalog();
  if (event.target.id==='definition-search') renderDefinitions();
});
document.addEventListener('change', event=>{
  if(event.target.id==='chapter-filter') renderCatalog();
  if(event.target.id==='include-history') $('#bundle-link').href='/api/queries/'+state.result.id+'/bundle'+(event.target.checked?'?history=1':'');
});
$('#notes-toggle').addEventListener('click',()=>{const box=$('#notes-box');box.hidden=!box.hidden;$('#notes-toggle').setAttribute('aria-expanded',String(!box.hidden));});
$('#about-link').addEventListener('click',event=>{event.preventDefault();$('#about-dialog').showModal();});
$$('dialog').forEach(dialog=>dialog.addEventListener('click',event=>{if(event.target===dialog)dialog.close();}));

api('/api/meta').then(meta=>{
  $('#snapshot').textContent=meta.snapshot;
  $('#stat-chapters').textContent=meta.chapters; $('#stat-foods').textContent=meta.food_entries;
  $('#stat-standards').textContent=meta.standards.toLocaleString(); $('#stat-taxonomies').textContent=meta.taxonomies;
}).catch(err=>error(err.message));

if (location.hash === '#definitions') setView('definitions');
const initialFood=new URLSearchParams(location.search).get('food');
if(initialFood && location.hash !== '#definitions') {$('#food-input').value=initialFood;classify();}

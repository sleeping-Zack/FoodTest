'use strict';
(() => {
  const data = JSON.parse(document.getElementById('viewer-data').textContent);
  const cases = data.cases || [], snapshot = data.snapshot || {};
  const sourceKey = entry => entry.source_key || (snapshot.sources || []).find(source => [entry.asset_id,entry.markdown_asset_id,entry.source_asset_id].filter(Boolean).includes(source.asset_id || source.sha256))?.source_key || '';
  const byId = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const text = value => typeof value === 'object' && value !== null ? JSON.stringify(value) : String(value ?? '未提供');
  const labels = {success:'查询成功',empty:'正常空结果',failed:'检索失败',failure:'检索失败',error:'检索错误',degraded:'已降级',unknown:'未知',review_pending:'待审核',approved:'已审核',literal_raw:'原始字面匹配',normalized_phrase:'规范化短语',zh_ngram_phrase:'中文二字与短语',exact_name:'精确名称',related_name:'相关名称，非同一对象',phrase_evidence:'原文字面命中',exact_metadata:'精确字段命中',exact:'精确字段',lexical:'中文全文',comparison:'方案比较',requirement:'指标要求',classification:'分类节点',chunk:'原文块',method:'方法事实',definition:'定义事实',standard_metadata:'版本目录',inspection_requirement:'检验表项目',standard_scope_catalog:'范围目录'};
  const fieldLabels = {designation:'标准号',family:'标准系列',year:'标准年份',version_id:'版本身份',document_id:'标准主体身份',record_id:'记录身份',project:'项目名称',project_id:'项目身份',clause:'条款或表格位置',method_standard:'方法标准',judgment_standard:'判定标准',namespace:'分类体系',node_id:'分类节点身份',category_code:'类别代码',record_type:'记录类型',review_state:'审核状态',requirement_type:'要求类型',chunk_id:'原文块身份',name:'名称',title:'标题',body:'正文',definition:'定义',scope:'范围',table:'表格',examples:'原文举例'};
  const label = value => labels[value] || String(value ?? '未知');
  const badge = (value, cls='') => `<span class="badge ${esc(cls || value)}">${esc(label(value))}</span>`;
  const jsonBlock = (title, value) => `<details class="subsection"><summary>${esc(title)}</summary><pre>${esc(JSON.stringify(value, null, 2))}</pre></details>`;
  const pair = (name, value) => `<dt>${esc(name)}</dt><dd>${esc(text(value))}</dd>`;
  let current = null, evidence = [], evidenceMap = new Map();
  const kind = item => item.kind || item.result?.channel || 'lexical';
  const runs = result => {
    if (result?.results_by_strategy) return Object.entries(result.results_by_strategy);
    if (result?.strategies && !Array.isArray(result.strategies)) return Object.entries(result.strategies);
    if (Array.isArray(result?.results)) return result.results.map((r, i) => [r.query?.mode || String(i + 1), r]);
    return [[result?.query?.mode || result?.channel || '结果', result || {}]];
  };
  function collectEvidence(value) {
    evidence = []; evidenceMap = new Map();
    const visit = entry => {
      if (!entry || typeof entry !== 'object') return;
      if (!Array.isArray(entry) && sourceKey(entry) && (entry.source_key || entry.locator || entry.quote)) {
        const key = JSON.stringify(entry);
        if (!evidenceMap.has(key)) { evidenceMap.set(key, `source-${evidence.length + 1}`); evidence.push(entry); }
      }
      for (const child of Object.values(entry)) visit(child);
    };
    visit(value);
  }
  function sourceLinks(sources) {
    const links = [];
    const visit = entry => {
      if (!entry || typeof entry !== 'object') return;
      const id = evidenceMap.get(JSON.stringify(entry));
      if (id) links.push(`<a href="#${esc(id)}" class="evidence-link">${esc(sourceKey(entry))}${entry.json_pointer ? ' · ' + esc(entry.json_pointer) : ''}</a>`);
      for (const child of Object.values(entry)) visit(child);
    };
    visit(sources);
    return links.length ? `<div class="evidence-links">${[...new Set(links)].join('')}</div>` : '<p class="help">此结果没有可展示的来源定位，需补证据。</p>';
  }
  function issueSummary(items) {
    if (!Array.isArray(items) || !items.length) return '';
    const groups = new Map();
    for (const item of items) {
      const key = `${item.kind || item.code || '待核'} / ${item.reason || item.message || text(item)}`;
      groups.set(key, (groups.get(key) || 0) + 1);
    }
    return `<div class="issues">${[...groups].map(([key, count]) => `<div class="issue">${esc(key)}${count > 1 ? ` × ${count}处` : ''}</div>`).join('')}</div>`;
  }
  function hitCard(hit) {
    const exact = hit.exact || {};
    const designation = exact.designation || exact.standard_code || exact.standard || exact.family || hit.record_id;
    const display = hit.display_fields || {};
    const heading = display.project || exact.project || display.title || display.name || exact.project_name || exact.item_name || exact.name || exact.title || designation;
    const fields = Object.entries(exact).map(([key, value]) => pair(fieldLabels[key] || key, ['record_type','review_state'].includes(key) ? label(value) : value)).join('') + Object.entries(display).map(([key,value])=>pair(`原文${fieldLabels[key] || key}`,value)).join('');
    const matches = (hit.matches || []).map(match => `<div class="match-row">${badge(match.match_kind)} <strong>${esc(match.term)}</strong><p><span class="help">字段 ${esc(fieldLabels[match.field] || match.field)}${match.raw_values ? '；精确字段原值' : `；原查询 ${esc(match.raw_term)}；字符位置 ${esc(text(match.span))}；定位精度 ${esc(match.span_precision)}`}</span></p><blockquote>${esc(match.raw_values ? text(match.raw_values) : match.matched_text)}</blockquote></div>`).join('');
    const checks = (hit.condition_checks || []).map(check => `<p class="condition-row"><strong>${check.check_kind === 'exclusion' ? '排除核对' : '必要条件核对'}</strong>：${esc(check.check_kind === 'exclusion' ? `排除谓词 ${check.predicate_status || 'unknown'}；范围状态 ${check.status}` : check.status || 'unknown')}；待补事实 ${esc(text(check.missing_facts || []))}</p>`).join('');
    return `<article class="card retrieval-hit" data-record-id="${esc(hit.record_id)}"><div class="heading"><h4><span class="rank">#${esc(hit.rank)}</span> ${esc(text(heading))}</h4>${badge(hit.review_state)}</div><p class="meta">${esc(text(designation))} · ${esc(label(hit.record_type))} · ${esc(hit.record_id)}</p><dl class="kv">${pair('检索分数', hit.score)}${pair('适用性', label(hit.applicability || 'unknown'))}${pair('可执行', hit.executable === true ? '是' : '否')}${fields}</dl><p class="help">检索分数只表示排序，不是业务置信度；命中没有确认适用。</p><section class="subsection"><h5>命中词与原始字段</h5>${matches || '<p class="help">没有命中定位。</p>'}</section><section class="subsection"><h5>条件、版本与资料缺口</h5><p class="help">未知条件保留候选，不作为排除理由。</p>${checks}${issueSummary(hit.gaps)}${jsonBlock('条件及其判断', {conditions:hit.conditions || [], exclusions:hit.exclusions || [], condition_checks:hit.condition_checks || [], applicability:hit.applicability || 'unknown'})}${hit.gap_ids?.length ? jsonBlock('缺口标识', hit.gap_ids) : ''}</section>${sourceLinks(hit.sources || hit.evidence || [])}</article>`;
  }
  function renderRun(name, result) {
    const status = result.status || 'unknown';
    const failed = ['failed','failure','error','unavailable'].includes(status);
    const degraded = status === 'degraded';
    const hits = result.hits || [];
    const fault = failed ? '<div class="fault"><strong>检索失败，不能解释为无资料或无要求。</strong></div>' : degraded ? '<div class="fault warning"><strong>索引未正常工作：当前结果来自明确的降级路径。</strong></div>' : '';
    const summary = [['状态',label(status)],['命中数 / 总数',`${hits.length} / ${result.total_count ?? '未知'}`],['空结果原因',result.empty_reason || '—'],['索引代次',result.generation_id || '未提供'],['原始查询',result.query?.raw ?? result.query?.filters ?? '未提供'],['规范化副本',result.query?.normalized ?? result.query?.normalized_filters ?? '未提供']];
    return `<section class="panel query-run" data-strategy="${esc(name)}"><div class="heading"><h3>${esc(label(name))}</h3><div class="badges">${badge(status)}${badge(result.channel || 'lexical')}</div></div>${fault}<div class="summary-grid">${summary.map(([key,value])=>`<div class="summary-item"><strong>${esc(key)}</strong>${esc(text(value))}</div>`).join('')}</div>${issueSummary(result.faults || result.errors || result.issues || [])}${result.unknown_conditions ? jsonBlock('未满足或未知的条件',result.unknown_conditions) : ''}<div class="cards">${hits.map(hitCard).join('')}</div>${!hits.length ? `<p class="empty">${failed ? '未取得可靠结果，须处理检索故障。' : degraded ? '降级路径没有命中；须同时核对故障与资料范围。' : '本查询正常执行但未命中。请结合空结果原因及资料缺口核对，不能判定没有应检要求。'}</p>` : ''}${jsonBlock('查询分词、过滤及索引追踪', {query:result.query,trace:result.trace,index_trace:result.index_trace,requested_mode:result.requested_mode,effective_mode:result.effective_mode,fallback_result_state:result.fallback_result_state,missing_capabilities:result.missing_capabilities,performance:result.performance,faults:result.faults,errors:result.errors})}${result.next_offset !== null && result.next_offset !== undefined ? `<p class="help">还有结果（下一偏移 ${esc(result.next_offset)}）；本页只展示此保存页，不代表候选穷尽。</p>` : ''}</section>`;
  }
  function renderSources() {
    byId('evidence-count').textContent = `${evidence.length} 个来源记录`;
    byId('evidence-list').innerHTML = evidence.map((entry,i) => {
      const key = sourceKey(entry), mapped = data.source_files?.[key];
      const file = mapped?.status === 'hash_verified' ? `<a href="${esc(mapped.url)}" target="_blank" rel="noopener">打开随包来源文件</a>` : `<span class="help">${mapped?.status === 'hash_mismatch' ? '文件哈希不符，未提供入口' : mapped?.status === 'unverified' ? '来源哈希未经核验，未提供入口' : '此来源未随结果包提供；可按来源键回工作区追溯'}</span>`;
      return `<details class="source-card" id="source-${i+1}"><summary>${esc(key)}</summary><dl class="kv">${pair('原始来源指针',entry.json_pointer || entry.pointer || '未提供')}${pair('定位',entry.locator || entry.location || '未提供')}${pair('定位质量',entry.locator_quality || entry.location_status || '未提供')}${pair('来源指纹',entry.sha256 || entry.source_asset_id || entry.asset_id || '未提供')}</dl><blockquote>${esc(entry.quote || entry.excerpt || '此来源记录未附原文摘录，不能据此补写原文。')}</blockquote><div class="source-actions">${file}</div>${jsonBlock('完整来源记录',entry)}</details>`;
    }).join('') || '<p class="empty">此场景没有来源记录；未补造原文。</p>';
  }
  function choose(item) {
    current = item;
    byId('results').hidden = false;
    byId('query-title').textContent = item.title || item.id;
    byId('query-note').textContent = `${item.synthetic === true ? '合成工程样例；不代表真实食品、标准或专家审核。 ' : '真实登记资料上的工程查询；真实知识待审。 '}${item.note || ''}`;
    byId('query-badges').innerHTML = badge(kind(item)) + badge(item.synthetic === true ? '合成场景' : '真实来源查询');
    collectEvidence(item.result);
    byId('query-input').innerHTML = jsonBlock('查询输入（原样保存）',item.input || {});
    const resultRuns = runs(item.result);
    byId('result-overview').innerHTML = `<p class="help">${resultRuns.length} 个保存的结果；以下排名和命中词来自查询引擎，本页不重新计算。</p>`;
    byId('query-details').innerHTML = resultRuns.map(([name,result]) => renderRun(name,result)).join('');
    byId('result-json').textContent = JSON.stringify(item.result,null,2);
    byId('trace-data').innerHTML = pair('场景 ID',item.id) + pair('索引 generation',snapshot.generation_id || snapshot.snapshot_id || '未提供') + pair('处理器版本',snapshot.processor_version || snapshot.algorithm_version || '未提供');
    renderSources();
    for(const button of document.querySelectorAll('.case-button')) button.classList.toggle('active',button.dataset.caseId === item.id);
    for(const link of document.querySelectorAll('.evidence-link'))link.addEventListener('click',()=>{const card=byId(link.getAttribute('href').slice(1));if(card)card.open=true;});
  }
  function filter() {
    const search=byId('scenario-search').value.trim().toLowerCase(), type=byId('kind-filter').value;
    const filtered=cases.filter(item=>(type==='all'||kind(item)===type)&&JSON.stringify([item.title,item.note,item.input,item.id]).toLowerCase().includes(search));
    byId('case-buttons').replaceChildren();
    for(const item of filtered){const button=document.createElement('button');button.type='button';button.className='example-button case-button';button.dataset.caseId=item.id;button.textContent=item.title||item.id;button.addEventListener('click',()=>choose(item));byId('case-buttons').appendChild(button);}
    byId('no-case').hidden=filtered.length>0;
    if(!filtered.length)byId('results').hidden=true;else choose(filtered.find(item=>item.id===current?.id)||filtered[0]);
  }
  function download(value, name) {
    const blob=new Blob([JSON.stringify(value,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),link=document.createElement('a');
    link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function percentage(value){return typeof value==='number'?`${(value*100).toFixed(1)}%`:'无分母';}
  const evaluation=data.evaluation||{};
  byId('evaluation-panel').hidden=!Object.keys(evaluation).length;
  byId('evaluation-json').textContent=JSON.stringify(evaluation,null,2);
  byId('evaluation-summary').innerHTML='<div class="table-wrap"><table><thead><tr><th>方案</th><th>查询数</th><th>标签召回@k</th><th>精确率下界</th><th>已标注精确率</th><th>未标注命中</th><th>故障查询</th><th>平均毫秒</th></tr></thead><tbody>'+(evaluation.summary||[]).map(row=>`<tr><td>${esc(label(row.strategy))}</td><td>${esc(row.queries)}</td><td>${esc(percentage(row.micro_recall_at_k))}</td><td>${esc(percentage(row.precision_at_k_lower_bound))}</td><td>${esc(percentage(row.judged_precision_at_k))}</td><td>${esc(row.unjudged_hit_count)}</td><td>${esc(row.failed_or_missing_queries)}</td><td>${esc(typeof row.mean_latency_ms==='number'?row.mean_latency_ms.toFixed(2):'未记录')}</td></tr>`).join('')+'</tbody></table></div><p class="help">召回分母是显式相关标签数；下界精确率 = 标签命中 / 全部返回，已标注精确率 = 标签命中 /（标签命中 + 已知误召回）。未标注不等于错误。故障查询保留在分母，不能用这组小样本推算全食品准确率。</p>';
  const summary=snapshot.summary||{};
  const stats=[['已保存查询',cases.length],['检索记录',summary.record_count ?? summary.records ?? '—'],['来源文件',(snapshot.sources||[]).length]];
  byId('statistics').innerHTML=stats.map(([name,value])=>`<div class="stat"><strong>${esc(value)}</strong><span>${esc(name)}</span></div>`).join('');
  byId('snapshot-state').textContent=`索引代次：${snapshot.generation_id||snapshot.snapshot_id||'未提供'}；本页读取冻结结果，审核和适用状态保持独立。`;
  byId('coverage-state').textContent=summary.accepted_body_versions !== undefined ? `正文全文范围：${summary.accepted_body_versions} 个接受的标准版本、${summary.accepted_body_chunks ?? '未知'} 个正文块；其余 ${summary.standard_bodies_not_indexed ?? '未知'} 个登记版本的主体全文未纳入本代次。分类、表格和目录字段分别作为候选资料。` : (snapshot.limitations || []).join(' ');
  byId('scenario-search').addEventListener('input',filter);byId('kind-filter').addEventListener('change',filter);
  byId('download-result').addEventListener('click',()=>{if(current)download(current.result,`${current.id}.json`);});
  byId('download-evaluation').addEventListener('click',()=>download(evaluation,'retrieval-evaluation.json'));
  filter();document.documentElement.dataset.viewerReady='true';
})();

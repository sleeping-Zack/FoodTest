'use strict';
(() => {
  const payload = JSON.parse(document.getElementById('viewer-data').textContent);
  const snapshot = payload.snapshot;
  const examples = payload.examples || [];
  const sourceFiles = payload.source_files || {};
  const $ = id => document.getElementById(id);
  const asList = value => Array.isArray(value) ? value : [];
  const text = value => value == null ? '未提供' : typeof value === 'object' ? JSON.stringify(value) : String(value);
  const statusLabels = {applicable:'条件满足',not_applicable:'条件不满足',unknown:'仍待核实',conflicting:'证据冲突',review_pending:'待业务审核',approved:'已审核',partial:'部分覆盖',complete:'覆盖完整',unverified:'尚未核实',verified:'已核事实',hash_verified:'文件指纹已核',source_pointer:'来源记录定位',page_claim:'原文页码声明',synthetic:'受控工程输入'};
  const factLabels = {process:'工艺',primary_heat_treatment:'最终主要热处理类别',milk_ingredient_proportion_percent:'生牛（羊）乳或复原乳比例（%）',part:'部位',food_part:'食品部位',raw_material:'原料',physical_state:'状态',product_identity:'产品身份',food_name:'食品名称',ingredients:'配料',ingredient_proportion:'配料比例',intended_use:'预期用途',purpose:'用途',packaging:'包装',species:'物种',food_species:'食品物种',category_id:'自报类别',category_namespace:'自报分类体系',task_regime:'任务制度',measurement_basis:'计量基准',population:'适用人群',production_stage:'生产环节',regime:'任务制度',business_date:'业务日期',date_role:'日期角色',matrix:'食品基质',project:'项目',component:'表达组分',sampling_plan:'采样方案'};
  const roleLabels = {sample_fact:'样品事实',document_fact:'资料事实',verified_observation:'已核观察',self_reported_category:'分类声明线索'};
  const nodeIndex = new Map(asList(snapshot.namespaces).flatMap(ns => asList(ns.nodes).map(node => [ns.namespace + ':' + (node.category_id || node.id), node])));
  const findNode = (namespace, id) => nodeIndex.get(namespace + ':' + id) || {};
  const namespaceIndex = new Map(asList(snapshot.namespaces).map(ns => [ns.namespace || ns.id, ns]));
  const sourceByAsset = new Map(asList(snapshot.sources).map(source => [source.asset_id || source.sha256, source]));
  const evidenceIndex = new Map();
  const evidenceRows = [];
  let selected = examples[0] || null;
  function element(tag, content, className) {
    const el = document.createElement(tag);
    if (content != null) el.textContent = text(content);
    if (className) el.className = className;
    return el;
  }
  function badge(status, label) {
    return element('span', label || statusLabels[status] || text(status), 'badge ' + (['applicable','not_applicable','unknown','conflicting'].includes(status) ? status : ''));
  }
  function evidenceKey(evidence) { return JSON.stringify(evidence); }
  function registerEvidence(evidence) {
    if (!evidence || typeof evidence !== 'object' || !evidence.source_key) return null;
    const key = evidenceKey(evidence);
    if (!evidenceIndex.has(key)) {
      const id = 'evidence-' + (evidenceRows.length + 1);
      evidenceIndex.set(key, id);
      evidenceRows.push({id, evidence});
    }
    return evidenceIndex.get(key);
  }
  function gatherEvidence(value, target = new Set()) {
    if (!value || typeof value !== 'object') return target;
    if (value.source_key && (value.quote != null || value.locator || value.json_pointer)) {
      const id = registerEvidence(value);
      if (id) target.add(id);
    }
    if (Array.isArray(value)) value.forEach(item => gatherEvidence(item, target));
    else Object.values(value).forEach(item => gatherEvidence(item, target));
    return target;
  }
  gatherEvidence(snapshot);
  examples.forEach(example => gatherEvidence(example.result));
  function evidenceLinks(evidences) {
    const div = element('div', null, 'evidence-links');
    const shown = new Set();
    asList(evidences).forEach(evidence => {
      const id = registerEvidence(evidence);
      if (!id || shown.has(id)) return;
      shown.add(id);
      const link = element('a', '依据 ' + id.replace('evidence-',''), 'evidence-link');
      link.href = '#' + id;
      div.append(link);
    });
    return div;
  }
  function stateRows(values, fallback, exclusions = false) {
    const ul = element('ul', null, 'condition-list');
    asList(values).forEach(value => {
      const row = typeof value === 'string' ? {raw:value,status:'unknown'} : value;
      const li = element('li');
      const status = row.status || row.result?.status || 'unknown';
      const exclusionLabels = {applicable:'触发排除',not_applicable:'未触发此排除',unknown:'排除仍待核实',conflicting:'排除证据冲突'};
      li.append(badge(status, exclusions ? exclusionLabels[status] : null));
      const raw = row.raw || row.condition?.raw || row.reason || row.message || fallback;
      if (text(raw).length > 350 || text(raw).split('\n').length > 8) {
        const detail = element('details',null,'long-original');
        detail.append(element('summary','展开完整条件原文'),element('p',raw,'definition'));
        li.append(detail);
      } else li.append(element('span',raw,'condition-raw'));
      const missing = asList(row.missing_facts || row.result?.missing_facts);
      if (missing.length) li.append(element('span', '待补：' + missing.map(field => factLabels[field] || field).join('、'), 'condition-missing'));
      if (row.evidence) li.append(evidenceLinks(row.evidence));
      ul.append(li);
    });
    if (!ul.childElementCount) ul.append(element('li', fallback, 'help'));
    return ul;
  }
  function section(title, content) {
    const div = element('section', null, 'branch-section');
    div.append(element('div', title, 'section-title'), content);
    return div;
  }
  function pathView(path) {
    const div = element('div', null, 'path');
    asList(path).forEach((node, index) => {
      if (index) div.append(element('span','›','separator'));
      div.append(element('span', typeof node === 'string' ? node : node.label || node.name || node.category_id));
    });
    if (!div.childElementCount) div.append(element('span','路径未建立；不能无依据继承。'));
    return div;
  }
  function requirementView(requirement, namespace) {
    const row = element('div', null, 'requirement');
    row.dataset.requirementId = requirement.id || requirement.requirement_id || '';
    const top = element('div', null, 'requirement-header');
    top.append(element('span', requirement.project || requirement.semantic?.project || requirement.label || requirement.title || requirement.id), badge(requirement.status || 'unknown'));
    row.append(top);
    row.append(element('p', '审核：' + (statusLabels[requirement.review_state] || requirement.review_state || '未提供') + ' · 不作为本次任务必检结论 · 不可用于自动判定'));
    if (requirement.semantic?.basis_raw) row.append(element('p', '判定依据原值：' + requirement.semantic.basis_raw));
    if (requirement.semantic?.method_raw) row.append(element('p', '方法依据原值：' + requirement.semantic.method_raw));
    const sourcePath = asList(requirement.source_path);
    if (sourcePath.length) row.append(element('div', '关联路径：' + sourcePath.map(item => typeof item === 'string' ? item : item.label || relationLabel(item.relation_kind) || item.kind || item.category_id || item.id).join(' → '), 'requirement-path'));
    if (requirement.condition_result) row.append(stateRows([requirement.condition_result], '关联条件尚未结构化。'));
    if (asList(requirement.exclusions_checked).length) row.append(stateRows(requirement.exclusions_checked, '排除范围未核实。', true));
    row.append(evidenceLinks(requirement.evidence || requirement.supporting_evidence));
    const id = requirement.source_category_id || requirement.category_id;
    if (id) row.append(evidenceLinks(findNode(namespace,id).evidence));
    return row;
  }
  function relationLabel(kind) {return {parent:'上下级关系',explicit_example:'原文列举',table_link:'检验表关联',manual_bridge:'人工桥接候选'}[kind] || kind;}
  function inheritanceView(paths) {
    const details = element('details');
    const distinctCount = new Set(asList(paths).map(path => JSON.stringify(path))).size;
    details.append(element('summary','查看完整关联路径与逐步条件（' + distinctCount + ' 条不同路径）'));
    const seen = new Set();
    asList(paths).forEach(path => {
      const key = JSON.stringify(path);
      if (seen.has(key)) return;
      seen.add(key);
      const entry = element('div',null,'path-evidence');
      asList(path).forEach(step => {
        const heading = element('div',null,'badges');
        heading.append(element('span',step.label || relationLabel(step.relation_kind) || step.kind),badge(step.status || 'unknown'));
        entry.append(heading);
        if (step.condition_result) entry.append(stateRows([step.condition_result],'这一步的条件尚未完整。'));
        if (asList(step.exclusions_checked).length) entry.append(stateRows(step.exclusions_checked,'排除范围尚未完整。',true));
        const shown = new Set(asList(step.condition_result?.evidence).map(evidenceKey));
        const extraEvidence = asList(step.evidence).filter(evidence => !shown.has(evidenceKey(evidence)));
        if(extraEvidence.length)entry.append(evidenceLinks(extraEvidence));
      });
      details.append(entry);
    });
    return details;
  }
  function branchView(branch, namespace) {
    const card = element('article', null, 'branch');
    card.dataset.categoryId = branch.category_id || '';
    const node = findNode(namespace,branch.category_id);
    const heading = element('div', null, 'section-heading');
    const title = element('div');
    title.append(element('h4', branch.label || node.name || branch.category_id), element('div', (branch.code ? '原分类代码：' + branch.code + ' · ' : '') + '类别标识：' + text(branch.category_id), 'category-code'));
    heading.append(title,badge(branch.status || 'unknown'));
    card.append(heading,pathView(branch.path));
    const states = element('div', null, 'badges');
    states.append(badge(node.review_state || branch.review_state || 'review_pending'));
    if (branch.condition_result) states.append(badge(branch.condition_result.status, '定义条件：' + (statusLabels[branch.condition_result.status] || branch.condition_result.status)));
    states.append(badge('unknown','版本日期：待核'));
    card.append(states);
    const definition = branch.definition || node.definition || {};
    const definitionText = typeof definition === 'string' ? definition : definition.raw || asList(definition.statements).map(statement => typeof statement === 'string' ? statement : statement.raw || statement.quote || text(statement)).join('\n');
    const definitionBody = element('div');
    if(definitionText.length > 450 || definitionText.split('\n').length > 8) {
      definitionBody.append(element('p','候选类别原文：' + (branch.label || node.name || branch.category_id),'definition'));
      const detail = element('details',null,'long-original');
      detail.append(element('summary','展开分类定义与原表完整文字'),element('p',definitionText,'definition'));
      definitionBody.append(detail);
    } else definitionBody.append(element('p',definitionText || '当前资料没有完整定义；保留候选并提示缺口。','definition'));
    definitionBody.append(evidenceLinks(branch.supporting_evidence || node.evidence));
    const reasonLabels = {exact_source_name:'来源名称精确匹配',self_reported_category_hint:'自报分类仅作线索',engineering_normalization_hint:'工程歧义提示，尚非已核别名',structured_definition:'有来源的定义条件候选'};
    if(asList(branch.candidate_reasons).length) definitionBody.append(element('p','候选来源：' + branch.candidate_reasons.map(reason => reasonLabels[reason] || reason).join('；'),'help'));
    card.append(section('定义与匹配依据',definitionBody));
    const conditions = [];
    if (branch.condition_result) conditions.push(branch.condition_result);
    card.append(section('适用条件',stateRows(conditions,'尚未建立完整定义条件，不能按名称直接确认适用。')));
    if (asList(branch.unresolved_conditions).length) card.append(element('p','还有 ' + branch.unresolved_conditions.length + ' 处未闭合条件引用；按路径和各条要求展开核对。','help'));
    card.append(section('排除核对',stateRows(branch.exclusions_checked,'来源未提供完整结构化排除对象，不能视为已全部排除核对。',true)));
    if (asList(branch.inheritance_paths).length) card.append(section('继承与桥接的依据',inheritanceView(branch.inheritance_paths)));
    const missing = asList(branch.missing_facts);
    if (missing.length) {
      const gaps = element('div',null,'gaps');
      missing.forEach(field => gaps.append(element('span',factLabels[field] || text(field),'gap')));
      card.append(section('仍需补充或核实',gaps));
    }
    const requirements = asList(branch.requirements);
    const requirementBox = element('div',null,'requirements');
    const count = branch.association_count == null ? '关联数量未知' : branch.association_count + ' 条已关联记录';
    requirementBox.append(element('h4','相关检验要求 · ' + count));
    requirementBox.append(element('p','这是有来源的关联候选。具体样品适用、版本与此次任务必检仍须分别核定。','help'));
    if (requirements.length) requirements.forEach(requirement => requirementBox.append(requirementView(requirement,namespace)));
    else requirementBox.append(element('p',branch.association_count == null ? '关联范围存在缺口，不能显示为 0 项。' : '本查询未返回要求记录；不表示该食品没有要求。','help'));
    card.append(requirementBox);
    return card;
  }
  function namespaceView(namespace) {
    const container = element('section',null,'namespace');
    container.dataset.namespace = namespace.namespace;
    const ns = namespaceIndex.get(namespace.namespace) || {};
    const top = element('div',null,'namespace-heading');
    const heading = element('div');
    heading.append(element('h3', namespace.label || ns.standard || ns.label || namespace.namespace,'namespace-label'));
    heading.append(element('div',namespace.namespace + ' · 版本标识：' + text(namespace.standard_version_id || ns.standard_version_id),'namespace-meta'));
    const state = element('div',null,'badges');
    state.append(badge(namespace.status || 'unknown'),badge(namespace.coverage_state || 'partial','目录：' + (statusLabels[namespace.coverage_state] || namespace.coverage_state || '部分覆盖')),badge(namespace.identity_status || 'unverified','文件身份：' + (statusLabels[namespace.identity_status] || namespace.identity_status || '尚未核实')));
    top.append(heading,state);
    container.append(top);
    const branches = element('div',null,'branches');
    asList(namespace.branches).forEach(branch => branches.append(branchView(branch,namespace.namespace)));
    if (!branches.childElementCount) branches.append(element('p','本体系未返回可展示分支；未覆盖或缺资料不能解释为无要求。','empty'));
    container.append(branches);
    const common = asList(namespace.common_requirements);
    if (common.length) {
      const shared = element('div',null,'shared-requirements');
      shared.append(element('strong','各保留分支可共用的关联记录（仍须核实适用）'));
      common.forEach(requirement => shared.append(requirementView(requirement,namespace.namespace)));
      container.append(shared);
    }
    if(asList(namespace.common_associations).length) container.append(element('p','多分支共用 ' + namespace.common_associations.length + ' 条同一来源关联；这里只证明引用相同，不证明完整要求语义等价或此次任务必检。','scope-note'));
    if(asList(namespace.comparison_unknown).length) container.append(element('p','有 ' + namespace.comparison_unknown.length + ' 条记录的要求语义仍部分抽取，共用／分支独有比较尚不能确定。','scope-note'));
    const specific = asList(namespace.branch_specific_requirements).filter(item => asList(item.requirements).length);
    if (specific.length) {
      const detail = element('details',null,'scope-note');detail.append(element('summary','查看有完整语义依据的分支独有要求'));
      specific.forEach(item => {detail.append(element('h4',findNode(namespace.namespace,item.category_id).name || item.category_id));asList(item.requirements).forEach(requirement => detail.append(requirementView(requirement,namespace.namespace)));});
      container.append(detail);
    }
    container.append(element('p','范围完整性：' + (namespace.scope_complete === true ? '本次声明范围已展开；不等于全部食品标准覆盖。' : '尚未完整。未知条件和未覆盖来源继续保留，未合并分支为确定清单。'),'scope-note'));
    if(namespace.gap_count) container.append(element('p','本体系快照另记录 ' + namespace.gap_count + ' 条来源或关系缺口，完整条目保留在查询 JSON。','help'));
    return container;
  }
  function sourceLink(key, page, label) {
    const source = sourceFiles[key];
    if (!source || source.status !== 'hash_verified' || !source.url) return null;
    const link = element('a',label);
    link.href = source.url + (Number.isInteger(page) && page > 0 && /\.pdf$/i.test(key) ? '#page=' + page : '');
    link.target = '_blank';link.rel = 'noopener noreferrer';
    return link;
  }
  function sourceView(id,evidence) {
    const card = element('details',null,'source-card');
    card.id = id;
    const locator = evidence.locator || {};
    card.append(element('summary','依据 ' + id.replace('evidence-','') + ' · ' + evidence.source_key));
    const location = [evidence.json_pointer ? '索引位置 ' + evidence.json_pointer : null,Number.isInteger(locator.pdf_page) ? 'PDF 第 ' + locator.pdf_page + (locator.pdf_end_page > locator.pdf_page ? '–' + locator.pdf_end_page : '') + ' 页' : null,locator.location_status,statusLabels[evidence.locator_quality] || evidence.locator_quality].filter(Boolean).join(' · ');
    card.append(element('div',location || '原文定位尚未提供。','source-meta'));
    card.append(element('blockquote',evidence.quote || '本记录仅提供来源定位，没有附逐段引文。'));
    const actions = element('div',null,'source-actions');
    const direct = sourceLink(evidence.source_key,locator.pdf_page,'打开来源文件');
    if (direct) actions.append(direct);
    else actions.append(element('span','来源文件：' + (statusLabels[sourceFiles[evidence.source_key]?.status] || sourceFiles[evidence.source_key]?.status || '未随结果包保存'),'help'));
    const original = sourceByAsset.get(evidence.source_asset_id);
    if (original && original.source_key !== evidence.source_key) {
      const originalLink = sourceLink(original.source_key,locator.pdf_page,'打开原件' + (locator.pdf_page ? '第 ' + locator.pdf_page + ' 页' : ''));
      if (originalLink) actions.append(originalLink);
    }
    card.append(actions);
    const evidenceKind = evidence.kind || evidence.relation_kind;
    if (evidenceKind) card.append(element('p','依据类型：' + evidenceKind,'source-meta'));
    if (evidence.legacy_id) card.append(element('p','原资料记录：' + evidence.legacy_id,'source-meta'));
    if (evidence.knowledge_record_id) card.append(element('p','知识记录：' + evidence.knowledge_record_id,'source-meta'));
    return card;
  }
  function renderFacts(facts) {
    $('facts').replaceChildren();
    const entries = facts && typeof facts === 'object' && !Array.isArray(facts) ? Object.entries(facts) : [];
    if (!entries.length) {$('facts').append(element('p','本查询没有可用于条件核对的结构化事实。','help'));return;}
    const table = element('table',null,'fact-table');
    const thead = element('thead');const head = element('tr');
    ['事实','原值 → 规范值','核验状态与角色','依据'].forEach(label => head.append(element('th',label)));
    thead.append(head);table.append(thead);
    const tbody = element('tbody');
    entries.forEach(([field,values]) => (Array.isArray(values) ? values : [values]).forEach(value => {
      const fact = value && typeof value === 'object' ? value : {raw_value:value,normalized_value:value};
      const row = element('tr');
      [factLabels[field] || field,text(fact.raw_value) + ' → ' + text(fact.normalized_value), (statusLabels[fact.verification_state] || fact.verification_state || '未核实') + ' / ' + (roleLabels[fact.role] || fact.role || '未声明'),text(fact.source_ref)].forEach(content => row.append(element('td',content)));
      tbody.append(row);
    }));
    table.append(tbody);$('facts').append(table);
  }
  function visibleNamespaces() {return asList(selected?.result?.namespaces).filter(ns => $('namespace-filter').value === 'all' || ns.namespace === $('namespace-filter').value);}
  function render() {
    const result = selected?.result;
    $('results').hidden = !result;
    $('download-result').disabled = !result;
    if (!result) return;
    const namespaces = visibleNamespaces();
    $('namespace-results').replaceChildren(...[...namespaces].sort((a,b) => asList(b.branches).length - asList(a.branches).length).map(namespaceView));
    $('result-highlights').replaceChildren();
    namespaces.filter(ns => asList(ns.branches).length).forEach(ns => {
      const box = element('div',null,'result-highlight');
      const label = namespaceIndex.get(ns.namespace)?.standard || ns.namespace;
      box.append(element('strong',label));
      box.append(element('span',asList(ns.branches).map(branch => branch.label + (branch.code ? '（' + branch.code + '）' : '')).join(' / ')));
      $('result-highlights').append(box);
    });
    const count = namespaces.reduce((sum,ns) => sum + asList(ns.branches).length,0);
    $('namespace-count').textContent = String(namespaces.length);
    $('branch-count').textContent = String(count);
    const missing = new Set(asList(result.missing_facts));
    $('missing-count').textContent = String(missing.size);
    const food = selected.food_name || result.food_name;
    const title = selected.title || food;
    $('query-title').textContent = title.includes(food) ? title : food + ' · ' + title;
    $('query-scope').textContent = '范围未完整 · 不自动判定';
    $('query-note').textContent = selected.note || '每套标准独立返回类别及路径；条件满足不等于版本、知识审核和任务依据全部就绪。';
    renderFacts(result.facts || selected.input?.facts || selected.facts);
    $('query-gaps').replaceChildren();
    missing.forEach(field => $('query-gaps').append(element('span','待补：' + (factLabels[field] || text(field)),'gap')));
    const ids = gatherEvidence(namespaces);
    namespaces.forEach(ns => asList(ns.branches).forEach(branch => gatherEvidence(findNode(ns.namespace,branch.category_id),ids)));
    $('source-list').replaceChildren(...evidenceRows.filter(row => ids.has(row.id)).map(row => sourceView(row.id,row.evidence)));
    $('source-count').textContent = ids.size + ' 条当前来源引用';
    $('result-json').textContent = JSON.stringify(result,null,2);
    document.querySelectorAll('.scenario-button').forEach(button => button.classList.toggle('active',button.dataset.exampleId === selected.id));
    document.querySelectorAll('.example-button').forEach(button => button.classList.toggle('active',button.dataset.foodName === (selected.food_name || result.food_name)));
  }
  const foods = [...new Set(examples.map(example => example.food_name || example.result?.food_name).filter(Boolean))];
  function selectExample(example) {selected = example;renderScenarios(example.food_name || example.result?.food_name);render();}
  function renderScenarios(food) {
    $('scenario-list').replaceChildren(element('span','已保存的事实场景：','scenario-label'));
    examples.filter(example => (example.food_name || example.result?.food_name) === food).forEach(example => {
      const button = element('button',example.scenario_label || example.title || example.id,'scenario-button');
      button.type = 'button';button.dataset.exampleId = example.id;
      button.addEventListener('click',() => {selected = example;render();});
      $('scenario-list').append(button);
    });
  }
  function filterExamples() {
    const query = $('food-search').value.trim();
    const matching = foods.filter(food => !query || food.includes(query));
    $('example-buttons').replaceChildren();
    matching.forEach(food => {
      const button = element('button',food,'example-button');button.type = 'button';button.dataset.foodName = food;
      button.addEventListener('click',() => selectExample(examples.find(example => (example.food_name || example.result?.food_name) === food)));
      $('example-buttons').append(button);
    });
    $('no-example').hidden = matching.length !== 0;
    $('scenario-list').hidden = matching.length === 0;
    if (!matching.length) {selected = null;$('results').hidden = true;$('source-list').replaceChildren();$('source-count').textContent = '尚未选择查询';$('download-result').disabled = true;$('namespace-count').textContent = '—';$('branch-count').textContent = '—';$('missing-count').textContent = '—';return;}
    const exact = examples.find(example => (example.food_name || example.result?.food_name) === query);
    if (exact) selectExample(exact);
    else if (!selected || !matching.includes(selected.food_name || selected.result?.food_name)) selectExample(examples.find(example => (example.food_name || example.result?.food_name) === matching[0]));
    else render();
  }
  function download(value,name) {
    const blob = new Blob([JSON.stringify(value,null,2)],{type:'application/json;charset=utf-8'});
    const url = URL.createObjectURL(blob);const link = element('a');
    link.href = url;link.download = name;document.body.append(link);link.click();link.remove();
    setTimeout(() => URL.revokeObjectURL(url),1000);
  }
  const namespaces = [...new Map(examples.flatMap(example => asList(example.result?.namespaces)).map(ns => [ns.namespace,ns])).values()];
  namespaces.forEach(ns => {const option = element('option',ns.label || namespaceIndex.get(ns.namespace)?.standard || namespaceIndex.get(ns.namespace)?.label || ns.namespace);option.value = ns.namespace;$('namespace-filter').append(option);});
  $('namespace-filter').addEventListener('change',render);
  $('food-search').addEventListener('input',filterExamples);
  $('download-result').addEventListener('click',() => {if(selected?.result) download(selected.result,'KB08-' + selected.id + '.json');});
  $('download-snapshot').addEventListener('click',() => download(snapshot,'KB08-分类快照.json'));
  document.addEventListener('click',event => {
    const link = event.target.closest('a.evidence-link');
    if (!link) return;
    const target = document.getElementById(link.getAttribute('href').slice(1));
    if (target) target.open = true;
  });
  $('snapshot-state').textContent = '快照：' + text(snapshot.snapshot_id) + ' · 来源目录：' + (statusLabels[snapshot.coverage_state] || snapshot.coverage_state || '部分覆盖') + ' · 本页没有审核或发布入口。';
  const fields = [['分类快照',snapshot.snapshot_id],['数据契约',snapshot.schema_version],['处理版本',snapshot.processor_version],['保存查询',examples.length],['来源文件',asList(snapshot.sources).length],['审核状态','真实知识待审核；工程核验不代替业务批准']];
  fields.forEach(([label,value]) => $('trace-data').append(element('dt',label),element('dd',value)));
  filterExamples();
  if(selected)renderScenarios(selected.food_name || selected.result?.food_name);
  document.documentElement.dataset.viewerReady = 'true';
})();

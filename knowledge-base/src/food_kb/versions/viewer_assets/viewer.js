'use strict';
(() => {
  const payload = JSON.parse(document.getElementById('viewer-data').textContent);
  const {snapshot, examples, source_files} = payload;
  const labels = {
    applicable:'条件满足', not_applicable:'有依据排除', unknown:'未知 / 待补依据', conflicting:'依据冲突',
    resolved:'已固定目标', complete:'展开完成', incomplete:'展开未完成', review_pending:'知识待审',
    unverified:'身份 / 声明未核', verified:'受控已核', approved:'已审核',
    production:'生产日期', sampling:'抽样日期', testing:'检验日期', task:'任务日期',
    publication:'发布日期', implementation:'实施日期', export:'导出日期',
    normative:'规范性', informative:'资料性', mention:'仅提及', replaces:'替代关系', amends:'修改关系',
    dated:'定年引用', undated:'不定年引用', whole:'全篇', clause:'条款', indicator:'指标',
    item:'指标 / 项目', not_dependency:'不作为规范依赖', available_candidate:'已取得待核主体', missing:'缺失',
    draft:'草案', body:'主体标准', added:'新增', removed:'移除关联', changed:'内容变更', recheck:'重新核查',
    original:'原件', amendment:'修改单', directory_claim:'目录声明',
    body_missing:'主体全文缺失', historical_version_missing:'历史版本缺失', source_missing:'来源缺失',
    missing_body:'主体全文缺失', missing_pages:'空文字页待补核（非PDF物理缺页）', unreadable_text:'文字乱码或不可靠',
    source_conflict:'来源声明冲突', unsupported_text:'文本无法无损表达，已隔离', version_policy_missing:'业务日期适用制度未核',
    identity_unverified:'版本身份未核', review_unfinished:'审核未完成', condition_unresolved:'条件未解',
    date_role_missing:'缺日期角色', version:'版本', classification:'分类', requirement:'要求', reference:'引用',
    gap:'缺口', rule:'规则', task_scope:'任务范围', cycle:'循环', budget_exhausted:'预算耗尽',
    REFERENCE_CONDITION_MISSING:'引用条件未登记', REFERENCE_CONDITION_UNKNOWN:'引用条件未知', REFERENCE_CONDITION_CONFLICTING:'引用条件冲突',
    REFERENCE_NATURE_UNVERIFIED:'引用性质待核', REFERENCE_SCOPE_UNVERIFIED:'引用范围待核', REFERENCE_BINDING_UNVERIFIED:'引用版本绑定待核',
    REFERENCE_SCOPE_UNKNOWN:'引用范围未明确', REFERENCE_REVIEW_PENDING:'引用仍待审核', REFERENCE_EVIDENCE_MISSING:'引用缺原文证据',
    REFERENCE_TARGET_MISSING:'引用目标资料缺失', REFERENCE_TARGET_BINDING_UNRESOLVED:'目标版本未唯一固定', REFERENCE_UNRESOLVED:'引用尚未闭合',
    DATED_REFERENCE_DESIGNATION_MISSING:'定年引用缺明确年份', DATED_REFERENCE_VERSION_MISMATCH:'定年引用与目标年份不符',
    UNDATED_REFERENCE_FAMILY_MISMATCH:'不定年引用与目标标准系列不符', UNDATED_REFERENCE_CONTEXT_MISSING:'不定年引用缺业务上下文',
    UNDATED_REFERENCE_CONTEXT_UNRESOLVED:'不定年引用未按上下文唯一选版', REFERENCE_DATEDNESS_UNKNOWN:'定年方式未知',
    REFERENCE_SCOPE_TARGET_MISSING:'缺少指定条款 / 指标定位', REFERENCE_TARGET_VERSION_MISSING:'目标版本未登记',
    REFERENCE_CYCLE:'循环引用已终止', NODE_BUDGET_EXHAUSTED:'节点预算耗尽', EDGE_BUDGET_EXHAUSTED:'关系预算耗尽', DEPTH_BUDGET_EXHAUSTED:'深度预算耗尽',
    VERSION_MISSING:'版本资料缺失', MENTION_OR_EVENT_NOT_NORMATIVE:'提及或变更事件不作为规范依赖', REFERENCE_NATURE_UNKNOWN:'引用性质未知',
    REFERENCE_CONDITION_FALSE:'有依据的引用条件不满足', reference_unresolved:'引用未解', identity_conflict:'来源身份冲突',
  };
  const byId = id => document.getElementById(id);
  const array = value => Array.isArray(value) ? value : [];
  const text = value => value == null ? '未提供' : typeof value === 'object' ? JSON.stringify(value) : String(value);
  const label = value => labels[value] || text(value);
  const objectKind = value => value==='task'?'审查任务':label(value);
  const node = (tag, cls, content) => {
    const el = document.createElement(tag); if(cls) el.className=cls;
    if(content != null) el.textContent=text(content); return el;
  };
  const badge = value => node('span','badge '+(value||'unknown'),label(value||'unknown'));
  const kv = entries => {
    const dl=node('dl','kv');
    entries.forEach(([name,value])=>{dl.append(node('dt','',name),node('dd','',value));}); return dl;
  };
  const summary = (parent,entries) => {
    const grid=node('div','summary-grid');
    entries.forEach(([name,value])=>{const box=node('div','summary-item');box.append(node('strong','',name),node('span','',value));grid.append(box);});
    parent.append(grid);
  };
  const issueText = issue => typeof issue === 'string' ? label(issue) :
    [issue.code||issue.kind,issue.reason||issue.message||issue.detail].filter(Boolean).map(label).join(' · ') || text(issue);
  const issues = (parent,values) => {
    const items=array(values);if(!items.length)return;
    const grouped=new Map();items.forEach(value=>{const message=issueText(value);grouped.set(message,(grouped.get(message)||0)+1);});
    const box=node('div','issues');for(const [message,count] of grouped)box.append(node('span','issue',message+(count>1?' × '+count+'处':'')));parent.append(box);
    if(grouped.size<items.length)details(parent,'查看'+items.length+'处理由的完整记录',items);
  };
  const details = (parent,title,value) => {
    const box=node('details','subsection');box.append(node('summary','',title),node('pre','',JSON.stringify(value,null,2)));parent.append(box);
  };
  const sourceAssets=new Map(array(snapshot.sources).map(s=>[s.asset_id||s.sha256,s]));
  const versionIndex=new Map(array(snapshot.versions).map(v=>[v.version_id,v]));
  const evidenceIndex = new Map();
  const evidenceLabel = evidence => {
    const loc=evidence.locator||{};const key=evidence.source_key||sourceAssets.get(evidence.source_asset_id||evidence.asset_id)?.source_key;
    return [key||evidence.source_ref||'来源依据',loc.pdf_page?'第'+loc.pdf_page+'页':null,
      evidence.json_pointer||loc.json_pointer||loc.clause||null].filter(Boolean).join(' · ');
  };
  const evidenceLinks = (parent,values) => {
    const list=array(values);if(!list.length)return;
    const box=node('div','evidence-links');
    list.forEach(value=>{
      if(!value||typeof value!=='object')return;
      const key=JSON.stringify(value);if(!evidenceIndex.has(key))evidenceIndex.set(key,{id:'evidence-'+evidenceIndex.size,value});
      const record=evidenceIndex.get(key);const link=node('a','evidence-link',evidenceLabel(value));link.href='#'+record.id;box.append(link);
    });parent.append(box);
  };
  const bodyLinks = (parent,assets) => {
    const box=node('div','source-actions body-actions');
    array(assets).forEach(asset=>{const source=sourceAssets.get(asset.asset_id);const key=asset.source_key||source?.source_key;const mapped=source_files[key];
      if(mapped?.status==='hash_verified'&&mapped.url){const link=node('a','','打开'+label(asset.role||'original')+' · '+key);link.href=mapped.url;link.target='_blank';link.rel='noopener';box.append(link);}
    });
    if(box.children.length)parent.append(box);
  };
  const collectEvidence = (parent,value) => {
    if(Array.isArray(value)){value.forEach(v=>collectEvidence(parent,v));return;}
    if(!value||typeof value!=='object')return;
    Object.entries(value).forEach(([key,child])=>{if(key==='evidence')evidenceLinks(parent,child);else if(typeof child==='object')collectEvidence(parent,child);});
  };
  const subsection = (parent,title) => {const box=node('section','subsection');box.append(node('h5','',title));parent.append(box);return box;};
  const kind = example => example.kind || (/reference|expansion/.test(example.result?.schema_version||'')?'references':/impact/.test(example.result?.schema_version||'')?'impact':'resolution');
  let selected=examples[0];let catalogLimit=30;

  function renderPolicies(parent,policies){
    const box=subsection(parent,'日期和适用制度依据');
    if(!array(policies).length){box.append(node('p','help','没有有来源的适用制度。发布日期、实施日或状态声明本身不能自动选版。'));return;}
    array(policies).forEach(policy=>{
      const card=node('div','policy');const head=node('div','heading');head.append(node('h5','',policy.policy_id||'适用制度'),badge(policy.status));card.append(head);
      const bounds=policy.bounds||{};const endpoint=(value,lower)=>value==null?'无'+(lower?'下':'上')+'界':text(value.value)+(value.inclusive?'（含当日）':'（不含当日）');
      card.append(kv([['日期角色',label(policy.date_role)],['日期判断',label(policy.date_result?.status||policy.date_result)],['适用区间',Object.keys(bounds).length?endpoint(bounds.from,true)+' 至 '+endpoint(bounds.until,false):'日期边界尚未核实']]));
      if(policy.condition_result){card.append(node('p','', '过渡 / 事实条件：'+label(policy.condition_result.status)));issues(card,policy.condition_result.issues);if(array(policy.condition_result.missing_facts).length)card.append(node('p','help','缺少事实：'+policy.condition_result.missing_facts.join('、')));}
      issues(card,policy.issues);evidenceLinks(card,policy.evidence);box.append(card);
    });
  }
  function renderResolution(result){
    const overview=byId('result-overview');const ctx=result.time_context||{};const dates=array(ctx.dates);
    const businessDates=dates.filter(d=>['production','sampling','testing','task'].includes(d.role));
    summary(overview,[['业务日期',businessDates.map(d=>text(d.value)).join('；')||ctx.value||ctx.date||'未知'],['日期角色',businessDates.map(d=>label(d.role)).join('；')||label(ctx.date_role||ctx.role||'unknown')],
      ['选中版本',versionIndex.get(result.selected_version_id)?.designation||result.selected_version_id||'未确定'],['可执行',result.executable===true?'是':'否 · 待审核 / 依据未闭合']]);
    if(dates.length)overview.append(kv(dates.map(d=>[label(d.role),text(d.raw_value||d.value)+' · '+label(d.verification_state||'unknown')])));
    const contextBox=node('details','subsection');contextBox.append(node('summary','','日期上下文、依据和原始值'),node('pre','',JSON.stringify(ctx,null,2)));overview.append(contextBox);
    evidenceLinks(overview,ctx.evidence);
    const cards=node('div','cards');
    array(result.candidates).forEach(candidate=>{
      const card=node('article','card version-candidate');card.dataset.versionId=candidate.version_id;
      const head=node('div','heading');head.append(node('h4','',candidate.designation||candidate.version_id),badge(candidate.status));card.append(head);
      const state=node('div','badges');state.append(badge(candidate.identity_state),badge(candidate.review_state));card.append(state);
      card.append(node('p','meta','版本标识：'+candidate.version_id));
      if(candidate.temporal_status||candidate.eligibility)card.append(kv([['时间条件',label(candidate.temporal_status||'unknown')],['资料类型',label(candidate.eligibility?.document_kind||'unknown')],['主体状态',label(candidate.eligibility?.body_state||'unknown')],['执行资格','未批准，不自动执行']]));
      renderPolicies(card,candidate.policies_checked);
      const conflicts=array(candidate.date_claim_conflicts);
      if(conflicts.length){const box=subsection(card,'相互冲突的日期声明');issues(box,conflicts);details(box,'查看全部冲突依据',conflicts);}
      if(array(candidate.gaps).length){const box=subsection(card,'保留为待核的缺口');issues(box,candidate.gaps);collectEvidence(box,candidate.gaps);}
      const sourceVersion=versionIndex.get(candidate.version_id);if(sourceVersion)evidenceLinks(card,sourceVersion.evidence);
      bodyLinks(card,candidate.body_assets||sourceVersion?.body_assets);bodyLinks(card,sourceVersion?.other_assets);
      const reason=subsection(card,'保留或排除理由');
      reason.append(node('p','',candidate.status==='not_applicable'?'有来源的日期或事实条件不满足，排除此候选。':candidate.status==='applicable'?'当前制度条件满足；身份、主体、审核和其他候选仍须分别核对。':candidate.status==='conflicting'?'存在冲突依据，保留双方，不任意选取一个。':'资料或条件未闭合，保留此候选，不能当作无要求。'));
      cards.append(card);
    });
    if(!cards.children.length)cards.append(node('p','empty','没有可供选择的版本记录；结果仍需查明资料缺口。'));
    byId('query-details').append(cards);
  }
  function referenceDimensions(parent,ref){
    parent.append(kv([['引用性质',label(ref.nature?.value||ref.nature)+' · '+label(ref.nature?.verification_state)],['引用范围',label(ref.scope?.value||ref.scope)+' · '+label(ref.scope?.verification_state)+(array(ref.scope?.refs).length?' · '+ref.scope.refs.join('、'):'')],
      ['年份绑定',label(ref.binding?.datedness||ref.datedness)],['目标版本',text(ref.target?.designation||ref.binding?.designation||ref.to_version_id||ref.target_version_id)],
      ['引用条件',label(ref.condition_result?.status||ref.condition?.state||'unknown')]]));
    if(array(ref.binding?.version_ids).length){const fixed=ref.binding.verification_state==='verified'&&ref.state==='applicable'&&!ref.candidate_only&&ref.binding.version_ids.length===1;parent.append(node('p','help',(fixed?'固定目标：':'目标候选：')+ref.binding.version_ids.map(id=>versionIndex.get(id)?.designation||id).join('、')));}
    if(array(ref.condition_result?.missing_facts).length)parent.append(node('p','help','待补事实：'+ref.condition_result.missing_facts.join('、')));
    evidenceLinks(parent,ref.evidence);collectEvidence(parent,{nature:ref.nature,scope:ref.scope,binding:ref.binding,condition:ref.condition});
  }
  function renderReferences(result){
    summary(byId('result-overview'),[['展开状态',label(result.status||result.state||result.coverage_state)],['已访问版本',array(result.visited_version_ids).map(id=>versionIndex.get(id)?.designation||id).join('、')||text(result.visited||result.nodes?.length||'未展开')],
      ['循环',array(result.cycles).length],['未展开项',array(result.unexpanded||result.unresolved).length]]);
    byId('result-overview').append(node('p','help',result.candidate_only?'候选探索：包含未核引用的线索，不能作为规范依赖或完整证据。':'规范依赖展开：引用性质、范围、年份、条件及审核逐项核对。'));
    const panel=node('section','panel');panel.append(node('h3','','引用展开轨迹'));
    const trace=array(result.trace||result.traces||result.edges||result.expanded);
    trace.forEach((item,index)=>{
      const row=node('article','trace-row');row.dataset.traceIndex=String(index);
      const badges=node('div','badges');badges.append(badge(item.status||item.state||item.action||'unknown'));if(item.candidate_only)badges.append(node('span','badge unknown','仅候选探索'));row.append(badges);
      const ref=item.reference||item.edge||item;
      const from=item.from_version_id||ref.from_version_id||item.from;const targets=array(ref.binding?.version_ids).map(id=>versionIndex.get(id)?.designation||id).join('、');
      row.append(node('div','path',text(versionIndex.get(from)?.designation||from)+' → '+text(item.target_version_id||item.to_version_id||item.target?.designation||ref.target?.designation||targets||item.to)));
      if(ref.reference_id)row.append(node('p','meta','引用标识：'+ref.reference_id));
      referenceDimensions(row,ref);issues(row,item.issues||item.reasons||item.reason_codes||(item.reason?[item.reason]:[]));
      details(row,'查看此步的完整记录',item);panel.append(row);
    });
    if(!trace.length)panel.append(node('p','empty','未展开关系；理由见未知、条件或资料缺口。'));
    for(const [name,key] of [['循环终止路径','cycles'],['未展开分支与原因','unexpanded'],['未解引用','unresolved']]){
      if(array(result[key]).length){const box=subsection(panel,name);array(result[key]).forEach(item=>{const row=node('article','trace-row');row.append(node('p','',issueText(item)));details(row,'查看路径 / 预算 / 目标',item);collectEvidence(row,item);box.append(row);});}
    }
    if(result.budget||result.budgets){const budget=result.budget||result.budgets;panel.append(kv([['深度预算',text(budget.max_depth)],['节点预算',text(budget.max_nodes)],['边预算',text(budget.max_edges)],['实际消耗',text(result.used)]]));details(panel,'查看完整资源预算',budget);}
    collectEvidence(panel,result.references);
    byId('query-details').append(panel);
  }
  function renderImpact(result){
    summary(byId('result-overview'),[['旧快照',result.old_snapshot_id||result.from_snapshot_id||'见完整结果'],['新快照',result.new_snapshot_id||result.to_snapshot_id||'见完整结果'],
      ['历史处理',result.old_history_unchanged===false?'待查历史变化':'旧结果保留，只列重新核查范围'],['发布 / 执行','不自动发布，不批准真实知识']]);
    const panel=node('section','panel');panel.append(node('h3','','变更与受影响对象'));
    for(const [title,value] of [['变更记录',result.changes],['受影响分类、要求、规则与任务',result.affected]]){
      const box=subsection(panel,title);
      if(Array.isArray(value)){box.append(node('p','help',value.length+'个已登记对象；未知范围仍需查明。'));value.forEach(item=>{const row=node('article','trace-row');row.append(node('p','',objectKind(item.kind)+' · '+text(item.id)+' · '+label(item.action)));details(row,'查看变更、依赖路径和重查原因',item);collectEvidence(row,item);box.append(row);});}
      else if(value&&typeof value==='object'){Object.entries(value).forEach(([key,items])=>{const values=array(items);const group=node('div','policy');group.append(node('h5','',label(key)+' · '+values.length+'项'));const list=node('ul','list');values.forEach(item=>list.append(node('li','',typeof item==='string'?item:text(item.id||item.version_id||item.classification_id||item.requirement_id||item))));group.append(list);if(values.length)details(group,'查看影响依据和完整标识',values);collectEvidence(group,values);box.append(group);});}
      else box.append(node('p','help','无已登记的变更 / 依赖对象；不能据此宣称全范围无影响。'));
    }
    if(result.unexpanded||result.unresolved)details(panel,'未展开影响范围',result.unexpanded||result.unresolved);
    if(array(result.trace).length){const box=subsection(panel,'来源依赖及影响路径');array(result.trace).forEach(step=>{const row=node('article','trace-row');row.append(node('div','path',objectKind(step.depends_on?.kind)+': '+text(step.depends_on?.id)+' → '+objectKind(step.dependent?.kind)+': '+text(step.dependent?.id)));evidenceLinks(row,step.evidence);box.append(row);});}
    byId('query-details').append(panel);
  }
  function renderEvidence(){
    const container=byId('evidence-list');container.replaceChildren();
    byId('evidence-count').textContent=evidenceIndex.size+'处当前场景证据';
    for(const {id,value} of evidenceIndex.values()){
      const card=node('details','source-card');card.id=id;card.append(node('summary','',evidenceLabel(value)));
      const loc=value.locator||{};const source=sourceAssets.get(value.source_asset_id||value.asset_id);const key=value.source_key||source?.source_key;
      card.append(node('p','meta','定位质量：'+text(value.locator_quality||loc.location_status||value.quote_kind||'来源声明')+' · 原件标识：'+text(value.source_asset_id||value.asset_id||source?.asset_id)));
      card.append(node('blockquote','',value.quote??value.raw_text??value.text??'此证据保存来源定位；未单独摘录逐字正文。'));
      const mapped=source_files[key];const actions=node('div','source-actions');
      if(mapped?.status==='hash_verified'&&mapped.url){const link=node('a','','打开已校验来源文件'+(loc.pdf_page?' · 第'+loc.pdf_page+'页':''));link.href=mapped.url+(loc.pdf_page?'#page='+loc.pdf_page:'');link.target='_blank';link.rel='noopener';actions.append(link);}
      else actions.append(node('span','help','该原件未随本结果包提供或未通过哈希核对，未生成文件链接。'));
      card.append(actions);container.append(card);
    }
    if(!evidenceIndex.size)container.append(node('p','empty','当前场景没有可列出的来源证据，仍需补齐依据。'));
  }
  function render(){
    if(!selected)return;
    const result=selected.result||{};evidenceIndex.clear();byId('results').hidden=false;
    byId('query-title').textContent=selected.title||selected.id;
    byId('query-note').textContent=[selected.note,selected.synthetic===true?'合成工程样例；受控输入只验证不变量。':'真实目录来源回放；未经专家确认。'].filter(Boolean).join(' ');
    byId('query-badges').replaceChildren(badge(result.status||result.state||result.coverage_state||'unknown'),badge('review_pending'));
    byId('result-overview').replaceChildren();byId('query-details').replaceChildren();byId('query-issues').replaceChildren();
    issues(byId('query-issues'),result.issues||result.gaps);
    const type=kind(selected);if(type==='references')renderReferences(result);else if(type==='impact')renderImpact(result);else renderResolution(result);
    if(selected.input)details(byId('result-overview'),'这次回放的实际输入（合成或来源声明状态仍需分别核对）',selected.input);
    collectEvidence(byId('query-issues'),result.time_context);
    byId('result-json').textContent=JSON.stringify(result,null,2);
    byId('trace-data').replaceChildren(...kv([['冻结快照',snapshot.snapshot_id],['来源处理器',snapshot.processor_version],['结果契约',result.schema_version],['算法',result.algorithm_version],['省略全局导航字段',payload.omitted_global_fields.join('、')],['完整结果','当前JSON下载与已保存结果完全相同']]).children);
    byId('trace-data').className='kv';renderEvidence();renderButtons();
  }
  function renderButtons(){
    const search=byId('scenario-search').value.trim().toLocaleLowerCase();const filter=byId('kind-filter').value;
    const filtered=examples.filter(example=>(filter==='all'||kind(example)===filter)&&[example.id,example.title,example.note,example.result?.designation,JSON.stringify(example.result?.candidates?.map(c=>c.designation)||[])].join(' ').toLocaleLowerCase().includes(search));
    const box=byId('example-buttons');box.replaceChildren();
    filtered.forEach(example=>{const button=node('button','example-button'+(selected?.id===example.id?' active':''),example.title||example.id);button.type='button';button.dataset.exampleId=example.id;button.onclick=()=>{selected=example;render();};box.append(button);});
    byId('no-example').hidden=filtered.length>0;byId('results').hidden=filtered.length===0;
    return filtered;
  }
  function catalog(){
    const query=byId('catalog-search').value.trim().toLocaleLowerCase();const values=array(snapshot.versions).filter(v=>[v.designation,v.title,v.family].join(' ').toLocaleLowerCase().includes(query));
    const box=byId('version-catalog');box.replaceChildren();byId('catalog-count').textContent=values.length+'个版本 · 当前展示'+Math.min(catalogLimit,values.length);
    values.slice(0,catalogLimit).forEach(version=>{
      const item=node('details','catalog-entry');item.dataset.versionId=version.version_id;item.append(node('summary','',version.designation+' · '+(version.title||'')));
      const states=node('div','badges');states.append(badge(version.identity_state),badge(version.review_state));if(version.is_amendment)states.append(badge('amendment'));item.append(states);
      item.append(kv([['版本标识',version.version_id],['主体载体',array(version.body_assets).length+'份（不表示身份已核）'],['日期声明',array(version.date_claims).map(c=>label(c.role)+': '+text(c.raw_value||c.value)+' · '+label(c.verification_state)).join('；')||'未登记'],['状态声明',array(version.status_claims).map(c=>text(c.value)+' · '+label(c.verification_state)).join('；')||'未登记']]));
      const gaps=array(snapshot.gaps).filter(g=>g.target?.id===version.version_id||array(g.affected_refs).some(ref=>ref.id===version.version_id));
      issues(item,gaps.map(g=>({code:g.kind,reason:g.reason})));if(gaps.length)details(item,'查看完整缺口和受影响对象',gaps);
      bodyLinks(item,version.body_assets);
      bodyLinks(item,version.other_assets);
      details(item,'查看版本目录的原始冻结记录',version);box.append(item);
    });
    if(values.length>catalogLimit){const more=node('button','', '继续显示30个版本');more.id='catalog-more';more.onclick=()=>{catalogLimit+=30;catalog();};box.append(more);}
    if(!values.length)box.append(node('p','empty','目录中未匹配；不代表该标准没有历史版或要求。'));
  }
  byId('download-result').onclick=()=>{if(!selected)return;const blob=new Blob([JSON.stringify(selected.result,null,2)],{type:'application/json;charset=utf-8'});const url=URL.createObjectURL(blob);const a=node('a');a.href=url;a.download=(selected.id||'KB09')+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  const onFilter=()=>{const values=renderButtons();if(values.length&&!values.some(v=>v.id===selected?.id)){selected=values[0];render();}};
  byId('scenario-search').oninput=onFilter;byId('kind-filter').onchange=onFilter;
  byId('catalog-search').oninput=()=>{catalogLimit=30;catalog();};
  document.addEventListener('click',event=>{const link=event.target.closest('.evidence-link');if(link){const target=byId(link.hash.slice(1));if(target)target.open=true;}});
  byId('snapshot-state').textContent='候选快照：'+snapshot.snapshot_id+' · '+snapshot.processor_version+' · 不可执行 / 范围未闭合';
  for(const [value,title] of [[array(snapshot.versions).length,'版本目录记录'],[snapshot.summary?.reference_count??snapshot.summary?.references??'见目录','引用候选'],[array(snapshot.gaps).length,'登记缺口']]){const stat=node('div','stat');stat.append(node('strong','',value),node('span','',title));byId('statistics').append(stat);}
  catalog();render();document.documentElement.dataset.viewerReady='true';
})();

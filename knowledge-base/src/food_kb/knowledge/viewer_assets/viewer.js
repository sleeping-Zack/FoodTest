/* Read-only offline candidate reader. Source HTML is reconstructed with an allowlist. */
"use strict";
(() => {
  const payload = JSON.parse(document.getElementById("viewer-data").textContent);
  const report = payload.report, records = report.records || [];
  const byId = new Map(records.map(r => [r.record_id, r]));
  const blocks = new Map((report.blocks || []).map(b => [b.block_id, b]));
  const chunks = new Map((report.chunks || []).map(c => [c.chunk_id, c]));
  const $ = id => document.getElementById(id);
  const text = (tag, value, cls) => { const node = document.createElement(tag); node.textContent = value; if (cls) node.className = cls; return node; };
  const stringify = value => value == null ? "" : typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
  const asList = value => value == null ? [] : Array.isArray(value) ? value : [value];
  const kindNames = {requirement:"要求",definition:"定义",method:"检验方法",practice:"生产经营规范",scope:"标准范围",standard_scope:"标准范围",standard_scope_catalog:"标准范围",standard:"标准范围",document:"标准范围",document_metadata:"标准范围",metadata:"标准范围"};
  const fieldNames = {name:"名称",term:"术语",definition:"定义内容",standard_number:"标准编号",standard_name:"标准名称",standard_title:"标准名称",standard_type:"标准类型",document_type:"文档类型",title:"标题",scope:"适用范围",application_scope:"适用范围",applicable_scope:"适用范围",food_category:"食品类别",analyte:"检验项目",project:"检验项目",item:"检验项目",item_name:"检验项目",property:"性质",requirement:"要求",comparator:"比较符",operator:"比较符",limit:"限值",limit_value:"限值",value:"值",unit:"单位",basis:"计量基准",basis_note:"计量限定",qualifier:"限定条件",qualifiers:"限定条件",method:"检验方法",method_reference:"方法引用",method_number:"方法编号",method_name:"方法名称",principle:"方法原理",reagents:"试剂和材料",apparatus:"仪器和设备",instruments:"仪器和设备",procedure:"分析步骤",steps:"分析步骤",formula:"计算公式",formulas:"计算公式",variables:"公式变量",calculation:"结果计算",precision:"精密度",precision_requirement:"精密度要求",tolerance:"允许差",safety:"安全提示",general_rules:"一般规定",conditions:"适用条件",test_conditions:"试验条件",note:"注释",notes:"注释",molecular_formula:"分子式",molecular_weight:"相对分子质量",relative_molecular_mass:"相对分子质量",issue_date:"发布日期",publication_date:"发布日期",effective_date:"实施日期",replacement:"替代标准",replaces:"替代标准",issuer:"发布机关",status:"状态"};
  const issueNames = {BUSINESS_REVIEW_REQUIRED:"尚待业务审核",REVIEW_REQUIRED:"尚待业务审核",REVIEW_PENDING:"尚待业务审核",UNRESOLVED_CONDITION:"适用条件尚待解释",CONDITION_UNRESOLVED:"适用条件尚待解释",UNRESOLVED_REFERENCE:"外部引用尚未完成匹配",REFERENCE_UNRESOLVED:"外部引用尚未完成匹配",EVIDENCE_MISSING:"缺少字段证据",MISSING_EVIDENCE:"缺少字段证据",UNKNOWN_STANDARD_TYPE:"标准类型尚待确认",NOT_APPROVED:"尚未批准",SOURCE_NOT_EXPERT_APPROVED:"来源尚待专家审核"};
  Object.assign(fieldNames,{subject:"适用对象",scope_text:"范围原文",requirement_type:"要求类型",expressed_as:"成分计量方式",equipment:"仪器和设备",submethods:"子方法",general_requirements:"通用试验要求",parameters:"试验参数",sections:"来源章节",namespaces:"分类体系及版本",regime:"适用制度",date_conditions:"时间适用条件",coverage_state:"标准覆盖状态",stage:"生产经营阶段",measure:"规定措施",target:"目标",responsible_party:"责任方",exceptions:"例外条件",general_standard_relation:"与通用标准的关系",matrix:"样品基质",components:"适用组分",sample_preparation:"样品制备",lod_loq:"检出限及定量限",required_evidence:"执行所需证据"});
  Object.assign(issueNames,{FIELD_UNRESOLVED:"字段尚未确认，需要核对来源",SCOPE_CATALOG_GAP:"标准范围目录信息尚不完整",SCOPE_COVERAGE_UNKNOWN:"尚未确认标准集合的覆盖范围",SCOPE_COVERAGE_PARTIAL:"目前只覆盖部分相关标准",EXPRESSED_AS_MISSING:"成分计量方式尚未完整保留",CONDITION_UNINTERPRETED:"条件文本尚未解释为可核对表达式",CONDITION_UNKNOWN:"适用条件仍有未知项",CONFLICT:"候选要求存在冲突，需要业务审核",REQUIREMENT_CONFLICT:"候选要求存在冲突，需要业务审核"});
  const standardNames={product:"产品标准",general:"通用标准",method:"检验方法标准",practice:"生产经营规范"};
  const valueNames={upper_limit:"上限要求",lower_limit:"下限要求",range:"范围要求",qualitative:"定性要求",use_restriction:"使用限制",formula:"公式要求",sampling_plan:"抽样方案",unknown:"尚未确定",partial:"部分覆盖",complete:"已记录完整覆盖",not_applicable:"不适用"};
  const factNames={product_identity:"产品身份",measurement_basis:"计量基准",food_name:"食品名称",food_species:"物种",food_part:"食品部位",process:"加工过程",physical_state:"物理状态",intended_use:"预期用途",population:"适用人群",production_stage:"生产阶段",regime:"适用制度",business_date:"业务日期",date_role:"日期含义",category_id:"食品分类",category_namespace:"分类体系及版本",matrix:"样品基质",project:"检验项目",component:"组分",sampling_plan:"抽样方案"};
  Object.assign(fieldNames,{projects:"项目线索",hazards:"危害类型",table_context:"技术要求上下文"});
  Object.assign(issueNames,{FIELD_LOCATOR_UNVERIFIED:"字段定位仍为人工转录的页码与行号声明，需核对原页后审核",DOCUMENT_IDENTITY_UNVERIFIED:"标准版本身份尚未核验",FIELD_CONFLICT:"同一项目和条件存在不相容要求，需审核冲突",RELATION_TARGET_UNKNOWN:"关联的候选知识尚未找到",METHOD_RELATION_UNRESOLVED:"检验方法关系尚待确认"});
  Object.assign(fieldNames,{submethods:"子方法与操作分支",references:"标准引用",method_checks:"需核对的方法条件",calculation_dependencies:"计算所引用的其他方法",role:"参数用途",label:"参数名称",lower:"下限",upper:"上限",source_method:"来源方法",target_method:"引用方法",symbol:"计算变量",formula_ids:"公式编号"});
  Object.assign(issueNames,{METHOD_SEMANTICS_PARTIAL:"仅整理了部分方法事实；完整适用性核对和公式复算尚未实现"});
  let selectedId = null, activeKind = records.some(r => r.candidate?.record_type === "requirement") ? "requirement" : "all", evidenceIndex = new Map();
  const candidate = record => record.candidate || {};
  const kind = record => candidate(record).record_type || "other";
  const kindName = record => kindNames[kind(record)] || "其他知识";
  const title = record => candidate(record).title || candidate(record).logical_key || record.record_id;
  const plain = value => stringify(value).replace(/<!--[\s\S]*?-->/g, "").replace(/<[^>]*>/g, "").replace(/[#$*_`|]/g, "").replace(/\s+/g, " ").trim();
  function issueText(value) {
    if (typeof value === "string") return issueNames[value.toUpperCase()] || value;
    if (!value || typeof value !== "object") return stringify(value);
    const translated=issueNames[String(value.code || "").toUpperCase()];
    return (translated || value.message || value.reason || stringify(value)) + (translated && value.field ? "（" + (fieldNames[value.field] || value.field) + "）" : "");
  }
  const pendingParts = record => [
    ...asList(record.validation?.issues),
    ...asList(record.validation?.publication_blockers).filter(i => !/^(BUSINESS_REVIEW_REQUIRED|REVIEW_REQUIRED|REVIEW_PENDING)$/.test(typeof i === "string" ? i : i?.code || "")),
    ...asList(candidate(record).condition?.unresolved),
    ...asList(candidate(record).references).filter(r => r && typeof r === "object" && (r.unresolved || ["unresolved", "pending", "review_pending", "not_resolved"].includes(r.state || r.status || r.resolution_state)))
  ];
  const md = window.markdownit({html:true,linkify:false,typographer:false,breaks:false});
  md.inline.ruler.before("escape", "inline_math", (state, silent) => {
    if (state.src[state.pos] !== "$" || state.src[state.pos + 1] === "$") return false;
    let end = state.pos + 1;
    while ((end = state.src.indexOf("$", end)) !== -1 && state.src[end - 1] === "\\") end++;
    if (end < 0 || end === state.pos + 1 || state.src.slice(state.pos + 1, end).includes("\n")) return false;
    if (!silent) state.push("inline_math", "", 0).content = state.src.slice(state.pos + 1, end);
    state.pos = end + 1; return true;
  });
  md.block.ruler.before("fence", "block_math", (state, start, end, silent) => {
    const first = state.src.slice(state.bMarks[start] + state.tShift[start], state.eMarks[start]).trim();
    if (!first.startsWith("$$")) return false;
    if (silent) return true;
    let body = first.slice(2), next = start + 1;
    if (body.endsWith("$$") && body.length >= 2) body = body.slice(0, -2);
    else {
      const lines = body ? [body] : []; let closed = false;
      for (; next < end; next++) {
        const line = state.src.slice(state.bMarks[next] + state.tShift[next], state.eMarks[next]);
        if (line.trim() === "$$") { next++; closed = true; break; } lines.push(line);
      }
      if (!closed) return false; body = lines.join("\n");
    }
    const token = state.push("block_math", "", 0); token.content = body; token.map = [start, next]; state.line = next; return true;
  }, {alt:["paragraph","reference","blockquote","list"]});
  md.renderer.rules.inline_math = (tokens, i) => `<span data-math="${md.utils.escapeHtml(tokens[i].content)}"></span>`;
  md.renderer.rules.block_math = (tokens, i) => `<span data-math="${md.utils.escapeHtml(tokens[i].content)}" data-display="true"></span>`;
  const allowed = new Set(["P","BR","STRONG","EM","S","DEL","UL","OL","LI","BLOCKQUOTE","PRE","CODE","H1","H2","H3","H4","H5","H6","HR","TABLE","THEAD","TBODY","TFOOT","TR","TH","TD","CAPTION","SUP","SUB","SPAN","A","IMG"]);
  function cleanNode(node) {
    if (node.nodeType === Node.TEXT_NODE) return document.createTextNode(node.textContent);
    if (node.nodeType !== Node.ELEMENT_NODE || !allowed.has(node.tagName)) return document.createDocumentFragment();
    const copy = document.createElement(node.tagName.toLowerCase());
    if (node.tagName === "IMG") {
      const source = payload.images[node.getAttribute("src")];
      if (!source) return document.createTextNode(node.getAttribute("alt") ? `[图片：${node.getAttribute("alt")}，请查看原件]` : "[图片请查看原件]");
      copy.src = source; copy.alt = node.getAttribute("alt") || "原文图片"; copy.loading = "lazy";
    }
    if (node.tagName === "A") { const href = node.getAttribute("href") || ""; if (href.startsWith("#") && !href.includes("\u0000")) copy.href = href; }
    if (node.tagName === "TH" || node.tagName === "TD") {
      for (const attr of ["rowspan","colspan"]) {
        const value = node.getAttribute(attr); if (value && /^\d{1,3}$/.test(value) && Number(value) > 0 && Number(value) <= 100) copy.setAttribute(attr, value);
      }
    }
    if (node.tagName === "SPAN" && node.hasAttribute("data-math")) { copy.dataset.math = node.getAttribute("data-math"); if (node.getAttribute("data-display") === "true") copy.dataset.display = "true"; }
    for (const child of node.childNodes) copy.append(cleanNode(child)); return copy;
  }
  function renderMarkdown(value, target, inline = false) {
    const inert = document.createElement("template"); inert.innerHTML = inline ? md.renderInline(stringify(value)) : md.render(stringify(value));
    target.replaceChildren(...Array.from(inert.content.childNodes, cleanNode));
    for (const formula of target.querySelectorAll("[data-math]")) {
      const expression = formula.dataset.math, display = formula.dataset.display === "true"; formula.className = display ? "math-display" : "math-inline";
      window.katex.render(expression, formula, {displayMode:display,throwOnError:false,trust:false,strict:"ignore",maxExpand:1000}); delete formula.dataset.math; delete formula.dataset.display;
    }
    for (const table of target.querySelectorAll("table")) { const wrapper = text("div", "", "table-scroll"); table.replaceWith(wrapper); wrapper.append(table); }
  }
  const fieldObject = value => value && typeof value === "object" && !Array.isArray(value) && ("raw" in value || "value" in value || "evidence" in value) ? value : {raw:value,value:value,evidence:[]};
  function orderedFields(record) {
    const orders={
      requirement:["subject","project","requirement_type","comparator","value","limit_value","unit","basis","expressed_as","method_reference","table_context"],
      method:["method_name","scope","submethods","parameters","calculation_dependencies","method_checks","precision","formulas","principle","reagents","equipment","steps","general_requirements","sections"],
      definition:["subject","definition"],
      standard_scope_catalog:["subject","scope_text","projects","hazards","namespaces","regime","date_conditions","coverage_state"]
    };
    const order=orders[kind(record)] || [];
    return Object.entries(candidate(record).fields || {}).sort(([a],[b])=>(order.includes(a) ? order.indexOf(a) : order.length)-(order.includes(b) ? order.indexOf(b) : order.length));
  }
  function sourceReference(item,target) {
    const block=blocks.get(item.block_id || item.source_block_id), details=document.createElement("details");
    details.className="source-reference";details.dataset.blockId=item.block_id || item.source_block_id || "";
    const valid=block && (!item.anchor || item.anchor===block.key) && (!item.title || item.title===block.title);
    const anchor=item.anchor || block?.key,heading=item.title || block?.title;
    details.append(text("summary",heading && anchor && heading.startsWith(anchor) ? heading : [anchor,heading].filter(Boolean).join(" · ") || "查看对应原文"));
    const content=text("div","","markdown");details.append(content);
    details.addEventListener("toggle",()=>{
      if(!details.open || content.childElementCount) return;
      if(valid) {renderMarkdown(block.text_raw || "此块只保留章节标题，请展开下级来源。",content);content.append(text("span",locationText(block.source),"source-location"));const actions=text("div","","source-actions");sourceButtons(block.source || {},actions);content.append(actions);}
      else content.append(text("p","来源引用缺失或不一致，请核对完整记录；未套用其他原文。","value-empty"));
    });target.append(details);
  }
  function semanticTable(value,target,name) {
    const labels=name==="parameters" ? ["用途 / 参数","值与单位","对应原文"] : name==="calculation_dependencies" ? ["变量 / 公式","引用关系","对应原文"] : ["核对内容","对应原文"];
    const wrapper=text("div","","table-scroll"),table=document.createElement("table"),head=document.createElement("thead"),headRow=document.createElement("tr"),body=document.createElement("tbody");
    for(const label of labels)headRow.append(text("th",label));head.append(headRow);table.append(head);
    for(const item of value){
      const row=document.createElement("tr");
      if(name==="parameters") {row.append(text("td",item.label || item.role || "用途待核"));let amount=item.value ?? (item.lower!=null || item.upper!=null ? [item.lower,item.upper].join("～") : "待核");row.append(text("td",`${item.comparator || ""} ${amount} ${item.unit || ""}`.trim()));}
      else if(name==="calculation_dependencies") row.append(text("td",[item.symbol,...asList(item.formula_ids)].filter(Boolean).join(" · ")),text("td",`${item.source_method || ""} → ${item.target_method || ""}`));
      else row.append(text("td",item.label || item.role || item.check || "核对条款"));
      const origin=document.createElement("td");if(item.raw){const raw=text("div","","parameter-raw");renderMarkdown(item.raw,raw);origin.append(raw);}sourceReference(item,origin);row.append(origin);body.append(row);
    }table.append(body);wrapper.append(table);target.append(wrapper);
  }
  function renderValue(value,target,name) {
    if(value == null || value === "") {target.append(text("span","未提供","value-empty"));return;}
    if(typeof value !== "object") {renderMarkdown(["requirement_type","coverage_state","standard_type"].includes(name) ? (valueNames[value] || standardNames[value] || value) : value,target);return;}
    if(Array.isArray(value)) {
      if(!value.length) {target.append(text("span","空列表 · 请结合字段状态核对","value-empty"));return;}
      if(["parameters","method_checks","calculation_dependencies"].includes(name) && value.every(item=>item && typeof item==="object" && (item.role || item.source_block_id))) semanticTable(value,target,name);
      else if(value.every(item => item && typeof item === "object" && "block_id" in item)) for(const item of value) sourceReference(item,target);
      else if(value.every(item => item && typeof item === "object" && ("text" in item || "text_raw" in item))) {
        for(const item of value) {
          const section=document.createElement("details");section.className="structured-section legacy-reference";section.append(text("summary",[item.anchor,item.title].filter(Boolean).join(" · ") || "展开保存的原文"));
          const content=text("div","","markdown");section.addEventListener("toggle",()=>{if(section.open && !content.childElementCount)renderMarkdown(item.text || item.text_raw || "",content);});section.append(content);target.append(section);
        }
      } else if(value.every(item => item && typeof item === "object" && "raw" in item)) {
        const wrapper=text("div","","table-scroll"),table=document.createElement("table"),head=document.createElement("thead"),labels=document.createElement("tr");
        for(const label of ["原文参数","来源位置","解释状态"]) labels.append(text("th",label));head.append(labels);table.append(head);
        const body=document.createElement("tbody");
        for(const item of value) {
          const row=document.createElement("tr"),raw=document.createElement("td");renderMarkdown(item.raw,raw,true);
          row.append(raw,text("td",item.anchor || "待定位"),text("td",item.interpretation === "literal_parameter_candidate" ? "原文参数候选，待审核" : item.interpretation || "待审核"));body.append(row);
        }
        table.append(body);wrapper.append(table);target.append(wrapper);
      } else {
        const list=document.createElement("ul");for(const item of value){const entry=document.createElement("li");renderValue(item,entry,name);list.append(entry);}target.append(list);
      }
    } else if(value.text || value.text_raw) renderMarkdown(value.text || value.text_raw,target);
    else {
      const list=text("dl","","structured-object");
      for(const [key,item] of Object.entries(value)) {
        list.append(text("dt",fieldNames[key] || factNames[key] || key));
        const description=document.createElement("dd");renderValue(item,description,key);list.append(description);
      }
      target.append(list);
    }
  }
  const evidenceKey = evidence => JSON.stringify([evidence?.block_id, evidence?.quote, evidence?.source]);
  function recordEvidence(record) {
    const result = [], seen = new Set();
    const sources=[...orderedFields(record).map(([,field])=>fieldObject(field)),candidate(record).condition,
      ...asList(candidate(record).condition?.unresolved),...asList(candidate(record).references),...asList(candidate(record).relations)];
    for (const field of sources) for (const item of asList(field?.evidence)) {
      if (!item || typeof item !== "object") continue;
      const key = evidenceKey(item); if (!seen.has(key)) { result.push(item); seen.add(key); }
    }
    return result;
  }
  function sourceOf(item) { return item?.source || blocks.get(item?.block_id)?.source || {}; }
  function pageNumbers(source) {
    const values = source?.pdf_pages || (source?.pdf_page ? [source.pdf_page] : []);
    return [...new Set(values.filter(n => Number.isInteger(n) && n > 0))];
  }
  function recordPages(record) { return [...new Set(recordEvidence(record).flatMap(e => pageNumbers(sourceOf(e))))].sort((a,b) => a-b); }
  function showPage(number) {
    const page = payload.pages[String(number)]; if (!page?.image) return;
    $("page-title").textContent = `原件 · PDF 第 ${number} 页`;
    $("printed-page").textContent = page.printed_page == null ? "此页未标印刷页码" : `印刷页码 ${page.printed_page}`;
    $("page-image").src = page.image; $("page-image").alt = `原件 PDF 第 ${number} 页`;
    $("dialog-pdf").hidden = !payload.pdf; if (payload.pdf) $("dialog-pdf").href = `${payload.pdf}#page=${number}`;
    $("page-dialog").showModal();
  }
  function sourceButtons(source, target) {
    for (const number of pageNumbers(source)) {
      const button = text("button", `PDF 第 ${number} 页`, "page-button"); button.type = "button"; button.dataset.pdfPage = number;
      if (payload.pages[String(number)]?.image) button.addEventListener("click", () => showPage(number));
      else { button.disabled = true; button.title = "请打开原 PDF 核对，该页图未提供"; }
      target.append(button);
    }
    if (payload.pdf) { const link = text("a", "打开原 PDF ↗", "secondary-button"); link.href = `${payload.pdf}#page=${pageNumbers(source)[0] || 1}`; link.target = "_blank"; link.rel = "noopener noreferrer"; target.append(link); }
  }
  function locationText(source) {
    const pages = pageNumbers(source), lines = source.line_start ? `转录第 ${source.line_start}–${source.line_end || source.line_start} 行` : "行号未提供";
    return `${pages.length ? `PDF 第 ${pages.join("、")} 页 · ` : ""}${lines}`;
  }
  function focusEvidence(index) {
    const card = document.querySelector(`.evidence-card[data-evidence-index="${index}"]`); if (!card) return;
    for (const item of $("evidence-list").querySelectorAll(".evidence-card")) item.classList.remove("highlight"); card.classList.add("highlight");
    for(let parent=card.parentElement;parent && parent!==$("evidence-list");parent=parent.parentElement) if(parent.tagName==="DETAILS") parent.open=true;
    card.scrollIntoView({behavior:"smooth",block:"center"}); card.tabIndex = -1; card.focus({preventScroll:true});
  }
  function renderFields(record) {
    const target = $("fields"); target.replaceChildren();
    const method=kind(record)==="method", essential=new Set(["method_name","scope","submethods","method_checks","parameters","calculation_dependencies"]);
    const additional=document.createElement("details");additional.className="additional-fields";additional.append(text("summary","其他方法原文引用与完整字段"));
    if(method) {
      const coverage=candidate(record).semantic_coverage, panel=text("div","","method-capabilities");panel.id="method-capabilities";
      panel.append(text("strong","方法语义整理 · 部分完成"));
      panel.append(text("p",coverage?.note || "完整方法正文仍在原文中；本页不表示全部实验条件已规范化。"));
      panel.append(text("p","方法条件仅原文关联；公式与变量保留来源，尚未实现公式复算。"));
      target.append(panel);
    }
    for (const [name, input] of orderedFields(record)) {
      const field = fieldObject(input), card = text("section", "", "field"); card.dataset.field = name;
      const head = text("div", "", "field-top"); head.append(text("h4", fieldNames[name] || name), text("span", name, "field-name")); card.append(head);
      const values = text("div", "", "field-values");
      const referenced=method && (Array.isArray(field.value) || ["method_name","scope"].includes(name));
      if(referenced)values.classList.add("reference-values");
      for (const [label, value] of referenced ? [["结构化事实 / 原文引用",field.value]] : [["原文原值",field.raw],["规范值",field.value]]) {
        const cell = text("div", ""); cell.append(text("span", label, "value-label"));
        const content = text("div", "", "value-text");
        if ((value == null || value === "") && field.state === "not_specified") content.append(text("span","原文未单列此项，需核对适用性","value-empty"));
        else if(method && name==="parameters" && Array.isArray(value) && !value.length)content.append(text("span","其他参数仍在原文中，本轮未规范化。","value-empty"));
        else renderValue(value,content,label === "原文原值" ? "" : name);
        cell.append(content); values.append(cell);
      }
      card.append(values);
      if (field.normalization_reason) { const note = text("div", "", "normalization-note"); note.append(text("strong", "规范化说明："), document.createTextNode(stringify(field.normalization_reason))); card.append(note); }
      const actions = text("div", "", "evidence-actions");
      for (const item of asList(field.evidence)) {
        const index = evidenceIndex.get(evidenceKey(item)); if (index == null) continue;
        const button = text("button", `核对证据 ${index + 1}`, "evidence-link"); button.type = "button"; button.dataset.evidenceIndex = index;
        button.addEventListener("click", () => focusEvidence(index)); actions.append(button);
      }
      if (!actions.childElementCount) actions.append(text("span", "此字段未提供独立证据，需核对来源。", "help-text"));
      card.append(actions); (method && (!essential.has(name) || field.state==="not_specified") ? additional : target).append(card);
    }
    if(additional.querySelector(".field"))target.append(additional);
    if (!target.childElementCount) target.append(text("p", "此记录尚未提供结构化字段。", "empty"));
  }
  function renderEvidence(record) {
    const entries = recordEvidence(record), target = $("evidence-list"); target.replaceChildren(); evidenceIndex = new Map(entries.map((e,i) => [evidenceKey(e),i]));
    $("evidence-count").textContent = `${entries.length} 段引文`;
    const group=document.createElement("details");group.className="method-evidence";group.append(text("summary",`展开 ${entries.length} 段字段来源引文`));
    const holder=kind(record)==="method" ? group : target;if(holder===group)target.append(group);
    for (const [i, entry] of entries.entries()) {
      const block = blocks.get(entry.block_id), source = sourceOf(entry), card = text("section", "", "evidence-card"); card.dataset.evidenceIndex = i;
      const head = text("div", "", "evidence-head"); head.append(text("strong", `证据 ${i+1}`), text("span", locationText(source))); card.append(head);
      card.append(text("div", "字段依据 · 保留原文", "quote-label"));
      const quote = text("div", "", "markdown evidence-quote"); renderMarkdown(entry.quote || "此定位未提供引文，请核对完整来源段。", quote); card.append(quote);
      const buttons = text("div", "", "source-actions"); sourceButtons(source, buttons); card.append(buttons);
      if (block) {
        const details = document.createElement("details"); details.className = "source-block"; details.dataset.blockId = block.block_id;
        const summary = text("summary", `展开完整来源段 · ${plain(block.title || block.key || block.block_id)}`); details.append(summary);
        const content = text("div", "", "markdown"); details.append(content, text("span", locationText(block.source), "source-location"));
        details.addEventListener("toggle", () => { if (details.open && !content.childElementCount) renderMarkdown(block.text_raw || "仅章节标题，内容见关联原文。", content); }); card.append(details);
      }
      holder.append(card);
    }
    if (!entries.length) target.append(text("p", "此记录未提供字段引文，请从下方关联原文核对。", "empty"));
    const context = $("chunk-context"); context.replaceChildren();
    for (const id of candidate(record).source_chunk_ids || []) {
      const chunk = chunks.get(id); if (!chunk) continue;
      const details = document.createElement("details"); details.className = "chunk-card"; details.dataset.chunkId = id;
      details.append(text("summary", chunk.title || chunk.anchor || id)); const body = text("div", "", "markdown"); details.append(body);
      details.addEventListener("toggle", () => { if (details.open && !body.childElementCount) renderMarkdown(chunk.raw_markdown || chunk.retrieval_text || "", body); });
      const actions = text("div", "", "source-actions"); sourceButtons(chunk.source || {}, actions); details.append(actions); context.append(details);
    }
    if (!context.childElementCount) context.append(text("p", "没有附带可展开的切分原文。", "help-text"));
  }
  function renderCondition(record) {
    const condition = candidate(record).condition || {}, panel = $("condition-panel"); panel.replaceChildren(text("h4", "适用条件"));
    const raw = text("div", "", "condition-raw"); renderMarkdown(condition.raw || "未提供独立条件文本，需结合标准范围核对。", raw); panel.append(raw);
    const unresolved = asList(condition.unresolved).filter(Boolean);
    panel.append(text("span", unresolved.length ? `${unresolved.length} 项条件待解释` : "条件已记录 · 尚待业务审核", "condition-state"));
    if (unresolved.length) { const list = document.createElement("ul"); for (const item of unresolved) list.append(text("li", issueText(item))); panel.append(list); }
    if (asList(condition.required_facts).length) panel.append(text("p", "应用前需要的事实：" + asList(condition.required_facts).map(value => factNames[value] || stringify(value)).join("；")));
    if (condition.ast) { const details = document.createElement("details"); details.append(text("summary", "查看条件表达式")); const pre = text("pre", stringify(condition.ast)); details.append(pre); panel.append(details); }
  }
  function renderReferences(record) {
    const refs = asList(candidate(record).references), panel = $("references-panel"); panel.replaceChildren(text("h4", "引用关系")); panel.hidden = !refs.length;
    for (const ref of refs) {
      const item = text("div", "", "reference-item"); item.append(text("strong", typeof ref === "object" ? ref.raw || ref.text || ref.standard_number || ref.label || ref.reference || ref.target || "引用" : stringify(ref)));
      if (ref && typeof ref === "object") {
        const state = ref.state || ref.status || ref.resolution_state;
        item.append(text("span", ref.unresolved || ["unresolved", "pending", "review_pending", "not_resolved"].includes(state) ? "待解析引用 · 尚不能确认目标版本及适用关系" : (state === "resolved" ? "引用目标已记录 · 尚待业务审核" : "引用已保留 · 解析情况见完整记录"), "reference-state"));
        if (ref.reason || ref.message) item.append(text("p", ref.reason || ref.message));
        for(const evidence of asList(ref.evidence)) {
          const index=evidenceIndex.get(evidenceKey(evidence));if(index==null) continue;
          const button=text("button","核对引用证据 " + (index+1),"evidence-link");button.type="button";button.addEventListener("click",()=>focusEvidence(index));item.append(button);
        }
      }
      panel.append(item);
    }
    const relations = asList(candidate(record).relations), target = $("relations-panel"); target.replaceChildren(text("h4", "关联知识")); target.hidden = !relations.length;
    for (const relation of relations) {
      const id = relation?.target_record_id || relation?.record_id || relation?.target_id, linked = byId.get(id) || records.find(record => candidate(record).logical_key === relation?.target_key);
      if (linked) { const button = text("button", title(linked), "evidence-link"); button.type = "button"; button.addEventListener("click", () => { activeKind = "all"; $("search").value = ""; $("issues-only").checked = false; selectedId = id; renderList(); }); target.append(button); }
      else target.append(text("p", typeof relation === "object" ? `${relation.role || relation.kind || relation.type || "关联"}：${relation.target_key || relation.target_logical_key || relation.logical_key || id || stringify(relation)}` : stringify(relation)));
    }
  }
  function renderStatus(record) {
    const passed = record.validation?.passed === true, state = record.review_state || "review_pending";
    $("review-state").textContent = {review_pending:"待业务审核",approved:"已有批准记录",rejected:"已有驳回记录",needs_revision:"待修订"}[state] || `审核状态：${state}`;
    $("machine-state").classList.toggle("warn", !passed); $("machine-state").replaceChildren(text("strong", passed ? "机器校验 · 通过" : "机器校验 · 需处理"), text("p", "检查字段结构、原文证据与条件表达；业务含义由审核确认。"));
    $("business-state").classList.toggle("warn", state !== "approved"); $("business-state").replaceChildren(text("strong", state === "approved" ? "业务审核 · 已记录" : "业务审核 · 待处理"), text("p", candidate(record).executable === false ? "当前记录不可用于自动判定。" : "是否可用于判定，请以发布服务的状态检查为准。"));
    const issues = [...asList(record.validation?.issues), ...asList(record.validation?.publication_blockers)], unique = [...new Map(issues.map(i => [issueText(i), i])).values()];
    const panel = $("blockers-panel"); panel.replaceChildren(); panel.hidden = !unique.length;
    if (unique.length) { panel.append(text("strong", "启用前需要处理")); const list = document.createElement("ul"); for (const issue of unique) list.append(text("li", issueText(issue))); panel.append(list); }
  }
  function selectRecord(id) {
    const record = byId.get(id); if (!record) return; selectedId = id;
    for (const item of $("record-list").children) { const active = item.dataset.recordId === id; item.classList.toggle("active", active); if (active) item.setAttribute("aria-current", "true"); else item.removeAttribute("aria-current"); }
    $("kind-label").textContent = `${kindName(record)} / ${standardNames[candidate(record).standard_type] || candidate(record).standard_type || "标准类型待确认"}`;
    renderMarkdown(title(record), $("record-title"), true); $("record-version").textContent = `候选修订 ${record.revision ?? 1}`; $("copy-status").textContent = "";
    renderStatus(record); renderEvidence(record); renderFields(record); renderCondition(record); renderReferences(record);
    const trace = $("trace-data"); trace.replaceChildren();
    for (const [label,value] of [["记录标识",record.record_id],["逻辑位置",candidate(record).logical_key],["抽取批次",report.extraction_id],["切分批次",report.chunk_run_id],["标准编号",report.document_metadata?.designation || report.source?.document_id],["登记状态（待核验）",report.document_metadata?.status_claim],["登记发布日期",report.document_metadata?.publication_date],["登记实施日期",report.document_metadata?.implementation_date],["原件指纹",report.source?.asset_id],["转录指纹",report.source?.markdown_asset_id],["知识契约",candidate(record).schema_version]]) trace.append(text("dt",label),text("dd",stringify(value) || "未提供"));
    $("record-json").textContent = JSON.stringify(record,null,2);
    try { history.replaceState(null,"",`#${encodeURIComponent(id)}`); } catch (_) { /* Local hosts may restrict history. */ }
  }
  function renderList() {
    const query = $("search").value.trim().toLocaleLowerCase();
    const matches = records.filter(record => (activeKind === "all" || kind(record) === activeKind) && (!$("issues-only").checked || pendingParts(record).length) && (!query || JSON.stringify(record).toLocaleLowerCase().includes(query)));
    $("list-count").textContent = `显示 ${matches.length} / ${records.length} 条`; $("empty-list").hidden = !!matches.length;
    $("clear-search").hidden = !query && activeKind === "all" && !$("issues-only").checked; const target = $("record-list"); target.replaceChildren();
    for (const record of matches) {
      const item = text("button","","record-item"); item.type = "button"; item.dataset.recordId = record.record_id;
      const top = text("span","","item-top"), pages = recordPages(record); top.append(text("span",kindName(record)),text("span",pages.length ? `第 ${pages.join("、")} 页` : "来源待定位")); item.append(top);
      const heading = text("span","","item-title"); renderMarkdown(title(record),heading,true); item.append(heading);
      const inputs=candidate(record).fields || {}, keys=kind(record)==="requirement" ? ["value","unit","basis","method_reference"] : ["subject","method_name","definition"];
      const facts = keys.filter(key=>inputs[key]).map(key=>plain(fieldObject(inputs[key]).value ?? fieldObject(inputs[key]).raw)).filter(Boolean);
      item.append(text("span",facts.join(" · ").slice(0,130),"item-excerpt"));
      const count = pendingParts(record).length; if (count) item.append(text("span",`${count} 项待解决信息`,"item-issue")); item.addEventListener("click",() => selectRecord(record.record_id)); target.append(item);
    }
    for (const filter of $("filters").children) filter.classList.toggle("active",filter.dataset.kind === activeKind);
    if (matches.length && !matches.some(r => r.record_id === selectedId)) selectRecord(matches[0].record_id); else if (selectedId) selectRecord(selectedId);
  }
  const factValue=(record,name)=>{const field=fieldObject(candidate(record).fields?.[name]);return field.value!=null && field.value!=="" ? plain(field.value) : field.state==="not_specified" ? "原文未单列" : "待核";};
  function openRecord(id) {
    if(!byId.has(id))return;
    activeKind=kind(byId.get(id));$("search").value="";$("issues-only").checked=false;selectedId=id;renderList();$("selected").scrollIntoView({behavior:"smooth",block:"start"});
  }
  function renderFacts() {
    const facts=records.filter(r=>kind(r)==="requirement"),body=$("facts-table").querySelector("tbody");$("fact-count").textContent=`${facts.length} 条`;
    for(const record of facts){
      const row=document.createElement("tr");row.dataset.recordId=record.record_id;
      const project=document.createElement("td"),button=text("button",factValue(record,"project"),"fact-record-link");button.type="button";button.addEventListener("click",()=>openRecord(record.record_id));project.append(button);row.append(project);
      const comparison=factValue(record,"comparator"),value=factValue(record,"value")==="待核" ? factValue(record,"limit_value") : factValue(record,"value");
      row.append(text("td",(comparison==="原文未单列" || comparison==="待核" ? "" : comparison+" ")+value));
      for(const name of ["unit","basis","expressed_as","method_reference"])row.append(text("td",factValue(record,name)));
      row.append(text("td",record.review_state==="approved" ? "已有批准记录" : "待业务审核","fact-review-state"));body.append(row);
    }
  }
  function renderExamples() {
    const examples=payload.fact_examples || report.fact_examples || [];
    if(!examples.length)return;$("fact-examples").hidden=false;
    for(const example of examples){
      const button=text("button",example.label || example.question,"example-button");button.type="button";button.dataset.exampleId=example.id;
      button.addEventListener("click",()=>{
        for(const item of $("example-buttons").children)item.classList.toggle("active",item===button);
        const result=example.result || {},answer=$("example-answer");answer.hidden=false;answer.replaceChildren(text("strong",example.question || example.label));
        const matches=asList(result.records);answer.append(text("p",matches.length ? `找到 ${matches.length} 条候选 · 样品适用性仍待核实` : "当前快照未命中候选；不能据此判定没有要求。","example-state"));
        for(const match of matches){
          const record=byId.get(match.record_id);if(!record)continue;
          const card=text("div","","example-record"),link=text("button",`${factValue(record,"basis")==="待核" || factValue(record,"basis")==="原文未单列" ? "" : factValue(record,"basis")+" · "}${title(record)}`,"evidence-link");link.type="button";link.dataset.recordId=record.record_id;link.addEventListener("click",()=>openRecord(record.record_id));card.append(link);
          if(kind(record)==="requirement")card.append(text("span",`${factValue(record,"comparator")} ${factValue(record,"value")} ${factValue(record,"unit")} · ${factValue(record,"method_reference")}`,"example-fact"));
          const missing=asList(match.required_facts);if(missing.length)card.append(text("p","应用前仍需核实："+missing.map(f=>factNames[f] || f).join("、"),"help-text"));
          for(const gap of asList(match.gaps))card.append(text("p",gap.message || issueText(gap),"example-gap"));
          for(const related of asList(match.related_records)){const relatedRecord=byId.get(related.record_id);if(!relatedRecord)continue;const relatedButton=text("button",related.title || title(relatedRecord),"evidence-link related-example-link");relatedButton.type="button";relatedButton.dataset.recordId=related.record_id;relatedButton.addEventListener("click",()=>openRecord(related.record_id));card.append(relatedButton);}
          answer.append(card);
        }
        for(const note of asList(result.notes))answer.append(text("p",typeof note==="object" ? note.message || issueText(note) : note,"example-note"));
      });$("example-buttons").append(button);
    }
  }
  function renderCoverage() {
    const coverage = report.coverage || [], statusNames = {passed:"原文已关联",extracted:"原文已关联",covered:"原文已关联",retained:"保留原文",context_only:"上下文保留",not_applicable:"无独立知识",not_extracted:"未抽取",unsupported:"暂不支持",skipped:"保留原文",review_pending:"待审核",manual_review_required:"待人工处理"};
    $("coverage-summary").textContent = `${coverage.length} 个片段 · ${coverage.filter(c => (c.record_ids || []).length).length} 个原文已关联`;
    const body = $("coverage-table").querySelector("tbody");
    for (const item of coverage) {
      const row = document.createElement("tr"); row.append(text("td",item.anchor || item.chunk_id),text("td",statusNames[item.status] || item.status || "待核对"));
      const links = document.createElement("td"); for (const id of item.record_ids || []) { const record = byId.get(id); if (!record) continue; const button = text("button",title(record),"coverage-link"); button.type = "button"; button.addEventListener("click",() => { activeKind = "all"; $("search").value = ""; $("issues-only").checked = false; selectedId = id; renderList(); $("selected").scrollIntoView({behavior:"smooth"}); }); links.append(button,document.createElement("br")); }
      row.append(links,text("td",item.reason || "—")); body.append(row);
    }
  }
  function download(value,filename,type="application/json;charset=utf-8") {
    const content = typeof value === "string" ? value : JSON.stringify(value,null,2), url = URL.createObjectURL(new Blob([content],{type})); const link = document.createElement("a"); link.href = url; link.download = filename; link.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
  }
  function reviewCsv() {
    const rows = [["记录标识","修订","知识类型","候选标题","机器校验","当前审核状态","待解决信息","原文页码","审核意见（待填写）","审核人（待填写）"]];
    for (const record of records) rows.push([record.record_id,record.revision ?? 1,kindName(record),title(record),record.validation?.passed ? "通过" : "需处理",record.review_state || "review_pending",pendingParts(record).map(issueText).join("；"),recordPages(record).join("、"),"",""]);
    const escape = value => { let content = stringify(value); if (/^[\s]*[=+@-]/.test(content)) content = "'" + content; return `"${content.replace(/"/g,'""')}"`; };
    return "\uFEFF" + rows.map(row => row.map(escape).join(",")).join("\r\n");
  }
  $("document-title").textContent = (report.document_metadata?.designation ? report.document_metadata.designation + " · " : "") + (report.source?.title || "候选知识审阅"); $("record-count").textContent = records.length;
  const metadata=report.document_metadata || {};
  $("standard-snapshot-state").textContent=metadata.designation ? `本页限于所选 ${metadata.designation} 原文。登记状态：${metadata.status_claim || "待核"}；标准身份${metadata.identity_status==="verified" ? "已核验" : "待核验"}，本页未判断业务日期适用性。` : "本页是指定原文的候选快照，未判断业务日期适用性。";
  $("pass-count").textContent = records.filter(r => r.validation?.passed === true).length; $("review-count").textContent = records.filter(r => r.review_state !== "approved").length;
  for (const type of ["all",...new Set(records.map(kind))]) { const button = text("button",type === "all" ? "全部记录" : (kindNames[type] || "其他知识"),"filter"); button.type = "button"; button.dataset.kind = type; button.addEventListener("click",() => {activeKind = type;renderList();}); $("filters").append(button); }
  $("search").addEventListener("input",renderList); $("issues-only").addEventListener("change",renderList);
  $("clear-search").addEventListener("click",() => {$("search").value="";activeKind="all";$("issues-only").checked=false;renderList();});
  $("close-page").addEventListener("click",() => $("page-dialog").close()); $("page-dialog").addEventListener("click",event => {if(event.target === $("page-dialog")) $("page-dialog").close();});
  $("download-report").addEventListener("click",() => download(report,"KB07候选知识完整结果.json"));
  $("download-record").addEventListener("click",() => download(byId.get(selectedId),`KB07候选记录-${selectedId}.json`));
  $("download-review").addEventListener("click",() => download(reviewCsv(),"KB07待审表.csv","text/csv;charset=utf-8"));
  $("copy-id").addEventListener("click",async () => { try { await navigator.clipboard.writeText(selectedId); $("copy-status").textContent="已复制"; } catch (_) { const area = document.createElement("textarea"); area.value=selectedId; document.body.append(area); area.select(); const copied=document.execCommand("copy"); area.remove(); $("copy-status").textContent=copied ? "已复制" : `记录标识：${selectedId}`; } });
  function fromHash() { try { const id=decodeURIComponent(location.hash.slice(1)); if(byId.has(id)){selectedId=id;return true;} } catch (_) { /* Invalid hash is ignored. */ } return false; }
  if(fromHash())activeKind=kind(byId.get(selectedId)); window.addEventListener("hashchange",() => {if(fromHash()){activeKind="all";$("search").value="";$("issues-only").checked=false;renderList();}});
  if(!selectedId) selectedId=(records.find(r => kind(r)==="requirement") || records[0])?.record_id;
  $("selected").hidden=!records.length;$("empty-reader").hidden=!!records.length;renderFacts();renderExamples();renderList();renderCoverage();document.documentElement.dataset.viewerReady="true";
})();

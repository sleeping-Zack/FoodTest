/* Offline source reader. Untrusted Markdown is reconstructed with an HTML allowlist. */
"use strict";
(() => {
  const payload = JSON.parse(document.getElementById("viewer-data").textContent);
  const bundle = payload.bundle;
  const chunks = bundle.chunks || [];
  const byId = new Map(chunks.map(c => [c.chunk_id, c]));
  const blocks = new Map((bundle.blocks || []).map(b => [b.block_id, b]));
  const $ = id => document.getElementById(id);
  const kindNames = {table_row:"表格行",table:"完整表格",navigation:"章节目录",method:"检验方法",submethod:"子方法",method_section:"方法片段",method_subsection:"方法片段",clause:"条款",section:"章节片段",front_matter:"文档说明",preamble:"文档说明",definition:"定义",scope:"适用范围",formula:"计算公式",figure:"图示",footnote:"脚注",requirement:"要求"};
  const roleNames = {scope:"适用范围",parent:"上级章节",method:"完整检验方法",submethod:"所属子方法",table:"完整表格与表头",footnote:"脚注与注释",reference:"关联引用"};
  const roleOrder = ["scope","parent","method","submethod","table","footnote","reference"];
  let selectedId = null;
  let activeKind = "all";
  const md = window.markdownit({html:true,linkify:false,typographer:false,breaks:false});

  md.inline.ruler.before("escape", "inline_math", (state, silent) => {
    if (state.src[state.pos] !== "$" || state.src[state.pos + 1] === "$") return false;
    let end = state.pos + 1;
    while ((end = state.src.indexOf("$", end)) !== -1 && state.src[end - 1] === "\\") end++;
    if (end < 0 || end === state.pos + 1 || state.src.slice(state.pos + 1, end).includes("\n")) return false;
    if (!silent) state.push("inline_math", "", 0).content = state.src.slice(state.pos + 1, end);
    state.pos = end + 1;
    return true;
  });
  md.block.ruler.before("fence", "block_math", (state, start, end, silent) => {
    const first = state.src.slice(state.bMarks[start] + state.tShift[start], state.eMarks[start]).trim();
    if (!first.startsWith("$$")) return false;
    if (silent) return true;
    let body = first.slice(2), next = start + 1;
    if (body.endsWith("$$") && body.length >= 2) body = body.slice(0, -2);
    else {
      const lines = body ? [body] : [];
      let closed = false;
      for (; next < end; next++) {
        const line = state.src.slice(state.bMarks[next] + state.tShift[next], state.eMarks[next]);
        if (line.trim() === "$$") { next++; closed = true; break; }
        lines.push(line);
      }
      if (!closed) return false;
      body = lines.join("\n");
    }
    const token = state.push("block_math", "", 0);
    token.content = body; token.map = [start, next]; state.line = next;
    return true;
  }, {alt:["paragraph","reference","blockquote","list"]});
  md.renderer.rules.inline_math = (tokens, index) => `<span data-math="${md.utils.escapeHtml(tokens[index].content)}"></span>`;
  md.renderer.rules.block_math = (tokens, index) => `<span data-math="${md.utils.escapeHtml(tokens[index].content)}" data-display="true"></span>`;

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
    if (node.tagName === "A") {
      const href = node.getAttribute("href") || "";
      if (href.startsWith("#") && !href.includes("\u0000")) copy.setAttribute("href", href);
      else if (payload.images[href]) { copy.href = payload.images[href]; copy.target = "_blank"; copy.rel = "noopener noreferrer"; }
    }
    if (node.tagName === "TD" || node.tagName === "TH") {
      for (const attr of ["rowspan","colspan"]) {
        const value = node.getAttribute(attr);
        if (value && /^\d{1,3}$/.test(value) && Number(value) > 0 && Number(value) <= 100) copy.setAttribute(attr, value);
      }
    }
    if (node.tagName === "SPAN" && node.hasAttribute("data-math")) {
      copy.dataset.math = node.getAttribute("data-math");
      if (node.getAttribute("data-display") === "true") copy.dataset.display = "true";
    }
    for (const child of node.childNodes) copy.append(cleanNode(child));
    return copy;
  }
  function renderMarkdown(text, target, inline = false) {
    const inert = document.createElement("template");
    inert.innerHTML = inline ? md.renderInline(String(text || "")) : md.render(String(text || ""));
    target.replaceChildren(...Array.from(inert.content.childNodes, cleanNode));
    for (const formula of target.querySelectorAll("[data-math]")) {
      const expression = formula.dataset.math;
      const display = formula.dataset.display === "true";
      formula.className = display ? "math-display" : "math-inline";
      window.katex.render(expression, formula, {displayMode:display,throwOnError:false,trust:false,strict:"ignore",maxExpand:1000});
      delete formula.dataset.math; delete formula.dataset.display;
    }
    for (const table of target.querySelectorAll("table")) {
      const wrapper = document.createElement("div"); wrapper.className = "table-scroll";
      table.replaceWith(wrapper); wrapper.append(table);
    }
  }
  const textNode = (tag, text, className) => {
    const item = document.createElement(tag); item.textContent = text;
    if (className) item.className = className;
    return item;
  };
  const kindName = chunk => kindNames[chunk.kind] || "其他片段";
  const retrievable = chunk => chunk.retrieval_enabled !== false;
  const pagesOf = obj => [...new Set((obj.source?.pdf_pages || []).filter(p => Number.isInteger(p) && p > 0))];
  const plain = value => String(value ?? "").replace(/<!--[\s\S]*?-->/g, "").replace(/<[^>]*>/g, "").replace(/[#$*_`|]/g, "").replace(/\s+/g, " ").trim();
  const stringify = value => value == null ? "" : typeof value === "object" ? String(value.value ?? value.text_raw ?? "") : String(value);

  function showPage(number) {
    const page = payload.pages[String(number)];
    if (!page?.image) return;
    $("page-title").textContent = `原件 · PDF 第 ${number} 页`;
    $("printed-page").textContent = page.printed_page == null ? "此页未标印刷页码" : `印刷页码 ${page.printed_page}`;
    $("page-image").src = page.image;
    $("page-image").alt = `原件 PDF 第 ${number} 页`;
    $("dialog-pdf").hidden = !payload.pdf;
    if (payload.pdf) $("dialog-pdf").href = `${payload.pdf}#page=${number}`;
    $("page-dialog").showModal();
  }
  function sourceButtons(obj, target) {
    target.replaceChildren();
    for (const number of pagesOf(obj)) {
      const button = textNode("button", `PDF 第 ${number} 页`, "page-button"); button.type = "button";
      button.dataset.pdfPage = number;
      if (payload.pages[String(number)]?.image) button.addEventListener("click", () => showPage(number));
      else { button.disabled = true; button.title = "该页图未提供，请打开原 PDF 核对"; }
      target.append(button);
    }
    if (payload.pdf) {
      const link = textNode("a", "打开原 PDF ↗", "secondary-button");
      link.href = `${payload.pdf}#page=${pagesOf(obj)[0] || 1}`; link.target = "_blank"; link.rel = "noopener noreferrer";
      target.append(link);
    }
  }
  function renderFacts(chunk) {
    const target = $("row-facts"), table = chunk.table;
    target.replaceChildren(); target.hidden = !table || chunk.kind !== "table_row";
    if (target.hidden) return;
    const fields = [["项目",table.project ?? table.cells?.[0]?.value ?? table.cells?.[0]?.text_raw],
      ["比较符",table.comparator],["限值 / 要求",table.value],["单位",table.unit],["检验方法",table.method]];
    for (const [label, value] of fields) {
      const fact = textNode("div", "", "fact"); fact.append(textNode("span", label, "fact-label"));
      const shown = textNode("div", "", "fact-value"); renderMarkdown(stringify(value) || "见原文", shown, true);
      fact.append(shown); target.append(fact);
    }
  }
  function renderOriginal(chunk) {
    const target = $("raw-content"), data = chunk.table;
    if (chunk.kind !== "table_row" || !data?.headers?.length || !data?.cells?.length) {
      renderMarkdown(chunk.raw_markdown, target); return;
    }
    target.replaceChildren();
    const wrapper = textNode("div", "", "table-scroll"), table = document.createElement("table");
    const head = document.createElement("thead"), labels = document.createElement("tr");
    for (const header of data.headers) {
      const cell = document.createElement("th"); renderMarkdown(header, cell, true); labels.append(cell);
    }
    head.append(labels); table.append(head);
    const body = document.createElement("tbody"), row = document.createElement("tr");
    for (const cell of data.cells) {
      const content = document.createElement("td");
      const value = textNode("div", "", "cell-value"); renderMarkdown(stringify(cell.inherited ? cell.value : cell.text_raw ?? cell.value), value, true);
      content.append(value);
      if (cell.inherited) content.append(textNode("small", "原表合并单元格，共用此内容", "inherited-note"));
      row.append(content);
    }
    body.append(row); table.append(body); wrapper.append(table); target.append(wrapper);
  }
  function lineageContains(block, target) {
    const seen = new Set(); let current = block;
    while (current && !seen.has(current.block_id)) {
      if (current.block_id === target) return true;
      seen.add(current.block_id); current = blocks.get(current.parent_id);
    }
    return false;
  }
  function renderContext(chunk) {
    const snapshot = payload.contexts[chunk.chunk_id] || {blocks:[],complete:false,omitted:[]};
    const links = (bundle.links || []).filter(link => link.chunk_id === chunk.chunk_id);
    const groups = new Map();
    for (const role of roleOrder) {
      const targets = links.filter(link => link.role === role).map(link => link.target_block_id);
      const matches = snapshot.blocks.filter(block => targets.some(id => block.block_id === id || (["method","submethod","reference"].includes(role) && lineageContains(block, id))));
      if (matches.length) groups.set(role, matches);
    }
    // Include the complete automatic parent chain and any dependency closure.
    const parents = snapshot.blocks.filter(block => !(chunk.block_ids || []).includes(block.block_id) && (chunk.block_ids || []).some(id => lineageContains(blocks.get(id), block.block_id)));
    if (parents.length) groups.set("parent", parents);
    const shown = new Set([...groups.values()].flat().map(block => block.block_id));
    const remaining = snapshot.blocks.filter(block => !shown.has(block.block_id) && !(chunk.block_ids || []).includes(block.block_id));
    if (remaining.length) groups.set("reference", [...(groups.get("reference") || []), ...remaining]);
    const warning = $("context-budget"); warning.hidden = snapshot.complete !== false;
    warning.textContent = `上下文展示已达到本次范围上限，还有 ${(snapshot.omitted || []).length} 个完整来源段未展开。原文没有被截成半句；请打开原 PDF 查看余下内容。`;
    const list = $("context-list"); list.replaceChildren(); $("empty-context").hidden = groups.size > 0;
    for (const role of roleOrder) {
      const items = groups.get(role); if (!items) continue;
      const card = document.createElement("details"); card.className = "context-card"; card.dataset.role = role;
      const summary = textNode("summary", roleNames[role]); summary.append(textNode("span", `${items.length} 个来源段`)); card.append(summary);
      const content = textNode("div", "", "context-content"); card.append(content);
      card.addEventListener("toggle", () => {
        if (!card.open || content.childElementCount) return;
        for (const block of items) {
          const article = textNode("section", "", "context-block"); article.dataset.blockId = block.block_id;
          article.append(textNode("h4", block.title || block.key || "原文片段", "context-block-title"));
          const body = textNode("div", "", "markdown"); renderMarkdown(block.text_raw || "（此处为章节标题，内容见下属片段。）", body); article.append(body);
          const buttons = textNode("div", "", "context-source"); sourceButtons(block, buttons); article.append(buttons); content.append(article);
        }
      });
      list.append(card);
    }
  }
  function renderQuality(chunk) {
    const target = $("quality-note"), issues = chunk.quality?.issues || [];
    target.replaceChildren(); target.hidden = !issues.length;
    if (!issues.length) return;
    target.append(textNode("strong", "这一片段有待核对的信息"));
    const list = document.createElement("ul");
    for (const issue of issues) {
      const message = typeof issue === "string" ? issue : issue.message || issue.reason || issue.code || "来源完整性需要核对";
      list.append(textNode("li", /[\u3400-\u9fff]/.test(message) ? message : "来源或上下文存在缺口，请结合原页核对。"));
    }
    target.append(list);
  }
  function selectChunk(id) {
    const chunk = byId.get(id); if (!chunk) return;
    selectedId = id;
    for (const button of $("chunk-list").querySelectorAll(".chunk-item")) {
      const active = button.dataset.chunkId === id; button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-current", "true"); else button.removeAttribute("aria-current");
    }
    $("heading-path").replaceChildren(...(chunk.heading_path || []).map(item => {
      const entry = textNode("span", ""); renderMarkdown(item, entry, true); return entry;
    }));
    $("kind-label").textContent = kindName(chunk);
    renderMarkdown(chunk.title || chunk.anchor || "原文片段", $("chunk-title"), true);
    sourceButtons(chunk, $("source-actions")); renderQuality(chunk); renderFacts(chunk);
    const location = chunk.source || {};
    $("source-location").textContent = location.line_start ? `转录原文第 ${location.line_start}–${location.line_end || location.line_start} 行` : "保留原文措辞";
    renderOriginal(chunk); renderContext(chunk);
    const trace = $("trace-data"); trace.replaceChildren();
    for (const [label, value] of [["标准",bundle.source.document_id],["片段标识",chunk.chunk_id],["结构位置",chunk.anchor],["原件指纹",bundle.source.asset_id],["转录指纹",bundle.source.markdown_asset_id],["内容状态","尚未专家审核；仅供检索和核对"]]) {
      trace.append(textNode("dt", label), textNode("dd", stringify(value) || "未提供"));
    }
    try { history.replaceState(null, "", `#${encodeURIComponent(id)}`); } catch (_) { /* File hosts may restrict history. */ }
  }
  function renderList() {
    const query = $("search").value.trim().toLocaleLowerCase();
    const candidates = chunks.filter(chunk => $("include-context").checked || retrievable(chunk));
    const filtered = candidates.filter(chunk => (activeKind === "all" || chunk.kind === activeKind) && (!query || [chunk.title,chunk.raw_markdown,chunk.retrieval_text,...(chunk.heading_path || [])].join(" ").toLocaleLowerCase().includes(query)));
    $("list-count").textContent = `显示 ${filtered.length} / ${candidates.length} 个片段`;
    $("clear-search").hidden = !query && activeKind === "all" && !$("include-context").checked;
    const list = $("chunk-list"); list.replaceChildren(); $("empty-list").hidden = filtered.length > 0;
    for (const chunk of filtered) {
      const button = textNode("button", "", "chunk-item"); button.type = "button"; button.dataset.chunkId = chunk.chunk_id;
      const top = textNode("span", "", "item-top");
      top.append(textNode("span", kindName(chunk)), textNode("span", pagesOf(chunk).length ? `第 ${pagesOf(chunk).join("、")} 页` : "来源待定位"));
      const title = textNode("span", "", "item-title"); renderMarkdown(chunk.title || chunk.anchor || "原文片段", title, true);
      button.append(top, title, textNode("span", plain(chunk.raw_markdown).slice(0, 110), "item-excerpt"));
      button.addEventListener("click", () => selectChunk(chunk.chunk_id)); list.append(button);
    }
    for (const filter of $("filters").children) {
      filter.classList.toggle("active", filter.dataset.kind === activeKind);
      filter.hidden = filter.dataset.kind !== "all" && !candidates.some(chunk => chunk.kind === filter.dataset.kind);
    }
    if (filtered.length && !filtered.some(chunk => chunk.chunk_id === selectedId)) selectChunk(filtered[0].chunk_id);
    else if (selectedId) selectChunk(selectedId);
  }

  $("document-title").textContent = bundle.source.title || "原文切分结果";
  $("total-count").textContent = chunks.filter(retrievable).length;
  $("context-only-count").textContent = `（${chunks.filter(chunk => !retrievable(chunk)).length}）`;
  $("row-count").textContent = chunks.filter(chunk => chunk.kind === "table_row").length;
  $("cross-count").textContent = chunks.filter(chunk => retrievable(chunk) && pagesOf(chunk).length > 1).length;
  for (const kind of ["all",...new Set(chunks.map(chunk => chunk.kind))]) {
    const button = textNode("button", kind === "all" ? "全部" : (kindNames[kind] || "其他片段"), "filter");
    button.type = "button"; button.dataset.kind = kind;
    button.addEventListener("click", () => { activeKind = kind; renderList(); }); $("filters").append(button);
  }
  $("search").addEventListener("input", renderList);
  $("include-context").addEventListener("change", () => { activeKind = "all"; renderList(); });
  $("clear-search").addEventListener("click", () => { $("search").value = ""; $("include-context").checked = false; activeKind = "all"; renderList(); });
  $("close-page").addEventListener("click", () => $("page-dialog").close());
  $("page-dialog").addEventListener("click", event => { if (event.target === $("page-dialog")) $("page-dialog").close(); });
  $("download").addEventListener("click", () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(bundle, null, 2)], {type:"application/json;charset=utf-8"}));
    const link = document.createElement("a"); link.href = url; link.download = "原文切分结果.json"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  $("selected").hidden = !chunks.length; $("empty-reader").hidden = !!chunks.length;
  function selectionFromHash() {
    try {
      const id = decodeURIComponent(location.hash.slice(1)), chunk = byId.get(id);
      if (!chunk) return false;
      selectedId = id;
      if (!retrievable(chunk)) $("include-context").checked = true;
      return true;
    } catch (_) { return false; }
  }
  selectionFromHash();
  window.addEventListener("hashchange", () => {
    if (selectionFromHash()) { $("search").value = ""; activeKind = "all"; renderList(); }
  });
  if (!selectedId) selectedId = (chunks.find(chunk => chunk.kind === "table_row") || chunks[0])?.chunk_id;
  renderList();
  document.documentElement.dataset.viewerReady = "true";
})();

// Browser verification of a read-only local knowledge artifact; no database writes.
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');
const {pathToFileURL} = require('url');
const {chromium} = require('C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
let browser;
(async () => {
  const filename = path.resolve(process.argv[2]);
  const output = path.resolve(process.argv[3] || path.dirname(filename));
  fs.mkdirSync(output,{recursive:true});
  browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  const page = await browser.newPage({viewport:{width:1560,height:1100}});
  const errors = [], remote = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {if(/^https?:/.test(request.url())) remote.push(request.url());});
  await page.goto(pathToFileURL(filename).href);
  await page.waitForSelector('html[data-viewer-ready="true"]');
  const report = await page.evaluate(() => JSON.parse(document.getElementById('viewer-data').textContent).report);
  assert.equal(await page.locator('#record-count').innerText(),String(report.records.length));
  assert.equal(await page.locator('#pass-count').innerText(),String(report.records.filter(r => r.validation?.passed === true).length));
  const facts=report.records.filter(r=>r.candidate.record_type==='requirement');
  assert.equal(await page.locator('#facts-table tbody tr').count(),facts.length);
  assert.equal(await page.locator('.record-item').count(),facts.length || report.records.length);
  if(facts.length)assert.equal(await page.locator('.filter.active').getAttribute('data-kind'),'requirement');
  if(report.document_metadata?.status_claim)assert.ok((await page.locator('#standard-snapshot-state').innerText()).includes(report.document_metadata.status_claim));
  await page.screenshot({path:path.join(output,'knowledge-viewer-facts.png'),fullPage:false});
  await page.locator('.filter[data-kind="all"]').click();
  assert.equal(await page.locator('.record-item').count(),report.records.length);
  assert.equal(await page.locator('#coverage-table tbody tr').count(),(report.coverage || []).length);
  assert.equal(await page.evaluate(() => window.PWNED),undefined);
  for(const record of report.records){
    await page.locator('.record-item[data-record-id="' + record.record_id + '"]').click();
    assert.equal(await page.locator('#fields .field').count(),Object.keys(record.candidate.fields).length);
    assert.equal(await page.locator('#selected .katex-error').count(),0);
    assert.equal(await page.locator('#selected script,#selected iframe,#selected [onclick],#selected [onerror]').count(),0);
  }
  const requirement = report.records.find(r => r.candidate.record_type === 'requirement' && /Fe|铁/.test(r.candidate.title)) || report.records.find(r => r.candidate.record_type === 'requirement');
  if(requirement){
    await page.locator(`.record-item[data-record-id="${requirement.record_id}"]`).click();
    assert.equal(await page.locator('#fields .field').count(),Object.keys(requirement.candidate.fields).length);
    assert.ok((await page.locator('#business-state').innerText()).includes('不可用于自动判定'));
    assert.ok((await page.locator('#fields').innerText()).includes('原文原值'));
    assert.ok((await page.locator('#fields').innerText()).includes('规范值'));
    const evidenceLink = page.locator('.evidence-link[data-evidence-index]').first();
    if(await evidenceLink.count()){
      await evidenceLink.click();
      assert.equal(await page.locator('.evidence-card.highlight').count(),1);
    }
    const block = page.locator('.source-block').first();
    if(await block.count()){
      await block.locator('summary').click();
      await page.waitForFunction(() => document.querySelector('.source-block[open] .markdown')?.childElementCount > 0);
    }
    await page.locator('#selected').evaluate(el=>el.scrollIntoView({block:'start'}));
    await page.screenshot({path:path.join(output,'knowledge-viewer-requirement.png'),fullPage:false});
  }
  const synthetic = report.source.document_id === 'SYNTHETIC';
  if(synthetic){
    assert.ok((await page.locator('#fields').innerText()).includes('0.003 5'));
    assert.ok((await page.locator('#fields').innerText()).includes('0.0035'));
    assert.ok(await page.locator('.source-block[open] td[rowspan="2"]').count() > 0);
  }
  const formulaRecord = report.records.find(r => /\$\$|\\frac\{/.test(JSON.stringify(r.candidate.fields)) || (r.candidate.source_chunk_ids || []).some(id=>/\$\$|\\frac\{/.test(report.chunks.find(c=>c.chunk_id===id)?.raw_markdown || '')));
  const heavyMetals = report.records.find(r => r.candidate.record_type === 'requirement' && /重金属/.test(r.candidate.title) && /Pb/.test(r.candidate.title+JSON.stringify(r.candidate.fields.expressed_as)));
  if(heavyMetals){
    await page.locator(`.record-item[data-record-id="${heavyMetals.record_id}"]`).click();
    assert.ok((await page.locator('#record-title').innerText()).includes('重金属'));
    assert.ok((await page.locator('#fields').innerText()).includes('Pb'));
    assert.ok((await page.locator('.field[data-field="value"]').innerText()).includes('10.0'));
    assert.ok((await page.locator('.field[data-field="unit"]').innerText()).includes('mg/kg'));
    assert.ok((await page.locator('#fields').innerText()).includes('以 Pb 计') || (await page.locator('#fields').innerText()).includes('以Pb计'));
    await page.locator('#selected').evaluate(el=>el.scrollIntoView({block:'start'}));
    await page.screenshot({path:path.join(output,'knowledge-viewer-heavy-metals.png'),fullPage:false});
  }
  if(formulaRecord){
    await page.locator(`.record-item[data-record-id="${formulaRecord.record_id}"]`).click();
    // Formula source can be a structured field or a full method source chunk.
    for(const summary of await page.locator('#chunk-context .chunk-card summary').all()) await summary.click();
    await page.waitForFunction(() => document.querySelectorAll('#selected .katex').length > 0);
    assert.equal(await page.locator('#selected .katex-error').count(),0);
    assert.equal(await page.locator('#selected script,#selected iframe,#selected [onclick],#selected [onerror]').count(),0);
    assert.equal(await page.evaluate(() => window.PWNED),undefined);
    await page.locator('#selected').evaluate(el=>el.scrollIntoView({block:'start'}));
    await page.screenshot({path:path.join(output,'knowledge-viewer-method.png'),fullPage:false});
  }
  const semanticMethod=report.records.find(r=>r.candidate.record_type==='method' && r.candidate.semantic_coverage);
  if(semanticMethod){
    await page.locator(`.record-item[data-record-id="${semanticMethod.record_id}"]`).click();
    assert.ok((await page.locator('#method-capabilities').innerText()).includes('部分完成'));
    assert.ok((await page.locator('#method-capabilities').innerText()).includes('尚未实现公式复算'));
    assert.equal(await page.locator('.additional-fields').evaluate(d=>d.open),false);
    assert.equal(await page.locator('.method-evidence').evaluate(d=>d.open),false);
    const allReferences=page.locator('.field .source-reference');
    if(await allReferences.count()){
      const reference=allReferences.first();
      const fieldExtra=reference.locator('xpath=ancestor::details[contains(@class,"additional-fields")]');
      if(await fieldExtra.count())await fieldExtra.locator(':scope > summary').click();
      await reference.locator(':scope > summary').click();
      await page.waitForFunction(()=>!!document.querySelector('.field .source-reference[open] .markdown')?.childElementCount);
      const blockId=await reference.getAttribute('data-block-id');
      const expected=report.blocks.find(b=>b.block_id===blockId);
      if(expected)assert.ok((await reference.innerText()).includes(expected.title || expected.key));
    }
    const semanticParameters=semanticMethod.candidate.fields.parameters?.value || [];
    for(const parameter of semanticParameters){
      assert.ok((await page.locator('.field[data-field="parameters"]').innerText()).includes(parameter.label || parameter.role));
      if(parameter.unit)assert.ok((await page.locator('.field[data-field="parameters"]').innerText()).includes(parameter.unit));
      if(parameter.comparator)assert.ok((await page.locator('.field[data-field="parameters"]').innerText()).includes(parameter.comparator));
    }
    for(const [name,field] of Object.entries(semanticMethod.candidate.fields)){
      if(!Array.isArray(field.value))continue;
      for(const referenceValue of field.value){
        if(!referenceValue.block_id || !referenceValue.anchor)continue;
        const expected=report.blocks.find(b=>b.block_id===referenceValue.block_id);
        if(expected && referenceValue.anchor!==expected.key){
          const reference=page.locator(`.field[data-field="${name}"] .source-reference`).first();
          const extra=reference.locator('xpath=ancestor::details[contains(@class,"additional-fields")]');
          if(await extra.count() && !(await extra.evaluate(d=>d.open)))await extra.locator(':scope > summary').click();
          if(!(await reference.evaluate(d=>d.open)))await reference.locator(':scope > summary').click();
          await reference.locator('p').filter({hasText:'来源引用缺失或不一致'}).waitFor();
          assert.ok((await reference.innerText()).includes('来源引用缺失或不一致'));
          assert.equal(await reference.locator('.katex').count(),0);
        }
      }
    }
  }
  const examples=await page.evaluate(()=>JSON.parse(document.getElementById('viewer-data').textContent).fact_examples || []);
  assert.equal(await page.locator('.example-button').count(),examples.length);
  for(const example of examples){
    await page.locator(`[data-example-id="${example.id}"]`).click();
    assert.equal(await page.locator('#example-answer .example-record').count(),(example.result.records || []).filter(r=>report.records.some(p=>p.record_id===r.record_id)).length);
    assert.ok(/适用性仍待核实|不能据此判定/.test(await page.locator('#example-answer').innerText()));
  }
  const basisExample=examples.find(e=>e.id==='total_alkali_basis');
  if(basisExample?.result.records.length===2){
    await page.locator('[data-example-id="total_alkali_basis"]').click();
    assert.ok((await page.locator('#example-answer').innerText()).includes('以干基计'));
    assert.ok((await page.locator('#example-answer').innerText()).includes('以湿基计'));
    for(const record of basisExample.result.records){await page.locator(`#example-answer button[data-record-id="${record.record_id}"]`).first().click();assert.equal(await page.locator('.record-item[aria-current="true"]').getAttribute('data-record-id'),record.record_id);}
    await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:path.join(output,'knowledge-viewer-basis-query.png'),fullPage:false});
  }
  const chloride=report.records.find(r=>r.candidate.record_type==='method' && /(?:^|:)A\.6$/.test(r.candidate.logical_key));
  if(chloride?.candidate.semantic_coverage){
    await page.locator('.filter[data-kind="all"]').click();
    await page.locator(`.record-item[data-record-id="${chloride.record_id}"]`).click();
    assert.equal(await page.locator('.field[data-field="submethods"] .source-reference').count(),2);
    await page.locator('#selected').evaluate(el=>el.scrollIntoView({block:'start'}));
    await page.screenshot({path:path.join(output,'knowledge-viewer-submethods.png'),fullPage:false});
  }
  const ironMethod=report.records.find(r=>r.candidate.record_type==='method' && r.candidate.logical_key.endsWith(':A.7'));
  if(ironMethod?.candidate.semantic_coverage){
    await page.locator('.filter[data-kind="all"]').click();await page.locator(`.record-item[data-record-id="${ironMethod.record_id}"]`).click();
    assert.ok((await page.locator('.field[data-field="parameters"]').innerText()).includes('≤ 0.0005 %'));
    assert.ok((await page.locator('.field[data-field="calculation_dependencies"]').innerText()).includes('A.7 → A.4'));
    await page.locator('#selected').evaluate(el=>el.scrollIntoView({block:'start'}));
    await page.screenshot({path:path.join(output,'knowledge-viewer-method.png'),fullPage:false});
    await page.locator('.field[data-field="calculation_dependencies"] .source-reference summary').click();
    await page.waitForFunction(()=>document.querySelector('.field[data-field="calculation_dependencies"] .source-reference[open] .katex'));
    assert.equal(await page.locator('#selected .katex-error').count(),0);
    await page.locator('.field[data-field="calculation_dependencies"]').evaluate(el=>el.scrollIntoView({block:'start'}));
    await page.screenshot({path:path.join(output,'knowledge-viewer-calculation-source.png'),fullPage:false});
  }
  const pageButton = page.locator('.source-actions .page-button:not([disabled]):visible').first();
  if(await pageButton.count()){
    await pageButton.click();
    await page.waitForFunction(() => {const image=document.getElementById('page-image');return image.complete && image.naturalWidth > 0;});
    assert.equal(await page.locator('#page-dialog').evaluate(d => d.open),true);
    await page.screenshot({path:path.join(output,'knowledge-viewer-original-page.png'),fullPage:false});
    await page.locator('#close-page').click();
  }
  await page.locator('#search').fill('unlikely-no-record-387419');
  assert.equal(await page.locator('.record-item').count(),0);
  assert.equal(await page.locator('#empty-list').isVisible(),true);
  await page.locator('#clear-search').click();
  assert.equal(await page.locator('.record-item').count(),report.records.length);
  if(requirement){
    await page.locator('.filter[data-kind="requirement"]').click();
    assert.equal(await page.locator('.record-item').count(),report.records.filter(r => r.candidate.record_type==='requirement').length);
    await page.locator('#clear-search').click();
    await page.evaluate(id => {location.hash=encodeURIComponent(id);},requirement.record_id);
    await page.waitForFunction(id => document.querySelector('.record-item[aria-current="true"]')?.dataset.recordId===id,requirement.record_id);
    await page.reload();await page.waitForSelector('html[data-viewer-ready="true"]');
    assert.equal(await page.locator('.record-item[aria-current="true"]').getAttribute('data-record-id'),requirement.record_id);
  }
  await page.locator('#issues-only').check();
  const issueCount = await page.locator('.record-item').count();
  assert.ok(issueCount <= report.records.length);
  await page.locator('#clear-search').click();
  await page.locator('.trace-details summary').click();
  const jsonPromise = page.waitForEvent('download');await page.locator('#download-report').click();
  const jsonDownload = await jsonPromise;await jsonDownload.saveAs(path.join(output,'knowledge-downloaded-report.json'));
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(output,'knowledge-downloaded-report.json'),'utf8')),report);
  const recordPromise=page.waitForEvent('download');await page.locator('#download-record').click();
  const recordDownload=await recordPromise;await recordDownload.saveAs(path.join(output,'knowledge-downloaded-record.json'));
  const selectedId=await page.locator('.record-item[aria-current="true"]').getAttribute('data-record-id');
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(output,'knowledge-downloaded-record.json'),'utf8')),report.records.find(r=>r.record_id===selectedId));
  const csvPromise=page.waitForEvent('download');await page.locator('#download-review').click();
  const csvDownload=await csvPromise;await csvDownload.saveAs(path.join(output,'knowledge-review.csv'));
  const csv=fs.readFileSync(path.join(output,'knowledge-review.csv'),'utf8');
  assert.ok(csv.includes('审核意见（待填写）'));
  if(synthetic) assert.ok(csv.includes("'=合成测试"));
  assert.equal(await page.locator('button').filter({hasText:/批准|启用/}).count(),0);
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1),false);
  await page.screenshot({path:path.join(output,'knowledge-viewer-mobile.png'),fullPage:false});
  assert.deepEqual(remote,[]);assert.deepEqual(errors,[]);
  const result={status:'passed',records:report.records.length,indicator_facts:facts.length,fact_examples:examples.length,coverage:(report.coverage || []).length,issue_filter_records:issueCount,formula_rendered:!!formulaRecord,page_errors:errors,remote_requests:remote,checks:['default_indicator_facts','all_record_access','fixed_fact_queries','basis_query_choices','method_semantic_limits','source_reference_expansion','chloride_submethod_references','record_counts','field_raw_and_normalized','field_evidence_focus','original_source_expansion','table_rowspan','formula_rendering','machine_business_separation','read_only_no_approval_button','page_image','search_filter_reset','record_permalink','report_and_record_json_download','review_csv_formula_safety','mobile_no_overflow','offline_no_network','source_html_allowlist']};
  fs.writeFileSync(path.join(output,'knowledge-viewer-checks.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{await browser?.close();});

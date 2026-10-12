// Real Edge verification of the frozen KB-10 query reader.
const fs=require('fs');
const path=require('path');
const assert=require('assert/strict');
const {pathToFileURL,fileURLToPath}=require('url');
const {chromium}=require('C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
let browser;
(async()=>{
  const filename=path.resolve(process.argv[2]),output=path.resolve(process.argv[3]||path.dirname(filename));
  fs.mkdirSync(output,{recursive:true});
  browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  const page=await browser.newPage({viewport:{width:1480,height:1050},acceptDownloads:true});
  const errors=[],remote=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(/^https?:/.test(r.url()))remote.push(r.url());});
  await page.goto(pathToFileURL(filename).href);await page.waitForSelector('html[data-viewer-ready="true"]');
  const payload=await page.evaluate(()=>JSON.parse(document.getElementById('viewer-data').textContent));
  const {snapshot,cases,source_files,evaluation}=payload;
  assert.ok(cases.length>0);assert.equal(await page.locator('.case-button').count(),cases.length);
  assert.equal(await page.evaluate(()=>window.PWNED),undefined);
  let runs=0,hits=0,failures=0,degraded=0;
  const getRuns=r=>r.results_by_strategy?Object.entries(r.results_by_strategy):r.strategies&&!Array.isArray(r.strategies)?Object.entries(r.strategies):Array.isArray(r.results)?r.results.map((x,i)=>[x.query?.mode||String(i+1),x]):[[r.query?.mode||r.channel||'结果',r]];
  for(const item of cases){
    await page.locator(`.case-button[data-case-id="${item.id}"]`).click();
    assert.deepEqual(JSON.parse(await page.locator('#result-json').textContent()),item.result);
    assert.equal(await page.locator('#query-title').textContent(),item.title||item.id);
    assert.ok((await page.locator('.notice').innerText()).includes('真实知识仍待审'));
    if(item.synthetic===true)assert.ok((await page.locator('#query-note').innerText()).includes('合成工程样例'));
    const resultRuns=getRuns(item.result);assert.equal(await page.locator('.query-run').count(),resultRuns.length);
    for(let i=0;i<resultRuns.length;i++){
      const result=resultRuns[i][1],section=page.locator('.query-run').nth(i);runs++;
      assert.equal(await section.locator('.retrieval-hit').count(),(result.hits||[]).length);
      if(['failed','failure','error','unavailable'].includes(result.status)){assert.ok((await section.innerText()).includes('不能解释为无资料'));failures++;}
      if(result.status==='degraded'){assert.ok((await section.innerText()).includes('降级路径'));degraded++;}
      for(let h=0;h<(result.hits||[]).length;h++){
        const hit=result.hits[h],card=section.locator('.retrieval-hit').nth(h);hits++;
        assert.equal(await card.getAttribute('data-record-id'),hit.record_id);
        if(hit.display_fields?.project||hit.exact?.project)assert.ok((await card.locator('h4').textContent()).includes(String(hit.display_fields?.project||hit.exact?.project)));
        assert.equal(await card.locator('.match-row').count(),(hit.matches||[]).length);
        assert.ok((await card.innerText()).includes('未知条件保留候选'));
        for(let m=0;m<(hit.matches||[]).length;m++){
          const match=hit.matches[m],row=card.locator('.match-row').nth(m);
          assert.equal(await row.locator('blockquote').textContent(),match.raw_values ? JSON.stringify(match.raw_values) : String(match.matched_text??''));
          if(match.match_kind==='related_name')assert.ok((await row.innerText()).includes('非同一对象'));
        }
      }
    }
    const name=item.kind==='failure'?'retrieval-failure.png':item.kind==='comparison'?'retrieval-comparison.png':'retrieval-query.png';
    if(!fs.existsSync(path.join(output,name))){await page.locator('#results').evaluate(el=>el.scrollIntoView({block:'start'}));await page.screenshot({path:path.join(output,name),fullPage:false});}
  }
  const first=cases[0];await page.locator(`.case-button[data-case-id="${first.id}"]`).click();
  await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:path.join(output,'retrieval-overview.png'),fullPage:false});
  const verified=Object.entries(source_files).filter(([_,v])=>v.status==='hash_verified');
  for(const [_,entry] of verified){const url=new URL(entry.url,pathToFileURL(filename));assert.equal(url.protocol,'file:');assert.ok(fs.existsSync(fileURLToPath(url)));}
  const sourceCase=cases.find(item=>JSON.stringify(item.result).includes('source_key')||JSON.stringify(item.result).includes('asset_id'));
  if(sourceCase){await page.locator(`.case-button[data-case-id="${sourceCase.id}"]`).click();const link=page.locator('.evidence-link').first();if(await link.count()){
    const id=(await link.getAttribute('href')).slice(1);await link.click();assert.equal(await page.locator('#'+id).evaluate(el=>el.open),true);
    assert.equal(await page.locator('#'+id+' script,#'+id+' iframe,#'+id+' img,#'+id+' [onerror]').count(),0);
    for(const sourceLink of await page.locator('#'+id+' .source-actions a').all()){const url=new URL(await sourceLink.evaluate(el=>el.href));assert.equal(url.protocol,'file:');assert.ok(fs.existsSync(fileURLToPath(url)));}
    await page.screenshot({path:path.join(output,'retrieval-source.png'),fullPage:false});
  }}
  await page.locator(`.case-button[data-case-id="${first.id}"]`).click();
  const download=page.waitForEvent('download');await page.locator('#download-result').click();const file=await download;
  const saved=path.join(output,'downloaded-result.json');await file.saveAs(saved);assert.deepEqual(JSON.parse(fs.readFileSync(saved,'utf8')),first.result);
  if(Object.keys(evaluation||{}).length){const event=page.waitForEvent('download');await page.locator('#download-evaluation').click();const file=await event;const saved=path.join(output,'downloaded-evaluation.json');await file.saveAs(saved);assert.deepEqual(JSON.parse(fs.readFileSync(saved,'utf8')),evaluation);}
  for(const type of ['exact','lexical','comparison','failure']){await page.locator('#kind-filter').selectOption(type);assert.equal(await page.locator('.case-button').count(),cases.filter(c=>(c.kind||c.result?.channel||'lexical')===type).length);}
  await page.locator('#kind-filter').selectOption('all');await page.locator('#scenario-search').fill('没有保存的工程反例999');
  assert.equal(await page.locator('#no-case').isVisible(),true);assert.equal(await page.locator('#results').isVisible(),false);assert.ok((await page.locator('#no-case').innerText()).includes('不能据此判断'));
  await page.locator('#scenario-search').fill('');await page.setViewportSize({width:390,height:900});await page.evaluate(()=>window.scrollTo(0,0));
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));await page.screenshot({path:path.join(output,'retrieval-mobile.png'),fullPage:false});
  for(const button of await page.locator('.case-button').all())await button.click();
  assert.equal(await page.evaluate(()=>window.PWNED),undefined);assert.deepEqual(errors,[]);assert.deepEqual(remote,[]);
  const checks={passed:true,generation_id:snapshot.generation_id,cases_checked:cases.length,result_runs_checked:runs,hits_checked:hits,
    failed_runs_checked:failures,degraded_runs_checked:degraded,copied_sources_checked:verified.length,mobile_width:390,downloads_preserve_exact_data:true,page_errors:errors,remote_requests:remote};
  fs.writeFileSync(path.join(output,'retrieval-viewer-checks.json'),JSON.stringify(checks,null,2));console.log(JSON.stringify(checks));await browser.close();
})().catch(async error=>{console.error(error);if(browser)await browser.close();process.exit(1);});

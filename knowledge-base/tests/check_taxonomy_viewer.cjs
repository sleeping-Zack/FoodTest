// Read-only verification of a frozen offline KB-08 result, with real browser interactions.
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');
const {pathToFileURL,fileURLToPath} = require('url');
const {chromium} = require('C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
let browser;
(async () => {
  const filename = path.resolve(process.argv[2]);
  const output = path.resolve(process.argv[3] || path.dirname(filename));
  fs.mkdirSync(output,{recursive:true});
  browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  const page = await browser.newPage({viewport:{width:1560,height:1100},acceptDownloads:true});
  const errors=[],remote=[];
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(/^https?:/.test(request.url()))remote.push(request.url());});
  await page.goto(pathToFileURL(filename).href);
  await page.waitForSelector('html[data-viewer-ready="true"]');
  const payload = await page.evaluate(()=>JSON.parse(document.getElementById('viewer-data').textContent));
  const {snapshot,examples}=payload;
  for(const source of snapshot.sources||[]){
    const mapped=payload.source_files[source.source_key];
    assert.equal(mapped?.status,'hash_verified');
    const target=new URL(mapped.url,pathToFileURL(filename));
    assert.equal(target.protocol,'file:');
    assert.ok(fs.existsSync(fileURLToPath(target)));
  }
  assert.ok(examples.length>0);
  const foods=[...new Set(examples.map(example=>example.food_name||example.result?.food_name))];
  assert.equal(await page.locator('.example-button').count(),foods.length);
  assert.equal(await page.evaluate(()=>window.PWNED),undefined);
  for (const example of examples) {
    const food=example.food_name||example.result.food_name;
    await page.locator(`.example-button[data-food-name="${food}"]`).click();
    await page.locator(`.scenario-button[data-example-id="${example.id}"]`).click();
    assert.equal(await page.locator('.namespace').count(),example.result.namespaces.length);
    assert.equal(await page.locator('.branch').count(),example.result.namespaces.reduce((sum,ns)=>sum+(ns.branches||[]).length,0));
    assert.equal(JSON.parse(await page.locator('#result-json').textContent()).snapshot_id,snapshot.snapshot_id);
    assert.deepEqual(JSON.parse(await page.locator('#result-json').textContent()),example.result);
    assert.ok((await page.locator('#results').innerText()).includes('不自动判定'));
    for(const ns of example.result.namespaces){
      const section=page.locator(`.namespace[data-namespace="${ns.namespace}"]`);
      assert.equal(await section.locator('.branch').count(),(ns.branches||[]).length);
      for(const branch of ns.branches||[]){
        const card=section.locator(`.branch[data-category-id="${branch.category_id}"]`);
        assert.ok((await card.innerText()).includes(branch.label));
        for(const requirement of branch.requirements||[]){
          const row=card.locator(`.requirements .requirement[data-requirement-id="${requirement.id}"]`);
          assert.equal(await row.count(),1);
          if(requirement.semantic?.project)assert.ok((await row.textContent()).includes(requirement.semantic.project));
          assert.ok((await row.innerText()).includes('不作为本次任务必检'));
        }
        if(branch.association_count==null)assert.ok((await card.innerText()).includes('关联数量未知'));
      }
    }
  }
  const first=examples[0];
  await page.locator(`.example-button[data-food-name="${first.food_name||first.result.food_name}"]`).click();
  await page.locator(`.scenario-button[data-example-id="${first.id}"]`).click();
  await page.locator('#input-facts > summary').click();
  assert.ok((await page.locator('#facts').innerText()).includes('事实'));
  await page.screenshot({path:path.join(output,'taxonomy-overview.png'),fullPage:false});
  const ns=first.result.namespaces[0];
  await page.locator('#namespace-filter').selectOption(ns.namespace);
  assert.equal(await page.locator('.namespace').count(),1);
  assert.equal(await page.locator('.namespace').getAttribute('data-namespace'),ns.namespace);
  await page.locator('#namespace-filter').selectOption('all');
  const link=page.locator('.evidence-link').first();
  if(await link.count()){
    const id=(await link.getAttribute('href')).slice(1);
    await link.click();
    assert.equal(await page.locator('#'+id).evaluate(el=>el.open),true);
    assert.ok(await page.locator('#'+id+' blockquote').innerText());
    assert.equal(await page.locator('#'+id+' script,#'+id+' iframe,#'+id+' img,#'+id+' [onerror]').count(),0);
    for(const sourceLink of await page.locator('#'+id+' .source-actions a').all()){
      const href=await sourceLink.evaluate(el=>el.href);
      assert.ok(href.startsWith('file:'));
      const url=new URL(href);url.hash='';
      assert.ok(fs.existsSync(fileURLToPath(url)));
    }
    await page.screenshot({path:path.join(output,'taxonomy-source.png'),fullPage:false});
  }
  const paths=page.locator('.branch-section details').first();
  if(await paths.count()){
    await paths.locator(':scope > summary').click();
    await paths.evaluate(el=>el.scrollIntoView({block:'start'}));
    await page.screenshot({path:path.join(output,'taxonomy-inheritance.png'),fullPage:false});
  }
  const downloadResult=page.waitForEvent('download');
  await page.locator('#download-result').click();
  const resultFile=await downloadResult;const resultPath=path.join(output,'downloaded-query.json');
  await resultFile.saveAs(resultPath);assert.deepEqual(JSON.parse(fs.readFileSync(resultPath,'utf8')),first.result);
  await page.locator('.trace-details > summary').click();
  const downloadSnapshot=page.waitForEvent('download');
  await page.locator('#download-snapshot').click();
  const snapshotFile=await downloadSnapshot;const snapshotPath=path.join(output,'downloaded-snapshot.json');
  await snapshotFile.saveAs(snapshotPath);assert.deepEqual(JSON.parse(fs.readFileSync(snapshotPath,'utf8')),snapshot);
  for(const food of ['苹果','苹果汁','萝卜','萝卜叶','牛奶']){
    const example=examples.find(e=>(e.food_name||e.result.food_name)===food);
    if(!example)continue;
    await page.locator('#food-search').fill(food);
    assert.deepEqual(JSON.parse(await page.locator('#result-json').textContent()),example.result);
  }
  for(const [id,namespace,filename] of [['radish','all','taxonomy-radish-two-standards.png'],
                                      ['milk-unknown','inspection-guide-2026-upper','taxonomy-milk-missing.png'],
                                      ['milk-pasteurized','inspection-guide-2026-upper','taxonomy-milk-process.png']]){
    const example=examples.find(e=>e.id===id);
    if(!example)continue;
    await page.locator('#food-search').fill(example.food_name||example.result.food_name);
    await page.locator(`.scenario-button[data-example-id="${id}"]`).click();
    await page.locator('#namespace-filter').selectOption(namespace);
    if(namespace==='all')await page.locator('.selected-summary').evaluate(el=>el.scrollIntoView({block:'start'}));
    else await page.locator('.namespace').evaluate(el=>el.scrollIntoView({block:'start'}));
    await page.screenshot({path:path.join(output,filename),fullPage:false});
  }
  await page.locator('#namespace-filter').selectOption('all');
  await page.locator('#food-search').fill('未知食品工程反例');
  assert.equal(await page.locator('#no-example').isVisible(),true);
  assert.equal(await page.locator('#results').isVisible(),false);
  assert.ok((await page.locator('#no-example').innerText()).includes('不能据此判定'));
  await page.locator('#food-search').fill('');
  await page.setViewportSize({width:390,height:900});
  await page.evaluate(()=>window.scrollTo(0,0));
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
  assert.ok(await page.locator('#food-search').isVisible());
  await page.screenshot({path:path.join(output,'taxonomy-mobile.png'),fullPage:false});
  for(const button of await page.locator('.example-button').all())await button.click();
  assert.equal(await page.evaluate(()=>window.PWNED),undefined);
  assert.deepEqual(errors,[]);assert.deepEqual(remote,[]);
  const checks={passed:true,snapshot_id:snapshot.snapshot_id,examples_checked:examples.length,foods_checked:foods,mobile_width:390,
                source_links_checked:true,source_files_checked:(snapshot.sources||[]).length,downloads_preserve_exact_data:true,page_errors:errors,remote_requests:remote};
  fs.writeFileSync(path.join(output,'taxonomy-viewer-checks.json'),JSON.stringify(checks,null,2));
  console.log(JSON.stringify(checks));
  await browser.close();
})().catch(async error=>{console.error(error);if(browser)await browser.close();process.exit(1);});

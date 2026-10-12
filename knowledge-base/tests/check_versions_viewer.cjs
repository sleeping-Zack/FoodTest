// Real browser verification of the read-only, frozen KB-09 reader.
const fs=require('fs');
const path=require('path');
const assert=require('assert/strict');
const {pathToFileURL,fileURLToPath}=require('url');
const {chromium}=require('C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
let browser;
(async()=>{
  const filename=path.resolve(process.argv[2]);const output=path.resolve(process.argv[3]||path.dirname(filename));
  fs.mkdirSync(output,{recursive:true});
  browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  const page=await browser.newPage({viewport:{width:1480,height:1050},acceptDownloads:true});
  const errors=[],remote=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(/^https?:/.test(r.url()))remote.push(r.url());});
  await page.goto(pathToFileURL(filename).href);await page.waitForSelector('html[data-viewer-ready="true"]');
  const payload=await page.evaluate(()=>JSON.parse(document.getElementById('viewer-data').textContent));
  const {snapshot,examples,source_files}=payload;
  assert.ok(examples.length>0);assert.equal(await page.locator('.example-button').count(),examples.length);
  assert.equal(await page.evaluate(()=>window.PWNED),undefined);
  const kind=e=>e.kind||(/reference|expansion/.test(e.result?.schema_version||'')?'references':/impact/.test(e.result?.schema_version||'')?'impact':'resolution');
  for(const example of examples){
    await page.locator(`.example-button[data-example-id="${example.id}"]`).click();
    assert.deepEqual(JSON.parse(await page.locator('#result-json').textContent()),example.result);
    assert.equal(await page.locator('#query-title').textContent(),example.title||example.id);
    assert.ok((await page.locator('.notice').innerText()).includes('真实知识仍待审'));
    if(example.synthetic===true)assert.ok((await page.locator('#query-note').innerText()).includes('合成工程样例'));
    if(example.id==='synthetic-loop'){
      assert.equal(await page.locator('#query-issues > .issues > .issue').count(),1);
      assert.ok((await page.locator('#query-issues').innerText()).includes('× 20处'));
      assert.deepEqual(JSON.parse(await page.locator('#query-issues details pre').textContent()),example.result.gaps);
    }
    if(kind(example)==='resolution'){
      assert.equal(await page.locator('.version-candidate').count(),(example.result.candidates||[]).length);
      for(const candidate of example.result.candidates||[]){
        const card=page.locator(`.version-candidate[data-version-id="${candidate.version_id}"]`);
        assert.ok((await card.innerText()).includes(candidate.designation||candidate.version_id));
        assert.ok((await card.innerText()).includes('保留或排除理由'));
        assert.equal(await card.locator('.policy').count(),(candidate.policies_checked||[]).length);
      }
    }
    const filename=kind(example)==='resolution'?'versions-date.png':kind(example)==='references'?'versions-references.png':'versions-impact.png';
    if(!fs.existsSync(path.join(output,filename))){await page.locator('#results').evaluate(el=>el.scrollIntoView({block:'start'}));await page.screenshot({path:path.join(output,filename),fullPage:false});}
  }
  await page.locator('#scenario-search').fill('');await page.locator('#kind-filter').selectOption('all');
  const first=examples[0];await page.locator(`.example-button[data-example-id="${first.id}"]`).click();
  await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:path.join(output,'versions-overview.png'),fullPage:false});
  const verifiedSources=Object.entries(source_files).filter(([_,v])=>v.status==='hash_verified');
  for(const [key,mapped] of verifiedSources){const target=new URL(mapped.url,pathToFileURL(filename));assert.equal(target.protocol,'file:');assert.ok(fs.existsSync(fileURLToPath(target)));}
  const link=page.locator('.evidence-link').first();
  if(await link.count()){
    const id=(await link.getAttribute('href')).slice(1);await link.click();
    assert.equal(await page.locator('#'+id).evaluate(el=>el.open),true);
    assert.ok(await page.locator('#'+id+' blockquote').innerText());
    assert.equal(await page.locator('#'+id+' script,#'+id+' iframe,#'+id+' img,#'+id+' [onerror]').count(),0);
    for(const sourceLink of await page.locator('#'+id+' .source-actions a').all()){
      const url=new URL(await sourceLink.evaluate(el=>el.href));assert.equal(url.protocol,'file:');url.hash='';assert.ok(fs.existsSync(fileURLToPath(url)));
    }
    await page.screenshot({path:path.join(output,'versions-source.png'),fullPage:false});
  }
  const download=page.waitForEvent('download');await page.locator('#download-result').click();const file=await download;
  const saved=path.join(output,'downloaded-result.json');await file.saveAs(saved);assert.deepEqual(JSON.parse(fs.readFileSync(saved,'utf8')),first.result);
  for(const type of ['resolution','references','impact']){
    await page.locator('#kind-filter').selectOption(type);assert.equal(await page.locator('.example-button').count(),examples.filter(e=>kind(e)===type).length);
  }
  await page.locator('#kind-filter').selectOption('all');
  await page.locator('#scenario-search').fill('没有保存的工程反例999');assert.equal(await page.locator('#no-example').isVisible(),true);assert.equal(await page.locator('#results').isVisible(),false);
  assert.ok((await page.locator('#no-example').innerText()).includes('不能据此判定'));
  await page.locator('#scenario-search').fill('');await page.locator('#catalog-search').fill('SYNTHETIC');
  if((snapshot.versions||[]).some(v=>v.designation.includes('SYNTHETIC'))){await page.locator('.catalog-entry').first().locator('summary').first().click();assert.ok((await page.locator('.catalog-entry').first().innerText()).includes('主体载体'));}
  await page.locator('#catalog-search').fill('');
  await page.setViewportSize({width:390,height:900});await page.evaluate(()=>window.scrollTo(0,0));
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
  await page.screenshot({path:path.join(output,'versions-mobile.png'),fullPage:false});
  for(const button of await page.locator('.example-button').all())await button.click();
  assert.equal(await page.evaluate(()=>window.PWNED),undefined);assert.deepEqual(errors,[]);assert.deepEqual(remote,[]);
  const checks={passed:true,snapshot_id:snapshot.snapshot_id,examples_checked:examples.length,mobile_width:390,
    copied_source_files_checked:verifiedSources.length,downloads_preserve_exact_data:true,page_errors:errors,remote_requests:remote};
  fs.writeFileSync(path.join(output,'versions-viewer-checks.json'),JSON.stringify(checks,null,2));console.log(JSON.stringify(checks));await browser.close();
})().catch(async error=>{console.error(error);if(browser)await browser.close();process.exit(1);});

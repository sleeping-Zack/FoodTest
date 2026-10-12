// Browser check for a generated offline viewer; does not access a database.
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');
const {pathToFileURL} = require('url');
const {chromium} = require('C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
let browser;

(async () => {
  const filename = path.resolve(process.argv[2]);
  const output = path.resolve(process.argv[3] || path.dirname(filename));
  fs.mkdirSync(output, {recursive:true});
  browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  const page = await browser.newPage({viewport:{width:1440,height:1050}});
  const errors = [], remote = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (/^https?:/.test(request.url())) remote.push(request.url()); });
  await page.goto(pathToFileURL(filename).href);
  await page.waitForSelector('html[data-viewer-ready="true"]');
  const bundle = await page.evaluate(() => JSON.parse(document.getElementById('viewer-data').textContent).bundle);
  const retrievable = bundle.chunks.filter(c => c.retrieval_enabled !== false);
  assert.equal(await page.locator('#total-count').innerText(),String(retrievable.length));
  assert.equal(await page.locator('.chunk-item').count(),retrievable.length);
  assert.equal(await page.evaluate(() => window.PWNED),undefined);
  const ironRow = bundle.chunks.find(c => c.kind === 'table_row' && /Fe/.test(c.title));
  const row = ironRow || bundle.chunks.find(c => c.kind === 'table_row' && c.table?.comparator) || bundle.chunks.find(c => c.kind === 'table_row');
  if (row) {
    await page.locator(`[data-chunk-id="${row.chunk_id}"]`).click();
    assert.ok((await page.locator('#chunk-title').innerText()).includes(row.title.split('$')[0].trim()));
    assert.equal(await page.locator('#row-facts .fact').count(),5);
    assert.equal(await page.locator('#raw-content tbody tr').count(),1);
    if (row.table.comparator) assert.ok((await page.locator('#row-facts').innerText()).includes(row.table.comparator));
    if (ironRow) {
      assert.ok((await page.locator('#row-facts').innerText()).includes('0.003 5'));
      assert.ok((await page.locator('#row-facts').innerText()).includes('附录 A 中 A.7'));
      const references = page.locator('.context-card[data-role="reference"]');
      await references.locator('summary').click();
      await page.waitForFunction(() => document.querySelector('.context-card[data-role="reference"] .context-content')?.childElementCount > 0);
      const contextText = await references.innerText();
      for (const heading of ['A.7.5 结果计算','A.1 安全提示','A.2 一般规定','A.4.4 结果计算']) assert.ok(contextText.includes(heading));
    }
    assert.equal(await page.locator('#raw-content script,#raw-content iframe,#raw-content [onclick],#raw-content [onerror]').count(),0);
    await page.evaluate(() => window.scrollTo(0,0));
    await page.screenshot({path:path.join(output,'viewer-table-row.png'),fullPage:false});
    const tableContext = page.locator('.context-card[data-role="table"]');
    if (await tableContext.count()) {
      await tableContext.locator('summary').click();
      await page.waitForFunction(() => document.querySelector('.context-card[data-role="table"] .context-content')?.childElementCount > 0);
      assert.ok(await tableContext.locator('table').count() > 0);
    }
  }
  const mergedRow = retrievable.find(c => c.kind === 'table_row' && c.table?.cells.some(cell => cell.inherited));
  if (mergedRow) {
    await page.locator(`[data-chunk-id="${mergedRow.chunk_id}"]`).click();
    assert.ok(await page.locator('#raw-content .inherited-note').count() > 0);
    const inherited = mergedRow.table.cells.find(cell => cell.inherited);
    assert.ok((await page.locator('#raw-content').innerText()).includes(inherited.value.split('<')[0]));
    assert.equal(await page.locator('#raw-content script,#raw-content iframe,#raw-content [onclick],#raw-content [onerror]').count(),0);
    assert.equal(await page.evaluate(() => window.PWNED),undefined);
    await page.evaluate(() => window.scrollTo(0,0));
    await page.screenshot({path:path.join(output,'viewer-merged-method.png'),fullPage:false});
  }
  const crossFormula = retrievable.find(c => /\$\$/.test(c.raw_markdown || '') && c.source.pdf_pages.length > 1);
  const formula = crossFormula || retrievable.find(c => /\$\$/.test(c.raw_markdown || ''));
  if (formula) {
    await page.locator('#search').fill('');
    await page.locator(`[data-chunk-id="${formula.chunk_id}"]`).click();
    assert.ok(await page.locator('#raw-content .katex').count() > 0);
    assert.equal(await page.locator('#raw-content .katex-error').count(),0);
    assert.equal(await page.locator('#source-actions button').count(),formula.source.pdf_pages.length);
    assert.equal(await page.locator('#raw-content script,#raw-content iframe,#raw-content [onclick],#raw-content [onerror]').count(),0);
    assert.equal(await page.evaluate(() => window.PWNED),undefined);
    const method = page.locator('.context-card[data-role="method"]');
    if (await method.count()) {
      await method.locator('summary').click();
      await page.waitForFunction(() => document.querySelector('.context-card[data-role="method"] .context-content')?.childElementCount > 0);
      assert.ok(await method.locator('.context-block').count() > 0);
    }
    await page.evaluate(() => window.scrollTo(0,0));
    await page.screenshot({path:path.join(output,'viewer-formula.png'),fullPage:false});
  }
  const pageButton = page.locator('#source-actions button:not([disabled])').first();
  if (await pageButton.count()) {
    await pageButton.click();
    await page.waitForFunction(() => { const image = document.getElementById('page-image'); return image.complete && image.naturalWidth > 0; });
    assert.equal(await page.locator('#page-dialog').evaluate(d => d.open),true);
    await page.screenshot({path:path.join(output,'viewer-source-page.png'),fullPage:false});
    await page.locator('#close-page').click();
  }
  const table = bundle.chunks.find(c => c.kind === 'table' && /rowspan/.test(c.raw_markdown || ''));
  if (table) {
    await page.locator('#include-context').check();
    await page.locator(`[data-chunk-id="${table.chunk_id}"]`).click();
    assert.ok(await page.locator('#raw-content td[rowspan="2"]').count() > 0);
    await page.reload();
    await page.waitForSelector('html[data-viewer-ready="true"]');
    assert.equal(await page.locator('.chunk-item[aria-current="true"]').getAttribute('data-chunk-id'),table.chunk_id);
    assert.equal(await page.locator('#include-context').isChecked(),true);
  }
  await page.locator('#search').fill('unlikely-no-matching-content-1843');
  assert.equal(await page.locator('.chunk-item').count(),0);
  assert.equal(await page.locator('#empty-list').isVisible(),true);
  await page.locator('#clear-search').click();
  if (row) {
    await page.locator('.filter[data-kind="table_row"]').click();
    assert.equal(await page.locator('.chunk-item').count(),retrievable.filter(c => c.kind === 'table_row').length);
    await page.locator('#clear-search').click();
    await page.evaluate(id => { location.hash = encodeURIComponent(id); },row.chunk_id);
    await page.waitForFunction(id => document.querySelector('.chunk-item[aria-current="true"]')?.dataset.chunkId === id,row.chunk_id);
    await page.reload();
    await page.waitForSelector('html[data-viewer-ready="true"]');
    assert.equal(await page.locator('.chunk-item[aria-current="true"]').getAttribute('data-chunk-id'),row.chunk_id);
  }
  assert.equal(await page.locator('.chunk-item').count(),retrievable.length);
  await page.locator('#search').fill('氯化物');
  const searchCount = await page.locator('.chunk-item').count();
  assert.ok(searchCount < retrievable.length);
  await page.locator('#clear-search').click();
  await page.locator('.trace-details summary').click();
  const downloadPromise = page.waitForEvent('download');
  await page.locator('#download').click();
  const download = await downloadPromise;
  await download.saveAs(path.join(output,'downloaded-chunks.json'));
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(output,'downloaded-chunks.json'),'utf8')),bundle);
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1),false);
  await page.screenshot({path:path.join(output,'viewer-mobile.png'),fullPage:false});
  assert.deepEqual(remote,[]);
  assert.deepEqual(errors,[]);
  const result = {status:'passed',chunks:bundle.chunks.length,retrievable:retrievable.length,search_results:searchCount,page_errors:errors,remote_requests:remote,coverage:{iron_row:!!ironRow,inherited_method:!!mergedRow,crosspage_formula:!!crossFormula},checks:['actual_chunk_count','retrieval_navigation_separation','table_facts','markdown_allowlist','table_context','formula_rendering','method_context','original_page_image','merged_cells','search_and_reset','kind_filter','hash_permalink','json_download','mobile_no_horizontal_overflow','offline_no_network']};
  fs.writeFileSync(path.join(output,'viewer-checks.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => { await browser?.close(); });

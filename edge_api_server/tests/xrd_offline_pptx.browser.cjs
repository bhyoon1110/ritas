// Validate HTML actually extracted from the generated PPT, without any server.
// Usage: NODE_PATH=<playwright> node tests/xrd_offline_pptx.browser.cjs <extracted-dir>
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const folder = process.argv[2];
const names = JSON.parse(fs.readFileSync(path.join(folder, 'viewers.json'), 'utf8'));
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const cases = [];
  try {
    for (const legacy of [false,true]) for (const width of [1440,390]) for (const name of names) {
      let html = fs.readFileSync(path.join(folder,name), 'utf8');
      if(legacy) html = html.replace(/type="module"/g,'type="text/plain"').replace(/<script nomodule>/g,'<script>');
      const payload = JSON.parse(html.match(/<script id="xrd-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);
      const context = await browser.newContext({viewport:{width,height:900},acceptDownloads:true});
      const page = await context.newPage(), errors=[], requests=[];
      page.on('pageerror', error=>errors.push(error.message));
      await page.route('**/*', route=> {
        if(route.request().url()==='https://offline.invalid/graph.html') return route.fulfill({contentType:'text/html',body:html});
        requests.push(route.request().url());return route.abort();
      });
      await page.goto('https://offline.invalid/graph.html');
      await page.waitForFunction(()=>document.querySelector('#plot')?.data?.some(t=>t.meta?.role==='measurement'));
      const ids=await page.evaluate(()=>document.querySelector('#plot').data.filter(t=>t.meta?.role==='measurement'&&t.visible!==false&&t.visible!=='legendonly').map(t=>t.meta.sampleId).sort());
      assert.deepEqual(ids,payload.project.samples.map(s=>s.id).sort());
      const zoom=page.locator('.modebar-btn[data-title="Zoom in"]');
      if(await zoom.count()) {
        const before=await page.evaluate(()=>document.querySelector('#plot')._fullLayout.xaxis.range);
        await zoom.first().click();
        await page.waitForFunction(range=>JSON.stringify(document.querySelector('#plot')._fullLayout.xaxis.range)!==JSON.stringify(range),before);
      }
      await page.screenshot({path:path.join(folder,`${legacy?'ES5':'native'}-${width}-${name}.png`)});
      assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);
      cases.push({name,legacy,width,samples:ids.length});
      await context.close();
    }
    fs.writeFileSync(path.join(folder,'browser-results.json'),JSON.stringify({browser:browser.version(),cases},null,2));
    console.log(JSON.stringify({browser:browser.version(),passed:cases.length}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});

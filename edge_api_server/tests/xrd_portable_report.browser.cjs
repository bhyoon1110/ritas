// Usage: NODE_PATH=<playwright modules> node tests/xrd_portable_report.browser.cjs <보고서.html> <qa-dir>
// Chrome automation verifies offline isolation, not Android/Supermium physical-device support.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const {chromium} = require('playwright');
const source = fs.readFileSync(process.argv[2], 'utf8');
const out = process.argv[3] || '/tmp/xrd-portable-browser';
fs.mkdirSync(out, {recursive:true});
const payloadPattern = /(<script id="xrd-portable-data" type="application\/json">)([\s\S]*?)(<\/script>)/;
const bundle = JSON.parse(source.match(payloadPattern)[2]);
const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const results = [];
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try {
    for (const legacy of [false,true]) for (const width of [1440,390]) {
      const name = `${legacy?'ES5':'native'}-${width}`;
      const payload = {...bundle, viewer:legacy ? bundle.viewer.replace(/type="module"/g,'type="text/plain"').replace(/<script nomodule>/g,'<script>') : bundle.viewer};
      const json = JSON.stringify(payload).replace(/</g,'\\u003c').replace(/\u2028/g,'\\u2028').replace(/\u2029/g,'\\u2029');
      // Simulate a provider's non-filesystem base. No server or neighboring files exist.
      const html = source.replace(payloadPattern, (_,a,b,c)=>a+json+c).replace('<head>','<head><base href="content://media/external/file/12345">');
      const context = await browser.newContext({viewport:{width,height:950},hasTouch:width===390,acceptDownloads:true});
      const page = await context.newPage(), errors=[], requests=[];
      page.on('pageerror', e=>errors.push(e.message));
      await context.route('**/*', route=>{
        if(route.request().url()==='https://offline.invalid/report.html') return route.fulfill({contentType:'text/html',body:html});
        requests.push(route.request().url()); return route.abort();
      });
      await page.goto('https://offline.invalid/report.html');
      await page.screenshot({path:path.join(out,name+'-report.png')});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth+2));
      const links = await page.locator('a[href]').evaluateAll(nodes=>nodes.map(n=>n.getAttribute('href')));
      for (const href of [...new Set(links)]) {
        const target=decodeURIComponent(href.split('#')[0]);
        const sample=bundle.samples[target], file=bundle.files[target];
        assert(sample || file || target==='뷰어.html',target);
        await page.locator('a').evaluateAll((nodes, href)=>{
          const node=nodes.find(n=>n.getAttribute('href')===href); node.click();
        },href);
        await page.waitForFunction(()=>!document.getElementById('portable-dialog').hidden);
        if (sample || target==='뷰어.html') {
          const frame = await page.locator('#portable-body iframe').elementHandle();
          const child = await frame.contentFrame();
          await child.waitForFunction(()=>document.querySelector('#plot')?.data?.some(t=>t.meta?.role==='measurement'),null,{timeout:15000});
          const ids=await child.evaluate(()=>document.querySelector('#plot').data.filter(t=>t.meta?.role==='measurement'&&t.visible!==false&&t.visible!=='legendonly').map(t=>t.meta.sampleId).sort());
          assert.deepEqual(ids,(sample?[sample.id]:Object.values(bundle.samples).map(s=>s.id)).sort());
          await page.screenshot({path:path.join(out,name+'-'+(sample?'sample':'all')+'.png')});
        } else {
          if(file.pages?.length) {
            assert.equal(await page.locator('#portable-body img').getAttribute('src'),'data:image/jpeg;base64,'+file.pages[0]);
            if(file.pages.length>1) {
              await page.locator('#portable-next').click();
              assert.equal(await page.locator('#portable-body img').getAttribute('src'),'data:image/jpeg;base64,'+file.pages[1]);
            }
          } else if(typeof file.text==='string') {
            assert.equal(await page.locator('#portable-body pre').textContent(),file.text.split(/\r\n|\r|\n/).slice(0,500).join('\n'));
          }
          const downloading=page.waitForEvent('download');
          await page.locator('#portable-download').click();
          const download=await downloading;
          assert.equal(download.suggestedFilename(),file.name);
          assert.equal(digest(fs.readFileSync(await download.path())),digest(Buffer.from(file.data,'base64')));
          await page.screenshot({path:path.join(out,name+'-attachment.png')});
        }
        await page.locator('#portable-close').click();
        assert.equal(await page.locator('#portable-body').innerHTML(),'');
        assert.equal(await page.evaluate(()=>document.body.style.overflow),'');
        results.push({name,target});
      }
      assert.deepEqual(requests,[],`External file access: ${requests}`);
      assert.deepEqual(errors,[],`Browser errors: ${errors}`);
      await context.close();
    }
    fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({browser:browser.version(),cases:results.length,results},null,2));
    console.log(JSON.stringify({browser:browser.version(),cases:results.length}));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

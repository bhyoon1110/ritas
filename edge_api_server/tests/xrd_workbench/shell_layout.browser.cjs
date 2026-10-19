// Actual browser layout of the shared shell (native + built Chrome49 path).
// NODE_PATH=<playwright modules> node tests/xrd_workbench/shell_layout.browser.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '../../app/xrd_workbench/web');
const output = fs.mkdtempSync(path.join(os.tmpdir(), 'rist-xrd-home-'));
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/fixture') {
    const legacy = url.searchParams.get('mode') === 'chrome49';
    const step = url.searchParams.get('step');
    const current = step === 'analysis' ? 'analysis' : ['report','save'].includes(step) ? 'report' : 'viewer';
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    res.end('<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">' +
      '<link rel="stylesheet" href="/shared/shell' + (legacy ? '.legacy' : '') + '.css">' +
      '<body data-step="' + current + '"><main>XRD header layout test</main>' +
      '<script ' + (legacy ? '' : 'type="module" ') + 'src="/shared/shell' + (legacy ? '.legacy.js' : '.mjs') + '"></script>');
  } else if (/^\/shared\/[a-z_]+\.(?:mjs|css|legacy\.js|legacy\.css)$/.test(url.pathname)) {
    const file = path.join(root, url.pathname);
    res.setHeader('Content-Type', file.endsWith('.css') ? 'text/css' : 'application/javascript');
    res.end(fs.readFileSync(file));
  } else { res.setHeader('Content-Type','application/json'); res.end('[]'); }
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless:true,
    executablePath:process.env.RIST_BROWSER_EXECUTABLE || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let checks = 0;
    for (const mode of ['native','chrome49']) {
      for (const step of ['upload','viewer','analysis','report','save']) {
        for (const width of [1440,1024,390,320]) {
          await page.setViewportSize({width,height:850});
          await page.goto('http://127.0.0.1:' + server.address().port + '/fixture?mode=' + mode + '&step=' + step);
          await page.locator('#shellHome').waitFor();
          const result = await page.evaluate(() => {
            const home = document.getElementById('shellHome'), actions = home.parentElement;
            const h = home.getBoundingClientRect(), a = actions.getBoundingClientRect();
            const reset = document.getElementById('shellReset').getBoundingClientRect();
            return {first:actions.firstElementChild===home, group:actions.className,
              right:a.right, left:h.left, homeBeforeReset:h.right<=reset.left,
              sameRow:Math.abs(h.top-reset.top)<2,
              overflow:document.documentElement.scrollWidth>innerWidth,
              buttonOverflow:Array.from(actions.querySelectorAll('a,button')).some(el=>el.scrollWidth>el.clientWidth+1)};
          });
          assert.equal(result.first,true); assert.equal(result.group,'shellActions');
          assert.ok(result.homeBeforeReset && result.sameRow);
          assert.ok(result.left>=0 && result.right<=width);
          assert.equal(result.overflow,false); assert.equal(result.buttonOverflow,false);
          if (mode==='native' && step==='viewer' && [1440,390].includes(width))
            await page.locator('#appShell').screenshot({path:path.join(output, 'header-'+width+'.png')});
          await page.evaluate(() => { window.xrdBeforeLeave=async()=>false; });
          await page.locator('#shellHome').click();
          assert.ok(page.url().includes('/fixture?'), 'cancelled leave must not navigate');
          checks++;
        }
      }
    }
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:checks,browser:await browser.version(),output}));
  } finally { await browser.close(); server.close(); }
})().catch(error => {console.error(error);server.close();process.exitCode=1;});

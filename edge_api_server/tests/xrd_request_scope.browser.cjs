// Mac Chrome + shipped legacy bundle; not Windows/Supermium hardware validation.
const assert = require('node:assert/strict'), fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const {spawn} = require('node:child_process'), {chromium} = require('playwright');
const output = fs.mkdtempSync(path.join(os.tmpdir(), 'rist-xrd-request-scope-'));
const server = spawn(process.env.RIST_TEST_PYTHON || 'python3', ['-u', path.join(__dirname, 'xrd_ppt_editor_fixture.py'), output], {env:process.env});
let logs = '';
server.stderr.on('data', data => logs += data);
const ready = new Promise((resolve, reject) => {
  let data = '';
  server.stdout.on('data', chunk => { data += chunk; if (data.includes('\n')) resolve(JSON.parse(data.split('\n')[0])); });
  server.on('exit', code => reject(Error('fixture exit ' + code + ' ' + logs)));
});
(async () => {
  let browser;
  try {
    const start = await ready, origin = 'http://127.0.0.1:' + start.port;
    browser = await chromium.launch({headless:true, executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
    for (const legacy of [false, true]) {
      const page = await browser.newPage({viewport:{width:1440,height:1000}, ...(legacy ? {userAgent:'Mozilla/5.0 Chrome/49.0.2623.75 Safari/537.36'} : {})});
      const errors = [], queries = [];
      page.on('pageerror', e => errors.push(e.message));
      page.setDefaultTimeout(20000);
      let fail = false;
      await page.route('**/api/v1/requests?**', async route => {
        const query = new URL(route.request().url()).searchParams;
        queries.push(query);
        assert.equal(query.get('experimentType'), 'XRD');
        assert(!query.has('testMtdCode') && !query.has('assetNumber') && !query.has('reqNumber'));
        await route.fulfill({status:fail ? 422 : 200, contentType:'application/json', body:JSON.stringify(fail
          ? {message:'기간으로 조회 범위를 좁혀 주세요.', code:'REQUEST_SCOPE_TOO_BROAD'}
          : {page:1,pageSize:200,totalCount:1,scopeNote:'XRD: 명확한 시험명 기준으로 조회합니다.',
              items:[{requestNumber:'REQ-XRD-ONLY',testMethodCode:'X-01',testMethodName:'XRD 정성분석',sampleName:'검증 시료',testState:7,requestUserName:'김의뢰',customerName:'검증기관',testChargerName:'박담당',sampleResultNo:701,testMethodResultNo:801}]})});
      });
      try {
        await page.goto(origin+'/api/v1/xrd/workbench/report.html?project='+start.sid);
        await page.waitForFunction(() => !document.getElementById('save').disabled);
        if (legacy) assert.equal(await page.locator('script[src$="report.legacy.js"]').count(), 1);
        await page.locator('.shellStep[data-step="save"]').click();
        const filters = page.locator('.rist-request-filters'), lookup = page.locator('#ristRequestLookup');
        await filters.locator('summary').click();
        for (const source of ['cache','lims']) {
          await page.getByLabel('조회 대상', {exact:true}).selectOption(source);
          await lookup.click();
          await page.waitForFunction(() => document.querySelectorAll('#rist-request-results [role=option]').length === 1);
          assert.equal(queries.at(-1).get('source'), source);
          assert.equal(queries.at(-1).get('testState'), '1,3,5,7');
          assert.match(await filters.textContent(), /명확한 시험명 기준/);
          await page.getByRole('combobox',{name:'의뢰 검색·선택'}).fill('김의뢰 검증기관');
          await page.locator('#rist-request-result-0').click();
          assert.match(await page.locator('#rist-request-detail').textContent(),/박담당/);
          assert.match(await page.locator('#rist-request-detail').textContent(),/701/);
          assert.equal(await page.locator('#ristRequestNumber').inputValue(), 'REQ-XRD-ONLY');
          assert.equal(await page.locator('#ristExperiment').inputValue(), 'X-01');
        }
        await page.getByLabel('시험 상태', {exact:true}).selectOption('');
        await lookup.click();
        await page.waitForFunction(() => document.querySelectorAll('#rist-request-results [role=option]').length === 1);
        assert.equal(queries.at(-1).get('includeCompleted'), 'true');
        assert(!queries.at(-1).has('testState'));
        fail = true;
        await lookup.click();
        await page.waitForFunction(() => document.getElementById('rist-request-info').textContent.includes('기간으로 조회 범위'));
        assert.equal(await page.locator('#rist-request-results [role=option]').count(), 0);
        await page.setViewportSize({width:390,height:900});
        await filters.scrollIntoViewIfNeeded();
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+2));
        await page.screenshot({path:path.join(output,(legacy?'legacy':'native')+'.png'),fullPage:true});
        assert.deepEqual(errors, []);
        console.log((legacy?'legacy':'native')+': XRD automatic scope, cache/live, selection, all states, error guidance, mobile layout passed');
      } finally { await page.close(); }
    }
  } finally { if (browser) await browser.close(); server.kill(); console.log('Artifacts: '+output); }
})().catch(e => {console.error(e, logs.slice(-2000)); process.exitCode=1;});

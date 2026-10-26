// Local Chrome + Chrome49 asset-path coverage. Not Windows/Supermium hardware certification.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-dashboard-')),python=process.env.RIST_TEST_PYTHON||'python3';
const server=spawn(python,['-u',path.join(__dirname,'workspace_dashboard_fixture.py'),output],{env:process.env});let logs='';server.stderr.on('data',chunk=>logs+=chunk);
const startup=new Promise((resolve,reject)=>{let text='';server.stdout.on('data',chunk=>{text+=chunk;if(text.includes('\n'))resolve(JSON.parse(text.split('\n')[0]));});server.on('exit',code=>reject(Error('fixture '+code+' '+logs)));});
(async()=>{
  const start=await startup,origin='http://127.0.0.1:'+start.port;
  const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try{
    for(const mode of ['member','admin','limited','none','supermium144','chrome49']){
      const context=await browser.newContext({viewport:{width:1440,height:1100},extraHTTPHeaders:{'x-dashboard-test-user':mode},
        ...(mode==='chrome49'?{userAgent:'Mozilla/5.0 (Windows NT 5.1) AppleWebKit/537.36 Chrome/49.0.2623.75 Safari/537.36'}:
          mode==='supermium144'?{userAgent:'Mozilla/5.0 (Windows NT 5.1) AppleWebKit/537.36 Chrome/144.0.0.0 Safari/537.36 Supermium/144.0.0.0'}:{})});
      const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));let queries=0;page.on('request',r=>{if(r.url().endsWith('/api/v1/workspace/dashboard'))queries++;});
      try{
        const response=await page.goto(origin);assert.equal(response.status(),200);
        await page.waitForFunction(()=>document.getElementById('workspace-updated').textContent.indexOf('조회 ')===0);
        assert.equal(await page.title(),'RIST 분석 포털');assert.equal(await page.locator('[data-project]:visible').count(),mode==='none'?0:mode==='limited'?1:4);
        assert.equal(await page.locator('#workspace-admin').isVisible(),mode==='admin');
        if(mode==='chrome49')assert(await page.locator('script[src*="/assets/browser-compat/"]').count()>0);
        if(mode==='supermium144'){
          assert.equal(await page.locator('script[src*="/assets/browser-compat/"]').count(),0);
          assert.equal(await page.evaluate(()=>window.RIST_CLIENT_PROFILE.browser),'supermium');
        }
        if(mode!=='none'){
          assert.equal(await page.locator('#count-active').textContent(),'1');
          await page.locator('[data-summary="active"]').click();assert.equal(await page.locator('.recent-row').count(),1);
          await page.locator('#workspace-filter-clear').click();await page.locator('#workspace-search').fill('C272-0');assert.equal(await page.locator('.recent-row').count(),1);
          await page.locator('#workspace-search').fill('');
        }
        assert.equal(queries,1);await page.evaluate(()=>window.scrollTo(0,0));
        await page.screenshot({path:path.join(output,mode+'-desktop.png'),fullPage:true});
        for(const width of [1024,390,320]){
          await page.setViewportSize({width,height:900});
          assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),'horizontal overflow at '+mode+' '+width);
          assert(await page.locator('.workspace-actions button,#workspace-refresh,#workspace-filter-clear').evaluateAll(nodes=>nodes.filter(n=>n.offsetWidth).every(n=>n.scrollWidth<=n.clientWidth+2)),'button clipping');
          if(width===390){
            await page.locator('#workspace-search').blur();
            await page.evaluate(()=>window.scrollTo(0,0));
            await page.screenshot({path:path.join(output,mode+'-mobile-top.png')});
            await page.locator('#recent-panel').scrollIntoViewIfNeeded();
            await page.screenshot({path:path.join(output,mode+'-mobile-work.png')});
            await page.locator('#transfers-panel').scrollIntoViewIfNeeded();
            await page.screenshot({path:path.join(output,mode+'-mobile-status.png')});
            await page.evaluate(()=>window.scrollTo(0,0));
          }
        }
        await page.route('**/api/v1/workspace/dashboard',r=>r.fulfill({status:503,contentType:'application/json',body:'{}'}));
        await page.locator('#workspace-refresh').click();await page.waitForFunction(()=>document.getElementById('workspace-notice').textContent.indexOf('불러오지 못했습니다')!==-1);
        assert.equal(await page.locator('#count-active').textContent(),'—');assert.equal(await page.locator('.recent-row').count(),0);
        assert.deepEqual(errors,[]);console.log(mode+': scope, filters, desktop/mobile, error-state passed');
      }catch(e){await page.screenshot({path:path.join(output,mode+'-failure.png'),fullPage:true});throw e;}finally{await context.close();}
    }
  }finally{await browser.close();server.kill();console.log('Artifacts: '+output);}
})().catch(e=>{server.kill();console.error(e);console.error(logs.slice(-6000));process.exitCode=1;});

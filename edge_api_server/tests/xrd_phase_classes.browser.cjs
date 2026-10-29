// Real loopback report save + browser interaction; only the delayed LLM reply is mocked.
// This tests Mac Chrome and the shipped legacy bundle, not Windows/Supermium hardware.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-xrd-phase-'));
const server=spawn(process.env.RIST_TEST_PYTHON||'python3',['-u',path.join(__dirname,'xrd_ppt_editor_fixture.py'),output],{env:process.env});
let logs='';server.stderr.on('data',data=>logs+=data);
const startup=new Promise((resolve,reject)=>{let data='';server.stdout.on('data',chunk=>{data+=chunk;if(data.includes('\n'))resolve(JSON.parse(data.split('\n')[0]));});server.on('exit',code=>reject(Error('fixture exited '+code+' '+logs)));});
(async()=>{
  let browser;
  try {
    const start=await startup,origin='http://127.0.0.1:'+start.port,base='/api/v1/xrd/workbench';
    browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
    for(const legacy of [false,true]){
      const page=await browser.newPage({viewport:{width:1440,height:1000},...(legacy?{userAgent:'Mozilla/5.0 Chrome/49.0.2623.75 Safari/537.36'}:{})});
      const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());page.setDefaultTimeout(30000);
      let release;const modelWait=new Promise(r=>release=r);
      await page.route('**/email-draft',async route=>{await modelWait;await route.fulfill({contentType:'application/json',body:JSON.stringify({subject:'선택 전 분석 기반 메일',body:'검토할 메일 초안',status:'llm',message:'테스트 응답'})});});
      try{
        await page.goto(origin+base+'/report.html?project='+start.sid);
        await page.waitForFunction(()=>!document.getElementById('save').disabled&&!!document.querySelector('[data-tab="email"]'));
        if(legacy)assert.equal(await page.locator('script[src$="report.legacy.js"]').count(),1);
        await page.locator('[data-tab="classes"]').click();const rows=page.locator('[data-pc-sample-id]'),row=rows.first();await row.waitFor();
        await row.evaluate(e=>window.testPhaseSelect=e);await row.focus();await row.selectOption('presumed');
        assert.equal(await row.inputValue(),'presumed');assert(await row.evaluate(e=>e===window.testPhaseSelect&&e===document.activeElement));
        const pending=page.waitForRequest(r=>r.url().endsWith('/email-draft'));
        await page.locator('[data-tab="email"]').click();if(await page.locator('#generateEmail').isEnabled())await page.locator('#generateEmail').click();await pending;
        await page.locator('[data-tab="classes"]').click();await row.waitFor();assert(await row.isEnabled());assert(await page.locator('#title').isDisabled());
        await row.selectOption('trace');assert(await row.isEnabled());assert(await page.locator('#pcApply').isDisabled());
        await page.locator('#classForm').screenshot({path:path.join(output,(legacy?'legacy':'native')+'-during-mail.png')});
        const reply=page.waitForResponse(r=>r.url().endsWith('/email-draft'));release();await reply;await page.waitForFunction(()=>!document.getElementById('save').disabled);
        assert.equal(await row.inputValue(),'trace');assert.match(await page.locator('#emailStatus').textContent(),/분석·보고서 입력이 변경/);
        const savedResponse=page.waitForResponse(r=>r.url().endsWith('/api/reports/'+start.sid)&&r.request().method()==='PUT');
        await page.locator('#save').click();assert.equal((await savedResponse).status(),200);await page.waitForFunction(()=>!document.getElementById('save').disabled);
        const result=await page.request.get(origin+base+'/api/reports/'+start.sid);assert.equal(result.status(),200);
        assert((await result.json()).document.phase_classes.labels.some(l=>l.class==='trace'&&l.origin==='manual'));
        await page.reload();await page.waitForFunction(()=>!document.getElementById('save').disabled);await page.locator('[data-tab="classes"]').click();await row.waitFor();
        assert.equal(await row.inputValue(),'trace');assert(await row.isEnabled());
        await page.setViewportSize({width:390,height:844});await page.locator('[data-work-view="editor"]').click();await row.selectOption('major');
        assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));assert(await row.isEnabled());
        await page.screenshot({path:path.join(output,(legacy?'legacy':'native')+'-mobile.png'),fullPage:true});
        assert.deepEqual(errors,[]);console.log((legacy?'Legacy':'Native')+': selection focus, slow mail, phase editing, actual save/reload and mobile layout passed');
      }catch(e){await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});throw e;}
      finally{release();await page.close();}
    }
  }finally{if(browser)await browser.close();server.kill();console.log('Artifacts: '+output);}
})().catch(e=>{console.error(e,logs.slice(-3000));process.exitCode=1;});

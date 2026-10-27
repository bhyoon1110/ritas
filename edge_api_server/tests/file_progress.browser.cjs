// Real Chrome rendering/downloads + legacy assets; not a Windows/Supermium VM test.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-file-progress-'));
const server=spawn(process.env.RIST_TEST_PYTHON||'python3',['-u',path.join(__dirname,'xrd_ppt_editor_fixture.py'),output],{env:process.env});
let logs='';server.stderr.on('data',b=>logs+=b);
const startup=new Promise((resolve,reject)=>{let data='';server.stdout.on('data',b=>{data+=b;if(data.includes('\n'))resolve(JSON.parse(data.split('\n')[0]));});server.on('exit',code=>reject(Error('server '+code+' '+logs)));});
(async()=>{
  const {port,sid}=await startup,origin='http://127.0.0.1:'+port,base='/api/v1/xrd/workbench';
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_BIN||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try{
    for(const legacy of [false,true]){
      const page=await browser.newPage({viewport:{width:1440,height:960},...(legacy?{userAgent:'Mozilla/5.0 Chrome/49.0.2623.75 Safari/537.36'}:{})});
      const errors=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(45000);
      let state='queued';
      // Deliberately slow/fail a job to verify visible waiting and recovery.
      await page.route('**/api/reports/*/exports',route=>route.fulfill({json:{job_id:'qa-progress',status:'queued',kind:'zip'}}));
      await page.route('**/api/jobs/qa-progress',route=>route.fulfill({json:{job_id:'qa-progress',status:state,kind:'zip',error:'QA: 보고서 생성 실패'}}));
      await page.route('**/api/jobs/qa-progress/file',route=>route.fulfill({headers:{'Content-Disposition':'attachment; filename="qa.zip"'},contentType:'application/zip',body:'QA download'}));
      await page.goto(origin+base+'/report.html?project='+sid+'&step=save');
      await page.waitForFunction(()=>document.getElementById('title').value&&!document.getElementById('zipExport').disabled);
      if(legacy)assert.equal(await page.locator('script[src$="report.legacy.js"]').count(),1);
      await page.locator('#zipExport').click();
      await page.getByText('서버 처리 대기 중…',{exact:true}).last().waitFor();
      const card=page.locator('.rist-file-progress');assert(await card.locator('[role=progressbar]').isVisible());
      assert.equal(await card.locator('[role=progressbar]').getAttribute('aria-valuenow'),null);
      await page.screenshot({path:path.join(output,legacy?'legacy-desktop.png':'desktop.png')});
      state='running';await page.getByText('서버에서 자료를 처리하는 중…',{exact:true}).last().waitFor();
      await page.setViewportSize({width:390,height:844});
      const box=await card.boundingBox();assert(box.x>=0&&box.y>=0&&box.x+box.width<=390&&box.y+box.height<=844);
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
      await page.screenshot({path:path.join(output,legacy?'legacy-mobile.png':'mobile.png')});
      state='failed';await page.locator('.rist-file-progress[data-state=error]').waitFor();assert(!await card.locator('[role=progressbar]').isVisible());
      assert(await page.locator('#zipExport').isEnabled());await card.getByRole('button',{name:'저장 상태 닫기'}).click();
      state='completed';let pending=page.waitForEvent('download');await page.locator('#zipExport').click();await pending;
      await page.locator('.rist-file-progress[data-state=done]').waitFor();assert.match(await card.textContent(),/브라우저 다운로드 목록/);
      await page.setViewportSize({width:1440,height:960});
      await page.goto(origin+base+'/?project='+sid+'&step=viewer');
      await page.waitForFunction(()=>document.getElementById('plot').data?.some(t=>t.meta?.role==='measurement'));
      await page.evaluate(()=>{const original=Plotly.toImage;Plotly.toImage=function(){const self=this,args=arguments;return new Promise(r=>setTimeout(r,700)).then(()=>original.apply(self,args));};});
      pending=page.waitForEvent('download');await page.locator('#quickImageExport').click();
      await page.locator('.rist-file-progress[data-state=working]').waitFor();
      const image=await pending;await image.saveAs(path.join(output,(legacy?'legacy-':'')+'graph.png'));assert.equal(await image.failure(),null);
      await page.locator('.rist-file-progress[data-state=done]').waitFor();
      assert.deepEqual(errors,[]);await page.close();console.log((legacy?'Legacy':'Native')+': queued/running/error/retry, browser handoff, real PNG export, desktop/mobile passed');
    }
  }finally{await browser.close();server.kill();console.log('Artifacts: '+output);}
})().catch(e=>{server.kill();console.error(e);console.error(logs.slice(-3000));process.exitCode=1;});

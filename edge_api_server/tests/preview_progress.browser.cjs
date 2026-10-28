// Mac Chrome/native + Chrome49 bundle; slow/failing requests are controlled locally.
// XRD uses real rendering. Shared FTIR/Raman/TEM editor UI uses isolated job responses.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-preview-progress-')),servers=[];
const pause=ms=>new Promise(r=>setTimeout(r,ms));
function fixture(name,dir){
  const child=spawn(process.env.RIST_TEST_PYTHON||'python3',['-u',path.join(__dirname,name),path.join(output,dir)],{env:process.env});servers.push(child);
  let logs='';child.stderr.on('data',b=>logs+=b);
  return new Promise((resolve,reject)=>{let data='';child.stdout.on('data',b=>{data+=b;if(data.includes('\n'))resolve(JSON.parse(data.split('\n')[0]));});child.on('exit',code=>reject(Error(name+' '+code+' '+logs)));});
}
function gate(){let release;const promise=new Promise(r=>release=r);return {promise,release};}
(async()=>{
  const [xrd,spectrum]=await Promise.all([fixture('xrd_ppt_editor_fixture.py','xrd'),fixture('spectrum_workflow_fixture.py','spectrum')]);
  const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  let sampleImage;
  try{
    for(const legacy of [false,true]){
      const options={viewport:{width:1440,height:1000},...(legacy?{userAgent:'Mozilla/5.0 Chrome/49.0.2623.75 Safari/537.36'}:{})};
      const page=await browser.newPage(options),errors=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(30000);
      const base='http://127.0.0.1:'+xrd.port+'/api/v1/xrd/workbench';
      let hold=gate(),fail=false;
      await page.route('**/draft-preview',async route=>{
        await hold.promise;
        if(fail)return route.fulfill({status:503,json:{detail:'테스트 생성 실패'}});
        const response=await route.fetch();assert.equal(response.status(),200);
        const value=await response.json();sampleImage=value.pages[0].image;
        return route.fulfill({response,json:value});
      });
      await page.goto(base+'/report.html?project='+xrd.sid,{waitUntil:'domcontentloaded'});
      const running=page.locator('.rist-task-progress.is-running').filter({has:page.locator('#page-status')});
      await running.waitFor();await pause(1100);
      await page.locator('#previewPane').screenshot({path:path.join(output,'xrd-'+legacy+'-waiting.png')});
      assert.match(await running.locator('.rist-task-elapsed').textContent(),/0:0[1-9]/);
      hold.release();
      await page.waitForFunction(()=>document.getElementById('slide-image').naturalWidth>0&&!document.getElementById('page-status').closest('.rist-task-progress').classList.contains('is-running'));
      fail=true;await page.locator('#draftRefresh').click();await page.getByText(/미리보기 생성 실패:/).waitFor();assert.equal(await running.count(),0);
      fail=false;hold=gate();await page.locator('#draftRefresh').click();await running.waitFor();
      const before=await page.locator('#slide-stage').boundingBox();hold.release();
      await page.waitForFunction(()=>!document.getElementById('page-status').closest('.rist-task-progress').classList.contains('is-running'));
      const after=await page.locator('#slide-stage').boundingBox();assert(Math.abs(before.y-after.y)<2,JSON.stringify({before,after}));
      await page.setViewportSize({width:390,height:844});hold=gate();await page.locator('#draftRefresh').click();await running.waitFor();
      await running.scrollIntoViewIfNeeded();assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
      await page.screenshot({path:path.join(output,'xrd-'+legacy+'-mobile.png')});hold.release();
      await page.waitForFunction(()=>!document.getElementById('page-status').closest('.rist-task-progress').classList.contains('is-running'));
      assert.deepEqual(errors,[]);await page.close();console.log('XRD '+legacy+': real preview, wait/error/retry, stable layout, mobile passed');

      for(const kind of ['ftir','raman','tem']){
        const p=await browser.newPage(options),issues=[];p.setDefaultTimeout(30000);p.on('pageerror',e=>issues.push(e.message));
        let layoutGate=gate(),imageGate=gate(),layoutFail=false,imageRequested=false;
        const document={title:kind+' 분석 보고서',completionDate:'2026-10-28',transfer:{},sections:[],variables:[],auxiliary:{}};
        await p.route('**/api/v1/**',async route=>{
          const url=new URL(route.request().url()),pathname=url.pathname;
          if(pathname.endsWith('/editor-data'))return route.fulfill({json:{document,job:{jobId:'lookup-test',status:'completed'}}});
          if(pathname.endsWith('/edit-layout')){
            await layoutGate.promise;
            return layoutFail?route.fulfill({status:503,json:{message:'테스트 미리보기 실패'}}):route.fulfill({json:{ratio:16/9,pages:[{id:'cover',title:'표지',regions:[]}],pageEditing:true,canvasEditing:true}});
          }
          if(pathname.includes('/edit-slides/')){
            imageRequested=true;await imageGate.promise;
            return route.fulfill({contentType:'image/png',headers:{'Cache-Control':'no-store'},body:Buffer.from(sampleImage.split(',')[1],'base64')});
          }
          return route.fulfill({json:{items:[]}});
        });
        await p.goto('http://127.0.0.1:'+spectrum.port+'/__test__/request-editor/'+kind,{waitUntil:'domcontentloaded'});
        const active=p.locator('.rist-task-progress.is-running').filter({has:p.locator('#page-status')});await active.waitFor();
        layoutGate.release();for(let i=0;i<100&&!imageRequested;i++)await pause(10);assert(imageRequested);assert.equal(await active.count(),1);
        await pause(1100);await p.locator('#page-status').scrollIntoViewIfNeeded();
        await p.screenshot({path:path.join(output,kind+'-'+legacy+'-image-wait.png')});
        imageGate.release();await p.waitForFunction(()=>document.getElementById('slide-image').naturalWidth>0&&!document.getElementById('page-status').closest('.rist-task-progress').classList.contains('is-running'));
        layoutFail=true;await p.locator('#reload-preview').click();await p.getByText('테스트 미리보기 실패',{exact:true}).waitFor();assert.equal(await active.count(),0);
        layoutFail=false;await p.locator('#reload-preview').click();await p.waitForFunction(()=>document.getElementById('page-status').textContent.includes('표지')&&!document.getElementById('page-status').closest('.rist-task-progress').classList.contains('is-running'));
        await p.setViewportSize({width:390,height:844});layoutGate=gate();await p.locator('#reload-preview').click();await active.waitFor();
        await active.scrollIntoViewIfNeeded();assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
        await p.screenshot({path:path.join(output,kind+'-'+legacy+'-mobile.png')});layoutGate.release();
        await p.waitForFunction(()=>!document.getElementById('page-status').closest('.rist-task-progress').classList.contains('is-running'));
        assert.deepEqual(issues,[]);await p.close();console.log(kind+' '+legacy+': slow layout + image, failure/retry, desktop/mobile passed');
      }
    }
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>{servers.forEach(s=>s.kill());console.log('Artifacts: '+output);});

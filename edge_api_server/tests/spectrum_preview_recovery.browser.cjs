// Real report creation, stale IndexedDB recovery and regeneration. UA routing is
// not Windows/Supermium hardware verification; this runs the local Chromium engine.
const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-preview-recovery-'));
const server=spawn(process.env.RIST_TEST_PYTHON||'python3',['-u',path.join(__dirname,'spectrum_workflow_fixture.py'),output],{env:process.env});
let logs='';server.stderr.on('data',b=>logs+=b);
const port=new Promise((resolve,reject)=>{
  let data='';server.stdout.on('data',b=>{data+=b;if(data.includes('\n'))resolve(JSON.parse(data.split('\n')[0]).port);});
  server.on('exit',code=>{if(code)reject(Error(logs));});
});
const raw=Buffer.from(Array.from({length:241},(_,i)=>{const x=400+i*15;return x+','+(.05+Math.exp(-Math.pow(x-1700,2)/(2*55**2)));}).join('\n'));
(async()=>{
  const origin='http://127.0.0.1:'+await port;
  for(let i=0;i<100;i++){try{if((await fetch(origin+'/ftir')).ok)break;}catch(_){}await new Promise(r=>setTimeout(r,100));}
  const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try{
    for(const version of [144,49])for(const kind of ['ftir','raman']){
      const context=await browser.newContext({viewport:{width:1440,height:1050},userAgent:'Mozilla/5.0 (Windows NT 5.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/'+version+'.0.0.0 Safari/537.36'+(version===144?' Supermium/144.0':'')});
      const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
      page.setDefaultTimeout(30000);
      async function waitEditor(){
        await page.waitForFunction(()=>{const f=document.getElementById('sw-report-editor');return f&&!f.hidden&&f.contentDocument&&f.contentDocument.querySelector('#edit-page option');},null,{timeout:180000});
        await page.frameLocator('#sw-report-editor').locator('#slide-image').evaluate(img=>new Promise((resolve,reject)=>{
          if(img.complete&&img.naturalWidth)resolve();else{img.addEventListener('load',resolve,{once:true});img.addEventListener('error',reject,{once:true});}
        }));
        await page.waitForFunction(()=>!document.getElementById('sw-step-4').disabled);
      }
      try{
        const response=await page.goto(origin+'/'+kind);
        assert.equal(response.headers()['x-rist-browser-profile'],version===144?'supermium':'chrome49');
        await page.waitForFunction(()=>document.getElementById('sw-step-2')&&!document.getElementById('sw-step-2').disabled);
        await page.locator('#'+kind+'-file-input').setInputFiles({name:'recovery.'+(kind==='ftir'?'dpt':'txt'),mimeType:'text/plain',buffer:raw});
        await page.waitForFunction(()=>document.getElementById('spectrum-workflow').dataset.step==='2'&&!document.getElementById('sw-save-graph').disabled);
        await page.locator('#sw-graph-name').fill('보존할 편집 그래프');await page.locator('#sw-save-graph').click();
        await page.waitForFunction(()=>document.querySelectorAll('.sw-card').length===1&&!document.getElementById('sw-build-report').disabled);
        const image=await page.locator('.sw-card img').getAttribute('src');
        await page.locator('#sw-build-report').click();await waitEditor();
        const first=await page.locator('#sw-report-editor').getAttribute('src'),id=first.split('/').slice(-2)[0];
        await page.locator('#sw-step-4').click();
        assert.equal((await page.request.post(origin+'/__test__/expire-preview/'+id)).status(),200);
        // IndexedDB still has the old job: no /edit navigation may reach the dead job.
        const brokenNavigations=[];page.on('request',r=>{if(r.isNavigationRequest()&&r.url().includes('/'+id+'/edit'))brokenNavigations.push(r.url());});
        await page.reload();
        await page.waitForFunction(()=>document.getElementById('sw-guide-detail')?.textContent.includes('만료되었습니다')&&!document.getElementById('sw-build-report').disabled);
        assert.equal(await page.locator('#spectrum-workflow').getAttribute('data-step'),'3');
        assert.equal(await page.locator('#sw-report-editor').getAttribute('src'),null);assert.deepEqual(brokenNavigations,[]);
        assert.equal(await page.locator('.sw-card img').getAttribute('src'),image);
        assert(await page.locator('#sw-step-4').isDisabled());
        await page.screenshot({path:path.join(output,kind+'-'+version+'-recovery.png'),fullPage:true});
        await page.locator('#sw-build-report').click();await waitEditor();
        const regenerated=await page.locator('#sw-report-editor').getAttribute('src');assert.notEqual(regenerated,first);
        // A valid job survives a temporary lookup failure and retries without generating.
        await page.locator('#sw-step-2').click();
        await page.route('**/editor-status',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({status:503,code:'TEMPORARY',message:'일시적인 연결 오류'})}));
        await page.locator('#sw-step-3').click();await page.locator('#sw-retry-editor').waitFor();
        assert(await page.locator('#sw-step-4').isDisabled());
        await page.unroute('**/editor-status');await page.locator('#sw-retry-editor').click();await waitEditor();
        assert.equal(await page.locator('#sw-report-editor').getAttribute('src'),regenerated);
        await page.screenshot({path:path.join(output,kind+'-'+version+'-preview.png'),fullPage:true});
        assert.deepEqual(errors,[]);
        console.log(kind+'/'+version+': initial preview, expired job, preserved graph, real regeneration, transient retry passed');
      }catch(error){await page.screenshot({path:path.join(output,kind+'-'+version+'-failure.png'),fullPage:true});throw error;}
      finally{await context.close();}
    }
    console.log(JSON.stringify({browser:await browser.version(),output}));
  }finally{await browser.close();server.kill('SIGTERM');}
})().catch(error=>{console.error(error);console.error(logs.slice(-4000));console.error('Artifacts:',output);server.kill('SIGTERM');process.exitCode=1;});

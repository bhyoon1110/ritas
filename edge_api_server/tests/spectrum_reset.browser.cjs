// Actual IndexedDB, file chooser, RAW analysis and Plotly in Chromium.
// User-agent compatibility routes are not Windows/Supermium hardware tests.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-spectrum-reset-'));
const server=spawn(process.env.RIST_TEST_PYTHON||'python3',['-u',path.join(__dirname,'spectrum_workflow_fixture.py'),output],{env:process.env});
let logs='';server.stderr.on('data',c=>logs+=c);
const port=new Promise((resolve,reject)=>{let text='';server.stdout.on('data',c=>{text+=c;if(text.includes('\n'))resolve(JSON.parse(text.split('\n')[0]).port);});server.on('exit',code=>{if(code)reject(Error(logs));});});
const raw=Buffer.from(Array.from({length:241},(_,i)=>{const x=400+i*15;return x+','+(.05+Math.exp(-Math.pow(x-1700,2)/(2*55**2)));}).join('\n'));
(async()=>{
  const origin='http://127.0.0.1:'+await port;
  for(let i=0;i<80;i++){try{if((await fetch(origin+'/ftir')).ok)break;}catch(_){}await new Promise(r=>setTimeout(r,100));}
  const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try{
    for(const profile of ['native144','chrome49'])for(const kind of ['ftir','raman']){
      const context=await browser.newContext({viewport:{width:1366,height:900},userAgent:profile==='chrome49'
        ?'Mozilla/5.0 (Windows NT 5.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/49.0.2623.75 Safari/537.36'
        :'Mozilla/5.0 (Windows NT 5.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.7559.256 Safari/537.36 Supermium/144.0'});
      const page=await context.newPage(),errors=[];let confirmReset=true;
      page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>confirmReset?d.accept():d.dismiss());page.setDefaultTimeout(30000);
      const gd=kind==='ftir'?'peak-plot':'raman-plot';
      const ready=()=>page.waitForFunction(()=>document.getElementById('sw-step-2')&&!document.getElementById('sw-step-2').disabled);
      async function saved(){await page.waitForFunction(()=>document.querySelectorAll('.sw-card').length===2&&!document.getElementById('sw-save-graph').disabled);}
      async function storage(action){return page.evaluate(({kind,action})=>new Promise((resolve,reject)=>{
        const opening=indexedDB.open('rist-'+kind+'-workspace-v1',1);
        opening.onerror=()=>reject(opening.error);opening.onsuccess=()=>{
          const db=opening.result,tx=db.transaction(db.objectStoreNames[0],action==='seed'?'readwrite':'readonly'),store=tx.objectStore(db.objectStoreNames[0]),result={};
          if(action==='seed')store.put({sentinel:true},'graph-collection-v1:other-user');
          for(const key of ['current','graph-collection-v1:workflow-test','graph-collection-v1:other-user']){const r=store.get(key);r.onsuccess=()=>result[key]=r.result;}
          tx.oncomplete=()=>{db.close();resolve(result);};tx.onabort=()=>reject(tx.error);
        }
      }),{kind,action});}
      try{
        const response=await page.goto(origin+'/'+kind);await ready();
        assert.equal(response.headers()['x-rist-browser-profile'],profile==='chrome49'?'chrome49':'supermium');
        assert(await page.locator('#sw-upload').isVisible());assert.equal(await page.locator('#'+gd).isVisible(),false);
        const chooser=page.locator('#sw-upload .'+kind+'-file-button');
        const chooseFile=page.waitForEvent('filechooser');await chooser.click();
        await (await chooseFile).setFiles({name:'reset.'+(kind==='ftir'?'dpt':'txt'),mimeType:'text/plain',buffer:raw});
        await page.waitForFunction(()=>document.getElementById('spectrum-workflow').dataset.step==='2'&&!document.getElementById('sw-save-graph').disabled);
        const templateDownload=page.waitForEvent('download');await page.locator('[data-template-save]').click();
        const template=await templateDownload;
        await page.locator('[data-template-file]').setInputFiles({name:'reset-template.json',mimeType:'application/json',buffer:fs.readFileSync(await template.path())});
        await page.waitForFunction(()=>!document.querySelector('[data-template-clear]').disabled);
        await page.locator('#sw-graph-name').fill('저장 A');await page.locator('#sw-save-graph').click();
        await page.waitForFunction(()=>document.querySelectorAll('.sw-card').length===1&&!document.getElementById('sw-save-graph').disabled);
        await page.locator('#sw-graph-name').fill('저장 B');await page.locator('#sw-save-copy').click();await saved();
        await storage('seed');await page.waitForTimeout(600);
        confirmReset=false;await page.locator('#'+kind+'-clear').click();
        assert.equal(await page.locator('.sw-card').count(),2);assert.equal(await page.locator('#'+kind+'-file-list button').count(),1);
        assert.equal(await page.locator('[data-template-clear]').isDisabled(),false);
        confirmReset=true;
        // Synchronous IndexedDB put failure must abort the preceding current-key deletion too.
        await page.evaluate(()=>{
          window.resetOriginalPut=IDBObjectStore.prototype.put;
          IDBObjectStore.prototype.put=function(value,key){if(String(key).indexOf('graph-collection-v1:')===0&&value.graphs&&value.graphs.length===0)throw new DOMException('test quota','QuotaExceededError');return window.resetOriginalPut.apply(this,arguments);};
        });
        await page.locator('#'+kind+'-clear').click();
        await page.waitForFunction(()=>document.querySelector('.sw-status').textContent.includes('초기화하지 못했습니다'));
        const failed=await storage('read');assert.equal(failed.current.files.length,1);assert.equal(failed['graph-collection-v1:workflow-test'].graphs.length,2);
        assert.equal(await page.locator('.sw-card').count(),2);
        assert.equal(await page.locator('[data-template-clear]').isDisabled(),false);
        await page.evaluate(()=>{IDBObjectStore.prototype.put=window.resetOriginalPut;delete window.resetOriginalPut;});
        await page.locator('#sw-step-3').click();await page.locator('#'+kind+'-clear').click();
        await page.waitForFunction(()=>document.getElementById('spectrum-workflow').dataset.step==='1'&&!document.getElementById('sw-step-2').disabled);
        assert(await page.locator('#sw-upload').isVisible());assert.equal(await page.locator('.sw-card').count(),0);
        assert.equal(await page.locator('#'+kind+'-file-list button').count(),0);
        assert.equal(await page.locator('[data-template-clear]').isDisabled(),true);
        assert.equal(await page.evaluate(id=>document.getElementById(id).data.filter(t=>t.meta&&t.meta.rist_sample_parent).length,gd),0);
        await page.waitForTimeout(600);
        const cleared=await storage('read');assert.equal(cleared['graph-collection-v1:workflow-test'].graphs.length,0);
        assert.equal(cleared['graph-collection-v1:other-user'].sentinel,true);
        assert(!cleared.current||!cleared.current.files.length);
        await page.screenshot({path:path.join(output,kind+'-'+profile+'-reset.png'),fullPage:true});
        await page.reload();await ready();assert.equal(await page.locator('#spectrum-workflow').getAttribute('data-step'),'1');
        assert.equal(await page.locator('.sw-card').count(),0);assert.equal(await page.locator('#'+kind+'-file-list button').count(),0);
        await page.setViewportSize({width:390,height:844});await page.waitForTimeout(500);assert(await page.locator('#sw-upload').isVisible());
        assert.equal(await page.locator('.'+kind+'-app-bar').count(),1);
        assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
        await page.screenshot({path:path.join(output,kind+'-'+profile+'-mobile.png'),fullPage:true});
        const newChooser=page.waitForEvent('filechooser');await chooser.focus();await page.keyboard.press('Enter');
        await (await newChooser).setFiles({name:'reset.'+(kind==='ftir'?'dpt':'txt'),mimeType:'text/plain',buffer:raw});
        await page.waitForFunction(()=>document.getElementById('spectrum-workflow').dataset.step==='2'&&!document.getElementById('sw-save-graph').disabled);
        assert.equal(await page.locator('.sw-card').count(),0);assert.equal(await page.locator('#'+kind+'-file-list button').count(),1);
        assert.deepEqual(errors,[]);console.log(kind+'/'+profile+': chooser, cancel, atomic failure, full reset, user isolation, reload, mobile and same-file re-upload passed');
      }catch(error){await page.screenshot({path:path.join(output,kind+'-'+profile+'-failure.png'),fullPage:true});throw error;}
      finally{await context.close();}
    }
    console.log(JSON.stringify({browser:await browser.version(),output}));
  }finally{await browser.close();server.kill('SIGTERM');}
})().catch(error=>{console.error(error);console.error(logs.slice(-5000));console.error('Artifacts:',output);server.kill('SIGTERM');process.exitCode=1;});

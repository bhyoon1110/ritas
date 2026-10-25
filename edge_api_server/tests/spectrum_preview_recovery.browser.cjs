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
      let creations=0;
      page.on('request',r=>{if(r.method()==='POST'&&/\/report\/upload-sessions\/[^/]+\/complete$/.test(r.url()))creations++;});
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
        // Both the top step button and the lower next action must auto-create.
        await page.locator(version===144?'#sw-step-3':'#sw-next').click();await waitEditor();assert.equal(creations,1);
        const first=await page.locator('#sw-report-editor').getAttribute('src'),id=first.split('/').slice(-2)[0];
        await page.locator('#sw-step-2').click();await page.locator('#sw-step-3').click();await waitEditor();
        assert.equal(creations,1);assert.equal(await page.locator('#sw-report-editor').getAttribute('src'),first);
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
        await page.locator('#sw-step-2').click();await page.locator('#sw-next').click();await waitEditor();assert.equal(creations,2);
        const regenerated=await page.locator('#sw-report-editor').getAttribute('src');assert.notEqual(regenerated,first);
        // A valid job survives a temporary lookup failure and retries without generating.
        await page.locator('#sw-step-2').click();
        await page.route('**/editor-status',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({status:503,code:'TEMPORARY',message:'일시적인 연결 오류'})}));
        await page.locator('#sw-step-3').click();await page.locator('#sw-retry-editor').waitFor();
        assert(await page.locator('#sw-step-4').isDisabled());
        await page.unroute('**/editor-status');await page.locator('#sw-retry-editor').click();await waitEditor();
        assert.equal(await page.locator('#sw-report-editor').getAttribute('src'),regenerated);
        assert.equal(creations,2);
        // The embedded editor must align both columns, including a short tablet
        // viewport. On narrow screens the form stays independently scrollable
        // above the preview instead of inheriting the desktop column height.
        for(const viewport of [{width:1440,height:1050},{width:1280,height:800},{width:1100,height:700},{width:390,height:844},{width:320,height:640}]){
          await page.setViewportSize(viewport);
          const frame=page.frameLocator('#sw-report-editor');
          const geometry=await frame.locator('.layout').evaluate(layout=>{
            const form=document.querySelector('.form-pane'),editor=document.getElementById('editor'),preview=document.querySelector('.preview-pane');
            const a=form.getBoundingClientRect(),b=preview.getBoundingClientRect(),c=editor.getBoundingClientRect();
            editor.scrollTop=editor.scrollHeight;
            const bottom=editor.scrollTop>0&&Math.abs(editor.scrollHeight-editor.clientHeight-editor.scrollTop)<=1;
            editor.scrollTop=0;
            return {columns:getComputedStyle(layout).display==='flex',leftTop:a.top,rightTop:b.top,leftBottom:c.bottom,rightBottom:b.bottom,
              leftHeight:c.height,scrollable:bottom,mailTop:document.getElementById('mail-editor').getBoundingClientRect().top,
              pageWidth:document.documentElement.scrollWidth,viewport:innerWidth};
          });
          assert(geometry.scrollable,JSON.stringify(geometry));
          assert(geometry.pageWidth<=geometry.viewport+1,JSON.stringify(geometry));
          assert(geometry.mailTop>=geometry.rightBottom-1,JSON.stringify(geometry));
          if(geometry.columns){
            assert(Math.abs(geometry.leftTop-geometry.rightTop)<=1,JSON.stringify(geometry));
            assert(Math.abs(geometry.leftBottom-geometry.rightBottom)<=1,JSON.stringify(geometry));
          }else{
            assert(geometry.leftHeight<=350,JSON.stringify(geometry));
            assert(geometry.leftBottom<=geometry.rightTop,JSON.stringify(geometry));
          }
          if(viewport.width===1280){
            // Reflow after collapsing the thumbnail rail and expanding object
            // properties must not leave the form at an old/fixed height.
            await frame.locator('#page-sidebar-toggle').click();
            await frame.locator('#canvas-toggle').click();
            await frame.locator('[aria-label="페이지 개체 선택"]').selectOption({index:1});
            assert(await frame.locator('.canvas-properties').isVisible());
            const expanded=await frame.locator('.layout').evaluate(()=>{
              const a=document.getElementById('editor').getBoundingClientRect(),b=document.querySelector('.preview-pane').getBoundingClientRect();
              return {left:a.bottom,right:b.bottom};
            });
            assert(Math.abs(expanded.left-expanded.right)<=1,JSON.stringify(expanded));
            await frame.locator('#canvas-toggle').click();
            await frame.locator('#page-sidebar-toggle').click();
            await page.waitForFunction(kind=>!document.getElementById(kind+'-report-progress').classList.contains('is-visible'),kind);
            await page.locator('#sw-report-editor').scrollIntoViewIfNeeded();
            await frame.locator('.layout').evaluate(el=>window.scrollTo(0,el.offsetTop+el.offsetHeight-innerHeight+30));
            await page.screenshot({path:path.join(output,kind+'-'+version+'-tablet-layout.png')});
          }
        }
        await page.setViewportSize({width:1440,height:1050});
        await page.screenshot({path:path.join(output,kind+'-'+version+'-preview.png'),fullPage:true});
        assert.deepEqual(errors,[]);
        console.log(kind+'/'+version+': step transition auto-generation, unchanged reuse, expired job, preserved graph, real regeneration, transient retry, aligned/responsive editor layout passed');
      }catch(error){await page.screenshot({path:path.join(output,kind+'-'+version+'-failure.png'),fullPage:true});throw error;}
      finally{await context.close();}
    }
    console.log(JSON.stringify({browser:await browser.version(),output}));
  }finally{await browser.close();server.kill('SIGTERM');}
})().catch(error=>{console.error(error);console.error(logs.slice(-4000));console.error('Artifacts:',output);server.kill('SIGTERM');process.exitCode=1;});

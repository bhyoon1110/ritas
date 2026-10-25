// Real local PPT/PDF conversion. Timings are observations, not machine-specific assertions.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-editor-latency-'));
const server=spawn(process.env.RIST_TEST_PYTHON||'python3',['-u',path.join(__dirname,'spectrum_workflow_fixture.py'),output],{env:process.env});
let logs='';server.stderr.on('data',data=>logs+=data);
const port=new Promise((resolve,reject)=>{let data='';server.stdout.on('data',chunk=>{data+=chunk;if(data.includes('\n'))resolve(JSON.parse(data.split('\n')[0]).port);});server.on('exit',code=>{if(code)reject(Error(logs));});});
(async()=>{
  const origin='http://127.0.0.1:'+await port;
  const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try{
    for(const kind of ['ftir','raman']){
      const page=await browser.newPage({viewport:{width:1500,height:1100}}),errors=[],timings=[];
      page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
      const raw=Buffer.from(Array.from({length:241},(_,i)=>{const x=400+i*15;return x+','+(.05+Math.exp(-Math.pow(x-1700,2)/(2*55**2)));}).join('\n'));
      await page.goto(origin+'/'+kind);
      await page.waitForFunction(()=>document.getElementById('sw-step-2')&&!document.getElementById('sw-step-2').disabled);
      await page.locator('#'+kind+'-file-input').setInputFiles({name:'latency.'+(kind==='ftir'?'dpt':'txt'),mimeType:'text/plain',buffer:raw});
      await page.waitForFunction(()=>document.getElementById('sw-save-graph')&&!document.getElementById('sw-save-graph').disabled);
      await page.locator('#sw-save-graph').click();await page.locator('.sw-card').waitFor();await page.locator('#sw-next').click();
      const frame=page.frameLocator('#sw-report-editor');
      const ready=async()=>page.waitForFunction(()=>{
        const iframe=document.getElementById('sw-report-editor'),d=iframe&&iframe.contentDocument,img=d&&d.getElementById('slide-image');
        return img&&img.complete&&img.naturalWidth>0&&d.getElementById('page-add')&&!d.getElementById('page-add').disabled;
      },null,{timeout:180000});
      await ready();
      const field=frame.getByLabel('보고서 제목',{exact:true});
      for(let index=0;index<3;index++){
        const title=kind+' 빠른 미리보기 '+index,start=Date.now();
        await field.fill(title);
        assert.equal(await frame.locator('#download').getAttribute('aria-disabled'),'true');
        await page.waitForFunction(()=>{
          const d=document.getElementById('sw-report-editor').contentDocument;
          return d.getElementById('status').classList.contains('error')||d.getElementById('status').textContent.includes('자동 저장 완료');
        },null,{timeout:180000});
        assert(!(await frame.locator('#status').getAttribute('class')).includes('error'),await frame.locator('#status').textContent());
        await ready();timings.push(Date.now()-start);
        const endpoint=await page.locator('#sw-report-editor').evaluate(f=>f.contentWindow.location.pathname.replace(/\/edit$/,'/editor-data'));
        assert.equal((await (await page.request.get(origin+endpoint)).json()).document.title,title);
      }
      // Revisit a page; actual network/cache status is observed through CDP.
      const cdp=await page.context().newCDPSession(page),responses=[];
      await cdp.send('Network.enable');cdp.on('Network.responseReceivedExtraInfo',e=>responses.push(e));
      const imageUrl=await frame.locator('#slide-image').getAttribute('src');
      await frame.locator('#edit-next').click();await ready();await frame.locator('#edit-previous').click();await ready();
      assert.equal(await frame.locator('#slide-image').getAttribute('src'),imageUrl);
      await frame.locator('#slide-stage').screenshot({path:path.join(output,kind+'-edited.png')});
      const image=await page.request.get(origin+imageUrl);
      if(image.headers().etag){
        assert.equal(image.headers()['cache-control'],'private, no-cache');
        const cached=await page.request.get(origin+imageUrl,{headers:{'If-None-Match':image.headers().etag}});
        assert.equal(cached.status(),304);assert.equal((await cached.body()).length,0);
        // Same-document image reuse may avoid a request entirely. A reload must
        // authorize again and must still show the latest saved title/image.
        await page.reload();await ready();
        assert.equal(await frame.locator('#slide-image').getAttribute('src'),imageUrl);
        assert.equal(await frame.getByLabel('보고서 제목',{exact:true}).inputValue(),kind+' 빠른 미리보기 2');
      }
      assert.deepEqual(errors,[]);
      console.log(JSON.stringify({kind,editToVisibleMs:timings,medianMs:[...timings].sort((a,b)=>a-b)[1],browser304:responses.filter(e=>e.statusCode===304).length}));
      await page.close();
    }
  }finally{await browser.close();server.kill();console.log('Artifacts: '+output);}
})().catch(e=>{server.kill();console.error(e);console.error(logs.slice(-5000));process.exitCode=1;});

// Actual Plotly/API integration. Chrome on macOS is not Supermium/Windows certification.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-peak-coordinates-'));
const server=spawn(process.env.RIST_TEST_PYTHON||'python3',['-u',path.join(__dirname,'spectrum_workflow_fixture.py'),output],{env:process.env});
let logs='';server.stderr.on('data',chunk=>{logs+=chunk;});
const port=new Promise((resolve,reject)=>{
  let data='';server.stdout.on('data',chunk=>{data+=chunk;if(data.includes('\n')){try{resolve(JSON.parse(data.split('\n')[0]).port);}catch(e){reject(e);}}});
  server.on('exit',code=>{if(code)reject(new Error(logs));});
});
const raw=scale=>Buffer.from(Array.from({length:481},(_,i)=>{
  const x=400+i*7.5;return x+','+scale*(.05+Math.exp(-Math.pow(x-1700,2)/(2*45**2))+.8*Math.exp(-Math.pow(x-1250,2)/(2*65**2)));
}).join('\n'));
(async()=>{
  let browser;
  try{
    const origin='http://127.0.0.1:'+await port;
    browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
    for(const kind of ['ftir','raman'])for(const width of [1440,390]){
      const page=await browser.newPage({viewport:{width,height:980},isMobile:width===390,hasTouch:width===390}),errors=[];
      page.on('pageerror',error=>errors.push(error.message));page.setDefaultTimeout(20000);
      const id=kind==='ftir'?'peak-plot':'raman-plot',label=kind+'-'+width;
      try{
        await page.goto(origin+'/'+kind);
        await page.waitForFunction(()=>document.getElementById('sw-step-2')&&!document.getElementById('sw-step-2').disabled);
        await page.locator('#'+kind+'-file-input').setInputFiles([1,2].map((scale,i)=>({name:'coordinate-'+i+'.'+(kind==='ftir'?'dpt':'txt'),mimeType:'text/plain',buffer:raw(scale)})));
        await page.waitForFunction(id=>document.getElementById(id).data.filter(t=>t.meta&&t.meta.rist_sample_parent).length===2&&!document.getElementById('sw-save-graph').disabled,id);
        await page.evaluate(async id=>{const g=document.getElementById(id);await g._ristStackReady;
          await g._ristApplyStackSettings({enabled:true,gap:2.4});await g._ristStackReady;},id);
        const button=page.locator('.rist-peak-coordinate-button'),toggle=page.locator('.rist-'+kind+'-tools-toggle');
        const click=locator=>width===390?locator.tap():locator.click();
        if(!await button.isVisible())await click(toggle);await click(button);
        const dialog=page.getByRole('dialog',{name:'X좌표로 피크 추가'}),input=dialog.getByLabel('피크 X좌표'),submit=dialog.getByRole('button',{name:'피크 추가',exact:true});
        await dialog.getByLabel('피크 추가 시료').selectOption({index:1});
        const curve=Number(await dialog.getByLabel('피크 추가 시료').inputValue());
        await input.fill('9000');assert.equal(await submit.isDisabled(),true);
        await input.fill('1375.25');assert.equal(await submit.isDisabled(),false);
        assert.match(await dialog.locator('[role=status]').textContent(),/선형 보간값/);
        const before=await page.evaluate(({id,curve})=>{
          const g=document.getElementById(id),trace=g.data[curve];
          return {raw:g.data.filter(t=>t.meta&&t.meta.rist_sample_parent).map(t=>({x:t.x,y:t.y})),
            point:g._ristPeakCoordinates.pointAt(curve,1375.25),group:trace.meta.rist_sample_group};
        },{id,curve});
        await dialog.screenshot({path:path.join(output,label+'-dialog.png')});
        await click(submit);
        await page.waitForFunction(id=>document.getElementById(id).data.some(t=>t.meta&&t.meta.rist_peak&&t.meta.rist_peak.position_method==='exact-x'),id);
        await page.waitForFunction(()=>document.querySelector('.rist-peak-coordinate-feedback').textContent.includes('추가했습니다'));
        const after=await page.evaluate(id=>{const g=document.getElementById(id);return {peak:g.data.find(t=>t.meta&&t.meta.rist_peak&&t.meta.rist_peak.position_method==='exact-x'),
          raw:g.data.filter(t=>t.meta&&t.meta.rist_sample_parent).map(t=>({x:t.x,y:t.y})),annotation:g.layout.annotations.find(a=>a.x===1375.25)};},id);
        assert.equal(after.peak.x[0],1375.25);assert.equal(after.peak.meta.rist_peak.sample_group,before.group);
        assert(Math.abs(after.peak.y[0]-before.point.y)<1e-9);assert.deepEqual(after.raw,before.raw);
        assert.match(after.annotation.text,/1375\.25/);
        await input.fill('1375.250');assert.equal(await submit.isDisabled(),true);
        if(width===390)await click(dialog.getByRole('button',{name:'닫기 (Esc)'}));else await input.press('Escape');
        await dialog.waitFor({state:'hidden'});
        await page.waitForFunction(()=>document.querySelector('.rist-interaction-status').dataset.mode==='pan');
        await page.evaluate(async id=>{const g=document.getElementById(id);await g._ristApplyStackSettings({enabled:false,gap:1.2});await g._ristStackReady;},id);
        const unstacked=await page.evaluate(id=>document.getElementById(id).data.find(t=>t.meta&&t.meta.rist_peak&&t.meta.rist_peak.position_method==='exact-x'),id);
        assert.equal(unstacked.x[0],1375.25);assert(Math.abs(unstacked.y[0]-after.peak.meta.rist_peak.base_y)<1e-9);
        if(kind==='ftir'){
          await page.evaluate(async id=>{const g=document.getElementById(id);await g._ristSetSignalMode('transmittance');await g._ristStackReady;await g._ristSetSignalMode('absorbance');await g._ristStackReady;},id);
          const restored=await page.evaluate(id=>document.getElementById(id).data.find(t=>t.meta&&t.meta.rist_peak&&t.meta.rist_peak.position_method==='exact-x'),id);
          assert.equal(restored.x[0],1375.25);assert(Math.abs(restored.y[0]-unstacked.y[0])<1e-9);
        }
        await page.locator('#'+id).screenshot({path:path.join(output,label+'-graph.png')});
        assert.deepEqual(errors,[]);console.log(label+': raw upload, exact-X, interpolation, duplicate guard, Esc, stack and unit round-trip passed');
      }catch(error){await page.screenshot({path:path.join(output,label+'-failure.png'),fullPage:true});throw error;}
      finally{await page.close();}
    }
    console.log(JSON.stringify({browser:browser.version(),output}));
  }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error,logs,output);process.exitCode=1;});

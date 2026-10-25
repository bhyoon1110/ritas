// Real RAW/API/Plotly verification on macOS Chrome; not Windows Supermium certification.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-peak-markers-'));
const server=spawn(process.env.RIST_TEST_PYTHON||'python3',['-u',path.join(__dirname,'spectrum_workflow_fixture.py'),output],{env:process.env});
let logs='';server.stderr.on('data',chunk=>{logs+=chunk;});
const port=new Promise((resolve,reject)=>{let data='';server.stdout.on('data',chunk=>{data+=chunk;if(data.includes('\n')){
  try{resolve(JSON.parse(data.split('\n')[0]).port);}catch(error){reject(error);}}});server.on('exit',code=>{if(code)reject(new Error(logs));});});
const raw=Buffer.from(Array.from({length:481},(_,i)=>{const x=400+i*7.5;return x+','+(.05+Math.exp(-Math.pow(x-1700,2)/(2*45**2))+.8*Math.exp(-Math.pow(x-1250,2)/(2*65**2)));}).join('\n'));
(async()=>{
  let browser;
  try{
    const origin='http://127.0.0.1:'+await port;
    browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
    for(const kind of ['ftir','raman'])for(const width of [1440,390]){
      const page=await browser.newPage({viewport:{width,height:980},isMobile:width===390,hasTouch:width===390}),errors=[];
      page.on('pageerror',error=>errors.push(error.message));page.setDefaultTimeout(20000);
      const id=kind==='ftir'?'peak-plot':'raman-plot',label=kind+'-'+width,click=locator=>width===390?locator.tap():locator.click();
      const openTools=async()=>{if(!await page.locator('.rist-peak-marker-toggle').isVisible())await click(page.locator('.rist-'+kind+'-tools-toggle'));};
      try{
        await page.goto(origin+'/'+kind);
        await page.waitForFunction(()=>document.getElementById('sw-step-2')&&!document.getElementById('sw-step-2').disabled);
        await page.locator('#'+kind+'-file-input').setInputFiles({name:'markers.'+(kind==='ftir'?'dpt':'txt'),mimeType:'text/plain',buffer:raw});
        await page.waitForFunction(id=>document.getElementById(id).data.some(t=>t.meta&&t.meta.rist_sample_parent)&&!document.getElementById('sw-save-graph').disabled,id);
        await openTools();await click(page.locator('.rist-peak-coordinate-button'));
        const dialog=page.locator('.rist-peak-coordinate-dialog');await dialog.getByLabel('피크 X좌표').fill('1375.25');
        await click(dialog.getByRole('button',{name:'피크 추가',exact:true}));
        await page.waitForFunction(()=>document.querySelector('.rist-peak-coordinate-feedback').textContent.includes('추가했습니다'));
        await click(dialog.getByRole('button',{name:'닫기 (Esc)'}));await openTools();
        const read=()=>page.evaluate(id=>{const g=document.getElementById(id);return {raw:g.data.filter(t=>t.meta&&t.meta.rist_sample_parent).map(t=>({x:t.x,y:t.y})),
          peaks:g.data.filter(t=>t.meta&&t.meta.rist_peak),labels:(g.layout.meta.ristPeakLabels||[]).map(l=>g.layout.annotations[l.annotationIndex])};},id);
        const before=await read(),size=page.locator('.rist-peak-marker-size');await size.fill('18');await size.press('Tab');
        await page.waitForFunction(id=>document.getElementById(id).data.filter(t=>t.meta&&t.meta.rist_peak).every(t=>t.marker.size===18),id);
        assert(await page.locator('#'+id+' .scatterlayer path.point').count()>0,'visible peak dots');
        await click(page.locator('.rist-peak-marker-toggle'));
        await page.waitForFunction(id=>document.getElementById(id).data.filter(t=>t.meta&&t.meta.rist_peak).every(t=>t.mode==='none'),id);
        await page.evaluate(async id=>{await document.getElementById(id)._ristPeakDisplayReady;},id);
        assert.equal(await page.locator('#'+id+' .scatterlayer path.point').count(),0);
        let after=await read();assert(after.labels.some(a=>a.visible!==false&&a.text.includes('1375.25')));assert(after.labels.every(a=>String(a.arrowcolor).replace(/\s/g,'')==='rgba(0,0,0,0)'));
        for(let i=0;i<after.labels.length;i++)assert.deepEqual([after.labels[i].x,after.labels[i].y,after.labels[i].ax,after.labels[i].ay],[before.labels[i].x,before.labels[i].y,before.labels[i].ax,before.labels[i].ay]);
        assert.deepEqual(after.raw,before.raw);assert(after.peaks.every(t=>t.marker.size===18));
        await page.locator('#'+id).screenshot({path:path.join(output,label+'-labels-only.png')});
        const png=await page.evaluate(async id=>{const g=document.getElementById(id);await g._ristPeakDisplayReady;return Plotly.toImage(g,{format:'png',width:1000,height:750});},id);
        fs.writeFileSync(path.join(output,label+'-export.png'),Buffer.from(png.split(',')[1],'base64'));
        await page.locator('#sw-graph-name').fill('라벨 전용');await click(page.locator('#sw-save-graph'));
        await page.waitForFunction(()=>document.querySelectorAll('.sw-card').length===1&&!document.getElementById('sw-save-graph').disabled);
        await page.reload();await page.waitForFunction(()=>document.querySelectorAll('.sw-card').length===1);
        await page.locator('#sw-step-2').click();
        await click(page.locator('.sw-card').getByRole('button',{name:'다시 편집',exact:true}));
        await page.waitForFunction(id=>document.getElementById(id).layout.meta.ristPeakMarkersVisible===false&&!document.getElementById('sw-save-graph').disabled,id);
        await openTools();assert.equal(await size.inputValue(),'18');
        await page.waitForFunction(id=>document.getElementById(id).data.some(t=>t.meta&&t.meta.rist_peak&&t.mode==='none'),id);
        await click(page.locator('.rist-peak-marker-toggle'));
        await page.waitForFunction(id=>document.getElementById(id).data.filter(t=>t.meta&&t.meta.rist_peak).every(t=>t.mode!=='none'&&t.marker.size===18),id);
        assert(await page.locator('#'+id+' .scatterlayer path.point').count()>0);
        await page.evaluate(async id=>{await document.getElementById(id)._ristPeakDisplayReady;},id);
        after=await read();assert.deepEqual(after.raw,before.raw);assert(after.labels.every(a=>String(a.arrowcolor).replace(/\s/g,'')!=='rgba(0,0,0,0)'));
        assert.deepEqual(errors,[]);console.log(label+': real RAW, dot size, labels-only, connectors, PNG, graph save and reload passed');
      }catch(error){await page.screenshot({path:path.join(output,label+'-failure.png'),fullPage:true});throw error;}
      finally{await page.close();}
    }
    console.log(JSON.stringify({browser:browser.version(),output}));
  }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error,logs,output);process.exitCode=1;});

// Real fallback downloads and RAW round-trip. Native OS picker is unit-tested separately.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-file-save-'));
const server=spawn(process.env.RIST_TEST_PYTHON||'python3',['-u',path.join(__dirname,'spectrum_workflow_fixture.py'),output],{env:process.env});
let logs='';server.stderr.on('data',data=>logs+=data);
const port=new Promise((resolve,reject)=>{let data='';server.stdout.on('data',chunk=>{data+=chunk;if(data.includes('\n'))resolve(JSON.parse(data.split('\n')[0]).port);});server.on('exit',code=>{if(code)reject(Error(logs));});});
(async()=>{
  const origin='http://127.0.0.1:'+await port;
  const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try{
    for(const kind of ['ftir','raman']){
      const page=await browser.newPage({viewport:{width:1500,height:1100}}),errors=[];
      await page.addInitScript(()=>{window.showSaveFilePicker=undefined;}); // Same fallback used on non-secure HTTP.
      page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
      const raw=Buffer.from(Array.from({length:241},(_,i)=>{const x=400+i*15;return x+','+(.05+Math.exp(-Math.pow(x-1700,2)/(2*55**2)));}).join('\n'));
      await page.goto(origin+'/'+kind);await page.waitForFunction(()=>document.getElementById('sw-step-2')&&!document.getElementById('sw-step-2').disabled);
      await page.locator('#'+kind+'-file-input').setInputFiles({name:'source.'+(kind==='ftir'?'dpt':'txt'),mimeType:'text/plain',buffer:raw});
      await page.waitForFunction(()=>document.getElementById('spectrum-workflow').dataset.step==='2'&&!document.getElementById('sw-save-graph').disabled);
      await page.locator('#sw-save-graph').click();await page.locator('.sw-card').waitFor();
      const exportButton=page.locator('.sw-card').first().getByRole('button',{name:'파일 내보내기',exact:true});
      await exportButton.click();let dialog=page.getByRole('dialog',{name:'파일 저장',exact:true});
      await dialog.getByRole('button',{name:'취소',exact:true}).click();assert.equal(await page.locator('.sw-card').count(),1);
      await exportButton.click();await dialog.getByLabel('저장 파일 이름').fill('나의 '+kind+' 그래프');
      await dialog.screenshot({path:path.join(output,kind+'-save-desktop.png')});
      let pending=page.waitForEvent('download');await dialog.getByRole('button',{name:'다운로드',exact:true}).click();
      const graph=await pending;assert.equal(graph.suggestedFilename(),'나의 '+kind+' 그래프.ristgraph');
      const graphPath=path.join(output,graph.suggestedFilename());await graph.saveAs(graphPath);assert.equal(await graph.failure(),null);
      // Import exactly the downloaded bytes: confirms the source RAW survived naming/export.
      const restored=await page.evaluate(async({bytes,kind})=>{
        const value=await window.RistSpectrumGraphFile.load(new File([new Uint8Array(bytes)],'copy.ristgraph'),kind);
        return {raw:await value.state.files[0].blob.text(),title:value.title};
      },{bytes:[...fs.readFileSync(graphPath)],kind});assert.equal(restored.raw,raw.toString());assert(restored.title);
      await page.locator('#sw-next').click();const frame=page.frameLocator('#sw-report-editor');
      await page.waitForFunction(()=>{const f=document.getElementById('sw-report-editor'),d=f&&f.contentDocument;return d&&d.getElementById('page-add')&&!d.getElementById('page-add').disabled;},null,{timeout:180000});
      await page.waitForFunction(()=>!document.getElementById('sw-step-4').disabled);await page.locator('#sw-step-4').click();
      const url=await frame.locator('#download').getAttribute('href');const original=await (await page.request.get(origin+url)).body();
      await frame.locator('#download').click();dialog=page.getByRole('dialog',{name:'파일 저장',exact:true});
      await dialog.getByLabel('저장 파일 이름').fill('검토 완료 '+kind+'.zip');
      pending=page.waitForEvent('download');await dialog.getByRole('button',{name:'다운로드',exact:true}).click();
      const report=await pending;assert.equal(report.suggestedFilename(),'검토 완료 '+kind+'.zip');
      const reportPath=path.join(output,report.suggestedFilename());await report.saveAs(reportPath);assert.equal(await report.failure(),null);
      assert.deepEqual(fs.readFileSync(reportPath),original);
      await page.setViewportSize({width:390,height:844});await frame.locator('#download').click();
      const box=await dialog.boundingBox();assert(box.width<=390&&box.x>=0&&box.y>=0&&box.y+box.height<=844);
      await dialog.screenshot({path:path.join(output,kind+'-save-mobile.png')});await dialog.getByRole('button',{name:'취소',exact:true}).click();
      assert.deepEqual(errors,[]);console.log(kind+': named graph + RAW round-trip, named byte-identical ZIP, cancel, desktop/mobile passed');await page.close();
    }
  }finally{await browser.close();server.kill();console.log('Artifacts: '+output);}
})().catch(e=>{server.kill();console.error(e);console.error(logs.slice(-5000));process.exitCode=1;});

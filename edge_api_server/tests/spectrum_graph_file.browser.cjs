// Real RAW -> edited Plotly -> downloaded portable file -> a clean browser -> report.
// macOS Chromium engine validation, not Windows/Supermium hardware certification.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn,execFileSync}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-portable-graphs-'));
const server=spawn(process.env.RIST_TEST_PYTHON||'python3',['-u',path.join(__dirname,'spectrum_workflow_fixture.py'),output],{env:process.env});
let logs='';server.stderr.on('data',b=>logs+=b);
const port=new Promise((resolve,reject)=>{let data='';server.stdout.on('data',b=>{data+=b;if(data.includes('\n')){try{resolve(JSON.parse(data.split('\n')[0]).port);}catch(e){reject(e);}}});server.on('exit',c=>{if(c)reject(Error(logs));});});
const raw=Buffer.from(Array.from({length:241},(_,i)=>{const x=400+i*15;return x+','+(.05+Math.exp(-Math.pow(x-1700,2)/(2*55**2))+.55*Math.exp(-Math.pow(x-1250,2)/(2*80**2)));}).join('\r\n'));
async function collection(page,kind){return page.evaluate(kind=>new Promise((resolve,reject)=>{
  const r=indexedDB.open('rist-'+kind+'-workspace-v1',1);r.onerror=reject;r.onsuccess=()=>{
    const db=r.result,q=db.transaction('workspace','readonly').objectStore('workspace').get('graph-collection-v1:workflow-test');
    q.onsuccess=()=>{db.close();resolve(q.result);};q.onerror=reject;
  };
}),kind);}
(async()=>{
  const origin='http://127.0.0.1:'+await port;
  for(let i=0;i<80;i++){try{if((await fetch(origin+'/ftir')).ok)break;}catch(_){}await new Promise(r=>setTimeout(r,100));}
  const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try{
    for(const kind of ['ftir','raman']){
      const id=kind==='ftir'?'peak-plot':'raman-plot',errors=[];
      const context=await browser.newContext({viewport:{width:1366,height:950}}),page=await context.newPage();
      page.setDefaultTimeout(30000);page.on('dialog',d=>d.accept());page.on('pageerror',e=>errors.push(e.message));
      await page.goto(origin+'/'+kind);await page.waitForFunction(()=>document.getElementById('sw-step-2')&&!document.getElementById('sw-step-2').disabled);
      const ext=kind==='ftir'?'dpt':'txt';
      await page.locator('#'+kind+'-file-input').setInputFiles([{name:'보존 10%.'+ext,mimeType:'text/plain',buffer:raw},{name:'original-second.'+ext,mimeType:'text/plain',buffer:raw}]);
      await page.waitForFunction(()=>document.getElementById('spectrum-workflow').dataset.step==='2'&&!document.getElementById('sw-save-graph').disabled);
      await page.evaluate(async id=>{
        const g=document.getElementById(id);await g._ristStackReady;
        await g._ristApplyStackSettings({enabled:true,gap:2.4});await g._ristStackReady;
        await g._ristAddPeakAt(g._ristPeakCoordinates.pointAt(0,1375.25));await g._ristStackReady;
        await Plotly.restyle(g,{'name':'편집한 시료','line.color':'#b4136b','line.width':3},[0]);
        await Plotly.relayout(g,{'title.text':'이동 가능한 편집 그래프','xaxis.range':[2200,1100],'xaxis.autorange':false,
          annotations:(g.layout.annotations||[]).concat([{name:'portable-note',text:'편집 메모',x:1700,y:1.2,showarrow:false,font:{color:'#b4136b'}}]),
          shapes:(g.layout.shapes||[]).concat([{type:'rect',x0:1600,x1:1800,y0:.2,y1:.8,line:{dash:'dash',color:'#2470ac'}}])});
      },id);
      if(kind==='ftir')await page.evaluate(async id=>{const g=document.getElementById(id);await g._ristSetSignalMode('transmittance');await g._ristStackReady;},id);
      await page.locator('#sw-graph-name').fill('RAW 포함 저장본');await page.locator('#sw-save-graph').click();
      await page.waitForFunction(()=>document.querySelectorAll('.sw-card').length===1&&!document.getElementById('sw-save-graph').disabled);
      const saved=await collection(page,kind);assert.equal(saved.graphs.length,1);
      // Unsaved edits must not leak into an export of the saved card.
      await page.evaluate(async id=>Plotly.relayout(document.getElementById(id),{'title.text':'미저장 제목'}),id);
      const downloadPromise=page.waitForEvent('download');await page.locator('.sw-card').getByRole('button',{name:'파일 내보내기',exact:true}).click();
      const download=await downloadPromise,file=path.join(output,kind+'.ristgraph');await download.saveAs(file);
      assert.match(download.suggestedFilename(),/\.ristgraph$/);
      const binary=fs.readFileSync(file),length=binary.readUInt32LE(8),manifest=JSON.parse(binary.subarray(16,16+length));
      assert.equal(manifest.state.plotLayout.title.text,'이동 가능한 편집 그래프');
      assert.deepEqual(manifest.state.plotData,saved.graphs[0].state.plotData);
      assert.deepEqual(manifest.state.plotLayout,saved.graphs[0].state.plotLayout);
      assert.deepEqual(binary.subarray(16+length),Buffer.concat([raw,raw]));
      assert.equal(manifest.rawFiles.length,2);assert(!manifest.state.reportTransfer);assert(!manifest.state.workTemplate);
      await context.close();
      // A different browser storage profile has no current RAW, saved entry or analysis.
      const fresh=await browser.newContext({viewport:{width:1366,height:950}}),target=await fresh.newPage();
      target.setDefaultTimeout(30000);target.on('dialog',d=>d.accept());target.on('pageerror',e=>errors.push(e.message));
      let analyses=0;target.on('request',r=>{if(r.url().endsWith('/'+kind+'/analyze'))analyses++;});
      await target.goto(origin+'/'+kind);await target.locator('#sw-import-graph').waitFor();
      await target.waitForFunction(()=>!document.getElementById('sw-import-graph').disabled);
      assert.equal(await collection(target,kind),undefined);
      await target.locator('#sw-graph-file-input').setInputFiles(file);
      await target.waitForFunction(id=>document.getElementById(id).layout.title?.text==='이동 가능한 편집 그래프'&&!document.getElementById('sw-save-graph').disabled,id);
      assert.equal(analyses,0,'import restores saved state rather than re-running sensitivity/LLM analysis');
      const imported=await collection(target,kind);assert.equal(imported.graphs.length,1);
      assert.deepEqual(imported.graphs[0].state.plotData,manifest.state.plotData);
      assert.deepEqual(imported.graphs[0].state.plotLayout,manifest.state.plotLayout);
      if(kind==='ftir'){
        assert.equal(await target.evaluate(id=>document.getElementById(id)._ristFtirSignalMode,id),'transmittance');
        assert.deepEqual(await target.evaluate(id=>document.getElementById(id).data.filter(t=>t.meta?.rist_sample_parent).map(t=>t.y),id),manifest.state.plotData.filter(t=>t.meta?.rist_sample_parent).map(t=>t.y));
        await target.evaluate(async id=>{const g=document.getElementById(id);await g._ristSetSignalMode('absorbance');await g._ristStackReady;},id);
      }
      const restored=await target.evaluate(id=>{const g=document.getElementById(id);return {name:g.data[0].name,color:g.data[0].line.color,width:g.data[0].line.width,range:g.layout.xaxis.range,notes:g.layout.annotations.map(a=>a.text),shapes:g.layout.shapes,
        raw:g.data.filter(t=>t.meta?.rist_sample_parent).map(t=>t.y),manual:g.data.find(t=>t.meta?.rist_peak?.position_method==='exact-x')};},id);
      assert.equal(restored.name,'편집한 시료');assert.equal(restored.color,'#b4136b');assert.equal(restored.width,3);
      assert.deepEqual(restored.range,[2200,1100]);assert(restored.notes.includes('편집 메모'));assert(restored.shapes.some(s=>s.type==='rect'&&s.x0===1600));
      assert.deepEqual(restored.raw,manifest.state.plotData.filter(t=>t.meta?.rist_sample_parent).map(t=>kind==='ftir'?t.meta.ftir_signal_toggle.absorbance_y:t.y),'stack positions are restored exactly');
      assert.equal(restored.manual.x[0],1375.25);assert.deepEqual(restored.manual.meta.rist_peak,manifest.state.plotData.find(t=>t.meta?.rist_peak?.position_method==='exact-x').meta.rist_peak);
      const rawBack=await target.evaluate(kind=>new Promise((resolve,reject)=>{const r=indexedDB.open('rist-'+kind+'-workspace-v1');r.onerror=reject;r.onsuccess=()=>{
        const db=r.result,q=db.transaction('workspace','readonly').objectStore('workspace').get('graph-collection-v1:workflow-test');
        q.onsuccess=async()=>{db.close();resolve(await Promise.all(q.result.graphs[0].state.files.map(async f=>({name:f.name,bytes:Array.from(new Uint8Array(await f.blob.arrayBuffer()))}))));};
      };}),kind);
      for(const f of rawBack)assert.deepEqual(Buffer.from(f.bytes),raw);
      assert.equal(rawBack[0].name,'보존 10%.'+ext);
      await target.locator('#'+id).screenshot({path:path.join(output,kind+'-restored.png')});
      await target.locator('#sw-library').screenshot({path:path.join(output,kind+'-library.png')});
      // Damaged files cannot alter an existing saved collection/current graph.
      const broken=Buffer.from(binary);broken[broken.length-1]^=1;
      await target.locator('#sw-graph-file-input').setInputFiles({name:'broken.ristgraph',mimeType:'application/octet-stream',buffer:broken});
      await target.waitForFunction(()=>document.querySelector('.sw-status').textContent.includes('손상'));
      assert.equal((await collection(target,kind)).graphs.length,1);
      assert.equal(await target.evaluate(id=>document.getElementById(id).layout.title.text,id),'이동 가능한 편집 그래프');
      // Additional editing and overwrite remain possible after import.
      await target.evaluate(async id=>Plotly.relayout(document.getElementById(id),{'title.text':'불러온 뒤 추가 편집'}),id);
      await target.locator('#sw-save-graph').click();await target.waitForFunction(()=>!document.getElementById('sw-save-graph').disabled);
      assert.equal((await collection(target,kind)).graphs.length,1);
      await target.locator('#sw-next').click();
      await target.waitForFunction(()=>{const f=document.getElementById('sw-report-editor');return f.src.includes('/edit?workflow=1')&&!f.hidden;},null,{timeout:180000});
      const editor=target.frameLocator('#sw-report-editor');await editor.locator('#edit-page option').first().waitFor({state:'attached',timeout:120000});
      assert(!await target.locator('.rist-interaction-status').isVisible(),'report editing must never show graph mode');
      await target.locator('#sw-step-4').click();await editor.locator('#download').waitFor({state:'visible'});
      assert(!await target.locator('.rist-interaction-status').isVisible(),'report delivery must never show graph mode');
      const href=await editor.locator('#download').getAttribute('href'),response=await target.request.get(origin+href);
      assert.equal(response.status(),200);const zip=path.join(output,kind+'-imported-report.zip');fs.writeFileSync(zip,await response.body());
      const expectedRaw=path.join(output,kind+'-expected.'+ext);fs.writeFileSync(expectedRaw,raw);
      console.log(execFileSync(process.env.RIST_TEST_PYTHON||'python3',['-c',
        'import sys,zipfile;from tests.test_ftir_raw_xlsx import xlsx_rows;z=zipfile.ZipFile(sys.argv[1]);raw=dict(map(float,line.split(",")) for line in open(sys.argv[2]).read().splitlines());rows=xlsx_rows(z.read("raw_data.xlsx"));assert len(rows[0])==3,rows[0];assert len(rows)==242;assert all(abs(row["B"]-raw[row["A"]])<1e-9 and abs(row["C"]-raw[row["A"]])<1e-9 for row in rows[1:]);assert "report.pptx" in z.namelist();print("Report ZIP: original RAW values in Excel and generated PPT verified")',zip,expectedRaw],{encoding:'utf8'}).trim());
      await target.locator('#sw-step-2').click();await target.setViewportSize({width:390,height:844});
      await target.locator('#sw-library').scrollIntoViewIfNeeded();await target.screenshot({path:path.join(output,kind+'-mobile.png')});
      assert.deepEqual(errors,[]);console.log(kind+': clean-browser RAW/edit restoration, checksum rejection, re-edit/save, report generation, desktop/mobile verified');
      await fresh.close();
    }
    console.log('Artifacts: '+output);
  }finally{await browser.close();}
})().catch(e=>{console.error(e);console.error(logs.slice(-6000));console.error('Artifacts: '+output);process.exitCode=1;}).finally(()=>server.kill('SIGTERM'));

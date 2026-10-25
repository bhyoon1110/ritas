// Real RAW/Plotly integration with a controlled recommendation response, not live model validation.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-peak-recommendations-'));
const server=spawn(process.env.RIST_TEST_PYTHON||'python3',['-u',path.join(__dirname,'spectrum_workflow_fixture.py'),output],{env:process.env});
let logs='';server.stderr.on('data',chunk=>logs+=chunk);
const port=new Promise((resolve,reject)=>{let text='';server.stdout.on('data',chunk=>{text+=chunk;if(text.includes('\n'))resolve(JSON.parse(text.split('\n')[0]).port);});server.on('exit',code=>{if(code)reject(new Error(logs));});});
const raw=Buffer.from(Array.from({length:481},(_,i)=>{const x=400+i*7.5;return x+','+(.05+Math.exp(-Math.pow(x-1700,2)/(2*45**2))+.8*Math.exp(-Math.pow(x-1250,2)/(2*65**2)));}).join('\n'));
(async()=>{
  let browser;
  try {
    const origin='http://127.0.0.1:'+await port;
    browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
    for(const kind of ['ftir','raman']) for(const width of [1440,390]) {
      const page=await browser.newPage({viewport:{width,height:width===390?844:768},isMobile:width===390,hasTouch:width===390});page.setDefaultTimeout(20000);
      await page.addInitScript(()=>{window.showSaveFilePicker=undefined;});
      const id=kind==='ftir'?'peak-plot':'raman-plot',errors=[],label=kind+'-'+width;page.on('pageerror',e=>errors.push(e.message));
      let pending=null,delay=false,fail=false,requests=0;
      await page.route('**/api/v1/'+kind+'/peaks/recommend',async route=>{
        requests++;const input=route.request().postDataJSON();
        assert(input.candidates.length>0 && input.candidates.length<=120);assert.equal(input.material,'탄소 시료 검토');
        const complete=()=>route.fulfill({status:fail?502:200,contentType:'application/json',body:JSON.stringify(fail?{message:'테스트 LLM 연결 실패'}:{
          peaks:input.candidates.slice(0,2).map((c,i)=>({...c,name:i?'검토 피크 B':'검토 피크 A',reason:'모의 응답 · 실제 위치 선택'}))
        })});
        if(delay) pending=complete;else await complete();
      });
      const recommendationCount=()=>page.evaluate(id=>document.getElementById(id).data.filter(t=>t.meta?.rist_peak?.recommendation).length,id);
      const sensitivity=async value=>{
        await page.evaluate(({id,value})=>{const g=document.getElementById(id),input=g.querySelector('.rist-peak-sensitivity-number');input.value=String(value);input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));}, {id,value});
        await page.waitForTimeout(350);
      };
      const dialog=page.getByRole('dialog',{name:'LLM 피크 추천'});
      const open=async()=>{
        if(!await page.locator('.rist-peak-recommend-button').isVisible())await page.locator('.rist-'+kind+'-tools-toggle').click();
        await page.locator('.rist-peak-recommend-button').click();await dialog.getByLabel('물질 힌트').fill('탄소 시료 검토');
      };
      const submit=()=>dialog.getByRole('button',{name:'피크 추천받기',exact:true}).click();
      const recommended=()=>page.waitForFunction(()=>/추천 목록을 받았습니다/.test(document.querySelector('.rist-peak-recommend-dialog [role=status]').textContent));
      const apply=async()=>{await dialog.getByRole('button',{name:'선택한 피크 추가',exact:true}).click();await page.waitForFunction(()=>/개 추가 · 중복/.test(document.querySelector('.rist-peak-recommend-dialog [role=status]').textContent));};
      try {
        await page.goto(origin+'/'+kind);
        await page.waitForFunction(()=>document.getElementById('sw-step-2')&&!document.getElementById('sw-step-2').disabled);
        await page.locator('#'+kind+'-file-input').setInputFiles([0,1].map(i=>({name:'recommend-'+i+'.'+(kind==='ftir'?'dpt':'txt'),mimeType:'text/plain',buffer:raw})));
        await page.waitForFunction(id=>document.getElementById(id)._ristPeakRecommendations && document.getElementById(id).data.filter(t=>t.meta?.rist_sample_parent).length===2&&!document.getElementById('sw-save-graph').disabled,id);
        await page.evaluate(async id=>{const g=document.getElementById(id);await g._ristStackReady;await g._ristApplyStackSettings({enabled:true,gap:2.4});await g._ristStackReady;},id);
        if(kind==='ftir') await page.evaluate(async id=>{const g=document.getElementById(id);await g._ristSetSignalMode('transmittance');await g._ristStackReady;},id);
        await sensitivity(0);await open();await dialog.getByLabel('추천 대상 시료').selectOption({index:1});
        const selected=Number(await dialog.getByLabel('추천 대상 시료').inputValue());
        const before=await page.evaluate(({id,selected})=>{const g=document.getElementById(id),t=g.data[selected];return {raw:g.data.filter(t=>t.meta?.rist_sample_parent),labels:g.layout.meta.ristPeakLabels.length,group:t.meta.rist_sample_group,candidates:g._ristPeakRecommendations.candidates(t)};},{id,selected});
        await submit();await recommended();assert.equal(await recommendationCount(),0,'Receiving recommendations does not add peaks');
        if(kind==='ftir')assert.equal(await page.evaluate(id=>document.getElementById(id)._ristFtirSignalMode,id),'transmittance','Reviewing does not switch display units');
        await dialog.getByLabel('추천 피크 1 이름').fill('사용자 수정 피크');
        await dialog.getByLabel('추천 피크 2 이름').fill('');
        await dialog.getByLabel('추천 피크 1 선택').uncheck();await dialog.getByLabel('추천 피크 2 선택').uncheck();
        assert.equal(await dialog.getByRole('button',{name:'선택한 피크 추가',exact:true}).isDisabled(),true);
        await dialog.getByLabel('추천 피크 1 선택').check();
        assert.equal(await dialog.evaluate(el=>{
          const dialog=el.getBoundingClientRect(),head=el.querySelector('h3').getBoundingClientRect(),actions=el.querySelector('.rist-peak-recommend-actions').getBoundingClientRect();
          return head.top>=dialog.top && actions.bottom<=dialog.bottom && actions.bottom<=innerHeight
            && el.scrollWidth<=el.clientWidth+1 && el.querySelector('.rist-peak-recommend-body').scrollHeight>el.querySelector('.rist-peak-recommend-body').clientHeight;
        }),true,'Review scrolls independently; title and explicit Add remain visible without horizontal overflow');
        await dialog.screenshot({path:path.join(output,label+'-review.png')});
        await apply();assert.equal(await recommendationCount(),1,'Unchecked peaks are not added');
        assert.equal(await page.evaluate(id=>document.getElementById(id).data.find(t=>t.meta?.rist_peak?.recommendation).meta.rist_peak.label,id),'사용자 수정 피크');
        await dialog.getByLabel('추천 피크 2 선택').check();await apply();assert.equal(await recommendationCount(),2);
        const after=await page.evaluate(id=>{const g=document.getElementById(id);return {raw:g.data.filter(t=>t.meta?.rist_sample_parent),labels:g.layout.meta.ristPeakLabels.length,peaks:g.data.filter(t=>t.meta?.rist_peak?.recommendation)};},id);
        const rawValues=traces=>traces.map(t=>({x:t.x,y:t.meta.ftir_signal_toggle?.absorbance_y || t.y}));
        assert.deepEqual(rawValues(after.raw),rawValues(before.raw));assert(after.peaks.every(t=>t.meta.rist_peak.sample_group===before.group));
        assert.equal(after.labels,before.labels+2);assert.equal(new Set(after.peaks.map(t=>t.meta.rist_peak.label_key)).size,2);
        assert.deepEqual(after.peaks.map(t=>t.meta.rist_peak.base_y),before.candidates.slice(0,2).map(p=>p.intensity));
        assert.equal(after.peaks[1].meta.rist_peak.label,'','Blank names retain only position labels');
        assert(after.peaks.every(t=>t.meta.rist_peak.recommendation.label_edited));
        await dialog.screenshot({path:path.join(output,label+'-recommend.png')});
        await submit();await recommended();assert.equal(await recommendationCount(),2);assert.match(await dialog.locator('.rist-peak-recommend-summary').textContent(),/기존 중복 2개/);
        assert.equal(await dialog.getByRole('button',{name:'선택한 피크 추가',exact:true}).isDisabled(),true);
        await dialog.getByRole('button',{name:'닫기 (Esc)'}).click();
        await sensitivity(100);
        assert.equal(await page.evaluate(id=>{const g=document.getElementById(id);return g.data.filter(t=>t.meta?.rist_peak?.recommendation).every(t=>g.data.filter(other=>
          other.meta?.rist_peak?.sample_group===t.meta.rist_peak.sample_group && other.meta.rist_peak.x===t.meta.rist_peak.x && other.visible!==false && other.visible!=='legendonly').length===1);},id),true);
        await sensitivity(0);assert.equal(await recommendationCount(),2);
        await page.evaluate(id=>document.getElementById(id)._ristHistory.undo(),id);
        await page.waitForFunction(id=>document.getElementById(id).data.filter(t=>t.meta?.rist_peak?.recommendation).length===1,id);
        await page.evaluate(id=>document.getElementById(id)._ristHistory.redo(),id);
        await page.waitForFunction(id=>document.getElementById(id).data.filter(t=>t.meta?.rist_peak?.recommendation).length===2,id);
        // Delete all peaks, then recommend from measured RAW extrema alone.
        await page.evaluate(id=>{const g=document.getElementById(id);g.dispatchEvent(new CustomEvent('rist-peak-delete',{detail:{curves:g.data.map((t,i)=>t.meta?.rist_peak?i:-1).filter(i=>i>=0)}}));},id);
        await page.waitForFunction(id=>!document.getElementById(id).data.some(t=>t.meta?.rist_peak),id);
        await open();await submit();await recommended();assert.equal(await recommendationCount(),0);
        await dialog.getByLabel('추천 피크 1 이름').fill('재검토 피크');await apply();assert.equal(await recommendationCount(),2);
        await dialog.getByRole('button',{name:'닫기 (Esc)'}).click();
        await page.evaluate(id=>document.getElementById(id)._ristHistory.undo(),id);
        await page.waitForFunction(id=>!document.getElementById(id).data.some(t=>t.meta?.rist_peak?.recommendation),id);
        await page.evaluate(id=>document.getElementById(id)._ristHistory.redo(),id);
        await page.waitForFunction(id=>document.getElementById(id).data.filter(t=>t.meta?.rist_peak?.recommendation).length===2,id);
        if(kind==='ftir'){
          await page.evaluate(async id=>{const g=document.getElementById(id);await g._ristSetSignalMode('transmittance');await g._ristStackReady;await g._ristSetSignalMode('absorbance');await g._ristStackReady;},id);
          assert.equal(await recommendationCount(),2);
        }
        await open();
        // Model failure and user cancellation cannot mutate the figure.
        fail=true;await submit();await page.waitForFunction(()=>document.querySelector('.rist-peak-recommend-dialog [role=status]').textContent.includes('LLM 연결 실패'));assert.equal(await recommendationCount(),2);fail=false;
        delay=true;await submit();while(!pending)await new Promise(r=>setTimeout(r,10));
        await dialog.getByRole('button',{name:'닫기 (Esc)'}).press('Escape');await pending().catch(()=>{});pending=null;await page.waitForTimeout(100);assert.equal(await recommendationCount(),2);
        await open();await submit();while(!pending)await new Promise(r=>setTimeout(r,10));
        await page.evaluate(id=>document.getElementById(id).dispatchEvent(new CustomEvent('rist-plot-data-replaced')),id);
        await pending().catch(()=>{});pending=null;await page.waitForTimeout(200);assert.equal(await recommendationCount(),2);
        await page.locator('#'+id).screenshot({path:path.join(output,label+'-graph.png')});
        await page.locator('#sw-graph-name').fill('추천 피크 검토본');await page.locator('#sw-save-graph').click();
        await page.locator('.sw-card').first().getByRole('button',{name:'파일 내보내기',exact:true}).click();
        const saveDialog=page.getByRole('dialog',{name:'파일 저장',exact:true}),downloadPromise=page.waitForEvent('download');
        await saveDialog.getByRole('button',{name:'다운로드',exact:true}).click();
        const download=await downloadPromise,filename=path.join(output,label+'.ristgraph');await download.saveAs(filename);
        const restored=await page.evaluate(async({bytes,kind})=>{
          const file=await window.RistSpectrumGraphFile.load(new File([new Uint8Array(bytes)],'peaks.ristgraph'),kind);
          return {peaks:file.state.plotData.filter(t=>t.meta?.rist_peak?.recommendation),raw:await file.state.files[0].blob.text()};
        },{bytes:[...fs.readFileSync(filename)],kind});
        assert.equal(restored.peaks.length,2);assert(restored.peaks.every(t=>t.meta.rist_peak.recommendation.material==='탄소 시료 검토'));assert.equal(restored.raw,raw.toString());
        assert.equal(restored.peaks[0].meta.rist_peak.label,'재검토 피크');assert.equal(restored.peaks[0].meta.rist_peak.recommendation.suggested_name,'검토 피크 A');
        assert.deepEqual(errors,[]);console.log(label+': review before mutation, selection/name edits, zero sensitivity, duplicates, batch undo/redo, cancellation, export passed; requests='+requests);
      } catch(error) {await page.screenshot({path:path.join(output,label+'-failure.png'),fullPage:true});throw error;}
      finally{await page.close();}
    }
    console.log(JSON.stringify({browser:browser.version(),output}));
  } finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error,logs,output);process.exitCode=1;});

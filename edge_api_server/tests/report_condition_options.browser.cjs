// Actual Chromium UI checks, including the Chrome49 fallback bundle; not Windows hardware testing.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-condition-options-'));
const server=spawn(process.env.RIST_TEST_PYTHON||'python3',['-u',path.join(__dirname,'spectrum_workflow_fixture.py'),output],{env:process.env});
let logs='';server.stderr.on('data',data=>logs+=data);
const port=new Promise((resolve,reject)=>{let data='';server.stdout.on('data',chunk=>{data+=chunk;if(data.includes('\n'))resolve(JSON.parse(data.split('\n')[0]).port);});server.on('exit',code=>{if(code)reject(Error(logs));});});
const key='rist-ftir-report-condition-options-v1';
(async()=>{
  const origin='http://127.0.0.1:'+await port,browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try{
    for(const legacy of [false,true])for(const width of [1366,390]){
      const context=await browser.newContext({viewport:{width,height:900},...(legacy?{userAgent:'Mozilla/5.0 (Windows NT 5.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/49.0.2623.75 Safari/537.36'}:{})});
      const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());page.setDefaultTimeout(30000);
      const name=(legacy?'chrome49':'native')+'-'+width;
      try{
        const ready=async()=>{await page.waitForFunction(()=>document.getElementById('sw-step-2')&&!document.getElementById('sw-step-2').disabled);};
        const conditions=async()=>{if(await page.locator('#ftir-report-meta details').getAttribute('open')===null)await page.locator('#ftir-report-meta summary').click();};
        const options=()=>page.locator('#ftir-report-options-open').click();
        const save=()=>page.locator('#ftir-report-options-save').click();
        const values=()=>page.locator('#ftir-report-equipment-model-options option').evaluateAll(items=>items.map(i=>i.value));
        const persisted=()=>page.evaluate(key=>JSON.parse(localStorage.getItem(key)),key);
        const model=page.locator('[data-report-field="equipmentModel"]');
        const pick=async value=>{
          const button=page.getByRole('button',{name:'장비모델 선택지 열기',exact:true});
          // Finish viewport scrolling first: the existing picker intentionally closes on scroll.
          await button.scrollIntoViewIfNeeded();await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
          await button.click();await page.getByRole('option',{name:value,exact:true}).click();
        };
        await page.goto(origin+'/ftir');await ready();
        // Upgrade from old preferences without erasing other custom conditions.
        await page.evaluate(key=>localStorage.setItem(key,JSON.stringify({analysisType:['현장 분석방법'],detector:['현장 검출기']})),key);
        await page.reload();await ready();await conditions();
        assert.deepEqual(await values(),[]);await model.fill('현장 직접 입력');
        await options();
        for(const text of ['Nicolet iS50','  Vertex 70  ','nicolet is50','']){
          await page.getByRole('button',{name:'장비모델 추가',exact:true}).click();
          await page.locator('[data-option-field="equipmentModel"]').last().fill(text);
        }
        await page.locator('#ftir-report-options-modal').screenshot({path:path.join(output,name+'-options.png')});
        await save();assert.deepEqual(await values(),['Nicolet iS50','Vertex 70']);assert.equal(await model.inputValue(),'현장 직접 입력');
        assert.deepEqual((await persisted()).analysisType,['현장 분석방법']);assert.deepEqual((await persisted()).detector,['현장 검출기']);
        await pick('Nicolet iS50');assert.equal(await model.inputValue(),'Nicolet iS50');
        await options();await page.locator('[data-option-field="equipmentModel"]').first().fill('취소할 수정');await page.locator('#ftir-report-options-cancel').click();
        assert.deepEqual(await values(),['Nicolet iS50','Vertex 70']);
        await options();await page.locator('[data-option-field="equipmentModel"]').first().fill('Nicolet iS50R');
        await page.getByRole('button',{name:'장비모델 선택지 삭제',exact:true}).last().click();await save();
        assert.deepEqual(await values(),['Nicolet iS50R']);assert.equal(await model.inputValue(),'Nicolet iS50');
        await pick('Nicolet iS50R');
        // Workspace persistence is debounced separately from choice persistence.
        await page.waitForTimeout(1000);await page.reload();await ready();await conditions();
        assert.deepEqual(await values(),['Nicolet iS50R']);assert.equal(await model.inputValue(),'Nicolet iS50R');
        await page.locator('#ftir-report-meta').screenshot({path:path.join(output,name+'-field.png')});
        if(!legacy&&width===1366){
          const raw=Buffer.from(Array.from({length:241},(_,i)=>{const x=400+i*15;return x+','+(.05+Math.exp(-Math.pow(x-1700,2)/(2*55**2)));}).join('\n'));
          await page.locator('#ftir-file-input').setInputFiles({name:'equipment.dpt',mimeType:'text/plain',buffer:raw});
          await page.waitForFunction(()=>document.getElementById('spectrum-workflow').dataset.step==='2'&&!document.getElementById('sw-save-graph').disabled);
          await page.locator('#sw-save-graph').click();await page.locator('.sw-card').waitFor();await page.locator('#sw-next').click();
          const frame=page.frameLocator('#sw-report-editor');await frame.locator('#edit-page option').first().waitFor({state:'attached',timeout:180000});
          const editorUrl=await page.locator('#sw-report-editor').getAttribute('src');
          const response=await page.request.get(origin+editorUrl.replace('/edit?workflow=1','/editor-data'));assert.equal(response.status(),200);
          const document=(await response.json()).document,rows=document.sections.flatMap(s=>s.rows||[]);
          assert(rows.some(row=>row.includes('장비모델')&&row.includes('Nicolet iS50R')),'selected equipment model must reach the report');
          await page.locator('#sw-step-1').click();await conditions();
        }
        // Empty lists are intentional and survive reload; deleting choices does not clear the current report value.
        await options();await page.getByRole('button',{name:'장비모델 선택지 삭제',exact:true}).click();await save();
        assert.deepEqual(await values(),[]);assert.equal(await model.inputValue(),'Nicolet iS50R');
        await page.reload();await ready();assert.deepEqual(await values(),[]);
        assert.deepEqual(errors,[]);console.log(name+': add/edit/delete/cancel/pick/reload, legacy preferences, manual value preservation passed');
      }catch(e){await page.screenshot({path:path.join(output,name+'-failure.png'),fullPage:true});throw e;}finally{await context.close();}
    }
  }finally{await browser.close();server.kill();console.log('Artifacts: '+output);}
})().catch(e=>{server.kill();console.error(e);console.error(logs.slice(-4000));process.exitCode=1;});

// Real RAW/report/revision/export through desktop Chrome and the Chrome49 UA fallback.
// This is not a Windows/Supermium hardware test.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn,execFileSync}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-editor-conditions-')),python=process.env.RIST_TEST_PYTHON||'python3';
const server=spawn(python,['-u',path.join(__dirname,'spectrum_workflow_fixture.py'),output],{env:process.env});
let logs='';server.stderr.on('data',data=>logs+=data);
const port=new Promise((resolve,reject)=>{let data='';server.stdout.on('data',chunk=>{data+=chunk;if(data.includes('\n'))resolve(JSON.parse(data.split('\n')[0]).port);});server.on('exit',code=>{if(code)reject(Error(logs));});});
const raw=Buffer.from(Array.from({length:241},(_,i)=>{const x=400+i*15;return x+','+(.05+Math.exp(-Math.pow(x-1700,2)/(2*55**2)));}).join('\n'));
(async()=>{
  const origin='http://127.0.0.1:'+await port;
  const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try{
    for(const kind of ['ftir','raman'])for(const legacy of [false,true]){
      const width=legacy?390:1440,name=kind+'-'+(legacy?'fallback-mobile':'desktop'),errors=[];
      const context=await browser.newContext({viewport:{width,height:1100},...(legacy?{userAgent:'Mozilla/5.0 (Windows NT 5.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/49.0.2623.75 Safari/537.36'}:{})});
      const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());page.setDefaultTimeout(30000);
      const field=kind==='ftir'?'equipmentModel':'excitationWavelength',label=kind==='ftir'?'장비모델':'Excitation Wavelength';
      const key='rist-'+kind+'-report-condition-options-v'+(kind==='ftir'?'1':'2');
      const value=kind==='ftir'?'Vertex 70 test':'785 nm',original=kind==='ftir'?'RAW model':'532 nm';
      const frame=page.frameLocator('#sw-report-editor');
      const base=()=>page.locator('#sw-report-editor').evaluate(f=>f.contentWindow.location.pathname.replace(/\/edit$/,''));
      const get=async suffix=>{const r=await page.request.get(origin+await base()+suffix);assert.equal(r.status(),200);return r.json();};
      const ready=()=>page.waitForFunction(()=>{const f=document.getElementById('sw-report-editor'),d=f&&f.contentDocument;
        return d&&d.getElementById('page-add')&&!d.getElementById('page-add').disabled&&d.getElementById('slide-image').naturalWidth>0;
      },null,{timeout:180000});
      try{
        await page.goto(origin+'/'+kind);await page.waitForFunction(()=>document.getElementById('sw-step-2')&&!document.getElementById('sw-step-2').disabled);
        await page.evaluate(({key,field,value})=>localStorage.setItem(key,JSON.stringify({[field]:[value]})),{key,field,value});
        await page.reload();await page.waitForFunction(()=>document.getElementById('sw-step-2')&&!document.getElementById('sw-step-2').disabled);
        await page.locator('#'+kind+'-report-meta summary').click();
        await page.locator('[data-report-field="'+field+'"]').fill(original);
        await page.locator('#'+kind+'-file-input').setInputFiles({name:'condition.'+(kind==='ftir'?'dpt':'txt'),mimeType:'text/plain',buffer:raw});
        await page.waitForFunction(()=>!document.getElementById('sw-save-graph').disabled);
        await page.locator('#sw-save-graph').click();await page.locator('.sw-card').waitFor();await page.locator('#sw-next').click();
        await frame.locator('#edit-page').waitFor({timeout:180000});await ready();
        const initial=(await get('/editor-data')).document,conditionSection=initial.sections.find(s=>s.sectionId==='experiment_conditions'),rows=conditionSection.rows;
        const index=rows.findIndex(row=>row[0]==='공통'&&row[1]===label);assert(index>=0);assert.equal(rows[index][2],original);
        if(legacy){
          assert(await page.locator('script[src*="/assets/browser-compat/"]').count()>0,'Chrome49 workspace bundle must load');
          await page.locator('#sw-report-editor').scrollIntoViewIfNeeded();
          if(await frame.locator('#page-sidebar-close').isVisible())await frame.locator('#page-sidebar-close').click();
        }
        const target=frame.locator('[aria-label="'+conditionSection.heading+' '+(index+1)+'행 값"]');
        const picker=target.locator('..').locator('.report-condition-picker');
        assert.deepEqual(await picker.locator('option').allTextContents(),['저장된 선택지에서 선택',value]);
        await frame.getByRole('button',{name:'선택지 관리',exact:true}).click();
        await frame.getByRole('combobox',{name:'관리할 실험조건·환경 항목',exact:true}).selectOption(field);
        await frame.locator('.report-condition-option-row input').first().fill('cancelled');
        await frame.getByRole('button',{name:'취소',exact:true}).click();assert.equal(await target.inputValue(),original);
        await frame.getByRole('button',{name:'선택지 관리',exact:true}).click();
        const chosen=kind==='ftir'?'Nicolet iS50 review':'633 nm';
        await frame.locator('.report-condition-option-row input').first().fill(chosen);
        await frame.getByRole('button',{name:'선택지 추가',exact:true}).click();
        await frame.locator('.report-condition-option-row input').last().fill('discard');
        await frame.getByRole('button',{name:label+' 선택지 2 삭제',exact:true}).click();
        await frame.locator('#report-condition-manager').screenshot({path:path.join(output,name+'-manager.png')});
        const old=await base();await frame.getByRole('button',{name:'선택지 저장',exact:true}).click();
        assert.equal(await base(),old);assert.equal(await target.inputValue(),original);
        assert.deepEqual(await page.evaluate(({key,field})=>JSON.parse(localStorage.getItem(key))[field],{key,field}),[chosen]);
        // A storage event from the editor iframe must update the still-open analysis form too.
        const listId=kind==='ftir'?'ftir-report-equipment-model-options':'raman-report-excitation-wavelength-options';
        await page.waitForFunction(({listId,chosen})=>document.getElementById(listId).options[0].value===chosen,{listId,chosen});
        await picker.selectOption('0');
        await page.waitForFunction(old=>{const f=document.getElementById('sw-report-editor'),d=f.contentDocument;
          return d.getElementById('status').classList.contains('error')||(f.contentWindow.location.pathname.replace(/\/edit$/,'')!==old&&d.getElementById('status').textContent.includes('자동 저장 완료'));
        },old,{timeout:180000});
        assert(!(await frame.locator('#status').getAttribute('class')).includes('error'),await frame.locator('#status').textContent());await ready();
        const updated=(await get('/editor-data')).document,expected=structuredClone(initial.sections);expected.find(s=>s.sectionId==='experiment_conditions').rows[index][2]=chosen;
        assert.deepEqual(updated.sections.map(s=>s.rows),expected.map(s=>s.rows));
        const layout=await get('/edit-layout'),ref='section/experiment_conditions/rows/'+index+'/2';
        const slide=layout.pages.findIndex(p=>p.regions.some(r=>r.refs.includes(ref)));assert(slide>=0);
        await frame.locator('#edit-page').selectOption(String(slide));await ready();
        await frame.locator('#slide-stage').screenshot({path:path.join(output,name+'-ppt.png')});
        await picker.scrollIntoViewIfNeeded();await frame.locator('.form-pane').screenshot({path:path.join(output,name+'-conditions.png')});
        assert(await picker.evaluate(e=>e.getBoundingClientRect().right<=window.innerWidth));
        assert(await frame.locator('body').evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1));
        const r=await page.request.get(origin+await base()+'/download');assert.equal(r.status(),200);
        const zip=path.join(output,name+'.zip');fs.writeFileSync(zip,await r.body());
        console.log(execFileSync(python,['-c',`import io,sys,zipfile
from pptx import Presentation
from pypdf import PdfReader
z=zipfile.ZipFile(sys.argv[1]);p=Presentation(io.BytesIO(z.read('report.pptx')))
text=' '.join(cell.text for slide in p.slides for shape in slide.shapes if shape.has_table for row in shape.table.rows for cell in row.cells)
assert sys.argv[2] in text
assert sys.argv[2] in ''.join(p.extract_text() for p in PdfReader(io.BytesIO(z.read('report.pdf'))).pages)
print('Final PPT/PDF contain selected condition: '+sys.argv[2])`,zip,chosen],{encoding:'utf8'}).trim());
        // Back/reload keeps both saved option lists and revised report values.
        await page.reload();await frame.locator('#edit-page').waitFor({timeout:180000});await ready();
        assert.equal(await target.inputValue(),chosen);assert.equal(await picker.locator('option').nth(1).textContent(),chosen);
        assert.deepEqual(errors,[]);console.log(name+': choice management, analysis synchronization, autosave, actual PPT/PDF and reload passed');
      }catch(e){await page.screenshot({path:path.join(output,name+'-failure.png'),fullPage:true});throw e;}
      finally{await context.close();}
    }
  }finally{await browser.close();server.kill();console.log('Artifacts: '+output);}
})().catch(e=>{server.kill();console.error(e);console.error(logs.slice(-5000));process.exitCode=1;});

// Actual RAW -> PPT -> LibreOffice PDF -> browser edit -> revision -> ZIP.
// This is local Chrome coverage, not Windows/Supermium hardware certification.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn,execFileSync}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-native-tables-'));
const python=process.env.RIST_TEST_PYTHON||'python3';
const server=spawn(python,['-u',path.join(__dirname,'spectrum_workflow_fixture.py'),output],{env:process.env});
let logs='';server.stderr.on('data',data=>logs+=data);
const port=new Promise((resolve,reject)=>{let data='';server.stdout.on('data',chunk=>{data+=chunk;if(data.includes('\n'))resolve(JSON.parse(data.split('\n')[0]).port);});server.on('exit',code=>{if(code)reject(Error(logs));});});
const raw=Buffer.from(Array.from({length:241},(_,i)=>{const x=400+i*15;return x+','+(.05+Math.exp(-Math.pow(x-1700,2)/(2*55**2)));}).join('\n'));
(async()=>{
  const origin='http://127.0.0.1:'+await port;
  const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try {
    for(const kind of ['ftir','raman'])for(const legacy of [false,true]){
      const name=kind+(legacy?'-chrome49-UA':'-desktop');
      const page=await browser.newPage({viewport:{width:1600,height:1200},...(legacy?{userAgent:'Mozilla/5.0 (Windows NT 5.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/49.0.2623.75 Safari/537.36'}:{})}),errors=[];
      page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());page.setDefaultTimeout(30000);
      const frame=page.frameLocator('#sw-report-editor');
      const base=()=>page.locator('#sw-report-editor').evaluate(f=>f.contentWindow.location.pathname.replace(/\/edit$/,''));
      const get=async suffix=>{const response=await page.request.get(origin+await base()+suffix);assert.equal(response.status(),200);return response.json();};
      const ready=()=>page.waitForFunction(()=>{const f=document.getElementById('sw-report-editor'),d=f&&f.contentDocument;
        return d&&d.getElementById('page-add')&&!d.getElementById('page-add').disabled&&d.getElementById('slide-image').complete&&d.getElementById('slide-image').naturalWidth>0;
      },null,{timeout:180000});
      const saved=async old=>{
        await page.waitForFunction(old=>{const f=document.getElementById('sw-report-editor'),d=f.contentDocument;
          return d.getElementById('status').classList.contains('error')||(f.contentWindow.location.pathname.replace(/\/edit$/,'')!==old&&d.getElementById('status').textContent.includes('자동 저장 완료'));
        },old,{timeout:180000});
        assert(!(await frame.locator('#status').getAttribute('class')).includes('error'),await frame.locator('#status').textContent());await ready();
      };
      try {
        await page.goto(origin+'/'+kind);await page.waitForFunction(()=>document.getElementById('sw-step-2')&&!document.getElementById('sw-step-2').disabled);
        await page.locator('#'+kind+'-file-input').setInputFiles({name:'native-table.'+(kind==='ftir'?'dpt':'txt'),mimeType:'text/plain',buffer:raw});
        await page.locator('#sw-save-graph').waitFor({state:'visible'});await page.waitForFunction(()=>!document.getElementById('sw-save-graph').disabled);
        await page.locator('#sw-save-graph').click();await page.locator('.sw-card').waitFor();
        await page.locator('#sw-next').click();await frame.locator('#page-add').waitFor({timeout:180000});await ready();
        const layout=await get('/edit-layout');let initial=(await get('/editor-data')).document;
        if(legacy)assert(await page.locator('script[src*="/assets/browser-compat/"]').count()>0);
        // Normal editing binds directly to the source, without a detached dialog.
        const si=layout.pages.findIndex(p=>p.regions.some(r=>r.refs.length===1&&/^section\/experiment_conditions\/rows\/\d+\/2$/.test(r.refs[0])));
        assert(si>=0);const region=layout.pages[si].regions.find(r=>r.refs.length===1&&/^section\/experiment_conditions\/rows\/\d+\/2$/.test(r.refs[0]));
        const ri=Number(region.refs[0].split('/')[3]),section=initial.sections.find(s=>s.sectionId==='experiment_conditions');
        const sourceLabel=section.heading+' '+(ri+1)+'행 '+section.columns[2];
        await frame.locator('#edit-page').selectOption(String(si));await ready();
        await frame.getByRole('button',{name:'PPT 편집 · '+sourceLabel,exact:true}).click();
        let old=await base();const sourceInput=frame.locator('.ppt-cell-input');
        assert(await sourceInput.evaluate(e=>e===document.activeElement));
        await sourceInput.fill('직접 입력 검증');
        assert.equal(await frame.getByLabel(sourceLabel,{exact:true}).inputValue(),'직접 입력 검증');
        assert(await frame.locator('#inline-editor').isHidden());
        await frame.locator('#slide-stage').screenshot({path:path.join(output,name+'-source-focus.png')});
        await sourceInput.press('Enter');await saved(old);
        initial=(await get('/editor-data')).document;assert.equal(initial.sections.find(s=>s.sectionId===section.sectionId).rows[ri][2],'직접 입력 검증');
        const index=layout.pages.findIndex(p=>p.title!=='보고서 전송 정보'&&p.objects.some(o=>o.kind==='table'&&o.cells.some(c=>c.row===1&&c.col===1)));
        assert(index>=0);const target=layout.pages[index],table=target.objects.find(o=>o.kind==='table');
        await frame.locator('#edit-page').selectOption(String(index));await ready();
        await frame.locator('#slide-stage').screenshot({path:path.join(output,kind+'-table-before.png')});
        assert(await frame.locator('.edit-region').count()>1);
        await frame.locator('#canvas-toggle').click();
        await frame.locator('[aria-label="페이지 개체 선택"]').selectOption(table.id);
        assert.equal(await frame.locator('.canvas-object').count(),target.objects.length);
        await frame.locator('[data-object-id="'+table.id+'"] .canvas-cell[data-row="1"][data-col="1"]').click();
        old=await base();
        const inline=frame.locator('.ppt-cell-input');assert(await inline.evaluate(e=>e===document.activeElement));
        await inline.fill('표 셀 수정 '+kind);
        await frame.locator('#slide-stage').screenshot({path:path.join(output,name+'-canvas-focus.png')});
        await inline.press('Enter');await saved(old);old=await base();
        await frame.locator('[aria-label="개체 채우기 색 (none: 없음)"]').fill('#fff4cc');
        await frame.locator('[aria-label="개체 채우기 색 (none: 없음)"]').blur();
        await saved(old);
        let model=(await get('/editor-data')).document;
        assert.deepEqual(model.sections,initial.sections);
        assert(model.canvasEdits[target.id].some(o=>o.tableCells.some(c=>c.text==='표 셀 수정 '+kind)));
        // Move and resize the whole grid, not separate cells.
        old=await base();
        await frame.locator('[aria-label="개체 왼쪽 (%)"]').fill('8');await frame.locator('[aria-label="개체 왼쪽 (%)"]').blur();
        await frame.locator('[aria-label="개체 너비 (%)"]').fill('78');await frame.locator('[aria-label="개체 너비 (%)"]').blur();
        await saved(old);
        let current=(await get('/edit-layout')).pages.find(p=>p.id===target.id),native=current.objects.find(o=>o.kind==='table');
        assert.equal(native.x,.08);assert.equal(native.w,.78);
        const source=target.regions.find(r=>r.y>=table.y&&r.x>=table.x);
        assert(source);const expectedX=.08+(source.x-table.x)*.78/table.w;
        assert(current.regions.some(r=>Math.abs(r.x-expectedX)<1e-6));
        await frame.locator('#slide-stage').screenshot({path:path.join(output,kind+'-table-edited.png')});
        await frame.locator('.canvas-properties').screenshot({path:path.join(output,kind+'-cell-controls.png')});
        old=await base();await frame.getByRole('button',{name:'개체 실행 취소',exact:true}).click();await saved(old);
        old=await base();await frame.getByRole('button',{name:'개체 다시 실행',exact:true}).click();await saved(old);
        await page.reload();await frame.locator('#page-add').waitFor({timeout:180000});await ready();
        const reloaded=await get('/edit-layout'),reindex=reloaded.pages.findIndex(p=>p.id===target.id);
        await frame.locator('#edit-page').selectOption(String(reindex));await ready();
        native=reloaded.pages[reindex].objects.find(o=>o.kind==='table');assert.equal(native.w,.78);
        assert.equal(native.cells.find(c=>c.row===1&&c.col===1).text,'표 셀 수정 '+kind);
        // Mobile controls: selection/edit via tap rather than requiring dragging.
        await page.setViewportSize({width:390,height:844});
        await page.locator('#sw-report-editor').evaluate(el=>el.scrollIntoView({block:'start'}));
        if(await frame.locator('#page-sidebar-close').isVisible())await frame.locator('#page-sidebar-close').click();
        await frame.locator('#canvas-toggle').click();await frame.locator('[aria-label="페이지 개체 선택"]').selectOption(table.id);
        await frame.locator('[aria-label="편집할 표 셀"]').selectOption('1:1');
        assert.equal(await frame.locator('[aria-label="개체 내용"]').inputValue(),'표 셀 수정 '+kind);
        assert.equal(await frame.locator('[aria-label="개체 글자 크기 (pt)"]').inputValue(),'12.5');
        assert.equal(await frame.locator('[aria-label="개체 정렬"]').inputValue(),'left');
        assert(await frame.locator('[aria-label="개체 테두리 색 (none: 없음)"]').inputValue());
        assert(await frame.locator('body').evaluate(el=>el.scrollWidth<=el.ownerDocument.documentElement.clientWidth+2));
        await frame.locator('[data-object-id="'+table.id+'"] .canvas-cell[data-row="1"][data-col="1"]').click();
        assert(await frame.locator('.ppt-cell-input').evaluate(e=>e===document.activeElement&&e.getBoundingClientRect().right<=window.innerWidth+2));
        await frame.locator('.ppt-cell-input').fill('이 값은 취소');await frame.locator('.ppt-cell-input').press('Escape');
        assert.equal(await frame.locator('[aria-label="개체 내용"]').inputValue(),'표 셀 수정 '+kind);
        await frame.locator('.canvas-properties').screenshot({path:path.join(output,kind+'-mobile-controls.png')});
        const response=await page.request.get(origin+await base()+'/download');assert.equal(response.status(),200);
        const zip=path.join(output,kind+'-tables.zip');fs.writeFileSync(zip,await response.body());
        console.log(execFileSync(python,['-c',`import io,sys,zipfile
from pptx import Presentation
from pypdf import PdfReader
z=zipfile.ZipFile(sys.argv[1]);p=Presentation(io.BytesIO(z.read('report.pptx')))
tables=[s for slide in p.slides for s in slide.shapes if s.has_table]
assert tables
assert not any(s.name.startswith(('Cell ','Header ')) for slide in p.slides for s in slide.shapes)
assert any('표 셀 수정 '+sys.argv[2]==cell.text for s in tables for row in s.table.rows for cell in row.cells)
assert z.read('report.pdf')==z.read('report-from-pptx.pdf')
pdf=PdfReader(io.BytesIO(z.read('report.pdf')))
assert len(pdf.pages)==len(p.slides)
assert '표 셀 수정' in ''.join(page.extract_text() for page in pdf.pages)
print(sys.argv[2]+': native tables, edited cell, final PPT/PDF consistency verified')`,zip,kind],{encoding:'utf8'}).trim());
        assert.deepEqual(errors,[]);console.log(name+': in-place source/canvas cell edit, move/resize, undo/redo, reload, mobile and export passed');
      }catch(error){await page.screenshot({path:path.join(output,kind+'-failure.png'),fullPage:true});throw error;}
      finally{await page.close();}
    }
  }finally{await browser.close();server.kill();console.log('Artifacts: '+output);}
})().catch(error=>{server.kill();console.error(error);console.error(logs.slice(-5000));process.exitCode=1;});

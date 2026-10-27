// Real LibreOffice/PPT/PDF test on Mac Chrome, NOT Windows/Supermium certification.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn,execFileSync}=require('node:child_process'),{chromium}=require('playwright');
const resizeImage=require('./canvas_resize_steps.cjs');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-tem-editor-')),python=process.env.RIST_TEST_PYTHON||'python3';
const server=spawn(python,['-u',path.join(__dirname,'tem_editor_fixture.py'),output],{env:process.env});
let logs='';server.stderr.on('data',data=>logs+=data);
const startup=new Promise((resolve,reject)=>{let data='';server.stdout.on('data',chunk=>{data+=chunk;if(data.includes('\n'))resolve(JSON.parse(data.split('\n')[0]));});server.on('exit',code=>reject(Error('fixture exited '+code+' '+logs)));});
(async()=>{
  const start=await startup,origin='http://127.0.0.1:'+start.port;
  const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try{
    const page=await browser.newPage({viewport:{width:1500,height:1100}}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());page.setDefaultTimeout(30000);
    const base=()=>page.evaluate(()=>location.pathname.replace(/\/edit$/,''));
    const get=async tail=>{const r=await page.request.get(origin+await base()+tail);assert.equal(r.status(),200);return r.json();};
    const ready=()=>page.waitForFunction(()=>!document.getElementById('page-add').disabled&&document.getElementById('slide-image').complete&&document.getElementById('slide-image').naturalWidth>0,null,{timeout:180000});
    const saved=async old=>{
      await page.waitForFunction(old=>document.getElementById('status').classList.contains('error')||
        (location.pathname.replace(/\/edit$/,'')!==old&&document.getElementById('status').textContent.includes('자동 저장 완료')),old,{timeout:180000});
      assert(!(await page.locator('#status').getAttribute('class')).includes('error'),await page.locator('#status').textContent());await ready();
    };
    try{
      await page.goto(origin+'/api/v1/tem/report/jobs/'+start.jobId+'/edit');await ready();
      const initial=await get('/edit-layout');assert.equal(initial.pages.length,5);
      await page.locator('#slide-stage').screenshot({path:path.join(output,'cover.png')});
      let old=await base();await page.locator('#page-add').click();
      await page.getByLabel('추가 페이지 제목',{exact:true}).fill('TEM 추가 검토');
      await page.getByLabel('추가 페이지 본문',{exact:true}).fill('원본 분석 결과를 보존한 검토 페이지입니다.');await saved(old);
      const added=(await get('/editor-data')).document.pageEdits.added[0].id;
      await page.locator('#canvas-toggle').click();old=await base();
      await page.getByRole('button',{name:'글자 삽입',exact:true}).click();
      await page.getByLabel('개체 내용',{exact:true}).fill('TEM 검토 완료');
      await page.getByLabel('개체 위 (%)',{exact:true}).fill('60');await page.getByLabel('개체 위 (%)',{exact:true}).blur();await saved(old);
      old=await base();await page.getByRole('button',{name:'개체 실행 취소',exact:true}).click();await saved(old);
      old=await base();await page.getByRole('button',{name:'개체 다시 실행',exact:true}).click();await saved(old);
      old=await base();await page.getByRole('button',{name:'선 삽입',exact:true}).click();
      await page.getByLabel('개체 선 종류',{exact:true}).selectOption('dash');
      await page.getByLabel('개체 위 (%)',{exact:true}).fill('85');await page.getByLabel('개체 위 (%)',{exact:true}).blur();await saved(old);
      const png=fs.readFileSync(path.join(output,'test-image.png'));
      old=await base();await page.locator('#canvas-file').setInputFiles({name:'test.png',mimeType:'image/png',buffer:png});await saved(old);
      old=await base();
      for(const [label,value] of [['너비','25'],['높이','15'],['왼쪽','65'],['위','60']]){
        await page.getByLabel('개체 '+label+' (%)',{exact:true}).fill(value);await page.getByLabel('개체 '+label+' (%)',{exact:true}).blur();
      }
      await saved(old);
      const imageId=(await get('/editor-data')).document.canvasEdits[added].find(o=>o.kind==='image').id;
      const imageGeometry=await resizeImage({page,editor:page,objectId:imageId,read:async()=>(await get('/editor-data')).document.canvasEdits[added].find(o=>o.id===imageId),commit:async action=>{const before=await base();await action();await saved(before);}});
      // Native tables inherit styles in TEM: edit a cell, then move/resize the whole table.
      let layout=await get('/edit-layout'),tableIndex=layout.pages.findIndex(p=>p.objects.some(o=>o.kind==='table'));
      const target=layout.pages[tableIndex],table=target.objects.find(o=>o.kind==='table');
      await page.locator('#edit-page').selectOption(String(tableIndex));await ready();
      await page.getByLabel('페이지 개체 선택',{exact:true}).selectOption(table.id);
      await page.locator('[data-object-id="'+table.id+'"] .canvas-cell[data-row="1"][data-col="1"]').click();old=await base();
      assert(await page.locator('.ppt-cell-input').evaluate(e=>e===document.activeElement));
      await page.locator('.ppt-cell-input').fill('TEM 표 검토 후');
      await page.locator('#slide-stage').screenshot({path:path.join(output,'native-table-focus.png')});
      await page.locator('.ppt-cell-input').press('Enter');await saved(old);old=await base();
      await page.getByLabel('개체 굵게',{exact:true}).check();
      await page.getByLabel('개체 너비 (%)',{exact:true}).fill('75');await page.getByLabel('개체 너비 (%)',{exact:true}).blur();await saved(old);
      const native=(await get('/edit-layout')).pages.find(p=>p.id===target.id).objects.find(o=>o.id===table.id);
      assert(Math.abs(native.w-.75)<1e-6);assert(native.cells.find(c=>c.row===1&&c.col===1).bold);
      await page.locator('#slide-stage').screenshot({path:path.join(output,'native-table.png')});
      // Selection, multi-page move, delete/restore and reload.
      await page.locator('[data-page-id="'+initial.pages[0].id+'"]').click();await ready();
      await page.locator('[data-page-id="'+added+'"]').click({modifiers:['Shift']});await ready();
      assert.equal(await page.locator('.page-thumb[aria-pressed="true"]').count(),2);
      old=await base();await page.locator('#page-down').click();await saved(old);
      old=await base();await page.locator('#page-delete').click();await saved(old);
      assert.equal((await get('/edit-layout')).pages.length,4);
      old=await base();await page.locator('#page-restore').click();await saved(old);
      assert.equal((await get('/edit-layout')).pages.length,6);
      await page.reload();await ready();layout=await get('/edit-layout');
      await page.locator('#edit-page').selectOption(String(layout.pages.findIndex(p=>p.id===added)));await ready();
      await page.locator('#slide-stage').screenshot({path:path.join(output,'added-page.png')});
      const response=await page.request.get(origin+await base()+'/download');assert.equal(response.status(),200);
      const zip=path.join(output,'tem-final.zip');fs.writeFileSync(zip,await response.body());
      console.log(execFileSync(python,['-c',`import io,sys,zipfile
from pptx import Presentation
from pypdf import PdfReader
z=zipfile.ZipFile(sys.argv[1]);p=Presentation(io.BytesIO(z.read('tem-report.pptx')))
pdf=PdfReader(io.BytesIO(z.read('tem-report.pdf')))
assert len(p.slides)==len(pdf.pages)==6
geometry=__import__('json').loads(sys.argv[2]);image=next(s for slide in p.slides for s in slide.shapes if s.name==geometry['id'])
assert abs(image.width-round(geometry['w']*p.slide_width))<=1
assert abs(image.height-round(geometry['h']*p.slide_height))<=1
text=''.join(s.text for slide in p.slides for s in slide.shapes if s.has_text_frame)
assert 'TEM 검토 완료' in text and 'TEM 추가 검토' in text
assert any(c.text=='TEM 표 검토 후' for slide in p.slides for s in slide.shapes if s.has_table for row in s.table.rows for c in row.cells)
pdftext=''.join(page.extract_text() for page in pdf.pages)
assert all(t in pdftext for t in ['TEM 추가 검토','TEM 검토 완료','TEM 표 검토 후'])
assert not any('base.pptx' in n or 'editor-assets' in n for n in z.namelist())
print('Final TEM PPT/PDF: six pages, resized image, native table, edited text, private base excluded')`,zip,JSON.stringify(imageGeometry)],{encoding:'utf8'}).trim());
      await page.setViewportSize({width:390,height:844});
      if(await page.locator('#page-sidebar-close').isVisible())await page.locator('#page-sidebar-close').click();
      await page.locator('#canvas-toggle').click();
      await page.getByLabel('페이지 개체 선택',{exact:true}).selectOption({label:(await page.getByLabel('페이지 개체 선택',{exact:true}).locator('option').allTextContents()).find(t=>t.includes('TEM 검토 완료'))});
      assert(await page.locator('body').evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+2));
      await page.locator('.canvas-properties').screenshot({path:path.join(output,'mobile-controls.png')});
      assert.deepEqual(errors,[]);console.log('TEM desktop/mobile: page add/order/multiselect/delete/restore, text/image/line/table, undo/redo, autosave/reload passed');
    }catch(e){await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});throw e;}
    finally{await page.close();}
  }finally{await browser.close();server.kill();console.log('Artifacts: '+output);}
})().catch(e=>{server.kill();console.error(e);console.error(logs.slice(-6000));process.exitCode=1;});

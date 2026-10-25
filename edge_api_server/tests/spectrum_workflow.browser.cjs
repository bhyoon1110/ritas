// Actual RAW uploads, IndexedDB, Plotly, multipart jobs, LibreOffice/PPT, and revisions.
// Chromium engine QA is not a claim of Windows XP / Supermium hardware testing.
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const {spawn, execFileSync} = require('node:child_process');
const {pathToFileURL} = require('node:url');
const {chromium} = require('playwright');
const output = fs.mkdtempSync(path.join(os.tmpdir(), 'rist-spectrum-workflow-'));
const server = spawn(process.env.RIST_TEST_PYTHON || 'python3', ['-u', path.join(__dirname, 'spectrum_workflow_fixture.py'), output], {env:process.env});
let logs = ''; server.stderr.on('data', chunk => { logs += chunk; });
const port = new Promise((resolve, reject) => {
  let data = '';
  server.stdout.on('data', chunk => { data += chunk; if (data.includes('\n')) { try { resolve(JSON.parse(data.split('\n')[0]).port); } catch (e) { reject(e); } } });
  server.on('exit', code => { if (code) reject(new Error(logs)); });
});
const raw = Buffer.from(Array.from({length:241}, (_, i) => {
  const x = 400 + i * 15;
  return x + ',' + (.05 + Math.exp(-Math.pow(x - 1700, 2) / (2 * 55 ** 2)) + .55 * Math.exp(-Math.pow(x - 1250, 2) / (2 * 80 ** 2)));
}).join('\n'));
(async () => {
  const origin = 'http://127.0.0.1:' + await port;
  for (let i = 0; i < 80; i++) { try { if ((await fetch(origin + '/ftir')).ok) break; } catch (_) {} await new Promise(r => setTimeout(r, 100)); }
  const browser = await chromium.launch({headless:true, executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try {
    for (const kind of ['ftir','raman']) {
      const page = await browser.newPage({viewport:{width:1440,height:1050}}), errors = [];
      page.on('pageerror', error => errors.push(error.message)); page.on('dialog', dialog => dialog.accept());
      page.setDefaultTimeout(20000);
      const gd = kind === 'ftir' ? 'peak-plot' : 'raman-plot';
      async function sizeGraph(width,height) {
        if(!await page.locator('.rist-axis-control-button').isVisible()) await page.locator('.rist-'+kind+'-tools-toggle').click();
        await page.locator('.rist-axis-control-button').click();
        await page.locator('[data-plot-width]').fill(String(width));
        await page.locator('[data-plot-height]').fill(String(height));
        await page.locator('[data-plot-size-apply]').click();
        await page.waitForFunction(({gd,width,height})=>{const g=document.getElementById(gd);return g.layout.width===width&&g.layout.height===height;},{gd,width,height});
        await page.locator('.rist-plot-size-controls').screenshot({path:path.join(output,kind+'-size-controls.png')});
        await page.locator('.rist-axis-control-close').click();
        if(await page.locator('.rist-'+kind+'-tools-close').isVisible())await page.locator('.rist-'+kind+'-tools-close').click();
      }
      try {
        await page.goto(origin + '/' + kind);
        await page.waitForFunction(() => { const e=document.getElementById('sw-step-2'); return e && !e.disabled; });
        assert.equal(await page.locator('#spectrum-workflow').getAttribute('data-step'),'1');
        const sourceFiles=[{name:'workflow.'+(kind==='ftir'?'dpt':'txt'),mimeType:'text/plain',buffer:raw}];
        if(kind==='ftir')sourceFiles.push({name:'hidden.dpt',mimeType:'text/plain',buffer:raw});
        await page.locator('#'+kind+'-file-input').setInputFiles(sourceFiles);
        await page.waitForFunction(() => document.getElementById('spectrum-workflow').dataset.step==='2' && !document.getElementById('sw-save-graph').disabled);
        if(kind==='ftir')await page.evaluate(async id=>{
          const p=document.getElementById(id);await p._ristStackReady;
          const curve=p.data.findIndex(t=>t.meta&&t.meta.rist_sample_parent&&t.meta.rist_sample_group==='sample:1');
          await p._ristDeleteSample(curve);await p._ristStackReady;
          await Plotly.relayout(p,{'xaxis.range':[2200,1100],'xaxis.autorange':false});
        },gd);
        await page.evaluate(async id => {
          const p=document.getElementById(id);
          await Plotly.relayout(p, {'title.text':'첫 번째 그래프', annotations:(p.layout.annotations||[]).concat([{name:'workflow-note',text:'첫 번째 메모',x:1700,y:1.2,showarrow:false}])});
        },gd);
        await sizeGraph(1000,700);
        const beforeSize=await page.evaluate(id=>{const g=document.getElementById(id);return {x:g.layout.xaxis.range,y:g.layout.yaxis.range,raw:g.data[0].y};},gd);
        const handle=page.locator('.rist-plot-resize-handle');await handle.scrollIntoViewIfNeeded();
        const handleBox=await handle.boundingBox();
        await page.mouse.move(handleBox.x+handleBox.width/2,handleBox.y+handleBox.height/2);await page.mouse.down();
        await page.mouse.move(handleBox.x+handleBox.width/2+100,handleBox.y+handleBox.height/2+120,{steps:10});
        assert.equal(await page.locator('.rist-plot-resize-preview span').textContent(),'1100 × 820 px');
        await page.mouse.up();
        await page.waitForFunction(id=>document.getElementById(id).layout.width===1100&&document.getElementById(id).layout.height===820,gd);
        assert.equal(await page.locator('.rist-plot-resize-preview').count(),0);
        assert.deepEqual(await page.evaluate(id=>{const g=document.getElementById(id);return {x:g.layout.xaxis.range,y:g.layout.yaxis.range,raw:g.data[0].y};},gd),beforeSize);
        await page.locator('#sw-graph-name').fill('비교 그래프 1'); await page.locator('#sw-save-graph').click();
        await page.waitForFunction(() => document.querySelectorAll('.sw-card').length===1 && !document.getElementById('sw-save-graph').disabled);
        await page.locator('#sw-step-1').click();
        while(await page.locator('#'+kind+'-file-list button').count())await page.locator('#'+kind+'-file-list button').last().click();
        await page.waitForFunction(id=>!document.getElementById(id).data.some(t=>t.meta&&t.meta.rist_sample_parent),gd);
        await page.locator('#'+kind+'-file-input').setInputFiles({name:'second.'+(kind==='ftir'?'dpt':'txt'),mimeType:'text/plain',buffer:raw});
        await page.waitForFunction(() => document.getElementById('spectrum-workflow').dataset.step==='2' && !document.getElementById('sw-save-graph').disabled);
        await page.evaluate(async id => { const p=document.getElementById(id); await Plotly.relayout(p, {'title.text':'두 번째 그래프',annotations:(p.layout.annotations||[]).concat([{name:'workflow-note',text:'두 번째 메모',x:1700,y:1.2,showarrow:false}])}); await Plotly.restyle(p, {'line.color':'#e74c3c'}, [0]); },gd);
        await sizeGraph(900,1200);
        await page.locator('#sw-graph-name').fill('비교 그래프 2'); await page.locator('#sw-save-graph').click();
        await page.waitForFunction(() => document.querySelectorAll('.sw-card').length===2 && !document.getElementById('sw-save-graph').disabled);
        await page.locator('.sw-card').first().getByRole('button',{name:'미리보기',exact:true}).click();
        assert.equal(await page.locator('.sw-preview img').count(),1); await page.locator('.sw-preview').getByRole('button',{name:'닫기',exact:true}).click();
        await page.locator('#sw-step-1').click();
        while(await page.locator('#'+kind+'-file-list button').count())await page.locator('#'+kind+'-file-list button').last().click();
        await page.waitForFunction(id=>!document.getElementById(id).data.some(t=>t.meta&&t.meta.rist_sample_parent),gd);
        await page.locator('#sw-step-2').click();
        await page.locator('.sw-card').first().getByRole('button',{name:'다시 편집',exact:true}).click();
        await page.waitForFunction(id => document.getElementById(id).layout.title.text==='첫 번째 그래프' && !document.getElementById('sw-save-graph').disabled,gd);
        assert.equal(await page.evaluate(id=>document.getElementById(id).layout.annotations.find(a=>a.name==='workflow-note').text,gd),'첫 번째 메모');
        await page.waitForFunction(id=>document.getElementById(id).layout.width===1100&&document.getElementById(id).layout.height===820,gd);
        await page.evaluate(async id => { const p=document.getElementById(id),index=p.layout.annotations.findIndex(a=>a.name==='workflow-note');await Plotly.relayout(p,{['annotations['+index+'].text']:'다시 편집한 메모'}); },gd);
        await page.locator('#sw-save-graph').click();
        await page.waitForFunction(()=>!document.getElementById('sw-save-graph').disabled);
        assert.equal(await page.locator('.sw-card').count(),2);
        await page.locator('#'+gd).screenshot({path:path.join(output,kind+'-edit.png')});
        assert.equal(await page.locator('#'+kind+'-home').isVisible(),true);
        assert.equal(await page.locator('#'+kind+'-account-link').isVisible(),true);
        const second=page.locator('.sw-card').nth(1);
        await second.getByRole('button',{name:'비교 그래프 2 앞 순서로',exact:true}).click();
        await page.locator('.sw-card').first().getByRole('checkbox').uncheck();
        await page.locator('.sw-card').first().getByRole('checkbox').check();
        // The next step generates automatically, without a separate build click.
        assert(await page.locator('#sw-next').isEnabled());
        assert((await page.locator('#sw-next').getAttribute('class')).includes('sw-primary'));
        await page.locator('#sw-next').click();
        assert.match(await page.locator('#sw-guide-detail').textContent(),/보고서를 생성하는 중/);
        await page.waitForFunction(()=>{const f=document.getElementById('sw-report-editor');return f.src.includes('/edit?workflow=1')&&!f.hidden;},null,{timeout:180000});
        let editor=page.frameLocator('#sw-report-editor');
        await editor.locator('#edit-page option').first().waitFor({state:'attached',timeout:120000});
        await editor.locator('#slide-image').evaluate(img => new Promise((resolve,reject)=>{if(img.complete&&img.naturalWidth)resolve();else{img.addEventListener('load',resolve,{once:true});img.addEventListener('error',reject,{once:true});}}));
        const originalUrl=await page.locator('#sw-report-editor').getAttribute('src');
        assert.equal(await editor.locator('#send').isVisible(),false);
        await page.screenshot({path:path.join(output,kind+'-report-edit.png'),fullPage:true});
        await editor.getByLabel('보고서 제목',{exact:true}).fill('검토 완료 '+kind.toUpperCase());
        await page.waitForFunction(()=>document.getElementById('sw-next').disabled&&document.getElementById('sw-guide-detail').textContent.includes('자동 저장 중'));
        await page.waitForFunction(()=>{const f=document.getElementById('sw-report-editor');return f.contentDocument.getElementById('status').textContent.includes('자동 저장 완료');},null,{timeout:180000});
        await page.waitForFunction(()=>!document.getElementById('sw-next').disabled&&document.getElementById('sw-next').classList.contains('sw-primary'));
        async function pagesSaved() {
          await page.waitForFunction(()=>{const d=document.getElementById('sw-report-editor').contentDocument;
            return d.getElementById('status').classList.contains('error')||d.getElementById('status').textContent.includes('자동 저장 완료')&&!d.getElementById('page-add').disabled;},null,{timeout:180000});
          assert(!(await editor.locator('#status').getAttribute('class')).includes('error'),await editor.locator('#status').textContent());
        }
        await pagesSaved();
        const originalPageCount=await editor.locator('#edit-page option').count();
        await editor.locator('#edit-page').selectOption('1'); // Analysis trace, not either saved graph.
        const removedTitle=await editor.locator('#edit-page option:checked').textContent();
        await editor.locator('#page-add').click();
        await editor.getByLabel('추가 페이지 제목',{exact:true}).fill('검토 의견 '+kind.toUpperCase());
        await editor.getByLabel('추가 페이지 본문',{exact:true}).fill('측정 조건과 피크 정보를 확인했습니다.\n추가 실험 결과와 검토 의견을 기재하는 페이지입니다.');
        await pagesSaved();
        assert.equal(await editor.locator('#edit-page option').count(),originalPageCount+1);
        assert.equal(await editor.locator('#edit-page').inputValue(),'2');
        await editor.getByLabel('PPT 편집 · 추가 페이지 본문',{exact:true}).click();
        await editor.getByLabel('PPT 수정 · 추가 페이지 본문',{exact:true}).fill('PPT에서 직접 수정한 검토 의견입니다.\n실험 조건과 분석 결과를 함께 확인하세요.');
        await pagesSaved();await editor.locator('#inline-close').click();
        await editor.locator('#edit-toggle').click();
        await editor.locator('#slide-image').screenshot({path:path.join(output,kind+'-added-page.png')});
        await editor.locator('#edit-toggle').click();
        await editor.locator('#edit-previous').click();await editor.locator('#page-delete').click();await pagesSaved();
        assert.equal(await editor.locator('#edit-page option').count(),originalPageCount);
        assert(!(await editor.locator('#edit-page').textContent()).includes(removedTitle.split('. ').slice(1).join('. ')));
        await editor.locator('#page-restore').click();await pagesSaved();
        assert.equal(await editor.locator('#edit-page option').count(),originalPageCount+1);
        await editor.locator('#page-delete').click();await pagesSaved();
        await editor.locator('#canvas-toggle').click();
        console.log(kind+': free object editor enabled');
        assert.equal(await editor.locator('#canvas-tools button svg').count(),12);
        assert.equal(await editor.locator('#canvas-tools button').evaluateAll(buttons=>buttons.every(b=>!b.textContent.trim()&&b.title&&b.getAttribute('aria-label')===b.title)),true);
        await editor.getByRole('button',{name:'글자 삽입',exact:true}).click();
        await editor.getByLabel('개체 내용',{exact:true}).fill('자유 편집으로 추가한 검토 의견');
        await editor.getByLabel('개체 글자 크기 (pt)',{exact:true}).fill('24');
        await editor.getByLabel('개체 글자 크기 (pt)',{exact:true}).dispatchEvent('change');
        await pagesSaved();
        // Drag an existing native object, then resize by its handle.
        let object=editor.locator('.canvas-object.selected');await object.scrollIntoViewIfNeeded();
        let rect=await object.boundingBox();await page.mouse.move(rect.x+20,rect.y+20);await page.mouse.down();await page.mouse.move(rect.x+55,rect.y+45,{steps:5});await page.mouse.up();await pagesSaved();
        const priorWidth=Number(await editor.getByLabel('개체 너비 (%)',{exact:true}).inputValue());
        await editor.locator('.canvas-resize').scrollIntoViewIfNeeded();
        let canvasHandle=await editor.locator('.canvas-resize').boundingBox();
        await page.mouse.move(canvasHandle.x+10,canvasHandle.y+10);await page.mouse.down();await page.mouse.move(canvasHandle.x+70,canvasHandle.y+30,{steps:5});await page.mouse.up();await pagesSaved();
        assert(Number(await editor.getByLabel('개체 너비 (%)',{exact:true}).inputValue())>priorWidth);
        async function geometry(x,y,w,h){
          for(const [label,value] of [['너비 (%)',w],['높이 (%)',h],['왼쪽 (%)',x],['위 (%)',y]]){
            const input=editor.getByLabel('개체 '+label,{exact:true});await input.fill(String(value));await input.dispatchEvent('change');
          }
        }
        await geometry(10,55,70,12);await pagesSaved();
        await editor.getByRole('button',{name:'사각형 삽입',exact:true}).click();
        await editor.getByLabel('개체 채우기 색 (none: 없음)',{exact:true}).fill('#DAEAFE');
        await editor.getByLabel('개체 채우기 색 (none: 없음)',{exact:true}).dispatchEvent('change');
        await geometry(8,50,84,22);
        await editor.getByRole('button',{name:'맨 뒤로',exact:true}).click();
        await pagesSaved();
        await editor.getByRole('button',{name:'선 삽입',exact:true}).click();
        await editor.getByLabel('개체 선 종류',{exact:true}).selectOption('dashDot');
        await editor.getByLabel('개체 선 굵기 (pt)',{exact:true}).fill('3');await editor.getByLabel('개체 선 굵기 (pt)',{exact:true}).dispatchEvent('change');
        await geometry(8,77,84,.2);
        await pagesSaved();
        await editor.locator('#canvas-file').setInputFiles({name:'test.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAHgAAAA8CAIAAAAiz+n/AAAAnElEQVR4nO3SQQ0AMAwDsbb8OW8s/DoTiHTKvolwZCWFVno0Umik0EihkUIjhUYKjRQaKTRSaKTQSKGRQiOFRgqNFBopNFJopNBIoZFCI4VGCo0UGik0Umik0EihkUIjhUYKjRQaKTRSaKTQSKGRQiOFRgqNFBopNFJopNBIoZFCI4VGCo0UGik0Umik0EihkUIjhUYKjRQaKfQYH3WpAXcqY4kYAAAAAElFTkSuQmCC','base64')});
        await pagesSaved();
        await editor.getByRole('button',{name:'개체 실행 취소',exact:true}).click();await pagesSaved();
        await editor.getByRole('button',{name:'개체 다시 실행',exact:true}).click();await pagesSaved();
        await editor.getByLabel('페이지 개체 선택',{exact:true}).selectOption({label:await editor.getByLabel('페이지 개체 선택',{exact:true}).locator('option').last().textContent()});
        await geometry(70,20,20,17.8);await pagesSaved();
        const freeState=await page.request.get(origin+(await page.locator('#sw-report-editor').evaluate(f=>f.contentWindow.location.pathname)).replace(/\/edit$/,'/editor-data'));
        assert(Object.values((await freeState.json()).document.canvasEdits).flat().some(v=>v.kind==='image'));
        await editor.locator('.preview-pane').screenshot({path:path.join(output,kind+'-canvas-controls.png')});
        await editor.locator('#canvas-toggle').click();
        await editor.locator('#slide-image').screenshot({path:path.join(output,kind+'-canvas.png')});
        // Real HTML drag-and-drop changes the saved page order, not just thumbnails.
        const added=editor.locator('.page-thumb[data-page-id^="added-"]').first();
        const addedId=await added.getAttribute('data-page-id');
        await added.scrollIntoViewIfNeeded();
        await added.dragTo(editor.locator('.page-thumb').first(),{targetPosition:{x:35,y:8}});
        await pagesSaved();
        assert.equal(await editor.locator('.page-thumb').first().getAttribute('data-page-id'),addedId);
        assert.equal(await editor.locator('.page-thumb[aria-current="page"]').getAttribute('data-page-id'),addedId);
        await editor.locator('#page-down').click();await pagesSaved();
        await editor.locator('.page-thumb[aria-current="page"]').focus();
        await page.keyboard.press('Alt+ArrowUp');await pagesSaved();
        const orderedIds=await editor.locator('.page-thumb').evaluateAll(buttons=>buttons.map(b=>b.dataset.pageId));
        assert.equal(orderedIds[0],addedId);
        const editorDataUrl=origin+(await page.locator('#sw-report-editor').evaluate(f=>f.contentWindow.location.pathname)).replace(/\/edit$/,'/editor-data');
        const orderedDocument=(await (await page.request.get(editorDataUrl)).json()).document;
        assert.deepEqual(orderedDocument.pageEdits.order.filter(id=>orderedIds.includes(id)),orderedIds);
        await editor.locator('#slide-image').evaluate(img=>new Promise(resolve=>{if(img.complete&&img.naturalWidth)resolve();else img.addEventListener('load',resolve,{once:true});}));
        await editor.locator('.preview-pane').screenshot({path:path.join(output,kind+'-page-sidebar.png')});
        const finalPageCount=await editor.locator('#edit-page option').count();
        const revisedUrl=await page.locator('#sw-report-editor').evaluate(f=>f.contentWindow.location.pathname);
        assert.notEqual(revisedUrl,originalUrl.split('?')[0]);
        await page.locator('#sw-step-2').click();
        assert.equal(await page.locator('#sw-report-editor').evaluate(f=>f.contentWindow.location.pathname),revisedUrl);
        await page.locator('#sw-step-4').click();
        await editor.locator('#send').waitFor({state:'visible'});
        assert.equal(await editor.locator('#send').isVisible(),true);
        const download=await editor.locator('#download').getAttribute('href');
        const response=await page.request.get(origin+download); assert.equal(response.status(),200);
        fs.writeFileSync(path.join(output,kind+'-reviewed.zip'),await response.body());
        console.log(execFileSync(process.env.RIST_TEST_PYTHON||'python3',['-c',
          `import io,sys,zipfile,xml.etree.ElementTree as E
from PIL import Image
import pdfplumber
z=zipfile.ZipFile(sys.argv[1]); ppt=zipfile.ZipFile(io.BytesIO(z.read('report.pptx')))
ns={'p':'http://schemas.openxmlformats.org/presentationml/2006/main','a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
sizes={}; found=[]
for name in ppt.namelist():
 if name.startswith('ppt/media/image'):
  sizes[name.split('/')[-1]]=Image.open(io.BytesIO(ppt.read(name))).size
assert sorted(sizes.values())==[(1800,2400),(2200,1640)], sizes
for name in ppt.namelist():
 if name.startswith('ppt/slides/slide') and name.endswith('.xml'):
  for pic in E.fromstring(ppt.read(name)).findall('.//p:pic',ns):
   image=pic.find('p:nvPicPr/p:cNvPr',ns).get('name')
   if image not in sizes: continue
   ext=pic.find('p:spPr/a:xfrm/a:ext',ns); w,h=sizes[image]
   assert abs(int(ext.get('cx'))/int(ext.get('cy'))-w/h)<1e-6
   found.append(image)
assert len(found)==2, found
assert any(n.startswith('ppt/media/canvas-') for n in ppt.namelist())
assert any(b'prst="line"' in ppt.read(n) and b'val="dashDot"' in ppt.read(n) for n in ppt.namelist() if n.startswith('ppt/slides/slide') and n.endswith('.xml'))
assert z.read('report.pdf')==z.read('report-from-pptx.pdf')
with pdfplumber.open(io.BytesIO(z.read('report.pdf'))) as pdf:
 assert len(pdf.pages)==int(sys.argv[3])
 texts=[p.extract_text() or '' for p in pdf.pages]
 assert any('직접 수정한 검토' in text for text in texts), texts
 assert '직접 수정한 검토' in texts[0], texts[0]
assert '직접 수정한 검토' in ''.join(E.fromstring(ppt.read('ppt/slides/slide1.xml')).itertext())
assert len([n for n in ppt.namelist() if n.startswith('ppt/slides/slide') and n.endswith('.xml')])==int(sys.argv[3])
z.extract('report.pdf',sys.argv[2])
print('Actual report: graph sizes, page add/delete/restore, direct text editing, matching PPT/PDF page count verified')`,
          path.join(output,kind+'-reviewed.zip'),path.join(output,kind),String(finalPageCount)],{encoding:'utf8'}).trim());
        if(kind==='ftir'){
          execFileSync(process.env.RIST_TEST_PYTHON||'python3',['-c',
            'import sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); [z.extract(n,sys.argv[2]) for n in ("ftir-raw-all.html","ftir-edited.html")]',
            path.join(output,kind+'-reviewed.zip'),output]);
          const offline=await browser.newContext({viewport:{width:1440,height:1050},offline:true});
          try{
            const rawView=await offline.newPage(),requests=[],offlineErrors=[];
            rawView.on('request',r=>{if(/^https?:/.test(r.url()))requests.push(r.url());});
            rawView.on('pageerror',e=>offlineErrors.push(e.message));
            await rawView.goto(pathToFileURL(path.join(output,'ftir-raw-all.html')).href);
            await rawView.waitForFunction(()=>document.getElementById('report-graph').data?.length===3);
            const original=await rawView.evaluate(()=>document.getElementById('report-graph').data.map(t=>({name:t.name,x:t.x,y:t.y})));
            assert.deepEqual(original.map(t=>t.name),['second','workflow','hidden']);
            const expected=new Map(raw.toString().split('\n').map(line=>line.split(',').map(Number)));
            for(const trace of original){
              assert.equal(trace.x.length,241);assert.equal(Math.min(...trace.x),400);assert.equal(Math.max(...trace.x),4000);
              trace.x.forEach((x,i)=>assert(Math.abs(trace.y[i]-expected.get(x))<1e-12));
            }
            assert.equal(await rawView.locator('#slides-panel').isVisible(),false);
            await rawView.locator('#view-mode').selectOption('stack');
            await rawView.waitForFunction(()=>document.getElementById('report-graph').layout.yaxis.showticklabels===false);
            await rawView.locator('#reset-view').click();
            await rawView.waitForFunction(()=>document.getElementById('report-graph').layout.yaxis.showticklabels===true);
            await rawView.screenshot({path:path.join(output,'ftir-raw-offline.png'),fullPage:true});
            await rawView.goto(pathToFileURL(path.join(output,'ftir-edited.html')).href);
            await rawView.waitForFunction(()=>document.getElementById('report-graph').data?.length>0);
            assert.equal(await rawView.locator('#graph-choice option').count(),2);
            await rawView.locator('#graph-choice').selectOption('1');
            await rawView.waitForFunction(()=>document.getElementById('report-graph').layout.title.text==='첫 번째 그래프');
            const edited=await rawView.evaluate(()=>{const g=document.getElementById('report-graph');return {names:g.data.map(t=>t.name),range:g.layout.xaxis.range,notes:(g.layout.annotations||[]).map(a=>a.text)};});
            assert.equal(await rawView.evaluate(()=>document.getElementById('report-graph').layout.width/document.getElementById('report-graph').layout.height),1100/820);
            assert(!edited.names.some(n=>n.includes('hidden')));assert.deepEqual(edited.range,[2200,1100]);
            assert(edited.notes.includes('다시 편집한 메모'));
            const bounds=await rawView.locator('#report-graph .nsewdrag').boundingBox();
            await rawView.mouse.move(bounds.x+bounds.width*.5,bounds.y+bounds.height*.5);await rawView.mouse.wheel(0,-120);
            await rawView.waitForFunction(()=>document.getElementById('report-graph').layout.xaxis.range[0]!==2200);
            await rawView.screenshot({path:path.join(output,'ftir-edited-offline.png'),fullPage:true});
            assert.deepEqual(requests,[]);assert.deepEqual(offlineErrors,[]);
            console.log('FTIR ZIP: full unedited source union, removed sample, full range, edited graph choices/notes/crop, offline stack and wheel zoom passed');
          }finally{await offline.close();}
        }
        const viewer=await browser.newPage(); await viewer.goto(origin+download.replace('/download','/preview'));
        await viewer.locator('#graph-tab').click();
        assert.equal(await viewer.locator('#graph-choice option').count(),2);
        await viewer.locator('#graph-choice').selectOption('1');
        await viewer.waitForFunction(()=>document.getElementById('report-graph').data&&document.getElementById('report-graph').layout.title.text==='첫 번째 그래프');
        assert.equal(await viewer.evaluate(()=>document.getElementById('report-graph').layout.width/document.getElementById('report-graph').layout.height),1100/820);
        await viewer.screenshot({path:path.join(output,kind+'-recipient.png'),fullPage:true}); await viewer.close();
        await page.reload();
        await page.waitForFunction(()=>document.getElementById('spectrum-workflow')?.dataset.step==='4'&&!document.getElementById('sw-step-2').disabled);
        assert.equal(await page.locator('#sw-report-editor').getAttribute('src'),revisedUrl+'?workflow=1');
        await editor.locator('.page-thumb').first().waitFor();
        assert.deepEqual(await editor.locator('.page-thumb').evaluateAll(buttons=>buttons.map(b=>b.dataset.pageId)),orderedIds);
        for(const width of [390,320]){
          await page.setViewportSize({width,height:844});
          // Reload the iframe so a narrow device starts with the drawer closed.
          await page.waitForFunction(width=>document.getElementById('sw-report-editor').contentWindow.innerWidth<=width,width);
          await page.locator('#sw-report-editor').evaluate(f=>new Promise(resolve=>{
            f.addEventListener('load',()=>resolve(),{once:true});f.contentWindow.location.reload();
          }));
          await editor.locator('#page-sidebar-toggle').waitFor();
          assert.equal(await editor.locator('#page-sidebar').isVisible(),false);
          await editor.locator('#page-sidebar-toggle').click();
          assert.equal(await editor.locator('#page-sidebar').isVisible(),true);
          await page.screenshot({path:path.join(output,kind+'-page-sidebar-'+width+'.png')});
          await editor.locator('#page-sidebar-close').click();
          assert.equal(await editor.locator('#page-sidebar').isVisible(),false);
          assert.equal(await page.locator('#sw-report-editor').evaluate(f=>f.contentDocument.documentElement.scrollWidth<=f.contentWindow.innerWidth+1),true);
        }
        await page.locator('#sw-step-2').click(); assert.equal(await page.locator('.sw-card').count(),2);
        await page.setViewportSize({width:390,height:844});
        await page.waitForTimeout(500);
        await page.evaluate(()=>window.scrollTo(0,0));
        await page.screenshot({path:path.join(output,kind+'-mobile.png')});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth <= window.innerWidth+1),true);
        await page.locator('#sw-step-4').click();
        assert(await page.locator('#'+kind+'-clear').isVisible());
        await page.locator('#'+kind+'-clear').click();
        await page.waitForFunction(()=>document.getElementById('spectrum-workflow')?.dataset.step==='1'&&!document.getElementById('sw-step-2').disabled);
        assert.equal(await page.locator('.sw-card').count(),0);
        assert.equal(await page.locator('#sw-report-editor').getAttribute('src'),null);
        assert.equal((await page.request.get(origin+download)).status(),200); // Published report is not deleted.
        await page.reload();
        await page.waitForFunction(()=>document.getElementById('spectrum-workflow')?.dataset.step==='1'&&!document.getElementById('sw-step-2').disabled);
        assert.equal(await page.locator('.sw-card').count(),0);
        assert(await page.locator('#sw-upload').isVisible());
        assert.deepEqual(errors,[]);
        console.log(kind+': upload, two graphs, preview, restore/re-edit, selection/order, real PPT/PDF, revision, back navigation, recipient graph switching, reload, mobile passed');
      } catch(error) { await page.screenshot({path:path.join(output,kind+'-failure.png'),fullPage:true}); console.error(logs.slice(-8000)); throw error; }
      finally { await page.close(); }
    }
    console.log(JSON.stringify({browser:await browser.version(),output}));
  } finally { await browser.close(); server.kill('SIGTERM'); }
})().catch(error=>{console.error(error);console.error('Artifacts:',output);server.kill('SIGTERM');process.exitCode=1;});

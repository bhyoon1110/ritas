// Real local RAW/report/revision pipeline and native DnD; not Windows/Supermium hardware certification.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn,execFileSync}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-report-graphs-'));
const python=process.env.RIST_TEST_PYTHON||'python3';
const server=spawn(python,['-u',path.join(__dirname,'spectrum_workflow_fixture.py'),output],{env:process.env});
let logs='';server.stderr.on('data',data=>logs+=data);
const port=new Promise((resolve,reject)=>{let data='';server.stdout.on('data',chunk=>{data+=chunk;if(data.includes('\n'))resolve(JSON.parse(data.split('\n')[0]).port);});server.on('exit',code=>{if(code)reject(Error(logs));});});
const raw=Buffer.from(Array.from({length:241},(_,i)=>{const x=400+i*15;return x+','+(.05+Math.exp(-Math.pow(x-1700,2)/(2*55**2)));}).join('\n'));
(async()=>{
  const origin='http://127.0.0.1:'+await port;
  const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try {
    for(const kind of ['ftir','raman']){
      const page=await browser.newPage({viewport:{width:1600,height:1200}}),errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());page.setDefaultTimeout(30000);
      page.on('console',message=>{if(message.type()==='error'&&/Content Security Policy|violates.*policy/i.test(message.text()))errors.push(message.text());});
      await page.addInitScript(()=>window.addEventListener('message',event=>{if(event.data&&event.data.type==='rist-saved-graphs')window.__savedGraphCatalog=event.data;}));
      try{
        await page.goto(origin+'/'+kind);await page.waitForFunction(()=>document.getElementById('sw-step-2')&&!document.getElementById('sw-step-2').disabled);
        await page.locator('#'+kind+'-file-input').setInputFiles({name:'drag-graph.'+(kind==='ftir'?'dpt':'txt'),mimeType:'text/plain',buffer:raw});
        await page.locator('#sw-save-graph').waitFor({state:'visible'});await page.waitForFunction(()=>!document.getElementById('sw-save-graph').disabled);
        await page.locator('#sw-graph-name').fill('삽입 그래프 A');await page.locator('#sw-save-graph').click();await page.locator('.sw-card').waitFor();
        await page.waitForFunction(()=>!document.getElementById('sw-save-copy').disabled);
        await page.locator('#sw-graph-name').fill('삽입 그래프 B');await page.locator('#sw-save-copy').click();await page.waitForFunction(()=>document.querySelectorAll('.sw-card').length===2);
        await page.locator('#sw-next').click();const frame=page.frameLocator('#sw-report-editor');
        await frame.locator('.report-graph-insert').first().waitFor({timeout:180000});
        const ready=async()=>{
          await page.waitForFunction(()=>{const d=document.getElementById('sw-report-editor').contentDocument;return d&&d.getElementById('page-add')&&!d.getElementById('page-add').disabled&&d.getElementById('slide-image').complete;},null,{timeout:180000});
        };
        const saved=async()=>{
          await page.waitForFunction(()=>{const d=document.getElementById('sw-report-editor').contentDocument;return d.getElementById('status').classList.contains('error')||d.getElementById('status').textContent.includes('자동 저장 완료');},null,{timeout:180000});
          assert(!(await frame.locator('#status').getAttribute('class')).includes('error'),await frame.locator('#status').textContent());await ready();
        };
        const data=async()=>{
          const url=await page.locator('#sw-report-editor').evaluate(f=>f.contentWindow.location.pathname.replace(/\/edit$/,'/editor-data'));
          const response=await page.request.get(origin+url);assert.equal(response.status(),200);return (await response.json()).document;
        };
        await ready();
        await page.waitForFunction(kind=>!document.getElementById(kind+'-report-progress').classList.contains('is-visible'),kind);
        await page.waitForFunction(()=>{
          const images=document.getElementById('sw-report-editor').contentDocument.querySelectorAll('.report-graph-thumbnail img');
          return images.length===2&&Array.from(images).every(img=>img.complete&&img.naturalWidth>0&&img.naturalWidth<=320&&img.naturalHeight<=180);
        });
        const beforePreview=await data();let imageUploads=0;
        page.on('request',request=>{if(request.method()==='POST'&&/\/editor-(graphs|images)$/.test(request.url()))imageUploads++;});
        await frame.locator('.report-graph-preview').first().click();
        await page.locator('.sw-preview img').waitFor();
        assert.equal(await page.locator('.sw-preview img').getAttribute('src'),await page.locator('.sw-card img').first().getAttribute('src'));
        await page.locator('.sw-preview').screenshot({path:path.join(output,kind+'-enlarged.png')});
        await page.keyboard.press('Escape');await page.locator('.sw-preview').waitFor({state:'detached'});
        assert(await frame.locator('.report-graph-preview').first().evaluate(el=>el===el.ownerDocument.activeElement));
        assert.deepEqual(await data(),beforePreview);assert.equal(imageUploads,0);
        await frame.locator('#report-saved-graphs').screenshot({path:path.join(output,kind+'-desktop-picker.png')});
        await frame.locator('#page-add').click();await saved();
        const pageId=await frame.locator('.page-thumb[aria-current=page]').getAttribute('data-page-id');
        const original=await data();
        // Native drag inside the editor; no scripted drop handler invocation.
        await frame.locator('.report-graph-insert').first().dragTo(frame.locator('#slide-stage'),{targetPosition:{x:180,y:130}});
        await saved();let model=await data();assert.equal(model.canvasEdits[pageId].length,1);
        assert.equal(model.canvasEdits[pageId][0].kind,'image');assert.deepEqual(model.sections,original.sections);
        await frame.locator('#slide-stage').screenshot({path:path.join(output,kind+'-dragged.png')});
        // Native cross-frame drag from the existing saved thumbnail in the parent.
        // Bring both source and slide into view before holding the mouse.
        await page.setViewportSize({width:1600,height:1700});
        await frame.locator('#slide-stage').scrollIntoViewIfNeeded();await page.locator('.sw-card img').first().scrollIntoViewIfNeeded();
        const source=await page.locator('.sw-card img').first().boundingBox(),target=await frame.locator('#slide-stage').boundingBox();
        await page.mouse.move(source.x+source.width/2,source.y+source.height/2);await page.mouse.down();
        await page.mouse.move(source.x+source.width/2+15,source.y+source.height/2+10,{steps:5});
        await page.mouse.move(target.x+target.width*.7,target.y+target.height*.6,{steps:25});await page.mouse.up();
        await saved();model=await data();assert.equal(model.canvasEdits[pageId].length,2);
        // Undo/redo and manual sizing remain available after native graph drops.
        await frame.getByRole('button',{name:'개체 실행 취소',exact:true}).click();await saved();assert.equal((await data()).canvasEdits[pageId].length,1);
        await frame.getByRole('button',{name:'개체 다시 실행',exact:true}).click();await saved();assert.equal((await data()).canvasEdits[pageId].length,2);
        // Narrow/mobile fallback: no touch drag requirement and no horizontal overflow.
        await page.setViewportSize({width:390,height:844});
        await page.locator('#sw-report-editor').evaluate(el=>el.scrollIntoView({block:'start'}));
        if(await frame.locator('#page-sidebar-close').isVisible())await frame.locator('#page-sidebar-close').click();
        await frame.locator('.report-graph-preview').nth(1).click();await page.locator('.sw-preview').waitFor();
        assert.match(await page.locator('.sw-preview h3').textContent(),/삽입 그래프 B/);
        const close=page.locator('.sw-preview').getByRole('button',{name:'닫기',exact:true}),closeBox=await close.boundingBox();
        assert(closeBox.x>=0&&closeBox.y>=0&&closeBox.x+closeBox.width<=390&&closeBox.y+closeBox.height<=844);
        await page.locator('.sw-preview').screenshot({path:path.join(output,kind+'-mobile-enlarged.png')});
        await close.click();assert.equal((await data()).canvasEdits[pageId].length,2);
        await frame.locator('.report-graph-insert').nth(1).click();await saved();
        model=await data();assert.equal(model.canvasEdits[pageId].length,3);
        assert(await frame.locator('body').evaluate(el=>el.scrollWidth<=el.ownerDocument.documentElement.clientWidth+2));
        await frame.locator('#report-saved-graphs').screenshot({path:path.join(output,kind+'-mobile-picker.png')});
        await page.reload();await frame.locator('#edit-page option').first().waitFor({state:'attached',timeout:180000});await ready();
        assert.equal((await data()).canvasEdits[pageId].length,3);
        const download=await frame.locator('#download').getAttribute('href'),response=await page.request.get(origin+download);assert.equal(response.status(),200);
        const zip=path.join(output,kind+'-graphs.zip');fs.writeFileSync(zip,await response.body());
        console.log(execFileSync(python,['-c',`import io,sys,zipfile,xml.etree.ElementTree as E
from PIL import Image
from pypdf import PdfReader
z=zipfile.ZipFile(sys.argv[1]);p=zipfile.ZipFile(io.BytesIO(z.read('report.pptx')))
ns={'p':'http://schemas.openxmlformats.org/presentationml/2006/main','a':'http://schemas.openxmlformats.org/drawingml/2006/main','r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
found=0
for name in p.namelist():
 if not(name.startswith('ppt/slides/slide') and name.endswith('.xml')):continue
 rels=E.fromstring(p.read(name.replace('slides/','slides/_rels/')+'.rels'))
 targets={r.get('Id'):r.get('Target') for r in rels}
 for pic in E.fromstring(p.read(name)).findall('.//p:pic',ns):
  if not pic.find('p:nvPicPr/p:cNvPr',ns).get('name','').startswith('object-'):continue
  target=targets[pic.find('p:blipFill/a:blip',ns).get('{'+ns['r']+'}embed')]
  image=Image.open(io.BytesIO(p.read('ppt/'+target.removeprefix('../'))));w,h=image.size
  ext=pic.find('p:spPr/a:xfrm/a:ext',ns)
  assert abs(int(ext.get('cx'))/int(ext.get('cy'))-w/h)<1e-5
  found+=1
assert found==3,found
assert z.read('report.pdf')==z.read('report-from-pptx.pdf')
assert len(PdfReader(io.BytesIO(z.read('report.pdf'))).pages)>1
print('PPT: 3 inserted graphs, preserved aspect ratio, matching rendered PDF verified')`,zip],{encoding:'utf8'}).trim());
        assert.deepEqual(errors,[]);console.log(kind+': native drag, cross-frame drag, mobile add, undo/redo, reload, final export passed');
      }catch(e){
        console.error('Browser errors:',errors);
        console.error('Catalog:',await page.frameLocator('#sw-report-editor').locator('body').evaluate(el=>el.ownerDocument.defaultView.__savedGraphCatalog).catch(()=>null));
        console.error('Thumbnail diagnostics:',await page.evaluate(()=>{
          const img=document.querySelector('.sw-card img'),c=document.createElement('canvas');c.width=320;c.height=180;
          try{c.getContext('2d').drawImage(img,0,0,320,180);const blob=window.RistSpectrumWorkflow.imageFile(c.toDataURL('image/png')),url=URL.createObjectURL(blob);URL.revokeObjectURL(url);return {width:img.naturalWidth,height:img.naturalHeight,size:blob.size};}
          catch(error){return String(error);}
        }).catch(()=>null));
        await page.screenshot({path:path.join(output,kind+'-failure.png'),fullPage:true});throw e;
      }finally{await page.close();}
    }
  }finally{await browser.close();server.kill();console.log('Artifacts: '+output);}
})().catch(e=>{server.kill();console.error(e);console.error(logs.slice(-5000));process.exitCode=1;});

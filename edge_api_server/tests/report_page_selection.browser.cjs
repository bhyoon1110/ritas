// Real local RAW -> PPT/PDF revisions, native modifier keys and group drag.
// macOS Chrome validation, not Windows/Supermium hardware certification.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-page-selection-'));
const server=spawn(process.env.RIST_TEST_PYTHON||'python3',['-u',path.join(__dirname,'spectrum_workflow_fixture.py'),output],{env:process.env});
let logs='';server.stderr.on('data',data=>logs+=data);
const port=new Promise((resolve,reject)=>{let text='';server.stdout.on('data',chunk=>{text+=chunk;if(text.includes('\n'))resolve(JSON.parse(text.split('\n')[0]).port);});server.on('exit',code=>{if(code)reject(Error(logs));});});
(async()=>{
  let browser;
  try{
    const origin='http://127.0.0.1:'+await port;
    browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
    for(const kind of ['ftir','raman']){
      const page=await browser.newPage({viewport:{width:1600,height:1100}}),errors=[];
      page.setDefaultTimeout(30000);page.on('pageerror',e=>errors.push(e.message));page.on('dialog',dialog=>dialog.accept());
      try{
        const raw=Buffer.from(Array.from({length:241},(_,i)=>{const x=400+i*15;return x+','+(.05+Math.exp(-Math.pow(x-1700,2)/(2*55**2)));}).join('\n'));
        await page.goto(origin+'/'+kind);
        await page.waitForFunction(()=>document.getElementById('sw-step-2')&&!document.getElementById('sw-step-2').disabled);
        await page.locator('#'+kind+'-file-input').setInputFiles({name:'selection.'+(kind==='ftir'?'dpt':'txt'),mimeType:'text/plain',buffer:raw});
        await page.waitForFunction(()=>document.getElementById('sw-save-graph')&&!document.getElementById('sw-save-graph').disabled);
        await page.locator('#sw-save-graph').click();await page.locator('.sw-card').waitFor();await page.locator('#sw-next').click();
        const frame=page.frameLocator('#sw-report-editor');
        const ready=()=>page.waitForFunction(()=>{
          const f=document.getElementById('sw-report-editor'),d=f&&f.contentDocument,img=d&&d.getElementById('slide-image');
          return img&&img.complete&&img.naturalWidth>0&&!d.getElementById('page-add').disabled;
        },null,{timeout:180000});
        const saved=async()=>{
          await page.waitForFunction(()=>{
            const d=document.getElementById('sw-report-editor').contentDocument;
            return d.getElementById('status').classList.contains('error')||d.getElementById('status').textContent.includes('자동 저장 완료');
          },null,{timeout:180000});
          assert(!(await frame.locator('#status').getAttribute('class')).includes('error'),await frame.locator('#status').textContent());await ready();
        };
        const ids=()=>frame.locator('.page-thumb').evaluateAll(nodes=>nodes.map(n=>n.dataset.pageId));
        const selected=()=>frame.locator('.page-thumb[aria-pressed="true"]').evaluateAll(nodes=>nodes.map(n=>n.dataset.pageId));
        const thumb=id=>frame.locator('.page-thumb[data-page-id="'+id+'"]');
        const data=async()=>{
          const endpoint=await page.locator('#sw-report-editor').evaluate(f=>f.contentWindow.location.pathname.replace(/\/edit$/,'/editor-data'));
          const response=await page.request.get(origin+endpoint);assert.equal(response.status(),200);return (await response.json()).document;
        };
        await ready();
        await page.waitForFunction(kind=>!document.getElementById(kind+'-report-progress').classList.contains('is-visible'),kind);
        const toggle=frame.locator('#canvas-toggle'),panelToggle=frame.locator('#page-sidebar-toggle'),tools=frame.locator('#canvas-controls');
        assert(await toggle.evaluate(el=>el.previousElementSibling.id==='page-sidebar-toggle'&&!document.getElementById('canvas-tools').contains(el)));
        const adjacent=async()=>{const a=await panelToggle.boundingBox(),b=await toggle.boundingBox();assert(a&&b&&Math.abs(a.y-b.y)<2&&b.x>=a.x+a.width&&b.x-a.x-a.width<=12);};
        await adjacent();await toggle.click();assert(await tools.isVisible());
        assert.equal(await frame.locator('#canvas-tools button').count(),11);
        await panelToggle.click();assert(!await frame.locator('#page-sidebar').isVisible());assert(await tools.isVisible());
        await toggle.click();assert(!await tools.isVisible());assert(await toggle.isVisible());
        await panelToggle.click();assert(await frame.locator('#page-sidebar').isVisible());
        await frame.locator('#preview-panel-toggles').screenshot({path:path.join(output,kind+'-toggles.png')});
        const original=await ids();assert(original.length>=3);let revisions=0;
        page.on('request',request=>{if(request.method()==='POST'&&request.url().endsWith('/revisions'))revisions++;});
        // On macOS, native Control+click is a context menu; use Cmd for native clicks.
        // Windows-style Ctrl+click is covered by native/Chrome49 DOM tests.
        await thumb(original[0]).click();await thumb(original[original.length-1]).click({modifiers:['Meta']});
        assert.deepEqual(await selected(),[original[0],original[original.length-1]]);
        await thumb(original[1]).click({modifiers:['Shift']});assert.deepEqual(await selected(),original.slice(1));
        await thumb(original[1]).click({modifiers:['Meta']});assert.deepEqual(await selected(),original.slice(2));
        await thumb(original[0]).click();await thumb(original[1]).click({modifiers:['Shift']});
        assert.deepEqual(await selected(),original.slice(0,2));assert.equal(revisions,0);
        await frame.locator('#page-sidebar').screenshot({path:path.join(output,kind+'-range.png')});
        const title=frame.getByLabel('보고서 제목',{exact:true});await title.focus();await page.keyboard.press('Meta+a');
        assert(await title.evaluate(el=>el.selectionStart===0&&el.selectionEnd===el.value.length));assert.deepEqual(await selected(),original.slice(0,2));
        await title.fill(kind+' 다중 선택 유지');await saved();assert.deepEqual(await selected(),original.slice(0,2));
        await frame.locator('#page-down').click();await saved();
        assert.deepEqual(await ids(),[original[2],...original.slice(0,2),...original.slice(3)]);assert.deepEqual(await selected(),original.slice(0,2));
        // Drag either selected thumbnail: move both, keeping their relative order.
        await thumb(original[0]).dragTo(thumb(original[2]),{targetPosition:{x:40,y:8}});await saved();
        assert.deepEqual(await ids(),original);assert.deepEqual(await selected(),original.slice(0,2));
        await thumb(original[0]).focus();await page.keyboard.press('Control+a');
        assert.deepEqual(await selected(),original);assert(await frame.locator('#page-delete').isDisabled());
        await thumb(original[0]).click();await thumb(original[1]).click({modifiers:['Shift']});
        await frame.locator('#page-delete').click();await saved();assert.deepEqual(await ids(),original.slice(2));
        assert.deepEqual((await data()).pageEdits.hidden,original.slice(0,2));
        await frame.locator('#page-restore').click();await saved();assert.deepEqual(await ids(),original);assert.deepEqual(await selected(),original.slice(0,2));
        assert.deepEqual((await data()).pageEdits.hidden,[]);
        // Text fields in the direct PPT editor retain native Cmd+A too.
        await thumb(original[0]).click();await ready();
        await frame.getByRole('button',{name:'PPT 편집 · 보고서 제목',exact:true}).click();
        const inline=frame.getByLabel('PPT 수정 · 보고서 제목',{exact:true});await inline.focus();await page.keyboard.press('Meta+a');
        assert(await inline.evaluate(el=>el.selectionStart===0&&el.selectionEnd===el.value.length));assert.deepEqual(await selected(),[original[0]]);
        await frame.locator('#inline-close').click();
        await page.setViewportSize({width:390,height:844});
        await adjacent();
        if(!await frame.locator('#page-sidebar').isVisible())await frame.locator('#page-sidebar-toggle').click();
        await thumb(original[1]).click({modifiers:['Meta']});assert.deepEqual(await selected(),original.slice(0,2));
        assert(await frame.locator('#page-sidebar').evaluate(el=>el.scrollWidth<=el.clientWidth+2));
        await page.locator('#sw-report-editor').evaluate(el=>el.scrollIntoView({block:'start'}));
        await frame.locator('#page-sidebar').screenshot({path:path.join(output,kind+'-narrow.png')});
        assert.deepEqual(errors,[]);console.log(JSON.stringify({kind,pages:original.length,revisions,selection:'range/toggle/all',group:'move/drag/delete/restore',textSelectAll:'preserved'}));
      }catch(error){await page.screenshot({path:path.join(output,kind+'-failure.png'),fullPage:true});throw error;}
      finally{await page.close();}
    }
    console.log(JSON.stringify({browser:browser.version(),output}));
  }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error,logs.slice(-5000),output);process.exitCode=1;});

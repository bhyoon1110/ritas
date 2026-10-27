// Mac Chromium UI regression, including the legacy bundle; not XP certification.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const resizeImage=require('./canvas_resize_steps.cjs');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-xrd-editor-'));
const server=spawn(process.env.RIST_TEST_PYTHON||'python3',['-u',path.join(__dirname,'xrd_ppt_editor_fixture.py'),output],{env:process.env});
let logs='';server.stderr.on('data',data=>logs+=data);
const startup=new Promise((resolve,reject)=>{let data='';server.stdout.on('data',chunk=>{data+=chunk;if(data.includes('\n'))resolve(JSON.parse(data.split('\n')[0]));});server.on('exit',code=>reject(Error('fixture exited '+code+' '+logs)));});
(async()=>{
  const start=await startup,origin='http://127.0.0.1:'+start.port,base='/api/v1/xrd/workbench';
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_BIN||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try {
    for(const legacy of [false,true]) {
      const page=await browser.newPage({viewport:{width:1600,height:1100},...(legacy?{userAgent:'Mozilla/5.0 Chrome/49.0.2623.75 Safari/537.36'}:{})});
      const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());page.setDefaultTimeout(30000);
      let latest;
      page.on('response',async r=>{if(r.url().endsWith('/draft-preview')&&r.ok())latest=await r.json();});
      const ready=()=>page.waitForFunction(()=>document.querySelector('#page-add')&&!document.querySelector('#page-add').disabled&&document.querySelector('#slide-image').naturalWidth>0);
      const updated=async action=>{const response=page.waitForResponse(r=>r.url().endsWith('/draft-preview')&&r.ok());await action();await response;await ready();};
      const get=async()=>{const r=await page.request.get(origin+base+'/api/reports/'+start.sid);assert.equal(r.status(),200);return (await r.json()).document;};
      try {
        await page.goto(origin+base+'/report.html?project='+start.sid);await ready();
        if(legacy)assert.equal(await page.locator('script[src$="report.legacy.js"]').count(),1);
        await page.locator('#previewPane').screenshot({path:path.join(output,legacy?'legacy-start.png':'desktop-start.png')});
        const initial=latest.pages.length;
        const beforeIds=latest.pages.map(p=>p.id);
        await updated(()=>page.locator('#page-add').click());assert.equal(latest.pages.length,initial+1);
        const added=latest.pages.find(p=>!beforeIds.includes(p.id)).id;
        await page.locator('#canvas-toggle').click();
        await page.getByRole('button',{name:'글자 삽입',exact:true}).click();
        await updated(async()=>{await page.getByLabel('개체 내용',{exact:true}).fill('QA '+legacy);await page.getByLabel('개체 내용',{exact:true}).blur();});
        await updated(()=>page.getByRole('button',{name:'개체 실행 취소',exact:true}).click());
        await updated(()=>page.getByRole('button',{name:'개체 다시 실행',exact:true}).click());
        await page.getByRole('button',{name:'선 삽입',exact:true}).click();
        await updated(()=>page.getByLabel('개체 선 종류',{exact:true}).selectOption('dash'));
        await updated(()=>page.locator('#canvas-file').setInputFiles(path.join(output,'image.png')));
        const imageId=latest.pages.find(p=>p.id===added).objects.find(o=>o.kind==='image').id;
        await resizeImage({page,editor:page,objectId:imageId,commit:updated,read:async()=>{
          const o=latest.pages.find(p=>p.id===added).objects.find(o=>o.id===imageId);
          return {id:o.id,x:o.x,y:o.y,w:o.w,h:o.h};
        }});
        const tablePage=latest.pages.find(p=>p.objects.some(o=>o.kind==='table'));
        const table=tablePage.objects.find(o=>o.kind==='table');
        await page.locator('#ppt-page').selectOption(String(latest.pages.indexOf(tablePage)));
        await page.getByLabel('페이지 개체 선택',{exact:true}).selectOption(table.id);
        await page.locator('[data-object-id="'+table.id+'"] .canvas-cell[data-row="1"][data-col="1"]').click();
        assert(await page.locator('.ppt-cell-input').evaluate(e=>e===document.activeElement));
        await updated(async()=>{await page.locator('.ppt-cell-input').fill('Reviewed '+legacy);await page.locator('.ppt-cell-input').press('Enter');});
        await page.locator('#slide-stage').screenshot({path:path.join(output,legacy?'legacy-table.png':'table.png')});
        // Shift range selection, group reorder, delete and restore.
        await page.locator('.page-thumb').nth(0).click();await page.locator('.page-thumb').nth(1).click({modifiers:['Shift']});
        assert.equal(await page.locator('.page-thumb[aria-pressed="true"]').count(),2);
        await updated(()=>page.locator('#page-down').click());
        await updated(()=>page.locator('#page-delete').click());assert.equal(latest.pages.length,initial-1);
        await updated(()=>page.locator('#page-restore').click());assert.equal(latest.pages.length,initial+1);
        await page.locator('.page-thumb').nth(0).click();await page.locator('.page-thumb').nth(2).click({modifiers:[process.platform==='darwin'?'Meta':'Control']});
        assert.equal(await page.locator('.page-thumb[aria-pressed="true"]').count(),2);
        await page.locator('.page-thumb').nth(2).press('Control+a');
        assert.equal(await page.locator('.page-thumb[aria-pressed="true"]').count(),initial+1);
        assert(await page.locator('#page-delete').isDisabled());
        const save=page.waitForResponse(r=>r.url().endsWith('/api/reports/'+start.sid)&&r.request().method()==='PUT');
        await page.locator('#save').click();assert.equal((await save).status(),200);await ready();
        const saved=await get();assert(saved.pageEdits.added.some(p=>p.id===added));assert(saved.canvasEdits[added].some(o=>o.text==='QA '+legacy));
        await page.reload();await ready();assert.equal(latest.pages.length,initial+1);
        await page.setViewportSize({width:390,height:844});
        await page.locator('[data-work-view="preview"]').click();
        if(await page.locator('#page-sidebar-close').isVisible())await page.locator('#page-sidebar-close').click();
        await page.locator('#canvas-toggle').click();
        assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
        await page.screenshot({path:path.join(output,legacy?'mobile-legacy.png':'mobile.png'),fullPage:true});
        assert.deepEqual(errors,[]);
        console.log((legacy?'Legacy bundle':'Modern bundle')+': page selection/order/add/delete/restore, text/line/image/table, undo/redo, save/reload, mobile passed');
      } catch(e){await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});throw e;}
      finally{await page.close();}
    }
  } finally{await browser.close();server.kill();console.log('Artifacts: '+output);}
})().catch(e=>{server.kill();console.error(e);console.error(logs.slice(-4000));process.exitCode=1;});

// Actual report documents and compiled Chrome49 bundle in Mac Chrome.
// No production reports, external services or mail delivery; not Windows hardware verification.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-report-split-')),servers=[];
async function fixture(name){
  const root=path.join(output,name);fs.mkdirSync(root);
  const server=spawn(process.env.RIST_TEST_PYTHON||'python3',['-u',path.join(__dirname,name+'.py'),root],{env:process.env});servers.push(server);
  let logs='';server.stderr.on('data',d=>logs+=d);
  return new Promise((resolve,reject)=>{let data='';server.stdout.on('data',d=>{data+=d;if(data.includes('\n'))resolve({...JSON.parse(data.split('\n')[0]),logs:()=>logs});});server.on('exit',code=>reject(Error(name+' exit '+code+' '+logs)));});
}
(async()=>{
  const common=await fixture('spectrum_workflow_fixture'),xrd=await fixture('xrd_ppt_editor_fixture');
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_BIN||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try{
    for(const kind of (process.env.RIST_SPLIT_KINDS||'ftir,raman,tem,xrd').split(','))for(const legacy of [false,true]){
      const name=kind+(legacy?'-chrome49':'-supermium144-policy'),errors=[],posts=[];
      const ua='Mozilla/5.0 (Windows NT 5.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/'+(legacy?'49.0.2623.75':'144.0.7559.256')+' Safari/537.36'+(legacy?'':' Supermium/144.0');
      const context=await browser.newContext({viewport:{width:1440,height:1000},userAgent:ua}),page=await context.newPage();page.setDefaultTimeout(30000);page.on('pageerror',e=>errors.push(e.message));
      page.on('dialog',d=>d.type()==='beforeunload'?d.accept():d.dismiss());
      page.on('request',r=>{if(r.method()==='POST'&&!r.url().endsWith('/draft-preview'))posts.push(r.url());});
      const origin='http://127.0.0.1:'+(kind==='xrd'?xrd.port:common.port),key='rist.report.split.'+kind.toUpperCase()+'.v1';
      let editor=page,xrdSave=false;
      const left=()=>editor.locator(kind==='xrd'?(xrdSave?'#savePane':'#editor'):'.form-pane'),right=()=>editor.locator(kind==='xrd'?'#previewPane':'.preview-pane');
      const separator=()=>editor.getByRole('separator',{name:'보고서 좌우 패널 폭 조절'});
      const width=async()=>Math.round((await left().boundingBox()).width);
      const settled=()=>page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
      async function dragBy(dx){const b=await separator().boundingBox();assert(b);await page.mouse.move(b.x+b.width/2,b.y+90);await page.mouse.down();await page.mouse.move(b.x+b.width/2+dx,b.y+90,{steps:8});await page.mouse.up();await settled();}
      async function bounds(){
        const l=await left().boundingBox(),r=await right().boundingBox(),h=await separator().boundingBox();
        assert(l.width>=(kind==='xrd'?320:280)-1&&r.width>=(kind==='xrd'?420:480)-1,JSON.stringify({l,r}));
        assert(Math.abs(l.x+l.width-h.x)<2&&Math.abs(h.x+h.width-r.x)<2,JSON.stringify({l,r,h}));
        assert(Math.abs(l.y-r.y)<2,JSON.stringify({l,r}));
        if(kind!=='xrd'){
          const scroll=await editor.locator('.form-scroll').boundingBox();assert(Math.abs(scroll.y+scroll.height-r.y-r.height)<2,JSON.stringify({scroll,r}));
        }
      }
      try{
        if(kind!=='xrd'){
          await page.route('**/api/v1/**',route=>{
            const p=new URL(route.request().url()).pathname,json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
            if(p.endsWith('/editor-data'))return json({document:{title:'Panel width test',completionDate:'2026-11-23',transfer:{requestNumber:'KEEP-001',limsExperimentCode:'',equipmentCode:'LAB-001',operatorId:''},sections:[],auxiliary:{},variables:[]}});
            if(p.endsWith('/edit-layout'))return json({ratio:16/9,pages:[{id:'cover',title:'표지',regions:[]}],pageEditing:false,canvasEditing:false});
            if(p.includes('/edit-slides/'))return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aGncAAAAASUVORK5CYII=','base64')});
            assert(!p.endsWith('/send'),'must not send');return json({items:[],status:'completed'});
          });
          if(kind==='ftir'){
            await page.route('**/__split-host',route=>route.fulfill({contentType:'text/html',body:'<style>html,body{margin:0;height:100%}iframe{width:100%;height:100%;border:0}</style><iframe src="/__test__/request-editor/ftir?workflow=1"></iframe>'}));
            await page.goto(origin+'/__split-host');editor=page.frames().find(f=>f.url().includes('/request-editor/'))||page.frameLocator('iframe');
          }else await page.goto(origin+'/__test__/request-editor/'+kind);
          await editor.getByLabel('의뢰번호',{exact:true}).waitFor();
        }else{
          // Entering delivery may generate a draft; keep that local and deterministic.
          await page.route('**/email-draft',route=>route.fulfill({contentType:'application/json',body:JSON.stringify({subject:'패널 조절 시험',body:'메일 본문 초안',status:'rule',message:''})}));
          await page.goto(origin+'/api/v1/xrd/workbench/report.html?project='+xrd.sid);
          await page.waitForFunction(()=>document.querySelector('#slide-image')?.naturalWidth>0);
          if(legacy)assert.equal(await page.locator('script[src$="report.legacy.js"]').count(),1);
        }
        await separator().waitFor();await settled();const original=await width();await bounds();
        await dragBy(180);assert(Math.abs(await width()-original-180)<=2);await bounds();
        const stored=await page.evaluate(k=>localStorage.getItem(k),key);assert(Number(stored)>0);const savedWidth=await width();
        await page.reload();
        if(kind==='ftir')editor=page.frameLocator('iframe');
        if(kind==='xrd')await page.waitForFunction(()=>document.querySelector('#slide-image')?.naturalWidth>0);
        await separator().waitFor();await settled();
        assert(Math.abs(await width()-savedWidth)<=2);await bounds();
        let count=posts.length;
        await separator().press('Home');assert.equal(await width(),kind==='xrd'?320:280);await bounds();
        await separator().press('End');await bounds();
        const end=await width();await separator().press('ArrowLeft');assert(Math.abs(await width()-end+20)<=1);
        await separator().press('Shift+ArrowLeft');assert(Math.abs(await width()-end+70)<=1);
        await separator().press('Enter');assert(Math.abs(await width()-original)<=1);
        await dragBy(100);const b=await separator().boundingBox();await page.mouse.move(b.x+9,b.y+90);await page.mouse.down();await page.mouse.move(b.x+200,b.y+90);await page.keyboard.press('Escape');await page.mouse.up();await settled();assert(Math.abs(await width()-original-100)<=2);
        await separator().dblclick({position:{x:9,y:90}});assert(Math.abs(await width()-original)<=2);assert.equal(await page.evaluate(k=>localStorage.getItem(k),key),null);
        await dragBy(110);
        if(kind==='xrd'){
          const expected=await width();await page.locator('[data-work-view="preview"]').click();assert(await separator().isHidden());
          await page.locator('[data-work-view="editor"]').click();assert(await separator().isHidden());
          await page.locator('[data-work-view="both"]').click();await bounds();assert.equal(await width(),expected);
          assert.equal(posts.length,count,'report resizing must not trigger a save/send POST');
          await page.evaluate(()=>window.xrdLocalStep('save'));xrdSave=true;
          await page.locator('#savePane').waitFor();await separator().waitFor();await settled();await bounds();
          assert.equal(await width(),expected,'delivery shares the current report split');
          assert.equal(new URL(page.url()).searchParams.get('step'),'save');
          await dragBy(-90);const deliveryWidth=await width();assert(Math.abs(deliveryWidth-expected+90)<=2);await bounds();
          await page.reload();await page.locator('#savePane').waitFor();
          await page.waitForFunction(()=>document.querySelector('#slide-image')?.naturalWidth>0);
          await separator().waitFor();await settled();await bounds();
          assert.equal(new URL(page.url()).searchParams.get('step'),'save');
          assert(Math.abs(await width()-deliveryWidth)<=2,'direct delivery reload preserves the split');
          await page.waitForFunction(()=>!document.getElementById('generateEmail').disabled);
          count=posts.length; // Delivery entry/reload can legitimately request an automatic mail draft.
          const subject=page.locator('#emailSubject'),body=page.getByRole('textbox',{name:'메일 본문 서식 편집',exact:true});
          await subject.fill('폭 조절 후 유지할 제목');await body.fill('폭 조절 후에도 유지할 한글 메일 본문');
          const unchanged=async()=>{assert.equal(await subject.inputValue(),'폭 조절 후 유지할 제목');assert.equal(await body.textContent(),'폭 조절 후에도 유지할 한글 메일 본문');};
          await separator().press('ArrowRight');assert(Math.abs(await width()-deliveryWidth-20)<=1);await bounds();
          await separator().press('Home');assert.equal(await width(),320);await bounds();
          await separator().press('End');await bounds();
          await separator().press('Enter');assert(Math.abs(await width()-original)<=2);
          await dragBy(90);await separator().dblclick({position:{x:9,y:90}});
          assert(Math.abs(await width()-original)<=2);assert.equal(await page.evaluate(k=>localStorage.getItem(k),key),null);
          await dragBy(110);await unchanged();const sharedWidth=await width();
          await page.evaluate(()=>window.xrdLocalStep('report'));xrdSave=false;await settled();await bounds();assert.equal(await width(),sharedWidth);
          await separator().press('ArrowRight');const reportWidth=await width();
          await page.evaluate(()=>window.xrdLocalStep('save'));xrdSave=true;await settled();await bounds();assert.equal(await width(),reportWidth);await unchanged();
          await page.locator('[data-work-view="preview"]').click();assert(await separator().isHidden());assert(await left().isHidden());
          await page.locator('[data-work-view="editor"]').click();assert(await separator().isHidden());assert(await right().isHidden());
          await page.locator('[data-work-view="both"]').click();await settled();await bounds();assert.equal(await width(),reportWidth);await unchanged();
          await separator().press('Enter');await dragBy(110);await unchanged();
        }
        await page.screenshot({path:path.join(output,name+'.png')});
        for(const size of [1000,900,390,320]){
          await page.setViewportSize({width:size,height:1000});await settled();
          if(size>(kind==='xrd'?900:1000))continue;
          assert(await separator().isHidden());
          const overflow=await editor.locator('html').evaluate(e=>e.scrollWidth>e.ownerDocument.defaultView.innerWidth+1);assert(!overflow,'horizontal overflow at '+size);
          const l=await left().boundingBox(),r=await right().boundingBox();assert(r.y>=l.y+l.height-2,JSON.stringify({l,r,size}));
        }
        await page.setViewportSize({width:1440,height:1000});await settled();await bounds();assert(Math.abs(await width()-original-110)<=2);
        assert.equal(posts.length,count,'resizing must not mutate reports');
        if(kind!=='xrd')assert.equal(await editor.getByLabel('의뢰번호',{exact:true}).inputValue(),'KEEP-001');
        else{assert.equal(await page.locator('#emailSubject').inputValue(),'폭 조절 후 유지할 제목');assert.equal(await page.locator('.mail-rich').textContent(),'폭 조절 후에도 유지할 한글 메일 본문');}
        assert.deepEqual(errors,[]);console.log(name+': drag, clamp, reload, keyboard, cancel, reset, responsive layout, unchanged content passed');
      }catch(e){await page.screenshot({path:path.join(output,name+'-failure.png'),fullPage:true});throw e;}
      finally{await context.close();}
    }
  }finally{await browser.close();servers.forEach(s=>s.kill());console.log('Artifacts: '+output);}
})().catch(e=>{servers.forEach(s=>s.kill());console.error(e);process.exitCode=1;});

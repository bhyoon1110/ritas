// Real shared editor UI with isolated job/layout responses, no report delivery.
// Mac Chrome + compiled Chrome49 fallback, not Windows/Supermium certification.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-page-help-'));
const server=spawn(process.env.RIST_TEST_PYTHON||'python3',['-u',path.join(__dirname,'spectrum_workflow_fixture.py'),output],{env:process.env});
let logs='';server.stderr.on('data',chunk=>logs+=chunk);
const port=new Promise((resolve,reject)=>{let text='';server.stdout.on('data',chunk=>{text+=chunk;if(text.includes('\n'))resolve(JSON.parse(text.split('\n')[0]).port);});server.on('exit',code=>reject(Error('fixture exit '+code+' '+logs)));});
(async()=>{
  let browser;
  try{
    const origin='http://127.0.0.1:'+await port;
    browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
    for(const kind of ['ftir','raman','tem'])for(const legacy of [false,true]){
      const name=kind+(legacy?'-fallback':'-native'),errors=[],posts=[];
      const context=await browser.newContext({viewport:{width:1440,height:900},...(legacy?{userAgent:'Mozilla/5.0 (Windows NT 5.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/49.0.2623.75 Safari/537.36'}:{})});
      const page=await context.newPage();page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message));
      let pageEditing=true;
      await page.route('**/api/v1/**',route=>{
        const request=route.request(),p=new URL(request.url()).pathname;
        if(request.method()!=='GET')posts.push(p);
        const json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
        if(p.endsWith('/editor-data'))return json({document:{title:'보고서 편집 테스트',completionDate:'2026-10-26',transfer:{},sections:[],auxiliary:{},variables:[]}});
        if(p.endsWith('/edit-layout'))return json({ratio:16/9,pageEditing,canvasEditing:true,pages:Array.from({length:12},(_,i)=>({id:'page-'+i,title:(i+1)+'번 분석 결과',regions:[]}))});
        if(p.includes('/edit-slides/'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 450"><rect width="800" height="450" fill="white"/><rect width="800" height="45" fill="#e4f2f8"/><text x="50" y="130" font-size="32" fill="#172b4d">Report preview</text></svg>'});
        return json({status:'completed'});
      });
      try{
        await page.goto(origin+'/__test__/request-editor/'+kind);
        const help=page.locator('#page-selection-help'),summary=help.locator('summary'),sidebar=page.locator('#page-sidebar'),list=page.locator('#page-thumbnails');
        await help.waitFor();if(legacy)assert(await page.locator('script[src*="/assets/browser-compat/"]').count()>0);
        assert.equal(await help.getAttribute('open'),null);assert(!await sidebar.locator('#page-selection-help').count());
        assert(!(await sidebar.textContent()).includes('Shift+클릭'));assert.equal(await list.locator('.page-thumb').count(),12);
        // Moving the guide must not change page multi-selection or save anything.
        const thumb=page.locator('.page-thumb');await thumb.nth(0).click();await thumb.nth(2).click({modifiers:['Shift']});
        assert.equal(await page.locator('.page-thumb[aria-pressed=true]').count(),3);
        await thumb.nth(1).click({modifiers:['Meta']});assert.equal(await page.locator('.page-thumb[aria-pressed=true]').count(),2);
        await thumb.nth(1).focus();await page.keyboard.press('Control+a');assert.equal(await page.locator('.page-thumb[aria-pressed=true]').count(),12);
        for(const width of [1440,1024,768,390,320]){
          await page.setViewportSize({width,height:900});
          if(width<=700&&await sidebar.isVisible())await page.locator('#page-sidebar-close').click();
          const dimensions=()=>page.evaluate(()=>{
            const stage=document.getElementById('slide-stage').getBoundingClientRect(),help=document.getElementById('page-selection-help').getBoundingClientRect();
            return {stageHeight:stage.height,listHeight:document.getElementById('page-thumbnails').getBoundingClientRect().height,helpTop:help.top,stageBottom:stage.bottom,helpHeight:help.height,overflow:document.documentElement.scrollWidth>innerWidth+1};
          });
          await summary.scrollIntoViewIfNeeded();const before=await dimensions();assert(before.helpTop>=before.stageBottom+6);assert(before.helpHeight<=42);assert(!before.overflow);
          await summary.focus();await page.keyboard.press('Enter');assert(await help.evaluate(el=>el.open));assert(await help.locator('ul').isVisible());
          const after=await dimensions();assert.equal(after.stageHeight,before.stageHeight);assert.equal(after.listHeight,before.listHeight);assert(!after.overflow);
          if(width===1440||width===390)await help.screenshot({path:path.join(output,name+'-'+width+'-help.png')});
          await page.keyboard.press('Space');assert(!await help.evaluate(el=>el.open));
          if(width===1440)await page.locator('.preview-workspace').screenshot({path:path.join(output,name+'-workspace.png')});
        }
        // Help is not offered on a layout that does not support page editing.
        pageEditing=false;await page.reload();await page.locator('#slide-image[src]').waitFor();
        assert(await help.isHidden());assert(await page.locator('#page-actions').isHidden());assert(await sidebar.isHidden());
        assert.deepEqual(posts,[]);assert.deepEqual(errors,[]);
        console.log(name+': compact guide below slide, keyboard disclosure, stable thumbnails, multi-select and 320–1440px bounds passed');
      }catch(error){await page.screenshot({path:path.join(output,name+'-failure.png'),fullPage:true});throw error;}
      finally{await context.close();}
    }
    console.log('Browser: '+browser.version());
  }finally{if(browser)await browser.close();server.kill();console.log('Artifacts: '+output);}
})().catch(error=>{console.error(error,logs.slice(-3000));process.exitCode=1;});

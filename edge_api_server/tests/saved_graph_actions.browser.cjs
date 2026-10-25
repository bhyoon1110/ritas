// Real RAW/save/report pipeline and responsive geometry in macOS Chrome.
// This is not Windows/Supermium hardware certification.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-graph-actions-'));
const server=spawn(process.env.RIST_TEST_PYTHON||'python3',['-u',path.join(__dirname,'spectrum_workflow_fixture.py'),output],{env:process.env});
let logs='';server.stderr.on('data',data=>logs+=data);
const port=new Promise((resolve,reject)=>{let data='';server.stdout.on('data',chunk=>{data+=chunk;if(data.includes('\n'))resolve(JSON.parse(data.split('\n')[0]).port);});server.on('error',reject);server.on('exit',code=>{if(code)reject(Error(logs));});});
const raw=Buffer.from(Array.from({length:241},(_,i)=>{const x=400+i*15;return x+','+(.05+Math.exp(-Math.pow(x-1700,2)/(2*55**2)));}).join('\n'));
(async()=>{
  let browser;
  try{
    const origin='http://127.0.0.1:'+await port;
    browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
    for(const kind of ['ftir','raman']){
      const page=await browser.newPage({viewport:{width:1366,height:1000}}),errors=[];
      page.setDefaultTimeout(30000);page.on('pageerror',e=>errors.push(e.message));page.on('dialog',dialog=>dialog.accept());
      const card=()=>page.locator('.sw-card').first(),button=name=>card().getByRole('button',{name,exact:true});
      async function checkLayout(step){
        for(const width of [1366,1024,768,390,320]){
          await page.setViewportSize({width,height:1000});
          await card().scrollIntoViewIfNeeded();
          const result=await page.locator('.sw-card').evaluateAll(cards=>cards.map(card=>{
            const bounds=card.getBoundingClientRect(),row=card.querySelector('.sw-card-actions');
            return {width:card.clientWidth,scroll:card.scrollWidth,height:bounds.height,row:row.getBoundingClientRect().height,
              buttons:Array.from(row.querySelectorAll('button'),b=>{const r=b.getBoundingClientRect();return {x:r.left-bounds.left,y:r.top-bounds.top,w:r.width,h:r.height,text:b.textContent,label:b.getAttribute('aria-label'),title:b.title,svg:!!b.querySelector('svg[aria-hidden="true"]')};})};
          }));
          assert.equal(result.length,2);assert(Math.abs(result[0].height-result[1].height)<2,'equal card heights for long/short names');
          for(const c of result){
            assert.equal(c.buttons.length,step===3?7:6);assert(c.scroll<=c.width+1,JSON.stringify({kind,width,c}));assert(c.row<=37);
            for(const b of c.buttons){assert(Math.abs(b.y-c.buttons[0].y)<1,'one fixed button row');assert(b.w>=32&&b.h>=36);assert(b.x>=0&&b.x+b.w<=c.width+1,JSON.stringify({kind,width,b,c}));assert(!b.text&&b.label&&b.title&&b.svg);}
          }
          await card().screenshot({path:path.join(output,kind+'-'+step+'-'+width+'.png')});
        }
        await page.setViewportSize({width:1366,height:1000});
      }
      try{
        await page.goto(origin+'/'+kind);await page.waitForFunction(()=>document.getElementById('sw-step-2')&&!document.getElementById('sw-step-2').disabled);
        await page.locator('#'+kind+'-file-input').setInputFiles({name:'actions.'+(kind==='ftir'?'dpt':'txt'),mimeType:'text/plain',buffer:raw});
        await page.waitForFunction(()=>!document.getElementById('sw-save-graph').disabled);
        await page.locator('#sw-graph-name').fill('긴 그래프 이름 · 실험 조건과 비교 내용을 포함한 저장 그래프 A');await page.locator('#sw-save-graph').click();await card().waitFor();
        await page.waitForFunction(()=>!document.getElementById('sw-save-copy').disabled);
        await page.locator('#sw-graph-name').fill('그래프 B');await page.locator('#sw-save-copy').click();await page.waitForFunction(()=>document.querySelectorAll('.sw-card').length===2&&!document.getElementById('sw-save-copy').disabled);
        await checkLayout(2);
        await button('미리보기').focus();await page.keyboard.press('Tab');assert(await button('다시 편집').evaluate(el=>el===document.activeElement));
        await button('미리보기').click();await page.locator('.sw-preview').getByRole('button',{name:'닫기',exact:true}).click();
        await button('파일 내보내기').click();assert((await page.getByLabel('저장 파일 이름',{exact:true}).inputValue()).endsWith('.ristgraph'));await page.getByRole('button',{name:'취소',exact:true}).click();
        const first=await card().getAttribute('data-graph-id');
        await page.locator('.sw-card').nth(1).locator('[data-action=up]').click();assert.notEqual(await card().getAttribute('data-graph-id'),first);
        await card().locator('[data-action=down]').click();assert.equal(await card().getAttribute('data-graph-id'),first);
        assert(await card().locator('[data-action=up]').isDisabled());assert(await page.locator('.sw-card').nth(1).locator('[data-action=down]').isDisabled());
        await button('다시 편집').click();await page.waitForFunction(()=>!document.getElementById('sw-save-graph').disabled);
        await page.locator('#sw-next').click();const frame=page.frameLocator('#sw-report-editor');
        await page.waitForFunction(()=>{const f=document.getElementById('sw-report-editor'),d=f&&f.contentDocument;return d&&d.getElementById('page-add')&&!d.getElementById('page-add').disabled;},null,{timeout:180000});
        await page.waitForFunction(kind=>!document.getElementById(kind+'-report-progress').classList.contains('is-visible'),kind);
        await checkLayout(3);
        await button('현재 페이지에 추가').click();
        await page.waitForFunction(()=>{const d=document.getElementById('sw-report-editor').contentDocument;return d.getElementById('status').textContent.includes('자동 저장 완료')&&!d.getElementById('page-add').disabled;},null,{timeout:180000});
        const endpoint=await page.locator('#sw-report-editor').evaluate(f=>f.contentWindow.location.pathname.replace(/\/edit$/,'/editor-data'));
        const response=await page.request.get(origin+endpoint);assert.equal(response.status(),200);
        const model=(await response.json()).document;assert(Object.values(model.canvasEdits).some(items=>items.some(i=>i.kind==='image')));
        const job=await page.locator('#sw-report-editor').getAttribute('src');await button('삭제').click();assert.equal(await page.locator('.sw-card').count(),1);assert.equal(await page.locator('#sw-report-editor').getAttribute('src'),job);
        assert.deepEqual(errors,[]);console.log(kind+': 5 widths × 2 stages, single-row icons, keyboard, preview/export/edit/order/add/delete passed');
      }catch(error){await page.screenshot({path:path.join(output,kind+'-failure.png'),fullPage:true});throw error;}
      finally{await page.close();}
    }
    console.log(JSON.stringify({browser:browser.version(),output}));
  }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error,logs.slice(-4000),output);process.exitCode=1;});

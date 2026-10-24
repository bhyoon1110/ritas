// Real upload/save/restore and computed CSS in Chrome; not Windows/Supermium hardware QA.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-spectrum-preview-'));
const server=spawn(process.env.RIST_TEST_PYTHON||'python3',['-u',path.join(__dirname,'spectrum_workflow_fixture.py'),output],{env:process.env});
let logs='';server.stderr.on('data',chunk=>{logs+=chunk;});
const port=new Promise((resolve,reject)=>{
  let data='';server.stdout.on('data',chunk=>{data+=chunk;if(data.includes('\n')){try{resolve(JSON.parse(data.split('\n')[0]).port);}catch(error){reject(error);}}});
  server.on('error',reject);server.on('exit',code=>{if(code)reject(new Error(logs));});
});
const raw=Buffer.from(Array.from({length:241},(_,i)=>{
  const x=400+i*15;return x+','+(.05+Math.exp(-Math.pow(x-1700,2)/(2*55**2)));
}).join('\n'));
(async()=>{
  let browser;
  try{
    const origin='http://127.0.0.1:'+await port;
    for(let i=0;i<80;i++){try{if((await fetch(origin+'/ftir')).ok)break;}catch(_){}await new Promise(r=>setTimeout(r,100));}
    browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
    for(const kind of ['ftir','raman']){
      const page=await browser.newPage({viewport:{width:1366,height:900}}),errors=[];
      page.on('pageerror',error=>errors.push(error.message));page.on('dialog',dialog=>dialog.accept());page.setDefaultTimeout(20000);
      const id=kind==='ftir'?'peak-plot':'raman-plot';
      try{
        await page.goto(origin+'/'+kind);
        await page.waitForFunction(()=>{const b=document.getElementById('sw-step-2');return b&&!b.disabled;});
        await page.locator('#'+kind+'-file-input').setInputFiles({name:'preview.'+(kind==='ftir'?'dpt':'txt'),mimeType:'text/plain',buffer:raw});
        await page.waitForFunction(()=>document.getElementById('spectrum-workflow').dataset.step==='2'&&!document.getElementById('sw-save-graph').disabled);
        await page.evaluate(id=>Plotly.relayout(document.getElementById(id),{'title.text':'저장한 그래프'}),id);
        await page.locator('#sw-graph-name').fill('저장한 그래프');await page.locator('#sw-save-graph').click();
        await page.waitForFunction(()=>document.querySelectorAll('.sw-card').length===1&&!document.getElementById('sw-save-graph').disabled);
        for(const width of [1366,390,320]){
          await page.setViewportSize({width,height:width===1366?900:844});
          const open=()=>page.locator('.sw-card').getByRole('button',{name:'미리보기',exact:true}).click();
          await open();const modal=page.locator('.sw-preview'),close=modal.getByRole('button',{name:'닫기',exact:true}),edit=modal.getByRole('button',{name:'그래프 다시 편집',exact:true});
          await modal.locator('img').evaluate(img=>img.decode());
          assert.equal(await close.evaluate(el=>el===document.activeElement),true);
          const styles=await page.evaluate(()=>{
            function read(selector){const s=getComputedStyle(document.querySelector(selector));return Object.fromEntries(['fontFamily','fontSize','fontWeight','borderRadius','borderWidth','padding','minHeight','backgroundColor','color','borderColor','whiteSpace'].map(k=>[k,s[k]]));}
            return {close:read('.sw-preview-actions button'),normal:read('#sw-next'),edit:read('.sw-preview .sw-primary'),primary:read('#sw-save-graph')};
          });
          assert.deepEqual(styles.close,styles.normal);assert.deepEqual(styles.edit,styles.primary);
          const a=await close.boundingBox(),b=await edit.boundingBox();
          assert(Math.abs(a.height-b.height)<1&&Math.abs(a.y-b.y)<1,'preview actions have equal height and share a row');
          for(const r of [a,b])assert(r.x>=0&&r.x+r.width<=width&&r.width>40&&r.height>=36);
          assert.equal(await modal.locator('div').first().evaluate(el=>el.scrollWidth<=el.clientWidth+1),true);
          await page.keyboard.press('Tab');assert.equal(await edit.evaluate(el=>el===document.activeElement),true);
          await modal.screenshot({path:path.join(output,kind+'-'+width+'.png')});
          await close.click();assert.equal(await modal.count(),0);
          await page.evaluate(id=>Plotly.relayout(document.getElementById(id),{'title.text':'임시 변경'}),id);
          await open();await edit.click();
          await page.waitForFunction(id=>document.getElementById(id).layout.title.text==='저장한 그래프'&&!document.getElementById('sw-save-graph').disabled,id);
          assert.equal(await modal.count(),0);assert.equal(await page.locator('.sw-card').count(),1);
          console.log(kind+'/'+width+': shared button styling, alignment, keyboard, close and saved graph restore passed');
        }
        assert.deepEqual(errors,[]);
      }catch(error){await page.screenshot({path:path.join(output,kind+'-failure.png'),fullPage:true});console.error(logs.slice(-4000));throw error;}
      finally{await page.close();}
    }
    console.log(JSON.stringify({browser:browser.version(),output}));
  }finally{if(browser)await browser.close();server.kill('SIGTERM');}
})().catch(error=>{console.error(error);console.error('Artifacts:',output);server.kill('SIGTERM');process.exitCode=1;});

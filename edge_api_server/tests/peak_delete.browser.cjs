// Real Plotly integration, including redraw listeners absent from DOM doubles.
// Chrome-engine QA is not a Windows/Supermium binary validation claim.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-peak-delete-'));
const server=spawn(process.env.RIST_TEST_PYTHON||'python3',['-u',path.join(__dirname,'spectrum_workflow_fixture.py'),output],{env:process.env});
let logs='';server.stderr.on('data',chunk=>{logs+=chunk;});
const port=new Promise((resolve,reject)=>{
  let data='';server.stdout.on('data',chunk=>{data+=chunk;if(data.includes('\n')){try{resolve(JSON.parse(data.split('\n')[0]).port);}catch(e){reject(e);}}});
  server.on('exit',code=>{if(code)reject(new Error(logs));});
});
const raw=Buffer.from(Array.from({length:481},(_,i)=>{
  const x=400+i*7.5;
  return x+','+(.05+Math.exp(-Math.pow(x-1700,2)/(2*45**2))+.8*Math.exp(-Math.pow(x-1250,2)/(2*65**2))+.65*Math.exp(-Math.pow(x-2900,2)/(2*50**2)));
}).join('\n'));
(async()=>{
  const origin='http://127.0.0.1:'+await port;
  for(let i=0;i<80;i++){try{if((await fetch(origin+'/ftir')).ok)break;}catch(_){}await new Promise(r=>setTimeout(r,100));}
  const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try{
    for(const kind of ['ftir','raman']){
      const page=await browser.newPage({viewport:{width:1440,height:1050}}),errors=[];
      page.on('pageerror',error=>errors.push(error.message));page.on('dialog',dialog=>dialog.accept());page.setDefaultTimeout(15000);
      const id=kind==='ftir'?'peak-plot':'raman-plot';
      try{
        await page.goto(origin+'/'+kind);
        await page.waitForFunction(()=>document.getElementById('sw-step-2')&&!document.getElementById('sw-step-2').disabled);
        await page.locator('#'+kind+'-file-input').setInputFiles({name:'peaks.'+(kind==='ftir'?'dpt':'txt'),mimeType:'text/plain',buffer:raw});
        await page.waitForFunction(id=>document.getElementById(id).data.filter(t=>t.meta&&t.meta.rist_peak).length>=3&&!document.getElementById('sw-save-graph').disabled,id);
        await page.evaluate(async id=>{
          const gd=document.getElementById(id);await Promise.resolve(gd._ristStackReady);
          await Plotly.relayout(gd,{annotations:gd.layout.annotations.concat([{name:'rist_text_box_text:keep',text:'보존할 사용자 메모',x:2200,y:.3,showarrow:false}]),
            shapes:gd.layout.shapes.concat([{name:'rist_text_box:keep',type:'rect',x0:2000,x1:2400,y0:.2,y1:.4}])});
          window.peakDeleteInitial={data:JSON.parse(JSON.stringify(gd.data)),layout:JSON.parse(JSON.stringify(gd.layout))};
        },id);
        const count=()=>page.evaluate(id=>{const gd=document.getElementById(id);return {peaks:gd.data.filter(t=>t.meta&&t.meta.rist_peak).length,labels:(gd.layout.meta.ristPeakLabels||[]).length,
          annotations:gd.layout.annotations.map(a=>({text:a.text,name:a.name,visible:a.visible})),shapes:gd.layout.shapes.length,visible:gd.querySelectorAll('.annotation').length};},id);
        const before=await count();console.log(kind,'before',JSON.stringify(before));
        await page.evaluate(id=>{const gd=document.getElementById(id);const curves=gd.data.map((t,i)=>t.meta&&t.meta.rist_peak?i:-1).filter(i=>i>=0);
          gd.dispatchEvent(new CustomEvent('rist-peak-delete',{detail:{curves:[curves[0]]}}));},id);
        await page.waitForTimeout(700);
        const single=await count();console.log(kind,'single',JSON.stringify(single));
        assert.equal(single.peaks,before.peaks-1);assert.equal(single.labels,before.labels-1);
        assert.equal(single.annotations.length,before.annotations.length-1,'single deletion removes exactly its label');
        await page.evaluate(id=>{const gd=document.getElementById(id);gd.dispatchEvent(new CustomEvent('rist-peak-delete',{detail:{curves:gd.data.map((t,i)=>t.meta&&t.meta.rist_peak?i:-1).filter(i=>i>=0)}}));},id);
        await page.waitForTimeout(700);
        const after=await count();console.log(kind,'all',JSON.stringify(after));
        assert.equal(after.peaks,0);assert.equal(after.labels,0);
        assert.deepEqual(after.annotations.map(a=>a.text),['보존할 사용자 메모']);assert.equal(after.shapes,1);
        await page.locator('#'+id).screenshot({path:path.join(output,kind+'-deleted.png')});
        await page.evaluate(async id=>{
          const gd=document.getElementById(id),initial=JSON.parse(JSON.stringify(window.peakDeleteInitial));
          await Plotly.react(gd,initial.data,initial.layout,gd._context);
          gd.dispatchEvent(new CustomEvent('rist-plot-data-replaced'));await Promise.resolve(gd._ristStackReady);
        },id);
        await page.waitForTimeout(200);
        await page.evaluate(id=>{
          const gd=document.getElementById(id);
          const curves=gd.data.map((t,i)=>t.meta&&t.meta.rist_peak?i:-1).filter(i=>i>=0);
          gd.dispatchEvent(new CustomEvent('rist-peak-delete',{detail:{curves:[curves[0]]}}));
          gd.dispatchEvent(new CustomEvent('rist-peak-delete',{detail:{curves:[curves[1]]}}));
        },id);
        await page.waitForTimeout(700);
        const rapid=await count();console.log(kind,'rapid',JSON.stringify(rapid));
        assert.equal(rapid.peaks,before.peaks-2);assert.equal(rapid.labels,before.labels-2);
        assert.equal(rapid.annotations.length,before.annotations.length-2,'rapid deletion must not leave stale labels');
        const restore=async()=>{
          await page.evaluate(async id=>{const gd=document.getElementById(id),initial=JSON.parse(JSON.stringify(window.peakDeleteInitial));
            await Plotly.react(gd,initial.data,initial.layout,gd._context);gd.dispatchEvent(new CustomEvent('rist-plot-data-replaced'));await Promise.resolve(gd._ristStackReady);},id);
          await page.waitForTimeout(200);
        };
        if(kind==='ftir'){
          await restore();
          await page.evaluate(async id=>{const gd=document.getElementById(id);await gd._ristSetSignalMode('transmittance');await Promise.resolve(gd._ristStackReady);
            gd.dispatchEvent(new CustomEvent('rist-peak-delete',{detail:{curves:[1]}}));},id);
          await page.waitForTimeout(300);
          await page.evaluate(async id=>{const gd=document.getElementById(id);await gd._ristSetSignalMode('absorbance');await Promise.resolve(gd._ristStackReady);},id);
          await page.waitForTimeout(300);
          const toggled=await count();assert.equal(toggled.peaks,before.peaks-1);
          assert.equal(toggled.annotations.length,before.annotations.length-1,'switching back cannot resurrect deleted labels');
        }
        await restore();
        await page.locator('.rist-'+kind+'-tools-toggle').click();
        await page.locator('.rist-peak-delete-button').click();
        await page.locator('#'+id).scrollIntoViewIfNeeded();
        const point=await page.evaluate(id=>{const gd=document.getElementById(id),tr=gd.data.find(t=>t.meta&&t.meta.rist_peak&&t.visible!==false&&t.visible!=='legendonly');
          const r=gd.getBoundingClientRect(),f=gd._fullLayout;return {x:r.left+f.xaxis._offset+f.xaxis.d2p(tr.x[0]),y:r.top+f.yaxis._offset+f.yaxis.d2p(tr.y[0])};},id);
        await page.mouse.click(point.x,point.y);await page.waitForTimeout(400);
        const clicked=await count();assert.equal(clicked.peaks,before.peaks-1,'real point click deletes one peak');
        assert.equal(clicked.annotations.length,before.annotations.length-1);
        if(!await page.locator('.rist-legend-edit-button').isVisible())await page.locator('.rist-'+kind+'-tools-toggle').click();
        await page.locator('.rist-legend-edit-button').click();
        await page.locator('.rist-legend-delete-all-peaks').click();
        await page.locator('.rist-legend-edit-save-all').click();await page.waitForTimeout(500);
        assert.deepEqual((await count()).annotations.map(a=>a.text),['보존할 사용자 메모']);
        assert.equal(await page.locator('.rist-legend-edit-close').isVisible(),false,'save all closes the legend editor');
        await page.evaluate(id=>document.getElementById(id)._ristHistory.undo(),id);await page.waitForTimeout(400);
        assert.equal((await count()).peaks,clicked.peaks);assert.equal((await count()).labels,clicked.labels);
        await page.evaluate(id=>document.getElementById(id)._ristHistory.redo(),id);await page.waitForTimeout(400);
        assert.equal((await count()).peaks,0);assert.deepEqual((await count()).annotations.map(a=>a.text),['보존할 사용자 메모']);
        await page.locator('#sw-graph-name').fill('삭제 검토 완료');await page.locator('#sw-save-graph').click();
        await page.waitForFunction(()=>document.querySelector('.sw-card')&&!document.getElementById('sw-save-graph').disabled);
        await page.locator('#sw-step-1').click();
        while(await page.locator('#'+kind+'-file-list button').count())await page.locator('#'+kind+'-file-list button').last().click();
        await page.waitForFunction(id=>!document.getElementById(id).data.some(t=>t.meta&&t.meta.rist_sample_parent),id);
        await page.locator('#sw-step-2').click();
        await page.locator('.sw-card').getByRole('button',{name:'다시 편집',exact:true}).click();
        await page.waitForFunction(()=>!document.getElementById('sw-save-graph').disabled);
        await page.waitForTimeout(400);
        assert.equal((await count()).peaks,0);assert.deepEqual((await count()).annotations.map(a=>a.text),['보존할 사용자 메모']);
        await page.reload();await page.waitForFunction(()=>document.querySelector('.sw-card')&&!document.getElementById('sw-save-graph').disabled);
        await page.locator('.sw-card').getByRole('button',{name:'다시 편집',exact:true}).click();
        await page.waitForFunction(()=>!document.getElementById('sw-save-graph').disabled);
        await page.waitForTimeout(400);
        assert.equal((await count()).peaks,0);assert.deepEqual((await count()).annotations.map(a=>a.text),['보존할 사용자 메모']);
        await page.locator('#'+id).screenshot({path:path.join(output,kind+'-restored.png')});
        console.log(kind+': rapid delete, actual point click, legend delete-all, undo/redo and saved graph reload passed');
        assert.deepEqual(errors,[]);
      }catch(error){await page.screenshot({path:path.join(output,kind+'-failure.png'),fullPage:true});throw error;}
      finally{await page.close();}
    }
    console.log(JSON.stringify({browser:await browser.version(),passed:true,output}));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);console.error('Artifacts:',output);process.exitCode=1;})
  .finally(()=>{server.kill('SIGTERM');fs.writeFileSync(path.join(output,'server.log'),logs);});

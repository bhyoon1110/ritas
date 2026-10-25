// Real Plotly/Chrome across viewport sizes, not Windows/Supermium certification.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),http=require('node:http');
const {execFileSync}=require('node:child_process'),{chromium}=require('playwright');
const bundle=JSON.parse(execFileSync(process.env.RIST_TEST_PYTHON||'python3',['-c',
  'from app.path_bootstrap import add_project_package_paths; add_project_package_paths(); from app.ftir_web import build_ftir_page, plotly_asset_path; from app.raman_web import build_raman_page; import json; print(json.dumps(dict(ftir=build_ftir_page(),raman=build_raman_page(),plotly=str(plotly_asset_path()))))'
],{encoding:'utf8',maxBuffer:32*1024*1024}));
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-legend-panels-'));
const server=http.createServer((req,res)=>{
  if(req.url.endsWith('/plotly.min.js')){res.setHeader('Content-Type','application/javascript');res.end(fs.readFileSync(bundle.plotly));}
  else if(bundle[req.url.slice(1)]){res.setHeader('Content-Type','text/html; charset=utf-8');res.end(bundle[req.url.slice(1)]);}
  else{res.setHeader('Content-Type','application/json');res.end(JSON.stringify({items:[],libraries:[],authenticated:false}));}
});
(async()=>{
  let browser;
  try{
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
    for(const kind of ['ftir','raman'])for(const viewport of [
      {width:1920,height:1080},{width:1366,height:768},{width:1024,height:768},{width:390,height:844}
    ]){
      const mobile=viewport.width===390,page=await browser.newPage({viewport,isMobile:mobile,hasTouch:mobile}),errors=[];
      page.setDefaultTimeout(10000);page.on('pageerror',err=>errors.push(err.message));
      const id=kind==='ftir'?'peak-plot':'raman-plot',label=kind+'-'+viewport.width;
      try{
        await page.goto('http://127.0.0.1:'+server.address().port+'/'+kind);
        await page.waitForFunction(()=>document.getElementById('sw-step-2')&&!document.getElementById('sw-step-2').disabled);
        await page.locator('#sw-step-2').click();
        await page.evaluate(async id=>{
          const g=document.getElementById(id);
          const data=[0,1,2,3,4,5].map(i=>({name:'Sample '+i,type:'scatter',mode:'lines',x:[100,200,300],y:[1+i,3+i,2+i],
            legendgroup:'sample:'+i,meta:{rist_sample_parent:true,rist_sample_group:'sample:'+i,rist_raman_stack_offset:0,
              rist_ftir_stack_offset_absorbance:0,rist_ftir_stack_offset_transmittance:0,
              ftir_signal_toggle:{absorbance_y:[1+i,3+i,2+i],transmittance_y:[30,20,10]}}}));
          await Plotly.react(g,data,Object.assign({},g.layout,{height:620,annotations:[],shapes:[],meta:{},
            xaxis:{range:[0,400]},yaxis:{range:[0,10]}}),g._context);
          g.dispatchEvent(new CustomEvent('rist-plot-data-replaced'));await g._ristStackReady;
        },id);
        const toolbar=page.locator('#'+id+' .rist-plot-control-row'),toggle=page.locator('.rist-'+kind+'-tools-toggle');
        const editor=page.locator('.rist-legend-edit-panel'),editButton=page.locator('.rist-legend-edit-button');
        const click=async locator=>mobile?locator.tap():locator.click();
        if(!await editButton.isVisible())await click(toggle);
        await click(editButton);await editor.waitFor({state:'visible'});
        assert.equal(await toolbar.isVisible(),false,label+': only one panel may be visible');
        const name=editor.locator('.is-sample textarea').first();await name.fill('저장 전 Sample');
        await page.waitForFunction(id=>document.getElementById(id).data[0].name==='저장 전 Sample',id);
        await editor.locator('.rist-legend-sample-width').first().fill('5');
        await editor.locator('.rist-legend-sample-dash').first().selectOption('dash');
        await page.waitForFunction(id=>{const line=document.getElementById(id).data[0].line;return line&&line.width===5&&line.dash==='dash';},id);
        // Autosave must continue storing the opening state, not the preview.
        await page.waitForTimeout(650);
        const readWorkspace=()=>page.evaluate(kind=>new Promise((resolve,reject)=>{
          const request=indexedDB.open('rist-'+kind+'-workspace-v1',1);
          request.onerror=()=>reject(request.error);request.onsuccess=()=>{
            const db=request.result,read=db.transaction('workspace').objectStore('workspace').get('current');
            read.onsuccess=()=>{resolve(read.result);db.close();};read.onerror=()=>{reject(read.error);db.close();};
          };
        }),kind);
        assert.equal((await readWorkspace()).plotData[0].name,'Sample 0',label+': preview must not leak to autosave');
        if(await toggle.isVisible()){
          await click(toggle);assert.equal(await toolbar.isVisible(),false);
          assert.equal(await name.inputValue(),'저장 전 Sample');
          assert.equal(await editor.evaluate(el=>document.activeElement===el),true);
        }
        if(viewport.width===1366){
          const head=await editor.locator('.rist-legend-edit-head').boundingBox();
          await page.mouse.move(head.x+60,head.y+15);await page.mouse.down();
          await page.mouse.move(head.x-150,head.y+70,{steps:6});await page.mouse.up();
          await page.setViewportSize({width:800,height:700});await page.waitForTimeout(150);
          assert.equal(await name.inputValue(),'저장 전 Sample');assert.equal(await toolbar.isVisible(),false);
        }
        const box=await editor.boundingBox(),plot=await page.locator('#'+id).boundingBox();
        assert(box.x>=plot.x-1&&box.x+box.width<=plot.x+plot.width+1,label+': editor fits graph width');
        await editor.screenshot({path:path.join(output,label+'-editor.png')});
        await editor.locator('.rist-legend-font-size').fill('99');await click(editor.locator('.rist-legend-edit-save-all'));
        await page.waitForFunction(()=>document.querySelector('.rist-legend-edit-panel').getAttribute('aria-busy')!=='true');
        assert.equal(await toolbar.isVisible(),false);assert.equal(await name.inputValue(),'저장 전 Sample');
        await editor.locator('.rist-legend-font-size').fill('16');await click(editor.locator('.rist-legend-edit-save-all'));
        await editor.waitFor({state:'hidden'});assert.equal(await toolbar.isVisible(),true);
        await page.waitForFunction(id=>document.getElementById(id).data[0].name==='저장 전 Sample',id);
        assert.equal(await page.evaluate(id=>document.getElementById(id).layout.dragmode,id),'pan');
        assert.equal(await page.evaluate(id=>document.getElementById(id)._ristEditMode,id),false);
        await page.waitForTimeout(650);assert.equal((await readWorkspace()).plotData[0].name,'저장 전 Sample');
        await click(editButton);await name.fill('저장하지 않은 이름');
        await page.waitForFunction(id=>document.getElementById(id).data[0].name==='저장하지 않은 이름',id);
        await click(editor.locator('.rist-legend-edit-close'));await editor.waitFor({state:'hidden'});
        assert.equal(await toolbar.isVisible(),true);
        assert.equal(await page.evaluate(id=>document.getElementById(id).data[0].name,id),'저장 전 Sample');
        await page.waitForTimeout(650);assert.equal((await readWorkspace()).plotData[0].name,'저장 전 Sample');
        const floating=await toggle.isVisible();
        if(floating && !await toolbar.isVisible())await click(toggle);
        await click(page.locator('.rist-peak-add-button'));
        await page.waitForFunction(id=>document.getElementById(id)._ristPeakEditMode==='add',id);
        if(floating){
          assert.equal(await toolbar.isVisible(),false,label+': Add Peak folds floating tools');
          assert.equal(await toggle.getAttribute('aria-expanded'),'false');
        }
        const rawBefore=await page.evaluate(id=>document.getElementById(id).data.filter(t=>t.meta&&t.meta.rist_sample_parent).map(t=>({x:t.x,y:t.y})),id);
        const drag=page.locator('#'+id+' .nsewdrag');await drag.scrollIntoViewIfNeeded();
        const plotArea=await drag.boundingBox(),point={x:plotArea.width/2,y:Math.min(60,plotArea.height/3)};
        if(mobile)await drag.tap({position:point});else await drag.click({position:point});
        await page.waitForFunction(id=>document.getElementById(id).data.some(t=>t.meta&&t.meta.rist_peak&&t.meta.rist_peak.user),id);
        await page.waitForTimeout(750);
        assert.equal(await page.evaluate(id=>document.getElementById(id).data.filter(t=>t.meta&&t.meta.rist_peak&&t.meta.rist_peak.user).length,id),1,'one tap/click adds exactly one peak');
        assert.equal(await page.evaluate(id=>document.getElementById(id)._ristPeakEditMode,id),'add');
        assert.deepEqual(await page.evaluate(id=>document.getElementById(id).data.filter(t=>t.meta&&t.meta.rist_sample_parent).map(t=>({x:t.x,y:t.y})),id),rawBefore);
        await page.locator('#'+id).screenshot({path:path.join(output,label+'-peak-tools-folded.png')});
        const status=page.locator('.rist-interaction-status');
        await page.waitForFunction(()=>document.querySelector('.rist-interaction-status').dataset.mode==='peak-add');
        assert.equal(await status.evaluate(el=>el.scrollWidth<=el.clientWidth),true,'status fits narrow screens');
        const statusHeight=(await status.boundingBox()).height;
        await status.screenshot({path:path.join(output,label+'-mode-add.png')});
        if(mobile)await status.locator('button').tap();else await page.keyboard.press('Escape');
        await page.waitForFunction(id=>document.getElementById(id)._ristPeakEditMode==='none',id);
        assert.equal((await status.boundingBox()).height,statusHeight);
        assert.equal(await page.evaluate(id=>document.getElementById(id).layout.dragmode,id),'pan');
        if(floating){
          assert.equal(await toolbar.isVisible(),false);
          await click(toggle);assert.equal(await toolbar.isVisible(),true,label+': tools can reopen after adding');
        }
        if(!await page.locator('.rist-peak-delete-button').isVisible())await click(toggle);
        await click(page.locator('.rist-peak-delete-button'));
        if(floating&&await toolbar.isVisible())await click(toggle);
        assert.equal(await page.evaluate(id=>document.getElementById(id)._ristPeakEditMode,id),'delete');
        await click(page.locator('#'+id+' .annotation .annotation-text').first());
        await page.waitForFunction(id=>!document.getElementById(id).data.some(t=>t.meta&&t.meta.rist_peak),id);
        assert.equal(await page.evaluate(id=>!!document.getElementById(id)._ristInlineTextEditing,id),false);
        if(mobile)await status.locator('button').tap();else await page.keyboard.press('Escape');
        await page.waitForFunction(()=>document.querySelector('.rist-interaction-status').dataset.mode==='pan');
        assert.deepEqual(await page.evaluate(id=>document.getElementById(id).data.filter(t=>t.meta&&t.meta.rist_sample_parent).map(t=>({x:t.x,y:t.y})),id),rawBefore);
        if(!await editButton.isVisible())await click(toggle);
        await click(editButton);await name.fill('');
        await page.waitForFunction(id=>document.getElementById(id).data[0].name==='',id);
        await click(editor.locator('.rist-legend-edit-save-all'));await editor.waitFor({state:'hidden'});
        assert.equal(await page.evaluate(id=>document.getElementById(id)._fullData[0].name,id),'','Plotly accepts empty text without a trace-name fallback');
        if(await toggle.isVisible()&&await toolbar.isVisible())await click(toggle);
        if(kind==='ftir'&&!mobile){
          const items=page.locator('#'+id+' g.legend g.traces');
          const index=await items.evaluateAll(nodes=>nodes.findIndex(n=>n.__data__[0].trace.index===0));
          assert(index>=0,'empty legend retains its editable line symbol');
          await items.nth(index).locator('.legendtoggle').dblclick();
          const inline=page.locator('[aria-label="범례 이름 수정"]');
          await inline.waitFor({state:'visible'});assert.equal(await inline.inputValue(),'');
          await inline.press('Escape');await inline.waitFor({state:'hidden'});
        }
        await page.locator('#'+id).screenshot({path:path.join(output,label+'-blank-legend.png')});
        await page.waitForTimeout(650);assert.equal((await readWorkspace()).plotData[0].name,'');
        await page.reload();
        await page.waitForFunction(id=>{const gd=document.getElementById(id);return gd.data.length===6&&gd.data[0].name==='';},id);
        await page.locator('#sw-step-2').click();
        if(!await editButton.isVisible())await click(toggle);
        await click(editButton);assert.equal(await name.inputValue(),'');
        await name.fill('다시 입력한 이름');await click(editor.locator('.rist-legend-edit-save-all'));
        await editor.waitFor({state:'hidden'});
        assert.equal(await page.evaluate(id=>document.getElementById(id).data[0].name,id),'다시 입력한 이름');
        assert.deepEqual(errors,[]);console.log(label+': live preview, autosave, rollback, panel coordination, Add Peak auto-fold and real graph click passed');
      }catch(error){await page.screenshot({path:path.join(output,label+'-failure.png'),fullPage:true});throw error;}
      finally{await page.close();}
    }
    console.log(JSON.stringify({browser:browser.version(),output}));
  }finally{if(browser)await browser.close();server.close();}
})().catch(error=>{console.error(error);console.error(output);process.exitCode=1;});

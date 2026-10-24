// Real pointer drags + Plotly. This does not substitute Windows/Supermium QA.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),http=require('node:http');
const {execFileSync}=require('node:child_process'),{chromium}=require('playwright');
const bundle=JSON.parse(execFileSync(process.env.RIST_TEST_PYTHON||'python3',['-c',
  'from app.path_bootstrap import add_project_package_paths; add_project_package_paths(); from app.ftir_web import build_ftir_page, plotly_asset_path; from app.raman_web import build_raman_page; import json; print(json.dumps(dict(ftir=build_ftir_page(),raman=build_raman_page(),plotly=str(plotly_asset_path()))))'
],{encoding:'utf8',maxBuffer:32*1024*1024}));
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-raw-order-'));
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
    for(const kind of ['ftir','raman']){
      const page=await browser.newPage({viewport:{width:1400,height:1000}}),errors=[];
      page.on('pageerror',err=>errors.push(err.message));
      const id=kind==='ftir'?'peak-plot':'raman-plot',key=kind==='ftir'?'ristFtirStack':'ristRamanStack';
      await page.goto('http://127.0.0.1:'+server.address().port+'/'+kind);
      await page.waitForFunction(()=>document.getElementById('sw-step-2')&&!document.getElementById('sw-step-2').disabled);
      await page.locator('#sw-step-2').click();
      await page.evaluate(async({id,key})=>{
        const g=document.getElementById(id);
        const traces=[0,1,2].map(i=>({type:'scatter',mode:'lines',name:'Raw '+(i+1),x:[100,200,300],y:[1,3,2],
          line:{color:['#1477cc','#e87323','#16a069'][i]},legendgroup:'sample:'+i,
          meta:{rist_sample_parent:true,rist_sample_group:'sample:'+i,rist_legend_edit_group:'sample:'+i,
            rist_raman_stack_offset:0,rist_ftir_stack_offset_absorbance:0,rist_ftir_stack_offset_transmittance:0,
            ftir_signal_toggle:{absorbance_y:[1,3,2],transmittance_y:[30,20,10]}}}));
        await Plotly.react(g,traces,Object.assign({},g.layout,{height:650,annotations:[],shapes:[],
          xaxis:{range:[0,400]},yaxis:{range:[-1,30]},meta:{[key]:{enabled:true,gap:1.2,unit:10,
            modeUnits:{absorbance:10,transmittance:100},sampleOrder:['sample:0','sample:1','sample:2'],
            samplePositions:{'sample:0':0,'sample:1':1.2,'sample:2':2.4},sampleOffsets:{'sample:0':0,'sample:1':12,'sample:2':24}}}}),g._context);
        g.dispatchEvent(new CustomEvent('rist-plot-data-replaced'));await g._ristStackReady;
      },{id,key});
      await page.locator('.rist-'+kind+'-tools-toggle').click();
      await page.locator('.rist-legend-edit-button').click();
      const rows=page.locator('.rist-legend-edit-row.is-sample');
      assert.deepEqual(await rows.locator('textarea').allTextContents(),['','','']);
      const source=rows.nth(2).locator('.rist-raw-order-handle');await source.scrollIntoViewIfNeeded();
      const from=await source.boundingBox(),to=await rows.nth(0).boundingBox();
      await rows.nth(2).locator('textarea').fill('저장 전 Raw 이름');
      await page.mouse.move(from.x+from.width/2,from.y+from.height/2);await page.mouse.down();
      await page.mouse.move(to.x+20,to.y+4,{steps:12});await page.mouse.up();
      await page.waitForFunction(({id,key})=>document.getElementById(id).layout.meta[key].legendOrder.join(',')==='sample:0,sample:2,sample:1',{id,key});
      await page.waitForFunction(()=>!document.querySelector('.rist-raw-order-handle').disabled);
      assert.equal(await rows.nth(0).locator('textarea').inputValue(),'저장 전 Raw 이름');
      const state=await page.evaluate(({id,key})=>{
        const g=document.getElementById(id);return {meta:g.layout.meta[key],names:g.data.map(t=>t.name),
          y:g.data.map(t=>t.y),offsets:g.data.map(t=>t.meta.rist_raman_stack_offset||t.meta.rist_ftir_stack_offset_absorbance||0)};
      },{id,key});
      assert.deepEqual(state.names,['Raw 1','Raw 2','Raw 3']);
      state.y.forEach((y,i)=>assert.deepEqual(y.map(v=>v-state.offsets[i]),[1,3,2]));
      await rows.nth(0).locator('.rist-raw-order-handle').press('ArrowDown');
      await page.waitForFunction(({id,key})=>document.getElementById(id).layout.meta[key].legendOrder[0]==='sample:2',{id,key});
      await page.waitForFunction(()=>!document.querySelector('.rist-raw-order-handle').disabled);
      assert.equal(await rows.nth(1).locator('.rist-raw-order-handle').evaluate(el=>document.activeElement===el),true);
      await page.keyboard.press('ArrowUp');
      await page.waitForFunction(({id,key})=>document.getElementById(id).layout.meta[key].legendOrder[0]==='sample:0',{id,key});
      await page.waitForFunction(()=>!document.querySelector('.rist-raw-order-handle').disabled);
      await page.locator('.rist-legend-edit-panel').screenshot({path:path.join(output,kind+'-raw-order.png')});
      await page.locator('.rist-legend-edit-close').click();
      await page.evaluate(async id=>{await Plotly.relayout(document.getElementById(id),{'meta.ristLegendVisible':true,
        'legend.x':0.1,'legend.y':0.9,'legend.xanchor':'left','legend.yanchor':'top'});},id);
      const legendRows=page.locator('g.legend g.traces .legendtoggle');
      await legendRows.nth(2).waitFor({state:'visible'});
      const directFrom=await legendRows.nth(2).boundingBox(),directTo=await legendRows.nth(0).boundingBox();
      const coords=()=>page.evaluate(id=>{const g=document.getElementById(id);return [g.layout.legend.x,g.layout.legend.y];},id);
      const beforeCoords=await coords();
      await page.mouse.move(directFrom.x+25,directFrom.y+directFrom.height/2);await page.mouse.down();
      await page.mouse.move(directTo.x+25,directTo.y+2,{steps:12});
      await page.locator('.rist-legend-order-line').waitFor({state:'visible'});
      await page.locator('#'+id).screenshot({path:path.join(output,kind+'-legend-drag.png')});
      await page.mouse.up();
      await page.waitForFunction(({id,key})=>document.getElementById(id).layout.meta[key].legendOrder.join(',')==='sample:1,sample:0,sample:2',{id,key});
      await page.waitForTimeout(700);
      assert.deepEqual(await coords(),beforeCoords,'row drag must not move the legend box');
      assert.equal(await page.locator('[aria-label="범례 이름 수정"]').count(),0);
      assert.equal(await page.evaluate(id=>document.getElementById(id).data.every(t=>t.visible!==false&&t.visible!=='legendonly'),id),true);
      await page.evaluate(async id=>{const g=document.getElementById(id);g._ristHistory.undo();},id);
      await page.waitForFunction(({id,key})=>document.getElementById(id).layout.meta[key].legendOrder.join(',')==='sample:0,sample:2,sample:1',{id,key});
      // The dedicated bar still moves the entire box, without reordering rows.
      const bar=await page.locator('.rist-legend-drag-handle').boundingBox();
      await page.mouse.move(bar.x+20,bar.y+8);await page.mouse.down();
      await page.mouse.move(bar.x+70,bar.y+28,{steps:8});await page.mouse.up();
      await page.waitForFunction(({id,x})=>document.getElementById(id).layout.legend.x!==x,{id,x:beforeCoords[0]});
      assert.equal(await page.evaluate(({id,key})=>document.getElementById(id).layout.meta[key].legendOrder.join(','),{id,key}),'sample:0,sample:2,sample:1');
      // Stationary clicks and double-click editing must remain independent.
      await legendRows.nth(0).click();
      await page.waitForFunction(id=>document.getElementById(id).data[0].visible==='legendonly',id);
      await legendRows.nth(0).dblclick({delay:60});
      await page.locator('[aria-label="범례 이름 수정"]').waitFor({state:'visible'});
      await page.locator('[aria-label="범례 이름 수정"]').press('Escape');
      await page.waitForTimeout(700);
      assert.equal(await page.evaluate(id=>document.getElementById(id).data[0].visible,id),'legendonly');
      await legendRows.nth(0).click();
      await page.waitForFunction(id=>document.getElementById(id).data[0].visible===true,id);
      await legendRows.nth(0).dblclick({delay:80});
      const nameEditor=page.locator('[aria-label="범례 이름 수정"]');await nameEditor.waitFor({state:'visible'});
      assert.equal(await nameEditor.inputValue(),'Raw 1');await nameEditor.fill('Renamed Raw 1');await nameEditor.press('Enter');
      await page.waitForFunction(id=>document.getElementById(id).data[0].name==='Renamed Raw 1',id);
      await page.waitForTimeout(700);
      assert.equal(await page.evaluate(id=>document.getElementById(id).data[0].visible,id),true);
      assert.deepEqual(errors,[]);console.log(kind+': actual drag, keyboard ordering, unchanged RAW/identities and draft text passed');
      await page.close();
    }
    console.log(JSON.stringify({browser:browser.version(),output}));
  }finally{if(browser)await browser.close();server.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});

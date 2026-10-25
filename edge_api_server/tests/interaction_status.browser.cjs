// Actual Plotly, mouse/touch and viewport QA. Not Windows/Supermium certification.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),http=require('node:http');
const {execFileSync}=require('node:child_process'),{chromium}=require('playwright');
const bundle=JSON.parse(execFileSync(process.env.RIST_TEST_PYTHON||'python3',['-c',
  'from app.path_bootstrap import add_project_package_paths; add_project_package_paths(); from app.ftir_web import build_ftir_page, plotly_asset_path; from app.raman_web import build_raman_page; import json; print(json.dumps(dict(ftir=build_ftir_page(),raman=build_raman_page(),plotly=str(plotly_asset_path()))))'
],{encoding:'utf8',maxBuffer:32*1024*1024}));
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-mode-status-'));
const server=http.createServer((req,res)=>{
  if(req.url.endsWith('/plotly.min.js')){res.setHeader('Content-Type','application/javascript');res.end(fs.readFileSync(bundle.plotly));}
  else if(bundle[req.url.slice(1)]){res.setHeader('Content-Type','text/html; charset=utf-8');res.end(bundle[req.url.slice(1)]);}
  else{res.setHeader('Content-Type','application/json');res.end(JSON.stringify({items:[],libraries:[],authenticated:false}));}
});
const overlaps=(a,b)=>Math.max(0,Math.min(a.x+a.width,b.x+b.width)-Math.max(a.x,b.x))*Math.max(0,Math.min(a.y+a.height,b.y+b.height)-Math.max(a.y,b.y));
(async()=>{
  let browser;
  try{
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
    for(const kind of ['ftir','raman'])for(const width of [1366,390]){
      const mobile=width===390,page=await browser.newPage({viewport:{width,height:900},isMobile:mobile,hasTouch:mobile}),errors=[];
      page.on('pageerror',err=>errors.push(err.message));page.setDefaultTimeout(15000);
      const id=kind==='ftir'?'peak-plot':'raman-plot',label=kind+'-'+width;
      const click=locator=>mobile?locator.tap():locator.click();
      try{
        await page.goto('http://127.0.0.1:'+server.address().port+'/'+kind);
        await page.waitForFunction(()=>document.getElementById('sw-step-2')&&!document.getElementById('sw-step-2').disabled);await page.locator('#sw-step-2').click();
        await page.evaluate(async id=>{const g=document.getElementById(id);await Plotly.react(g,[{name:'Sample',mode:'lines',x:[100,200,300],y:[1,3,2],meta:{rist_sample_parent:true,rist_sample_group:'sample:0'}}],
          Object.assign({},g.layout,{meta:{ristPlotSize:{width:Math.max(320,g.parentElement.clientWidth),height:1600,ratio:'custom'}},annotations:[],shapes:[],xaxis:{range:[0,400]},yaxis:{range:[0,4]}}),g._context);
          g.dispatchEvent(new CustomEvent('rist-plot-data-replaced'));await g._ristStackReady;},id);
        const status=page.locator('.rist-interaction-status'),toggle=page.locator('.rist-'+kind+'-tools-toggle'),toolbar=page.locator('#'+id+' .rist-plot-control-row');
        if(!await page.locator('.rist-peak-add-button').isVisible())await click(toggle);await click(page.locator('.rist-peak-add-button'));
        await page.waitForFunction(()=>document.querySelector('.rist-interaction-status').dataset.mode==='peak-add');
        const scrollGraph=async()=>{await page.evaluate(id=>{const g=document.getElementById(id);window.scrollTo(0,window.scrollY+g.getBoundingClientRect().top+140);},id);await page.waitForTimeout(150);};
        await scrollGraph();
        let rect=await status.boundingBox();assert(rect&&rect.x>=11&&rect.x+rect.width<=width-11&&rect.y+rect.height<=889,'floats inside visible viewport');
        assert.equal(await status.evaluate(el=>getComputedStyle(el).position),'fixed');
        assert.equal(await status.evaluate(el=>el.scrollWidth<=el.clientWidth),true,'no text overflow');
        assert.equal(await page.locator('#'+id+' .rist-interaction-status').count(),0);
        const before=await page.locator('#'+id).boundingBox();
        const pass=await status.locator('.rist-interaction-copy').evaluate(node=>{const r=node.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+10;
          const target=document.elementFromPoint(x,y);return {x,y,plot:!!target.closest('.js-plotly-plot'),badge:!!target.closest('.rist-interaction-status')};});
        assert.equal(pass.badge,false,'text does not intercept graph interaction');assert.equal(pass.plot,true,'actual graph below badge');
        if(mobile)await page.touchscreen.tap(pass.x,pass.y);else await page.mouse.click(pass.x,pass.y);
        await page.waitForFunction(id=>document.getElementById(id).data.some(t=>t.meta&&t.meta.rist_peak&&t.meta.rist_peak.user),id);
        await page.screenshot({path:path.join(output,label+'-add.png')});
        await click(status.locator('button'));await page.waitForFunction(()=>document.querySelector('.rist-interaction-status').dataset.mode==='pan');
        assert.equal((await page.locator('#'+id).boundingBox()).height,before.height,'mode switch cannot resize graph');
        assert.equal((await status.boundingBox()).height,rect.height);
        if(!await toolbar.isVisible())await click(toggle);await click(toolbar.locator('.rist-legend-edit-button'));
        const editor=page.locator('.rist-legend-edit-panel');await editor.waitFor({state:'visible'});await page.waitForTimeout(150);
        if(!mobile){
          // Move the actual editor toward the dock, checking real drag/observer integration.
          const head=await editor.locator('[data-window-drag-handle]').boundingBox(),dock=await status.boundingBox();
          await page.mouse.move(head.x+8,head.y+8);await page.mouse.down();await page.mouse.move(dock.x+8,dock.y+8,{steps:8});await page.mouse.up();await page.waitForTimeout(150);
          assert.equal(overlaps(await status.boundingBox(),await editor.boundingBox()),0,'avoids dragged editor');
        }
        await page.screenshot({path:path.join(output,label+'-editor.png')});
        await click(editor.locator('.rist-legend-edit-close'));if(await toolbar.isVisible())await click(toggle);
        await scrollGraph();
        // Confirm changing screen size and hiding the edit step cannot leave an orphan badge.
        await page.setViewportSize({width:320,height:640});await page.waitForTimeout(150);
        rect=await status.boundingBox();assert(rect&&rect.x>=11&&rect.x+rect.width<=309&&rect.y+rect.height<=629);
        const overflow=await page.evaluate(async id=>{
          const g=document.getElementById(id),variants=[{_ristLegendEditOpen:true},{_ristInlineTextEditing:true},
            {_ristPeakCoordinateOpen:true},{_ristPeakEditMode:'add'},{_ristPeakEditMode:'delete'},{_ristPeakEditMode:'select'},
            {_ristAxisCropActive:true},{_ristRamanYDragMode:true},{_ristRamanRatioMode:true,_ristRamanRatioStage:'denominator'},
            {_ristShapeDrawMode:true,_ristShapeDrawKind:'text'},{_ristEditMode:true},{_ristAxisScaleActive:true},{_ristAxisSettingsOpen:true}];
          const keys=[...new Set(variants.flatMap(item=>Object.keys(item)))],saved=Object.fromEntries(keys.map(key=>[key,g[key]])),bad=[];
          for(const variant of variants){
            keys.forEach(key=>g[key]=false);Object.assign(g,variant);g._ristInteractionModes.refresh();
            await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
            const bar=document.querySelector('.rist-interaction-status'),rect=bar.getBoundingClientRect();
            for(const node of bar.querySelectorAll('.rist-interaction-name,.rist-interaction-help')){
              const box=node.getBoundingClientRect();if(box.bottom>rect.bottom-5||box.top<rect.top+5||node.scrollWidth>node.clientWidth)bad.push(bar.dataset.mode);
            }
          }
          keys.forEach(key=>{if(saved[key]===undefined)delete g[key];else g[key]=saved[key];});g._ristInteractionModes.refresh();return bad;
        },id);
        assert.deepEqual(overflow,[],'all current-mode names and instructions fit at 320px');
        await page.screenshot({path:path.join(output,label+'-narrow.png')});
        const svg=await page.evaluate(async id=>decodeURIComponent(await Plotly.toImage(document.getElementById(id),{format:'svg',width:800,height:600})),id);
        assert(!svg.includes('현재 모드')&&!svg.includes('rist-interaction-status'),'badge excluded from image');
        await page.locator('#sw-step-1').click();await status.waitFor({state:'hidden'});
        await page.locator('#sw-step-2').click();await scrollGraph();await status.waitFor({state:'visible'});
        await page.evaluate(id=>{const spacer=document.createElement('div');spacer.style.height='2000px';document.body.appendChild(spacer);window.scrollTo(0,document.body.scrollHeight);},id);
        await status.waitFor({state:'hidden'});
        assert.deepEqual(errors,[]);console.log(label+': scroll/resize, click-through peak add, Exit, panel avoidance, workflow hiding and clean export passed');
      }catch(error){await page.screenshot({path:path.join(output,label+'-failure.png'),fullPage:true});throw error;}
      finally{await page.close();}
    }
    console.log(JSON.stringify({browser:browser.version(),output}));
  }finally{if(browser)await browser.close();server.close();}
})().catch(error=>{console.error(error,output);process.exitCode=1;});

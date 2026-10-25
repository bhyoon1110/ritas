// Real Chrome/Plotly mouse and touch QA, not a Windows/Supermium binary claim.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),http=require('node:http');
const {execFileSync}=require('node:child_process'),{chromium}=require('playwright');
const bundle=JSON.parse(execFileSync(process.env.RIST_TEST_PYTHON||'python3',['-c',
  'from app.path_bootstrap import add_project_package_paths; add_project_package_paths(); from app.ftir_web import build_ftir_page, plotly_asset_path; from app.raman_web import build_raman_page; import json; print(json.dumps(dict(ftir=build_ftir_page(),raman=build_raman_page(),plotly=str(plotly_asset_path()))))'
],{encoding:'utf8',maxBuffer:32*1024*1024}));
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-floating-panels-'));
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
    for(const kind of ['ftir','raman'])for(const width of [1366,390]){
      const mobile=width===390,page=await browser.newPage({viewport:{width,height:980},isMobile:mobile,hasTouch:mobile}),errors=[];
      page.setDefaultTimeout(15000);page.on('pageerror',err=>errors.push(err.message));const id=kind==='ftir'?'peak-plot':'raman-plot',label=kind+'-'+width;
      const click=locator=>mobile?locator.tap():locator.click();
      const touch=mobile?await page.context().newCDPSession(page):null;
      async function drag(panel){
        await panel.locator('.rist-floating-head').scrollIntoViewIfNeeded();
        const h=await panel.locator('[data-window-drag-handle]').boundingBox(),start={x:h.x+h.width/2,y:h.y+h.height/2},end={x:20,y:20};
        if(mobile){
          await touch.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[start]});
          for(let n=1;n<=8;n++)await touch.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:start.x+(end.x-start.x)*n/8,y:start.y+(end.y-start.y)*n/8}]});
          await touch.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
        }else{await page.mouse.move(start.x,start.y);await page.mouse.down();await page.mouse.move(end.x,end.y,{steps:8});await page.mouse.up();}
        await page.waitForTimeout(80);
        assert.equal(await panel.evaluate(node=>getComputedStyle(node).position),'fixed');
        const rect=await panel.boundingBox();assert(rect.x>=7&&rect.y>=7&&rect.x+rect.width<=width+1,'viewport clamp');assert(rect.y<40,'move above graph');
        const alpha=panel.locator('[data-window-opacity]');await click(alpha);
        const opacity=Number(await panel.evaluate(node=>getComputedStyle(node).opacity));assert(opacity>.55&&opacity<1,'mouse/touch opacity control');
        await alpha.focus();await alpha.press('Home');await alpha.press('ArrowRight');
        assert(Math.abs(Number(await panel.evaluate(node=>getComputedStyle(node).opacity))-.56)<.01);
        await alpha.press('End');assert.equal(await panel.evaluate(node=>getComputedStyle(node).opacity),'1');
      }
      try{
        await page.goto('http://127.0.0.1:'+server.address().port+'/'+kind);
        await page.waitForFunction(()=>document.getElementById('sw-step-2')&&!document.getElementById('sw-step-2').disabled);await page.locator('#sw-step-2').click();
        await page.evaluate(async id=>{const g=document.getElementById(id);await Plotly.react(g,[{name:'Sample',x:[100,200,300],y:[1,3,2],meta:{rist_sample_parent:true,rist_sample_group:'sample:0'}}],
          Object.assign({},g.layout,{height:620,meta:{},annotations:[],shapes:[],xaxis:{range:[0,400]},yaxis:{range:[0,4]}}),g._context);g.dispatchEvent(new CustomEvent('rist-plot-data-replaced'));await g._ristStackReady;},id);
        const toolbar=page.locator('#'+id+' .rist-plot-control-row'),toggle=page.locator('.rist-'+kind+'-tools-toggle');
        await click(toggle);await drag(toolbar);await toolbar.screenshot({path:path.join(output,label+'-tools.png')});
        const before=await toolbar.boundingBox();await click(toolbar.locator('.rist-legend-edit-button'));
        const legend=page.locator('.rist-legend-edit-panel');await drag(legend);
        await legend.locator('.is-sample textarea').first().fill('temporary');
        await page.waitForFunction(id=>document.getElementById(id).data[0].name==='temporary',id);
        await click(legend.locator('.rist-legend-edit-close'));await legend.waitFor({state:'hidden'});
        assert.equal(await page.evaluate(id=>document.getElementById(id).data[0].name,id),'Sample');
        await toolbar.waitFor({state:'visible'});await page.waitForTimeout(50);assert(Math.abs((await toolbar.boundingBox()).x-before.x)<2);
        await click(toolbar.locator('.rist-axis-control-button'));const axis=page.locator('.rist-axis-control-panel');await drag(axis);
        await click(axis.locator('.rist-axis-control-close'));
        if(!await toolbar.isVisible())await click(toggle);
        await click(toolbar.locator('.rist-peak-coordinate-button'));const coordinate=page.locator('.rist-peak-coordinate-dialog');await drag(coordinate);
        await coordinate.getByLabel('피크 X좌표').fill('150.25');await click(coordinate.getByRole('button',{name:'피크 추가',exact:true}));
        await page.waitForFunction(id=>document.getElementById(id).data.some(t=>t.meta&&t.meta.rist_peak&&t.x[0]===150.25),id);
        await click(coordinate.getByRole('button',{name:'닫기 (Esc)'}));
        if(!await toolbar.isVisible())await click(toggle);await click(toolbar.locator('.rist-legend-edit-button'));
        await click(legend.locator('.rist-legend-color-input').first());const palette=page.locator('#rist-color-palette');await drag(palette);
        await palette.screenshot({path:path.join(output,label+'-palette.png')});await click(palette.locator('.rist-palette-close'));
        await click(legend.locator('.rist-legend-edit-close'));
        // The remaining real generated panels share the same controller; inspect
        // sizing, pointer movement, opacity and reset without changing their data.
        const selectors=['.rist-shape-editor-panel','#'+kind+'-library-modal .'+kind+'-library-dialog','#'+kind+'-report-options-modal .'+kind+'-library-dialog'];
        if(kind==='raman')selectors.push('.rist-ratio-selection','.rist-ratio-candidates','.rist-raman-ratio-editor','.rist-ratio-batch');
        for(const selector of selectors){
          console.log(label+': checking '+selector);
          const panel=page.locator(selector);await panel.evaluate(node=>{
            node.hidden=false;node.style.display='block';
            // Real openCandidates() positions this otherwise unpositioned
            // absolute panel at the clicked peak and supplies a title/list.
            if(node.matches('.rist-ratio-candidates')){
              node.style.left='8px';node.style.top='8px';
              node.querySelector('[data-candidates-title]').textContent='겹친 피크 2개 · 분자 선택';
            }
            const modal=node.closest('.ftir-library-modal,.raman-library-modal');if(modal)modal.style.display='flex';
          });
          await drag(panel);await click(panel.locator('.rist-floating-reset'));assert.notEqual(await panel.evaluate(node=>node._ristFloatingPanel.position),undefined);
          await panel.evaluate(node=>{node.hidden=true;node.style.display='none';const modal=node.closest('.ftir-library-modal,.raman-library-modal');if(modal)modal.style.display='none';});
        }
        await toolbar.locator('.rist-floating-reset').evaluate(node=>node.click());
        assert.equal(await toolbar.evaluate(node=>node._ristFloatingPanel.position),null);
        const scroll=await page.evaluate(()=>{window.scrollTo(0,0);return document.documentElement.scrollHeight;});assert(scroll>980);
        await page.mouse.move(width-10,500);await page.mouse.wheel(0,300);await page.waitForTimeout(100);assert((await page.evaluate(()=>window.scrollY))>0,'page scrolling unlocked');
        assert.deepEqual(errors,[]);console.log(label+': popup drag outside graph, opacity, viewport limits, reset, cancel/edit and scroll passed');
      }catch(error){await page.screenshot({path:path.join(output,label+'-failure.png'),fullPage:true});throw error;}
      finally{await page.close();}
    }
    console.log(JSON.stringify({browser:browser.version(),output}));
  }finally{if(browser)await browser.close();server.close();}
})().catch(error=>{console.error(error,output);process.exitCode=1;});

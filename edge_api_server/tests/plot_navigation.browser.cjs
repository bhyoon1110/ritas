// Actual Plotly in Chromium; not a claim of Windows/Supermium hardware coverage.
const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),http=require('node:http');
const {execFileSync}=require('node:child_process');const {chromium}=require('playwright');
const bundle=JSON.parse(execFileSync(process.env.RIST_TEST_PYTHON||'python3',['-c',
  'from app.path_bootstrap import add_project_package_paths; add_project_package_paths(); from app.ftir_web import build_ftir_page, plotly_asset_path; from app.raman_web import build_raman_page; import json; print(json.dumps(dict(ftir=build_ftir_page(),raman=build_raman_page(),plotly=str(plotly_asset_path()))))'
],{encoding:'utf8',maxBuffer:32*1024*1024}));
const out=fs.mkdtempSync(path.join(os.tmpdir(),'rist-navigation-'));
const server=http.createServer((req,res)=>{
  if(req.url.endsWith('/plotly.min.js')) {res.setHeader('Content-Type','application/javascript');res.end(fs.readFileSync(bundle.plotly));}
  else if(bundle[req.url.slice(1)]) {res.setHeader('Content-Type','text/html; charset=utf-8');res.end(bundle[req.url.slice(1)]);}
  else {res.setHeader('Content-Type','application/json');res.end(JSON.stringify({items:[],libraries:[],authenticated:false}));}
});
(async()=>{
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try {
    for(const kind of ['ftir','raman']) {
      const page=await browser.newPage({viewport:{width:1400,height:1000}}),errors=[];
      page.on('pageerror',err=>errors.push(err.message));page.setDefaultTimeout(10000);
      const id=kind==='ftir'?'peak-plot':'raman-plot';
      try {
        await page.goto('http://127.0.0.1:'+server.address().port+'/'+kind);
        await page.waitForFunction(()=>document.getElementById('sw-step-2')&&!document.getElementById('sw-step-2').disabled);
        await page.locator('#sw-step-2').click();
        await page.waitForFunction(id=>document.getElementById(id)._ristEditModeToggleInstalled,id);
        const mode=()=>page.evaluate(id=>document.getElementById(id).layout.dragmode,id);
        assert.equal(await mode(),'pan');
        assert.equal(await page.locator('#'+id+' .modebar-btn[data-val="select"]').count(),0);
        await page.evaluate(async id=>{
          const gd=document.getElementById(id),layout=Object.assign({},gd.layout,{dragmode:'zoom',xaxis:{range:[0,100]},yaxis:{range:[0,100]},annotations:[],shapes:[],meta:{}});
          await Plotly.react(gd,[{x:[0,25,50,75,100],y:[10,70,25,85,10],type:'scatter',mode:'lines',name:'Raw',
            meta:{rist_sample_parent:true,rist_sample_group:'sample',rist_raman_stack_offset:0}}],layout,gd._context);
          gd.dispatchEvent(new CustomEvent('rist-plot-data-replaced'));
        },id);
        await page.waitForTimeout(200);assert.equal(await mode(),'pan');
        await page.locator('#'+id).scrollIntoViewIfNeeded();
        const zoom=page.locator('#'+id+' .modebar-btn[data-val="zoom"]');
        await zoom.click();assert.equal(await mode(),'zoom');
        await page.locator('#'+id+' .rist-edit-mode-toggle').click();assert.equal(await mode(),false);
        assert.equal(await zoom.getAttribute('aria-disabled'),'true');
        await page.locator('#'+id+' .rist-edit-mode-toggle').click();await page.waitForTimeout(100);
        assert.equal(await mode(),'pan');
        assert.equal(await page.locator('#'+id+' .modebar-btn[data-val="pan"]').evaluate(e=>e.classList.contains('active')),true);
        const rect=await page.locator('#'+id+' .nsewdrag').boundingBox();
        const before=await page.evaluate(id=>document.getElementById(id).layout.xaxis.range,id);
        await page.mouse.move(rect.x+rect.width*.4,rect.y+rect.height*.5);await page.mouse.down();
        await page.mouse.move(rect.x+rect.width*.5,rect.y+rect.height*.5,{steps:8});await page.mouse.up();
        const after=await page.evaluate(id=>document.getElementById(id).layout.xaxis.range,id);
        assert.notDeepEqual(after,before);assert.ok(Math.abs((after[1]-after[0])-(before[1]-before[0]))<1e-6);
        const openTools=async()=>{
          if(!await page.locator('.rist-'+kind+'-tools-close').isVisible()) await page.locator('.rist-'+kind+'-tools-toggle').click();
        };
        await openTools();await page.locator('.rist-axis-control-button').click();
        await page.locator('[data-axis-action="crop"]').click();await page.waitForTimeout(200);
        assert.equal(await mode(),'select');await page.keyboard.press('Escape');await page.waitForTimeout(120);assert.equal(await mode(),'pan');
        await openTools();await page.locator('.rist-axis-control-button').click();await page.locator('[data-axis-action="crop"]').click();await page.waitForTimeout(120);
        await page.mouse.move(rect.x+rect.width*.2,rect.y+rect.height*.2);await page.mouse.down();
        await page.mouse.move(rect.x+rect.width*.8,rect.y+rect.height*.8,{steps:8});await page.mouse.up();await page.waitForTimeout(200);
        assert.equal(await mode(),'pan');assert.ok(await page.evaluate(id=>!!document.getElementById(id).layout.meta.ristCropRanges,id));
        await openTools();await page.locator('.rist-'+kind+'-tools-close').click();await page.waitForTimeout(100);assert.equal(await mode(),'pan');
        await page.locator('#'+id).screenshot({path:path.join(out,kind+'-pan.png')});
        assert.deepEqual(errors,[]);console.log(kind+': initial/load Pan, no Box Select, explicit Zoom, edit exit, real Pan drag, Crop apply/cancel, tool close passed');
      } catch(err) {await page.screenshot({path:path.join(out,kind+'-failure.png'),fullPage:true});throw err;}
      finally {await page.close();}
    }
    console.log(JSON.stringify({browser:await browser.version(),passed:true,output:out}));
  } finally {await browser.close();server.close();}
})().catch(err=>{console.error(err);console.error('Screenshots:',out);server.close();process.exitCode=1;});

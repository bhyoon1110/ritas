// Uploaded spectra meet at the far right, but have disjoint ranges in the zoom window.
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const {pathToFileURL} = require('node:url');
const {spawn,execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const python = process.env.RIST_TEST_PYTHON || 'python3';
const output = fs.mkdtempSync(path.join(os.tmpdir(),'rist-ftir-fold-'));
const server = spawn(python,['-u',path.join(__dirname,'spectrum_workflow_fixture.py'),output]);
let logs = '';
server.stderr.on('data',chunk=>logs+=chunk);
const startup = new Promise((resolve,reject)=>{
  let text='';server.stdout.on('data',chunk=>{text+=chunk;if(text.includes('\n'))resolve(JSON.parse(text.split('\n')[0]).port);});
  server.once('exit',code=>reject(Error('fixture '+code+' '+logs)));
});
function spectrum(upper) {
  return Buffer.from(Array.from({length:401},(_,i)=>{
    const x=400+i*9, y=.15+.05*Math.sin(i/7)+(upper && x<3400 ? .7 : 0);
    return x+','+y;
  }).join('\n'));
}
(async()=>{
  let browser;
  try {
    const port=await startup;
    browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_BIN||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
    for(const legacy of [false,true]) {
      const context=await browser.newContext(legacy?{userAgent:'Mozilla/5.0 Chrome/49.0.2623.75 Safari/537.36'}:{});
      const page=await context.newPage();page.setDefaultTimeout(20000);const errors=[];page.on('pageerror',error=>errors.push(error.message));
      await page.goto('http://127.0.0.1:'+port+'/ftir');
      const button=page.getByRole('button',{name:'빈 Y구간 접기',exact:true});
      const toggle=async()=>{if(!await button.isVisible())await page.locator('.rist-ftir-tools-toggle').click();await button.click();};
      const state=()=>page.evaluate(async()=>{
        const g=document.getElementById('peak-plot');await g._ristStackReady;
        return {fold:g.layout.meta.ristFtirStack.foldY || null, ticks:g.layout.yaxis.ticktext || [],
          parents:g.data.filter(t=>t.meta?.rist_sample_parent).map(t=>({y:t.y,raw:t.customdata})),
          range:g.layout.yaxis.range,offsets:g.layout.meta.ristFtirStack.overlayOffsets};
      });
      const zoom=range=>page.evaluate(async r=>{const g=document.getElementById('peak-plot');await Plotly.relayout(g,{'xaxis.range':r});await g._ristStackReady;},range);
      await page.waitForFunction(()=>document.getElementById('sw-step-2') && !document.getElementById('sw-step-2').disabled);
      await page.locator('#ftir-file-input').setInputFiles([false,true].map((upper,i)=>({name:'fold-'+i+'.dpt',mimeType:'text/plain',buffer:spectrum(upper)})));
      await page.waitForFunction(()=>document.getElementById('peak-plot').data.filter(t=>t.meta?.rist_sample_parent).length===2).catch(async error=>{
        console.error(await page.locator('#ftir-status, #ftir-message').allTextContents(),errors);
        throw error;
      });
      const original=await state();assert.equal(original.fold,null);
      await toggle();assert.equal((await state()).fold.breaks.length,0);
      await zoom([2300,1400]);
      const folded=await state();assert.equal(folded.fold.breaks.length,1);
      assert(folded.ticks.includes('//'));assert(folded.ticks.some(t=>Number(t)>=.8));
      assert(folded.range[1]-folded.range[0]<.4);
      for(const [i,tr] of folded.parents.entries()){
        assert.deepEqual(tr.raw,original.parents[i].raw);
        const offset=tr.y[0]-tr.raw[0];
        assert(tr.y.every((y,j)=>Math.abs(y-tr.raw[j]-offset)<1e-12));
      }
      assert((await page.locator('#peak-plot .ytick text').allTextContents()).includes('//'));
      await page.locator('#peak-plot').screenshot({path:path.join(output,legacy?'legacy.png':'native.png')});
      const panel=page.locator('.rist-axis-control-button');
      if(!await panel.isVisible())await page.locator('.rist-ftir-tools-toggle').click();
      await panel.click();
      const top=page.locator('[data-axis="y"][data-axis-field="to"]');
      assert(Number(await top.inputValue())>.89);
      await top.fill('0.92');await page.locator('[data-axis-action="apply"]').click();
      const actualTop=await page.evaluate(async()=>{const g=document.getElementById('peak-plot');await g._ristStackReady;return g._ristFtirMeasuredY(g.layout.yaxis.range[1],true);});
      assert(Math.abs(actualTop-.92)<1e-8);
      const saved=await state();
      await page.locator('#sw-save-graph').click();
      await page.locator('.sw-card').first().waitFor();
      await zoom([4000,400]);assert.equal((await state()).fold.breaks.length,0);
      await toggle();assert.equal((await state()).fold,null);
      page.once('dialog',dialog=>dialog.accept());
      await page.locator('.sw-card').first().getByRole('button',{name:'다시 편집',exact:true}).click();
      await page.waitForFunction(()=>document.getElementById('peak-plot').layout.yaxis.ticktext?.includes('//'));
      const restored=await state();
      assert.deepEqual(restored.parents,folded.parents);assert.deepEqual(restored.ticks,saved.ticks);
      if(!legacy){
        const source=await page.evaluate(()=>{const g=document.getElementById('peak-plot');return JSON.parse(JSON.stringify({data:g.data,layout:g.layout}));});
        const input=path.join(output,'source.json'),html=path.join(output,'published.html');fs.writeFileSync(input,JSON.stringify(source));
        execFileSync(python,['-c','import json,sys;from pathlib import Path;from app.report.spectrum_viewer import viewer_html;Path(sys.argv[2]).write_text(viewer_html(title="FTIR",figure=json.loads(Path(sys.argv[1]).read_text()),slides=[],graph_only=True))',input,html]);
        const viewer=await page.context().newPage();viewer.on('pageerror',e=>errors.push(e.message));await viewer.goto(pathToFileURL(html).href);
        await viewer.waitForFunction(()=>document.getElementById('report-graph').layout?.yaxis.ticktext?.includes('//'));
        await viewer.evaluate(()=>Plotly.relayout(document.getElementById('report-graph'),{'xaxis.range':[4000,400]}));
        await viewer.waitForFunction(()=>!document.getElementById('report-graph').layout.yaxis.ticktext?.includes('//'));
        await viewer.close();
      }
      await page.evaluate(async()=>{const g=document.getElementById('peak-plot');await g._ristSetSignalMode('transmittance');await g._ristStackReady;});
      const trans=await state();assert.equal(trans.fold.breaks.length,1);assert(trans.parents[0].raw.every(y=>y>0));
      await page.evaluate(async()=>{const g=document.getElementById('peak-plot');await g._ristSetSignalMode('absorbance');await g._ristStackReady;});
      await toggle();const flat=await state();assert.equal(flat.fold,null);assert(!flat.ticks.includes('//'));
      flat.parents.forEach((tr,i)=>assert.deepEqual(tr.y,original.parents[i].y));
      await page.evaluate(async()=>{const g=document.getElementById('peak-plot'),meta=g.layout.meta.ristFtirStack;
        meta.overlayOffsets.absorbance[meta.sampleOrder[1]]=.19;
        g.dispatchEvent(new CustomEvent('rist-plot-data-replaced'));await g._ristStackReady;
      });
      const shifted=await state();await toggle();
      const compared=await state();assert.deepEqual(compared.parents,folded.parents);
      await toggle();assert.deepEqual((await state()).parents,shifted.parents);
      assert.deepEqual(errors,[]);await context.close();console.log(legacy?'legacy passed':'native + published HTML passed');
    }
    console.log('Artifacts: '+output);
  } finally {if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e,logs.slice(-1400),output);process.exitCode=1;});

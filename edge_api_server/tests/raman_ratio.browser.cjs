// Real Plotly + complete generated Raman page. Does not claim Windows/XP coverage.
// PYTHONPATH=. RIST_TEST_PYTHON=... NODE_PATH=<playwright modules> node tests/raman_ratio.browser.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const payload = JSON.parse(execFileSync(process.env.RIST_TEST_PYTHON || 'python3',['-c',
  'from app.path_bootstrap import add_project_package_paths; add_project_package_paths(); from app.raman_web import build_raman_page, plotly_asset_path; import json; print(json.dumps(dict(html=build_raman_page(),plotly=str(plotly_asset_path()))))'
],{encoding:'utf8',maxBuffer:32*1024*1024}));
const server=http.createServer((req,res)=>{
  if(req.url==='/raman/assets/plotly.min.js') {res.setHeader('Content-Type','application/javascript');res.end(fs.readFileSync(payload.plotly));}
  else if(req.url==='/raman') {res.setHeader('Content-Type','text/html; charset=utf-8');res.end(payload.html);}
  else {res.setHeader('Content-Type','application/json');res.end(JSON.stringify({items:[],libraries:[],authenticated:false}));}
});
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-ratio-browser-'));
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true,
    executablePath:process.env.RIST_BROWSER_EXECUTABLE || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try {
    const page=await browser.newPage({viewport:{width:1400,height:1000}});
    page.setDefaultTimeout(20000);
    const errors=[]; page.on('pageerror',e=>{errors.push(e.message); console.error('Browser error:',e.message);});
    await page.goto('http://127.0.0.1:'+server.address().port+'/raman');
    await page.waitForFunction(()=>document.getElementById('raman-plot')._ristRamanRatioInstalled);
    console.log('Loaded full Raman page');
    await page.evaluate(async()=>{
      const gd=document.getElementById('raman-plot');
      const parent=(g,name,y,color)=>({type:'scatter',mode:'lines',name,x:[0,100,200,300,400,500],y,
        line:{color,width:2},meta:{rist_sample_parent:true,rist_sample_group:g,rist_raman_stack_offset:0}});
      const peak=(g,name,x,y,base)=>({type:'scatter',mode:'markers',name,x:[x],y:[y],marker:{size:8},
        meta:{rist_sample_group:g,rist_peak:{sample_group:g,x,base_y:base,label:name},rist_raman_stack_offset:y-base}});
      await Plotly.react(gd,[parent('a','시료 A',[50,120,100,240,100,60],'#2563eb'),
        peak('a','D band',100,120,120),peak('a','G band',300,240,240),
        parent('b','시료 B',[60,121,110,180,110,70],'#e87500'),peak('b','D overlap',101,121,121)],
        {title:{text:'Raman ratio selection'},height:680,margin:{l:70,r:30,t:100,b:80},
          xaxis:{range:[0,500],title:{text:'Raman shift (cm⁻¹)'}},
          yaxis:{range:[0,300],title:{text:'Intensity (a.u.)'}},meta:{},annotations:[],shapes:[],showlegend:false},
        {responsive:true,displayModeBar:true,scrollZoom:true});
      gd.dispatchEvent(new CustomEvent('rist-plot-data-replaced'));
    });
    await page.waitForTimeout(300);
    console.log('Rendered Plotly fixture');
    await page.locator('#raman-plot').scrollIntoViewIfNeeded();
    const coords=curve=>page.evaluate(curve=>{
      const gd=document.getElementById('raman-plot'),r=gd.querySelector('.nsewdrag').getBoundingClientRect();
      return {x:r.left+gd._fullLayout.xaxis.d2p(gd.data[curve].x[0]),y:r.top+gd._fullLayout.yaxis.d2p(gd.data[curve].y[0])};
    },curve);
    const before=await page.evaluate(()=>Plotly.toImage(document.getElementById('raman-plot'),{format:'png',width:900,height:600}));
    console.log('Captured baseline export');
    await page.locator('.rist-raman-tools-toggle').click();
    await page.locator('.rist-raman-ratio-button').first().click();
    assert.equal(await page.locator('.rist-raman-tools-close').isVisible(),false);
    console.log('Entered ratio selection');
    const a=await coords(1);
    await page.mouse.move(a.x,a.y); await page.mouse.click(a.x,a.y);
    await page.locator('.rist-ratio-candidates:not([hidden])').waitFor();
    console.log('Overlap candidates visible');
    assert.equal(await page.locator('[data-candidate-key]').count(),2);
    await page.locator('[data-candidate-key]').filter({hasText:'시료 A'}).click();
    await page.waitForTimeout(120);
    assert.equal(await page.locator('[data-selection-sample]').inputValue(),'a');
    assert.match(await page.locator('[data-selection-numerator]').innerText(),/실제 강도 I = 120/);
    assert.equal(await page.locator('.rist-ratio-marker.numerator').isVisible(),true);
    assert.ok(await page.locator('.scatterlayer .rist-ratio-muted').count()>=2);
    const dim=await page.locator('.scatterlayer .rist-ratio-muted').first().evaluate(el=>getComputedStyle(el).opacity);
    assert.equal(dim,'0.18');
    const focused=await page.evaluate(()=>Plotly.toImage(document.getElementById('raman-plot'),{format:'png',width:900,height:600}));
    assert.equal(focused,before,'selection UI must not leak into PNG export');
    console.log('PNG export unchanged');
    const b=await coords(2); await page.mouse.move(b.x,b.y); await page.waitForTimeout(120);
    assert.match(await page.locator('.rist-ratio-hover-tip').innerText(),/실제 강도 I = 240/);
    await page.mouse.click(b.x,b.y); await page.waitForTimeout(200);
    assert.equal(await page.locator('.rist-ratio-marker.denominator').isVisible(),true);
    assert.match(await page.locator('[data-selection-result]').innerText(),/0.500/);
    const geometry=await page.locator('.rist-ratio-marker.numerator').boundingBox();
    assert.ok(Math.abs(geometry.x+geometry.width/2-a.x)<2);
    assert.ok(Math.abs(geometry.y+geometry.height/2-a.y)<2);
    await page.locator('[data-selection-swap]').click(); await page.waitForTimeout(150);
    assert.match(await page.locator('[data-selection-result]').innerText(),/2.000/);
    await page.locator('#raman-plot').screenshot({path:path.join(output,'desktop.png')});
    const buttonOverflow=await page.locator('.rist-ratio-selection button').evaluateAll(nodes=>nodes.filter(n=>n.scrollWidth>n.clientWidth+2).map(n=>n.textContent));
    assert.deepEqual(buttonOverflow,[]);
    await page.locator('[data-selection-close]').click(); await page.waitForTimeout(100);
    assert.equal(await page.locator('.scatterlayer .rist-ratio-muted').count(),0);
    await page.locator('#raman-plot .annotation').first().hover(); await page.waitForTimeout(100);
    assert.equal(await page.locator('.rist-ratio-marker.numerator').isVisible(),true);
    assert.equal(await page.locator('.rist-ratio-marker.denominator').isVisible(),true);
    await page.mouse.move(10,10); await page.waitForTimeout(100);
    assert.equal(await page.locator('.rist-ratio-marker.numerator').isVisible(),false);
    // Full native pointer path: two X bands, independent Raw maxima, explicit
    // confirmation, retained manual ratio, no sensitivity dependency or raw edits.
    const rawBefore=await page.evaluate(()=>JSON.stringify(document.getElementById('raman-plot').data));
    await page.locator('.rist-raman-tools-toggle').click();
    await page.locator('[data-ratio-batch]').click();
    async function dragRange(a,b) {
      const points=await page.evaluate(([a,b])=>{
        const gd=document.getElementById('raman-plot'),r=gd.querySelector('.nsewdrag').getBoundingClientRect();
        return {a:r.left+gd._fullLayout.xaxis.d2p(a),b:r.left+gd._fullLayout.xaxis.d2p(b),y:r.bottom-25};
      },[a,b]);
      await page.mouse.move(points.a,points.y);await page.mouse.down();await page.mouse.move(points.b,points.y,{steps:8});await page.mouse.up();
    }
    await dragRange(5,200);await dragRange(250,495);
    await page.locator('[data-batch-review]:not([hidden])').waitFor();
    assert.equal(await page.locator('[data-batch-row]').count(),2);
    assert.match(await page.locator('.rist-ratio-batch tbody').innerText(),/120/);
    await page.locator('.rist-ratio-batch tbody tr').first().hover();await page.waitForTimeout(100);
    await page.locator('#raman-plot').screenshot({path:path.join(output,'batch-desktop.png')});
    assert.deepEqual(await page.locator('.rist-ratio-batch button').evaluateAll(nodes=>nodes.filter(n=>n.scrollWidth>n.clientWidth+2).map(n=>n.textContent)),[]);
    await page.locator('[data-batch-apply]').click();await page.waitForTimeout(250);
    assert.equal(await page.evaluate(()=>JSON.stringify(document.getElementById('raman-plot').data)),rawBefore);
    const batch=await page.evaluate(()=>document.getElementById('raman-plot').layout.meta.ristRamanRatios);
    assert.equal(batch.length,3);assert.equal(batch.filter(r=>r.method==='raw-range-max').length,2);
    assert.equal(batch[1].numeratorPoint.intensity,120);assert.equal(batch[1].denominatorPoint.intensity,240);
    // Real shared history must undo the entire batch and redo both samples.
    await page.evaluate(()=>document.getElementById('raman-plot')._ristHistory.undo());await page.waitForTimeout(250);
    assert.equal(await page.evaluate(()=>document.getElementById('raman-plot').layout.meta.ristRamanRatios.length),1);
    await page.evaluate(()=>document.getElementById('raman-plot')._ristHistory.redo());await page.waitForTimeout(250);
    assert.equal(await page.evaluate(()=>document.getElementById('raman-plot').layout.meta.ristRamanRatios.length),3);
    console.log('Batch pointer ranges, original intensities, manual preservation and undo/redo passed');
    await page.setViewportSize({width:390,height:844});
    await page.waitForTimeout(150);
    await page.locator('.rist-raman-tools-toggle').click();
    await page.locator('.rist-raman-ratio-button').first().click();
    assert.equal(await page.locator('.rist-raman-tools-close').isVisible(),false);
    await page.locator('#raman-plot').scrollIntoViewIfNeeded();
    const sizes=await page.evaluate(()=>{
      const gd=document.getElementById('raman-plot'),g=gd.getBoundingClientRect(),p=gd.querySelector('.rist-ratio-selection').getBoundingClientRect();
      return {left:p.left-g.left,right:g.right-p.right};
    });
    assert.ok(sizes.left>=0 && sizes.right>=0);
    await page.locator('[data-selection-collapse]').click();
    assert.equal(await page.locator('[data-selection-body]').isVisible(),false);
    await page.locator('#raman-plot').screenshot({path:path.join(output,'mobile.png')});
    await page.locator('[data-selection-close]').click();
    await page.locator('.rist-raman-tools-toggle').click();await page.locator('[data-ratio-batch]').click();
    for(const [role,a,b] of [['numerator','5','200'],['denominator','250','495']]) {
      const inputs=page.locator('[data-batch-pick="'+role+'"]').locator('..').locator('input');
      await inputs.nth(0).fill(a);await inputs.nth(1).fill(b);await inputs.nth(1).press('Tab');
    }
    await page.locator('[data-batch-review]:not([hidden])').waitFor();
    const box=await page.locator('.rist-ratio-batch').boundingBox();assert.ok(box.x>=0&&box.x+box.width<=390);
    await page.locator('#raman-plot').screenshot({path:path.join(output,'batch-mobile.png')});
    await page.locator('[data-batch-close]').click();
    assert.equal(await page.locator('.rist-ratio-range-surface').isVisible(),false);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({browser:await browser.version(),passed:true,output,
      checks:['full-page overlap click','same-sample lock','true intensity','trace dimming','PNG export identical','completed label hover',
        'hover preview','persistent marker geometry','swap','button overflow','390px layout','collapse',
        'batch pointer drag','batch original intensities','batch undo/redo','batch mobile layout','no JS errors']}));
  } catch(error) {
    const page=browser.contexts()[0]?.pages()[0];
    if(page) {
      await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});
      console.error('Failure screenshot:',output);
      console.error(await page.evaluate(()=>Array.from(document.querySelectorAll('.rist-ratio-selection,.rist-ratio-candidates,.rist-raman-ratio-button,.rist-raman-tools-toggle')).map(e=>({
        className:e.className,hidden:e.hidden,display:getComputedStyle(e).display,rect:e.getBoundingClientRect().toJSON()
      }))));
    }
    throw error;
  } finally {await browser.close();server.close();}
})().catch(e=>{console.error(e);server.close();process.exitCode=1;});

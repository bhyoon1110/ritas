// Real wheel events + complete pages + real Plotly. Not Windows/Supermium OS coverage.
// PYTHONPATH=. RIST_TEST_PYTHON=... NODE_PATH=<playwright modules> node tests/stack_zoom.browser.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path'), http = require('node:http');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const bundle = JSON.parse(execFileSync(process.env.RIST_TEST_PYTHON || 'python3', ['-c',
  'from app.path_bootstrap import add_project_package_paths; add_project_package_paths(); from app.ftir_web import build_ftir_page, plotly_asset_path; from app.raman_web import build_raman_page; import json; print(json.dumps(dict(ftir=build_ftir_page(),raman=build_raman_page(),plotly=str(plotly_asset_path()))))'
], {encoding:'utf8', maxBuffer:32*1024*1024}));
const output = fs.mkdtempSync(path.join(os.tmpdir(), 'rist-stack-wheel-'));
const server = http.createServer((req,res) => {
  if (req.url.endsWith('/plotly.min.js')) {
    res.setHeader('Content-Type','application/javascript'); res.end(fs.readFileSync(bundle.plotly));
  } else if (bundle[req.url.slice(1)]) {
    res.setHeader('Content-Type','text/html; charset=utf-8'); res.end(bundle[req.url.slice(1)]);
  } else {
    res.setHeader('Content-Type','application/json'); res.end(JSON.stringify({items:[],libraries:[],authenticated:false}));
  }
});
(async () => {
  let browser;
  try {
    await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
    browser = await chromium.launch({headless:true,
      executablePath:process.env.RIST_BROWSER_EXECUTABLE || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
    for (const kind of ['raman','ftir']) {
      const page = await browser.newPage({viewport:{width:1400,height:1000}});
      const id = kind === 'raman' ? 'raman-plot' : 'peak-plot';
      const key = kind === 'raman' ? 'ristRamanStack' : 'ristFtirStack';
      const errors = [];
      page.on('pageerror',error => errors.push(error.message));
      page.setDefaultTimeout(10000);
      try {
        await page.goto('http://127.0.0.1:'+server.address().port+'/'+kind);
        await page.waitForFunction(()=>document.getElementById('sw-step-2')&&!document.getElementById('sw-step-2').disabled);
        await page.locator('#sw-step-2').click();
        await page.waitForFunction(id => document.getElementById(id)._ristAxisControlsInstalled,id);
        await page.evaluate(async ({id,key,kind}) => {
          const gd = document.getElementById(id);
          const traces = [[10,45,20,80,10],[20,65,30,70,20]].map((y,i) => ({
            type:'scatter',mode:'lines',name:'시료 '+(i+1),x:[100,200,300,400,500],y,customdata:y.slice(),
            line:{color:i ? '#e87500' : '#2563eb'},meta:{
              rist_sample_parent:true,rist_sample_group:'sample:'+i,
              rist_raman_stack_offset:0,rist_ftir_stack_offset_absorbance:0
            }
          }));
          await Plotly.react(gd,traces,Object.assign({},gd.layout,{
            title:{text:kind.toUpperCase()+' wheel zoom'},height:600,
            xaxis:{range:[0,600],fixedrange:false},yaxis:{range:[-20,100],fixedrange:false},
            annotations:[],shapes:[],meta:{[key]:{enabled:false,gap:1.2,unit:80,
              sampleOffsets:{'sample:0':0,'sample:1':0},sampleOrder:['sample:0','sample:1']}}
          }),gd._context);
          gd.dispatchEvent(new CustomEvent('rist-plot-data-replaced'));
          await gd._ristStackReady;
        },{id,key,kind});
        await page.locator('#'+id).scrollIntoViewIfNeeded();
        // Grid controls must change actual SVG gridlines, not only saved metadata.
        async function chooseGrid(major,minor) {
          if (!await page.locator('.rist-axis-control-button').isVisible()) await page.locator('.rist-'+kind+'-tools-toggle').click();
          await page.locator('.rist-axis-control-button').click();
          for (const axis of ['x','y']) {
            await page.locator('[data-axis-grid="'+axis+'"][data-grid-kind="major"]').setChecked(major);
            await page.locator('[data-axis-grid="'+axis+'"][data-grid-kind="minor"]').setChecked(minor);
          }
          await page.locator('[data-axis-action="apply"]').click();
          if(await page.locator('.rist-'+kind+'-tools-close').isVisible())await page.locator('.rist-'+kind+'-tools-close').click();
        }
        const gridCount = () => page.locator('#'+id+' .gridlayer path, #'+id+' .minor-gridlayer path').count();
        await chooseGrid(false,false);
        assert.equal(await gridCount(),0);
        await chooseGrid(true,true);
        assert((await gridCount())>0);
        const gridBefore = await gridCount();
        await page.locator('#'+kind+'-origin').uncheck();
        await page.locator('#'+kind+'-origin').check();
        await page.waitForFunction(id=>document.getElementById(id).layout.xaxis.showgrid===true,id);
        assert((await gridCount())>0);
        for(const width of [1400,420]) {
          await page.setViewportSize({width,height:1000});
          await page.locator('.rist-'+kind+'-tools-toggle').click();
          await page.locator('.rist-axis-control-button').click();
          await page.locator('.rist-axis-control-panel').screenshot({path:path.join(output,kind+'-grid-controls-'+width+'.png')});
          assert(await page.locator('.rist-axis-grid-checks').evaluateAll(rows=>rows.every(row=>row.scrollWidth<=row.clientWidth+1)));
          await page.locator('.rist-axis-control-close').click();
          if(await page.locator('.rist-'+kind+'-tools-close').isVisible())await page.locator('.rist-'+kind+'-tools-close').click();
        }
        await page.setViewportSize({width:1400,height:1000});
        const exported=await page.evaluate(async id=>{
          const g=document.getElementById(id), payload=g._ristBuildImageExportPayload(g.data,g.layout,1000,700);
          const png=await Plotly.toImage({data:payload.data,layout:payload.layout},{format:'png',width:1000,height:700});
          return {png,x:payload.layout.xaxis.showgrid,xMinor:payload.layout.xaxis.minor.showgrid,
            y:payload.layout.yaxis.showgrid,yMinor:payload.layout.yaxis.minor.showgrid};
        },id);
        assert(exported.x&&exported.xMinor&&exported.y&&exported.yMinor);
        fs.writeFileSync(path.join(output,kind+'-grid-export.png'),Buffer.from(exported.png.split(',')[1],'base64'));
        console.log(kind+': major/minor grids rendered, Origin retained, desktop/mobile controls fit, PNG exported ('+gridBefore+' SVG lines)');
        await page.locator('.rist-'+kind+'-tools-toggle').click();
        await page.getByRole('button',{name:'스택',exact:true}).click();
        await page.waitForFunction(({id,key}) => document.getElementById(id).layout.meta[key].enabled,{id,key});
        await page.locator('.rist-'+kind+'-tools-close').click();
        await page.waitForTimeout(150);
        const snapshot = () => page.evaluate(({id,key}) => {
          const gd = document.getElementById(id);
          return {x:gd.layout.xaxis.range.slice(),y:gd.layout.yaxis.range.slice(),
            raw:JSON.stringify(gd.data),stack:JSON.stringify(gd.layout.meta[key])};
        },{id,key});
        const reset = (lockX=false,lockY=false) => page.evaluate(async ({id,lockX,lockY}) => {
          await Plotly.relayout(document.getElementById(id),{
            'xaxis.range':[0,600],'yaxis.range':[-20,220],
            'xaxis.autorange':false,'yaxis.autorange':false,'xaxis.fixedrange':lockX,'yaxis.fixedrange':lockY
          });
        },{id,lockX,lockY});
        const wheel = async modifier => {
          const rect = await page.locator('#'+id+' .nsewdrag').boundingBox();
          await page.mouse.move(rect.x+rect.width*.4,rect.y+rect.height*.5);
          if (modifier) await page.keyboard.down(modifier);
          try {await page.mouse.wheel(0,-120); await page.waitForTimeout(200);}
          finally {if (modifier) await page.keyboard.up(modifier);}
        };
        const expectAxes = (before,after,x,y,stacked=true) => {
          assert.equal(JSON.stringify(before.x) !== JSON.stringify(after.x),x);
          assert.equal(JSON.stringify(before.y) !== JSON.stringify(after.y),y);
          if (stacked) assert.equal(after.y[0],before.y[0]);
          assert.equal(after.raw,before.raw); assert.equal(after.stack,before.stack);
        };
        for (const [modifier,x,y] of [[null,true,true],['Shift',true,false],['Control',false,true],[null,true,true]]) {
          await reset(); const before = await snapshot(); await wheel(modifier);
          expectAxes(before,await snapshot(),x,y);
          assert.equal(await page.locator('[data-wheel-default]').innerText(),'기본: X·Y축');
        }
        for (const [lockX,lockY] of [[true,false],[false,true],[true,true]]) {
          await reset(lockX,lockY); const before = await snapshot(); await wheel();
          expectAxes(before,await snapshot(),!lockX,!lockY);
        }
        await reset();
        const hint = page.locator('.rist-axis-wheel-help');
        // Tight width catches wrapping/layout shifts that DOM-only tests cannot.
        await hint.evaluate(el => {el.style.width='380px';});
        await page.waitForTimeout(2100);
        const hidden = await hint.boundingBox();
        await wheel(); assert.equal(await hint.getAttribute('aria-hidden'),'false');
        assert.deepEqual(await hint.boundingBox(),hidden);
        await page.screenshot({path:path.join(output,kind+'-stack.png'),fullPage:true});
        await page.waitForTimeout(2100);
        assert.equal(await hint.getAttribute('aria-hidden'),'true');
        assert.deepEqual(await hint.boundingBox(),hidden);
        await page.locator('.rist-'+kind+'-tools-toggle').click();
        await page.getByRole('button',{name:'스택',exact:true}).click();
        await page.waitForFunction(({id,key}) => !document.getElementById(id).layout.meta[key].enabled,{id,key});
        await page.locator('.rist-'+kind+'-tools-close').click();
        await page.waitForTimeout(150);
        await reset(); const before = await snapshot(); await wheel();
        expectAxes(before,await snapshot(),kind === 'ftir',true,false);
        assert.equal(await page.locator('[data-wheel-default]').innerText(),'기본: '+(kind === 'ftir' ? 'X·Y축' : 'Y축'));
        assert.deepEqual(await hint.boundingBox(),hidden);
        assert.deepEqual(errors,[]);
        console.log(kind+': stacked XY/Shift X/Ctrl Y/release, axis locks, raw integrity, 2s fixed-space hint, unstack policy passed');
      } catch(error) {
        await page.screenshot({path:path.join(output,kind+'-failure.png'),fullPage:true}); throw error;
      } finally {await page.close();}
    }
    console.log(JSON.stringify({browser:await browser.version(),passed:true,output}));
  } finally {
    if (browser) await browser.close();
    server.close();
  }
})().catch(error => {console.error(error); console.error('Screenshots:',output); process.exitCode=1;});

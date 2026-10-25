// Real Plotly geometry/PNG tests; macOS Chrome, not Windows/Supermium certification.
const assert = require('node:assert/strict'), fs = require('node:fs'), os = require('node:os');
const path = require('node:path'), http = require('node:http');
const {execFileSync} = require('node:child_process'), {chromium} = require('playwright');
const bundle = JSON.parse(execFileSync(process.env.RIST_TEST_PYTHON || 'python3', ['-c',
  'from app.path_bootstrap import add_project_package_paths; add_project_package_paths(); from app.ftir_web import build_ftir_page, plotly_asset_path; from app.raman_web import build_raman_page; import json; print(json.dumps(dict(ftir=build_ftir_page(),raman=build_raman_page(),plotly=str(plotly_asset_path()))))'
], {encoding:'utf8', maxBuffer:32*1024*1024}));
const output = fs.mkdtempSync(path.join(os.tmpdir(), 'rist-plot-size-'));
const server = http.createServer((req, res) => {
  if (req.url.endsWith('/plotly.min.js')) {
    res.setHeader('Content-Type','application/javascript');res.end(fs.readFileSync(bundle.plotly));
  } else if (bundle[req.url.slice(1)]) {
    res.setHeader('Content-Type','text/html; charset=utf-8');res.end(bundle[req.url.slice(1)]);
  } else {
    res.setHeader('Content-Type','application/json');res.end(JSON.stringify({items:[],libraries:[],authenticated:false}));
  }
});
(async () => {
  let browser;
  try {
    await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
    browser = await chromium.launch({headless:true, executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
    for (const kind of ['ftir','raman']) for (const viewport of [{width:1366,height:1000},{width:390,height:844}]) {
      const mobile = viewport.width === 390, page = await browser.newPage({viewport,isMobile:mobile,hasTouch:mobile});
      const id = kind === 'ftir' ? 'peak-plot' : 'raman-plot', label = kind+'-'+viewport.width, errors = [];
      page.setDefaultTimeout(15000);page.on('pageerror', error => errors.push(error.message));
      try {
        await page.goto('http://127.0.0.1:'+server.address().port+'/'+kind);
        await page.waitForFunction(() => document.getElementById('sw-step-2') && !document.getElementById('sw-step-2').disabled);
        await page.locator('#sw-step-2').click();
        await page.evaluate(async id => {
          const g = document.getElementById(id);
          const data = [0,1,2,3,4,5].map(i => ({name:'Sample '+i,type:'scatter',mode:'lines',showlegend:true,
            x:[100,150,200,250,300],y:[i+1,i+1,i+3,i+1,i+2],legendgroup:'sample:'+i,
            meta:{rist_sample_parent:true,rist_sample_group:'sample:'+i,rist_raman_stack_offset:0,
              rist_ftir_stack_offset_absorbance:0,rist_ftir_stack_offset_transmittance:0,
              ftir_signal_toggle:{absorbance_y:[i+1,i+1,i+3,i+1,i+2],transmittance_y:[30,30,20,30,25]}}}));
          await Plotly.react(g,data,Object.assign({},g.layout,{height:620,annotations:[],shapes:[],meta:{ristLegendVisible:true},showlegend:true,
            xaxis:{range:[0,400],automargin:true},yaxis:{range:[0,10],automargin:true}}),g._context);
          g.dispatchEvent(new CustomEvent('rist-plot-data-replaced'));await g._ristStackReady;
        },id);
        const click = async locator => mobile ? locator.tap() : locator.click();
        const button = page.locator('.rist-axis-control-button'), panel = page.locator('.rist-axis-control-panel');
        if (!await button.isVisible()) await click(page.locator('.rist-'+kind+'-tools-toggle'));
        await click(button);await panel.waitFor({state:'visible'});
        await panel.locator('[data-plot-width]').fill('1000');
        await panel.locator('[data-plot-ratio]').selectOption('4:3');
        await click(panel.locator('[data-plot-size-apply]'));
        await page.waitForFunction(id => document.getElementById(id).layout.margin.autoexpand === false,id);
        await click(panel.locator('.rist-axis-control-close'));
        // Geometry comes from Plotly, not a width/height mock. Font changes,
        // many legend rows and long names must not consume data-area width.
        const geometry = () => page.evaluate(id => {
          const g = document.getElementById(id), l = g._fullLayout, rect = g.getBoundingClientRect();
          return {width:l.width,height:l.height,plotWidth:l._size.w,plotHeight:l._size.h,
            left:l._size.l,right:l._size.r,top:l._size.t,bottom:l._size.b,
            rectWidth:rect.width,x:l.xaxis.range,y:l.yaxis.range};
        },id);
        const baseline = await geometry();
        assert.equal(baseline.right,24,label+': no 260px legend gutter');
        assert.equal(baseline.bottom,70,label+': no 150px mobile legend gutter');
        if (!mobile) { assert.equal(baseline.width,1000);assert.equal(baseline.height,750); }
        for (const font of [12,28]) for (const visible of [false,true]) {
          await page.evaluate(async ({id,font,visible}) => {
            const g = document.getElementById(id);
            await Plotly.restyle(g,{name:g.data.map((_,i) => '아주 긴 시료명 '+i+' / 반복 이름 '.repeat(8))});
            await Plotly.relayout(g,{'legend.font.size':font,showlegend:visible,'meta.ristLegendVisible':visible});
          },{id,font,visible});
          assert.deepEqual(await geometry(),baseline,label+': legend independent plot geometry');
          assert.equal(await page.locator('#'+id+' .legend').isVisible(),visible);
          assert.equal(await page.evaluate(id=>document.getElementById(id).layout.legend.font.size,id),font);
        }
        await page.evaluate(async id => {
          const g = document.getElementById(id);
          await Plotly.restyle(g,{name:g.data.map((_,i) => 'Sample '+i)});
          await Plotly.relayout(g,{'legend.font.size':12,'legend.x':0.1,'legend.y':0.8});
          await g._ristApplyResponsiveLayout();
        },id);
        assert.deepEqual(await page.evaluate(id => {
          const l=document.getElementById(id).layout;return [l.legend.x,l.legend.y];
        },id),[0.1,0.8],label+': preserve dragged legend position');
        assert.equal(await page.locator('#'+id+' .legend').isVisible(),true,label+': legend remains visible');
        await page.evaluate(async id=>{
          const g=document.getElementById(id);
          const more=Array.from({length:18},(_,i)=>Object.assign({},g.data[0],{name:'Extra '+i,
            legendgroup:'extra:'+i,meta:{rist_sample_parent:true,rist_sample_group:'extra:'+i}}));
          await Plotly.addTraces(g,more);
        },id);
        assert.deepEqual(await geometry(),baseline,label+': many legend rows must not shrink the plot');
        await page.evaluate(async id=>{
          const g=document.getElementById(id);await Plotly.deleteTraces(g,Array.from({length:18},(_,i)=>i+6));
        },id);
        await page.locator('#'+id).screenshot({path:path.join(output,label+'.png')});
        // The same helper used by report capture also migrates older snapshots.
        const rendered = await page.evaluate(async id => {
          const g = document.getElementById(id), s = g._ristPlotSize;
          const dimensions = s.exportDimensions({}), old = JSON.parse(JSON.stringify(g.layout));
          old.margin.r=260;old.margin.b=150;old.margin.autoexpand=true;
          old.legend.x=1.02;old.legend.y=1;
          const temp=document.createElement('div');document.body.appendChild(temp);
          try {
            await Plotly.newPlot(temp,g.data,Object.assign({},s.prepareExportLayout(old),dimensions,{autosize:false}),{staticPlot:true});
            const png=await Plotly.toImage(temp,Object.assign({format:'png',scale:1},dimensions));
            return {png,dimensions,plotWidth:temp._fullLayout._size.w,margin:temp._fullLayout._size};
          } finally { Plotly.purge(temp);temp.remove(); }
        },id);
        assert.deepEqual(rendered.dimensions,{width:1000,height:750});
        assert.equal(rendered.margin.r,24);assert.equal(rendered.margin.b,70);
        const png=Buffer.from(rendered.png.split(',')[1],'base64');
        assert.equal(png.readUInt32BE(16),1000);assert.equal(png.readUInt32BE(20),750);
        fs.writeFileSync(path.join(output,label+'-report.png'),png);
        assert.deepEqual(await geometry(),baseline,label+': export must not resize the live graph');
        if (!mobile) {
          const handle=page.locator('#'+id+' .rist-plot-resize-handle');
          await handle.scrollIntoViewIfNeeded();const box=await handle.boundingBox();
          const x=box.x+box.width/2,y=box.y+box.height/2;
          await page.mouse.move(x,y);await page.mouse.down();
          await page.mouse.move(x+80,y+40,{steps:5});await page.mouse.up();
          await page.waitForFunction(id=>{
            const g=document.getElementById(id);return g.layout.width===1080 && g.layout.height===790;
          },id);
          const dragged=await geometry();
          assert.equal(dragged.plotWidth,baseline.plotWidth+80);assert.equal(dragged.plotHeight,baseline.plotHeight+40);
          assert.deepEqual(dragged.x,baseline.x);assert.deepEqual(dragged.y,baseline.y);
          assert.equal(dragged.right,24);assert.equal(dragged.bottom,70);
        }
        assert.deepEqual(errors,[]);
        console.log(label+': real plot area, font/visibility/long names, legend position, resize, 1000x750 PNG passed');
      } catch (error) {
        await page.screenshot({path:path.join(output,label+'-failure.png'),fullPage:true});throw error;
      } finally { await page.close(); }
    }
    console.log(JSON.stringify({browser:browser.version(),output}));
  } finally { if (browser) await browser.close();server.close(); }
})().catch(error => {console.error(error);console.error(output);process.exitCode=1;});

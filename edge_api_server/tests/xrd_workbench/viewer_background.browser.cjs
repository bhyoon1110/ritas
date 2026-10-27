// Real Plotly rendering + actual self-contained export_html, not just a Plotly stub.
// Run with NODE_PATH=<Playwright modules> RIST_BUILD_PYTHON=<project Python> node this-file.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '../../app/xrd_workbench/web');
const output = fs.mkdtempSync(path.join(os.tmpdir(), 'rist-xrd-background-'));
const python = process.env.RIST_BUILD_PYTHON || 'python3';
const project = {id:'background-browser',samples:[],sources:[],warnings:[]};
for (let i = 0; i < 12; i++) {
  const x = Array.from({length:1001}, (_, j) => 10 + j * 0.02);
  project.samples.push({id:'s'+i,name:'RAW '+(i+1),
    measurement:{x,y:x.map(v => 20 + 2*v + i*10
      + Math.max(0,1-Math.abs(v-15)/0.15)*80 + Math.max(0,1-Math.abs(v-25)/0.15)*100),
      axis_kind:'two_theta',axis_unit:'deg',intensity_unit:'cps'},
    phases:[{id:'p'+i,name:'Phase '+i,card:String(i)}],
    observed_peaks:[15,25].map((v,j)=>({no:j+1,x:v,x_raw:String(v),height_raw:'999',page:1})),
    assignments:[15,25].map((v,j)=>({no:j+1,x:v,x_raw:String(v),page:1,matches:[{phase:'Phase '+i,hkl:'100'}]})),
    reference_peaks:[],sources:[],warnings:[]});
}
const env = {...process.env,PYTHONDONTWRITEBYTECODE:'1',PYTHONPATH:'.:..:../common:../sune:../rin:../lim:../ahn'};
const portable = execFileSync(python,['-c',
  'import sys,json; from app.xrd_workbench.render.viewer_export import export_html; p=json.load(sys.stdin); print(export_html(p,{"projectId":p["id"],"peakLines":True,"peakbar":False}))'],
  {cwd:path.resolve(__dirname,'../..'),env,input:JSON.stringify(project),encoding:'utf8',maxBuffer:32*1024*1024});
const plotly = execFileSync(python,['-c','from plotly.offline import get_plotlyjs; print(get_plotlyjs())'],
  {env,encoding:'utf8',maxBuffer:16*1024*1024});
fs.writeFileSync(path.join(output,'viewer.html'),portable);
const server = http.createServer((req,res)=>{
  const url = new URL(req.url,'http://localhost');
  if (url.pathname === '/portable') {
    const html = url.searchParams.get('mode') === 'chrome49'
      ? portable.replace(/<script type="module"[\s\S]*?<\/script>/g,'').replace(/<script nomodule>/g,'<script>') : portable;
    res.setHeader('Content-Type','text/html; charset=utf-8');res.end(html);
  } else if (url.pathname === '/online') {
    const html = fs.readFileSync(path.join(root,'index.html'),'utf8')
      .replace('<script type="module" src="/shared/shell.mjs"></script>','');
    res.setHeader('Content-Type','text/html; charset=utf-8');res.end(html);
  } else if (url.pathname === '/api/projects/fixture') {
    res.setHeader('Content-Type','application/json');res.end(JSON.stringify(project));
  } else if (url.pathname === '/vendor/plotly.min.js') {
    res.setHeader('Content-Type','application/javascript');res.end(plotly);
  } else if (/^\/(viewer|shared)\/[a-z_]+\.(mjs|css)$/.test(url.pathname)) {
    res.setHeader('Content-Type',url.pathname.endsWith('.css')?'text/css':'application/javascript');
    res.end(fs.readFileSync(path.join(root,url.pathname)));
  } else {res.setHeader('Content-Type','application/json');res.end('[]');}
});
function verify(data) {
  const measured = data.filter(t=>t.meta?.role==='measurement');
  const lines = data.filter(t=>t.meta?.role==='peak-line');
  assert.equal(measured.length,12,'all 12 RAW samples must be displayed');
  assert.equal(lines.length,12,'all 12 RAW samples must have guides');
  for (let i=0;i<12;i++) {
    const trace = measured.find(t=>t.meta.sampleId==='s'+i), line = lines.find(t=>t.meta.sampleId==='s'+i);
    const offset = trace.y[0] - project.samples[i].measurement.y[0];
    assert.ok(Math.abs(line.y[0] - (50+i*10+offset)) < 0.01);
    assert.ok(Math.abs(line.y[3] - (70+i*10+offset)) < 0.01);
    assert.deepEqual([line.y[1],line.y[4]],[130+i*10+offset,170+i*10+offset]);
    trace.y.forEach((v,j)=>assert.equal(v,project.samples[i].measurement.y[j]+offset));
  }
}
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser = await chromium.launch({headless:true,
    executablePath:process.env.RIST_BROWSER_EXECUTABLE || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const checks = [];
  try {
    for (const [route,width] of [['online?project=fixture',1440],['portable',1440],['portable?mode=chrome49',1440],['portable',390]]) {
      const page = await browser.newPage({viewport:{width,height:900}}), errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      await page.goto('http://127.0.0.1:'+server.address().port+'/'+route);
      await page.waitForFunction(()=>document.getElementById('plot').data?.some(t=>t.meta?.role==='measurement'));
      if (!(await page.locator('#peakLines').isChecked())) await page.locator('#peakLines').check();
      await page.waitForFunction(()=>document.getElementById('plot').data?.some(t=>t.meta?.role==='peak-line'));
      for (const button of ['quickOverlay','quickStack']) {
        await page.locator('#'+button).click();
        await page.waitForFunction(()=>document.getElementById('status').textContent.includes('12 시료 중 12 표시'));
        verify(await page.evaluate(()=>document.getElementById('plot').data));
      }
      await page.locator('#originStyle').check();
      await page.evaluate(()=>Plotly.relayout(document.getElementById('plot'),{'xaxis.range':[14,16]}));
      verify(await page.evaluate(()=>document.getElementById('plot').data));
      await page.locator('#resetView').click();
      const bounds = await page.evaluate(()=>{
        const p=document.getElementById('plot'),r=p._fullLayout.yaxis.range;
        return p.data.filter(t=>t.meta?.role==='measurement').every(t=>t.y.every(v=>v>=r[0]&&v<=r[1]));
      });
      assert.equal(bounds,true,'all RAW curves must fit the full view, not just six');
      await page.evaluate(()=>{
        const original=Plotly.toImage;
        Plotly.toImage=function(figure,options){window.exportedFigure=JSON.parse(JSON.stringify(figure));return original.call(this,figure,options);};
      });
      const downloadEvent=page.waitForEvent('download');
      await page.locator('#quickImageExport').click();
      const download=await downloadEvent;
      await download.saveAs(path.join(output,'export-'+checks.length+'.png'));
      verify(await page.evaluate(()=>window.exportedFigure.data));
      assert.match(await page.locator('#peakLineHint').textContent(),/추정 배경선.*원자료는 변경하지 않습니다/);
      await page.screenshot({path:path.join(output,'viewer-'+checks.length+'.png'),fullPage:true});
      assert.deepEqual(errors,[]);
      checks.push({route,width,raws:12,peakLines:24});
      await page.close();
    }
    console.log(JSON.stringify({browser:await browser.version(),checks,output}));
  } finally {await browser.close();server.close();}
})().catch(e=>{console.error(e);server.close();process.exitCode=1;});

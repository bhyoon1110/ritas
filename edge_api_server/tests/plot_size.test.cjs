// Shipped native/Chrome49 programs in a DOM double; not native Windows QA.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {JSDOM} = require('jsdom');
const clone = x => JSON.parse(JSON.stringify(x));
const tick = () => new Promise(resolve => setTimeout(resolve,100));
const pages = {};
for(const experiment of ['ftir','raman']) {
  pages[experiment] = execFileSync(process.env.RIST_TEST_PYTHON || 'python3',['-c',
    `from app.path_bootstrap import add_project_package_paths; add_project_package_paths(); from app.${experiment}_web import build_${experiment}_page; print(build_${experiment}_page())`],{encoding:'utf8'});
}
const assetRoot = path.resolve(__dirname,'../app/static/chrome49');
const manifest = JSON.parse(fs.readFileSync(path.join(assetRoot,'manifest.json'),'utf8'));

function setup(t, experiment, mode) {
  const html=pages[experiment];
  const scripts=[...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
  const dom=new JSDOM(html,{runScripts:'outside-only',pretendToBeVisual:true,url:'http://rist.test/'+experiment});
  t.after(()=>{dom.window.dispatchEvent(new dom.window.Event('pagehide'));dom.window.close();});
  const w=dom.window,d=w.document,gd=d.getElementById(experiment==='ftir'?'peak-plot':'raman-plot');
  let available=1600,captures=0;
  Object.defineProperty(gd.parentElement,'clientWidth',{get:()=>available});
  Object.defineProperty(gd,'clientWidth',{get:()=>parseFloat(gd.style.width)||available});
  Object.defineProperty(gd,'clientHeight',{get:()=>parseFloat(gd.style.height)||720});
  gd.layout={width:1600,height:720,meta:{unrelated:'keep'},xaxis:{range:[4000,400],fixedrange:true},
    yaxis:{range:[-12,300],fixedrange:false},margin:{l:60,t:82,b:70,r:260}};
  gd.data=[{x:[4000,3500,400],y:[-12,300,40],name:'Sample',meta:{rist_sample_parent:true}}];
  gd._fullLayout=clone(gd.layout);gd._context={};
  gd._ristHistory={capture:()=>captures++};
  const events={},updates=[],exports=[];
  gd.on=(name,fn)=>{(events[name] ||= []).push(fn);return gd;};
  const emit=(name,ev)=>{for(const fn of events[name]||[])fn(ev);};
  w.Plotly={relayout:async(_,patch)=>{
    updates.push(clone(patch));
    for(const [key,value] of Object.entries(patch)) {
      const parts=key.split('.');let target=gd.layout;
      for(const part of parts.slice(0,-1))target=target[part] ||= {};
      target[parts.at(-1)]=clone(value);
    }
    gd._fullLayout=clone(gd.layout);emit('plotly_afterplot');emit('plotly_relayout',patch);
  },newPlot:async(target,data,layout)=>{target.data=data;target.layout=layout;},
  downloadImage:async(target,options)=>{exports.push({layout:clone(target.layout),options:clone(options)});},
  toImage:async(target,options)=>{exports.push({layout:clone(target.layout),options:clone(options)});return 'data:image/png;base64,test';},
  purge:()=>{}};
  if(mode==='chrome49')w.eval(fs.readFileSync(path.join(assetRoot,manifest.runtime),'utf8'));
  function program(token) {
    const source=scripts.find(s=>s.includes(token));assert(source,token);
    if(mode!=='chrome49')return source;
    const filename=manifest.scripts[createHash('sha256').update(source.trim()).digest('hex')];
    assert(filename,'rebuild compatibility assets: '+token);
    return fs.readFileSync(path.join(assetRoot,filename),'utf8');
  }
  w.eval(program('var axisLocksEnabled'));
  w.eval(program('var ratios ='));
  // Real page-specific responsive layout and report capture, not a test reimplementation.
  const upload=scripts.find(s=>s.includes('function applyResponsiveLayout()'));
  const responsive=upload.match(/function applyResponsiveLayout\(\) \{[\s\S]*?gd\._ristApplyResponsiveLayout = applyResponsiveLayout;/)[0];
  const capture=upload.match(/async function captureReportFigureImage\(figure\) \{[\s\S]*?\n  \}/)[0];
  w.eval('(function(){var gd=document.getElementById('+JSON.stringify(gd.id)+');'+responsive+capture+';gd.testCapture=captureReportFigureImage;})();');
  const q=s=>d.querySelector(s);
  async function set(width,ratio) {
    q('[data-plot-width]').value=width;q('[data-plot-ratio]').value=ratio;
    q('[data-plot-size-apply]').click();await tick();
  }
  return {w,d,gd,q,updates,exports,set,emit,captures:()=>captures,
    resize:async width=>{available=width;w.dispatchEvent(new w.Event('resize'));await tick();},
    run:token=>w.eval(program(token))};
}

for(const mode of ['native','chrome49'])for(const experiment of ['ftir','raman']) {
  const prefix=`${experiment}/${mode}`;
  test(`${prefix}: 4:3,16:9,1:1 size only the canvas, preserving raw data/axis locks/ranges`,async t=>{
    const h=setup(t,experiment,mode),raw=clone(h.gd.data),x=clone(h.gd.layout.xaxis),y=clone(h.gd.layout.yaxis);
    for(const [ratio,height] of [['4:3',900],['16:9',675],['1:1',1200]]) {
      await h.set('1200',ratio);
      assert.equal(h.gd.layout.width,1200);assert.equal(h.gd.layout.height,height);
      assert.equal(h.gd.style.getPropertyPriority('height'),'important');
      assert.equal(h.gd.style.minHeight,'0');assert(h.gd.classList.contains('rist-size-compact'));
      assert.deepEqual(clone(h.gd.data),raw);assert.deepEqual(clone(h.gd.layout.xaxis),x);assert.deepEqual(clone(h.gd.layout.yaxis),y);
      assert.equal(h.gd.layout.meta.unrelated,'keep');
      assert.deepEqual(clone(h.gd._ristPlotSize.exportDimensions({})),{width:1200,height});
    }
    assert.equal(h.captures(),3);
  });
  test(`${prefix}: mobile clamp keeps graph usable and exports the requested ratio, resize recovers width`,async t=>{
    const h=setup(t,experiment,mode);await h.set('1200','16:9');
    await h.resize(340);
    assert.equal(h.gd.layout.width,340);assert.equal(h.gd.layout.height,420);
    assert.equal(h.gd.layout.legend.orientation,'h');
    assert.deepEqual(clone(h.gd._ristPlotSize.exportDimensions({})),{width:1200,height:675});
    await h.resize(1800);assert.equal(h.gd.layout.width,1200);assert.equal(h.gd.layout.height,675);
    assert.equal(h.gd.layout.legend.orientation,'v');
    const count=h.updates.length;await tick();assert.equal(h.updates.length,count,'no relayout loop');
  });
  test(`${prefix}: blank width fills available space, width-only works, auto restores page CSS`,async t=>{
    const h=setup(t,experiment,mode),original=h.gd.style.cssText;
    await h.set('','4:3');assert.equal(h.gd.layout.width,1600);assert.equal(h.gd.layout.height,1200);
    await h.set('800','auto');assert.equal(h.gd.layout.width,800);assert(h.gd.layout.height>=420);
    h.q('[data-plot-size-reset]').click();await tick();
    assert.equal(h.gd.style.cssText,original);assert.equal(h.gd.layout.width,null);assert.equal(h.gd.layout.autosize,true);
    assert(!h.gd.classList.contains('rist-size-compact'));
    const fallback={width:900,height:640};assert.equal(h.gd._ristPlotSize.exportDimensions(fallback),fallback);
  });
  test(`${prefix}: invalid widths never change layout or history`,async t=>{
    const h=setup(t,experiment,mode);
    for(const width of ['319','2401','900.5','-100']) {
      await h.set(width,'4:3');assert.equal(h.q('[data-plot-width]').getAttribute('aria-invalid'),'true');
    }
    assert.equal(h.captures(),0);assert.equal(h.gd.layout.meta.ristPlotSize,undefined);
  });
  test(`${prefix}: restored metadata resizes DOM, missing/invalid metadata resets, reanalysis preserves only sizing`,async t=>{
    const h=setup(t,experiment,mode);await h.set('960','4:3');
    const saved=clone(h.gd.layout);
    const next={meta:{newAnalysis:true}};
    h.gd._ristPlotSize.preserve(next);assert.equal(next.meta.ristPlotSize.width,960);
    assert.equal(next.meta.newAnalysis,true);assert.equal(next.meta.unrelated,undefined);
    delete h.gd.layout.meta.ristPlotSize;
    h.gd.dispatchEvent(new h.w.CustomEvent('rist-history-restored'));await tick();
    assert.equal(h.q('[data-plot-width]').value,'');assert.equal(h.gd.layout.width,null);
    h.gd.layout=saved;
    h.gd.dispatchEvent(new h.w.CustomEvent('rist-plot-data-replaced'));await tick();
    assert.equal(h.gd.style.width,'960px');assert.equal(h.gd.style.height,'720px');
    assert.equal(h.q('[data-plot-ratio]').value,'4:3');
    h.gd.layout.meta.ristPlotSize={width:1e9,ratio:'__proto__'};
    h.gd.dispatchEvent(new h.w.CustomEvent('rist-history-restored'));await tick();
    assert.equal(h.q('[data-plot-ratio]').value,'auto');assert.equal(h.gd.style.width,'100%');
  });
  test(`${prefix}: image download and report PNG use selected dimensions without changing live graph`,async t=>{
    const h=setup(t,experiment,mode);await h.set('1200','16:9');
    h.run('function buildImageExportPayload');
    const snapshot=clone(h.gd.layout);
    const download=h.d.querySelector('[data-fmt="png"]');assert(download);download.click();await tick();
    assert.equal(h.exports.length,1);assert.equal(h.exports[0].options.width,1200);assert.equal(h.exports[0].options.height,675);
    await h.gd.testCapture({data:h.gd.data,layout:h.gd.layout});
    assert.equal(h.exports.length,2);assert.equal(h.exports[1].options.width,1200);assert.equal(h.exports[1].layout.height,675);
    assert.deepEqual(clone(h.gd.layout),snapshot);
  });
}

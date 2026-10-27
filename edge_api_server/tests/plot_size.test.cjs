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
  // JSDOM has no layout; model a visible graph unless the test hides it.
  gd.getClientRects=()=>gd.hidden ? [] : [{width:1600,height:720}];
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
  downloadImage:async(target,options)=>{exports.push({data:clone(target.data),layout:clone(target.layout),options:clone(options)});},
  toImage:async(target,options)=>{exports.push({data:clone(target.data),layout:clone(target.layout),options:clone(options)});return 'data:image/png;base64,test';},
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
  w.eval(program('window.RistFileProgress = '));
  // Synthetic DOM events have no browser-managed active pointer. Model its
  // capture lifecycle explicitly, including the Chrome49 pointer polyfill.
  const handle=gd.querySelector('.rist-plot-resize-handle'),captured=new Set();
  handle.setPointerCapture=id=>captured.add(id);
  handle.hasPointerCapture=id=>captured.has(id);
  handle.releasePointerCapture=id=>captured.delete(id);
  // Real page-specific responsive layout and report capture, not a test reimplementation.
  const upload=scripts.find(s=>s.includes('function applyResponsiveLayout()'));
  const responsive=upload.match(/function applyResponsiveLayout\(\) \{[\s\S]*?gd\._ristApplyResponsiveLayout = applyResponsiveLayout;/)[0];
  const capture=upload.match(/async function captureReportFigureImage\(figure\) \{[\s\S]*?\n  \}/)[0];
  w.eval('(function(){var workTemplate=null;var gd=document.getElementById('+JSON.stringify(gd.id)+');'+responsive+capture+';gd.testCapture=captureReportFigureImage;})();');
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
  test(`${prefix}: hidden workflow stages skip relayout until the graph is visible again`,async t=>{
    const h=setup(t,experiment,mode);
    h.gd.hidden=true;
    await h.gd._ristApplyResponsiveLayout();
    assert.equal(h.updates.length,0);
    h.gd.hidden=false;
    await h.gd._ristApplyResponsiveLayout();
    assert(h.updates.length>0);
  });
  test(`${prefix}: 4:3,16:9,1:1 size only the canvas, preserving raw data/axis locks/ranges`,async t=>{
    const h=setup(t,experiment,mode),raw=clone(h.gd.data),x=clone(h.gd.layout.xaxis),y=clone(h.gd.layout.yaxis);
    for(const [ratio,height] of [['4:3',900],['16:9',675],['1:1',1200]]) {
      await h.set('1200',ratio);
      assert.equal(h.gd.layout.width,1200);assert.equal(h.gd.layout.height,height);
      assert.equal(h.gd.style.getPropertyPriority('height'),'important');
      assert.equal(h.gd.style.minHeight,'0');assert(h.gd.classList.contains('rist-size-compact'));
      assert.deepEqual(clone(h.gd.data),raw);assert.deepEqual(clone(h.gd.layout.xaxis),x);assert.deepEqual(clone(h.gd.layout.yaxis),y);
      assert.equal(h.gd.layout.meta.unrelated,'keep');
      assert.equal(h.gd.layout.margin.r,24);assert.equal(h.gd.layout.margin.b,70);
      assert.equal(h.gd.layout.margin.autoexpand,false);
      assert.equal(h.gd.layout.legend.x,0.02);assert.equal(h.gd.layout.legend.y,0.98);
      assert.deepEqual(clone(h.gd._ristPlotSize.exportDimensions({})),{width:1200,height});
    }
    assert.equal(h.captures(),3);
  });
  test(`${prefix}: mobile clamp keeps graph usable and exports the requested ratio, resize recovers width`,async t=>{
    const h=setup(t,experiment,mode);await h.set('1200','16:9');
    await h.resize(340);
    assert.equal(h.gd.layout.width,340);assert.equal(h.gd.layout.height,420);
    assert.equal(h.gd.layout.legend.orientation,'v');
    assert.equal(h.gd.layout.margin.b,70);assert.equal(h.gd.layout.margin.autoexpand,false);
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
    assert.equal(h.gd.layout.margin.r,260);assert.equal(h.gd.layout.margin.autoexpand,true);
    assert(!h.gd.classList.contains('rist-size-compact'));
    const fallback={width:900,height:640};assert.equal(h.gd._ristPlotSize.exportDimensions(fallback),fallback);
  });
  test(`${prefix}: an automatic restored workspace drops its stale desktop width on mobile`,async t=>{
    const h=setup(t,experiment,mode);
    h.gd.layout.width=1280;h.gd.layout.autosize=false;
    h.gd.layout.meta.ristPlotSize={width:null,ratio:'auto'};
    await h.resize(390);
    // In auto mode the page's resize listener owns resizing, not the custom-size controller.
    await h.gd._ristApplyResponsiveLayout();
    assert.equal(h.gd.layout.width,null);
    assert.equal(h.gd.layout.autosize,true);
    assert.equal(h.gd.layout.legend.orientation,'h');
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
    h.gd.data[0].line={width:3.5,dash:'dash',color:'#123456'};
    h.run('function buildImageExportPayload');
    const snapshot=clone(h.gd.layout);
    const download=h.d.querySelector('[data-fmt="png"]');assert(download);download.click();await tick();
    assert.equal(h.exports.length,1);assert.equal(h.exports[0].options.width,1200);assert.equal(h.exports[0].options.height,675);
    await h.gd.testCapture({data:h.gd.data,layout:h.gd.layout});
    assert.equal(h.exports.length,2);assert.equal(h.exports[1].options.width,1200);assert.equal(h.exports[1].layout.height,675);
    for(const output of h.exports) assert.deepEqual(output.data[0].line,{width:3.5,dash:'dash',color:'#123456'});
    assert.deepEqual(clone(h.gd.layout),snapshot);
  });
  test(`${prefix}: legend text, font, visibility and inside position do not reserve canvas space`,async t=>{
    const h=setup(t,experiment,mode);await h.set('960','4:3');
    await h.w.Plotly.relayout(h.gd,{'legend.x':0.18,'legend.y':0.72,'legend.font.size':32});
    h.gd.data[0].name='아주 긴 시료 이름 '.repeat(10);
    for(const showlegend of [false,true]) {
      await h.w.Plotly.relayout(h.gd,{showlegend});
      for(const available of [340,1600]) {
        await h.resize(available);
        assert.equal(h.gd.layout.margin.r,24);assert.equal(h.gd.layout.margin.b,70);
        assert.equal(h.gd.layout.margin.autoexpand,false);
        assert.equal(h.gd.layout.legend.x,0.18);assert.equal(h.gd.layout.legend.y,0.72);
        assert.deepEqual(clone(h.gd._ristPlotSize.exportDimensions({})),{width:960,height:720});
      }
    }
    const patch=h.gd._ristPlotSize.applyLayout({'legend.x':0.85,'legend.y':0.65,'legend.xanchor':'right'});
    assert.equal(patch['legend.x'],0.85);assert.equal(patch['legend.y'],0.65,'inside work-template pair survives');
  });
  test(`${prefix}: older sized report snapshots lose external legend gutters without mutation`,async t=>{
    const h=setup(t,experiment,mode);await h.set('1200','16:9');
    const layout=clone(h.gd.layout);
    Object.assign(layout.margin,{r:260,b:150,autoexpand:true});
    Object.assign(layout.legend,{x:1.02,y:1,xanchor:'left',yanchor:'top'});
    const before=clone(layout);
    await h.gd.testCapture({data:h.gd.data,layout});
    const output=h.exports[0].layout;
    assert.equal(output.width,1200);assert.equal(output.height,675);
    assert.equal(output.margin.r,24);assert.equal(output.margin.b,70);assert.equal(output.margin.autoexpand,false);
    assert.equal(output.legend.x,0.02);assert.equal(output.legend.y,0.98);
    assert.deepEqual(layout,before);assert.deepEqual(output.xaxis,before.xaxis);assert.deepEqual(output.yaxis,before.yaxis);
    const automatic={meta:{ristPlotSize:{ratio:'auto',width:null}},margin:{r:260}};
    assert.equal(h.gd._ristPlotSize.prepareExportLayout(automatic),automatic);
  });
  test(`${prefix}: extra landscape/portrait presets, bounded exports and numeric height persist`,async t=>{
    const h=setup(t,experiment,mode);
    for(const [ratio,height] of [['3:2',800],['5:4',960],['16:10',750],['2:1',600],['21:9',514],['3:4',1600],['2:3',1800],['9:16',2133]]) {
      await h.set('1200',ratio);
      assert.equal(h.gd.layout.height,height);
      assert.deepEqual(clone(h.gd._ristPlotSize.exportDimensions({})),{width:1200,height});
    }
    await h.set('2400','9:16');
    assert.deepEqual(clone(h.gd._ristPlotSize.exportDimensions({})),{width:1350,height:2400});
    h.q('[data-plot-height]').value='820';h.q('[data-plot-height]').dispatchEvent(new h.w.Event('input'));
    assert.equal(h.q('[data-plot-ratio]').value,'custom');
    await h.set('1100','custom');
    const value={width:1100,height:820,ratio:'custom'};
    assert.deepEqual(clone(h.gd.layout.meta.ristPlotSize),value);
    const next={};h.gd._ristPlotSize.preserve(next);assert.deepEqual(clone(next.meta.ristPlotSize),value);
    await h.resize(340);assert.equal(h.gd.layout.height,420);
    assert.deepEqual(clone(h.gd._ristPlotSize.exportDimensions({})),{width:1100,height:820});
    await h.gd.testCapture({data:h.gd.data,layout:h.gd.layout});
    assert.equal(h.exports[0].layout.width,1100);assert.equal(h.exports[0].layout.height,820);
    await h.resize(1600);assert.equal(h.gd.layout.height,820);
    const captures=h.captures();
    for(const height of ['', '239','2401','820.5']) {
      h.q('[data-plot-height]').value=height;await h.set('1100','custom');
      assert.equal(h.q('[data-plot-height]').getAttribute('aria-invalid'),'true');
    }
    assert.equal(h.captures(),captures);
  });
  test(`${prefix}: resize preview commits once, preserves data/ranges, cancels and supports aspect lock/keyboard/touch`,async t=>{
    const h=setup(t,experiment,mode);await h.set('1000','4:3');
    const raw=clone(h.gd.data), axes=clone([h.gd.layout.xaxis,h.gd.layout.yaxis]);
    const handle=h.q('.rist-plot-resize-handle');
    const mouse=(target,type,x,y)=>target.dispatchEvent(new h.w.MouseEvent(type,{bubbles:true,cancelable:true,clientX:x,clientY:y,button:0}));
    mouse(handle,'mousedown',1000,750);mouse(h.w,'mousemove',1200,850);
    assert.equal(h.q('.rist-plot-resize-preview span').textContent,'1200 × 850 px');
    assert.equal(h.captures(),1);assert.equal(h.gd.layout.width,1000);
    mouse(h.w,'mouseup',1200,850);await tick();
    assert.equal(h.q('.rist-plot-resize-preview'),null);assert.equal(h.captures(),2);
    assert.deepEqual(clone(h.gd.layout.meta.ristPlotSize),{width:1200,height:850,ratio:'custom'});
    for(const cancel of ['escape','blur','pagehide']) {
      mouse(handle,'mousedown',1200,850);mouse(h.w,'mousemove',1100,700);
      if(cancel==='escape')h.w.dispatchEvent(new h.w.KeyboardEvent('keydown',{key:'Escape',cancelable:true}));
      else h.w.dispatchEvent(new h.w.Event(cancel));
      mouse(h.w,'mouseup',1100,700);await tick();assert.equal(h.captures(),2);assert.equal(h.q('.rist-plot-resize-preview'),null);
    }
    h.q('[data-plot-size-lock]').checked=true;
    mouse(handle,'mousedown',1200,850);mouse(h.w,'mousemove',1440,800);mouse(h.w,'mouseup',1440,800);await tick();
    assert.equal(h.gd.layout.width,1440);assert.equal(h.gd.layout.height,1020);
    h.q('[data-plot-size-lock]').checked=false;
    handle.dispatchEvent(new h.w.KeyboardEvent('keydown',{key:'ArrowDown',shiftKey:true,cancelable:true,bubbles:true}));await tick();
    assert.equal(h.gd.layout.height,1021);
    function touch(target,type,x,y) {
      const e=h.w.PointerEvent ? new h.w.PointerEvent(({touchstart:'pointerdown',touchmove:'pointermove',touchend:'pointerup'})[type],
        {bubbles:true,cancelable:true,clientX:x,clientY:y,pointerType:'touch',pointerId:7,button:0}) : new h.w.Event(type,{bubbles:true,cancelable:true});
      e.touches=[{clientX:x,clientY:y}];target.dispatchEvent(e);return e;
    }
    assert.equal(handle.getAttribute('touch-action'),'none');
    assert(touch(handle,'touchstart',1440,1021).defaultPrevented);
    touch(h.w,'touchmove',1400,900);touch(h.w,'touchend',1400,900);await tick();
    assert.equal(h.gd.layout.height,900);assert.equal(h.gd.layout.width,1400);
    const e=touch(h.w,'touchmove',1400,850);assert.equal(e.defaultPrevented,false,'normal page scrolling after drag');
    assert.deepEqual(clone(h.gd.data),raw);assert.deepEqual(clone([h.gd.layout.xaxis,h.gd.layout.yaxis]),axes);
  });
}

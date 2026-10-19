// Execute shipped FTIR/Raman navigation, Crop and edit-mode programs.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const {JSDOM}=require('jsdom');
const pages=JSON.parse(execFileSync(process.env.RIST_TEST_PYTHON||'python3',['-c',
  'from app.path_bootstrap import add_project_package_paths; add_project_package_paths(); from app.ftir_web import build_ftir_page; from app.raman_web import build_raman_page; import json; print(json.dumps(dict(ftir=build_ftir_page(),raman=build_raman_page())))'
],{encoding:'utf8',maxBuffer:32*1024*1024}));
const clone=x=>JSON.parse(JSON.stringify(x));
const tick=()=>new Promise(r=>setTimeout(r,40));
function setup(t,kind,tag='button') {
  const dom=new JSDOM(pages[kind],{runScripts:'outside-only',pretendToBeVisual:true,url:'http://localhost/'+kind});
  t.after(()=>dom.window.close());const w=dom.window,d=w.document,gd=d.getElementById(kind==='ftir'?'peak-plot':'raman-plot');
  const scripts=[...pages[kind].matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
  const run=key=>{const src=scripts.find(s=>s.includes(key));assert.ok(src,key);w.eval(src);};
  gd.data=[];gd.layout={dragmode:'pan',xaxis:{range:[0,100]},yaxis:{range:[0,100]},annotations:[],shapes:[],meta:{}};
  gd._context={scrollZoom:true};const events={};
  gd.on=(key,fn)=>{(events[key]||=[]).push(fn);return gd;};const emit=(key,value)=>{(events[key]||[]).forEach(fn=>fn(value));};
  const sync=()=>{gd._fullLayout={...clone(gd.layout),_size:{l:40,t:100,w:800,h:400}};};sync();
  gd.getBoundingClientRect=()=>({left:0,top:0,width:900,height:650});
  w.Plotly={relayout:async(_,patch)=>{
    for(const [key,value] of Object.entries(patch)) {
      const parts=key.split('.');let target=gd.layout;
      for(const part of parts.slice(0,-1)) target=target[part]||={};
      target[parts.at(-1)]=clone(value);
    }sync();emit('plotly_relayout',patch);emit('plotly_afterplot');
  },restyle:async()=>{},Plots:{resize:()=>{}},react:async(_,data,layout)=>{gd.data=data;gd.layout=layout;sync();emit('plotly_afterplot');}};
  gd._ristHistory={capture:()=>{}};
  const bar=d.createElement('div');bar.className='modebar-group';gd.appendChild(bar);
  for(const mode of ['pan','zoom']) {
    const b=d.createElement(tag);b.className='modebar-btn';b.setAttribute('data-title',mode==='pan'?'Pan':'Zoom');
    b.setAttribute('data-attr','dragmode');b.setAttribute('data-val',mode);bar.appendChild(b);
    b.onclick=()=>w.Plotly.relayout(gd,{dragmode:mode});
  }
  run('gd._ristEditModeToggleInstalled');
  const custom=(name,detail)=>gd.dispatchEvent(new w.CustomEvent(name,{detail}));
  return {w,d,gd,run,custom,emit,q:s=>gd.querySelector(s)};
}
for(const kind of ['ftir','raman']) {
  test(kind+': generated page starts in Pan and removes only native Box Select',()=>{
    assert.match(pages[kind],/"dragmode":\s*"pan"/);
    assert.match(pages[kind],/"modeBarButtonsToRemove":\s*\["select2d"\]/);
  });
  for(const tag of ['a','button']) test(kind+': edit off returns Pan even after Zoom; '+tag+' modebar gating',async t=>{
    const f=setup(t,kind,tag), toggle=f.q('.rist-edit-mode-toggle'), zoom=f.q('[data-val="zoom"]');
    zoom.click();await tick();assert.equal(f.gd.layout.dragmode,'zoom');
    toggle.click();await tick();assert.equal(f.gd.layout.dragmode,false);assert.equal(zoom.getAttribute('aria-disabled'),'true');
    zoom.click();await tick();assert.equal(f.gd.layout.dragmode,false);
    toggle.click();await tick();assert.equal(f.gd.layout.dragmode,'pan');assert.equal(f.gd._context.scrollZoom,true);
    assert.equal(zoom.getAttribute('aria-disabled'),'false');
    zoom.click();await tick();assert.equal(f.gd.layout.dragmode,'zoom','explicit Zoom must still work');
  });
  test(kind+': Crop still selects with hidden Box Select and restores Pan on apply/Escape',async t=>{
    const f=setup(t,kind);f.run('var axisLocksEnabled');
    await f.w.Plotly.relayout(f.gd,{dragmode:'zoom'});
    f.q("[data-axis-action='crop']").click();await tick();assert.equal(f.gd.layout.dragmode,'select');
    f.emit('plotly_selected',{range:{x:[20,80],y:[10,90]}});await tick();
    assert.equal(f.gd.layout.dragmode,'pan');assert.deepEqual(clone(f.gd.layout.xaxis.range),[20,80]);
    f.q("[data-axis-action='crop']").click();await tick();assert.equal(f.gd.layout.dragmode,'select');
    f.w.document.dispatchEvent(new f.w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));await tick();
    assert.equal(f.gd.layout.dragmode,'pan');assert.equal(f.gd._ristAxisCropActive,false);
  });
  test(kind+': restore/reset cannot revive saved Box Select or disabled drag mode',async t=>{
    const f=setup(t,kind);
    for(const mode of ['select','lasso','zoom',false]) {
      f.gd.layout.dragmode=mode;f.custom('rist-plot-data-replaced');await tick();assert.equal(f.gd.layout.dragmode,'pan');
      f.gd.layout.dragmode=mode;f.custom('rist-history-restored');await tick();assert.equal(f.gd.layout.dragmode,'pan');
    }
    f.q('.rist-edit-mode-toggle').click();await tick();f.custom('rist-close-edit-tool');await tick();
    assert.equal(f.gd.layout.dragmode,'pan');assert.equal(f.gd._ristEditMode,false);
  });
  test(kind+': Pan reset does not interrupt active custom gestures; peak exit returns Pan',async t=>{
    const f=setup(t,kind);f.run('function addPeakAt');
    await f.w.Plotly.relayout(f.gd,{dragmode:'zoom'});
    f.q('.rist-peak-add-button').click();await tick();
    await f.gd._ristRestoreNavigation();assert.equal(f.gd.layout.dragmode,'zoom');
    f.q('.rist-peak-add-button').click();await tick();assert.equal(f.gd.layout.dragmode,'pan');
    for(const flag of ['_ristAxisCropActive','_ristShapeDrawMode','_ristRamanRatioMode','_ristFtirYDragMode','_ristRamanYDragMode']) {
      f.gd[flag]=true;f.gd.layout.dragmode='select';await f.gd._ristRestoreNavigation();assert.equal(f.gd.layout.dragmode,'select');f.gd[flag]=false;
    }
  });
}
test('other consumers retain previous drag mode unless they opt in',async t=>{
  const f=setup(t,'raman');f.gd._ristDefaultDragmode=null;
  f.gd.layout.dragmode='zoom';f.q('.rist-edit-mode-toggle').click();await tick();
  f.q('.rist-edit-mode-toggle').click();await tick();assert.equal(f.gd.layout.dragmode,'zoom');
});

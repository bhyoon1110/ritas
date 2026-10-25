// Run the shipped Raman programs against DOM/Plotly doubles. Browser rendering
// is checked separately; these tests do not claim native XP/Supermium coverage.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {JSDOM} = require('jsdom');
const acorn = require('acorn');
const html = execFileSync(process.env.RIST_TEST_PYTHON || 'python3', ['-c',
  'from app.path_bootstrap import add_project_package_paths; add_project_package_paths(); from app.raman_web import build_raman_page; print(build_raman_page())'], {encoding:'utf8'});
const scripts = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
const findScript = token => {const script=scripts.find(s=>s.includes(token)); assert.ok(script,token); return script;};
const clone = x=>JSON.parse(JSON.stringify(x));
const tick = ()=>new Promise(resolve=>setTimeout(resolve,25));

function setup(t) {
  const dom = new JSDOM(html,{runScripts:'outside-only',pretendToBeVisual:true,url:'http://localhost/raman'});
  t.after(()=>dom.window.close());
  const w=dom.window,d=w.document,gd=d.getElementById('raman-plot');
  gd.data=[];
  gd.layout={xaxis:{range:[0,4000],fixedrange:false},yaxis:{range:[0,4],fixedrange:false},meta:{},annotations:[],shapes:[],dragmode:'zoom'};
  gd._fullLayout={...clone(gd.layout),_size:{l:60,t:100,w:800,h:400}};
  gd.getBoundingClientRect=()=>({left:0,top:0,width:1000,height:650});
  gd._context={scrollZoom:true};
  const handlers={};
  gd.on=(name,fn)=>{(handlers[name] ||= []).push(fn); return gd;};
  const emit=(name,event)=> (handlers[name]||[]).map(fn=>fn(event));
  const updates=[];
  function setPath(target,key,value) {
    const parts=key.replace(/\[(\d+)\]/g,'.$1').split('.');
    for(const part of parts.slice(0,-1)) target=target[part] ||= {};
    target[parts.at(-1)]=clone(value);
  }
  w.Plotly={
    relayout:async(_,changes)=>{
      updates.push(clone(changes));
      for(const [key,val] of Object.entries(changes)) setPath(gd.layout,key,val);
      gd._fullLayout={...clone(gd.layout),_size:{l:60,t:100,w:800,h:400}};
      emit('plotly_relayout',changes);emit('plotly_afterplot');
    },
    react:async(_,data,layout)=>{gd.data=data;gd.layout=layout;emit('plotly_afterplot');},
    restyle:async(_,changes,curves)=>{
      curves.forEach((curve,i)=>{for(const [key,value] of Object.entries(changes)) setPath(gd.data[curve],key,Array.isArray(value)?value[i]:value);});
      emit('plotly_afterplot');
    },
    Plots:{resize:()=>{}}, purge:()=>{}
  };
  w.Plotly.update=async(_,data,layout,curves)=>{await w.Plotly.restyle(gd,data,curves);await w.Plotly.relayout(gd,layout);};
  let captures=0;
  gd._ristHistory={capture:()=>captures++};
  const dispatch=name=>gd.dispatchEvent(new w.CustomEvent(name));
  return {w,d,gd,emit,updates,dispatch,captures:()=>captures,run:token=>w.eval(findScript(token))};
}

test('all emitted inline programs parse, Raman opts into axis locks and Y-only wheel',()=>{
  scripts.forEach(script=>acorn.parse(script,{ecmaVersion:'latest'}));
  assert.match(html,/var axisLocksEnabled = true/);
  assert.match(html,/var scrollZoomYOnly = true/);
});

test('plot size applies and restores through the real undo/redo controller',async t=>{
  const {gd,d,w,run}=setup(t);
  Object.defineProperty(gd.parentElement,'clientWidth',{value:1400});
  run('var axisLocksEnabled');
  gd._ristHistory=null;run('var undoStack');run('var ratios =');
  d.querySelector('[data-plot-width]').value='1000';
  d.querySelector('[data-plot-ratio]').value='4:3';
  d.querySelector('[data-plot-size-apply]').click();
  await new Promise(r=>setTimeout(r,120));
  assert.equal(gd.layout.width,1000);assert.equal(gd.style.height,'750px');
  gd._ristHistory.undo();await new Promise(r=>setTimeout(r,120));
  assert.equal(gd.style.width,'100%');assert.equal(d.querySelector('[data-plot-ratio]').value,'auto');
  gd._ristHistory.redo();await new Promise(r=>setTimeout(r,120));
  assert.equal(gd.layout.width,1000);assert.equal(gd.style.height,'750px');
  assert.equal(d.querySelector('[data-plot-width]').value,'1000');
  const height=d.querySelector('[data-plot-height]');height.value='1300';height.dispatchEvent(new w.Event('input'));
  d.querySelector('[data-plot-size-apply]').click();await new Promise(r=>setTimeout(r,120));
  assert.equal(gd.style.height,'1300px');
  gd._ristHistory.undo();await new Promise(r=>setTimeout(r,120));
  assert.equal(gd.style.height,'750px');assert.equal(d.querySelector('[data-plot-ratio]').value,'4:3');
  gd._ristHistory.redo();await new Promise(r=>setTimeout(r,120));
  assert.equal(gd.style.height,'1300px');assert.equal(d.querySelector('[data-plot-height]').value,'1300');
  assert.deepEqual(clone(gd._ristPlotSize.exportDimensions({})),{width:1000,height:1300});
  w.dispatchEvent(new w.Event('pagehide'));
});

test('raw Raman stack uses the measurement scale, fits negatives and restores exact values',async t=>{
  const {gd,d,run,dispatch}=setup(t);
  const original=[[-400,12000,300],[-200,6000,900]];
  gd.data=original.map((y,i)=>({name:'Sample '+i,x:[100,150,200],y:y.slice(),customdata:y.slice(),
    meta:{rist_sample_group:'sample:'+i,rist_sample_parent:true,rist_raman_stack_offset:0}}));
  gd.layout.meta={ristRamanStack:{enabled:false,gap:1.2,unit:12400,
    sampleOffsets:{'sample:0':0,'sample:1':0},sampleOrder:['sample:0','sample:1']}};
  run('gd._ristRamanStackInstalled');await tick();
  const stack=d.querySelector('.rist-raman-stack-button');
  assert.deepEqual(clone(gd.data.map(t=>t.y)),original);
  stack.click();await tick();
  assert.deepEqual(clone(gd.layout.meta.ristRamanStack.legendOrder),['sample:0','sample:1']);
  assert.equal(gd.data[0].meta.rist_raman_stack_offset,14880);
  assert.deepEqual(clone(gd.data[0].y),original[0].map(y=>y+14880));
  assert.deepEqual(clone(gd.data[1].y),original[1]);
  assert.deepEqual(clone(gd.data[1].customdata),original[1]);
  assert(gd.layout.yaxis.range[0]<-400);assert(gd.layout.yaxis.range[1]>20880);
  stack.click();await tick();
  assert.deepEqual(clone(gd.data.map(t=>t.y)),original);
  assert(gd.layout.yaxis.range[0]<-400);assert(gd.layout.yaxis.range[1]>12000);
  // Structure refresh/undo metadata must keep the raw-scale stack unit.
  dispatch('rist-plot-structure-changed');await tick();stack.click();await tick();
  assert.equal(gd.data[0].meta.rist_raman_stack_offset,14880);
});

test('manual Raman peak retains raw intensity even when added to a vertically shifted curve',async t=>{
  const {gd,d,w,run}=setup(t);
  gd.data=[{name:'Sample',x:[100,200,300],y:[14980,26880,15180],
    meta:{rist_sample_parent:true,rist_sample_group:'sample:0',rist_raman_stack_offset:14880}}];
  gd._fullLayout.xaxis.p2d=x=>x;gd._fullLayout.yaxis.p2d=y=>30000-y;
  const drag=d.createElement('div');drag.className='nsewdrag';gd.appendChild(drag);
  drag.getBoundingClientRect=()=>({left:0,top:0,width:400,height:300});
  w.Plotly.addTraces=async(_,trace)=>{gd.data.push(trace);};
  run('function addPeakAt');
  d.querySelector('.rist-peak-add-button').click();
  drag.dispatchEvent(new w.MouseEvent('click',{bubbles:true,cancelable:true,clientX:200,clientY:100}));
  await tick();
  assert.equal(gd.data.length,2);
  assert.equal(gd.data[1].meta.rist_peak.base_y,12000);
  assert.equal(gd.data[1].meta.rist_raman_stack_offset,14880);
  assert.equal(gd.data[1].y[0],26880);
});

test('FTIR stack round-trip preserves raw values and the initial raw Y range',async t=>{
  const {gd,d,w}=setup(t);
  const program=execFileSync(process.env.RIST_TEST_PYTHON || 'python3',['-c',
    'from ftir.plotting import ftir_stack_js; print(ftir_stack_js("raman-plot"))'],{encoding:'utf8'}).match(/<script>([\s\S]*?)<\/script>/)[1];
  const original=[[0.1,0.5,0.2],[-0.2,1.3,0.3]];
  gd.data=original.map((y,i)=>({name:'Sample '+i,x:[100,150,200],y:y.slice(),
    meta:{rist_sample_group:'sample:'+i,rist_sample_parent:true,rist_ftir_stack_offset_absorbance:0,
      rist_ftir_stack_offset_transmittance:0,ftir_signal_toggle:{absorbance_y:y.slice(),
        transmittance_y:y.map(v=>100*Math.pow(10,-v))}}}));
  const initialRange=[-0.2-1.5*0.08,1.3+1.5*0.25];
  gd.layout.yaxis.range=initialRange.slice();
  gd.layout.meta={ristFtirStack:{enabled:false,gap:1.2,modeUnits:{absorbance:1.5,transmittance:100},
    samplePositions:{'sample:0':0,'sample:1':0},sampleOrder:['sample:0','sample:1']}};
  w.eval(program);await tick();
  const stack=d.querySelector('.rist-ftir-stack-button');
  stack.click();await tick();assert(gd.data[0].y[1]>1.3);
  assert.deepEqual(clone(gd.data[1].y),original[1]);
  stack.click();await tick();
  assert.deepEqual(clone(gd.data.map(t=>t.y)),original);
  assert.deepEqual(clone(gd.layout.yaxis.range),initialRange);
});

test('palette selection preserves legend editor and its explicit save/undo contract',async t=>{
  const {w,d,gd,run}=setup(t);
  gd.data=[{name:'Sample A',x:[1,2],y:[1,2],line:{color:'#377eb8',dash:'solid'},meta:{rist_sample_parent:true}}];
  run('window.__ristColorPaletteInstalled');run('function legendItemFor');
  gd._ristHistory=null;run('var undoStack');await tick();await tick();
  d.querySelector('.rist-legend-edit-button').click();
  const input=d.querySelector('.rist-legend-color-input');
  input.getClientRects=()=>[{}];input.getBoundingClientRect=()=>({left:10,top:10,bottom:40});
  input.click();assert.equal(d.querySelector('#rist-color-palette').style.display,'block');
  d.querySelector('[data-palette-color="#e41a1c"]').click();
  await new Promise(resolve=>setTimeout(resolve,180));
  assert.equal(input.value,'#e41a1c');assert.equal(gd.data[0].line.color,'#e41a1c');
  assert.equal(d.querySelector('.rist-history-undo').disabled,true,'preview is not history');
  assert.equal(d.querySelector('.rist-legend-edit-panel').style.display,'block');
  d.querySelector('.rist-legend-edit-save-all').click();await tick();
  assert.equal(gd.data[0].line.color,'#e41a1c');
  gd._ristHistory.undo();await tick();assert.equal(gd.data[0].line.color,'#377eb8');
  gd._ristHistory.redo();await tick();assert.equal(gd.data[0].line.color,'#e41a1c');
});

for(const editing of [false,true]) test(`wheel changes Y only, including editing=${editing}`,t=>{
  const {w,gd,run,captures}=setup(t);run('var axisLocksEnabled');
  gd._ristEditMode=editing;gd._context.scrollZoom=!editing;
  const x=clone(gd.layout.xaxis.range);
  const ev=new w.WheelEvent('wheel',{bubbles:true,cancelable:true,clientX:300,clientY:300,deltaY:-120});
  gd.dispatchEvent(ev);
  assert.equal(ev.defaultPrevented,true);
  assert.deepEqual(clone(gd.layout.xaxis.range),x);
  assert.ok(gd.layout.yaxis.range[1]-gd.layout.yaxis.range[0]<4);
  assert.equal(captures(),1);
  const outside=new w.WheelEvent('wheel',{bubbles:true,cancelable:true,clientX:300,clientY:50,deltaY:-120});
  gd.dispatchEvent(outside);assert.equal(outside.defaultPrevented,false);
});

test('numeric axis locks persist through zoom, reset, wheel, crop; explicit numeric edits unlock',async t=>{
  const {w,d,gd,run,emit}=setup(t);run('var axisLocksEnabled');
  const q=s=>d.querySelector(s);
  q('.rist-axis-control-button').click();
  q('[data-axis-lock="x"]').checked=true;
  q('[data-axis="x"][data-axis-field="from"]').value='100';
  q('[data-axis-action="apply"]').click();await tick();
  assert.equal(gd.layout.xaxis.fixedrange,true);
  assert.deepEqual(clone(gd.layout.xaxis.range),[100,4000]);
  q('.rist-axis-control-button').click();
  q('[data-axis-action="zoom-in"]').click();await tick();
  assert.deepEqual(clone(gd.layout.xaxis.range),[100,4000]);
  q('[data-axis-action="reset"]').click();await tick();
  assert.equal(gd.layout.xaxis.fixedrange,true);
  assert.deepEqual(clone(gd.layout.xaxis.range),[100,4000]);
  q('[data-axis-action="crop"]').click();await tick();
  assert.equal(gd.layout.selectdirection,'v');
  emit('plotly_selected',{range:{x:[900,1100],y:[1,3]}});await tick();
  assert.deepEqual(clone(gd.layout.xaxis.range),[100,4000]);
  assert.deepEqual(clone(gd.layout.yaxis.range),[1,3]);
  q('.rist-axis-control-button').click();
  q('[data-axis-lock="y"]').checked=true;
  q('[data-axis-action="apply"]').click();await tick();
  gd.dispatchEvent(new w.WheelEvent('wheel',{bubbles:true,cancelable:true,clientX:300,clientY:300,deltaY:200}));
  assert.deepEqual(clone(gd.layout.yaxis.range),[1,3]);
  q('.rist-axis-control-button').click();
  q('[data-axis-action="crop"]').click();await tick();
  assert.equal(gd._ristAxisCropActive,false);
  assert.match(q('.rist-axis-control-status').textContent,/고정을 해제/);
  q('[data-axis-lock="x"]').checked=false;
  q('[data-axis-lock="y"]').checked=false;
  q('[data-axis-action="apply"]').click();await tick();
  assert.equal(gd.layout.xaxis.fixedrange,false);assert.equal(gd.layout.yaxis.fixedrange,false);
});

function ratioFixture(t, history=false) {
  const api=setup(t),{gd,run,d,emit}=api;
  gd.data=['A','B'].flatMap((group,i)=>[
    {name:`Sample ${group}`,meta:{rist_sample_parent:true,rist_sample_group:group}},
    ...[100,200,300].map((x,j)=>({name:`Peak ${x}`,x:[x],y:[j+1+i*4],meta:{rist_sample_group:group,rist_raman_stack_offset:i*4,rist_peak:{x,base_y:j+1,sample_group:group}}}))
  ]);
  // Existing-report fixture. Pointer selection/overlap/lock behavior is tested
  // with pixel geometry in browser_compat/tests/raman_ratio.test.cjs.
  gd.layout.meta.ristRamanRatios=['A','B'].map((group,i)=>({
    id:i+1, numeratorKey:JSON.stringify([group,100]), denominatorKey:JSON.stringify([group,200])
  }));
  if(history) {gd._ristHistory=null;run('var undoStack');}
  run('var ratioMetaKey');
  return api;
}

function modesFixture(t) {
  const api=setup(t),{gd,run}=api;
  run('function addPeakAt');run('gd._ristDefaultDragmode =');
  run('var bar = document.createElement');
  return api;
}

test('peak modes expose a persistent status; Escape returns to Pan without undoing data',async t=>{
  const {gd,d,w}=modesFixture(t);
  for(const mode of ['add','delete','select']){
    d.querySelector('.rist-peak-'+mode+'-button').click();await tick();
    assert.equal(d.querySelector('.rist-interaction-status').dataset.mode,'peak-'+mode);
    assert.match(d.querySelector('.rist-interaction-help').textContent,/Esc/);
    const data=JSON.stringify(gd.data);
    d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));await tick();
    assert.equal(gd._ristPeakEditMode,'none');assert.equal(gd.layout.dragmode,'pan');
    assert.equal(d.querySelector('.rist-interaction-status').dataset.mode,'pan');
    assert.equal(JSON.stringify(gd.data),data);
  }
  d.querySelector('.rist-peak-add-button').click();await tick();
  d.querySelector('.rist-interaction-exit').click();await tick();
  assert.equal(gd._ristPeakEditMode,'none','touch-accessible Exit works without a keyboard');
});

test('Escape respects text input, IME, prevented keys and another active graph',async t=>{
  const {gd,d,w}=modesFixture(t);
  d.querySelector('.rist-peak-delete-button').click();await tick();
  const input=d.createElement('textarea');gd.appendChild(input);input.focus();
  input.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));
  d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',isComposing:true,bubbles:true}));
  const handled=new w.KeyboardEvent('keydown',{key:'Escape',cancelable:true});handled.preventDefault();d.dispatchEvent(handled);
  assert.equal(gd._ristPeakEditMode,'delete');
  const other=d.createElement('div');d.body.appendChild(other);w.__ristActiveHistoryPlot=other;
  d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape'}));assert.equal(gd._ristPeakEditMode,'delete');
  w.__ristActiveHistoryPlot=gd;
  d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',cancelable:true}));await tick();
  assert.equal(gd._ristPeakEditMode,'none');
});

test('label deletion uses its own trace and consumes native/Plotly duplicate events',async t=>{
  const {gd,d,w,run,emit}=setup(t);
  gd.data=[{name:'Raw',x:[1,2],y:[1,2],meta:{rist_sample_parent:true}},
    ...[1,2].map(x=>({name:'peak'+x,x:[x],y:[x],meta:{rist_peak:{x,base_y:x}}}))];
  gd.layout.annotations=[{text:'moved peak1'},{text:'peak2'},{text:'memo',name:'rist_text_box_text:memo'}];
  gd.layout.shapes=[{name:'connector1'},{name:'connector2'},{name:'memo'}];
  gd.layout.meta.ristPeakLabels=[{traceIndex:1,annotationIndex:0,shapeIndex:0},{traceIndex:2,annotationIndex:1,shapeIndex:1}];
  gd.insertAdjacentHTML('beforeend','<svg><g class="annotation" data-index="0"><text class="annotation-text">moved peak1</text></g><g class="annotation" data-index="2"><text class="annotation-text">memo</text></g></svg>');
  run('function legendItemFor');run('function addPeakAt');
  d.querySelector('.rist-peak-delete-button').click();
  const label=gd.querySelector('.annotation-text');
  label.dispatchEvent(new w.MouseEvent('mousedown',{bubbles:true,cancelable:true,clientX:700,clientY:20}));
  await tick();
  label.dispatchEvent(new w.MouseEvent('click',{bubbles:true,cancelable:true,clientX:700,clientY:20}));
  emit('plotly_clickannotation',{index:0});emit('plotly_click',{points:[{curveNumber:1}]});
  label.dispatchEvent(new w.MouseEvent('dblclick',{bubbles:true,cancelable:true}));await tick();
  assert.deepEqual(clone(gd.data.map(t=>t.name)),['Raw','peak2']);
  assert.deepEqual(clone(gd.layout.annotations.map(a=>a.text)),['peak2','memo']);
  assert.deepEqual(clone(gd.layout.shapes.map(a=>a.name)),['connector2','memo']);
  assert.equal(gd._ristPeakEditMode,'delete');assert.ok(!gd._ristInlineTextEditing);
  assert.equal(gd.layout.meta.ristPeakLabels[0].traceIndex,1);
});

test('Crop, shape edit, Y move and zoom share mode indication and Escape cleanup',async t=>{
  const {gd,d,w,run}=modesFixture(t);
  run('var axisLocksEnabled');run('gd._ristRamanStackInstalled');
  const escape=async()=>{d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',cancelable:true}));await tick();assert.equal(gd.layout.dragmode,'pan');};
  await gd._ristSetEditMode(true);await tick();
  assert.equal(d.querySelector('.rist-interaction-status').dataset.mode,'shape-edit');
  await escape();assert.equal(gd._ristEditMode,false);assert.equal(gd._context.scrollZoom,true);
  gd._ristSetYDragMode(true);await tick();
  assert.equal(d.querySelector('.rist-interaction-status').dataset.mode,'y-drag');
  await escape();assert.equal(gd._ristRamanYDragMode,false);
  d.querySelector('[data-axis-action="crop"]').click();await tick();
  assert.equal(gd._ristAxisCropActive,true);assert.equal(d.querySelector('.rist-interaction-status').dataset.mode,'crop');
  await escape();assert.equal(gd._ristAxisCropActive,false);
  assert.equal(gd.classList.contains('rist-axis-crop-mode'),false);
  await w.Plotly.relayout(gd,{dragmode:'zoom'});await tick();
  assert.equal(d.querySelector('.rist-interaction-status').dataset.mode,'zoom');await escape();
});

test('Raman ratio Escape leaves existing calculated ratios untouched',async t=>{
  const {gd,d,w,run}=ratioFixture(t);await tick();
  run('gd._ristDefaultDragmode =');run('var bar = document.createElement');
  const before=clone(gd.layout.meta.ristRamanRatios);
  d.querySelector('.rist-raman-ratio-button').click();await tick();
  assert.equal(gd._ristRamanRatioMode,true);assert.equal(d.querySelector('.rist-interaction-status').dataset.mode,'ratio');
  d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',cancelable:true}));await tick();
  assert.equal(gd._ristRamanRatioMode,false);assert.equal(gd.layout.dragmode,'pan');
  assert.deepEqual(clone(gd.layout.meta.ristRamanRatios),before);
});

test('batch ratio Escape closes its capture surface without applying a draft',async t=>{
  const {gd,d,w,run}=setup(t);
  run('window.RistRamanRanges = {maximum');run('var ratioMetaKey');
  run('gd._ristDefaultDragmode =');run('var bar = document.createElement');
  d.querySelector('[data-ratio-batch]').click();await tick();
  assert.equal(gd._ristRamanRatioMode,true);assert.equal(gd._ristRamanRatioStage,'range');
  assert.equal(d.querySelector('.rist-ratio-range-surface').hidden,false);
  d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',cancelable:true}));await tick();
  assert.equal(gd._ristRamanRatioMode,false);assert.equal(gd.layout.dragmode,'pan');
  assert.equal(d.querySelector('.rist-ratio-range-surface').hidden,true);
  assert.equal(d.querySelector('.rist-ratio-batch').hidden,true);
  assert.equal((gd.layout.meta.ristRamanRatios||[]).length,0);
});

test('shape drawing Escape clears the overlay and keeps existing shapes',async t=>{
  const {gd,d,w,run}=modesFixture(t);
  gd.layout.shapes=[{name:'existing',type:'rect',x0:10,x1:20,y0:1,y1:2}];
  run('function beginShapeSelection');
  d.querySelector('.rist-shape-tool-button').click();await tick();
  assert.equal(gd._ristShapeDrawMode,true);
  assert.equal(d.querySelector('.rist-interaction-status').dataset.mode,'shape-draw');
  d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',cancelable:true}));await tick();
  assert.equal(gd._ristShapeDrawMode,false);assert.equal(!!gd._ristEditMode,false);
  assert.equal(gd.classList.contains('rist-shape-editing'),false);
  assert.equal(d.querySelector('.rist-shape-editor-panel').style.display,'none');
  assert.equal(gd.layout.shapes[0].name,'existing');assert.equal(gd.layout.dragmode,'pan');
});

test('ratios have no connector and edit/delete only the selected sample ratio',async t=>{
  const {gd,d}=ratioFixture(t);await tick();
  assert.equal(gd.layout.annotations.length,2);assert.equal(gd.layout.shapes.length,0);
  assert.match(gd.layout.annotations[0].text,/0.500/); // base intensity, not stack offset
  d.querySelectorAll('.rist-raman-ratio-button')[1].click();
  const rows=d.querySelectorAll('[data-ratio-id]');
  assert.equal(rows.length,2);assert.match(rows[1].textContent,/Sample B/);
  for(const row of rows) {
    assert.equal(row.style.whiteSpace,'nowrap');
    assert.equal(row.style.textOverflow,'ellipsis');
    assert.equal(row.title,row.textContent);
  }
  rows[0].click();
  d.querySelector('[data-ratio-title]').value='<script>test</script>';
  d.querySelector('[data-ratio-denominator]').value='3';
  d.querySelector('[data-ratio-form] button[type=submit]').click();await tick();
  assert.match(gd.layout.annotations[0].text,/&lt;script&gt;/);
  assert.match(gd.layout.annotations[0].text,/0.333/);
  assert.match(gd.layout.annotations[1].text,/0.500/);
  d.querySelectorAll('.rist-raman-ratio-button')[1].click();
  d.querySelector('[data-ratio-delete]').click();await tick();
  assert.equal(gd.layout.annotations.length,1);
  assert.equal(gd.layout.annotations[0].name,'rist_raman_ratio:2');
  assert.equal(gd.layout.meta.ristRamanRatios.length,1);
});

test('ratio double-click, position, reindex, save/restore and deleted source peaks remain consistent',async t=>{
  const {gd,d,emit,dispatch,w}=ratioFixture(t);await tick();
  emit('plotly_clickannotation',{index:1});emit('plotly_clickannotation',{index:1});
  assert.equal(d.querySelector('[data-ratio-form]').hidden,false);
  assert.equal(d.querySelector('[data-ratio-numerator]').value,'5');
  d.querySelector('[data-ratio-close]').click();
  await w.Plotly.relayout(gd,{'annotations[1].x':180,'annotations[1].y':7});
  gd.data.splice(0,1);dispatch('rist-plot-structure-changed');await tick();
  assert.equal(gd.layout.annotations.length,2);
  assert.match(gd.layout.annotations[1].text,/0.500/);
  assert.equal(gd.layout.annotations[1].x,180);assert.equal(gd.layout.annotations[1].y,7);
  const saved=clone(gd.layout);
  gd.layout=clone(saved);dispatch('rist-plot-data-replaced');await tick();
  assert.equal(gd.layout.meta.ristRamanRatios.length,2);
  gd.data.splice(0,1);dispatch('rist-plot-structure-changed');await tick();
  assert.equal(gd.layout.annotations.length,1);
  assert.equal(gd.layout.annotations[0].name,'rist_raman_ratio:2');
  // A fresh analysis carries no ratio metadata; don't leak the old list.
  gd.layout={...gd.layout,meta:{},annotations:[]};dispatch('rist-plot-data-replaced');await tick();
  d.querySelectorAll('.rist-raman-ratio-button')[1].click();
  assert.match(d.querySelector('[data-ratio-list]').textContent,/없습니다/);
});

test('legend overlay double-click edits the correct trace even when legend order differs',async t=>{
  const {gd,w,d,run,emit}=setup(t);
  gd.data=[{name:'A',meta:{rist_sample_parent:true}},{name:'B',meta:{rist_sample_parent:true}}];
  gd.insertAdjacentHTML('beforeend','<svg><g class="legend"><g class="traces"><text class="legendtext">B</text><rect class="legendtoggle"></rect></g></g></svg>');
  const item=gd.querySelector('g.traces');item.__data__=[{trace:{index:1}}];
  run('function legendItemFor');
  // Plotly ignores false from earlier listeners if the final one is undefined.
  gd.on('plotly_legendclick',()=>undefined);
  gd.on('plotly_legenddoubleclick',()=>undefined);
  gd._ristHistory=null;run('var undoStack');
  item.querySelector('rect').dispatchEvent(new w.MouseEvent('mousedown',{bubbles:true}));
  gd.data[1].visible='legendonly'; // first-click toggle must not survive name editing
  gd._fullLayout.legend={_clickTimeout:w.setTimeout(()=>{gd.data[1].visible='legendonly';},10)};
  item.querySelector('rect').dispatchEvent(new w.MouseEvent('dblclick',{bubbles:true,cancelable:true}));
  let input=d.querySelector('[aria-label="범례 이름 수정"]');assert.ok(input);assert.equal(input.value,'B');
  assert.equal(gd.data[1].visible,true);
  input.value='Changed B';input.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));await tick();
  assert.equal(gd.data[1].name,'Changed B');assert.equal(gd.data[0].name,'A');
  assert.equal(gd.data[1].visible,true);
  assert.equal(emit('plotly_legenddoubleclick',{curveNumber:1}).at(-1),false);
  assert.ok(d.querySelector('[aria-label="범례 이름 수정"]'));
});

test('sample single-click toggles once; a double-click edits without a pending toggle',async t=>{
  const {gd,w,d,run,emit}=setup(t);
  gd.data=[{name:'A',meta:{rist_sample_parent:true}}];
  gd.insertAdjacentHTML('beforeend','<svg><g class="legend"><g class="traces"><text class="legendtext">A</text><rect class="legendtoggle"/></g></g></svg>');
  gd.querySelector('g.traces').__data__=[{trace:{index:0}}];
  run('function legendItemFor');gd._ristHistory=null;run('var undoStack');
  assert.equal(emit('plotly_legendclick',{curveNumber:0}).at(-1),false);
  await new Promise(r=>setTimeout(r,380));assert.equal(gd.data[0].visible,'legendonly');
  emit('plotly_legendclick',{curveNumber:0});emit('plotly_legendclick',{curveNumber:0});
  const input=d.querySelector('[aria-label="범례 이름 수정"]');assert.ok(input);
  input.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape'}));
  await new Promise(r=>setTimeout(r,380));assert.equal(gd.data[0].visible,'legendonly');
});

test('individual ratio deletion can be undone/redone, then stacked and edited without resurrecting siblings',async t=>{
  const {gd,d,dispatch}=ratioFixture(t,true);await tick();await tick();await tick();
  d.querySelectorAll('.rist-raman-ratio-button')[1].click();
  d.querySelector('[data-ratio-id="1"]').click();
  d.querySelector('[data-ratio-delete]').click();await tick();
  assert.equal(gd.layout.annotations.length,1);
  gd._ristHistory.undo();await tick();
  assert.equal(gd.layout.annotations.length,2);
  gd._ristHistory.redo();await tick();
  assert.equal(gd.layout.annotations.length,1);
  gd.data[4].visible='legendonly';dispatch('rist-raman-stack-change');await tick();
  assert.equal(gd.layout.annotations[0].visible,false);
  gd.data[4].visible=true;dispatch('rist-raman-stack-change');await tick();
  assert.equal(gd.layout.annotations[0].visible,true);
  assert.equal(gd.layout.annotations[0].name,'rist_raman_ratio:2');
});

test('TXT selected in library chooser uses raw analyze path, JSON/CSV remain assignment libraries',async t=>{
  const {w,gd,run,d}=setup(t),requests=[];
  w.fetch=async(url,options)=>{
    requests.push({url,options});
    return {ok:true,json:async()=>url.includes('/analyze')?{figure:{data:[],layout:gd.layout},samples:[]}:{libraries:[],roles:[],userId:'interaction-test'}};
  };
  run('rist-spectrum-work-template');
  run('window.RistSpectrumWorkflow =');
  run('async function analyze()');await tick();requests.length=0;
  const input=d.getElementById('raman-library-input');
  Object.defineProperty(input,'files',{configurable:true,value:[new w.File(['100 1\n200 2\n'],'spectrum.TXT')]});
  input.dispatchEvent(new w.Event('change'));await tick();
  assert.equal(requests.filter(r=>r.url==='/api/v1/raman/analyze').length,1);
  assert.equal(requests.filter(r=>r.url==='/api/v1/raman/assignment-libraries'&&r.options?.method==='POST').length,0);
  assert.match(d.getElementById('raman-file-list').textContent,/spectrum.TXT/);
  Object.defineProperty(input,'files',{configurable:true,value:[new w.File(['{}'],'library.json')]});
  input.dispatchEvent(new w.Event('change'));await tick();
  assert.equal(requests.filter(r=>r.url==='/api/v1/raman/assignment-libraries'&&r.options?.method==='POST').length,1);
});

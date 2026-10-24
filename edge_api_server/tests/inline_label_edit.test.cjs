// Exercise the shipped FTIR/Raman programs and their Chrome49 builds. These
// DOM tests complement real Plotly browser QA; they are not Windows VM tests.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {createHash} = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const clone = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setTimeout(resolve,40));
const pages = {};
for (const kind of ['ftir','raman']) {
  pages[kind] = execFileSync(process.env.RIST_TEST_PYTHON || 'python3', ['-c',
    `from app.${kind}_web import build_${kind}_page; print(build_${kind}_page())`], {encoding:'utf8'});
}
const assets = path.resolve(__dirname,'../app/static/chrome49');
const manifest = JSON.parse(fs.readFileSync(path.join(assets,'manifest.json'),'utf8'));

function setup(t,kind,mode,pointer=false) {
  const dom = new JSDOM(pages[kind],{runScripts:'outside-only',pretendToBeVisual:true,url:'http://localhost/'+kind});
  const w=dom.window,d=w.document,gd=d.getElementById(kind==='ftir'?'peak-plot':'raman-plot');
  t.after(()=>{w.dispatchEvent(new w.Event('pagehide'));w.close();});
  if (pointer) w.PointerEvent=w.MouseEvent;
  gd.data=[{name:'Sample',x:[100,200,300],y:[1,3,2],legendgroup:'sample:0',meta:{rist_sample_parent:true,rist_sample_group:'sample:0'}},
    {name:'C–O',x:[200],y:[3],legendgroup:'peak:1',marker:{color:'#ff0000'},meta:{rist_legend_edit_group:'peak:1',rist_sample_group:'sample:0',
      rist_peak:{label_key:'peak:1',sample_group:'sample:0',label:'C–O',x:200}}}];
  gd.layout={meta:{ristPeakLabels:[{traceIndex:1,annotationIndex:0,shapeIndex:-1,labelKey:'peak:1',legendgroup:'peak:1',wnText:'200 cm⁻¹'}]},
    xaxis:{range:[0,4000]},yaxis:{range:[0,4]},dragmode:'zoom',shapes:[],annotations:[
      {text:'<b>200 cm⁻¹</b><br>C–O',x:200,y:3,ax:0,ay:-40,font:{color:'#ff0000'},captureevents:true,visible:true},
      {name:'rist_text_box_text:1',text:'A &amp; B<br>메모',x:500,y:1,width:180,height:60,font:{size:16,color:'#112233'}}]};
  gd._context={scrollZoom:true};
  const sync=()=>{gd._fullLayout={...clone(gd.layout),_size:{l:60,t:100,w:800,h:400}};
    gd._fullLayout.xaxis.p2d=x=>x;gd._fullLayout.yaxis.p2d=y=>4-y/100;
    gd._fullLayout.xaxis.d2p=x=>x;gd._fullLayout.yaxis.d2p=y=>(4-y)*100;};
  sync();gd.getBoundingClientRect=()=>({left:0,top:0,width:1000,height:650});
  gd._ristFtirUnitOriginalAnnotations=clone(gd.layout.annotations);
  gd.insertAdjacentHTML('beforeend','<svg><g class="legend"><g class="traces"><text class="legendtext">Sample</text><rect class="legendtoggle"/></g></g>'
    +'<g class="annotation" data-index="0"><text class="annotation-text">200 cm⁻¹ C–O</text></g>'
    +'<g class="annotation" data-index="1"><text class="annotation-text">A &amp; B 메모</text></g></svg><div class="nsewdrag"></div>');
  gd.querySelector('g.traces').__data__=[{trace:{index:0}}];
  gd.querySelectorAll('.annotation-text').forEach((n,i)=>{n.getBoundingClientRect=()=>({left:200+i*300,top:200,width:150,height:40,right:350+i*300,bottom:240});});
  gd.querySelector('.nsewdrag').getBoundingClientRect=()=>({left:0,top:0,width:800,height:400,right:800,bottom:400});
  const events={},updates=[];
  gd.on=(name,fn)=>{(events[name] ||= []).push(fn);return gd;};
  const emit=(name,value)=>(events[name]||[]).map(fn=>fn(value));
  function setPath(obj,key,value) {
    const parts=key.replace(/\[(\d+)\]/g,'.$1').split('.');
    for (const part of parts.slice(0,-1)) obj=obj[part] ||= {};
    obj[parts.at(-1)]=clone(value);
  }
  w.Plotly={
    restyle:async(_,patch,curves)=>{for (const [pos,curve] of curves.entries()) for(const [key,value] of Object.entries(patch))
      setPath(gd.data[curve],key,Array.isArray(value)?value[pos % value.length]:value);sync();emit('plotly_restyle',[patch,curves]);emit('plotly_afterplot');},
    relayout:async(_,patch)=>{updates.push(clone(patch));for (const [key,val] of Object.entries(patch)) setPath(gd.layout,key,val);sync();emit('plotly_relayout',patch);emit('plotly_afterplot');},
    react:async(_,data,layout)=>{gd.data=data;gd.layout=layout;sync();emit('plotly_afterplot');},
    addTraces:async(_,trace)=>{gd.data.push(trace);},
    deleteTraces:async(_,curves)=>{gd.data=gd.data.filter((_,i)=>!curves.includes(i));},
    Plots:{resize:()=>{}},purge:()=>{}
  };
  w.Plotly.update=async(_,data,layout,curves)=>{await w.Plotly.restyle(gd,data,curves);await w.Plotly.relayout(gd,layout);};
  if (mode==='chrome49') w.eval(fs.readFileSync(path.join(assets,manifest.runtime),'utf8'));
  const scripts=[...pages[kind].matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
  const run=token=>{
    let source=scripts.find(s=>s.includes(token));assert(source,token);
    if (mode==='chrome49') {
      const file=manifest.scripts[createHash('sha256').update(source.trim()).digest('hex')];
      assert(file,'rebuild browser assets: '+token);source=fs.readFileSync(path.join(assets,file),'utf8');
    }
    w.eval(source);
  };
  run('function legendItemFor');run('function addPeakAt');run('var undoStack');
  const q=selector=>gd.querySelector(selector);
  const mouse=(target,type,x=220,y=220)=>target.dispatchEvent(new w.MouseEvent(type,{bubbles:true,cancelable:true,button:0,clientX:x,clientY:y}));
  const key=(target,value,extra={})=>target.dispatchEvent(new w.KeyboardEvent('keydown',{key:value,bubbles:true,cancelable:true,...extra}));
  const editor=()=>q('textarea[aria-label$="수정"]');
  const open=(index=0)=>{mouse(q('.annotation[data-index="'+index+'"] .annotation-text'),'dblclick');assert.ok(editor());return editor();};
  const dispatch=name=>gd.dispatchEvent(new w.CustomEvent(name));
  return {w,d,gd,q,run,emit,mouse,key,editor,open,dispatch,updates};
}

function shapeFixture(h) {
  h.gd.layout.shapes=[{type:'line',x0:200,x1:200,y0:0,y1:3},
    {name:'rist_text_box:1',type:'rect',x0:450,x1:650,y0:.5,y1:1.5,line:{color:'#112233',width:2},fillcolor:'rgba(255,255,255,0)'}];
  h.gd.layout.meta.ristPeakLabels[0].shapeIndex=0;
  h.gd._ristFtirUnitOriginalShapes=clone(h.gd.layout.shapes);
  h.run('function beginShapeSelection');
}

async function reorderFixture(t,kind,mode,pointer=false) {
  const h=setup(t,kind,mode,pointer), key=kind==='ftir'?'ristFtirStack':'ristRamanStack';
  const trace=(i,peak=false)=>({name:peak?'Peak '+i:'Raw '+i,x:peak?[200]:[100,200,300],y:peak?[3]:[1,3,2],
    legendgroup:'sample:'+i,mode:peak?'markers':'lines',line:{color:'#123456',width:2},
    meta:{rist_sample_parent:!peak,rist_sample_group:'sample:'+i,rist_legend_edit_group:peak?'peak:'+i:'sample:'+i,
      rist_raman_stack_offset:0,rist_ftir_stack_offset_absorbance:0,rist_ftir_stack_offset_transmittance:0,
      ftir_signal_toggle:{absorbance_y:peak?[3]:[1,3,2],transmittance_y:peak?[20]:[30,20,10]},
      ...(peak?{rist_peak:{sample_group:'sample:'+i,label_key:'peak:'+i,label:'Peak '+i,x:200}}:{})}});
  h.gd.data=[0,1,2].map(i=>trace(i)).concat([0,1,2].map(i=>trace(i,true)));
  h.gd.layout.meta={ristPeakLabels:[0,1,2].map(i=>({traceIndex:i+3,annotationIndex:i,shapeIndex:i,
    legendgroup:'sample:'+i,labelKey:'peak:'+i,annotationBaseY:3,shapeBaseY0:0,shapeBaseY1:3})),
    [key]:{enabled:true,gap:1.2,unit:10,modeUnits:{absorbance:10,transmittance:100},sampleOrder:['sample:0','sample:1','sample:2'],
      sampleOffsets:{'sample:0':0,'sample:1':12,'sample:2':24},samplePositions:{'sample:0':0,'sample:1':1.2,'sample:2':2.4}}};
  h.gd.layout.annotations=[0,1,2].map(i=>({text:'Peak '+i,x:200,y:3}));
  h.gd.layout.shapes=[0,1,2].map(i=>({type:'line',x0:200,x1:200,y0:0,y1:3}));
  h.dispatch('rist-plot-data-replaced');
  h.run(kind==='ftir'?'gd._ristFtirStackInstalled':'gd._ristRamanStackInstalled');await tick();
  h.q('.rist-legend-edit-button').click();
  const rows=()=>[...h.gd.querySelectorAll('.rist-legend-edit-row.is-sample')];
  h.q('.rist-legend-edit-panel').getBoundingClientRect=()=>({left:0,top:0,right:400,bottom:360,width:400,height:360});
  rows().forEach((row,i)=>{row.getBoundingClientRect=()=>({left:0,top:i*90,right:400,bottom:i*90+80});});
  const order=()=>rows().map(row=>h.gd.data[Number(row.dataset.curve)].meta.rist_sample_group);
  return {...h,stackKey:key,rows,order};
}

for(const kind of ['ftir','raman']) for(const mode of ['native','chrome49']) {
  test(kind+'/'+mode+': Raw drag reorders stack slots, keeps unsaved row edits, peak identities and true intensity',async t=>{
    const h=await reorderFixture(t,kind,mode), originals=h.gd.data.slice(), before=clone(h.gd.layout.meta[h.stackKey]);
    assert.deepEqual(h.order(),['sample:2','sample:1','sample:0']);
    h.rows()[2].querySelector('textarea').value='저장 전 이름';
    h.mouse(h.rows()[2].querySelector('.rist-raw-order-handle'),'mousedown',20,200);
    h.mouse(h.d,'mousemove',20,5);h.mouse(h.d,'mouseup',20,5);await tick();await h.gd._ristStackReady;
    assert.deepEqual(h.order(),['sample:0','sample:2','sample:1']);
    assert.equal(h.rows()[0].querySelector('textarea').value,'저장 전 이름');assert.equal(h.gd.data[0].name,'Raw 0');
    assert.deepEqual(h.gd.data.map(t=>t.name),originals.map(t=>t.name));
    assert.deepEqual([...h.gd.querySelectorAll('.rist-legend-raw-tree')].map(t=>t.dataset.sampleGroup),h.order());
    for(let i=0;i<3;i++){
      const tr=h.gd.data[i], offset=kind==='ftir'?tr.meta.rist_ftir_stack_offset_absorbance:tr.meta.rist_raman_stack_offset;
      assert.deepEqual(Array.from(tr.y,y=>y-offset),[1,3,2]);
      assert.equal(h.gd.data[i+3].y[0]-offset,3);assert.equal(h.gd.layout.annotations[i].y-offset,3);
      assert.equal(h.gd.layout.shapes[i].y0-offset,0);
      if(kind==='ftir')assert.deepEqual(Array.from(tr.meta.ftir_signal_toggle.transmittance_y,y=>y-tr.meta.rist_ftir_stack_offset_transmittance),[30,20,10]);
    }
    h.gd._ristHistory.undo();await tick();await h.gd._ristStackReady;
    assert.deepEqual(h.order(),['sample:2','sample:1','sample:0']);
    assert.equal(h.gd.layout.meta[h.stackKey].orderTopDown,before.orderTopDown);
    h.gd._ristHistory.redo();await tick();await h.gd._ristStackReady;
    assert.deepEqual(h.order(),['sample:0','sample:2','sample:1']);
  });
  test(kind+'/'+mode+': Raw order persists across stack toggle, gap, reload; cancel and keyboard are safe',async t=>{
    const h=await reorderFixture(t,kind,mode,true), initial=h.order();
    h.mouse(h.rows()[0].querySelector('.rist-raw-order-handle'),'pointerdown',20,20);
    h.mouse(h.d,'pointermove',20,280);h.w.dispatchEvent(new h.w.Event('blur'));h.mouse(h.d,'pointerup',20,280);await tick();
    assert.deepEqual(h.order(),initial);
    h.key(h.rows()[0].querySelector('.rist-raw-order-handle'),'ArrowDown');await tick();await h.gd._ristStackReady;
    const expected=['sample:1','sample:2','sample:0'];assert.deepEqual(h.order(),expected);
    for(const enabled of [false,true]){await h.gd._ristApplyStackSettings({enabled,gap:2});assert.deepEqual(h.order(),expected);}
    h.dispatch('rist-plot-data-replaced');await h.gd._ristStackReady;assert.deepEqual(h.order(),expected);
    await assert.rejects(h.gd._ristReorderSamples(['sample:0','sample:0','sample:2']));
    assert.deepEqual(h.order(),expected);
    // Fresh uploads must not inherit the previous dataset's manual order.
    delete h.gd.layout.meta[h.stackKey].orderTopDown;h.gd.layout.meta[h.stackKey].sampleOrder=['sample:0','sample:1','sample:2'];
    h.gd.layout.meta[h.stackKey].enabled=false;
    h.dispatch('rist-plot-data-replaced');await h.gd._ristStackReady;
    assert.deepEqual(h.order(),['sample:0','sample:1','sample:2']);
    const unstacked=clone(h.gd.data.map(trace=>trace.y));
    await h.gd._ristReorderSamples(expected);
    assert.deepEqual(h.order(),expected);
    assert.deepEqual(clone(h.gd.data.map(trace=>trace.y)),unstacked);
    await h.gd._ristApplyStackSettings({enabled:true,gap:2});assert.deepEqual(h.order(),expected);
  });
}

for (const kind of ['ftir','raman']) for (const mode of ['native','chrome49']) {
  const label=kind+'/'+mode;
  test(label+': peak display hides only points/labels/connectors and restores exact styling/evidence',async t=>{
    const h=setup(t,kind,mode);shapeFixture(h);await tick();
    const peak=h.gd.data[1];peak.mode='markers';peak.hoverinfo='x+y';peak.hovertemplate='%{x}: %{y}';
    const raw=clone(h.gd.data[0]),before=clone(peak),annotations=clone(h.gd.layout.annotations),shapes=clone(h.gd.layout.shapes);
    const button=h.q('.rist-peak-display-toggle');assert.equal(button.textContent,'피크 표시');
    button.click();await tick();
    assert.equal(button.getAttribute('aria-pressed'),'false');assert.equal(h.gd.layout.meta.ristPeaksVisible,false);
    assert.equal(peak.mode,'none');assert.equal(peak.hoverinfo,'skip');assert.equal(peak.hovertemplate,null);
    assert.equal(peak.visible,before.visible);assert.deepEqual(clone(peak.meta.rist_peak),before.meta.rist_peak);
    assert.deepEqual(clone(h.gd.data[0]),raw);assert.deepEqual(clone(peak.x),before.x);assert.deepEqual(clone(peak.y),before.y);
    assert.equal(h.gd.layout.annotations[0].visible,false);assert.equal(h.gd.layout.shapes[0].visible,false);
    assert.deepEqual(clone(h.gd.layout.annotations[1]),annotations[1]);assert.deepEqual(clone(h.gd.layout.shapes[1]),shapes[1]);
    assert.equal(h.gd._ristNearestPeakCurveFromEvent({clientX:200,clientY:100,target:h.gd}),null);
    h.q('.rist-peak-label-toggle').click();await tick(); // Remember independent label preference.
    button.click();await tick();
    assert.deepEqual(clone(peak),before);assert.equal(h.gd.layout.annotations[0].visible,false);
    h.q('.rist-peak-label-toggle').click();await tick();
    assert.equal(h.gd.layout.annotations[0].visible,true);assert.equal(h.gd.layout.shapes[0].visible,true);
  });
  test(label+': display toggle supports undo/redo, saved work restoration, and old work defaults',async t=>{
    const h=setup(t,kind,mode);shapeFixture(h);await tick();h.gd.data[1].mode='markers';
    const button=h.q('.rist-peak-display-toggle');button.click();await tick();
    const saved={data:clone(h.gd.data),layout:clone(h.gd.layout)};
    h.gd._ristHistory.undo();await tick();assert.equal(h.gd.data[1].mode,'markers');assert.equal(button.getAttribute('aria-pressed'),'true');
    h.gd._ristHistory.redo();await tick();assert.equal(h.gd.data[1].mode,'none');assert.equal(h.gd.layout.annotations[0].visible,false);
    h.gd.data=saved.data;h.gd.layout=saved.layout;h.dispatch('rist-plot-data-replaced');await tick();
    assert.equal(button.getAttribute('aria-pressed'),'false');button.click();await tick();assert.equal(h.gd.data[1].mode,'markers');
    delete h.gd.layout.meta.ristPeaksVisible;h.dispatch('rist-plot-data-replaced');await tick();
    assert.equal(button.getAttribute('aria-pressed'),'true');assert.equal(h.gd.layout.annotations[0].visible,true);
  });
  test(label+': sensitivity and all/sample visibility changes cannot unmask hidden peaks',async t=>{
    const h=setup(t,kind,mode);shapeFixture(h);await tick();
    h.gd.data[1].meta.rist_peak.sensitivity_min=20;h.gd.data[1].mode='markers';
    h.run('gd._ristPeakSensitivityInstalled');h.q('.rist-peak-display-toggle').click();await tick();
    for(const value of [0,100,0,100]) {
      const input=h.q('.rist-peak-sensitivity-number');input.value=String(value);input.dispatchEvent(new h.w.Event('input'));await tick();
      assert.equal(h.gd.data[1].mode,'none');assert.equal(h.gd.layout.annotations[0].visible,false);
      assert.equal(h.gd.data[1].visible,value===0?false:true);
    }
    for(const visible of ['legendonly',true]) {
      await h.w.Plotly.restyle(h.gd,{visible},[0]);await tick();
      assert.equal(h.gd.data[1].mode,'none');assert.equal(h.gd.layout.annotations[0].visible,false);
    }
    h.q('[data-visible="true"]').click();await tick();assert.equal(h.gd.data[1].mode,'none');
    h.q('.rist-peak-display-toggle').click();await tick();
    assert.equal(h.gd.data[1].mode,'markers');assert.equal(h.gd.layout.annotations[0].visible,true);
  });
  test(label+': new peaks stay hidden, rapid toggles settle, and exports omit only peak graphics',async t=>{
    const h=setup(t,kind,mode);shapeFixture(h);await tick();h.run('function buildImageExportPayload');
    const button=h.q('.rist-peak-display-toggle');button.click();await tick();
    const added=clone(h.gd.data[1]);delete added.meta.rist_peak_display;added.mode='markers';added.name='Manual';added.meta.rist_peak.source='user';
    await h.w.Plotly.addTraces(h.gd,added);h.emit('plotly_afterplot');await tick();assert.equal(added.mode,'none');
    const snapshot=clone({data:h.gd.data,layout:h.gd.layout});
    const output=h.gd._ristBuildImageExportPayload(h.gd.data,h.gd.layout,1000,700);
    assert.equal(output.data.length,1);assert.match(output.data[0].name,/>Sample<\/span>/);
    assert.equal(output.layout.annotations[0].visible,false);assert.equal(output.layout.shapes[0].visible,false);
    assert.equal(output.layout.annotations[1].text,snapshot.layout.annotations[1].text);
    assert.deepEqual(clone({data:h.gd.data,layout:h.gd.layout}),snapshot);
    button.click();button.click();button.click();await tick();
    assert.equal(button.getAttribute('aria-pressed'),'true');assert.equal(h.gd.data[1].mode,'markers');assert.equal(added.mode,'markers');
    assert.equal(h.gd._ristBuildImageExportPayload(h.gd.data,h.gd.layout,1000,700).data.length,3);
  });
  test(label+': sample width saves with color/dash, only changes its RAW trace, and supports undo/redo',async t=>{
    const h=setup(t,kind,mode);
    h.gd.data[0].line={width:1.25,color:'#123456',dash:'solid'};
    h.gd.data.push({name:'Other',x:[100,200],y:[2,4],line:{width:2,color:'#654321'},showlegend:true,legendgrouptitle:{text:''},
      meta:{rist_sample_parent:true,rist_sample_group:'sample:1'}});
    const before=clone(h.gd.data),events=[];
    h.gd.addEventListener('rist-sample-line-style-change',ev=>events.push(clone(ev.detail)));
    h.q('.rist-legend-edit-button').click();
    assert.equal(h.q('.rist-legend-sample-width').value,'1.25');
    assert.equal(h.q('.is-peak .rist-legend-sample-width'),null);
    h.q('.rist-legend-sample-width').value='3.5';
    h.q('.rist-legend-sample-dash').value='dash';
    h.q('.is-sample .rist-legend-color-input').value='#abcdef';
    h.q('.rist-legend-edit-save-all').click();await tick();
    assert.deepEqual(clone(h.gd.data[0].line),{width:3.5,color:'#abcdef',dash:'dash'});
    assert.deepEqual(clone(h.gd.data.slice(1)),before.slice(1));
    assert.deepEqual(clone(h.gd.data[0].x),before[0].x);assert.deepEqual(clone(h.gd.data[0].y),before[0].y);
    assert.equal(events.length,1);assert.equal(events[0].width,3.5);assert.deepEqual(events[0].curves,[0]);
    h.gd._ristHistory.undo();await tick();assert.deepEqual(clone(h.gd.data),before);
    h.gd._ristHistory.redo();await tick();assert.equal(h.gd.data[0].line.width,3.5);
    h.q('.rist-legend-edit-button').click();assert.equal(h.q('.rist-legend-sample-width').value,'3.5');
    h.q('.rist-legend-edit-save-all').click();await tick();assert.equal(events.length,1,'unchanged save is a no-op');
  });
  test(label+': sample-only legend starts hidden, toggles without hiding peaks, and survives history',async t=>{
    const h=setup(t,kind,mode);await tick();
    assert.equal(h.gd.layout.showlegend,false);assert.equal(h.gd.layout.legend.font.size,16);assert.equal(h.gd.layout.legend.font.weight,700);
    assert.equal(h.gd.data[0].showlegend,true);assert.equal(h.gd.data[1].showlegend,false);
    assert.equal(h.q('[data-peak-legend-visible]'),null);
    const button=h.q('.rist-legend-bulk-controls .rist-legend-visible-toggle');button.click();await tick();
    assert.equal(h.gd.layout.showlegend,true);assert.equal(button.textContent,'범례 숨김');
    assert.notEqual(h.gd.data[1].visible,false);assert.equal(h.gd.layout.annotations[0].visible,true);
    h.gd._ristHistory.undo();await tick();assert.equal(h.gd.layout.showlegend,false);
    h.gd._ristHistory.redo();await tick();assert.equal(h.gd.layout.showlegend,true);
    h.gd.data[1].showlegend=true;h.dispatch('rist-plot-data-replaced');await tick();
    assert.equal(h.gd.layout.showlegend,true);assert.equal(h.gd.data[1].showlegend,false);
    h.q('.rist-legend-edit-button').click();
    assert.equal(h.q('.rist-legend-peak-details').open,false);
    assert.equal(h.q('.rist-legend-peak-details .is-peak').getAttribute('data-curve'),'1');
    assert.equal(h.q('.rist-legend-edit-body > .is-sample').getAttribute('data-curve'),'0');
  });
  test(label+': legend editor only closes with Close, preserving pending edits across outside/Escape/mode/save',async t=>{
    const h=setup(t,kind,mode);await tick();h.q('.rist-legend-edit-button').click();
    const panel=h.q('.rist-legend-edit-panel'),input=h.q('.is-sample textarea');input.value='Edited Sample';
    h.d.body.click();h.key(h.d.body,'Escape');h.q('.rist-legend-edit-button').click();
    h.gd.dispatchEvent(new h.w.CustomEvent('rist-exclusive-interaction-start',{detail:{mode:'shape-edit'}}));
    assert.equal(panel.style.display,'block');assert.equal(h.gd._ristLegendEditOpen,true);assert.equal(input.value,'Edited Sample');
    h.q('.rist-legend-edit-save-all').click();await tick();
    assert.equal(h.gd.data[0].name,'Edited Sample');assert.equal(panel.style.display,'block');
    h.q('.rist-legend-edit-close').click();assert.equal(panel.style.display,'none');assert.equal(h.gd._ristLegendEditOpen,false);
  });
  test(label+': legend font/color sync and export styling never rewrite live names/RAW',async t=>{
    const h=setup(t,kind,mode);await tick();h.run('function buildImageExportPayload');
    h.q('.rist-legend-edit-button').click();
    const size=h.q('.rist-legend-font-size');
    for(const bad of ['9','33','12.5','']) {size.value=bad;h.q('.rist-legend-edit-save-all').click();assert.equal(h.gd.layout.legend.font.size,16);}
    size.value='22';h.q('.rist-legend-font-bold').checked=false;
    h.q('.is-sample .rist-legend-color-input').value='#e11d48';
    h.q('.rist-legend-edit-save-all').click();await tick();
    assert.equal(h.gd.layout.legend.font.size,22);assert.equal(h.gd.layout.legend.font.weight,400);
    assert.equal(h.q('text.legendtext').style.fill,'#e11d48');assert.equal(h.gd.data[0].name,'Sample');
    const figure={data:h.gd.data,layout:h.gd.layout},before=clone(figure);
    const hidden=h.gd._ristBuildImageExportPayload(figure.data,figure.layout,1000,700);assert.equal(hidden.layout.showlegend,false);
    h.q('.rist-legend-appearance .rist-legend-visible-toggle').click();await tick();
    const styled=h.gd._ristPrepareLegendFigure(figure);
    assert.match(styled.data[0].name,/color:#e11d48.*Sample/);assert.equal(h.gd.data[0].name,'Sample');
    const exported=h.gd._ristBuildImageExportPayload(h.gd.data,h.gd.layout,1000,700);
    assert.equal(exported.layout.showlegend,true);assert.equal(exported.layout.legend.font.size,22);assert.equal(exported.layout.legend.font.weight,400);
    assert.deepEqual(clone(h.gd.data),before.data);
  });
  test(label+': image export collision checks scale with the selected legend font',async t=>{
    const h=setup(t,kind,mode);await tick();h.run('function buildImageExportPayload');
    const data=[{name:'Sample12',x:[1000],y:[3.8],meta:{rist_sample_parent:true}}];
    const layout={xaxis:{range:[0,4000]},yaxis:{range:[0,4]},meta:{ristLegendVisible:true},legend:{font:{size:10,weight:700}}};
    const small=h.gd._ristBuildImageExportPayload(data,layout,1000,700);
    assert.equal(small.layout.meta.ristImageExport.legendCorner,'top-left');
    layout.legend.font.size=32;
    const large=h.gd._ristBuildImageExportPayload(data,layout,1000,700);
    assert.equal(large.layout.meta.ristImageExport.legendCorner,'top-right');
  });
  test(label+': deleting a Raw graph removes associated peaks only, remaps labels, and undoes/redoes',async t=>{
    const h=setup(t,kind,mode);shapeFixture(h);await tick();
    const parent=clone(h.gd.data[0]);parent.name='Other';parent.meta.rist_sample_group=parent.legendgroup='sample:1';
    const peak=clone(h.gd.data[1]);peak.meta.rist_sample_group=peak.meta.rist_peak.sample_group='sample:1';peak.meta.rist_peak.label_key='other';
    h.gd.data.push(parent,peak);h.gd.layout.annotations.push({text:'Other peak',x:200,y:3});
    h.gd.layout.meta.ristPeakLabels.push({traceIndex:3,annotationIndex:2,shapeIndex:-1});
    h.dispatch('rist-plot-data-replaced');await tick();
    const before=clone({data:h.gd.data,layout:h.gd.layout});
    h.q('.rist-legend-edit-button').click();h.w.confirm=()=>false;h.q('.rist-legend-sample-delete').click();await tick();assert.equal(h.gd.data.length,4);
    h.w.confirm=()=>true;h.q('.rist-legend-sample-delete').click();await tick();
    assert.equal(h.gd.data.length,2);assert.equal(h.gd.data[0].name,'Other');assert.equal(h.gd.layout.meta.ristPeakLabels[0].traceIndex,1);
    assert.equal(h.gd.layout.meta.ristPeakLabels[0].annotationIndex,1);assert.equal(h.gd.layout.annotations[0].name,'rist_text_box_text:1');
    assert.equal(h.gd.layout.shapes.length,1);assert.equal(h.gd.layout.shapes[0].name,'rist_text_box:1');
    assert.equal(h.q('.rist-legend-edit-panel').style.display,'block');assert.deepEqual(clone(h.gd.layout.meta.ristDeletedSampleGroups),['sample:0']);
    h.gd._ristHistory.undo();await tick();assert.deepEqual(clone(h.gd.data),before.data);assert.equal(h.gd.layout.meta.ristPeakLabels.length,2);
    h.gd._ristHistory.redo();await tick();assert.equal(h.gd.data.length,2);
    await h.gd._ristDeleteSample(0);await tick();assert.equal(h.gd.data.length,0);assert.equal(h.gd.layout.meta.ristDeletedSampleGroups.length,2);
  });
  test(label+': panel buttons share styling and Raw delete stays beside width inside sample style',async t=>{
    const h=setup(t,kind,mode);await tick();h.q('.rist-legend-edit-button').click();
    const toggle=h.q('.rist-legend-appearance .rist-legend-visible-toggle');
    assert(toggle.classList.contains('rist-legend-panel-button'));
    const remove=h.q('.is-sample .rist-legend-sample-delete');
    assert(remove.classList.contains('rist-legend-panel-button'));
    assert(remove.closest('.rist-legend-sample-style'));
    assert.equal(remove.previousElementSibling.className,'rist-legend-sample-width-label');
    assert.equal(remove.parentNode.className,'rist-legend-sample-width-actions');
    toggle.click();await tick();assert.equal(toggle.textContent,'범례 숨김');assert(toggle.classList.contains('is-active'));
    toggle.click();await tick();assert.equal(toggle.textContent,'범례 표시');assert(!toggle.classList.contains('is-active'));
  });
  test(label+': Raw trees keep mixed-sample groups separate; renaming preserves pending edits',async t=>{
    const h=setup(t,kind,mode);await tick();
    const parent=clone(h.gd.data[0]);parent.name='Other raw.txt';parent.meta.rist_sample_group=parent.legendgroup='sample:1';
    const peak=clone(h.gd.data[1]);peak.meta.rist_sample_group=peak.meta.rist_peak.sample_group='sample:1';
    peak.meta.rist_legend_edit_group=peak.meta.rist_peak.label_key='other';
    h.gd.data.push(parent,peak);
    for(const i of [1,3]) {h.gd.data[i].meta.rist_peak.manual_group_key='manual-peak-group:shared';h.gd.data[i].meta.rist_peak.group_name='Shared group';}
    h.dispatch('rist-plot-data-replaced');h.q('.rist-legend-edit-button').click();
    const sources=()=>[...h.gd.querySelectorAll('.rist-legend-raw-name')].map(n=>n.textContent);
    assert.deepEqual(sources(),['Sample','Other raw.txt']);
    assert.equal(h.gd.querySelectorAll('.rist-legend-raw-tree .is-group-member').length,2);
    assert.equal(h.gd.querySelectorAll('.rist-legend-manual-groups .rist-legend-group-row').length,1);
    assert.equal(h.gd.querySelectorAll('.rist-legend-manual-groups .is-peak').length,0);
    h.q('.rist-legend-peak-details').open=true;
    const pending=h.q('.is-peak textarea');pending.value='아직 저장하지 않은 피크';
    h.gd.data[0].name='Renamed<br>sample.dpt';h.dispatch('rist-legend-name-change');
    assert.equal(sources()[0],'Renamed\nsample.dpt');
    assert.equal(h.q('.is-peak textarea'),pending);assert.equal(pending.value,'아직 저장하지 않은 피크');
    assert(h.q('.rist-legend-peak-details').open);
    h.q('.is-sample textarea').value='Saved sample';h.q('.rist-legend-edit-save-all').click();await tick();
    assert.equal(sources()[0],'Saved sample');
    h.gd.data[0].name='<img src=x onerror=alert(1)>Safe raw';h.dispatch('rist-legend-name-change');
    assert.equal(sources()[0],'Safe raw');assert.equal(h.q('.rist-legend-raw-name img'),null);
    h.gd.data[1].meta.rist_sample_group=h.gd.data[1].meta.rist_peak.sample_group='missing';
    h.dispatch('rist-peak-sensitivity-change');assert.equal(sources()[2],'소속 샘플 확인 불가');
    assert.equal(h.q('.rist-legend-raw-tree[data-sample-group="missing"] .is-peak').getAttribute('data-curve'),'1');
    assert(h.q('.rist-legend-peak-details').open);
  });
  test(label+': Raw trees group by stable identity, show positions/counts and retain collapse through refresh/reorder',async t=>{
    const h=setup(t,kind,mode);await tick();h.gd.data[0].name='Same raw';
    const parent=clone(h.gd.data[0]);parent.legendgroup=parent.meta.rist_sample_group='sample:1';
    const other=clone(h.gd.data[1]);other.meta.rist_sample_group=other.meta.rist_peak.sample_group='sample:1';
    other.meta.rist_legend_edit_group=other.meta.rist_peak.label_key='other';
    const second=clone(h.gd.data[1]);second.meta.rist_legend_edit_group=second.meta.rist_peak.label_key='second';
    second.meta.rist_peak.x=250.25;second.x=[250.25];second.meta.rist_peak.source='user';
    h.gd.data.push(parent,other,second);h.dispatch('rist-plot-data-replaced');
    h.q('.rist-legend-edit-button').click();
    const tree=key=>h.q('.rist-legend-raw-tree[data-sample-group="'+key+'"]');
    assert.equal(h.gd.querySelectorAll('.rist-legend-raw-tree').length,2);
    assert.equal(tree('sample:0').querySelectorAll('.is-peak').length,2);
    assert.equal(tree('sample:1').querySelectorAll('.is-peak').length,1);
    assert.equal(tree('sample:0').querySelector('.rist-legend-raw-count').textContent,'피크 2개');
    assert.equal(tree('sample:0').querySelector('[data-curve="4"] .rist-legend-peak-info').textContent,'250.3 cm⁻¹');
    const before=clone(h.gd.data),layout=clone(h.gd.layout);
    tree('sample:0').open=false;
    assert.deepEqual(clone(h.gd.data),before);assert.deepEqual(clone(h.gd.layout),layout);
    h.gd.data[1].meta.rist_peak.sensitivity_min=25;h.gd._ristPeakSensitivityValue=0;
    h.dispatch('rist-peak-sensitivity-change');
    assert(!tree('sample:0').open);assert.equal(tree('sample:0').querySelector('.rist-legend-raw-count').textContent,'피크 1개');
    h.gd._ristPeakSensitivityValue=100;h.dispatch('rist-peak-sensitivity-change');
    assert(!tree('sample:0').open);assert.equal(tree('sample:0').querySelector('.rist-legend-raw-count').textContent,'피크 2개');
    h.gd.data[2].legendrank=0;h.gd.data[0].legendrank=10;h.dispatch('rist-'+kind+'-stack-change');
    assert.equal(h.q('.rist-legend-raw-tree').getAttribute('data-sample-group'),'sample:1');assert(!tree('sample:0').open);
    h.q('.rist-legend-edit-close').click();h.q('.rist-legend-edit-button').click();assert(!tree('sample:0').open);
    await h.gd._ristDeleteSample(0);await tick();assert.equal(tree('sample:0'),null);assert(tree('sample:1'));
    h.gd._ristHistory.undo();await tick();assert(tree('sample:0'));assert(!tree('sample:0').open);
  });
  test(label+': deleting the last peak leaves an empty Raw branch and undo restores its count',async t=>{
    const h=setup(t,kind,mode);await tick();h.q('.rist-legend-edit-button').click();
    h.q('.is-peak .rist-legend-peak-delete').click();h.q('.rist-legend-raw-tree').open=false;
    h.q('.rist-legend-edit-save-all').click();await tick();
    assert.equal(h.gd.data.length,1);assert.equal(h.q('.rist-legend-raw-count').textContent,'피크 0개');
    assert(h.q('.rist-legend-raw-empty'));assert(!h.q('.rist-legend-raw-tree').open);
    h.gd._ristHistory.undo();await tick();
    assert.equal(h.q('.rist-legend-raw-count').textContent,'피크 1개');assert.equal(h.q('.rist-legend-raw-empty'),null);
  });
  test(label+': rapid deletes atomically publish traces, labels and connectors, preserving user notes',async t=>{
    const h=setup(t,kind,mode);shapeFixture(h);await tick();
    const raw=clone(h.gd.data[0]),peak=clone(h.gd.data[1]);
    const note=clone(h.gd.layout.annotations[1]),box=clone(h.gd.layout.shapes[1]);
    const ann=clone(h.gd.layout.annotations[0]),line=clone(h.gd.layout.shapes[0]);
    h.gd.data=[h.gd.data[0]];h.gd.layout.annotations=[];h.gd.layout.shapes=[];h.gd.layout.meta.ristPeakLabels=[];
    for(let i=0;i<3;i++){
      const p=clone(peak);p.name='Peak '+i;p.x=[200+i*10];p.meta.rist_legend_edit_group=p.meta.rist_peak.label_key='peak:'+i;
      h.gd.data.push(p);h.gd.layout.annotations.push({...ann,text:'Label '+i});h.gd.layout.shapes.push({...line,x0:200+i*10,x1:200+i*10});
      h.gd.layout.meta.ristPeakLabels.push({traceIndex:i+1,annotationIndex:i,shapeIndex:i,labelKey:'peak:'+i});
    }
    h.gd.layout.annotations.push(note);h.gd.layout.shapes.push(box);
    h.gd._ristFtirUnitOriginalAnnotations=clone(h.gd.layout.annotations);h.gd._ristFtirUnitOriginalShapes=clone(h.gd.layout.shapes);
    h.dispatch('rist-plot-data-replaced');await tick();
    const pending=[];
    for(const name of ['react','deleteTraces']){
      const original=h.w.Plotly[name];
      h.w.Plotly[name]=(...args)=>{original(...args);return new Promise(resolve=>pending.push(resolve));};
    }
    const remove=()=>h.gd.dispatchEvent(new h.w.CustomEvent('rist-peak-delete',{detail:{curves:[1]}}));
    remove();remove();assert.equal(pending.length,2);
    pending.splice(0).forEach(resolve=>resolve());await tick();
    assert.deepEqual(clone(h.gd.data[0]),raw);assert.equal(h.gd.data.length,2);
    assert.equal(h.gd.data[1].name,'Peak 2');
    assert.deepEqual([...h.gd.layout.annotations].map(a=>a.text),['Label 2',note.text]);
    assert.equal(h.gd.layout.shapes.length,2);assert.deepEqual(clone(h.gd.layout.shapes[1]),box);
    assert.equal(h.gd.layout.meta.ristPeakLabels.length,1);
    assert.equal(h.gd.layout.meta.ristPeakLabels[0].traceIndex,1);
    assert.equal(h.gd.layout.meta.ristPeakLabels[0].annotationIndex,0);
    assert.equal(h.gd.layout.meta.ristPeakLabels[0].shapeIndex,0);
    assert.deepEqual(clone(h.gd._ristFtirUnitOriginalAnnotations),clone(h.gd.layout.annotations));
  });
  if(kind==='ftir')test(label+': transmittance deletion removes cached absorbance labels and undo restores them',async t=>{
    const h=setup(t,kind,mode);shapeFixture(h);await tick();
    const annotations=clone(h.gd._ristFtirUnitOriginalAnnotations),shapes=clone(h.gd._ristFtirUnitOriginalShapes);
    h.gd._ristFtirSignalMode='transmittance';h.gd.layout.annotations=[];h.gd.layout.shapes=[];
    h.gd.dispatchEvent(new h.w.CustomEvent('rist-peak-delete',{detail:{curves:[1]}}));await tick();
    assert.equal(h.gd.data.length,1);assert.equal(h.gd.layout.meta.ristPeakLabels.length,0);
    assert.deepEqual(clone(h.gd._ristFtirUnitOriginalAnnotations),[annotations[1]]);
    assert.deepEqual(clone(h.gd._ristFtirUnitOriginalShapes),[shapes[1]]);
    assert.equal(h.gd.layout.annotations.length,0);assert.equal(h.gd.layout.shapes.length,0);
    h.gd._ristHistory.undo();await tick();assert.equal(h.gd.data.length,2);
    assert.deepEqual(clone(h.gd._ristFtirUnitOriginalAnnotations),annotations);
    h.gd._ristHistory.redo();await tick();assert.equal(h.gd.data.length,1);
    assert.deepEqual(clone(h.gd._ristFtirUnitOriginalAnnotations),[annotations[1]]);
  });
  test(label+': shared group edits, selected additions, removals and clear keep Raw ownership intact',async t=>{
    const h=setup(t,kind,mode);await tick();
    const parent=clone(h.gd.data[0]);parent.name='Other';parent.meta.rist_sample_group=parent.legendgroup='sample:1';
    const other=clone(h.gd.data[1]);other.meta.rist_sample_group=other.meta.rist_peak.sample_group='sample:1';
    other.meta.rist_legend_edit_group=other.meta.rist_peak.label_key='other';
    const extra=clone(h.gd.data[1]);extra.meta.rist_legend_edit_group=extra.meta.rist_peak.label_key='extra';extra.meta.rist_peak.selected=true;
    h.gd.data.push(parent,other,extra);
    for(const i of [1,3]) Object.assign(h.gd.data[i].meta.rist_peak,{manual_group_key:'manual-peak-group:Shared',group_name:'Shared'});
    h.dispatch('rist-plot-data-replaced');h.q('.rist-legend-edit-button').click();
    h.q('.rist-legend-group-add').click();h.q('.rist-legend-group-title').value='Renamed';
    h.q('.rist-legend-group-color').value='#00aa55';h.q('.rist-legend-edit-save-all').click();await tick();
    for(const i of [1,3,4]) {assert.equal(h.gd.data[i].meta.rist_peak.group_name,'Renamed');assert.equal(h.gd.data[i].marker.color,'#00aa55');}
    assert.equal(h.gd.querySelectorAll('.rist-legend-group-row').length,1);
    assert.equal(h.gd.querySelectorAll('.rist-legend-raw-tree[data-sample-group="sample:0"] .is-peak').length,2);
    assert.equal(h.gd.querySelectorAll('.rist-legend-raw-tree[data-sample-group="sample:1"] .is-peak').length,1);
    h.q('.is-peak[data-curve="3"] .rist-legend-group-remove').click();h.q('.rist-legend-edit-save-all').click();await tick();
    assert(!h.gd.data[3].meta.rist_peak.manual_group_key);assert.equal(h.gd.data[1].meta.rist_peak.group_name,'Renamed');
    h.q('.rist-legend-group-clear').click();h.q('.rist-legend-edit-save-all').click();await tick();
    assert.equal(h.gd.querySelectorAll('.rist-legend-group-row').length,0);assert.equal(h.gd.querySelectorAll('.rist-legend-raw-tree .is-peak').length,3);
    for(const i of [1,3,4]) assert(!h.gd.data[i].meta.rist_peak.manual_group_key);
  });
  if(kind==='raman') test(label+': Raw deletion removes only its ratios and undo restores their peak references',async t=>{
    const h=setup(t,kind,mode);await tick();
    const peak=clone(h.gd.data[1]);peak.x=[250];peak.meta.rist_peak.x=250;peak.meta.rist_peak.label_key='peak:2';
    h.gd.data.push(peak);
    h.gd.layout.meta.ristRamanRatios=[{id:1,numeratorKey:'["sample:0",200]',denominatorKey:'["sample:0",250]'}];
    h.run('var ratioMode =');h.dispatch('rist-plot-data-replaced');await tick();
    assert.equal(h.gd.layout.meta.ristRamanRatios.length,1);
    assert(h.gd.layout.annotations.some(a=>a.name==='rist_raman_ratio:1'));
    await h.gd._ristDeleteSample(0);await tick();
    assert.equal(h.gd.layout.meta.ristRamanRatios.length,0);assert(!h.gd.layout.annotations.some(a=>a.name==='rist_raman_ratio:1'));
    h.gd._ristHistory.undo();await tick();
    assert.equal(h.gd.layout.meta.ristRamanRatios.length,1);assert(h.gd.layout.annotations.some(a=>a.name==='rist_raman_ratio:1'));
    assert.equal(h.gd.layout.meta.ristRamanRatios[0].numeratorCurve,1);
    assert.equal(h.gd.layout.meta.ristRamanRatios[0].denominatorCurve,2);
  });
  test(label+': invalid width blocks the whole save without changing names or colors; bounds/decimals work',async t=>{
    const h=setup(t,kind,mode),before=clone(h.gd.data);
    h.q('.rist-legend-edit-button').click();
    const width=h.q('.rist-legend-sample-width');
    h.q('.is-sample .rist-legend-edit-input').value='pending name';
    h.q('.is-sample .rist-legend-color-input').value='#abcdef';
    for(const value of ['', '0', '-1', '20.1', 'Infinity', 'text']) {
      width.value=value;h.q('.rist-legend-edit-save-all').click();await tick();
      assert.equal(width.getAttribute('aria-invalid'),'true');
      assert.match(h.q('.rist-legend-edit-error').textContent,/0.1~20/);
      assert.deepEqual(clone(h.gd.data),before);assert.equal(h.q('.rist-legend-edit-panel').style.display,'block');
    }
    for(const value of ['0.1','20','1.25']) {
      h.q('.rist-legend-sample-width').value=value;h.q('.rist-legend-edit-save-all').click();await tick();
      assert.equal(h.gd.data[0].line.width,Number(value));h.q('.rist-legend-edit-button').click();
      assert.equal(h.q('.rist-legend-edit-error').textContent,'');
    }
  });
  if(kind==='raman') test(label+': highlight reset preserves newly saved width and new RAW does not inherit a stale width cache',async t=>{
    const h=setup(t,kind,mode);h.gd.data[0].line={width:1.5,color:'#123456'};
    h.run('var DIM =');h.gd._snapCurve=0;h.gd._lastPtr={modifier:true};
    h.emit('plotly_doubleclick');await tick();assert.equal(h.gd.data[0].line.width,1.5*2.2);
    h.q('.rist-legend-edit-button').click();assert.equal(h.q('.rist-legend-sample-width').value,'1.5');
    h.q('.rist-legend-sample-width').value='4';h.q('.rist-legend-edit-save-all').click();await tick();
    h.q('.rist-legend-edit-close').click();
    h.emit('plotly_doubleclick');await tick();h.emit('plotly_doubleclick');await tick();
    assert.equal(h.gd.data[0].line.width,4);
    h.gd.data[0].line.width=0.5;h.dispatch('rist-plot-data-replaced');
    h.emit('plotly_doubleclick');await tick();assert.equal(h.gd.data[0].line.width,0.5*2.2);
    h.emit('plotly_doubleclick');await tick();assert.equal(h.gd.data[0].line.width,0.5);
  });
  test(label+': peak name updates its legend/annotation/original cache, preserves values, and undoes/redoes',async t=>{
    const h=setup(t,kind,mode),before=clone(h.gd.layout.annotations[0]),raw=clone(h.gd.data[1]);
    const inp=h.open();assert.equal(inp.value,'C–O');assert.equal(inp.getAttribute('aria-label'),'피크 라벨 이름 수정');
    inp.value='C–O 결합\n사용자 지정';h.key(inp,'Enter');await tick();
    assert.equal(h.gd.data[1].name,'C–O 결합<br>사용자 지정');
    assert.match(h.gd.layout.annotations[0].text,/<b>200 cm⁻¹<\/b>.*C–O 결합<br>사용자 지정/);
    assert.equal(h.gd.layout.annotations[0].text,h.gd._ristFtirUnitOriginalAnnotations[0].text);
    assert.deepEqual(clone({...h.gd.layout.annotations[0],text:before.text}),before);
    assert.deepEqual(clone({...h.gd.data[1],name:raw.name}),raw);
    h.gd._ristHistory.undo();await tick();assert.equal(h.gd.data[1].name,'C–O');
    assert.equal(h.gd._ristFtirUnitOriginalAnnotations[0].text,before.text);
    h.gd._ristHistory.redo();await tick();assert.match(h.gd.layout.annotations[0].text,/사용자 지정/);
  });
  test(label+': text box multiline/empty/literal markup changes preserve geometry, styling and FTIR cache',async t=>{
    const h=setup(t,kind,mode),before=clone(h.gd.layout.annotations[1]);
    let inp=h.open(1);assert.equal(inp.value,'A & B\n메모');
    inp.value='직접 수정\n<실험> & 결과';h.key(inp,'Enter');await tick();
    assert.equal(h.gd.layout.annotations[1].text,'직접 수정<br>&lt;실험&gt; &amp; 결과');
    assert.deepEqual(clone({...h.gd.layout.annotations[1],text:before.text}),before);
    inp=h.open(1);assert.equal(inp.value,'직접 수정\n<실험> & 결과');
    inp.value='';h.key(inp,'Enter');await tick();assert.equal(h.gd.layout.annotations[1].text,'');
    assert.equal(h.gd._ristFtirUnitOriginalAnnotations[1].text,'');
  });
  test(label+': Korean composition, Shift+Enter, Escape and blur have safe save semantics',async t=>{
    const h=setup(t,kind,mode);let inp=h.open();inp.value='조합 중';
    inp.dispatchEvent(new h.w.CompositionEvent('compositionstart'));h.key(inp,'Enter');assert.equal(h.editor(),inp);
    inp.dispatchEvent(new h.w.CompositionEvent('compositionend'));h.key(inp,'Enter',{isComposing:true});assert.equal(h.editor(),inp);
    h.key(inp,'Enter',{keyCode:229});assert.equal(h.editor(),inp);
    h.key(inp,'Enter',{shiftKey:true});assert.equal(h.editor(),inp);
    h.key(inp,'Escape');assert.equal(h.editor(),null);assert.equal(h.gd.data[1].name,'C–O');
    assert.equal(h.q('.rist-history-undo').disabled,true);
    inp=h.open();h.key(inp,'Enter');assert.equal(h.q('.rist-history-undo').disabled,true);
    inp=h.open();inp.value='Blur saved';await new Promise(r=>setTimeout(r,270));
    inp.dispatchEvent(new h.w.FocusEvent('blur'));await tick();assert.equal(h.gd.data[1].name,'Blur saved');
  });
  test(label+': double-click legend preserves sample visibility and renders multiline editable text',async t=>{
    const h=setup(t,kind,mode);h.gd.data[0].name='Sample<br>한글';
    h.mouse(h.q('.legendtoggle'),'dblclick');let inp=h.editor();assert.equal(inp.value,'Sample\n한글');
    inp.value='시료 변경';h.key(inp,'Enter');await tick();
    assert.equal(h.gd.data[0].name,'시료 변경');assert.equal(h.gd.data[0].visible,undefined);
    assert.equal(h.q('.rist-legend-edit-button').textContent,'범례·스타일');
  });
  test(label+': data replacement, structural change or mode change cannot apply a stale edit',async t=>{
    const h=setup(t,kind,mode);
    for (const event of ['rist-plot-data-replaced','rist-plot-structure-changed','rist-history-restored']) {
      const inp=h.open();inp.value='stale';h.dispatch(event);h.key(inp,'Enter');
      assert.equal(h.editor(),null);assert.equal(h.gd.data[1].name,'C–O');
    }
    const inp=h.open();inp.value='cancelled';
    h.gd.dispatchEvent(new h.w.CustomEvent('rist-exclusive-interaction-start',{detail:{mode:'axis-crop'}}));
    assert.equal(h.editor(),null);assert.equal(h.gd.data[1].name,'C–O');
  });
  test(label+': first click on a label cannot add/delete/select its peak before the double-click',async t=>{
    const h=setup(t,kind,mode);
    for (const button of ['.rist-peak-add-button','.rist-peak-delete-button','.rist-peak-select-button']) {
      h.q(button).click();const target=h.q('.annotation[data-index="0"] .annotation-text');
      h.mouse(target,'mousedown');h.mouse(target,'mouseup');h.mouse(target,'click');
      await tick();assert.equal(h.gd.data.length,2);assert.equal(h.editor(),null);
      h.mouse(target,'dblclick');assert.ok(h.editor());assert.equal(h.gd._ristPeakEditMode,'none');
      h.key(h.editor(),'Escape');
    }
  });
  test(label+': pointer double-tap works through shape overlay; native + Plotly single click is not double-counted',async t=>{
    const h=setup(t,kind,mode,true),overlay=h.d.createElement('div');overlay.className='rist-shape-interaction-layer';h.gd.appendChild(overlay);
    h.mouse(overlay,'pointerdown');h.mouse(overlay,'pointerup');
    h.emit('plotly_clickannotation',{index:0,annotation:h.gd.layout.annotations[0]});assert.equal(h.editor(),null);
    h.mouse(overlay,'pointerdown');assert.ok(h.editor());h.key(h.editor(),'Escape');
    h.mouse(overlay,'pointerup');
  });
  test(label+': label drag is not a double-tap; cancellation resets pending pointer state',async t=>{
    const h=setup(t,kind,mode,true),target=h.q('.annotation[data-index="0"] .annotation-text');
    h.mouse(target,'pointerdown');h.mouse(target,'pointermove',240,260);h.mouse(target,'pointerup',240,260);
    h.mouse(target,'pointerdown');assert.equal(h.editor(),null);h.mouse(target,'pointerup');
    h.dispatch('pointercancel');h.mouse(target,'pointerdown');assert.equal(h.editor(),null);
  });
  test(label+': Plotly-only annotation fallback edits just the addressed peak; ratio annotations are excluded',async t=>{
    const h=setup(t,kind,mode);
    h.gd.layout.annotations.push({name:'rist_raman_ratio:1',text:'I(D)/I(G)'});
    h.emit('plotly_clickannotation',{index:2});h.emit('plotly_clickannotation',{index:2});assert.equal(h.editor(),null);
    h.emit('plotly_clickannotation',{index:0});assert.equal(h.editor(),null);
    h.emit('plotly_clickannotation',{index:0});assert.ok(h.editor());
  });
  test(label+': single-click shape panel waits for double-click, and cannot reopen over the inline editor',async t=>{
    const h=setup(t,kind,mode);shapeFixture(h);
    h.emit('plotly_clickannotation',{index:1,annotation:h.gd.layout.annotations[1]});
    assert.notEqual(h.q('.rist-shape-editor-panel').style.display,'block');
    h.emit('plotly_clickannotation',{index:1,annotation:h.gd.layout.annotations[1]});
    assert.equal(h.editor().getAttribute('aria-label'),'텍스트 박스 내용 수정');
    await new Promise(r=>setTimeout(r,550));assert.notEqual(h.q('.rist-shape-editor-panel').style.display,'block');
  });
  test(label+': later shape styling preserves multiline text/entities/empty content',async t=>{
    const h=setup(t,kind,mode);shapeFixture(h);
    h.emit('plotly_clickannotation',{index:1,annotation:h.gd.layout.annotations[1]});
    await new Promise(r=>setTimeout(r,550));assert.equal(h.q('.rist-shape-editor-panel').style.display,'block');
    assert.equal(h.q('.rist-shape-editor-text').value,'A & B\n메모');
    const size=h.q('.rist-shape-font-size');size.value='20';size.dispatchEvent(new h.w.Event('input',{bubbles:true}));await tick();
    assert.equal(h.gd.layout.annotations[1].text,'A &amp; B<br>메모');assert.equal(h.gd.layout.annotations[1].font.size,20);
    const text=h.q('.rist-shape-editor-text');text.value='';text.dispatchEvent(new h.w.Event('input',{bubbles:true}));await tick();
    assert.equal(h.gd.layout.annotations[1].text,'');
  });
  test(label+': empty text box remains editable by hitting its bounds',async t=>{
    const h=setup(t,kind,mode);shapeFixture(h);h.gd.layout.annotations[1].text='';
    const overlay=h.d.createElement('div');h.gd.appendChild(overlay);
    h.mouse(overlay,'dblclick',550,300);assert.equal(h.editor().value,'');
    h.editor().value='빈 박스에 다시 입력';h.key(h.editor(),'Enter');await tick();
    assert.equal(h.gd.layout.annotations[1].text,'빈 박스에 다시 입력');
  });
  test(label+': shape layer reordering and deletion keep peak annotation/shape indexes correct',async t=>{
    const h=setup(t,kind,mode);shapeFixture(h);
    h.emit('plotly_clickannotation',{index:1,annotation:h.gd.layout.annotations[1]});
    h.q('.rist-shape-send-backward').click();await tick();
    assert.equal(h.gd.layout.annotations[0].name,'rist_text_box_text:1');
    assert.equal(h.gd.layout.meta.ristPeakLabels[0].annotationIndex,1);assert.equal(h.gd.layout.meta.ristPeakLabels[0].shapeIndex,1);
    assert.equal(h.gd._ristFtirUnitOriginalAnnotations[0].name,'rist_text_box_text:1');
    h.q('.annotation[data-index="0"]').setAttribute('data-index','1');
    h.q('.annotation[data-index="1"] .annotation-text').textContent='200 cm⁻¹ C–O';
    const inp=h.open(1);assert.equal(inp.getAttribute('aria-label'),'피크 라벨 이름 수정');
    inp.value='재정렬 후 피크';h.key(inp,'Enter');await tick();
    assert.match(h.gd.layout.annotations[1].text,/재정렬 후 피크/);assert.match(h.gd.layout.annotations[0].text,/메모/);
    h.emit('plotly_clickannotation',{index:0,annotation:h.gd.layout.annotations[0]});
    h.q('.rist-shape-delete').click();await tick();
    assert.equal(h.gd.layout.meta.ristPeakLabels[0].annotationIndex,0);assert.equal(h.gd.layout.meta.ristPeakLabels[0].shapeIndex,0);
    assert.match(h.gd.layout.annotations[0].text,/재정렬 후 피크/);
  });
}

const {test:nodeTest} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const source = fs.readFileSync(path.join(__dirname, '../../app/raman_ratio.js'), 'utf8');
const rangesSource = fs.readFileSync(path.join(__dirname, '../../app/raman_ratio_ranges.js'), 'utf8');

function figure() {
  const parent = (group, name) => ({name, x:[0,100], y:[0,100], mode:'lines',
    meta:{rist_sample_group:group, rist_sample_parent:true}});
  const peak = (group, name, x, y, base) => ({name, x:[x], y:[y], mode:'markers',
    meta:{rist_sample_group:group, rist_raman_stack_offset:y-base,
      rist_peak:{x, base_y:base, label:name, sample_group:group}}});
  return {data:[parent('a','시료 A'), peak('a','D band',30,30,10),
    peak('a','G band',60,60,20), parent('b','시료 B'),
    peak('b','B overlap',30.3,30.3,50), peak('a','Zero',10,10,0)],
    layout:{meta:{},annotations:[],shapes:[]}};
}
function setupFixture(t, hover=true, variant='native') {
  const dom = new JSDOM('<div id="raman-plot"><div class="nsewdrag"></div><svg><g class="scatterlayer"></g></svg></div>',
    {runScripts:'outside-only', pretendToBeVisual:true, url:'http://localhost/'});
  t.after(()=>dom.window.close());
  const w=dom.window, gd=w.document.getElementById('raman-plot');
  w.matchMedia=()=>({matches:hover});
  Object.assign(gd, figure());
  Object.defineProperties(gd,{clientWidth:{value:900},clientHeight:{value:650}});
  gd.getBoundingClientRect=()=>({left:0,top:0,width:900,height:650,right:900,bottom:650});
  const drag=gd.querySelector('.nsewdrag');
  drag.getBoundingClientRect=()=>({left:50,top:100,width:800,height:450,right:850,bottom:550});
  gd._fullLayout={xaxis:{_length:800,d2p:x=>x*8,p2d:x=>x/8},yaxis:{_length:450,d2p:y=>450-y*4.5}};
  gd.data.forEach(trace=>{
    const node=w.document.createElementNS('http://www.w3.org/2000/svg','g');
    node.classList.add('trace'); node.__data__=[{trace}];
    gd.querySelector('.scatterlayer').appendChild(node);
  });
  const events={}, stats={captures:0,relayouts:0};
  gd.on=(name,fn)=>{(events[name]||(events[name]=[])).push(fn);};
  const emit=(name,payload)=>{(events[name]||[]).forEach(fn=>fn(payload));};
  gd._ristHistory={capture:()=>stats.captures++};
  w.Plotly={relayout:(_,updates)=>{
    stats.relayouts++; Object.assign(gd.layout,updates); emit('plotly_afterplot');
    return Promise.resolve();
  }};
  if(variant==='chrome49') {
    const root=path.join(__dirname,'../../app/static/chrome49');
    const manifest=JSON.parse(fs.readFileSync(path.join(root,'manifest.json'),'utf8'));
    w.eval(fs.readFileSync(path.join(root,manifest.runtime),'utf8'));
    for (const program of [rangesSource,source]) {
      const key=require('node:crypto').createHash('sha256').update(program.trim()).digest('hex');
      assert.ok(manifest.scripts[key],'rebuild compatibility bundle after modifying the source');
      w.eval(fs.readFileSync(path.join(root,manifest.scripts[key]),'utf8'));
    }
  } else { w.eval(rangesSource); w.eval(source); }
  const q=s=>gd.querySelector(s);
  const tick=()=>new Promise(resolve=>w.setTimeout(resolve,45));
  function pointer(type, x, y) {
    drag.dispatchEvent(new w.MouseEvent(type,{bubbles:true,cancelable:true,button:0,clientX:50+x*8,clientY:550-y*4.5}));
  }
  function selectSample(value) { q('[data-selection-sample]').value=value; q('[data-selection-sample]').dispatchEvent(new w.Event('change')); }
  function begin(value='') { q('.rist-raman-ratio-button').click(); if(value) selectSample(value); }
  function click(x,y) {pointer('mousedown',x,y);pointer('click',x,y);}
  const custom=(name,detail)=>gd.dispatchEvent(new w.CustomEvent(name,{detail}));
  return {w,gd,q,tick,pointer,selectSample,begin,click,custom,emit,stats};
}

function batchFixture(t,variant) {
  const f=setupFixture(t,true,variant);
  const raw=(g,ys,offset)=>({name:'시료 '+g,x:ys.map((_,i)=>i*10),y:ys.map(y=>y+offset),customdata:ys.slice(),mode:'lines',
    meta:{rist_sample_group:g,rist_sample_parent:true,rist_raman_stack_offset:offset}});
  f.gd.data=[raw('A',[1,3,8,4,1,1,2,4,2,1,1],20),raw('B',[1,4,6,3,2,2,3,12,4,1,1],50)];
  f.gd.layout.meta={ristPeakSensitivity:0};
  f.custom('rist-plot-data-replaced');
  f.q('.rist-ratio-range-surface').getBoundingClientRect=()=>({left:50,top:100,width:800,height:450,right:850,bottom:550});
  f.start=()=>f.q('[data-ratio-batch]').click();
  f.range=(role,a,b)=>{
    const inputs=f.q('[data-batch-pick="'+role+'"]').parentNode.querySelectorAll('input');
    inputs[0].value=a;inputs[1].value=b;inputs[1].dispatchEvent(new f.w.Event('change'));
  };
  f.review=()=>{f.range('numerator',10,40);f.range('denominator',60,90);};
  f.apply=async()=>{f.q('[data-batch-apply]').click();await f.tick();};
  return f;
}

for(const variant of ['native','chrome49']) {
  const test=(name,fn)=>nodeTest(variant+' batch: '+name,fn);
  test('raw maxima independent of zero sensitivity and stack; checked samples only; one undo',async t=>{
    const f=batchFixture(t,variant), original=JSON.stringify(f.gd.data);f.start();f.review();
    assert.equal(f.gd.layout.meta.ristRamanRatios,undefined);
    assert.match(f.q('tbody').textContent,/20 cm⁻¹ \/ 8/);
    assert.match(f.q('tbody').textContent,/70 cm⁻¹ \/ 4/);
    f.q('[data-batch-row="1"]').checked=false;await f.apply();
    const [r]=f.gd.layout.meta.ristRamanRatios;
    assert.equal(r.method,'raw-range-max');assert.equal(r.numeratorPoint.intensity,8);assert.equal(r.denominatorPoint.intensity,4);
    assert.equal(f.gd.layout.meta.ristRamanRatios.length,1);assert.equal(f.stats.captures,1);
    assert.equal(JSON.stringify(f.gd.data),original);assert.equal(f.gd._ristRamanRatioMode,false);
    assert.match(f.gd.layout.annotations[0].text,/2.000/);
  });
  test('reapply replaces batch result, manual ratios survive; title edit and individual delete',async t=>{
    const f=batchFixture(t,variant);f.start();f.review();await f.apply();
    const id=f.gd.layout.meta.ristRamanRatios[0].id;
    f.start();f.review();await f.apply();
    assert.equal(f.gd.layout.meta.ristRamanRatios.length,2);assert.equal(f.gd.layout.meta.ristRamanRatios[0].id,id);
    f.q('.rist-raman-ratio-button:nth-child(2)').click();f.q('[data-ratio-id="'+id+'"]').click();
    assert.equal(f.q('[data-ratio-numerator]').disabled,true);
    f.q('[data-ratio-title]').value='D/G 확인';f.q('[data-ratio-form]').dispatchEvent(new f.w.Event('submit',{cancelable:true}));await f.tick();
    assert.equal(f.gd.layout.meta.ristRamanRatios[0].title,'D/G 확인');
    f.q('.rist-raman-ratio-button:nth-child(2)').click();f.q('[data-ratio-delete]').click();await f.tick();
    assert.equal(f.gd.layout.meta.ristRamanRatios.length,1);
  });
  test('missing, sparse, zero denominator invalid; edge/ties/negative warn; hidden samples excluded',t=>{
    const f=batchFixture(t,variant), api=f.w.RistRamanRanges;
    const tr=f.gd.data[0], r={numerator:[10,40],denominator:[60,90]};
    tr.customdata=[1,3,8,8,1,1,0,0,0,0,1];
    let rows=api.rows([tr],r);assert.match(rows[0].error,/분모/);assert.match(rows[0].warnings.join(),/동일 최고값/);
    assert.equal(rows[0].numeratorPoint.x,20);
    assert.match(api.rows([tr],{numerator:[11,19],denominator:[60,90]})[0].error,/없음/);
    assert.match(api.rows([tr],{numerator:[19,21],denominator:[60,90]})[0].error,/2점 미만/);
    tr.customdata=[1,-8,-6,-3,-1,1,-2,-4,-2,-1,1];
    rows=api.rows([tr],r);assert.match(rows[0].warnings.join(),/경계/);assert.match(rows[0].warnings.join(),/음의 강도/);
    tr.visible='legendonly';assert.equal(api.rows([tr],r).length,0);
  });
  test('pointer drag reversed interval; cancel, blur and exclusive modes release only picker',async t=>{
    const f=batchFixture(t,variant);f.start();
    const s=f.q('.rist-ratio-range-surface');
    function ev(type,x) {s.dispatchEvent(new f.w.MouseEvent(type,{bubbles:true,cancelable:true,button:0,clientX:50+x*8,clientY:400}));}
    ev('mousedown',40);ev('mousemove',10);ev('mouseup',10);
    assert.equal(f.q('[data-batch-pick="numerator"]').parentNode.querySelector('input').value,'10');
    ev('mousedown',60);f.w.dispatchEvent(new f.w.Event('blur'));ev('mouseup',90);
    assert.equal(f.q('[data-batch-pick="denominator"]').parentNode.querySelector('input').value,'');
    f.custom('rist-exclusive-interaction-start',{mode:'axis-crop'});
    assert.equal(s.hidden,true);assert.equal(f.gd._ristRamanRatioMode,false);assert.equal(f.stats.captures,0);
    f.start();f.review();f.w.document.dispatchEvent(new f.w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
    assert.equal(f.q('.rist-ratio-batch').hidden,true);
  });
  test('stale raw is re-reviewed before commit; hidden/deleted Raw removes reportable ratios; undo restores',async t=>{
    const f=batchFixture(t,variant);f.start();f.review();f.gd.data[0].customdata[2]=10;
    await f.apply();assert.equal(f.stats.captures,0);assert.match(f.q('[data-batch-status]').textContent,/변경/);
    await f.apply();assert.equal(f.stats.captures,1);
    const saved=JSON.parse(JSON.stringify(f.gd.layout));
    f.gd.data[0].visible=false;f.custom('rist-raman-stack-change');await f.tick();assert.equal(f.gd.layout.annotations[0].visible,false);
    f.gd.data.shift();f.custom('rist-plot-structure-changed');await f.tick();assert.equal(f.gd.layout.meta.ristRamanRatios.length,1);
    f.gd.layout=saved;f.custom('rist-history-restored');await f.tick();assert.equal(f.gd.layout.meta.ristRamanRatios.length,1);
  });
  test('no selection, overlapping ranges and nonfinite points cannot silently apply',async t=>{
    const f=batchFixture(t,variant);f.start();f.range('numerator',10,40);f.range('denominator',30,70);
    assert.equal(f.q('[data-batch-apply]').disabled,true);assert.match(f.q('[data-batch-status]').textContent,/겹칩니다/);
    f.range('denominator',60,90);f.gd.querySelectorAll('[data-batch-row]').forEach(c=>c.checked=false);await f.apply();assert.equal(f.stats.captures,0);
    const tr=f.gd.data[0];tr.customdata[2]=null;tr.customdata[3]=NaN;
    assert.equal(f.w.RistRamanRanges.maximum(tr,[10,40]).x,10);
  });
  test('sequential numeric bound entry preserves partially typed values; collapsed review keeps state',t=>{
    const f=batchFixture(t,variant);f.start();
    for(const [role,a,b] of [['numerator','10','40'],['denominator','60','90']]) {
      const inputs=f.q('[data-batch-pick="'+role+'"]').parentNode.querySelectorAll('input');
      inputs[0].value=a;inputs[0].dispatchEvent(new f.w.Event('change'));assert.equal(inputs[0].value,a);
      inputs[1].value=b;inputs[1].dispatchEvent(new f.w.Event('change'));
    }
    assert.equal(f.q('[data-batch-apply]').disabled,false);
    f.q('[data-batch-collapse]').click();assert.equal(f.q('.rist-ratio-batch').classList.contains('collapsed'),true);
    f.q('[data-batch-collapse]').click();assert.equal(f.gd.querySelectorAll('[data-batch-row]').length,2);
  });
  test('batch preview uses circle/square even without peaks; stack changes only plotted positions',async t=>{
    const f=batchFixture(t,variant);f.start();f.review();
    f.q('tbody tr').dispatchEvent(new f.w.MouseEvent('mouseenter'));await f.tick();
    assert.equal(f.q('.rist-ratio-marker.numerator').hidden,false);assert.equal(f.q('.rist-ratio-marker.denominator').hidden,false);
    await f.apply();
    const before=f.gd.layout.annotations[0].y;
    f.gd.data[0].meta.rist_raman_stack_offset+=5;f.gd.data[0].y=f.gd.data[0].y.map(y=>y+5);
    f.custom('rist-raman-stack-change');await f.tick();
    assert.equal(f.gd.layout.annotations[0].y,before+5);assert.match(f.gd.layout.annotations[0].text,/2.000/);
  });
  test('render failure restores prior ratios and permits retry',async t=>{
    const f=batchFixture(t,variant);f.start();f.review();await f.apply();
    const saved=JSON.stringify(f.gd.layout.meta.ristRamanRatios), original=f.w.Plotly.relayout;
    f.start();f.range('numerator',10,30);f.range('denominator',60,90);
    let fail=true;f.w.Plotly.relayout=(...args)=>{if(fail){fail=false;return Promise.reject(new Error('render failed'));}return original(...args);};
    await f.apply();assert.equal(JSON.stringify(f.gd.layout.meta.ristRamanRatios),saved);
    assert.match(f.q('[data-batch-status]').textContent,/기존 결과/);assert.equal(f.q('[data-batch-apply]').disabled,false);
    await f.apply();assert.equal(f.q('.rist-ratio-batch').hidden,true);
  });
  test('all-peak deletion and sensitivity events retain range results but not stale Raw',async t=>{
    const f=batchFixture(t,variant);f.start();f.review();await f.apply();
    f.gd.layout.meta.ristPeakSensitivity=100;f.custom('rist-plot-structure-changed',{reason:'peak-delete-all'});await f.tick();
    assert.equal(f.gd.layout.meta.ristRamanRatios.length,2);
    f.gd.layout.meta.ristPeakSensitivity=0;f.emit('plotly_afterplot');await f.tick();
    assert.equal(f.gd.layout.meta.ristRamanRatios.length,2);
  });
}

nodeTest('native batch: touch cancellation and close restore page interaction',t=>{
  const f=batchFixture(t,'native');f.start();const surface=f.q('.rist-ratio-range-surface');
  function touch(type,x) {
    const event=new f.w.Event(type,{bubbles:true,cancelable:true});
    const point={clientX:50+x*8,clientY:400};
    Object.defineProperty(event,'touches',{value:type==='touchend'?[]:[point]});
    Object.defineProperty(event,'changedTouches',{value:[point]});
    surface.dispatchEvent(event);return event;
  }
  assert.equal(touch('touchstart',10).defaultPrevented,true);touch('touchmove',40);touch('touchend',40);
  assert.equal(f.q('[data-batch-pick="numerator"]').parentNode.querySelector('input').value,'10');
  touch('touchstart',60);surface.dispatchEvent(new f.w.Event('touchcancel'));touch('touchend',90);
  assert.equal(f.q('[data-batch-pick="denominator"]').parentNode.querySelector('input').value,'');
  f.q('[data-batch-close]').click();assert.equal(surface.hidden,true);assert.equal(f.gd._ristRamanRatioMode,false);
  assert.equal(f.w.document.body.style.touchAction,undefined);
});

for(const variant of ['native','chrome49']) {
const test=(name,fn)=>nodeTest(variant+': '+name,fn);
const setup=(t,hover=true)=>setupFixture(t,hover,variant);

test('Raw focus and mouse hover are display-only; original intensity excludes stack offset',async t=>{
  const f=setup(t); f.begin('a'); const original=JSON.stringify([f.gd.data,f.gd.layout]);
  f.pointer('mousemove',30,30); await f.tick(); await f.tick();
  assert.match(f.q('.rist-ratio-hover-tip').textContent,/시료 A.*\nD band · 30.00 cm⁻¹\n실제 강도 I = 10/);
  assert.equal(f.q('.rist-ratio-marker.hover').hidden,false);
  assert.equal(f.q('.rist-ratio-marker.numerator').hidden,true);
  assert.equal(f.gd.querySelectorAll('.rist-ratio-muted').length,2);
  assert.equal(JSON.stringify([f.gd.data,f.gd.layout]),original);
  assert.equal(f.stats.captures,0); assert.equal(f.stats.relayouts,0);
});

test('overlaps open candidates, hover/focus never commits, then denominator is sample-locked',async t=>{
  const f=setup(t); f.begin(); f.click(30,30); await f.tick();
  assert.equal(f.q('.rist-ratio-candidates').hidden,false);
  assert.equal(f.gd.querySelectorAll('[data-candidate-key]').length,2);
  assert.equal(f.q('.rist-ratio-marker.numerator').hidden,true);
  assert.equal(f.stats.captures,0);
  const candidates=f.gd.querySelectorAll('[data-candidate-key]');
  assert.match(candidates[1].textContent,/시료 B/);
  candidates[0].click(); await f.tick();
  assert.equal(f.q('[data-selection-sample]').value,'a');
  assert.equal(f.q('.rist-ratio-marker.numerator').hidden,false);
  assert.match(f.q('[data-selection-numerator]').textContent,/D band/);
  f.click(60,60); await f.tick();
  assert.equal(f.gd.layout.meta.ristRamanRatios.length,1);
  assert.equal(f.gd.layout.annotations[0].text,'I(30)/I(60) = 0.500');
  assert.equal(f.gd.layout.shapes.length,0);
  assert.equal(f.q('.rist-ratio-marker.denominator').hidden,false);
});

test('native/Plotly click duplication creates only one ratio; swap/reselect edit that ratio',async t=>{
  const f=setup(t); f.begin('a'); f.click(30,30);
  f.emit('plotly_click',{points:[{curveNumber:1}],event:{clientX:290,clientY:415}});
  assert.equal(f.stats.captures,0);
  f.click(60,60); await f.tick();
  const id=f.gd.layout.meta.ristRamanRatios[0].id;
  f.q('[data-selection-swap]').click(); await f.tick();
  assert.equal(f.gd.layout.meta.ristRamanRatios.length,1);
  assert.equal(f.gd.layout.meta.ristRamanRatios[0].id,id);
  assert.equal(f.gd.layout.annotations[0].text,'I(60)/I(30) = 2.000');
  f.q('[data-reselect="numerator"]').click(); f.click(30,30); await f.tick();
  assert.equal(f.gd.layout.meta.ristRamanRatios.length,1);
  assert.equal(f.gd.layout.annotations[0].text,'I(30)/I(30) = 1.000');
  assert.equal(f.stats.captures,3);
});

test('zero denominator is rejected; zero numerator cannot be swapped into denominator',async t=>{
  const f=setup(t); f.begin('a'); f.click(30,30); f.click(10,10);
  assert.match(f.q('[data-selection-status]').textContent,/0인 피크/);
  assert.equal(f.stats.captures,0);
  f.q('[data-selection-new]').click(); f.click(10,10); f.click(60,60); await f.tick();
  f.q('[data-selection-swap]').click();
  assert.match(f.q('[data-selection-status]').textContent,/분모가 0/);
  assert.equal(f.gd.layout.annotations[0].text,'I(10)/I(60) = 0.000');
});

test('foreign, hidden and out-of-range peaks cannot be chosen',async t=>{
  const f=setup(t); f.gd.data[4].x=[80]; f.gd.data[4].meta.rist_peak.x=80;
  f.gd.data[4].y=[80]; f.gd.data[2].visible=false;
  f.begin('a'); f.click(30,30); f.click(80,80); f.click(60,60);
  assert.equal(f.stats.captures,0);
  f.gd.data[2].visible=true; f.gd._fullLayout.xaxis.d2p=x=>x*80; f.emit('plotly_afterplot');
  await f.tick(); assert.equal(f.q('.rist-ratio-marker.numerator').hidden,true);
  assert.match(f.q('[data-selection-numerator]').textContent,/D band/);
});

test('touch uses click candidate flow without relying on hover',async t=>{
  const f=setup(t,false); f.begin(); f.pointer('mousemove',30,30); await f.tick();
  assert.equal(f.q('.rist-ratio-hover-tip').hidden,true);
  f.gd.dispatchEvent(new f.w.Event('touchstart',{bubbles:true}));
  f.pointer('click',30,30);
  assert.equal(f.q('.rist-ratio-candidates').hidden,false);
  f.q('[data-candidate-key]').click(); await f.tick();
  assert.equal(f.q('.rist-ratio-marker.numerator').hidden,false);
});

test('mutually exclusive modes and history restore clean all transient UI',async t=>{
  const f=setup(t); f.begin('a'); f.click(30,30); await f.tick();
  f.custom('rist-exclusive-interaction-start',{mode:'axis-crop'}); await f.tick();
  assert.equal(f.q('.rist-ratio-selection').hidden,true);
  assert.equal(f.q('.rist-ratio-marker.numerator').hidden,true);
  assert.equal(f.gd.classList.contains('rist-ratio-focus'),false);
  assert.equal(f.gd.querySelectorAll('.rist-ratio-muted').length,0);
  f.begin(); f.click(30,30); f.custom('rist-history-restored'); await f.tick();
  assert.equal(f.q('.rist-ratio-candidates').hidden,true);
  assert.equal(f.gd._ristRamanRatioMode,false);
});

test('trace reindex preserves chosen key; removal clears stale selection and candidates',async t=>{
  const f=setup(t); f.begin('a'); f.click(30,30);
  f.gd.data.unshift({name:'unrelated',x:[0],y:[0]}); f.emit('plotly_afterplot'); await f.tick();
  assert.match(f.q('[data-selection-numerator]').textContent,/D band/);
  f.gd.data.splice(2,1); f.custom('rist-plot-structure-changed'); await f.tick();
  assert.match(f.q('[data-selection-numerator]').textContent,/아직 선택/);
  assert.equal(f.q('.rist-ratio-marker.numerator').hidden,true);
  f.selectSample(''); f.click(30.3,30.3); f.gd.data=f.gd.data.filter(tr=>tr.meta?.rist_sample_group!=='b');
  f.emit('plotly_afterplot'); await f.tick();
  assert.equal(f.q('[data-selection-sample]').value,'');
});

test('markers follow zoom and reflow while stored intensity stays original',async t=>{
  const f=setup(t); f.begin('a'); f.click(30,30); await f.tick();
  assert.equal(f.q('.rist-ratio-marker.numerator').style.left,'290px');
  f.gd._fullLayout.xaxis.d2p=x=>x*4; f.emit('plotly_afterplot'); await f.tick();
  assert.equal(f.q('.rist-ratio-marker.numerator').style.left,'170px');
  assert.match(f.q('[data-selection-numerator]').textContent,/실제 강도 I = 10/);
});

test('completed ratio label hover shows both roles and clears on leave',async t=>{
  const f=setup(t); f.begin('a'); f.click(30,30); f.click(60,60); await f.tick();
  f.q('[data-selection-close]').click();
  const label=f.w.document.createElement('div');
  label.className='annotation'; label.setAttribute('data-index','0'); f.gd.appendChild(label);
  label.dispatchEvent(new f.w.MouseEvent('mouseover',{bubbles:true})); await f.tick();
  assert.equal(f.q('.rist-ratio-marker.numerator').hidden,false);
  assert.equal(f.q('.rist-ratio-marker.denominator').hidden,false);
  label.dispatchEvent(new f.w.MouseEvent('mouseout',{bubbles:true})); await f.tick();
  assert.equal(f.q('.rist-ratio-marker.numerator').hidden,true);
});

}

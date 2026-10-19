const {test:nodeTest} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const source = fs.readFileSync(path.join(__dirname, '../../app/raman_ratio.js'), 'utf8');

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
  gd._fullLayout={xaxis:{_length:800,d2p:x=>x*8},yaxis:{_length:450,d2p:y=>450-y*4.5}};
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
    const key=require('node:crypto').createHash('sha256').update(source.trim()).digest('hex');
    assert.ok(manifest.scripts[key],'rebuild compatibility bundle after modifying the source');
    w.eval(fs.readFileSync(path.join(root,manifest.runtime),'utf8'));
    w.eval(fs.readFileSync(path.join(root,manifest.scripts[key]),'utf8'));
  } else w.eval(source);
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

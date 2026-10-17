// Execute shipped native/Chrome49 controls; these are not Windows binary tests.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {createHash} = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const clone = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setTimeout(resolve, 40));
const pages = {};
for (const kind of ['ftir', 'raman']) {
  pages[kind] = execFileSync(process.env.RIST_TEST_PYTHON || 'python3', ['-c',
    `from app.${kind}_web import build_${kind}_page; print(build_${kind}_page())`], {encoding:'utf8'});
}
const assets = path.resolve(__dirname, '../app/static/chrome49');
const manifest = JSON.parse(fs.readFileSync(path.join(assets, 'manifest.json'), 'utf8'));

function setup(t, kind, mode) {
  const html = pages[kind];
  const dom = new JSDOM(html, {url:'http://localhost/'+kind, runScripts:'outside-only', pretendToBeVisual:true});
  const w = dom.window, d = w.document;
  t.after(() => {w.dispatchEvent(new w.Event('pagehide')); w.close();});
  const gd = d.getElementById(kind === 'ftir' ? 'peak-plot' : 'raman-plot');
  const key = kind === 'ftir' ? 'ristFtirStack' : 'ristRamanStack';
  gd.layout = {meta:{[key]:{enabled:true}}, xaxis:{range:kind === 'ftir' ? [4000,400] : [100,3000]},
    yaxis:{range:[-20,100]}, annotations:[], shapes:[], dragmode:'zoom'};
  gd.data = [{name:'Sample', x:[100,200,300], y:[-10,40,80], meta:{rist_sample_parent:true}}];
  const sync = () => {gd._fullLayout = {...clone(gd.layout), _size:{l:60,t:100,w:800,h:400}};};
  sync();
  gd.getBoundingClientRect = () => ({left:0,top:0,width:1000,height:650});
  gd._context = {scrollZoom:true};
  const events = {}, updates = [];
  gd.on = (name, fn) => {(events[name] ||= []).push(fn); return gd;};
  const emit = (name, value) => (events[name] || []).forEach(fn => fn(value));
  let captures = 0;
  gd._ristHistory = {capture:() => captures++};
  function setPath(obj, name, value) {
    const parts = name.replace(/\[(\d+)\]/g, '.$1').split('.');
    for (const part of parts.slice(0,-1)) obj = obj[part] ||= {};
    obj[parts.at(-1)] = clone(value);
  }
  w.Plotly = {
    relayout:async (_, patch) => {
      updates.push(clone(patch));
      for (const [name, value] of Object.entries(patch)) setPath(gd.layout, name, value);
      sync(); emit('plotly_relayout', patch); emit('plotly_afterplot');
    },
    react:async (_, data, layout) => {gd.data = data; gd.layout = layout; sync(); emit('plotly_afterplot');},
    Plots:{resize:()=>{}}, purge:()=>{}
  };
  if (mode === 'chrome49') w.eval(fs.readFileSync(path.join(assets, manifest.runtime), 'utf8'));
  const scripts = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
  function run(token) {
    let source = scripts.find(s => s.includes(token)); assert(source, token);
    if (mode === 'chrome49') {
      const file = manifest.scripts[createHash('sha256').update(source.trim()).digest('hex')];
      assert(file, 'rebuild browser assets: '+token);
      source = fs.readFileSync(path.join(assets,file),'utf8');
    }
    w.eval(source);
  }
  run('var axisLocksEnabled');
  const q = selector => d.querySelector(selector);
  const action = name => q('[data-axis-action="'+name+'"]').click();
  function wheel(delta, y=200, target=gd, options={}) {
    const event = new w.WheelEvent('wheel', {bubbles:true,cancelable:true,clientX:300,clientY:y,deltaY:delta,...options});
    target.dispatchEvent(event); return event;
  }
  function modebar(value, tag='button') {
    const button = d.createElement(tag);
    button.className = 'modebar-btn'; button.setAttribute('data-attr','zoom'); button.setAttribute('data-val',value);
    gd.appendChild(button); button.click(); return button;
  }
  const dispatch = name => gd.dispatchEvent(new w.CustomEvent(name));
  return {w,d,gd,key,q,action,wheel,modebar,run,emit,dispatch,sync,updates,captures:()=>captures};
}

async function displayFixture(t, kind, mode, saved) {
  const h = setup(t,kind,mode);
  h.gd.data = [0,1].map(i => ({name:'Sample '+i,x:[100,200,300],y:[1,3,2],
    meta:{rist_sample_parent:true,rist_sample_group:'sample:'+i,rist_raman_stack_offset:0,
      rist_ftir_stack_offset_absorbance:0,rist_ftir_stack_offset_transmittance:0,
      ftir_signal_toggle:{absorbance_y:[1,3,2],transmittance_y:[30,20,10]}}}));
  h.gd.layout.yaxis = {range:[0,10],showticklabels:true,ticks:'inside',ticklen:7,
    showgrid:true,zeroline:true,dtick:2,tickformat:'.2f',title:{text:'Measured signal'},
    minor:{ticks:'inside',ticklen:4,showgrid:true,dtick:0.5,tickcolor:'#ff0000'}};
  h.gd.layout.meta[h.key] = {enabled:false,gap:1.2,unit:3,modeUnits:{absorbance:3,transmittance:100},
    sampleOrder:['sample:0','sample:1'],sampleOffsets:{'sample:0':0,'sample:1':0},samplePositions:{'sample:0':0,'sample:1':0}};
  if (saved) {
    h.gd.layout.meta[h.key].enabled = true;
    h.gd.layout.meta[h.key].yAxisDisplay = saved;
    h.gd.layout.yaxis.showticklabels = false; h.gd.layout.yaxis.ticks = ''; h.gd.layout.yaxis.ticklen = 0;
  }
  h.sync(); h.run(kind === 'ftir' ? 'gd._ristFtirStackInstalled' : 'gd._ristRamanStackInstalled'); await tick();
  h.toggle = () => h.q('.rist-'+kind+'-stack-button').click();
  return h;
}

function assertStackAxis(axis) {
  assert.equal(axis.showticklabels,false); assert.equal(axis.ticks,''); assert.equal(axis.ticklen,0);
  assert.equal(axis.minor.ticks,''); assert.equal(axis.minor.ticklen,0);
  assert.equal(axis.showgrid,false); assert.equal(axis.minor.showgrid,false); assert.equal(axis.zeroline,false);
}

for (const mode of ['native','chrome49']) for (const kind of ['ftir','raman']) {
  const label = kind+'/'+mode;
  test(label+': stack hides major/minor Y scale, then restores it without losing new tick intervals', async t => {
    const h = await displayFixture(t,kind,mode), x = clone(h.gd.layout.xaxis);
    h.toggle(); await tick(); assertStackAxis(h.gd.layout.yaxis);
    const data = clone(h.gd.data), y = h.gd.layout.yaxis;
    await h.w.Plotly.relayout(h.gd,{'yaxis.dtick':4,'yaxis.minor.dtick':0.25});
    h.action('zoom-in'); await tick(); assertStackAxis(h.gd.layout.yaxis);
    h.dispatch('rist-plot-structure-changed'); await h.gd._ristStackReady;
    assertStackAxis(h.gd.layout.yaxis); assert.deepEqual(clone(h.gd.data),data);
    h.run('function buildImageExportPayload');
    const exported=h.gd._ristBuildImageExportPayload(h.gd.data,h.gd.layout,1200,720);
    assertStackAxis(exported.layout.yaxis);
    h.toggle(); await tick();
    assert.equal(y.showticklabels,true); assert.equal(y.ticks,'inside'); assert.equal(y.ticklen,7);
    assert.equal(y.minor.ticks,'inside'); assert.equal(y.minor.ticklen,4);
    assert.equal(y.showgrid,true); assert.equal(y.minor.showgrid,true); assert.equal(y.zeroline,true);
    assert.equal(y.dtick,4); assert.equal(y.minor.dtick,0.25); assert.equal(y.tickformat,'.2f');
    assert.equal(y.minor.tickcolor,'#ff0000'); assert.equal(y.title.text,'Measured signal');
    assert.equal(h.gd.layout.xaxis.range[0],(x.range[0]+x.range[1])/2+(x.range[0]-x.range[1])*0.4);
    h.toggle(); await tick(); assertStackAxis(h.gd.layout.yaxis);
    h.toggle(); await tick(); assert.equal(h.gd.layout.yaxis.showticklabels,true);
  });
  test(label+': Origin on/off while stacked stays hidden and restores the newly selected style', async t => {
    const h = await displayFixture(t,kind,mode);
    h.run('var originAxis ='); h.toggle(); await tick();
    for (const origin of [false,true,false]) {
      await h.gd._ristOriginStyle.setEnabled(origin,true,false); await h.gd._ristStackReady;
      assertStackAxis(h.gd.layout.yaxis);
      h.toggle(); await tick();
      assert.equal(h.gd.layout.yaxis.showticklabels,true);
      assert.equal(h.gd.layout.yaxis.ticks,origin?'inside':'outside');
      assert.equal(h.gd.layout.yaxis.ticklen,origin?7:5);
      assert.equal(h.gd.layout.yaxis.minor.ticks,origin?'inside':'');
      assert.equal(h.gd.layout.yaxis.showgrid,!origin);
      assert.equal(h.gd.layout.yaxis.minor.dtick,0.5);
      h.toggle(); await tick(); assertStackAxis(h.gd.layout.yaxis);
    }
  });
  test(label+': older major-only stack snapshots gain minor hiding and normal scale returns on unstack/reload', async t => {
    const h = await displayFixture(t,kind,mode,{ticks:{exists:true,value:'inside'},ticklen:{exists:true,value:7},
      showticklabels:{exists:true,value:false}});
    assertStackAxis(h.gd.layout.yaxis);
    h.toggle(); await tick();
    assert.equal(h.gd.layout.yaxis.showticklabels,true); assert.equal(h.gd.layout.yaxis.minor.ticks,'inside');
    assert.equal(h.gd.layout.yaxis.minor.ticklen,4); assert.equal(h.gd.layout.yaxis.minor.showgrid,true);
    h.gd.layout.yaxis.showticklabels = false;
    h.dispatch('rist-plot-data-replaced'); await h.gd._ristStackReady;
    assert.equal(h.gd.layout.yaxis.showticklabels,true);
  });
  test(label+': +/- controls anchor the displayed bottom without altering RAW, offsets, labels or shapes', async t => {
    const h = setup(t,kind,mode), before = clone(h.gd.data), meta = clone(h.gd.layout.meta);
    h.gd.layout.annotations = [{x:200,y:40,text:'Peak'}]; h.gd.layout.shapes = [{y0:10,y1:40}];
    h.action('zoom-in'); await tick();
    assert.deepEqual(clone(h.gd.layout.yaxis.range),[-20,76]);
    h.action('zoom-out'); await tick();
    assert.deepEqual(clone(h.gd.layout.yaxis.range),[-20,100]);
    assert.deepEqual(clone(h.gd.data),before); assert.deepEqual(clone(h.gd.layout.meta),meta);
    assert.equal(h.gd.layout.annotations[0].y,40); assert.equal(h.gd.layout.shapes[0].y0,10);
    assert.equal(h.captures(),2);
    for (const [range,type] of [[[25,125],'linear'],[[100,-20],'linear'],[[-2,3],'log']]) {
      h.gd.layout.yaxis.range = range; h.gd.layout.yaxis.type = type; h.sync();
      h.action('zoom-in'); await tick();
      assert.equal(h.gd.layout.yaxis.range[0],range[0]);
      assert.equal(h.gd.layout.yaxis.range[1],range[0]+(range[1]-range[0])*0.8);
    }
  });
  test(label+': wheel is bottom-anchored at any pointer height and preserves each experiment X policy', async t => {
    const h = setup(t,kind,mode), x = clone(h.gd.layout.xaxis.range);
    for (const y of [110,300,490]) {
      h.gd.layout.yaxis.range = [-20,100]; h.sync();
      assert(h.wheel(-120,y).defaultPrevented);
      assert.equal(h.gd.layout.yaxis.range[0],-20);
      assert(Math.abs(h.gd.layout.yaxis.range[1]-(-20+120*Math.exp(-0.24))) < 1e-9);
      h.wheel(120,y); assert(Math.abs(h.gd.layout.yaxis.range[1]-100) < 1e-9);
    }
    h.wheel(-120);
    if (kind === 'raman') assert.deepEqual(clone(h.gd.layout.xaxis.range),x);
    else assert.notDeepEqual(clone(h.gd.layout.xaxis.range),x);
    assert.equal(h.wheel(-120,50).defaultPrevented,false); // Page can still scroll.
    assert.equal(h.wheel(-120,300,h.q('.rist-axis-control-button')).defaultPrevented,false);
  });
  test(label+': modifier wheel selects only one axis in stack/normal views and never changes measured data', t => {
    const h = setup(t,kind,mode), raw = clone(h.gd.data);
    for (const stacked of [true,false]) {
      h.gd.layout.meta[h.key].enabled = stacked;
      for (const options of [{shiftKey:true},{ctrlKey:true},{ctrlKey:true,shiftKey:true}]) {
        h.gd.layout.xaxis.range = kind === 'ftir' ? [4000,400] : [100,3000];
        h.gd.layout.yaxis.range = [-20,100]; h.sync();
        const x = clone(h.gd.layout.xaxis.range), y = clone(h.gd.layout.yaxis.range);
        assert(h.wheel(-120,300,h.gd,options).defaultPrevented);
        if (options.ctrlKey) {
          assert.deepEqual(clone(h.gd.layout.xaxis.range),x);
          assert.notDeepEqual(clone(h.gd.layout.yaxis.range),y);
          assert.equal(h.gd.layout.yaxis.range[0] === y[0],stacked);
        } else {
          assert.deepEqual(clone(h.gd.layout.yaxis.range),y);
          const fraction = (300-60)/800, anchor = x[0]+(x[1]-x[0])*fraction;
          const next = x.map(v=>anchor+(v-anchor)*Math.exp(-.24));
          assert.deepEqual(clone(h.gd.layout.xaxis.range),next);
        }
        h.wheel(120,300,h.gd,options);
        h.gd.layout.xaxis.range.forEach((v,i)=>assert(Math.abs(v-x[i])<1e-9));
        h.gd.layout.yaxis.range.forEach((v,i)=>assert(Math.abs(v-y[i])<1e-9));
      }
    }
    assert.deepEqual(clone(h.gd.data),raw);
  });
  test(label+': releasing modifiers restores default policy; Shift horizontal deltas and edit-mode shortcuts work', t => {
    const h = setup(t,kind,mode);
    assert(h.wheel(0,300,h.gd,{shiftKey:true,deltaX:-120}).defaultPrevented);
    assert.deepEqual(clone(h.gd.layout.yaxis.range),[-20,100]);
    const x = clone(h.gd.layout.xaxis.range);
    h.wheel(-120); // No sticky modifier after release or focus loss.
    assert(h.gd.layout.yaxis.range[1] < 100);
    if (kind === 'raman') assert.deepEqual(clone(h.gd.layout.xaxis.range),x);
    else assert.notDeepEqual(clone(h.gd.layout.xaxis.range),x);
    h.gd._context.scrollZoom = false; h.gd._ristEditMode = true;
    const y = clone(h.gd.layout.yaxis.range);
    h.wheel(-3,300,h.gd,{shiftKey:true,deltaMode:1});
    assert.deepEqual(clone(h.gd.layout.yaxis.range),y);
    h.wheel(-1,300,h.gd,{ctrlKey:true,deltaMode:2});
    assert.equal(h.gd.layout.yaxis.range[0],y[0]);
    assert(h.gd.layout.yaxis.range[1] < y[1]);
  });
  test(label+': shortcuts honor locked axes, Crop bounds and active drawing/text operations', async t => {
    const h = setup(t,kind,mode);
    for (const [axis,options] of [['xaxis',{shiftKey:true}],['yaxis',{ctrlKey:true}]]) {
      h.gd.layout[axis].fixedrange = true;
      const before = clone(h.gd.layout);
      assert(h.wheel(-120,300,h.gd,options).defaultPrevented);
      assert.deepEqual(clone(h.gd.layout),before);
      h.gd.layout[axis].fixedrange = false;
    }
    for (const flag of ['_ristAxisCropActive','_ristAxisScaleActive','_ristShapeDrawMode','_ristInlineTextEditing']) {
      h.gd[flag] = true;
      const before = clone(h.gd.layout);
      h.wheel(-120,300,h.gd,{ctrlKey:true}); h.wheel(-120,300,h.gd,{shiftKey:true});
      assert.deepEqual(clone(h.gd.layout),before); h.gd[flag] = false;
    }
    h.action('crop'); await tick(); h.emit('plotly_selected',{range:{x:[500,2000],y:[0,100]}}); await tick();
    await h.w.Plotly.relayout(h.gd,{'yaxis.range':[20,90]});
    const x = clone(h.gd.layout.xaxis.range);
    h.wheel(400,300,h.gd,{ctrlKey:true}); await tick();
    assert.deepEqual(clone(h.gd.layout.yaxis.range),[20,100]);
    assert.deepEqual(clone(h.gd.layout.xaxis.range),x);
    h.wheel(400,300,h.gd,{shiftKey:true}); await tick();
    assert.deepEqual(clone(h.gd.layout.xaxis.range),x);
    assert.deepEqual(clone(h.gd.layout.yaxis.range),[20,100]);
  });
  test(label+': browser/page/legend/editor scrolling is not captured outside the plotting area', t => {
    const h = setup(t,kind,mode), before = clone(h.gd.layout);
    for (const options of [{shiftKey:true},{ctrlKey:true}]) {
      assert.equal(h.wheel(-120,50,h.gd,options).defaultPrevented,false);
      assert.equal(h.wheel(-120,300,h.d.body,options).defaultPrevented,false);
      for (const tag of ['input','textarea','select','button','div']) {
        const node = h.d.createElement(tag);
        if (tag === 'div') node.setAttribute('contenteditable','true');
        h.gd.appendChild(node);
        assert.equal(h.wheel(-120,300,node,options).defaultPrevented,false);
      }
      for (const className of ['legend','rist-legend-edit-panel','rist-shape-editor-panel']) {
        const node = h.d.createElement('div'); node.className = className; h.gd.appendChild(node);
        assert.equal(h.wheel(-120,300,node,options).defaultPrevented,false);
      }
      assert.equal(h.wheel(-120,300,h.gd,{...options,altKey:true}).defaultPrevented,false);
      assert.equal(h.wheel(-120,300,h.gd,{...options,metaKey:true}).defaultPrevented,false);
    }
    assert.deepEqual(clone(h.gd.layout),before); assert.equal(h.captures(),0);
  });
  test(label+': shortcut guidance stays below the graph, outside export/toolbar and without popup or key latch', t => {
    const h = setup(t,kind,mode), hint = h.q('.rist-axis-wheel-help');
    assert.equal(h.gd.nextElementSibling,hint);
    assert.equal(h.gd.contains(hint),false);
    assert.equal(h.w.getComputedStyle(hint).display,'flex');
    assert.equal(h.w.getComputedStyle(hint).flexWrap,'wrap');
    assert(!['absolute','fixed'].includes(h.w.getComputedStyle(hint).position));
    assert.match(hint.textContent,/Shift \+ 휠: X축만/);
    assert.match(hint.textContent,/Ctrl \+ 휠: Y축만/);
    assert.match(hint.textContent,/스택: Y축 바닥 고정/);
    assert(hint.textContent.includes('기본: '+(kind === 'ftir' ? 'X·Y축' : 'Y축')));
    assert(h.gd.getAttribute('aria-describedby').includes(hint.id));
    h.run('var axisLocksEnabled');
    assert.equal(h.d.querySelectorAll('.rist-axis-wheel-help').length,1);
    assert.equal(h.wheel(-120,300,hint,{ctrlKey:true}).defaultPrevented,false);
  });
  test(label+': Plotly +/- buttons use the same anchor, including anchor/button markup', async t => {
    const h = setup(t,kind,mode);
    h.modebar('in','a'); await tick(); assert.deepEqual(clone(h.gd.layout.yaxis.range),[-20,40]);
    h.modebar('out'); await tick(); assert.deepEqual(clone(h.gd.layout.yaxis.range),[-20,100]);
    h.gd._ristEditMode = true; h.modebar('in'); await tick();
    assert.deepEqual(clone(h.gd.layout.yaxis.range),[-20,100]);
  });
  test(label+': axis dragging keeps the bottom; pointer cleanup still releases scaling mode', async t => {
    const h = setup(t,kind,mode);
    function pointer(name,y) {
      const event = new h.w.MouseEvent(name,{bubbles:true,cancelable:true,clientX:40,clientY:y,button:0});
      Object.defineProperties(event,{pointerId:{value:1},pointerType:{value:'mouse'}});
      h.gd.dispatchEvent(event);
    }
    pointer('pointerdown',300); pointer('pointermove',240); await tick(); pointer('pointerup',240);
    assert.equal(h.gd.layout.yaxis.range[0],-20); assert(h.gd.layout.yaxis.range[1]<100);
    assert.equal(h.gd._ristAxisScaleActive,false); assert.equal(h.captures(),1);
  });
  test(label+': ordinary mode, locks and non-zoom relayout are not converted to anchored zoom', async t => {
    const h = setup(t,kind,mode);
    h.gd.layout.yaxis.fixedrange = true;
    h.action('zoom-in'); await tick(); h.wheel(-120);
    assert.deepEqual(clone(h.gd.layout.yaxis.range),[-20,100]);
    h.gd.layout.yaxis.fixedrange = false;
    h.gd.layout.meta[h.key].enabled = false;
    h.action('zoom-in'); await tick(); assert.deepEqual(clone(h.gd.layout.yaxis.range),[-8,88]);
    const before = clone(h.gd.layout.yaxis.range), event = h.wheel(-120);
    if (kind === 'ftir') {assert.equal(event.defaultPrevented,false); assert.deepEqual(clone(h.gd.layout.yaxis.range),before);}
    else assert.notEqual(h.gd.layout.yaxis.range[0],before[0]);
    h.gd.layout.meta[h.key].enabled = true;
    await h.w.Plotly.relayout(h.gd,{'yaxis.range':[10,90]}); await tick(); // Pan/box zoom/direct edits.
    assert.deepEqual(clone(h.gd.layout.yaxis.range),[10,90]);
    h.action('zoom-in'); await tick(); assert.deepEqual(clone(h.gd.layout.yaxis.range),[10,74]);
  });
  test(label+': Crop ceiling caps zoom-out without pushing the bottom down; restored views use their own baseline', async t => {
    const h = setup(t,kind,mode);
    h.action('crop'); await tick(); h.emit('plotly_selected',{range:{x:[500,2000],y:[0,100]}}); await tick();
    assert.deepEqual(clone(h.gd.layout.yaxis.range),[0,100]);
    await h.w.Plotly.relayout(h.gd,{'yaxis.range':[20,90]}); await tick();
    h.action('zoom-out'); await tick(); assert.deepEqual(clone(h.gd.layout.yaxis.range),[20,100]);
    h.wheel(400); await tick(); assert.deepEqual(clone(h.gd.layout.yaxis.range),[20,100]);
    h.action('uncrop'); await tick();
    assert.deepEqual(clone(h.gd.layout.yaxis.range),[-20,100]);
    h.gd.layout.yaxis.range = [-500,2000]; h.dispatch('rist-history-restored'); await tick();
    h.action('zoom-in'); await tick(); assert.deepEqual(clone(h.gd.layout.yaxis.range),[-500,1500]);
    h.gd.layout.yaxis.range = [80,180]; h.dispatch('rist-plot-data-replaced');
    h.action('zoom-in'); await tick(); assert.deepEqual(clone(h.gd.layout.yaxis.range),[80,160]);
  });
  test(label+': real stack toggle remains RAW-preserving and zoom switches back when stack is disabled', async t => {
    const h = setup(t,kind,mode), original = [[-10,40,80],[-5,30,90]];
    h.gd.data = original.map((y,i) => ({name:'Sample '+i,x:[100,200,300],y:y.slice(),customdata:y.slice(),
      meta:{rist_sample_parent:true,rist_sample_group:'sample:'+i,rist_raman_stack_offset:0,
        rist_ftir_stack_offset_absorbance:0,rist_ftir_stack_offset_transmittance:0,
        ftir_signal_toggle:{absorbance_y:y.slice(),transmittance_y:[90,80,70]}}}));
    h.gd.layout.meta[h.key] = {enabled:false,gap:1.2,unit:100,modeUnits:{absorbance:100,transmittance:20},
      sampleOrder:['sample:0','sample:1'],samplePositions:{'sample:0':0,'sample:1':0},sampleOffsets:{'sample:0':0,'sample:1':0}};
    h.run(kind === 'ftir' ? 'gd._ristFtirStackInstalled' : 'gd._ristRamanStackInstalled'); await tick();
    const toggle = h.q('.rist-'+kind+'-stack-button'); toggle.click(); await tick();
    const stacked = clone(h.gd.data), bottom = h.gd.layout.yaxis.range[0];
    h.action('zoom-in'); await tick(); h.wheel(-120); await tick();
    assert.equal(h.gd.layout.yaxis.range[0],bottom); assert.deepEqual(clone(h.gd.data),stacked);
    toggle.click(); await tick(); assert.deepEqual(clone(h.gd.data.map(trace=>trace.y)),original);
    const unstackedBottom = h.gd.layout.yaxis.range[0]; h.action('zoom-in'); await tick();
    assert.notEqual(h.gd.layout.yaxis.range[0],unstackedBottom);
  });
}

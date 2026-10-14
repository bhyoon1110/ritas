// DOM/event tests for the shipped programs, not native Windows/Supermium QA.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {createHash} = require('node:crypto');
const {JSDOM} = require('jsdom');
const html = execFileSync(process.env.RIST_TEST_PYTHON || 'python3', ['-c',
  'from rist_common.plotting.color_palette import color_palette_html; print(color_palette_html())'], {encoding:'utf8'});
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1].trim();
const xrdHtml = execFileSync(process.env.RIST_TEST_PYTHON || 'python3', ['-c',
  'from lim.xrd_plot import build_xrd_phase_group_editor_js; print(build_xrd_phase_group_editor_js("xrd-plot"))'], {encoding:'utf8'});
const xrdScript = xrdHtml.match(/<script>([\s\S]*?)<\/script>/)[1];
const assetRoot = path.resolve(__dirname,'../app/static/chrome49');
const manifest = JSON.parse(fs.readFileSync(path.join(assetRoot,'manifest.json'),'utf8'));
const tick = () => new Promise(r=>setTimeout(r,35));

function setup(t, mode) {
  const dom = new JSDOM('<body><div id="editor"></div><button id="outside">Outside</button>'+html+'</body>',
    {runScripts:'outside-only',pretendToBeVisual:true,url:'http://rist.test/'});
  const w=dom.window,d=w.document;
  t.after(()=>{w.dispatchEvent(new w.Event('pagehide'));w.close();});
  // JSDOM has no layout; supply geometry only, without bypassing the real event handlers.
  w.HTMLElement.prototype.getClientRects=function() {
    return this.isConnected && !this.closest('[hidden]') ? [this.getBoundingClientRect()] : [];
  };
  w.HTMLElement.prototype.getBoundingClientRect=function() {
    return this.id==='rist-color-palette' ? {left:0,top:0,right:326,bottom:420,width:326,height:420}
      : {left:600,top:700,right:634,bottom:734,width:34,height:34};
  };
  Object.defineProperty(d.documentElement,'clientWidth',{configurable:true,value:1024});
  Object.defineProperty(w,'innerHeight',{configurable:true,value:768});
  let program=script;
  if(mode==='chrome49') {
    const filename=manifest.scripts[createHash('sha256').update(script).digest('hex')];
    assert(filename,'rebuild compatibility assets');
    w.eval(fs.readFileSync(path.join(assetRoot,manifest.runtime),'utf8'));
    program=fs.readFileSync(path.join(assetRoot,filename),'utf8');
  }
  w.eval(program);
  const q=s=>d.querySelector(s);
  function input(value='#377eb8') {
    const node=d.createElement('input');node.type='color';node.value=value;
    q('#editor').appendChild(node);return node;
  }
  function key(node,key,options={}) {node.dispatchEvent(new w.KeyboardEvent('keydown',{key,bubbles:true,cancelable:true,...options}));}
  function choose(value) {q(`[data-palette-color="${value}"]`).click();}
  function manual(value) {q('.rist-palette-hex').value=value;q('.rist-palette-apply').click();}
  const visible=()=>q('#rist-color-palette')?.style.display==='block';
  return {w,d,q,input,key,choose,manual,visible,rerun:()=>w.eval(program)};
}

for(const mode of ['native','chrome49']) {
  test(`${mode}: dynamic inputs open 80-color palette without native picker, commit input/change once`, t=>{
    const h=setup(t,mode), node=h.input(), events=[];
    ['input','change'].forEach(name=>node.addEventListener(name,()=>events.push(name)));
    const click=new h.w.MouseEvent('click',{bubbles:true,cancelable:true});node.dispatchEvent(click);
    assert(click.defaultPrevented);assert(h.visible());
    assert.equal(h.d.querySelectorAll('.rist-palette-presets button').length,80);
    assert.equal(h.q('[data-palette-color="#377eb8"]').getAttribute('aria-pressed'),'true');
    assert.deepEqual(events,[]);
    h.choose('#e41a1c');assert.equal(node.value,'#e41a1c');assert.deepEqual(events,['input','change']);
    assert(!h.visible());assert.equal(h.d.activeElement,node);
    node.click();h.choose('#e41a1c');assert.equal(events.length,2);
  });
  test(`${mode}: cancel/escape changes neither the original color nor the parent editor`, t=>{
    const h=setup(t,mode), node=h.input();let parentEscapes=0;
    h.d.addEventListener('keydown',ev=>{if(ev.key==='Escape')parentEscapes++;});
    h.key(node,'Enter');assert(h.visible());h.q('.rist-palette-hex').value='#112233';
    h.key(h.q('.rist-palette-hex'),'Escape');assert(!h.visible());
    assert.equal(node.value,'#377eb8');assert.equal(parentEscapes,0);
    h.key(node,' ');assert(h.visible());h.q('.rist-palette-close').click();assert(!h.visible());
  });
  test(`${mode}: custom HEX rejects invalid values, accepts shorthand, and isolates text undo`, t=>{
    const h=setup(t,mode), node=h.input();let graphUndo=0;
    h.d.addEventListener('keydown',()=>graphUndo++);node.click();
    h.manual('nope');assert(h.visible());assert.equal(node.value,'#377eb8');
    assert.equal(h.q('.rist-palette-hex').getAttribute('aria-invalid'),'true');
    h.key(h.q('.rist-palette-hex'),'z',{ctrlKey:true});assert.equal(graphUndo,0);
    h.q('.rist-palette-hex').value='#AbC';h.key(h.q('.rist-palette-hex'),'Enter');
    assert.equal(node.value,'#aabbcc');assert(!h.visible());
  });
  test(`${mode}: HSL previews do not modify the graph until one final apply`, t=>{
    const h=setup(t,mode), node=h.input();let count=0;
    node.addEventListener('input',()=>count++);node.click();
    const sliders=h.d.querySelectorAll('.rist-palette-slider input');
    [120,100,50].forEach((v,i)=>{sliders[i].value=String(v);sliders[i].dispatchEvent(new h.w.Event('input',{bubbles:true}));});
    assert.equal(h.q('.rist-palette-hex').value,'#00FF00');assert.equal(count,0);
    h.q('.rist-palette-apply').click();assert.equal(node.value,'#00ff00');assert.equal(count,1);
  });
  test(`${mode}: recent colors are deduplicated, bounded and shared across different editors`, t=>{
    const h=setup(t,mode), a=h.input(), b=h.input();
    for(let i=0;i<12;i++){a.click();h.manual('#1234'+i.toString(16).padStart(2,'0'));}
    b.click();assert.equal(h.d.querySelectorAll('.rist-palette-recent button').length,10);
    h.choose('#12340b');assert.equal(b.value,'#12340b');
    const recent=JSON.parse(h.w.localStorage.getItem('rist-color-palette-recent-v1'));
    assert.equal(recent.length,10);assert.equal(recent[0],'#12340b');
    assert.equal(new Set(recent).size,10);
  });
  test(`${mode}: outside click/scroll/focus and window blur dismiss without locking page scrolling`, t=>{
    const h=setup(t,mode), node=h.input();let outside=0;
    h.q('#outside').addEventListener('click',()=>outside++);
    node.click();h.q('#outside').dispatchEvent(new h.w.Event('pointerdown',{bubbles:true}));h.q('#outside').click();
    assert.equal(outside,1);assert(!h.visible());
    node.click();h.d.dispatchEvent(new h.w.Event('scroll'));assert(!h.visible());
    node.click();h.q('#outside').focus();assert(!h.visible());
    node.click();h.w.dispatchEvent(new h.w.Event('blur'));assert(!h.visible());
    assert.equal(h.d.body.style.overflow,'');assert.equal(h.d.body.style.touchAction,undefined);
  });
  test(`${mode}: palette pointer gestures cannot start a graph drag, but preserve default slider behavior`, t=>{
    const h=setup(t,mode), node=h.input();let drags=0;
    h.d.addEventListener('pointerdown',()=>drags++,true);node.click();
    const slider=h.q('input[type=range]');
    const event=new h.w.Event('pointerdown',{bubbles:true,cancelable:true});slider.dispatchEvent(event);
    assert.equal(drags,0);assert(!event.defaultPrevented);assert(h.visible());
  });
  test(`${mode}: removed/hidden/disabled source is never recolored and popup releases focus state`, async t=>{
    const h=setup(t,mode), node=h.input();node.click();node.remove();h.manual('#123456');
    assert.equal(node.value,'#377eb8');assert(!h.visible());
    const other=h.input();other.click();h.q('#editor').hidden=true;await tick();assert(!h.visible());
    h.q('#editor').hidden=false;other.disabled=true;other.click();assert(!h.visible());
    other.disabled=false;other.readOnly=true;other.click();assert(!h.visible());
  });
  test(`${mode}: duplicate installation does not toggle twice or duplicate popups/listeners`, t=>{
    const h=setup(t,mode);h.rerun();h.rerun();const node=h.input();node.click();
    assert(h.visible());assert.equal(h.d.querySelectorAll('#rist-color-palette').length,1);
    h.choose('#000000');assert.equal(node.value,'#000000');
  });
  test(`${mode}: blocked storage and malformed stored colors never break offline palette`, t=>{
    const h=setup(t,mode);h.w.localStorage.setItem('rist-color-palette-recent-v1','["not-color","#abc",null,"#aabbcc"]');
    const node=h.input();node.click();assert.equal(h.d.querySelectorAll('.rist-palette-recent button').length,1);
    h.choose('#aabbcc');
    Object.defineProperty(h.w,'localStorage',{get(){throw new Error('blocked');}});
    node.click();h.manual('#234567');assert.equal(node.value,'#234567');
  });
  test(`${mode}: popover is clamped inside viewport and never exported in print`, t=>{
    const h=setup(t,mode), node=h.input();node.click();const popup=h.q('#rist-color-palette');
    assert(+popup.style.top.replace('px','')>=8);assert(+popup.style.top.replace('px','')+420<=760);
    assert(+popup.style.left.replace('px','')+326<=1016);
    assert.match(html,/@media print\{#rist-color-palette\{display:none!important\}\}/);
  });
  test(`${mode}: XRD group palette sends one update to the correct group only`,async t=>{
    const h=setup(t,mode),gd=h.d.createElement('div');gd.id='xrd-plot';h.d.body.appendChild(gd);
    gd.data=['Group A','Group A','Group B'].map((group,i)=>({name:'Phase '+i,line:{color:'#377eb8'},
      meta:{xrd_phase_candidate:true,xrd_manual_phase_group:group}}));
    const updates=[];
    h.w.Plotly={restyle:async(_,changes,curves)=>{
      updates.push({changes,curves:[...curves]});
      curves.forEach(i=>{gd.data[i].line.color=changes['line.color'];});
    }};
    h.w.eval(xrdScript);h.q('.xrd-phase-group-button').click();
    h.q('.xrd-phase-section-head .xrd-phase-group-color').click();h.choose('#e41a1c');await tick();
    assert.equal(updates.length,1);assert.deepEqual(updates[0].curves,[0,1]);
    assert.deepEqual(gd.data.map(trace=>trace.line.color),['#e41a1c','#e41a1c','#377eb8']);
    assert.equal(gd.data[0].meta.xrd_manual_phase_color,'#e41a1c');
    assert.equal(h.q('.xrd-phase-group-panel').style.display,'block');
  });
}

// Geometry/ownership regressions for the shipped native and Chrome 49 programs.
const {test}=require('node:test'),assert=require('node:assert/strict'),{execFileSync}=require('node:child_process');
const {JSDOM}=require('jsdom'),{createHash}=require('node:crypto'),fs=require('node:fs'),path=require('node:path');
const html=execFileSync(process.env.RIST_TEST_PYTHON||'python3',['-c',
  'from rist_common.plotting.interaction_modes import interaction_modes_html; print(interaction_modes_html("peak-plot"))'],{encoding:'utf8'});
const source=html.match(/<script>([\s\S]*?)<\/script>/)[1];
const assets=path.resolve(__dirname,'../app/static/chrome49'),manifest=JSON.parse(fs.readFileSync(path.join(assets,'manifest.json')));
const tick=()=>new Promise(r=>setTimeout(r,40));
function setup(t,mode){
  const dom=new JSDOM('<html><head>'+html.match(/<style>[\s\S]*?<\/style>/)[0]+'</head><body><section><div id="peak-plot"></div></section></body></html>',{runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window,d=w.document,g=d.getElementById('peak-plot');w.innerWidth=1200;w.innerHeight=900;
  t.after(()=>{w.dispatchEvent(new w.Event('pagehide'));w.close();});
  const mock=(node,left,top,width,height)=>{
    const rect={left,top,width,height,right:left+width,bottom:top+height};
    node.getClientRects=()=>node.closest('[hidden]')?[]:[rect];node.getBoundingClientRect=()=>rect;return rect;
  };
  const graph=mock(g,100,180,1000,900),data=d.createElement('div');data.className='nsewdrag';g.appendChild(data);mock(data,150,220,850,820);
  let removed=0;g.layout={dragmode:'pan'};g.on=()=>g;g.removeListener=()=>removed++;
  let code=source;if(mode==='chrome49'){
    w.eval(fs.readFileSync(path.join(assets,manifest.runtime),'utf8'));
    const file=manifest.scripts[createHash('sha256').update(source.trim()).digest('hex')];assert(file,'rebuild interaction mode assets');
    code=fs.readFileSync(path.join(assets,file),'utf8');
  }
  w.eval(code);const bar=d.querySelector('.rist-interaction-status');Object.defineProperty(bar,'offsetHeight',{value:60});
  const refresh=async()=>{g._ristInteractionModes.refresh();await tick();};
  const rect=()=>({left:parseFloat(bar.style.left),top:parseFloat(bar.style.top),width:parseFloat(bar.style.width),height:60});
  return {w,d,g,bar,graph,mock,refresh,rect,removed:()=>removed,code};
}
for(const mode of ['native','chrome49']){
  test(mode+': body portal is fixed, click-through and outside saved/exported graph DOM',async t=>{
    const h=setup(t,mode);await h.refresh();
    assert.equal(h.bar.parentElement,h.d.body);assert.equal(h.g.querySelector('.rist-interaction-status'),null);
    assert.equal(h.w.getComputedStyle(h.bar).position,'fixed');assert.equal(h.w.getComputedStyle(h.bar).pointerEvents,'none');
    assert.equal(h.bar.style.visibility,'visible');assert.equal(h.rect().top,828);
    h.g._ristPeakEditMode='add';await h.refresh();
    assert.equal(h.bar.dataset.mode,'peak-add');assert.match(h.bar.textContent,/현재 모드: 피크 추가/);
    assert.equal(h.w.getComputedStyle(h.bar.querySelector('button')).pointerEvents,'auto');
    assert.equal(h.rect().height,60);assert.deepEqual(h.g.layout,{dragmode:'pan'});
  });
  test(mode+': scrolling, narrow viewports and soft keyboard keep badge visible without changing mode',async t=>{
    const h=setup(t,mode);await h.refresh();
    h.graph.top=-300;h.graph.bottom=600;h.w.dispatchEvent(new h.w.Event('scroll'));await tick();
    assert.equal(h.rect().top,610,'prefer below the whole canvas when it fits');
    h.w.innerWidth=320;h.w.dispatchEvent(new h.w.Event('resize'));await tick();
    assert(h.rect().left>=12&&h.rect().left+h.rect().width<=308);
    const view=new h.w.EventTarget();Object.assign(view,{offsetLeft:20,offsetTop:80,width:290,height:380});
    h.w.visualViewport=view;await h.refresh();assert(h.rect().left>=32);assert(h.rect().top+h.rect().height<=448);
    h.graph.top=1200;h.graph.bottom=2100;h.w.dispatchEvent(new h.w.Event('scroll'));await tick();
    assert.equal(h.bar.style.visibility,'hidden','no badge after graph leaves the screen');
    h.graph.top=180;h.graph.bottom=1080;await h.refresh();assert.equal(h.bar.style.visibility,'visible');
    assert.equal(h.bar.dataset.mode,'pan');
  });
  test(mode+': open/moving panels cause automatic avoidance and closing restores the preferred dock',async t=>{
    const h=setup(t,mode);await h.refresh();const before=h.rect();
    const panel=h.d.createElement('div');panel.className='rist-legend-edit-panel';
    h.mock(panel,before.left-10,before.top-10,before.width+20,80);h.d.body.appendChild(panel);await tick();
    assert.notDeepEqual(h.rect(),before);assert(h.rect().top+60<=before.top-16,'moves clear of the editor');
    panel.hidden=true;await tick();assert.deepEqual(h.rect(),before);
  });
  test(mode+': hidden workflow/visible active plot ownership and detachment clean up the portal',async t=>{
    const h=setup(t,mode);await h.refresh();
    h.g.parentElement.hidden=true;await tick();assert.equal(h.bar.style.visibility,'hidden');
    h.g.parentElement.hidden=false;await tick();assert.equal(h.bar.style.visibility,'visible');
    const other=h.d.createElement('div');h.mock(other,0,100,1000,500);h.d.body.appendChild(other);
    h.w.__ristActiveHistoryPlot=other;h.d.dispatchEvent(new h.w.CustomEvent('rist-active-mode-plot'));await tick();
    assert.equal(h.bar.style.visibility,'hidden');
    h.g.dispatchEvent(new h.w.MouseEvent('mousedown',{bubbles:true}));await tick();assert.equal(h.bar.style.visibility,'visible');
    const controller=h.g._ristInteractionModes;h.g.remove();await tick();
    assert.equal(h.d.querySelector('.rist-interaction-status'),null);assert.equal(h.g._ristInteractionModes,undefined);assert.equal(h.removed(),2);
    controller.refresh();h.w.dispatchEvent(new h.w.Event('resize'));await tick();assert.equal(h.d.querySelector('.rist-interaction-status'),null);
  });
  test(mode+': visible wheel help is not covered, but its hidden reserved space is ignored',async t=>{
    const h=setup(t,mode);await h.refresh();const before=h.rect(),hint=h.d.createElement('div');
    hint.className='rist-axis-wheel-help';h.mock(hint,0,before.top-6,1200,80);h.d.body.appendChild(hint);await tick();
    assert.deepEqual(h.rect(),before);
    hint.classList.add('is-visible');await tick();assert(h.rect().top+60<before.top-6);
    hint.classList.remove('is-visible');await tick();assert.deepEqual(h.rect(),before);
  });
  test(mode+': exit preserves completed data and inactive/input contexts cannot consume Escape',async t=>{
    const h=setup(t,mode);let exits=0;h.g.data=[{name:'raw',y:[1,2,3]}];h.g._ristPeakEditMode='delete';
    h.g.addEventListener('rist-exclusive-interaction-start',()=>{exits++;h.g._ristPeakEditMode='none';});
    h.g._ristRestoreNavigation=()=>{h.g.layout.dragmode='pan';};await h.refresh();
    const input=h.d.createElement('input');h.d.body.appendChild(input);
    input.dispatchEvent(new h.w.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));assert.equal(exits,0);
    h.bar.querySelector('button').click();await tick();assert.equal(exits,1);assert.equal(h.bar.dataset.mode,'pan');
    assert.deepEqual(h.g.data,[{name:'raw',y:[1,2,3]}]);assert.equal(h.bar.querySelector('button').disabled,true);
    h.d.dispatchEvent(new h.w.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));assert.equal(exits,1);
  });
}

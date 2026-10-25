const {test}=require('node:test'),assert=require('node:assert/strict'),{execFileSync}=require('node:child_process');
const {JSDOM}=require('jsdom'),{createHash}=require('node:crypto'),fs=require('node:fs'),path=require('node:path');
const html=execFileSync(process.env.RIST_TEST_PYTHON||'python3',['-c','from rist_common.plotting.floating_panels import floating_panels_html; print(floating_panels_html())'],{encoding:'utf8'});
const source=html.match(/<script>([\s\S]*?)<\/script>/)[1];
const assets=path.resolve(__dirname,'../app/static/chrome49'),manifest=JSON.parse(fs.readFileSync(path.join(assets,'manifest.json')));
const tick=()=>new Promise(r=>setTimeout(r,25));
function setup(t,mode,pointer=true){
  const dom=new JSDOM('<html><body><div id="graph" class="js-plotly-plot"><div class="rist-legend-edit-panel" style="position:absolute;left:10px;top:20px"><div class="rist-legend-edit-head"><span>범례</span><input class="rist-legend-opacity-slider" type="range" min="55" max="100" value="97"><button>닫기</button></div><input class="name" value="Sample"></div></div></body></html>',{runScripts:'outside-only',pretendToBeVisual:true});
  t.after(()=>{dom.window.dispatchEvent(new dom.window.Event('pagehide'));dom.window.close();});const w=dom.window,d=w.document;if(pointer)w.PointerEvent=w.MouseEvent;
  const panel=d.querySelector('.rist-legend-edit-panel'),head=panel.firstElementChild;
  function mock(node,left=210,top=220,width=240,height=180){
    node.getClientRects=()=>node.hidden?[]:[{}];Object.defineProperty(node,'offsetHeight',{get:()=>height});
    node.getBoundingClientRect=()=>({left,top,width,height,right:left+width,bottom:top+height});
  }
  mock(panel);w.innerWidth=1200;w.innerHeight=900;
  let code=source;if(mode==='chrome49'){w.eval(fs.readFileSync(path.join(assets,manifest.runtime),'utf8'));
    const file=manifest.scripts[createHash('sha256').update(source.trim()).digest('hex')];assert(file,'rebuild floating panel assets');code=fs.readFileSync(path.join(assets,file),'utf8');}
  w.eval(code);
  const mouse=(node,type,x,y)=>node.dispatchEvent(new w.MouseEvent(type,{bubbles:true,cancelable:true,button:0,clientX:x,clientY:y}));
  const down=pointer?'pointerdown':'mousedown',move=pointer?'pointermove':'mousemove',up=pointer?'pointerup':'mouseup';
  return {w,d,panel,head,mock,mouse,down,move,up};
}
for(const mode of ['native','chrome49'])for(const pointer of [true,false]){
  const label=mode+'/'+(pointer?'pointer':'mouse-touch');
  test(label+': drag escapes graph, clamps to viewport, survives legacy positioning and resets without editing data',async t=>{
    const h=setup(t,mode,pointer);let legacy=0;h.head.addEventListener(h.down,()=>legacy++);
    h.mouse(h.head,h.down,230,240);h.mouse(h.d,h.move,820,40);h.mouse(h.d,h.up,820,40);await tick();
    assert.equal(legacy,0);assert.equal(h.panel.style.position,'fixed');assert.equal(h.panel.style.left,'800px');assert.equal(h.panel.style.top,'20px');
    assert.equal(h.panel.parentElement.id,'graph','keep DOM/CSS and existing save handlers');
    h.panel.style.left='5px';h.panel.style.top='400px';await tick();assert.equal(h.panel.style.left,'800px');assert.equal(h.panel.style.top,'20px');
    h.w.innerWidth=400;h.w.innerHeight=240;h.w.dispatchEvent(new h.w.Event('resize'));await tick();
    assert.equal(h.panel.style.left,'152px');assert.equal(h.panel.style.maxHeight,'224px');
    const slider=h.panel.querySelector('[data-window-opacity]');slider.value='60';slider.dispatchEvent(new h.w.Event('input',{bubbles:true}));
    assert.equal(h.panel.style.opacity,'0.6');assert.equal(h.panel.querySelector('.name').value,'Sample');
    h.panel.querySelector('.rist-floating-reset').click();await tick();
    assert.equal(h.panel.style.position,'absolute');assert.equal(h.panel.style.left,'10px');assert.equal(h.panel.style.top,'20px');assert.equal(h.panel.style.opacity,'0.6');
  });
  test(label+': form controls never start drag; blur/cancel/hidden/removal release interaction',async t=>{
    const h=setup(t,mode,pointer);const slider=h.panel.querySelector('[data-window-opacity]');
    h.mouse(slider,h.down,230,240);h.mouse(h.d,h.move,800,30);assert.equal(h.panel.style.position,'absolute');
    h.mouse(h.head,h.down,230,240);h.w.dispatchEvent(new h.w.Event('blur'));h.mouse(h.d,h.move,800,30);assert.equal(h.panel.style.left,'210px');
    assert.equal(h.panel.classList.contains('rist-floating-dragging'),false);
    h.mouse(h.head,h.down,230,240);h.panel.hidden=true;await tick();h.mouse(h.d,h.move,800,30);assert.equal(h.panel.style.left,'210px');
    h.panel.hidden=false;h.mouse(h.head,h.down,230,240);h.panel.remove();await tick();h.mouse(h.d,h.move,800,30);
    assert.equal(h.panel.classList.contains('rist-floating-dragging'),false);
  });
  test(label+': all dynamic popup families receive controls once; palette gesture guard does not block title drag',async t=>{
    const h=setup(t,mode,pointer);
    const classes=['rist-axis-control-panel','rist-shape-editor-panel','rist-peak-coordinate-dialog','rist-ratio-selection','rist-ratio-candidates','rist-raman-ratio-editor','rist-ratio-batch','ftir-library-dialog','raman-library-dialog','rist-image-format-menu'];
    for(const cls of classes){const node=h.d.createElement('section');node.className=cls;node.innerHTML='<header>창</header>';h.mock(node);h.d.body.appendChild(node);}
    const palette=h.d.createElement('div');palette.id='rist-color-palette';palette.innerHTML='<div class="rist-palette-head">색상 팔레트</div>';h.mock(palette);h.d.body.appendChild(palette);
    const preview=h.d.createElement('div');preview.className='sw-preview';preview.innerHTML='<div><h3>미리보기</h3></div>';h.d.body.appendChild(preview);
    const inline=h.d.createElement('div');inline.id='inline-editor';inline.innerHTML='<div class="actions"><button>저장</button></div>';h.mock(inline);h.d.body.appendChild(inline);
    const toolbar=h.d.createElement('div');toolbar.className='rist-plot-control-row';h.d.body.appendChild(toolbar);await tick();
    assert.equal(toolbar.querySelector('.rist-floating-controls'),null);
    toolbar.innerHTML='<div class="rist-ftir-tools-head">그래프 도구</div>';await tick();
    h.w.__ristFloatingPanels.scan(h.d.body);await tick();
    [...classes.map(cls=>h.d.querySelector('.'+cls)),palette,preview.firstElementChild,toolbar,inline].forEach(node=>{
      assert.equal(node.querySelectorAll('.rist-floating-controls').length,1,node.className);
      assert(node.querySelector('[data-window-opacity]'));assert(node.querySelector('.rist-floating-reset'));
    });
    h.d.addEventListener(h.down,ev=>{if(palette.contains(ev.target))ev.stopImmediatePropagation();},true);
    h.mouse(palette.firstElementChild,h.down,230,240);h.mouse(h.d,h.move,600,100);h.mouse(h.d,h.up,600,100);assert.equal(palette.style.position,'fixed');assert.equal(palette.style.left,'580px');
  });
  test(label+': newly opened modal layer stays above a previously dragged panel and keeps its own position',async t=>{
    const h=setup(t,mode,pointer);
    h.mouse(h.head,h.down,230,240);h.mouse(h.d,h.up,230,240);
    const layer=h.d.createElement('div');layer.className='ftir-library-modal';
    const dialog=h.d.createElement('section');dialog.className='ftir-library-dialog';dialog.hidden=true;dialog.innerHTML='<header>라이브러리</header>';
    h.mock(dialog);layer.appendChild(dialog);h.d.body.appendChild(layer);await tick();
    dialog.hidden=false;await tick();assert(Number(layer.style.zIndex)>Number(h.panel.style.zIndex));
    h.mouse(dialog.firstElementChild,h.down,230,240);h.mouse(h.d,h.move,800,80);h.mouse(h.d,h.up,800,80);await tick();
    assert.equal(dialog.style.top,'60px');assert.equal(layer.style.zIndex,dialog.style.zIndex);
    dialog.hidden=true;await tick();dialog.hidden=false;dialog.style.top='300px';await tick();
    assert.equal(dialog.style.top,'60px');assert.equal(layer.style.zIndex,dialog.style.zIndex);
  });
  if(!pointer)test(label+': touch fallback moves without page scroll and releases on touchcancel',async t=>{
    const h=setup(t,mode,false);
    h.d.elementFromPoint=()=>h.head;await tick();
    function touch(node,type,x,y){const ev=new h.w.Event(type,{bubbles:true,cancelable:true});
      const points=[{clientX:x,clientY:y,identifier:0,target:h.head}];
      Object.defineProperty(ev,'touches',{value:type==='touchcancel'?[]:points});Object.defineProperty(ev,'changedTouches',{value:points});node.dispatchEvent(ev);return ev;}
    assert(touch(h.head,'touchstart',230,240).defaultPrevented);
    assert(touch(h.head,'touchmove',600,70).defaultPrevented);assert.equal(h.panel.style.left,'580px');assert.equal(h.panel.style.top,'50px');
    touch(h.head,'touchcancel',600,70);
    assert.equal(touch(h.d.body,'touchmove',900,300).defaultPrevented,false);assert.equal(h.panel.style.left,'580px');
    assert.equal(h.panel.classList.contains('rist-floating-dragging'),false);
  });
}

const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom'),acorn=require('acorn');
const source=fs.readFileSync(path.join(__dirname,'../app/report_split.js'),'utf8');
function setup(t,extra={}){
  const dom=new JSDOM('<main><div id="left"></div><div id="right"></div></main>',{url:'http://localhost',runScripts:'outside-only',pretendToBeVisual:true});
  t.after(()=>dom.window.close());const w=dom.window,d=w.document,host=d.querySelector('main'),left=d.getElementById('left'),right=d.getElementById('right');
  let size=1200,enabled=true;Object.defineProperty(host,'clientWidth',{get:()=>size});w.innerWidth=1400;
  if(extra.stored!==undefined)w.localStorage.setItem('split',extra.stored);
  if(extra.blocked)Object.defineProperty(w,'localStorage',{get(){throw Error('blocked');}});
  w.eval(source);const api=w.RistReportSplit.mount({container:host,left,right,breakpoint:1000,storageKey:'split',defaultLeft:()=>340,enabled:()=>enabled,...extra});
  const handle=d.querySelector('[role=separator]'),width=()=>parseFloat(left.style.width);
  const mouse=(target,type,x)=>target.dispatchEvent(new w.MouseEvent(type,{bubbles:true,cancelable:true,clientX:x,button:0}));
  const key=(code,shift=false)=>handle.dispatchEvent(new w.KeyboardEvent('keydown',{bubbles:true,cancelable:true,key:code,shiftKey:shift}));
  return {w,d,api,host,left,right,handle,width,mouse,key,setSize:n=>{size=n;},enabled:v=>{enabled=v;api.refresh();}};
}
test('source is ES5 and drag/keyboard widths clamp and persist',t=>{
  acorn.parse(source,{ecmaVersion:5});const h=setup(t);assert.equal(h.width(),340);
  h.mouse(h.handle,'mousedown',340);h.mouse(h.d,'mousemove',530);h.mouse(h.d,'mouseup',530);assert.equal(h.width(),530);
  assert.equal(Number(h.w.localStorage.getItem('split')),530/1182);
  h.key('End');assert.equal(h.width(),702);h.key('Home');assert.equal(h.width(),280);
  h.key('ArrowRight',true);assert.equal(h.width(),330);h.key('ArrowLeft');assert.equal(h.width(),310);
  assert.equal(h.handle.getAttribute('aria-valuenow'),'26');assert.match(h.handle.getAttribute('aria-valuetext'),/미리보기/);
});
test('stored ratios restore, narrow/inactive layouts clear styles and preserve preference',t=>{
  const h=setup(t,{stored:'.5',grid:true});assert.equal(h.width(),591);assert.equal(h.right.style.gridColumn,'3');
  h.w.innerWidth=390;h.setSize(390);h.w.dispatchEvent(new h.w.Event('resize'));
  assert(h.handle.hidden);assert.equal(h.left.style.width,'');assert.equal(h.host.style.gridTemplateColumns,'');assert.equal(h.right.style.gridColumn,'');
  assert.equal(h.w.localStorage.getItem('split'),'.5');h.w.innerWidth=1400;h.setSize(1200);h.api.refresh();assert.equal(h.width(),591);
  h.enabled(false);assert(h.handle.hidden);assert.equal(h.left.style.width,'');h.enabled(true);assert.equal(h.width(),591);
});
test('Escape, blur and resize abort a drag without persisting it or leaving overlays',t=>{
  const h=setup(t);for(const cancel of [()=>h.key('Escape'),()=>h.w.dispatchEvent(new h.w.Event('blur')),()=>h.w.dispatchEvent(new h.w.Event('resize'))]){
    h.mouse(h.handle,'mousedown',340);h.mouse(h.d,'mousemove',600);cancel();assert.equal(h.width(),340);assert.equal(h.w.localStorage.getItem('split'),null);
    assert.equal(h.d.body.style.cursor,'');assert.equal(h.d.querySelectorAll('[aria-hidden=true]').length,0);
  }
});
test('default reset and destruction restore original layout',t=>{
  const h=setup(t,{stored:'.5'});h.mouse(h.handle,'dblclick',0);assert.equal(h.width(),340);assert.equal(h.w.localStorage.getItem('split'),null);
  h.key('ArrowRight');h.key('Enter');assert.equal(h.width(),340);h.api.destroy();assert.equal(h.left.style.width,'');assert.equal(h.d.querySelector('[role=separator]'),null);
});
for(const stored of ['garbage','0','1','Infinity'])test('invalid saved preference '+stored+' uses default',t=>assert.equal(setup(t,{stored}).width(),340));
test('unavailable browser storage does not stop sizing',t=>{const h=setup(t,{blocked:true});h.key('ArrowRight');assert.equal(h.width(),360);h.key('Enter');assert.equal(h.width(),340);});
test('touch drag, unrelated touch and touch cancellation',t=>{
  const h=setup(t);function touch(target,type,x,id=7){const e=new h.w.Event(type,{bubbles:true,cancelable:true});Object.assign(e,{touches:[{clientX:x,identifier:id}],changedTouches:[{clientX:x,identifier:id}]});target.dispatchEvent(e);}
  touch(h.handle,'touchstart',340);touch(h.d,'touchmove',700,9);touch(h.d,'touchmove',540);touch(h.d,'touchend',540);assert.equal(h.width(),540);
  touch(h.handle,'touchstart',540);touch(h.d,'touchmove',300);touch(h.d,'touchcancel',300);assert.equal(h.width(),540);
});

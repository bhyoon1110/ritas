const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom'),acorn=require('acorn');
const source=['report_editor_icons.js','report_page_sidebar.js'].map(n=>fs.readFileSync(path.join(__dirname,'../app',n),'utf8')).join('\n');
function setup(t){
  const dom=new JSDOM('<button id="page-sidebar-toggle"></button><aside id="page-sidebar"><button id="page-sidebar-close"></button><span id="page-count"></span><ol id="page-thumbnails"></ol></aside>',{url:'http://localhost',runScripts:'outside-only'});
  t.after(()=>dom.window.close());const w=dom.window,d=w.document,moves=[],selections=[];w.eval(source);
  const api=new w.RistPageSidebar({move(...args){moves.push(args);},select(i){selections.push(i);api.select(i);}});
  api.layout({ratio:16/9,pageEditing:true,pages:[0,1,2].map(i=>({id:'p'+i,title:'<Title> '+i}))},'/slides');api.lock(false,false);
  const buttons=()=>[...d.querySelectorAll('.page-thumb')],event=(name,y=0)=>new w.MouseEvent(name,{bubbles:true,cancelable:true,clientY:y});
  buttons().forEach((b,i)=>b.getBoundingClientRect=()=>({top:i*100,bottom:i*100+90}));
  return {w,d,api,moves,selections,buttons,event};
}
test('sidebar is ES5; local drag/keyboard reorder, external drops and cancellations are safe',t=>{
  acorn.parse(source,{ecmaVersion:5});const h=setup(t),b=h.buttons();
  assert.equal(h.d.querySelectorAll('title').length,0);b[2].dispatchEvent(h.event('drop'));assert.equal(h.moves.length,0);
  b[0].dispatchEvent(h.event('dragstart'));b[2].dispatchEvent(h.event('dragover',270));b[2].dispatchEvent(h.event('drop',270));
  assert.deepEqual(h.moves,[['p0','p2',true]]);
  b[1].dispatchEvent(new h.w.KeyboardEvent('keydown',{keyCode:38,altKey:true,bubbles:true}));assert.deepEqual(h.moves[1],['p1','p0',false]);
  b[0].dispatchEvent(h.event('dragstart'));h.w.dispatchEvent(new h.w.Event('blur'));b[2].dispatchEvent(h.event('drop',270));assert.equal(h.moves.length,2);
  h.api.lock(true,false);b[0].dispatchEvent(h.event('dragstart'));b[2].dispatchEvent(h.event('drop',270));assert.equal(h.moves.length,2);
  b[2].click();assert.deepEqual(h.selections,[2]);assert.equal(b[2].getAttribute('aria-current'),'page');
  h.api.lock(true,true);b[1].click();assert.equal(h.selections.length,1);
  h.api.lock(false,false);b[2].focus();
  h.api.layout({pageEditing:true,pages:[2,0,1].map(i=>({id:'p'+i,title:'Title '+i}))},'/reordered');
  assert.equal(h.d.activeElement.getAttribute('data-page-id'),'p2');
});
test('thumbnails are bounded, sequential, visible-only and do not continue when collapsed',t=>{
  const h=setup(t),list=h.d.getElementById('page-thumbnails');Object.defineProperty(list,'clientHeight',{value:80});
  list.getBoundingClientRect=()=>({top:0,bottom:80});list.dispatchEvent(new h.w.Event('scroll'));
  const images=[...list.querySelectorAll('img')];assert.equal(images.filter(i=>i.hasAttribute('src')).length,1);assert.equal(images[0].getAttribute('src'),'/slides/0?thumbnail=true');
  images[0].dispatchEvent(new h.w.Event('load'));assert.equal(images[1].getAttribute('src'),'/slides/1?thumbnail=true');
  h.d.getElementById('page-sidebar-close').click();images[1].dispatchEvent(new h.w.Event('load'));assert(!images[2].hasAttribute('src'));
  assert(h.d.getElementById('page-sidebar').hidden);assert.equal(h.d.getElementById('page-sidebar-toggle').getAttribute('aria-expanded'),'false');
  h.api.layout({pages:[],pageEditing:false},'/tem');assert(h.d.getElementById('page-sidebar-toggle').hidden);
});

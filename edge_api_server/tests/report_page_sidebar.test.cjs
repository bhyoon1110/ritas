const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom'),acorn=require('acorn');
const source=['report_editor_icons.js','report_page_sidebar.js'].map(n=>fs.readFileSync(path.join(__dirname,'../app',n),'utf8')).join('\n');
function setup(t,mode='native'){
  const dom=new JSDOM('<button id="page-sidebar-toggle"></button><aside id="page-sidebar"><button id="page-sidebar-close"></button><span id="page-count"></span><ol id="page-thumbnails"></ol></aside>',{url:'http://localhost',runScripts:'outside-only'});
  t.after(()=>dom.window.close());const w=dom.window,d=w.document,moves=[],selections=[];
  if(mode==='chrome49'){
    const root=path.join(__dirname,'../app/static/chrome49'),manifest=JSON.parse(fs.readFileSync(path.join(root,'manifest.json')));
    w.eval(fs.readFileSync(path.join(root,manifest.runtime),'utf8'));
    const original=['file_save.js','report_editor_icons.js','report_page_sidebar.js','report_cell_editor.js','report_direct_editor.js','report_canvas_editor.js','report_saved_graphs.js','report_condition_options.js','xrd_workbench/web/shared/mail_editor_icons.js','xrd_workbench/web/shared/mail_table_editor.js','xrd_workbench/web/shared/mail_editor.js','report_editor.js']
      .map(file=>fs.readFileSync(path.join(__dirname,'../app',file),'utf8')).join('\n').trim();
    const asset=manifest.scripts[require('node:crypto').createHash('sha256').update(original).digest('hex')];assert(asset,'rebuild compatibility assets');
    const compiled=fs.readFileSync(path.join(root,asset),'utf8');
    const programs=acorn.parse(compiled,{ecmaVersion:5}).body.map(node=>compiled.slice(node.start,node.end))
      .filter(program=>/window\.Rist(?:PageSidebar|ReportIcons)\s*=/.test(program));
    assert.equal(programs.length,2);w.eval(programs.join('\n'));
  }else w.eval(source);
  const api=new w.RistPageSidebar({move(...args){moves.push(args);},select(i,preserve){selections.push(i);api.select(i,preserve);}});
  api.layout({ratio:16/9,pageEditing:true,pages:[0,1,2].map(i=>({id:'p'+i,title:'<Title> '+i}))},'/slides');api.select(0);api.lock(false,false);
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
for(const mode of ['native','chrome49']){
  test(mode+': Shift ranges, Ctrl/Cmd toggles and plain click use a stable anchor',t=>{
    const h=setup(t,mode);h.api.layout({pageEditing:true,pages:[0,1,2,3,4,5].map(i=>({id:'p'+i,title:'Page '+i}))},'/slides');
    const click=(i,keys={})=>h.buttons()[i].dispatchEvent(new h.w.MouseEvent('click',{bubbles:true,...keys}));
    const selected=()=>Array.from(h.api.selectedIds());
    click(1);click(4,{shiftKey:true});assert.deepEqual(selected(),['p1','p2','p3','p4']);
    click(2,{shiftKey:true});assert.deepEqual(selected(),['p1','p2']);
    click(5,{ctrlKey:true});assert.deepEqual(selected(),['p1','p2','p5']);
    click(2,{ctrlKey:true});assert.deepEqual(selected(),['p1','p5']);
    click(0,{shiftKey:true});assert.deepEqual(selected(),['p0','p1','p2']);
    click(4,{ctrlKey:true,shiftKey:true});assert.deepEqual(selected(),['p0','p1','p2','p3','p4']);
    click(3);click(0,{metaKey:true});assert.deepEqual(selected(),['p0','p3']);
    click(3,{metaKey:true});click(0,{metaKey:true});assert.deepEqual(selected(),[]);
    assert.match(h.d.getElementById('page-count').textContent,/0개 선택/);
    assert.equal(h.d.querySelectorAll('.page-thumb[aria-pressed="true"]').length,0);
    assert.equal(h.d.querySelectorAll('.page-thumb[aria-current="page"]').length,1);
  });
  test(mode+': select all is list-focused, nonmutating and respects locks/composition',t=>{
    const h=setup(t,mode),b=h.buttons();
    const key=(target,keys={})=>{const e=new h.w.KeyboardEvent('keydown',{key:'a',keyCode:65,ctrlKey:true,bubbles:true,cancelable:true,...keys});target.dispatchEvent(e);return e;};
    assert.equal(key(h.d.body).defaultPrevented,false);assert.equal(h.api.selectedIds().length,1);
    assert.equal(key(b[1],{isComposing:true}).defaultPrevented,false);
    assert.equal(key(b[1]).defaultPrevented,true);assert.equal(h.api.selectedIds().length,3);assert.equal(h.moves.length,0);
    h.api.select(1);assert.equal(key(b[1],{ctrlKey:false,metaKey:true}).defaultPrevented,true);assert.equal(h.api.selectedIds().length,3);
    h.api.select(0);h.api.lock(true,true);assert.equal(key(b[0]).defaultPrevented,false);assert.equal(h.api.selectedIds().length,1);
  });
  test(mode+': selection survives reorder/refresh by ID and dragging inside it does not move',t=>{
    const h=setup(t,mode),b=h.buttons();
    b[1].dispatchEvent(new h.w.MouseEvent('click',{bubbles:true,shiftKey:true}));
    b[0].dispatchEvent(h.event('dragstart'));assert.equal(h.d.querySelectorAll('.is-dragging').length,2);
    b[1].dispatchEvent(h.event('drop',170));assert.equal(h.moves.length,0);
    b[0].dispatchEvent(h.event('dragstart'));b[2].dispatchEvent(h.event('drop',270));assert.equal(h.moves.length,1);
    h.api.layout({pageEditing:true,pages:[2,0,1].map(i=>({id:'p'+i,title:'Renamed '+i}))},'/new');h.api.select(2,true);
    assert.deepEqual(Array.from(h.api.selectedIds()),['p0','p1']);assert.equal(h.d.querySelectorAll('[aria-pressed="true"]').length,2);
    h.api.layout({pageEditing:true,pages:[2,1].map(i=>({id:'p'+i,title:'Page '+i}))},'/newer');
    assert.deepEqual(Array.from(h.api.selectedIds()),['p1']);
  });
  test(mode+': group restore after a reorder retains a surviving Shift anchor',t=>{
    const h=setup(t,mode);h.buttons()[2].dispatchEvent(new h.w.MouseEvent('click',{bubbles:true,ctrlKey:true}));
    h.api.layout({pageEditing:true,pages:[1,0,2].map(i=>({id:'p'+i,title:'Page '+i}))},'/reordered');
    h.api.setSelected(['p0','p2']);h.api.select(2,true);
    h.buttons()[0].dispatchEvent(new h.w.MouseEvent('click',{bubbles:true,shiftKey:true}));
    assert.deepEqual(Array.from(h.api.selectedIds()),['p1','p0','p2']);
  });
}
test('thumbnails are bounded, sequential, visible-only and do not continue when collapsed',t=>{
  const h=setup(t),list=h.d.getElementById('page-thumbnails');Object.defineProperty(list,'clientHeight',{value:80});
  list.getBoundingClientRect=()=>({top:0,bottom:80});list.dispatchEvent(new h.w.Event('scroll'));
  const images=[...list.querySelectorAll('img')];assert.equal(images.filter(i=>i.hasAttribute('src')).length,1);assert.equal(images[0].getAttribute('src'),'/slides/0?thumbnail=true');
  images[0].dispatchEvent(new h.w.Event('load'));assert.equal(images[1].getAttribute('src'),'/slides/1?thumbnail=true');
  h.d.getElementById('page-sidebar-close').click();images[1].dispatchEvent(new h.w.Event('load'));assert(!images[2].hasAttribute('src'));
  assert(h.d.getElementById('page-sidebar').hidden);assert.equal(h.d.getElementById('page-sidebar-toggle').getAttribute('aria-expanded'),'false');
  h.api.layout({pages:[],pageEditing:false},'/tem');assert(h.d.getElementById('page-sidebar-toggle').hidden);
});

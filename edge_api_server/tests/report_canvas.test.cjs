const {test}=require('node:test'), assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom'),acorn=require('acorn');
const script=['report_editor_icons.js','report_canvas_editor.js'].map(name=>fs.readFileSync(path.join(__dirname,'../app',name),'utf8')).join('\n');
function setup(t, upload){
  const dom=new JSDOM('<div id="canvas-tools"></div><div><div id="slide-stage"><div id="canvas-layer"></div></div></div>',{runScripts:'outside-only'});
  t.after(()=>dom.window.close());const w=dom.window,d=w.document;w.eval(script);let changes=0,modes=[];
  const api=new w.RistCanvasEditor({changed(){changes++;},message(){},mode(v){modes.push(v);},upload:upload||function(f,done){done(null,{asset:'a'.repeat(64),width:120,height:60});}});
  const item={id:'shape-'+'a'.repeat(20),kind:'text',name:'원래 본문',text:'기존 분석 내용',x:.1,y:.2,w:.5,h:.2,fontSize:18,color:'#123456',fill:'none',stroke:'none',lineWidth:1,bold:false,align:'left',dash:'solid'};
  api.load({});api.layout({canvasEditing:true,ratio:16/9});api.page({id:'page1',objects:[item]});
  const button=text=>[...d.querySelectorAll('button')].find(b=>b.getAttribute('aria-label')===text),field=label=>d.querySelector('[aria-label="개체 '+label+'"]');
  const set=(label,value)=>{const e=field(label);e.value=value;e.dispatchEvent(new w.Event(e.tagName==='TEXTAREA'?'input':'change',{bubbles:true}));};
  button('자유 편집 켜기').click();return {w,d,api,button,field,set,item,changes:()=>changes,modes};
}
test('ES5 canvas inserts text/shapes/lines, edits numeric geometry and styles, undo redo',t=>{
  acorn.parse(script,{ecmaVersion:5});const h=setup(t);
  h.button('글자 삽입').click();h.set('내용','<script>문자로 표시');h.set('글자 크기 (pt)','32');
  h.set('왼쪽 (%)','80');h.set('너비 (%)','90');
  let value=h.api.value().page1[0];assert.equal(value.text,'<script>문자로 표시');assert.equal(value.fontSize,32);
  assert(value.x+value.w<=1.001);assert.equal(h.d.querySelectorAll('script').length,0);
  h.button('개체 삭제').click();assert(h.api.value().page1[0].hidden);
  h.button('개체 실행 취소').click();assert(!h.api.value().page1[0].hidden);
  h.button('개체 다시 실행').click();assert(h.api.value().page1[0].hidden);
  h.button('선 삽입').click();h.set('선 종류','dashDot');h.set('선 굵기 (pt)','3');
  value=h.api.value().page1[1];assert.equal(value.kind,'line');assert.equal(value.dash,'dashDot');assert.equal(value.lineWidth,3);
  h.button('맨 뒤로').click();assert.equal(h.api.value().page1[1].layer,'back');
});
test('every free-edit command is an accessible local SVG icon with a tooltip',t=>{
  const h=setup(t),buttons=[...h.d.querySelectorAll('#canvas-tools button')];
  assert.equal(buttons.length,12);
  for(const b of buttons){assert(b.querySelector('svg[aria-hidden="true"]'));assert(b.title);assert.equal(b.getAttribute('aria-label'),b.title);assert.equal(b.textContent,'');}
  assert.equal(h.d.querySelectorAll('.canvas-tool-group').length,3);
});
test('existing text moves/resizes only on release, blur cancels and input history stays local',t=>{
  const h=setup(t),stage=h.d.getElementById('slide-stage');stage.getBoundingClientRect=()=>({width:1000,height:500});
  function start(){h.d.querySelector('.canvas-object').dispatchEvent(new h.w.MouseEvent('mousedown',{bubbles:true,button:0,clientX:100,clientY:100}));}
  start();h.d.dispatchEvent(new h.w.MouseEvent('mousemove',{bubbles:true,clientX:200,clientY:150}));
  assert.equal(h.changes(),0);h.d.dispatchEvent(new h.w.MouseEvent('mouseup',{bubbles:true}));
  assert.equal(h.changes(),1);assert.equal(h.api.value().page1[0].x,.2);
  start();h.d.dispatchEvent(new h.w.MouseEvent('mousemove',{bubbles:true,clientX:700,clientY:300}));h.w.dispatchEvent(new h.w.Event('blur'));
  assert.equal(h.changes(),1);assert.equal(h.api.value().page1[0].x,.2);
  h.set('내용','편집 내용');assert.equal(h.api.value().page1[0].text,'편집 내용');
  h.api.lock(true);h.button('글자 삽입').click();assert.equal(h.api.value().page1.length,1);
  h.api.lock(false);h.button('자유 편집 끄기').click();assert(h.d.getElementById('canvas-layer').hidden);
});
test('saved object edits survive reload, hidden pages and mode changes without leaking to another page',t=>{
  const h=setup(t);h.button('타원 삽입').click();const state=h.api.value();
  h.api.page({id:'page2',objects:[]});assert(!h.d.querySelector('.canvas-object'));
  h.button('사각형 삽입').click();assert.equal(h.api.value().page2.length,1);
  h.api.load(state);h.api.page({id:'page1',objects:[h.item]});assert.equal(h.d.querySelectorAll('.canvas-object').length,2);
  h.w.confirm=()=>true;h.button('이 페이지 개체 편집 초기화').click();assert.deepEqual(JSON.parse(JSON.stringify(h.api.value())),{});
  h.api.layout({canvasEditing:false});assert(h.d.getElementById('canvas-tools').hidden);
});
test('existing table cells do not consume new edit quota; palette follows selection and locks',t=>{
  const h=setup(t);h.api.page({id:'page1',objects:Array.from({length:105},(_,i)=>({...h.item,id:'shape-'+i.toString(16).padStart(20,'0')}))});
  h.button('사각형 삽입').click();assert.equal(h.api.value().page1.length,1);
  h.set('채우기 색 (none: 없음)','#aabbcc');
  const palette=h.d.querySelector('[aria-label="채우기 팔레트"]');assert.equal(palette.value,'#aabbcc');
  assert(!h.d.querySelector('[aria-label="페이지 개체 선택"]').textContent.includes('object-'));
  h.api.lock(true);assert(palette.disabled);
});
test('saved graphs add without replacing selected objects, fit inside page, preserve aspect and undo',t=>{
  const h=setup(t);h.button('글자 삽입').click();h.set('내용','보존할 문구');
  const original=h.api.value().page1[0];let error;
  h.api.insertGraph(new h.w.Blob(['png'],{type:'image/png'}),{x:.98,y:.99},e=>error=e);
  assert.equal(error,null);const items=h.api.value().page1;assert.equal(items.length,2);
  assert.deepEqual(items[0],original);const image=items[1];assert.equal(image.kind,'image');assert.equal(image.asset,'a'.repeat(64));
  assert(Math.abs((image.w/image.h)*(16/9)-2)<1e-9);assert(image.x+image.w<=1);assert(image.y+image.h<=1);
  h.button('개체 실행 취소').click();assert.equal(h.api.value().page1.length,1);
  h.button('개체 다시 실행').click();assert.equal(h.api.value().page1.length,2);
});
test('saved graph upload cannot insert into changed/locked/loading pages and rejects oversize inputs',t=>{
  let callback,calls=0,flag;
  const h=setup(t,(f,done,saved)=>{calls++;callback=done;flag=saved;});let error;
  h.api.insertGraph(new h.w.Blob(['png'],{type:'image/png'}),null,e=>error=e);assert.equal(flag,true);
  h.api.page({id:'other',objects:[]});callback(null,{asset:'a'.repeat(64),width:100,height:200});
  assert.match(error.message,/페이지가 변경/);assert.equal(Object.keys(h.api.value()).length,0);
  h.api.lock(true);assert(!h.api.canInsertGraph());h.api.insertGraph({type:'image/png',size:3},null,e=>error=e);assert.equal(calls,1);
  h.api.lock(false);h.api.insertGraph({type:'image/png',size:17*1024*1024},null,e=>error=e);assert.match(error.message,/16MB/);assert.equal(calls,1);
  h.api.loading();assert(!h.api.canInsertGraph());
});

const {test}=require('node:test'), assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom'),acorn=require('acorn');
const script=['report_editor_icons.js','report_cell_editor.js','report_canvas_editor.js','report_ppt_templates.js'].map(name=>fs.readFileSync(path.join(__dirname,'../app',name),'utf8')).join('\n');
const babel=require('@babel/core');
const programs={native:script,fallback:babel.transformSync(script,{configFile:false,babelrc:false,presets:[[require('@babel/preset-env'),{targets:{chrome:'49'},forceAllTransforms:true,modules:false}]]}).code};
function setup(t, upload, mode='native', options={}){
  const dom=new JSDOM('<div id="preview-panel-toggles"><button id="page-sidebar-toggle"></button></div><div id="canvas-tools"></div><div><div id="slide-stage"><div id="canvas-layer"></div></div></div>',{runScripts:'outside-only'});
  t.after(()=>dom.window.close());const w=dom.window,d=w.document;w.eval(programs[mode]);let changes=0,modes=[];
  const api=new w.RistCanvasEditor({changed(){changes++;},message(){},mode(v){modes.push(v);},upload:upload||function(f,done){done(null,{asset:'a'.repeat(64),width:120,height:60});},...options});
  const item={id:'shape-'+'a'.repeat(20),kind:'text',name:'원래 본문',text:'기존 분석 내용',x:.1,y:.2,w:.5,h:.2,fontSize:18,color:'#123456',fill:'none',stroke:'none',lineWidth:1,bold:false,align:'left',dash:'solid'};
  api.load({});api.layout({canvasEditing:true,ratio:16/9});api.page({id:'page1',objects:[item]});
  const button=text=>[...d.querySelectorAll('button')].find(b=>b.getAttribute('aria-label')===text),field=label=>d.querySelector('[aria-label="개체 '+label+'"]');
  const set=(label,value)=>{const e=field(label);e.value=value;e.dispatchEvent(new w.Event(e.tagName==='TEXTAREA'?'input':'change',{bubbles:true}));};
  button('자유 편집 켜기').click();return {w,d,api,button,field,set,item,changes:()=>changes,modes};
}
for(const mode of Object.keys(programs)){
  test(mode+': native reset and renderer version undo together, preserving authored values',t=>{
    let version='legacy';
    const h=setup(t,null,mode,{historyContext:()=>version,restoreHistoryContext:v=>{version=v;}});
    const before={page1:[{id:h.item.id,text:'보존할 내용',fontSize:35,color:'#112233'},
      {id:'object-template-background',kind:'image',asset:'a'.repeat(64)},
      {id:'object-added',kind:'text',text:'추가 내용',x:.2}]};
    h.api.load(before);h.api.lock(true);
    assert.equal(h.api.restoreNativeDefaults('2026-10-29'),false);assert.equal(version,'legacy');
    h.api.lock(false);assert.equal(h.api.restoreNativeDefaults('2026-10-29'),true);
    assert.equal(version,'2026-10-29');
    const restored=JSON.parse(JSON.stringify(h.api.value()));
    assert.deepEqual(restored,{page1:[{id:h.item.id,text:'보존할 내용'},before.page1[2]]});
    h.button('개체 실행 취소').click();assert.equal(version,'legacy');assert.deepEqual(JSON.parse(JSON.stringify(h.api.value())),before);
    h.button('개체 다시 실행').click();assert.equal(version,'2026-10-29');assert.deepEqual(JSON.parse(JSON.stringify(h.api.value())),restored);
    // A clean legacy report still needs an undo entry for its design change.
    h.api.load({});version='legacy';h.api.restoreNativeDefaults('2026-10-29');
    h.button('개체 실행 취소').click();assert.equal(version,'legacy');
  });
  test(mode+': template background hit target stays behind text before autosave',t=>{
    const h=setup(t,null,mode);
    h.api.applyStyles({page1:[{id:'object-template-background',kind:'image',asset:'a'.repeat(64),x:0,y:0,w:1,h:1,layer:'back'}]});
    const children=[...h.d.querySelectorAll('#canvas-layer > .canvas-object')];
    assert.equal(children[0].dataset.objectId,'object-template-background');
    assert.equal(children[1].dataset.objectId,h.item.id);
    children[1].click();assert.equal(h.field('내용').value,h.item.text);
  });
  test(mode+': template formatting is a single undoable edit and preserves current content',t=>{
    const h=setup(t,null,mode);h.api.load({page1:[{id:h.item.id,text:'현재 보고서',color:'#112233'}]});
    assert.equal(h.api.applyStyles({page1:[{id:h.item.id,kind:'text',fontSize:28,color:'#339955'}]}),true);
    assert.equal(h.api.value().page1[0].text,'현재 보고서');assert.equal(h.api.value().page1[0].fontSize,28);
    h.button('개체 실행 취소').click();assert.equal(h.api.value().page1[0].fontSize,undefined);assert.equal(h.api.value().page1[0].color,'#112233');
    h.button('개체 다시 실행').click();assert.equal(h.api.value().page1[0].fontSize,28);
    h.api.lock(true);assert.equal(h.api.applyStyles({page1:[{id:h.item.id,fontSize:30}]}),false);assert.equal(h.api.value().page1[0].fontSize,28);
  });
  function tableSetup(t){
    const h=setup(t,null,mode);h.item={...h.item,kind:'table',cells:[
      {row:0,col:0,x:0,y:0,w:1,h:.5,text:'병합 머리글'},
      {row:1,col:0,x:0,y:.5,w:.5,h:.5,text:'시료'},
      {row:1,col:1,x:.5,y:.5,w:.5,h:.5,text:'이전 값'}]};
    h.api.page({id:'page1',objects:[h.item]});
    h.cell=(r,c)=>h.d.querySelector('.canvas-cell[data-row="'+r+'"][data-col="'+c+'"]');
    h.key=(code,shift=false)=>h.d.activeElement.dispatchEvent(new h.w.KeyboardEvent('keydown',{keyCode:code,shiftKey:shift,bubbles:true,cancelable:true}));
    h.type=text=>{const input=h.d.querySelector('.ppt-cell-input');input.value=text;input.dispatchEvent(new h.w.Event('input',{bubbles:true}));};
    return h;
  }
  test(mode+': native cell click focuses in place, Tab skips covered cells, Enter commits one undo unit',t=>{
    const h=tableSetup(t);h.cell(1,0).click();let input=h.d.activeElement;
    assert.equal(input.className,'ppt-cell-input');assert.equal(input.value,'시료');
    assert.equal(input.parentNode.style.left,'10%');assert.equal(input.parentNode.style.width,'25%');
    h.type('<b>한글</b>\n시료명');assert.equal(h.d.querySelectorAll('b').length,0);
    h.key(13,true);assert.equal(h.d.activeElement,input,'Shift+Enter leaves the cell open');
    const nextHit=h.cell(1,1);nextHit.dispatchEvent(new h.w.MouseEvent('mousedown',{bubbles:true,cancelable:true,button:0}));
    assert(nextHit.isConnected,'commit must not detach the next click target');nextHit.click();h.key(9,true);
    h.key(9);assert.equal(h.d.activeElement.value,'이전 값');h.type('123.45');h.key(13);
    assert(!h.d.querySelector('.ppt-cell-input'));
    let saved=JSON.parse(JSON.stringify(h.api.value()));
    assert.deepEqual(saved.page1[0],{id:h.item.id,tableCells:[{row:1,col:0,text:'<b>한글</b>\n시료명'},{row:1,col:1,text:'123.45'}]});
    h.button('개체 실행 취소').click();assert.equal(h.api.value().page1[0].tableCells.length,1);
    h.button('개체 다시 실행').click();assert.deepEqual(JSON.parse(JSON.stringify(h.api.value())),saved);
    h.cell(1,0).click();h.key(9,true);assert.equal(h.d.activeElement.value,'병합 머리글');
    h.key(27);h.cell(1,1).click();h.type('취소');h.key(27);assert.deepEqual(JSON.parse(JSON.stringify(h.api.value())),saved);
    // Deleting text is not deleting the table, and cells cannot initiate a move.
    h.cell(1,1).dispatchEvent(new h.w.MouseEvent('mousedown',{bubbles:true,cancelable:true,button:0}));h.cell(1,1).click();
    h.key(8);h.type('');h.key(13);assert.equal(h.api.value().page1[0].hidden,undefined);
    assert.equal(h.api.value().page1[0].x,undefined);assert.equal(h.api.value().page1[0].tableCells[1].text,'');
  });
  test(mode+': IME does not commit midway; outside click saves and movement handles remain separate',t=>{
    const h=tableSetup(t);h.cell(1,1).click();const input=h.d.activeElement;
    input.dispatchEvent(new h.w.Event('compositionstart'));h.type('분석');h.key(13);assert.equal(h.d.activeElement,input);
    input.dispatchEvent(new h.w.Event('compositionend'));h.type('분석 결과');
    h.d.body.dispatchEvent(new h.w.MouseEvent('mousedown',{bubbles:true}));assert(!h.d.querySelector('.ppt-cell-input'));
    h.d.getElementById('slide-stage').getBoundingClientRect=()=>({width:1000,height:500});
    h.d.querySelector('.canvas-move').dispatchEvent(new h.w.MouseEvent('mousedown',{bubbles:true,cancelable:true,button:0,clientX:100,clientY:100}));
    h.d.dispatchEvent(new h.w.MouseEvent('mousemove',{bubbles:true,clientX:200,clientY:150}));h.d.dispatchEvent(new h.w.MouseEvent('mouseup'));
    assert.equal(h.api.value().page1[0].x,.2);assert(Math.abs(h.api.value().page1[0].y-.3)<1e-9);
    h.cell(1,1).click();assert.equal(h.d.activeElement.parentNode.style.left,'45%');h.key(13);
    h.api.lock(true);h.cell(0,0).click();assert(!h.d.querySelector('.ppt-cell-input'));
  });
  function imageSetup(t){
    const h=setup(t,null,mode);h.item={...h.item,kind:'image',x:.1,y:.2,w:.4,h:.2};h.api.page({id:'page1',objects:[h.item]});
    h.d.getElementById('slide-stage').getBoundingClientRect=()=>({width:1000,height:500});h.d.querySelector('.canvas-object').click();
    h.mouse=(target,type,x=100,y=100,shiftKey=false)=>target.dispatchEvent(new h.w.MouseEvent(type,{bubbles:true,cancelable:true,button:0,clientX:x,clientY:y,shiftKey}));
    h.start=()=>h.mouse(h.d.querySelector('.canvas-resize'),'mousedown');
    h.move=(dx,dy,shift=false)=>h.mouse(h.d,'mousemove',100+dx,100+dy,shift);
    h.stop=()=>h.mouse(h.d,'mouseup');
    h.outline=()=>{const n=h.d.querySelector('.canvas-object.selected');return {w:parseFloat(n.style.width)/100,h:parseFloat(n.style.height)/100};};
    return h;
  }
  test(mode+': image drag is free by default, changes only on release, undo/redo and reload retain geometry',t=>{
    const h=imageSetup(t);h.start();h.move(100,0);assert.equal(h.changes(),0);assert.deepEqual(h.outline(),{w:.5,h:.2});h.stop();
    assert.equal(h.changes(),1);let saved=h.api.value();assert.equal(saved.page1[0].w,.5);assert.equal(saved.page1[0].h,.2);
    h.start();h.move(0,50);h.stop();saved=h.api.value();assert.equal(saved.page1[0].w,.5);assert(Math.abs(saved.page1[0].h-.3)<1e-9);
    h.button('개체 실행 취소').click();assert.equal(h.api.value().page1[0].h,.2);h.button('개체 다시 실행').click();assert.deepEqual(h.api.value(),saved);
    h.api.load(saved);h.api.page({id:'page1',objects:[h.item]});h.d.querySelector('.canvas-object').click();assert(Math.abs(h.outline().w/h.outline().h-5/3)<1e-9);
  });
  test(mode+': Shift resize follows horizontal or vertical gestures and clamps both axes without changing ratio',t=>{
    const h=imageSetup(t);
    for(const [dx,dy] of [[100,0],[0,50],[-100,0],[0,-25],[10000,10000],[-10000,-10000]]){
      h.api.load({});h.api.page({id:'page1',objects:[h.item]});h.d.querySelector('.canvas-object').click();h.start();h.move(dx,dy,true);
      const v=h.outline();assert(Math.abs(v.w/v.h-2)<1e-9);assert(v.w>=.002&&v.h>=.002);assert(h.item.x+v.w<=1+1e-9&&h.item.y+v.h<=1+1e-9);
      if(dx===0&&dy>0)assert(v.h>h.item.h);if(dx===0&&dy<0)assert(v.h<h.item.h);h.stop();
      const patch=h.api.value().page1[0];assert(Math.abs(patch.w/patch.h-2)<1e-9);
    }
  });
  test(mode+': Shift key switches mid-drag immediately; Escape, blur and locking discard the preview',t=>{
    const h=imageSetup(t);h.start();h.move(100,10);const free=h.outline();assert(Math.abs(free.w/free.h-2)>.1);
    h.d.dispatchEvent(new h.w.KeyboardEvent('keydown',{key:'Shift',keyCode:16,bubbles:true}));assert(Math.abs(h.outline().w/h.outline().h-2)<1e-9);
    h.d.dispatchEvent(new h.w.KeyboardEvent('keyup',{key:'Shift',keyCode:16,bubbles:true}));assert.deepEqual(h.outline(),free);assert.equal(h.changes(),0);
    h.d.dispatchEvent(new h.w.KeyboardEvent('keydown',{key:'Escape',keyCode:27,bubbles:true}));h.stop();assert.equal(h.changes(),0);assert.deepEqual(h.outline(),{w:.4,h:.2});
    h.start();h.move(100,50);h.w.dispatchEvent(new h.w.Event('blur'));assert.equal(h.changes(),0);assert.deepEqual(h.outline(),{w:.4,h:.2});
    h.start();h.move(100,50);h.api.lock(true);h.stop();assert.equal(h.changes(),0);assert.deepEqual(h.outline(),{w:.4,h:.2});
  });
  test(mode+': touch image resize stays free and touchcancel restores dimensions',t=>{
    const h=imageSetup(t),touch=(target,type,x,y)=>{const e=new h.w.Event(type,{bubbles:true,cancelable:true});Object.defineProperty(e,'touches',{value:type==='touchend'?[]:[{clientX:x,clientY:y}]});target.dispatchEvent(e);};
    touch(h.d.querySelector('.canvas-resize'),'touchstart',100,100);touch(h.d,'touchmove',200,100);assert.deepEqual(h.outline(),{w:.5,h:.2});touch(h.d,'touchcancel',200,100);assert.equal(h.changes(),0);
    touch(h.d.querySelector('.canvas-resize'),'touchstart',100,100);touch(h.d,'touchmove',100,150);touch(h.d,'touchend',100,150);assert.equal(h.changes(),1);assert.equal(h.api.value().page1[0].w,.4);assert(Math.abs(h.api.value().page1[0].h-.3)<1e-9);
  });
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
  const h=setup(t),buttons=[...h.d.querySelectorAll('#canvas-toggle,#canvas-tools button')];
  assert.equal(buttons.length,12);
  for(const b of buttons){assert(b.querySelector('svg[aria-hidden="true"]'));assert(b.title);assert.equal(b.getAttribute('aria-label'),b.title);assert.equal(b.textContent,'');}
  assert.equal(h.d.querySelectorAll('.canvas-tool-group').length,3);
});
test('only the mode toggle moves beside page visibility; tools stay in their existing container',t=>{
  const h=setup(t),toggle=h.d.getElementById('canvas-toggle'),host=h.d.getElementById('canvas-tools'),body=h.d.getElementById('canvas-controls');
  assert.equal(toggle.previousElementSibling.id,'page-sidebar-toggle');assert(!host.contains(toggle));
  assert.equal(body.parentNode,host);assert.equal(host.querySelectorAll('button').length,11);
  assert.equal(toggle.getAttribute('aria-controls'),body.id);assert.equal(toggle.getAttribute('aria-expanded'),'true');
  const controls=[...host.querySelectorAll('button')],before=JSON.stringify(h.api.value());
  toggle.click();assert(body.hidden);assert.equal(toggle.getAttribute('aria-expanded'),'false');
  assert(!toggle.hidden);assert.equal(JSON.stringify(h.api.value()),before);assert.equal(h.changes(),0);
  toggle.click();assert(!body.hidden);assert.equal(toggle.getAttribute('aria-expanded'),'true');assert.deepEqual([...host.querySelectorAll('button')],controls);
  h.api.layout({canvasEditing:false});assert(toggle.hidden);assert(host.hidden);assert.equal(toggle.getAttribute('aria-expanded'),'false');
  h.api.layout({canvasEditing:true});assert(!toggle.hidden);assert(!host.hidden);assert.equal(toggle.getAttribute('aria-expanded'),'true');
  h.api.lock(true);assert(toggle.disabled);toggle.click();assert(h.api.active());
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

test('native table is one movable object with cell-specific text/styles, undo and persisted selection',t=>{
  const h=setup(t),table={id:h.item.id,kind:'table',name:'표',x:.05,y:.2,w:.9,h:.4,
    cells:[{row:0,col:0,text:'머리글',fontSize:15,bold:true,color:'#ffffff',fill:'#123456',stroke:'#ffffff',lineWidth:.75,dash:'solid',align:'center'},
      {row:1,col:0,text:'시료 A',fontSize:12.5,bold:false,color:'#172b4d',fill:'#ffffff',stroke:'#cccccc',lineWidth:.75,dash:'solid',align:'left'}]};
  h.api.page({id:'page1',objects:[table],legacyTableLayout:true});
  assert.equal(h.d.querySelectorAll('.canvas-object').length,1);
  h.d.querySelector('.canvas-object').click();
  const choice=h.d.querySelector('[aria-label="편집할 표 셀"]');assert.equal(choice.options.length,2);
  choice.value='1:0';choice.dispatchEvent(new h.w.Event('change'));
  assert.equal(h.field('내용').value,'시료 A');assert(!h.field('내용').parentNode.hidden);
  h.set('내용','셀만 수정 <b>문자</b>');h.set('글자색','#abcdef');h.set('채우기 색 (none: 없음)','#eeeeee');
  h.set('왼쪽 (%)','8');h.set('너비 (%)','80');
  let patch=h.api.value().page1[0];assert.equal(patch.x,.08);assert.equal(patch.w,.8);assert.equal(patch.text,undefined);
  assert.equal(patch.tableCells.length,1);assert.equal(patch.tableCells[0].text,'셀만 수정 <b>문자</b>');
  assert.equal(patch.tableCells[0].color,'#abcdef');assert.equal(patch.tableCells[0].fill,'#eeeeee');
  assert.equal(table.cells[1].text,'시료 A');assert.equal(h.d.querySelectorAll('b').length,0);
  assert.equal(choice.value,'1:0');assert(!h.d.querySelector('[aria-label="테두리 팔레트"]').disabled);
  h.button('개체 실행 취소').click();assert.equal(h.api.value().page1[0].w,undefined);
  h.button('개체 다시 실행').click();assert.equal(h.api.value().page1[0].w,.8);
  const saved=h.api.value();saved.page1[0].tableCells[0].fontSize=null;saved.page1[0].tableCells[0].align=null;
  h.api.load(saved);h.api.page({id:'page1',objects:[table]});h.d.querySelector('.canvas-object').click();
  choice.value='1:0';choice.dispatchEvent(new h.w.Event('change'));assert.equal(h.field('내용').value,'셀만 수정 <b>문자</b>');
  assert.equal(h.field('글자 크기 (pt)').value,'12.5');assert.equal(h.field('정렬').value,'left');
  h.api.lock(true);assert(choice.disabled);assert(h.field('내용').disabled);
});
test('control availability traverses a large page once instead of once per input',t=>{
  const h=setup(t),objects=Array.from({length:200},(_,i)=>({...h.item,id:'shape-'+i.toString(16).padStart(20,'0')}));
  h.api.page({id:'page1',objects});h.d.querySelector('.canvas-object').click();
  const stringify=h.w.JSON.stringify;let copies=0;
  h.w.JSON.stringify=function(value){copies++;return stringify(value);};
  h.api.lock(false);assert.equal(copies,objects.length);
  assert(!h.field('내용').disabled);
  copies=0;h.api.lock(true);assert.equal(copies,0);assert(h.field('내용').disabled);
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

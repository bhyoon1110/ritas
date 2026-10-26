const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom'),acorn=require('acorn');
const source=fs.readFileSync(path.join(__dirname,'../app/report_condition_options.js'),'utf8');
function setup(t,initial){
  const dom=new JSDOM('<main></main>',{url:'https://example.test/edit',runScripts:'outside-only'}),w=dom.window,d=w.document;
  t.after(()=>w.close());if(initial!==undefined)w.localStorage.setItem('choices',typeof initial==='string'?initial:JSON.stringify(initial));
  w.eval(source);const fields=[{field:'model',label:'장비모델',defaults:[]},{field:'type',label:'Type',defaults:['ATR','Transmission']}];
  const api=new w.RistReportConditionOptions({storageKey:'choices',fields});api.mount(d.querySelector('main'));
  const input=d.createElement('input');input.type='text';input.value='RAW original';d.body.appendChild(input);let changes=0,label='Type';
  input.addEventListener('input',()=>changes++);api.attach(d.body,input,()=>label);api.refresh();
  const choices=d.querySelector('datalist'),manager=d.getElementById('report-condition-manager');
  const click=text=>[...d.querySelectorAll('button')].find(b=>b.textContent===text).click();
  const group=d.querySelector('#report-condition-manager select');
  return {w,d,api,input,choices,manager,group,click,changes:()=>changes,label(value){label=value;api.refresh();},
    values:()=>[...choices.options].map(o=>o.value),stored:()=>JSON.parse(w.localStorage.getItem('choices')),
    rows:()=>[...manager.querySelectorAll('.report-condition-option-row input')],
    edit(i,value){const e=this.rows()[i];e.value=value;e.dispatchEvent(new w.Event('input',{bubbles:true}));},
    groupTo(value){group.value=value;group.dispatchEvent(new w.Event('change'));},
    external(value){w.localStorage.setItem('choices',JSON.stringify(value));w.dispatchEvent(new w.StorageEvent('storage',{key:'choices'}));}};
}
test('condition choices use ES5 and preserve manual values, normalized saved choices and intentional empties',t=>{
  acorn.parse(source,{ecmaVersion:5});const h=setup(t,{type:['  Custom  ','custom','', '<b>Literal</b>'],model:[]});
  assert.deepEqual(h.values(),['Custom','<b>Literal</b>']);assert.equal(h.d.querySelectorAll('b').length,0);
  assert.equal(h.input.value,'RAW original');assert.equal(h.changes(),0);
  assert.equal(h.input.list,h.choices);assert.equal(h.d.querySelectorAll('.report-condition-picker').length,0);
  h.input.value='Custom';h.input.dispatchEvent(new h.w.Event('input',{bubbles:true}));assert.equal(h.input.value,'Custom');assert.equal(h.changes(),1);
  h.label('장비모델');assert.deepEqual(h.values(),[]);assert.equal(h.input.value,'Custom');
  h.label('unknown');assert.equal(h.input.list,null);h.label('  type  ');assert.equal(h.input.list,h.choices);
});
test('one editable field accepts unlisted and blank values; existing multiline text is never coerced',t=>{
  const h=setup(t);assert(h.api.supports('Type','manual'));assert(!h.api.supports('Type','line 1\nline 2'));
  assert(!h.api.supports('Type','line 1\rline 2'));assert(!h.api.supports('환경/비고',''));
  for(const value of ['unlisted value','']){h.input.value=value;h.input.dispatchEvent(new h.w.Event('input'));h.api.refresh();assert.equal(h.input.value,value);}
  assert.equal(h.changes(),2);assert.equal(h.stored(),null);
  const note=h.d.createElement('textarea');note.value='RAW\nmodel';h.d.body.appendChild(note);
  h.api.attach(h.d.body,note,()=> 'Type');h.api.refresh();assert.equal(note.value,'RAW\nmodel');assert.equal(h.d.querySelectorAll('datalist').length,1);
});
test('add edit delete save cancel reset and reopen retain choices without touching report',t=>{
  const h=setup(t,{other:['preserve']});h.click('선택지 관리');h.click('선택지 추가');h.edit(0,'Model 1');h.click('선택지 추가');h.edit(1,' model 1 ');
  h.groupTo('type');h.edit(0,'edited');h.click('취소');assert(h.manager.hidden);assert.deepEqual(h.stored(),{other:['preserve']});
  h.click('선택지 관리');assert.deepEqual(h.rows().map(e=>e.value),['ATR','Transmission']);
  h.click('삭제');h.edit(0,'ATR-NEW');h.click('선택지 저장');assert.deepEqual(h.values(),['ATR-NEW']);assert.deepEqual(h.stored().other,['preserve']);
  h.click('선택지 관리');h.click('삭제');h.click('선택지 저장');assert.deepEqual(h.values(),[]);
  h.click('선택지 관리');h.click('전체 기본값 복원');assert.deepEqual(h.values(),[]);h.click('선택지 저장');assert.deepEqual(h.values(),['ATR','Transmission']);
  h.click('선택지 관리');h.groupTo('model');h.click('선택지 추가');h.edit(0,' Model 1 ');h.click('선택지 추가');h.edit(1,'model 1');h.click('선택지 저장');
  assert.deepEqual(h.stored().model,['Model 1']);assert.equal(h.changes(),0);assert.equal(h.input.value,'RAW original');
});
test('storage synchronization refreshes menus, merges unrelated changes and detects same-field conflicts',t=>{
  const h=setup(t);h.click('선택지 관리');h.groupTo('type');h.edit(0,'Local');h.external({model:['Other window']});
  h.click('선택지 저장');assert.deepEqual(h.stored().model,['Other window']);assert.deepEqual(h.values(),['Local','Transmission']);
  h.click('선택지 관리');h.edit(0,'Unsubmitted');h.external({type:['External'],model:['Other window']});
  h.click('선택지 저장');assert(!h.manager.hidden);assert.match(h.manager.textContent,/같은 항목/);assert.equal(h.rows()[0].value,'Unsubmitted');
  assert.deepEqual(h.values(),['External']);h.click('취소');h.click('선택지 관리');assert.equal(h.rows()[0].value,'External');
  assert.equal(h.input.value,'RAW original');assert.equal(h.changes(),0);
});
test('sending locks all choice controls; Escape cancels without saving',t=>{
  const h=setup(t);h.click('선택지 관리');h.api.lock(true);
  assert([...h.manager.querySelectorAll('button,input,select')].every(n=>n.disabled));assert(h.input.disabled);assert.equal(h.changes(),0);
  h.api.lock(false);h.group.dispatchEvent(new h.w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
  assert(h.manager.hidden);assert.equal(h.stored(),null);
});
test('unavailable storage reports failure and retains the draft instead of pretending to save',t=>{
  const h=setup(t);h.click('선택지 관리');h.click('선택지 추가');h.edit(0,'Keep draft');
  h.w.Storage.prototype.setItem=function(){throw Error('quota');};h.click('선택지 저장');
  assert(!h.manager.hidden);assert.match(h.manager.textContent,/저장하지 못했습니다/);assert.equal(h.rows()[0].value,'Keep draft');assert.equal(h.changes(),0);
});
test('malformed preferences fall back safely without overwriting broken storage on save',t=>{
  const h=setup(t,'invalid json');assert.deepEqual(h.values(),['ATR','Transmission']);h.click('선택지 관리');
  h.click('선택지 추가');h.edit(0,'new');h.click('선택지 저장');assert(!h.manager.hidden);assert.match(h.manager.textContent,/저장하지 못했습니다/);
});

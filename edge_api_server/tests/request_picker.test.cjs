const {test} = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), {JSDOM} = require('jsdom');
function fixture(t) {
  const dom = new JSDOM('<div id="host"></div>', {runScripts:'outside-only',pretendToBeVisual:true});
  t.after(() => dom.window.close());
  const w=dom.window, d=w.document, choices=[], calls=[];
  w.RistFileProgress={monitor:() => ({start(){},stop(){}})};
  for (const name of ['request_filters','request_picker']) w.eval(fs.readFileSync(path.join(__dirname,'../app/'+name+'.js'),'utf8'));
  const picker=w.RistRequestPicker.mount(d.getElementById('host'),{experimentType:'TEM',fetch:(url,callback)=>calls.push({url,callback}),onSelect:item=>choices.push(item)});
  const byId=id=>d.getElementById('request-'+id), results=()=>Array.from(d.querySelectorAll('#request-results [role=option]'));
  const search=value=>{byId('search').value=value;byId('search').dispatchEvent(new w.Event('input'));};
  const reply=(items,page=1,totalCount=items.length)=>calls.at(-1).callback(null,{items,page,pageSize:200,totalCount});
  const load=()=>byId('load').click();
  return {w,d,picker,calls,choices,byId,results,search,reply,load};
}
const item={requestNumber:'REQ-SAME',requestDate:'2026-11-23',customerRequestName:'원소 분포 확인',requestUserName:'김의뢰',
  customerName:'연구기관',testChargerName:'박담당',sampleName:'입자 A',testMethodCode:'M-EDS',testMethodName:'TEM-EDS',testState:7,
  sampleResultNo:101,testMethodResultNo:201,requestResultNo:301};

test('shared table shows names and identities; selected detail retains exact duplicate request/test row',t=>{
  const ui=fixture(t),other={...item,sampleResultNo:102,testMethodResultNo:202};
  ui.load();ui.reply([item,other]);assert.equal(ui.results().length,2);
  assert.match(ui.results()[0].textContent,/김의뢰연구기관/);
  assert.match(ui.results()[0].textContent,/시료 #101/);
  assert.match(ui.results()[1].textContent,/시험 #202/);
  ui.results()[1].click();assert.equal(ui.choices[0],other);assert.equal(ui.picker.selected(),other);
  const details=ui.byId('detail').textContent;
  for(const text of ['박담당','원소 분포 확인','102','202','301']) assert(details.includes(text),text);
  assert.equal(ui.byId('results').hidden,true);
});

test('all visible identifying fields and person names support multi-word current-page search',t=>{
  const ui=fixture(t);ui.load();ui.reply([item,{...item,requestUserName:'홍다른',testChargerName:'최다른',sampleResultNo:999}]);
  for(const query of ['김의뢰 연구기관','박담당','101','분포 확인','TEM-EDS']) {
    ui.search(query);assert(ui.results().length>0,query);
  }
  ui.search('김의뢰 999');assert.equal(ui.results().length,0);assert.equal(ui.calls.length,1);assert.equal(ui.choices.length,0);
  assert.match(ui.byId('results').textContent,/현재 페이지/);
});

test('untrusted names are text and absent values are explicitly marked without invented identifiers',t=>{
  const ui=fixture(t);ui.load();ui.reply([{requestNumber:'<img src=x onerror=alert(1)>',sampleName:'<b>시료</b>'}]);
  assert.equal(ui.byId('results').querySelectorAll('img,b').length,0);
  assert.match(ui.results()[0].textContent,/미제공/);ui.results()[0].click();
  assert.equal(ui.byId('detail').querySelectorAll('img,b').length,0);
});

test('query changes and failed reloads cannot expose stale selectable results; selected identity is cleared',t=>{
  const ui=fixture(t);ui.load();ui.reply([item]);ui.results()[0].click();
  const source=ui.d.querySelector('[aria-label="조회 대상"]');source.value='lims';source.dispatchEvent(new ui.w.Event('change'));
  assert.equal(ui.results().length,0);assert.equal(ui.picker.selected(),null);
  ui.load();ui.calls.at(-1).callback(Error('서버 조회 실패'));
  ui.byId('search').focus();assert.equal(ui.results().length,0);assert.equal(ui.choices.length,1);
  assert.match(ui.byId('info').textContent,/서버 조회 실패/);assert.equal(ui.byId('load').disabled,false);
});

test('paging keeps keyword and shows page scope; typing does not select, IME Enter does not commit',t=>{
  const ui=fixture(t);ui.load();ui.reply([item],1,201);ui.search('홍다른');
  ui.byId('next').click();assert.match(ui.calls.at(-1).url,/page=2&/);ui.reply([{...item,requestUserName:'홍다른'}],2,201);
  assert.equal(ui.results().length,1);assert.match(ui.byId('info').textContent,/2페이지/);
  const input=ui.byId('search');input.dispatchEvent(new ui.w.KeyboardEvent('keydown',{key:'Enter'}));assert.equal(ui.choices.length,0);
  input.dispatchEvent(new ui.w.KeyboardEvent('keydown',{key:'ArrowDown'}));
  input.dispatchEvent(new ui.w.Event('compositionstart'));
  input.dispatchEvent(new ui.w.KeyboardEvent('keydown',{key:'Enter'}));assert.equal(ui.choices.length,0);
  input.dispatchEvent(new ui.w.Event('compositionend'));
  input.dispatchEvent(new ui.w.KeyboardEvent('keydown',{key:'ArrowDown'}));
  input.dispatchEvent(new ui.w.KeyboardEvent('keydown',{key:'Enter'}));assert.equal(ui.choices.length,1);
});

test('clear removes prior details; destroy removes body overlay and ignores late responses',t=>{
  const ui=fixture(t);ui.load();ui.reply([item]);ui.results()[0].click();
  ui.picker.clear();assert.equal(ui.byId('detail').hidden,true);assert.equal(ui.picker.selected(),null);
  ui.load();ui.picker.destroy();ui.reply([item]);assert.equal(ui.byId('results'),null);
  assert.equal(ui.choices.length,1);
});


test('changing between current and ALL clears selection and requires a fresh lookup', t => {
  const ui=fixture(t);ui.load();ui.reply([item]);ui.results()[0].click();
  const scope=ui.d.querySelector('[aria-label="조회 범위"]');
  assert.equal(scope.value,'current');
  scope.value='all';scope.dispatchEvent(new ui.w.Event('change'));
  assert.equal(ui.picker.selected(),null);assert.equal(ui.byId('detail').hidden,true);
  assert.equal(ui.results().length,0);assert.match(ui.byId('info').textContent,/이전 선택을 해제/);
  ui.load();assert(!new URL(ui.calls.at(-1).url,'https://edge.test').searchParams.has('experimentType'));
  ui.reply([item]);ui.results()[0].click();
  scope.value='current';scope.dispatchEvent(new ui.w.Event('change'));
  assert.equal(ui.picker.selected(),null);assert.equal(ui.results().length,0);
  ui.load();assert.equal(new URL(ui.calls.at(-1).url,'https://edge.test').searchParams.get('experimentType'),'TEM');
});

test('partial live search retains mode while paging and clears selection when code filters change',t=>{
  const ui=fixture(t),field=name=>ui.d.querySelector('[aria-label="'+name+'"]');
  assert.equal(field('검색 방식').value,'EXACT');assert.equal(field('검색 방식').disabled,true);
  const change=(name,value)=>{field(name).value=value;field(name).dispatchEvent(new ui.w.Event('change'));};
  change('조회 대상','lims');change('검색 방식','CONTAINS');change('검색할 의뢰번호','T007');
  ui.load();ui.reply([{...item,axTestMtdCode:'2022M-EDS'}],1,201);ui.results()[0].click();
  assert.match(ui.byId('detail').textContent,/전송용 시험방법코드2022M-EDS/);
  ui.byId('next').click();assert.equal(new URL(ui.calls.at(-1).url,'https://edge.test').searchParams.get('matchMode'),'CONTAINS');
  ui.reply([{...item,axTestMtdCode:'2022M-EDS'}],2,201);
  change('시험방법코드','M-E');assert.equal(ui.picker.selected(),null);assert.equal(ui.byId('detail').hidden,true);assert.equal(ui.results().length,0);
  ui.load();assert.equal(new URL(ui.calls.at(-1).url,'https://edge.test').searchParams.get('page'),'1');ui.reply([item]);
  change('조회 대상','cache');assert.equal(field('검색 방식').value,'EXACT');assert.equal(field('검색 방식').disabled,true);
});

test('a selected display-only code is explicitly missing the transfer code',t=>{
  const ui=fixture(t);ui.load();ui.reply([{...item,requestNumber:'2022T00793',testMethodCode:'A11111'}]);ui.results()[0].click();
  assert.match(ui.byId('info').textContent,/전송용 시험방법코드가 제공되지/);
  assert.match(ui.byId('detail').textContent,/전송용 시험방법코드미제공/);
});

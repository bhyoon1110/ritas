// Offline DOM/event regression tests; these do not replace real browser layout QA.
// Requires jsdom@26 and acorn@8 in NODE_PATH, and the project Python environment.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {JSDOM} = require('jsdom');
const acorn = require('acorn');

const pages = JSON.parse(execFileSync(process.env.RIST_TEST_PYTHON || 'python3', ['-c', `
import json
from datetime import datetime, timedelta, timezone
from app.auth import AuthContext
from app.voc_web import build_voc_page
pages = {}
for name in ('alice', 'bob', 'admin'):
    context = AuthContext(user_id=name, login_id=name, email='', display_name=name,
        status='ACTIVE', session_id='test', session_expires_at=datetime.now(timezone.utc)+timedelta(hours=1),
        sso_authenticated_at=None, projects=frozenset({'FTIR', 'RAMAN'} if name=='alice' else {'FTIR'}),
        roles=frozenset({'ADMIN'} if name=='admin' else set()), sso_identity=None)
    pages[name] = build_voc_page(context, 'FTIR')
print(json.dumps(pages))
`], {encoding:'utf8'}));

function item(overrides={}) {
  return {vocId:'test-voc',project:'FTIR',projectLabel:'FT-IR',pageUrl:'/ftir',
    authorUserId:'alice',authorName:'작성자',authorLoginId:'alice',title:'범례 표시 개선',
    content:'피크 표시를 확인해 주세요.',status:'RESOLVED',statusLabel:'조치 완료 · 확인 대기',
    resolutionNote:'수정하고 검증했습니다.',resolvedAt:'2026-10-09T01:00:00+00:00',
    resolvedByName:'관리자',createdAt:'2026-10-09T00:00:00+00:00',
    updatedAt:'2026-10-09T01:00:00+00:00',confirmedAt:null,version:3,
    events:[{actorName:'관리자',actorLoginId:'admin',statusLabel:'조치 완료',
      note:'수정하고 검증했습니다.',createdAt:'2026-10-09T01:00:00+00:00'}],...overrides};
}
function list(items) {
  const counts={OPEN:0,IN_PROGRESS:0,RESOLVED:0,CONFIRMED:0};
  for(const row of items) counts[row.status]++;
  return {items,counts,total:items.length,page:1,pageSize:20};
}
function response(data,status=200) {return {ok:status<400,status,json:async()=>data};}
function deferred() {let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};}
async function flush() {await new Promise(setImmediate);await new Promise(setImmediate);}
function setup(t,role,handler) {
  const dom=new JSDOM(pages[role],{url:'https://rist.test/voc?project=FTIR',runScripts:'outside-only'});
  t.after(()=>dom.window.close());
  const w=dom.window, d=w.document, calls=[];
  w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
  w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');this.dispatchEvent(new w.Event('close'));};
  w.fetch=async(url,options)=>{calls.push({url,options});return handler(url,options);};
  for(const script of d.scripts) acorn.parse(script.textContent,{ecmaVersion:2022});
  w.eval(Array.from(d.scripts).find(script=>script.textContent.includes('const config=')).textContent);
  return {w,d,calls,$:id=>d.getElementById(id),submit:id=>d.getElementById(id).dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}))};
}

test('same-project reader sees safe text but cannot act or confirm',async t=>{
  const record=item({title:'<img src=x onerror=alert(1)>',content:'<script>injected()</script>'});
  const ui=setup(t,'bob',async url=>response(url.includes('?')?list([record]):record));
  await flush();
  assert.deepEqual(Array.from(ui.$('project-filter').options,option=>option.value),['','FTIR']);
  assert.equal(ui.$('voc-list').textContent.includes(record.title),true);
  assert.equal(ui.$('voc-list').querySelector('img'),null);
  ui.d.querySelector('.voc-row').click();await flush();
  assert.equal(ui.$('detail-text').textContent,record.content);
  assert.equal(ui.$('detail-text').querySelector('script'),null);
  assert.equal(ui.$('confirm-form').hidden,true);
  assert.equal(ui.$('admin-section').hidden,true);
});

test('author checkbox submits the displayed version and hides after confirmation',async t=>{
  let record=item();
  const ui=setup(t,'alice',async(url,options)=>{
    if(options.method==='POST') {
      assert.equal(url,'/api/v1/voc/test-voc/confirm');
      assert.deepEqual(JSON.parse(options.body),{expectedVersion:3});
      assert.equal(options.headers['X-Requested-With'],'RIST-VOC');
      record=item({status:'CONFIRMED',version:4,confirmedAt:'2026-10-09T02:00:00Z'});
    }
    return response(url.includes('?')?list([record]):record);
  });
  await flush();ui.d.querySelector('.voc-row').click();await flush();
  assert.equal(ui.$('confirm-form').hidden,false);
  assert.equal(ui.$('confirm-submit').disabled,true);
  ui.$('confirm-check').click();assert.equal(ui.$('confirm-submit').disabled,false);
  ui.submit('confirm-form');await flush();
  assert.equal(ui.$('confirm-form').hidden,true);
  assert.equal(ui.$('confirmed-note').hidden,false);
  assert.equal(ui.$('confirm-submit').disabled,true);
});

test('admin saves required resolution and cannot confirm another author',async t=>{
  let record=item({status:'OPEN',resolutionNote:'',version:1,resolvedAt:null});
  const ui=setup(t,'admin',async(url,options)=>{
    if(options.method==='POST') {
      assert.equal(url,'/api/v1/voc/test-voc/status');
      assert.deepEqual(JSON.parse(options.body),{expectedVersion:1,status:'RESOLVED',note:'조치 내용'});
      record=item({version:2,resolutionNote:'조치 내용'});
    }
    return response(url.includes('?')?list([record]):record);
  });
  await flush();ui.d.querySelector('.voc-row').click();await flush();
  assert.equal(ui.$('admin-section').hidden,false);
  ui.$('action-status').value='RESOLVED';ui.$('action-status').dispatchEvent(new ui.w.Event('change'));
  assert.equal(ui.$('action-note').required,true);
  ui.$('action-note').value=' 조치 내용 ';ui.submit('action-form');await flush();
  assert.equal(ui.$('resolution-note').textContent,'조치 내용');
  assert.equal(ui.$('confirm-form').hidden,true);
});

test('create preserves UUID for network retry then displays the created record',async t=>{
  let tries=0, key;
  const record=item({status:'OPEN',resolutionNote:'',version:1});
  const ui=setup(t,'alice',async(url,options)=>{
    if(options.method==='POST') {
      const requestKey=options.headers['Idempotency-Key'];
      assert.match(requestKey,/^[a-f0-9-]{36}$/);
      if(++tries===1){key=requestKey;throw new Error('시험 네트워크 오류');}
      assert.equal(requestKey,key);
      assert.deepEqual(JSON.parse(options.body),{project:'FTIR',title:'새 제목',content:'새 내용'});
      return response(record,201);
    }
    return response(url.includes('?')?list(tries>1?[record]:[]):record);
  });
  await flush();ui.$('new-title').value=' 새 제목 ';ui.$('new-content').value=' 새 내용 ';
  ui.submit('create-form');await flush();
  assert.equal(ui.$('new-content').value,' 새 내용 ');
  assert.equal(ui.$('create-submit').disabled,false);
  ui.submit('create-form');await flush();
  assert.equal(ui.$('new-content').value,'');
  assert.equal(ui.$('detail').open,true);
  assert.equal(ui.$('detail-title').textContent,record.title);
});

test('stale confirmation refreshes resolution and requires a fresh checkbox',async t=>{
  let record=item();
  const ui=setup(t,'alice',async(url,options)=>{
    if(options.method==='POST') {
      record=item({version:4,resolutionNote:'새로 보완한 조치 내용'});
      return response({message:'최신 내용을 확인하세요.'},409);
    }
    return response(url.includes('?')?list([record]):record);
  });
  await flush();ui.d.querySelector('.voc-row').click();await flush();ui.$('confirm-check').click();
  ui.submit('confirm-form');await flush();
  assert.equal(ui.$('resolution-note').textContent,'새로 보완한 조치 내용');
  assert.equal(ui.$('confirm-check').checked,false);
  assert.equal(ui.$('confirm-submit').disabled,true);
  assert.equal(ui.$('detail-notice').textContent,'최신 내용을 확인하세요.');
});

test('older list response never replaces a newer filter result',async t=>{
  const pending=deferred(), record=item();let slow=false;
  const ui=setup(t,'alice',async url=>{
    if(slow&&url.includes('project=RAMAN'))return pending.promise;
    return response(list([record]));
  });
  await flush();slow=true;ui.$('project-filter').value='RAMAN';ui.$('project-filter').dispatchEvent(new ui.w.Event('change'));
  ui.$('project-filter').value='FTIR';ui.$('project-filter').dispatchEvent(new ui.w.Event('change'));await flush();
  pending.resolve(response(list([item({title:'오래된 Raman 목록'})])));await flush();
  assert.equal(ui.d.querySelector('.row-title').textContent,record.title);
});

test('old mutation completion does not invalidate a newly opened detail request',async t=>{
  const mutation=deferred(), secondDetail=deferred();
  const first=item(), second=item({vocId:'second-voc',title:'두 번째 VOC'});
  const ui=setup(t,'alice',async(url,options)=>{
    if(options.method==='POST')return mutation.promise;
    if(url.includes('?'))return response(list([first,second]));
    if(url.endsWith('/second-voc'))return secondDetail.promise;
    return response(first);
  });
  await flush();ui.d.querySelectorAll('.voc-row')[0].click();await flush();
  ui.$('confirm-check').click();ui.submit('confirm-form');
  ui.$('close-detail').click();ui.d.querySelectorAll('.voc-row')[1].click();
  mutation.resolve(response(item({status:'CONFIRMED'})));await flush();
  secondDetail.resolve(response(second));await flush();
  assert.equal(ui.$('detail').open,true);
  assert.equal(ui.$('detail-content').hidden,false);
  assert.equal(ui.$('detail-title').textContent,'두 번째 VOC');
});

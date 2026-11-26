const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom');
const source=fs.readFileSync(path.join(__dirname,'../app/report_delivery_progress.js'),'utf8');
const ui=require('../app/report_delivery_progress.js');

test('local enqueue and legacy completion never claim LIMS acceptance or recipient delivery',()=>{
  assert.equal(ui.summary({transferStatus:'PENDING'}).label,'Edge 전송 요청 접수');
  assert.equal(ui.summary({transferStatus:'COMPLETED'}).label,'전송 기록 완료 · 메일 상태 미확인');
  const sent=ui.summary({transferStatus:'COMPLETED',mailStatus:'SENT'});
  assert.equal(sent.label,'메일 서버 전달 완료');
  assert.match(sent.description,/수신자의 수신 확인을 뜻하지 않습니다/);
  assert.equal(sent.terminal,true);
});

test('retry is offered only for failed queue and failed existing mail; polling slows for review',()=>{
  const item={transfer_status:'FAILED',mailStatus:'FAILED',mailRequestId:'mail-123'};
  assert.equal(ui.summary(item).canRetryMail,true);
  for(const patch of [{mailStatus:'PENDING'},{transfer_status:'RETRY_WAIT'},{mailStatus:'SENT'},{mailRequestId:null}])
    assert.equal(ui.summary({...item,...patch}).canRetryMail,false);
  assert.equal(ui.summary({transferStatus:'RETRY_WAIT',documentStatus:'FAILED',deliveryPhase:'DOCUMENT_PENDING'}).terminal,false);
  assert.equal(ui.summary({transferStatus:'RETRY_WAIT',needsReview:true}).refreshMs,600000);
});

test('progress identifiers and external errors are escaped; no private envelope is rendered',()=>{
  const html=ui.html({mailRequestId:'<img src=x onerror=alert(1)>',mailErrorMessage:'<script>evil()</script>',
    mailTryCount:0,to:'private@example.com',htmlBody:'secret body',needsReview:true});
  assert(!html.includes('<img'));assert(!html.includes('<script>'));assert(html.includes('&lt;script&gt;'));
  assert(html.includes('LIMS 발송 시도'));assert(html.includes('<dd>0</dd>'));
  assert(!html.includes('private@example.com'));assert(!html.includes('secret body'));
  assert(html.includes('확인 필요'));
});

test('UNKNOWN_KEY explains the LIMS registration mismatch and preserves the escaped original error',()=>{
  const message='code=UNKNOWN_KEY, message=RITAS 에 없는 의뢰번호·시험방법코드입니다. reqNumber = 2022T00793, testMtdCode = A11111 <img src=x onerror=alert(1)>';
  for(const item of [
    {transferStatus:'FAILED',lastErrorCode:'LIMS_BUSINESS_KEY_REJECTED',lastErrorMessage:message},
    {transfer_status:'FAILED',last_error_code:'UNKNOWN_KEY',last_error_message:message}
  ]) {
    const summary=ui.summary(item),html=ui.html(item);
    assert.equal(summary.label,'LIMS 의뢰·시험 확인 필요');
    assert.match(summary.description,/LIMS 문서 등록이 거절/);
    assert.match(summary.description,/문서등록용 시험코드/);
    assert.match(summary.description,/LIMS 담당자/);
    assert.match(summary.description,/반복 전송하지 마세요/);
    assert.equal(summary.step,-1); // No persisted stage proves which prior steps finished.
    assert.equal(summary.terminal,true);assert.equal(summary.canRetryMail,false);
    assert(html.includes('reqNumber = 2022T00793'));assert(html.includes('testMtdCode = A11111'));
    assert(html.includes('&lt;img'));assert(!html.includes('<img'));
    assert(!html.includes('SSO'));assert(!html.includes('장비코드'));
  }
});

test('unrelated business errors and stale UNKNOWN_KEY do not override actual delivery state',()=>{
  for(const item of [
    {transferStatus:'FAILED',lastErrorCode:'LIMS_BUSINESS_KEY_REJECTED',lastErrorMessage:'testMtdCode 값이 없습니다.'},
    {transferStatus:'FAILED',lastErrorCode:'LIMS_BUSINESS_KEY_REJECTED',lastErrorMessage:'code=UNKNOWN_KEY_EXTRA, message=other'},
    {transferStatus:'FAILED',lastErrorCode:'SFTP_CONNECTION_FAILED',lastErrorMessage:'UNKNOWN_KEY'},
    {transferStatus:'FAILED',documentStatus:'COMPLETED',deliveryPhase:'MAIL_READY',lastErrorCode:'UNKNOWN_KEY'},
  ]) assert.equal(ui.summary(item).label,'Edge 전송 실패');
  assert.equal(ui.summary({transferStatus:'RETRY_WAIT',lastErrorCode:'UNKNOWN_KEY'}).label,'Edge 전송 요청 접수');
  assert.equal(ui.summary({transferStatus:'COMPLETED',mailStatus:'SENT',lastErrorCode:'UNKNOWN_KEY'}).label,'메일 서버 전달 완료');
  assert.equal(ui.summary({transferStatus:'FAILED',mailStatus:'FAILED',lastErrorCode:'UNKNOWN_KEY',mailRequestId:'mail-1'}).label,'메일 발송 실패');
});

test('shared delivery script uses ES5 syntax for legacy browsers',()=>{
  require('acorn').parse(source,{ecmaVersion:5,allowReserved:true});
});

test('attachment limit display distinguishes raw attachments from encoded message size',()=>{
  const html=ui.html({packageSizeBytes:1048576,mailAttachmentLimits:{singleBytes:2097152,totalAttachmentBytes:4194304,basis:'raw',configured:true}});
  assert(html.includes('1.00 MiB'));assert(html.includes('파일당 한도: 2.00 MiB'));
  assert(html.includes('첨부 합계 한도: 4.00 MiB'));assert(html.includes('첨부파일 원본 크기 기준'));
  assert(!html.includes('메일 전체 한도'));
});

function watcher(t){
  const dom=new JSDOM('<div id="host"></div>',{url:'http://edge.test',runScripts:'outside-only',pretendToBeVisual:true});
  t.after(()=>dom.window.close());const w=dom.window,timers=new Map(),calls=[];let id=0,hidden=false;
  Object.defineProperty(w.document,'hidden',{get:()=>hidden});
  w.setTimeout=(callback,ms)=>{timers.set(++id,{callback,ms});return id;};
  w.clearTimeout=id=>timers.delete(id);
  w.XMLHttpRequest=class {
    open(method,url){this.method=method;this.url=url;}
    setRequestHeader(){}
    send(){calls.push(this);}
    abort(){this.aborted=true;}
    reply(payload,status=200){this.status=status;this.responseText=JSON.stringify(payload);this.onload();}
  };
  w.eval(source);
  return {w,calls,timers,api:w.RistDeliveryProgress,
    tick(){const [key,v]=timers.entries().next().value;timers.delete(key);v.callback();return v.ms;},
    hidden(value){hidden=value;w.document.dispatchEvent(new w.Event('visibilitychange'));}};
}

test('watcher avoids overlapping calls, pauses hidden tabs and stops after terminal SENT',t=>{
  const u=watcher(t),seen=[],watch=u.api.watch({url:'/state',onState:s=>seen.push(s)});
  assert.equal(u.calls.length,1);assert.equal(u.calls[0].timeout,15000);
  watch.refresh();assert.equal(u.calls.length,1);
  u.calls[0].reply({transferStatus:'RETRY_WAIT',mailStatus:'PENDING'});
  assert.equal(u.timers.size,1);u.hidden(true);assert.equal(u.timers.size,0);
  u.hidden(false);assert.equal(u.calls.length,2);
  u.calls[1].reply({transferStatus:'COMPLETED',mailStatus:'SENT'});
  assert.equal(u.timers.size,0);u.hidden(true);u.hidden(false);assert.equal(u.calls.length,2);
  watch.refresh();assert.equal(u.calls.length,3);watch.stop();assert(u.calls[2].aborted);
});

test('watcher uses ten-minute review interval, caps automatic polling and can resume manually',t=>{
  const u=watcher(t);let paused=0;
  const watch=u.api.watch({url:'/state',maxCalls:2,onPause:()=>paused++});
  u.calls[0].reply({transferStatus:'RETRY_WAIT',needsReview:true});
  assert.equal(u.tick(),600000);
  u.calls[1].reply({transferStatus:'RETRY_WAIT',needsReview:true});
  assert.equal(paused,1);assert.equal(u.timers.size,0);
  watch.refresh();assert.equal(u.calls.length,3);
  u.calls[2].reply({transferStatus:'RETRY_WAIT'});assert.equal(u.timers.size,1);watch.stop();
});

test('watcher stops automatic calls on denied access and backs off network errors',t=>{
  const u=watcher(t),watch=u.api.watch({url:'/state'});
  u.calls[0].reply({message:'login'},401);assert.equal(u.timers.size,0);
  watch.refresh();u.calls[1].onerror();assert.equal(u.tick(),60000);
  u.calls[2].onerror();assert.equal(u.tick(),600000);
  watch.stop();
});

test('review enqueue message describes Edge and waits for actual LIMS state',t=>{
  const u=watcher(t),d=u.w.document;
  d.body.innerHTML='<span id="status"></span><div id="message"></div><div id="delivery-progress" data-status-url="/state"></div><button id="send" data-send-url="/send"></button><button id="refresh-delivery"></button>';
  u.api.mountReview();u.calls[0].reply({transferStatus:'READY_FOR_REVIEW',canQueue:true});
  d.getElementById('send').click();assert.equal(u.calls[1].method,'POST');
  u.calls[1].reply({status:'PENDING'});
  assert.match(d.getElementById('message').textContent,/Edge가 전송 요청을 접수/);
  assert(!d.getElementById('message').textContent.includes('LIMS 전송 큐에 등록했습니다'));
  u.calls[2].reply({transferStatus:'RETRY_WAIT',mailStatus:'ACCEPTED',canQueue:false});
  assert.match(d.getElementById('status').textContent,/LIMS 메일 접수 완료/);
  assert.equal(d.getElementById('send').disabled,true);
});

test('review action follows terminal state without sending or enabling automatic retry',t=>{
  const u=watcher(t),d=u.w.document;
  d.body.innerHTML='<span id="status"></span><div id="message"></div><div id="delivery-progress" data-status-url="/state"></div><button id="send" data-send-url="/send"></button><button id="refresh-delivery"></button>';
  u.api.mountReview();
  const states=[
    [{transferStatus:'FAILED',lastErrorCode:'LIMS_BUSINESS_KEY_REJECTED',lastErrorMessage:'code=UNKNOWN_KEY, message=missing'},'전송 실패 · 오류 확인'],
    [{transfer_status:'CANCELLED'},'전송 취소됨'],
    [{transferStatus:'COMPLETED'},'전송 기록 완료'],
    [{transferStatus:'COMPLETED',mailStatus:'SENT'},'메일 서버 전달 완료']
  ];
  for(let i=0;i<states.length;i++) {
    if(i) d.getElementById('refresh-delivery').click();
    u.calls[i].reply({...states[i][0],canQueue:false});
    assert.equal(d.getElementById('send').textContent,states[i][1]);
    assert.equal(d.getElementById('send').disabled,true);
    d.getElementById('send').click();
    assert.equal(u.calls.length,i+1);
    assert.equal(u.timers.size,0);
  }
  assert(u.calls.every(call=>call.method==='GET'));
});

test('management detail preserves form edits during status refresh and offers immutable-mail retry',async t=>{
  const html=fs.readFileSync(path.join(__dirname,'../app/report_management.html'),'utf8');
  const dom=new JSDOM(html,{url:'http://edge.test/report-management',runScripts:'outside-only',pretendToBeVisual:true});
  t.after(()=>dom.window.close());const w=dom.window,d=w.document,requests=[],watches=[];
  let item={report_id:'report-1',request_number:'REQ-1',experiment_code:'TEM',transfer_status:'FAILED',mailStatus:'FAILED',mailRequestId:'mail-123',artifacts:[],attempts:[]};
  w.eval(source);w.RistDeliveryProgress.watch=opts=>{watches.push(opts);opts.onState(opts.initialState);return {stop(){},refresh(){}}};
  w.fetch=async(url,opt)=>{requests.push({url,opt});let data={};if(url.includes('/summary'))data={projects:{},orphanFiles:[]};else if(url.includes('?'))data={items:[item],page:1,totalPages:1};else if(opt&&opt.method==='POST'){item={...item,transfer_status:'PENDING'};data={reportId:'report-1',status:'PENDING'};}else data=item;return {ok:true,status:200,json:async()=>data}};
  w.alert=()=>{};w.confirm=()=>false;
  const script=[...d.querySelectorAll('script')].find(x=>!x.src).textContent;w.eval(script);
  const flush=()=>new Promise(resolve=>setTimeout(resolve,0));await flush();
  d.querySelector('tr[data-id]').click();await flush();
  assert.equal(d.getElementById('retry').textContent,'기존 메일 재시도');
  const input=d.getElementById('retention');input.value='2026-11-05T09:00';
  watches[0].onState({...item,needsReview:true});assert.equal(input.value,'2026-11-05T09:00');
  d.getElementById('retry').click();await flush();assert(!requests.some(x=>x.opt&&x.opt.method==='POST'));
  w.confirm=()=>true;d.getElementById('retry').click();await flush();
  assert(requests.some(x=>x.url==='/api/v1/report-management/report-1/retry'&&x.opt.method==='POST'));
});

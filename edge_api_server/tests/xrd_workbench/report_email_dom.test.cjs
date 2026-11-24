// Run the actual editor, not just its helpers: recovery/busy states used to skip mail generation.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
const {JSDOM,VirtualConsole}=require('jsdom');
const esbuild=require('esbuild');
const root=path.resolve(__dirname,'../../app/xrd_workbench/web');
const repo=path.resolve(__dirname,'../../..');
const fixture=JSON.parse(execFileSync(process.env.RIST_TEST_PYTHON||'python3',['-c',
  'import json; from tests.xrd_workbench.test_posco_pptx import fixture; from app.xrd_workbench.core.email import rule_email,email_evidence; p,d=fixture(); print(json.dumps(dict(project=p,document=d,email_default=rule_email(email_evidence(p,d)))))'],
  {cwd:path.join(repo,'edge_api_server'),env:{...process.env,PYTHONDONTWRITEBYTECODE:'1',PYTHONPATH:[repo,path.join(repo,'common'),path.join(repo,'edge_api_server'),process.env.PYTHONPATH||''].join(path.delimiter)},encoding:'utf8'}));
const programs={native:esbuild.buildSync({entryPoints:[path.join(root,'report/report.mjs')],bundle:true,write:false,format:'iife',logLevel:'silent'}).outputFiles[0].text,
  chrome49:fs.readFileSync(path.join(root,'report/report.legacy.js'),'utf8')};
const clone=v=>JSON.parse(JSON.stringify(v));
const flush=async()=>{for(let i=0;i<12;i++)await new Promise(setImmediate);};
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {resolve,promise};};
const response=(value,status=200)=>({ok:status===200,status,headers:{get:()=>null},json:async()=>clone(value),text:async()=>'<html>preview</html>'});

function setup(t,{mode,recovery=null,model,save,load,step='report',mailTimeoutMs=null}={}) {
  const errors=[],calls=[];
  const virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM(fs.readFileSync(path.join(root,'report.html'),'utf8'),{url:'http://rist.test/report.html?project=sample&step='+step,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole});
  t.after(()=>dom.window.close());
  const w=dom.window,d=w.document;
  w.structuredClone=clone;w.confirm=()=>true;
  if(mailTimeoutMs!==null){const timer=w.setTimeout.bind(w);w.setTimeout=(callback,delay,...args)=>timer(callback,delay===60000?mailTimeoutMs:delay,...args);}
  w.XMLHttpRequest=class {
    open(method,url){assert(url.startsWith('/api/v1/report-mail/recipient?'));}
    send(){this.status=200;this.responseText=JSON.stringify({recipientName:'의뢰자',email:'requester@rist.re.kr'});this.onload();}
    abort(){}
  };
  let saved=clone(fixture.document),draft=clone(recovery);
  w.fetch=async (url,options={})=>{
    calls.push({url,options});
    if(url==='/api/platform')return response({operatorId:saved.metadata.operator_id});
    if(url==='/api/presets')return response([]);
    if(url.endsWith('/email-draft'))return model ? model(JSON.parse(options.body)) : response({detail:'QA model unavailable'},503);
    if(url.endsWith('/draft-preview'))return response({});
    if(url.endsWith('/draft/clear')){draft=null;return response({revision:2,document:null});}
    if(url.endsWith('/draft'))return response(options.method==='PUT'?{revision:1}:draft||{revision:0,document:null});
    if(url.endsWith('/checklist'))return response({report:[],samples:[],summary:{samples:0,reviewed:0,blockers:0,warnings:0,infos:0}});
    if(url==='/api/reports/sample'){
      if(options.method==='PUT'){
        const body=JSON.parse(options.body);
        if(save)await save();
        saved={...body.document,revision:body.expected_revision+1};
        return response(saved);
      }
      if(load)await load();
      return response({...fixture,document:saved});
    }
    throw Error('Unexpected request: '+url);
  };
  require('../ppt_tools_harness.cjs')(w);
  w.eval(programs[mode]);
  return {w,d,calls,errors,mailCalls:()=>calls.filter(c=>c.url.endsWith('/email-draft')),
    email:()=>({subject:d.getElementById('emailSubject').value,body:d.getElementById('emailBody').value}),
    openMail:()=>d.querySelector('[data-tab="email"]').click()};
}

for(const mode of Object.keys(programs)) {
  test(`${mode}: delivery step edits request and mail together and preserves them across steps and saving`,async t=>{
    const ui=setup(t,{mode});await flush();
    const byId=id=>ui.d.getElementById(id);
    byId('nextStep').click();await flush();
    assert.equal(byId('savePane').hidden,false);
    assert.equal(byId('editor').hidden,true);
    assert.equal(byId('emailForm').hidden,false);
    assert.ok(byId('deliveryFields').contains(byId('ristRequestLookup')));
    assert.ok(byId('deliveryFields').contains(byId('emailSubject')));
    assert.ok(byId('deliveryFields').contains(byId('ristReportSend')));
    const input=(id,value)=>{
      byId(id).value=value;
      byId(id).dispatchEvent(new ui.w.Event('input',{bubbles:true}));
    };
    input('ristRequestNumber','REQ-TEST');
    input('emailSubject','검토한 제목');
    const body=ui.d.querySelector('.mail-rich');
    body.innerHTML='<p>검토한 분석 결과</p>';
    body.dispatchEvent(new ui.w.Event('input',{bubbles:true}));
    ui.w.xrdLocalStep('report');
    assert.equal(byId('reportNumber').value,'REQ-TEST');
    input('reportNumber','REQ-COVER');
    ui.w.xrdLocalStep('save');await flush();
    assert.equal(byId('ristRequestNumber').value,'REQ-COVER');
    assert.equal(byId('emailSubject').value,'검토한 제목');
    assert.equal(body.textContent,'검토한 분석 결과');
    byId('saveDelivery').click();await flush();
    const saved=JSON.parse(ui.calls.filter(c=>c.url==='/api/reports/sample'&&c.options.method==='PUT').pop().options.body).document;
    assert.equal(saved.metadata.report_number,'REQ-COVER');
    assert.equal(saved.email.subject,'검토한 제목');
    assert.equal(saved.email.delivery.bodyHtml,'<p>검토한 분석 결과</p>');
    assert.equal(byId('savePane').hidden,false);
    assert.equal(ui.d.querySelectorAll('#emailSubject').length,1);
    assert.deepEqual(ui.errors,[]);
  });
  test(`${mode}: direct delivery URL loads mail and resumes automatic drafting after the report arrives`,async t=>{
    const ui=setup(t,{mode,step:'save'});await flush();
    assert.equal(ui.d.getElementById('savePane').hidden,false);
    assert.equal(ui.d.getElementById('emailForm').hidden,false);
    assert.equal(ui.mailCalls().length,1);
    assert.ok(ui.email().subject&&ui.email().body);
    assert.equal(ui.d.getElementById('deliveryFields').disabled,false);
    assert.deepEqual(ui.errors,[]);
  });
  test(`${mode}: mail addresses and rich body survive regeneration, autosave and reload`,async t=>{
    const ui=setup(t,{mode,model:async()=>response({...fixture.email_default,subject:'새 제목',body:'새 본문',status:'llm'})});await flush();ui.openMail();await flush();
    const input=(label,value)=>{const e=ui.d.querySelector('[aria-label="'+label+'"]');e.value=value;e.dispatchEvent(new ui.w.Event('input',{bubbles:true}));};
    assert(ui.d.querySelector('[aria-label="수신자 이메일 주소"]').readOnly);
    input('수신자 이메일 주소','to@example.com');input('비밀참조 (BCC)','hidden@rist.re.kr');input('메일 서명','RIST 서명');
    const rich=ui.d.querySelector('.mail-rich');rich.innerHTML='<strong>분석 결과</strong>';rich.dispatchEvent(new ui.w.Event('input',{bubbles:true}));
    ui.d.getElementById('saveDelivery').click();await flush();
    const saved=JSON.parse(ui.calls.filter(c=>c.url==='/api/reports/sample'&&c.options.method==='PUT').pop().options.body).document;
    assert.equal(saved.email.delivery.to,'');assert.equal(saved.email.delivery.bcc,'hidden@rist.re.kr');assert.equal(saved.email.delivery.bodyHtml,'<strong>분석 결과</strong>');assert.equal(saved.email.body,'분석 결과');
    ui.d.getElementById('generateEmail').click();await flush();
    assert.equal(ui.d.querySelector('[aria-label="비밀참조 (BCC)"]').value,'hidden');assert.equal(ui.d.querySelector('[aria-label="메일 서명"]').value,'RIST 서명');assert.equal(rich.textContent,'새 본문');
    assert.deepEqual(ui.errors,[]);
  });
  test(`${mode}: clicking mail before the initial report arrives does not break the editor`,async t=>{
    const wait=deferred();const ui=setup(t,{mode,load:()=>wait.promise});
    ui.openMail();assert.deepEqual(ui.errors,[]);
    wait.resolve();await flush();assert.ok(ui.email().subject && ui.email().body);
    assert.deepEqual(ui.errors,[]);
  });
  test(`${mode}: seed is filled before opening mail, survives slow/failed requests and saves`,async t=>{
    const wait=deferred();const ui=setup(t,{mode,model:()=>wait.promise});await flush();
    assert.equal(ui.d.getElementById('emailForm').closest('#savePane').id,'savePane');
    assert.deepEqual(ui.email(),{subject:fixture.email_default.subject,body:fixture.email_default.body});
    assert.equal(ui.mailCalls().length,0);
    ui.openMail();await flush();assert.equal(ui.mailCalls().length,1);
    assert.ok(ui.email().subject && ui.email().body);
    assert.equal(ui.d.getElementById('editorFields').disabled,false);
    assert.equal(ui.d.getElementById('deliveryFields').disabled,false);
    wait.resolve(response({detail:'QA network failure'},503));await flush();
    assert.match(ui.d.getElementById('emailStatus').textContent,/기존 문안은 유지/);
    assert.equal(ui.email().body,fixture.email_default.body);
    ui.d.getElementById('save').click();await flush();
    const saved=ui.calls.find(c=>c.url==='/api/reports/sample'&&c.options.method==='PUT');
    assert.equal(JSON.parse(saved.options.body).document.email.body,fixture.email_default.body);
    assert.equal(ui.mailCalls().length,1);assert.deepEqual(ui.errors,[]);
  });
  test(`${mode}: delayed automatic draft leaves mail editable and never overwrites manual edits`,async t=>{
    const wait=deferred(),ui=setup(t,{mode,model:()=>wait.promise});await flush();ui.openMail();await flush();
    const byId=id=>ui.d.getElementById(id),rich=ui.d.querySelector('.mail-rich');
    assert(!byId('emailSubject').matches(':disabled'));
    assert.equal(rich.contentEditable,'true');
    assert.equal(byId('generateEmail').disabled,true);
    assert.equal(byId('cancelEmail').hidden,false);
    byId('emailSubject').value='직접 작성한 제목';byId('emailSubject').dispatchEvent(new ui.w.Event('input',{bubbles:true}));
    rich.innerHTML='<p>직접 작성한 <strong>메일 본문</strong></p>';rich.dispatchEvent(new ui.w.Event('input',{bubbles:true}));
    const cc=ui.d.querySelector('[aria-label="참조 (CC)"]');cc.value='review';cc.dispatchEvent(new ui.w.Event('input',{bubbles:true}));
    wait.resolve(response({...fixture.email_default,subject:'늦게 도착한 제목',body:'늦게 도착한 본문',status:'llm'}));await flush();
    assert.equal(byId('emailSubject').value,'직접 작성한 제목');assert.equal(rich.textContent,'직접 작성한 메일 본문');
    assert.match(byId('emailStatus').textContent,/직접 수정한 문안을 유지/);
    byId('saveDelivery').click();await flush();
    const saved=JSON.parse(ui.calls.filter(c=>c.url==='/api/reports/sample'&&c.options.method==='PUT').pop().options.body).document;
    assert.equal(saved.email.subject,'직접 작성한 제목');assert.equal(saved.email.delivery.bodyHtml,'<p>직접 작성한 <strong>메일 본문</strong></p>');
    assert.equal(saved.email.delivery.cc,'review@rist.re.kr');assert.equal(saved.email.status,'manual');assert.deepEqual(ui.errors,[]);
  });
  test(`${mode}: cancelling an automatic draft ignores a late result and allows a new request`,async t=>{
    const first=deferred();let count=0;
    const ui=setup(t,{mode,model:()=>++count===1?first.promise:Promise.resolve(response({...fixture.email_default,subject:'다시 작성한 제목',body:'다시 작성한 본문',status:'llm'}))});
    await flush();ui.openMail();await flush();const seed=ui.email();
    ui.d.getElementById('cancelEmail').click();await flush();
    assert.equal(ui.d.getElementById('generateEmail').disabled,false);assert.equal(ui.d.getElementById('cancelEmail').hidden,true);
    first.resolve(response({...fixture.email_default,subject:'취소된 제목',body:'취소된 본문',status:'llm'}));await flush();
    assert.deepEqual(ui.email(),seed);
    ui.d.getElementById('generateEmail').click();await flush();
    assert.equal(ui.email().subject,'다시 작성한 제목');assert.deepEqual(ui.errors,[]);
  });
  test(`${mode}: final saving cancels background generation without waiting or changing saved mail`,async t=>{
    const wait=deferred(),ui=setup(t,{mode,model:()=>wait.promise});await flush();ui.openMail();await flush();const seed=ui.email();
    ui.d.getElementById('saveDelivery').click();await flush();
    const saved=JSON.parse(ui.calls.filter(c=>c.url==='/api/reports/sample'&&c.options.method==='PUT').pop().options.body).document;
    assert.equal(saved.email.subject,seed.subject);assert.equal(ui.d.getElementById('saveDelivery').disabled,false);
    wait.resolve(response({...fixture.email_default,subject:'저장 후 늦은 제목',body:'저장 후 늦은 본문',status:'llm'}));await flush();
    assert.deepEqual(ui.email(),seed);assert.deepEqual(ui.errors,[]);
  });
  test(`${mode}: a late draft cannot replace text while Korean composition is in progress`,async t=>{
    const wait=deferred(),ui=setup(t,{mode,model:()=>wait.promise});await flush();ui.openMail();await flush();
    const rich=ui.d.querySelector('.mail-rich'),before=rich.innerHTML;
    rich.dispatchEvent(new ui.w.Event('compositionstart',{bubbles:true}));
    wait.resolve(response({...fixture.email_default,subject:'늦은 자동 제목',body:'늦은 자동 문안',status:'llm'}));await flush();
    assert.equal(rich.innerHTML,before);
    rich.textContent='한글 분석 결과를 입력합니다';rich.dispatchEvent(new ui.w.Event('input',{bubbles:true}));
    rich.dispatchEvent(new ui.w.Event('compositionend',{bubbles:true}));
    ui.d.getElementById('saveDelivery').click();await flush();
    const saved=JSON.parse(ui.calls.filter(c=>c.url==='/api/reports/sample'&&c.options.method==='PUT').pop().options.body).document;
    assert.equal(saved.email.body,'한글 분석 결과를 입력합니다');assert.deepEqual(ui.errors,[]);
  });
  test(`${mode}: a stalled automatic draft times out without locking the mail editor`,async t=>{
    const ui=setup(t,{mode,model:()=>new Promise(()=>{}),mailTimeoutMs:5});await flush();ui.openMail();
    await new Promise(resolve=>setTimeout(resolve,20));await flush();
    assert.equal(ui.d.getElementById('generateEmail').disabled,false);assert.equal(ui.d.getElementById('cancelEmail').hidden,true);
    assert.equal(ui.d.getElementById('deliveryFields').disabled,false);assert.match(ui.d.getElementById('emailStatus').textContent,/응답이 지연/);
    assert.deepEqual(ui.errors,[]);
  });
  test(`${mode}: recovery keeps a visible seed and resumes generation after discard`,async t=>{
    const recovery={revision:1,document:clone(fixture.document),baseline:clone(fixture.document)};
    recovery.document.summary='복구 전 요약';
    const ui=setup(t,{mode,recovery});await flush();ui.openMail();await flush();
    assert.ok(ui.email().subject && ui.email().body);
    assert.equal(ui.mailCalls().length,0);
    assert.match(ui.d.getElementById('emailStatus').textContent,/복구 초안/);
    ui.d.getElementById('discardDraft').click();await flush();
    assert.equal(ui.mailCalls().length,1);assert.ok(ui.email().body);
    assert.deepEqual(ui.errors,[]);
  });
  test(`${mode}: restoring authored mail does not silently replace it with an automatic draft`,async t=>{
    const recovery={revision:1,document:clone(fixture.document),baseline:clone(fixture.document)};
    recovery.document.email={...fixture.document.email,subject:'복구 제목',body:'복구 본문',status:'manual'};
    const ui=setup(t,{mode,recovery});await flush();ui.openMail();
    ui.d.getElementById('restoreDraft').click();await flush();
    assert.deepEqual(ui.email(),{subject:'복구 제목',body:'복구 본문'});
    assert.equal(ui.mailCalls().length,0);assert.deepEqual(ui.errors,[]);
  });
  test(`${mode}: opening mail during save resumes the deferred request once unlocked`,async t=>{
    const wait=deferred();const ui=setup(t,{mode,save:()=>wait.promise});await flush();
    ui.d.getElementById('save').click();await flush();ui.openMail();await flush();
    assert.equal(ui.mailCalls().length,0);assert.ok(ui.email().body);
    wait.resolve();await flush();
    assert.equal(ui.mailCalls().length,1);assert.ok(ui.email().body);
    assert.deepEqual(ui.errors,[]);
  });
}

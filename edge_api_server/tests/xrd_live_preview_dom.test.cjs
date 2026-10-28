// Real screen handlers in a DOM harness, not a claim of native XP/Supermium testing.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
const {JSDOM}=require('jsdom');
const esbuild=require('esbuild');
const root=path.resolve(__dirname,'../app/xrd_workbench/web');
const data=JSON.parse(execFileSync(process.env.RIST_TEST_PYTHON||'python3',['-c',
  'import json; from tests.xrd_workbench.fixture_data import example_project; from app.xrd_workbench.core.report import new_document; p=example_project(); print(json.dumps(dict(project=p,document=new_document(p))))'
],{cwd:path.resolve(__dirname,'..'),encoding:'utf8',maxBuffer:16*1024*1024}));
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn){for(let i=0;i<200;i++){if(fn())return;await pause(5);}assert.fail('DOM condition did not become true');}
for(const legacy of [false,true])test(`${legacy?'Chrome49 fallback':'native'} report: live unsaved draft, exact input links, IME and bfcache`,async t=>{
  const dom=new JSDOM(fs.readFileSync(path.join(root,'report.html'),'utf8'),{url:'http://localhost/report.html?project=fixture',runScripts:'outside-only',pretendToBeVisual:true});
  t.after(()=>dom.window.close());
  const w=dom.window,d=w.document,calls=[];let draftRevision=0;
  w.HTMLElement.prototype.scrollIntoView=function(){};
  w.structuredClone=x=>JSON.parse(JSON.stringify(x));
  const timer=w.setTimeout.bind(w);w.setTimeout=(fn,ms,...args)=>timer(fn,ms>=900?20:ms,...args);
  const mockFetch=async(url,options={})=>{
    calls.push({url,options});
    if(url.endsWith('/draft-preview'))return {ok:true,json:async()=>({pageEditing:true,canvasEditing:true,ratio:16/9,pages:[{
      id:'generated-'+'a'.repeat(20)+'-0',title:JSON.parse(options.body).document.metadata.title,
      objects:[],targets:[{x:.1,y:.1,w:.3,h:.2,target:{tab:'overview',field:'client'}}],image:'data:image/png;base64,QA',thumbnail:'data:image/png;base64,QA'}]})};
    if(url.endsWith('/draft')){if(options.method==='PUT'){assert.equal(JSON.parse(options.body).expected_revision,draftRevision);draftRevision++;}return {ok:true,json:async()=>({revision:draftRevision,document:null})};}
    let value=url==='/api/reports/fixture'?data:url==='/api/platform'?{operatorId:''}:url==='/api/presets'?[]:{};
    return {ok:true,json:async()=>JSON.parse(JSON.stringify(value))};
  };
  w.fetch=mockFetch;
  require('./ppt_tools_harness.cjs')(w);
  if(legacy){
    const compat=path.resolve(__dirname,'../app/static/chrome49');
    const manifest=JSON.parse(fs.readFileSync(path.join(compat,'manifest.json'),'utf8'));
    w.eval(fs.readFileSync(path.join(compat,manifest.runtime),'utf8'));
    w.fetch=mockFetch;
    w.eval(fs.readFileSync(path.join(root,'report/report.legacy.js'),'utf8'));
  }else w.eval(esbuild.buildSync({entryPoints:[path.join(root,'report/report.mjs')],bundle:true,write:false,format:'iife',logLevel:'silent'}).outputFiles[0].text);
  const frame=d.getElementById('reportPreview'),title=d.getElementById('title');
  const preview=()=>d.getElementById('page-status').textContent;
  await until(()=>preview().includes('XRD 분석 결과'));
  const progress=()=>d.getElementById('page-status').closest('.rist-task-progress');
  assert(progress().classList.contains('is-running'));
  d.getElementById('slide-image').onload();assert(!progress().classList.contains('is-running'));
  assert.equal(frame.getAttribute('sandbox'),'allow-scripts allow-popups allow-popups-to-escape-sandbox');
  const edit=value=>{title.value=value;title.dispatchEvent(new w.Event('input',{bubbles:true}));};
  title.focus();edit('새 제목');edit('최종 제목');
  await until(()=>preview().includes('최종 제목'));
  assert(progress().classList.contains('is-running'));
  d.getElementById('slide-image').onerror();assert(!progress().classList.contains('is-running'));assert.match(preview(),/불러오지 못/);
  d.getElementById('draftRefresh').click();await until(()=>preview().includes('최종 제목'));
  d.getElementById('slide-image').onload();assert(!progress().classList.contains('is-running'));
  assert.equal(d.activeElement,title);
  assert.match(d.getElementById('previewStatus').textContent,/미저장 초안/);
  assert(!calls.some(c=>(c.options.method==='PUT'&&c.url==='/api/reports/fixture')||/\/exports$|\/send$/.test(c.url)));
  await until(()=>draftRevision>0);
  const n=calls.filter(c=>c.url.endsWith('/draft-preview')).length;
  title.dispatchEvent(new w.CompositionEvent('compositionstart',{bubbles:true}));edit('한글 조합');
  await pause(50);assert.equal(calls.filter(c=>c.url.endsWith('/draft-preview')).length,n);
  title.dispatchEvent(new w.CompositionEvent('compositionend',{bubbles:true}));
  await until(()=>preview().includes('한글 조합'));
  const send=(target,source=frame.contentWindow)=>w.dispatchEvent(new w.MessageEvent('message',{source,data:{type:'xrd-preview-edit',target}}));
  send({tab:'overview',field:'client'},w);assert.equal(d.activeElement,title); // unrelated frame ignored
  d.querySelector('.edit-region').click();assert.equal(d.activeElement.id,'client');
  const sample=data.document.samples.find(s=>s.notes.length>1);
  assert(sample,'synthetic sample has multiple notes');
  send({tab:sample.sample_id,field:'note',id:sample.notes[1].id});
  assert.equal(d.activeElement.dataset.noteText,sample.notes[1].id);
  send({tab:'overview',field:'title'});assert.equal(d.activeElement,title);
  const count=calls.length;
  w.dispatchEvent(new w.PageTransitionEvent('pagehide',{persisted:true}));
  w.dispatchEvent(new w.PageTransitionEvent('pageshow',{persisted:true}));
  await until(()=>calls.length>count);
  edit('복귀 후 편집');await until(()=>preview().includes('복귀 후 편집'));
});

for(const legacy of [false,true])test(`${legacy?'Chrome49 fallback':'native'} upload: cards follow mapping edits and block orphan PDF`,async t=>{
  const dom=new JSDOM(fs.readFileSync(path.join(root,'index.html'),'utf8'),{url:'http://localhost/?project=fixture',runScripts:'outside-only',pretendToBeVisual:true});
  t.after(()=>dom.window.close());const w=dom.window,d=w.document,calls=[];
  const mockFetch=async url=>{calls.push(url);return {ok:false,status:404,json:async()=>({detail:'No saved project in this upload-only fixture'})};};
  w.HTMLElement.prototype.scrollIntoView=function(){};
  w.structuredClone=x=>JSON.parse(JSON.stringify(x));
  w.Plotly={newPlot:async el=>{el.on=()=>{};},react:async()=>{}};
  w.fetch=mockFetch;
  w.eval(fs.readFileSync(path.join(__dirname,'../../common/rist_common/plotting/file_progress.js'),'utf8'));
  if(legacy){
    const compat=path.resolve(__dirname,'../app/static/chrome49');
    const manifest=JSON.parse(fs.readFileSync(path.join(compat,'manifest.json'),'utf8'));
    w.eval(fs.readFileSync(path.join(compat,manifest.runtime),'utf8'));w.fetch=mockFetch;
    w.eval(fs.readFileSync(path.join(root,'viewer/viewer.legacy.js'),'utf8'));
  }else w.eval(esbuild.buildSync({entryPoints:[path.join(root,'viewer/viewer.mjs')],bundle:true,write:false,format:'iife',logLevel:'silent'}).outputFiles[0].text);
  await until(()=>d.getElementById('status').textContent.includes('등록 프로젝트를 열지 못했습니다'));
  const files=['A.txt','A_상매칭_Report.pdf','B_상매칭_Report.pdf'].map(name=>new w.File(['synthetic'],name));
  const input=d.getElementById('fileInput');Object.defineProperty(input,'files',{value:files});
  input.dispatchEvent(new w.Event('change',{bubbles:true}));
  assert.equal(d.querySelectorAll('.uploadSample').length,2);
  assert.match(d.getElementById('uploadSummary').textContent,/B: TXT 없음/);
  d.getElementById('uploadSubmit').click();
  assert.match(d.getElementById('uploadError').textContent,/오류를 먼저/);
  assert(!calls.some(url=>url.includes('upload')));
  const ignore=d.querySelector('[data-file="2"] .roleMapping');ignore.value='ignore';
  ignore.dispatchEvent(new w.Event('change',{bubbles:true}));
  assert.equal(d.querySelectorAll('.uploadSample').length,1);
  assert.match(d.getElementById('uploadSummary').textContent,/제외 파일 1개/);
  assert(!d.querySelector('#uploadSummary .error'));
});

function runScreen(w,screen,legacy,mockFetch){
  w.HTMLElement.prototype.scrollIntoView=function(){};
  w.structuredClone=x=>JSON.parse(JSON.stringify(x));w.fetch=mockFetch;
  w.eval(fs.readFileSync(path.join(__dirname,'../../common/rist_common/plotting/file_progress.js'),'utf8'));
  if(screen==='report')require('./ppt_tools_harness.cjs')(w);
  if(legacy){
    const compat=path.resolve(__dirname,'../app/static/chrome49');
    const manifest=JSON.parse(fs.readFileSync(path.join(compat,'manifest.json'),'utf8'));
    w.eval(fs.readFileSync(path.join(compat,manifest.runtime),'utf8'));w.fetch=mockFetch;
    w.eval(fs.readFileSync(path.join(root,`${screen}/${screen}.legacy.js`),'utf8'));
  }else w.eval(esbuild.buildSync({entryPoints:[path.join(root,`${screen}/${screen}.mjs`)],bundle:true,write:false,format:'iife',logLevel:'silent'}).outputFiles[0].text);
}

for(const legacy of [false,true])test(`${legacy?'Chrome49 fallback':'native'} recovery: explicit restore merges latest report and final save clears only own draft`,async t=>{
  const dom=new JSDOM(fs.readFileSync(path.join(root,'report.html'),'utf8'),{url:'http://localhost/report.html?project=fixture',runScripts:'outside-only',pretendToBeVisual:true});
  t.after(()=>dom.window.close());const w=dom.window,d=w.document,copy=x=>JSON.parse(JSON.stringify(x));
  const original=copy(data.document),latest=copy(original),draft=copy(original),calls=[],confirms=[];
  latest.revision=2;latest.metadata.client='다른 창의 최신 의뢰기관';draft.metadata.title='복구할 제목';
  let revision=8,storedDraft={revision,document:draft,baseline:original};
  w.confirm=message=>{confirms.push(message);return true;};
  const timer=w.setTimeout.bind(w);w.setTimeout=(fn,ms,...args)=>timer(fn,ms>=900?20:ms,...args);
  runScreen(w,'report',legacy,async(url,options={})=>{
    calls.push({url,options});let value={};
    if(url.endsWith('/draft-preview'))return {ok:true,json:async()=>({pages:[],ratio:16/9})};
    if(url.endsWith('/draft')){
      if(options.method==='PUT'){
        const body=JSON.parse(options.body);assert.equal(body.expected_revision,revision);
        storedDraft={...body,revision:++revision};
      }value=storedDraft;
    }else if(url.endsWith('/draft/clear')){
      assert.equal(JSON.parse(options.body).expected_revision,revision);value=storedDraft={revision:++revision,document:null};
    }else if(url==='/api/reports/fixture'){
      value=options.method==='PUT'?{...JSON.parse(options.body).document,revision:3}:{project:data.project,document:latest};
    }else if(url.includes('/checklist?'))value={summary:{blockers:0,warnings:0,infos:0,samples:2,reviewed:0},report:[],samples:[]};
    else if(url==='/api/presets')value=[];
    return {ok:true,json:async()=>copy(value)};
  });
  await until(()=>!d.getElementById('restoreDraft').hidden&&!d.getElementById('save').disabled);
  assert.equal(d.getElementById('title').value,latest.metadata.title);
  assert(!calls.some(c=>c.options.method==='PUT'));
  d.getElementById('restoreDraft').click();
  assert.equal(d.getElementById('title').value,'복구할 제목');
  assert.equal(d.getElementById('client').value,'다른 창의 최신 의뢰기관');
  assert.equal(confirms.length,1);assert.match(confirms[0],/기준/);
  await until(()=>revision>8);
  d.getElementById('save').click();
  await until(()=>d.getElementById('status').textContent.includes('버전 3을 저장'));
  assert.equal(storedDraft.document,null);
  const saved=JSON.parse(calls.find(c=>c.url==='/api/reports/fixture'&&c.options.method==='PUT').options.body);
  assert.equal(saved.expected_revision,2);assert.equal(saved.document.metadata.title,'복구할 제목');
  assert.equal(saved.document.metadata.client,'다른 창의 최신 의뢰기관');
  assert(!calls.some(c=>/\/exports$|\/send$/.test(c.url)));
});

for(const legacy of [false,true])test(`${legacy?'Chrome49 fallback':'native'} upload: pause, verified resume, diagnostics and explicit analysis`,async t=>{
  const dom=new JSDOM(fs.readFileSync(path.join(root,'index.html'),'utf8'),{url:'http://localhost/?project=fixture',runScripts:'outside-only',pretendToBeVisual:true});
  t.after(()=>dom.window.close());const w=dom.window,d=w.document,calls=[],raw='10 1\n20 2\n'.repeat(60000);
  let uploaded=0,pauseOnce=true,complete=false;
  w.Plotly={newPlot:async el=>{el.on=()=>{};},react:async()=>{}};
  w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
  runScreen(w,'viewer',legacy,async(url,options={})=>{
    calls.push({url,options});let value={};
    if(url==='/api/projects/fixture')value={...data.project,storage_id:'fixture'};
    else if(url==='/api/upload-sessions')value=options.method==='POST'?{upload_id:'session'}:[{upload_id:'session',updated_at:1,filenames:['A.txt']}];
    else if(url==='/api/upload-sessions/session')value={files:uploaded?[{name:'A.txt',size:raw.length,uploaded_bytes:uploaded,prefix_sha256:require('node:crypto').createHash('sha256').update(raw.slice(0,uploaded)).digest('hex')}]:[]};
    else if(url.endsWith('/chunks')){
      assert.equal(Number(options.body.get('offset')),uploaded);uploaded+=options.body.get('file').size;
      if(pauseOnce){pauseOnce=false;d.getElementById('uploadPause').click();}
    }else if(url.endsWith('/preflight'))value={ok:true,total_points:120000,errors:[],files:[{name:'A.txt',ok:true,points:120000,x_range:[10,20],y_range:[1,2],unit:'cps',axis:'2θ',preview:[{line:1,x:10,y:1}]}]};
    else if(url.endsWith('/complete')){complete=true;value={job_id:'j',status:'completed',storage_id:'fixture'};}
    return {ok:true,json:async()=>value};
  });
  await until(()=>d.getElementById('status').textContent.includes('원자료 유지'));
  const input=d.getElementById('fileInput');Object.defineProperty(input,'files',{value:[new w.File([raw],'A.txt')]});
  input.dispatchEvent(new w.Event('change',{bubbles:true}));d.getElementById('uploadSubmit').click();
  await until(()=>d.getElementById('uploadError').textContent.includes('일시중지'));
  assert.equal(uploaded,512*1024);assert(d.getElementById('uploadComplete').disabled);assert(!complete);
  d.getElementById('uploadSubmit').click();
  await until(()=>!d.getElementById('uploadComplete').disabled);
  assert.equal(uploaded,raw.length);assert.equal(calls.filter(c=>c.url.endsWith('/chunks')).length,2);
  assert.match(d.getElementById('rawDiagnostics').textContent,/120,000/);assert(!complete);
  d.getElementById('uploadComplete').click();await until(()=>complete&&!d.getElementById('uploadSubmit').disabled);
  assert.equal(calls.filter(c=>c.url.endsWith('/complete')).length,1);
});

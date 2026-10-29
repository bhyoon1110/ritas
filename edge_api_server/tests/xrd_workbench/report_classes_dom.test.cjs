// Exercise the actual editor's select/apply/save flow in both shipped code paths.
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
  'import json; from tests.xrd_workbench.test_posco_pptx import fixture; from app.xrd_workbench.core.report import with_phase_defaults; p,d=fixture(); d=with_phase_defaults(p,d); d["revision"]=1; print(json.dumps(dict(project=p,document=d)))'],
  {cwd:path.join(repo,'edge_api_server'),env:{...process.env,PYTHONDONTWRITEBYTECODE:'1',
    PYTHONPATH:[repo,path.join(repo,'common'),path.join(repo,'edge_api_server')].join(path.delimiter)},encoding:'utf8'}));
const programs={
  native:esbuild.buildSync({entryPoints:[path.join(root,'report/report.mjs')],bundle:true,write:false,format:'iife',logLevel:'silent'}).outputFiles[0].text,
  chrome49:fs.readFileSync(path.join(root,'report/report.legacy.js'),'utf8'),
};
const clone=v=>JSON.parse(JSON.stringify(v));
const flush=async()=>{for(let i=0;i<12;i++)await new Promise(setImmediate);};
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
function setup(t,program,defaultsUpdated=false,hooks={}){
  const errors=[],saves=[],calls=[];
  const console=new VirtualConsole();console.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM(fs.readFileSync(path.join(root,'report.html'),'utf8'),{
    url:'http://rist.test/report.html?project=sample',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:console});
  t.after(()=>dom.window.close());
  const w=dom.window,d=w.document;
  let saved=clone(fixture.document);
  if(hooks.initialRevision!==undefined)saved.revision=hooks.initialRevision;
  const response=value=>({ok:true,status:200,headers:{get:()=>null},json:async()=>clone(value),text:async()=>'<html>preview</html>'});
  w.structuredClone=clone;w.confirm=()=>true;
  w.XMLHttpRequest=class {
    open(method,url){assert(url.startsWith('/api/v1/report-mail/recipient?'));}
    send(){this.status=200;this.responseText=JSON.stringify({recipientName:'의뢰자',email:null});this.onload();}
    abort(){}
  };
  w.fetch=async(url,options={})=>{
    calls.push({url,options});
    if(url==='/api/platform')return response({});
    if(url==='/api/presets')return response([]);
    if(url.endsWith('/draft-preview')){if(hooks.preview)await hooks.preview();return response({});}
    if(url.endsWith('/email-draft')){if(hooks.email)await hooks.email();return response({subject:'이전 분석 기반 메일',body:'메일 초안',status:'llm'});}
    if(url.endsWith('/draft/clear'))return response({revision:2,document:null});
    if(url.endsWith('/draft')){if(options.method==='PUT'&&hooks.draft)await hooks.draft();return response({revision:0,document:null});}
    if(url.endsWith('/checklist'))return response({samples:[],summary:{blockers:0,warnings:0}});
    if(url.includes('/phase-classes?')){if(hooks.phases)await hooks.phases();return response({samples:fixture.project.samples.map(s=>({
      sample_id:s.id,phases:s.phases.map(p=>({phase:{name:p.name,card:p.card,db:p.db},quantity:null,trace:null}))}))});}
    if(url==='/api/reports/sample'){
      if(options.method==='PUT'){
        const body=JSON.parse(options.body);
        saves.push(clone(body.document));
        if(hooks.save)await hooks.save();
        saved={...body.document,revision:body.expected_revision+1};
        defaultsUpdated=false;
        return response(saved);
      }
      return response({...fixture,document:saved,phase_defaults_update_required:defaultsUpdated});
    }
    throw Error('Unexpected request: '+url);
  };
  require('../ppt_tools_harness.cjs')(w);
  w.eval(program);
  return {w,d,errors,saves,calls};
}
for(const [mode,program] of Object.entries(programs)){
  test(`${mode}: a new unsaved report offers parsed phases before the first final save`,async t=>{
    const ui=setup(t,program,false,{initialRevision:0});await flush();ui.d.querySelector('[data-tab="classes"]').click();await flush();
    const row=ui.d.querySelector('[data-pc-sample-id="A"]');assert(row);assert(!row.matches(':disabled'));assert.equal(row.value,'major');
    assert.equal(ui.calls.some(c=>c.url.includes('/phase-classes?')),false);
    row.value='trace';row.dispatchEvent(new ui.w.Event('change',{bubbles:true}));ui.d.getElementById('save').click();await flush();
    assert.equal(ui.saves.at(-1).phase_classes.labels.find(l=>l.sample_id==='A'&&l.phase.name==='Phase0').class,'trace');assert.deepEqual(ui.errors,[]);
  });
  test(`${mode}: phase choices stay connected and focused across input/change, bulk apply and late list refresh`,async t=>{
    const hooks={},ui=setup(t,program,false,hooks);await flush();ui.d.querySelector('[data-tab="classes"]').click();await flush();
    const row=ui.d.querySelector('[data-pc-sample-id="A"]');row.focus();row.value='trace';row.dispatchEvent(new ui.w.Event('input',{bubbles:true}));
    assert(row.isConnected);assert.equal(ui.d.activeElement,row);assert.equal(row.closest('.pcRow').querySelector('.pcOrigin').textContent,'직접');
    row.value='presumed';row.dispatchEvent(new ui.w.Event('change',{bubbles:true}));assert(row.isConnected);assert.equal(ui.d.activeElement,row);
    const wait=deferred();hooks.phases=()=>wait.promise;ui.d.querySelector('[data-tab="classes"]').click();
    const bulk=ui.d.querySelector('[data-pc-sample-all="A"]');bulk.value='trace';bulk.focus();
    wait.resolve();await flush();assert.equal(ui.d.activeElement,bulk);assert.equal(bulk.value,'trace');assert(row.isConnected);
    ui.d.querySelector('[data-pc-sample-apply="A"]').click();assert(row.isConnected);assert.equal(row.value,'trace');
    row.value='major';row.dispatchEvent(new ui.w.Event('change',{bubbles:true}));ui.d.getElementById('save').click();await flush();
    assert.equal(ui.saves.at(-1).phase_classes.labels.find(l=>l.sample_id==='A'&&l.phase.name==='Phase0').class,'major');
    assert(!row.matches(':disabled'));assert.deepEqual(ui.errors,[]);
  });
  test(`${mode}: background preview and draft writes leave phase selects available and later edits win`,async t=>{
    const preview=deferred(),draft=deferred(),ui=setup(t,program,false,{preview:()=>preview.promise,draft:()=>draft.promise});
    await flush();ui.d.querySelector('[data-tab="classes"]').click();await flush();
    const row=ui.d.querySelector('[data-pc-sample-id="A"]');row.value='trace';row.dispatchEvent(new ui.w.Event('change',{bubbles:true}));
    await new Promise(r=>setTimeout(r,1650));assert(ui.calls.some(c=>c.url.endsWith('/draft')&&c.options.method==='PUT'));
    assert(ui.calls.some(c=>c.url.endsWith('/draft-preview')));assert(!row.matches(':disabled'));
    row.value='presumed';row.dispatchEvent(new ui.w.Event('change',{bubbles:true}));preview.resolve();draft.resolve();await flush();
    assert(row.isConnected);assert.equal(row.value,'presumed');ui.d.getElementById('save').click();await flush();
    assert.equal(ui.saves.at(-1).phase_classes.labels.find(l=>l.sample_id==='A'&&l.phase.name==='Phase0').class,'presumed');assert.deepEqual(ui.errors,[]);
  });
  for(const fail of [false,true])test(`${mode}: phase edits survive ${fail?'failed':'slow'} mail generation and stale mail is identified`,async t=>{
    const wait=deferred(),ui=setup(t,program,false,{email:()=>wait.promise});await flush();
    ui.d.querySelector('[data-tab="email"]').click();ui.d.getElementById('generateEmail').click();await flush();
    ui.d.querySelector('[data-tab="classes"]').click();await flush();
    const row=ui.d.querySelector('[data-pc-sample-id="A"]');assert(!row.matches(':disabled'));assert(ui.d.getElementById('title').matches(':disabled'));
    assert(ui.d.getElementById('pcApply').disabled);assert.match(ui.d.getElementById('classBusyStatus').textContent,/메일 문안 생성 중에도/);
    row.value='presumed';row.dispatchEvent(new ui.w.Event('change',{bubbles:true}));
    if(fail)wait.reject(Error('모델 연결 실패'));else wait.resolve();await flush();
    assert(row.isConnected);assert.equal(row.value,'presumed');assert(!row.matches(':disabled'));assert(ui.d.getElementById('classBusyStatus').hidden);
    if(!fail)assert.match(ui.d.getElementById('emailStatus').textContent,/분석·보고서 입력이 변경/);
    ui.d.getElementById('save').click();await flush();assert.equal(ui.saves.at(-1).phase_classes.labels.find(l=>l.sample_id==='A'&&l.phase.name==='Phase0').class,'presumed');assert.deepEqual(ui.errors,[]);
  });
  test(`${mode}: final-save lock explains itself and releases even on failure without losing the selection`,async t=>{
    const wait=deferred(),ui=setup(t,program,false,{save:()=>wait.promise});await flush();ui.d.querySelector('[data-tab="classes"]').click();await flush();
    const row=ui.d.querySelector('[data-pc-sample-id="A"]');row.value='presumed';row.dispatchEvent(new ui.w.Event('change',{bubbles:true}));
    ui.d.getElementById('save').click();await flush();assert(row.matches(':disabled'));assert.match(ui.d.getElementById('classBusyStatus').textContent,/변경 내용 보호/);
    wait.reject(Error('저장 연결 실패'));await flush();assert(!row.matches(':disabled'));assert.equal(row.value,'presumed');assert(ui.d.getElementById('classBusyStatus').hidden);assert.deepEqual(ui.errors,[]);
  });
  test(`${mode}: legacy default initialization is an unsaved edit and persists on save`,async t=>{
    const ui=setup(t,program,true);await flush();
    assert.match(ui.d.getElementById('revision').textContent,/미저장 변경/);
    ui.d.getElementById('save').click();await flush();
    assert.deepEqual(ui.saves.at(-1).phase_classes,fixture.document.phase_classes);
    assert.equal(ui.d.getElementById('revision').textContent.includes('미저장'),false);
    assert.deepEqual(ui.errors,[]);
  });
  test(`${mode}: major is the bulk default; saved row classes survive until explicit apply, then save`,async t=>{
    const ui=setup(t,program);await flush();
    ui.d.querySelector('[data-tab="classes"]').click();await flush();
    const bulk=()=>ui.d.getElementById('pcRowClass');
    const row=(id,i)=>ui.d.querySelector(`[data-pc-sample-id="${id}"][data-pc-phase-index="${i}"]`);
    assert.equal(bulk().value,'major');
    assert.equal(bulk().selectedOptions[0].textContent,'주요상');
    assert.equal(ui.d.getElementById('classForm').textContent.includes('(고정)'),false);
    assert.equal(row('A',0).value,'major');
    assert.equal(row('A',1).value,'trace');
    assert.equal(row('A',2).value,'presumed');
    assert.equal(row('B',0).value,'major');
    assert.equal(row('B',0).closest('.pcRow').querySelector('.pcOrigin').textContent,'기본');
    const sampleBulk=()=>ui.d.querySelector('[data-pc-sample-all="A"]');
    assert.equal(sampleBulk().value,'major');
    ui.d.getElementById('save').click();await flush();
    assert.deepEqual(ui.saves.at(-1).phase_classes,fixture.document.phase_classes);
    ui.d.querySelector('[data-tab="classes"]').click();await flush();
    ui.d.getElementById('pcRowApply').click();await flush();
    assert.equal(row('B',0).value,'major');
    assert.equal(row('A',1).value,'trace');
    assert.equal(row('A',2).value,'presumed');
    ui.d.getElementById('save').click();await flush();
    const labels=ui.saves.at(-1).phase_classes.labels;
    assert.equal(labels.find(l=>l.sample_id==='B' && l.phase.name==='Phase0').class,'major');
    assert.equal(labels.find(l=>l.sample_id==='A' && l.phase.name==='Phase1').class,'trace');
    assert.equal(labels.find(l=>l.sample_id==='A' && l.phase.name==='Phase2').class,'presumed');
    ui.d.querySelector('[data-tab="classes"]').click();await flush();
    sampleBulk().value='trace';sampleBulk().dispatchEvent(new ui.w.Event('input',{bubbles:true}));
    assert.equal(row('A',0).value,'major'); // a bulk choice needs Apply
    ui.d.querySelector('[data-pc-sample-apply="A"]').click();await flush();
    assert.equal(row('A',0).value,'trace');
    assert.equal(row('A',2).value,'trace');
    assert.equal(row('B',0).value,'major');
    // Default major can be applied without changing the already-selected option.
    assert.equal(sampleBulk().value,'major');
    ui.d.querySelector('[data-pc-sample-apply="A"]').click();await flush();
    assert.equal(row('A',2).value,'major');
    ui.d.getElementById('save').click();await flush();
    assert.ok(ui.saves.at(-1).phase_classes.labels.filter(l=>l.sample_id==='A').every(l=>l.class==='major'));
    assert.deepEqual(ui.errors,[]);
  });
}

// Actual report event handlers, including queued/failed exports, in both bundles.
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {execFileSync}=require('node:child_process'),{JSDOM,VirtualConsole}=require('jsdom'),esbuild=require('esbuild');
const root=path.resolve(__dirname,'../../app/xrd_workbench/web');
const fixture=JSON.parse(execFileSync(process.env.RIST_TEST_PYTHON||'python3',['-c',
  'import json; from tests.xrd_workbench.test_posco_pptx import fixture; p,d=fixture(); d["revision"]=1; print(json.dumps(dict(project=p,document=d)))'],
  {cwd:path.resolve(__dirname,'../..'),encoding:'utf8'}));
const programs={native:esbuild.buildSync({entryPoints:[path.join(root,'report/report.mjs')],bundle:true,write:false,format:'iife'}).outputFiles[0].text,
  legacy:fs.readFileSync(path.join(root,'report/report.legacy.js'),'utf8')};
const response=(value,status=200)=>({ok:status===200,status,headers:{get:()=>null},json:async()=>structuredClone(value),blob:async()=>new Blob(['HTML'])});
async function until(fn){for(let i=0;i<150;i++){if(fn())return;await new Promise(r=>setTimeout(r,10));}assert.fail('timed out');}
function setup(t,mode){
  const errors=[],requests=[],downloads=[];const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM(fs.readFileSync(path.join(root,'report.html'),'utf8'),{url:'http://rist.test/report.html?project=fixture',runScripts:'outside-only',virtualConsole:vc});
  t.after(()=>dom.window.close());const w=dom.window,d=w.document;
  const timeout=w.setTimeout.bind(w);w.setTimeout=(fn,ms)=>timeout(fn,ms===1200?15:ms);
  w.structuredClone=structuredClone;w.URL.createObjectURL=()=> 'blob:html';w.URL.revokeObjectURL=()=>{};
  w.HTMLAnchorElement.prototype.click=function(){downloads.push(this.href);};
  let state='queued',postError=false,saveError=false,holdSave=null,saved=structuredClone(fixture.document);
  w.fetch=async(url,options={})=>{
    requests.push({url,options});
    if(url==='/api/platform')return response({operatorId:saved.metadata.operator_id});
    if(url==='/api/presets')return response([]);
    if(url.endsWith('/draft-preview'))return response({});
    if(url.endsWith('/draft'))return response({revision:0,document:null});
    if(url.endsWith('/exports'))return postError?response({message:'생성 요청 실패'},503):response({job_id:'export',status:'queued',kind:JSON.parse(options.body).kind});
    if(url==='/api/jobs/export')return response({job_id:'export',status:state,kind:'zip',error:'QA 생성 실패'});
    if(url==='/api/export/html')return state==='failed'?response({message:'HTML 준비 실패'},500):response({});
    if(url==='/api/reports/fixture'){
      if(options.method==='PUT'){
        if(holdSave)await holdSave;
        if(saveError)return response({message:'수정 내용 저장 실패'},500);
        saved={...JSON.parse(options.body).document,revision:saved.revision+1};return response(saved);
      }
      return response({...fixture,document:saved});
    }
    if(url.endsWith('/email-draft'))return response({message:'QA disabled'},503);
    throw Error('Unexpected '+url);
  };
  require('../ppt_tools_harness.cjs')(w);w.eval(programs[mode]);
  return {w,d,errors,requests,downloads,state:v=>state=v,postError:v=>postError=v,saveError:v=>saveError=v,holdSave:v=>holdSave=v,
    card:()=>d.querySelector('.rist-file-progress'),status:()=>d.querySelector('.rist-file-progress [role=status]')?.textContent,
    ready:()=>until(()=>!d.getElementById('save').disabled&&d.getElementById('title').value)};
}
for(const mode of Object.keys(programs)){
  for(const kind of ['pdf','pptx','zip'])test(`${mode}/${kind}: shows preparation, queued/running and browser handoff; no duplicate export`,async t=>{
    const h=setup(t,mode);await h.ready();h.d.getElementById(kind+'Export').click();
    assert.equal(h.card().dataset.state,'working');assert(!h.d.querySelector('[role=progressbar]').hasAttribute('aria-valuenow'));
    h.d.getElementById('zipExport').click();await until(()=>h.status().includes('대기'));
    assert.equal(h.requests.filter(r=>r.url.endsWith('/exports')).length,1);
    h.state('running');await until(()=>h.status().includes('서버에서'));h.state('completed');
    await until(()=>h.card().dataset.state==='done');assert.equal(h.downloads.length,1);
    assert.match(h.status(),/브라우저 다운로드 목록/);assert(!h.d.getElementById('pptxExport').disabled);assert.deepEqual(h.errors,[]);
  });
  test(`${mode}: generation/request failures unlock controls and can be retried`,async t=>{
    const h=setup(t,mode);await h.ready();h.state('failed');h.d.getElementById('zipExport').click();
    await until(()=>h.card()?.dataset.state==='error');assert.match(h.status(),/QA 생성 실패/);assert.equal(h.downloads.length,0);
    h.card().querySelector('button').click();h.postError(true);h.d.getElementById('pdfExport').click();
    await until(()=>h.card()?.dataset.state==='error');assert.match(h.status(),/생성 요청 실패/);
    h.card().querySelector('button').click();h.postError(false);h.state('completed');h.d.getElementById('pptxExport').click();
    await until(()=>h.card()?.dataset.state==='done');assert.equal(h.downloads.length,1);assert.deepEqual(h.errors,[]);
  });
  test(`${mode}: HTML gets the same visible status and failure cleanup`,async t=>{
    const h=setup(t,mode);await h.ready();h.d.getElementById('viewerExport').click();assert.equal(h.card().dataset.state,'working');
    await until(()=>h.card().dataset.state==='done');assert.equal(h.downloads.length,1);
    h.state('failed');h.d.getElementById('viewerExport').click();await until(()=>h.card().dataset.state==='error');
    assert.match(h.status(),/HTML 준비 실패/);assert(!h.d.getElementById('viewerExport').disabled);assert.equal(h.downloads.length,1);
  });
  test(`${mode}: loading covers saving edits before export and reports save failure`,async t=>{
    const h=setup(t,mode);await h.ready();let release;h.holdSave(new Promise(r=>release=r));h.saveError(true);
    const input=h.d.getElementById('title');input.value='Edited';input.dispatchEvent(new h.w.Event('input',{bubbles:true}));
    h.d.getElementById('pdfExport').click();await until(()=>h.requests.some(r=>r.options.method==='PUT'&&r.url==='/api/reports/fixture'));
    assert.equal(h.card().dataset.state,'working');assert.equal(h.requests.filter(r=>r.url.endsWith('/exports')).length,0);
    release();await until(()=>h.card().dataset.state==='error');assert.match(h.status(),/수정 내용 저장 실패/);assert.equal(h.downloads.length,0);
  });
}

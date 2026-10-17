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
  const w=dom.window,d=w.document,calls=[];
  w.HTMLElement.prototype.scrollIntoView=function(){};
  w.structuredClone=x=>JSON.parse(JSON.stringify(x));
  const timer=w.setTimeout.bind(w);w.setTimeout=(fn,ms,...args)=>timer(fn,ms>=900?20:ms,...args);
  const mockFetch=async(url,options={})=>{
    calls.push({url,options});
    if(url.endsWith('/draft-preview'))return {ok:true,text:async()=>'<p>'+JSON.parse(options.body).document.metadata.title+'</p>'};
    let value=url==='/api/reports/fixture'?data:url==='/api/platform'?{operatorId:''}:url==='/api/presets'?[]:{};
    return {ok:true,json:async()=>JSON.parse(JSON.stringify(value))};
  };
  w.fetch=mockFetch;
  if(legacy){
    const compat=path.resolve(__dirname,'../app/static/chrome49');
    const manifest=JSON.parse(fs.readFileSync(path.join(compat,'manifest.json'),'utf8'));
    w.eval(fs.readFileSync(path.join(compat,manifest.runtime),'utf8'));
    w.fetch=mockFetch;
    w.eval(fs.readFileSync(path.join(root,'report/report.legacy.js'),'utf8'));
  }else w.eval(esbuild.buildSync({entryPoints:[path.join(root,'report/report.mjs')],bundle:true,write:false,format:'iife',logLevel:'silent'}).outputFiles[0].text);
  const frame=d.getElementById('reportPreview'),title=d.getElementById('title');
  await until(()=>frame.srcdoc.includes('XRD 분석 결과'));
  assert.equal(frame.getAttribute('sandbox'),'allow-scripts allow-popups allow-popups-to-escape-sandbox');
  const edit=value=>{title.value=value;title.dispatchEvent(new w.Event('input',{bubbles:true}));};
  title.focus();edit('새 제목');edit('최종 제목');
  await until(()=>frame.srcdoc.includes('최종 제목'));
  assert.equal(d.activeElement,title);
  assert.match(d.getElementById('previewStatus').textContent,/미저장 초안/);
  assert(!calls.some(c=>c.options.method==='PUT'||/\/exports$|\/send$/.test(c.url)));
  const n=calls.filter(c=>c.url.endsWith('/draft-preview')).length;
  title.dispatchEvent(new w.CompositionEvent('compositionstart',{bubbles:true}));edit('한글 조합');
  await pause(50);assert.equal(calls.filter(c=>c.url.endsWith('/draft-preview')).length,n);
  title.dispatchEvent(new w.CompositionEvent('compositionend',{bubbles:true}));
  await until(()=>frame.srcdoc.includes('한글 조합'));
  const send=(target,source=frame.contentWindow)=>w.dispatchEvent(new w.MessageEvent('message',{source,data:{type:'xrd-preview-edit',target}}));
  send({tab:'overview',field:'client'},w);assert.equal(d.activeElement,title); // unrelated frame ignored
  send({tab:'overview',field:'client'});assert.equal(d.activeElement.id,'client');
  const sample=data.document.samples.find(s=>s.notes.length>1);
  assert(sample,'synthetic sample has multiple notes');
  send({tab:sample.sample_id,field:'note',id:sample.notes[1].id});
  assert.equal(d.activeElement.dataset.noteText,sample.notes[1].id);
  send({tab:'overview',field:'title'});assert.equal(d.activeElement,title);
  const count=calls.length;
  w.dispatchEvent(new w.PageTransitionEvent('pagehide',{persisted:true}));
  w.dispatchEvent(new w.PageTransitionEvent('pageshow',{persisted:true}));
  await until(()=>calls.length>count);
  edit('복귀 후 편집');await until(()=>frame.srcdoc.includes('복귀 후 편집'));
});

for(const legacy of [false,true])test(`${legacy?'Chrome49 fallback':'native'} upload: cards follow mapping edits and block orphan PDF`,async t=>{
  const dom=new JSDOM(fs.readFileSync(path.join(root,'index.html'),'utf8'),{url:'http://localhost/?project=fixture',runScripts:'outside-only',pretendToBeVisual:true});
  t.after(()=>dom.window.close());const w=dom.window,d=w.document,calls=[];
  const mockFetch=async url=>{calls.push(url);return {ok:false,status:404,json:async()=>({detail:'No saved project in this upload-only fixture'})};};
  w.HTMLElement.prototype.scrollIntoView=function(){};
  w.structuredClone=x=>JSON.parse(JSON.stringify(x));
  w.Plotly={newPlot:async el=>{el.on=()=>{};},react:async()=>{}};
  w.fetch=mockFetch;
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

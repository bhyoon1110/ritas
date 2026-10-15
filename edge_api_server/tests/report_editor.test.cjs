const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const acorn=require('acorn');
const {execFileSync}=require('node:child_process');
const clone=x=>JSON.parse(JSON.stringify(x));
const script=fs.readFileSync(path.join(__dirname,'../app/report_editor.js'),'utf8');
const template=fs.readFileSync(path.join(__dirname,'../app/report_editor.html'),'utf8');
function setup(t,kind='ftir',identity='') {
  const prefix='/api/v1/'+kind+'/report/jobs',posts=[],gets=[],timers=new Map(); let timerId=0;
  let doc={title:'Report',transfer:{requestNumber:'',limsExperimentCode:'',equipmentCode:'',operatorId:''},
    sections:[{sectionId:'summary',heading:'분석 결과 요약',paragraphs:['AI 초안'],bullets:[],columns:null,rows:null,source:'llm'},
      {sectionId:'experiment_conditions',heading:'실험조건 및 실험환경',paragraphs:[],bullets:[],columns:['대상','항목','값'],rows:[['공통','온도','']],source:'rule'}],auxiliary:{}};
  const dom=new JSDOM(template.replace('__CONFIG__',JSON.stringify({prefix,jobId:'original'})).replace('__SCRIPT__',''),
    {url:'http://localhost'+prefix+'/original/edit',runScripts:'outside-only'});
  t.after(()=>dom.window.close());const w=dom.window,d=w.document,q=id=>d.getElementById(id);
  let pending=null,delay=false,fail=false,revisions=0,items=[],lookupError=false,pollError=false,rateLimit=false;
  w.setTimeout=(fn,ms)=>{timers.set(++timerId,{fn,ms});return timerId;};w.clearTimeout=id=>timers.delete(id);
  w.XMLHttpRequest=class {
    open(method,url){this.method=method;this.url=url;this.headers={};}
    setRequestHeader(k,v){this.headers[k]=v;}
    send(raw){
      const body=raw&&JSON.parse(raw);if(body) posts.push({url:this.url,body,headers:this.headers});
      if(this.method==='GET') gets.push({url:this.url,headers:this.headers});
      let result;
      if(this.url.endsWith('/editor-data')) result={document:doc,job:{jobId:this.url.includes('/revision/')?'revision':'original',status:'completed',parentJobId:'original'},authenticatedOperatorId:identity};
      else if(this.url.endsWith('/revisions')) {
        revisions++;
        doc=clone(body);doc.sections.forEach(s=>{s.source='user';s.columns=s.rows?['대상','항목','값']:null;});
        result={jobId:revisions===1?'revision':'revision'+revisions,status:'queued'};
      } else if(this.url.endsWith('/send')) result={queued:true};
      else if(this.url.startsWith('/api/v1/requests?')) result={items};
      else result={jobId:this.url.split('/').pop(),status:'completed',transfer:doc.transfer,parentJobId:revisions===1?'original':'revision'};
      const finish=()=>{this.status=fail?403:lookupError&&this.url.startsWith('/api/v1/requests?')?503:pollError&&/\/revision\d*$/.test(this.url)?503:rateLimit&&this.url.endsWith('/revisions')?429:200;
        this.responseText=JSON.stringify(this.status>=400?{message:'SSO 인증이 필요합니다.'}:result);this.onload();};
      if(delay&&this.url.endsWith('/revisions')) pending=finish;else finish();
    }
  };
  w.eval(script);
  return {w,d,q,posts,gets,prefix,timers,field:label=>d.querySelector('[aria-label="'+label+'"]'),
    tick(){const entry=timers.entries().next().value;assert(entry,'expected a scheduled operation');timers.delete(entry[0]);entry[1].fn();},
    lookup(value,error=false){items=value;lookupError=error;},pollError(value){pollError=value;},rateLimit(value){rateLimit=value;},
    input(label,value){const e=this.field(label);e.value=value;e.dispatchEvent(new w.Event('input',{bubbles:true}));},
    delay(){delay=true;},finish(){delay=false;pending();},fail(){fail=true;}};
}
test('analyst editor is ES5 and renders text without HTML interpretation',t=>{
  acorn.parse(script,{ecmaVersion:5});const h=setup(t);
  assert.equal(h.field('실험조건 및 실험환경 1행 값').value,'');
  assert.equal(h.field('분석 결과 요약 · 문단 1').value,'AI 초안');
  assert.equal(h.q('preview').getAttribute('sandbox'),'allow-scripts allow-downloads');
});
for(const kind of ['ftir','raman']) test(kind+' saves blank fields and edited AI prose into new revision',t=>{
  const h=setup(t,kind);
  h.input('의뢰번호','REQ-1');h.input('실험코드','LIMS-01');h.input('실험장비','LAB1');h.input('실험자','OP1');
  h.input('실험조건 및 실험환경 1행 값','23°C');
  h.input('분석 결과 요약 · 문단 1','분석자 수정 <script>bad()</script>');
  assert.equal(h.q('download').getAttribute('aria-disabled'),'true');assert(h.q('send').disabled);
  h.delay();h.q('save').click();assert(!h.field('의뢰번호').disabled);assert(h.q('save').disabled);h.finish();
  const body=h.posts[0].body;
  assert.equal(body.transfer.requestNumber,'REQ-1');assert.equal(body.sections[1].rows[0][2],'23°C');
  assert.equal(body.sections[0].paragraphs[0],'분석자 수정 <script>bad()</script>');
  assert.equal(h.q('preview').getAttribute('src'),h.prefix+'/revision/preview#view=slides&page=0');
  assert(h.q('download').href.endsWith('/revision/download'));
  assert.equal(h.w.location.pathname,h.prefix+'/revision/edit');
  assert.equal(JSON.parse(h.w.localStorage.getItem('rist-report-revision')).previousJobId,'original');
  assert.equal(h.q('download').getAttribute('aria-disabled'),'false');
  assert.equal(h.d.querySelectorAll('script').length,2);
  h.q('send').click();assert(h.posts[1].url.endsWith('/revision/send'));
  assert.equal(h.posts[1].body.experimentCode,'LIMS-01');
  assert.equal(h.posts[1].headers['X-Requested-With'],'RIST-Report-Editor');
});
test('SSO identity requires save before sending and is locked',t=>{
  const h=setup(t,'ftir','SSO-001');assert.equal(h.field('실험자').value,'SSO-001');
  assert(h.field('실험자').readOnly);assert(h.q('send').disabled);
  assert.equal(h.q('download').getAttribute('aria-disabled'),'true');
});
test('failed save retains unsaved edits and blocks stale download/send',t=>{
  const h=setup(t);h.input('분석 결과 요약 · 문단 1','보존할 문장');h.fail();h.q('save').click();
  assert.equal(h.field('분석 결과 요약 · 문단 1').value,'보존할 문장');assert(!h.q('save').disabled);
  assert(h.q('send').disabled);assert.equal(h.q('download').getAttribute('aria-disabled'),'true');
  assert.match(h.q('status').textContent,/SSO 인증이 필요합니다/);assert.equal(h.timers.size,0);
});
for(const kind of ['ftir','raman']) test(kind+' debounces edits and autosaves without clicking; keeps focus and newer edits',t=>{
  const h=setup(t,kind);h.input('보고서 제목','첫 입력');h.input('보고서 제목','저장할 제목');
  assert.equal(h.posts.length,0);assert.equal(h.timers.size,1);
  const field=h.field('보고서 제목');field.focus();h.delay();h.tick();
  h.input('보고서 제목','생성 중 추가 수정');assert.equal(h.posts.length,1);assert.equal(h.timers.size,0);
  h.finish();assert.equal(h.field('보고서 제목'),field);assert.equal(h.d.activeElement,field);
  assert.equal(field.value,'생성 중 추가 수정');assert(h.q('send').disabled);
  assert.equal(h.q('download').getAttribute('aria-disabled'),'true');assert.equal(h.timers.size,1);
  h.tick();assert.equal(h.posts.length,2);assert(h.posts[1].url.endsWith('/revision/revisions'));
  assert.equal(h.posts[1].body.title,'생성 중 추가 수정');assert(!h.q('send').disabled);
  assert(h.q('download').href.endsWith('/revision2/download'));assert.equal(h.timers.size,0);
});
test('IME composition and reverting unsaved changes do not create revisions',t=>{
  const h=setup(t),field=h.field('보고서 제목');h.input('보고서 제목','입');
  field.dispatchEvent(new h.w.Event('compositionstart'));h.input('보고서 제목','입력');assert.equal(h.timers.size,0);
  field.dispatchEvent(new h.w.Event('compositionend'));assert.equal(h.timers.size,1);
  h.input('보고서 제목','Report');assert.equal(h.timers.size,0);assert.equal(h.posts.length,0);
  assert(!h.q('send').disabled);
});
test('poll retry resumes the same revision instead of creating duplicate work',t=>{
  const h=setup(t);h.input('보고서 제목','수정');h.pollError(true);h.tick();
  assert.equal(h.posts.length,1);assert.equal(h.timers.size,0);assert(h.q('send').disabled);
  h.pollError(false);h.q('save').click();assert.equal(h.posts.length,1);assert(!h.q('send').disabled);
});
test('global rendering busy response retries with a bound and preserves inputs',t=>{
  const h=setup(t);h.rateLimit(true);h.input('보고서 제목','유지');
  for(let i=0;i<6;i++) h.tick();
  assert.equal(h.posts.length,6);assert.equal(h.timers.size,0);assert(h.q('send').disabled);
  assert.equal(h.field('보고서 제목').value,'유지');
});
for(const kind of ['ftir','raman']) test(kind+' lookup is experiment-scoped, manual entry works, SSO stays unchanged',t=>{
  const h=setup(t,kind,'SSO-001');h.lookup([{requestNumber:'REQ-9',testMethodCode:'CODE-9',sampleName:'<b>시료</b>',operatorId:'NOT-SSO'}]);
  h.input('실험장비','CURRENT-LAB');h.q('request-load').click();
  assert(h.gets.some(g=>g.url.endsWith('experimentType='+encodeURIComponent(kind==='ftir'?'FT-IR':'RAMAN'))&&g.headers['X-Request-Id']));
  assert.equal(h.q('request-select').querySelectorAll('b').length,0);
  h.q('request-select').value='0';h.q('request-select').dispatchEvent(new h.w.Event('change'));
  assert.equal(h.field('의뢰번호').value,'REQ-9');assert.equal(h.field('실험코드').value,'CODE-9');
  assert.equal(h.field('실험장비').value,'CURRENT-LAB');assert.equal(h.field('실험자').value,'SSO-001');
  assert.equal(h.timers.size,1);h.tick();assert.equal(h.posts[0].body.transfer.requestNumber,'REQ-9');
  h.input('의뢰번호','MANUAL-1');assert.equal(h.q('request-select').value,'');assert.equal(h.field('실험코드').value,'');
});
test('request pagination, local filtering, equipment selection and lookup failures',t=>{
  const h=setup(t);h.lookup(Array.from({length:200},(_,i)=>({requestNumber:'REQ-'+i,experimentCode:'CODE',equipmentCode:'LAB'})));
  h.q('request-load').click();assert(!h.q('request-next').disabled);assert(h.q('request-previous').disabled);
  h.input('현재 의뢰 목록 검색','REQ-199');assert.equal(h.q('request-select').options.length,2);assert.equal(h.posts.length,0);
  h.q('request-select').value='199';h.q('request-select').dispatchEvent(new h.w.Event('change'));
  assert.equal(h.field('실험장비').value,'LAB');h.lookup([]);h.q('request-next').click();
  assert(h.gets.some(g=>g.url.includes('page=2&')));assert(h.q('request-next').disabled);assert(!h.q('request-previous').disabled);
  h.lookup([],true);h.q('request-load').click();assert.match(h.d.body.textContent,/조회 실패/);
  assert.equal(h.field('의뢰번호').value,'REQ-199');assert(!h.field('의뢰번호').disabled);
});
test('preview page/tab state is bounded and only accepted from the sandboxed viewer',t=>{
  const h=setup(t),state={type:'rist-report-view-state',view:'slides',page:3};
  h.w.dispatchEvent(new h.w.MessageEvent('message',{source:h.w,data:{...state,page:9}}));
  h.w.dispatchEvent(new h.w.MessageEvent('message',{source:h.q('preview').contentWindow,data:state}));
  h.w.dispatchEvent(new h.w.MessageEvent('message',{source:h.q('preview').contentWindow,data:{...state,page:999}}));
  h.w.dispatchEvent(new h.w.MessageEvent('message',{source:h.q('preview').contentWindow,data:{...state,page:NaN}}));
  h.input('보고서 제목','새 제목');h.tick();assert(h.q('preview').src.endsWith('#view=slides&page=3'));
});
test('empty transfer fields allow report review but not sending',t=>{
  const h=setup(t);h.q('send').click();assert.equal(h.posts.length,0);
  assert.match(h.q('status').textContent,/의뢰번호/);
});

const clientPrograms=JSON.parse(execFileSync(process.env.RIST_TEST_PYTHON || 'python3',['-c',
  'import json; from app import ftir_web,raman_web; print(json.dumps([ftir_web._UPLOAD_SCRIPT,raman_web._UPLOAD_SCRIPT]))'],{encoding:'utf8'}));
for(const [index,kind] of ['ftir','raman'].entries()) test(kind+' original workspace switches download/send to the confirmed revision',async()=>{
  const source=clientPrograms[index].match(/window.addEventListener\("storage", function\(event\) \{[\s\S]*?\n  \}\);/)[0];
  const fields={},select={value:'old'},calls=[];let handler;
  const job={jobId:'new',parentJobId:'old',status:'completed',transfer:{requestNumber:'UPDATED',operatorId:'SSO'}};
  const state=new Function('window','fetchJson','requestSelect','setReportTransferValue','updatePersistentReportDownload','updateReportSendAvailability','scheduleWorkspaceSave','setSuccessMessage','setMessage',
    'var lastReportJob={jobId:"old"};'+source+';return ()=>lastReportJob;')(
      {addEventListener:(name,fn)=>handler=fn},async url=>{calls.push(url);return job;},select,
      (key,value)=>fields[key]=value,j=>calls.push(j.jobId),()=>{},()=>{},()=>{},message=>{throw Error(message);});
  handler({key:'rist-report-revision',newValue:JSON.stringify({previousJobId:'unrelated',job})});
  assert.equal(calls.length,0);
  handler({key:'rist-report-revision',newValue:JSON.stringify({previousJobId:'old',job})});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(state().jobId,'new');assert.equal(fields.requestNumber,'UPDATED');assert.equal(select.value,'');
  assert.equal(calls[0],'/api/v1/'+kind+'/report/jobs/new');
});

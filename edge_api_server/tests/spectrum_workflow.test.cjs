const {test}=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom'),acorn=require('acorn');
const programs=['file_save.js','report_editor_icons.js','spectrum_workflow.js'].map(name=>fs.readFileSync(path.join(__dirname,'../app',name),'utf8'));
const source=programs.join('\n');
function setup(t,kind='ftir',mode='native') {
  const dom=new JSDOM('<header class="ftir-app-bar"><button id="ftir-clear"></button><button id="ftir-report"></button><label><input id="ftir-file-input"></label><label><input id="ftir-origin"></label></header><section class="ftir-library-band"></section><section id="ftir-drop-zone"></section><section id="ftir-report-meta"></section><div id="plot"></div>'.replace(/ftir/g,kind),{url:'http://localhost/'+kind,runScripts:'outside-only'});
  t.after(()=>dom.window.close()); const w=dom.window,d=w.document; w.confirm=()=>true;w.requestAnimationFrame=fn=>fn();
  let stored=null,fail=false,captured=0,restored=null,resets=0;
  const requests=[];
  w.XMLHttpRequest=function(){
    this.setRequestHeader=()=>{};
    this.open=(method,url)=>{this.url=url;};
    this.send=()=>{requests.push(this);queueMicrotask(()=>{
      if(this.held)return;
      this.status=200;this.responseText=JSON.stringify({jobId:this.url.split('/').slice(-2)[0],status:'completed'});this.onload();
    });};
  };
  const api={kind,gd:d.getElementById('plot'),busy:()=>false,hasGraph:()=>true,read:async()=>stored,
    write:async state=>{if(fail)throw Error('quota');stored=structuredClone(state);},
    reset:async(empty,didClear,pending)=>{await pending.catch(()=>{});if(fail)throw Error('blocked');stored=structuredClone(empty);resets++;didClear();},
    capture:async()=>({state:{plotData:[{name:'raw'+(++captured)}],files:[]},report:{image:'data:image/png;base64,AA==',analysis:{}}}),
    restore:async value=>{restored=value;},saveWorkspace:()=>{},resize:()=>{},build:async()=>({jobId:'original'})};
  if(mode==='chrome49'){
    const root=path.join(__dirname,'../app/static/chrome49'),manifest=JSON.parse(fs.readFileSync(path.join(root,'manifest.json')));
    w.eval(fs.readFileSync(path.join(root,manifest.runtime),'utf8'));
    for(const script of programs){const file=manifest.scripts[require('node:crypto').createHash('sha256').update(script.trim()).digest('hex')];assert(file,'rebuild workflow assets');w.eval(fs.readFileSync(path.join(root,file),'utf8'));}
  }else w.eval(source);
  const workflow=w.RistSpectrumWorkflow.install(api);
  return {w,d,workflow,api,requests,get stored(){return stored;},get restored(){return restored;},get resets(){return resets;},fail(value){fail=value;}};
}
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
for(const kind of ['ftir','raman'])for(const mode of ['native','chrome49'])test(kind+'/'+mode+': saved graph icons preserve actions, ordering, boundaries and busy guards',async t=>{
  const h=setup(t,kind,mode);await h.workflow.start();h.workflow.go(2);
  h.d.getElementById('sw-graph-name').value='First';await h.workflow.save(false);
  h.d.getElementById('sw-graph-name').value='Second';await h.workflow.save(true);
  const cards=()=>h.d.querySelectorAll('.sw-card'),action=(index,name)=>cards()[index].querySelector('[data-action="'+name+'"]');
  assert.deepEqual([...cards()[0].querySelectorAll('button')].map(b=>b.dataset.action),['preview','edit','download','up','down','trash']);
  for(const button of h.d.querySelectorAll('.sw-card button')){
    assert(button.querySelector('svg[aria-hidden="true"]'));assert(button.title);assert(button.getAttribute('aria-label'));assert.equal(button.textContent,'');
  }
  assert(action(0,'up').disabled);assert(action(1,'down').disabled);
  action(1,'up').click();await tick();assert.deepEqual(h.stored.graphs.map(g=>g.title),['Second','First']);
  action(0,'down').click();await tick();assert.deepEqual(h.stored.graphs.map(g=>g.title),['First','Second']);
  action(0,'preview').click();assert(h.d.querySelector('.sw-preview'));h.d.querySelector('.sw-preview button').click();
  action(0,'edit').click();await tick();assert.equal(h.restored.plotData[0].name,'raw1');
  await h.workflow.go(3);const frame=h.d.getElementById('sw-report-editor'),sent=[];frame.contentWindow.postMessage=data=>sent.push(data);
  assert.deepEqual([...cards()[0].querySelectorAll('button')].map(b=>b.dataset.action),['add','preview','edit','download','up','down','trash']);
  const before=JSON.stringify(h.stored);action(0,'add').click();assert.equal(sent[0].type,'rist-insert-saved-graph');assert.equal(sent[0].id,h.stored.graphs[0].id);assert.equal(JSON.stringify(h.stored),before);
  h.workflow.setBusy(true);assert([...h.d.querySelectorAll('.sw-card button')].every(b=>b.disabled));action(0,'trash').click();assert.equal(JSON.stringify(h.stored),before);
  h.workflow.setBusy(false);h.w.confirm=()=>false;action(0,'trash').click();assert.equal(h.stored.graphs.length,2);
  h.w.confirm=()=>true;action(0,'trash').click();await tick();assert.equal(h.stored.graphs.length,1);assert.equal(h.stored.job.jobId,'original');
  assert(action(0,'up').disabled);assert(action(0,'down').disabled);
});
for(const kind of ['ftir','raman'])test(kind+': saved graph drag uses IDs, transfers only requested PNG to the bound editor and leaves report selection unchanged',async t=>{
  const h=setup(t,kind);await h.workflow.start();h.workflow.go(2);await h.workflow.save(false);await h.workflow.go(3);
  const frame=h.d.getElementById('sw-report-editor'),sent=[];frame.contentWindow.postMessage=(data,origin)=>sent.push({data,origin});
  const graph=h.stored.graphs[0],image=h.d.querySelector('.sw-card img'),data={};
  assert(image.draggable);image.ondragstart({dataTransfer:{setData:(k,v)=>data[k]=v}});
  assert.deepEqual(JSON.parse(data['application/x-rist-saved-graph']),{id:graph.id,jobId:'original'});
  const request={type:'rist-saved-graph-request',id:graph.id,jobId:'original',requestId:'graph-request'};
  const send=(value,origin=h.w.location.origin,source=frame.contentWindow)=>h.w.dispatchEvent(new h.w.MessageEvent('message',{data:value,origin,source}));
  send(request,'https://evil.example');send(request,h.w.location.origin,h.w);send({...request,jobId:'old'});assert.equal(sent.length,0);
  send(request);assert.equal(sent.length,1);assert.equal(sent[0].data.image.type,'image/png');assert.equal(sent[0].data.image.size,1);
  assert.equal(h.stored.graphs[0].included,true);assert.equal(h.stored.stale,false);
  send({...request,id:'missing'});assert(sent[1].data.error);
});
function respond(request,status,code,message='서버 확인 실패') {
  request.held=true;request.status=status;request.responseText=JSON.stringify({status,code,message});request.onload();
}
for(const kind of ['ftir','raman'])test(kind+': report picker enlargement is read-only, bound to the editor, dismissible and returns focus',async t=>{
  const h=setup(t,kind);await h.workflow.start();h.workflow.go(2);await h.workflow.save(false);await h.workflow.go(3);
  const frame=h.d.getElementById('sw-report-editor'),graph=h.stored.graphs[0];
  frame.contentDocument.write('<!doctype html><body></body>');
  const trigger=frame.contentDocument.createElement('button');frame.contentDocument.body.appendChild(trigger);trigger.focus();
  const request={type:'rist-preview-saved-graph',id:graph.id,jobId:'original'};
  const send=(data=request,origin=h.w.location.origin,source=frame.contentWindow)=>h.w.dispatchEvent(new h.w.MessageEvent('message',{data,origin,source}));
  send(request,'https://evil.example');send(request,h.w.location.origin,h.w);send({...request,jobId:'old'});send({...request,id:'missing'});
  assert(!h.d.querySelector('.sw-preview'));
  const before=JSON.stringify(h.stored);send();send();const modal=h.d.querySelector('.sw-preview');
  assert.equal(h.d.querySelectorAll('.sw-preview').length,1);assert.equal(modal.getAttribute('aria-modal'),'true');
  assert.equal(modal.querySelector('img').src,graph.report.image);assert.equal(modal.querySelectorAll('button').length,1);
  const close=modal.querySelector('button');assert.equal(h.d.activeElement,close);
  const tab=new h.w.KeyboardEvent('keydown',{keyCode:9,bubbles:true,cancelable:true});close.dispatchEvent(tab);assert(tab.defaultPrevented);
  close.dispatchEvent(new h.w.KeyboardEvent('keydown',{keyCode:27,bubbles:true,cancelable:true}));
  assert(!h.d.querySelector('.sw-preview'));assert.equal(frame.contentDocument.activeElement,trigger);assert.equal(JSON.stringify(h.stored),before);
  send();h.d.querySelector('.sw-preview').click();assert(!h.d.querySelector('.sw-preview'));
  h.workflow.go(4);send();assert(!h.d.querySelector('.sw-preview'));
});
test('report thumbnails are downsampled once per saved image, reused and revoked on overwrite/reset',async t=>{
  const h=setup(t);let draws=0;const sizes=[],revoked=[];
  h.w.HTMLCanvasElement.prototype.getContext=function(){sizes.push([this.width,this.height]);return {drawImage(){draws++;}};};
  h.w.HTMLCanvasElement.prototype.toDataURL=()=> 'data:image/png;base64,AA==';
  h.w.URL.createObjectURL=()=> 'blob:http://localhost/thumb-'+draws;
  h.w.URL.revokeObjectURL=url=>revoked.push(url);
  await h.workflow.start();h.workflow.go(2);await h.workflow.save(false);await h.workflow.go(3);
  const sent=[],frame=h.d.getElementById('sw-report-editor');frame.contentWindow.postMessage=data=>sent.push(data);
  function load(){const image=h.d.querySelector('.sw-card img');Object.defineProperties(image,{naturalWidth:{value:1600},naturalHeight:{value:900}});image.onload();}
  load();assert.deepEqual(sizes,[[320,180]]);assert.equal(draws,1);
  const entry=sent.at(-1).graphs[0];assert.equal(entry.thumbnail,'blob:http://localhost/thumb-1');assert(!entry.image);assert(!entry.state);
  h.workflow.go(2);load();assert.equal(draws,1);assert.equal(revoked.length,0);
  h.api.capture=async()=>({state:{},report:{image:'data:image/png;base64,AQ=='}});await h.workflow.save(false);
  assert.deepEqual(revoked,['blob:http://localhost/thumb-1']);load();assert.equal(draws,2);
  await h.workflow.reset();assert.deepEqual(revoked,['blob:http://localhost/thumb-1','blob:http://localhost/thumb-2']);
});
for(const kind of ['ftir','raman'])for(const trigger of ['sw-step-3','sw-next'])test(kind+': '+trigger+' automatically generates once, reuses unchanged report and protects existing edits',async t=>{
  const h=setup(t,kind);await h.workflow.start();h.workflow.go(2);await h.workflow.save(false);await h.workflow.save(true);
  // Chosen graph order, not the current display, determines report content.
  h.d.querySelectorAll('.sw-card')[1].querySelector('[data-action="up"]').click();
  let calls=0,finish,selection;h.api.build=items=>{calls++;selection=items.map(g=>g.state.plotData[0].name);return new Promise(resolve=>finish=resolve);};
  h.w.confirm=()=>{throw Error('first report must not need a confirmation');};
  h.d.getElementById(trigger).click();h.d.getElementById('sw-step-3').click();h.d.getElementById('sw-next').click();h.d.getElementById('sw-build-report').click();
  assert.equal(calls,1);assert.deepEqual(Array.from(selection),['raw2','raw1']);assert.match(h.d.querySelector('.sw-status').textContent,/생성하는 중/);
  finish({jobId:'first'});await tick();await tick();
  assert.equal(h.stored.step,3);assert.match(h.d.getElementById('sw-report-editor').src,/first\/edit/);
  h.workflow.go(2);await h.workflow.go(3);assert.equal(calls,1);assert.equal(h.stored.job.jobId,'first');
  h.workflow.go(2);await h.workflow.save(false);h.w.confirm=()=>false;h.d.getElementById(trigger).click();
  assert.equal(calls,1);assert.equal(h.d.getElementById('spectrum-workflow').dataset.step,'2');assert.equal(h.stored.job.jobId,'first');
  h.w.confirm=()=>true;h.d.getElementById(trigger).click();assert.equal(calls,2);
  finish({jobId:'second'});await tick();await tick();assert.equal(h.stored.job.jobId,'second');assert.equal(h.stored.step,3);
});
test('automatic preview requires saved selection and returns to pending edits without replacing them',async t=>{
  const h=setup(t);let calls=0;h.api.build=async()=>{calls++;return {jobId:'first'};};await h.workflow.start();h.workflow.go(2);h.workflow.go(3);
  assert.equal(calls,0);assert.equal(h.d.getElementById('spectrum-workflow').dataset.step,'2');assert.match(h.d.querySelector('.sw-status').textContent,/그래프 저장/);
  await h.workflow.save(false);h.d.querySelector('.sw-card input').click();h.workflow.go(3);
  assert.equal(calls,0);assert.match(h.d.querySelector('.sw-status').textContent,/체크/);assert(h.d.getElementById('sw-next').disabled);
  h.d.querySelector('.sw-card input').click();await h.workflow.go(3);assert.equal(calls,1);h.workflow.go(2);
  const frame=h.d.getElementById('sw-report-editor');h.w.dispatchEvent(new h.w.MessageEvent('message',{origin:h.w.location.origin,source:frame.contentWindow,data:{type:'rist-workflow-editor-state',jobId:'first',busy:true}}));
  h.workflow.go(3);assert.equal(h.d.getElementById('spectrum-workflow').dataset.step,'3');assert.equal(calls,1);assert.match(h.d.querySelector('.sw-status').textContent,/저장되지 않은 수정/);assert(!frame.hidden);
});
test('failed automatic creation retains graph selection and does not retry in a loop',async t=>{
  const h=setup(t);await h.workflow.start();h.workflow.go(2);await h.workflow.save(false);const graphs=structuredClone(h.stored.graphs);
  let calls=0;h.api.build=async()=>{calls++;throw Error('render failed');};await h.workflow.go(3);await tick();
  assert.equal(calls,1);assert.equal(h.d.getElementById('spectrum-workflow').dataset.step,'2');assert.deepEqual(h.stored.graphs,graphs);
  assert(!h.d.getElementById('sw-next').disabled);assert.match(h.d.querySelector('.sw-status').textContent,/render failed/);
});
test('explicit transition regenerates an expired report once; failure of the new job does not loop',async t=>{
  const h=setup(t);await h.workflow.start();h.workflow.go(2);await h.workflow.save(false);await h.workflow.go(3);h.workflow.go(2);
  let calls=0;h.api.build=async()=>{calls++;return {jobId:'new-job'};};
  const transition=h.workflow.go(3);respond(h.requests.at(-1),404,'REPORT_JOB_NOT_FOUND');
  await Promise.resolve();await Promise.resolve();
  assert.equal(calls,1);assert.match(h.requests.at(-1).url,/new-job\/editor-status$/);
  respond(h.requests.at(-1),404,'REPORT_JOB_NOT_FOUND');await transition;await tick();
  assert.equal(calls,1);assert.equal(h.stored.job,null);assert.equal(h.stored.graphs.length,1);
});
for(const kind of ['ftir','raman'])test(kind+': stale saved preview never navigates to a missing job and keeps graph/source data for regeneration',async t=>{
  const h=setup(t,kind);await h.workflow.start();h.workflow.go(2);await h.workflow.save(false);
  const state=structuredClone(h.stored);state.step=4;state.job={jobId:'expired'};
  h.api.read=async()=>state;
  const starting=h.workflow.start();await Promise.resolve();
  const request=h.requests.at(-1);respond(request,404,'REPORT_JOB_NOT_FOUND');await starting;await tick();
  assert.equal(h.d.getElementById('sw-report-editor').getAttribute('src'),null);
  assert.equal(h.stored.job,null);assert.equal(h.stored.step,3);
  assert.deepEqual(h.stored.graphs,state.graphs);assert.equal(h.stored.active,state.active);
  assert.match(h.d.getElementById('sw-guide-detail').textContent,/RAW와 저장한 그래프는 유지/);
  assert(!h.d.getElementById('sw-build-report').disabled);assert(h.d.getElementById('sw-step-4').disabled);
  h.d.getElementById('sw-build-report').click();await tick();await tick();
  assert.match(h.d.getElementById('sw-report-editor').src,/original\/edit\?workflow=1$/);
  assert(!h.d.getElementById('sw-step-4').disabled);
});
test('saved job in graph editing is checked only when entering preview',async t=>{
  const h=setup(t);h.api.read=async()=>({version:1,graphs:[],step:2,job:{jobId:'expired'}});await h.workflow.start();
  assert.equal(h.requests.length,0);h.workflow.go(3);
  assert(h.d.getElementById('sw-step-4').disabled);
  respond(h.requests.at(-1),410,'REPORT_EDIT_EXPIRED');await tick();
  assert.equal(h.stored.job,null);assert.equal(h.d.getElementById('sw-report-editor').getAttribute('src'),null);
});
for(const response of [401,403,404,500,'network','timeout'])test('preview '+response+' preserves job and supports retry without rebuilding',async t=>{
  const h=setup(t);h.api.read=async()=>({version:1,graphs:[],step:3,job:{jobId:'retained'}});
  const starting=h.workflow.start();await Promise.resolve();const request=h.requests.at(-1);request.held=true;
  if(response==='network')request.onerror();else if(response==='timeout')request.ontimeout();else respond(request,response,'OTHER_ERROR');
  await starting;await tick();
  assert(h.d.getElementById('sw-report-editor').hidden);assert(h.d.getElementById('sw-step-4').disabled);
  const retry=h.d.getElementById('sw-retry-editor');assert(!retry.hidden);assert(!retry.disabled);
  h.workflow.go(4);assert.equal(h.d.getElementById('spectrum-workflow').dataset.step,'3');
  retry.click();await tick();assert.match(h.d.getElementById('sw-report-editor').src,/retained\/edit/);
  assert(!h.d.getElementById('sw-step-4').disabled);assert(retry.hidden);
});
test('missing job after successful preflight is recovered from iframe JSON without losing graph snapshots',async t=>{
  const h=setup(t);await h.workflow.start();h.workflow.go(2);await h.workflow.save(false);
  h.d.getElementById('sw-build-report').click();await tick();await tick();
  const frame=h.d.getElementById('sw-report-editor');
  frame.contentDocument.write('<html><body></body></html>');
  frame.contentDocument.body.textContent=JSON.stringify({status:404,code:'REPORT_JOB_NOT_FOUND',message:'보고서 작업을 찾을 수 없습니다.'});frame.onload();await tick();
  assert(frame.hidden);assert.equal(frame.getAttribute('src'),null);assert.equal(h.stored.job,null);assert.equal(h.stored.graphs.length,1);
});
test('invalid generation ID never opens undefined preview URL',async t=>{
  const h=setup(t);await h.workflow.start();h.workflow.go(2);await h.workflow.save(false);
  h.api.build=async()=>({});h.d.getElementById('sw-build-report').click();await tick();await tick();
  assert.equal(h.requests.length,0);assert.equal(h.d.getElementById('sw-report-editor').getAttribute('src'),null);
  assert.match(h.d.querySelector('.sw-status').textContent,/작업 번호/);
});
test('initial editor data failure reports back, but unrelated frames and old job errors cannot clear the job',async t=>{
  const h=setup(t);await h.workflow.start();h.workflow.go(2);await h.workflow.save(false);
  h.d.getElementById('sw-build-report').click();await tick();await tick();
  const frame=h.d.getElementById('sw-report-editor');
  const notify=(jobId,source)=>h.w.dispatchEvent(new h.w.MessageEvent('message',{origin:h.w.location.origin,source,
    data:{type:'rist-workflow-editor-error',jobId,status:404,code:'REPORT_JOB_NOT_FOUND'}}));
  notify('original',h.w);notify('older',frame.contentWindow);await tick();assert.equal(h.stored.job.jobId,'original');
  notify('original',frame.contentWindow);await tick();assert.equal(h.stored.job,null);assert.equal(h.stored.graphs.length,1);
});
for(const kind of ['ftir','raman'])test(kind+': next action follows save/selection/report state, with direct generation in step 2',async t=>{
  const h=setup(t,kind);await h.workflow.start();h.workflow.go(2);
  const build=h.d.getElementById('sw-build-report'),next=h.d.getElementById('sw-next'),save=h.d.getElementById('sw-save-graph');
  const detail=()=>h.d.getElementById('sw-guide-detail').textContent;
  assert(!build.hidden);assert(build.disabled);assert(save.classList.contains('sw-primary'));
  assert.match(detail(),/그래프 저장/);assert(next.hidden);
  await h.workflow.save(false);
  assert(!build.disabled);assert(next.classList.contains('sw-primary'));assert(!save.classList.contains('sw-primary'));
  assert.match(next.textContent,/생성·미리보기/);assert.match(detail(),/자동 생성/);
  assert.match(h.d.querySelector('.sw-selection-count').textContent,/포함 1개 \/ 저장 1개/);
  const check=h.d.querySelector('.sw-card input');check.checked=false;check.onchange();
  assert(build.disabled);assert.match(detail(),/체크/);assert.match(build.title,/체크/);
  h.d.querySelector('.sw-card input').click();assert(!build.disabled);
  let finish,calls=0;h.api.build=()=>{calls++;return new Promise(resolve=>finish=resolve);};
  build.click();assert(build.disabled);assert.match(detail(),/보고서를 생성하는 중/);
  build.click();assert.equal(calls,1);
  finish({jobId:'created'});await tick();await tick();
  assert.equal(h.d.getElementById('spectrum-workflow').dataset.step,'3');
  assert(next.classList.contains('sw-primary'));assert(!next.hidden);assert(!next.disabled);
  assert(!build.classList.contains('sw-primary'));assert.match(detail(),/미리보기/);
  const frame=h.d.getElementById('sw-report-editor');
  const child=busy=>h.w.dispatchEvent(new h.w.MessageEvent('message',{origin:h.w.location.origin,source:frame.contentWindow,data:{type:'rist-workflow-editor-state',busy,jobId:'created'}}));
  const focused=h.d.querySelector('.sw-card input');focused.focus();child(true);
  assert.equal(h.d.activeElement,focused,'editor status must not rebuild cards');
  assert(next.disabled);assert(build.disabled);assert(h.d.getElementById('sw-step-4').disabled);assert.match(detail(),/자동 저장/);
  h.workflow.go(4);assert.equal(h.d.getElementById('spectrum-workflow').dataset.step,'3');
  child(false);assert(!next.disabled);assert(!h.d.getElementById('sw-step-4').disabled);
  focused.click();assert(next.disabled);assert.match(next.title,/다시 생성/);
  h.d.querySelector('.sw-card input').click();assert(!build.disabled);assert(build.classList.contains('sw-primary'));
});
test('action guide explains unavailable states and failed generation remains retryable',async t=>{
  const h=setup(t);await h.workflow.start();h.workflow.go(3);
  assert(!h.d.getElementById('sw-return-edit').hidden);assert(h.d.getElementById('sw-next').hidden);
  h.d.getElementById('sw-return-edit').click();await h.workflow.save(false);
  const build=h.d.getElementById('sw-build-report'),detail=()=>h.d.getElementById('sw-guide-detail').textContent;
  h.workflow.setBusy(true);assert(build.disabled);assert.match(detail(),/RAW 분석/);
  h.workflow.setBusy(false);assert(!build.disabled);
  h.api.build=async()=>{throw Error('test build failure');};build.click();await tick();await tick();
  assert(!build.disabled);assert(h.d.getElementById('sw-next').classList.contains('sw-primary'));
  assert.match(h.d.querySelector('.sw-status').textContent,/test build failure/);
  assert(h.d.getElementById('sw-step-4').disabled);
});
for(const kind of ['ftir','raman'])test(kind+': full reset clears graph collection/report, returns to open RAW upload, and ignores stale editor messages',async t=>{
  const h=setup(t,kind);await h.workflow.start();
  const upload=h.d.getElementById('sw-upload'),clear=h.d.getElementById(kind+'-clear');
  assert(!upload.hidden);assert(upload.contains(h.d.getElementById(kind+'-file-input')));
  h.workflow.go(2);await h.workflow.save(false);await h.workflow.save(true);h.workflow.go(3);
  h.d.getElementById('sw-build-report').click();await tick();await tick();h.workflow.go(4);
  assert(!clear.classList.contains('sw-stage-hidden'));assert(!clear.disabled);
  h.w.confirm=()=>false;await h.workflow.reset();assert.equal(h.stored.graphs.length,2);assert.equal(h.resets,0);
  h.w.confirm=()=>true;await h.workflow.reset();
  assert.equal(h.resets,1);assert.equal(h.stored.graphs.length,0);assert.equal(h.stored.job,null);assert.equal(h.stored.active,null);
  assert.equal(h.stored.step,1);assert(!upload.hidden);assert.equal(h.d.querySelectorAll('.sw-card').length,0);
  const frame=h.d.getElementById('sw-report-editor');assert(frame.hidden);assert.equal(frame.getAttribute('src'),null);
  assert.equal(h.d.getElementById('sw-graph-name').value,'');
  h.w.dispatchEvent(new h.w.MessageEvent('message',{origin:h.w.location.origin,source:frame.contentWindow,data:{type:'rist-workflow-editor-state',jobId:'old',busy:true}}));
  assert(!clear.disabled);assert.equal(h.stored.job,null);
  await h.workflow.start();assert(!upload.hidden);assert.equal(h.d.querySelectorAll('.sw-card').length,0);
});
test('reset waits for earlier writes; busy, child editing and failed storage cannot partially clear the UI',async t=>{
  const h=setup(t);await h.workflow.start();h.workflow.go(2);await h.workflow.save(false);
  h.workflow.setBusy(true);await h.workflow.reset();assert.equal(h.resets,0);h.workflow.setBusy(false);
  h.workflow.go(3);h.d.getElementById('sw-build-report').click();await tick();await tick();
  const frame=h.d.getElementById('sw-report-editor');
  function child(busy){h.w.dispatchEvent(new h.w.MessageEvent('message',{origin:h.w.location.origin,source:frame.contentWindow,data:{type:'rist-workflow-editor-state',jobId:'original',busy}}));}
  child(true);await h.workflow.reset();assert.equal(h.resets,0);child(false);
  h.fail(true);await h.workflow.reset();assert.equal(h.d.querySelectorAll('.sw-card').length,1);assert.equal(h.stored.job.jobId,'original');
  assert.match(h.d.querySelector('.sw-status').textContent,/기존 작업은 유지/);h.fail(false);
  let release;const write=h.api.write;h.api.write=state=>new Promise(resolve=>{release=()=>write(state).then(resolve);});
  h.workflow.go(1);await tick();const resetting=h.workflow.reset();await tick();
  assert.equal(h.resets,0);assert(h.d.getElementById('ftir-clear').disabled);
  await release();await resetting;assert.equal(h.stored.graphs.length,0);assert.equal(h.stored.job,null);
});
for(const kind of ['ftir','raman'])test(kind+': preview uses shared action buttons and preserves close/re-edit behavior',async t=>{
  const h=setup(t,kind);await h.workflow.start();h.workflow.go(2);await h.workflow.save(false);
  const open=()=>h.d.querySelector('.sw-card .sw-controls button').click();open();
  const dialog=h.d.querySelector('.sw-preview'),actions=dialog.querySelector('.sw-controls.sw-preview-actions');
  assert.equal(dialog.parentNode,h.d.body);
  assert.deepEqual(Array.from(actions.children,b=>b.textContent),['닫기','그래프 다시 편집','파일 내보내기']);
  assert.equal(actions.children[1].className,'sw-primary');assert.equal(h.d.activeElement,actions.firstChild);
  actions.firstChild.click();assert.equal(h.d.querySelector('.sw-preview'),null);assert.equal(h.restored,null);
  assert.equal(h.d.querySelectorAll('.sw-card').length,1);
  open();h.d.querySelector('.sw-preview .sw-primary').click();await tick();await tick();
  assert.equal(h.d.querySelector('.sw-preview'),null);assert.equal(h.restored.plotData[0].name,'raw1');
  assert.equal(h.d.getElementById('spectrum-workflow').dataset.step,'2');assert.equal(h.stored.graphs.length,1);
});
test('shared workflow is ES5',()=>acorn.parse(source,{ecmaVersion:5}));
for(const kind of ['ftir','raman'])test(kind+': body portals follow every workflow stage and reset',async t=>{
  const h=setup(t,kind);await h.workflow.start();
  const stage=()=>h.d.body.getAttribute('data-rist-spectrum-step');assert.equal(stage(),'1');
  h.workflow.go(2);assert.equal(stage(),'2');await h.workflow.save(false);
  await h.workflow.go(3);assert.equal(stage(),'3');h.workflow.go(4);assert.equal(stage(),'4');
  h.workflow.go(2);assert.equal(stage(),'2');await h.workflow.reset();assert.equal(stage(),'1');
});
for(const kind of ['ftir','raman'])test(kind+': portable import appends, confirms replacement and leaves the existing collection intact',async t=>{
  const h=setup(t,kind);await h.workflow.start();h.workflow.go(2);await h.workflow.save(false);
  const first=structuredClone(h.stored.graphs[0]);
  h.w.RistSpectrumGraphFile={load:async()=>({title:'Imported',state:{plotData:[{name:'imported'}],portableImport:true},report:{image:'data:image/png;base64,AA=='}})};
  h.w.confirm=()=>false;await h.workflow.importFiles([{name:'copy.ristgraph'}]);await tick();
  assert.equal(h.stored.graphs.length,2);assert.deepEqual(h.stored.graphs[0],first);assert.equal(h.restored,null);
  h.w.confirm=()=>true;await h.workflow.importFiles([{name:'copy.ristgraph'}]);await tick();
  assert.equal(h.stored.graphs.length,3);assert.equal(h.restored.plotData[0].name,'imported');
  assert.equal(new Set(h.stored.graphs.map(g=>g.id)).size,3);assert.equal(h.stored.active,h.stored.graphs[2].id);
  assert.match(h.d.querySelector('.sw-status').textContent,/라이브러리.*다시 선택/);
});
test('portable import validates before mutation, rolls back quota failure, limits count and blocks concurrent operations',async t=>{
  const h=setup(t);await h.workflow.start();h.workflow.go(2);await h.workflow.save(false);
  const initial=structuredClone(h.stored);let loads=0,release;
  h.w.RistSpectrumGraphFile={load:()=>{loads++;return new Promise(resolve=>release=resolve);}};
  await h.workflow.importFiles([{name:'a.dpt'},{name:'a.ristgraph'}]);assert.equal(loads,0);
  const importing=h.workflow.importFiles([{name:'a.ristgraph'}]);
  await h.workflow.importFiles([{name:'a.ristgraph'}]);await h.workflow.save(true);assert.equal(loads,1);
  h.fail(true);release({title:'x',state:{},report:{}});await importing;
  assert.deepEqual(h.stored,initial);assert.equal(h.restored,null);assert.match(h.d.querySelector('.sw-status').textContent,/저장 공간/);
  h.fail(false);h.w.RistSpectrumGraphFile.load=async()=>{throw Error('wrong experiment');};
  await h.workflow.importFiles([{name:'a.ristgraph'}]);assert.deepEqual(h.stored,initial);assert.equal(h.restored,null);
  for(let i=0;i<5;i++)await h.workflow.save(true);
  assert(h.d.getElementById('sw-import-graph-library').disabled);
  await h.workflow.importFiles([{name:'a.ristgraph'}]);assert.match(h.d.querySelector('.sw-status').textContent,/6개/);
});
test('failed imported Plotly restore recovers previous editing state without losing saved entries',async t=>{
  const h=setup(t);await h.workflow.start();h.workflow.go(2);await h.workflow.save(false);
  const previous={plotData:[{name:'unsaved original'}]},calls=[];
  h.api.checkpoint=()=>previous;h.api.restore=async state=>{calls.push(state);if(state.portableImport)throw Error('render failed');};
  h.w.RistSpectrumGraphFile={load:async()=>({title:'New',state:{portableImport:true},report:{image:'data:image/png;base64,AA=='}})};
  await h.workflow.importFiles([{name:'a.ristgraph'}]);await tick();
  assert.equal(calls.length,2);assert.equal(calls[1],previous);assert.equal(h.stored.graphs.length,2);
  assert.equal(h.stored.active,h.stored.graphs[0].id);assert.match(h.d.querySelector('.sw-status').textContent,/이전 편집 화면을 복구/);
});
test('export downloads the saved snapshot, never recaptures unsaved current edits',async t=>{
  const h=setup(t);await h.workflow.start();h.workflow.go(2);await h.workflow.save(false);
  let exported,download;h.api.capture=()=>{throw Error('must not capture');};
  h.w.RistSpectrumGraphFile={write:async(g,kind)=>{exported=structuredClone(g);assert.equal(kind,'ftir');return new h.w.Blob(['file']);}};
  h.w.URL.createObjectURL=()=> 'blob:test';h.w.URL.revokeObjectURL=()=>{};
  h.w.HTMLAnchorElement.prototype.click=function(){download=this.download;};
  h.d.querySelector('.sw-card [data-action="download"]').click();await tick();await tick();
  assert.equal(exported,undefined);
  h.d.querySelector('[aria-label="저장 파일 이름"]').value='내 편집 그래프';
  Array.from(h.d.querySelectorAll('.rist-file-save button')).find(b=>b.textContent==='다운로드').click();await tick();await tick();
  assert.deepEqual(exported,h.stored.graphs[0]);assert.equal(download,'내 편집 그래프.ristgraph');
  assert.match(h.d.querySelector('.sw-status').textContent,/미저장 수정/);assert(!h.d.getElementById('sw-save-graph').disabled);
});
test('cancelling graph export performs no RAW serialization and restores controls',async t=>{
  const h=setup(t);await h.workflow.start();h.workflow.go(2);await h.workflow.save(false);
  h.w.RistSpectrumGraphFile={write:()=>{throw Error('cancel must not serialize');}};
  h.d.querySelector('.sw-card [data-action="download"]').click();
  Array.from(h.d.querySelectorAll('.rist-file-save button')).find(b=>b.textContent==='취소').click();await tick();
  assert(!h.d.getElementById('sw-save-graph').disabled);assert.equal(h.stored.graphs.length,1);
});
test('Raman PNG upload decodes bounded blocks and rejects empty or oversized images',async t=>{
  const h=setup(t),sizes=[],decode=h.w.atob.bind(h.w);
  h.w.atob=value=>{sizes.push(value.length);return decode(value);};
  const bytes=Buffer.alloc(2*1024*1024,123);
  const blob=h.w.RistSpectrumWorkflow.imageFile('data:image/png;base64,'+bytes.toString('base64'));
  assert.equal(blob.size,bytes.length);assert.equal(blob.type,'image/png');
  assert(Math.max(...sizes)<=32768);
  for(const value of ['',null,'data:image/png;base64,','data:image/jpeg;base64,AA==',
    'data:image/png;base64,'+'A'.repeat(4*Math.ceil(16*1024*1024/3)+4)]) {
    assert.throws(()=>h.w.RistSpectrumWorkflow.imageFile(value),/PNG|16MB/);
  }
});
test('saved copies, overwrite, return to upload and restore keep independent graph states',async t=>{
  const h=setup(t);await h.workflow.start();h.workflow.go(2);
  h.d.getElementById('sw-graph-name').value='One';await h.workflow.save(false);
  h.d.getElementById('sw-graph-name').value='Two';await h.workflow.save(true);
  assert.equal(h.stored.graphs.length,2);assert.notDeepEqual(h.stored.graphs[0].state,h.stored.graphs[1].state);
  h.workflow.go(1);await tick();assert.equal(h.stored.graphs.length,2);
  h.workflow.go(2);h.d.querySelector('.sw-card .sw-controls button:nth-child(2)').click();await tick();await tick();
  assert.equal(h.restored.plotData[0].name,'raw1');await h.workflow.save(false);
  assert.equal(h.stored.graphs.length,2);assert.equal(h.stored.graphs[0].state.plotData[0].name,'raw3');
  assert.equal(h.stored.graphs[1].state.plotData[0].name,'raw2');
});
test('failed IndexedDB commit rolls back the saved list and tells the user',async t=>{
  const h=setup(t);await h.workflow.start();h.workflow.go(2);await h.workflow.save(false);
  h.fail(true);await h.workflow.save(true);
  assert.equal(h.d.querySelectorAll('.sw-card').length,1);
  assert.match(h.d.querySelector('.sw-status').textContent,/저장하지 못했습니다/);
  assert.equal(h.stored.graphs.length,1);
});
test('six graph cap and empty-report delivery guard',async t=>{
  const h=setup(t);await h.workflow.start();h.workflow.go(4);
  assert.equal(h.d.getElementById('spectrum-workflow').dataset.step,'1');
  h.workflow.go(2);for(let i=0;i<7;i++)await h.workflow.save(true);
  assert.equal(h.stored.graphs.length,6);
  assert.match(h.d.querySelector('.sw-status').textContent,/6장/);
});
test('report refresh retains frame on back navigation and changed selection blocks stale delivery',async t=>{
  const h=setup(t);await h.workflow.start();h.workflow.go(2);await h.workflow.save(false);h.workflow.go(3);
  h.d.getElementById('sw-build-report').click();await tick();await tick();
  const frame=h.d.getElementById('sw-report-editor'),src=frame.src;
  h.workflow.go(2);assert.equal(frame.src,src);h.workflow.go(4);await tick();assert.equal(h.d.getElementById('spectrum-workflow').dataset.step,'4');
  h.workflow.go(3);const checkbox=h.d.querySelector('.sw-card input');checkbox.checked=false;checkbox.onchange();h.workflow.go(4);
  assert.equal(h.d.getElementById('spectrum-workflow').dataset.step,'3');
  assert.match(h.d.querySelector('.sw-status').textContent,/다시 생성/);
  assert.equal(h.d.getElementById('sw-build-report').disabled,true);
});
test('untrusted messages cannot replace the final report id',async t=>{
  const h=setup(t);await h.workflow.start();h.workflow.go(2);await h.workflow.save(false);h.workflow.go(3);
  h.d.getElementById('sw-build-report').click();await tick();await tick();
  h.w.dispatchEvent(new h.w.MessageEvent('message',{origin:'https://evil.example',source:h.d.getElementById('sw-report-editor').contentWindow,data:{type:'rist-workflow-editor-state',jobId:'evil'}}));await tick();
  assert.equal(h.stored.job.jobId,'original');
});

const {test}=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom'),acorn=require('acorn');
const source=fs.readFileSync(path.join(__dirname,'../app/spectrum_workflow.js'),'utf8');
function setup(t,kind='ftir') {
  const dom=new JSDOM('<header class="ftir-app-bar"><button id="ftir-clear"></button><button id="ftir-report"></button><label><input id="ftir-file-input"></label><label><input id="ftir-origin"></label></header><section class="ftir-library-band"></section><section id="ftir-drop-zone"></section><section id="ftir-report-meta"></section><div id="plot"></div>'.replace(/ftir/g,kind),{url:'http://localhost/'+kind,runScripts:'outside-only'});
  t.after(()=>dom.window.close()); const w=dom.window,d=w.document; w.confirm=()=>true;w.requestAnimationFrame=fn=>fn();
  let stored=null,fail=false,captured=0,restored=null;
  const api={kind,gd:d.getElementById('plot'),busy:()=>false,hasGraph:()=>true,read:async()=>stored,
    write:async state=>{if(fail)throw Error('quota');stored=structuredClone(state);},
    capture:async()=>({state:{plotData:[{name:'raw'+(++captured)}],files:[]},report:{image:'data:image/png;base64,AA==',analysis:{}}}),
    restore:async value=>{restored=value;},saveWorkspace:()=>{},resize:()=>{},build:async()=>({jobId:'original'})};
  w.eval(source); const workflow=w.RistSpectrumWorkflow.install(api);
  return {w,d,workflow,api,get stored(){return stored;},get restored(){return restored;},fail(value){fail=value;}};
}
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
for(const kind of ['ftir','raman'])test(kind+': preview uses shared action buttons and preserves close/re-edit behavior',async t=>{
  const h=setup(t,kind);await h.workflow.start();h.workflow.go(2);await h.workflow.save(false);
  const open=()=>h.d.querySelector('.sw-card .sw-controls button').click();open();
  const dialog=h.d.querySelector('.sw-preview'),actions=dialog.querySelector('.sw-controls.sw-preview-actions');
  assert.equal(dialog.parentNode,h.d.body);
  assert.deepEqual(Array.from(actions.children,b=>b.textContent),['닫기','그래프 다시 편집']);
  assert.equal(actions.lastChild.className,'sw-primary');assert.equal(h.d.activeElement,actions.firstChild);
  actions.firstChild.click();assert.equal(h.d.querySelector('.sw-preview'),null);assert.equal(h.restored,null);
  assert.equal(h.d.querySelectorAll('.sw-card').length,1);
  open();h.d.querySelector('.sw-preview .sw-primary').click();await tick();await tick();
  assert.equal(h.d.querySelector('.sw-preview'),null);assert.equal(h.restored.plotData[0].name,'raw1');
  assert.equal(h.d.getElementById('spectrum-workflow').dataset.step,'2');assert.equal(h.stored.graphs.length,1);
});
test('shared workflow is ES5',()=>acorn.parse(source,{ecmaVersion:5}));
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
  h.workflow.go(2);assert.equal(frame.src,src);h.workflow.go(4);assert.equal(h.d.getElementById('spectrum-workflow').dataset.step,'4');
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

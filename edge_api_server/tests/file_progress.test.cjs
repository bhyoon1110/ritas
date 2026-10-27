const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom'),acorn=require('acorn');
const source=fs.readFileSync(path.join(__dirname,'../../common/rist_common/plotting/file_progress.js'),'utf8');
function setup(t){
  const dom=new JSDOM('<a download="원본.txt" href="data:text/plain,RAW">저장</a>',{url:'http://rist.test',runScripts:'outside-only'});
  t.after(()=>dom.window.close());const w=dom.window;w.eval(source);return {w,d:w.document,api:w.RistFileProgress};
}
test('ES5 loading has no invented percent, measures written bytes, and stops on error',t=>{
  acorn.parse(source,{ecmaVersion:5});const {d,api}=setup(t),p=api.begin('보고서 ZIP');
  const bar=d.querySelector('[role=progressbar]');assert(!bar.hasAttribute('aria-valuenow'));
  p.update('서버 처리 대기 중…');assert.match(d.querySelector('[role=status]').textContent,/대기/);
  p.bytes(524288,1048576);assert.equal(bar.getAttribute('aria-valuenow'),'50');
  p.finish('연결이 끊어졌습니다.',true);assert(bar.hidden);assert.equal(d.querySelector('section').dataset.state,'error');
  p.bytes(1048576,1048576);assert.match(d.querySelector('[role=status]').textContent,/연결이 끊어/);
  d.querySelector('button').click();assert.equal(d.querySelector('section'),null);
});
test('unknown size remains indeterminate and duplicate installs add no listeners',t=>{
  const {w,d,api}=setup(t);w.eval(source);assert.equal(w.RistFileProgress,api);
  const p=api.begin('RAW');p.bytes(1024,0);assert(!d.querySelector('[role=progressbar]').hasAttribute('aria-valuenow'));
  p.dismiss();assert.equal(d.querySelector('section'),null);
});
test('each concurrent export owns its status; completed notices are bounded',t=>{
  const {d,api}=setup(t),first=api.begin('PDF'),second=api.begin('PNG');first.finish('PDF 완료');
  assert.equal(d.querySelectorAll('[data-state=working]').length,1);second.finish('PNG 완료');
  const third=api.begin('TXT');assert.equal(d.querySelectorAll('section').length,1);third.dismiss();
});
test('native download feedback says handoff, not disk completion; prevented links stay quiet',async t=>{
  const {w,d}=setup(t),a=d.querySelector('a');a.dispatchEvent(new w.Event('click',{bubbles:true,cancelable:true}));
  await new Promise(r=>setTimeout(r,15));assert.match(d.querySelector('[role=status]').textContent,/브라우저 다운로드 목록/);
  d.querySelector('button').click();a.addEventListener('click',e=>e.preventDefault());a.dispatchEvent(new w.Event('click',{bubbles:true,cancelable:true}));
  await new Promise(r=>setTimeout(r,15));assert.equal(d.querySelector('section'),null);
});

// Exercise the shipped operations console, including the Chrome 49 build.
const {test}=require('node:test'),assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const {createHash}=require('node:crypto');
const fs=require('node:fs'),path=require('node:path');
const {JSDOM,VirtualConsole}=require('jsdom'),acorn=require('acorn');
const html=execFileSync(process.env.RIST_TEST_PYTHON||'python3',['-c',
  "from app.error_archive import _operations_console_html; print(_operations_console_html('errors'))"],{encoding:'utf8'});
const assets=path.resolve(__dirname,'../app/static/chrome49');
const trace='Traceback (most recent call last):\n  File "보고서.py", line 7\nValueError: <script>bad()</script> & "값"\n'+'  context\n'.repeat(5000);
const flush=async()=>{await new Promise(setImmediate);await new Promise(setImmediate);};
async function setup(t,mode,body=trace){
  const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e));
  const dom=new JSDOM(html,{url:'http://rist.test/errors',runScripts:'outside-only',virtualConsole:vc});
  t.after(()=>dom.window.close());const w=dom.window,d=w.document;
  let program=d.querySelector('script').textContent.trim();
  if(mode==='chrome49'){
    const manifest=JSON.parse(fs.readFileSync(path.join(assets,'manifest.json'),'utf8'));
    const key=createHash('sha256').update(program).digest('hex');
    assert(manifest.scripts[key],'rebuild compatibility bundle');
    program=fs.readFileSync(path.join(assets,manifest.scripts[key]),'utf8');
  }
  acorn.parse(program,{ecmaVersion:mode==='chrome49'?5:2022});
  const item={eventId:'test-error',project:'TEM',code:'TEM_REPORT_BUILD_FAILED',message:'DOCX 추출 실패',traceback:body};
  w.fetch=async url=>({ok:true,status:200,json:async()=>url.includes('?')?{items:[item],total:1}:item});
  w.eval(program);await flush();d.querySelector('tr[data-id]').click();await flush();
  assert.deepEqual(errors,[]);return{w,d};
}
for(const mode of ['native','chrome49']){
  test(`${mode}: clipboard API receives exact multiline trace, without HTML interpretation`,async t=>{
    const {w,d}=await setup(t,mode);let copied;
    Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async value=>{copied=value;}}});
    assert.equal(d.querySelector('#error-trace').textContent,trace);
    assert.equal(d.querySelector('#error-trace script'),null);
    await d.querySelector('#copy-trace').onclick();
    assert.equal(copied,trace);assert.equal(d.querySelector('#trace-copy-status').textContent,'복사했습니다.');
  });
  for(const fallback of ['http','denied','throw'])test(`${mode}: ${fallback} uses legacy copy and cleans up focus/scroll`,async t=>{
    const {w,d}=await setup(t,mode);let copied;
    if(fallback!=='http')Object.defineProperty(w.navigator,'clipboard',{value:{writeText:()=>{
      if(fallback==='throw')throw new Error('blocked');return Promise.reject(new Error('denied'));
    }}});
    d.execCommand=command=>{assert.equal(command,'copy');copied=d.activeElement.value;return true;};
    const button=d.querySelector('#copy-trace'),detail=d.querySelector('#detail');
    button.focus();detail.scrollTop=123;
    await button.onclick();assert.equal(copied,trace);assert.equal(detail.scrollTop,123);
    assert.equal(d.querySelectorAll('textarea').length,1); // comment field only
    assert.equal(button.disabled,false);assert.equal(d.querySelector('#trace-copy-status').textContent,'복사했습니다.');
  });
  test(`${mode}: blocked copy selects text and offers manual copy instead of claiming success`,async t=>{
    const {w,d}=await setup(t,mode);d.execCommand=()=>false;
    await d.querySelector('#copy-trace').onclick();
    assert.equal(w.getSelection().toString(),trace);assert.match(d.querySelector('#trace-copy-status').textContent,/Ctrl\+C/);
    assert.equal(d.querySelectorAll('textarea').length,1);
  });
  test(`${mode}: records without a trace have no copy control`,async t=>{
    const {d}=await setup(t,mode,'');assert.equal(d.querySelector('#copy-trace'),null);
  });
  test(`${mode}: closing a dialog during a denied async copy does not affect the next record`,async t=>{
    const {w,d}=await setup(t,mode);let reject;
    Object.defineProperty(w.navigator,'clipboard',{value:{writeText:()=>new Promise((_,r)=>{reject=r;})}});
    const pending=d.querySelector('#copy-trace').onclick();d.querySelector('#detail-close').click();
    reject(new Error('denied'));await pending;assert.equal(d.querySelector('#detail').textContent,'');
    assert.equal(d.querySelectorAll('textarea').length,0);
  });
}

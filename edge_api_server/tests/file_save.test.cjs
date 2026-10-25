const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom'),acorn=require('acorn');
const script=fs.readFileSync(path.join(__dirname,'../app/file_save.js'),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function setup(t,secure=false){
  const dom=new JSDOM('<button id="trigger">저장</button>',{url:'http://localhost/ftir',runScripts:'outside-only'});
  t.after(()=>dom.window.close());const w=dom.window,d=w.document,downloads=[];
  Object.defineProperty(w,'isSecureContext',{value:secure});w.URL.createObjectURL=()=> 'blob:http://localhost/file';w.URL.revokeObjectURL=()=>{};
  w.HTMLAnchorElement.prototype.click=function(){downloads.push({url:this.href,name:this.download});};
  w.eval(script);d.getElementById('trigger').focus();
  return {w,d,downloads,button:name=>[...d.querySelectorAll('.rist-file-save button')].find(b=>b.textContent===name),
    input:()=>d.querySelector('[aria-label="저장 파일 이름"]'),open:options=>w.RistFileSave.open({name:'한글 보고서',extension:'.zip',mime:'application/zip',url:'/report/download',...options})};
}
test('ES5 HTTP dialog names a native URL download without buffering and explains location setting',async t=>{
  acorn.parse(script,{ecmaVersion:5});const h=setup(t);h.w.fetch=()=>{throw Error('must use browser streaming download');};
  const done=h.open();assert.match(h.d.querySelector('.rist-file-save').textContent,/저장 위치 확인/);
  h.input().value='새 이름';h.button('다운로드').click();const result=await done;
  assert.equal(result.status,'download');assert.equal(h.downloads[0].name,'새 이름.zip');
  assert.equal(new URL(h.downloads[0].url).searchParams.get('filename'),'새 이름.zip');
  assert.equal(h.d.activeElement.id,'trigger');assert(!h.d.querySelector('.rist-file-save'));
});
test('native chooser starts before RAW serialization; successful close is required',async t=>{
  const h=setup(t,true),events=[];let choose,finish;
  h.w.showSaveFilePicker=options=>{events.push('picker');assert.equal(options.suggestedName,'graph.ristgraph');return new Promise(r=>choose=r);};
  const done=h.open({url:null,name:'graph',extension:'.ristgraph',mime:'application/octet-stream',blob:()=>{events.push('blob');return Promise.resolve(new h.w.Blob(['raw']));}});
  h.button('저장 위치 선택').click();assert.deepEqual(events,['picker']);
  choose({createWritable:async()=>({write:async b=>{assert.equal(b.size,3);events.push('write');},close:()=>{events.push('close');return new Promise(r=>finish=r);},abort:async()=>events.push('abort')})});
  await tick();let completed=false;done.then(()=>completed=true);await tick();assert(!completed);assert(h.button('취소').disabled);
  finish();assert.equal((await done).status,'saved');assert.deepEqual(events,['picker','blob','write','close']);assert.equal(h.downloads.length,0);
});
test('chooser cancellation and permission denial never start a fallback download',async t=>{
  const h=setup(t,true);h.w.fetch=()=>{throw Error('cancel must not fetch');};h.w.showSaveFilePicker=()=>Promise.reject(new h.w.DOMException('cancel','AbortError'));
  let done=h.open({url:null,blob:()=>{throw Error('cancel must not prepare data');}});h.button('저장 위치 선택').click();assert.equal((await done).status,'cancelled');
  h.w.showSaveFilePicker=()=>Promise.reject(new h.w.DOMException('blocked','SecurityError'));done=h.open();h.button('저장 위치 선택').click();await tick();
  assert.equal(h.downloads.length,0);assert(h.button('다운로드'));h.button('취소').click();assert.equal((await done).status,'cancelled');
});
test('large ZIP is read/written sequentially without response.blob or accumulating buffers',async t=>{
  const h=setup(t,true),events=[];let index=0,pending=0;
  h.w.fetch=async()=>({ok:true,headers:{get:()=> 'application/zip'},body:{getReader:()=>({read:async()=>{assert.equal(pending,0);events.push('read');return index++<3?{value:new Uint8Array(1024),done:false}:{done:true};},cancel:async()=>{}})},blob:()=>{throw Error('do not buffer ZIP');}});
  h.w.showSaveFilePicker=async()=>({createWritable:async()=>({write:async()=>{pending++;events.push('write');await tick();pending--;},close:async()=>events.push('close'),abort:async()=>events.push('abort')})});
  const done=h.open();h.button('저장 위치 선택').click();assert.equal((await done).status,'saved');
  assert.deepEqual(events,['read','write','read','write','read','write','read','close']);
});
test('stream failure aborts the uncommitted file; stale revision never fetches or writes',async t=>{
  const h=setup(t,true);let aborted=0,cancelled=0,closed=0,writes=0;
  h.w.fetch=async()=>({ok:true,headers:{get:()=> 'application/zip'},body:{getReader:()=>({read:async()=>{throw Error('connection lost');},cancel:async()=>cancelled++})}});
  h.w.showSaveFilePicker=async()=>({createWritable:async()=>{writes++;return {close:async()=>closed++,abort:async()=>aborted++};}});
  let done=h.open();h.button('저장 위치 선택').click();await tick();await tick();assert.equal(aborted,1);assert.equal(cancelled,1);assert.equal(closed,0);
  assert.match(h.d.querySelector('[role="status"]').textContent,/connection lost/);h.button('취소').click();await done;
  let ready=true,choose;h.w.showSaveFilePicker=()=>new Promise(r=>choose=r);h.w.fetch=()=>{throw Error('stale must not fetch');};
  done=h.open({ready:()=>ready});h.button('저장 위치 선택').click();ready=false;choose({});await tick();assert.equal(writes,1);
  assert.match(h.d.querySelector('[role="status"]').textContent,/최신 수정본/);h.button('취소').click();await done;
});
test('invalid server response and cross-origin URL cannot be saved as a report',async t=>{
  const h=setup(t,true);let writes=0;
  h.w.fetch=async()=>({ok:true,headers:{get:()=> 'text/html'}});
  h.w.showSaveFilePicker=async()=>({createWritable:async()=>{writes++;}});
  let done=h.open();h.button('저장 위치 선택').click();await tick();assert.equal(writes,0);h.button('취소').click();await done;
  done=h.open({url:'https://other.example/report'});h.button('브라우저 다운로드').click();await tick();assert.equal(h.downloads.length,0);h.button('취소').click();await done;
});
test('names are portable and extension-safe; Escape cancels without preparing a blob',async t=>{
  const h=setup(t);assert.equal(h.w.RistFileSave.filename('CON.zip','.zip'),'_CON.zip');
  assert.equal(h.w.RistFileSave.filename('../a\\b\r\n.zip','.zip'),'_a_b__.zip');
  assert.equal(h.w.RistFileSave.filename('분석.ZIP','.zip'),'분석.zip');
  const done=h.open({url:null,blob:()=>{throw Error('cancel');}});
  h.input().dispatchEvent(new h.w.KeyboardEvent('keydown',{keyCode:27,bubbles:true}));assert.equal((await done).status,'cancelled');assert.equal(h.downloads.length,0);
});
test('same-origin editor iframe opens in the app viewport and returns focus to its download button',async t=>{
  const h=setup(t),iframe=h.d.createElement('iframe');iframe.src='/report/edit';h.d.body.appendChild(iframe);
  const child=iframe.contentWindow;child.document.open();child.document.write('<button id="download">저장</button>');child.document.close();child.eval(script);
  const button=child.document.getElementById('download');button.focus();
  const done=child.RistFileSave.open({name:'report',extension:'.zip',mime:'application/zip',url:'/report/download'});
  assert(h.d.querySelector('.rist-file-save'));assert(!child.document.querySelector('.rist-file-save'));
  h.button('취소').click();assert.equal((await done).status,'cancelled');assert.equal(child.document.activeElement,button);
});

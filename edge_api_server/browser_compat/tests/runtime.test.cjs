const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {JSDOM} = require('jsdom');
const acorn = require('acorn');
const root = path.resolve(__dirname,'../../app/static/chrome49');
const manifest = JSON.parse(fs.readFileSync(path.join(root,'manifest.json'),'utf8'));
const runtime = fs.readFileSync(path.join(root,manifest.runtime),'utf8');

function setup(t, url='http://localhost/') {
  const dom = new JSDOM('<!doctype html><html><body><form><input name="name" value="sample"><input name="name" value="two"></form><button>drag</button></body></html>', {url,runScripts:'outside-only',pretendToBeVisual:true});
  t.after(()=>dom.window.close());
  const w = dom.window;
  delete w.fetch; delete w.AbortController; delete w.AbortSignal; delete w.PointerEvent;
  delete w.URLSearchParams; delete w.Promise.prototype.finally;
  delete w.Object.entries; delete w.String.prototype.padStart;
  delete w.NodeList.prototype.forEach; delete w.Element.prototype.replaceChildren;
  delete w.FormData.prototype.get; delete w.FormData.prototype.getAll;
  delete w.FormData.prototype.entries;
  delete w.FormData.prototype.keys; delete w.FormData.prototype.values;
  w.eval(runtime);
  return w;
}

test('all emitted programs parse as ES5, including runtime and vendored libraries',()=>{
  for(const file of new Set([manifest.runtime,manifest.plotly,...Object.values(manifest.scripts)].filter(Boolean))) {
    acorn.parse(fs.readFileSync(path.join(root,file),'utf8'),{ecmaVersion:5,allowReserved:true});
  }
});
test('missing language, file and DOM APIs are restored',async t=>{
  const w = setup(t);
  assert.equal(w.RIST_BROWSER_PROFILE,'chrome49');
  assert.equal(new w.URLSearchParams('?project=FTIR').get('project'),'FTIR');
  assert.equal(w.String.prototype.padStart.call('3',2,'0'),'03');
  assert.equal(typeof w.Object.entries,'function');
  let finalized = false;
  await w.Promise.resolve(1).finally(()=>{finalized=true;});
  assert.equal(finalized,true);
  const blob = new w.Blob(['hello']);
  assert.equal(await blob.text(),'hello');
  assert.equal((await blob.arrayBuffer()).byteLength,5);
  const p = w.document.createElement('p');
  p.replaceChildren('safe <img>',w.document.createElement('b'));
  assert.equal(p.querySelector('img'),null);
  assert.equal(p.textContent,'safe <img>');
  let count=0; w.document.querySelectorAll('input').forEach(()=>count++);
  assert.equal(count,2);
});
test('FormData keeps duplicate fields, native multipart bodies and binary data', async t=>{
  const w = setup(t);
  const form = new w.FormData(w.document.querySelector('form'));
  assert.equal(form.get('name'),'sample');
  assert.deepEqual(Array.from(form.getAll('name')),['sample','two']);
  form.append('file',new w.Blob(['file-content']),'sample.dpt');
  const native = form._asNative();
  assert.ok(native);
  const multipart = await form._blob().text();
  assert.match(multipart,/filename="sample.dpt"/);
  assert.match(multipart,/file-content/);
});
test('mouse drag generates pointer events and capture APIs',t=>{
  const w = setup(t), button=w.document.querySelector('button'), seen=[];
  button.addEventListener('pointerdown',e=>{seen.push(e.pointerType);button.setPointerCapture(e.pointerId);});
  w.document.addEventListener('pointerup',e=>seen.push('up'));
  button.dispatchEvent(new w.MouseEvent('mousedown',{bubbles:true,button:0,buttons:1,clientX:12,clientY:20}));
  button.dispatchEvent(new w.MouseEvent('mouseup',{bubbles:true,button:0,buttons:0,clientX:12,clientY:20}));
  assert.deepEqual(seen,['mouse','up']);
  assert.equal(typeof button.hasPointerCapture,'function');
});
test('cancelled pointer drag cannot also start native Plotly mouse zoom',t=>{
  const w=setup(t), button=w.document.querySelector('button');
  const seen=[];
  button.addEventListener('mousedown',()=>seen.push('native-mouse'));
  button.dispatchEvent(new w.MouseEvent('mousedown',{bubbles:true,cancelable:true,buttons:1}));
  button.dispatchEvent(new w.MouseEvent('mouseup',{bubbles:true,cancelable:true,buttons:0}));
  assert.deepEqual(seen,['native-mouse']);
  button.addEventListener('pointerdown',e=>{seen.push('pointer');e.preventDefault();button.setPointerCapture(e.pointerId);});
  button.addEventListener('pointermove',()=>seen.push('move'));
  button.addEventListener('pointercancel',()=>seen.push('cancel'));
  button.dispatchEvent(new w.MouseEvent('mousedown',{bubbles:true,cancelable:true,buttons:1}));
  button.dispatchEvent(new w.MouseEvent('mousemove',{bubbles:true,cancelable:true,buttons:1}));
  assert.deepEqual(seen,['native-mouse','pointer','move']);
  w.dispatchEvent(new w.Event('blur'));
  assert.equal(seen[seen.length-1],'cancel');
});
test('Chrome 49 boolean event options preserve capture, once and explicit removal',t=>{
  const dom=new JSDOM('<button></button>',{runScripts:'outside-only'});t.after(()=>dom.window.close());
  const w=dom.window,proto=w.EventTarget.prototype,add=proto.addEventListener,remove=proto.removeEventListener;
  proto.addEventListener=function(type,fn,options){return add.call(this,type,fn,!!options);};
  proto.removeEventListener=function(type,fn,options){return remove.call(this,type,fn,!!options);};
  w.eval(runtime);
  const button=w.document.querySelector('button');let count=0;const fn=()=>count++;
  button.addEventListener('click',fn,{capture:false,once:true});
  button.click();button.click();assert.equal(count,1);
  button.addEventListener('click',fn,{capture:false,once:true});
  button.removeEventListener('click',fn,false);button.click();assert.equal(count,1);
  button.addEventListener('click',fn,{capture:false,passive:false});
  button.removeEventListener('click',fn,false);button.click();assert.equal(count,1);
  button.addEventListener('click',fn,false);
  button.addEventListener('click',fn,{capture:false,once:true});
  button.click();button.click();assert.equal(count,3);
  button.removeEventListener('click',fn,{capture:false});
  button.click();assert.equal(count,3);
});
test('XHR-backed fetch preserves login cookie, CSRF headers, multipart and cancellation',async t=>{
  const requests=[];
  const server=http.createServer((req,res)=>{
    let body=''; req.on('data',x=>body+=x.toString());
    req.on('end',()=>{
      requests.push({url:req.url,headers:req.headers,body});
      if(req.url==='/slow') return;
      res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({ok:true}));
    });
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>{server.closeAllConnections();server.close();});
  const w=setup(t,`http://127.0.0.1:${server.address().port}/`);
  w.document.cookie='rist_session=test-cookie';
  const form=new w.FormData();form.append('file',new w.Blob(['abc']),'sample.dpt');
  const response=await w.fetch('/upload',{method:'POST',body:form,headers:{'X-Requested-With':'RIST-Test'}});
  assert.equal((await response.json()).ok,true);
  assert.match(requests[0].headers.cookie,/rist_session=test-cookie/);
  assert.equal(requests[0].headers['x-requested-with'],'RIST-Test');
  assert.match(requests[0].headers['content-type'],/multipart\/form-data; boundary=/);
  assert.match(requests[0].body,/sample.dpt/);
  const abort=new w.AbortController();
  const pending=w.fetch('/slow',{signal:abort.signal});abort.abort();
  await assert.rejects(pending,err=>err.name==='AbortError');
});

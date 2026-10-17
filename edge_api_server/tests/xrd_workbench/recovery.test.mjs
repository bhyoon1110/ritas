import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash,randomBytes} from 'node:crypto';
import {FileSHA256,hashBlobPrefix} from '../../app/xrd_workbench/web/shared/file_hash.mjs';
import {transferUpload} from '../../app/xrd_workbench/web/shared/resumable_upload.mjs';
import {createDraftAutosave} from '../../app/xrd_workbench/web/report/draft_autosave.mjs';
const flush=()=>new Promise(r=>setImmediate(r));
globalThis.FileReader=class {readAsArrayBuffer(blob){blob.arrayBuffer().then(b=>{this.result=b;this.onload();},()=>this.onerror());}};
test('streaming SHA256 matches known and random vectors across arbitrary block splits',async()=>{
  for(const length of [0,1,55,56,63,64,65,511,262151]){
    const data=randomBytes(length),h=new FileSHA256();
    for(let i=0;i<length;i+=29)h.update(data.subarray(i,i+29));
    const expected=createHash('sha256').update(data).digest('hex');
    assert.equal(h.hex(),expected);assert.equal(await hashBlobPrefix(new Blob([data]),length),expected);
  }
  assert.equal(new FileSHA256().update(new TextEncoder().encode('abc')).hex(),'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
});
test('resume verifies all stored prefix bytes and transfers only missing chunks before diagnostics',async()=>{
  const raw='10 1\n20 2\n30 3\n',file=new File([raw],'A.txt'),cut=5,calls=[];
  const request=async(url,options={})=>{
    calls.push({url,options});let value={};
    if(url.endsWith('/s'))value={files:[{name:'A.txt',size:file.size,uploaded_bytes:cut,prefix_sha256:createHash('sha256').update(raw.slice(0,cut)).digest('hex')}]};
    if(url.endsWith('/preflight'))value={ok:true,files:[]};
    return {json:async()=>value};
  };
  const result=await transferUpload([file],{}, {request,chunkSize:4,uploadId:'s'});
  const chunks=calls.filter(c=>c.url.endsWith('/chunks'));
  assert.equal(Number(chunks[0].options.body.get('offset')),cut);
  assert.equal((await Promise.all(chunks.map(c=>c.options.body.get('file').text()))).join(''),raw.slice(cut));
  assert(result.diagnostics.ok);assert(!calls.some(c=>c.url.endsWith('/complete')));
  const bad=async()=>({json:async()=>({files:[{name:'A.txt',size:file.size,uploaded_bytes:cut,prefix_sha256:'wrong'}]})});
  await assert.rejects(transferUpload([file],{}, {request:bad,chunkSize:4,uploadId:'s'}),/내용과 다릅니다/);
  await assert.rejects(transferUpload([file],{}, {request,chunkSize:4,uploadId:'s',canceled:()=>true}),/일시중지/);
});
test('draft autosave serializes revisions, retains edits during in-flight work and respects conflicts',async()=>{
  let current='a',resolve;const writes=[],status=[],timers=new Map();let id=0;
  const clock={setTimeout(fn){timers.set(++id,fn);return id;},clearTimeout(i){timers.delete(i);}};
  const run=()=>{const jobs=[...timers.values()];timers.clear();jobs.forEach(fn=>fn());};
  const saver=createDraftAutosave({clock,snapshot:()=>current,write:(v,r)=>{writes.push([v,r]);return new Promise(done=>{resolve=done;});},clear:async r=>({revision:r+1}),status:s=>status.push(s)});
  saver.initialize(0);saver.schedule();run();await flush();assert.deepEqual(writes,[['a',0]]);
  current='b';saver.schedule();run();assert.equal(writes.length,1);resolve({revision:1});await flush();run();await flush();
  assert.deepEqual(writes,[['a',0],['b',1]]);resolve({revision:2});await flush();await saver.clear();
  current='c';saver.schedule();run();await flush();assert.deepEqual(writes[2],['c',3]);resolve({revision:4});await flush();saver.dispose();
  const failing=createDraftAutosave({clock,snapshot:()=>current,write:async()=>{throw Object.assign(Error('conflict'),{status:409});},clear:async()=>{},status:s=>status.push(s)});
  failing.initialize(1);failing.schedule();run();await flush();assert.match(status.at(-1),/conflict/);failing.schedule();assert.equal(timers.size,0);
});

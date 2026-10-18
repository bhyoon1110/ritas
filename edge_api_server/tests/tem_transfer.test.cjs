// Deterministic policy/timing/worker tests, not a Windows real-device claim.
const {test}=require('node:test'), assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {JSDOM}=require('jsdom'),acorn=require('acorn');
const source=fs.readFileSync(path.join(__dirname,'../app/tem_transfer.js'),'utf8');
const workerSource=fs.readFileSync(path.join(__dirname,'../app/tem_upload_worker.js'),'utf8');
const MiB=1024*1024;
function setup(t,hints={deviceMemory:8,hardwareConcurrency:8}) {
  const dom=new JSDOM('',{runScripts:'outside-only',pretendToBeVisual:true});t.after(()=>dom.window.close());
  const w=dom.window;w.eval(source+';window.moduleUnderTest=TemTransfer;');
  let time=0;const options={navigator:{userAgent:'Windows NT 6.1; Supermium/144',...hints},document:w.document,now:()=>time};
  const c=w.moduleUnderTest.create(options);t.after(()=>c.dispose('test'));
  return {w,c,options,setTime:value=>time=value,policy:w.moduleUnderTest.policy};
}
test('hardware budgets are conservative for mobile, unknown hardware and data saver',t=>{
  const {policy}=setup(t);
  const cases=[
    [{},2,2,false], [{deviceMemory:8},2,2,false],
    [{deviceMemory:2,hardwareConcurrency:8},2,2,false],
    [{deviceMemory:4,hardwareConcurrency:4},2,4,true],
    [{deviceMemory:8,hardwareConcurrency:8},4,8,true],
    [{deviceMemory:8,hardwareConcurrency:8,userAgent:'Android Mobile'},2,2,false],
    [{deviceMemory:8,hardwareConcurrency:8,userAgent:'Macintosh',maxTouchPoints:5},2,2,false],
    [{deviceMemory:8,hardwareConcurrency:8,connection:{saveData:true}},1,1,false],
    [{deviceMemory:8,hardwareConcurrency:8,connection:{effectiveType:'slow-2g'}},1,1,false],
    [{deviceMemory:1,hardwareConcurrency:4},1,1,false]
  ];
  for(const [h,start,max,prefetch] of cases){const p=policy(h);assert.equal(p.initial,start*MiB);assert.equal(p.max,max*MiB);assert.equal(p.prefetch,prefetch);}
});
function ack(c,bytes,ms,extra={}) {c.ack(bytes,{elapsedMs:ms,responseWaitMs:20,serverMs:5,epoch:c.epoch(),retried:false,...extra});}
test('grows after two fast acknowledgements, shrinks on slow response or retry with cooldown',t=>{
  const {c}=setup(t);c.begin(100*MiB,0,8*MiB);
  ack(c,4*MiB,500);assert.equal(c.chunkBytes(),4*MiB);
  ack(c,4*MiB,500);assert.equal(c.chunkBytes(),8*MiB);
  ack(c,8*MiB,9000);assert.equal(c.chunkBytes(),4*MiB);
  c.retry();assert.equal(c.chunkBytes(),2*MiB);
  for(let i=0;i<3;i++) ack(c,2*MiB,500);
  assert.equal(c.chunkBytes(),2*MiB);
  ack(c,2*MiB,500);ack(c,2*MiB,500);assert.equal(c.chunkBytes(),4*MiB);
});
test('honors server ceiling and ignores tiny tails, retried/suspended samples',t=>{
  const {c}=setup(t);c.begin(100*MiB,0,3*MiB);
  for(let i=0;i<6;i++)ack(c,3*MiB,500);
  assert.equal(c.chunkBytes(),3*MiB);
  c.retry();const size=c.chunkBytes();
  for(let i=0;i<6;i++)ack(c,10,500);
  for(let i=0;i<6;i++)ack(c,size,500,{retried:true});
  for(let i=0;i<6;i++)ack(c,size,500,{epoch:-1});
  assert.equal(c.chunkBytes(),size);
});
test('rate/ETA exclude resumed bytes and visibility/offline suspension; retries never count twice',t=>{
  const {c,w,options,setTime}=setup(t);c.begin(20*MiB,10*MiB,8*MiB);
  setTime(1000);ack(c,MiB,1000);assert.equal(c.snapshot().bytesPerSecond,MiB);
  assert.equal(c.snapshot().remainingSeconds,9);
  Object.defineProperty(w.document,'visibilityState',{value:'hidden',configurable:true});
  w.document.dispatchEvent(new w.Event('visibilitychange'));
  setTime(61000);assert.equal(c.snapshot().activeMs,1000);
  Object.defineProperty(w.document,'visibilityState',{value:'visible',configurable:true});
  w.document.dispatchEvent(new w.Event('visibilitychange'));
  options.navigator.onLine=false;w.dispatchEvent(new w.Event('offline'));
  setTime(121000);options.navigator.onLine=true;w.dispatchEvent(new w.Event('online'));
  c.retry();c.observe({elapsedMs:500,responseWaitMs:null});
  setTime(122000);ack(c,MiB,500,{retried:true});
  const s=c.snapshot();assert.equal(s.committed,2*MiB);assert.equal(s.bytesPerSecond,MiB);
  assert.equal(s.requestMs,2000);assert.equal(s.responseWaitMs,40);assert.equal(s.serverMs,10);assert.equal(s.retries,1);
});
test('bytes completed across suspension cannot inflate the active transfer rate',t=>{
  const {c,setTime}=setup(t);c.begin(20*MiB,0,8*MiB);
  setTime(1000);ack(c,8*MiB,60000,{epoch:-1});
  assert.equal(c.snapshot().committed,8*MiB);assert.equal(c.snapshot().bytesPerSecond,0);
  assert.equal(c.snapshot().remainingSeconds,null);
});
test('worker error falls back once and is not repeatedly spawned',async t=>{
  const {w,c}=setup(t);let created=0,terminated=0,fallback=0;
  w.Worker=class{constructor(){created++;}postMessage(){setTimeout(()=>this.onerror({preventDefault(){}}),0);}terminate(){terminated++;}};
  const blob=new w.Blob(['123456789']);
  const calculate=async()=>{fallback++;return 'cbf43926';};
  assert.equal(await c.checksum(blob,calculate),'cbf43926');
  assert.equal(await c.checksum(blob,calculate),'cbf43926');
  assert.equal(created,1);assert.equal(terminated,1);assert.equal(fallback,2);assert.equal(c.canPrefetch(),false);
});
test('successful worker is reused and terminated, with at most one checksum in flight',async t=>{
  const {w,c}=setup(t);let created=0,terminated=0,active=0,peak=0;
  w.Worker=class{constructor(){created++;}postMessage(task){peak=Math.max(peak,++active);setTimeout(()=>{active--;this.onmessage({data:{id:task.id,crc32:'cbf43926'}});},0);}terminate(){terminated++;}};
  const calculate=()=>assert.fail('fallback should not run');
  await c.checksum(new w.Blob(['123456789']),calculate);assert(c.canPrefetch());
  await c.checksum(new w.Blob(['123456789']),calculate);
  c.dispose('done');assert.equal(created,1);assert.equal(terminated,1);assert.equal(peak,1);
  await assert.rejects(c.checksum(new w.Blob(['x']),calculate),/stopped/);
});
test('pending worker cancellation rejects without falling back or retaining task',async t=>{
  const {w,c}=setup(t);let terminated=0;
  w.Worker=class{postMessage(){}terminate(){terminated++;}};
  const promise=c.checksum(new w.Blob(['x']),()=>assert.fail('must not calculate after dispose'));
  c.dispose('stopped');await assert.rejects(promise,/stopped/);assert.equal(terminated,1);
});
test('worker startup timeout releases resources and falls back',async t=>{
  const {w,c}=setup(t);let terminated=0;
  const schedule=w.setTimeout.bind(w);
  w.setTimeout=(callback,delay)=>schedule(callback,delay===30000?1:delay);
  w.Worker=class{postMessage(){}terminate(){terminated++;}};
  assert.equal(await c.checksum(new w.Blob(['x']),async()=> '12345678'),'12345678');
  assert.equal(terminated,1);assert.equal(c.canPrefetch(),false);
});
test('slow actual preparation lowers oversized hardware hints',async t=>{
  const {w,c,setTime}=setup(t);let time=0;
  c.begin(30*MiB,0,8*MiB);
  const fallback=async()=>{time+=9000;setTime(time);return '00000000';};
  await c.checksum(new w.Blob([new Uint8Array(4*MiB)]),fallback);
  await c.checksum(new w.Blob([new Uint8Array(4*MiB)]),fallback);
  assert.equal(c.chunkBytes(),2*MiB);assert.equal(c.snapshot().maxBytes,2*MiB);
});
test('actual ES5 worker CRC implementation uses <=256KiB reads and a known vector',()=>{
  acorn.parse(workerSource,{ecmaVersion:5});
  const replies=[],reads=[];
  const context={Uint32Array,Uint8Array,self:{postMessage:value=>replies.push(value)},FileReaderSync:class{readAsArrayBuffer(blob){reads.push(blob.buffer.length);const b=blob.buffer;return b.buffer.slice(b.byteOffset,b.byteOffset+b.length);}}};
  const blob=b=>({size:b.length,buffer:b,slice:(start,end)=>blob(b.subarray(start,end))});
  vm.runInNewContext(workerSource,context);
  context.self.onmessage({data:{id:1,blob:blob(Buffer.from('123456789'))}});
  context.self.onmessage({data:{id:2,blob:blob(Buffer.alloc(4*MiB))}});
  assert.equal(replies[0].crc32,'cbf43926');assert.equal(replies[1].crc32,'1147406a');
  assert.equal(reads.length,17);assert(Math.max(...reads)<=256*1024);
});

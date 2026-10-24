// Exercise shipped FTIR upload helpers; not a real XP/Supermium browser test.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const acorn = require('acorn');
const source = execFileSync(process.env.RIST_TEST_PYTHON || 'python3', ['-c',
  'from app.ftir_web import _UPLOAD_SCRIPT; print(_UPLOAD_SCRIPT)'], {encoding:'utf8'});
function extract(name) {
  const start=source.search(new RegExp('  (?:async )?function '+name+'\\('));
  assert(start>=0,name);
  return source.slice(start,source.indexOf('\n  }',start)+4);
}
function context(extra={}) {
  const ctx=vm.createContext({Blob,FormData,atob,...extra});
  vm.runInContext(['reportImageFile','requestErrorMessage','fetchJson','completeReportUploadWithRetry']
    .map(extract).join('\n'),ctx);
  return ctx;
}

test('collection RAW export includes every graph source and deduplicates by bytes, never filename/date alone',async()=>{
  const sizes=[];
  class Reader {
    readAsArrayBuffer(blob) { sizes.push(blob.size);blob.arrayBuffer().then(value=>{this.result=value;this.onload();},()=>this.onerror()); }
  }
  const ctx=vm.createContext({File,Blob,FileReader:Reader,Uint8Array,MAX_TOTAL_BYTES:50*1024*1024});
  vm.runInContext(extract('recordFile')+extract('allRawReportFiles'),ctx);
  const record=(name,text)=>({name,lastModified:1,blob:new Blob([text])});
  const a=record('same.dpt','1234'),same=record('same.dpt','1234'),different=record('same.dpt','5678');
  const large='x'.repeat(600000);
  const result=await ctx.allRawReportFiles([
    {state:{files:[a,record('hidden.dpt','abcd'),record('large.dpt',large)]}},
    {state:{files:[same,different,record('other.dpt','1234'),record('large.dpt',large)]}}
  ]);
  assert.deepEqual(Array.from(result,f=>f.name),['same.dpt','hidden.dpt','large.dpt','same.dpt','other.dpt']);
  assert.equal(await result[0].text(),'1234');assert.equal(await result[3].text(),'5678');
  assert(Math.max(...sizes)<=256*1024);
  ctx.MAX_TOTAL_BYTES=1;
  await assert.rejects(ctx.allRawReportFiles([{state:{files:[a]}}]),/50MB/);
  ctx.MAX_TOTAL_BYTES=50*1024*1024;
  await assert.rejects(ctx.allRawReportFiles([{state:{files:Array.from({length:61},(_,i)=>record(i+'.dpt','1'))}}]),/60개/);
});

test('full RAW collection fails visibly when a source cannot be read',async()=>{
  class BrokenReader { readAsArrayBuffer(){this.onerror();} }
  const ctx=vm.createContext({File,Blob,FileReader:BrokenReader,Uint8Array,MAX_TOTAL_BYTES:50*1024*1024});
  vm.runInContext(extract('recordFile')+extract('allRawReportFiles'),ctx);
  const entry=()=>({state:{files:[{name:'x.dpt',blob:new Blob(['1'])}]}});
  await assert.rejects(ctx.allRawReportFiles([entry(),entry()]),/RAW 파일을 읽지 못했습니다/);
});

test('large captured PNG is a binary multipart file, with bounded base64 decode blocks',async()=>{
  let largestBlock=0, calls=0, posted;
  const bytes=Buffer.alloc(1200001,123); // Deliberately above Starlette text field limit.
  const ctx=context({atob:s=>{largestBlock=Math.max(largestBlock,s.length);return atob(s);},
    wait:async()=>{},setReportProgress:()=>{},fetch:async(url,options)=>{
      posted=options.body;
      if (++calls===1) throw new Error('response lost');
      return {ok:true,json:async()=>({jobId:'same-job'})};
    }});
  acorn.parse(extract('reportImageFile')+extract('requestErrorMessage'),{ecmaVersion:5});
  const file=ctx.reportImageFile('data:image/png;base64,'+bytes.toString('base64'));
  assert.equal(file.type,'image/png');assert.equal(file.size,bytes.length);
  assert.deepEqual(Buffer.from(await file.arrayBuffer()),bytes);
  assert(largestBlock<=32768);
  assert.equal((await ctx.completeReportUploadWithRetry('session',{
    analysis_json:'{}',figure_image_file:file,viewer_figure:new Blob(['{}'])})).jobId,'same-job');
  assert.equal(calls,2);
  assert.equal(posted.has('figure_image'),false);
  assert.equal(posted.get('figure_image_file').name,'current_graph.png');
  const request=new Request('http://localhost',{method:'POST',body:posted});
  const roundtrip=await request.formData();
  assert.deepEqual(Buffer.from(await roundtrip.get('figure_image_file').arrayBuffer()),bytes);
});

test('client rejects empty, unsupported and oversized image data',()=>{
  const ctx=context();
  for (const value of ['',null,'data:image/jpeg;base64,YQ==','data:image/png;base64,']) {
    assert.throws(()=>ctx.reportImageFile(value));
  }
  assert.throws(()=>ctx.reportImageFile('data:image/png;base64,'+'A'.repeat(24*1024*1024)),/16MB/);
});

test('HTTP detail/status is visible without exposing validation inputs; retry rules are retained',async()=>{
  for (const [status,payload,pattern,transient] of [
    [400,{detail:'Part exceeded maximum size of 1024KB.'},/Part exceeded/,false],
    [400,{message:'PNG 파일 오류'},/PNG 파일 오류/,false],
    [422,{detail:[{input:'private-value',msg:'invalid'}]},/HTTP 422/,false],
    [413,null,/HTTP 413/,false],
    [503,{},/HTTP 503/,true],
  ]) {
    const ctx=context({fetch:async()=>({ok:false,status,json:async()=>payload,
      headers:new Headers({'X-Error-Event-Id':'test-event'})})});
    await assert.rejects(ctx.fetchJson('/test'),e=>{
      assert.match(e.message,pattern);assert(!e.message.includes('private-value'));
      assert.equal(e.isTransientError,transient);assert.equal(e.errorEventId,'test-event');return true;
    });
  }
  const ctx=context({fetch:async()=>({ok:false,status:502,json:async()=>{throw Error('HTML');},headers:new Headers()})});
  await assert.rejects(ctx.fetchJson('/test'),/HTTP 502/);
});

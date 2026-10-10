// Execute the actual TEM page program with synthetic File/DOM/XHR objects.
// This is not an XP/Supermium binary test.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {JSDOM} = require('jsdom');
const acorn = require('acorn');
const html = execFileSync(process.env.RIST_TEST_PYTHON || 'python3', ['-c',
  'from app.ahn_web import build_ahn_page; print(build_ahn_page())'], {encoding:'utf8'});
const script = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]).find(s=>s.includes('function chunkCrc32'));
const response = data => ({ok:true,status:200,json:async()=>data,text:async()=>JSON.stringify(data)});
function setup(t, low=true) {
  const dom = new JSDOM(html, {runScripts:'outside-only',url:'http://localhost/tem'});
  t.after(()=>dom.window.close());
  const w = dom.window;
  w.RIST_CLIENT_PROFILE = {lowResource:low,uploadChunkBytes:(low?2:4)*1024*1024,fileListLimit:low?200:500};
  w.fetch = async()=>response({roles:[]});
  w.eval(script.replace('\n    renderRequestOptions([]);', `
    window.__tem = {chunkCrc32, entryToBundleItems, addBundleItems, renderFileList, uploadBundleWithSession,
      items:function(){return bundleItems;}};
    renderRequestOptions([]);`));
  assert.ok(w.__tem);
  return {w,d:w.document,api:w.__tem};
}

test('TEM script syntax and known CRC32 vector with FileReader fallback',async t=>{
  acorn.parse(script,{ecmaVersion:'latest'});
  const {w,api}=setup(t);
  assert.equal(await api.chunkCrc32(new w.Blob(['123456789'])),'cbf43926');
});

test('checksum reads <=256KB blocks and yields without reading the entire chunk',async t=>{
  const {w,api}=setup(t);
  const reads=[];let ticks=0;
  const timer=w.setInterval(()=>ticks++,0);
  const file={size:4*1024*1024,slice:(start,end)=>({arrayBuffer:async()=>{
    reads.push(end-start);
    return new Uint8Array(end-start).buffer;
  }}),arrayBuffer:()=>assert.fail('whole chunk read')};
  assert.equal(await api.chunkCrc32(file),'1147406a'); // zlib CRC32 of 4MiB zeroes
  w.clearInterval(timer);
  assert.equal(reads.length,16);
  assert.ok(reads.every(size=>size<=256*1024));
  assert.ok(ticks>=16,`UI ticks: ${ticks}`);
});

for(const low of [true,false]) test(`bounded file-list DOM retains every selected file (low=${low})`,t=>{
  const {api,d}=setup(t,low);
  const files=Array.from({length:3000},(_,i)=>({path:`stem/${i}.tif`,file:{name:`${i}.tif`,size:100,lastModified:0}}));
  api.addBundleItems(files);api.renderFileList();
  assert.equal(api.items().length,3000);
  assert.equal(d.querySelectorAll('.ahn-chip').length,(low?200:500)+1);
  assert.match(d.body.textContent,/목록만 생략/);
  api.addBundleItems(files);
  assert.equal(api.items().length,3000);
  assert.throws(()=>api.addBundleItems(Array.from({length:3000},(_,i)=>({path:`tem/${i}.tif`,file:files[i].file}))),/5000/);
  assert.equal(api.items().length,3000);
});

test('folder collection resolves files sequentially instead of a Promise.all burst',async t=>{
  const {api}=setup(t);let active=0,peak=0;
  const children=Array.from({length:120},(_,i)=>({isFile:true,file(resolve){
    peak=Math.max(peak,++active);
    setTimeout(()=>{active--;resolve({name:`${i}.tif`});},0);
  }}));
  // FileSystemDirectoryReader callbacks are asynchronous in the browser.
  const entry={name:'stem',isDirectory:true,createReader(){let sent=false;return {readEntries(resolve){
    const batch=sent?[]:children;sent=true;setTimeout(()=>resolve(batch),0);
  }};}};
  const items=await api.entryToBundleItems(entry,'');
  assert.equal(items.length,120);
  assert.equal(peak,1);
  assert.equal(items[119].path,'stem/119.tif');
});

test('empty/deep directory trees are bounded too',async t=>{
  const {api}=setup(t);
  const entry={isDirectory:true,createReader:()=>assert.fail('limit must precede traversal')};
  await assert.rejects(api.entryToBundleItems(entry,'',[],33),/한도/);
  await assert.rejects(api.entryToBundleItems(entry,'',[],0,{visited:10000}),/한도/);
});

test('5MiB upload is sequential 2+2+1MiB with CRC retry and deferred validation',async t=>{
  const {w,api}=setup(t);
  const chunks=[],urls=[];let active=0,peak=0,failed=false;
  w.fetch=async url=>{urls.push(url);return response(url.includes('/complete')?{jobId:'job',status:'queued'}:{uploadId:'session'});};
  w.XMLHttpRequest=class {
    constructor(){this.upload={};}
    open(method,url){assert.equal(method,'POST');assert.match(url,/\/chunks$/);}
    send(form){
      peak=Math.max(peak,++active);
      const row={offset:Number(form.get('offset')),size:form.get('file').size,crc:form.get('chunk_crc32')};
      chunks.push(row);
      setTimeout(()=>{
        active--;
        this.status=failed?200:400;
        this.responseText=JSON.stringify(failed?{}:{retryable:true,code:'TEM_UPLOAD_CHUNK_CHECKSUM_MISMATCH',message:'retry'});
        failed=true;this.onload();
      },0);
    }
  };
  api.addBundleItems([{path:'data.zip',file:new w.File([new Uint8Array(5*1024*1024)],'data.zip')}]);
  assert.equal((await api.uploadBundleWithSession()).jobId,'job');
  assert.equal(peak,1);
  assert.equal(chunks.length,4);
  assert.deepEqual(chunks[0],chunks[1]);
  assert.deepEqual(chunks.map(x=>x.size),[2,2,2,1].map(x=>x*1024*1024));
  assert.deepEqual(chunks.map(x=>x.offset),[0,0,2,4].map(x=>x*1024*1024));
  assert.ok(chunks.every(x=>/^[0-9a-f]{8}$/.test(x.crc)));
  assert.ok(urls.includes('/api/v1/tem/upload-sessions/session/complete?defer_validation=true'));
});

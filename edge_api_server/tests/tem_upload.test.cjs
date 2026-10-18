// Execute the actual TEM page program with synthetic File/DOM/XHR objects.
// This is not an XP/Supermium binary test.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {JSDOM} = require('jsdom');
const acorn = require('acorn');
const html = execFileSync(process.env.RIST_TEST_PYTHON || 'python3', ['-c',
  'from app.ahn_web import build_ahn_page; print(build_ahn_page())'], {encoding:'utf8'});
const scripts = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
const script = scripts.find(s=>s.includes('function chunkCrc32'));
const profileScript = scripts.find(s=>s.includes('window.RIST_CLIENT_PROFILE ='));
const response = data => ({ok:true,status:200,json:async()=>data,text:async()=>JSON.stringify(data)});
const tick = () => new Promise(resolve=>setTimeout(resolve,15));
function setup(t, low=true, version=144) {
  const dom = new JSDOM(html, {runScripts:'outside-only',url:'http://localhost/tem'});
  t.after(()=>dom.window.close());
  const w = dom.window;
  Object.defineProperties(w.navigator, {
    userAgent: {get:()=> `Windows NT 6.1; Chrome/${version}.0 Supermium/${version}.0`},
    deviceMemory: {get:()=>low?2:8},
    hardwareConcurrency: {get:()=>low?2:8}
  });
  w.eval(profileScript);
  assert.equal(w.RIST_CLIENT_PROFILE.browser,'supermium');
  assert.equal(w.RIST_CLIENT_PROFILE.lowResource,low);
  w.fetch = async()=>response({roles:[]});
  w.eval(script.replace('\n    renderRequestOptions([]);', `
    window.__tem = {chunkCrc32, entryToBundleItems, addBundleItems, renderFileList, uploadBundleWithSession,
      setBusy, temRecovery, loadTemWork, persistTemWork, rememberTemUpload, rememberTemJob,
      checkTemResumeSelection, waitTemVisible, requestTemWake, releaseTemWake, temReturned,
      restoreTemWork, waitForReportJob, temRecoveryError, items:function(){return bundleItems;}};
    renderRequestOptions([]);`));
  assert.ok(w.__tem);
  return {w,d:w.document,api:w.__tem};
}

test('TEM script syntax and known CRC32 vector with FileReader fallback',async t=>{
  acorn.parse(script,{ecmaVersion:'latest'});
  const {w,api}=setup(t);
  assert.equal(await api.chunkCrc32(new w.Blob(['123456789'])),'cbf43926');
});

test('resume skips committed bytes, validates prefix, and completes without a second session',async t=>{
  const {w,api}=setup(t);
  const id='a'.repeat(32), file=new w.File([new Uint8Array(5*1024*1024)],'data.zip',{lastModified:123});
  const saved={path:'data.zip',size:file.size,lastModified:123,uploadedBytes:2*1024*1024,
    chunks:[{offset:0,size:2*1024*1024,crc32:await api.chunkCrc32(file.slice(0,2*1024*1024))}]};
  const urls=[],offsets=[];
  api.addBundleItems([{path:'data.zip',file}]);
  api.rememberTemUpload(id);
  w.fetch=async url=>{urls.push(url);return response(url.endsWith(id)?{uploadId:id,resumable:true,files:[saved]}:{jobId:'b'.repeat(32),status:'queued'});};
  w.XMLHttpRequest=class {
    constructor(){this.upload={};}
    open(){}
    send(form){offsets.push(Number(form.get('offset')));this.status=200;this.responseText='{}';setTimeout(()=>this.onload(),0);}
  };
  await api.uploadBundleWithSession();
  assert.deepEqual(offsets,[2,4].map(x=>x*1024*1024));
  assert.equal(urls.filter(url=>url==='/api/v1/tem/upload-sessions').length,0);
  assert.equal(JSON.parse(w.localStorage.getItem('rist.tem.resume.v1.anonymous')).uploadId,id);
});

test('reselected same-size and same-date but changed content cannot resume',async t=>{
  const {w,api}=setup(t);
  const items=[{path:'a.docx',file:new w.File(['abcdef'],'a.docx',{lastModified:12})}];
  const state={resumable:true,files:[{path:'a.docx',size:6,lastModified:12,uploadedBytes:3,
    chunks:[{offset:0,size:3,crc32:await api.chunkCrc32(new w.Blob(['xyz']))}]}]};
  await assert.rejects(api.checkTemResumeSelection(items,state),/파일 내용이 다릅니다/);
  state.files[0].lastModified=13;
  await assert.rejects(api.checkTemResumeSelection(items,state),/수정일/);
  await assert.rejects(api.checkTemResumeSelection([],state),/전체 파일/);
});

test('lost completion response restores the server job without resending any file',async t=>{
  const {w,api}=setup(t);
  const job={jobId:'b'.repeat(32),status:'queued'};
  api.addBundleItems([{path:'a.docx',file:new w.File(['docx'],'a.docx')}]);
  api.rememberTemUpload('a'.repeat(32));
  w.fetch=async()=>response({job});
  w.XMLHttpRequest=class{constructor(){assert.fail('no chunks after completion');}};
  assert.equal((await api.uploadBundleWithSession()).jobId,job.jobId);
  assert.equal(api.temRecovery.record.jobId,job.jobId);
});

test('job identity is saved before polling and completed state survives a reload without redirect',async t=>{
  const {w,d,api}=setup(t);
  const id='b'.repeat(32);
  let requests=0;
  w.fetch=async()=>{requests++;return response({jobId:id,status:'completed',summary:{}});};
  const work=api.waitForReportJob({jobId:id,status:'queued'});
  assert.equal(JSON.parse(w.localStorage.getItem('rist.tem.resume.v1.anonymous')).jobId,id);
  await work;
  api.temRecovery.record=null;
  api.loadTemWork('anonymous');
  await api.restoreTemWork();
  assert.equal(requests,2);
  assert.match(d.getElementById('ahn-recovery-message').textContent,/이전 보고서가 완성/);
  assert.equal(w.location.pathname,'/tem');
  assert.equal(d.getElementById('ahn-recovery-resume').disabled,false);
});

test('records are isolated by login and unavailable storage does not break the current upload',t=>{
  const {w,api}=setup(t);
  api.loadTemWork('author');api.rememberTemUpload('a'.repeat(32));
  api.loadTemWork('other');assert.equal(api.temRecovery.record,null);
  api.loadTemWork('author');assert.equal(api.temRecovery.record.uploadId,'a'.repeat(32));
  Object.defineProperty(w,'localStorage',{get(){throw new Error('blocked');}});
  api.rememberTemJob({jobId:'b'.repeat(32)});
  assert.equal(api.temRecovery.record.jobId,'b'.repeat(32));
});

test('hidden/offline transfer waits for both visible and online without repeated continuation',async t=>{
  const {w,d,api}=setup(t);
  let visible=false,online=false,continued=0;
  Object.defineProperty(d,'visibilityState',{get:()=>visible?'visible':'hidden'});
  Object.defineProperty(w.navigator,'onLine',{get:()=>online});
  const wait=api.waitTemVisible().then(()=>continued++);
  await tick();assert.equal(continued,0);
  visible=true;d.dispatchEvent(new w.Event('visibilitychange'));
  await tick();assert.equal(continued,0);
  online=true;w.dispatchEvent(new w.Event('online'));await wait;
  w.dispatchEvent(new w.Event('online'));d.dispatchEvent(new w.Event('visibilitychange'));
  assert.equal(continued,1);
});

test('a page reopened while offline restores on return, only once across repeated events',async t=>{
  const {w,d,api}=setup(t);let online=false,requests=0,release;
  Object.defineProperty(w.navigator,'onLine',{get:()=>online});
  api.rememberTemUpload('a'.repeat(32));
  w.fetch=async()=>{requests++;await new Promise(resolve=>release=resolve);return response({uploadedBytes:3});};
  await api.restoreTemWork();assert.equal(requests,0);
  online=true;w.dispatchEvent(new w.Event('online'));
  w.dispatchEvent(new w.Event('pageshow'));d.dispatchEvent(new w.Event('visibilitychange'));
  await tick();assert.equal(requests,1);release();await tick();
  assert.match(d.getElementById('ahn-recovery-message').textContent,/같은 전체 파일/);
});

test('unsupported, denied and late wake locks never block upload or keep a background screen lock',async t=>{
  const {w,d,api}=setup(t);
  api.temRecovery.uploadActive=true;
  await api.requestTemWake(); // no API / insecure context
  Object.defineProperty(w,'isSecureContext',{value:true});
  let requests=0,releases=0,resolveLock;
  Object.defineProperty(w.navigator,'wakeLock',{value:{request:async()=>{requests++;throw Error('denied');}},configurable:true});
  await api.requestTemWake();assert.equal(requests,1);assert.equal(api.temRecovery.wake,null);
  Object.defineProperty(w.navigator,'wakeLock',{value:{request:()=>new Promise(resolve=>resolveLock=resolve)},configurable:true});
  const pending=api.requestTemWake();
  api.temRecovery.uploadActive=false;
  resolveLock({release:async()=>releases++});await pending;
  assert.equal(releases,1);
  api.temRecovery.uploadActive=true;
  Object.defineProperty(w.navigator,'wakeLock',{value:{request:async()=>({release:async()=>releases++,addEventListener(){}})}});
  await api.requestTemWake();assert.ok(api.temRecovery.wake);
  Object.defineProperty(d,'visibilityState',{value:'hidden'});
  d.dispatchEvent(new w.Event('visibilitychange'));await tick();
  assert.equal(releases,2);assert.equal(api.temRecovery.wake,null);
});

test('expired work clears the identifier; authorization failure preserves it for the original user',t=>{
  const {api,d}=setup(t);
  api.rememberTemUpload('a'.repeat(32));
  api.temRecoveryError({status:403});assert.ok(api.temRecovery.record);
  assert.match(d.getElementById('ahn-recovery-message').textContent,/원래 계정/);
  api.temRecoveryError({status:404});assert.equal(api.temRecovery.record,null);
  assert.match(d.getElementById('ahn-recovery-message').textContent,/만료/);
});

test('deployed Chrome49 compiled TEM program restores a completed job',async t=>{
  const fs=require('node:fs'),path=require('node:path'),{createHash}=require('node:crypto');
  const root=path.resolve(__dirname,'../app/static/chrome49');
  const manifest=JSON.parse(fs.readFileSync(path.join(root,'manifest.json'),'utf8'));
  const hash=createHash('sha256').update(script.trim()).digest('hex');
  assert.ok(manifest.scripts[hash],'TEM asset must be rebuilt');
  const dom=new JSDOM(html,{runScripts:'outside-only',url:'http://localhost/tem'});
  t.after(()=>dom.window.close());
  const w=dom.window,id='b'.repeat(32);
  w.localStorage.setItem('rist.tem.resume.v1.anonymous',JSON.stringify({version:1,jobId:id}));
  delete w.AbortController;delete w.Blob.prototype.arrayBuffer;
  w.eval(fs.readFileSync(path.join(root,manifest.runtime),'utf8'));
  // The compatibility runtime intentionally installs its XHR-backed fetch.
  // Mock transport after bootstrap; its cookie/abort behavior has separate tests.
  w.fetch=async url=>response(url.includes('/jobs/')?{jobId:id,status:'completed',summary:{temSampleCount:2}}:{roles:[]});
  w.eval(fs.readFileSync(path.join(root,manifest.scripts[hash]),'utf8'));
  await tick();
  assert.match(w.document.getElementById('ahn-recovery-message').textContent,/이전 보고서가 완성/);
  assert.equal(w.document.getElementById('ahn-recovery-resume').disabled,false);
});

async function finishCollection(d) {
  for (let attempt=0; attempt<200; attempt++) {
    if (d.getElementById('ahn-drop').getAttribute('aria-busy') !== 'true') return;
    await new Promise(resolve=>setTimeout(resolve,5));
  }
  assert.fail('file collection did not finish');
}

function dropEvent(w, d, transfer) {
  const event=new w.Event('drop',{bubbles:true,cancelable:true});
  Object.defineProperty(event,'dataTransfer',{value:transfer});
  d.getElementById('ahn-drop').dispatchEvent(event);
  assert.equal(event.defaultPrevented,true);
}

test('drop captures protected file data synchronously before the paint yield',async t=>{
  const {w,d,api}=setup(t);
  const file=new w.File(['docx'],'test.docx');
  let readable=true;
  dropEvent(w,d,{get files(){return readable?[file]:[];}});
  readable=false; // Browser protects the drag store when dispatch returns.
  await finishCollection(d);
  assert.equal(api.items().length,1);
  assert.equal(api.items()[0].file,file);
  assert.match(d.getElementById('ahn-bundle-meta').textContent,/EDS 1/);
  assert.equal(d.getElementById('ahn-run').disabled,false);
});

test('drop keeps directory handles and a loose file whose entry API returns null',async t=>{
  const {w,d,api}=setup(t);
  let readable=true;
  const file=new w.File(['docx'],'standalone.docx');
  const children=Array.from({length:120},(_,i)=>({isFile:true,file:resolve=>
    setTimeout(()=>resolve(new w.File(['x'],`${i}.tiff`)),0)}));
  const entry={name:'tem',isDirectory:true,createReader(){let offset=0;return {readEntries(resolve){
    const batch=children.slice(offset,offset+100);offset+=100;setTimeout(()=>resolve(batch),0);
  }};}};
  dropEvent(w,d,{items:[
    {kind:'file',webkitGetAsEntry:()=>readable?entry:null},
    {kind:'file',webkitGetAsEntry:()=>null,getAsFile:()=>readable?file:null}
  ]});
  readable=false;
  await finishCollection(d);
  assert.equal(api.items().length,121);
  assert.equal(api.items()[119].path,'tem/119.tiff');
  assert.equal(api.items()[120].path,'standalone.docx');
});

test('empty and failed drops report an error and release the collection lock',async t=>{
  const {w,d,api}=setup(t);
  dropEvent(w,d,{files:[]});
  await finishCollection(d);
  assert.match(d.getElementById('ahn-status').textContent,/첨부할 파일을 찾지 못했습니다/);
  dropEvent(w,d,{items:[{kind:'file',webkitGetAsEntry:()=>({isFile:true,file:(_resolve,reject)=>
    reject(new Error('Access denied'))})}]});
  await finishCollection(d);
  assert.match(d.getElementById('ahn-status').textContent,/Access denied/);
  assert.equal(api.items().length,0);
  assert.equal(d.getElementById('ahn-add-folder').disabled,false);
});

test('opening or cancelling either picker never starts collection or disables the page',t=>{
  const {w,d}=setup(t);
  for(const kind of ['files','folder']) {
    const input=d.getElementById(`ahn-bundle-${kind}`);
    let opens=0;
    input.click=()=>opens++;
    d.getElementById(`ahn-add-${kind}`).click();
    assert.equal(opens,1);
    assert.equal(d.getElementById('ahn-drop').getAttribute('aria-busy'),'false');
    assert.equal(d.getElementById('ahn-add-folder').disabled,false);
    assert.equal(d.getElementById('ahn-collect-progress').classList.contains('is-visible'),false);
    input.dispatchEvent(new w.Event('cancel'));
    w.dispatchEvent(new w.Event('focus'));
    d.getElementById(`ahn-add-${kind}`).click();
    assert.equal(opens,2);
  }
});

test('folder selection starts collection on change, keeps relative paths and blocks overlapping drops',async t=>{
  const {w,d,api}=setup(t);
  const input=d.getElementById('ahn-bundle-folder');
  const file=new w.File(['x'],'sample.docx');
  Object.defineProperty(file,'webkitRelativePath',{value:'bundle/reports/sample.docx'});
  Object.defineProperty(input,'files',{value:[file]});
  input.dispatchEvent(new w.Event('change'));
  assert.equal(d.getElementById('ahn-drop').getAttribute('aria-busy'),'true');
  dropEvent(w,d,{files:[new w.File(['x'],'extra.docx')]});
  await finishCollection(d);
  assert.deepEqual(Array.from(api.items(),item=>item.path),['bundle/reports/sample.docx']);
  api.setBusy(true);
  dropEvent(w,d,{files:[new w.File(['x'],'extra.docx')]});
  assert.equal(api.items().length,1);
  api.setBusy(false);
  assert.equal(d.getElementById('ahn-run').disabled,false);
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

for(const version of [144,150]) for(const low of [true,false]) test(`Supermium ${version} bounded file-list DOM retains every selected file (low=${low})`,t=>{
  const {api,d}=setup(t,low,version);
  const files=Array.from({length:3000},(_,i)=>({path:`stem/${i}.tif`,file:{name:`${i}.tif`,size:100,lastModified:0}}));
  api.addBundleItems(files);api.renderFileList();
  assert.equal(api.items().length,3000);
  assert.equal(d.querySelectorAll('.ahn-chip').length,(low?200:500)+1);
  assert.match(d.body.textContent,/목록만 생략/);
  assert.match(d.body.textContent,low?/자원 절약 모드: 2MB/:/일반 성능 모드: 4MB/);
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

for(const version of [144,150]) for(const low of [true,false]) test(`Supermium ${version} 5MiB upload uses hardware budget with CRC retry (low=${low})`,async t=>{
  const {w,api}=setup(t,low,version);
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
  assert.equal(chunks.length,low?4:3);
  assert.deepEqual(chunks[0],chunks[1]);
  assert.deepEqual(chunks.map(x=>x.size),(low?[2,2,2,1]:[4,4,1]).map(x=>x*1024*1024));
  assert.deepEqual(chunks.map(x=>x.offset),(low?[0,0,2,4]:[0,0,4]).map(x=>x*1024*1024));
  assert.ok(chunks.every(x=>/^[0-9a-f]{8}$/.test(x.crc)));
  assert.ok(urls.includes('/api/v1/tem/upload-sessions/session/complete?defer_validation=true'));
});

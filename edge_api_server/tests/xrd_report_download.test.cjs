// Exercise the actual workspace download handler in native and Chrome 49 builds.
// DOM tests do not claim Windows/Supermium binary validation.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {createHash} = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');

const page = execFileSync(process.env.RIST_TEST_PYTHON || 'python3', ['-c',
  'from app.xrd_web import build_xrd_page; print(build_xrd_page())'], {encoding:'utf8',maxBuffer:8*1024*1024});
const assetRoot = path.resolve(__dirname, '../app/static/chrome49');
const manifest = JSON.parse(fs.readFileSync(path.join(assetRoot,'manifest.json'),'utf8'));
const report = `<!doctype html><html><head><script src="/xrd/assets/plotly.min.js"></script></head><body>
  <div id="xrd-analysis-result" contenteditable="true" aria-label="분석결과 편집"><p>원본 분석결과</p></div>
  <div id="xrd-plot"><div class="modebar">temporary tools</div></div>
  <button id="xrd-report-pdf-export">PDF Export</button>
  <script>window.reportGraphReady=true;</script></body></html>`;
const tick = () => new Promise(resolve=>setTimeout(resolve,15));

async function setup(t, mode) {
  const dom = new JSDOM(page,{url:'http://localhost/xrd',runScripts:'outside-only',pretendToBeVisual:true});
  t.after(()=>dom.window.close());
  const w=dom.window,d=w.document;
  if(mode==='chrome49') w.eval(fs.readFileSync(path.join(assetRoot,manifest.runtime),'utf8'));
  w.TextEncoder=TextEncoder;
  w.fetch=async url=>({ok:true,status:200,json:async()=>({roles:[]}),
    text:async()=>url==='/api/v1/xrd/example'?report:'window.Plotly={ready:true};'});
  const blobs=new Map();let sequence=0;const downloads=[];
  w.URL.createObjectURL=blob=>{const url='blob:test-'+ ++sequence;blobs.set(url,blob);return url;};
  w.URL.revokeObjectURL=()=>{};
  w.HTMLAnchorElement.prototype.click=function(){downloads.push({name:this.download,blob:blobs.get(this.href)});};
  let script=[...page.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1])
    .find(s=>s.includes('function sanitizeDownloadHtml('));
  assert(script);
  if(mode==='chrome49') {
    const key=createHash('sha256').update(script.trim()).digest('hex');
    assert(manifest.scripts[key],'Rebuild Chrome 49 assets');
    script=fs.readFileSync(path.join(assetRoot,manifest.scripts[key]),'utf8');
  }
  w.eval(script);
  d.getElementById('xrd-example').click();await tick();
  assert.equal(d.getElementById('xrd-download').getAttribute('aria-disabled'),'false');
  const frame=d.querySelector('#xrd-preview iframe');
  // jsdom does not implement iframe.srcdoc; populate it as the browser does.
  frame.contentDocument.open();frame.contentDocument.write(frame.srcdoc);frame.contentDocument.close();
  const result=frame.contentDocument.getElementById('xrd-analysis-result');
  function readBlob(blob){return new Promise((resolve,reject)=>{
    const reader=new w.FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsText(blob);
  });}
  async function download(){d.getElementById('xrd-download').dispatchEvent(new w.MouseEvent('click',{bubbles:true,cancelable:true}));await tick();}
  return {w,d,frame,result,downloads,readBlob,download};
}

for(const mode of ['native','chrome49']) {
  test(`${mode}: downloaded HTML preserves final edited prose but cannot edit it on reopen`,async t=>{
    const h=await setup(t,mode);
    const edited='<p><strong>검토 완료</strong>한 결과 &amp; 근거</p><div contenteditable="true">추가 문안</div>';
    h.result.innerHTML=edited;
    await h.download();assert.equal(h.downloads.length,1);
    assert.equal(h.downloads[0].name,'xrd-report.html');
    const saved=await h.readBlob(h.downloads[0].blob);
    const reopened=new JSDOM(saved,{url:'file:///xrd-report.html',runScripts:'dangerously'});
    t.after(()=>reopened.window.close());
    const result=reopened.window.document.getElementById('xrd-analysis-result');
    assert.equal(result.getAttribute('contenteditable'),'false');
    assert.equal(result.getAttribute('aria-readonly'),'true');
    assert.equal(result.querySelector('[contenteditable]').getAttribute('contenteditable'),'false');
    assert.equal(result.textContent,h.result.textContent);
    assert.equal(result.querySelector('strong').textContent,'검토 완료');
    assert.equal(reopened.window.reportGraphReady,true);
    assert.equal(reopened.window.Plotly.ready,true);
    assert(reopened.window.document.getElementById('xrd-report-pdf-export'));
    assert.equal(reopened.window.document.querySelector('.modebar'),null);
    assert.equal(h.result.getAttribute('contenteditable'),'true');
    assert.equal(h.result.innerHTML,edited);
    h.result.innerHTML='<p>두 번째 검토 결과</p>';
    await h.download();assert.equal(h.downloads.length,2);
    assert.match(await h.readBlob(h.downloads[1].blob),/두 번째 검토 결과/);
  });

  test(`${mode}: failed export preparation cannot fall back to an editable file`,async t=>{
    const h=await setup(t,mode);
    h.w.DOMParser=function(){throw new Error('parser failure');};
    await h.download();
    assert.equal(h.downloads.length,0);
    assert.match(h.d.getElementById('xrd-status').textContent,/읽기 전용으로 저장하지 못했습니다/);
    assert.equal(h.result.getAttribute('contenteditable'),'true');
  });
}

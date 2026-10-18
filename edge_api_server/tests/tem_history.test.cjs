const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {JSDOM} = require('jsdom');
const acorn = require('acorn');
const app = path.resolve(__dirname, '../app');
const source = fs.readFileSync(path.join(app, 'tem_history.js'), 'utf8').trim();
const html = '<button id="ahn-history-open">작업 이력</button>' + fs.readFileSync(path.join(app, 'tem_history.html'), 'utf8');
const assets = path.join(app, 'static/chrome49');
const manifest = JSON.parse(fs.readFileSync(path.join(assets, 'manifest.json')));
const flush = async () => { for (let i=0;i<6;i++) await new Promise(setImmediate); };
const row = (extra={}) => ({jobId:'a'.repeat(32), title:'Sample A', status:'completed', createdAt:1700000000,
  bytes:1024, message:'완료', editUrl:'/api/v1/tem/report/jobs/'+'a'.repeat(32)+'/edit', ...extra});
const response = (items, total=items.length) => ({items, total, storage:{usedBytes:50000, myBytes:40000,
  limitBytes:50*1024**3, retentionDays:30, trashDays:7}});
function setup(t, mode, handler) {
  const dom = new JSDOM(html, {url:'http://rist.test/tem', runScripts:'outside-only'});
  t.after(()=>dom.window.close());
  const w = dom.window, d = w.document, calls=[];
  w.fetch = async (url, options) => {calls.push({url, options}); const body=await handler(url,options);
    return {ok:!body.error, json:async()=>body};};
  w.confirm = () => true;
  let program = source;
  if (mode==='chrome49') {
    const file = manifest.scripts[createHash('sha256').update(source).digest('hex')];
    assert.ok(file, 'Rebuild compatibility assets');
    program = fs.readFileSync(path.join(assets,file),'utf8');
  }
  acorn.parse(program,{ecmaVersion:mode==='chrome49'?5:2022});
  w.eval(program);
  const click = text => {const b=[...d.querySelectorAll('button')].find(b=>b.textContent===text); assert.ok(b,text); b.click();};
  return {w,d,calls,click,open:async()=>{d.getElementById('ahn-history-open').click(); await flush();}};
}
for(const mode of ['native','chrome49']) {
  test(`${mode}: lazy load, escaped titles, safe report link, scroll and focus restoration`,async t=>{
    const h=setup(t,mode,async()=>response([row({title:'<img src=x onerror=alert(1)>',message:'<script>bad()</script>'})]));
    assert.equal(h.calls.length,0);
    h.d.body.style.overflow='auto'; await h.open();
    assert.equal(h.calls.length,1); assert.equal(h.calls[0].options.credentials,'same-origin');
    assert.equal(h.d.querySelector('#th-list img'),null); assert.equal(h.d.querySelector('#th-list script'),null);
    const link=h.d.querySelector('#th-list a'); assert.equal(link.target,'_blank'); assert.equal(link.rel,'noopener');
    assert.equal(h.d.body.style.overflow,'hidden'); assert.match(h.d.getElementById('th-policy').textContent,/30일/);
    h.click('닫기'); assert.equal(h.d.body.style.overflow,'auto'); assert.equal(h.d.activeElement.id,'ahn-history-open');
  });
  test(`${mode}: rename and trash confirmation, authenticated mutations and refresh`,async t=>{
    const item=row();
    const h=setup(t,mode,async(url,o)=>{
      if(o.method==='POST') {const p=JSON.parse(o.body); assert.equal(o.headers['X-Requested-With'],'RIST-TEM-History');
        if(p.action==='rename') item.title=p.title; return {saved:true};}
      return response([item]);
    });
    await h.open(); h.d.querySelector('#th-list input').value='교정 시료'; h.click('이름 저장'); await flush();
    assert.equal(h.d.querySelector('#th-list input').value,'교정 시료');
    const before=h.calls.length; h.w.confirm=()=>false; h.click('휴지통'); await flush(); assert.equal(h.calls.length,before);
    h.w.confirm=()=>true; h.click('휴지통'); await flush();
    assert.ok(h.calls.some(c=>c.options.body && JSON.parse(c.options.body).action==='trash'));
  });
  test(`${mode}: busy jobs disabled and pinned rows have no delete`,async t=>{
    const h=setup(t,mode,async()=>response([row({busy:true, status:'running',editUrl:null}),row({jobId:'b'.repeat(32),pinned:true})]));
    await h.open(); const first=h.d.querySelectorAll('.th-row')[0], second=h.d.querySelectorAll('.th-row')[1];
    assert.ok([...first.querySelectorAll('button')].every(b=>b.disabled)); assert.equal(first.querySelector('a'),null);
    assert.ok(![...second.querySelectorAll('button')].some(b=>b.textContent==='휴지통'));
  });
  test(`${mode}: trash batch purges only visible rows and reports partial failure`,async t=>{
    const items=[row({deletedAt:1}),row({jobId:'b'.repeat(32),deletedAt:1})]; let posts=0;
    const h=setup(t,mode,async(url,o)=>{
      if(o.method==='POST') {posts++; return posts===2 ? {error:true,message:'전송 중입니다.'} : {deleted:true};}
      return response(items,100);
    });
    await h.open(); const f=h.d.getElementById('th-filter'); f.value='trash'; f.dispatchEvent(new h.w.Event('change')); await flush();
    h.click('이 페이지 휴지통 비우기'); await flush();
    assert.equal(posts,2); assert.match(h.d.getElementById('th-notice').textContent,/1건 처리.*전송 중/);
    assert.equal(h.d.getElementById('th-close').disabled,false);
  });
  test(`${mode}: late response cannot overwrite newest filter and search`,async t=>{
    let release, count=0;
    const h=setup(t,mode,async()=>{if(++count===1) return new Promise(r=>release=r); return response([row({title:'휴지통 최신',deletedAt:1})]);});
    await h.open(); const f=h.d.getElementById('th-filter'); f.value='trash'; f.dispatchEvent(new h.w.Event('change')); await flush();
    release(response([row({title:'이전 응답'})])); await flush();
    assert.equal(h.d.querySelector('#th-list input').value,'휴지통 최신');
  });
  test(`${mode}: network error is visible and refresh recovers`,async t=>{
    let fail=true;
    const h=setup(t,mode,async()=>{if(fail) throw new Error('네트워크 오류'); return response([]);});
    await h.open(); assert.match(h.d.getElementById('th-notice').textContent,/네트워크 오류/);
    fail=false; h.click('새로고침'); await flush(); assert.match(h.d.getElementById('th-list').textContent,/없습니다/);
  });
}

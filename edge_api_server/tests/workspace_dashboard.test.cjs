const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom'),acorn=require('acorn'),babel=require('@babel/core');
const root=path.join(__dirname,'../app'),source=fs.readFileSync(path.join(root,'workspace_dashboard.js'),'utf8');
const html=fs.readFileSync(path.join(root,'workspace_dashboard.html'),'utf8').replace(/<script>[\s\S]*?<\/script>/g,'');
const programs={native:source,chrome49:babel.transformSync(source,{configFile:false,babelrc:false,presets:[[require('@babel/preset-env'),{targets:{chrome:'49'},forceAllTransforms:true,modules:false}]]}).code};
function payload(){return {user:{displayName:'윤 연구원',roles:[],ssoRecentlyAuthenticated:false},projects:['FTIR','TEM'],counts:{active:1,completedToday:1,pendingTransfers:0,failed:1,failedTransfers:0,voc:1},
  recent:[{id:'1',project:'FTIR',title:'C272 보고서',status:'completed',updatedAt:'2026-10-26T16:00:00Z',completedAt:'2026-10-26T16:00:00Z',requestNumber:'R-001',href:'/api/v1/ftir/report/jobs/abc/edit',action:'보고서 편집'},
    {id:'2',project:'TEM',title:'LTO201',status:'running',updatedAt:'2026-10-26T16:00:00Z',requestNumber:'',href:'/tem',action:'작업 이력'},
    {id:'3',project:'TEM',title:'이전 작업',status:'failed',updatedAt:'2026-10-25T12:00:00Z',requestNumber:'R-003',href:'/tem'}],
  total:3,transfers:[],transferAvailable:true,voc:{count:1,items:[{project:'FTIR',title:'조치 완료 확인',status:'RESOLVED',href:'/voc?project=FTIR'}]},admin:null,warnings:[],unavailable:[],generatedAt:'2026-10-26T17:00:00Z'};}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function setup(t,mode,response=payload()){
  const dom=new JSDOM(html,{url:'https://rist.test/',runScripts:'outside-only'});t.after(()=>dom.window.close());const w=dom.window,d=w.document,calls=[];
  w.HTMLElement.prototype.scrollIntoView=function(){this.dataset.scrolled='true';};
  w.fetch=(url,options)=>{calls.push([url,options]);return Promise.resolve({ok:true,status:200,json:()=>Promise.resolve(response)});};
  w.eval(programs[mode]);return {w,d,calls,dom};
}
for(const mode of Object.keys(programs)){
  test(mode+': permissions, recent tasks, status details and safe experiment cards',async t=>{
    const h=setup(t,mode);await flush();const d=h.d;
    assert.equal(d.title,'RIST 분석 포털');assert.equal(d.querySelector('#count-active').textContent,'1');
    assert.equal(d.querySelectorAll('[data-project]:not([hidden])').length,2);assert(d.getElementById('workspace-admin').hidden);
    assert.equal(d.querySelectorAll('.recent-row').length,3);assert.equal(d.querySelectorAll('#workspace-attention .alert-row').length,2);
    assert.equal(h.calls.length,1);assert.equal(h.calls[0][1].credentials,'same-origin');
    const search=d.getElementById('workspace-search');search.value='r-001';search.dispatchEvent(new h.w.Event('input'));assert.equal(d.querySelectorAll('.recent-row').length,1);
    search.value='';search.dispatchEvent(new h.w.Event('input'));d.querySelector('[data-summary="active"]').click();assert.match(d.getElementById('workspace-recent').textContent,/LTO201/);assert.equal(d.querySelectorAll('.recent-row').length,1);
    d.querySelector('[data-summary="completedToday"]').click();assert.match(d.getElementById('workspace-recent').textContent,/C272/);assert.equal(d.querySelectorAll('.recent-row').length,1);
    d.getElementById('workspace-filter-clear').click();assert.equal(d.querySelectorAll('.recent-row').length,3);
    assert.equal(h.calls.length,1,'Filters must not trigger queries or polling');
  });
  test(mode+': titles are text, links reject scripts and admin counts are role-gated',async t=>{
    const p=payload();p.recent[0].title='<img src=x onerror=alert(1)>';p.recent[0].href='javascript:alert(1)';p.admin={pendingUsers:4,reports:12,failedTransfers:1,diskFreeBytes:1073741824,diskTotalBytes:2147483648};
    const h=setup(t,mode,p);await flush();assert.equal(h.d.querySelectorAll('#workspace-recent img').length,0);assert.equal(h.d.querySelectorAll('.brand-logo').length,1);assert.equal(h.d.querySelector('.recent-row a').pathname,'/');assert(h.d.getElementById('workspace-admin').hidden);
    p.user.roles=['ADMIN'];h.d.getElementById('workspace-refresh').click();await flush();assert(!h.d.getElementById('workspace-admin').hidden);assert.match(h.d.getElementById('workspace-admin-metrics').textContent,/1.0 GB/);
  });
  test(mode+': failed refresh clears private details and never presents failure as zero',async t=>{
    const h=setup(t,mode);await flush();h.w.fetch=()=>Promise.resolve({ok:false,status:401});h.d.getElementById('workspace-refresh').click();await flush();
    assert(!h.d.body.textContent.includes('C272'));assert(!h.d.body.textContent.includes('윤 연구원'));
    assert.equal(h.d.getElementById('count-active').textContent,'—');assert.match(h.d.getElementById('workspace-notice').textContent,/로그인/);assert(h.d.getElementById('workspace-account-actions').hidden);
  });
  test(mode+': unavailable sources and no permissions have distinct empty states',async t=>{
    const p=payload();p.projects=[];p.recent=[];p.total=0;p.user.roles=[];p.counts.pendingTransfers=null;p.counts.voc=null;p.counts.failed=0;p.unavailable=['TEM'];p.transferAvailable=false;p.voc=null;p.warnings=['TEM: 조회 실패'];
    const h=setup(t,mode,p);await flush();assert(!h.d.getElementById('workspace-no-permission').hidden);assert.equal(h.d.querySelectorAll('[data-project]:not([hidden])').length,0);
    assert.equal(h.d.getElementById('count-attention').textContent,'—');assert.equal(h.d.getElementById('count-completedToday').textContent,'—');assert.match(h.d.getElementById('workspace-transfers').textContent,/조회할 수 없습니다/);
  });
}
test('dashboard script is ES5 and timeout ignores a late response',async t=>{
  acorn.parse(source,{ecmaVersion:5});const h=setup(t,'native');await flush();let complete,timed;
  h.w.setTimeout=fn=>{timed=fn;return 1;};h.w.clearTimeout=()=>{};h.w.fetch=()=>new Promise(resolve=>complete=resolve);
  h.d.getElementById('workspace-refresh').click();assert(h.d.getElementById('workspace-refresh').disabled);timed();assert.match(h.d.getElementById('workspace-notice').textContent,/지연/);
  complete({ok:true,status:200,json:()=>Promise.resolve(payload())});await flush();assert(!h.d.body.textContent.includes('C272'));assert(!h.d.getElementById('workspace-refresh').disabled);
});

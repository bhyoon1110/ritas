const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const esbuild = require('esbuild');
const babel = require('@babel/core');
const acorn = require('acorn');
const native = esbuild.buildSync({entryPoints:[path.resolve(__dirname,'../../app/xrd_workbench/web/shared/project_manager.mjs')],
  bundle:true, write:false, format:'iife', globalName:'Manager', logLevel:'silent'}).outputFiles[0].text;
const legacy = babel.transformSync(native, {configFile:false, babelrc:false,
  presets:[[require('@babel/preset-env'), {targets:{chrome:'49'}, forceAllTransforms:true, modules:false}]]}).code;
acorn.parse(legacy, {ecmaVersion:5});
const flush = async () => {for (let i=0;i<6;i++) await new Promise(setImmediate);};
const row = (id,label) => ({storage_id:id,label,managed_revision:0,archived_at:null,created_at:'2026-10-17T10:00:00Z',sample_count:2,file_count:5,total_bytes:12345});

function setup(t,program,{current='a',initial=[row('a','Alpha'),row('b','Beta')]}={}) {
  const dom = new JSDOM('<body class="withShell"><button id="manage">프로젝트 관리</button></body>',{url:'http://rist.test/',runScripts:'outside-only'});
  t.after(()=>dom.window.close());
  const w=dom.window,d=w.document,calls=[],navigated=[];
  let data=initial, refreshes=0, fail=null, pending=null, planned=[];
  w.fetch=async (url,options={})=>{
    calls.push({url,options});
    if(pending) await pending;
    if(fail) return {ok:false,status:fail,headers:{get:()=>null},json:async()=>({detail:'목록 새로고침 후 다시 시도하세요.'})};
    if(!options.method) {
      const params=new URL(url,'http://rist.test').searchParams;
      const all=data.filter(p=>!!p.archived_at===(params.get('archived')==='true'));
      const found=all.filter(p=>p.label.toLowerCase().includes((params.get('q')||'').toLowerCase()));
      const size=Number(params.get('per_page')||20),pages=Math.max(1,Math.ceil(found.length/size)),page=Math.min(Number(params.get('page')||1),pages);
      return {ok:true,json:async()=>({items:found.slice((page-1)*size,page*size).map(p=>({...p})),total:found.length,total_in_tab:all.length,page,pages})};
    }
    if(url==='/api/project-purges/plan') {
      const value=JSON.parse(options.body);
      planned=data.filter(p=>p.archived_at && (value.all_trash || value.items.some(i=>i.storage_id===p.storage_id)));
      return {ok:true,json:async()=>({plan_id:'plan',count:planned.length,total_bytes:1000})};
    }
    if(url==='/api/project-purges') {
      const value=JSON.parse(options.body);
      assert.equal(value.confirmation,'영구 삭제');assert.equal(value.expected_count,planned.length);
      data=data.filter(p=>!planned.includes(p));
      return {ok:true,json:async()=>({status:'completed',job_id:'purge'})};
    }
    if(url==='/api/projects/manage-batch') {
      const value=JSON.parse(options.body);
      value.items.forEach(item=>{const p=data.find(p=>p.storage_id===item.storage_id);assert.equal(item.expected_revision,p.managed_revision);p.archived_at=value.action==='archive'?'2026-10-17T12:00:00Z':null;p.managed_revision++;});
      return {ok:true,json:async()=>({count:value.items.length})};
    }
    const id=url.split('/')[3],p=data.find(p=>p.storage_id===id),payload=JSON.parse(options.body);
    assert.equal(payload.expected_revision,p.managed_revision);
    if(options.method==='PATCH')p.label=payload.label;
    if(options.method==='DELETE')p.archived_at='2026-10-17T12:00:00Z';
    if(options.method==='POST')p.archived_at=null;
    p.managed_revision++;
    return {ok:true,json:async()=>({...p})};
  };
  w.confirm=()=>true;
  w.prompt=()=>null;
  w.eval(program);
  const overlay=w.Manager.mountProjectManager({button:d.getElementById('manage'),getCurrent:()=>current,
    refreshProjects:async()=>{refreshes++;},host:w,navigate:url=>navigated.push(url)});
  const q=s=>overlay.querySelector(s);
  const action=(id,name)=>q(`[data-project-id="${id}"] [data-action="${name}"]`).click();
  return {w,d,calls,navigated,q,overlay,action,get refreshes(){return refreshes;},get data(){return data;},
    fail:status=>{fail=status;},hold:p=>{pending=p;},open:async()=>{d.getElementById('manage').focus();d.getElementById('manage').click();await flush();}};
}

for(const [mode,program] of Object.entries({native,chrome49:legacy})) {
  test(`${mode}: project name editing refreshes choices without navigating`,async t=>{
    const ui=setup(t,program);await ui.open();
    ui.q('[data-project-id="a"] [data-name]').value='Renamed <sample>';
    ui.action('a','rename');await flush();
    assert.equal(ui.data[0].label,'Renamed <sample>');
    assert.equal(ui.refreshes,1);
    assert.equal(ui.navigated.length,0);
    assert.equal(ui.overlay.querySelectorAll('sample').length,0);
    assert.match(ui.q('[data-notice]').textContent,/시료명과 보고서 제목/);
  });
  test(`${mode}: delete confirmation and unsaved/busy guards prevent mutation`,async t=>{
    const ui=setup(t,program);await ui.open();
    ui.w.confirm=()=>false;ui.action('a','archive');await flush();
    ui.w.confirm=()=>true;ui.w.xrdBeforeLeave=async()=>false;ui.action('a','archive');await flush();
    ui.w.xrdBeforeLeave=async()=>{throw new Error('must not reach');};ui.w.xrdCanReset=()=>false;
    ui.action('a','archive');await flush();
    assert.equal(ui.calls.filter(c=>c.options.method).length,0);
    assert.equal(ui.data[0].archived_at,null);
    assert.equal(ui.navigated.length,0);
  });
  test(`${mode}: trash hides another project and restore keeps its name`,async t=>{
    const ui=setup(t,program);await ui.open();ui.action('b','archive');await flush();
    assert.equal(ui.q('[data-project-id="b"]'),null);
    assert.equal(ui.navigated.length,0);
    ui.q('[data-tab="trash"]').click();await flush();
    assert.equal(ui.q('[data-project-id="a"]'),null);
    assert.equal(ui.q('[data-name]').readOnly,true);
    ui.action('b','restore');await flush();
    assert.equal(ui.data[1].archived_at,null);
    assert.equal(ui.data[1].label,'Beta');
    assert.equal(ui.data[1].managed_revision,2);
    assert.equal(ui.refreshes,2);
    ui.q('[data-tab="active"]').click();await flush();
    assert.ok(ui.q('[data-project-id="b"]'));
  });
  test(`${mode}: current project deletion navigates to an empty fresh upload only after success`,async t=>{
    const ui=setup(t,program);await ui.open();ui.action('a','archive');await flush();
    assert.deepEqual(ui.navigated,['/?step=upload&fresh=1']);
    assert.equal(ui.data[0].managed_revision,1);
  });
  test(`${mode}: conflict/network errors preserve the current input and never pretend to succeed`,async t=>{
    const ui=setup(t,program);await ui.open();ui.fail(409);
    let canceled=0;ui.w.xrdLeaveCanceled=()=>{canceled++;};
    ui.q('[data-project-id="a"] [data-name]').value='Pending name';
    ui.action('a','rename');await flush();
    assert.equal(ui.q('[data-project-id="a"] [data-name]').value,'Pending name');
    assert.match(ui.q('[data-notice]').textContent,/새로고침/);
    assert.equal(ui.refreshes,0);
    assert.equal(ui.q('[data-close]').disabled,false);
    ui.action('a','archive');await flush();
    assert.equal(canceled,2);
    assert.equal(ui.navigated.length,0);
    ui.q('[data-tab="trash"]').click();await flush();
    assert.equal(ui.q('[data-rows]').children.length,0);
    assert.equal(ui.q('[data-next]').disabled,true);
  });
  test(`${mode}: paginated search escapes names and renders only twenty rows`,async t=>{
    const initial=Array.from({length:62},(_,i)=>row('id'+i,`Sample ${i}`));
    initial[60].label='<img src=x onerror=alert(1)> rare';
    const ui=setup(t,program,{initial});await ui.open();
    assert.equal(ui.overlay.querySelectorAll('.projectRow').length,20);
    ui.q('[data-select-all]').click();assert.equal(ui.q('[data-selected]').textContent,'20개 선택');
    ui.q('[data-next]').click();await flush();
    assert.equal(ui.overlay.querySelectorAll('.projectRow').length,20);
    assert.equal(ui.q('[data-selected]').textContent,'0개 선택');
    ui.q('[data-search]').value='rare';ui.q('[data-search]').dispatchEvent(new ui.w.Event('input'));
    await new Promise(r=>setTimeout(r,300));await flush();
    assert.equal(ui.overlay.querySelectorAll('.projectRow').length,1);
    assert.equal(ui.overlay.querySelectorAll('img').length,0);
    ui.overlay.click();assert.equal(ui.overlay.hidden,false);
    ui.q('[data-close]').click();assert.equal(ui.overlay.hidden,true);
    assert.equal(ui.d.activeElement.id,'manage');
  });
  test(`${mode}: pending request blocks double submission and closing`,async t=>{
    const ui=setup(t,program);await ui.open();let resolve;
    ui.hold(new Promise(r=>{resolve=r;}));
    ui.action('b','archive');await flush();
    assert.equal(ui.q('[data-close]').disabled,true);
    ui.action('b','archive');ui.q('[data-close]').click();
    ui.overlay.dispatchEvent(new ui.w.KeyboardEvent('keydown',{key:'Escape'}));
    assert.equal(ui.overlay.hidden,false);
    assert.equal(ui.calls.filter(c=>c.url==='/api/projects/manage-batch').length,1);
    resolve();await flush();
    assert.equal(ui.q('[data-close]').disabled,false);
  });
  test(`${mode}: bulk selection is page scoped and restores selected projects`,async t=>{
    const ui=setup(t,program,{current:'none',initial:Array.from({length:32},(_,i)=>row('id'+i,'Sample '+i))});await ui.open();
    ui.q('[data-select-all]').click();ui.q('[data-bulk]').click();await flush();
    assert.equal(ui.data.filter(p=>p.archived_at).length,20);
    ui.q('[data-tab="trash"]').click();await flush();
    ui.q('[data-select]').click();ui.q('[data-bulk]').click();await flush();
    assert.equal(ui.data.filter(p=>p.archived_at).length,19);
  });
  test(`${mode}: empty trash requires exact text and includes filtered-out trash`,async t=>{
    const initial=[row('a','Alpha'),row('b','Beta'),row('c','Keep')];
    initial[0].archived_at=initial[1].archived_at='2026-10-17T12:00:00Z';
    const ui=setup(t,program,{initial});await ui.open();ui.q('[data-tab="trash"]').click();await flush();
    ui.q('[data-search]').value='Alpha';ui.q('[data-search]').dispatchEvent(new ui.w.Event('input'));
    await new Promise(r=>setTimeout(r,300));await flush();
    assert.equal(ui.overlay.querySelectorAll('.projectRow').length,1);
    ui.w.prompt=()=> 'yes';ui.q('[data-empty]').click();await flush();
    assert.equal(ui.data.length,3);
    let text='';ui.w.prompt=message=>{text=message;return '영구 삭제';};ui.q('[data-empty]').click();await flush();
    assert.match(text,/전체 휴지통 2개/);assert.match(text,/복원할 수 없습니다/);
    assert.equal(ui.data.length,1);assert.equal(ui.data[0].storage_id,'c');
    assert.equal(ui.q('[data-empty]').disabled,true);
    assert.match(ui.q('[data-notice]').textContent,/영구 삭제했습니다/);
  });
  test(`${mode}: individual permanent deletion never includes another project`,async t=>{
    const initial=[row('a','Alpha'),row('b','Beta')];initial.forEach(p=>p.archived_at='2026-10-17T12:00:00Z');
    const ui=setup(t,program,{initial});await ui.open();ui.q('[data-tab="trash"]').click();await flush();
    ui.w.prompt=()=> '영구 삭제';ui.action('a','purge');await flush();
    assert.equal(ui.data.length,1);assert.equal(ui.data[0].storage_id,'b');
  });
  test(`${mode}: superseded search response cannot replace newer results`,async t=>{
    const ui=setup(t,program);await ui.open();let resolve;
    ui.hold(new Promise(r=>{resolve=r;}));
    ui.q('[data-search]').value='Alpha';ui.q('[data-search]').dispatchEvent(new ui.w.Event('input'));
    await new Promise(r=>setTimeout(r,280));await flush();
    ui.hold(null);
    ui.q('[data-search]').value='Beta';ui.q('[data-search]').dispatchEvent(new ui.w.Event('input'));
    await new Promise(r=>setTimeout(r,280));await flush();
    assert.equal(ui.q('[data-name]').value,'Beta');
    resolve();await flush();
    assert.equal(ui.q('[data-name]').value,'Beta');
    assert.equal(ui.q('[data-select-all]').checked,false);
  });
}

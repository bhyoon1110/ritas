// Execute the mounted shell, including the emitted fallback bundle. JSDOM is
// event/request regression coverage, not a substitute for Supermium/XP UI QA.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM, VirtualConsole} = require('jsdom');
const esbuild = require('esbuild');
const acorn = require('acorn');
const root = path.resolve(__dirname, '../../app/xrd_workbench/web/shared');
const programs = {
  native: esbuild.buildSync({entryPoints:[path.join(root, 'shell.mjs')], bundle:true,
    write:false, format:'iife', logLevel:'silent'}).outputFiles[0].text,
  chrome49: fs.readFileSync(path.join(root, 'shell.legacy.js'), 'utf8'),
};
const flush = async () => { for (let i=0; i<6; i++) await new Promise(setImmediate); };
const response = payload => ({ok:true, json:async () => payload});
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((a, b) => {resolve=a; reject=b;});
  return {promise, resolve, reject};
};

function setup(t, mode, handler, project='') {
  const errors=[], calls=[], alerts=[];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => errors.push(error));
  const dom = new JSDOM('<body data-step="viewer"><main>viewer</main></body>', {
    url:'http://rist.test/api/v1/xrd/workbench/'+(project ? '?project='+project : ''),
    runScripts:'outside-only', virtualConsole,
  });
  const w=dom.window, d=w.document;
  t.after(() => w.close());
  w.XRD_API_BASE='/api/v1/xrd/workbench';
  w.fetch=async (url, options) => {
    calls.push({url, options});
    const relative=url.replace(w.XRD_API_BASE, '');
    return handler(relative.startsWith('/api/projects?') ? '/api/projects' : relative, options);
  };
  w.alert=message => alerts.push(message);
  acorn.parse(programs[mode], {ecmaVersion:mode==='chrome49' ? 5 : 2022});
  w.eval(programs[mode]);
  const change = project => w.dispatchEvent(new w.CustomEvent('xrd-shell', {detail:{project}}));
  return {w, d, calls, alerts, errors, change, select:d.getElementById('shellProject')};
}

for (const mode of Object.keys(programs)) {
  test(`${mode}: mounted project manager opens active and trash lists`, async t => {
    const ui=setup(t,mode,url=>response(url.includes('/api/project-catalog')?{items:[{storage_id:'saved',label:'Saved',managed_revision:0}],page:1,pages:1,total:1,total_in_tab:1}:url.includes('/api/projects')?[{storage_id:'saved',label:'Saved',managed_revision:0}]:{document:{revision:0}}),'saved');
    await flush();
    ui.d.getElementById('shellProjectManage').click(); await flush();
    const modal=ui.d.querySelector('.xrdProjectManager');
    assert.equal(modal.hidden,false);
    assert.equal(modal.querySelector('[data-name]').value,'Saved');
    modal.querySelector('[data-tab="trash"]').click(); await flush();
    assert.ok(ui.calls.some(c=>c.url.includes('/api/project-catalog?archived=true')));
    modal.querySelector('[data-close]').click();
    assert.equal(modal.hidden,true);
    assert.deepEqual(ui.errors,[]);
  });
  test(`${mode}: reset is visible and cancellation never changes current data`, async t => {
    const ui=setup(t,mode,url=>response(url==='/api/projects'?[{storage_id:'saved',label:'Saved'}]:{document:{revision:0}}),'saved');
    await flush();
    ui.w.localStorage.setItem('keep-report','draft');
    ui.w.confirm=()=>false;
    const button=ui.d.getElementById('shellReset');
    assert.equal(button.textContent,'초기화');
    button.click();await flush();
    assert.equal(ui.select.value,'saved');
    assert.equal(ui.w.localStorage.getItem('keep-report'),'draft');
    assert.equal(button.disabled,false);
    assert.ok(ui.calls.every(c=>!c.options?.method||c.options.method==='GET'));
    assert.deepEqual(ui.errors,[]);
  });
  test(`${mode}: newly imported project is selected without reload`, async t => {
    let projects=[];
    const ui=setup(t, mode, async url => response(url==='/api/projects' ? projects : {document:{revision:0}}));
    await flush();
    assert.match(ui.select.textContent, /등록된 프로젝트 없음/);
    projects=[{storage_id:'new-upload',label:'SampleA, SampleB'}];
    ui.change('new-upload');
    assert.equal(ui.select.value, 'new-upload'); // selected even while fetching
    await flush();
    assert.equal(ui.select.value, 'new-upload');
    assert.equal(ui.select.selectedOptions[0].textContent, 'SampleA, SampleB');
    assert.equal(ui.w.location.search, '?project=new-upload');
    assert.equal(ui.calls.filter(c=>c.url.includes('/api/projects?limit=20')).length, 2);
    assert.deepEqual(ui.errors, []);
  });

  test(`${mode}: slow initial and superseded lists cannot replace the newest project`, async t => {
    const pending=[];
    const ui=setup(t, mode, url => {
      if (url!=='/api/projects') return response({document:{revision:0}});
      const wait=deferred(); pending.push(wait); return wait.promise;
    });
    ui.change('first');
    ui.change('second');
    pending[2].resolve(response([{storage_id:'second',label:'Second'}]));
    await flush();
    pending[1].resolve(response([{storage_id:'first',label:'First'}]));
    pending[0].resolve(response([]));
    await flush();
    assert.equal(ui.select.value, 'second');
    assert.equal(ui.select.textContent, 'Second');
    assert.deepEqual(ui.errors, []);
  });

  test(`${mode}: refresh failure preserves choices and retries on focus`, async t => {
    let count=0;
    const ui=setup(t, mode, url => {
      if (url!=='/api/projects') return response({document:{revision:0}});
      count++;
      if (count===2) throw new Error('offline');
      return response(count===1 ? [{storage_id:'old',label:'Old'}] : [
        {storage_id:'old',label:'Old'}, {storage_id:'new',label:'New <sample>'}]);
    }, 'old');
    await flush();
    ui.change('new');
    await flush();
    assert.equal(ui.select.value, 'new');
    assert.ok([...ui.select.options].some(option => option.value==='old'));
    ui.select.dispatchEvent(new ui.w.Event('focus'));
    await flush();
    assert.equal(ui.select.value, 'new');
    assert.equal(ui.select.selectedOptions[0].textContent, 'New <sample>');
    assert.equal(count, 3);
    assert.deepEqual(ui.errors, []);
  });

  test(`${mode}: late checklist for an old project cannot mark the current project blocked`, async t => {
    const stale=deferred();
    const ui=setup(t, mode, url => {
      if (url==='/api/projects') return response([{storage_id:'old',label:'Old'}]);
      if (url.includes('/checklist')) return stale.promise;
      return response({document:{revision:url.endsWith('/old') ? 1 : 0}});
    }, 'old');
    await flush();
    ui.change('new');
    await flush();
    stale.resolve(response({summary:{blockers:5,warnings:0}}));
    await flush();
    assert.equal(ui.d.querySelectorAll('.shellBadge').length, 0);
    assert.equal(ui.d.getElementById('shellState').textContent, '원자료 보존 · 단계별 작업');
    assert.deepEqual(ui.errors, []);
  });

  test(`${mode}: mounted member menu exposes account, SSO, VOC and protected logout`, async t => {
    const ui=setup(t, mode, url => url==='/api/v1/auth/logout'
      ? {ok:false} : response([]));
    await flush();
    const menu=ui.d.getElementById('shellAccount');
    assert.equal(menu.hidden, true);
    ui.d.getElementById('shellAccountToggle').click();
    assert.equal(menu.hidden, false);
    assert.equal(ui.d.getElementById('shellAccountToggle').getAttribute('aria-expanded'), 'true');
    assert.match(menu.querySelector('a[href="/account"]').textContent, /SSO/);
    assert.equal(menu.querySelector('a[href="/voc?project=XRD"]').textContent, 'VOC 게시판');
    ui.d.getElementById('ristLogout').click();
    await flush();
    const logout=ui.calls.find(c=>c.url==='/api/v1/auth/logout');
    assert.equal(logout.options.method, 'POST');
    assert.equal(logout.options.credentials, 'same-origin');
    assert.equal(logout.options.headers['X-Requested-With'], 'RIST-XRD');
    assert.equal(ui.alerts.length, 1); // failed logout must not pretend to succeed
    assert.equal(ui.w.location.pathname, '/api/v1/xrd/workbench/');
    ui.d.getElementById('shellAccountToggle').click();
    assert.equal(menu.hidden, true);
    assert.deepEqual(ui.errors, []);
  });
}

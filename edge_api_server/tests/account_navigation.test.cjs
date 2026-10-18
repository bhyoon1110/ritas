// Run the actual workspace auth programs, both native and emitted Chrome 49 JS.
// JSDOM covers events and requests, not XP/Supermium binaries or visual layout.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {createHash} = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM, VirtualConsole} = require('jsdom');
const acorn = require('acorn');

const pages = JSON.parse(execFileSync(process.env.RIST_TEST_PYTHON || 'python3', ['-c', `
import json
from app.preview_web import build_workspace_index
from app.ftir_web import build_ftir_page
from app.raman_web import build_raman_page
from app.xrd_web import build_xrd_page
from app.ahn_web import build_ahn_page
print(json.dumps({"workspace":build_workspace_index(),"ftir":build_ftir_page(),
    "raman":build_raman_page(),"xrd":build_xrd_page(),"ahn":build_ahn_page()}))
`], {encoding:'utf8', maxBuffer:32*1024*1024}));
const assetRoot = path.resolve(__dirname, '../app/static/chrome49');
const manifest = JSON.parse(fs.readFileSync(path.join(assetRoot, 'manifest.json'), 'utf8'));
const response = (payload, status=200) => ({ok:status<400, status, json:async()=>payload});
const flush = async () => { await new Promise(setImmediate); await new Promise(setImmediate); };

function setup(t, name, mode, handler) {
  const errors = [], alerts = [], calls = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => errors.push(error));
  const dom = new JSDOM(pages[name], {url:'http://rist.test/', runScripts:'outside-only', virtualConsole});
  const w = dom.window, d = w.document;
  t.after(() => w.close());
  // These two programs initialize auth before their analysis-controller guard.
  const plot = d.getElementById(name === 'ftir' ? 'peak-plot' : 'raman-plot');
  if (plot) plot.remove();
  const source = Array.from(d.scripts).find(script => script.textContent.includes('fetch("/api/v1/auth/logout"')).textContent.trim();
  let program = source;
  if (mode === 'chrome49') {
    const key = createHash('sha256').update(source).digest('hex');
    assert.ok(manifest.scripts[key], `${name}: rebuild compatibility assets`);
    program = fs.readFileSync(path.join(assetRoot, manifest.scripts[key]), 'utf8');
    for (const style of d.querySelectorAll('style')) {
      const styleKey = createHash('sha256').update(style.textContent).digest('hex');
      if (manifest.styles[styleKey]) style.textContent = manifest.styles[styleKey];
    }
  }
  acorn.parse(program, {ecmaVersion:mode === 'chrome49' ? 5 : 2022});
  w.fetch = async (url, options) => { calls.push({url, options}); return handler(url, options); };
  w.alert = message => alerts.push(message);
  const controls = {
    account:name === 'workspace' ? d.querySelector('#workspace-account-actions a') : d.getElementById(name+'-account-link'),
    accountGroup:d.getElementById('workspace-account-actions'),
    admin:d.getElementById(name+'-admin-link'),
    logout:d.getElementById(name+'-logout'),
  };
  assert.ok(controls.account); assert.ok(controls.admin); assert.ok(controls.logout);
  assert.equal(controls.account.getAttribute('href'), '/account');
  if (name !== 'workspace') {
    assert.equal(d.getElementById(name+'-home').getAttribute('href'), '/');
    assert.equal(d.getElementById(name+'-home').hidden, false);
    assert.equal(controls.account.target, '_blank');
    assert.equal(controls.account.rel, 'noopener');
  }
  const hidden = () => name === 'workspace' ? controls.accountGroup.hidden : controls.account.hidden;
  assert.equal(hidden(), true);
  w.eval(program);
  return {w, ...controls, hidden, alerts, errors, calls};
}

for (const mode of ['native', 'chrome49']) for (const name of Object.keys(pages)) {
  for (const roles of [[], ['ADMIN'], 'ADMIN']) test(`${mode} ${name}: account menu and role-gated operations (${JSON.stringify(roles)})`, async t => {
    const ui = setup(t, name, mode, async () => response({roles}));
    await flush();
    assert.equal(ui.hidden(), false);
    assert.equal(ui.logout.hidden, false);
    assert.equal(ui.admin.hidden, !(Array.isArray(roles) && roles.includes('ADMIN')));
    assert.equal(ui.calls.filter(call=>call.url === '/api/v1/auth/me').length, 1);
    assert.equal(ui.calls[0].options.credentials, 'same-origin');
    // The project grid fallback must not constrain account navigation to 1/3 width.
    if (name === 'workspace') assert.equal(ui.w.getComputedStyle(ui.account).maxWidth, '');
    assert.deepEqual(ui.errors, []);
  });

  for (const failure of ['401', 'network', 'bad-json']) test(`${mode} ${name}: auth ${failure} keeps privileged and account controls hidden`, async t => {
    const ui = setup(t, name, mode, async () => {
      if (failure === 'network') throw new Error('offline');
      if (failure === 'bad-json') return {ok:true, json:async()=>{throw new Error('invalid JSON');}};
      return response({}, 401);
    });
    await flush();
    assert.equal(ui.hidden(), true);
    assert.equal(ui.admin.hidden, true);
    if (name !== 'workspace') assert.equal(ui.logout.hidden, true);
    assert.deepEqual(ui.errors, []);
  });

  test(`${mode} ${name}: logout retries failures, blocks double submit, and navigates only on success`, async t => {
    let resolvePending, attempts = 0;
    const ui = setup(t, name, mode, async url => {
      if (url === '/api/v1/auth/me') return response({roles:[]});
      assert.equal(url, '/api/v1/auth/logout');
      if (++attempts === 1) return response({}, 500);
      if (attempts === 2) throw new Error('offline');
      return new Promise(resolve => { resolvePending = resolve; });
    });
    await flush();
    ui.logout.click(); await flush();
    assert.equal(ui.logout.disabled, false);
    assert.equal(ui.alerts.length, 1);
    assert.deepEqual(ui.errors, []);
    ui.logout.click(); await flush();
    assert.equal(ui.logout.disabled, false);
    assert.equal(ui.alerts.length, 2);
    assert.deepEqual(ui.errors, []);
    ui.logout.click();
    assert.equal(ui.logout.disabled, true);
    ui.logout.dispatchEvent(new ui.w.Event('click'));
    assert.equal(attempts, 3);
    const post = ui.calls.filter(call => call.url === '/api/v1/auth/logout');
    assert.equal(post.length, 3);
    for (const call of post) {
      assert.equal(call.options.method, 'POST');
      assert.equal(call.options.credentials, 'same-origin');
      assert.equal(call.options.headers['X-Requested-With'], 'RIST-Project');
    }
    resolvePending(response({loggedOut:true})); await flush();
    assert.equal(ui.alerts.length, 2);
    // JSDOM reports full-page navigation instead of performing it.
    assert.equal(ui.errors.length, 1);
    assert.match(ui.errors[0].message, /navigation/);
    assert.equal(ui.logout.disabled, true);
  });
}

const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const esbuild = require('esbuild');

const app = path.resolve(__dirname, '../../app');
const web = path.join(app, 'xrd_workbench/web');
const bundle = esbuild.buildSync({entryPoints:[path.join(web, 'shared/platform.mjs')],
  bundle:true, format:'iife', globalName:'Platform', write:false}).outputFiles[0].text;

async function openPicker(t, pages) {
  const dom = new JSDOM(fs.readFileSync(path.join(web, 'report.html'), 'utf8'), {runScripts:'outside-only'});
  t.after(() => dom.window.close());
  const w = dom.window, d = w.document, byId = id => d.getElementById(id);
  const queries = [], sends = [];
  w.eval(fs.readFileSync(path.join(app, 'request_filters.js'), 'utf8'));
  w.eval(fs.readFileSync(path.resolve(app, '../../common/rist_common/plotting/file_progress.js'), 'utf8'));
  w.eval(bundle);
  w.fetch = async url => {
    if (url === '/api/platform') return {ok:true, json:async () => ({operatorId:'op'})};
    const request = new URL(url, 'https://edge.invalid');
    assert.equal(request.pathname, '/api/v1/requests');
    queries.push(request.searchParams);
    const page = Number(request.searchParams.get('page'));
    return {ok:true, json:async () => ({items:pages[page - 1], page, pageSize:200, totalCount:201})};
  };
  await w.Platform.mountReportPlatform(() => sends.push(w.Platform.transferValues()), () => {});
  const field = label => d.querySelector('[aria-label="' + label + '"]');
  function change(element, value) {
    element.value = value;
    element.dispatchEvent(new w.Event('change', {bubbles:true}));
  }
  return {d, w, byId, field, queries, sends, change};
}

test('XRD workbench uses live v5 filters and paging and selects the actual test method', async t => {
  const ui = await openPicker(t, [[{requestNumber:'R-1', testMethodCode:'A', testState:'1'}],
    [{requestNumber:'R-2', testMethodCode:'M-2', testMethodName:'회절 측정', testState:7} ]]);
  const {byId, field, change, queries, sends} = ui;
  change(field('조회 대상'), 'lims');
  field('장비 자산번호').value = 'AS-120';
  await byId('ristRequestLookup').onclick();
  assert.deepEqual([queries[0].get('source'), queries[0].get('assetNumber'), queries[0].get('testState')],
    ['lims','AS-120','1,3,5,7']);
  assert.ok(!queries[0].has('experimentType'));
  const next = Array.from(ui.d.querySelectorAll('.rist-request-filters button')).find(b => b.textContent === '다음');
  assert.equal(next.disabled, false);
  next.click();
  await new Promise(setImmediate);
  assert.equal(queries[1].get('page'), '2');
  assert.match(byId('ristRequests').options[1].textContent, /M-2 · 회절 측정 · 시험중/);
  change(byId('ristRequests'), '0');
  assert.equal(byId('ristEquipment').value, '');
  byId('ristEquipment').value = 'XRD-17';
  await byId('ristReportSend').onclick();
  assert.equal(sends[0].experimentCode, 'M-2');
  assert.equal(sends[0].equipmentCode, 'XRD-17');
  assert.equal(sends[0].requestNumber, 'R-2');
  byId('reportNumber').value = 'MANUAL';
  byId('reportNumber').dispatchEvent(new ui.w.Event('input'));
  assert.equal(byId('ristExperiment').value, '');
});

test('missing method blocks sending while changing requests preserves the user-entered equipment', async t => {
  const {byId, field, change, sends} = await openPicker(t, [[{requestNumber:'R-3', sampleName:'S'}]]);
  assert.equal(byId('ristExperiment').value, '');
  byId('ristExperiment').value = 'old-draft-method';
  byId('ristEquipment').value = 'XRD-7';
  change(field('조회 대상'), 'lims');
  await byId('ristRequestLookup').onclick();
  change(byId('ristRequests'), '0');
  assert.equal(byId('ristExperiment').value, '');
  assert.equal(byId('ristEquipment').value, 'XRD-7');
  await byId('ristReportSend').onclick();
  assert.equal(sends.length, 0);
  assert.match(byId('ristTransferStatus').textContent, /시험방법코드/);
});

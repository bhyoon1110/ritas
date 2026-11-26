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
  w.eval(fs.readFileSync(path.join(app, 'request_picker.js'), 'utf8'));
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
  const ui = await openPicker(t, [[{requestNumber:'R-1', testMethodCode:'A',axTestMtdCode:'A', testState:'1'}],
    [{requestNumber:'R-2', testMethodCode:'M-2',axTestMtdCode:'M-2', testMethodName:'회절 측정', testState:7} ]]);
  const {byId, field, change, queries, sends} = ui;
  change(field('조회 대상'), 'lims');
  field('장비 자산번호').value = 'AS-120';
  await byId('ristRequestLookup').onclick();
  assert.deepEqual([queries[0].get('source'), queries[0].get('assetNumber'), queries[0].get('testState')],
    ['lims','AS-120','1,3,5,7']);
  assert.equal(queries[0].get('experimentType'), 'XRD');
  const next = Array.from(ui.d.querySelectorAll('.rist-request-filters button')).find(b => b.textContent === '다음');
  assert.equal(next.disabled, false);
  next.click();
  await new Promise(setImmediate);
  assert.equal(queries[1].get('page'), '2');
  assert.match(byId('rist-request-result-0').textContent, /회절 측정M-2/);
  byId('rist-request-result-0').click();
  assert.equal(byId('ristRequestNumber').value, 'R-2');
  assert.equal(byId('ristEquipment').value, '');
  byId('ristEquipment').value = 'XRD-17';
  await byId('ristReportSend').onclick();
  assert.equal(sends[0].experimentCode, 'M-2');
  assert.equal(sends[0].equipmentCode, 'XRD-17');
  assert.equal(sends[0].requestNumber, 'R-2');
  byId('ristRequestNumber').value = 'MANUAL';
  byId('ristRequestNumber').dispatchEvent(new ui.w.Event('input', {bubbles:true}));
  assert.equal(byId('reportNumber').value, 'MANUAL');
  assert.equal(byId('ristExperiment').value, '');
  byId('reportNumber').value = 'COVER-EDIT';
  byId('reportNumber').dispatchEvent(new ui.w.Event('input'));
  assert.equal(byId('ristRequestNumber').value, 'COVER-EDIT');
});

test('missing method blocks sending while changing requests preserves the user-entered equipment', async t => {
  const {byId, field, change, sends} = await openPicker(t, [[{requestNumber:'R-3', sampleName:'S'}]]);
  assert.equal(byId('ristExperiment').value, '');
  byId('ristExperiment').value = 'old-draft-method';
  byId('ristEquipment').value = 'XRD-7';
  change(field('조회 대상'), 'lims');
  await byId('ristRequestLookup').onclick();
  byId('rist-request-result-0').click();
  assert.equal(byId('ristExperiment').value, '');
  assert.equal(byId('ristEquipment').value, 'XRD-7');
  await byId('ristReportSend').onclick();
  assert.equal(sends.length, 0);
  assert.match(byId('ristSendStatus').textContent, /시험방법코드/);
});

test('lookup shows the server scope guidance and clears stale selections on failure', async t => {
  const {w, byId} = await openPicker(t, [[{requestNumber:'R-1', testMethodCode:'A',axTestMtdCode:'A'}]]);
  await byId('ristRequestLookup').onclick();
  assert.equal(byId('rist-request-results').querySelectorAll('[role=option]').length, 1);
  w.fetch = async () => ({ok:false, json:async () => ({code:'REQUEST_SCOPE_TOO_BROAD',message:'기간으로 조회 범위를 좁혀 주세요.'})});
  await byId('ristRequestLookup').onclick();
  assert.equal(byId('rist-request-results').querySelectorAll('[role=option]').length, 0);
  assert.match(byId('rist-request-info').textContent, /기간으로 조회 범위/);
  assert.equal(byId('ristRequestLookup').disabled, false);
});


test('XRD ALL selection retains actual non-XRD method and removes stale cross-experiment intent', async t => {
  const ui=await openPicker(t, [[{requestNumber:'2022T00793',testMethodCode:'A11112',axTestMtdCode:'A11112',testMethodName:'부품가공 (판형)',testState:5}]]);
  const {w,byId,field,change,queries,sends}=ui;
  change(field('조회 대상'),'lims');
  field('장비 자산번호').value='100000128';
  change(field('조회 범위'),'all');
  assert.match(ui.d.querySelector('.rist-request-filters').textContent,/직접 입력한.*조건은 유지/);
  await byId('ristRequestLookup').onclick();
  assert.equal(queries.at(-1).has('experimentType'),false);
  assert.equal(queries.at(-1).get('assetNumber'),'100000128');
  assert.equal(queries.at(-1).has('testMtdCode'),false);
  byId('rist-request-result-0').click();
  byId('ristEquipment').value='100000128';
  await byId('ristReportSend').onclick();
  assert.equal(sends[0].requestScope,'all');
  assert.equal(sends[0].experimentCode,'A11112');
  assert.equal(sends[0].requestNumber,'2022T00793');
  assert.match(byId('ristTransferStatus').textContent,/XRD 보고서를 선택한 의뢰와 실제 시험방법코드/);

  // Editing the method invalidates the ALL selection, even if the old value is restored.
  byId('ristExperiment').value='OTHER';
  byId('ristExperiment').dispatchEvent(new w.Event('input'));
  byId('ristExperiment').value='A11112';
  assert.equal(w.Platform.transferValues().requestScope,undefined);
  assert.equal(byId('rist-request-detail').hidden,true);
  await byId('ristRequestLookup').onclick();byId('rist-request-result-0').click();
  assert.equal(w.Platform.transferValues().requestScope,'all');

  // Merely switching back to ALL cannot resurrect the previous selection.
  change(field('조회 범위'),'current');
  assert.equal(w.Platform.transferValues().requestScope,undefined);
  change(field('조회 범위'),'all');
  assert.equal(w.Platform.transferValues().requestScope,undefined);
  await byId('ristRequestLookup').onclick();byId('rist-request-result-0').click();
  byId('ristRequestNumber').value='MANUAL';
  byId('ristRequestNumber').dispatchEvent(new w.Event('input'));
  assert.equal(w.Platform.transferValues().requestScope,undefined);
});

test('XRD applies the selected AX code verbatim and clears it on search-mode changes', async t => {
  const ui=await openPicker(t, [[{requestNumber:'2022T00793',testMethodCode:'A11111',experimentCode:'A11111',axTestMtdCode:'2022A11111',equipmentCode:'EQ'}]]);
  const {byId,field,change,sends}=ui;
  change(field('조회 대상'),'lims');await byId('ristRequestLookup').onclick();byId('rist-request-result-0').click();
  assert.equal(byId('ristExperiment').value,'2022A11111');assert.match(byId('rist-request-detail').textContent,/시험방법코드A11111/);
  await byId('ristReportSend').onclick();assert.equal(sends[0].experimentCode,'2022A11111');
  change(field('검색 방식'),'CONTAINS');assert.equal(byId('ristExperiment').value,'');assert.equal(byId('rist-request-detail').hidden,true);
  await byId('ristReportSend').onclick();assert.equal(sends.length,1);
});

test('XRD does not send a display code when the AX registration code is absent',async t=>{
  const {byId,sends}=await openPicker(t, [[{requestNumber:'2022T00793',requestDate:'2022-11-23',testMethodCode:'A11111',experimentCode:'A11111',equipmentCode:'EQ'}]]);
  await byId('ristRequestLookup').onclick();byId('rist-request-result-0').click();
  assert.equal(byId('ristExperiment').value,'');assert.match(byId('ristTransferStatus').textContent,/전송용 시험방법코드가 제공되지/);
  await byId('ristReportSend').onclick();assert.equal(sends.length,0);
});

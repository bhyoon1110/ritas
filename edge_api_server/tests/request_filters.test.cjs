const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const sandbox = { window: {} };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../app/request_filters.js'), 'utf8'), sandbox);
const filters = sandbox.window.RistRequestFilters;

test('cache query preserves experiment filter and completion policy', () => {
  const url = new URL(filters.buildQuery({ source: 'cache', reqNumber: 'REQ+한글', testChargerId: 'ignored', assetNumber: 'live-only' }, 'FT-IR', 2, 200), 'https://edge.test');
  assert.equal(url.searchParams.get('page'), '2');
  assert.equal(url.searchParams.get('reqNumber'), 'REQ+한글');
  assert.equal(url.searchParams.get('experimentType'), 'FT-IR');
  assert.equal(url.searchParams.get('testState'), '1,3,5,7');
  assert.equal(url.searchParams.has('assetNumber'), false);
  assert.equal(url.searchParams.has('testChargerId'), false);
  assert.match(filters.buildQuery({source: 'cache', testState: ''}, 'FT-IR', 1, 200), /includeCompleted=true/);
});

test('live query forwards independent asset, state and method filters', () => {
  const values = { source: 'lims', reqNumber: 'R1', testMtdCode: 'A23142', fromDate: '2026-10-01', toDate: '2026-10-31', testChargerId: 'hong', testChargerNo: '9223372036854775807', assetNumber: '장비/1+2', testState: '0,9' };
  const url = new URL(filters.buildQuery(values, 'XRD', 1, 200), 'https://edge.test');
  ['reqNumber', 'testMtdCode', 'fromDate', 'toDate', 'testChargerId', 'testChargerNo', 'assetNumber', 'testState'].forEach(key => assert.equal(url.searchParams.get(key), values[key]));
  assert.equal(url.searchParams.get('experimentType'), 'XRD');
  assert.equal(url.searchParams.has('includeCompleted'), false);
  const device = new URL(filters.buildQuery({source: 'lims', assetNumber: 'A-01'}, 'XRD', 1, 200), 'https://edge.test');
  assert.equal(device.searchParams.get('assetNumber'), 'A-01');
  assert.equal(device.searchParams.get('testState'), '1,3,5,7');
  assert.equal(device.searchParams.has('testMtdCode'), false);
  assert.throws(() => filters.buildQuery({ fromDate: '2026-11-01', toDate: '2026-10-01' }, '', 1, 200), /시작일/);
});

test('mounted controls page through totalCount and reset on changed filters', () => {
  const byLabel = {}, all = [];
  const document = { createElement(tag) {
    const element = { tag, children: [], style: {}, value: '', checked: false, events: {}, ownerDocument: document,
      appendChild(child) { this.children.push(child); child.parentNode = this; if (this.tag === 'select' && !this.value) this.value = child.value; },
      insertBefore(child, anchor) { this.children.splice(this.children.indexOf(anchor), 0, child); child.parentNode = this; },
      setAttribute(key, value) { if (key === 'aria-label') byLabel[value] = this; },
      addEventListener(key, handler) { this.events[key] = handler; }
    };
    all.push(element); return element;
  }};
  const parent = document.createElement('div'), anchor = document.createElement('select'); parent.appendChild(anchor);
  let reloads = 0;
  const panel = filters.mount(anchor, { experimentType: 'XRD', reload() { reloads++; } });
  panel.showResult({ page: 1, pageSize: 200, totalCount: 401, items: [] });
  const next = all.find(el => el.textContent === '다음'), previous = all.find(el => el.textContent === '이전');
  assert.equal(previous.disabled, true); assert.equal(next.disabled, false);
  next.events.click(); assert.match(panel.url(), /page=2&/); assert.equal(reloads, 1);
  panel.showResult({ page: 3, pageSize: 200, totalCount: 401, items: [{}] });
  assert.equal(next.disabled, true);
  byLabel['검색할 의뢰번호'].value = 'R1'; byLabel['검색할 의뢰번호'].events.change();
  assert.match(panel.url(), /page=1&/);
  assert.equal(byLabel['장비 자산번호'].disabled, true);
  byLabel['조회 대상'].value = 'lims'; byLabel['조회 대상'].events.change();
  assert.equal(byLabel['시험담당자 로그인 ID'].disabled, false);
  assert.equal(byLabel['장비 자산번호'].disabled, false);
  assert.equal(byLabel['시험 상태'].disabled, false);
  byLabel['장비 자산번호'].value = 'LIMS-ASSET-42'; byLabel['시험 상태'].value = '0';
  const cancelled = new URL(panel.url(), 'https://edge.test');
  assert.equal(cancelled.searchParams.get('testState'), '0');
  assert.equal(cancelled.searchParams.get('assetNumber'), 'LIMS-ASSET-42');
  panel.setBusy(true); assert.equal(byLabel['조회 대상'].disabled, true);
  assert.equal(previous.disabled, true); assert.equal(next.disabled, true);
});

for (const experiment of ['FT-IR','RAMAN','XRD','TEM']) for (const source of ['cache','lims']) {
  test(source+' scopes '+experiment+' without requiring manual filters',()=>{
    const url=new URL(filters.buildQuery({source},experiment,1,200),'https://edge.test');
    assert.equal(url.searchParams.get('experimentType'),experiment);
    assert.equal(url.searchParams.get('testState'),'1,3,5,7');
    assert(!url.searchParams.has('assetNumber'));assert(!url.searchParams.has('testMtdCode'));
  });
}

test('all-status search remains unfiltered for cache and live experiment scopes',()=>{
  for(const source of ['cache','lims']){
    const url=new URL(filters.buildQuery({source,testState:''},'TEM',1,200),'https://edge.test');
    assert.equal(url.searchParams.get('includeCompleted'),'true');
    assert(!url.searchParams.has('testState'));
  }
});


test('ALL removes only automatic experiment scope and retains explicit search conditions', () => {
  for (const experiment of ['FT-IR','RAMAN','XRD','TEM']) {
    for (const source of ['cache','lims']) {
      const values = {source, requestScope:'all', reqNumber:'2022T00793', testMtdCode:'A11112',
        assetNumber:'100000128', fromDate:'2022-01-01', toDate:'2022-12-31', testState:'5'};
      const query = new URL(filters.buildQuery(values,experiment,1,200),'https://edge.test').searchParams;
      assert.equal(query.has('experimentType'), false);
      for (const key of ['reqNumber','testMtdCode','fromDate','toDate','testState']) assert.equal(query.get(key),values[key]);
      assert.equal(query.get('assetNumber'), source === 'lims' ? values.assetNumber : null);
      assert.equal(new URL(filters.buildQuery({...values,requestScope:'current'},experiment,1,200),'https://edge.test').searchParams.get('experimentType'),experiment);
    }
  }
  const allStates = new URL(filters.buildQuery({source:'lims',requestScope:'all',testState:''},'XRD',1,200),'https://edge.test').searchParams;
  assert.equal(allStates.get('includeCompleted'),'true');
  for (const key of ['experimentType','testMtdCode','assetNumber','testState']) assert.equal(allStates.has(key),false);
});

test('display code and AX registration code stay distinct without inferred years or display fallback', () => {
  const row={requestNumber:'2022T00793',requestDate:'2026-11-26',testMethodCode:'A11111',experimentCode:'A11111',axTestMtdCode:' 2022A11111 '};
  assert.equal(filters.displayMethodCode(row),'A11111');
  assert.equal(filters.transferMethodCode(row),'2022A11111');
  for(const axTestMtdCode of [undefined,null,'',123]) assert.equal(filters.transferMethodCode({...row,axTestMtdCode}),'');
  assert.equal(filters.displayMethodCode({testMtdCode:'A11111'}),'A11111');
});

test('partial search forwards literal code fragments, keeps paging and validates each entered code', () => {
  const values={source:'lims',matchMode:'CONTAINS',reqNumber:'T00',testMtdCode:'A11',assetNumber:'%_\\'};
  const query=new URL(filters.buildQuery(values,'XRD',3,200),'https://edge.test').searchParams;
  assert.equal(query.get('matchMode'),'CONTAINS');assert.equal(query.get('page'),'3');
  for(const key of ['reqNumber','testMtdCode','assetNumber']) assert.equal(query.get(key),values[key]);
  for(const key of ['reqNumber','testMtdCode','assetNumber']) assert.throws(()=>filters.buildQuery({...values,[key]:'A1'},'XRD',1,200),/3자/);
  assert.throws(()=>filters.buildQuery({...values,reqNumber:'😀😀'},'XRD',1,200),/3자/);
  assert.throws(()=>filters.buildQuery({...values,source:'cache'},'XRD',1,200),/LIMS 실시간/);
  assert.throws(()=>filters.buildQuery({...values,matchMode:'BAD'},'XRD',1,200),/검색/);
  assert(!new URL(filters.buildQuery({source:'lims',reqNumber:'A'},'XRD',1,200),'https://edge.test').searchParams.has('matchMode'));
  assert.match(filters.buildQuery({source:'lims',matchMode:'CONTAINS',fromDate:'2022-01-01'},'XRD',1,200),/matchMode=CONTAINS/);
});

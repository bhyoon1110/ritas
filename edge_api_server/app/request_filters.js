(function (global) {
  'use strict';

  var stateNames = {'0': '시험취소', '1': '시험의뢰대기', '3': '시험의뢰', '5': '시험접수', '7': '시험중', '9': '시험완료'};

  function testStateName(code) {
    if (code == null) return '';
    return Object.prototype.hasOwnProperty.call(stateNames, code) ? stateNames[code] : String(code);
  }

  function buildQuery(values, experimentType, page, pageSize) {
    var source = values.source === 'lims' ? 'lims' : 'cache';
    var params = ['source=' + source, 'page=' + page, 'pageSize=' + pageSize];
    var keys = ['reqNumber', 'testMtdCode', 'fromDate', 'toDate'];
    var states = values.testState === undefined ? '1,3,5,7' : values.testState;
    if (states) params.push('testState=' + encodeURIComponent(states));
    if (source === 'lims') {
      keys.push('testChargerId', 'testChargerNo', 'assetNumber');
    } else {
      params.push('experimentType=' + encodeURIComponent(experimentType || ''));
      if (!states) params.push('includeCompleted=true');
    }
    if (values.fromDate && values.toDate && values.fromDate > values.toDate) {
      throw new Error('조회 시작일은 종료일보다 늦을 수 없습니다.');
    }
    keys.forEach(function (key) {
      var value = String(values[key] || '').replace(/^\s+|\s+$/g, '');
      if (value) params.push(key + '=' + encodeURIComponent(value));
    });
    return '/api/v1/requests?' + params.join('&');
  }

  function mount(anchor, options) {
    options = options || {};
    if (!anchor || !anchor.parentNode) return null;
    var doc = anchor.ownerDocument;
    var panel = doc.createElement('div');
    panel.className = 'rist-request-filters';
    panel.style.cssText = 'margin:8px 0;padding:10px;border:1px solid #cbd5e1;border-radius:6px;background:#f8fafc;color:#24344a;font-size:13px;';
    var details = doc.createElement('details');
    var summary = doc.createElement('summary');
    summary.textContent = '의뢰 검색 조건';
    details.appendChild(summary);
    var form = doc.createElement('div');
    form.style.cssText = 'display:flex;flex-wrap:wrap;margin-top:8px;';
    details.appendChild(form);
    panel.appendChild(details);
    var fields = {};
    function addField(key, title, type) {
      var label = doc.createElement('label');
      label.style.cssText = 'display:block;flex:1 1 150px;min-width:130px;margin-right:8px;margin-bottom:8px;';
      var name = doc.createElement('span');
      name.style.display = 'block';
      name.textContent = title;
      label.appendChild(name);
      var input = doc.createElement(type === 'select' ? 'select' : 'input');
      if (type !== 'select') input.type = type;
      input.setAttribute('aria-label', title);
      input.style.cssText = 'width:100%;box-sizing:border-box;min-height:30px;';
      input.maxLength = 200;
      label.appendChild(input);
      form.appendChild(label);
      fields[key] = input;
      return input;
    }
    var source = addField('source', '조회 대상', 'select');
    [['cache', '저장된 의뢰'], ['lims', 'LIMS 실시간']].forEach(function (entry) {
      var item = doc.createElement('option');
      item.value = entry[0]; item.textContent = entry[1]; source.appendChild(item);
    });
    addField('reqNumber', '검색할 의뢰번호', 'text');
    addField('testMtdCode', '시험방법코드', 'text');
    addField('assetNumber', '장비 자산번호', 'text');
    addField('fromDate', '의뢰일 시작', 'date');
    addField('toDate', '의뢰일 종료', 'date');
    addField('testChargerId', '시험담당자 로그인 ID', 'text');
    var chargerNo = addField('testChargerNo', '시험담당자 사용자번호', 'text');
    chargerNo.setAttribute('inputmode', 'numeric');
    chargerNo.setAttribute('pattern', '[0-9]+');
    var state = addField('testState', '시험 상태', 'select');
    [['1,3,5,7', '미완료 시험 전체'], ['', '전체 상태']].concat(
      ['1', '3', '5', '7', '9', '0'].map(function (code) { return [code, stateNames[code]]; })
    ).forEach(function (entry) {
      var item = doc.createElement('option');
      item.value = entry[0]; item.textContent = entry[1]; state.appendChild(item);
    });
    var hint = doc.createElement('p');
    hint.style.cssText = 'margin:6px 0 0;color:#526174;';
    details.appendChild(hint);
    var pager = doc.createElement('div');
    pager.style.cssText = 'display:flex;align-items:center;margin-top:7px;';
    var previous = options.previous || doc.createElement('button');
    previous.type = 'button'; previous.textContent = '이전';
    previous.style.marginRight = '8px';
    var next = options.next || doc.createElement('button');
    next.type = 'button'; next.textContent = '다음';
    next.style.marginLeft = '8px';
    var status = doc.createElement('span');
    status.setAttribute('role', 'status');
    status.style.cssText = 'flex:1;';
    status.textContent = '조건을 선택한 뒤 의뢰 조회를 눌러 주세요.';
    pager.appendChild(previous); pager.appendChild(status); pager.appendChild(next);
    panel.appendChild(pager);
    anchor.parentNode.insertBefore(panel, anchor);
    var page = 1;
    var pageSize = options.pageSize || 200;
    var total = null;
    var busy = false;
    function sync() {
      var live = source.value === 'lims';
      Object.keys(fields).forEach(function (key) { fields[key].disabled = busy; });
      fields.testChargerId.disabled = busy || !live;
      fields.testChargerNo.disabled = busy || !live;
      fields.assetNumber.disabled = busy || !live;
      previous.disabled = busy || page <= 1;
      next.disabled = busy || total === null || page * pageSize >= total;
      hint.textContent = live
        ? '특정 장비로 조회하려면 자산번호를 입력하세요. 미완료 시험은 그 장비로 수행할 수 있는 시험을 표시합니다.'
        : '현재 실험의 저장된 의뢰를 조회합니다. 자산번호·담당자 ID 검색은 LIMS 실시간 조회를 선택하세요.';
    }
    Object.keys(fields).forEach(function (key) {
      fields[key].addEventListener('change', function () {
        page = 1; total = null;
        status.textContent = '변경한 조건으로 의뢰 조회를 눌러 주세요.';
        sync();
      });
    });
    function turn(delta) {
      if (busy) return;
      page = Math.max(1, page + delta);
      sync();
      if (options.reload) options.reload();
    }
    previous.addEventListener('click', function () { turn(-1); });
    next.addEventListener('click', function () { turn(1); });
    sync();
    return {
      url: function () {
        var values = {};
        Object.keys(fields).forEach(function (key) { values[key] = fields[key].value; });
        return buildQuery(values, options.experimentType, page, pageSize);
      },
      showResult: function (payload) {
        page = Number(payload.page) || page;
        pageSize = Number(payload.pageSize) || pageSize;
        total = typeof payload.totalCount === 'number' ? payload.totalCount : null;
        var count = Array.isArray(payload.items) ? payload.items.length : 0;
        status.textContent = total === null ? count + '건 조회' : '전체 ' + total + '건 · ' + page + ' / ' + Math.max(1, Math.ceil(total / pageSize)) + '페이지';
        sync();
      },
      setBusy: function (value) { busy = !!value; sync(); }
    };
  }
  global.RistRequestFilters = { mount: mount, buildQuery: buildQuery, testStateName: testStateName };
}(typeof window !== 'undefined' ? window : this));

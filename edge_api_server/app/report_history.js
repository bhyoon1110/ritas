/* Personal report history. ES5 syntax for the existing Chrome 49 path. */
(function () {
  'use strict';
  var currentPage = 1, totalPages = 1, active = null, generation = 0, lastFilters = null;
  var labels = {FTIR: 'FT-IR', RAMAN: 'Raman', XRD: 'XRD', TEM: 'TEM'};
  function $(id) { return document.getElementById(id); }
  function node(tag, parent, text, className) {
    var element = document.createElement(tag);
    if (text != null) element.textContent = String(text);
    if (className) element.className = className;
    if (parent) parent.appendChild(element);
    return element;
  }
  function notice(message, error) {
    $('history-notice').textContent = message || '';
    $('history-notice').className = 'notice' + (error ? ' error' : '');
    $('history-notice').hidden = !message;
  }
  function displayDate(value) {
    if (!value) return '-';
    var date = new Date(value);
    if (!isFinite(date.getTime())) return String(value);
    return date.toLocaleString('ko-KR');
  }
  function size(value) {
    var bytes = Number(value);
    if (value == null || !isFinite(bytes) || bytes < 0) return '-';
    return bytes >= 1048576 ? (bytes / 1048576).toFixed(2) + ' MiB' : Math.ceil(bytes / 1024) + ' KiB';
  }
  function empty(message) {
    $('history-list').textContent = '';
    node('p', $('history-list'), message, 'empty');
  }
  function busy(value) {
    $('history-list').setAttribute('aria-busy', value ? 'true' : 'false');
    $('history-search').disabled = value;
    $('history-refresh').disabled = value;
    $('history-prev').disabled = value || currentPage <= 1;
    $('history-next').disabled = value || currentPage >= totalPages;
  }
  function clearResults(message) {
    empty(message);
    $('history-count').textContent = '조회 결과 없음';
    $('history-page').textContent = '—';
    $('history-updated').textContent = '';
    currentPage = 1; totalPages = 1;
  }
  function readFilters() {
    return {q: $('history-query').value.trim(), project: $('history-project').value,
      transferStatus: $('history-status').value, dateFrom: $('history-from').value,
      dateTo: $('history-to').value, pageSize: $('history-page-size').value};
  }
  function query(filters, page) {
    var values = [], keys = ['q', 'project', 'transferStatus', 'dateFrom', 'dateTo', 'pageSize'];
    for (var i = 0; i < keys.length; i++) values.push(encodeURIComponent(keys[i]) + '=' + encodeURIComponent(filters[keys[i]]));
    values.push('page=' + page);
    return values.join('&');
  }
  function render(payload) {
    var rows = payload.items;
    if (!Array.isArray(rows) || !isFinite(Number(payload.totalCount)) || Number(payload.totalCount) < 0) throw new Error('invalid history response');
    currentPage = Math.max(1, Number(payload.page) || 1);
    totalPages = Math.max(1, Number(payload.totalPages) || 1);
    $('history-list').textContent = '';
    for (var i = 0; i < rows.length; i++) {
      var item = rows[i], id = String(item.reportId || '');
      if (!id) throw new Error('missing report ID');
      var row = node('article', $('history-list'), null, 'report-row');
      var main = node('div', row, null, 'report-main');
      node('h3', main, item.requestNumber || '의뢰번호 미입력', 'report-title');
      node('p', main, (labels[item.analysisType] || item.analysisType || '분석 종류 미확인') + ' · 생성 ' + displayDate(item.generatedAt), 'meta');
      node('p', main, '시험방법 ' + (item.experimentCode || '-') + ' · 장비 ' + (item.equipmentCode || '-'), 'meta');
      node('p', main, '보고서 ID ' + id, 'report-id');
      var status = node('div', row, null, 'report-status');
      var summary = window.RistDeliveryProgress.summary(item);
      var style = item.mailStatus === 'SENT' ? 'sent' : item.transferStatus === 'FAILED' || item.documentStatus === 'FAILED' || item.mailStatus === 'FAILED' || item.mailStatus === 'REJECTED' ? 'failed' : ['PENDING', 'PROCESSING', 'RETRY_WAIT'].indexOf(item.transferStatus) >= 0 ? 'pending' : '';
      node('span', status, summary.label, 'status-label ' + style);
      node('p', status, summary.description, 'delivery-description');
      var errors = [item.documentErrorMessage, item.mailErrorMessage, item.lastErrorMessage], seen = [];
      for (var e = 0; e < errors.length; e++) if (errors[e] && seen.indexOf(errors[e]) < 0) {
        seen.push(errors[e]); node('p', status, errors[e], 'report-error');
      }
      if (item.lastErrorCode && !seen.length) node('p', status, '오류 코드: ' + item.lastErrorCode, 'report-error');
      var actions = node('div', row, null, 'report-actions');
      var detail = node('a', actions, '결과·오류 상세', 'button secondary');
      detail.href = '/reports/' + encodeURIComponent(id);
      detail.setAttribute('aria-label', (item.requestNumber || id) + ' 결과·오류 상세');
      if (item.localFilesRetained !== false && item.downloadUrl) {
        var download = node('a', actions, 'ZIP 다운로드', 'button secondary');
        download.href = '/api/v1/reports/' + encodeURIComponent(id) + '/package';
        download.setAttribute('aria-label', (item.requestNumber || id) + ' ZIP 다운로드');
        node('p', actions, size(item.packageSizeBytes), 'meta');
      } else node('p', actions, item.localFilesRetained === false ? '로컬 파일 보관 만료' : '다운로드 파일 없음', 'expired');
    }
    if (!rows.length) empty('검색 조건에 맞는 내 보고서가 없습니다.');
    $('history-count').textContent = '전체 ' + Number(payload.totalCount) + '건 · 최신 생성 순';
    $('history-page').textContent = currentPage + ' / ' + totalPages + ' 페이지';
    $('history-updated').textContent = '조회 ' + displayDate(new Date().toISOString());
  }
  function load(page, filters) {
    var token = ++generation;
    if (active) active.abort();
    active = null;
    filters = filters || readFilters();
    if (filters.dateFrom && filters.dateTo && filters.dateFrom > filters.dateTo) {
      clearResults('생성일 검색 범위를 확인하세요.'); busy(false);
      notice('생성일 시작은 종료일보다 늦을 수 없습니다.', true); return;
    }
    lastFilters = filters;
    notice('', false); $('history-login').hidden = true;
    clearResults('보고서 이력을 불러오고 있습니다.'); busy(true);
    var xhr = new XMLHttpRequest(), done = false;
    active = xhr;
    function finish(error, payload, status) {
      if (done || token !== generation) return;
      done = true; active = null;
      if (!error) {
        try { render(payload); } catch (invalid) { error = '보고서 이력 응답을 읽지 못했습니다. 다시 조회해 주세요.'; }
      }
      if (error) {
        clearResults('보고서 이력을 표시할 수 없습니다.');
        notice(error, true); $('history-login').hidden = status !== 401;
      }
      busy(false);
    }
    xhr.open('GET', '/api/v1/my/reports?' + query(filters, page), true);
    xhr.timeout = 15000;
    xhr.setRequestHeader('Accept', 'application/json');
    xhr.onload = function () {
      if (xhr.status === 401) return finish('로그인이 만료되었습니다. 다시 로그인한 뒤 내 보고서 이력을 확인하세요.', null, 401);
      if (xhr.status === 403) return finish('보고서 이력을 조회할 권한이 없습니다. 회원 상태와 분석 권한을 확인하세요.', null, 403);
      if (xhr.status < 200 || xhr.status >= 300) return finish('보고서 이력을 불러오지 못했습니다. 잠시 후 다시 조회해 주세요.');
      try { finish(null, JSON.parse(xhr.responseText)); } catch (error) { finish('보고서 이력 응답을 읽지 못했습니다. 다시 조회해 주세요.'); }
    };
    xhr.onerror = function () { finish('서버에 연결할 수 없습니다. 연결 상태를 확인한 뒤 다시 조회해 주세요.'); };
    xhr.ontimeout = function () { finish('조회가 지연되고 있습니다. 잠시 후 다시 조회해 주세요.'); };
    xhr.send();
  }
  $('history-filters').onsubmit = function (event) { event.preventDefault(); load(1); };
  $('history-reset').onclick = function () { $('history-filters').reset(); load(1); };
  $('history-refresh').onclick = function () { load(currentPage, lastFilters || readFilters()); };
  $('history-prev').onclick = function () { if (currentPage > 1) load(currentPage - 1, lastFilters); };
  $('history-next').onclick = function () { if (currentPage < totalPages) load(currentPage + 1, lastFilters); };
  $('history-page-size').onchange = function () { load(1); };
  load(1);
}());

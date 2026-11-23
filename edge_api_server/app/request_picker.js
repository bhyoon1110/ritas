(function (global) {
  'use strict';
  function value(input) { return input == null ? '' : String(input).trim(); }
  function method(item) { return value(item.testMethodCode || item.experimentCode); }
  function methodName(item) { return value(item.testMethodName || item.experimentName); }
  function state(item) { return global.RistRequestFilters.testStateName(item.testState); }
  function searchable(item) {
    return [item.requestNumber, item.requestDate, item.customerRequestName, item.requestUserName,
      item.customerName, item.testChargerName, item.sampleName, method(item), methodName(item),
      state(item), item.requestStateName, item.projectCode, item.requestResultNo, item.sampleResultNo,
      item.testMethodResultNo].map(value).join(' ').toLowerCase();
  }
  function matches(item, query) {
    var text = searchable(item);
    return value(query).toLowerCase().split(/\s+/).every(function (part) { return text.indexOf(part) !== -1; });
  }
  function detailRows(item) {
    return [['의뢰번호', item.requestNumber], ['의뢰일', item.requestDate], ['의뢰명', item.customerRequestName],
      ['의뢰자', item.requestUserName], ['의뢰기관', item.customerName], ['분석 담당자', item.testChargerName],
      ['시료명', item.sampleName], ['시험명', methodName(item)], ['시험방법코드', method(item)],
      ['시험 상태', state(item)], ['의뢰 상태', item.requestStateName], ['프로젝트', item.projectCode],
      ['의뢰 결과번호', item.requestResultNo], ['시료 결과번호', item.sampleResultNo], ['시험 결과번호', item.testMethodResultNo]];
  }
  function styles(doc) {
    if (doc.getElementById('rist-request-picker-style')) return;
    var style = doc.createElement('style'); style.id = 'rist-request-picker-style';
    style.textContent =
      '.rist-request-picker{min-width:0}.rist-request-picker [hidden],.rist-request-results[hidden]{display:none!important}' +
      '.rist-request-picker .rr-label{display:block;margin:8px 0 4px;font-size:13px;font-weight:600}' +
      '.rist-request-picker .rr-combo{position:relative;margin:4px 0}.rist-request-picker .rr-combo input{box-sizing:border-box;width:100%;min-width:0;padding-right:42px;margin:0}' +
      '.rist-request-picker .rr-toggle{position:absolute;top:1px;bottom:1px;right:1px;width:36px;min-height:0;padding:0;border:0;background:transparent}' +
      '.rist-request-picker .rr-help{font-size:12px;line-height:1.6;color:#526174;margin:6px 0;white-space:normal;overflow-wrap:break-word}' +
      '.rist-request-results{box-sizing:border-box;position:fixed;z-index:1800;overflow:auto;overscroll-behavior:contain;background:white;color:#213547;border:1px solid #94a3b8;border-radius:6px;box-shadow:0 6px 24px rgba(15,23,42,.22);font:13px/1.5 Arial,sans-serif;text-align:left}' +
      '.rist-request-results .rr-head,.rist-request-results .rr-row{display:flex;align-items:stretch;width:100%;box-sizing:border-box;text-align:left}' +
      '.rist-request-results .rr-head{position:sticky;top:0;background:#eaf1f5;color:#24455a;font-weight:bold;z-index:1}' +
      '.rist-request-results .rr-row{border:0;border-bottom:1px solid #e2e8f0;border-radius:0;padding:0;margin:0;white-space:normal;background:white;color:inherit;font:inherit;cursor:pointer;box-shadow:none;height:auto;max-height:none}' +
      '.rist-request-results .rr-row:hover,.rist-request-results .rr-row.is-active{background:#edf6ff;outline:2px solid #327cb0;outline-offset:-2px}' +
      '.rist-request-results .rr-row[aria-selected=true]{background:#e4f1f8;box-shadow:inset 3px 0 #17627d}' +
      '.rist-request-results .rr-cell{display:block;box-sizing:border-box;padding:9px 8px;min-width:0;overflow-wrap:break-word;word-break:break-word;white-space:normal}' +
      '.rist-request-results .rr-c0{width:20%}.rist-request-results .rr-c1{width:11%}.rist-request-results .rr-c2{width:17%}.rist-request-results .rr-c3{width:19%}.rist-request-results .rr-c4{width:24%}.rist-request-results .rr-c5{width:9%}' +
      '.rist-request-results .rr-primary{display:block;font-weight:600}.rist-request-results .rr-secondary{display:block;font-size:12px;color:#526174;margin-top:3px}' +
      '.rist-request-results .rr-mobile-label{display:none}.rist-request-results .rr-empty{padding:16px;margin:0}' +
      '.rist-request-picker .rr-detail{margin:10px 0;padding:10px;background:#f4f8fa;border:1px solid #cbdce5;border-radius:6px;font-size:13px;line-height:1.5}' +
      '.rist-request-picker .rr-detail summary{font-weight:600;cursor:pointer}.rist-request-picker .rr-detail dl{display:flex;flex-wrap:wrap;margin:8px -4px 0}' +
      '.rist-request-picker .rr-detail dl>div{box-sizing:border-box;width:50%;min-width:0;padding:5px 4px}.rist-request-picker .rr-detail dt{color:#526174;font-size:12px}.rist-request-picker .rr-detail dd{margin:2px 0;overflow-wrap:break-word;word-break:break-word}' +
      '.rist-request-picker input:focus,.rist-request-picker button:focus{outline:2px solid #327cb0;outline-offset:1px}' +
      '@media(max-width:600px){.rist-request-results .rr-head{display:none}.rist-request-results .rr-row{flex-wrap:wrap;padding:5px 7px}.rist-request-results .rr-cell{width:50%;padding:5px 6px}.rist-request-results .rr-c0{width:100%}.rist-request-results .rr-c1{width:34%}.rist-request-results .rr-c2{width:66%}.rist-request-results .rr-c5{width:100%;display:flex;align-items:center}.rist-request-results .rr-mobile-label{display:block;color:#526174;font-size:11px;font-weight:normal;margin-bottom:2px}.rist-request-results .rr-c5 .rr-mobile-label{margin:0 8px 0 0}}';
    doc.head.appendChild(style);
  }
  function mount(parent, options) {
    options = options || {};
    var doc = parent.ownerDocument, win = doc.defaultView || global, id = options.id || 'request';
    styles(doc);
    function el(tag, host, text, cls) {
      var node = doc.createElement(tag); if (text != null) node.textContent = text;
      if (cls) node.className = cls; if (host) host.appendChild(node); return node;
    }
    var root = el('div', parent, null, 'rist-request-picker');
    var load = options.loadButton || el('button', root, '의뢰 조회'); load.type = 'button';
    if (!load.id) load.id = id + '-load';
    var label = el('label', root, '의뢰 검색·선택', 'rr-label');
    var combo = el('div', root, null, 'rr-combo'), input = el('input', combo), toggle = el('button', combo, '▾', 'rr-toggle');
    input.id = id + '-search'; label.htmlFor = input.id; input.type = 'text'; input.maxLength = 1000; input.autocomplete = 'off';
    input.placeholder = '현재 페이지: 의뢰번호·의뢰자·기관·시료·시험 검색';
    input.setAttribute('aria-label', '의뢰 검색·선택'); input.setAttribute('role', 'combobox'); input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-controls', id + '-results'); input.setAttribute('aria-describedby', id + '-info');
    toggle.id = id + '-toggle'; toggle.type = 'button'; toggle.setAttribute('aria-label', '의뢰 목록 열기'); toggle.setAttribute('aria-controls', id + '-results');
    var results = el('div', doc.body, null, 'rist-request-results'); results.id = id + '-results';
    results.setAttribute('role', 'listbox'); results.setAttribute('aria-label', '조회된 의뢰 목록');
    var help = el('p', root, '검색은 현재 조회한 페이지 안에서만 적용됩니다. 다른 페이지는 이전·다음으로, 전체 범위는 위 검색 조건의 의뢰번호·기간으로 조회하세요.', 'rr-help');
    var info = el('p', root, '의뢰 조회를 누르면 200건씩 불러옵니다. 의뢰번호 직접 입력도 가능합니다.', 'rr-help');
    info.id = id + '-info'; info.setAttribute('role', 'status');
    var detail = el('details', root, null, 'rr-detail'); detail.id = id + '-detail'; detail.hidden = true;
    var summary = el('summary', detail, '선택한 의뢰 상세'), detailList = el('dl', detail);
    var items = [], optionsList = [], selected = null, selectedText = '', active = -1, page = 0;
    var loading = false, destroyed = false, composing = false, anchorRect;
    var previous = el('button'), next = el('button'); previous.id = id + '-previous'; next.id = id + '-next';
    var panel = global.RistRequestFilters.mount(label, {experimentType:options.experimentType, reload:reload, previous:previous, next:next,
      onChange:function () { items = []; page = 0; render(); close(); info.textContent = '검색 조건이 바뀌었습니다. 의뢰 조회를 눌러 다시 불러오세요.'; }});
    var progress = global.RistFileProgress.monitor(info);
    function blocked() { return loading || destroyed || !!(options.isBlocked && options.isBlocked()); }
    function close() {
      results.hidden = true; active = -1; input.removeAttribute('aria-activedescendant');
      input.setAttribute('aria-expanded', 'false'); toggle.setAttribute('aria-expanded', 'false');
    }
    function position() {
      if (results.hidden || destroyed) return;
      var rect = combo.getBoundingClientRect(); anchorRect = rect;
      var width = Math.min(1000, win.innerWidth - 16), below = win.innerHeight - rect.bottom - 12, above = rect.top - 12;
      var up = below < 220 && above > below, height = Math.max(40, Math.min(430, up ? above : below));
      results.style.width = width + 'px'; results.style.maxHeight = height + 'px';
      results.style.left = Math.max(8, Math.min(rect.left, win.innerWidth - width - 8)) + 'px';
      results.style.top = (up ? Math.max(8, rect.top - Math.min(height, results.scrollHeight) - 4) : rect.bottom + 4) + 'px';
    }
    function open() {
      if (blocked()) return;
      results.hidden = false; input.setAttribute('aria-expanded', 'true'); toggle.setAttribute('aria-expanded', 'true'); position();
    }
    function availability() {
      var busy = blocked(); load.disabled = input.disabled = toggle.disabled = busy; panel.setBusy(busy); if (busy) close();
    }
    function updateDetail() {
      detail.hidden = !selected; detailList.textContent = ''; if (!selected) return;
      summary.textContent = '선택한 의뢰 · ' + (value(selected.requestNumber) || '번호 미제공');
      detailRows(selected).forEach(function (row) { var pair = el('div', detailList); el('dt', pair, row[0]); el('dd', pair, value(row[1]) || '미제공'); });
      detail.open = true;
    }
    function choose(index) {
      if (blocked() || !items[index]) return;
      selected = items[index]; selectedText = [selected.requestNumber, selected.sampleName, method(selected)].filter(Boolean).join(' · ');
      input.value = selectedText; updateDetail(); close();
      info.textContent = '선택한 의뢰를 반영했습니다. 아래 상세 정보에서 시료·시험을 확인하세요.';
      if (options.onSelect) options.onSelect(selected);
      if (!destroyed) { input.focus(); close(); }
    }
    var headings = ['의뢰번호 / 의뢰명', '의뢰일', '의뢰자 / 기관', '시료', '시험명 / 코드', '시험 상태'];
    function render() {
      results.textContent = ''; optionsList = []; active = -1; input.removeAttribute('aria-activedescendant');
      var head = el('div', results, null, 'rr-head'); head.setAttribute('aria-hidden', 'true');
      headings.forEach(function (name, i) { el('span', head, name, 'rr-cell rr-c' + i); });
      var query = selected && input.value === selectedText ? '' : input.value;
      items.forEach(function (item, index) {
        if (!matches(item, query)) return;
        var row = el('button', results, null, 'rr-row'); row.type = 'button'; row.tabIndex = -1;
        row.id = id + '-result-' + index; row.setAttribute('role', 'option'); row.setAttribute('aria-selected', String(item === selected));
        var cells = [[item.requestNumber, item.customerRequestName], [item.requestDate], [item.requestUserName, item.customerName],
          [item.sampleName].concat(value(item.sampleResultNo) ? ['시료 #' + item.sampleResultNo] : []),
          [methodName(item), method(item)].concat(value(item.testMethodResultNo) ? ['시험 #' + item.testMethodResultNo] : []), [state(item)]];
        var accessible = [];
        cells.forEach(function (parts, i) {
          var cell = el('span', row, null, 'rr-cell rr-c' + i); el('span', cell, headings[i], 'rr-mobile-label').setAttribute('aria-hidden', 'true');
          parts.forEach(function (part, n) { el('span', cell, value(part) || '미제공', n ? 'rr-secondary' : 'rr-primary'); });
          accessible.push(headings[i] + ': ' + parts.map(function (part) { return value(part) || '미제공'; }).join(' / '));
        });
        row.setAttribute('aria-label', accessible.join(' · ')); row.title = accessible.join(' · ');
        row.onmousedown = function (event) { event.preventDefault(); }; row.onclick = function () { choose(index); };
        optionsList.push(row);
      });
      if (!optionsList.length) el('p', results, page ? '현재 페이지에 일치하는 의뢰가 없습니다. 이전·다음 페이지 또는 위 검색 조건을 확인하세요.' : '먼저 의뢰 조회를 눌러 주세요.', 'rr-empty');
      if (page) info.textContent = page + '페이지 · ' + items.length + '건 중 ' + optionsList.length + '건 표시. 검색만으로 선택한 의뢰는 바뀌지 않습니다.';
      position();
    }
    function reload() {
      if (blocked()) return;
      var url; try { url = panel.url(); } catch (error) { info.textContent = error.message; return; }
      items = []; page = 0; render(); loading = true; availability(); progress.start('의뢰 목록 조회 중…');
      return options.fetch(url, function (error, result) {
        if (destroyed) return;
        progress.stop(); loading = false;
        if (error) { info.textContent = '조회 실패: ' + error.message + ' 다시 조회하거나 의뢰번호를 직접 입력하세요.'; }
        else { items = Array.isArray(result.items) ? result.items : []; page = Number(result.page) || 1; panel.showResult(result); render(); }
        availability(); if (!error && !blocked()) { input.focus(); open(); }
      });
    }
    load.onclick = reload;
    input.oninput = function () { if (!blocked() && !composing) { render(); open(); } };
    input.onfocus = function () { render(); open(); };
    input.addEventListener('compositionstart', function () { composing = true; });
    input.addEventListener('compositionend', function () { composing = false; render(); open(); });
    input.onkeydown = function (event) {
      if (blocked() || composing || event.isComposing || event.keyCode === 229) return;
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault(); if (results.hidden) { render(); open(); }
        if (optionsList.length) {
          active = active < 0 ? (event.key === 'ArrowDown' ? 0 : optionsList.length - 1) : Math.max(0, Math.min(optionsList.length - 1, active + (event.key === 'ArrowDown' ? 1 : -1)));
          optionsList.forEach(function (row, n) { row.classList.toggle('is-active', n === active); });
          var row = optionsList[active]; input.setAttribute('aria-activedescendant', row.id);
          var top = row.offsetTop, headerHeight = results.firstChild.offsetHeight;
          if (top < results.scrollTop + headerHeight) results.scrollTop = top - headerHeight;
          else if (top + row.offsetHeight > results.scrollTop + results.clientHeight) results.scrollTop = top + row.offsetHeight - results.clientHeight;
        }
      } else if (event.key === 'Enter' && !results.hidden) { event.preventDefault(); if (active >= 0) optionsList[active].click(); }
      else if (event.key === 'Escape' && !results.hidden) { event.preventDefault(); event.stopPropagation(); close(); }
      else if (event.key === 'Tab') close();
    };
    toggle.onmousedown = function (event) { event.preventDefault(); };
    toggle.onclick = function () { if (results.hidden) { input.focus(); render(); open(); } else close(); };
    function outside(event) { if (!combo.contains(event.target) && !results.contains(event.target)) close(); }
    function scrolled(event) {
      if (results.hidden || event.target === input || (event.target.nodeType && results.contains(event.target))) return;
      var rect = combo.getBoundingClientRect(); if (anchorRect && rect.top === anchorRect.top && rect.left === anchorRect.left) return;
      close();
    }
    doc.addEventListener('mousedown', outside); doc.addEventListener('touchstart', outside); doc.addEventListener('focusin', outside);
    win.addEventListener('scroll', scrolled, true); win.addEventListener('resize', close);
    close(); render(); availability();
    return {availability:availability, selected:function () { return selected; },
      clear:function () { selected = null; selectedText = ''; input.value = ''; updateDetail(); close(); render(); },
      destroy:function () {
        destroyed = true; progress.stop(); doc.removeEventListener('mousedown', outside); doc.removeEventListener('touchstart', outside); doc.removeEventListener('focusin', outside);
        win.removeEventListener('scroll', scrolled, true); win.removeEventListener('resize', close); if (results.parentNode) results.parentNode.removeChild(results);
      }};
  }
  global.RistRequestPicker = {mount:mount, searchable:searchable, matches:matches, detailRows:detailRows};
}(typeof window !== 'undefined' ? window : this));

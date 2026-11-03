/* Shared delivery presentation. ES5 syntax also runs on the Chrome 49 path. */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.RistDeliveryProgress = api;
}(this, function () {
  'use strict';
  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  }
  function value(item, key, snake) { return item[key] == null ? item[snake] : item[key]; }
  function size(bytes) {
    var n = Number(bytes), units = ['B', 'KiB', 'MiB', 'GiB'], i = 0;
    if (!isFinite(n) || n < 0) return '-';
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return (i ? n.toFixed(2) : String(n)) + ' ' + units[i];
  }
  function summary(item) {
    item = item || {};
    var queue = String(value(item, 'transferStatus', 'transfer_status') || 'NOT_QUEUED');
    var doc = String(value(item, 'documentStatus', 'document_status') || '');
    var mail = String(value(item, 'mailStatus', 'mail_status') || '');
    var phase = item.deliveryPhase || '';
    var review = item.needsReview === true || item.mailRetryUncertain === true;
    var label = '전송 전', step = -1, description = '보고서와 메일 내용을 확인한 뒤 전송을 요청하세요.';
    if (['PENDING', 'PROCESSING', 'RETRY_WAIT'].indexOf(queue) >= 0) {
      step = 0; label = 'Edge 전송 요청 접수';
      description = 'Edge에서 전송을 준비하고 있습니다. LIMS 메일 접수는 아직 확인되지 않았습니다.';
    }
    if (queue === 'PROCESSING' && !phase) {
      step = 1; label = '파일 전송 준비·처리 중';
      description = '파일 검증과 SFTP 전송을 처리하고 있습니다.';
    }
    if (phase === 'DOCUMENT_PENDING' || doc === 'PENDING' || doc === 'TRANSFERRING' || doc === 'FAILED') {
      step = 2; label = doc === 'FAILED' ? '문서 적재 확인 필요' : 'LIMS 문서 적재 확인 중';
      description = doc === 'FAILED' ? '적재 실패 사유를 확인하세요. LIMS에서 이후 적재가 완료되는지 재확인합니다.' : '문서가 적재되고 첨부 파일번호가 발급될 때까지 기다립니다.';
    }
    if (doc === 'COMPLETED') {
      step = 2; label = '문서 적재 완료 · 메일 접수 준비';
      description = 'LIMS 첨부 파일번호를 확보했습니다. 메일 접수 결과를 확인하고 있습니다.';
    }
    if (mail === 'ACCEPTED' || mail === 'PENDING') {
      step = 3; label = mail === 'ACCEPTED' ? 'LIMS 메일 접수 완료' : 'LIMS 메일 발송 대기';
      description = '1분 주기의 LIMS 발송 배치에서 처리합니다.';
      if (Number(item.mailTryCount) > 0) description = '발송 오류 후 LIMS에서 자동 재시도 중입니다. 최대 5회 시도하며 직전 실패 사유를 아래에서 확인할 수 있습니다.';
    }
    if (phase === 'MAIL_SUBMITTING') {
      step = 2; label = 'LIMS 메일 접수 확인 중';
      description = '접수 응답을 확인 중입니다. 같은 요청번호로 중복 발송을 방지합니다.';
    }
    if (phase === 'MAIL_RETRYING') {
      step = 3; label = '메일 재시도 접수 확인 중';
      description = '이전 메일 내용과 첨부를 그대로 사용합니다. 재시도를 반복 요청하지 마세요.';
    }
    if (mail === 'FAILED' || mail === 'REJECTED') {
      step = 3; label = mail === 'FAILED' ? '메일 발송 실패' : '메일 접수 거절';
      description = '일시적 발송 오류는 재시도할 수 있습니다. 주소·제목·본문을 수정하려면 수정한 보고서를 새 요청으로 접수하세요.';
    }
    if (queue === 'FAILED' && !mail && doc !== 'FAILED') {
      label = 'Edge 전송 실패'; description = '오류를 확인한 뒤 전송을 다시 요청하세요.';
    }
    if (queue === 'CANCELLED') { label = 'Edge 전송 취소'; description = '전송 대기를 취소했습니다.'; }
    if (queue === 'COMPLETED' && mail !== 'SENT') {
      label = '전송 기록 완료 · 메일 상태 미확인';
      description = '이전 전송 기록만으로는 메일 서버 전달 완료를 확인할 수 없습니다.';
    }
    if (mail === 'SENT') {
      step = 4; label = '메일 서버 전달 완료';
      description = 'LIMS가 메일 서버로 전달을 마쳤습니다. 수신자의 수신 확인을 뜻하지 않습니다.';
    }
    var terminal = mail === 'SENT' || ['FAILED', 'CANCELLED', 'COMPLETED'].indexOf(queue) >= 0 || step < 0;
    var seconds = review ? 600 : Number(item.nextPollSeconds || item.pollDelaySeconds || 60);
    seconds = Math.min(600, Math.max(60, seconds || 60));
    return {label: label, step: step, description: description, needsReview: review,
      canRetryMail: queue === 'FAILED' && mail === 'FAILED' && !!item.mailRequestId,
      terminal: terminal, refreshMs: seconds * 1000};
  }
  function compact(item) {
    var s = summary(item);
    return '<span class="badge">' + esc(s.label) + '</span>' + (s.needsReview ? '<br><span class="warn">확인 필요</span>' : '');
  }
  function html(item) {
    item = item || {};
    var s = summary(item), fields = [
      ['문서번호', item.axDocNum], ['첨부 파일번호', item.fileNo], ['메일 요청번호', item.mailRequestId],
      ['LIMS 접수번호', item.alarmResultNo], ['LIMS 발송 시도', item.mailTryCount],
      ['문서 상태 조회', item.documentPollCount], ['메일 상태 조회', item.mailPollCount],
      ['메일 접수 시각', item.acceptedAt], ['메일 서버 전달 시각', item.sentAt]
    ];
    var steps = ['Edge 접수', '파일 전송', '문서 적재', 'LIMS 메일 접수', '메일 서버 전달'];
    var result = '<div class="delivery-state" role="status" aria-live="polite"><strong>' + esc(s.label) + '</strong>';
    result += '<p>' + esc(s.description) + '</p><ol class="delivery-steps">';
    for (var i = 0; i < steps.length; i++) result += '<li' + (i === s.step ? ' aria-current="step"' : '') + '>' + (i < s.step ? '✓ ' : '') + esc(steps[i]) + '</li>';
    result += '</ol>';
    if (s.needsReview) result += '<p class="warn"><b>확인 필요</b> · ' + esc(item.reviewReason || '처리가 지연되고 있습니다. 아래 식별자로 LIMS 담당자에게 확인을 요청하세요.') + '</p>';
    if (item.mailRetryUncertain) result += '<p class="warn">재시도 접수 응답을 확인하지 못했습니다. 먼저 기존 메일 상태를 확인하세요.</p>';
    result += '<dl class="delivery-identifiers">';
    for (var j = 0; j < fields.length; j++) if (fields[j][1] != null && fields[j][1] !== '') result += '<dt>' + esc(fields[j][0]) + '</dt><dd>' + esc(fields[j][1]) + '</dd>';
    result += '</dl>';
    if (item.packageSizeBytes != null) result += '<p>보고서 ZIP: <b>' + esc(size(item.packageSizeBytes)) + '</b></p>';
    if (item.mailAttachmentLimits) {
      var limits = item.mailAttachmentLimits;
      result += '<p class="muted">첨부파일 원본 크기 기준 · 파일당 한도: ' + esc(limits.singleBytes > 0 ? size(limits.singleBytes) : '미설정');
      result += ' · 첨부 합계 한도: ' + esc(limits.totalAttachmentBytes > 0 ? size(limits.totalAttachmentBytes) : '미설정') + '</p>';
      if (!limits.configured) result += '<p class="muted">LIMS 첨부 용량 기준 확인 후 제한값을 적용합니다.</p>';
    }
    var errors = [item.documentErrorMessage, item.mailErrorMessage, item.lastErrorMessage || item.last_error_message];
    var seen = [];
    for (var k = 0; k < errors.length; k++) if (errors[k] && seen.indexOf(errors[k]) < 0) {
      seen.push(errors[k]); result += '<p class="warn delivery-error">' + esc(errors[k]) + '</p>';
    }
    if (!s.terminal) result += '<p class="muted">상태 화면 자동 갱신: ' + (s.refreshMs / 60000) + '분 간격</p>';
    return result + '</div>';
  }
  function request(method, url, done) {
    var xhr = new XMLHttpRequest(), finished = false;
    function finish(error, payload) { if (!finished) { finished = true; done(error, payload); } }
    xhr.open(method, url, true); xhr.timeout = 15000;
    xhr.setRequestHeader('Accept', 'application/json');
    xhr.setRequestHeader('X-Request-Id', 'report-progress-' + Date.now());
    xhr.onload = function () {
      var payload;
      try { payload = JSON.parse(xhr.responseText || '{}'); } catch (_) { finish(new Error('상태 응답을 읽지 못했습니다.')); return; }
      if (xhr.status >= 200 && xhr.status < 300) finish(null, payload);
      else { var error = new Error(payload.message || payload.detail || '상태를 조회하지 못했습니다.'); error.status = xhr.status; error.details = payload.details; finish(error); }
    };
    xhr.onerror = function () { finish(new Error('서버 연결을 확인하세요.')); };
    xhr.ontimeout = function () { finish(new Error('서버 응답이 지연되고 있습니다.')); };
    xhr.send(null); return xhr;
  }
  function watch(options) {
    var timer = null, pending = null, stopped = false, paused = false, calls = 0, failures = 0;
    var maxCalls = options.maxCalls || 240, delay = 60000;
    function clear() { if (timer) clearTimeout(timer); timer = null; }
    function schedule(ms) { clear(); if (!stopped && !paused && !document.hidden) timer = setTimeout(refresh, ms); }
    function refresh(manual) {
      clear(); if (stopped || pending || document.hidden) return;
      if (manual) { calls = 0; paused = false; }
      if (paused) return;
      pending = request('GET', options.url, function (error, payload) {
        pending = null; if (stopped) return;
        calls++;
        if (error) {
          failures++; if (options.onError) options.onError(error);
          if (error.status === 401 || error.status === 403 || error.status === 404) paused = true;
          delay = failures >= 3 ? 600000 : 60000;
        } else {
          failures = 0; var state = summary(payload); delay = state.refreshMs;
          if (options.onState) options.onState(payload);
          if (state.terminal) paused = true;
        }
        if (calls >= maxCalls) { paused = true; if (options.onPause) options.onPause(); }
        schedule(delay);
      });
    }
    function visibility() { if (document.hidden) clear(); else if (!paused) refresh(); }
    function stop() {
      stopped = true; clear(); if (pending) pending.abort(); pending = null;
      document.removeEventListener('visibilitychange', visibility);
      window.removeEventListener('pagehide', stop);
    }
    document.addEventListener('visibilitychange', visibility); window.addEventListener('pagehide', stop);
    if (options.initialState) {
      var initial = summary(options.initialState); delay = initial.refreshMs; paused = initial.terminal;
      if (options.onState) options.onState(options.initialState);
      schedule(delay);
    } else refresh();
    return {refresh: function () { refresh(true); }, stop: stop};
  }
  function mountReview() {
    var host = document.getElementById('delivery-progress'); if (!host) return;
    var button = document.getElementById('send'), message = document.getElementById('message');
    var status = document.getElementById('status'), sending = false;
    var poller = watch({url: host.getAttribute('data-status-url'),
      onState: function (item) {
        host.innerHTML = html(item); status.textContent = summary(item).label;
        if (button && !sending) { button.disabled = !item.canQueue; button.textContent = item.canQueue ? 'SSO 인증 후 LIMS 전송' : 'Edge 전송 요청 접수됨'; }
      },
      onError: function (error) { message.textContent = error.message; },
      onPause: function () { message.textContent = '장시간 자동 갱신을 멈췄습니다. 상태 새로고침을 눌러 다시 확인할 수 있습니다.'; }
    });
    var refresh = document.getElementById('refresh-delivery'); if (refresh) refresh.onclick = poller.refresh;
    if (button) button.onclick = function () {
      if (sending || button.disabled) return; sending = true; button.disabled = true;
      message.textContent = 'Edge 전송 큐에 등록하는 중입니다.';
      request('POST', button.getAttribute('data-send-url'), function (error) {
        sending = false;
        if (error) {
          message.textContent = error.message; button.disabled = false;
          var url = error.details && error.details.reauthUrl;
          if (typeof url === 'string' && url.charAt(0) === '/' && url.charAt(1) !== '/') window.location.href = url;
          return;
        }
        message.textContent = 'Edge가 전송 요청을 접수했습니다. 아래에서 LIMS 적재·메일 접수 결과를 확인하세요.';
        button.textContent = 'Edge 전송 요청 접수됨'; poller.refresh();
      });
    };
  }
  return {summary: summary, compact: compact, html: html, watch: watch, mountReview: mountReview};
}));

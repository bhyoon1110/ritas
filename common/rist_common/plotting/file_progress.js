/* File preparation is indeterminate; only measured writes show a percentage. */
(function () {
  "use strict";
  if (window.RistFileProgress) return;
  var running = 0;
  function node(tag, parent, text) {
    var el = document.createElement(tag);
    if (text != null) el.textContent = text;
    if (parent) parent.appendChild(el);
    return el;
  }
  function begin(label, host) {
    try {
      if (!host && window.parent !== window && window.parent.location.origin === window.location.origin && window.parent.RistFileProgress)
        return window.parent.RistFileProgress.begin(label);
    } catch (_) {}
    if (!document.getElementById('rist-file-progress-style')) {
      var style = node('style', document.head); style.id = 'rist-file-progress-style';
      style.textContent = '.rist-file-progress-stack{position:fixed;right:16px;bottom:42px;z-index:2147483647;width:370px;max-width:calc(100% - 32px);max-height:70vh;overflow:auto;box-sizing:border-box}'
        + '.rist-file-progress{box-sizing:border-box;margin:8px 0;padding:16px;border:1px solid #a9c4d2;border-radius:10px;background:#fff;color:#173c52;box-shadow:0 5px 24px rgba(20,48,66,.18);font:14px/1.5 Arial,sans-serif;text-align:left;overflow-wrap:break-word;word-wrap:break-word}'
        + '.rist-file-progress strong{display:block;font-size:15px;padding-right:30px}.rist-file-progress p{margin:7px 0}.rist-file-progress small{display:block;color:#536c7d;font-size:12px}'
        + '.rist-file-progress button{float:right;padding:2px 8px;border:1px solid #becdd6;border-radius:5px;background:#fff;color:#244c63;font:18px/1.4 Arial,sans-serif;cursor:pointer}'
        + '.rist-file-progress [hidden]{display:none!important}.rist-file-progress[data-state=error]{border-color:#c77770;color:#9e3028}'
        + '.rist-file-progress-track{height:6px;overflow:hidden;background:#e1edf3;border-radius:4px;margin:12px 0}'
        + '.rist-file-progress-fill{height:100%;width:36%;background:#28718e;animation:rist-file-progress-slide 1.4s ease-in-out infinite}'
        + '.rist-file-progress-fill.measured{animation:none;transition:width .15s linear}'
        + '@keyframes rist-file-progress-slide{0%{transform:translateX(-100%)}100%{transform:translateX(280%)}}'
        + '@media(prefers-reduced-motion:reduce){.rist-file-progress-fill{animation:none}.rist-file-progress-fill.measured{transition:none}}';
    }
    if (!host) {
      host = document.getElementById('rist-file-progress-stack');
      if (!host) { host = node('div', document.body); host.id = 'rist-file-progress-stack'; host.className = 'rist-file-progress-stack'; }
      // Keep errors until dismissed, but do not accumulate completed notifications.
      Array.prototype.forEach.call(host.querySelectorAll('[data-state=done]'), function (old) { old.remove(); });
    }
    var card = node('section', host); card.className = 'rist-file-progress'; card.setAttribute('data-state', 'working');
    card.setAttribute('aria-label', label + ' 저장 상태');
    var close = node('button', card, '×'); close.type = 'button'; close.hidden = true; close.setAttribute('aria-label', '저장 상태 닫기');
    var live = node('div', card); live.setAttribute('role', 'status'); live.setAttribute('aria-live', 'polite');
    var title = node('strong', live, label), detail = node('p', live, '파일을 준비하고 있습니다…');
    var track = node('div', card); track.className = 'rist-file-progress-track'; track.setAttribute('role', 'progressbar'); track.setAttribute('aria-label', label + ' 준비 중');
    var fill = node('div', track); fill.className = 'rist-file-progress-fill';
    var elapsed = node('small', card), started = Date.now(), ended = false, lastBytes = 0;
    function clock() { var seconds = Math.floor((Date.now() - started) / 1000); elapsed.textContent = '경과 ' + Math.floor(seconds / 60) + ':' + ('0' + seconds % 60).slice(-2); }
    clock(); var timer = window.setInterval(clock, 1000); running++;
    function update(text) { if (!ended && detail.textContent !== text) detail.textContent = text; }
    function stop() { if (ended) return; ended = true; running--; window.clearInterval(timer); window.removeEventListener('pagehide', stop); clock(); track.hidden = true; close.hidden = false; }
    function dismiss() { stop(); card.remove(); }
    close.onclick = dismiss;
    window.addEventListener('pagehide', stop);
    return {
      update: update,
      paint: function () { return new Promise(function (resolve) { window.setTimeout(resolve, 30); }); },
      bytes: function (loaded, total) {
        if (ended) return;
        // Large TEM ZIPs can produce thousands of small chunks per second.
        // Updating the DOM must not slow the sequential disk writes.
        var now = Date.now();
        if (now - lastBytes < 100 && (!total || loaded < total)) return;
        lastBytes = now;
        var amount = (loaded / 1048576).toFixed(1) + ' MB';
        if (total > 0 && loaded <= total) {
          var percent = Math.min(100, Math.floor(100 * loaded / total));
          fill.className = 'rist-file-progress-fill measured'; fill.style.width = percent + '%';
          track.setAttribute('aria-valuenow', String(percent)); track.setAttribute('aria-valuemin', '0'); track.setAttribute('aria-valuemax', '100');
          update('파일 쓰는 중… ' + amount + ' / ' + (total / 1048576).toFixed(1) + ' MB');
        } else {
          fill.className = 'rist-file-progress-fill'; fill.style.width = '';
          track.removeAttribute('aria-valuenow'); track.removeAttribute('aria-valuemin'); track.removeAttribute('aria-valuemax');
          update('파일 쓰는 중… ' + amount);
        }
      },
      finish: function (text, error) { stop(); card.setAttribute('data-state', error ? 'error' : 'done'); title.textContent = label + (error ? ' · 실패' : ' · 준비 완료'); detail.textContent = text; },
      dismiss: dismiss
    };
  }
  var handoff = '다운로드를 시작했습니다. 전송 진행과 저장 완료는 브라우저 다운로드 목록에서 확인하세요.';
  // Reserve a thin track under an existing status line. Automatic preview
  // updates must not open dialogs, steal focus, or move the slide/buttons.
  function monitor(status) {
    if (status._ristProgressMonitor) return status._ristProgressMonitor;
    if (!document.getElementById('rist-task-progress-style')) {
      var style = node('style', document.head); style.id = 'rist-task-progress-style';
      style.textContent = '.rist-task-progress{min-width:0;flex:1;position:relative;padding-bottom:8px;box-sizing:border-box}'
        + '.rist-task-progress-row{display:flex;align-items:center;min-width:0;gap:8px}.rist-task-progress-row>:first-child{flex:1;min-width:0;margin:0!important}'
        + '.rist-task-elapsed{flex:none;font:11px/1.4 Arial,sans-serif;color:#536c7d;white-space:nowrap;visibility:hidden}'
        + '.rist-task-track{position:absolute;bottom:0;left:0;right:0;height:4px;overflow:hidden;border-radius:3px;background:#e1edf3;visibility:hidden}'
        + '.rist-task-track>span{display:block;height:100%;width:36%;background:#28718e}'
        + '.rist-task-progress.is-running .rist-task-elapsed,.rist-task-progress.is-running .rist-task-track{visibility:visible}'
        + '.rist-task-progress.is-running .rist-task-track>span{animation:rist-task-shimmer 1.4s ease-in-out infinite}'
        + '@keyframes rist-task-shimmer{0%{transform:translateX(-100%)}100%{transform:translateX(280%)}}'
        + '@media(prefers-reduced-motion:reduce){.rist-task-progress.is-running .rist-task-track>span{animation:none}}';
    }
    var wrapper = node('div'); wrapper.className = 'rist-task-progress';
    status.parentNode.insertBefore(wrapper, status);
    var row = node('div', wrapper); row.className = 'rist-task-progress-row'; row.appendChild(status);
    var elapsed = node('small', row); elapsed.className = 'rist-task-elapsed'; elapsed.setAttribute('aria-hidden', 'true');
    var track = node('div', wrapper); track.className = 'rist-task-track'; track.setAttribute('role', 'progressbar');
    track.setAttribute('aria-label', '작업 처리 중'); track.setAttribute('aria-hidden', 'true'); node('span', track);
    var timer = null, started = 0;
    function clock() { var seconds = Math.floor((Date.now() - started) / 1000); elapsed.textContent = Math.floor(seconds / 60) + ':' + ('0' + seconds % 60).slice(-2); }
    function stop() { window.clearInterval(timer); timer = null; wrapper.classList.remove('is-running'); track.setAttribute('aria-hidden', 'true'); }
    window.addEventListener('pagehide', stop);
    status._ristProgressMonitor = {
      start: function (text) {
        if (text != null) status.textContent = text;
        if (timer !== null) return;
        started = Date.now(); clock(); timer = window.setInterval(clock, 1000);
        wrapper.classList.add('is-running'); track.setAttribute('aria-hidden', 'false');
      },
      stop: stop,
      paint: function () { return new Promise(function (resolve) { window.setTimeout(resolve, 30); }); }
    };
    return status._ristProgressMonitor;
  }
  window.RistFileProgress = {begin: begin, handoff: handoff, monitor: monitor};
  // Small existing-file/JSON downloads also receive feedback without fetching
  // them a second time or buffering a large file in memory.
  document.addEventListener('click', function (event) {
    var a = event.target;
    while (a && a.tagName !== 'A') a = a.parentElement;
    if (!a || !a.hasAttribute('download') || !a.href || a.getAttribute('aria-disabled') === 'true' || running) return;
    window.setTimeout(function () {
      if (event.defaultPrevented || running) return;
      var progress = begin(a.download || '파일 다운로드');
      progress.finish(handoff);
    }, 0);
  });
}());

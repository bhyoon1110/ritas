/* Shared report-panel sizing. ES5 for the Chrome 49 compatibility path. */
(function (global) {
  'use strict';
  var SIZE = 18;
  function mount(options) {
    var host = options.container, left = options.left, right = options.right;
    var doc = host.ownerDocument, win = doc.defaultView, preferred = null;
    var handle = doc.createElement('div'), active = false, drag = null, frame = null, pending = null;
    var total = 0, lower = 0, upper = 0, current = 0, destroyed = false;
    var paneKeys = ['width', 'minWidth', 'maxWidth', 'flex', 'gridColumn'];
    function remember(node, keys) {
      var values = {};
      keys.forEach(function (key) { values[key] = node.style[key]; });
      return function () { keys.forEach(function (key) { node.style[key] = values[key]; }); };
    }
    var restoreLeft = remember(left, paneKeys), restoreRight = remember(right, paneKeys);
    var restoreHost = remember(host, ['gridTemplateColumns']);
    if (!doc.getElementById('rist-report-split-style')) {
      var style = doc.createElement('style'); style.id = 'rist-report-split-style';
      style.textContent = '.rist-report-split{box-sizing:border-box;position:relative;align-self:stretch;cursor:col-resize;touch-action:none;user-select:none;-webkit-user-select:none;outline:none;background:transparent;padding:0;margin:0;border:0;min-height:80px}' +
        '.rist-report-split:before{content:"";position:absolute;left:8px;top:0;bottom:0;width:2px;background:#cbd5e1}' +
        '.rist-report-split:after{content:"↔";position:absolute;top:64px;left:0;width:18px;text-align:center;font:14px/30px Arial,sans-serif;color:#476779;background:#e2eaf0;border-radius:4px}' +
        '.rist-report-split:hover:before,.rist-report-split:focus:before{background:#226785;width:3px}' +
        '.rist-report-split:hover:after,.rist-report-split:focus:after{background:#226785;color:white}' +
        '.rist-report-split[hidden]{display:none!important}.rist-split-active>.form-pane{padding-right:0}.rist-split-active>.form-pane>.form-scroll{right:0}';
      doc.head.appendChild(style);
    }
    handle.className = 'rist-report-split'; handle.tabIndex = 0; handle.hidden = true;
    handle.setAttribute('role', 'separator'); handle.setAttribute('aria-orientation', 'vertical');
    handle.setAttribute('aria-label', '보고서 좌우 패널 폭 조절');
    handle.title = '좌우로 끌어 폭 조절 · ←/→ 키 · 두 번 클릭 또는 Enter: 기본 폭';
    handle.style.flex = '0 0 ' + SIZE + 'px'; handle.style.width = SIZE + 'px';
    handle.style.minWidth = SIZE + 'px'; handle.style.maxWidth = SIZE + 'px';
    if (options.grid) { handle.style.gridColumn = '2'; handle.style.gridRow = '2'; }
    host.insertBefore(handle, right);
    try {
      var stored = win.localStorage.getItem(options.storageKey), value = Number(stored);
      if (stored !== null && isFinite(value) && value > 0 && value < 1) preferred = value;
    } catch (_) {}
    function persist() {
      try {
        if (preferred === null) win.localStorage.removeItem(options.storageKey);
        else win.localStorage.setItem(options.storageKey, String(preferred));
      } catch (_) {}
    }
    function paint(width) {
      current = Math.max(lower, Math.min(upper, width));
      left.style.width = current + 'px'; left.style.minWidth = '0'; left.style.maxWidth = 'none'; left.style.flex = '0 0 ' + current + 'px';
      right.style.width = (total - current) + 'px'; right.style.minWidth = '0'; right.style.maxWidth = 'none'; right.style.flex = '1 1 0px';
      if (options.grid) {
        host.style.gridTemplateColumns = current + 'px ' + SIZE + 'px minmax(0,1fr)';
        right.style.gridColumn = '3';
      }
      handle.setAttribute('aria-valuemin', String(Math.round(lower / total * 100)));
      handle.setAttribute('aria-valuemax', String(Math.round(upper / total * 100)));
      handle.setAttribute('aria-valuenow', String(Math.round(current / total * 100)));
      handle.setAttribute('aria-valuetext', '입력 ' + Math.round(current / total * 100) + '% · 미리보기 ' + Math.round((total-current) / total * 100) + '%');
    }
    function refresh() {
      if (destroyed) return;
      var css = win.getComputedStyle(host);
      total = host.clientWidth - (parseFloat(css.paddingLeft) || 0) - (parseFloat(css.paddingRight) || 0) - SIZE;
      lower = options.minLeft || 280; upper = total - (options.minRight || 480);
      active = win.innerWidth > options.breakpoint && total > 0 && upper >= lower &&
        !left.hidden && !right.hidden && (!options.enabled || options.enabled());
      handle.hidden = !active; host.classList.toggle('rist-split-active', active);
      if (!active) { restoreLeft(); restoreRight(); restoreHost(); return; }
      paint(preferred === null ? options.defaultLeft(total) : preferred * total);
    }
    function flush() {
      if (frame !== null) { win.cancelAnimationFrame(frame); frame = null; }
      if (pending !== null) { paint(pending); pending = null; }
    }
    function finish(cancelled) {
      if (!drag) return;
      flush();
      if (cancelled) preferred = drag.preferred;
      else if (drag.moved) { preferred = current / total; persist(); }
      doc.body.style.cursor = drag.cursor; doc.body.style.userSelect = drag.select;
      doc.body.style.webkitUserSelect = drag.webkitSelect;
      if (drag.shield.parentNode) drag.shield.parentNode.removeChild(drag.shield);
      drag = null; handle.style.zIndex = ''; refresh();
      // Reposition in-place cell editors, without regenerating or saving a report.
      var event = doc.createEvent('Event'); event.initEvent('resize', false, false); win.dispatchEvent(event);
    }
    function point(event) {
      if (!drag || drag.touch === null) return event.clientX;
      var touches = event.changedTouches || [];
      for (var i = 0; i < touches.length; i++) if (touches[i].identifier === drag.touch) return touches[i].clientX;
      return null;
    }
    function start(event) {
      if (!active || drag || (event.type === 'mousedown' && event.button !== 0)) return;
      if (event.touches && event.touches.length !== 1) return;
      event.preventDefault(); handle.focus();
      var shield = doc.createElement('div');
      shield.style.cssText = 'position:fixed;left:0;top:0;right:0;bottom:0;z-index:2147483646;cursor:col-resize;touch-action:none;background:transparent';
      shield.setAttribute('aria-hidden', 'true'); doc.body.appendChild(shield);
      // Keep the separator above the shield so native click/double-click targets stay intact.
      handle.style.zIndex = '2147483647';
      drag = {x:event.touches ? event.touches[0].clientX : event.clientX, width:current, preferred:preferred,
        touch:event.touches ? event.touches[0].identifier : null, moved:false, shield:shield,
        cursor:doc.body.style.cursor, select:doc.body.style.userSelect, webkitSelect:doc.body.style.webkitUserSelect};
      doc.body.style.cursor = 'col-resize'; doc.body.style.userSelect = 'none'; doc.body.style.webkitUserSelect = 'none';
    }
    function move(event) {
      if (!drag) return;
      var x = point(event); if (x === null) return;
      event.preventDefault(); if (Math.abs(x - drag.x) > 1) drag.moved = true;
      pending = drag.width + x - drag.x;
      if (frame === null) frame = win.requestAnimationFrame(function () { frame = null; flush(); });
    }
    function end(event) { if (drag && point(event) !== null) finish(false); }
    function cancel() { finish(true); }
    function resize() { if (drag) cancel(); else refresh(); }
    function reset() { if (active) { preferred = null; persist(); refresh(); } }
    function key(event) {
      var code = event.key || event.keyCode;
      if ((code === 'Escape' || code === 27) && drag) { event.preventDefault(); cancel(); return; }
      if (event.target !== handle || !active) return;
      var width = current, amount = event.shiftKey ? 50 : 20;
      if (code === 'ArrowLeft' || code === 'Left' || code === 37) width -= amount;
      else if (code === 'ArrowRight' || code === 'Right' || code === 39) width += amount;
      else if (code === 'Home' || code === 36) width = lower;
      else if (code === 'End' || code === 35) width = upper;
      else if (code === 'Enter' || code === 13) { event.preventDefault(); reset(); return; }
      else return;
      event.preventDefault(); paint(width); preferred = current / total; persist();
    }
    handle.addEventListener('mousedown', start); handle.addEventListener('touchstart', start, false);
    handle.addEventListener('dblclick', reset);
    doc.addEventListener('mousemove', move); doc.addEventListener('mouseup', end);
    // Explicit non-passive touch handling, including browsers predating passive options.
    var touchOptions = false;
    try { var probe = Object.defineProperty({}, 'passive', {get:function () { touchOptions = {passive:false}; }});
      win.addEventListener('rist-passive-test', reset, probe); win.removeEventListener('rist-passive-test', reset, probe);
    } catch (_) {}
    doc.addEventListener('touchmove', move, touchOptions); doc.addEventListener('touchend', end);
    doc.addEventListener('touchcancel', cancel); doc.addEventListener('keydown', key);
    win.addEventListener('resize', resize); win.addEventListener('blur', cancel);
    refresh();
    return {refresh:refresh, setLeft:function (pane) {
      if (destroyed) return;
      if (pane !== left) {
        cancel();
        restoreLeft();
        left = pane;
        restoreLeft = remember(left, paneKeys);
      }
      refresh();
    }, destroy:function () {
      cancel(); destroyed = true; restoreLeft(); restoreRight(); restoreHost(); host.classList.remove('rist-split-active');
      handle.parentNode.removeChild(handle);
      doc.removeEventListener('mousemove', move); doc.removeEventListener('mouseup', end);
      doc.removeEventListener('touchmove', move, touchOptions); doc.removeEventListener('touchend', end);
      doc.removeEventListener('touchcancel', cancel); doc.removeEventListener('keydown', key);
      win.removeEventListener('resize', resize); win.removeEventListener('blur', cancel);
    }};
  }
  global.RistReportSplit = {mount:mount};
}(window));

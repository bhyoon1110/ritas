"""Exact-X manual peak placement, separate from click-to-nearest-peak editing."""

import json


def peak_coordinates_html(div_id: str) -> str:
    return _HTML.replace("__PLOT_ID__", json.dumps(div_id))


_HTML = r"""
<style>
.rist-peak-coordinate-overlay { position: fixed; inset: 0; z-index: 12000; background: rgba(15,23,42,.3);
  display: none; align-items: center; justify-content: center; padding: 16px; box-sizing: border-box; }
.rist-peak-coordinate-dialog { width: 420px; max-width: 100%; max-height: 90vh; overflow: auto;
  box-sizing: border-box; padding: 20px; border: 1px solid #cbd5e1; border-radius: 8px;
  background: #fff; color: #243b53; box-shadow: 0 8px 28px rgba(0,0,0,.2); font: 13px Arial,sans-serif; }
.rist-peak-coordinate-dialog h3 { margin: 0 0 14px; font-size: 17px; }
.rist-peak-coordinate-dialog label { display: block; margin: 12px 0; }
.rist-peak-coordinate-dialog select,.rist-peak-coordinate-dialog input { display: block; width: 100%;
  min-width: 0; box-sizing: border-box; margin-top: 6px; padding: 8px; border: 1px solid #9fb3c8;
  border-radius: 4px; background: #fff; color: #243b53; font: inherit; }
.rist-peak-coordinate-dialog p { line-height: 1.6; margin: 10px 0; }
.rist-peak-coordinate-feedback { min-height: 42px; }
.rist-peak-coordinate-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
.rist-peak-coordinate-actions button { padding: 8px 12px; border: 1px solid #9fb3c8; border-radius: 4px;
  background: #f5f7fa; color: #243b53; cursor: pointer; font: inherit; white-space: nowrap; }
.rist-peak-coordinate-actions button[type=submit] { background: #24577b; color: #fff; }
.rist-peak-coordinate-actions button:disabled { opacity: .5; cursor: default; }
.rist-peak-coordinate-button { order: 50; white-space: nowrap; }
</style>
<script>
(function() {
  var gd = document.getElementById(__PLOT_ID__);
  if (!gd || !gd._ristAddPeakAt || gd._ristPeakCoordinates) return;
  var row = gd.querySelector('.rist-plot-control-row');
  if (!row) return;
  var button = document.createElement('button');
  button.type = 'button'; button.className = 'rist-peak-edit-button rist-peak-coordinate-button';
  button.textContent = 'X좌표 추가'; button.title = '시료와 X좌표를 지정하여 피크 추가';
  row.insertBefore(button, gd.querySelector('.rist-peak-delete-button'));
  // A sibling of the graph: no impact on its dimensions, saved images or Plotly events.
  var overlay = document.createElement('div'); overlay.className = 'rist-peak-coordinate-overlay';
  overlay.innerHTML = '<form class="rist-peak-coordinate-dialog" role="dialog" aria-modal="true" aria-label="X좌표로 피크 추가">'
    + '<h3>X좌표로 피크 추가</h3><label>Raw 시료<select aria-label="피크 추가 시료"></select></label>'
    + '<label>X좌표 (cm⁻¹)<input type="number" step="any" inputmode="decimal" aria-label="피크 X좌표"></label>'
    + '<p>입력한 X위치에 표시합니다. 측정점 사이의 Y값은 인접 측정값을 선형 보간합니다. 원본 데이터는 변경하지 않습니다.</p>'
    + '<p class="rist-peak-coordinate-feedback" role="status" aria-live="polite"></p>'
    + '<div class="rist-peak-coordinate-actions"><button type="button">닫기 (Esc)</button><button type="submit">피크 추가</button></div></form>';
  gd.insertAdjacentElement('afterend', overlay);
  var form = overlay.querySelector('form'), sample = overlay.querySelector('select');
  var input = overlay.querySelector('input'), feedback = overlay.querySelector('[role=status]');
  var closeButton = overlay.querySelector('button[type=button]'), submit = overlay.querySelector('button[type=submit]');
  var busy = false, previousFocus = null;
  function group(trace) { var m = trace.meta || {}; return String(m.rist_sample_group || (m.rist_peak && m.rist_peak.sample_group) || ''); }
  function finite(value) { return value != null && value !== '' && Number.isFinite(Number(value)); }
  function parent(curve) {
    var trace = (gd.data || [])[curve];
    return trace && trace.meta && trace.meta.rist_sample_parent && trace.visible !== false && trace.visible !== 'legendonly' ? trace : null;
  }
  function pointAt(curve, x) {
    var trace = parent(curve);
    if (!trace || !Number.isFinite(x)) return null;
    var xs = trace.x || [], ys = trace.y || [], a = -1, b = -1, ratio = 0;
    for (var i = 0; i < Math.min(xs.length, ys.length); i++) {
      if (!finite(xs[i]) || !finite(ys[i])) continue;
      if (Number(xs[i]) === x) { a = b = i; break; }
      if (i === 0 || !finite(xs[i-1]) || !finite(ys[i-1])) continue;
      var left = Number(xs[i-1]), right = Number(xs[i]);
      if ((left < x && x < right) || (right < x && x < left)) {
        a = i-1; b = i; ratio = (x-left)/(right-left); break;
      }
    }
    if (a < 0) return null; // No extrapolation or bridging missing-data gaps.
    function interpolate(values) {
      if (!values || !finite(values[a]) || !finite(values[b])) return null;
      return Number(values[a]) + (Number(values[b])-Number(values[a])) * ratio;
    }
    var point = {x:x, y:interpolate(ys), curve:curve, coordinateInput:true, interpolated:a!==b};
    var meta = trace.meta || {}, toggle = meta.ftir_signal_toggle;
    if (toggle) {
      var abs = interpolate(toggle.absorbance_y), trans = interpolate(toggle.transmittance_y);
      if (abs == null || trans == null) return null;
      point.ftirSignal = {absorbance_y:[abs], transmittance_y:[trans]};
    }
    return Number.isFinite(point.y) ? point : null;
  }
  function chosen() { return sample.value === '' || input.value.trim() === '' ? null : pointAt(Number(sample.value), Number(input.value)); }
  function duplicate(point) {
    var sampleGroup = group(parent(point.curve));
    return (gd.data || []).some(function(trace) {
      var peak = trace.meta && trace.meta.rist_peak;
      return peak && (peak.user || peak.source === 'user') && group(trace) === sampleGroup
        && Math.abs(Number(peak.x)-point.x) < 1e-9;
    });
  }
  function update() {
    if (busy) return;
    var point = chosen(); submit.disabled = !point || duplicate(point) || button.disabled;
    if (!sample.options.length) feedback.textContent = '표시 중인 Raw 시료가 없습니다. 시료를 업로드하거나 표시해 주세요.';
    else if (!point) feedback.textContent = '시료의 측정 범위 안에 있는 유효한 X좌표를 입력하세요. 데이터가 없는 구간에는 추가할 수 없습니다.';
    else if (duplicate(point)) feedback.textContent = '이 시료의 같은 X좌표에 이미 수동 피크가 있습니다.';
    else {
      var meta = parent(point.curve).meta || {};
      var offset = Number(meta.rist_raman_stack_offset || meta.rist_ftir_stack_offset_absorbance || 0);
      feedback.textContent = 'X: ' + point.x + ' cm⁻¹ · Y: ' + Number((point.y-offset).toPrecision(8))
        + (point.interpolated ? ' (선형 보간값)' : ' (측정값)') + (offset ? ' · 스택 이동량 제외' : '');
    }
  }
  function close(restore) {
    overlay.style.display = 'none'; gd._ristPeakCoordinateOpen = false;
    gd.dispatchEvent(new CustomEvent('rist-interaction-mode-change'));
    if (restore !== false) {
      if (gd._ristRestoreNavigation) gd._ristRestoreNavigation();
      if (previousFocus && document.documentElement.contains(previousFocus)) previousFocus.focus();
    }
  }
  button.addEventListener('click', function(ev) {
    ev.preventDefault(); ev.stopPropagation();
    if (button.disabled || busy) return;
    previousFocus = document.activeElement;
    gd.dispatchEvent(new CustomEvent('rist-exclusive-interaction-start', {detail:{mode:'peak-coordinate'}}));
    var selected = sample.value; sample.innerHTML = '';
    (gd.data || []).forEach(function(trace, index) {
      if (!parent(index)) return;
      var option = document.createElement('option'); option.value = String(index);
      option.textContent = (String(trace.name || '').replace(/<br\s*\/?\s*>/gi,' ').replace(/<[^>]*>/g,'').trim() || '이름 없음') + ' · Raw ' + (sample.options.length+1);
      sample.appendChild(option);
    });
    if (Array.prototype.some.call(sample.options, function(option) {return option.value === selected;})) sample.value = selected;
    overlay.style.display = 'flex'; gd._ristPeakCoordinateOpen = true;
    gd.dispatchEvent(new CustomEvent('rist-interaction-mode-change')); update(); input.focus();
  });
  closeButton.addEventListener('click', function() {close();});
  input.addEventListener('input', update); sample.addEventListener('change', update);
  form.addEventListener('submit', function(ev) {
    ev.preventDefault();
    if (busy || !gd._ristPeakCoordinateOpen || button.disabled) return;
    var point = chosen(); if (!point || duplicate(point)) {update(); return;}
    busy = true; submit.disabled = true; sample.disabled = input.disabled = true;
    feedback.textContent = '피크를 추가하는 중입니다…';
    Promise.resolve().then(function() { return gd._ristAddPeakAt(point); }).then(function() {return gd._ristStackReady;}).then(function() {
      feedback.textContent = 'X = ' + point.x + ' cm⁻¹에 피크를 추가했습니다. 다른 좌표도 추가할 수 있습니다.';
    }).catch(function(error) {
      feedback.textContent = '피크 추가에 실패했습니다. 그래프를 확인한 후 다시 시도해 주세요.';
      console.error('RIST coordinate peak failed', error);
    }).then(function() {busy = false; sample.disabled = input.disabled = false; if (gd._ristPeakCoordinateOpen) input.focus();});
  });
  overlay.addEventListener('keydown', function(ev) {
    if (ev.key === 'Escape') {ev.preventDefault(); ev.stopPropagation(); close();}
    if (ev.key === 'Tab') {
      var nodes = Array.prototype.slice.call(overlay.querySelectorAll('button,input,select,textarea')).filter(function(node) {return !node.disabled;});
      var first = nodes[0], last = nodes[nodes.length-1];
      if (ev.shiftKey && document.activeElement === first) {ev.preventDefault(); last.focus();}
      else if (!ev.shiftKey && document.activeElement === last) {ev.preventDefault(); first.focus();}
    }
  });
  gd.addEventListener('rist-exclusive-interaction-start', function(ev) {
    if (gd._ristPeakCoordinateOpen && (!ev.detail || ev.detail.mode !== 'peak-coordinate')) close(false);
  });
  gd.addEventListener('rist-peak-actions-disabled', function(ev) {
    button.disabled = !!(ev.detail && ev.detail.disabled); if (button.disabled) close(false);
  });
  ['rist-plot-data-replaced','rist-history-restored'].forEach(function(type) {
    gd.addEventListener(type, function() {if (gd._ristPeakCoordinateOpen) close(false);});
  });
  button.disabled = !!gd.querySelector('.rist-peak-add-button').disabled;
  gd._ristPeakCoordinates = {pointAt:pointAt};
})();
</script>
"""

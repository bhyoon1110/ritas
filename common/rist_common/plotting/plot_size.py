"""Canvas sizing controls shared by the interactive spectrum pages."""

import json
import re


def plot_size_controls_html(div_id: str) -> str:
    # No CSS aspect-ratio/ResizeObserver dependency: also shipped to Chrome 49.
    css_id = re.sub(r"([^a-zA-Z0-9_-])", r"\\\1", div_id)
    return _HTML.replace("__PLOT_ID__", json.dumps(div_id)).replace("__PLOT_CSS__", css_id)


_HTML = r"""
<style>
.rist-plot-size-controls { border: 1px solid #cbd5e1; border-radius: 5px; padding: 8px; margin: 0; min-width: 0; }
.rist-plot-size-controls legend { font-weight: 700; font-size: 12px; padding: 0 4px; }
.rist-plot-size-fields { display: flex; flex-wrap: wrap; }
.rist-plot-size-fields > label { flex: 1 1 120px; margin: 0 4px 8px; }
.rist-plot-size-controls .rist-axis-control-actions { margin-top: 8px; }
.rist-plot-size-status { margin: 6px 0 0; font-size: 11px; color: #475569; }
.rist-plot-size-lock { display: flex; align-items: center; font-size: 12px; gap: 4px; }
.rist-plot-size-lock input { width: auto; }
.rist-plot-resize-handle { position: absolute; right: 3px; bottom: 3px; z-index: 57; cursor: nwse-resize; touch-action: none; user-select: none; border: 1px solid #9fb3c8; border-radius: 4px; background: #fff; color: #243b53; font: 12px Arial,sans-serif; padding: 4px 7px; white-space: nowrap; }
.rist-plot-resize-preview { position: fixed; pointer-events: none; z-index: 10000; border: 2px dashed #2563eb; box-sizing: border-box; background: rgba(37,99,235,.04); }
.rist-plot-resize-preview span { position: absolute; right: 0; bottom: 0; padding: 4px 8px; background: #1e40af; color: white; font: 12px Arial,sans-serif; white-space: nowrap; }
.rist-axis-control-panel.is-axis-specific .rist-plot-size-controls { display: none; }
.rist-axis-control-panel { max-height: calc(100vh - 120px); overflow-y: auto; }
#__PLOT_CSS__.rist-size-compact .rist-ftir-tools-toggle,
#__PLOT_CSS__.rist-size-compact .rist-raman-tools-toggle {
  display: inline-flex; position: absolute; top: 34px; right: 8px; z-index: 56;
  align-items: center; justify-content: center; width: 30px; height: 30px; padding: 0;
  border: 1px solid #9fb3c8; border-radius: 4px; background: #fff; color: #243b53; cursor: pointer;
}
#__PLOT_CSS__.rist-size-compact .rist-tools-toggle-icon { width: 16px; height: 16px; }
#__PLOT_CSS__.rist-size-compact .rist-plot-control-row {
  display: none !important; left: auto !important; right: 8px !important; top: 70px !important;
  width: calc(100% - 24px) !important; max-width: 860px; max-height: 300px;
  flex-wrap: wrap; align-items: flex-start; justify-content: flex-start; overflow: auto;
  padding: 8px; border: 1px solid #c7d0dd; border-radius: 6px; background: rgba(255,255,255,0.98);
  box-shadow: 0 4px 18px rgba(15,23,42,0.16); box-sizing: border-box; z-index: 55;
}
#__PLOT_CSS__.rist-size-compact.rist-ftir-tools-open .rist-plot-control-row,
#__PLOT_CSS__.rist-size-compact.rist-raman-tools-open .rist-plot-control-row { display: flex !important; }
#__PLOT_CSS__.rist-size-compact .rist-ftir-tools-head,
#__PLOT_CSS__.rist-size-compact .rist-raman-tools-head {
  display: flex; order: -100; flex: 1 0 100%; align-items: center; height: 28px;
  border-bottom: 1px solid #d7dee8; font: bold 12px Arial, sans-serif;
}
#__PLOT_CSS__.rist-size-compact .rist-plot-control-row > * { flex-shrink: 0; }
#__PLOT_CSS__.rist-size-compact .rist-ftir-tools-head span,
#__PLOT_CSS__.rist-size-compact .rist-raman-tools-head span { flex: 1; }
#__PLOT_CSS__.rist-size-compact .rist-ftir-tools-opacity,
#__PLOT_CSS__.rist-size-compact .rist-raman-tools-opacity { width: 76px; margin: 0 8px; }
</style>
<script>
(function() {
  var gd = document.getElementById(__PLOT_ID__);
  if (!gd || gd._ristPlotSize || !window.Plotly) return;
  var panel = gd.querySelector(".rist-axis-control-panel");
  if (!panel) return;
  var controls = document.createElement("fieldset");
  controls.className = "rist-plot-size-controls";
  controls.innerHTML = "<legend>그래프 크기</legend>"
    + "<div class='rist-plot-size-fields'>"
    + "<label class='rist-axis-control-field'><span>최대 폭 (px)</span><input type='number' min='320' max='2400' step='1' placeholder='자동 (화면 너비)' aria-label='그래프 최대 폭' data-plot-width></label>"
    + "<label class='rist-axis-control-field'><span>높이 (px)</span><input type='number' min='240' max='2400' step='1' placeholder='자동' aria-label='그래프 높이' data-plot-height></label>"
    + "<label class='rist-axis-control-field'><span>그래프 비율 (범례 제외)</span><select aria-label='그래프 비율' data-plot-ratio>"
    + "<option value='auto'>자동</option><option value='4:3'>4:3</option><option value='16:9'>16:9</option><option value='1:1'>1:1</option><option value='3:2'>3:2</option><option value='5:4'>5:4</option><option value='16:10'>16:10</option><option value='2:1'>2:1</option><option value='21:9'>21:9</option><option value='3:4'>3:4 (세로)</option><option value='2:3'>2:3 (세로)</option><option value='9:16'>9:16 (세로)</option><option value='custom'>직접 입력</option></select></label></div>"
    + "<label class='rist-plot-size-lock'><input type='checkbox' data-plot-size-lock>드래그할 때 비율 유지</label>"
    + "<p class='rist-axis-control-help'>폭 320~2400px, 높이 240~2400px. 높이를 입력하면 직접 입력 비율로 전환됩니다. 오른쪽 아래 ↘ 크기를 드래그하거나 방향키로 조절할 수 있습니다. 크기·비율은 범례 여백을 제외하고 축을 포함한 그래프 이미지 기준입니다. 범례는 그래프 안에 겹쳐 표시되며 위치를 옮길 수 있습니다. 범례 길이·글자 크기·표시 여부는 그래프 크기에 영향을 주지 않습니다. 좁은 화면에서는 가독성을 위한 최소 높이를 적용하며, 저장 이미지와 보고서는 설정 비율을 유지합니다. 긴 변은 최대 2400px입니다.</p>"
    + "<div class='rist-axis-control-actions'><button type='button' data-plot-size-reset>자동 크기</button><button type='button' data-plot-size-apply>크기 적용</button></div>"
    + "<p class='rist-plot-size-status' role='status' aria-live='polite'></p>";
  var body = panel.querySelector(".rist-axis-control-body");
  body.insertBefore(controls, body.querySelector(".rist-axis-control-footer"));
  var widthInput = controls.querySelector("[data-plot-width]");
  var heightInput = controls.querySelector("[data-plot-height]");
  var lockInput = controls.querySelector("[data-plot-size-lock]");
  var ratioInput = controls.querySelector("[data-plot-ratio]");
  var status = controls.querySelector(".rist-plot-size-status");
  var ratios = {"4:3": 4 / 3, "16:9": 16 / 9, "1:1": 1, "3:2": 3 / 2,
    "5:4": 5 / 4, "16:10": 16 / 10, "2:1": 2, "21:9": 21 / 9,
    "3:4": 3 / 4, "2:3": 2 / 3, "9:16": 9 / 16};
  var styleKeys = ["width", "height", "min-height", "max-width", "margin-left", "margin-right"];
  var originalStyles = {};
  styleKeys.forEach(function(key) {
    originalStyles[key] = [gd.style.getPropertyValue(key), gd.style.getPropertyPriority(key)];
  });
  var applied = false;
  var appliedSignature = "";
  var timer = 0;
  var busy = false;

  function settings(layout) {
    layout = layout || gd.layout;
    var stored = layout && layout.meta && layout.meta.ristPlotSize || {};
    var width = Number(stored.width);
    var value = {width: isFinite(width) && width >= 320 && width <= 2400 ? Math.round(width) : null,
      ratio: Object.prototype.hasOwnProperty.call(ratios, stored.ratio) ? stored.ratio : "auto"};
    var height = Number(stored.height);
    if (stored.ratio === "custom" && value.width !== null && isFinite(height) && height >= 240 && height <= 2400) {
      value.ratio = "custom";
      value.height = Math.round(height);
    }
    return value;
  }
  function aspect(value) { return value.ratio === "custom" ? value.width / value.height : ratios[value.ratio]; }
  function active(value) { return value.width !== null || value.ratio !== "auto"; }
  function availableWidth() {
    var parent = gd.parentElement;
    var css = parent ? getComputedStyle(parent) : {};
    var width = parent ? parent.clientWidth - (parseFloat(css.paddingLeft) || 0)
      - (parseFloat(css.paddingRight) || 0) : 0;
    return Math.max(1, Math.floor(width || gd.clientWidth || window.innerWidth || 1000));
  }
  function viewportWidth() {
    var value = settings();
    return Math.min(availableWidth(), value.width || availableWidth());
  }
  function signature() {
    var value = settings();
    return [value.width, value.height, value.ratio, availableWidth()].join("|");
  }
  function restoreStyles() {
    styleKeys.forEach(function(key) {
      var original = originalStyles[key];
      if (original[0]) gd.style.setProperty(key, original[0], original[1]);
      else gd.style.removeProperty(key);
    });
  }
  // Size the graph canvas independently of the external legend gutter and
  // Plotly's legend auto-margins. Keep labels/axes; float the legend inside the
  // paper instead of allowing its text or row count to shrink the spectrum.
  function legendIndependentLayout(updates, layout) {
    var legend = layout.legend || {};
    updates["margin.r"] = 24;
    updates["margin.b"] = 70;
    updates["margin.autoexpand"] = false;
    updates["legend.orientation"] = "v";
    updates["legend.xref"] = "paper";
    updates["legend.yref"] = "paper";
    function inside(value) { return typeof value === "number" && isFinite(value) && value >= 0 && value <= 1; }
    var useProposed = inside(updates["legend.x"]) && inside(updates["legend.y"]);
    var useCurrent = inside(legend.x) && inside(legend.y);
    ["x", "y"].forEach(function(axis) {
      var value = legend[axis], anchor = legend[axis + "anchor"];
      // Responsive layout suggests outside positions; preserve a user's
      // inside position unless a work template supplies another inside pair.
      if (useProposed) {
        value = updates["legend." + axis]; anchor = updates["legend." + axis + "anchor"];
      } else if (!useCurrent) {
        value = axis === "x" ? 0.02 : 0.98;
        anchor = axis === "x" ? "left" : "top";
      }
      updates["legend." + axis] = value;
      updates["legend." + axis + "anchor"] = anchor || (axis === "x" ? "left" : "top");
    });
    return updates;
  }
  function prepareExportLayout(layout) {
    if (!active(settings(layout))) return layout;
    var result = Object.assign({}, layout, {margin: Object.assign({}, layout.margin || {}),
      legend: Object.assign({}, layout.legend || {})});
    var patch = legendIndependentLayout({}, layout);
    Object.keys(patch).forEach(function(key) {
      var parts = key.split("."); result[parts[0]][parts[1]] = patch[key];
    });
    return result;
  }
  function applyLayout(updates) {
    var value = settings();
    appliedSignature = signature();
    if (!active(value)) {
      gd.classList.remove("rist-size-compact");
      // Workspace/template restores may carry a desktop Plotly width even
      // without an explicit size preference. Recompute it for this viewport.
      updates.width = null;
      updates.autosize = true;
      updates["margin.autoexpand"] = true;
      if (applied) {
        restoreStyles();
        if (updates.height == null) updates.height = null;
      }
      applied = false;
      return updates;
    }
    restoreStyles();
    var width = viewportWidth();
    gd.classList.toggle("rist-size-compact", width <= 1440);
    gd.style.setProperty("width", width + "px", "important");
    gd.style.setProperty("max-width", "100%", "important");
    gd.style.setProperty("margin-left", "auto");
    gd.style.setProperty("margin-right", "auto");
    var ratio = aspect(value);
    var height = ratio ? Math.max(width < 600 ? 420 : 240, Math.round(width / ratio))
      : Math.max(420, Math.round(parseFloat(getComputedStyle(gd).height) || updates.height || 720));
    gd.style.setProperty("height", height + "px", "important");
    gd.style.setProperty("min-height", "0", "important");
    updates.width = width;
    updates.height = height;
    updates.autosize = false;
    legendIndependentLayout(updates, gd.layout || {});
    applied = true;
    return updates;
  }
  function exportDimensions(fallback) {
    var value = settings();
    if (!active(value)) return fallback;
    // Keep export resolution independent of a narrow/mobile viewport.
    var width = value.width || Math.max(900, Math.min(2400, viewportWidth()));
    var ratio = aspect(value);
    if (ratio && width / ratio > 2400) width = Math.round(2400 * ratio);
    return {width: width, height: ratio ? Math.round(width / ratio)
      : Math.max(420, Math.round((gd._fullLayout || {}).height || gd.clientHeight || 720))};
  }
  function preserve(layout) {
    var value = settings();
    if (active(value)) {
      layout.meta = Object.assign({}, layout.meta || {}, {ristPlotSize: value});
    }
    return layout;
  }
  function refreshInputs() {
    var value = settings();
    widthInput.value = value.width == null ? "" : String(value.width);
    ratioInput.value = value.ratio;
    refreshHeight();
  }
  function refreshHeight() {
    var ratio = ratios[ratioInput.value];
    if (ratio) heightInput.value = String(Math.round(Math.min(2400, (Number(widthInput.value) || availableWidth()) / ratio)));
    else if (ratioInput.value === "auto") heightInput.value = "";
    else heightInput.value = String(settings().height || heightInput.value || Math.round(gd.clientHeight) || 720);
  }
  function apply() {
    if (busy) { schedule(); return Promise.resolve(); }
    busy = true;
    var task = typeof gd._ristApplyResponsiveLayout === "function"
      ? gd._ristApplyResponsiveLayout() : window.Plotly.relayout(gd, applyLayout({}));
    return Promise.resolve(task).then(function() {
      busy = false;
    }, function(err) {
      busy = false;
      status.textContent = "그래프 크기를 적용하지 못했습니다. 다시 시도해 주세요.";
      console.error("RIST plot sizing failed", err);
    });
  }
  function schedule() {
    if (timer) return;
    timer = window.setTimeout(function() {
      timer = 0;
      if (gd.isConnected === false) return;
      if (active(settings()) || applied) apply();
    }, 60);
  }
  function commit(reset) {
    var text = widthInput.value.trim();
    var width = text === "" ? null : Number(text);
    if (!reset && ((widthInput.validity && widthInput.validity.badInput)
        || (text !== "" && (!isFinite(width) || width < 320 || width > 2400 || width !== Math.round(width))))) {
      status.textContent = "폭은 320~2400 사이의 정수를 입력하거나 비워 주세요.";
      widthInput.setAttribute("aria-invalid", "true");
      return;
    }
    widthInput.removeAttribute("aria-invalid");
    var value = {width: width, ratio: ratioInput.value};
    if (!reset && value.ratio === "custom") {
      var height = Number(heightInput.value);
      if (!heightInput.value.trim() || (heightInput.validity && heightInput.validity.badInput)
          || !isFinite(height) || height < 240 || height > 2400 || height !== Math.round(height)) {
        status.textContent = "높이는 240~2400 사이의 정수를 입력해 주세요.";
        heightInput.setAttribute("aria-invalid", "true");
        return;
      }
      value.width = width || Math.max(320, Math.min(2400, availableWidth()));
      value.height = height;
    }
    if (ratios[value.ratio] && width && width / ratios[value.ratio] > 2400) value.width = Math.round(2400 * ratios[value.ratio]);
    heightInput.removeAttribute("aria-invalid");
    return save(reset ? {width: null, ratio: "auto"} : value, reset);
  }
  function save(value, reset) {
    if (gd._ristHistory) gd._ristHistory.capture();
    var meta = Object.assign({}, gd.layout.meta || {});
    meta.ristPlotSize = value;
    return window.Plotly.relayout(gd, {meta: meta}).then(function() {
      refreshInputs();
      return apply();
    }).then(function() {
      status.textContent = reset ? "자동 크기로 복원했습니다." : "그래프 크기를 적용했습니다.";
      gd.dispatchEvent(new CustomEvent("rist-plot-size-change"));
    }).catch(function(err) { status.textContent = "그래프 크기를 저장하지 못했습니다. 다시 시도해 주세요."; console.error(err); });
  }
  controls.querySelector("[data-plot-size-apply]").addEventListener("click", function() { commit(false); });
  controls.querySelector("[data-plot-size-reset]").addEventListener("click", function() { commit(true); });
  [widthInput, heightInput].forEach(function(input) {
    input.addEventListener("keydown", function(ev) {
      if (ev.key === "Enter") { ev.preventDefault(); ev.stopPropagation(); commit(false); }
    });
  });
  heightInput.addEventListener("input", function() { ratioInput.value = "custom"; });
  widthInput.addEventListener("input", function() { if (ratioInput.value !== "custom") refreshHeight(); });
  ratioInput.addEventListener("change", refreshHeight);

  // Preview only during dragging: avoid repeatedly rendering large RAW traces.
  var handle = document.createElement("button");
  handle.type = "button";
  handle.className = "rist-plot-resize-handle";
  handle.textContent = "↘ 크기";
  handle.title = "드래그로 그래프 크기 조절 · 방향키 10px / Shift+방향키 1px · Esc 취소";
  handle.setAttribute("aria-label", "그래프 크기 조절");
  handle.setAttribute("touch-action", "none"); // PEP/Chrome49 also needs the attribute.
  gd.appendChild(handle);
  var drag = null;
  function bounded(width, height, ratio) {
    if (ratio) {
      width = Math.max(Math.max(320, 240 * ratio), Math.min(Math.min(2400, 2400 * ratio), width));
      height = width / ratio;
    }
    return {width: Math.round(Math.max(320, Math.min(2400, width))),
      height: Math.round(Math.max(240, Math.min(2400, height))), ratio: "custom"};
  }
  function point(ev) { return ev.touches ? ev.touches[0] : ev; }
  function startDrag(ev) {
    if (drag || (ev.button != null && ev.button !== 0)) return;
    ev.preventDefault(); ev.stopPropagation();
    var rect = gd.getBoundingClientRect(), p = point(ev);
    var dimensions = exportDimensions({width: rect.width || gd.clientWidth, height: rect.height || gd.clientHeight});
    var preview = document.createElement("div");
    preview.className = "rist-plot-resize-preview";
    preview.innerHTML = "<span></span>";
    document.body.appendChild(preview);
    drag = {x: p.clientX, y: p.clientY, rect: rect, width: dimensions.width, height: dimensions.height,
      ratio: lockInput.checked ? dimensions.width / dimensions.height : null, preview: preview, value: null, id: ev.pointerId};
    if (handle.setPointerCapture && ev.pointerId != null) handle.setPointerCapture(ev.pointerId);
    handle.focus();
    moveDrag(ev);
  }
  function moveDrag(ev) {
    if (!drag || (ev.pointerId != null && ev.pointerId !== drag.id)) return;
    if (ev.cancelable) ev.preventDefault();
    ev.stopPropagation();
    var p = point(ev);
    if (!p) return;
    var dx = p.clientX - drag.x, dy = p.clientY - drag.y;
    var width = drag.width + dx;
    if (drag.ratio && Math.abs(dy * drag.ratio) > Math.abs(dx)) width = drag.width + dy * drag.ratio;
    drag.value = bounded(width, drag.height + dy, drag.ratio);
    var shownWidth = Math.min(availableWidth(), drag.value.width);
    var shownHeight = Math.max(shownWidth < 600 ? 420 : 240, shownWidth * drag.value.height / drag.value.width);
    var css = drag.preview.style;
    css.left = (drag.rect.left + ((drag.rect.width || gd.clientWidth) - shownWidth) / 2) + "px";
    css.top = drag.rect.top + "px"; css.width = shownWidth + "px"; css.height = shownHeight + "px";
    drag.preview.firstChild.textContent = drag.value.width + " × " + drag.value.height + " px";
  }
  function endDrag(ev, cancel) {
    if (!drag || (ev && ev.pointerId != null && ev.pointerId !== drag.id)) return;
    if (ev) { ev.stopPropagation(); if (ev.cancelable) ev.preventDefault(); }
    var finished = drag; drag = null;
    finished.preview.remove();
    if (handle.releasePointerCapture && finished.id != null && handle.hasPointerCapture(finished.id)) handle.releasePointerCapture(finished.id);
    if (!cancel && finished.value && (finished.value.width !== finished.width || finished.value.height !== finished.height)) save(finished.value, false);
  }
  if (window.PointerEvent) {
    handle.addEventListener("pointerdown", startDrag);
    window.addEventListener("pointermove", moveDrag, {passive: false});
    window.addEventListener("pointerup", function(ev) { endDrag(ev, false); });
    window.addEventListener("pointercancel", function(ev) { endDrag(ev, true); });
    handle.addEventListener("lostpointercapture", function(ev) { endDrag(ev, true); });
  } else {
    handle.addEventListener("mousedown", startDrag);
    handle.addEventListener("touchstart", startDrag, {passive: false});
    window.addEventListener("mousemove", moveDrag);
    window.addEventListener("touchmove", moveDrag, {passive: false});
    window.addEventListener("mouseup", function(ev) { endDrag(ev, false); });
    window.addEventListener("touchend", function(ev) { endDrag(ev, false); });
    window.addEventListener("touchcancel", function(ev) { endDrag(ev, true); });
  }
  handle.addEventListener("click", function(ev) { ev.stopPropagation(); });
  handle.addEventListener("dblclick", function(ev) { ev.stopPropagation(); });
  handle.addEventListener("keydown", function(ev) {
    if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].indexOf(ev.key) < 0 || drag) return;
    ev.preventDefault(); ev.stopPropagation();
    var size = exportDimensions({width: gd.clientWidth, height: gd.clientHeight}), step = ev.shiftKey ? 1 : 10;
    var dx = ev.key === "ArrowLeft" ? -step : ev.key === "ArrowRight" ? step : 0;
    var dy = ev.key === "ArrowUp" ? -step : ev.key === "ArrowDown" ? step : 0;
    var ratio = lockInput.checked ? size.width / size.height : null;
    save(bounded(size.width + dx + (ratio ? dy * ratio : 0), size.height + dy, ratio), false);
  });
  window.addEventListener("keydown", function(ev) { if (drag && ev.key === "Escape") endDrag(ev, true); }, true);
  window.addEventListener("blur", function() { endDrag(null, true); });
  gd._ristPlotSize = {applyLayout: applyLayout, viewportWidth: viewportWidth,
    exportDimensions: exportDimensions, prepareExportLayout: prepareExportLayout, preserve: preserve};
  gd.on("plotly_afterplot", function() {
    if (!busy && signature() !== appliedSignature) schedule();
  });
  ["rist-history-restored", "rist-plot-data-replaced"].forEach(function(name) {
    gd.addEventListener(name, function() { endDrag(null, true); refreshInputs(); schedule(); });
  });
  window.addEventListener("resize", schedule);
  window.addEventListener("pagehide", function() { endDrag(null, true); window.clearTimeout(timer); timer = 0; });
  refreshInputs();
  schedule();
})();
</script>
"""

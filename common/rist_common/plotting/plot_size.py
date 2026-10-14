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
    + "<label class='rist-axis-control-field'><span>전체 그래프 비율</span><select aria-label='그래프 비율' data-plot-ratio>"
    + "<option value='auto'>자동</option><option value='4:3'>4:3</option><option value='16:9'>16:9</option><option value='1:1'>1:1</option></select></label></div>"
    + "<p class='rist-axis-control-help'>폭은 320~2400px, 빈칸은 화면 너비입니다. 비율은 축·범례를 포함한 전체 이미지 기준이며 데이터와 축 범위는 바뀌지 않습니다. 좁은 화면에서는 읽기 쉬운 높이를 확보하고, 저장 이미지는 선택한 비율을 유지합니다.</p>"
    + "<div class='rist-axis-control-actions'><button type='button' data-plot-size-reset>자동 크기</button><button type='button' data-plot-size-apply>크기 적용</button></div>"
    + "<p class='rist-plot-size-status' role='status' aria-live='polite'></p>";
  var body = panel.querySelector(".rist-axis-control-body");
  body.insertBefore(controls, body.querySelector(".rist-axis-control-footer"));
  var widthInput = controls.querySelector("[data-plot-width]");
  var ratioInput = controls.querySelector("[data-plot-ratio]");
  var status = controls.querySelector(".rist-plot-size-status");
  var ratios = {"4:3": 4 / 3, "16:9": 16 / 9, "1:1": 1};
  var styleKeys = ["width", "height", "min-height", "max-width", "margin-left", "margin-right"];
  var originalStyles = {};
  styleKeys.forEach(function(key) {
    originalStyles[key] = [gd.style.getPropertyValue(key), gd.style.getPropertyPriority(key)];
  });
  var applied = false;
  var appliedSignature = "";
  var timer = 0;
  var busy = false;

  function settings() {
    var stored = gd.layout && gd.layout.meta && gd.layout.meta.ristPlotSize || {};
    var width = Number(stored.width);
    return {width: isFinite(width) && width >= 320 && width <= 2400 ? Math.round(width) : null,
      ratio: Object.prototype.hasOwnProperty.call(ratios, stored.ratio) ? stored.ratio : "auto"};
  }
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
    return [value.width, value.ratio, availableWidth()].join("|");
  }
  function restoreStyles() {
    styleKeys.forEach(function(key) {
      var original = originalStyles[key];
      if (original[0]) gd.style.setProperty(key, original[0], original[1]);
      else gd.style.removeProperty(key);
    });
  }
  function applyLayout(updates) {
    var value = settings();
    appliedSignature = signature();
    if (!active(value)) {
      gd.classList.remove("rist-size-compact");
      if (applied) {
        restoreStyles();
        updates.width = null;
        updates.autosize = true;
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
    var ratio = ratios[value.ratio];
    var height = ratio ? Math.max(420, Math.round(width / ratio))
      : Math.max(420, Math.round(parseFloat(getComputedStyle(gd).height) || updates.height || 720));
    gd.style.setProperty("height", height + "px", "important");
    gd.style.setProperty("min-height", "0", "important");
    updates.width = width;
    updates.height = height;
    updates.autosize = false;
    applied = true;
    return updates;
  }
  function exportDimensions(fallback) {
    var value = settings();
    if (!active(value)) return fallback;
    // Keep export resolution independent of a narrow/mobile viewport.
    var width = value.width || Math.max(900, Math.min(2400, viewportWidth()));
    var ratio = ratios[value.ratio];
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
    if (gd._ristHistory) gd._ristHistory.capture();
    var meta = Object.assign({}, gd.layout.meta || {});
    meta.ristPlotSize = reset ? {width: null, ratio: "auto"} : {width: width, ratio: ratioInput.value};
    window.Plotly.relayout(gd, {meta: meta}).then(function() {
      refreshInputs();
      return apply();
    }).then(function() {
      status.textContent = reset ? "자동 크기로 복원했습니다." : "그래프 크기를 적용했습니다.";
      gd.dispatchEvent(new CustomEvent("rist-plot-size-change"));
    });
  }
  controls.querySelector("[data-plot-size-apply]").addEventListener("click", function() { commit(false); });
  controls.querySelector("[data-plot-size-reset]").addEventListener("click", function() { commit(true); });
  widthInput.addEventListener("keydown", function(ev) {
    if (ev.key === "Enter") { ev.preventDefault(); ev.stopPropagation(); commit(false); }
  });
  gd._ristPlotSize = {applyLayout: applyLayout, viewportWidth: viewportWidth,
    exportDimensions: exportDimensions, preserve: preserve};
  gd.on("plotly_afterplot", function() {
    if (!busy && signature() !== appliedSignature) schedule();
  });
  ["rist-history-restored", "rist-plot-data-replaced"].forEach(function(name) {
    gd.addEventListener(name, function() { refreshInputs(); schedule(); });
  });
  window.addEventListener("resize", schedule);
  window.addEventListener("pagehide", function() { window.clearTimeout(timer); timer = 0; });
  refreshInputs();
  schedule();
})();
</script>
"""

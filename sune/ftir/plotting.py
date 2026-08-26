# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: FTIR 분석용 Plotly 그림 생성기(전처리/피크/막대/비교).
#            공통 plot_style 모듈의 스타일·출력 헬퍼와 연계된다.
# 실행 방법: 모듈 — 직접 실행하지 않고 import해서 사용 (ftir_analyze.py / ftir.cli 를 통해 동작)
# ─────────────────────────────────────────────────────────────────────────────
"""Plotly figure builders for FTIR analysis (preprocess / peaks / bar / comparison)."""

import os
import json

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy.signal import find_peaks
from rist_common.plotting import peak_editor_js

from .findings import assign_group_candidates
from .peaks import build_interactive_peak_candidates
from .preprocess import load_csv, preprocess

SAMPLE_PALETTE = [
    "#2563eb", "#dc2626", "#16a34a", "#ea580c", "#7c3aed",
    "#0891b2", "#be123c", "#4d7c0f", "#9333ea", "#0f766e",
]
DEFAULT_ASSIGNMENT_LIBRARY_ID = "general-ftir"


def _transmittance_percent(absorbance):
    values = np.asarray(absorbance, dtype=float)
    return 100.0 * np.power(10.0, -values)


def _merge_trace_meta(trace, values):
    meta = trace.meta if isinstance(trace.meta, dict) else {}
    trace.meta = {**meta, **values}


def _enable_abs_trans_toggle(trace, absorbance_y, *, absorbance_offset=0.0,
                             transmittance_offset=0.0):
    abs_values = np.asarray(absorbance_y, dtype=float)
    base_abs = abs_values - absorbance_offset
    trans_values = _transmittance_percent(base_abs) + transmittance_offset
    _merge_trace_meta(trace, {
        "ftir_signal_toggle": {
            "absorbance_y": abs_values.tolist(),
            "transmittance_y": trans_values.tolist(),
        }
    })
    return trace


def _peak_label_text(wn, label):
    return (
        f"<b>{wn:.0f}</b><br>"
        f"<span style='font-size:10px'>{label}</span>"
    )


def _peak_display_name(names):
    if not names:
        return ""
    return "<br>".join(names)


def _peak_assignment_color(assignments):
    for item in assignments:
        if item.get("library_id") != DEFAULT_ASSIGNMENT_LIBRARY_ID:
            return item["color"]
    return assignments[0]["color"]


def _peak_assignment(wn, func_groups):
    assignments = assign_group_candidates(wn, func_groups)
    if not assignments:
        return {
            "unknown": True,
            "display_name": f"{wn:.0f} cm⁻¹",
            "color": "#9ca3af",
            "note": "",
            "assignments": [],
        }
    unique_names = list(dict.fromkeys(item["name"] for item in assignments))
    notes = []
    for item in assignments:
        source = item["library_name"] or item["library_id"]
        detail = f"{source}: {item['name']}"
        if item["note"]:
            detail += f" — {item['note']}"
        notes.append(detail)
    return {
        "unknown": False,
        "display_name": _peak_display_name(unique_names),
        "color": _peak_assignment_color(assignments),
        "note": "<br>".join(notes),
        "assignments": assignments,
    }


def _sample_key(index):
    return f"sample:{index}"


def ftir_peak_label_sync_js(div_id: str) -> str:
    """Backward-compatible wrapper for the shared peak editor."""
    return peak_editor_js(div_id)


def ftir_abs_trans_toggle_js(div_id: str, *, yaxis_titles: dict[str, dict[str, str]]) -> str:
    """FT-IR HTML 그래프에서 흡광도/투과도 표시를 전환하는 JS 스니펫."""
    titles_json = json.dumps(yaxis_titles, ensure_ascii=False)
    return f"""
<style>
#{div_id} .rist-plot-control-row {{
  position: absolute;
  top: 58px;
  right: 30px;
  z-index: 20;
  display: flex;
  gap: 8px;
  align-items: center;
}}
#{div_id} .rist-ftir-unit-toggle {{
  order: 110;
  border: 1px solid #c7d0dd;
  border-radius: 4px;
  background: rgba(255,255,255,0.92);
  color: #1f2933;
  cursor: pointer;
  font: 12px Arial, sans-serif;
  padding: 5px 9px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.08);
}}
</style>
<script>
(function() {{
  var gd = document.getElementById("{div_id}");
  if (!gd) return;
  var TITLES = {titles_json};
  var mode = "absorbance";

  function tracesWithToggle() {{
    var data = gd.data || [];
    var out = [];
    for (var i = 0; i < data.length; i++) {{
      var meta = data[i] && data[i].meta && data[i].meta.ftir_signal_toggle;
      if (meta && meta.absorbance_y && meta.transmittance_y) {{
        out.push([i, meta]);
      }}
    }}
    return out;
  }}

  function applyMode(nextMode) {{
    if (!window.Plotly) return;
    gd._ristFtirSignalMode = nextMode;
    if (!gd._ristFtirUnitOriginalShapes) {{
      gd._ristFtirUnitOriginalShapes = (gd.layout.shapes || []).slice();
      gd._ristFtirUnitOriginalAnnotations = (gd.layout.annotations || []).map(function(a) {{
        return Object.assign({{}}, a);
      }});
    }}
    var pairs = tracesWithToggle();
    var indexes = [];
    var ys = [];
    pairs.forEach(function(pair) {{
      indexes.push(pair[0]);
      ys.push(nextMode === "transmittance"
        ? pair[1].transmittance_y
        : pair[1].absorbance_y);
    }});
    var restyle = ys.length ? window.Plotly.restyle(gd, {{ y: ys }}, indexes) : Promise.resolve();
    restyle.then(function() {{
      var layout = {{}};
      Object.keys(TITLES).forEach(function(axis) {{
        var t = TITLES[axis] || {{}};
        layout[axis + ".title.text"] = nextMode === "transmittance"
          ? t.transmittance
          : t.absorbance;
      }});
      if (nextMode === "transmittance") {{
        layout.shapes = [];
        layout.annotations = [];
      }} else {{
        layout.shapes = gd._ristFtirUnitOriginalShapes || [];
        layout.annotations = gd._ristFtirUnitOriginalAnnotations || [];
      }}
      return window.Plotly.relayout(gd, layout);
    }}).then(function() {{
      gd.dispatchEvent(new CustomEvent("rist-ftir-signal-mode-change", {{
        detail: {{mode: nextMode}}
      }}));
    }});
    mode = nextMode;
    btn.textContent = nextMode === "transmittance" ? "흡광도 보기" : "투과도 보기";
  }}

  var btn = document.createElement("button");
  btn.type = "button";
  btn.className = "rist-ftir-unit-toggle";
  btn.textContent = "투과도 보기";
  btn.addEventListener("click", function(ev) {{
    ev.preventDefault();
    ev.stopPropagation();
    gd.dispatchEvent(new CustomEvent("rist-exclusive-interaction-start", {{
      detail: {{mode: "ftir-signal-mode"}}
    }}));
    applyMode(mode === "absorbance" ? "transmittance" : "absorbance");
  }});
  if (getComputedStyle(gd).position === "static") gd.style.position = "relative";
  var toolbar = gd.querySelector(".rist-plot-control-row");
  if (!toolbar) {{
    toolbar = document.createElement("div");
    toolbar.className = "rist-plot-control-row";
    gd.appendChild(toolbar);
  }}
  toolbar.appendChild(btn);
  toolbar.style.removeProperty("left");
  toolbar.style.removeProperty("right");
  toolbar.style.removeProperty("top");

  function resetMode() {{
    mode = "absorbance";
    gd._ristFtirSignalMode = mode;
    btn.textContent = "투과도 보기";
    gd._ristFtirUnitOriginalShapes = (gd.layout.shapes || []).map(function(shape) {{
      return Object.assign({{}}, shape);
    }});
    gd._ristFtirUnitOriginalAnnotations = (gd.layout.annotations || []).map(function(annotation) {{
      return Object.assign({{}}, annotation);
    }});
  }}
  gd.addEventListener("rist-plot-data-replaced", resetMode);

}})();
</script>
"""


def ftir_stack_js(div_id: str) -> str:
    """FT-IR 다중 시료 스택 표시와 시료별 Y 이동 제어를 추가한다."""
    template = r"""
<script>
(function() {
  var gd = document.getElementById(__DIV_ID__);
  if (!gd || !window.Plotly || gd._ristFtirStackInstalled) return;
  gd._ristFtirStackInstalled = true;
  var state = {
    initialized: false,
    enabled: false,
    gap: 1.2,
    positions: {},
    order: [],
    units: {absorbance: 1, transmittance: 100},
    traces: {},
    annotations: {},
    shapes: {},
    dragMode: false,
    dragging: null,
    autoLayout: true,
    compactTimer: null,
    raf: null,
    applyInFlight: false,
    applyPending: false,
    yAxisDisplay: null
  };

  function traceMeta(index) {
    var trace = (gd.data || [])[index] || {};
    return trace.meta && typeof trace.meta === "object" ? trace.meta : {};
  }

  function groupOfTrace(index) {
    var meta = traceMeta(index);
    return String(meta.rist_sample_group || (meta.rist_peak && meta.rist_peak.sample_group) || "");
  }

  function stackMeta() {
    if (!gd.layout.meta || typeof gd.layout.meta !== "object") gd.layout.meta = {};
    if (!gd.layout.meta.ristFtirStack) gd.layout.meta.ristFtirStack = {};
    return gd.layout.meta.ristFtirStack;
  }

  function axisProperty(axis, key) {
    var exists = Object.prototype.hasOwnProperty.call(axis, key);
    return {exists: exists, value: exists ? axis[key] : null};
  }

  function restoreAxisProperty(axis, key, saved) {
    if (saved && saved.exists) axis[key] = saved.value;
    else delete axis[key];
  }

  function savedYAxisDisplay(value) {
    if (!value || typeof value !== "object") return null;
    return {
      showticklabels: value.showticklabels || {exists: false, value: null},
      ticks: value.ticks || {exists: false, value: null},
      ticklen: value.ticklen || {exists: false, value: null}
    };
  }

  function syncStackYAxis(layout) {
    if (!layout.yaxis) layout.yaxis = {};
    var axis = layout.yaxis;
    if (state.enabled) {
      if (!state.yAxisDisplay) {
        state.yAxisDisplay = {
          showticklabels: axisProperty(axis, "showticklabels"),
          ticks: axisProperty(axis, "ticks"),
          ticklen: axisProperty(axis, "ticklen")
        };
      }
      axis.showticklabels = false;
      axis.ticks = "";
      axis.ticklen = 0;
      return;
    }
    if (!state.yAxisDisplay) return;
    restoreAxisProperty(axis, "showticklabels", state.yAxisDisplay.showticklabels);
    restoreAxisProperty(axis, "ticks", state.yAxisDisplay.ticks);
    restoreAxisProperty(axis, "ticklen", state.yAxisDisplay.ticklen);
    state.yAxisDisplay = null;
  }

  function applyInitialYAxisDisplay() {
    applyOffsets({preserveView: true}).catch(function(error) {
      console.error("FT-IR 스택 초기 표시 반영 실패", error);
    });
  }

  function numberArray(values) {
    return Array.prototype.slice.call(values || []).map(function(value) {
      var next = Number(value);
      return Number.isFinite(next) ? next : 0;
    });
  }

  function discoverOrder() {
    var seen = {};
    var order = [];
    (gd.data || []).forEach(function(trace, index) {
      var group = groupOfTrace(index);
      if (!group || !traceMeta(index).rist_sample_parent || seen[group]) return;
      seen[group] = true;
      order.push(group);
    });
    return order;
  }

  function traceVisible(index) {
    var trace = (gd.data || [])[index] || {};
    return trace.visible !== false && trace.visible !== "legendonly";
  }

  function sampleVisible(group) {
    var found = false;
    var visible = false;
    Object.keys(state.traces).forEach(function(indexText) {
      var index = Number(indexText);
      var base = state.traces[index];
      if (!base || base.group !== group || !base.parent) return;
      found = true;
      visible = traceVisible(index);
    });
    return found ? visible : true;
  }

  function labelItems() {
    return gd.layout.meta && Array.isArray(gd.layout.meta.ristPeakLabels)
      ? gd.layout.meta.ristPeakLabels : [];
  }

  function labelFor(key, index) {
    return labelItems().find(function(item) { return item[key] === index; }) || null;
  }

  function mode() {
    return gd._ristFtirSignalMode === "transmittance" ? "transmittance" : "absorbance";
  }

  function offsetFor(group, signalMode) {
    if (!state.enabled) return 0;
    return Number(state.positions[group] || 0) * Number(state.units[signalMode] || 1);
  }

  function initState() {
    var meta = stackMeta();
    state.enabled = !!meta.enabled;
    state.yAxisDisplay = savedYAxisDisplay(meta.yAxisDisplay);
    state.gap = Number.isFinite(Number(meta.gap)) ? Number(meta.gap) : 1.2;
    state.units = Object.assign(
      {absorbance: 1, transmittance: 100},
      meta.modeUnits || {}
    );
    state.order = Array.isArray(meta.sampleOrder) && meta.sampleOrder.length
      ? meta.sampleOrder.map(String) : discoverOrder();
    state.positions = {};
    state.order.forEach(function(group, index) {
      var configured = meta.samplePositions && meta.samplePositions[group];
      state.positions[group] = Number.isFinite(Number(configured))
        ? Number(configured) : (state.enabled ? index * state.gap : 0);
    });

    state.traces = {};
    (gd.data || []).forEach(function(trace, index) {
      var group = groupOfTrace(index);
      var toggle = traceMeta(index).ftir_signal_toggle;
      if (!group || !toggle || !Object.prototype.hasOwnProperty.call(state.positions, group)) return;
      var absOffset = Number(traceMeta(index).rist_ftir_stack_offset_absorbance);
      var transOffset = Number(traceMeta(index).rist_ftir_stack_offset_transmittance);
      if (!Number.isFinite(absOffset)) absOffset = offsetFor(group, "absorbance");
      if (!Number.isFinite(transOffset)) transOffset = offsetFor(group, "transmittance");
      state.traces[index] = {
        group: group,
        parent: !!traceMeta(index).rist_sample_parent,
        absorbance: numberArray(toggle.absorbance_y).map(function(value) { return value - absOffset; }),
        transmittance: numberArray(toggle.transmittance_y).map(function(value) { return value - transOffset; })
      };
    });

    state.annotations = {};
    (gd.layout.annotations || []).forEach(function(annotation, index) {
      var label = labelFor("annotationIndex", index);
      if (!label) return;
      var group = String(label.legendgroup || "");
      if (!Object.prototype.hasOwnProperty.call(state.positions, group)) return;
      var baseY = Number(label.annotationBaseY);
      if (!Number.isFinite(baseY)) baseY = Number(annotation.y) - offsetFor(group, "absorbance");
      state.annotations[index] = {group: group, y: baseY};
    });

    state.shapes = {};
    (gd.layout.shapes || []).forEach(function(shape, index) {
      var label = labelFor("shapeIndex", index);
      if (!label) return;
      var group = String(label.legendgroup || "");
      if (!Object.prototype.hasOwnProperty.call(state.positions, group)) return;
      var y0 = Number(label.shapeBaseY0);
      var y1 = Number(label.shapeBaseY1);
      if (!Number.isFinite(y0)) y0 = Number(shape.y0) - offsetFor(group, "absorbance");
      if (!Number.isFinite(y1)) y1 = Number(shape.y1) - offsetFor(group, "absorbance");
      state.shapes[index] = {group: group, y0: y0, y1: y1};
    });
    state.initialized = true;
    syncControls();
  }

  function fitRange(signalMode) {
    var low = Infinity;
    var high = -Infinity;
    Object.keys(state.traces).forEach(function(indexText) {
      var index = Number(indexText);
      var base = state.traces[index];
      if (!base.parent || !sampleVisible(base.group)) return;
      var offset = offsetFor(base.group, signalMode);
      base[signalMode].forEach(function(value) {
        low = Math.min(low, value + offset);
        high = Math.max(high, value + offset);
      });
    });
    if (!Number.isFinite(low) || !Number.isFinite(high)) return null;
    var span = Math.max(high - low, signalMode === "transmittance" ? 1 : 0.02);
    return [low - span * 0.08, high + span * 0.18];
  }

  function updateMeta() {
    var meta = stackMeta();
    meta.enabled = state.enabled;
    meta.gap = state.gap;
    meta.samplePositions = Object.assign({}, state.positions);
    meta.sampleOrder = state.order.slice();
    meta.modeUnits = Object.assign({}, state.units);
    meta.yAxisDisplay = state.yAxisDisplay;
  }

  function visualLegendOrder() {
    var originalIndexes = {};
    state.order.forEach(function(group, index) {
      originalIndexes[group] = index;
    });
    var groups = state.order.slice();
    if (!state.enabled) return groups;
    return groups.sort(function(left, right) {
      var delta = Number(state.positions[right] || 0)
        - Number(state.positions[left] || 0);
      if (Math.abs(delta) > 0.0001) return delta;
      return originalIndexes[left] - originalIndexes[right];
    });
  }

  function syncLegendRanks(data) {
    var groups = visualLegendOrder();
    var groupRanks = {};
    var itemRanks = {};
    groups.forEach(function(group, index) {
      groupRanks[group] = index;
      itemRanks[group] = 0;
    });
    (data || []).forEach(function(trace, index) {
      var group = groupOfTrace(index);
      if (!Object.prototype.hasOwnProperty.call(groupRanks, group)) return;
      var meta = traceMeta(index);
      var itemRank = itemRanks[group] || 0;
      trace.legendrank = groupRanks[group] * 100000
        + (meta.rist_sample_parent ? 0 : 10000)
        + itemRank;
      itemRanks[group] = itemRank + 1;
    });
    stackMeta().legendOrder = groups;
  }

  function applyOffsets(options) {
    options = options || {};
    if (!state.initialized) initState();
    var signalMode = mode();
    var data = gd.data || [];
    Object.keys(state.traces).forEach(function(indexText) {
      var index = Number(indexText);
      var base = state.traces[index];
      if (!base || !data[index] || !data[index].meta
          || !data[index].meta.ftir_signal_toggle) return;
      var absOffset = offsetFor(base.group, "absorbance");
      var transOffset = offsetFor(base.group, "transmittance");
      var toggle = data[index].meta.ftir_signal_toggle;
      toggle.absorbance_y = base.absorbance.map(function(value) { return value + absOffset; });
      toggle.transmittance_y = base.transmittance.map(function(value) { return value + transOffset; });
      data[index].y = signalMode === "transmittance" ? toggle.transmittance_y : toggle.absorbance_y;
      data[index].meta.rist_ftir_stack_position = Number(state.positions[base.group] || 0);
      data[index].meta.rist_ftir_stack_offset_absorbance = absOffset;
      data[index].meta.rist_ftir_stack_offset_transmittance = transOffset;
    });
    syncLegendRanks(data);

    var layout = gd.layout || {};
    if (signalMode === "absorbance") {
      layout.annotations = (layout.annotations || []).map(function(annotation, index) {
        var base = state.annotations[index];
        if (!base) return annotation;
        var next = Object.assign({}, annotation);
        next.y = base.y + offsetFor(base.group, "absorbance");
        return next;
      });
      layout.shapes = (layout.shapes || []).map(function(shape, index) {
        var base = state.shapes[index];
        if (!base) return shape;
        var offset = offsetFor(base.group, "absorbance");
        var next = Object.assign({}, shape);
        next.y0 = base.y0 + offset;
        next.y1 = base.y1 + offset;
        return next;
      });
      gd._ristFtirUnitOriginalAnnotations = (layout.annotations || []).map(function(item) {
        return Object.assign({}, item);
      });
      gd._ristFtirUnitOriginalShapes = (layout.shapes || []).map(function(item) {
        return Object.assign({}, item);
      });
    }
    if (!layout.yaxis) layout.yaxis = {};
    syncStackYAxis(layout);
    if (!options.preserveView) {
      var range = fitRange(signalMode);
      if (range) layout.yaxis.range = range;
    }
    updateMeta();
    syncControls();
    return window.Plotly.react(gd, data, layout, gd._context).then(function() {
      gd.dispatchEvent(new CustomEvent("rist-ftir-stack-change"));
    });
  }

  function requestApply() {
    state.applyPending = true;
    if (state.raf || state.applyInFlight) return;
    state.raf = requestAnimationFrame(function() {
      state.raf = null;
      if (!state.applyPending) return;
      state.applyPending = false;
      state.applyInFlight = true;
      Promise.resolve().then(function() {
        return applyOffsets({preserveView: true});
      }).catch(function(error) {
        console.error("FT-IR Y 이동 반영 실패", error);
      }).then(function() {
        state.applyInFlight = false;
        if (state.applyPending) requestApply();
      });
    });
  }

  function resetPositions() {
    var visibleIndex = 0;
    state.order.forEach(function(group) {
      if (state.enabled && sampleVisible(group)) {
        state.positions[group] = visibleIndex * state.gap;
        visibleIndex += 1;
      } else {
        state.positions[group] = 0;
      }
    });
    state.autoLayout = true;
  }

  function scheduleCompaction() {
    if (state.compactTimer) return;
    state.compactTimer = setTimeout(function() {
      state.compactTimer = null;
      if (!state.initialized) initState();
      if (!state.enabled || !state.autoLayout || state.dragging) return;
      resetPositions();
      applyOffsets();
    }, 0);
  }

  function plotPoint(ev) {
    var drag = gd.querySelector(".nsewdrag");
    var layout = gd._fullLayout || {};
    if (!drag || !layout.xaxis || !layout.yaxis) return null;
    var rect = drag.getBoundingClientRect();
    if (ev.clientX < rect.left || ev.clientX > rect.right || ev.clientY < rect.top || ev.clientY > rect.bottom) return null;
    return {
      x: layout.xaxis.p2d(ev.clientX - rect.left),
      y: layout.yaxis.p2d(ev.clientY - rect.top),
      rect: rect,
      xaxis: layout.xaxis,
      yaxis: layout.yaxis
    };
  }

  function nearestSample(ev) {
    var point = plotPoint(ev);
    if (!point) return null;
    var best = null;
    (gd.data || []).forEach(function(trace, index) {
      var base = state.traces[index];
      if (!base || !base.parent || !traceVisible(index)) return;
      var xs = numberArray(trace.x);
      var nearest = -1;
      var nearestDelta = Infinity;
      xs.forEach(function(value, itemIndex) {
        var delta = Math.abs(value - point.x);
        if (delta < nearestDelta) { nearest = itemIndex; nearestDelta = delta; }
      });
      if (nearest < 0) return;
      var currentMode = mode();
      var y = base[currentMode][nearest] + offsetFor(base.group, currentMode);
      var dx = Math.abs(point.xaxis.d2p(xs[nearest]) - (ev.clientX - point.rect.left));
      var dy = Math.abs(point.yaxis.d2p(y) - (ev.clientY - point.rect.top));
      var distance = Math.sqrt(dx * dx + dy * dy);
      if (distance <= 36 && (!best || distance < best.distance)) {
        best = {group: base.group, point: point, distance: distance};
      }
    });
    return best;
  }

  function syncControls() {
    gd._ristFtirYDragMode = !!state.dragMode;
    gd.classList.toggle("rist-ftir-y-drag-active", !!state.dragMode);
    if (!stackButton || !dragButton || !gapSlider || !gapValue) return;
    var hasMultipleSamples = state.order.length > 1;
    stackButton.classList.toggle("is-active", !!state.enabled);
    dragButton.classList.toggle("is-active", !!state.dragMode);
    stackButton.disabled = !hasMultipleSamples;
    dragButton.disabled = !hasMultipleSamples;
    gapSlider.disabled = !hasMultipleSamples;
    gapSlider.value = String(Math.round(state.gap * 100));
    gapValue.textContent = state.gap.toFixed(1);
  }

  function setDragMode(enabled, announce) {
    var nextEnabled = !!enabled;
    if (nextEnabled && typeof gd._ristSetEditMode === "function") {
      gd._ristSetEditMode(false, false);
    }
    if (nextEnabled && !state.dragMode && announce !== false) {
      gd.dispatchEvent(new CustomEvent("rist-exclusive-interaction-start", {
        detail: {mode: "ftir-y-drag"}
      }));
    }
    state.dragMode = nextEnabled;
    if (!nextEnabled) finishStackDrag();
    syncControls();
  }

  gd._ristSetYDragMode = function(enabled) {
    setDragMode(enabled, false);
  };

  if (getComputedStyle(gd).position === "static") gd.style.position = "relative";
  var toolbar = gd.querySelector(".rist-plot-control-row");
  if (!toolbar) {
    toolbar = document.createElement("div");
    toolbar.className = "rist-plot-control-row";
    gd.appendChild(toolbar);
  }
  var control = document.createElement("div");
  control.className = "rist-ftir-stack-control";
  control.innerHTML =
    "<button type='button' class='rist-ftir-stack-button' title='시료를 위아래로 벌려서 보기'>스택</button>"
    + "<input class='rist-ftir-stack-gap' type='range' min='60' max='220' step='5' value='120' title='스택 간격' aria-label='스택 간격'>"
    + "<span class='rist-ftir-stack-value'>1.2</span>"
    + "<button type='button' class='rist-ftir-stack-button' title='시료 곡선을 위아래로 드래그'>Y 이동</button>";
  toolbar.appendChild(control);
  var buttons = control.querySelectorAll(".rist-ftir-stack-button");
  var stackButton = buttons[0];
  var dragButton = buttons[1];
  var gapSlider = control.querySelector(".rist-ftir-stack-gap");
  var gapValue = control.querySelector(".rist-ftir-stack-value");

  stackButton.addEventListener("click", function() {
    gd.dispatchEvent(new CustomEvent("rist-exclusive-interaction-start", {
      detail: {mode: "ftir-stack-layout"}
    }));
    state.enabled = !state.enabled;
    resetPositions();
    applyOffsets();
  });
  dragButton.addEventListener("click", function() {
    setDragMode(!state.dragMode, true);
  });
  gapSlider.addEventListener("input", function() {
    gd.dispatchEvent(new CustomEvent("rist-exclusive-interaction-start", {
      detail: {mode: "ftir-stack-layout"}
    }));
    state.gap = Math.max(0.6, Math.min(2.2, Number(gapSlider.value) / 100));
    if (state.enabled) resetPositions();
    applyOffsets();
  });

  function exitYDragModeOnOutsidePointer(ev) {
    if (!state.dragMode || state.dragging) return;
    if (ev.target && gd.contains(ev.target)) return;
    setDragMode(false, false);
  }

  document.addEventListener("pointerdown", exitYDragModeOnOutsidePointer, true);

  gd.addEventListener("pointerdown", function(ev) {
    if (
      (ev.pointerType === "mouse" && ev.button !== 0)
      || state.dragging
      || !state.dragMode
      || gd._ristAxisCropActive
      || gd.classList.contains("rist-axis-crop-mode")
      || gd._ristEditMode
      || gd._ristShapeDrawMode
      || gd._ristAxisScaleActive
      || gd._ristAxisSettingsOpen
      || gd._ristRamanRatioMode
      || gd._ristPeakSensitivityInteracting
      || gd._ristLegendEditOpen
      || gd._ristLegendDragging
      || gd._ristInlineTextEditing
      || (gd._ristPeakEditMode && gd._ristPeakEditMode !== "none")
    ) return;
    if (ev.target && ev.target.closest && ev.target.closest(
      ".rist-legend-drag-handle,.legend,.modebar,.rist-plot-control-row,.rist-legend-edit-panel"
    )) return;
    if (!state.initialized) initState();
    var nearest = nearestSample(ev);
    if (!nearest) {
      setDragMode(false, false);
      return;
    }
    ev.preventDefault();
    ev.stopPropagation();
    if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
    var startPixelY = ev.clientY - nearest.point.rect.top;
    var nextY = nearest.point.yaxis.p2d(startPixelY + 1);
    state.dragging = {
      group: nearest.group,
      pointerId: ev.pointerId,
      startClientY: ev.clientY,
      dataPerPixel: Number(nextY) - Number(nearest.point.y),
      startPosition: Number(state.positions[nearest.group] || 0)
    };
    try { gd.setPointerCapture(ev.pointerId); } catch (error) {}
  }, true);

  function handleStackPointerMove(ev) {
    if (!state.dragging || ev.pointerId !== state.dragging.pointerId) return;
    if (ev.cancelable) ev.preventDefault();
    ev.stopPropagation();
    if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
    var unit = Number(state.units[mode()] || 1);
    state.positions[state.dragging.group] =
      state.dragging.startPosition
      + (ev.clientY - state.dragging.startClientY) * state.dragging.dataPerPixel / unit;
    state.autoLayout = false;
    state.enabled = state.order.some(function(group) {
      return Math.abs(Number(state.positions[group] || 0)) > 0.001;
    });
    requestApply();
  }

  function preventStackTouchScroll(ev) {
    if (!state.dragging) return;
    if (ev.cancelable) ev.preventDefault();
  }

  document.addEventListener("pointermove", handleStackPointerMove, {
    capture: true,
    passive: false
  });
  gd.addEventListener("touchmove", preventStackTouchScroll, {
    capture: true,
    passive: false
  });

  function finishStackDrag(ev) {
    if (!state.dragging) return;
    if (ev && ev.pointerId != null && ev.pointerId !== state.dragging.pointerId) return;
    var pointerId = state.dragging.pointerId;
    state.dragging = null;
    try {
      if (gd.hasPointerCapture(pointerId)) gd.releasePointerCapture(pointerId);
    } catch (error) {}
  }

  function rebuildStackReferences() {
    finishStackDrag();
    state.applyPending = false;
    if (state.raf) {
      cancelAnimationFrame(state.raf);
      state.raf = null;
    }
    state.initialized = false;
    function rebuildWhenIdle() {
      if (state.applyInFlight) {
        setTimeout(rebuildWhenIdle, 0);
        return;
      }
      initState();
      applyInitialYAxisDisplay();
    }
    setTimeout(rebuildWhenIdle, 0);
  }

  document.addEventListener("pointerup", finishStackDrag);
  document.addEventListener("pointercancel", finishStackDrag);
  gd.addEventListener("lostpointercapture", finishStackDrag, true);
  window.addEventListener("blur", function() { finishStackDrag(); });
  document.addEventListener("visibilitychange", function() {
    if (document.hidden) finishStackDrag();
  });

  gd.addEventListener("rist-exclusive-interaction-start", function(ev) {
    var activeMode = String(ev.detail && ev.detail.mode || "");
    if (!state.dragMode || !activeMode || activeMode === "ftir-y-drag") return;
    setDragMode(false, false);
  });

  gd.addEventListener("rist-plot-data-replaced", function() {
    state.autoLayout = true;
    rebuildStackReferences();
  });
  gd.addEventListener("rist-plot-structure-changed", rebuildStackReferences);
  gd.addEventListener("rist-ftir-signal-mode-change", function() {
    if (!state.initialized) initState();
    applyOffsets();
  });
  gd.on("plotly_restyle", scheduleCompaction);
  gd.addEventListener("rist-legend-visibility-change", scheduleCompaction);
  gd._ristFtirSignalMode = gd._ristFtirSignalMode || "absorbance";
  initState();
  applyInitialYAxisDisplay();
})();
</script>
"""
    return template.replace("__DIV_ID__", json.dumps(div_id))


def build_preprocess_fig(raw, sample_vec, grid, sample_label, wn_min, wn_max):
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        subplot_titles=["원시 스펙트럼 (Raw)", "전처리 후 (정규화)"],
        vertical_spacing=0.12,
    )
    fig.add_trace(
        _enable_abs_trans_toggle(
            go.Scatter(x=raw["wn"], y=raw["y"], mode="lines",
                       line=dict(color="#555", width=1), name="Raw"),
            raw["y"],
        ),
        row=1, col=1,
    )
    fig.add_trace(
        _enable_abs_trans_toggle(
            go.Scatter(x=grid, y=sample_vec, mode="lines",
                       line=dict(color="#2563eb", width=1.5), name="Preprocessed"),
            sample_vec,
        ),
        row=2, col=1,
    )
    fig.update_layout(
        title=f"전처리 확인 — {sample_label}",
        height=500, plot_bgcolor="white", paper_bgcolor="#fafafa",
        showlegend=False,
    )
    fig.update_xaxes(range=[wn_max, wn_min], title_text="Wavenumber (cm⁻¹)", row=2, col=1)
    fig.update_yaxes(title_text="Absorbance", showgrid=True, gridcolor="#e8e8e8", row=1, col=1)
    fig.update_yaxes(title_text="Normalized Absorbance", showgrid=True,
                     gridcolor="#e8e8e8", row=2, col=1)
    return fig


def build_peak_fig(
    sample_vec,
    grid,
    peak_idx,
    peak_wn,
    peak_val,
    peak_fwhm,
    func_groups,
    sample_label,
    wn_min,
    wn_max,
    initial_sensitivity="medium",
):
    fig = go.Figure()
    sample_key = _sample_key(0)
    raw_trace = _enable_abs_trans_toggle(
        go.Scatter(
            x=grid, y=sample_vec, mode="lines", name=sample_label,
            legendgroup=sample_key,
            legendgrouptitle_text=sample_label,
            line=dict(color="#374151", width=1.8),
            hovertemplate="%{x:.1f} cm⁻¹ | %{y:.4f}<extra></extra>",
        ),
        sample_vec,
    )
    _merge_trace_meta(raw_trace, {
        "rist_sample_group": sample_key,
        "rist_sample_parent": True,
        "rist_legend_edit_group": sample_key,
    })
    fig.add_trace(raw_trace)

    candidates = build_interactive_peak_candidates(
        sample_vec,
        grid,
        peak_idx,
        peak_wn,
        peak_val,
        peak_fwhm,
        initial_sensitivity=initial_sensitivity,
    )
    top_peak_indexes = {
        candidate["index"]
        for candidate in sorted(
            candidates,
            key=lambda item: -item["value"],
        )[:25]
    }
    annotations = []
    peak_labels = []
    seen_legendgroups = set()

    for candidate in candidates:
        wn = candidate["wn"]
        val = candidate["value"]
        fwhm = candidate["fwhm"]
        initially_visible = candidate["initial"]
        assignment = _peak_assignment(wn, func_groups)
        color = assignment["color"]
        note = assignment["note"]
        display_name = assignment["display_name"]
        legendgroup = (
            f"unknown:{wn:.1f}"
            if assignment["unknown"]
            else display_name
        )
        label_key = f"{sample_key}:peak:{legendgroup}"
        trace_index = len(fig.data)
        peak_trace = _enable_abs_trans_toggle(
            go.Scatter(
                x=[wn], y=[val], mode="markers",
                marker=dict(color=color, size=9, symbol="circle",
                            line=dict(color="white", width=1.5)),
                name=display_name,
                legendgroup=legendgroup,
                visible=initially_visible,
                showlegend=initially_visible and legendgroup not in seen_legendgroups,
                hovertemplate=(
                    f"<b>{wn:.1f} cm⁻¹</b><br>{display_name}<br>"
                    f"Value: %{{y:.4f}}<br>FWHM: {fwhm:.1f} cm⁻¹<br>"
                    f"<i>{note}</i><extra></extra>"
                ),
            ),
            [val],
        )
        _merge_trace_meta(peak_trace, {
            "rist_sample_group": sample_key,
            "rist_legend_edit_group": label_key,
            "rist_peak": {
                "source": "detected",
                "x": float(wn),
                "label": display_name,
                "sample_group": sample_key,
                "label_key": label_key,
                "sensitivity_levels": candidate["levels"],
                "sensitivity_min": candidate["sensitivity_min"],
                "assignments": assignment["assignments"],
            }
        })
        fig.add_trace(peak_trace)
        if initially_visible:
            seen_legendgroups.add(legendgroup)

        if candidate["index"] in top_peak_indexes:
            y_label = val + 0.07 + (0.07 if len(annotations) % 2 == 0 else 0.0)
            annotation_index = len(annotations)
            shape_index = len(fig.layout.shapes)
            annotations.append(dict(
                x=wn, y=y_label, text=_peak_label_text(wn, display_name),
                showarrow=True, captureevents=True,
                arrowhead=0, arrowcolor=color, arrowwidth=1,
                ax=0, ay=-28, font=dict(size=9, color=color),
                bgcolor="rgba(255,255,255,0.88)",
                bordercolor=color, borderwidth=1, borderpad=2,
                name=f"ftir_peak_label_{annotation_index}",
                visible=initially_visible,
            ))
            peak_labels.append({
                "annotationIndex": annotation_index,
                "shapeIndex": shape_index,
                "traceIndex": trace_index,
                "legendgroup": legendgroup,
                "labelKey": label_key,
                "wnText": f"{wn:.0f}",
            })
            fig.add_shape(type="line", x0=wn, x1=wn, y0=0, y1=val,
                          line=dict(color=color, width=0.8, dash="dot"),
                          visible=initially_visible)

    fig.update_layout(
        title=dict(
            text=f"FTIR Peak Analysis — {sample_label}",
            font=dict(size=18),
            x=0.01,
            y=0.98,
            yanchor="top",
        ),
        xaxis=dict(
            title="Wavenumber (cm⁻¹)", range=[wn_max, wn_min],
            showgrid=True, gridcolor="#e8e8e8",
            tickmode="auto", fixedrange=False,
            minor=dict(showgrid=True, gridcolor="#f4f4f4"),
        ),
        yaxis=dict(
            title="Normalized Absorbance", showgrid=True, gridcolor="#e8e8e8",
            range=[-0.05, max(peak_val) * 1.6 if len(peak_val) else 1.3],
            tickmode="auto", fixedrange=False,
        ),
        annotations=annotations,
        legend=dict(
            orientation="h", x=0.5, xanchor="center", y=-0.18, yanchor="top",
            itemclick="toggle", itemdoubleclick="toggleothers",
            bgcolor="rgba(255,255,255,0.9)",
            bordercolor="#ccc", borderwidth=1, font=dict(size=10),
            itemsizing="constant", tracegroupgap=2,
        ),
        plot_bgcolor="white", paper_bgcolor="#fafafa",
        height=620, hovermode="closest",
        margin=dict(l=70, r=30, t=100, b=125),
        meta={"ristPeakLabels": peak_labels},
    )
    return fig


def build_multi_peak_fig(
    samples,
    func_groups,
    wn_min,
    wn_max,
    initial_sensitivity="medium",
):
    """여러 FT-IR 시료의 raw/preprocessed trace와 피크를 한 그래프에 그린다."""
    fig = go.Figure()
    annotations = []
    peak_labels = []
    max_y = float("-inf")
    min_y = float("inf")
    stack_enabled = len(samples) > 1
    stack_gap = 1.2
    absorbance_values = [
        float(value)
        for sample in samples
        for value in np.asarray(
            sample.get("display_vec", sample["sample_vec"]),
            dtype=float,
        )
        if np.isfinite(value)
    ]
    transmittance_values = [
        float(value)
        for sample in samples
        for value in _transmittance_percent(
            sample.get("display_vec", sample["sample_vec"]),
        )
        if np.isfinite(value)
    ]
    absorbance_unit = max(
        (max(absorbance_values) - min(absorbance_values))
        if absorbance_values else 0.0,
        0.1,
    )
    transmittance_unit = max(
        (max(transmittance_values) - min(transmittance_values))
        if transmittance_values else 0.0,
        10.0,
    )
    sample_positions = {
        _sample_key(index): (index * stack_gap if stack_enabled else 0.0)
        for index in range(len(samples))
    }

    for sample_no, sample in enumerate(samples):
        sample_key = _sample_key(sample_no)
        label = sample["label"]
        grid = sample["grid"]
        analysis_vec = sample["sample_vec"]
        sample_vec = np.asarray(sample.get("display_vec", analysis_vec), dtype=float)
        stack_position = sample_positions[sample_key]
        absorbance_offset = stack_position * absorbance_unit
        transmittance_offset = stack_position * transmittance_unit
        plotted_sample_vec = sample_vec + absorbance_offset
        color = SAMPLE_PALETTE[sample_no % len(SAMPLE_PALETTE)]
        if len(sample_vec):
            sample_min = float(np.nanmin(sample_vec))
            sample_max = float(np.nanmax(sample_vec))
            max_y = max(max_y, sample_max + absorbance_offset)
            min_y = min(min_y, sample_min + absorbance_offset)
        else:
            sample_min = 0.0
            sample_max = 1.0
        sample_span = max(
            sample_max - sample_min,
            0.02,
        )
        label_gap = sample_span * 0.06

        raw_trace = _enable_abs_trans_toggle(
            go.Scatter(
                x=grid, y=plotted_sample_vec, mode="lines", name=label,
                legendgroup=sample_key,
                legendgrouptitle_text=label,
                line=dict(color=color, width=1.8),
                hovertemplate=f"<b>{label}</b><br>%{{x:.1f}} cm⁻¹ | %{{y:.4f}}<extra></extra>",
            ),
            plotted_sample_vec,
            absorbance_offset=absorbance_offset,
            transmittance_offset=transmittance_offset,
        )
        _merge_trace_meta(raw_trace, {
            "rist_sample_group": sample_key,
            "rist_sample_parent": True,
            "rist_legend_edit_group": sample_key,
            "rist_ftir_stack_position": stack_position,
            "rist_ftir_stack_offset_absorbance": absorbance_offset,
            "rist_ftir_stack_offset_transmittance": transmittance_offset,
            "rist_ftir_sample_index": sample_no,
        })
        fig.add_trace(raw_trace)

        peak_wn = sample["peak_wn"]
        peak_val = sample["peak_val"]
        analysis_peak_val = sample.get("analysis_peak_val", peak_val)
        peak_fwhm = sample["peak_fwhm"]
        candidates = build_interactive_peak_candidates(
            analysis_vec,
            grid,
            sample["peak_idx"],
            peak_wn,
            analysis_peak_val,
            peak_fwhm,
            initial_sensitivity=initial_sensitivity,
        )
        top_peak_indexes = {
            candidate["index"]
            for candidate in sorted(
                candidates,
                key=lambda item: -item["value"],
            )[:25]
        }
        seen_label_keys = set()

        for peak_no, candidate in enumerate(candidates):
            wn = candidate["wn"]
            peak_index = int(candidate["index"])
            base_val = (
                float(sample_vec[peak_index])
                if 0 <= peak_index < len(sample_vec)
                else float(candidate["value"])
            )
            val = base_val + absorbance_offset
            fwhm = candidate["fwhm"]
            initially_visible = candidate["initial"]
            assignment = _peak_assignment(wn, func_groups)
            peak_color = assignment["color"]
            note = assignment["note"]
            display_name = assignment["display_name"]
            local_group = (
                f"unknown:{wn:.1f}"
                if assignment["unknown"]
                else display_name
            )
            label_key = f"{sample_key}:peak:{local_group}"
            trace_index = len(fig.data)
            peak_trace = _enable_abs_trans_toggle(
                go.Scatter(
                    x=[wn], y=[val], mode="markers",
                    marker=dict(color=peak_color, size=8, symbol="circle",
                                line=dict(color="white", width=1.5)),
                    name=display_name,
                    legendgroup=sample_key,
                    visible=initially_visible,
                    showlegend=initially_visible and label_key not in seen_label_keys,
                    hovertemplate=(
                        f"<b>{label}</b><br>{wn:.1f} cm⁻¹<br>{display_name}<br>"
                        f"Value: %{{y:.4f}}<br>FWHM: {fwhm:.1f} cm⁻¹<br>"
                        f"<i>{note}</i><extra></extra>"
                    ),
                ),
                [val],
                absorbance_offset=absorbance_offset,
                transmittance_offset=transmittance_offset,
            )
            _merge_trace_meta(peak_trace, {
                "rist_sample_group": sample_key,
                "rist_legend_edit_group": label_key,
                "rist_peak": {
                    "source": "detected",
                    "x": float(wn),
                    "label": display_name,
                    "sample_group": sample_key,
                    "label_key": label_key,
                    "sensitivity_levels": candidate["levels"],
                    "sensitivity_min": candidate["sensitivity_min"],
                    "assignments": assignment["assignments"],
                    "base_y": base_val,
                },
                "rist_ftir_stack_position": stack_position,
                "rist_ftir_stack_offset_absorbance": absorbance_offset,
                "rist_ftir_stack_offset_transmittance": transmittance_offset,
                "rist_ftir_sample_index": sample_no,
            })
            fig.add_trace(peak_trace)
            if initially_visible:
                seen_label_keys.add(label_key)

            if candidate["index"] in top_peak_indexes:
                y_label = val + label_gap + (
                    label_gap * 0.8 if (len(annotations) + peak_no) % 2 == 0 else 0.0
                )
                annotation_index = len(annotations)
                shape_index = len(fig.layout.shapes)
                annotations.append(dict(
                    x=wn, y=y_label, text=_peak_label_text(wn, display_name),
                    showarrow=True, captureevents=True,
                    arrowhead=0, arrowcolor=peak_color, arrowwidth=1,
                    ax=0, ay=-28, font=dict(size=9, color=peak_color),
                    bgcolor="rgba(255,255,255,0.88)",
                    bordercolor=peak_color, borderwidth=1, borderpad=2,
                    name=f"ftir_peak_label_{sample_no}_{annotation_index}",
                    visible=initially_visible,
                ))
                peak_labels.append({
                    "annotationIndex": annotation_index,
                    "shapeIndex": shape_index,
                    "traceIndex": trace_index,
                    "legendgroup": sample_key,
                    "labelKey": label_key,
                    "wnText": f"{wn:.0f}",
                    "annotationBaseY": y_label - absorbance_offset,
                    "shapeBaseY0": min(0, sample_min),
                    "shapeBaseY1": base_val,
                })
                fig.add_shape(type="line", x0=wn, x1=wn,
                              y0=min(0, sample_min) + absorbance_offset, y1=val,
                              line=dict(color=peak_color, width=0.8, dash="dot"),
                              visible=initially_visible)

    title = "FTIR Peak Analysis — " + ", ".join(sample["label"] for sample in samples[:3])
    if len(samples) > 3:
        title += f" 외 {len(samples) - 3}개"

    if not np.isfinite(min_y) or not np.isfinite(max_y):
        min_y, max_y = 0.0, 1.0
    y_span = max(max_y - min_y, 0.02)

    fig.update_layout(
        title=dict(
            text=title,
            font=dict(size=18),
            x=0.01,
            y=0.98,
            yanchor="top",
        ),
        xaxis=dict(
            title="Wavenumber (cm⁻¹)", range=[wn_max, wn_min],
            showgrid=True, gridcolor="#e8e8e8",
            tickmode="auto", fixedrange=False,
            minor=dict(showgrid=True, gridcolor="#f4f4f4"),
        ),
        yaxis=dict(
            title="Absorbance", showgrid=True, gridcolor="#e8e8e8",
            range=[
                min_y - max(y_span * 0.08, 0.02),
                max_y + max(y_span * 0.25, 0.08),
            ],
            tickmode="auto", fixedrange=False,
        ),
        annotations=annotations,
        legend=dict(
            orientation="v", x=1.02, xanchor="left", y=1.0, yanchor="top",
            itemclick="toggle", itemdoubleclick="toggleothers",
            groupclick="toggleitem",
            bgcolor="rgba(255,255,255,0.9)",
            bordercolor="#ccc", borderwidth=1, font=dict(size=10),
            itemsizing="constant", tracegroupgap=10,
        ),
        plot_bgcolor="white", paper_bgcolor="#fafafa",
        height=720, hovermode="closest",
        margin=dict(l=70, r=260, t=105, b=70),
        meta={
            "ristPeakLabels": peak_labels,
            "ristFtirStack": {
                "enabled": stack_enabled,
                "gap": stack_gap,
                "samplePositions": sample_positions,
                "sampleOrder": [_sample_key(index) for index in range(len(samples))],
                "modeUnits": {
                    "absorbance": absorbance_unit,
                    "transmittance": transmittance_unit,
                },
            },
        },
    )
    return fig


def build_bar_fig(best_per_material, top_n, sample_label):
    TIER_COLORS = {
        "동정 (Identified)":          "#16a34a",
        "후보 복수 (Ambiguous)":      "#f59e0b",
        "미동정 (No reliable match)": "#9ca3af",
    }
    bar_colors = [TIER_COLORS.get(t, "#888") for t in best_per_material["tier"]]
    fig = go.Figure(go.Bar(
        x=best_per_material["composite_pct"],
        y=best_per_material["material"],
        orientation="h",
        marker_color=bar_colors,
        text=best_per_material["composite_pct"].apply(lambda v: f"{v:.1f}%"),
        textposition="outside",
        customdata=np.stack([
            best_per_material["cosine_pct"],
            best_per_material["deriv_pct"],
            best_per_material["peak_pct"],
            best_per_material["category_label"],
        ], axis=-1),
        hovertemplate=("<b>%{y}</b><br>종합: %{x:.1f}%<br>"
                       "코사인: %{customdata[0]:.1f}% | 미분: %{customdata[1]:.1f}% | "
                       "피크: %{customdata[2]:.1f}%<br>%{customdata[3]}<extra></extra>"),
    ))
    fig.update_layout(
        title=f"Top {top_n} 종합 점수 매칭 — {sample_label}",
        xaxis=dict(title="Composite Score (%)", range=[0, 110]),
        yaxis=dict(autorange="reversed"),
        height=max(350, top_n * 40),
        plot_bgcolor="white", paper_bgcolor="#fafafa",
        margin=dict(l=200, r=60, t=60, b=50),
    )
    return fig


def build_comparison_fig(sample_vec, grid, best_per_material, plot_top_n, sample_label,
                         library_dir, wn_min, wn_max, peak_height, peak_prominence,
                         peak_distance, smooth, smooth_win, smooth_poly):
    top_matches = best_per_material.head(plot_top_n)
    OFFSET_STEP = 1.3
    PALETTE = ["#2563eb", "#dc2626", "#16a34a", "#ea580c", "#7c3aed"]

    fig = go.Figure()
    cmp_annotations = []

    def add_peak_markers(vec, offset, color, label_prefix=""):
        p_idx, _ = find_peaks(vec, height=peak_height, prominence=peak_prominence,
                               distance=peak_distance)
        if len(p_idx) == 0:
            return
        p_wn = grid[p_idx]
        p_val = vec[p_idx] + offset
        fig.add_trace(_enable_abs_trans_toggle(
            go.Scatter(
                x=p_wn, y=p_val, mode="markers",
                marker=dict(color=color, size=7, symbol="circle-open",
                            line=dict(color=color, width=2)),
                name=f"{label_prefix}peaks",
                legendgroup=f"{label_prefix}peaks",
                showlegend=False,
                hovertemplate="%{x:.1f} cm⁻¹<br>%{y:.3f}<extra></extra>",
            ),
            p_val,
            absorbance_offset=offset,
            transmittance_offset=offset * 80.0,
        ))
        top_n = min(8, len(p_idx))
        top_sort = sorted(zip(p_wn, p_val, vec[p_idx]), key=lambda x: -x[2])[:top_n]
        for i, (wn_p, yy, _) in enumerate(top_sort):
            y_ann = yy + 0.06 + (0.05 if i % 2 == 0 else 0.0)
            cmp_annotations.append(dict(
                x=wn_p, y=y_ann, text=f"<b>{wn_p:.0f}</b>",
                showarrow=True, arrowhead=0, arrowcolor=color, arrowwidth=1,
                ax=0, ay=-22, font=dict(size=8, color=color),
                bgcolor="rgba(255,255,255,0.75)", borderpad=1,
            ))

    total_offset = plot_top_n * OFFSET_STEP
    fig.add_trace(_enable_abs_trans_toggle(
        go.Scatter(
            x=grid, y=sample_vec + total_offset, mode="lines",
            name=f"★ {sample_label}",
            line=dict(color="black", width=2),
            hovertemplate=f"<b>{sample_label}</b><br>%{{x:.1f}} cm⁻¹<extra></extra>",
        ),
        sample_vec + total_offset,
        absorbance_offset=total_offset,
        transmittance_offset=total_offset * 80.0,
    ))
    add_peak_markers(sample_vec, total_offset, "black", "sample")

    for rank, (_, row) in enumerate(top_matches.iterrows()):
        fpath = os.path.join(library_dir, row["file"])
        try:
            df_lib = load_csv(fpath, wn_min, wn_max)
            vec = preprocess(df_lib["wn"].values, df_lib["y"].values, grid,
                             smooth, smooth_win, smooth_poly)
        except Exception:
            continue

        offset = (plot_top_n - 1 - rank) * OFFSET_STEP
        color = PALETTE[rank % len(PALETTE)]
        label = f"#{rank+1} {row['material']}  ({row['composite_pct']:.1f}%)"

        fig.add_trace(_enable_abs_trans_toggle(
            go.Scatter(
                x=grid, y=vec + offset, mode="lines", name=label,
                line=dict(color=color, width=1.4),
                hovertemplate=(f"<b>{row['material']}</b><br>%{{x:.1f}} cm⁻¹<br>"
                               f"종합: {row['composite_pct']:.1f}%<extra></extra>"),
            ),
            vec + offset,
            absorbance_offset=offset,
            transmittance_offset=offset * 80.0,
        ))
        add_peak_markers(vec, offset, color, label)

    fig.update_layout(
        title=f"Spectral Comparison — {sample_label} vs Top {plot_top_n} Matches",
        xaxis=dict(
            title="Wavenumber (cm⁻¹)", range=[wn_max, wn_min],
            showgrid=True, gridcolor="#e8e8e8",
            tickmode="auto", fixedrange=False,
            minor=dict(showgrid=True, gridcolor="#f4f4f4"),
        ),
        yaxis=dict(
            title="Normalized Absorbance (offset)",
            showgrid=False, zeroline=False, showticklabels=False,
            tickmode="auto", fixedrange=False,
        ),
        annotations=cmp_annotations,
        legend=dict(
            title="<b>범례 클릭으로 표시/숨기기</b>",
            itemclick="toggle", itemdoubleclick="toggleothers",
            bgcolor="rgba(255,255,255,0.9)", bordercolor="#ccc", borderwidth=1,
        ),
        plot_bgcolor="white", paper_bgcolor="#fafafa",
        height=700, hovermode="closest",
        margin=dict(l=60, r=20, t=70, b=60),
    )
    return fig

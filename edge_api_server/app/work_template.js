/* Settings only: never serialize RAW, sample identities, credentials or analysis evidence. */
(function() {
  "use strict";
  var FORMAT = "rist-spectrum-work-template";
  var MAX_BYTES = 256 * 1024;
  var own = function(value, key) { return Object.prototype.hasOwnProperty.call(value, key); };
  var clone = function(value) { return JSON.parse(JSON.stringify(value)); };
  var conditionFields = {
    FTIR: ["equipmentModel", "analysisType", "detector", "crystal", "resolution", "scanTime", "range"],
    RAMAN: ["excitationWavelength", "laserCurrent", "excitationPower", "excitationPowerDensity",
      "ndFilter", "spectrographCenterWavelength", "grating", "slitWidth"]
  };
  function invalid() { throw new Error("작업 템플릿 형식이나 설정값이 올바르지 않습니다."); }
  function object(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) invalid();
    return value;
  }
  function text(value, max) { if (typeof value !== "string" || value.length > max) invalid(); return value; }
  function number(value, min, max) {
    if (typeof value !== "number" || !isFinite(value) || value < min || value > max) {
      throw new Error("숫자 설정이 올바르지 않습니다 (" + String(value).slice(0, 30) + ", 허용: " + min + "~" + max + ").");
    }
    return value;
  }
  function boolean(value) { if (typeof value !== "boolean") invalid(); return value; }
  function choice(value, allowed) {
    if (allowed.indexOf(value) < 0) throw new Error("지원하지 않는 설정값입니다: " + String(value).slice(0, 80));
    return value;
  }
  function color(value) {
    text(value, 80);
    if (!/^(#[a-f\d]{3,8}|rgba?\([\d.,%\s]+\)|[a-z]{1,24})$/i.test(value)) {
      throw new Error("색상 설정을 확인하세요: " + value);
    }
    return value;
  }
  function font(value) {
    var out = {}, input = object(value);
    if (own(input, "size")) out.size = number(input.size, 6, 72);
    if (own(input, "weight")) out.weight = choice(input.weight, [400, 700]);
    if (own(input, "family")) out.family = text(input.family, 200);
    if (own(input, "color")) out.color = color(input.color);
    return out;
  }
  function axis(value) {
    var out = {}, input = object(value);
    ["fixedrange", "showgrid", "zeroline", "showline", "mirror"].forEach(function(key) {
      if (own(input, key) && typeof input[key] === "boolean") out[key] = input[key];
    });
    if (own(input, "range")) {
      if (!Array.isArray(input.range) || input.range.length !== 2) invalid();
      out.range = input.range.map(function(v) { return number(v, -1e15, 1e15); });
      if (out.range[0] === out.range[1]) invalid();
    }
    if (own(input, "tickmode")) out.tickmode = choice(input.tickmode, ["auto", "linear"]);
    if (own(input, "dtick") && input.dtick !== null) out.dtick = number(input.dtick, 1e-12, 1e15);
    if (own(input, "tickformat")) out.tickformat = text(input.tickformat || "", 40);
    ["linecolor", "gridcolor", "zerolinecolor"].forEach(function(key) {
      if (own(input, key)) out[key] = color(input[key]);
    });
    if (input.tickfont) out.tickfont = font(input.tickfont);
    if (input.minor) {
      out.minor = {};
      if (input.minor.dtick != null) out.minor.dtick = number(input.minor.dtick, 1e-12, 1e15);
      if (input.minor.tickmode) out.minor.tickmode = choice(input.minor.tickmode, ["auto", "linear"]);
    }
    return out;
  }
  function legend(value) {
    var input = object(value), out = {};
    ["x", "y"].forEach(function(key) { if (own(input, key)) out[key] = number(input[key], -2, 3); });
    if (input.orientation) out.orientation = choice(input.orientation, ["h", "v"]);
    if (input.xanchor) out.xanchor = choice(input.xanchor, ["auto", "left", "center", "right"]);
    if (input.yanchor) out.yanchor = choice(input.yanchor, ["auto", "top", "middle", "bottom"]);
    if (input.font) out.font = font(input.font);
    ["bgcolor", "bordercolor"].forEach(function(key) { if (own(input, key)) out[key] = color(input[key]); });
    if (own(input, "borderwidth")) out.borderwidth = number(input.borderwidth, 0, 10);
    return out;
  }
  function normalize(value, experiment) {
    var source = object(value);
    if (source.format !== FORMAT || source.version !== 1) {
      throw new Error("지원하지 않는 템플릿입니다. RIST 작업 템플릿 JSON 파일을 선택하세요.");
    }
    if (source.experiment !== experiment) {
      throw new Error("실험 종류가 다릅니다. " + experiment + "에서 저장한 템플릿을 선택하세요.");
    }
    var input = object(source.settings), settings = {};
    settings.sensitivity = number(input.sensitivity, 0, 100);
    if (Math.round(settings.sensitivity) !== settings.sensitivity) invalid();
    if (!Array.isArray(input.libraryIds) || input.libraryIds.length > 100) invalid();
    settings.libraryIds = input.libraryIds.map(function(id) { return text(id, 200); });
    settings.originStyle = boolean(input.originStyle);
    settings.signalMode = choice(input.signalMode, experiment === "FTIR"
      ? ["absorbance", "transmittance"] : ["intensity"]);
    var stack = object(input.stack), size = object(input.plotSize);
    settings.stack = {enabled: boolean(stack.enabled), gap: number(stack.gap, 0, 6)};
    settings.plotSize = {width: size.width === null ? null : number(size.width, 320, 2400),
      ratio: choice(size.ratio, ["auto", "4:3", "16:9", "1:1", "3:2", "5:4", "16:10", "2:1", "21:9", "3:4", "2:3", "9:16", "custom"])};
    if (settings.plotSize.ratio === "custom") {
      if (settings.plotSize.width === null) invalid();
      settings.plotSize.height = number(size.height, 240, 2400);
    }
    settings.axes = {xaxis: axis(object(input.axes).xaxis), yaxis: axis(input.axes.yaxis)};
    settings.legend = legend(input.legend);
    settings.peakLabelsVisible = boolean(input.peakLabelsVisible);
    settings.legendVisible = own(input, "legendVisible") ? boolean(input.legendVisible) : false;
    settings.peaksVisible = own(input, "peaksVisible") ? boolean(input.peaksVisible) : true;
    settings.peakLegendEntriesVisible = boolean(input.peakLegendEntriesVisible);
    if (!Array.isArray(input.sampleStyles) || input.sampleStyles.length > 100) invalid();
    settings.sampleStyles = input.sampleStyles.map(function(style) {
      object(style);
      var result = {};
      if (own(style, "color")) result.color = color(style.color);
      if (own(style, "width")) result.width = number(style.width, 0.1, 20);
      if (own(style, "dash")) result.dash = choice(style.dash,
        ["solid", "dot", "dash", "longdash", "dashdot", "longdashdot"]);
      return result;
    });
    settings.conditions = {};
    object(input.conditions);
    conditionFields[experiment].forEach(function(field) {
      if (own(input.conditions, field)) settings.conditions[field] = text(input.conditions[field], 4000);
    });
    if (own(input, "title")) settings.title = text(input.title, 1000);
    return {format: FORMAT, version: 1, experiment: experiment,
      name: text(source.name, 120), settings: settings};
  }
  function parse(raw, experiment) {
    if (typeof raw !== "string" || raw.length > MAX_BYTES) invalid();
    var value;
    try {
      value = JSON.parse(raw.replace(/^\uFEFF/, ""), function(key, item) {
        if (key === "__proto__" || key === "constructor" || key === "prototype") invalid();
        return item;
      });
    } catch (err) { throw new Error("작업 템플릿 JSON을 읽을 수 없습니다."); }
    return normalize(value, experiment);
  }
  function escaped(value) {
    return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function capture(options, name, ranges, title) {
    var gd = options.gd, layout = gd.layout || {}, meta = layout.meta || {};
    var stack = meta[options.experiment === "FTIR" ? "ristFtirStack" : "ristRamanStack"] || {};
    var environment = options.environment(), settings = {
      sensitivity: environment.sensitivity, libraryIds: environment.libraryIds,
      originStyle: environment.originStyle, conditions: environment.conditions,
      signalMode: options.experiment === "FTIR" ? gd._ristFtirSignalMode || "absorbance" : "intensity",
      stack: {enabled: !!stack.enabled, gap: stack.gap == null ? 1.2 : stack.gap},
      plotSize: meta.ristPlotSize || {width: null, ratio: "auto"},
      axes: {}, legend: layout.legend || {},
      peakLabelsVisible: meta.ristPeakLabelsVisible !== false,
      legendVisible: meta.ristLegendVisible === true,
      peaksVisible: meta.ristPeaksVisible !== false,
      peakLegendEntriesVisible: meta.ristPeakLegendEntriesVisible !== false,
      sampleStyles: (gd.data || []).filter(function(trace) {
        return trace.meta && trace.meta.rist_sample_parent;
      }).map(function(trace) { return trace.line || {}; })
    };
    ["xaxis", "yaxis"].forEach(function(key) {
      settings.axes[key] = axis(layout[key] || {});
      if (!ranges) delete settings.axes[key].range;
    });
    if (title) settings.title = typeof layout.title === "string" ? layout.title : (layout.title || {}).text || "";
    return normalize({format: FORMAT, version: 1, experiment: options.experiment,
      name: name || options.experiment + " 작업 템플릿", settings: settings}, options.experiment);
  }
  function prepareFigure(figure, template) {
    var s = template.settings, layout = figure.layout, meta = layout.meta || (layout.meta = {});
    meta.ristPlotSize = clone(s.plotSize);
    meta.ristPeakLabelsVisible = s.peakLabelsVisible;
    meta.ristSampleLegendOnly = true;
    meta.ristLegendVisible = s.legendVisible;
    layout.showlegend = s.legendVisible;
    layout.legend = Object.assign({}, layout.legend || {}, {font: Object.assign({size:16, weight:700}, s.legend.font || {})});
    meta.ristPeaksVisible = s.peaksVisible;
    meta.ristPeakLegendEntriesVisible = s.peakLegendEntriesVisible;
    // Map appearance by RAW sample order, never by an old name/id; extra samples keep their own style.
    var index = 0;
    (figure.data || []).forEach(function(trace) {
      if (trace.meta && trace.meta.rist_sample_parent) {
        var style = s.sampleStyles[index++];
        if (style) trace.line = Object.assign({}, trace.line || {}, style);
      }
      if (trace.meta && trace.meta.rist_peak) trace.showlegend = s.peakLegendEntriesVisible;
    });
    if (own(s, "title")) layout.title = Object.assign({}, layout.title || {}, {text: escaped(s.title)});
    return figure;
  }
  function applyPlot(options, template) {
    var gd = options.gd, s = template.settings;
    return Promise.resolve(gd._ristStackReady).then(function() {
      return gd._ristApplyStackSettings(s.stack);
    }).then(function() {
      if (options.experiment === "FTIR" && gd._ristSetSignalMode) return gd._ristSetSignalMode(s.signalMode);
    }).then(function() { return gd._ristStackReady; }).then(function() {
      var patch = {};
      ["xaxis", "yaxis"].forEach(function(key) {
        Object.keys(s.axes[key]).forEach(function(prop) { patch[key + "." + prop] = clone(s.axes[key][prop]); });
        if (s.axes[key].range) patch[key + ".autorange"] = false;
      });
      var compact = (gd._ristPlotSize ? gd._ristPlotSize.viewportWidth() : window.innerWidth) <= 760;
      legendPatch(patch, s.legend, compact);
      return window.Plotly.relayout(gd, patch);
    });
  }
  function legendPatch(patch, value, compact) {
    Object.keys(value).forEach(function(key) {
      if (!compact || ["x", "y", "xanchor", "yanchor", "orientation"].indexOf(key) < 0) {
        patch["legend." + key] = clone(value[key]);
      }
    });
  }
  function restorePlot(options, saved, sensitivity) {
    var gd = options.gd;
    return window.Plotly.react(gd, saved.data, saved.layout, gd._context).then(function() {
      gd.dispatchEvent(new CustomEvent("rist-plot-data-replaced", {detail: {sensitivity: sensitivity}}));
      return gd._ristStackReady;
    }).then(function() {
      if (saved.originalAnnotations) gd._ristFtirUnitOriginalAnnotations = saved.originalAnnotations;
      if (saved.originalShapes) gd._ristFtirUnitOriginalShapes = saved.originalShapes;
      if (saved.signalMode === "transmittance" && gd._ristSetSignalMode) {
        return gd._ristSetSignalMode(saved.signalMode);
      }
    }).then(function() { return gd._ristStackReady; }).then(function() {
      var patch = {};
      ["xaxis", "yaxis"].forEach(function(key) {
        if (saved.layout[key] && saved.layout[key].range) patch[key + ".range"] = saved.layout[key].range;
      });
      return window.Plotly.relayout(gd, patch);
    });
  }
  function install(options) {
    var active = null, ready = false, externalBusy = false, importing = false;
    var band = document.createElement("section");
    band.className = "rist-work-template-band";
    band.setAttribute("aria-label", "작업 템플릿");
    band.innerHTML = '<div class="rist-work-template-controls">'
      + '<strong class="rist-work-template-title">작업 템플릿</strong>'
      + '<input class="rist-work-template-name" data-template-name aria-label="템플릿 이름" maxlength="120" placeholder="템플릿 이름">'
      + '<div class="rist-work-template-options">'
      + '<label class="rist-work-template-option"><input type="checkbox" data-template-ranges> 축 범위 포함</label>'
      + '<label class="rist-work-template-option"><input type="checkbox" data-template-title> 제목 포함</label></div>'
      + '<div class="rist-work-template-actions">'
      + '<button type="button" class="rist-work-template-button" data-template-save>템플릿 저장</button>'
      + '<button type="button" class="rist-work-template-button" data-template-load>템플릿 불러오기</button>'
      + '<button type="button" class="rist-work-template-button" data-template-clear>자동 적용 해제</button></div>'
      + '<input type="file" accept=".json" data-template-file hidden></div>'
      + '<p class="rist-work-template-help">표시 설정·색상·스택·민감도·라이브러리 선택·공통 실험조건을 저장합니다. RAW·시료명·피크/도형/비율 결과·의뢰정보·Raman 세부조건 원문은 제외합니다. 색상은 새 시료 순서대로 적용됩니다. 축 범위 미포함 시 새 데이터에 맞춥니다.</p>'
      + '<p class="rist-work-template-status" data-template-status role="status" aria-live="polite"></p>';
    var anchor = document.querySelector("." + options.experiment.toLowerCase() + "-library-band");
    anchor.parentNode.insertBefore(band, anchor.nextSibling);
    var q = function(selector) { return band.querySelector(selector); };
    function busy() { return !ready || externalBusy || importing; }
    function refresh() {
      band.querySelectorAll("button, input").forEach(function(control) { control.disabled = busy(); });
      q("[data-template-clear]").disabled = busy() || !active;
    }
    function message(value) { q("[data-template-status]").textContent = value; }
    function activeMessage() {
      message(active ? "적용 중: " + active.name + " · RAW 재분석 시 자동 적용됩니다. 실험조건을 확인해 주세요."
        : "템플릿을 먼저 불러온 뒤 RAW를 추가하거나, 현재 RAW에 적용할 수 있습니다.");
    }
    function syncTemplateFields(template) {
      q("[data-template-name]").value = template.name;
      q("[data-template-ranges]").checked = !!(template.settings.axes.xaxis.range || template.settings.axes.yaxis.range);
      q("[data-template-title]").checked = own(template.settings, "title");
    }
    function validateLibraries(template) {
      var available = options.libraries().filter(function(item) { return item.valid; }).map(function(item) { return item.id; });
      var missing = template.settings.libraryIds.filter(function(id) { return available.indexOf(id) < 0; });
      if (missing.length) throw new Error("필요한 피크 라이브러리가 없습니다: " + missing.join(", ")
        + ". 같은 라이브러리를 먼저 등록한 후 다시 불러오세요.");
    }
    function load(template) {
      if (busy()) return Promise.reject(new Error("현재 처리가 끝난 후 다시 시도하세요."));
      template = normalize(template, options.experiment);
      validateLibraries(template);
      if (options.hasFiles() && !window.confirm("현재 RAW를 템플릿 설정으로 다시 분석합니다. 수동 피크·도형·비율 편집은 초기화됩니다. 계속할까요?")) {
        return Promise.resolve(false);
      }
      var previous = active, environment = clone(options.environment());
      var gd = options.gd, saved = options.hasFiles() ? {
        data: clone(gd.data), layout: clone(gd.layout), signalMode: gd._ristFtirSignalMode,
        originalAnnotations: gd._ristFtirUnitOriginalAnnotations ? clone(gd._ristFtirUnitOriginalAnnotations) : null,
        originalShapes: gd._ristFtirUnitOriginalShapes ? clone(gd._ristFtirUnitOriginalShapes) : null,
        context: options.snapshot ? options.snapshot() : null
      } : null;
      importing = true; refresh(); active = template;
      gd.dispatchEvent(new CustomEvent("rist-exclusive-interaction-start", {detail: {mode: "work-template"}}));
      options.setEnvironment(template.settings);
      message("템플릿 적용 중...");
      return Promise.resolve().then(function() {
        return options.hasFiles() ? options.analyze() : true;
      }).then(function(ok) {
        if (ok !== true) throw new Error("새 분석에 실패해 템플릿을 적용하지 않았습니다. 이전 설정을 유지합니다.");
        syncTemplateFields(template);
        options.invalidateReport(); activeMessage(); options.save(); return true;
      }).catch(function(err) {
        active = previous; options.setEnvironment(environment);
        return (saved ? restorePlot(options, saved, environment.sensitivity) : Promise.resolve()).then(function() {
          if (saved && options.restoreContext) options.restoreContext(saved.context);
          throw err;
        });
      }).finally(function() { importing = false; refresh(); options.save(); });
    }
    q("[data-template-save]").addEventListener("click", function() {
      if (busy()) return;
      try {
        var template = active && !options.hasFiles() ? clone(active) : capture(options,
          q("[data-template-name]").value.trim(), q("[data-template-ranges]").checked, q("[data-template-title]").checked);
        if (q("[data-template-name]").value.trim()) template.name = q("[data-template-name]").value.trim();
        var blob = new Blob([JSON.stringify(template, null, 2)], {type: "application/json;charset=utf-8"});
        if (blob.size > MAX_BYTES) throw new Error("템플릿이 256KB를 초과합니다.");
        var url = URL.createObjectURL(blob), link = document.createElement("a");
        link.href = url; link.download = options.experiment.toLowerCase() + "-"
          + template.name.replace(/[\\/:*?"<>|\x00-\x1f]/g, "_").slice(0, 80) + ".rist-template.json";
        document.body.appendChild(link); link.click(); link.remove();
        window.setTimeout(function() { URL.revokeObjectURL(url); }, 30000);
        message("템플릿 파일을 저장했습니다. 다른 RAW에서 ‘템플릿 불러오기’로 적용하세요.");
      } catch (err) { message(err.message); }
    });
    q("[data-template-load]").addEventListener("click", function() { if (!busy()) q("[data-template-file]").click(); });
    q("[data-template-file]").addEventListener("change", function() {
      var file = this.files && this.files[0]; this.value = "";
      if (!file || busy()) return;
      if (!/\.json$/i.test(file.name) || !file.size || file.size > MAX_BYTES) {
        message("256KB 이하의 작업 템플릿 JSON 파일을 선택하세요."); return;
      }
      var reader = new FileReader();
      importing = true; refresh();
      reader.onload = function() {
        importing = false; refresh();
        try { load(parse(reader.result, options.experiment)).catch(function(err) { message(err.message); }); }
        catch (err) { message(err.message); }
      };
      reader.onerror = function() { importing = false; refresh(); message("템플릿 파일 읽기에 실패했습니다."); };
      reader.readAsText(file, "UTF-8");
    });
    q("[data-template-clear]").addEventListener("click", function() {
      if (busy()) return;
      active = null; refresh(); message("자동 적용을 해제했습니다. 현재 그래프는 유지됩니다."); options.save();
    });
    refresh(); activeMessage();
    return {
      setBusy: function(value) { externalBusy = !!value; refresh(); }, isBusy: busy,
      isImporting: function() { return importing; },
      isActive: function() { return !!active; },
      setReady: function() { ready = true; refresh(); },
      state: function() { return active ? clone(active) : null; },
      restore: function(value) {
        if (!value) return;
        try {
          active = normalize(value, options.experiment); validateLibraries(active);
          syncTemplateFields(active); activeMessage();
        }
        catch (err) { active = null; message(err.message + " 자동 적용을 해제했습니다."); }
        refresh();
      },
      clear: function() { active = null; activeMessage(); refresh(); },
      prepare: function(figure) { if (active) { validateLibraries(active); prepareFigure(figure, active); } return figure; },
      responsiveLayout: function(updates, compact) {
        if (active) legendPatch(updates, active.settings.legend, compact);
      },
      apply: function() {
        return active ? applyPlot(options, active).then(function() {
          if (options.applied) options.applied(active.settings);
        }) : Promise.resolve();
      },
      load: load
    };
  }
  window.RistWorkTemplate = {install: install, parse: parse, normalize: normalize,
    capture: capture, prepareFigure: prepareFigure, applyPlot: applyPlot};
})();

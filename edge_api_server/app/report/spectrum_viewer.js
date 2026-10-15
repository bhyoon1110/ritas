/* Published report viewer. No analysis editor or result-writing endpoints. ES5. */
(function () {
  "use strict";
  var payload = JSON.parse(document.getElementById("report-data").textContent);
  var original = payload.figure;
  var graph = document.getElementById("report-graph");
  var mode = document.getElementById("view-mode");
  var message = document.getElementById("graph-message");
  var pageSelect = document.getElementById("page-select");
  var page = 0;
  var activeView = "slides";
  var initialPage = /(?:^#|&)page=(\d+)(?:&|$)/.exec(window.location.hash);
  var initialView = /(?:^#|&)view=(slides|analysis|graph)(?:&|$)/.exec(window.location.hash);
  var initialized = false;
  var busy = false;
  var pending = false;
  var hidden = {};
  function clone(value) { return JSON.parse(JSON.stringify(value)); }
  function notifyView() {
    // Presentation state only; never accept edits or expose report content across frames.
    if (window.parent !== window) window.parent.postMessage({type:"rist-report-view-state",view:activeView,page:page}, "*");
  }
  function showPage(index) {
    page = Math.max(0, Math.min(payload.slides.length - 1, index));
    document.getElementById("slide-image").src = payload.slides[page];
    document.getElementById("slide-image").alt = "PPT " + (page + 1) + "페이지";
    pageSelect.value = String(page);
    document.getElementById("page-count").textContent = (page + 1) + " / " + payload.slides.length;
    document.getElementById("previous").disabled = page === 0;
    document.getElementById("next").disabled = page === payload.slides.length - 1;
    notifyView();
  }
  payload.slides.forEach(function (_, index) {
    var option = document.createElement("option");
    option.value = String(index);
    option.textContent = String(index + 1);
    pageSelect.appendChild(option);
  });
  pageSelect.addEventListener("change", function () { showPage(Number(pageSelect.value)); });
  document.getElementById("previous").onclick = function () { showPage(page - 1); };
  document.getElementById("next").onclick = function () { showPage(page + 1); };
  showPage(initialPage ? Number(initialPage[1]) : 0);

  function dimensions(layout) {
    var size = (original.layout.meta || {}).size || {};
    var requested = Number(size.width || original.layout.width) || 1200;
    var width = Math.min(graph.parentNode.clientWidth || requested, requested);
    var ratio = (Number(original.layout.width) || 1200) / (Number(original.layout.height) || 720);
    if (size.ratio === "4:3") { ratio = 4 / 3; }
    if (size.ratio === "16:9") { ratio = 16 / 9; }
    if (size.ratio === "1:1") { ratio = 1; }
    graph.style.maxWidth = requested + "px";
    graph.style.margin = "36px auto 0";
    graph.style.height = Math.max(360, width / ratio) + "px";
    layout.width = width;
    layout.height = Math.max(360, width / ratio);
    layout.autosize = false;
    if (width < 600) {
      layout.margin = {l:50, r:20, t:110, b:140};
      layout.legend = clone(original.layout.legend || {});
      layout.legend.orientation = "h";
      layout.legend.x = 0;
      layout.legend.xanchor = "left";
      layout.legend.y = -0.25;
      layout.legend.yanchor = "top";
      layout.legend.font = {size:10};
      layout.title = clone(original.layout.title || {});
      layout.title.font = {size:12};
      layout.title.x = 0.02;
      layout.title.xanchor = "left";
      if (layout.title.text) layout.title.text = layout.title.text.replace(/, /g, ",<br>");
      layout.height = Math.max(600, layout.height);
      graph.style.height = layout.height + "px";
    }
  }
  function viewFigure() {
    var figure = clone(original);
    var groups = [];
    var offsets = {};
    var minimum = Infinity;
    var maximum = -Infinity;
    original.data.forEach(function (trace) {
      var meta = trace.meta;
      if (!meta.parent) { return; }
      if (groups.indexOf(meta.group) < 0) { groups.push(meta.group); }
      trace.y.forEach(function (y) {
        if (typeof y !== "number" || !isFinite(y)) { return; }
        minimum = Math.min(minimum, y - meta.offset);
        maximum = Math.max(maximum, y - meta.offset);
      });
    });
    var gap = isFinite(maximum - minimum) && maximum > minimum ? (maximum - minimum) * 1.15 : 1;
    groups.forEach(function (group, index) { offsets[group] = (groups.length - 1 - index) * gap; });
    var deltas = {};
    figure.data.forEach(function (trace) {
      var meta = trace.meta;
      var delta = mode.value === "original" ? 0 : -meta.offset;
      if (mode.value === "stack") { delta += offsets[meta.group] || 0; }
      deltas[meta.group] = delta;
      trace.y = trace.y.map(function (y) { return typeof y === "number" && isFinite(y) ? y + delta : y; });
      trace.visible = hidden[meta.group] ? "legendonly" : true;
    });
    var annotations = figure.layout.annotations || [];
    var shapes = figure.layout.shapes || [];
    var shiftedAnnotations = {};
    var shiftedShapes = {};
    function shiftAnnotation(index, group) {
      var annotation = annotations[index];
      if (!annotation || shiftedAnnotations[index]) { return; }
      shiftedAnnotations[index] = true;
      if (!annotation.yref || annotation.yref === "y") {
        if (typeof annotation.y === "number") { annotation.y += deltas[group] || 0; }
        if (annotation.ayref === "y" && typeof annotation.ay === "number") { annotation.ay += deltas[group] || 0; }
      }
      if (hidden[group]) { annotation.visible = false; }
    }
    (figure.layout.meta.labels || []).forEach(function (label) {
      var trace = figure.data[label.traceIndex];
      if (!trace) { return; }
      var group = trace.meta.group;
      shiftAnnotation(label.annotationIndex, group);
      var shape = shapes[label.shapeIndex];
      if (shape && !shiftedShapes[label.shapeIndex]) {
        shiftedShapes[label.shapeIndex] = true;
        if (!shape.yref || shape.yref === "y") {
          if (typeof shape.y0 === "number") { shape.y0 += deltas[group] || 0; }
          if (typeof shape.y1 === "number") { shape.y1 += deltas[group] || 0; }
        }
        if (hidden[group]) { shape.visible = false; }
      }
    });
    annotations.forEach(function (annotation, index) {
      var group = figure.layout.meta.ratioGroups[annotation.name];
      if (group) { shiftAnnotation(index, group); }
    });
    if (mode.value !== "original") {
      delete figure.layout.yaxis.range;
      figure.layout.yaxis.autorange = true;
    }
    var axis = figure.layout.yaxis;
    var minor = axis.minor || (axis.minor = {});
    var stacked = mode.value === "stack" || (mode.value === "original" && figure.layout.meta.stacked);
    var saved = figure.layout.meta.yAxisDisplay || {};
    function restore(target, key, entry, fallback) {
      if (entry) {
        if (entry.exists) target[key] = entry.value;
        else delete target[key];
      } else if (figure.layout.meta.stacked) target[key] = fallback;
    }
    if (stacked) {
      axis.showticklabels = false;
      axis.ticks = ""; axis.ticklen = 0; axis.showgrid = false; axis.zeroline = false;
      minor.ticks = ""; minor.ticklen = 0; minor.showgrid = false;
    } else {
      axis.showticklabels = true;
      // Original stacked layouts carry hidden ticks: recover only visibility,
      // leaving tick spacing, ranges, units and published analysis unchanged.
      restore(axis, "ticks", saved.ticks, "inside");
      restore(axis, "ticklen", saved.ticklen, 7);
      restore(axis, "showgrid", saved.showgrid, false);
      restore(axis, "zeroline", saved.zeroline, false);
      restore(minor, "ticks", saved.minorTicks, "inside");
      restore(minor, "ticklen", saved.minorTicklen, 4);
      restore(minor, "showgrid", saved.minorShowgrid, false);
    }
    dimensions(figure.layout);
    return figure;
  }
  var config = {
    editable: false, scrollZoom: true, displaylogo: false, displayModeBar: true,
    responsive: false, showLink: false,
    modeBarButtonsToRemove: ["select2d", "lasso2d", "sendDataToCloud", "editInChartStudio"],
    edits: {annotationPosition:false, annotationTail:false, annotationText:false,
      axisTitleText:false, colorbarPosition:false, colorbarTitleText:false,
      legendPosition:false, legendText:false, shapePosition:false, titleText:false}
  };
  function draw() {
    if (!original.data.length) {
      graph.hidden = true;
      document.getElementById("graph-fallback").hidden = false;
      document.getElementById("graph-fallback").src = payload.fallback;
      mode.disabled = true;
      document.getElementById("reset-view").disabled = true;
      message.textContent = "좌표가 포함되지 않은 이전 요청입니다. 발행 당시 그래프 이미지를 표시합니다. 인터랙티브 보기는 새 보고서 생성 후 사용할 수 있습니다.";
      return;
    }
    if (busy) { pending = true; return; }
    busy = true;
    var figure = viewFigure();
    var first = !initialized;
    var task;
    try { task = first ? Plotly.newPlot(graph, figure.data, figure.layout, config) : Plotly.react(graph, figure.data, figure.layout, config); }
    catch (error) { failed(error); return; }
    task.then(function () {
      if (first) {
        initialized = true;
        graph.on("plotly_legendclick", function (event) {
          var group = graph.data[event.curveNumber].meta.group;
          hidden[group] = !hidden[group];
          draw();
          return false;
        });
        graph.on("plotly_legenddoubleclick", function () { return false; });
      }
      busy = false;
      message.textContent = "분석 결과는 고정되어 있습니다. 범례를 클릭하면 시료를 표시하거나 숨깁니다.";
      if (pending) { pending = false; draw(); }
    }, failed);
  }
  function failed() {
    busy = false;
    message.textContent = "그래프를 표시하지 못했습니다. PPT 페이지 또는 발행 당시 그래프 이미지를 확인하세요.";
    document.getElementById("graph-fallback").hidden = false;
    document.getElementById("graph-fallback").src = payload.fallback;
  }
  function tab(active) {
    activeView = active;
    ["analysis", "slides", "graph"].forEach(function (name) {
      var panel = document.getElementById(name + "-panel");
      var button = document.getElementById(name + "-tab");
      if (panel) panel.hidden = active !== name;
      if (button) button.setAttribute("aria-pressed", String(active === name));
    });
    if (active === "graph" && !initialized) { draw(); }
    notifyView();
  }
  document.getElementById("slides-tab").onclick = function () { tab("slides"); };
  document.getElementById("graph-tab").onclick = function () { tab("graph"); };
  var analysisTab = document.getElementById("analysis-tab");
  if (analysisTab) analysisTab.onclick = function () { tab("analysis"); };
  tab(initialView && document.getElementById(initialView[1] + "-panel") ? initialView[1] : "slides");
  mode.onchange = draw;
  document.getElementById("reset-view").onclick = function () { mode.value = "original"; hidden = {}; draw(); };
  var resizeTimer;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      if (initialized && !document.getElementById("graph-panel").hidden && !busy) {
        var layout = {};
        dimensions(layout);
        // Responsive presentation only; preserve the user's current zoom ranges.
        Plotly.relayout(graph, {width:layout.width, height:layout.height,
          margin:layout.margin || original.layout.margin,
          legend:layout.legend || original.layout.legend, title:layout.title || original.layout.title});
      }
    }, 150);
  });
  document.addEventListener("keydown", function (event) {
    if (document.getElementById("slides-panel").hidden || /INPUT|SELECT|TEXTAREA|BUTTON/.test(event.target.tagName)) { return; }
    if (event.key === "ArrowLeft" || event.keyCode === 37) { event.preventDefault(); showPage(page - 1); }
    if (event.key === "ArrowRight" || event.keyCode === 39) { event.preventDefault(); showPage(page + 1); }
  });
}());

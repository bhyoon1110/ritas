"""Discoverable, plot-scoped interaction status and Escape for spectrum editors."""

import json


def interaction_modes_html(div_id: str) -> str:
    return _HTML.replace("__PLOT_ID__", json.dumps(div_id))


_HTML = r"""
<style>
.rist-interaction-status { position: fixed; z-index: 12000; display: flex; align-items: center;
  box-sizing: border-box; height: 60px; width: 440px; max-width: calc(100% - 24px);
  padding: 6px 10px; margin: 0; border: 1px solid #cbd5e1; pointer-events: none;
  border-radius: 8px; box-shadow: 0 2px 10px rgba(15,23,42,.14);
  background: rgba(248,250,252,.94); color: #334155; font: 12px Arial, "Noto Sans KR", sans-serif; }
.rist-interaction-status.is-active { border-color: #7ba6cd; background: rgba(238,246,255,.94); }
.rist-interaction-copy { flex: 1; min-width: 0; }
.rist-interaction-name { display: block; font-size: 13px; line-height: 20px; }
.rist-interaction-help { display: block; line-height: 16px; }
.rist-interaction-status button.rist-interaction-exit { flex: 0 0 auto; margin-left: 10px;
  border: 1px solid #9fb3c8; border-radius: 4px; padding: 7px 10px; background: #fff;
  color: #243b53; font: 12px Arial, "Noto Sans KR", sans-serif; white-space: nowrap; cursor: pointer;
  pointer-events: auto; min-height: 36px; }
.rist-interaction-status button:disabled { opacity: .45; cursor: default; pointer-events: none; }
body[data-rist-spectrum-step]:not([data-rist-spectrum-step="2"]) > .rist-interaction-status { display: none !important; }
@media(max-width: 560px) { .rist-interaction-status { height: 76px; padding: 6px 8px; } }
@media print { .rist-interaction-status { display: none !important; } }
</style>
<script>
(function() {
  var gd = document.getElementById(__PLOT_ID__);
  if (!gd || gd._ristInteractionModes) return;
  var bar = document.createElement("div");
  bar.className = "rist-interaction-status";
  bar.innerHTML = "<div class='rist-interaction-copy' role='status' aria-live='polite' aria-atomic='true'>"
    + "<strong class='rist-interaction-name'></strong><span class='rist-interaction-help'></span></div>"
    + "<button type='button' class='rist-interaction-exit' title='현재 모드 종료 · 완료한 편집은 유지'>종료 (Esc)</button>";
  // A viewport portal avoids overflow/transform clipping in the plot's parents.
  // It never changes plot sizing, saved figures or exported images.
  bar.style.visibility = "hidden";
  document.body.appendChild(bar);
  var name = bar.querySelector(".rist-interaction-name"), help = bar.querySelector(".rist-interaction-help");
  var exitButton = bar.querySelector("button"), frame = 0, signature = "", disposed = false;
  var listeners = [], observer;
  var obstacles = ".rist-plot-control-row,.rist-legend-edit-panel,.rist-axis-control-panel,"
    + ".rist-shape-editor-panel,.rist-peak-coordinate-dialog,.rist-peak-recommend-dialog,#rist-color-palette,"
    + ".rist-ratio-selection,.rist-ratio-candidates,.rist-raman-ratio-editor,.rist-ratio-batch,"
    + ".ftir-library-dialog,.raman-library-dialog,.sw-preview > div,.modebar,.legend,"
    + ".rist-ftir-tools-toggle,.rist-raman-tools-toggle,.rist-plot-resize-handle,.rist-axis-wheel-help.is-visible";
  function listen(node, type, handler, capture) {
    node.addEventListener(type, handler, !!capture); listeners.push([node,type,handler,!!capture]);
  }
  function setStyle(key, value) { if (bar.style[key] !== value) bar.style[key] = value; }
  function visibleRect(node) {
    if (!node || !node.getClientRects().length) return null;
    var style = window.getComputedStyle(node);
    if (style.visibility === "hidden" || style.display === "none") return null;
    return node.getBoundingClientRect();
  }
  function viewRect() {
    // visualViewport is optional: Supermium/modern mobile keyboards and zoom;
    // Chrome 49 retains the ordinary layout viewport path.
    var view = window.visualViewport;
    var left = view ? view.offsetLeft : 0, top = view ? view.offsetTop : 0;
    return {left:left,top:top,right:left+(view ? view.width : window.innerWidth),
      bottom:top+(view ? view.height : window.innerHeight)};
  }
  function area(a, b) {
    return Math.max(0,Math.min(a.right,b.right)-Math.max(a.left,b.left))
      * Math.max(0,Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top));
  }
  function canvasRect(plot) { return visibleRect(plot.querySelector(".svg-container") || plot); }
  function editingStage() {
    // The status is a body portal, not a descendant of the hidden editor.
    // Standalone plot editors have no workflow attribute and retain their status.
    var step = document.body.getAttribute("data-rist-spectrum-step");
    return step === null || step === "2";
  }
  function place() {
    if (!editingStage() || !visibleRect(gd)) {setStyle("visibility","hidden");return;}
    var view = viewRect(), graph = canvasRect(gd), active = window.__ristActiveHistoryPlot;
    var other = active && active !== gd && document.documentElement.contains(active) ? canvasRect(active) : null;
    if (!graph || !area(graph,view) || (other && area(other,view))) {
      setStyle("visibility","hidden"); return;
    }
    var width = Math.min(440,view.right-view.left-24), height = bar.offsetHeight;
    if (width < 160 || view.bottom-view.top < height+24) {setStyle("visibility","hidden");return;}
    setStyle("width",width+"px");
    function x(value) {return Math.max(view.left+12,Math.min(value,view.right-width-12));}
    function y(value) {return Math.max(view.top+12,Math.min(value,view.bottom-height-12));}
    var center = x((Math.max(graph.left,view.left)+Math.min(graph.right,view.right)-width)/2);
    // Prefer just below the canvas, otherwise the visible viewport's lower edge.
    var bottom = y(graph.bottom+10), top = y(graph.top-height-10);
    var xs = [center,x(graph.left+12),x(graph.right-width-12)];
    var ys = [bottom,top,y(view.top+12)], avoid = [];
    Array.prototype.forEach.call(document.querySelectorAll(obstacles),function(node) {
      var rect = visibleRect(node); if (rect && area(rect,view)) avoid.push(rect);
    });
    var data = visibleRect(gd.querySelector(".nsewdrag")) || graph, best = null;
    ys.forEach(function(v) {xs.forEach(function(h) {
      var rect = {left:h,top:v,right:h+width,bottom:v+height};
      // Tool/legend overlap has priority over keeping the preferred position.
      var score = area(rect,data)*.02 + Math.abs(h-center) + Math.abs(v-bottom)*2;
      avoid.forEach(function(item) {score += area(rect,{left:item.left-6,top:item.top-6,
        right:item.right+6,bottom:item.bottom+6})*100;});
      if (!best || score < best.score) best = {left:h,top:v,score:score};
    });});
    setStyle("left",Math.round(best.left)+"px"); setStyle("top",Math.round(best.top)+"px");
    setStyle("visibility","visible");
  }
  function state() {
    if (gd._ristLegendEditOpen) return ["legend", "범례·스타일 편집", "전체 저장으로 반영 · 닫기로 취소", false];
    if (gd._ristInlineTextEditing) return ["text", "글자 편집", "Enter로 반영 · Esc로 입력 취소", false];
    if (gd._ristPeakCoordinateOpen) return ["peak-coordinate", "X좌표 피크 추가", "시료·X좌표 입력 후 추가 · Esc로 종료", true];
    if (gd._ristPeakRecommendationOpen) return ["peak-recommend", "LLM 피크 추천", "물질 힌트로 추천 · 검토 필요 · Esc로 종료", true];
    var peak = gd._ristPeakEditMode;
    if (peak === "delete") return ["peak-delete", "피크 삭제", "피크 점 또는 라벨을 클릭 · Esc로 종료", true];
    if (peak === "add") return ["peak-add", "피크 추가", "스펙트럼을 클릭하여 추가 · Esc로 종료", true];
    if (peak === "select") return ["peak-select", "피크 선택", "피크 점을 클릭하여 선택·해제 · Esc로 종료", true];
    if (gd._ristAxisCropActive) return ["crop", "Crop", "남길 영역을 드래그 · Esc로 취소", true];
    if (gd._ristFtirYDragMode || gd._ristRamanYDragMode) return ["y-drag", "Y축 이동", "시료 그래프를 위아래로 드래그 · Esc로 종료", true];
    if (gd._ristRamanRatioMode) {
      var stage = gd._ristRamanRatioStage;
      return ["ratio", stage === "range" ? "비율 일괄 선택" : "비율 선택",
        stage === "range" ? "분자·분모 영역을 선택 · Esc로 종료" :
        stage === "complete" ? "선택 정보창에서 결과 확인 · Esc로 종료" :
        (stage === "denominator" ? "같은 시료의 분모 피크 선택" : "시료와 분자 피크 선택") + " · Esc로 종료", true];
    }
    if (gd._ristShapeDrawMode) return ["shape-draw", gd._ristShapeDrawKind === "text" ? "텍스트 상자 추가" : "도형 추가", "그래프에서 영역을 드래그 · Esc로 종료", true];
    if (gd._ristEditMode || gd.classList.contains("rist-shape-editing")) return ["shape-edit", "그래프 편집", "라벨·도형 위치 및 크기 수정 · Esc로 종료", true];
    if (gd._ristAxisScaleActive) return ["axis-scale", "축 조절", "축을 드래그하여 범위 조절 · Esc로 종료", true];
    if (gd._ristAxisSettingsOpen) return ["axis-settings", "축 범위 설정", "범위와 눈금을 설정 · Esc로 닫기", true];
    if (gd.layout && gd.layout.dragmode === "zoom") return ["zoom", "영역 확대", "드래그하여 확대 · Esc로 이동(Pan) 복귀", true];
    return ["pan", "이동 (Pan)", "드래그하여 이동 · 휠로 확대·축소", false];
  }
  function refresh() {
    frame = 0;
    if (disposed) return;
    if (!document.documentElement.contains(gd)) {destroy();return;}
    var next = state(), key = next.join("|");
    if (key !== signature) {
      signature = key;
      bar.setAttribute("data-mode", next[0]);
      bar.classList.toggle("is-active", next[3]);
      name.textContent = "현재 모드: " + next[1]; help.textContent = next[2];
      exitButton.disabled = !next[3];
    }
    place();
  }
  function schedule() {
    if (!disposed && !frame && window.document && document.documentElement) frame = window.requestAnimationFrame(refresh);
  }
  function activate() {
    window.__ristActiveHistoryPlot = gd;
    document.dispatchEvent(new CustomEvent("rist-active-mode-plot"));
  }
  function ownsEscape(ev) {
    if (!editingStage()) return false;
    if (ev.defaultPrevented || ev.isComposing || ev.keyCode === 229) return false;
    var target = ev.target;
    if (target && (target.isContentEditable || (target.closest && target.closest(
      "input,textarea,select,[contenteditable]:not([contenteditable='false'])")))) return false;
    var active = window.__ristActiveHistoryPlot;
    if (active && !document.documentElement.contains(active)) active = null;
    if (active) return active === gd;
    return document.querySelectorAll(".rist-interaction-status").length === 1;
  }
  function exitMode() {
    if (!state()[3]) return Promise.resolve();
    // All tools already use this bus to release captures, touch locks and drafts.
    // Do not reset history/data or roll back completed edits when leaving a mode.
    gd.dispatchEvent(new CustomEvent("rist-exclusive-interaction-start", {detail: {mode: "interaction-exit"}}));
    return Promise.resolve().then(function() {
      if (gd._ristRestoreNavigation) return gd._ristRestoreNavigation();
    }).then(refresh, function() { schedule(); });
  }
  function destroy() {
    disposed = true;
    if (frame) window.cancelAnimationFrame(frame);
    if (observer) observer.disconnect();
    listeners.forEach(function(item) {item[0].removeEventListener(item[1],item[2],item[3]);});
    if (gd.removeListener) {gd.removeListener("plotly_afterplot",schedule);gd.removeListener("plotly_relayout",schedule);}
    bar.remove(); delete gd._ristInteractionModes;
  }
  gd._ristInteractionModes = {refresh: schedule, exit: exitMode};
  listen(exitButton, "click", function() { activate(); exitMode(); });
  ["pointerdown", "mousedown", "focusin"].forEach(function(type) {
    listen(gd, type, activate, true); listen(bar, type, activate, true);
  });
  listen(gd, "rist-exclusive-interaction-start", function() { activate(); schedule(); });
  ["rist-interaction-mode-change", "rist-edit-mode-change", "rist-axis-settings-change",
    "rist-history-restored", "rist-plot-data-replaced", "focusout"].forEach(function(type) {
    listen(gd, type, schedule);
  });
  // Capture schedules after the current click even when tool handlers stop propagation.
  listen(gd, "click", schedule, true);
  gd.on("plotly_afterplot", schedule); gd.on("plotly_relayout", schedule);
  listen(document, "keydown", function(ev) {
    if (ev.key !== "Escape" || !ownsEscape(ev) || !state()[3]) return;
    ev.preventDefault(); exitMode();
  });
  listen(document, "rist-active-mode-plot", schedule);
  listen(window, "scroll", schedule, true); listen(window, "resize", schedule);
  if (window.visualViewport) {
    listen(window.visualViewport,"scroll",schedule); listen(window.visualViewport,"resize",schedule);
  }
  // No polling or pointer-move work: batch layout changes once per animation frame.
  // Ignore our own portal and SVG hover mutations to avoid feedback/render loops.
  observer = new MutationObserver(function(records) {
    if (records.some(function(record) {
      var target = record.target;
      return target.nodeType === 1 && target.namespaceURI !== "http://www.w3.org/2000/svg"
        && !target.closest(".rist-interaction-status");
    })) schedule();
  });
  observer.observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:["style","class","hidden","data-rist-spectrum-step"]});
  listen(window,"pagehide",function(ev) {if (!ev.persisted) destroy();});
  listen(window,"pageshow",schedule);
  refresh();
})();
</script>
"""

"""Discoverable, plot-scoped interaction status and Escape for spectrum editors."""

import json


def interaction_modes_html(div_id: str) -> str:
    return _HTML.replace("__PLOT_ID__", json.dumps(div_id))


_HTML = r"""
<style>
.rist-interaction-status { display: flex; align-items: center; box-sizing: border-box;
  height: 62px; width: 100%; padding: 6px 10px; margin: 6px 0; border: 1px solid #cbd5e1;
  border-radius: 6px; background: #f8fafc; color: #334155; font: 12px Arial, "Noto Sans KR", sans-serif; }
.rist-interaction-status.is-active { border-color: #7ba6cd; background: #eef6ff; }
.rist-interaction-copy { flex: 1; min-width: 0; }
.rist-interaction-name { display: block; font-size: 13px; line-height: 20px; }
.rist-interaction-help { display: block; line-height: 16px; }
.rist-interaction-status button.rist-interaction-exit { flex: 0 0 auto; margin-left: 10px;
  border: 1px solid #9fb3c8; border-radius: 4px; padding: 7px 10px; background: #fff;
  color: #243b53; font: 12px Arial, "Noto Sans KR", sans-serif; white-space: nowrap; cursor: pointer; }
.rist-interaction-status button:disabled { opacity: .45; cursor: default; }
@media(max-width: 560px) { .rist-interaction-status { height: 82px; padding: 6px 8px; } }
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
  // Outside the Plotly canvas: never changes export dimensions or covers data.
  // Fixed height keeps mode switches from moving the plot or its controls.
  gd.insertAdjacentElement("beforebegin", bar);
  var name = bar.querySelector(".rist-interaction-name"), help = bar.querySelector(".rist-interaction-help");
  var exitButton = bar.querySelector("button"), frame = 0, signature = "";
  function state() {
    if (gd._ristLegendEditOpen) return ["legend", "범례·스타일 편집", "전체 저장으로 반영 · 닫기로 취소", false];
    if (gd._ristInlineTextEditing) return ["text", "글자 편집", "Enter로 반영 · Esc로 입력 취소", false];
    if (gd._ristPeakCoordinateOpen) return ["peak-coordinate", "X좌표 피크 추가", "시료·X좌표 입력 후 추가 · Esc로 종료", true];
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
    var next = state(), key = next.join("|");
    if (key === signature) return;
    signature = key;
    bar.setAttribute("data-mode", next[0]);
    bar.classList.toggle("is-active", next[3]);
    name.textContent = "현재 모드: " + next[1]; help.textContent = next[2];
    exitButton.disabled = !next[3];
  }
  function schedule() { if (!frame) frame = window.requestAnimationFrame(refresh); }
  function activate() { window.__ristActiveHistoryPlot = gd; }
  function ownsEscape(ev) {
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
  gd._ristInteractionModes = {refresh: schedule, exit: exitMode};
  exitButton.addEventListener("click", function() { activate(); exitMode(); });
  ["pointerdown", "mousedown", "focusin"].forEach(function(type) {
    gd.addEventListener(type, activate, true); bar.addEventListener(type, activate, true);
  });
  gd.addEventListener("rist-exclusive-interaction-start", function() { activate(); schedule(); });
  ["rist-interaction-mode-change", "rist-edit-mode-change", "rist-axis-settings-change",
    "rist-history-restored", "rist-plot-data-replaced", "focusout"].forEach(function(type) {
    gd.addEventListener(type, schedule);
  });
  // Capture schedules after the current click even when tool handlers stop propagation.
  gd.addEventListener("click", schedule, true);
  gd.on("plotly_afterplot", schedule); gd.on("plotly_relayout", schedule);
  document.addEventListener("keydown", function(ev) {
    if (ev.key !== "Escape" || !ownsEscape(ev) || !state()[3]) return;
    ev.preventDefault(); exitMode();
  });
  refresh();
})();
</script>
"""

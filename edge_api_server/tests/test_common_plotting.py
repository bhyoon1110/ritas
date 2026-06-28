from __future__ import annotations

import plotly.graph_objects as go

from rist_common.plotting import (
    LEGEND_BREAKPOINT_PX,
    apply_legend_text,
    peak_sensitivity_js,
    write_responsive_html,
)


def test_shared_plotly_module_writes_responsive_html(tmp_path) -> None:
    figure = go.Figure(data=[go.Scatter(x=[1, 2], y=[3, 4])])
    output = tmp_path / "plot.html"

    write_responsive_html(figure, str(output), div_id="shared-plot")

    html = output.read_text(encoding="utf-8")
    assert "shared-plot" in html
    assert "viewport" in html
    assert '"editable": true' in html
    assert '"annotationPosition": true' in html
    assert '"annotationTail": true' in html
    assert '"annotationText": false' in html
    assert LEGEND_BREAKPOINT_PX > 0


def test_shared_plotly_module_accepts_local_plotly_asset(tmp_path) -> None:
    figure = go.Figure(data=[go.Scatter(x=[1, 2], y=[3, 4])])
    output = tmp_path / "local-plotly.html"

    write_responsive_html(
        figure,
        str(output),
        include_plotlyjs="/assets/plotly.min.js",
    )

    assert 'src="/assets/plotly.min.js"' in output.read_text(encoding="utf-8")


def test_shared_plotly_module_applies_legend_text(tmp_path) -> None:
    figure = go.Figure(
        data=[
            go.Scatter(
                x=[1, 2],
                y=[3, 4],
                name="Raw",
                legendgroup="sample",
                legendgrouptitle_text="Sample",
            ),
            go.Scatter(x=[1, 2], y=[4, 5], name="Reference"),
        ]
    )
    output = tmp_path / "plot.html"

    write_responsive_html(
        figure,
        str(output),
        div_id="shared-plot",
        legend_text={0: "원본", "Reference": "기준"},
        legend_group_text={"sample": "시료"},
        legend_text_edit=True,
    )

    html = output.read_text(encoding="utf-8")
    assert "\\uc6d0\\ubcf8" in html
    assert "\\uae30\\uc900" in html
    assert "\\uc2dc\\ub8cc" in html
    assert "text.legendtext" in html
    assert "rist-plot-control-row" in html
    assert "top: 58px" in html
    assert "rist-legend-edit-button" in html
    assert "rist-history-controls" in html
    assert "rist-history-undo" in html
    assert "rist-history-redo" in html
    assert "lucide lucide-undo-2" in html
    assert "lucide lucide-redo-2" in html
    assert "실행취소 (Ctrl/Cmd+Z)" in html
    assert "다시 실행 (Ctrl/Cmd+Shift+Z)" in html
    assert "MAX_HISTORY = 50" in html
    assert "window.Plotly.react" in html
    assert "rist-history-restored" in html
    assert 'key === "z" && ev.shiftKey' in html
    assert 'key === "y"' in html
    assert "rist-legend-edit-panel" in html
    assert "rist-legend-drag-handle" in html
    assert "범례 이동" in html
    assert "legendPosition\": false" in html
    assert 'layout.orientation === "h"' not in html
    assert "z-index: 35" in html
    assert "setMobileLegendReserve(!wide)" in html
    assert "legendReservePx = 120" in html
    assert "mobileMinHeightPx = 900" in html
    assert "baseHeightPx + legendReservePx" in html
    assert '"height": mobileMinHeightPx' in html
    assert '"margin.b": 150' in html
    assert '"legend.y": -0.30' in html
    assert "touch-action: none" in html
    assert "overscroll-behavior: contain" in html
    assert 'gd.addEventListener("touchmove", containLegendScroll' in html
    assert 'gd.addEventListener("wheel", containLegendScroll' in html
    assert "ev.preventDefault()" in html
    assert "handleLeft: handleRect.left - gdRect.left" in html
    assert "handle.style.left = Math.round(dragState.handleLeft + dx)" in html
    assert "if (dragState) return" in html
    assert "setLegendPreview(dx, dy)" in html
    assert 'legend.setAttribute(' in html
    assert 'previewLegendTransform + " " + offset' in html
    assert 'legend.removeAttribute("transform")' in html
    assert "clearLegendPreview()" in html
    assert "handle.addEventListener(\"pointerdown\"" in html
    assert "window.Plotly.relayout(gd, {" in html
    assert "\"legend.x\": nextX" in html
    assert "\"legend.y\": nextY" in html
    assert "max-width: calc(100% - 16px)" in html
    assert "overflow-x: hidden" in html
    assert "rist-legend-opacity-control" in html
    assert "rist-legend-opacity-slider" in html
    assert "aria-label='범례 수정창 투명도'" in html
    assert 'opacitySlider.addEventListener("input"' in html
    assert "panel.style.opacity = String(value / 100)" in html
    assert "position: sticky" in html
    assert "top: -10px" in html
    assert "bottom: -10px" in html
    assert "is-panel-dragging" in html
    assert "constrainPanelPosition" in html
    assert 'panelHead.addEventListener("pointerdown"' in html
    assert 'document.addEventListener("pointermove"' in html
    assert 'document.addEventListener("pointerup", finishPanelDrag)' in html
    assert 'document.addEventListener("pointercancel", finishPanelDrag)' in html
    assert 'panel.style.right = "auto"' in html
    assert "rist-legend-edit-save-all" in html
    assert "<textarea class='rist-legend-edit-input' rows='1'></textarea>" in html
    assert "white-space: pre-wrap" in html
    assert "overflow-wrap: anywhere" in html
    assert "legendDisplayToEdit" in html
    assert "legendEditToDisplay" in html
    assert 'ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)' in html
    assert "rist-legend-color-input" in html
    assert "rist-legend-group-row" in html
    assert "rist-legend-group-title" in html
    assert "rist-legend-group-color" in html
    assert "type='color' title='그룹 색상 선택'" in html
    assert "rist-legend-group-color-button" not in html
    assert "rist-legend-group-clear" in html
    assert "rist-legend-group-add" in html
    assert "rist-legend-group-remove" in html
    assert "선택한 피크 추가" in html
    assert "그룹에서 제외" in html
    assert "selectedPeakCurvesForGroup" in html
    assert "queueGroupAdd" in html
    assert "pendingAddCurves" in html
    assert "updatePendingAddBadge" in html
    assert "data-add-curves" in html
    assert "data-remove-group" in html
    assert "is-pending-group-remove" in html
    assert "is-pending-group-add" in html
    assert "is-drop-target" in html
    assert 'kindBadge.addEventListener("dragstart"' in html
    assert 'groupRow.addEventListener("dragover"' in html
    assert 'groupRow.addEventListener("drop"' in html
    assert 'ev.dataTransfer.setData("text/plain", String(curve))' in html
    assert "피크 그룹으로 드래그" in html
    assert "rist-legend-row-kind" in html
    assert "isSampleCurve" in html
    assert "isPeakCurve" in html
    assert "sampleNameForCurve" in html
    assert "peakCurvesForLegendItem" in html
    assert '" is-sample"' in html
    assert '" is-peak"' in html
    assert '"샘플" : (peakCurve ? "피크" : "항목")' in html
    assert "rist-legend-peak-delete" in html
    assert "is-pending-delete" in html
    assert "data-delete" in html
    assert "dispatchPeakDelete" in html
    assert "deleteCurves.concat(peakCurvesForLegendItem(curve))" in html
    assert "dispatchPeakGroupClear" in html
    assert "dispatchPeakGroupUpdate" in html
    assert "addCurves: addCurves || []" in html
    assert "removeCurves: removeCurves || []" in html
    assert "data-clear" in html
    assert "data-first-curve" in html
    assert "nextTitle === manualPeakGroupName(firstCurve)" in html
    assert "nextName && nextName !== traceName(curve)" in html
    assert "is-pending-clear" in html
    assert "manualPeakGroupKey" in html
    assert "manualPeakGroupName" in html
    assert 'kind: "group"' in html
    assert "rist-legend-bulk-controls" in html
    assert "rist-legend-bulk-button" in html
    assert "rist-legend-name-change" in html
    assert "rist-legend-color-change" in html
    assert "rist-legend-visibility-change" in html
    assert "function closePanel()" in html
    assert "panel.contains(ev.target)" in html
    assert "btn.contains(ev.target)" in html
    assert figure.data[0].name == "원본"
    assert figure.data[1].name == "기준"
    assert figure.data[0].legendgrouptitle.text == "시료"


def test_trace_highlight_keeps_plain_double_click_reset(tmp_path) -> None:
    figure = go.Figure(data=[go.Scatter(x=[1, 2], y=[3, 4], name="Raw")])
    output = tmp_path / "highlight.html"

    write_responsive_html(
        figure,
        str(output),
        div_id="highlight-plot",
        trace_highlight=True,
    )

    html = output.read_text(encoding="utf-8")
    assert "if (!modifier) return true" in html
    assert "e.shiftKey || e.altKey" in html


def test_shared_peak_editor_adds_peak_controls(tmp_path) -> None:
    figure = go.Figure(
        data=[
            go.Scatter(x=[1, 2], y=[3, 4], name="Raw"),
            go.Scatter(
                x=[1.5],
                y=[3.5],
                mode="markers",
                name="Peak",
                meta={"rist_peak": {"source": "test"}},
            ),
        ]
    )
    figure.update_layout(
        meta={
            "ristPeakLabels": [
                {
                    "traceIndex": 1,
                    "annotationIndex": 0,
                    "shapeIndex": 0,
                    "legendgroup": "peak",
                    "wnText": "1.5",
                }
            ]
        },
        annotations=[{"x": 1.5, "y": 3.5, "text": "Peak"}],
        shapes=[{"type": "line", "x0": 1.5, "x1": 1.5, "y0": 0, "y1": 3.5}],
    )
    output = tmp_path / "peaks.html"

    write_responsive_html(
        figure,
        str(output),
        div_id="peak-plot",
        peak_editor=True,
        shape_editor=True,
    )

    html = output.read_text(encoding="utf-8")
    assert "ristPeakLabels" in html
    assert "rist_peak" in html
    assert "rist-peak-edit-button" in html
    assert "피크 추가" in html
    assert "피크 삭제" in html
    assert "피크 선택" in html
    assert "그룹명" in html
    assert "그룹 적용" in html
    assert "rist-peak-group-name" in html
    assert "rist-peak-group-color" in html
    assert "applyPeakGroup" in html
    assert "gd._ristHistory.capture()" in html
    assert "selectedPeakCurves" in html
    assert "togglePeakSelection" in html
    assert 'if (prev === "select" && mode !== "select")' in html
    assert "manual-peak-group:" in html
    assert "manual_group_key" in html
    assert "group_color" in html
    assert "rist_color_group" in html
    assert "original_color" in html
    assert "original_legendgroup" in html
    assert "original_legend_title" in html
    assert "var originalColor = peakMeta.original_color || traceColor(curve)" in html
    assert "colors.push(originalColor)" in html
    assert "legendgroup: curves.map(function() { return groupKey; })" in html
    assert "legendgrouptitle.text" in html
    assert "marker.color" in html
    assert "line.color" in html
    assert "nearestPeakCurveFromEvent" in html
    assert ".legend,.modebar,.rist-plot-control-row,.rist-legend-edit-panel" in html
    assert "function axisPixel" in html
    assert "axis.d2p" in html
    assert "var pickRadius = 32" in html
    assert "px < 0 || py < 0 || px > r.width || py > r.height" in html
    assert "distanceSquared <= bestDistanceSquared" in html
    assert "bestVisibleX <= 120" not in html
    assert "annotationClick && bestX <= 120" not in html
    assert "handlePeakSelectPointer" in html
    assert 'gd.addEventListener("mousedown", handlePeakSelectPointer, true)' in html
    assert 'ev.type === "click" && gd._ristHandledPeakSelectClick' in html
    assert "gd._ristHandledPeakSelectAt = Date.now()" in html
    assert "ev.event" in html
    assert "피크 선택 필요" in html
    assert "적용 실패" in html
    assert "RIST peak group apply failed" in html
    assert "clearPeakGroupByKey" in html
    assert "updatePeakGroupByKey" in html
    assert "existingCurves" in html
    assert "finalCurves" in html
    assert "affectedCurves" in html
    assert "detail.addCurves" in html
    assert "detail.removeCurves" in html
    assert "rist-peak-group-clear" in html
    assert "rist-peak-group-update" in html
    assert "RIST peak group update failed" in html
    assert "name: groupName" not in html
    assert "Plotly.addTraces" in html
    assert "Plotly.deleteTraces" in html
    assert 'gd.addEventListener("rist-peak-delete"' in html
    assert ".sort(function(a, b) { return b - a; })" in html
    assert "return promise.then(function() { return deletePeakTrace(curve, true); })" in html
    assert "captureevents: true" in html
    assert "updatePeakColorList" in html
    assert "rist-legend-color-change" in html
    assert "rist_sample_group" in html
    assert "rist_sample_parent" in html
    assert "syncSampleChildren" in html
    assert "childCurvesForSample" in html
    assert "peakMatchesCurrentSensitivity" in html
    assert "peakMatchesCurrentSensitivity(childCurve) ? visible : false" in html
    assert "rist-shape-tool-button" in html
    assert "lucide lucide-square" in html
    assert "rist-shape-editor-panel" in html
    assert "rist-shape-editor-text" in html
    assert "rist-shape-border-color" in html
    assert "rist-shape-fill-color" in html
    assert "rist-shape-border-style" in html
    assert "<option value='none'>선 없음</option>" in html
    assert "<option value='solid' selected>실선</option>" in html
    assert "<option value='dash'>파선</option>" in html
    assert "<option value='dot'>점선</option>" in html
    assert "<option value='dashdot'>일점쇄선</option>" in html
    assert "rist-shape-opacity" in html
    assert "rist-shape-fill-none-row" in html
    assert "rist-shape-fill-none" in html
    assert "배경 없음" in html
    assert "function fillColor" in html
    assert "function updateFillControl" in html
    assert "fillNoneInput.checked ? 0" in html
    assert 'fillNoneInput.addEventListener("change"' in html
    assert "rist-shape-text-options" in html
    assert "rist-shape-font-color" in html
    assert "rist-shape-font-size" in html
    assert "function fontSizeValue" in html
    assert "function borderLine" in html
    assert 'width: hidden ? 0 : 2' in html
    assert "color: fontColorInput.value" in html
    assert "size: fontSizeValue()" in html
    assert "rist-shape-draw-preview" in html
    assert "rist-shape-kind-tabs" in html
    assert "data-kind='rect'>사각형</button>" in html
    assert "data-kind='text'>텍스트 박스</button>" in html
    assert "rist-shape-selection" in html
    assert "rist-shape-resize-handle" in html
    assert "rist_rect:" in html
    assert "rist_text_box:" in html
    assert "rist_text_box_text:" in html
    assert "function addObject" in html
    assert "rist-shape-apply" not in html
    assert ">적용</button>" not in html
    assert "function deleteSelection" in html
    assert "var editSnapshot = null" in html
    assert "function captureEditSnapshot" in html
    assert "function finishSelection" in html
    assert "function editedObjectsFromControls" in html
    assert "function previewSelection" in html
    assert "function schedulePreview" in html
    assert 'input.addEventListener("input", schedulePreview)' in html
    assert 'document.addEventListener("pointerdown"' in html
    assert "panel.contains(ev.target)" in html
    assert "finishSelection(true)" in html
    assert "gd._ristHistory.captureState(editSnapshot.historyState)" in html
    assert "captureState: captureState" in html
    assert "snapshot: snapshot" in html
    assert "reset: reset" in html
    assert 'gd.addEventListener("rist-plot-data-replaced"' in html
    assert "function objectAtPoint" in html
    assert "function resizedBounds" in html
    assert "function applyTransformedBounds" in html
    assert "function updateSelectionOverlay" in html
    assert 'mode: handle ? handle.dataset.dir : "move"' in html
    assert 'gd.addEventListener("pointerdown"' in html
    assert 'gd.on("plotly_clickannotation"' in html
    assert "syncOriginalArrays" in html
    assert "gd._ristHistory.capture()" in html


def test_peak_sensitivity_control_filters_detected_peak_metadata() -> None:
    html = peak_sensitivity_js("peak-plot", initial="low")

    assert "rist-peak-sensitivity-control" in html
    assert "rist-peak-sensitivity-slider" in html
    assert "rist-peak-sensitivity-number" in html
    assert "피크 검출 민감도" in html
    assert "sensitivity_min" in html
    assert "applySensitivity" in html
    assert "requestSensitivity" in html
    assert "pendingSensitivity" in html
    assert "updateSensitivityInteraction" in html
    assert "sliderPointerActive" in html
    assert "sliderKeyboardActive" in html
    assert "numberInputActive" in html
    assert "rist-peak-sensitivity-interaction-end" in html
    assert "min='0' max='100'" in html
    assert "피크 검출 민감도 수치" in html
    assert 'eligible ? (on ? true : "legendonly") : false' in html
    assert "changedCurves" in html
    assert "currentVisibility !== visibility" in html
    assert "if (!changedCurves.length)" in html
    assert "window.Plotly.update" in html
    assert "updateStatus(eligibleCount)" in html
    assert "gd._ristPeakSensitivityValue = sensitivity" in html
    assert "rist-peak-sensitivity-change" in html
    assert "value='25'" in html


def test_peak_editor_prefers_nearest_local_maximum_by_x(tmp_path) -> None:
    figure = go.Figure(data=[go.Scatter(x=[1, 2], y=[3, 4], name="Raw")])
    output = tmp_path / "snap-peak.html"

    write_responsive_html(
        figure,
        str(output),
        div_id="snap-peak",
        crosshair=True,
        peak_editor=True,
    )

    html = output.read_text(encoding="utf-8")
    assert "gd._snapPoint = { x: best.x, y: best.y, curve: best.tt }" in html
    assert "if (y < prev || y < next) continue" in html
    assert "var d = Math.abs(x - curX)" in html
    assert "localMaximum: true" in html
    assert "var curY = fl.yaxis.p2d(py)" in html
    assert "var targetTrace = -1" in html
    assert "var dy = Math.abs(yAtX - curY)" in html
    assert "if (targetTrace >= 0 && t2 !== targetTrace) continue" in html
    assert "yNearestTrace: true" in html
    assert "if (tr.meta && tr.meta.rist_peak) continue" in html
    assert "return { x: gd._snapPoint.x, y: gd._snapPoint.y, snapped: true }" in html


def test_apply_legend_text_renames_without_html_output() -> None:
    figure = go.Figure(data=[go.Scatter(x=[1], y=[2], name="Raw")])

    apply_legend_text(figure, {"Raw": "원본"})

    assert figure.data[0].name == "원본"

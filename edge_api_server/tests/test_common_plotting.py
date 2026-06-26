from __future__ import annotations

import plotly.graph_objects as go

from rist_common.plotting import (
    LEGEND_BREAKPOINT_PX,
    apply_legend_text,
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
    assert "rist-legend-edit-button" in html
    assert "rist-legend-edit-panel" in html
    assert "rist-legend-edit-save-all" in html
    assert "rist-legend-color-input" in html
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
    )

    html = output.read_text(encoding="utf-8")
    assert "ristPeakLabels" in html
    assert "rist_peak" in html
    assert "rist-peak-edit-button" in html
    assert "피크 추가" in html
    assert "피크 삭제" in html
    assert "Plotly.addTraces" in html
    assert "Plotly.deleteTraces" in html
    assert "captureevents: true" in html
    assert "updatePeakColorList" in html
    assert "rist-legend-color-change" in html
    assert "rist_sample_group" in html
    assert "rist_sample_parent" in html
    assert "syncSampleChildren" in html
    assert "childCurvesForSample" in html


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
    assert "if (tr.meta && tr.meta.rist_peak) continue" in html
    assert "return { x: gd._snapPoint.x, y: gd._snapPoint.y, snapped: true }" in html


def test_apply_legend_text_renames_without_html_output() -> None:
    figure = go.Figure(data=[go.Scatter(x=[1], y=[2], name="Raw")])

    apply_legend_text(figure, {"Raw": "원본"})

    assert figure.data[0].name == "원본"

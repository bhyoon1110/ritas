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
    assert "rist-legend-edit-button" in html
    assert "rist-legend-edit-panel" in html
    assert "rist-legend-name-change" in html
    assert figure.data[0].name == "원본"
    assert figure.data[1].name == "기준"
    assert figure.data[0].legendgrouptitle.text == "시료"


def test_apply_legend_text_renames_without_html_output() -> None:
    figure = go.Figure(data=[go.Scatter(x=[1], y=[2], name="Raw")])

    apply_legend_text(figure, {"Raw": "원본"})

    assert figure.data[0].name == "원본"

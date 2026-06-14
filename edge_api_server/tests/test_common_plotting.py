from __future__ import annotations

import plotly.graph_objects as go

from rist_common.plotting import LEGEND_BREAKPOINT_PX, write_responsive_html


def test_shared_plotly_module_writes_responsive_html(tmp_path) -> None:
    figure = go.Figure(data=[go.Scatter(x=[1, 2], y=[3, 4])])
    output = tmp_path / "plot.html"

    write_responsive_html(figure, str(output), div_id="shared-plot")

    html = output.read_text(encoding="utf-8")
    assert "shared-plot" in html
    assert "viewport" in html
    assert LEGEND_BREAKPOINT_PX > 0

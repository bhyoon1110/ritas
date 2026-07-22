from __future__ import annotations

import base64
from pathlib import Path

from app.xrd_portable_html import make_xrd_html_portable


def test_make_xrd_html_portable_embeds_plotly_without_raw_script_terminator(
    tmp_path: Path,
) -> None:
    asset = tmp_path / "plotly.min.js"
    asset_bytes = b"window.Plotly={ready:true};/* </script> */"
    asset.write_bytes(asset_bytes)
    source = (
        '<html><head><script charset="utf-8" src="/xrd/assets/plotly.min.js">'
        "</script></head><body><script>Plotly.newPlot('graph', [], {});</script></body></html>"
    )

    result = make_xrd_html_portable(source, asset_path=asset)

    assert 'data-xrd-embedded-plotly="true"' in result
    assert 'src="/xrd/assets/plotly.min.js"' not in result
    assert asset_bytes.decode("ascii") not in result
    assert base64.b64encode(asset_bytes).decode("ascii") in result
    assert "Plotly.newPlot('graph'" in result


def test_make_xrd_html_portable_is_idempotent(tmp_path: Path) -> None:
    asset = tmp_path / "plotly.min.js"
    asset.write_text("window.Plotly={};", encoding="utf-8")
    source = '<script src="/xrd/assets/plotly.min.js"></script>'

    once = make_xrd_html_portable(source, asset_path=asset)

    assert make_xrd_html_portable(once, asset_path=asset) == once

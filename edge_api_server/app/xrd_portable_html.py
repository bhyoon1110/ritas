from __future__ import annotations

import base64
from functools import lru_cache
import json
from pathlib import Path
import re

import plotly


XRD_PLOTLY_MARKER = 'data-xrd-embedded-plotly="true"'
_XRD_PLOTLY_SCRIPT_PATTERN = re.compile(
    r'<script\b[^>]*\bsrc=["\'](?:https?://[^"\']+)?/xrd/assets/plotly\.min\.js["\'][^>]*>\s*</script>',
    flags=re.IGNORECASE,
)
_PLOTLY_CDN_SCRIPT_PATTERN = re.compile(
    r'<script\b[^>]*\bsrc=["\']https://cdn\.plot\.ly/plotly-[^"\']+\.min\.js["\'][^>]*>\s*</script>',
    flags=re.IGNORECASE,
)


def plotly_asset_path() -> Path:
    return Path(plotly.__file__).resolve().parent / "package_data" / "plotly.min.js"


@lru_cache(maxsize=4)
def _encoded_asset(path_text: str, modified_ns: int, size: int) -> str:
    del modified_ns, size
    return base64.b64encode(Path(path_text).read_bytes()).decode("ascii")


def _embedded_plotly_script(asset_path: Path) -> str:
    resolved = asset_path.expanduser().resolve()
    stat = resolved.stat()
    encoded = _encoded_asset(str(resolved), stat.st_mtime_ns, stat.st_size)
    chunks = [encoded[index:index + 64_000] for index in range(0, len(encoded), 64_000)]
    return (
        f"<script {XRD_PLOTLY_MARKER}>\n"
        "(function(){"
        f"var encoded={json.dumps(chunks, separators=(',', ':'))}.join('');"
        "var binary=atob(encoded);"
        "var bytes=new Uint8Array(binary.length);"
        "for(var index=0;index<binary.length;index++){bytes[index]=binary.charCodeAt(index);}"
        "var code=window.TextDecoder?new TextDecoder('utf-8').decode(bytes)"
        ":decodeURIComponent(escape(binary));"
        "(0,eval)(code);"
        "})();\n</script>"
    )


def make_xrd_html_portable(html_text: str, *, asset_path: Path | None = None) -> str:
    """Embed Plotly so both XRD graphs work in downloaded/offline HTML files."""
    if not html_text or XRD_PLOTLY_MARKER in html_text:
        return html_text

    pattern = _XRD_PLOTLY_SCRIPT_PATTERN
    if not pattern.search(html_text):
        pattern = _PLOTLY_CDN_SCRIPT_PATTERN
    if not pattern.search(html_text):
        return html_text

    embedded = _embedded_plotly_script(asset_path or plotly_asset_path())
    return pattern.sub(lambda _match: embedded, html_text, count=1)

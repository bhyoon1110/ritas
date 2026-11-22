"""Shared controls for application pages, never for exported report documents."""
from pathlib import Path
import re

_STYLE = '<style id="rist-ui-controls">' + Path(__file__).with_name("ui_controls.css").read_text(encoding="utf-8") + '</style>'


def with_ui_controls(html: str) -> str:
    """Scope controls to the page body and keep repeated UI wrappers idempotent."""
    if 'id="rist-ui-controls"' in html:
        return html
    html = re.sub(r"<body\b([^>]*)>", lambda match: '<body data-rist-ui' + match.group(1) + '>', html, count=1, flags=re.IGNORECASE)
    return re.sub(r"</head\s*>", lambda match: _STYLE + match.group(0), html, count=1, flags=re.IGNORECASE)

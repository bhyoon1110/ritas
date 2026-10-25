"""Shared staged authoring UI; existing project authentication/report APIs stay authoritative."""
from pathlib import Path


def spectrum_workflow_script() -> str:
    source = Path(__file__)
    return ("<style>" + source.with_suffix(".css").read_text(encoding="utf-8") + "</style>"
            + "<script>" + source.with_name("spectrum_graph_file.js").read_text(encoding="utf-8") + "</script>"
            + "<script>" + source.with_suffix(".js").read_text(encoding="utf-8") + "</script>")

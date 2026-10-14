"""Portable spectrum settings, deliberately separate from RAW/workspace snapshots."""

from pathlib import Path


def work_template_script() -> str:
    return "<script>\n" + Path(__file__).with_suffix(".js").read_text(encoding="utf-8") + "\n</script>"

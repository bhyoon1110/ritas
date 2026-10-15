"""Portable spectrum settings, deliberately separate from RAW/workspace snapshots."""

from pathlib import Path


def work_template_script() -> str:
    source = Path(__file__)
    return (
        "<style>\n" + source.with_suffix(".css").read_text(encoding="utf-8") + "\n</style>"
        + "<script>\n" + source.with_suffix(".js").read_text(encoding="utf-8") + "\n</script>"
    )

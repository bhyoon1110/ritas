"""Offline, ES5-compatible feedback shared by file export surfaces."""
from pathlib import Path


def file_progress_script() -> str:
    return Path(__file__).with_suffix('.js').read_text(encoding='utf-8')


def file_progress_html() -> str:
    return '<script>' + file_progress_script() + '</script>'

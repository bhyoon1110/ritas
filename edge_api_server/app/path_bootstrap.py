"""Import-path bootstrap for sibling project packages.

The production systemd service starts inside edge_api_server. In that layout
Python sees app/, but sibling projects such as ../rin and ../sune may be absent
from sys.path unless the virtualenv was freshly installed in editable mode.
"""

from __future__ import annotations

import sys
from pathlib import Path


def add_project_package_paths() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    for path in (
        repo_root,
        repo_root / "sune",
        repo_root / "rin",
        repo_root / "common",
    ):
        text = str(path)
        if path.exists() and text not in sys.path:
            sys.path.insert(0, text)

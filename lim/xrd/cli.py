"""`python -m lim.xrd.cli` 진입점."""

from __future__ import annotations

import sys
from pathlib import Path


_LIM_DIR = Path(__file__).resolve().parents[1]
if str(_LIM_DIR) not in sys.path:
    sys.path.insert(0, str(_LIM_DIR))

from xrd_plot import main  # noqa: E402


if __name__ == "__main__":
    main()

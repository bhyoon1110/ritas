from __future__ import annotations

import importlib.util
from pathlib import Path


def test_plot_library_import_does_not_load_data_or_write_html() -> None:
    path = Path(__file__).resolve().parents[1] / "plot_ftir_library.py"
    spec = importlib.util.spec_from_file_location("plot_ftir_library_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(module)

    assert callable(module.main)

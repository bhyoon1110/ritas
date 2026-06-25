from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd
import pytest
from argparse import Namespace

SUNE_DIR = Path(__file__).resolve().parents[1]
if str(SUNE_DIR) not in sys.path:
    sys.path.insert(0, str(SUNE_DIR))

from ftir.library_matcher import assign_confidence_tier
from ftir.cli import _resolve_peak_params
from ftir.preprocess import load_csv
from ftir.plotting import build_peak_fig, ftir_peak_label_sync_js
from ftir.scoring import rank_best_per_material


def test_load_csv_reports_missing_required_columns(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("foo,bar\n1,2\n", encoding="utf-8")

    with pytest.raises(ValueError) as raised:
        load_csv(path, 400, 4000)

    assert "wavenumber/absorbance" in str(raised.value)


def test_assign_confidence_tier_rejects_empty_candidates() -> None:
    with pytest.raises(ValueError):
        assign_confidence_tier(pd.DataFrame(), 85, 10, 65)


def test_rank_best_per_material_rejects_empty_scores() -> None:
    with pytest.raises(ValueError):
        rank_best_per_material(pd.DataFrame(), 5, 85, 10, 65)


def test_peak_sensitivity_medium_keeps_existing_defaults() -> None:
    args = Namespace(
        peak_sensitivity="medium",
        peak_height=None,
        peak_prominence=None,
        peak_distance=None,
    )

    assert _resolve_peak_params(args) == {
        "height": 0.05,
        "prominence": 0.03,
        "distance": 15,
    }


def test_peak_sensitivity_low_suppresses_small_peaks() -> None:
    args = Namespace(
        peak_sensitivity="low",
        peak_height=None,
        peak_prominence=None,
        peak_distance=None,
    )

    assert _resolve_peak_params(args) == {
        "height": 0.08,
        "prominence": 0.06,
        "distance": 25,
    }


def test_peak_explicit_options_override_sensitivity() -> None:
    args = Namespace(
        peak_sensitivity="low",
        peak_height=0.12,
        peak_prominence=0.09,
        peak_distance=40,
    )

    assert _resolve_peak_params(args) == {
        "height": 0.12,
        "prominence": 0.09,
        "distance": 40,
    }


def test_peak_params_reject_invalid_values() -> None:
    args = Namespace(
        peak_sensitivity="medium",
        peak_height=None,
        peak_prominence=None,
        peak_distance=0,
    )

    with pytest.raises(ValueError):
        _resolve_peak_params(args)


def test_peak_fig_labels_show_functional_group_names() -> None:
    grid = pd.Series([1000.0, 1100.0, 1200.0, 1300.0]).to_numpy()
    sample_vec = pd.Series([0.1, 0.4, 1.0, 0.2]).to_numpy()
    func_groups = [(1200, 20, "C-O stretch", "#2563eb", "")]

    fig = build_peak_fig(
        sample_vec,
        grid,
        peak_idx=pd.Series([2]).to_numpy(),
        peak_wn=pd.Series([1200.0]).to_numpy(),
        peak_val=pd.Series([1.0]).to_numpy(),
        peak_fwhm=pd.Series([12.0]).to_numpy(),
        func_groups=func_groups,
        sample_label="sample",
        wn_min=1000,
        wn_max=1300,
    )

    assert "C-O stretch" in fig.layout.annotations[0].text
    assert fig.layout.meta["ftirPeakLabels"][0]["legendgroup"] == "C-O stretch"


def test_peak_label_sync_script_listens_for_legend_edits() -> None:
    html = ftir_peak_label_sync_js("peak-plot")

    assert "rist-legend-name-change" in html
    assert "ftirPeakLabels" in html

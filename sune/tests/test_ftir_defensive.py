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
from ftir.peaks import build_interactive_peak_candidates, peak_params_for_sensitivity
from ftir.plotting import (
    build_multi_peak_fig,
    build_peak_fig,
    ftir_abs_trans_toggle_js,
    ftir_peak_label_sync_js,
)
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


def test_numeric_peak_sensitivity_interpolates_presets() -> None:
    assert peak_params_for_sensitivity(0) == {
        "height": 0.40,
        "prominence": 0.30,
        "distance": 70,
    }
    assert peak_params_for_sensitivity(25) == {
        "height": 0.08,
        "prominence": 0.06,
        "distance": 25,
    }
    assert peak_params_for_sensitivity(50) == {
        "height": 0.05,
        "prominence": 0.03,
        "distance": 15,
    }
    assert peak_params_for_sensitivity(100) == {
        "height": 0.03,
        "prominence": 0.015,
        "distance": 10,
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
    assert fig.layout.annotations[0].captureevents is True
    assert fig.layout.meta["ristPeakLabels"][0]["legendgroup"] == "C-O stretch"
    assert fig.layout.meta["ristPeakLabels"][0]["labelKey"] == "sample:0:peak:C-O stretch"
    assert "traceIndex" in fig.layout.meta["ristPeakLabels"][0]
    assert "shapeIndex" in fig.layout.meta["ristPeakLabels"][0]
    assert fig.data[1].meta["rist_peak"]["source"] == "detected"
    assert "sensitivity_min" in fig.data[1].meta["rist_peak"]
    assert fig.data[0].meta["rist_sample_parent"] is True
    assert fig.data[1].meta["rist_sample_group"] == "sample:0"
    assert fig.data[1].meta["rist_peak"]["sample_group"] == "sample:0"
    assert fig.layout.legend.orientation == "h"
    assert fig.layout.title.y == pytest.approx(0.98)
    assert fig.layout.title.yanchor == "top"
    assert fig.layout.margin.t == 100
    assert fig.layout.margin.b >= 120


def test_interactive_peak_candidates_include_all_sensitivity_levels() -> None:
    grid = pd.Series(range(100), dtype=float).to_numpy()
    sample_vec = pd.Series([0.0] * 100, dtype=float).to_numpy()
    sample_vec[20] = 0.04
    sample_vec[50] = 0.07
    sample_vec[80] = 0.50

    candidates = build_interactive_peak_candidates(
        sample_vec,
        grid,
        selected_idx=pd.Series([80]).to_numpy(),
        selected_wn=pd.Series([80.0]).to_numpy(),
        selected_val=pd.Series([0.50]).to_numpy(),
        selected_fwhm=pd.Series([1.0]).to_numpy(),
    )
    by_index = {candidate["index"]: candidate for candidate in candidates}

    assert by_index[20]["levels"] == ["high"]
    assert by_index[50]["levels"] == ["high", "medium"]
    assert by_index[80]["levels"] == ["high", "low", "medium"]
    assert 0 < by_index[20]["sensitivity_min"] <= 100
    assert 0 < by_index[50]["sensitivity_min"] < by_index[20]["sensitivity_min"]
    assert by_index[80]["sensitivity_min"] == 0
    assert by_index[20]["initial"] is False
    assert by_index[80]["initial"] is True


def test_peak_fig_keeps_unknown_peaks_as_separate_legend_items() -> None:
    grid = pd.Series([1000.0, 1100.0, 1200.0, 1300.0]).to_numpy()
    sample_vec = pd.Series([0.1, 0.8, 0.2, 0.7]).to_numpy()

    fig = build_peak_fig(
        sample_vec,
        grid,
        peak_idx=pd.Series([1, 3]).to_numpy(),
        peak_wn=pd.Series([1100.0, 1300.0]).to_numpy(),
        peak_val=pd.Series([0.8, 0.7]).to_numpy(),
        peak_fwhm=pd.Series([10.0, 11.0]).to_numpy(),
        func_groups=[],
        sample_label="sample",
        wn_min=1000,
        wn_max=1300,
    )

    peak_traces = list(fig.data[1:])
    assert peak_traces[0].legendgroup == "unknown:1100.0"
    assert peak_traces[1].legendgroup == "unknown:1300.0"
    assert peak_traces[0].name == "1100 cm⁻¹"
    assert peak_traces[1].name == "1300 cm⁻¹"


def test_peak_label_sync_script_listens_for_legend_edits() -> None:
    html = ftir_peak_label_sync_js("peak-plot")

    assert "rist-legend-name-change" in html
    assert "plotly_restyle" in html
    assert "rist-legend-visibility-change" in html
    assert "ristPeakLabels" in html
    assert "rist-peak-edit-button" in html


def test_ftir_toggle_aligns_toolbar_with_legend_right_edge() -> None:
    html = ftir_abs_trans_toggle_js(
        "peak-plot",
        yaxis_titles={
            "yaxis": {
                "absorbance": "Normalized Absorbance",
                "transmittance": "Transmittance (%)",
            }
        },
    )

    assert "function alignToolbarWithLegend()" in html
    assert 'gd.querySelector(".legend")' in html
    assert "gdRect.right - legendRect.right" in html
    assert 'gd.on("plotly_afterplot", alignToolbarWithLegend)' in html
    assert 'window.addEventListener("resize", alignToolbarWithLegend)' in html


def test_multi_peak_fig_groups_peaks_under_each_sample() -> None:
    grid = pd.Series([1000.0, 1100.0, 1200.0, 1300.0]).to_numpy()
    func_groups = [(1200, 20, "C-O stretch", "#2563eb", "")]
    samples = [
        {
            "label": "sample-a",
            "grid": grid,
            "sample_vec": pd.Series([0.1, 0.4, 1.0, 0.2]).to_numpy(),
            "peak_idx": pd.Series([2]).to_numpy(),
            "peak_wn": pd.Series([1200.0]).to_numpy(),
            "peak_val": pd.Series([1.0]).to_numpy(),
            "peak_fwhm": pd.Series([12.0]).to_numpy(),
        },
        {
            "label": "sample-b",
            "grid": grid,
            "sample_vec": pd.Series([0.2, 0.9, 0.3, 0.1]).to_numpy(),
            "peak_idx": pd.Series([1]).to_numpy(),
            "peak_wn": pd.Series([1100.0]).to_numpy(),
            "peak_val": pd.Series([0.9]).to_numpy(),
            "peak_fwhm": pd.Series([10.0]).to_numpy(),
        },
    ]

    fig = build_multi_peak_fig(samples, func_groups, wn_min=1000, wn_max=1300)

    assert fig.data[0].name == "sample-a"
    assert fig.data[0].legendgroup == "sample:0"
    assert fig.data[0].meta["rist_sample_parent"] is True
    assert fig.data[1].legendgroup == "sample:0"
    assert fig.data[1].meta["rist_peak"]["sample_group"] == "sample:0"
    assert fig.data[2].name == "sample-b"
    assert fig.data[2].legendgroup == "sample:1"
    assert fig.data[3].legendgroup == "sample:1"
    assert fig.layout.legend.orientation == "v"
    assert fig.layout.legend.groupclick == "toggleitem"
    assert fig.layout.title.y == pytest.approx(0.98)
    assert fig.layout.title.yanchor == "top"
    assert fig.layout.margin.t == 105
    assert fig.layout.meta["ristPeakLabels"][0]["labelKey"] == "sample:0:peak:C-O stretch"
    assert fig.layout.meta["ristPeakLabels"][1]["labelKey"] == "sample:1:peak:unknown:1100.0"

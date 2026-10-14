"""Raw coordinates, not merely a similar-looking normalized curve, are the contract."""

import numpy as np
import pytest

from ftir.web_analysis import analyze_dpt_files
from rin.raman.web_analysis import analyze_raman_files
from rist_common.plotting.raw_spectrum import peaks_on_raw_points
from app.ftir_web import _build_ftir_raw_series
from app.raman_web import _build_raman_raw_series


@pytest.mark.parametrize("experiment", ["ftir", "raman"])
@pytest.mark.parametrize("sensitivity", [0, 25, 100])
@pytest.mark.parametrize("smooth", [False, True])
def test_raw_coordinates_preserve_baseline_noise_amplitude_and_full_range(experiment, sensitivity, smooth):
    # Nonuniform X, negative/positive baseline, narrow spike, repeated X, and
    # measurements outside the traditional 400..4000 / 0..4000 analysis grid.
    x = np.array([-5, 100, 399, 401, 650, 777, 1000, 1249, 1250,
                  1250, 1251, 1700, 2100, 2800, 3999, 4100, 4200], dtype=float)
    y = np.array([-0.22, -0.15, 0.01, 0.36, 0.31, 0.37, 0.42, 0.46,
                  4.2, 3.8, 0.45, 0.64, 0.68, 0.79, 0.88, 0.91, 0.97])
    if experiment == "raman":
        y *= 10000  # Raw Raman counts must not silently turn into a 0..1 signal.
    content = "\n".join(f"{a},{b}" for a, b in zip(x[::-1], y[::-1])).encode()
    name = "raw.dpt" if experiment == "ftir" else "raw.csv"
    analyze = analyze_dpt_files if experiment == "ftir" else analyze_raman_files
    result = analyze([(name, content), (name, content)], sensitivity=sensitivity, smooth=smooth)
    order = np.argsort(x[::-1], kind="stable")
    expected_x, expected_y = x[::-1][order].tolist(), y[::-1][order].tolist()
    parents = [t for t in result["figure"]["data"] if t.get("meta", {}).get("rist_sample_parent")]
    assert len(parents) == 2
    for trace in parents:
        assert trace["x"] == expected_x
        assert trace["y"] == expected_y
        assert trace["line"]["shape"] == "linear"
        assert trace["line"]["simplify"] is False
    assert result["settings"]["displayMode"] == "raw"
    assert result["settings"]["displayPreprocessing"] is False
    assert result["settings"]["analysisPreprocessing"]["normalize"] is True
    assert all(sample["pointCount"] == len(x) for sample in result["samples"])
    axis = result["figure"]["layout"]["xaxis"]["range"]
    assert sorted(axis) == [min(x), max(x)]
    y_range = result["figure"]["layout"]["yaxis"]["range"]
    assert y_range[0] <= min(y) and y_range[1] >= max(y)
    for trace in result["figure"]["data"]:
        peak = trace.get("meta", {}).get("rist_peak")
        if peak:
            assert (trace["x"][0], trace["y"][0]) in set(zip(x, y))
            assert peak["base_y"] == trace["y"][0]
    series_builder = _build_ftir_raw_series if experiment == "ftir" else _build_raman_raw_series
    series = series_builder([(name, content)])[0]
    assert series.axis == expected_x
    assert series.values == expected_y


@pytest.mark.parametrize("experiment", ["ftir", "raman"])
def test_sensitivity_changes_peaks_not_measurements(experiment):
    x = np.linspace(600, 3600, 601)
    y = 0.3 + 0.0001 * x + np.exp(-((x - 1500) / 50) ** 2)
    content = "\n".join(f"{a},{b}" for a, b in zip(x, y)).encode()
    analyze = analyze_dpt_files if experiment == "ftir" else analyze_raman_files
    name = "raw.dpt" if experiment == "ftir" else "raw.csv"
    low = analyze([(name, content)], sensitivity=0)
    high = analyze([(name, content)], sensitivity=100)
    assert low["figure"]["data"][0]["y"] == high["figure"]["data"][0]["y"]
    # Decimal-to-float parsing can differ by one ULP, not by signal processing.
    np.testing.assert_allclose(low["figure"]["data"][0]["y"], y, rtol=1e-15, atol=0)
    assert low["samples"][0]["peakCount"] == 0
    assert high["samples"][0]["peakCount"] > 0


def test_analysis_peaks_snap_without_duplicate_or_out_of_range_markers():
    candidates = [
        {"wn": x, "index": i, "value": 1, "fwhm": 5, "levels": [level],
         "sensitivity_min": sensitivity, "initial": initial}
        for i, (x, level, sensitivity, initial) in enumerate([
            (99, "high", 80, False), (110, "high", 80, False),
            (111, "low", 25, True), (400, "high", 80, False),
        ])
    ]
    result = peaks_on_raw_points(candidates, [100, 110, 120], [4, 8, 6], x_key="wn")
    assert len(result) == 1
    assert result[0]["wn"] == 110
    assert result[0]["raw_value"] == 8
    assert result[0]["initial"] is True
    assert result[0]["sensitivity_min"] == 25
    assert result[0]["levels"] == ["high", "low"]

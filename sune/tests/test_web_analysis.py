from __future__ import annotations

import math

import pytest

from ftir.web_analysis import DptAnalysisError, analyze_dpt_files


def synthetic_dpt(center: float = 1700.0) -> bytes:
    rows = []
    for index in range(241):
        wn = 400.0 + index * 15.0
        peak = math.exp(-((wn - center) ** 2) / (2 * 55.0**2))
        shoulder = 0.55 * math.exp(-((wn - 1250.0) ** 2) / (2 * 80.0**2))
        rows.append(f"{wn:.3f},{0.05 + peak + shoulder:.8f}")
    return ("\n".join(rows) + "\n").encode()


def test_analyze_uploaded_dpt_bytes_builds_multi_sample_figure() -> None:
    result = analyze_dpt_files(
        [
            ("sample-a.dpt", synthetic_dpt()),
            ("sample-b.dpt", synthetic_dpt(1550.0)),
        ],
        sensitivity=25,
    )

    assert len(result["samples"]) == 2
    assert all(sample["pointCount"] == 241 for sample in result["samples"])
    assert all(sample["peakCount"] >= 1 for sample in result["samples"])
    assert result["settings"]["sensitivity"] == 25
    assert result["figure"]["data"]
    assert result["figure"]["layout"]["meta"]["ristPeakLabels"]


def test_analyze_uploaded_dpt_rejects_insufficient_data() -> None:
    with pytest.raises(DptAnalysisError) as exc_info:
        analyze_dpt_files([("empty.dpt", b"400,0.1\n500,0.2\n")])

    assert exc_info.value.code == "INSUFFICIENT_DPT_DATA"

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.raman_web import build_raman_page, create_raman_preview_app
from rin.raman.preprocess import load_raman_raw


SAMPLE_TXT = (
    Path(__file__).resolve().parents[2]
    / "rin"
    / "data"
    / "LMR"
    / "LMR1.txt"
)


def test_raman_workspace_contains_upload_controls() -> None:
    page = build_raman_page()

    assert 'id="raman-file-input"' in page
    assert 'id="raman-drop-zone"' in page
    assert "/api/v1/raman/analyze" in page
    assert "/raman/assets/plotly.min.js" in page
    assert "RIN Raman" in page
    assert "rist-peak-sensitivity-control" in page
    assert "rist-legend-edit-button" in page
    assert "rist-shape-editor-panel" in page
    assert "SNAP_PX = 24" in page
    assert "scrollZoom" in page
    assert "dispatchDataReplaced" in page
    assert "rist-plot-data-replaced" in page
    assert "gd._context" in page
    assert "annotationPosition" in page
    assert "annotationTail" in page


def test_raman_raw_loader_reads_instrument_txt() -> None:
    frame = load_raman_raw(SAMPLE_TXT.name, SAMPLE_TXT.read_bytes())

    assert len(frame) > 100
    assert list(frame.columns) == ["shift", "intensity"]
    assert frame["shift"].is_monotonic_increasing


def test_raman_analyze_api_accepts_txt_sample() -> None:
    with TestClient(create_raman_preview_app()) as client:
        response = client.post(
            "/api/v1/raman/analyze",
            files={
                "files": (
                    SAMPLE_TXT.name,
                    SAMPLE_TXT.read_bytes(),
                    "text/plain",
                )
            },
            data={"sensitivity": "25"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["samples"][0]["fileName"] == SAMPLE_TXT.name
    assert payload["samples"][0]["pointCount"] > 100
    assert payload["samples"][0]["peakCount"] >= 1
    assert payload["settings"]["sensitivity"] == 25
    assert payload["figure"]["data"]
    assert any(
        trace.get("meta", {}).get("rist_peak")
        for trace in payload["figure"]["data"]
    )


def test_raman_analyze_api_rejects_unknown_extension() -> None:
    with TestClient(create_raman_preview_app()) as client:
        response = client.post(
            "/api/v1/raman/analyze",
            files={"files": ("sample.bin", b"1 2\n3 4\n", "application/octet-stream")},
        )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_RAMAN_EXTENSION"

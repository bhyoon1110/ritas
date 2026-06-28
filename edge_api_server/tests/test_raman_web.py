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
    assert 'id="raman-file-list"' in page
    assert 'id="raman-drop-zone"' in page
    assert 'id="raman-drop-prompt"' in page
    assert 'id="raman-library-list"' in page
    assert 'id="raman-library-filter"' in page
    assert 'id="raman-library-new"' in page
    assert 'id="raman-library-modal"' in page
    assert "/api/v1/raman/analyze" in page
    assert "/api/v1/raman/assignment-libraries" in page
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
    assert "raman-file-remove" in page
    assert "raman-library-row-remove" in page
    assert "assignment_library_ids" in page
    assert "assignment_library_selection_explicit" in page
    assert "raman-drop-zone" in page
    assert "Raman raw 파일을 선택하거나 여기에 놓으세요" in page
    assert "prompt.style.display = files.length ? \"none\" : \"inline\"" in page
    assert "files.splice(index, 1)" in page
    assert "else resetPlot()" in page


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


def test_raman_assignment_library_api_defaults_and_assigns_sample() -> None:
    with TestClient(create_raman_preview_app()) as client:
        libraries_response = client.get("/api/v1/raman/assignment-libraries")
        assert libraries_response.status_code == 200
        libraries = libraries_response.json()["libraries"]
        assert libraries[0]["id"] == "general-raman"
        assert libraries[0]["defaultSelected"] is True

        response = client.post(
            "/api/v1/raman/analyze",
            files={
                "files": (
                    SAMPLE_TXT.name,
                    SAMPLE_TXT.read_bytes(),
                    "text/plain",
                )
            },
            data={
                "sensitivity": "25",
                "assignment_library_ids": ["general-raman"],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["settings"]["assignmentLibraries"][0]["id"] == "general-raman"
    peak_names = [
        trace.get("name", "")
        for trace in payload["figure"]["data"]
        if trace.get("meta", {}).get("rist_peak")
    ]
    assert any("D band" in name or "G band" in name for name in peak_names)


def test_raman_analyze_api_rejects_unknown_extension() -> None:
    with TestClient(create_raman_preview_app()) as client:
        response = client.post(
            "/api/v1/raman/analyze",
            files={"files": ("sample.bin", b"1 2\n3 4\n", "application/octet-stream")},
        )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_RAMAN_EXTENSION"

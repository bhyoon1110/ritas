from __future__ import annotations

import math

from fastapi.testclient import TestClient

from app.ftir_web import (
    build_ftir_page,
    create_ftir_preview_app,
    plotly_asset_path,
)


def synthetic_dpt(center: float = 1700.0) -> bytes:
    rows = []
    for index in range(241):
        wn = 400.0 + index * 15.0
        peak = math.exp(-((wn - center) ** 2) / (2 * 55.0**2))
        shoulder = 0.55 * math.exp(-((wn - 1250.0) ** 2) / (2 * 80.0**2))
        rows.append(f"{wn:.3f},{0.05 + peak + shoulder:.8f}")
    return ("\n".join(rows) + "\n").encode()


def test_ftir_workspace_contains_upload_and_editor_controls() -> None:
    page = build_ftir_page()

    assert 'id="ftir-file-input"' in page
    assert 'id="ftir-drop-zone"' in page
    assert "/api/v1/ftir/analyze" in page
    assert "/ftir/assets/plotly.min.js" in page
    assert "rist-shape-tool-button" in page
    assert "rist-peak-sensitivity-control" in page
    assert "rist-plot-data-replaced" in page
    assert plotly_asset_path().is_file()


def test_ftir_analysis_api_accepts_multiple_dpt_files() -> None:
    with TestClient(create_ftir_preview_app()) as client:
        response = client.post(
            "/api/v1/ftir/analyze",
            files=[
                ("files", ("sample-a.dpt", synthetic_dpt(), "application/octet-stream")),
                (
                    "files",
                    ("sample-b.DPT", synthetic_dpt(1550.0), "application/octet-stream"),
                ),
            ],
            data={"sensitivity": "25"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert [sample["fileName"] for sample in payload["samples"]] == [
        "sample-a.dpt",
        "sample-b.DPT",
    ]
    assert payload["settings"]["sensitivity"] == 25
    assert payload["figure"]["data"]


def test_ftir_analysis_api_rejects_non_dpt() -> None:
    with TestClient(create_ftir_preview_app()) as client:
        response = client.post(
            "/api/v1/ftir/analyze",
            files={"files": ("sample.csv", synthetic_dpt(), "text/csv")},
            data={"sensitivity": "25"},
        )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_DPT_EXTENSION"

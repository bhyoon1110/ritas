from __future__ import annotations

import json
import math
from pathlib import Path

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
    assert 'id="ftir-library-list"' in page
    assert 'id="ftir-library-filter"' in page
    assert 'id="ftir-library-input"' in page
    assert 'id="ftir-library-new"' in page
    assert 'id="ftir-library-modal"' in page
    assert 'id="ftir-library-dialog-save"' in page
    assert "ftir-library-state" in page
    assert "rightSelected - leftSelected" in page
    assert "검색 결과가 없습니다" in page
    assert "ftir-library-delete" not in page
    assert "/api/v1/ftir/analyze" in page
    assert "/api/v1/ftir/assignment-libraries" in page
    assert "/ftir/assets/plotly.min.js" in page
    assert "rist-shape-tool-button" in page
    assert "rist-peak-sensitivity-control" in page
    assert "rist-ftir-tools-toggle" in page
    assert "rist-ftir-tools-open" in page
    assert "rist-ftir-tools-head" in page
    assert "rist-ftir-tools-opacity" in page
    assert "setToolPanelAlphaFromPointer" in page
    assert "--rist-ftir-tool-panel-alpha" in page
    assert "@media (max-width: 1440px)" in page
    assert "right: 8px !important" in page
    assert "width: min(860px, calc(100% - 24px)) !important" in page
    assert "max-width: calc(100% - 24px)" in page
    assert "rist-plot-data-replaced" in page
    assert "height: calc(100vh - 180px + 360px) !important" in page
    assert "min-height: 900px" in page
    assert '"height": 900' in page
    assert '"margin.t": 145' in page
    assert '"margin.b": 150' in page
    assert '"legend.y": -0.30' in page
    assert plotly_asset_path().is_file()


def test_ftir_analysis_api_accepts_multiple_dpt_files(tmp_path: Path) -> None:
    with TestClient(create_ftir_preview_app(tmp_path / "libraries")) as client:
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
    assert payload["settings"]["assignmentLibraries"][0]["id"] == "general-ftir"
    assert payload["figure"]["data"]


def test_ftir_analysis_api_rejects_non_dpt(tmp_path: Path) -> None:
    with TestClient(create_ftir_preview_app(tmp_path / "libraries")) as client:
        response = client.post(
            "/api/v1/ftir/analyze",
            files={"files": ("sample.csv", synthetic_dpt(), "text/csv")},
            data={"sensitivity": "25"},
        )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_DPT_EXTENSION"


def test_explicit_empty_library_selection_disables_assignment(
    tmp_path: Path,
) -> None:
    with TestClient(create_ftir_preview_app(tmp_path / "libraries")) as client:
        response = client.post(
            "/api/v1/ftir/analyze",
            files={
                "files": (
                    "sample.dpt",
                    synthetic_dpt(1700),
                    "application/octet-stream",
                )
            },
            data={
                "sensitivity": "25",
                "assignment_library_selection_explicit": "true",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["settings"]["assignmentLibraries"] == []
    peak_names = [
        trace.get("name", "")
        for trace in payload["figure"]["data"]
        if trace.get("meta", {}).get("rist_peak")
    ]
    assert peak_names
    assert all(name.endswith("cm⁻¹") for name in peak_names)


def assignment_library(name: str, peak_name: str) -> bytes:
    return json.dumps({
        "name": name,
        "assignments": [{
            "centerWavenumber": 1700,
            "tolerance": 40,
            "name": peak_name,
            "color": "#2563eb",
            "note": "test",
        }],
    }).encode()


def test_assignment_library_api_upload_select_and_edit(tmp_path: Path) -> None:
    with TestClient(create_ftir_preview_app(tmp_path / "libraries")) as client:
        initial = client.get("/api/v1/ftir/assignment-libraries")
        assert initial.status_code == 200
        assert initial.json()["deleteEnabled"] is False
        initial_ids = {item["id"] for item in initial.json()["libraries"]}
        assert "general-ftir" in initial_ids
        assert "engineering-plastic-commodity-peaks" in initial_ids
        detail = client.get(
            "/api/v1/ftir/assignment-libraries/general-ftir"
        )
        assert detail.status_code == 200
        assert detail.json()["library"]["assignmentCount"] == 47
        assert detail.json()["library"]["assignments"][0]["name"]

        created = client.post(
            "/api/v1/ftir/assignment-libraries/create",
            json={
                "id": "editor-test",
                "name": "Editor Test",
                "description": "created in editor",
                "assignments": [{
                    "centerWavenumber": 1700,
                    "tolerance": 20,
                    "name": "C=O test",
                    "color": "#123456",
                    "note": "first",
                }],
            },
        )
        assert created.status_code == 201
        updated = client.put(
            "/api/v1/ftir/assignment-libraries/editor-test",
            json={
                "name": "Editor Test Updated",
                "description": "updated in editor",
                "assignments": [
                    {
                        "centerWavenumber": 1710,
                        "tolerance": 15,
                        "name": "C=O updated",
                        "color": "#654321",
                        "note": "updated",
                    },
                    {
                        "centerWavenumber": 1250,
                        "tolerance": 25,
                        "name": "C-O test",
                        "color": "#2563eb",
                        "note": "",
                    },
                ],
            },
        )
        assert updated.status_code == 200
        assert updated.json()["library"]["name"] == "Editor Test Updated"
        assert updated.json()["library"]["assignmentCount"] == 2
        assert (tmp_path / "libraries" / "editor-test.json").is_file()
        disabled_delete = client.delete(
            "/api/v1/ftir/assignment-libraries/editor-test"
        )
        assert disabled_delete.status_code == 403
        assert disabled_delete.json()["code"] == "ASSIGNMENT_LIBRARY_DELETE_DISABLED"
        assert (tmp_path / "libraries" / "editor-test.json").is_file()

        for file_id, name, peak_name in (
            ("material-a", "Material A", "Carbonyl A"),
            ("material-b", "Material B", "Carbonyl B"),
        ):
            uploaded = client.post(
                "/api/v1/ftir/assignment-libraries",
                files={
                    "file": (
                        f"{file_id}.json",
                        assignment_library(name, peak_name),
                        "application/json",
                    )
                },
            )
            assert uploaded.status_code == 201

        response = client.post(
            "/api/v1/ftir/analyze",
            files={
                "files": (
                    "sample.dpt",
                    synthetic_dpt(1700),
                    "application/octet-stream",
                )
            },
            data={
                "sensitivity": "25",
                "assignment_library_ids": ["material-a", "material-b"],
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert [
            item["id"] for item in payload["settings"]["assignmentLibraries"]
        ] == ["material-a", "material-b"]
        peak_names = [
            trace.get("name", "")
            for trace in payload["figure"]["data"]
            if trace.get("meta", {}).get("rist_peak")
        ]
        assert any("Carbonyl A<br>Carbonyl B" in name for name in peak_names)


def test_assignment_library_delete_requires_feature_flag(tmp_path: Path) -> None:
    with TestClient(
        create_ftir_preview_app(
            tmp_path / "libraries",
            assignment_library_delete_enabled=True,
        )
    ) as client:
        created = client.post(
            "/api/v1/ftir/assignment-libraries/create",
            json={
                "id": "delete-test",
                "name": "Delete Test",
                "description": "",
                "assignments": [{
                    "centerWavenumber": 1700,
                    "tolerance": 20,
                    "name": "C=O test",
                    "color": "#123456",
                    "note": "",
                }],
            },
        )
        assert created.status_code == 201
        initial = client.get("/api/v1/ftir/assignment-libraries")
        assert initial.status_code == 200
        assert initial.json()["deleteEnabled"] is True
        assert (tmp_path / "libraries" / "delete-test.json").is_file()

        deleted = client.delete("/api/v1/ftir/assignment-libraries/delete-test")

    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "id": "delete-test"}
    assert not (tmp_path / "libraries" / "delete-test.json").exists()

from __future__ import annotations

import json
import math
import time
from io import BytesIO
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app import assignment_suggestions, preview_report
from app.ftir_web import (
    build_ftir_page,
    create_ftir_preview_app,
    plotly_asset_path,
)


TINY_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6"
    "kgAAAABJRU5ErkJggg=="
)


def use_fake_pptx_pdf_converter(monkeypatch) -> None:
    def fake_convert(pptx_path: Path, pdf_path: Path) -> Path:
        assert pptx_path.name == "report.pptx"
        assert pdf_path.name == "report-from-pptx.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n% RIST test PDF\n")
        return pdf_path

    monkeypatch.setattr(preview_report, "convert_pptx_to_pdf", fake_convert)


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
    assert '<button type="button" class="ftir-clear-button" id="ftir-clear">초기화</button>' in page
    assert 'id="ftir-report"' in page
    assert page.index('id="ftir-report"') < page.index('id="ftir-clear"')
    assert page.index('id="ftir-clear"') < page.index('id="ftir-file-input"')
    assert "/api/v1/ftir/report/jobs" in page
    assert 'id="ftir-report-progress"' in page
    assert 'id="ftir-report-meta"' in page
    assert "reportAnalysisPayload" in page
    assert "populateReportMetadataFromPayload" in page
    assert "setReportControlIfEmpty" in page
    assert "experimentConditions" in page
    assert "raw 자동 추출 + 직접 입력" in page
    assert 'id="ftir-report-options-open"' in page
    assert 'id="ftir-report-options-modal"' in page
    assert 'id="ftir-report-options-save"' in page
    assert 'id="ftir-report-options-reset"' in page
    assert "rist-ftir-report-condition-options-v1" in page
    assert "renderReportDatalists" in page
    assert "openReportOptionsEditor" in page
    assert "saveReportOptionsEditor" in page
    assert "resetReportOptionsEditor" in page
    assert "installReportOptionPickers" in page
    assert "openReportOptionPicker" in page
    assert "closeReportOptionPicker" in page
    assert "ftir-report-picker-button" in page
    assert "ftir-report-picker-menu" in page
    assert 'button.textContent = "▼"' in page
    assert 'control.dispatchEvent(new Event("change", {bubbles: true}))' in page
    assert "#ftir-report-options-modal .ftir-library-dialog" in page
    assert "height: min(660px, calc(100dvh - 32px))" in page
    assert "max-height: calc(100dvh - 16px)" in page
    assert "min-height: 0" in page
    assert 'data-report-field="equipmentModel"' in page
    assert 'data-report-label="장비모델"' in page
    assert 'data-report-field="analysisType"' in page
    assert 'list="ftir-report-type-options"' in page
    assert '<input type="text" value="ATR method"' not in page
    assert '<option value="ATR method">' in page
    assert '<option value="Diffuse reflectance">' in page
    assert '<option value="KBr pellet">' in page
    assert 'data-report-field="detector"' in page
    assert 'list="ftir-report-detector-options"' in page
    assert '<input type="text" value="DTGS"' not in page
    assert '<option value="DTGS">' in page
    assert '<option value="MCT">' in page
    assert '<option value="Photoacoustic detector">' in page
    assert 'data-report-field="crystal"' in page
    assert 'list="ftir-report-crystal-options"' in page
    assert '<input type="text" value="diamond"' not in page
    assert '<option value="diamond">' in page
    assert '<option value="KRS-5">' in page
    assert '<option value="AMTIR">' in page
    assert 'data-report-field="resolution"' in page
    assert 'list="ftir-report-resolution-options"' in page
    assert '<input type="text" value="4 cm-1"' not in page
    assert '<option value="4 cm-1">' in page
    assert '<option value="1 cm-1">' in page
    assert '<option value="16 cm-1">' in page
    assert 'data-report-field="scanTime"' in page
    assert 'list="ftir-report-scan-options"' in page
    assert '<input type="text" value="64 scans"' not in page
    assert '<option value="64 scans">' in page
    assert '<option value="256 scans">' in page
    assert 'data-report-field="range"' in page
    assert 'list="ftir-report-range-options"' in page
    assert '<input type="text" value="4000 ~ 400 cm-1"' not in page
    assert '<option value="4000 ~ 400 cm-1">' in page
    assert '<option value="7800 ~ 350 cm-1">' in page
    assert "pollReportJob" in page
    assert "setReportDownloadLink" in page
    assert "MESSAGE_AUTO_HIDE_MS = 5000" in page
    assert "clearMessageTimer" in page
    assert "ftir-message-close" in page
    assert ".ftir-loading {" in page
    assert "z-index: 200" in page
    assert "background: rgba(248,250,252,0.76)" in page
    assert "Plotly.toImage" in page
    assert "clearButton.hidden = false" in page
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
    assert "/api/v1/ftir/assignment-libraries/suggest" in page
    assert "LLM 추천 채우기" in page
    assert "ftir-library-suggest" in page
    assert "/ftir/assets/plotly.min.js" in page
    assert "rist-shape-tool-button" in page
    assert "rist-peak-sensitivity-control" in page
    assert "rist-ftir-tools-toggle" in page
    assert "rist-ftir-tools-open" in page
    assert "rist-ftir-tools-head" in page
    assert "rist-ftir-tools-opacity" in page
    assert "setToolPanelAlphaFromPointer" in page
    assert 'gd.dispatchEvent(new CustomEvent("rist-open-edit-tool"))' in page
    assert 'new CustomEvent("rist-ftir-tools-toggle"' in page
    assert "--rist-ftir-tool-panel-alpha" in page
    assert "@media (max-width: 1440px)" in page
    assert "z-index: 56" in page
    assert "z-index: 55" in page
    assert "right: 8px !important" in page
    assert "width: min(860px, calc(100% - 24px)) !important" in page
    assert "max-width: calc(100% - 24px)" in page
    assert "justify-content: flex-end" in page
    assert "text-align: right" in page
    assert 'var title = gd.querySelector(".gtitle")' in page
    assert "var titleBottom = title ? title.getBoundingClientRect().bottom - plotRect.top + 8 : 0" in page
    assert "var minTop = Math.max(window.innerWidth <= 420 ? 76 : 70, titleBottom)" in page
    assert "top: clamp(top, minTop, Math.max(minTop, plotRect.height - height - 8))" in page
    assert "rist-plot-data-replaced" in page
    assert "rist-ftir-workspace-v1" in page
    assert "indexedDB.open(SESSION_DB_NAME, 1)" in page
    assert "restoreWorkspace()" in page
    assert "installWorkspaceAutosave()" in page
    assert "clearWorkspaceState()" in page
    assert "plotData: JSON.parse(JSON.stringify(gd.data || []))" in page
    assert "files = (state.files || []).map(recordFile)" in page
    assert "function freshEmptyData" in page
    assert "function freshEmptyLayout" in page
    assert "Plotly.react(gd, freshEmptyData(), freshEmptyLayout(), gd._context)" in page
    assert "height: calc(100vh - 180px + 360px) !important" in page
    assert "min-height: 900px" in page
    assert "toolsOpen" not in page
    assert '"height": 900' in page
    assert '"margin.t": 82' in page
    assert '"margin.b": 150' in page
    assert '"legend.y": -0.30' in page
    assert 'gd.addEventListener("rist-ftir-tools-toggle"' in page
    assert "if (restored) return applyResponsiveLayout()" in page
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


def test_ftir_analysis_extracts_optional_dpt_metadata(tmp_path: Path) -> None:
    content = (
        b"# Resolution: 4 cm-1\n"
        b"# Measurement Mode: ATR\n"
        + synthetic_dpt()
    )
    with TestClient(create_ftir_preview_app(tmp_path / "libraries")) as client:
        response = client.post(
            "/api/v1/ftir/analyze",
            files={"files": ("sample-a.dpt", content, "application/octet-stream")},
            data={"sensitivity": "25"},
        )

    assert response.status_code == 200
    sample = response.json()["samples"][0]
    assert sample["metadata"]["Resolution"] == "4 cm-1"
    assert sample["metadata"]["Measurement Mode"] == "ATR"


def test_ftir_report_api_builds_package_with_graph_and_raw_xlsx(
    tmp_path: Path,
    monkeypatch,
) -> None:
    use_fake_pptx_pdf_converter(monkeypatch)
    with TestClient(create_ftir_preview_app(tmp_path / "libraries")) as client:
        analysis_response = client.post(
            "/api/v1/ftir/analyze",
            files={"files": ("sample-a.dpt", synthetic_dpt(), "application/octet-stream")},
            data={"sensitivity": "25"},
        )
        assert analysis_response.status_code == 200
        analysis = analysis_response.json()
        report_response = client.post(
            "/api/v1/ftir/report",
            files={"files": ("sample-a.dpt", synthetic_dpt(), "application/octet-stream")},
            data={
                "analysis_json": json.dumps(analysis),
                "figure_json": json.dumps(analysis["figure"]),
                "figure_image": TINY_PNG_DATA_URL,
            },
        )

    assert report_response.status_code == 200
    with zipfile.ZipFile(BytesIO(report_response.content)) as archive:
        names = set(archive.namelist())
        assert {
            "report.pptx",
            "report.pdf",
            "report-from-pptx.pdf",
            "report.html",
            "email_body.md",
            "raw_data.xlsx",
            "current_graph.png",
        } <= names
        assert "report.json" not in names
        html_report = archive.read("report.html").decode("utf-8")
        ppt_text = "\n".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.startswith("ppt/slides/slide") and name.endswith(".xml")
        )
        assert "의뢰번호" in html_report
        assert "분석 결과 요약" in html_report
        assert "현재 그래프 표시 피크" in html_report
        assert "요청번호" not in html_report
        assert "작업자" not in html_report
        assert "WEB-PREVIEW" not in html_report
        assert "web-preview" not in html_report
        assert "LLM 보조 설명" not in html_report
        assert "LLM 사용" not in html_report
        assert "요청번호" not in ppt_text
        assert "작업자" not in ppt_text
        assert "WEB-PREVIEW" not in ppt_text
        assert "web-preview" not in ppt_text
        assert "LLM 보조 설명" not in ppt_text
        assert "LLM 사용" not in ppt_text
        assert "<a:t>LLM</a:t>" not in ppt_text


def test_ftir_report_job_api_tracks_progress_and_downloads_package(
    tmp_path: Path,
    monkeypatch,
) -> None:
    use_fake_pptx_pdf_converter(monkeypatch)
    with TestClient(create_ftir_preview_app(tmp_path / "libraries")) as client:
        analysis_response = client.post(
            "/api/v1/ftir/analyze",
            files={"files": ("sample-a.dpt", synthetic_dpt(), "application/octet-stream")},
            data={"sensitivity": "25"},
        )
        assert analysis_response.status_code == 200
        analysis = analysis_response.json()
        job_response = client.post(
            "/api/v1/ftir/report/jobs",
            files={"files": ("sample-a.dpt", synthetic_dpt(), "application/octet-stream")},
            data={
                "analysis_json": json.dumps(analysis),
                "figure_json": json.dumps(analysis["figure"]),
                "figure_image": TINY_PNG_DATA_URL,
            },
        )
        assert job_response.status_code == 202
        job = job_response.json()
        assert job["status"] in {"queued", "running", "completed"}
        assert job["progressPct"] >= 0

        for _ in range(100):
            status_response = client.get(f"/api/v1/ftir/report/jobs/{job['jobId']}")
            assert status_response.status_code == 200
            job = status_response.json()
            if job["status"] == "completed":
                break
            time.sleep(0.03)

        assert job["status"] == "completed"
        assert job["progressPct"] == 100
        assert job["downloadUrl"].endswith(f"/{job['jobId']}/download")
        download_response = client.get(job["downloadUrl"])

    assert download_response.status_code == 200
    with zipfile.ZipFile(BytesIO(download_response.content)) as archive:
        names = set(archive.namelist())
    assert {
        "report.pptx",
        "report.pdf",
        "report-from-pptx.pdf",
        "report.html",
        "raw_data.xlsx",
        "current_graph.png",
    } <= names


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


def test_assignment_library_suggest_api_returns_draft(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured = {}

    def fake_suggest(settings, request):
        captured["request"] = request
        return {
            "library": {
                "id": "ethanol-ftir",
                "name": "Ethanol FT-IR",
                "description": "LLM draft",
                "fileName": "ethanol-ftir.json",
                "assignmentCount": 1,
                "defaultSelected": False,
                "valid": True,
                "error": "",
                "assignments": [{
                    "centerWavenumber": 1050,
                    "tolerance": 30,
                    "name": "C-O stretch",
                    "color": "#2563eb",
                    "note": "draft",
                }],
            },
            "warning": "검토 필요",
        }

    monkeypatch.setattr(
        assignment_suggestions,
        "suggest_assignment_library",
        fake_suggest,
    )
    with TestClient(create_ftir_preview_app(tmp_path / "libraries")) as client:
        response = client.post(
            "/api/v1/ftir/assignment-libraries/suggest",
            json={"material": "ethanol"},
        )

    assert response.status_code == 200
    assert captured["request"].experiment_code == "FT-IR"
    assert captured["request"].material == "ethanol"
    payload = response.json()
    assert payload["warning"] == "검토 필요"
    assert payload["library"]["assignments"][0]["name"] == "C-O stretch"


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

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
import time
import zipfile

from fastapi.testclient import TestClient

from app import assignment_suggestions, preview_report
from app.raman_web import _blank_figure, build_raman_page, create_raman_preview_app
from rin.raman.preprocess import load_raman_raw, load_raman_raw_samples

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


SAMPLE_TXT = (
    Path(__file__).resolve().parents[2]
    / "rin"
    / "data"
    / "LMR"
    / "LMR1.txt"
)
MULTI_SAMPLE_TXT = (
    Path(__file__).resolve().parents[2]
    / "rin"
    / "data"
    / "대기민감성 샘플"
    / "LiOH.txt"
)
LARGE_LMR_TXT = (
    Path(__file__).resolve().parents[2]
    / "rin"
    / "data"
    / "LMR"
    / "LMR 6종_2.txt"
)


def compact_report_figure(figure: dict) -> dict:
    payload = json.loads(json.dumps(figure))
    for trace in payload.get("data", []):
        for key in ("x", "y", "z", "customdata", "text", "hovertext"):
            trace.pop(key, None)
    return payload


def test_raman_workspace_contains_upload_controls() -> None:
    page = build_raman_page()

    assert 'id="raman-file-input"' in page
    assert 'id="raman-admin-link" href="/operations" hidden>운영 관리</a>' in page
    assert 'id="raman-logout" type="button" hidden>로그아웃</button>' in page
    assert 'fetch("/api/v1/auth/me"' in page
    assert 'fetch("/api/v1/auth/logout"' in page
    assert 'roles.indexOf("ADMIN") === -1' in page
    assert 'id="raman-origin" checked' in page
    assert "Origin 스타일" in page
    assert "gd._ristOriginStyle" in page
    assert "originStyle: originStyleEnabled()" in page
    assert "withOriginStyle(payload.figure.layout || {})" in page
    assert 'CustomEvent("rist-origin-style-change"' in page
    assert "lucide-sliders-horizontal" in page
    assert 'button.textContent = open ? "닫기" : "도구"' not in page
    assert 'id="raman-report"' in page
    assert page.index('id="raman-report"') < page.index('id="raman-clear"')
    assert page.index('id="raman-clear"') < page.index('id="raman-file-input"')
    assert "/api/v1/raman/report/jobs" in page
    assert "/api/v1/raman/report/upload-sessions" in page
    assert "uploadReportFilesWithSession" in page
    assert "REPORT_UPLOAD_CHUNK_RETRIES" in page
    assert "/api/v1/raman/report/jobs/" in page
    assert "/send" in page
    assert 'id="raman-report-transfer"' in page
    assert 'id="raman-request-load"' in page
    assert 'id="raman-request-select"' in page
    assert 'id="raman-request-detail"' in page
    assert 'id="raman-report-download" href="#" aria-disabled="true"' in page
    assert "보고서 생성이 완료되면 다운로드할 수 있습니다." in page
    assert 'id="raman-report-send" disabled' in page
    assert 'data-transfer-field="requestNumber"' in page
    assert 'data-transfer-field="limsExperimentCode"' in page
    assert 'data-transfer-field="equipmentCode"' in page
    assert 'value="RAMAN-01"' not in page
    assert "의뢰 선택 시 자동 입력" in page
    assert 'var DEFAULT_EQUIPMENT_CODE = "RAMAN-EDGE-01";' in page
    assert "applyRequestEquipmentCode(item)" in page
    assert 'data-transfer-field="operatorId"' in page
    assert "loadRequestItems" in page
    assert "experimentType=" in page
    assert "encodeURIComponent(REQUEST_EXPERIMENT_TYPE)" in page
    assert 'var REQUEST_EXPERIMENT_TYPE = "RAMAN";' in page
    assert "renderRequestDetail" in page
    assert "updateReportSendAvailability" in page
    assert "updatePersistentReportDownload" in page
    assert "reportDownloadInfo" in page
    assert 'X-Request-Id": "raman-request-list-' in page
    assert 'X-Request-Id": "raman-request-list-all-' not in page
    assert "조회된 Raman 의뢰가 없습니다." in page
    assert "validateReportTransfer" in page
    assert "sendReportJob" in page
    assert "message.appendChild(link)" not in page
    assert "message.appendChild(send)" not in page
    assert "sendReportJob(job, send)" not in page
    assert "requestNumber: transfer.requestNumber" in page
    assert 'experimentCode: transfer.limsExperimentCode' in page
    assert 'id="raman-report-progress"' in page
    assert ".raman-report-progress.is-error" in page
    assert "reportProgressHideTimer" in page
    assert 'setTimeout(function()' in page
    assert 'id="raman-report-meta"' in page
    assert 'id="raman-report-apply-common-conditions"' in page
    assert 'id="raman-report-sample-condition-list"' in page
    assert 'id="raman-report-condition-add"' not in page
    assert 'id="raman-report-extra-list"' not in page
    assert "reportAnalysisPayload" in page
    assert "delete payload.figure" in page
    assert "compactReportFigurePayload" in page
    assert "populateReportMetadataFromPayload" in page
    assert "reportSampleConditionsState" in page
    assert "renderReportSampleConditionSets" in page
    assert "applyCommonConditionsToSamples" in page
    assert "applyCommonConditionFieldToSamples" in page
    assert 'data-report-apply-field="excitationWavelength"' in page
    assert 'data-report-apply-field="conditionDetail"' in page
    assert "샘플 적용" in page
    assert "conditionRowsFromObject" in page
    assert "visibleSampleNames" in page
    assert "normalizedLaserValue" in page
    assert "setReportControlIfEmpty" in page
    assert "공통값을 모든 샘플에 적용" in page
    assert "sampleConditions" in page
    assert "experimentConditions" in page
    assert "existingConditions.concat(conditions)" in page
    assert "실험환경/조건 <span>raw 헤더 자동 추출 + 직접 입력</span>" in page
    assert "raw 헤더 자동 추출 + 직접 입력" in page
    assert "보고서 정보" not in page
    assert 'data-report-field="measurementDate"' not in page
    assert 'data-report-label="측정일"' not in page
    assert 'data-report-field="requester"' not in page
    assert 'data-report-label="의뢰자"' not in page
    assert 'id="raman-report-options-open"' in page
    assert 'id="raman-report-options-modal"' in page
    assert 'id="raman-report-options-save"' in page
    assert 'id="raman-report-options-reset"' in page
    assert "rist-raman-report-condition-options-v2" in page
    assert "renderReportDatalists" in page
    assert "openReportOptionsEditor" in page
    assert "saveReportOptionsEditor" in page
    assert "resetReportOptionsEditor" in page
    assert "installReportOptionPickers" in page
    assert "openReportOptionPicker" in page
    assert "closeReportOptionPicker" in page
    assert "raman-report-picker-button" in page
    assert "raman-report-picker-menu" in page
    assert 'button.textContent = "▼"' in page
    assert 'control.dispatchEvent(new Event("change", {bubbles: true}))' in page
    assert "#raman-report-options-modal .raman-library-dialog" in page
    assert "height: min(660px, calc(100dvh - 32px))" in page
    assert "max-height: calc(100dvh - 16px)" in page
    assert 'data-report-field="excitationWavelength"' in page
    assert 'list="raman-report-excitation-wavelength-options"' in page
    assert "<select data-report-field=\"excitationWavelength\"" not in page
    assert '<option value="532 nm">' in page
    assert '<option value="785 nm">' in page
    assert 'data-report-field="laserCurrent"' in page
    assert 'list="raman-report-laser-current-options"' in page
    assert '<option value="10 mA">' in page
    assert 'data-report-field="excitationPower"' in page
    assert 'list="raman-report-excitation-power-options"' in page
    assert '<option value="1 mW">' in page
    assert 'data-report-field="excitationPowerDensity"' in page
    assert 'list="raman-report-power-density-options"' in page
    assert 'data-report-field="ndFilter"' in page
    assert 'list="raman-report-nd-filter-options"' in page
    assert 'data-report-field="spectrographCenterWavelength"' in page
    assert 'list="raman-report-center-wavelength-options"' in page
    assert 'data-report-field="grating"' in page
    assert 'list="raman-report-grating-options"' in page
    assert '<option value="1800 g/mm">' in page
    assert 'data-report-field="slitWidth"' in page
    assert 'list="raman-report-slit-width-options"' in page
    assert 'data-report-label="기타"' in page
    assert "pollReportJob" in page
    assert "setReportDownloadLink" in page
    assert "MESSAGE_AUTO_HIDE_MS = 5000" in page
    assert "clearMessageTimer" in page
    assert ".raman-message-item" in page
    assert "updateMessageStackVisibility" in page
    assert "removeMessageItem" in page
    assert "message.appendChild(item)" in page
    assert "raman-message-close" in page
    assert ".raman-loading {" in page
    assert "z-index: 200" in page
    assert "background: rgba(248,250,252,0.76)" in page
    assert "Plotly.toImage" in page
    assert 'id="raman-file-list"' in page
    assert 'id="raman-drop-zone"' in page
    assert 'id="raman-drop-prompt"' in page
    assert '<button class="raman-clear-button" id="raman-clear" type="button">초기화</button>' in page
    assert "clearButton.hidden = false" in page
    assert 'id="raman-library-list"' in page
    assert 'id="raman-library-filter"' in page
    assert 'id="raman-library-new"' in page
    assert 'id="raman-library-modal"' in page
    assert "/api/v1/raman/analyze" in page
    assert "/api/v1/raman/assignment-libraries" in page
    assert "/api/v1/raman/assignment-libraries/suggest" in page
    assert "LLM 추천 채우기" in page
    assert "raman-library-suggest" in page
    assert "/raman/assets/plotly.min.js" in page
    assert "RIN Raman" in page
    assert "rist-peak-sensitivity-control" in page
    assert "rist-raman-stack-control" in page
    assert "rist-raman-ratio-control" in page
    assert "ristRamanStack" in page
    assert "function visibleStackGroups" in page
    assert "function compactVisibleStackOffsets" in page
    assert "function scheduleStackCompaction" in page
    assert 'gd.addEventListener("rist-legend-visibility-change", scheduleStackCompaction)' in page
    assert 'gd.on("plotly_restyle", function()' in page
    assert "I(num)/I(den)" in page
    assert "분자 선택" in page
    assert "분모 선택" in page
    assert "Y 이동" in page
    assert "rist-raman-tools-toggle" in page
    assert "rist-raman-tools-open" in page
    assert "rist-raman-tools-head" in page
    assert "rist-raman-tools-opacity" in page
    assert "setToolPanelAlphaFromPointer" in page
    assert "setPanelPosition" in page
    assert 'gd.dispatchEvent(new CustomEvent("rist-open-edit-tool"))' in page
    assert 'new CustomEvent("rist-raman-tools-toggle"' in page
    assert "setOpen(!gd.classList.contains" in page
    assert "gd._ristRamanRatioMode && gd.contains(ev.target)" in page
    assert "gd._ristRamanRatioMode = ratioMode" in page
    assert "ratioMode = false" in page
    assert "max-width: calc(100% - 16px)" in page
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
    assert "@media (max-width: 760px)" in page
    assert "@media (max-width: 1440px)" in page
    assert "@media (max-width: 420px)" in page
    assert "var compact = window.innerWidth <= 760" in page
    assert "toolsOpen" not in page
    assert '"height": 900' in page
    assert '"margin.t": 82' in page
    assert '"margin.b": 150' in page
    assert '"legend.y": -0.30' in page
    assert '"margin.t": 90' in page
    assert "height: calc(100vh - 248px + 360px) !important" in page
    assert "min-height: 900px" in page
    assert "rist-legend-edit-button" in page
    assert "rist-shape-editor-panel" in page
    assert "SNAP_PX = 24" in page
    assert "scrollZoom" in page
    assert "dispatchDataReplaced" in page
    assert "rist-plot-data-replaced" in page
    assert "rist-raman-workspace-v1" in page
    assert "indexedDB.open(SESSION_DB_NAME, 1)" in page
    assert "restoreWorkspace()" in page
    assert "installWorkspaceAutosave()" in page
    assert 'gd.addEventListener("rist-raman-tools-toggle"' in page
    assert "if (restored) return applyResponsiveLayout()" in page
    assert "clearWorkspaceState()" in page
    assert "plotData: JSON.parse(JSON.stringify(gd.data || []))" in page
    assert "files = (state.files || []).map(recordFile)" in page
    assert "function filterReportAnalysisPayload" in page
    assert "function filteredReportFigurePayload" in page
    assert "function reportFilesForVisibleSamples" in page
    assert "async function captureReportFigureImage" in page
    assert "var reportFigure = currentFigurePayload()" in page
    assert "var reportFiles = reportFilesForVisibleSamples()" in page
    assert "window.Plotly.newPlot(" in page
    assert "function freshEmptyData" in page
    assert "function freshEmptyLayout" in page
    assert "Plotly.react(gd, freshEmptyData(), freshEmptyLayout(), gd._context)" in page
    assert "gd._context" in page
    assert "rist-raman-stack-change" in page
    assert "function handleRatioPeakPointer" in page
    assert "gd._ristNearestPeakCurveFromEvent(ev)" in page
    assert 'gd.addEventListener("mousedown", handleRatioPeakPointer, true)' in page
    assert "gd._ristHandledRamanRatioAt = Date.now()" in page
    assert 'new CustomEvent("rist-peak-actions-disabled"' in page
    assert "detail: {disabled: ratioMode}" in page
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


def test_raman_blank_figure_starts_without_reserved_legend_margin() -> None:
    figure = _blank_figure()

    assert len(figure.data) == 0
    assert figure.layout.margin.r == 70
    assert figure.layout.yaxis.title.text == "Intensity (a.u.)"


def test_raman_analyze_api_stacks_multiple_samples() -> None:
    content = SAMPLE_TXT.read_bytes()
    with TestClient(create_raman_preview_app()) as client:
        response = client.post(
            "/api/v1/raman/analyze",
            files=[
                ("files", (SAMPLE_TXT.name, content, "text/plain")),
                ("files", (SAMPLE_TXT.name, content, "text/plain")),
            ],
            data={"sensitivity": "25"},
        )

    assert response.status_code == 200
    figure = response.json()["figure"]
    assert figure["layout"]["yaxis"]["title"]["text"] == "Intensity (a.u.)"
    stack = figure["layout"]["meta"]["ristRamanStack"]
    assert stack["enabled"] is True
    assert stack["sampleOffsets"]["sample:0"] == 0
    assert stack["sampleOffsets"]["sample:1"] > 0

    parent_traces = [
        trace for trace in figure["data"]
        if trace.get("meta", {}).get("rist_sample_parent")
    ]
    assert len(parent_traces) == 2
    assert parent_traces[0]["meta"]["rist_raman_stack_offset"] == 0
    assert parent_traces[1]["meta"]["rist_raman_stack_offset"] > 0
    assert parent_traces[1]["y"][0] > parent_traces[0]["y"][0]


def test_raman_raw_loader_reads_instrument_txt() -> None:
    frame = load_raman_raw(SAMPLE_TXT.name, SAMPLE_TXT.read_bytes())

    assert len(frame) > 100
    assert list(frame.columns) == ["shift", "intensity"]
    assert frame["shift"].is_monotonic_increasing


def test_raman_raw_loader_reads_multi_sample_txt() -> None:
    samples = load_raman_raw_samples(
        MULTI_SAMPLE_TXT.name,
        MULTI_SAMPLE_TXT.read_bytes(),
    )

    assert [sample.label for sample in samples] == [
        "LiOH_4",
        "LiOH_3",
        "LiOH_2",
        "LiOH",
    ]
    assert samples[0].metadata is not None
    assert samples[0].metadata["Excitation Wavelength"] == "532.06 nm"
    assert samples[0].metadata["Exposure Time"] == "3 s"
    assert samples[0].metadata["Measurement Mode"] == "Point"
    assert [len(sample.frame) for sample in samples] == [1340, 1340, 1340, 1340]
    assert all(sample.frame["shift"].is_monotonic_increasing for sample in samples)


def test_raman_raw_loader_reads_shared_shift_multi_sample_csv() -> None:
    rows = ["Raman Shift,Sample A,Sample B"]
    for index in range(20):
        shift = 100 + index * 5
        rows.append(f"{shift},{index + 1},{(index + 1) * 2}")

    samples = load_raman_raw_samples(
        "shared-shift.csv",
        ("\n".join(rows) + "\n").encode(),
    )

    assert [sample.label for sample in samples] == ["Sample A", "Sample B"]
    assert [len(sample.frame) for sample in samples] == [20, 20]
    assert samples[0].frame["intensity"].iloc[-1] == 20
    assert samples[1].frame["intensity"].iloc[-1] == 40


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
    assert any(
        isinstance(trace.get("meta", {}).get("rist_peak", {}).get("base_y"), float)
        for trace in payload["figure"]["data"]
        if trace.get("meta", {}).get("rist_peak")
    )


def test_raman_analyze_api_expands_multi_sample_txt() -> None:
    with TestClient(create_raman_preview_app()) as client:
        response = client.post(
            "/api/v1/raman/analyze",
            files={
                "files": (
                    MULTI_SAMPLE_TXT.name,
                    MULTI_SAMPLE_TXT.read_bytes(),
                    "text/plain",
                )
            },
            data={"sensitivity": "25"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert [sample["label"] for sample in payload["samples"]] == [
        "LiOH_4",
        "LiOH_3",
        "LiOH_2",
        "LiOH",
    ]
    assert payload["samples"][0]["metadata"]["Excitation Wavelength"] == "532.06 nm"
    assert payload["samples"][0]["metadata"]["Exposure Time"] == "3 s"
    assert all(sample["fileName"] == MULTI_SAMPLE_TXT.name for sample in payload["samples"])
    parent_traces = [
        trace for trace in payload["figure"]["data"]
        if trace.get("meta", {}).get("rist_sample_parent")
    ]
    assert [trace["name"] for trace in parent_traces] == [
        "LiOH_4",
        "LiOH_3",
        "LiOH_2",
        "LiOH",
    ]
    assert payload["figure"]["layout"]["meta"]["ristRamanStack"]["enabled"] is True


def test_raman_report_api_builds_package_with_graph_and_raw_xlsx(monkeypatch) -> None:
    use_fake_pptx_pdf_converter(monkeypatch)
    content = MULTI_SAMPLE_TXT.read_bytes()
    with TestClient(create_raman_preview_app()) as client:
        analysis_response = client.post(
            "/api/v1/raman/analyze",
            files={"files": (MULTI_SAMPLE_TXT.name, content, "text/plain")},
            data={"sensitivity": "25"},
        )
        assert analysis_response.status_code == 200
        analysis = analysis_response.json()
        report_response = client.post(
            "/api/v1/raman/report",
            files={"files": (MULTI_SAMPLE_TXT.name, content, "text/plain")},
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
        with zipfile.ZipFile(BytesIO(archive.read("report.pptx"))) as pptx_archive:
            ppt_text = "\n".join(
                pptx_archive.read(name).decode("utf-8")
                for name in pptx_archive.namelist()
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            )
        assert "의뢰번호" in html_report
        assert "요청번호" not in html_report
        assert "작업자" not in html_report
        assert "WEB-PREVIEW" not in html_report
        assert "web-preview" not in html_report
        assert "LLM 보조 설명" not in html_report
        assert "LLM 사용" not in html_report
        assert "라이브러리" not in html_report
        assert "요청번호" not in ppt_text
        assert "작업자" not in ppt_text
        assert "WEB-PREVIEW" not in ppt_text
        assert "web-preview" not in ppt_text
        assert "LLM 보조 설명" not in ppt_text
        assert "LLM 사용" not in ppt_text
        assert "라이브러리" not in ppt_text
        assert "<a:t>LLM</a:t>" not in ppt_text


def test_raman_report_job_api_tracks_progress_and_downloads_package(monkeypatch) -> None:
    use_fake_pptx_pdf_converter(monkeypatch)
    content = MULTI_SAMPLE_TXT.read_bytes()
    with TestClient(create_raman_preview_app()) as client:
        analysis_response = client.post(
            "/api/v1/raman/analyze",
            files={"files": (MULTI_SAMPLE_TXT.name, content, "text/plain")},
            data={"sensitivity": "25"},
        )
        assert analysis_response.status_code == 200
        analysis = analysis_response.json()
        analysis["reportContext"] = {
            "requestNumber": "REQ-RAMAN-001",
            "limsExperimentCode": "LIMS-RAMAN-01",
            "limsExperimentName": "Raman 정성분석",
        }
        job_response = client.post(
            "/api/v1/raman/report/jobs",
            files={"files": (MULTI_SAMPLE_TXT.name, content, "text/plain")},
            data={
                "analysis_json": json.dumps(analysis),
                "figure_json": json.dumps(analysis["figure"]),
                "figure_image": TINY_PNG_DATA_URL,
                "requestNumber": "REQ-RAMAN-001",
                "equipmentCode": "RAMAN-EDGE-01",
                "operatorId": "operator01",
            },
        )
        assert job_response.status_code == 202
        job = job_response.json()
        assert job["status"] in {"queued", "running", "completed"}
        assert job["progressPct"] >= 0

        for _ in range(100):
            status_response = client.get(f"/api/v1/raman/report/jobs/{job['jobId']}")
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
    with zipfile.ZipFile(BytesIO(download_response.content)) as archive:
        html_report = archive.read("report.html").decode("utf-8")
    assert "REQ-RAMAN-001" in html_report
    assert "LIMS-RAMAN-01" in html_report
    assert "RAMAN-EDGE-01" in html_report
    assert "operator01" in html_report


def test_raman_report_chunked_upload_session_retries_and_downloads_package(monkeypatch) -> None:
    use_fake_pptx_pdf_converter(monkeypatch)
    content = MULTI_SAMPLE_TXT.read_bytes()
    first_chunk = content[:120]
    second_chunk = content[120:]

    with TestClient(create_raman_preview_app()) as client:
        analysis_response = client.post(
            "/api/v1/raman/analyze",
            files={"files": (MULTI_SAMPLE_TXT.name, content, "text/plain")},
            data={"sensitivity": "25"},
        )
        assert analysis_response.status_code == 200
        analysis = analysis_response.json()

        session_response = client.post("/api/v1/raman/report/upload-sessions")
        assert session_response.status_code == 201
        upload_id = session_response.json()["uploadId"]

        first_data = {
            "relative_path": f"raw/{MULTI_SAMPLE_TXT.name}",
            "offset": "0",
            "total_size": str(len(content)),
            "chunk_index": "0",
            "chunk_count": "2",
        }
        for _attempt in range(2):
            chunk_response = client.post(
                f"/api/v1/raman/report/upload-sessions/{upload_id}/chunks",
                data=first_data,
                files={"file": ("chunk-0", first_chunk, "application/octet-stream")},
            )
            assert chunk_response.status_code == 200
            assert chunk_response.json()["fileCompleted"] is False

        chunk_response = client.post(
            f"/api/v1/raman/report/upload-sessions/{upload_id}/chunks",
            data={
                "relative_path": f"raw/{MULTI_SAMPLE_TXT.name}",
                "offset": str(len(first_chunk)),
                "total_size": str(len(content)),
                "chunk_index": "1",
                "chunk_count": "2",
            },
            files={"file": ("chunk-1", second_chunk, "application/octet-stream")},
        )
        assert chunk_response.status_code == 200
        assert chunk_response.json()["fileCompleted"] is True

        complete_data = {
            "analysis_json": json.dumps(analysis),
            "figure_json": json.dumps(analysis["figure"]),
            "figure_image": TINY_PNG_DATA_URL,
            "requestNumber": "REQ-RAMAN-CHUNK",
            "equipmentCode": "RAMAN-EDGE-01",
            "operatorId": "operator01",
        }
        complete_response = client.post(
            f"/api/v1/raman/report/upload-sessions/{upload_id}/complete",
            data=complete_data,
        )
        assert complete_response.status_code == 202
        repeat_complete_response = client.post(
            f"/api/v1/raman/report/upload-sessions/{upload_id}/complete",
            data=complete_data,
        )
        assert repeat_complete_response.status_code == 202
        assert repeat_complete_response.json()["jobId"] == complete_response.json()["jobId"]
        job = repeat_complete_response.json()
        for _ in range(100):
            status_response = client.get(f"/api/v1/raman/report/jobs/{job['jobId']}")
            assert status_response.status_code == 200
            job = status_response.json()
            if job["status"] == "completed":
                break
            time.sleep(0.03)

        assert job["status"] == "completed"
        download_response = client.get(job["downloadUrl"])
        assert download_response.status_code == 200
        with zipfile.ZipFile(BytesIO(download_response.content)) as archive:
            names = set(archive.namelist())
        assert "report.pptx" in names
        assert "raw_data.xlsx" in names


def test_raman_report_job_accepts_large_multi_sample_lmr_payload(monkeypatch) -> None:
    use_fake_pptx_pdf_converter(monkeypatch)
    content = LARGE_LMR_TXT.read_bytes()
    with TestClient(create_raman_preview_app()) as client:
        analysis_response = client.post(
            "/api/v1/raman/analyze",
            files={"files": (LARGE_LMR_TXT.name, content, "text/plain")},
            data={"sensitivity": "25"},
        )
        assert analysis_response.status_code == 200
        analysis = analysis_response.json()
        uncompact_size = len(json.dumps(analysis))
        figure = compact_report_figure(analysis["figure"])
        analysis.pop("figure", None)
        assert uncompact_size > 1024 * 1024
        assert len(json.dumps(analysis)) < 1024 * 1024
        assert len(json.dumps(figure)) < 1024 * 1024

        job_response = client.post(
            "/api/v1/raman/report/jobs",
            files={"files": (LARGE_LMR_TXT.name, content, "text/plain")},
            data={
                "analysis_json": json.dumps(analysis),
                "figure_json": json.dumps(figure),
                "figure_image": TINY_PNG_DATA_URL,
            },
        )
        assert job_response.status_code == 202
        job = job_response.json()
        for _ in range(100):
            status_response = client.get(f"/api/v1/raman/report/jobs/{job['jobId']}")
            assert status_response.status_code == 200
            job = status_response.json()
            if job["status"] == "completed":
                break
            assert job["status"] != "failed", job.get("error")
            time.sleep(0.03)

        assert job["status"] == "completed"
        download_response = client.get(job["downloadUrl"])
        assert download_response.status_code == 200
        with zipfile.ZipFile(BytesIO(download_response.content)) as archive:
            names = set(archive.namelist())
        assert {"report.pptx", "report.pdf", "raw_data.xlsx"} <= names


def test_raman_assignment_library_api_defaults_and_assigns_sample() -> None:
    with TestClient(create_raman_preview_app()) as client:
        libraries_response = client.get("/api/v1/raman/assignment-libraries")
        assert libraries_response.status_code == 200
        libraries = libraries_response.json()["libraries"]
        by_id = {library["id"]: library for library in libraries}
        assert by_id["general-raman"]["defaultSelected"] is True
        assert by_id["carbon-graphite-raman"]["assignmentCount"] == 8
        assert by_id["lithium-compound-raman"]["assignmentCount"] == 23
        assert by_id["lmr-layered-oxide-raman"]["assignmentCount"] == 6

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


def test_raman_assignment_library_suggest_api_returns_draft(monkeypatch) -> None:
    captured = {}

    def fake_suggest(settings, request):
        captured["request"] = request
        return {
            "library": {
                "id": "graphite-raman",
                "name": "Graphite Raman",
                "description": "LLM draft",
                "fileName": "graphite-raman.json",
                "assignmentCount": 1,
                "defaultSelected": False,
                "valid": True,
                "error": "",
                "assignments": [{
                    "centerWavenumber": 1580,
                    "tolerance": 25,
                    "name": "G band",
                    "color": "#16a34a",
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
    with TestClient(create_raman_preview_app()) as client:
        response = client.post(
            "/api/v1/raman/assignment-libraries/suggest",
            json={"material": "graphite"},
        )

    assert response.status_code == 200
    assert captured["request"].experiment_code == "RAMAN"
    assert captured["request"].material == "graphite"
    payload = response.json()
    assert payload["warning"] == "검토 필요"
    assert payload["library"]["assignments"][0]["name"] == "G band"


def test_raman_pptx_lmr_assignment_library_assigns_sample() -> None:
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
            data={
                "sensitivity": "25",
                "assignment_library_ids": ["lmr-layered-oxide-raman"],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["settings"]["assignmentLibraries"][0]["id"] == "lmr-layered-oxide-raman"
    peak_names = [
        trace.get("name", "")
        for trace in payload["figure"]["data"]
        if trace.get("meta", {}).get("rist_peak")
    ]
    assert any("LMR A1g mode" in name for name in peak_names)


def test_raman_analyze_api_rejects_unknown_extension() -> None:
    with TestClient(create_raman_preview_app()) as client:
        response = client.post(
            "/api/v1/raman/analyze",
            files={"files": ("sample.bin", b"1 2\n3 4\n", "application/octet-stream")},
        )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_RAMAN_EXTENSION"

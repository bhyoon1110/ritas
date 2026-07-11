from __future__ import annotations

from io import BytesIO
import time
import unicodedata
import zipfile

from fastapi.testclient import TestClient

from app import xrd_web
from app.xrd_web import (
    XRD_NO_STORE_HEADERS,
    _build_xrd_example_html,
    _safe_relative_path,
    _write_synthetic_icdd_pdf_dir,
    _write_synthetic_xrd_raw,
    build_xrd_page,
    create_xrd_preview_app,
)
from lim.xrd_plot import build_xrd_html


def _synthetic_pdf_upload(tmp_path):
    pdf_dir = tmp_path / "pdf"
    _write_synthetic_icdd_pdf_dir(pdf_dir)
    pdf_path = next(pdf_dir.glob("*.pdf"))
    return pdf_path.name, pdf_path.read_bytes()


def test_xrd_workspace_contains_upload_controls() -> None:
    page = build_xrd_page()

    assert 'id="xrd-bundle-files"' in page
    assert 'id="xrd-bundle-folder"' in page
    assert 'class="xrd-hidden-input"' in page
    assert 'name="files"' in page
    assert "webkitdirectory" in page
    assert "XRD 번들 추가" in page
    assert "파일 추가" in page
    assert "폴더 추가" in page
    assert ".zip" in page
    assert "raw TXT, ICDD PDF 폴더, Excel/CSV, 이미지 또는 ZIP을 여기에 한꺼번에 드래그" in page
    assert "entryToBundleItems" in page
    assert "droppedBundleItems" in page
    assert 'id="xrd-origin" name="origin" value="true" checked' in page
    assert 'id="xrd-example"' in page
    assert 'http-equiv="Cache-Control"' in page
    assert 'content="no-store, no-cache, must-revalidate, max-age=0"' in page
    assert 'http-equiv="Pragma"' in page
    assert 'http-equiv="Expires"' in page
    assert 'id="xrd-pdf-export"' not in page
    assert "PDF Export" not in page
    assert "contentWindow.print()" not in page
    assert "보고서 다운로드" in page
    assert 'id="xrd-download" aria-disabled="true"' in page
    assert 'class="xrd-status-stack" id="xrd-status"' in page
    assert "xrd-status-close" in page
    assert "status.appendChild(item)" in page
    assert "timer = setTimeout(remove, error ? 7200 : 4300)" in page
    assert 'id="xrd-upload-progress"' in page
    assert 'id="xrd-upload-progress-bar"' in page
    assert 'id="xrd-report-progress"' in page
    assert 'id="xrd-report-progress-bar"' in page
    assert "setUploadProgress" in page
    assert "waitForReportJob" in page
    assert "/api/v1/xrd/report/jobs/" in page
    assert "startReportProgress" in page
    assert "finishReportProgress" in page
    assert "makeReadOnlyDownloadHtml" in page
    assert "loadPlotlyAssetText" in page
    assert "inlinePlotlyAsset" in page
    assert "textToBase64" in page
    assert "embeddedPlotlyScript" in page
    assert "JSON.stringify(encodedChunks)" in page
    assert "(0,eval)(code)" in page
    assert "escapeScriptText" not in page
    assert 'data-xrd-embedded-plotly="true"' in page
    assert "/xrd/assets/plotly.min.js" in page
    assert "window.readOnlyReport=true" in page
    assert "data-read-only-report" in page
    assert "readOnlyReportPrelude" in page
    assert "data-xrd-readonly-prelude" in page
    assert "data-xrd-readonly-lock" in page
    assert "[contenteditable=true]" in page
    assert "currentReportHtml" in page
    assert "reportFrame.contentDocument.documentElement.outerHTML" in page
    assert '"<!doctype html>\\n" + reportFrame.contentDocument.documentElement.outerHTML' in page
    assert '"<!doctype html>\n" + reportFrame.contentDocument.documentElement.outerHTML' not in page
    assert 'downloadLink.addEventListener("click"' in page
    assert "다운로드 준비 중..." in page
    assert "closest('#xrd-plot')" in page
    assert 'exampleButton.addEventListener("click"' in page
    assert "Bundle 안에 ICDD PDF 파일이 필요합니다." in page
    assert "raw와 ICDD Card 데이터를 분석하는 중입니다." in page
    assert "/api/v1/xrd/upload-sessions" in page
    assert "uploadBundleWithSession" in page
    assert "XRD_UPLOAD_CHUNK_RETRIES" in page
    assert "/api/v1/xrd/example" in page
    assert "LIM XRD" in page


def test_xrd_plotly_asset_route_serves_local_js() -> None:
    client = TestClient(create_xrd_preview_app())

    response = client.get("/xrd/assets/plotly.min.js")

    assert response.status_code == 200
    assert "application/javascript" in response.headers["content-type"]
    assert b"Plotly" in response.content[:2000]


def test_xrd_server_pdf_route_validates_html() -> None:
    with TestClient(create_xrd_preview_app()) as client:
        response = client.post("/api/v1/xrd/render-pdf", json={"html": ""})

    assert response.status_code == 400
    assert "XRD_PDF_RENDER_HTML_REQUIRED" in response.text


def test_xrd_server_pdf_route_returns_pdf(monkeypatch) -> None:
    captured = {}

    def fake_render(html: str, *, landscape: bool) -> bytes:
        captured["html"] = html
        captured["landscape"] = landscape
        return b"%PDF-1.4\n%%EOF\n"

    monkeypatch.setattr(xrd_web, "_render_xrd_html_pdf", fake_render)
    with TestClient(create_xrd_preview_app()) as client:
        response = client.post(
            "/api/v1/xrd/render-pdf",
            json={
                "html": "<!doctype html><html><head></head><body>report</body></html>",
                "landscape": True,
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert captured["landscape"] is True
    assert "report" in captured["html"]


def test_xrd_server_pdf_css_injection_sets_orientation() -> None:
    landscape = xrd_web._inject_xrd_print_page_css(
        "<html><head></head><body></body></html>",
        landscape=True,
    )
    portrait = xrd_web._inject_xrd_print_page_css(
        "<html><head></head><body></body></html>",
        landscape=False,
    )

    assert "@page { size: A4 portrait;" in landscape
    assert "@page:first { size: A4 landscape;" in landscape
    assert "@page xrd-graph-landscape { size: A4 landscape;" in landscape
    assert "body.xrd-report-graph-landscape #xrd-graph-section" in landscape
    assert "body.xrd-report-graph-landscape #xrd-image-info" in landscape
    assert "body.xrd-report-graph-landscape #xrd-llm-comment" in landscape
    assert "page: auto" in landscape
    assert "@page { size: A4 portrait;" in portrait
    assert "@page:first" not in portrait
    assert "@page xrd-graph-landscape" not in portrait
    assert "data-xrd-server-pdf" in landscape


def test_xrd_pdf_chrome_bin_env_override(monkeypatch, tmp_path) -> None:
    chrome = tmp_path / "chromium"
    chrome.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("RIST_PDF_CHROME_BIN", str(chrome))

    assert xrd_web._find_xrd_pdf_chrome() == str(chrome)


def test_xrd_pdf_snap_failure_message_is_actionable() -> None:
    message = xrd_web._xrd_pdf_failure_message(
        "snap-confine is packaged without necessary permissions "
        "cap_dac_override not found"
    )

    assert "NoNewPrivileges" in message
    assert "daemon-reload" in message


def test_xrd_pdf_missing_failure_message_mentions_render_dir() -> None:
    message = xrd_web._xrd_pdf_failure_message(
        "--headless=new: exit=0 pdf_size=0 pdf_missing=true stderr=dbus warning"
    )

    assert "PDF 파일을 만들지 못했습니다" in message
    assert "RIST_XRD_PDF_RENDER_DIR" in message


def test_xrd_pdf_render_parent_uses_env(monkeypatch, tmp_path) -> None:
    render_dir = tmp_path / "pdf-renders"
    monkeypatch.setenv("RIST_XRD_PDF_RENDER_DIR", str(render_dir))

    assert xrd_web._xrd_pdf_render_parent() == render_dir
    assert render_dir.is_dir()


def test_xrd_workspace_is_not_cached() -> None:
    with TestClient(create_xrd_preview_app()) as client:
        response = client.get("/xrd")

    assert response.status_code == 200
    for name, value in XRD_NO_STORE_HEADERS.items():
        assert response.headers[name] == value


def test_xrd_analyze_rejects_raw_without_pdf_cards() -> None:
    raw = b"10 1\n20 3\n30 2\n"

    with TestClient(create_xrd_preview_app()) as client:
        response = client.post(
            "/api/v1/xrd/analyze",
            files=[
                ("rawFiles", ("sample.txt", raw, "text/plain")),
            ],
        )

    assert response.status_code == 400
    assert "MISSING_XRD_PDF" in response.text
    assert "ICDD PDF" in response.text


def test_xrd_analyze_accepts_raw_with_pdf_cards(tmp_path) -> None:
    raw = b"10 1\n20 3\n30 2\n"
    pdf_name, pdf_bytes = _synthetic_pdf_upload(tmp_path)

    with TestClient(create_xrd_preview_app()) as client:
        response = client.post(
            "/api/v1/xrd/analyze",
            files=[
                ("rawFiles", ("sample.txt", raw, "text/plain")),
                ("pdfFiles", (pdf_name, pdf_bytes, "application/pdf")),
            ],
        )

    assert response.status_code == 200
    assert "sample Report" in response.text
    assert "상 동정 (Phase Identification) 결과" in response.text
    assert 'id="xrd-report-pdf-export"' in response.text
    assert "window.print()" in response.text
    assert "결정상(Phase) 정보" in response.text
    assert "plotly" in response.text.lower()
    assert '"editable": false' in response.text
    assert '"mirror":true' in response.text
    assert '"ticks":"inside"' in response.text


def test_xrd_analyze_includes_table_and_image_inputs(tmp_path) -> None:
    raw = b"10 1\n20 3\n30 2\n"
    pdf_name, pdf_bytes = _synthetic_pdf_upload(tmp_path)
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02"
        b"\x00\x00\x00\x0bIDATx\xdac\xf8\xff\x1f\x00\x03\x03\x01"
        b"\xfe\x02\xfe_\xbb\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    with TestClient(create_xrd_preview_app()) as client:
        response = client.post(
            "/api/v1/xrd/analyze",
            files=[
                ("files", ("sample.txt", raw, "text/plain")),
                ("files", (pdf_name, pdf_bytes, "application/pdf")),
                ("files", ("peaks.csv", b"No.,2theta\n1,20\n", "text/csv")),
                ("files", ("match.png", png, "image/png")),
            ],
        )

    assert response.status_code == 200
    assert "Peak list Excel Display" in response.text
    assert "peaks.csv" in response.text
    assert "그래프/상매칭 보조 이미지" in response.text
    assert "match.png" in response.text
    assert "data:image/png;base64" in response.text


def test_xrd_analyze_accepts_zipped_bundle(tmp_path) -> None:
    raw = b"10 1\n20 3\n30 2\n"
    pdf_name, pdf_bytes = _synthetic_pdf_upload(tmp_path)
    archive_bytes = BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("XRD Bundle/raw/sample.txt", raw)
        archive.writestr(f"XRD Bundle/ICDD Card/{pdf_name}", pdf_bytes)
        archive.writestr("XRD Bundle/peaks.csv", b"No.,2theta\n1,20\n")

    with TestClient(create_xrd_preview_app()) as client:
        response = client.post(
            "/api/v1/xrd/analyze",
            files=[
                (
                    "files",
                    ("xrd-bundle.zip", archive_bytes.getvalue(), "application/zip"),
                ),
            ],
        )

    assert response.status_code == 200
    assert "sample Report" in response.text
    assert "상 동정 (Phase Identification) 결과" in response.text
    assert "peaks.csv" in response.text


def test_xrd_chunked_upload_session_retries_and_builds_report(tmp_path) -> None:
    raw = b"10 1\n20 3\n30 2\n"
    first_chunk = raw[:5]
    second_chunk = raw[5:]
    pdf_name, pdf_bytes = _synthetic_pdf_upload(tmp_path)

    with TestClient(create_xrd_preview_app()) as client:
        session_response = client.post("/api/v1/xrd/upload-sessions")
        assert session_response.status_code == 201
        upload_id = session_response.json()["uploadId"]

        first_data = {
            "relative_path": "bundle/raw/sample.txt",
            "offset": "0",
            "total_size": str(len(raw)),
            "chunk_index": "0",
            "chunk_count": "2",
        }
        for _attempt in range(2):
            chunk_response = client.post(
                f"/api/v1/xrd/upload-sessions/{upload_id}/chunks",
                data=first_data,
                files={"file": ("chunk-0", first_chunk, "application/octet-stream")},
            )
            assert chunk_response.status_code == 200
            assert chunk_response.json()["fileCompleted"] is False

        chunk_response = client.post(
            f"/api/v1/xrd/upload-sessions/{upload_id}/chunks",
            data={
                "relative_path": "bundle/raw/sample.txt",
                "offset": str(len(first_chunk)),
                "total_size": str(len(raw)),
                "chunk_index": "1",
                "chunk_count": "2",
            },
            files={"file": ("chunk-1", second_chunk, "application/octet-stream")},
        )
        assert chunk_response.status_code == 200
        assert chunk_response.json()["fileCompleted"] is True

        pdf_response = client.post(
            f"/api/v1/xrd/upload-sessions/{upload_id}/chunks",
            data={
                "relative_path": f"bundle/ICDD Card/{pdf_name}",
                "offset": "0",
                "total_size": str(len(pdf_bytes)),
                "chunk_index": "0",
                "chunk_count": "1",
            },
            files={"file": ("chunk-pdf", pdf_bytes, "application/octet-stream")},
        )
        assert pdf_response.status_code == 200
        assert pdf_response.json()["fileCompleted"] is True

        complete_response = client.post(
            f"/api/v1/xrd/upload-sessions/{upload_id}/complete",
            data={"origin": "true"},
        )
        assert complete_response.status_code == 200
        complete_payload = complete_response.json()
        assert complete_payload["status"] in {"queued", "running", "completed"}
        assert complete_payload["jobId"]

        repeat_complete_response = client.post(
            f"/api/v1/xrd/upload-sessions/{upload_id}/complete",
            data={"origin": "true"},
        )
        assert repeat_complete_response.status_code == 200
        assert repeat_complete_response.json()["jobId"] == complete_payload["jobId"]

        deadline = time.time() + 10
        job_payload = complete_payload
        while job_payload["status"] not in {"completed", "failed"} and time.time() < deadline:
            time.sleep(0.05)
            job_payload = client.get(
                f"/api/v1/xrd/report/jobs/{complete_payload['jobId']}"
            ).json()
        assert job_payload["status"] == "completed"
        html_response = client.get(
            f"/api/v1/xrd/report/jobs/{complete_payload['jobId']}/html"
        )
        assert html_response.status_code == 200

    assert "sample Report" in html_response.text
    assert "상 동정 (Phase Identification) 결과" in html_response.text


def test_xrd_analyze_warns_for_pdf_that_cannot_be_extracted() -> None:
    raw = b"10 1\n20 3\n30 2\n"

    with TestClient(create_xrd_preview_app()) as client:
        response = client.post(
            "/api/v1/xrd/analyze",
            files=[
                ("files", ("sample.txt", raw, "text/plain")),
                ("files", ("not-a-card.pdf", b"%PDF-1.4\nbroken", "application/pdf")),
            ],
        )

    assert response.status_code == 200
    assert "sample Report" in response.text
    assert "not-a-card.pdf" in response.text
    assert "자동 피크 표 추출에는 실패" in response.text
    assert "No /Root object" not in response.text
    assert "Is this really a PDF" not in response.text


def test_xrd_analyze_rejects_file_with_pdf_suffix_but_no_pdf_header() -> None:
    raw = b"10 1\n20 3\n30 2\n"

    with TestClient(create_xrd_preview_app()) as client:
        response = client.post(
            "/api/v1/xrd/analyze",
            files=[
                ("files", ("sample.txt", raw, "text/plain")),
                ("files", ("not-a-pdf.pdf", b"not a pdf", "application/pdf")),
            ],
        )

    assert response.status_code == 400
    assert "INVALID_XRD_PDF" in response.text
    assert "not-a-pdf.pdf" in response.text
    assert "실제 PDF 문서가 아닙니다" in response.text


def test_xrd_analyze_keeps_legacy_split_upload_fields(tmp_path) -> None:
    raw = b"10 1\n20 3\n30 2\n"
    pdf_name, pdf_bytes = _synthetic_pdf_upload(tmp_path)

    with TestClient(create_xrd_preview_app()) as client:
        response = client.post(
            "/api/v1/xrd/analyze",
            files=[
                ("rawFiles", ("sample.txt", raw, "text/plain")),
                ("pdfFiles", (pdf_name, pdf_bytes, "application/pdf")),
            ],
        )

    assert response.status_code == 200
    assert "sample Report" in response.text


def test_xrd_example_falls_back_when_sample_files_are_absent(tmp_path) -> None:
    html = _build_xrd_example_html(tmp_path)

    assert "synthetic-xrd Report" in html
    assert "상 동정 (Phase Identification) 결과" in html
    assert "Synthetic Anatase Example" in html
    assert "25.300" in html
    assert "결정상(Phase) 정보" in html
    assert "피크 정보" in html
    assert "PDF 파일을 찾지 못했습니다" not in html


def test_xrd_report_can_use_llm_comment_provider(tmp_path) -> None:
    raw_path = tmp_path / "synthetic-xrd.txt"
    pdf_dir = tmp_path / "pdf"
    _write_synthetic_xrd_raw(raw_path)
    _write_synthetic_icdd_pdf_dir(pdf_dir)
    captured = {}

    def provider(context):
        captured.update(context)
        return {
            "html": "<p><strong>요약</strong><br>XRD 분석결과</p>",
            "note": "분석결과 확인",
        }

    result = build_xrd_html([(str(raw_path), str(pdf_dir))], comment_provider=provider)

    assert result["llm_comment_used"] is True
    assert "XRD 분석결과" in result["html"]
    assert "분석결과 확인" in result["html"]
    assert 'id="xrd-analysis-result" contenteditable="true"' in result["html"]
    assert "#xrd-plot .modebar," in result["html"]
    assert "preparePrintLegend" in result["html"]
    assert "xrd-print-legend" in result["html"]
    assert "refreshPrintLegend" in result["html"]
    assert 'handle.textContent = "범례"' in result["html"]
    assert "#xrd-plot .rist-legend-drag-handle" in result["html"]
    assert "#xrd-plot .rist-xrd-legend-checkbox" in result["html"]
    assert "#xrd-plot .rist-xrd-legend-branch" in result["html"]
    assert "#xrd-plot .legend {" in result["html"]
    assert "column-count: 2" in result["html"]
    assert "page-break-inside: auto" in result["html"]
    assert "page-break-after: always" in result["html"]
    assert "#d62728" in result["html"]
    assert '"width":2.2' in result["html"]
    assert "autoScale2d" in result["html"]
    assert '"editable": false' in result["html"]
    assert "xrd-axis-text-guard" in result["html"]
    assert "function blockAxisTextEdit(event)" in result["html"]
    assert ".yaxislayer-above" in result["html"]
    assert "rist-xrd-legend-checkbox" in result["html"]
    assert 'rect.setAttribute("fill", visible ? "#2563eb" : "#ffffff")' in result["html"]
    assert "function rowVisible(row)" in result["html"]
    assert "window.getComputedStyle(row).opacity" in result["html"]
    assert 'gd.on("plotly_restyle", schedule)' in result["html"]
    assert 'row.setAttribute("data-rist-xrd-legend-text-x"' in result["html"]
    assert 'textNode.setAttribute("x", String(tx + offset + 18))' in result["html"]
    assert "rist-xrd-legend-branch" in result["html"]
    assert "function legendKind(row)" in result["html"]
    assert "xrd_raw_group" in result["html"]
    assert "xrd_legend_kind" in result["html"]
    assert '"legend.x": 0.98' in result["html"]
    assert '"legend.y": 0.98' in result["html"]
    assert "bindRawLegendDomClick" not in result["html"]
    assert "xrd-tool-toggle" in result["html"]
    assert "xrd-tool-panel" in result["html"]
    assert "xrd-tool-opacity-slider" in result["html"]
    assert "xrd-phase-group-button" in result["html"]
    assert "xrd-phase-select" in result["html"]
    assert "xrd_phase_candidate" in result["html"]
    assert "xrd_original_color" in result["html"]
    assert "xrd_manual_phase_group" in result["html"]
    assert "legendgrouptitle.text" in result["html"]
    assert '"traceorder":"grouped"' in result["html"]
    assert "Synthetic Anatase Example (TiO2)" in result["html"]
    assert captured["experiment"] == "XRD"
    assert captured["raw_patterns"][0]["detected_raw_peaks"]
    assert captured["icdd_candidates"]["major"]
    assert captured["supporting_files"] == {"tables": [], "peak_lists": [], "images": []}


def test_xrd_bundle_relative_path_is_preserved_for_phase_folders() -> None:
    relative = _safe_relative_path(
        "ICDD Card (라이브러리 pdf)/유사상 1/TiO2 00-064-0863(S).pdf",
        "bundle-1",
    )

    assert relative.parts[-3:] == (
        "ICDD Card (라이브러리 pdf)",
        "유사상 1",
        "TiO2 00-064-0863(S).pdf",
    )


def test_xrd_bundle_repairs_cp949_zip_folder_mojibake() -> None:
    garbled_similar = "유사상 1".encode("cp949").decode("cp437")

    relative = _safe_relative_path(
        f"ICDD Card (라이브러리 pdf)/{garbled_similar}/TiO2 00-064-0863(S).pdf",
        "bundle-1",
    )

    assert relative.parts[-3:] == (
        "ICDD Card (라이브러리 pdf)",
        "유사상 1",
        "TiO2 00-064-0863(S).pdf",
    )


def test_xrd_bundle_keeps_normal_korean_paths() -> None:
    relative = _safe_relative_path(
        "ICDD Card (라이브러리 pdf)/유사상 1/상매칭 이미지.png",
        "bundle-1",
    )

    assert relative.parts[-3:] == (
        "ICDD Card (라이브러리 pdf)",
        "유사상 1",
        "상매칭 이미지.png",
    )


def test_xrd_bundle_repairs_utf8_nfd_zip_folder_mojibake() -> None:
    garbled_similar = unicodedata.normalize("NFD", "유사상 2").encode("utf-8").decode("cp437")
    garbled_icdd = (
        unicodedata.normalize("NFD", "ICDD Card (라이브러리 pdf)")
        .encode("utf-8")
        .decode("cp437")
    )

    relative = _safe_relative_path(
        f"{garbled_icdd}/{garbled_similar}/ZnO 01-082-9744(I).pdf",
        "bundle-1",
    )

    assert relative.parts[-3:] == (
        "ICDD Card (라이브러리 pdf)",
        "유사상 2",
        "ZnO 01-082-9744(I).pdf",
    )


def test_xrd_bundle_repairs_latin1_utf8_folder_mojibake() -> None:
    garbled_similar = "유사상 1".encode("utf-8").decode("latin1")

    relative = _safe_relative_path(
        f"ICDD Card (라이브러리 pdf)/{garbled_similar}/ZnO 01-082-9744(I).pdf",
        "bundle-1",
    )

    assert relative.parts[-3:] == (
        "ICDD Card (라이브러리 pdf)",
        "유사상 1",
        "ZnO 01-082-9744(I).pdf",
    )

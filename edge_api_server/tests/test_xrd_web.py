from __future__ import annotations

from fastapi.testclient import TestClient

from app.xrd_web import (
    _build_xrd_example_html,
    _write_synthetic_icdd_pdf_dir,
    _write_synthetic_xrd_raw,
    build_xrd_page,
    create_xrd_preview_app,
)
from lim.xrd_plot import build_xrd_html


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
    assert "raw TXT, ICDD PDF 폴더, Excel/CSV, 이미지를 여기에 한꺼번에 드래그" in page
    assert "entryToBundleItems" in page
    assert "droppedBundleItems" in page
    assert 'id="xrd-origin" name="origin" value="true" checked' in page
    assert 'id="xrd-example"' in page
    assert 'id="xrd-pdf-export"' not in page
    assert "PDF Export" not in page
    assert "contentWindow.print()" not in page
    assert "보고서 다운로드" in page
    assert 'id="xrd-download" aria-disabled="true"' in page
    assert 'class="xrd-status-stack" id="xrd-status"' in page
    assert "xrd-status-close" in page
    assert "status.appendChild(item)" in page
    assert "timer = setTimeout(remove, error ? 7200 : 4300)" in page
    assert 'id="xrd-report-progress"' in page
    assert 'id="xrd-report-progress-bar"' in page
    assert "startReportProgress" in page
    assert "finishReportProgress" in page
    assert "raw와 ICDD Card 데이터를 분석하는 중입니다." in page
    assert "/api/v1/xrd/analyze" in page
    assert "/api/v1/xrd/example" in page
    assert "LIM XRD" in page


def test_xrd_analyze_accepts_raw_without_pdf_cards() -> None:
    raw = b"10 1\n20 3\n30 2\n"

    with TestClient(create_xrd_preview_app()) as client:
        response = client.post(
            "/api/v1/xrd/analyze",
            files=[
                ("rawFiles", ("sample.txt", raw, "text/plain")),
            ],
        )

    assert response.status_code == 200
    assert "sample Report" in response.text
    assert "그래프 영역" in response.text
    assert 'id="xrd-report-pdf-export"' in response.text
    assert "window.print()" in response.text
    assert "PDF 파일" in response.text
    assert "plotly" in response.text.lower()
    assert '"editable": false' in response.text
    assert '"mirror":true' in response.text
    assert '"ticks":"inside"' in response.text


def test_xrd_analyze_includes_table_and_image_inputs() -> None:
    raw = b"10 1\n20 3\n30 2\n"
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
                ("files", ("peaks.csv", b"No.,2theta\n1,20\n", "text/csv")),
                ("files", ("match.png", png, "image/png")),
            ],
        )

    assert response.status_code == 200
    assert "제공된 Excel 파일 Display" in response.text
    assert "peaks.csv" in response.text
    assert "그래프/상매칭 보조 이미지" in response.text
    assert "match.png" in response.text
    assert "data:image/png;base64" in response.text


def test_xrd_analyze_skips_unreadable_pdf_in_bundle() -> None:
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
    assert "PDF를 읽지 못했습니다" in response.text


def test_xrd_analyze_keeps_legacy_split_upload_fields() -> None:
    raw = b"10 1\n20 3\n30 2\n"

    with TestClient(create_xrd_preview_app()) as client:
        response = client.post(
            "/api/v1/xrd/analyze",
            files=[
                ("rawFiles", ("sample.txt", raw, "text/plain")),
            ],
        )

    assert response.status_code == 200
    assert "sample Report" in response.text


def test_xrd_example_falls_back_when_sample_files_are_absent(tmp_path) -> None:
    html = _build_xrd_example_html(tmp_path)

    assert "synthetic-xrd Report" in html
    assert "그래프 영역" in html
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
            "html": "<p><strong>요약</strong><br>LLM XRD 해석 초안</p>",
            "note": "LLM 연결 확인",
        }

    result = build_xrd_html([(str(raw_path), str(pdf_dir))], comment_provider=provider)

    assert result["llm_comment_used"] is True
    assert "LLM XRD 해석 초안" in result["html"]
    assert "LLM 연결 확인" in result["html"]
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
    assert '"legend.x": 1.02' in result["html"]
    assert '"legend.y": 0.84' in result["html"]
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
    assert "Synthetic Anatase Example / TiO2" in result["html"]
    assert captured["experiment"] == "XRD"
    assert captured["raw_patterns"][0]["detected_raw_peaks"]
    assert captured["icdd_candidates"]["major"]
    assert captured["supporting_files"] == {"tables": [], "images": []}

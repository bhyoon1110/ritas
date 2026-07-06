from __future__ import annotations

from fastapi.testclient import TestClient

from app.xrd_web import build_xrd_page, create_xrd_preview_app


def test_xrd_workspace_contains_upload_controls() -> None:
    page = build_xrd_page()

    assert 'id="xrd-bundle-files"' in page
    assert 'id="xrd-bundle-folder"' in page
    assert 'name="files"' in page
    assert "webkitdirectory" in page
    assert "raw TXT, ICDD PDF, Excel/CSV, 이미지를 한꺼번에" in page
    assert 'id="xrd-example"' in page
    assert 'id="xrd-download" aria-disabled="true"' in page
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
    assert "PDF 파일" in response.text
    assert "plotly" in response.text.lower()


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

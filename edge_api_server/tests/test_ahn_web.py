from __future__ import annotations

from io import BytesIO
import zipfile

from fastapi.testclient import TestClient
from PIL import Image
import pytest

import app.ahn_web as ahn_web
from app.ahn_web import build_ahn_page, create_tem_preview_app, _find_ahn_input_root


def _tiny_tiff_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (80, 60), (220, 224, 230)).save(buffer, format="TIFF")
    return buffer.getvalue()


def test_ahn_workspace_contains_folder_upload_controls() -> None:
    page = build_ahn_page()

    assert 'id="ahn-bundle-files"' in page
    assert 'id="ahn-bundle-folder"' in page
    assert "webkitdirectory" in page
    assert "TEM raw bundle 추가" in page
    assert "tem, stem, report, scale 폴더" in page
    assert "파일 추가" in page
    assert "폴더 추가" in page
    assert 'id="ahn-example"' in page
    assert 'id="ahn-run"' in page
    assert 'id="ahn-download-pptx"' in page
    assert 'id="ahn-download-package"' in page
    assert 'aria-disabled="true"' in page
    assert "/api/v1/tem/analyze" in page
    assert "/api/v1/tem/example" in page
    assert "entryToBundleItems" in page
    assert "droppedBundleItems" in page
    assert "PowerPoint 보고서를 렌더링하는 중입니다." in page
    assert "TEM/STEM" in page


def test_ahn_input_root_finds_browser_top_level_folder(tmp_path) -> None:
    root = tmp_path / "upload"
    nested = root / "TESTData" / "stem"
    nested.mkdir(parents=True)

    assert _find_ahn_input_root(root) == root / "TESTData"


def test_ahn_analyze_accepts_folder_bundle_and_downloads_pptx() -> None:
    pytest.importorskip("pptx")

    with TestClient(create_tem_preview_app()) as client:
        response = client.post(
            "/api/v1/tem/analyze",
            files=[
                (
                    "files",
                    (
                        "Bundle/stem/001_100kX.tif",
                        _tiny_tiff_bytes(),
                        "image/tiff",
                    ),
                ),
            ],
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["summary"]["stemImageCount"] == 1
        assert payload["summary"]["temImageCount"] == 0
        assert payload["downloads"]["pptx"].endswith("/download/pptx")
        assert payload["downloads"]["package"].endswith("/download/package")

        pptx_response = client.get(payload["downloads"]["pptx"])
        assert pptx_response.status_code == 200
        assert pptx_response.content.startswith(b"PK")

        package_response = client.get(payload["downloads"]["package"])
        assert package_response.status_code == 200
        with zipfile.ZipFile(BytesIO(package_response.content)) as archive:
            names = set(archive.namelist())
        assert "tem-report.pptx" in names
        assert "analysis-result.json" in names
        assert "manifest.json" in names


def test_ahn_analyze_rejects_empty_upload() -> None:
    with TestClient(create_tem_preview_app()) as client:
        response = client.post("/api/v1/tem/analyze", files=[])

    assert response.status_code == 400
    assert "TEM_FILES_REQUIRED" in response.text


def test_tem_example_falls_back_when_sample_data_is_absent(tmp_path, monkeypatch) -> None:
    pytest.importorskip("pptx")
    fake_file = tmp_path / "edge_api_server" / "app" / "ahn_web.py"
    fake_file.parent.mkdir(parents=True)
    fake_file.write_text("# test path\n", encoding="utf-8")
    monkeypatch.setattr(ahn_web, "__file__", str(fake_file))

    with TestClient(create_tem_preview_app()) as client:
        response = client.get("/api/v1/tem/example")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["temImageCount"] == 1
    assert payload["summary"]["stemImageCount"] == 1
    assert payload["summary"]["stemBfImageCount"] == 1
    assert payload["downloads"]["pptx"].endswith("/download/pptx")

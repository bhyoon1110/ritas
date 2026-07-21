from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
import time
import zipfile
import zlib

from fastapi.testclient import TestClient
from PIL import Image
import pytest

import app.ahn_web as ahn_web
from app.ahn_web import build_ahn_page, create_tem_preview_app, _find_ahn_input_root


def _tiny_tiff_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (80, 60), (220, 224, 230)).save(buffer, format="TIFF")
    return buffer.getvalue()


def _minimal_ooxml_bytes(*, document: bool = False, binary_workbook: bool = False) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        if document:
            archive.writestr("word/document.xml", "<w:document xmlns:w='w'/>")
        elif binary_workbook:
            archive.writestr("xl/workbook.bin", b"workbook")
        else:
            archive.writestr("xl/workbook.xml", "<workbook/>")
    return buffer.getvalue()


def _wait_for_tem_job(client: TestClient, payload: dict, *, timeout: float = 8.0) -> dict:
    assert payload["jobId"]
    deadline = time.time() + timeout
    current = payload
    while current.get("status") not in {"completed", "failed"} and time.time() < deadline:
        time.sleep(0.05)
        response = client.get(f"/api/v1/tem/report/jobs/{payload['jobId']}")
        assert response.status_code == 200
        current = response.json()
    assert current.get("status") in {"completed", "failed"}
    return current


def test_ahn_workspace_contains_folder_upload_controls() -> None:
    page = build_ahn_page()

    assert 'id="ahn-bundle-files"' in page
    assert 'id="ahn-bundle-folder"' in page
    assert "webkitdirectory" in page
    assert "TEM raw bundle 추가" in page
    assert "tem, stem, report, scale 폴더를 포함한 raw 폴더 또는 ZIP" in page
    assert ".zip" in page
    assert "파일 추가" in page
    assert "폴더 추가" in page
    assert 'id="ahn-example"' in page
    assert 'id="ahn-run" disabled' in page
    assert 'id="ahn-download-pptx"' in page
    assert 'id="ahn-download-package"' in page
    assert 'id="ahn-upload-progress"' in page
    assert 'id="ahn-collect-progress"' in page
    assert "선택한 파일 목록을 준비하는 중입니다." in page
    assert 'aria-disabled="true"' in page
    assert "xlsm" in page
    assert "xlsb" in page
    assert "/api/v1/tem/upload-sessions" in page
    assert "/api/v1/tem/example" in page
    assert "/api/v1/tem/report/jobs/" in page
    assert "waitForReportJob" in page
    assert "uploadBundleWithSession" in page
    assert "TEM_UPLOAD_CHUNK_RETRIES" in page
    assert "requestJsonPostWithRetry" in page
    assert "parseErrorMessage" in page
    assert "서버 연결에 실패했습니다" in page
    assert "XMLHttpRequest" in page
    assert "raw 파일 업로드 중" in page
    assert "chunkCrc32" in page
    assert 'formData.append("chunk_crc32"' in page
    assert "무결성 및 암호화 여부를 검사하는 중입니다" in page
    assert "transientFailures" in page
    assert "entryToBundleItems" in page
    assert "droppedBundleItems" in page
    assert "collectingFiles" in page
    assert "waitForCollectionPaint" in page
    assert "requestAnimationFrame" in page
    assert "syncActionState" in page
    assert "폴더 안의 raw 파일 목록을 읽는 중입니다." in page
    assert "목록 표시가 완료된 뒤 다시 실행하세요." in page
    assert "완료되면 다운로드 버튼이 활성화됩니다." in page
    assert 'id="ahn-report-transfer"' in page
    assert page.index('id="ahn-status"') < page.index('id="ahn-report-transfer"')
    assert 'id="ahn-request-load"' in page
    assert 'id="ahn-request-select"' in page
    assert 'X-Request-Id": "tem-request-list-' in page
    assert 'data-ahn-transfer-field="requestNumber"' in page
    assert 'data-ahn-transfer-field="limsExperimentCode"' in page
    assert 'id="ahn-report-send" disabled' in page
    assert 'REQUEST_EXPERIMENT_TYPE = "TEM"' in page
    assert 'encodeURIComponent(lastReportJob.jobId) + "/send"' in page
    assert "TEM/STEM" in page


def test_tem_send_route_queues_completed_package(monkeypatch, tmp_path) -> None:
    package_path = tmp_path / "tem-report-package.zip"
    with zipfile.ZipFile(package_path, "w") as archive:
        archive.writestr("tem-report.pptx", b"pptx")
    job = SimpleNamespace(
        job_id="tem-job-1",
        status="completed",
        package_path=package_path,
    )
    captured = {}

    def fake_send_preview_report_package(**values):
        captured.update(values)
        return {
            "queued": True,
            "sent": False,
            "transferId": "transfer-1",
            "status": "PENDING",
        }

    monkeypatch.setattr(
        ahn_web,
        "send_preview_report_package",
        fake_send_preview_report_package,
    )
    monkeypatch.setattr(ahn_web, "_cleanup_old_jobs", lambda: None)
    with ahn_web._ahn_report_jobs_lock:
        ahn_web._ahn_report_jobs[job.job_id] = job
    try:
        with TestClient(create_tem_preview_app()) as client:
            response = client.post(
                "/api/v1/tem/report/jobs/tem-job-1/send",
                json={
                    "requestNumber": "REQ-001",
                    "experimentCode": "TEM",
                    "equipmentCode": "TEM-EDGE-01",
                    "operatorId": "operator-1",
                },
            )
    finally:
        with ahn_web._ahn_report_jobs_lock:
            ahn_web._ahn_report_jobs.pop(job.job_id, None)

    assert response.status_code == 200
    assert response.json()["transferId"] == "transfer-1"
    assert captured["job"] is job
    assert captured["payload"].request_number == "REQ-001"
    assert captured["payload"].experiment_code == "TEM"


def test_ahn_input_root_finds_browser_top_level_folder(tmp_path) -> None:
    root = tmp_path / "upload"
    nested = root / "TESTData" / "stem"
    nested.mkdir(parents=True)

    assert _find_ahn_input_root(root) == root / "TESTData"


def test_ahn_input_root_accepts_reports_folder_alias(tmp_path) -> None:
    root = tmp_path / "upload"
    nested = root / "TESTData" / "reports"
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
                (
                    "files",
                    (
                        "Bundle/raw data.xlsx",
                        _minimal_ooxml_bytes(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                ),
            ],
        )

        assert response.status_code == 200
        payload = _wait_for_tem_job(client, response.json())
        assert payload["status"] == "completed"
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
        assert "raw/raw data.xlsx" in names


def test_ahn_analyze_accepts_zipped_bundle_and_downloads_pptx() -> None:
    pytest.importorskip("pptx")
    archive_bytes = BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("TESTData/stem/001_100kX.tif", _tiny_tiff_bytes())
        archive.writestr("TESTData/reports/001 point raw.xlsm", _minimal_ooxml_bytes())

    with TestClient(create_tem_preview_app()) as client:
        response = client.post(
            "/api/v1/tem/analyze",
            files=[
                (
                    "files",
                    ("tem-bundle.zip", archive_bytes.getvalue(), "application/zip"),
                ),
            ],
        )

        assert response.status_code == 200
        payload = _wait_for_tem_job(client, response.json())
        assert payload["status"] == "completed"
        assert payload["summary"]["stemImageCount"] == 1
        assert payload["summary"]["temImageCount"] == 0
        assert payload["summary"]["spreadsheetCount"] == 1
        assert payload["downloads"]["pptx"].endswith("/download/pptx")
        assert payload["downloads"]["package"].endswith("/download/package")

        package_response = client.get(payload["downloads"]["package"])
        assert package_response.status_code == 200
        with zipfile.ZipFile(BytesIO(package_response.content)) as package:
            names = set(package.namelist())
        assert "raw/reports/001 point raw.xlsm" in names


def test_ahn_chunked_upload_session_retries_and_downloads_package() -> None:
    pytest.importorskip("pptx")
    image_bytes = _tiny_tiff_bytes()
    first_chunk = image_bytes[:100]
    second_chunk = image_bytes[100:]

    with TestClient(create_tem_preview_app()) as client:
        session_response = client.post("/api/v1/tem/upload-sessions")
        assert session_response.status_code == 200
        upload_id = session_response.json()["uploadId"]

        data = {
            "relative_path": "Bundle/stem/001_100kX.tif",
            "offset": "0",
            "total_size": str(len(image_bytes)),
            "chunk_index": "0",
            "chunk_count": "2",
        }
        for _attempt in range(2):
            chunk_response = client.post(
                f"/api/v1/tem/upload-sessions/{upload_id}/chunks",
                data=data,
                files={"file": ("chunk-0", first_chunk, "application/octet-stream")},
            )
            assert chunk_response.status_code == 200
            assert chunk_response.json()["fileCompleted"] is False

        chunk_response = client.post(
            f"/api/v1/tem/upload-sessions/{upload_id}/chunks",
            data={
                "relative_path": "Bundle/stem/001_100kX.tif",
                "offset": str(len(first_chunk)),
                "total_size": str(len(image_bytes)),
                "chunk_index": "1",
                "chunk_count": "2",
            },
            files={"file": ("chunk-1", second_chunk, "application/octet-stream")},
        )
        assert chunk_response.status_code == 200
        assert chunk_response.json()["fileCompleted"] is True

        complete_response = client.post(f"/api/v1/tem/upload-sessions/{upload_id}/complete")
        assert complete_response.status_code == 200
        repeat_complete_response = client.post(f"/api/v1/tem/upload-sessions/{upload_id}/complete")
        assert repeat_complete_response.status_code == 200
        assert repeat_complete_response.json()["jobId"] == complete_response.json()["jobId"]
        payload = _wait_for_tem_job(client, complete_response.json())
        assert payload["status"] == "completed"
        assert payload["summary"]["stemImageCount"] == 1
        assert payload["downloads"]["package"].endswith("/download/package")

        package_response = client.get(payload["downloads"]["package"])
        assert package_response.status_code == 200
        with zipfile.ZipFile(BytesIO(package_response.content)) as package:
            names = set(package.namelist())
        assert "tem-report.pptx" in names
        assert "analysis-result.json" in names


def test_ahn_chunked_upload_rejects_checksum_mismatch_without_storing_chunk() -> None:
    image_bytes = _tiny_tiff_bytes()

    with TestClient(create_tem_preview_app()) as client:
        session_response = client.post("/api/v1/tem/upload-sessions")
        upload_id = session_response.json()["uploadId"]
        chunk_response = client.post(
            f"/api/v1/tem/upload-sessions/{upload_id}/chunks",
            data={
                "relative_path": "Bundle/stem/001_100kX.tif",
                "offset": "0",
                "total_size": str(len(image_bytes)),
                "chunk_index": "0",
                "chunk_count": "1",
                "chunk_crc32": "00000000",
            },
            files={"file": ("image.tif", image_bytes, "application/octet-stream")},
        )

        assert chunk_response.status_code == 400
        payload = chunk_response.json()
        assert payload["code"] == "TEM_UPLOAD_CHUNK_CHECKSUM_MISMATCH"
        session = ahn_web._get_upload_session(upload_id)
        state = session.files["Bundle/stem/001_100kX.tif"]
        assert not (session.input_root / state.temp_path).exists()
        assert state.uploaded_bytes == 0


def test_ahn_chunked_upload_accepts_matching_checksum() -> None:
    image_bytes = _tiny_tiff_bytes()
    checksum = f"{zlib.crc32(image_bytes) & 0xFFFFFFFF:08x}"

    with TestClient(create_tem_preview_app()) as client:
        session_response = client.post("/api/v1/tem/upload-sessions")
        upload_id = session_response.json()["uploadId"]
        chunk_response = client.post(
            f"/api/v1/tem/upload-sessions/{upload_id}/chunks",
            data={
                "relative_path": "Bundle/stem/001_100kX.tif",
                "offset": "0",
                "total_size": str(len(image_bytes)),
                "chunk_index": "0",
                "chunk_count": "1",
                "chunk_crc32": checksum,
            },
            files={"file": ("image.tif", image_bytes, "application/octet-stream")},
        )

    assert chunk_response.status_code == 200
    assert chunk_response.json()["fileCompleted"] is True


def test_ahn_chunked_upload_rejects_corrupt_docx_before_report_job() -> None:
    corrupt_docx = b"not-a-docx-container"

    with TestClient(create_tem_preview_app()) as client:
        session_response = client.post("/api/v1/tem/upload-sessions")
        upload_id = session_response.json()["uploadId"]
        chunk_response = client.post(
            f"/api/v1/tem/upload-sessions/{upload_id}/chunks",
            data={
                "relative_path": "Bundle/reports/Project 1_Test MAP.docx",
                "offset": "0",
                "total_size": str(len(corrupt_docx)),
                "chunk_index": "0",
                "chunk_count": "1",
            },
            files={"file": ("report.docx", corrupt_docx, "application/octet-stream")},
        )
        assert chunk_response.status_code == 200
        assert chunk_response.json()["fileCompleted"] is True

        complete_response = client.post(f"/api/v1/tem/upload-sessions/{upload_id}/complete")

    assert complete_response.status_code == 400
    payload = complete_response.json()
    assert payload["code"] == "TEM_UPLOAD_INTEGRITY_FAILED"
    assert "Project 1_Test MAP.docx" in payload["message"]
    assert "Office" in payload["message"]
    assert "jobId" not in payload


def test_ahn_chunked_upload_rechecks_stored_file_size_before_report_job() -> None:
    image_bytes = _tiny_tiff_bytes()

    with TestClient(create_tem_preview_app()) as client:
        session_response = client.post("/api/v1/tem/upload-sessions")
        upload_id = session_response.json()["uploadId"]
        chunk_response = client.post(
            f"/api/v1/tem/upload-sessions/{upload_id}/chunks",
            data={
                "relative_path": "Bundle/stem/001_100kX.tif",
                "offset": "0",
                "total_size": str(len(image_bytes)),
                "chunk_index": "0",
                "chunk_count": "1",
            },
            files={"file": ("image.tif", image_bytes, "application/octet-stream")},
        )
        assert chunk_response.status_code == 200
        session = ahn_web._get_upload_session(upload_id)
        state = session.files["Bundle/stem/001_100kX.tif"]
        (session.input_root / state.stored_path).write_bytes(image_bytes[:-10])

        complete_response = client.post(f"/api/v1/tem/upload-sessions/{upload_id}/complete")

    assert complete_response.status_code == 409
    payload = complete_response.json()
    assert payload["code"] == "TEM_UPLOAD_STORAGE_MISMATCH"
    assert payload["retryable"] is True
    assert "001_100kX.tif" in payload["message"]


def test_ahn_chunked_upload_rejects_drm_or_encrypted_office_before_report_job() -> None:
    protected_docx = (
        ahn_web.OLE_COMPOUND_MAGIC
        + b"\x00" * 128
        + "EncryptedPackage".encode("utf-16le")
    )

    with TestClient(create_tem_preview_app()) as client:
        session_response = client.post("/api/v1/tem/upload-sessions")
        upload_id = session_response.json()["uploadId"]
        chunk_response = client.post(
            f"/api/v1/tem/upload-sessions/{upload_id}/chunks",
            data={
                "relative_path": "Bundle/reports/Project 1_Protected Point.docx",
                "offset": "0",
                "total_size": str(len(protected_docx)),
                "chunk_index": "0",
                "chunk_count": "1",
            },
            files={"file": ("protected.docx", protected_docx, "application/octet-stream")},
        )
        assert chunk_response.status_code == 200

        complete_response = client.post(f"/api/v1/tem/upload-sessions/{upload_id}/complete")

    assert complete_response.status_code == 400
    payload = complete_response.json()
    assert payload["code"] == "TEM_PROTECTED_FILE"
    assert "Project 1_Protected Point.docx" in payload["message"]
    assert "DRM" in payload["message"] or "암호" in payload["message"]


def test_ahn_validation_recovers_docx_with_unknown_extension(tmp_path) -> None:
    upload_root = tmp_path / "upload"
    source = upload_root / "Bundle" / "reports" / "Project 1_Test MAP.payload"
    source.parent.mkdir(parents=True)
    source.write_bytes(_minimal_ooxml_bytes(document=True))

    result = ahn_web._validate_ahn_upload_files(upload_root)

    normalized = upload_root / "Bundle" / "reports" / "Project 1_Test MAP.docx"
    assert normalized.exists()
    assert not source.exists()
    assert result["normalizedFiles"] == [
        {
            "from": "Bundle/reports/Project 1_Test MAP.payload",
            "to": "Bundle/reports/Project 1_Test MAP.docx",
            "type": "docx",
        }
    ]


def test_ahn_chunked_upload_detects_protected_office_with_unknown_extension() -> None:
    protected = (
        ahn_web.OLE_COMPOUND_MAGIC
        + b"\x00" * 128
        + "EncryptedPackage".encode("utf-16le")
    )

    with TestClient(create_tem_preview_app()) as client:
        upload_id = client.post("/api/v1/tem/upload-sessions").json()["uploadId"]
        chunk_response = client.post(
            f"/api/v1/tem/upload-sessions/{upload_id}/chunks",
            data={
                "relative_path": "Bundle/reports/Project 1_Protected.payload",
                "offset": "0",
                "total_size": str(len(protected)),
                "chunk_index": "0",
                "chunk_count": "1",
            },
            files={"file": ("protected.payload", protected, "application/octet-stream")},
        )
        assert chunk_response.status_code == 200
        complete_response = client.post(f"/api/v1/tem/upload-sessions/{upload_id}/complete")

    assert complete_response.status_code == 400
    assert complete_response.json()["code"] == "TEM_PROTECTED_FILE"


def test_ahn_analyze_rejects_corrupt_image_before_report_job() -> None:
    with TestClient(create_tem_preview_app()) as client:
        response = client.post(
            "/api/v1/tem/analyze",
            files=[
                (
                    "files",
                    ("Bundle/stem/broken.tif", b"broken-image", "image/tiff"),
                )
            ],
        )

    assert response.status_code == 400
    payload = response.json()
    assert payload["code"] == "TEM_UPLOAD_INTEGRITY_FAILED"
    assert "broken.tif" in payload["message"]


def test_ahn_analyze_rejects_zip_with_corrupt_docx_before_report_job() -> None:
    archive_bytes = BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("TESTData/stem/001_100kX.tif", _tiny_tiff_bytes())
        archive.writestr("TESTData/reports/broken.docx", b"not-a-docx")

    with TestClient(create_tem_preview_app()) as client:
        response = client.post(
            "/api/v1/tem/analyze",
            files=[("files", ("tem-bundle.zip", archive_bytes.getvalue(), "application/zip"))],
        )

    assert response.status_code == 400
    payload = response.json()
    assert payload["code"] == "TEM_UPLOAD_INTEGRITY_FAILED"
    assert "tem-bundle.zip::TESTData/reports/broken.docx" in payload["message"]


def test_ahn_analyze_returns_before_zip_extraction_finishes(monkeypatch) -> None:
    pytest.importorskip("pptx")
    archive_bytes = BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("TESTData/stem/001_100kX.tif", _tiny_tiff_bytes())
    original_extract = ahn_web._extract_pending_zips

    def slow_extract(root):
        time.sleep(0.5)
        return original_extract(root)

    monkeypatch.setattr(ahn_web, "_extract_pending_zips", slow_extract)

    with TestClient(create_tem_preview_app()) as client:
        started = time.perf_counter()
        response = client.post(
            "/api/v1/tem/analyze",
            files=[
                (
                    "files",
                    ("tem-bundle.zip", archive_bytes.getvalue(), "application/zip"),
                ),
            ],
        )
        elapsed = time.perf_counter() - started

        assert response.status_code == 200
        assert elapsed < 0.4
        payload = _wait_for_tem_job(client, response.json(), timeout=5)
        assert payload["status"] == "completed"
        assert payload["summary"]["stemImageCount"] == 1


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
    payload = _wait_for_tem_job(client, response.json())
    assert payload["status"] == "completed"
    assert payload["summary"]["temImageCount"] == 1
    assert payload["summary"]["stemImageCount"] == 1
    assert payload["summary"]["stemBfImageCount"] == 1
    assert payload["downloads"]["pptx"].endswith("/download/pptx")


def test_tem_example_reports_build_failure_without_masking(monkeypatch) -> None:
    def fail_build_outputs(**_kwargs):
        raise RuntimeError("python-pptx missing")

    monkeypatch.setattr(ahn_web, "build_outputs", fail_build_outputs)

    with TestClient(create_tem_preview_app(), raise_server_exceptions=False) as client:
        response = client.get("/api/v1/tem/example")

    assert response.status_code == 200
    payload = _wait_for_tem_job(client, response.json())
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "TEM_REPORT_BUILD_FAILED"
    assert "python-pptx missing" in payload["error"]["message"]
    assert payload["error"]["details"]["exceptionType"] == "RuntimeError"

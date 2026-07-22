from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.config import Settings
from app.error_archive import install_error_management
from app.errors import ApiException
from app.main import _set_experiment_pc_usage_context
from app.usage_archive import (
    UsageArchive,
    UsageArchiveSettings,
    record_background_usage,
    set_usage_context,
)


def test_usage_archive_records_filters_and_reads_event(tmp_path: Path) -> None:
    archive = UsageArchive(UsageArchiveSettings(root=tmp_path / "usage"))
    success = archive.record(
        project="XRD",
        action="보고서 생성 요청",
        result="success",
        status_code=202,
        duration_ms=1250,
        method="POST",
        endpoint="/api/v1/xrd/report",
        job_id="job-xrd-1",
        request_number="REQ-2026-001",
        client="127.0.0.1",
        forwarded_for="203.0.113.17, 10.0.0.8",
        real_ip="10.0.0.8",
        client_type="C#/.NET",
        client_version="2.1.0",
        source_host_name="LAB-XRD-01",
        file_relative_path="raw/Mix2.txt",
        file_name="Mix2.txt",
        file_size_bytes=532481,
        file_sha256="a" * 64,
    )
    archive.record(
        project="TEM",
        action="업로드 완료 및 보고서 생성",
        result="failure",
        status_code=422,
        duration_ms=80,
        method="POST",
        endpoint="/api/v1/tem/upload-sessions/upload-1/complete",
        job_id="job-tem-1",
    )
    completed = record_background_usage(
        archive,
        project="XRD",
        action="보고서 생성 완료",
        result="success",
        duration_ms=3200,
        job_id="job-xrd-1",
        endpoint="/background/xrd/report/jobs/job-xrd-1",
        experiment_code="XRD",
        file_name="xrd-report.html",
        file_size_bytes=4096,
        client_context={
            "client": "127.0.0.1",
            "forwarded_for": "203.0.113.17, 10.0.0.8",
            "real_ip": "10.0.0.8",
            "client_type": "C#/.NET",
            "client_name": "RIST XRD Uploader",
            "client_version": "2.1.0",
            "source_host_name": "LAB-XRD-01",
            "request_id": "request-xrd-1",
        },
    )

    assert archive.get(str(success["eventId"]))["requestNumber"] == "REQ-2026-001"
    assert [item["jobId"] for item in archive.list(project="XRD")] == [
        "job-xrd-1",
        "job-xrd-1",
    ]
    assert [item["jobId"] for item in archive.list(result="failure")] == ["job-tem-1"]
    assert [item["jobId"] for item in archive.list(query="REQ-2026-001")] == [
        "job-xrd-1"
    ]
    assert len(archive.list(query="LAB-XRD-01")) == 2
    assert [item["jobId"] for item in archive.list(query="raw/Mix2.txt")] == [
        "job-xrd-1"
    ]
    stored = archive.get(str(success["eventId"]))
    assert stored["clientApplication"] == {
        "type": "C#/.NET",
        "name": None,
        "version": "2.1.0",
        "sourceHostName": "LAB-XRD-01",
    }
    assert stored["file"]["sizeBytes"] == 532481
    assert stored["request"] == {
        "method": "POST",
        "endpoint": "/api/v1/xrd/report",
        "routePath": None,
        "client": "127.0.0.1",
        "clientIp": "203.0.113.17",
        "peerIp": "127.0.0.1",
        "forwardedFor": "203.0.113.17, 10.0.0.8",
        "realIp": "10.0.0.8",
        "userAgent": None,
    }
    assert success["activityType"] == "REPORT_REQUEST"
    assert completed is not None
    assert completed["activityType"] == "REPORT_COMPLETE"
    assert completed["clientApplication"]["sourceHostName"] == "LAB-XRD-01"
    assert completed["request"]["clientIp"] == "203.0.113.17"
    assert completed["requestId"] == "request-xrd-1"
    assert archive.list(activity_type="REPORT_COMPLETE") == [completed]


def test_usage_middleware_records_user_actions_and_skips_repeated_requests(
    tmp_path: Path,
) -> None:
    app = FastAPI()
    settings = Settings(
        storage_root=tmp_path / "jobs",
        error_archive_root=tmp_path / "errors",
        usage_log_root=tmp_path / "usage",
    )
    install_error_management(app, settings)

    @app.get("/ftir")
    def ftir_page() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/v1/ftir/report")
    def create_report(request: Request) -> JSONResponse:
        set_usage_context(
            request,
            job_id="job-ftir-1",
            request_number="REQ-FTIR-1",
            experiment_code="FT-IR",
            equipment_code="FTIR-01",
            operator_id="operator-1",
        )
        return JSONResponse({"jobId": "job-ftir-1"}, status_code=202)

    @app.post("/api/v1/tem/report")
    def failed_report() -> JSONResponse:
        return JSONResponse({"message": "invalid"}, status_code=422)

    @app.post("/api/v1/tem/upload-sessions/upload-1/chunks")
    def upload_chunk() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/ftir/report/jobs/job-ftir-1")
    def report_status() -> dict[str, str]:
        return {"status": "running"}

    client = TestClient(app)
    assert client.get("/ftir").status_code == 200
    assert client.post("/api/v1/ftir/report").status_code == 202
    assert client.post("/api/v1/tem/report").status_code == 422
    assert client.post("/api/v1/tem/upload-sessions/upload-1/chunks").status_code == 200
    assert client.get("/api/v1/ftir/report/jobs/job-ftir-1").status_code == 200

    listing = client.get("/api/v1/usage-events").json()
    assert listing["count"] == 3
    by_action = {item["action"]: item for item in listing["items"]}
    assert by_action["작업 화면 조회"]["project"] == "FT-IR"
    ftir_report = next(
        item
        for item in listing["items"]
        if item["project"] == "FT-IR" and item["action"] == "보고서 생성 요청"
    )
    assert ftir_report["jobId"] == "job-ftir-1"
    assert ftir_report["requestNumber"] == "REQ-FTIR-1"
    assert ftir_report["result"] == "success"
    assert ftir_report["operatorId"] == "operator-1"
    assert ftir_report["equipmentCode"] == "FTIR-01"
    assert ftir_report["experimentCode"] == "FT-IR"
    assert ftir_report["statusCode"] == 202
    assert ftir_report["durationMs"] >= 0
    assert ftir_report["activityType"] == "REPORT_REQUEST"
    screen_views = client.get(
        "/api/v1/usage-events?activityType=SCREEN_VIEW"
    ).json()["items"]
    assert len(screen_views) == 1
    assert screen_views[0]["action"] == "작업 화면 조회"
    tem_failures = [
        item
        for item in listing["items"]
        if item["project"] == "TEM" and item["result"] == "failure"
    ]
    assert len(tem_failures) == 1

    event_id = str(tem_failures[0]["eventId"])
    assert client.get(f"/api/v1/usage-events/{event_id}").json()["statusCode"] == 422


def test_operations_console_has_usage_and_error_tabs(tmp_path: Path) -> None:
    app = FastAPI()
    settings = Settings(
        storage_root=tmp_path / "jobs",
        error_archive_root=tmp_path / "errors",
        usage_log_root=tmp_path / "usage",
    )
    install_error_management(app, settings)
    client = TestClient(app)

    operations = client.get("/operations")
    errors = client.get("/errors")

    assert operations.status_code == 200
    assert 'data-default-tab="usage"' in operations.text
    assert "사용 기록" in operations.text
    assert "오류 기록" in operations.text
    assert "클라이언트" in operations.text
    assert "전체 기록 유형" in operations.text
    assert "보고서 완료" in operations.text
    assert "클라이언트 / 접속 위치" in operations.text
    assert "접속 IP" in operations.text
    assert 'class="detail-backdrop"' in operations.text
    assert 'href="/admin/users"' in operations.text
    assert "회원 관리" in operations.text
    assert 'role="dialog"' in operations.text
    assert "function closeDetail()" in operations.text
    assert "position:fixed" in operations.text
    assert errors.status_code == 200
    assert 'data-default-tab="errors"' in errors.text
    assert client.get("/api/v1/usage-events").json()["count"] == 0

    app.state.usage_archive.record(
        project="EDGE",
        action="GET 요청",
        result="success",
        status_code=200,
        duration_ms=10,
        method="GET",
        endpoint="/operations/",
    )
    assert client.get("/api/v1/usage-events").json()["count"] == 0


def test_csharp_file_upload_usage_includes_job_client_and_file_context(
    tmp_path: Path,
) -> None:
    app = FastAPI()
    settings = Settings(
        storage_root=tmp_path / "jobs",
        error_archive_root=tmp_path / "errors",
        usage_log_root=tmp_path / "usage",
    )
    install_error_management(app, settings)

    class FakeDatabase:
        @staticmethod
        def fetch_job(job_id: str) -> dict[str, object]:
            return {
                "job_id": job_id,
                "request_number": "REQ-CSHARP-001",
                "experiment_code": "XRD",
                "equipment_code": "XRD-01",
                "operator_id": "operator-7",
                "source_host_name": "LAB-PC-XRD-07",
                "client_version": "1.4.2",
            }

    @app.post("/api/v1/jobs/{job_id}/files")
    def upload_from_csharp(job_id: str, request: Request) -> JSONResponse:
        _set_experiment_pc_usage_context(
            request,
            FakeDatabase(),  # type: ignore[arg-type]
            job_id,
            file_relative_path="raw/sample.txt",
            file_name="sample.txt",
            file_size_bytes=128,
            file_sha256="b" * 64,
        )
        return JSONResponse({"status": "UPLOADED"}, status_code=201)

    @app.put("/api/v1/jobs/{job_id}/files/{relative_path:path}")
    def failed_upload_from_csharp(
        job_id: str, relative_path: str, request: Request
    ) -> JSONResponse:
        _set_experiment_pc_usage_context(
            request,
            FakeDatabase(),  # type: ignore[arg-type]
            job_id,
            file_relative_path=relative_path,
            file_name=relative_path.rsplit("/", 1)[-1],
            file_size_bytes=256,
            file_sha256="c" * 64,
        )
        raise ApiException(422, "FILE_HASH_MISMATCH", "파일 해시 불일치")

    client = TestClient(app)
    response = client.post(
        "/api/v1/jobs/job-csharp-1/files",
        headers={
            "X-Request-Id": "request-csharp-1",
            "X-Client-Type": "C#/.NET",
            "X-Client-Name": "RIST XRD Uploader",
        },
    )
    assert response.status_code == 201

    item = client.get("/api/v1/usage-events?q=sample.txt").json()["items"][0]
    assert item["project"] == "XRD"
    assert item["action"] == "파일 업로드"
    assert item["activityType"] == "FILE_TRANSFER"
    assert item["jobId"] == "job-csharp-1"
    assert item["requestNumber"] == "REQ-CSHARP-001"
    assert item["clientApplication"] == {
        "type": "C#/.NET",
        "name": "RIST XRD Uploader",
        "version": "1.4.2",
        "sourceHostName": "LAB-PC-XRD-07",
    }
    assert item["file"] == {
        "relativePath": "raw/sample.txt",
        "name": "sample.txt",
        "sizeBytes": 128,
        "sha256": "b" * 64,
    }

    failed = client.put(
        "/api/v1/jobs/job-csharp-1/files/raw/broken.txt",
        headers={
            "X-Request-Id": "request-csharp-2",
            "X-Client-Type": "C#/.NET",
            "X-Client-Name": "RIST XRD Uploader",
        },
    )
    assert failed.status_code == 422
    error_item = client.get("/api/v1/errors?q=broken.txt").json()["items"][0]
    assert error_item["project"] == "XRD"
    assert error_item["clientApplication"]["type"] == "C#/.NET"
    assert error_item["file"]["relativePath"] == "raw/broken.txt"

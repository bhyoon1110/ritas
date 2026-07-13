from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.config import Settings
from app.error_archive import install_error_management
from app.usage_archive import UsageArchive, UsageArchiveSettings, set_usage_context


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

    assert archive.get(str(success["eventId"]))["requestNumber"] == "REQ-2026-001"
    assert [item["jobId"] for item in archive.list(project="XRD")] == ["job-xrd-1"]
    assert [item["jobId"] for item in archive.list(result="failure")] == ["job-tem-1"]
    assert [item["jobId"] for item in archive.list(query="REQ-2026-001")] == [
        "job-xrd-1"
    ]


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
    assert errors.status_code == 200
    assert 'data-default-tab="errors"' in errors.text

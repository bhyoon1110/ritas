from __future__ import annotations

import io
from pathlib import Path
import zipfile

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.config import Settings
from app.error_archive import (
    ErrorArchive,
    ErrorArchiveSettings,
    install_error_management,
    record_background_error,
)
from app.errors import ApiException


def test_error_archive_persists_metadata_trace_and_files(tmp_path: Path) -> None:
    source = tmp_path / "input"
    source.mkdir()
    (source / "분석 원본.txt").write_text("raw-data", encoding="utf-8")
    archive = ErrorArchive(ErrorArchiveSettings(root=tmp_path / "errors"))

    try:
        raise RuntimeError("renderer failed")
    except RuntimeError as exc:
        event = archive.record(
            project="TEM",
            code="TEM_REPORT_BUILD_FAILED",
            message="보고서 생성 실패",
            exception=exc,
            job_id="job-1",
            source_paths=[source],
            file_blobs=[("extra/상태.json", b"{}")],
        )

    stored = archive.get(str(event["eventId"]))
    assert stored["project"] == "TEM"
    assert stored["jobId"] == "job-1"
    assert "RuntimeError: renderer failed" in stored["traceback"]
    assert {item["sourceName"] for item in stored["files"]} == {
        "분석 원본.txt",
        "상태.json",
    }
    assert archive.file_path(str(event["eventId"]), "input/분석 원본.txt").read_text(
        encoding="utf-8"
    ) == "raw-data"


def test_error_console_api_filters_downloads_resolves_and_deletes(tmp_path: Path) -> None:
    app = FastAPI()
    settings = Settings(
        storage_root=tmp_path / "jobs",
        error_archive_root=tmp_path / "errors",
    )
    archive = install_error_management(app, settings)
    event = archive.record(
        project="XRD",
        code="XRD_BAD_INPUT",
        message="PDF 파일 오류",
        exception=RuntimeError("broken PDF"),
        file_blobs=[("ICDD/card.pdf", b"pdf")],
    )
    event_id = str(event["eventId"])

    client = TestClient(app)
    console_response = client.get("/errors")
    assert console_response.status_code == 200
    assert "고객 코멘트" in console_response.text
    listing = client.get("/api/v1/errors", params={"project": "XRD"}).json()
    assert listing["count"] == 1
    assert listing["items"][0]["eventId"] == event_id
    assert listing["items"][0]["comments"] == []

    comment = client.post(
        f"/api/v1/errors/{event_id}/comments",
        json={"author": "고객 A", "content": "Windows에서 같은 PDF를 올리면 재현됩니다."},
    )
    assert comment.status_code == 201
    assert comment.json()["author"] == "고객 A"
    assert comment.json()["content"] == "Windows에서 같은 PDF를 올리면 재현됩니다."
    detail = client.get(f"/api/v1/errors/{event_id}").json()
    assert detail["comments"][0]["source"] == "customer"
    feedback = client.get(f"/error-feedback/{event_id}")
    assert feedback.status_code == 200
    assert "Windows에서 같은 PDF를 올리면 재현됩니다." in feedback.text

    file_response = client.get(f"/api/v1/errors/{event_id}/files/ICDD/card.pdf")
    assert file_response.content == b"pdf"
    zip_response = client.get(f"/api/v1/errors/{event_id}/archive")
    with zipfile.ZipFile(io.BytesIO(zip_response.content)) as bundle:
        assert "event.json" in bundle.namelist()
        assert "files/ICDD/card.pdf" in bundle.namelist()

    resolved = client.patch(
        f"/api/v1/errors/{event_id}", json={"status": "resolved"}
    ).json()
    assert resolved["status"] == "resolved"
    persisted = (tmp_path / "errors" / event_id / "event.json").read_text(
        encoding="utf-8"
    )
    assert '"traceback"' not in persisted
    assert client.delete(f"/api/v1/errors/{event_id}").status_code == 204
    assert client.get(f"/api/v1/errors/{event_id}").status_code == 404


def test_error_console_api_paginates_results(tmp_path: Path) -> None:
    app = FastAPI()
    settings = Settings(
        storage_root=tmp_path / "jobs",
        error_archive_root=tmp_path / "errors",
    )
    archive = install_error_management(app, settings)
    for index in range(3):
        archive.record(
            project="TEM",
            code=f"TEM_TEST_{index}",
            message=f"test error {index}",
        )

    response = TestClient(app).get(
        "/api/v1/errors", params={"page": 2, "pageSize": 1}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert data["total"] == 3
    assert data["page"] == 2
    assert data["pageSize"] == 1
    assert data["totalPages"] == 3


def test_error_archive_supports_multi_filters_dates_sorting_and_pages(
    tmp_path: Path,
) -> None:
    archive = ErrorArchive(ErrorArchiveSettings(root=tmp_path / "errors"))
    xrd = archive.record(
        project="XRD",
        code="XRD_BAD_INPUT",
        message="XRD input failed",
    )
    tem = archive.record(
        project="TEM",
        code="TEM_BAD_INPUT",
        message="TEM input failed",
    )
    archive.record(
        project="FT-IR",
        code="FTIR_BAD_INPUT",
        message="FT-IR input failed",
    )
    archive.update_status(str(tem["eventId"]), "resolved")
    event_date = str(xrd["timestamp"])[:10]

    first_page, total = archive.list_page(
        project="XRD,TEM",
        status="open,resolved",
        date_from=event_date,
        date_to=event_date,
        sort_by="project",
        sort_dir="asc",
        page=1,
        page_size=1,
    )
    second_page, second_total = archive.list_page(
        project="XRD,TEM",
        status="open,resolved",
        date_from=event_date,
        date_to=event_date,
        sort_by="project",
        sort_dir="asc",
        page=2,
        page_size=1,
    )

    assert total == second_total == 2
    assert first_page[0]["project"] == "TEM"
    assert first_page[0]["status"] == "resolved"
    assert second_page[0]["project"] == "XRD"


def test_installed_handler_records_request_files_and_returns_event_header(
    tmp_path: Path,
) -> None:
    app = FastAPI()
    settings = Settings(
        storage_root=tmp_path / "jobs",
        error_archive_root=tmp_path / "errors",
    )
    install_error_management(app, settings)
    transient = tmp_path / "transient-upload"
    transient.mkdir()
    (transient / "sample.dpt").write_bytes(b"1 2")

    @app.get("/api/v1/ftir/fail")
    def fail(request: Request) -> None:
        request.state.error_source_paths = [transient]
        request.state.error_cleanup_paths = [transient]
        raise ApiException(422, "FTIR_TEST_FAILURE", "분석 실패")

    client = TestClient(app)
    response = client.get("/api/v1/ftir/fail")
    assert response.status_code == 422
    event_id = response.headers["X-Error-Event-Id"]
    assert response.headers["X-Error-Comment-Url"] == f"/error-feedback/{event_id}"
    assert response.json()["errorEventId"] == event_id
    assert response.json()["errorFeedbackUrl"] == f"/error-feedback/{event_id}"
    event = client.get(f"/api/v1/errors/{event_id}").json()
    assert event["project"] == "FT-IR"
    assert event["files"][0]["sourceName"] == "sample.dpt"
    assert not transient.exists()


def test_background_error_helper_keeps_input_bundle(tmp_path: Path) -> None:
    source = tmp_path / "bundle"
    source.mkdir()
    (source / "raw.txt").write_text("x", encoding="utf-8")
    archive = ErrorArchive(ErrorArchiveSettings(root=tmp_path / "errors"))

    event_id = record_background_error(
        archive,
        project="XRD",
        code="XRD_REPORT_BUILD_FAILED",
        message="failed",
        job_id="job-xrd",
        source_paths=[source],
    )

    assert event_id is not None
    event = archive.get(event_id)
    assert event["project"] == "XRD"
    assert event["files"][0]["sourceName"] == "raw.txt"

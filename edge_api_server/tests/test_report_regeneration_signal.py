from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.errors import ApiException
from app.models import RegenerateReportSignalRequest
from app.service import EdgeService


class FakeReportDatabase:
    def __init__(self) -> None:
        self.report = {
            "report_id": "report-1",
            "source_job_id": "job-1",
            "deleted_at": None,
        }
        self.idempotency: dict[tuple[str, str], dict] = {}

    def fetch_report_run(self, report_id: str) -> dict | None:
        if report_id == self.report["report_id"]:
            return dict(self.report)
        return None

    def fetch_idempotency(self, endpoint: str, key: str) -> dict | None:
        return self.idempotency.get((endpoint, key))

    def insert_idempotency(
        self,
        endpoint: str,
        key: str,
        request_hash: str,
        response_status: int,
        response: dict,
        created_at: str,
    ) -> None:
        self.idempotency[(endpoint, key)] = {
            "request_hash": request_hash,
            "response_status": response_status,
            "response": dict(response),
            "created_at": created_at,
        }


def create_service(tmp_path: Path) -> tuple[EdgeService, FakeReportDatabase]:
    database = FakeReportDatabase()
    service = EdgeService(
        Settings(storage_root=tmp_path / "jobs"),
        database,  # type: ignore[arg-type]
    )
    return service, database


def test_receives_regeneration_signal_without_queueing_work(tmp_path: Path) -> None:
    service, database = create_service(tmp_path)
    request = RegenerateReportSignalRequest(
        requestedBy="spring-boot",
        reason="태블릿 재생성 요청",
    )

    status, response = service.receive_report_regeneration_signal(
        "report-1", request, "signal-key-1"
    )

    assert status == 202
    assert response["reportId"] == "report-1"
    assert response["sourceJobId"] == "job-1"
    assert response["status"] == "RECEIVED"
    assert response["executionQueued"] is False
    assert len(database.idempotency) == 1

    repeated_status, repeated_response = service.receive_report_regeneration_signal(
        "report-1", request, "signal-key-1"
    )
    assert repeated_status == 202
    assert repeated_response == response


def test_rejects_missing_report_and_changed_idempotent_request(tmp_path: Path) -> None:
    service, database = create_service(tmp_path)

    with pytest.raises(ApiException) as missing:
        service.receive_report_regeneration_signal(
            "missing-report", RegenerateReportSignalRequest(), "missing-key"
        )
    assert missing.value.status_code == 404
    assert missing.value.code == "REPORT_NOT_FOUND"

    request = RegenerateReportSignalRequest(reason="first")
    service.receive_report_regeneration_signal("report-1", request, "signal-key-2")
    with pytest.raises(ApiException) as reused:
        service.receive_report_regeneration_signal(
            "report-1",
            RegenerateReportSignalRequest(reason="changed"),
            "signal-key-2",
        )
    assert reused.value.status_code == 409
    assert reused.value.code == "IDEMPOTENCY_KEY_REUSED"

    database.report["deleted_at"] = "2026-07-23T12:00:00+09:00"
    with pytest.raises(ApiException) as deleted:
        service.receive_report_regeneration_signal(
            "report-1", RegenerateReportSignalRequest(), "deleted-key"
        )
    assert deleted.value.code == "REPORT_NOT_FOUND"

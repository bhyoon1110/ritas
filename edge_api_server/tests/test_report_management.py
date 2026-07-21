from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.report_management import _decorate_row, _trash_report, router


KST = timezone(timedelta(hours=9))


def settings(storage_root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        storage_root=storage_root,
        report_test_retention_days=7,
        report_failed_retention_days=30,
        report_completed_retention_days=90,
        report_trash_retention_days=7,
    )


def old_time(days: int) -> str:
    return (datetime.now(KST) - timedelta(days=days)).isoformat()


def report_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "report_id": "report-1",
        "generated_at": old_time(10),
        "updated_at": old_time(10),
        "transfer_status": "NOT_QUEUED",
        "is_test": True,
        "pinned": False,
        "deleted_at": None,
        "artifacts": [],
    }
    row.update(overrides)
    return row


class TrashDatabase:
    def __init__(self, *, shared_references: int = 0) -> None:
        self.shared_references = shared_references
        self.marked: dict[str, object] | None = None

    def count_active_artifact_references(
        self,
        _relative_path: str,
        *,
        excluding_report_id: str,
    ) -> int:
        assert excluding_report_id == "report-1"
        return self.shared_references

    def mark_report_trashed(self, report_id: str, **values: object) -> None:
        self.marked = {"report_id": report_id, **values}


def test_report_management_console_serves_bundled_ui() -> None:
    app = FastAPI()
    app.include_router(router)

    response = TestClient(app).get("/report-management")

    assert response.status_code == 200
    assert "보고서/파일 관리" in response.text
    assert "선택 항목 휴지통 이동" in response.text
    assert "보존 정책 설정" in response.text
    assert "/api/v1/report-management/summary" in response.text


def test_retention_policy_only_selects_expired_inactive_reports(tmp_path: Path) -> None:
    configured = settings(tmp_path)

    expired_test = _decorate_row(report_row(), configured, include_artifacts=False)
    active = _decorate_row(
        report_row(transfer_status="PROCESSING"),
        configured,
        include_artifacts=False,
    )
    pinned = _decorate_row(
        report_row(pinned=True),
        configured,
        include_artifacts=False,
    )
    completed_too_recently = _decorate_row(
        report_row(
            is_test=False,
            transfer_status="COMPLETED",
            transfer_completed_at=old_time(30),
        ),
        configured,
        include_artifacts=False,
    )

    assert expired_test["cleanupEligible"] is True
    assert expired_test["retentionPolicy"] == "미전송 테스트"
    assert active["cleanupEligible"] is False
    assert active["deleteBlockedReason"] == "활성 전송 작업"
    assert pinned["cleanupEligible"] is False
    assert pinned["deleteBlockedReason"] == "보존 지정"
    assert completed_too_recently["cleanupEligible"] is False


def test_disabled_policy_keeps_manual_cleanup_but_stops_automatic_cleanup(
    tmp_path: Path,
) -> None:
    configured = settings(tmp_path)
    policies = {
        "UNSENT_TEST": {
            "retention_days": 7,
            "auto_cleanup_enabled": False,
            "description": "test",
        }
    }

    expired_test = _decorate_row(
        report_row(),
        configured,
        include_artifacts=False,
        policies=policies,
    )

    assert expired_test["cleanupEligible"] is True
    assert expired_test["autoCleanupEligible"] is False
    assert expired_test["autoCleanupEnabled"] is False


class PolicyDatabase:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, object]] = {}

    def ensure_report_retention_policies(
        self,
        defaults: dict[str, dict[str, object]],
    ) -> None:
        for key, value in defaults.items():
            self.rows.setdefault(
                key,
                {
                    "policy_key": key,
                    **value,
                    "updated_at": None,
                    "updated_by": None,
                },
            )

    def list_report_retention_policies(self) -> list[dict[str, object]]:
        return list(self.rows.values())

    def update_report_retention_policies(
        self,
        policies: dict[str, dict[str, object]],
        *,
        actor: str,
    ) -> None:
        for key, value in policies.items():
            self.rows[key].update(value)
            self.rows[key]["updated_by"] = actor


def test_retention_policy_api_reads_and_updates_all_policies(tmp_path: Path) -> None:
    app = FastAPI()
    database = PolicyDatabase()
    app.state.settings = settings(tmp_path)
    app.state.database = database
    app.include_router(router)
    client = TestClient(app)

    response = client.get("/api/v1/report-management/policies")

    assert response.status_code == 200
    assert [item["policyKey"] for item in response.json()["policies"]] == [
        "UNSENT_TEST",
        "FAILED_OR_CANCELLED",
        "COMPLETED",
        "TRASH",
    ]

    response = client.put(
        "/api/v1/report-management/policies",
        json={
            "actor": "admin-user",
            "policies": {
                "unsent_test": {
                    "retentionDays": 14,
                    "autoCleanupEnabled": False,
                },
                "FAILED_OR_CANCELLED": {
                    "retentionDays": 45,
                    "autoCleanupEnabled": True,
                },
                "COMPLETED": {
                    "retentionDays": 120,
                    "autoCleanupEnabled": True,
                },
                "TRASH": {
                    "retentionDays": 10,
                    "autoCleanupEnabled": False,
                },
            },
        },
    )

    assert response.status_code == 200
    items = {item["policyKey"]: item for item in response.json()["policies"]}
    assert items["UNSENT_TEST"]["retentionDays"] == 14
    assert items["UNSENT_TEST"]["autoCleanupEnabled"] is False
    assert items["UNSENT_TEST"]["updatedBy"] == "admin-user"
    assert items["TRASH"]["retentionDays"] == 10
    assert items["TRASH"]["autoCleanupEnabled"] is False


def test_retention_policy_api_requires_all_policy_keys(tmp_path: Path) -> None:
    app = FastAPI()
    app.state.settings = settings(tmp_path)
    app.state.database = PolicyDatabase()
    app.include_router(router)

    response = TestClient(app).put(
        "/api/v1/report-management/policies",
        json={
            "policies": {
                "UNSENT_TEST": {
                    "retentionDays": 7,
                    "autoCleanupEnabled": True,
                }
            }
        },
    )

    assert response.status_code == 422
    assert "누락" in response.json()["detail"]


def test_trash_report_moves_artifact_and_records_sha_state(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    artifact_path = tmp_path / "web-reports" / "TEM" / "report-1" / "report.pptx"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(b"report")
    digest = hashlib.sha256(b"report").hexdigest()
    row = report_row(
        artifacts=[
            {
                "artifact_id": "artifact-1",
                "artifact_type": "PPTX",
                "relative_path": "web-reports/TEM/report-1/report.pptx",
                "file_name": "report.pptx",
                "size_bytes": 6,
                "sha256": digest,
                "deleted_at": None,
            }
        ]
    )
    database = TrashDatabase()

    result = _trash_report(
        settings=configured,
        database=database,  # type: ignore[arg-type]
        row=row,
        actor="tester",
        reason="test cleanup",
    )

    assert result == {
        "reportId": "report-1",
        "movedFiles": 1,
        "retainedSharedFiles": 0,
        "trashed": True,
    }
    assert not artifact_path.exists()
    assert database.marked is not None
    artifact_id, trash_relative_path = database.marked["artifacts"][0]  # type: ignore[index]
    assert artifact_id == "artifact-1"
    assert (tmp_path / trash_relative_path).read_bytes() == b"report"


def test_trash_report_keeps_file_referenced_by_another_report(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    artifact_path = tmp_path / "shared" / "report.pdf"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(b"shared")
    row = report_row(
        artifacts=[
            {
                "artifact_id": "artifact-1",
                "artifact_type": "PDF",
                "relative_path": "shared/report.pdf",
                "file_name": "report.pdf",
                "size_bytes": 6,
                "sha256": hashlib.sha256(b"shared").hexdigest(),
                "deleted_at": None,
            }
        ]
    )
    database = TrashDatabase(shared_references=1)

    result = _trash_report(
        settings=configured,
        database=database,  # type: ignore[arg-type]
        row=row,
        actor="tester",
        reason="shared cleanup",
    )

    assert artifact_path.read_bytes() == b"shared"
    assert result["movedFiles"] == 0
    assert result["retainedSharedFiles"] == 1
    assert database.marked is not None
    assert database.marked["artifacts"] == [("artifact-1", "")]


def test_trash_report_rejects_active_transfer_without_moving_files(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    artifact_path = tmp_path / "active" / "report.zip"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(b"active")
    row = report_row(
        transfer_status="PENDING",
        artifacts=[
            {
                "artifact_id": "artifact-1",
                "artifact_type": "ZIP",
                "relative_path": "active/report.zip",
                "file_name": "report.zip",
                "size_bytes": 6,
                "sha256": hashlib.sha256(b"active").hexdigest(),
                "deleted_at": None,
            }
        ],
    )

    with pytest.raises(HTTPException) as captured:
        _trash_report(
            settings=configured,
            database=TrashDatabase(),  # type: ignore[arg-type]
            row=row,
            actor="tester",
            reason="must be blocked",
        )

    assert captured.value.status_code == 409
    assert artifact_path.read_bytes() == b"active"

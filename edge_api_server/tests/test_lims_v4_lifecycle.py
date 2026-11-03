from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from app.config import Settings
from app.database import Database, DatabaseConfig
from app.report_management import _decorate_row, _purge_old_trash, _retention_policy_response, _trash_report
from app.report_queue import ReportQueueError, enqueue_report_package, validate_mail_attachment_sizes
from tests.test_report_management import PolicyDatabase, report_row
from tests.test_report_queue import CapturingDatabase, write_zip


def test_default_policy_expires_after_24_hours_without_extra_trash_days(tmp_path):
    settings = Settings(storage_root=tmp_path)
    assert settings.report_completed_retention_hours == 24
    assert settings.report_trash_retention_hours == 0
    old = datetime.now(timezone.utc) - timedelta(hours=25)
    recent = datetime.now(timezone.utc) - timedelta(hours=23)
    row = report_row(transfer_status="RETRY_WAIT", document_status="COMPLETED", document_completed_at=old.isoformat(), mail_status="PENDING")
    assert _decorate_row(row, settings, include_artifacts=False)["autoCleanupEligible"]
    for override in ({"document_completed_at": recent.isoformat()}, {"pinned": True}, {"document_status": "PENDING"}):
        assert not _decorate_row({**row, **override}, settings, include_artifacts=False)["autoCleanupEligible"]
    response = _retention_policy_response(settings, PolicyDatabase())
    policies = {p["policyKey"]: p for p in response["policies"]}
    assert policies["COMPLETED"]["effectiveRetentionHours"] == 24
    assert policies["COMPLETED"]["source"] == "environment"


def test_raw_and_zip_are_purged_after_durable_ingestion_while_mail_waits(tmp_path):
    settings = Settings(storage_root=tmp_path)
    raw = tmp_path / "sample.raw"
    package = tmp_path / "report.zip"
    raw.write_bytes(b"original")
    package.write_bytes(b"archive")
    artifacts = [{"artifact_id": name, "relative_path": path.name, "size_bytes": path.stat().st_size}
                 for name, path in (("raw", raw), ("zip", package))]
    row = report_row(transfer_status="RETRY_WAIT", document_status="COMPLETED", mail_status="PENDING",
                     document_completed_at=(datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(), artifacts=artifacts)
    class DB:
        def count_active_artifact_references(self, *args, **kwargs): return 0
        def mark_report_trashed(self, *args, **kwargs): row["deleted_at"] = "now"
        def fetch_report_management(self, *args): return row
    db = DB()
    assert _trash_report(settings=settings, database=db, row=row, actor="test", reason="expired")["movedFiles"] == 2
    assert not raw.exists() and not package.exists()
    assert _purge_old_trash(settings, database=db) == 1
    assert not (tmp_path / ".report-trash" / "report-1").exists()
    assert row["mail_status"] == "PENDING"


def test_shared_raw_is_retained_until_last_active_report_reference(tmp_path):
    from tests.test_report_management import TrashDatabase
    settings = Settings(storage_root=tmp_path)
    raw = tmp_path / "shared.raw"
    raw.write_bytes(b"shared")
    row = report_row(transfer_status="COMPLETED", document_status="COMPLETED",
                     document_completed_at=(datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
                     artifacts=[{"artifact_id":"raw", "relative_path":"shared.raw", "size_bytes":6}])
    result = _trash_report(settings=settings, database=TrashDatabase(shared_references=1), row=row, actor="test", reason="expired")
    assert result["retainedSharedFiles"] == 1 and raw.exists()


def test_attachment_caps_check_single_and_total_raw_bytes(tmp_path):
    settings = replace(Settings(storage_root=tmp_path), report_mail_max_attachment_bytes=10, report_mail_max_total_attachment_bytes=15)
    validate_mail_attachment_sizes(settings, [10, 5])
    with pytest.raises(ReportQueueError, match="단일"):
        validate_mail_attachment_sizes(settings, [11])
    with pytest.raises(ReportQueueError, match="합계"):
        validate_mail_attachment_sizes(settings, [8, 8])
    validate_mail_attachment_sizes(Settings(storage_root=tmp_path), [2**40])


def test_oversize_rejected_before_any_queue_write(tmp_path):
    package = tmp_path / "report.zip"
    write_zip(package)
    db = CapturingDatabase()
    with pytest.raises(ReportQueueError) as error:
        enqueue_report_package(settings=replace(Settings(storage_root=tmp_path), report_mail_max_attachment_bytes=1),
                               database=db, report_id="test", package_path=package, source_job_id=None,
                               request_number="R1", experiment_code="XRD", equipment_code="X1", operator_id="tester",
                               report_options={"email": {"subject":"Result", "body":"Result"}})
    assert error.value.code == "REPORT_MAIL_ATTACHMENT_TOO_LARGE"
    assert not db.values and not db.report_runs


@pytest.mark.parametrize("value", ["-1", "nan", "inf", "87601"])
def test_invalid_retention_hours_fail_configuration(monkeypatch, value):
    monkeypatch.setenv("RIST_REPORT_COMPLETED_RETENTION_HOURS", value)
    with pytest.raises(ValueError): Settings.from_env()


def test_mail_retry_is_atomic_and_preserves_checkpoint_and_attempt_history(mariadb, tmp_path):
    db = Database(DatabaseConfig(**mariadb))
    package = tmp_path / "report.zip"
    write_zip(package)
    enqueue_report_package(settings=Settings(storage_root=tmp_path), database=db, report_id="retry-test", package_path=package,
                           source_job_id=None, request_number="R1", experiment_code="XRD", equipment_code="X1", operator_id="tester")
    checkpoint = {"phase":"MAIL_PENDING", "mailStatus":"FAILED", "requestId":"stable-id", "fileNo":55001, "axDocNum":1001}
    with db.transaction() as c:
        c.execute("UPDATE report_transfers SET status='FAILED', mail_status='FAILED', document_status='COMPLETED', "
                  "document_completed_at=CURRENT_TIMESTAMP(6), attempt_count=37, failure_count=5, delivery_state_json=? WHERE report_id=?",
                  (json.dumps(checkpoint), "retry-test"))
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(db.retry_report_transfer, ["retry-test", "retry-test"])) == [False, True]
        row = db.fetch_report_transfer_for_report("retry-test")
        state = json.loads(row["delivery_state_json"])
        assert row["status"] == "PENDING" and row["attempt_count"] == 37 and row["failure_count"] == 0
        assert state["requestId"] == "stable-id" and state["fileNo"] == 55001
        assert state["mailRetryRequested"] is True and state["mailRetryGeneration"] == 1
        assert row["document_completed_at"] is not None
    finally:
        db.close()


@pytest.mark.parametrize("case,expected", [("missing", "SSO_LINK_REQUIRED"), ("expired", "SSO_REAUTH_REQUIRED"), ("role", "REPORT_SEND_PERMISSION_REQUIRED")])
def test_manual_retry_requires_recent_sso_and_sender_permission(tmp_path, case, expected):
    from datetime import datetime
    from unittest.mock import Mock
    from app.errors import ApiException
    from app.report_management import retry_transfer
    from tests.test_auth import _context, _request
    context = _context(roles=frozenset({"ADMIN", "REPORT_SENDER"}),
                       sso_identity={"subject":"operator"}, sso_authenticated_at=datetime.utcnow())
    if case == "missing": context = replace(context, sso_identity=None)
    if case == "expired": context = replace(context, sso_authenticated_at=datetime.utcnow() - timedelta(hours=1))
    if case == "role": context = replace(context, roles=frozenset({"ADMIN"}))
    request = _request(tmp_path, context)
    db = Mock()
    db.fetch_report_run.return_value = {"request_number":"R1", "experiment_code":"FTIR", "equipment_code":"F1"}
    request.app.state.database = db
    with pytest.raises(ApiException) as failure:
        retry_transfer(request, "report")
    assert failure.value.code == expected
    db.retry_report_transfer.assert_not_called()

from __future__ import annotations

import json
import hashlib
from contextlib import contextmanager
from itertools import count
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from app.database import Database
from app.errors import ApiException
from app.lims_requests import LimsRequestsClient, NUMBER_FIELDS, TEXT_FIELDS, validate_response
from app.models import RequestSummary
from app.report_queue import ReportQueueError, enqueue_report_package, enqueue_registered_report_package
from app.service import EdgeService


REQUEST = "2022T00793"
ROW_IDS = count(1)


def row(method="A11111", ax="2022A11111", request=REQUEST):
    item = {key: None for key in NUMBER_FIELDS | TEXT_FIELDS}
    item.update(reqNumber=request, testMtdCode=method, axTestMtdCode=ax,
                reqResultNo=225881, smpResultNo=next(ROW_IDS), testMtdResultNo=414143)
    return item


class QueueDatabase:
    def __init__(self):
        self.writes = []

    def fetch_job(self, _job_id):
        return None

    def register_report_run(self, **values):
        self.writes.append(("report", values))

    def upsert_report_artifact(self, **values):
        self.writes.append(("artifact", values))

    def enqueue_report_transfer(self, **values):
        self.writes.append(("transfer", values))
        return {"transfer_id": values["transfer_id"], "status": "PENDING"}


class Lookup:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def fetch(self, page, size, filters):
        self.calls.append((page, size, filters))
        return validate_response({"totalCount": len(self.rows), "page": page, "size": size,
                                  "items": self.rows[(page - 1) * size:page * size]}, page, size)


def install_lookup(monkeypatch, rows):
    lookup = Lookup(rows)
    monkeypatch.setattr(LimsRequestsClient, "from_settings", classmethod(lambda cls, settings: lookup))
    return lookup


def queue(tmp_path, database, *, method="A11111", enabled=True, options=None):
    package = tmp_path / "report.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("report.pdf", b"test report")
    report_options = {"transfer": {"requestNumber": REQUEST, "limsExperimentCode": method},
                      "email": {"subject": "분석 결과", "bodyHtml": "<p>검토한 분석 결과입니다.</p>"}}
    report_options.update(options or {})
    result = enqueue_report_package(
        settings=SimpleNamespace(storage_root=tmp_path, report_storage_key="REPORTS",
                                 report_transfer_max_attempts=5, lims_requests_enabled=enabled),
        database=database, report_id="report", package_path=package, source_job_id=None,
        request_number=REQUEST, experiment_code="XRD", equipment_code="ACTUAL-EQUIPMENT",
        operator_id="operator", report_options=report_options,
    )
    return result, report_options


def test_live_summary_keeps_query_and_registration_codes_separate():
    summary = EdgeService._live_request_summary(row())
    serialized = RequestSummary.model_validate(summary).model_dump(by_alias=True)
    assert serialized["testMethodCode"] == "A11111"
    assert serialized["experimentCode"] == "A11111"
    assert serialized["axTestMtdCode"] == "2022A11111"
    # Request-year prefixes are never synthesized for older upstream responses.
    original = row()
    original.pop("axTestMtdCode")
    assert RequestSummary.model_validate(EdgeService._live_request_summary(original)).ax_test_method_code is None


def test_cache_contains_search_cannot_silently_use_exact_filter(tmp_path):
    service = EdgeService(SimpleNamespace(storage_root=tmp_path), object())
    with pytest.raises(ApiException) as error:
        service.request_summaries(1, 50, source="cache", request_filters={"reqNumber": "00793", "matchMode": "CONTAINS"})
    assert error.value.code == "LIMS_LOOKUP_REQUIRED"


@pytest.mark.parametrize("method", ["A11111", "2022A11111"])
def test_queue_uses_matching_original_ax_key_and_keeps_authored_display(monkeypatch, tmp_path, method):
    lookup = install_lookup(monkeypatch, [row(), row("A11112", "2022A11112")])
    database = QueueDatabase()
    result, authored = queue(tmp_path, database, method=method)
    assert result["queued"] is True
    assert lookup.calls == [(1, 1000, {"reqNumber": REQUEST, "matchMode": "EXACT"})]
    transfer = next(values for kind, values in database.writes if kind == "transfer")
    options = json.loads(transfer["report_options_json"])
    assert options["testMtdCode"] == "2022A11111"
    assert options["transfer"]["limsExperimentCode"] == method
    assert "testMtdCode" not in authored
    assert transfer["experiment_code"] == "XRD"
    assert transfer["equipment_code"] == "ACTUAL-EQUIPMENT"
    assert next(values for kind, values in database.writes if kind == "report")["experiment_code"] == "XRD"


def test_exact_ax_input_is_not_replaced_by_another_test_with_same_display(monkeypatch, tmp_path):
    install_lookup(monkeypatch, [row(), row(ax="2023A11111")])
    database = QueueDatabase()
    queue(tmp_path, database, method="2022A11111")
    transfer = next(values for kind, values in database.writes if kind == "transfer")
    assert json.loads(transfer["report_options_json"])["testMtdCode"] == "2022A11111"


@pytest.mark.parametrize("rows,method,expected", [
    ([row(ax=None)], "A11111", "REPORT_AX_TEST_METHOD_REQUIRED"),
    ([row(ax="")], "A11111", "REPORT_AX_TEST_METHOD_REQUIRED"),
    ([row(ax=" ")], "A11111", "REPORT_AX_TEST_METHOD_REQUIRED"),
    ([row(), row(ax=None)], "A11111", "REPORT_AX_TEST_METHOD_REQUIRED"),
    ([row(), row(ax="2023A11111")], "A11111", "REPORT_TEST_METHOD_AMBIGUOUS"),
    ([row("A11112", "2022A11112")], "A11111", "REPORT_TEST_METHOD_NOT_FOUND"),
    ([row("A11112", "2022A11112")], "2022A11111", "REPORT_TEST_METHOD_NOT_FOUND"),
    ([], "A11111", "REPORT_TEST_METHOD_NOT_FOUND"),
    ([row(request="2022T00794")], "A11111", "REPORT_TEST_METHOD_LOOKUP_INVALID"),
    ([row(ax="X" * 21)], "A11111", "REPORT_TEST_METHOD_INVALID"),
])
def test_unverified_or_ambiguous_key_cannot_write_report_or_queue(monkeypatch, tmp_path, rows, method, expected):
    install_lookup(monkeypatch, rows)
    database = QueueDatabase()
    with pytest.raises(ReportQueueError) as error:
        queue(tmp_path, database, method=method)
    assert error.value.code == expected
    assert not database.writes


def test_lookup_finishes_all_pages_before_accepting_registration_key(monkeypatch, tmp_path):
    rows = [row()] + [row("OTHER", "OTHER-REGISTRATION") for _ in range(999)] + [row()]
    lookup = install_lookup(monkeypatch, rows)
    database = QueueDatabase()
    queue(tmp_path, database)
    assert [call[0] for call in lookup.calls] == [1, 2]
    transfer = next(values for kind, values in database.writes if kind == "transfer")
    assert json.loads(transfer["report_options_json"])["testMtdCode"] == "2022A11111"


def test_later_page_conflict_rejects_first_page_match(monkeypatch, tmp_path):
    lookup = install_lookup(monkeypatch, [row() for _ in range(1000)] + [row(ax="2023A11111")])
    database = QueueDatabase()
    with pytest.raises(ReportQueueError) as error:
        queue(tmp_path, database)
    assert error.value.code == "REPORT_TEST_METHOD_AMBIGUOUS"
    assert len(lookup.calls) == 2
    assert not database.writes


@pytest.mark.parametrize("fault", ["total_changed", "short_page", "too_many"])
def test_incomplete_or_unbounded_lookup_cannot_write_queue(monkeypatch, tmp_path, fault):
    calls = []
    def fetch(page, size, filters):
        calls.append(page)
        if fault == "too_many":
            return {"totalCount": 10001, "items": [row() for _ in range(size)]}
        if fault == "short_page":
            return {"totalCount": 1001, "items": [row()]}
        return {"totalCount": 1001 if page == 1 else 1002, "items": [row() for _ in range(1000 if page == 1 else 2)]}
    monkeypatch.setattr(LimsRequestsClient, "from_settings", classmethod(lambda cls, settings: SimpleNamespace(fetch=fetch)))
    database = QueueDatabase()
    with pytest.raises(ReportQueueError) as error:
        queue(tmp_path, database)
    assert error.value.code == ("REPORT_TEST_METHOD_LOOKUP_TOO_BROAD" if fault == "too_many" else "REPORT_TEST_METHOD_LOOKUP_INVALID")
    assert len(calls) <= 2
    assert not database.writes


def test_failed_lookup_is_clear_and_has_no_queue_side_effect(monkeypatch, tmp_path):
    def fetch(*args):
        raise ApiException(502, "LIMS_REQUESTS_UNAVAILABLE", "private relay detail", retryable=True)
    monkeypatch.setattr(LimsRequestsClient, "from_settings", classmethod(lambda cls, settings: SimpleNamespace(fetch=fetch)))
    database = QueueDatabase()
    with pytest.raises(ReportQueueError) as error:
        queue(tmp_path, database)
    assert error.value.code == "REPORT_TEST_METHOD_LOOKUP_UNAVAILABLE"
    assert error.value.retryable is True
    assert "private relay detail" not in str(error.value)
    assert not database.writes


def test_disabled_live_lookup_preserves_manual_registration_code(monkeypatch, tmp_path):
    def forbidden(*args):
        raise AssertionError("offline queue must not call LIMS")
    monkeypatch.setattr(LimsRequestsClient, "from_settings", classmethod(forbidden))
    database = QueueDatabase()
    queue(tmp_path, database, method="MANUAL-REG-CODE", enabled=False)
    options = json.loads(next(values for kind, values in database.writes if kind == "transfer")["report_options_json"])
    assert options["transfer"]["limsExperimentCode"] == "MANUAL-REG-CODE"
    assert "testMtdCode" not in options


@pytest.mark.parametrize("with_ids", [True, False])
def test_repeated_page_rows_do_not_hide_an_unread_test(monkeypatch, tmp_path, with_ids):
    first = [row() for _ in range(1000)]
    if not with_ids:
        for item in first:
            item.update(reqResultNo=None, smpResultNo=None, testMtdResultNo=None)
            item["smpResultName"] = str(id(item))
    lookup = install_lookup(monkeypatch, first + [dict(first[0])])
    database = QueueDatabase()
    with pytest.raises(ReportQueueError) as error:
        queue(tmp_path, database)
    assert error.value.code == "REPORT_TEST_METHOD_LOOKUP_INVALID"
    assert len(lookup.calls) == 2
    assert not database.writes


@pytest.mark.parametrize("mapped_project", ["XRD", "TEM"])
def test_saved_legacy_report_persists_ax_key_in_same_transaction_before_queue(monkeypatch, tmp_path, mapped_project):
    install_lookup(monkeypatch, [row(), row("A11112", "2022A11112")])
    package = tmp_path / "existing-report.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("report.pdf", b"existing reviewed report")
    report = {
        "report_id": "legacy-report", "source_job_id": None, "request_number": REQUEST,
        "experiment_code": "XRD", "equipment_code": "EQUIPMENT", "operator_id": "operator",
        "generation_status": "READY", "storage_key": "REPORTS", "package_relative_path": package.name,
        "package_size_bytes": package.stat().st_size, "package_sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
        "report_options_json": json.dumps({
            "analysisType": "XRD", "workbenchVersion": 1,
            "transfer": {"requestNumber": REQUEST, "limsExperimentCode": "A11111"},
            "email": {"subject": "분석 결과", "bodyHtml": "<p>저장된 분석 결과입니다.</p>"},
        }),
    }
    database = Database.__new__(Database)
    database.fetch_report_run = lambda report_id: report
    statements = []
    class Connection:
        def execute(self, sql, params):
            statements.append((" ".join(sql.split()), params))
            return self
        def fetchone(self):
            return {"version_no": 1, "transfer_id": "transfer", "status": "PENDING"}
    @contextmanager
    def transaction():
        yield Connection()
    database.transaction = transaction
    config = SimpleNamespace(storage_root=tmp_path, report_storage_key="REPORTS",
                             report_transfer_max_attempts=5, lims_requests_enabled=True,
                             request_method_map=(("A11111", mapped_project),))
    if mapped_project != "XRD":
        with pytest.raises(ReportQueueError) as error:
            enqueue_registered_report_package(settings=config, database=database, report=report, operator_id="operator")
        assert error.value.code == "REPORT_REQUEST_SCOPE_MISMATCH"
        assert not statements
        return
    result = enqueue_registered_report_package(settings=config, database=database, report=report, operator_id="operator")
    assert result["queued"] is True
    update_index = next(i for i, (sql, _) in enumerate(statements) if sql.startswith("UPDATE report_runs"))
    update_sql, params = statements[update_index]
    options = json.loads(params[0])
    assert options["testMtdCode"] == "2022A11111"
    assert options["transfer"]["limsExperimentCode"] == "A11111"
    assert "experiment_code =" not in update_sql and "version_no =" not in update_sql
    assert update_index < next(i for i, (sql, _) in enumerate(statements) if sql.startswith("INSERT INTO report_transfers"))
    assert report["experiment_code"] == "XRD"
    assert "testMtdCode" not in json.loads(report["report_options_json"])


def test_lookup_response_arriving_after_deadline_cannot_write_queue(monkeypatch, tmp_path):
    install_lookup(monkeypatch, [row()])
    elapsed = iter((0.0, 0.0, 20.1))
    monkeypatch.setattr("app.report_queue.time", SimpleNamespace(monotonic=lambda: next(elapsed)))
    database = QueueDatabase()
    with pytest.raises(ReportQueueError) as error:
        queue(tmp_path, database)
    assert error.value.code == "REPORT_TEST_METHOD_LOOKUP_UNAVAILABLE"
    assert error.value.retryable is True
    assert not database.writes

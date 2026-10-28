from __future__ import annotations

from queue import LifoQueue
import threading

import pytest

from app.database import (
    FILE_COLUMNS,
    JOB_COLUMNS,
    Database,
    DatabaseConfig,
    _validate_columns,
)


def test_validate_columns_rejects_unknown_job_column() -> None:
    with pytest.raises(ValueError) as raised:
        _validate_columns("jobs", {"job_id": "1", "status; DROP TABLE jobs": "x"}, JOB_COLUMNS)

    assert "허용되지 않은 컬럼" in str(raised.value)


def test_validate_columns_accepts_known_file_columns() -> None:
    _validate_columns(
        "files",
        {"file_id": "f1", "job_id": "j1", "relative_path": "raw/a.txt"},
        FILE_COLUMNS,
    )


class _FakeConnection:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.pings = 0
        self.closed = False

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def ping(self, reconnect: bool) -> None:
        assert reconnect is True
        self.pings += 1

    def close(self) -> None:
        self.closed = True


def test_connection_pool_reuses_idle_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    database = Database.__new__(Database)
    database.config = DatabaseConfig(host="db", pool_size=2, pool_timeout_seconds=1)
    database._pool = LifoQueue(maxsize=2)
    database._pool_lock = threading.Lock()
    database._pool_created = 0
    database._pool_closed = False
    created: list[_FakeConnection] = []

    def create() -> _FakeConnection:
        connection = _FakeConnection()
        created.append(connection)
        return connection

    monkeypatch.setattr(database, "_create_raw_connection", create)

    with database._connect():
        pass
    with database._connect():
        pass

    assert len(created) == 1
    assert created[0].commits == 2
    assert created[0].pings == 1

    database.close()
    assert created[0].closed is True


class _FetchAllCursor:
    def fetchall(self) -> list[dict[str, object]]:
        return []


class _CaptureConnection:
    def __init__(self) -> None:
        self.sql = ""
        self.params: tuple[object, ...] = ()

    def execute(self, sql: str, params: tuple[object, ...]) -> _FetchAllCursor:
        self.sql = sql
        self.params = params
        return _FetchAllCursor()

    def __enter__(self) -> "_CaptureConnection":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


def test_mail_requester_lookup_uses_exact_parameterized_identity(monkeypatch):
    database = Database.__new__(Database)
    connection = _CaptureConnection()
    monkeypatch.setattr(database, '_connect', lambda: connection)
    number = "REQ-1' OR 1=1 --"
    assert database.fetch_request_recipient(number) == []
    assert 'req_number = ?' in connection.sql and 'LIMIT 2' in connection.sql
    assert number not in connection.sql and connection.params == (number,)


def test_registered_queue_persists_normalized_mail_in_same_transaction():
    from contextlib import contextmanager
    import json
    database = Database.__new__(Database)
    statements = []
    class Connection:
        def execute(self, sql, params):
            statements.append((' '.join(sql.split()), params))
            return self
        def fetchone(self):
            return {'version_no': 2, 'transfer_id': 'transfer', 'status': 'PENDING'}
    @contextmanager
    def transaction():
        yield Connection()
    database.transaction = transaction
    options = json.dumps({'email': {'to': [], 'recipientPolicy':'lims', 'cc':['review@rist.re.kr']}})
    database.enqueue_report_transfer(report_id='report',transfer_id='transfer',source_job_id=None,
        request_number='REQ-1',experiment_code='XRD',equipment_code='AX',operator_id='SSO',
        storage_key='reports',package_relative_path='report.zip',package_file_name='report.zip',
        package_size_bytes=10,package_sha256='0'*64,report_options_json=options,
        generated_at='2026-10-28',max_attempts=5,register_report_run=False)
    updates=[(i,sql,params) for i,(sql,params) in enumerate(statements) if sql.startswith('UPDATE report_runs')]
    assert len(updates)==1
    i,sql,params=updates[0]
    assert params==(options,'report') and 'report_options_json = ?' in sql
    assert 'request_number =' not in sql and 'version_no =' not in sql
    assert i < next(i for i,(sql,_) in enumerate(statements) if sql.startswith('INSERT INTO report_transfers'))


def test_request_summary_completion_filter_is_parameterized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database.__new__(Database)
    connection = _CaptureConnection()
    monkeypatch.setattr(database, "_connect", lambda: connection)

    rows = database.fetch_request_summaries(
        50,
        0,
        experiment_keywords=("ft-ir",),
        include_completed=False,
    )

    assert rows == []
    assert "%완료%" not in connection.sql
    assert connection.params[0] == "%완료%"
    assert connection.params[1:] == ("%ft-ir%", "%ft-ir%", 50, 0)

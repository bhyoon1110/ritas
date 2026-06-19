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

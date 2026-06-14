from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


TERMINAL_STATUSES = {"COMPLETED", "FAILED", "UPLOAD_EXPIRED"}


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def _initialize(self) -> None:
        with self.transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    request_number TEXT NOT NULL,
                    experiment_code TEXT NOT NULL,
                    equipment_code TEXT NOT NULL,
                    operator_id TEXT NOT NULL,
                    source_host_name TEXT NOT NULL,
                    declared_ip_address TEXT,
                    observed_remote_ip TEXT,
                    client_version TEXT,
                    expected_file_count INTEGER NOT NULL,
                    expected_total_size_bytes INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    upload_expires_at TEXT NOT NULL,
                    verified_at TEXT,
                    report_requested_at TEXT,
                    processing_started_at TEXT,
                    completed_at TEXT,
                    root_relative_path TEXT NOT NULL,
                    report_options_json TEXT,
                    error_json TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_business_pk
                ON jobs (
                    request_number,
                    experiment_code,
                    equipment_code,
                    operator_id
                );

                CREATE TABLE IF NOT EXISTS files (
                    file_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    last_modified_at TEXT,
                    uploaded_at TEXT NOT NULL,
                    UNIQUE(job_id, relative_path),
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id)
                );

                CREATE TABLE IF NOT EXISTS idempotency_records (
                    endpoint TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    response_status INTEGER NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(endpoint, idempotency_key)
                );
                """
            )

    def fetch_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return dict(row) if row else None

    def fetch_jobs_by_status(
        self, status: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = ?
                ORDER BY report_requested_at, created_at
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def claim_queued_job(self, job_id: str, started_at: str) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = 'PROCESSING',
                    progress = 50,
                    processing_started_at = ?,
                    error_json = NULL
                WHERE job_id = ? AND status = 'QUEUED'
                """,
                (started_at, job_id),
            )
        return cursor.rowcount == 1

    def fetch_active_job(
        self,
        request_number: str,
        experiment_code: str,
        equipment_code: str,
        operator_id: str,
    ) -> dict[str, Any] | None:
        placeholders = ",".join("?" for _ in TERMINAL_STATUSES)
        params = (
            request_number,
            experiment_code,
            equipment_code,
            operator_id,
            *sorted(TERMINAL_STATUSES),
        )
        query = f"""
            SELECT * FROM jobs
            WHERE request_number = ?
              AND experiment_code = ?
              AND equipment_code = ?
              AND operator_id = ?
              AND status NOT IN ({placeholders})
            ORDER BY created_at DESC
            LIMIT 1
        """
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        return dict(row) if row else None

    def insert_job(self, job: dict[str, Any]) -> None:
        columns = ", ".join(job)
        placeholders = ", ".join("?" for _ in job)
        with self.transaction() as connection:
            connection.execute(
                f"INSERT INTO jobs ({columns}) VALUES ({placeholders})",
                tuple(job.values()),
            )

    def update_job(self, job_id: str, **values: Any) -> None:
        if not values:
            return
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.transaction() as connection:
            connection.execute(
                f"UPDATE jobs SET {assignments} WHERE job_id = ?",
                (*values.values(), job_id),
            )

    def fetch_files(self, job_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM files WHERE job_id = ? ORDER BY relative_path",
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def fetch_file(self, job_id: str, relative_path: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM files WHERE job_id = ? AND relative_path = ?",
                (job_id, relative_path),
            ).fetchone()
        return dict(row) if row else None

    def insert_file(self, file_record: dict[str, Any]) -> None:
        columns = ", ".join(file_record)
        placeholders = ", ".join("?" for _ in file_record)
        with self.transaction() as connection:
            connection.execute(
                f"INSERT INTO files ({columns}) VALUES ({placeholders})",
                tuple(file_record.values()),
            )

    def fetch_idempotency(
        self, endpoint: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM idempotency_records
                WHERE endpoint = ? AND idempotency_key = ?
                """,
                (endpoint, idempotency_key),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["response"] = json.loads(result.pop("response_json"))
        return result

    def insert_idempotency(
        self,
        endpoint: str,
        idempotency_key: str,
        request_hash: str,
        response_status: int,
        response: dict[str, Any],
        created_at: str,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO idempotency_records (
                    endpoint,
                    idempotency_key,
                    request_hash,
                    response_status,
                    response_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    endpoint,
                    idempotency_key,
                    request_hash,
                    response_status,
                    json.dumps(response, ensure_ascii=False, sort_keys=True),
                    created_at,
                ),
            )

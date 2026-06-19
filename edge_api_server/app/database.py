from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

import pymysql
from pymysql.cursors import DictCursor

from rist_common import get_logger

logger = get_logger(__name__)


TERMINAL_STATUSES = {"COMPLETED", "FAILED", "UPLOAD_EXPIRED"}
JOB_COLUMNS = {
    "job_id",
    "request_number",
    "experiment_code",
    "equipment_code",
    "operator_id",
    "source_host_name",
    "declared_ip_address",
    "observed_remote_ip",
    "client_version",
    "expected_file_count",
    "expected_total_size_bytes",
    "status",
    "progress",
    "created_at",
    "upload_expires_at",
    "verified_at",
    "report_requested_at",
    "processing_started_at",
    "completed_at",
    "root_relative_path",
    "report_options_json",
    "error_json",
}
FILE_COLUMNS = {
    "file_id",
    "job_id",
    "relative_path",
    "size_bytes",
    "sha256",
    "last_modified_at",
    "uploaded_at",
}


@dataclass(frozen=True)
class DatabaseConfig:
    """MariaDB 접속 정보."""

    host: str
    name: str = "rist_edge"
    port: int = 3306
    user: str = "rist"
    password: str = ""


_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS jobs (
        job_id VARCHAR(36) NOT NULL,
        request_number VARCHAR(128) NOT NULL,
        experiment_code VARCHAR(64) NOT NULL,
        equipment_code VARCHAR(64) NOT NULL,
        operator_id VARCHAR(64) NOT NULL,
        source_host_name VARCHAR(255) NOT NULL,
        declared_ip_address VARCHAR(64),
        observed_remote_ip VARCHAR(64),
        client_version VARCHAR(64),
        expected_file_count INT NOT NULL,
        expected_total_size_bytes BIGINT NOT NULL,
        status VARCHAR(32) NOT NULL,
        progress INT NOT NULL DEFAULT 0,
        created_at VARCHAR(64) NOT NULL,
        upload_expires_at VARCHAR(64) NOT NULL,
        verified_at VARCHAR(64),
        report_requested_at VARCHAR(64),
        processing_started_at VARCHAR(64),
        completed_at VARCHAR(64),
        root_relative_path VARCHAR(512) NOT NULL,
        report_options_json LONGTEXT,
        error_json LONGTEXT,
        PRIMARY KEY (job_id),
        KEY idx_jobs_business_pk (
            request_number,
            experiment_code,
            equipment_code,
            operator_id
        )
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS files (
        file_id VARCHAR(36) NOT NULL,
        job_id VARCHAR(36) NOT NULL,
        relative_path VARCHAR(512) NOT NULL,
        size_bytes BIGINT NOT NULL,
        sha256 VARCHAR(64) NOT NULL,
        last_modified_at VARCHAR(64),
        uploaded_at VARCHAR(64) NOT NULL,
        PRIMARY KEY (file_id),
        UNIQUE KEY uq_files_job_path (job_id, relative_path),
        CONSTRAINT fk_files_job FOREIGN KEY (job_id) REFERENCES jobs(job_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS idempotency_records (
        endpoint VARCHAR(128) NOT NULL,
        idempotency_key VARCHAR(128) NOT NULL,
        request_hash VARCHAR(128) NOT NULL,
        response_status INT NOT NULL,
        response_json LONGTEXT NOT NULL,
        created_at VARCHAR(64) NOT NULL,
        PRIMARY KEY (endpoint, idempotency_key)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
)


class _Cursor:
    """PyMySQL 커서를 dict 결과 인터페이스로 감싼다."""

    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def fetchone(self) -> dict[str, Any] | None:
        row = self._cursor.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._cursor.fetchall()]


class _Connection:
    """PyMySQL 연결을 execute/commit 인터페이스로 감싼다."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        cursor = self._raw.cursor()
        cursor.execute(sql.replace("?", "%s"), params)
        return _Cursor(cursor)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()

    def __enter__(self) -> "_Connection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            if exc_type is None:
                self._raw.commit()
            else:
                self._raw.rollback()
        finally:
            self._raw.close()


class Database:
    def __init__(self, config: DatabaseConfig) -> None:
        if not config.host:
            raise ValueError("MariaDB 접속 호스트(RIST_DB_HOST)가 필요합니다.")
        self.config = config
        logger.info(
            "MariaDB에 연결합니다 (host=%s, port=%s, db=%s, user=%s)",
            config.host,
            config.port,
            config.name,
            config.user,
        )
        self._ensure_database()
        self._initialize()
        logger.info("MariaDB 스키마 초기화 완료 (db=%s)", config.name)

    @classmethod
    def from_settings(cls, settings: Any) -> "Database":
        return cls(
            DatabaseConfig(
                host=settings.db_host,
                port=settings.db_port,
                name=settings.db_name,
                user=settings.db_user,
                password=settings.db_password,
            )
        )

    def _ensure_database(self) -> None:
        """접속 정보만으로 동작하도록 대상 DB가 없으면 생성한다."""
        connection = pymysql.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            password=self.config.password,
            charset="utf8mb4",
            autocommit=True,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{self.config.name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
        finally:
            connection.close()

    def _connect(self) -> _Connection:
        connection = pymysql.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            password=self.config.password,
            database=self.config.name,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
        )
        return _Connection(connection)

    @contextmanager
    def transaction(self) -> Iterator[_Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.transaction() as connection:
            for statement in _SCHEMA:
                connection.execute(statement)

    def fetch_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return row

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
        return rows

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
            claimed = cursor.rowcount == 1
        return claimed

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
        return row

    def insert_job(self, job: dict[str, Any]) -> None:
        _validate_columns("jobs", job, JOB_COLUMNS)
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
        _validate_columns("jobs", values, JOB_COLUMNS)
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
        return rows

    def fetch_file(self, job_id: str, relative_path: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM files WHERE job_id = ? AND relative_path = ?",
                (job_id, relative_path),
            ).fetchone()
        return row

    def insert_file(self, file_record: dict[str, Any]) -> None:
        _validate_columns("files", file_record, FILE_COLUMNS)
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
        row["response"] = json.loads(row.pop("response_json"))
        return row

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


def _validate_columns(
    table: str,
    values: dict[str, Any],
    allowed: set[str],
) -> None:
    invalid = sorted(set(values) - allowed)
    if invalid:
        raise ValueError(
            f"{table}에 허용되지 않은 컬럼입니다: {', '.join(invalid)}"
        )

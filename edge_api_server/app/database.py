from __future__ import annotations

import json
from queue import Empty, Full, LifoQueue
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator

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
    pool_size: int = 8
    pool_timeout_seconds: float = 10.0


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
    """
    CREATE OR REPLACE VIEW request_summary AS
    SELECT request_number,
           COUNT(*) AS job_count,
           SUM(status = 'COMPLETED') AS completed_job_count,
           SUM(status = 'FAILED') AS failed_job_count,
           GROUP_CONCAT(DISTINCT status ORDER BY status) AS statuses,
           GROUP_CONCAT(DISTINCT experiment_code ORDER BY experiment_code) AS experiments,
           GROUP_CONCAT(DISTINCT equipment_code ORDER BY equipment_code) AS equipment_codes,
           MIN(created_at) AS created_at,
           MAX(COALESCE(
               completed_at,
               processing_started_at,
               report_requested_at,
               verified_at,
               created_at
           )) AS updated_at
    FROM jobs
    GROUP BY request_number
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

    def __init__(self, raw: Any, release: Callable[[Any], None]) -> None:
        self._raw = raw
        self._release = release
        self._closed = False

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        cursor = self._raw.cursor()
        cursor.execute(sql.replace("?", "%s"), params)
        return _Cursor(cursor)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._release(self._raw)

    def __enter__(self) -> "_Connection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            if exc_type is None:
                self._raw.commit()
            else:
                self._raw.rollback()
        finally:
            self.close()


class Database:
    def __init__(self, config: DatabaseConfig) -> None:
        if not config.host:
            raise ValueError("MariaDB 접속 호스트(RIST_DB_HOST)가 필요합니다.")
        if config.pool_size < 1:
            raise ValueError("MariaDB 커넥션 풀 크기는 1 이상이어야 합니다.")
        if config.pool_timeout_seconds <= 0:
            raise ValueError("MariaDB 커넥션 풀 대기 시간은 0보다 커야 합니다.")
        self.config = config
        self._pool: LifoQueue[Any] = LifoQueue(maxsize=config.pool_size)
        self._pool_lock = threading.Lock()
        self._pool_created = 0
        self._pool_closed = False
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
                pool_size=settings.db_pool_size,
                pool_timeout_seconds=settings.db_pool_timeout_seconds,
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
        return _Connection(
            self._acquire_raw_connection(), self._release_raw_connection
        )

    def _create_raw_connection(self) -> Any:
        return pymysql.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            password=self.config.password,
            database=self.config.name,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
        )

    def _acquire_raw_connection(self) -> Any:
        while True:
            with self._pool_lock:
                if self._pool_closed:
                    raise RuntimeError("닫힌 MariaDB 커넥션 풀을 사용할 수 없습니다.")
            try:
                connection = self._pool.get_nowait()
            except Empty:
                with self._pool_lock:
                    can_create = (
                        not self._pool_closed
                        and self._pool_created < self.config.pool_size
                    )
                    if can_create:
                        self._pool_created += 1
                if can_create:
                    try:
                        return self._create_raw_connection()
                    except Exception:
                        with self._pool_lock:
                            self._pool_created -= 1
                        raise
                try:
                    connection = self._pool.get(
                        timeout=self.config.pool_timeout_seconds
                    )
                except Empty as exc:
                    raise TimeoutError(
                        "MariaDB 커넥션 풀 대기 시간이 초과되었습니다."
                    ) from exc

            try:
                connection.ping(reconnect=True)
            except Exception:
                self._discard_raw_connection(connection)
                continue
            return connection

    def _release_raw_connection(self, connection: Any) -> None:
        try:
            connection.rollback()
        except Exception:
            self._discard_raw_connection(connection)
            return
        with self._pool_lock:
            pool_closed = self._pool_closed
        if pool_closed:
            self._discard_raw_connection(connection)
            return
        try:
            self._pool.put_nowait(connection)
        except Full:
            self._discard_raw_connection(connection)

    def _discard_raw_connection(self, connection: Any) -> None:
        try:
            connection.close()
        finally:
            with self._pool_lock:
                self._pool_created -= 1

    def close(self) -> None:
        """유휴 연결을 닫는다. API/worker 종료 훅에서 한 번 호출한다."""
        with self._pool_lock:
            if self._pool_closed:
                return
            self._pool_closed = True
        while True:
            try:
                self._discard_raw_connection(self._pool.get_nowait())
            except Empty:
                return

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

    def delete_file(self, job_id: str, relative_path: str) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM files WHERE job_id = ? AND relative_path = ?",
                (job_id, relative_path),
            )
        return cursor.rowcount == 1

    def update_file(self, job_id: str, relative_path: str, **values: Any) -> None:
        _validate_columns(
            "files",
            values,
            FILE_COLUMNS - {"file_id", "job_id", "relative_path"},
        )
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.transaction() as connection:
            connection.execute(
                "UPDATE files SET "
                f"{assignments} WHERE job_id = ? AND relative_path = ?",
                (*values.values(), job_id, relative_path),
            )

    def fetch_request_summaries(
        self, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT * FROM request_summary
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()

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

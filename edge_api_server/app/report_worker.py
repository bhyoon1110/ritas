from __future__ import annotations

import argparse
import json
import signal
import time
from typing import Any

from rist_common import get_logger

from .config import Settings
from .database import Database
from .error_archive import ErrorArchive, ErrorArchiveSettings, record_background_error
from .llm_client import LocalLlmClient
from .manifest import write_manifest
from .report import generate_report
from .spring_callback import SpringCallbackClient, SpringCallbackError
from .time_utils import isoformat_kst
from .usage_archive import (
    UsageArchive,
    UsageArchiveSettings,
    record_background_usage,
)

logger = get_logger(__name__)


class ReportWorker:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        llm_client: LocalLlmClient,
        spring_client: SpringCallbackClient | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.llm_client = llm_client
        self.spring_client = spring_client or SpringCallbackClient(
            settings.spring_callback_url,
            settings.spring_callback_timeout_seconds,
            settings.spring_callback_max_attempts,
        )
        self.error_archive = ErrorArchive(
            ErrorArchiveSettings(
                root=settings.error_archive_root or (settings.storage_root / "errors"),
                retention_days=settings.error_retention_days,
                capture_files=settings.error_capture_files,
                max_file_bytes=settings.error_max_file_bytes,
                max_total_bytes=settings.error_max_total_bytes,
            )
        )
        self.usage_archive = UsageArchive(
            UsageArchiveSettings(
                root=settings.usage_log_root or (settings.storage_root / "usage"),
                retention_days=settings.usage_log_retention_days,
            )
        )

    def run_once(self) -> bool:
        queued = self.database.fetch_jobs_by_status("QUEUED", limit=1)
        if not queued:
            return False
        job = queued[0]
        started_at = isoformat_kst()
        if not self.database.claim_queued_job(job["job_id"], started_at):
            return False
        job = self.database.fetch_job(job["job_id"]) or job
        logger.info("작업을 선점하여 처리를 시작합니다 (job_id=%s)", job["job_id"])
        self._write_manifest(job["job_id"])
        self.process_job(job)
        return True

    def process_job(self, job: dict[str, Any]) -> None:
        job_id = job["job_id"]
        started = time.perf_counter()
        try:
            generated_at = isoformat_kst()
            document = generate_report(
                self.settings,
                job,
                llm_client=self.llm_client,
                generated_at=generated_at,
            )
            package_path = (
                self.settings.storage_root
                / job["root_relative_path"]
                / "report"
                / "report-package.zip"
            )
            if self.spring_client.enabled:
                self.database.update_job(
                    job_id,
                    status="CALLBACK_PENDING",
                    progress=90,
                    error_json=None,
                )
                self._write_manifest(job_id)
                self.spring_client.deliver(job, package_path)
            self.database.update_job(
                job_id,
                status="COMPLETED",
                progress=100,
                completed_at=generated_at,
                error_json=None,
            )
            logger.info(
                "보고서 생성 및 작업 완료 (job_id=%s, llm_used=%s)",
                job_id,
                document.llm_used,
            )
            record_background_usage(
                self.usage_archive,
                project=str(job.get("experiment_code") or "EDGE"),
                action="보고서 생성 완료",
                result="success",
                duration_ms=round((time.perf_counter() - started) * 1000),
                job_id=job_id,
                endpoint=f"/background/edge/report/jobs/{job_id}",
                request_number=job.get("request_number"),
                experiment_code=job.get("experiment_code"),
                equipment_code=job.get("equipment_code"),
                operator_id=job.get("operator_id"),
                file_name=package_path.name,
                file_size_bytes=(
                    package_path.stat().st_size if package_path.is_file() else None
                ),
            )
        except FileNotFoundError as exc:
            self._mark_failed(
                job_id,
                "ANALYSIS_RESULT_NOT_FOUND",
                str(exc),
                False,
                exc,
                duration_ms=round((time.perf_counter() - started) * 1000),
            )
        except SpringCallbackError as exc:
            self._mark_failed(
                job_id,
                exc.code,
                str(exc),
                exc.retryable,
                exc,
                duration_ms=round((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            logger.exception("보고서 worker 처리 중 예외 발생 (job_id=%s)", job_id)
            self._mark_failed(
                job_id,
                "REPORT_WORKER_ERROR",
                f"보고서 worker 오류: {exc}",
                True,
                exc,
                duration_ms=round((time.perf_counter() - started) * 1000),
            )
        finally:
            self._write_manifest(job_id)

    def _mark_failed(
        self,
        job_id: str,
        code: str,
        message: str,
        retryable: bool,
        exception: BaseException | None = None,
        *,
        duration_ms: int = 0,
    ) -> None:
        logger.error(
            "작업 실패 (job_id=%s, code=%s, retryable=%s): %s",
            job_id,
            code,
            retryable,
            message,
        )
        error = {
            "code": code,
            "message": message,
            "retryable": retryable,
        }
        self.database.update_job(
            job_id,
            status="FAILED",
            progress=50,
            completed_at=isoformat_kst(),
            error_json=json.dumps(error, ensure_ascii=False),
        )
        job = self.database.fetch_job(job_id)
        source_paths = []
        project = "EDGE"
        if job:
            project = str(job.get("experiment_code") or "EDGE")
            relative_root = str(job.get("root_relative_path") or "").strip()
            if relative_root:
                source_paths.append(self.settings.storage_root / relative_root / "input")
        record_background_error(
            self.error_archive,
            project=project,
            code=code,
            message=message,
            exception=exception,
            job_id=job_id,
            details={"retryable": retryable},
            source_paths=source_paths,
        )
        record_background_usage(
            self.usage_archive,
            project=project,
            action="보고서 생성 실패",
            result="failure",
            duration_ms=duration_ms,
            job_id=job_id,
            endpoint=f"/background/edge/report/jobs/{job_id}",
            request_number=(job or {}).get("request_number"),
            experiment_code=(job or {}).get("experiment_code"),
            equipment_code=(job or {}).get("equipment_code"),
            operator_id=(job or {}).get("operator_id"),
        )

    def _write_manifest(self, job_id: str) -> None:
        write_manifest(self.settings, self.database, job_id)


def build_worker(settings: Settings | None = None) -> ReportWorker:
    resolved = settings or Settings.from_env()
    database = Database.from_settings(resolved)
    llm_client = LocalLlmClient(
        resolved.llm_base_url,
        resolved.llm_model,
        resolved.llm_timeout_seconds,
        resolved.llm_temperature,
        resolved.llm_max_tokens,
        resolved.llm_validate_model,
    )
    return ReportWorker(resolved, database, llm_client)


def main() -> None:
    parser = argparse.ArgumentParser(description="RIST report generation worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="대기 중인 작업을 최대 한 건 처리한 뒤 종료",
    )
    args = parser.parse_args()
    worker = build_worker()
    if args.once:
        logger.info("worker를 --once 모드로 실행합니다")
        try:
            worker.run_once()
        finally:
            worker.llm_client.close()
            worker.spring_client.close()
            worker.database.close()
        return

    running = True

    def stop(*_: object) -> None:
        nonlocal running
        running = False
        logger.info("종료 신호를 수신했습니다. worker를 종료합니다.")

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    logger.info(
        "보고서 worker를 시작합니다 (poll_seconds=%s)",
        worker.settings.worker_poll_seconds,
    )
    try:
        while running:
            processed = worker.run_once()
            if not processed:
                time.sleep(worker.settings.worker_poll_seconds)
    finally:
        worker.llm_client.close()
        worker.spring_client.close()
        worker.database.close()
        logger.info("보고서 worker가 종료되었습니다.")


if __name__ == "__main__":
    main()

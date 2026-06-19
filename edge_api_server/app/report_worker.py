from __future__ import annotations

import argparse
import json
import signal
import time
from typing import Any

from rist_common import get_logger

from .config import Settings
from .database import Database
from .llm_client import LocalLlmClient
from .report import generate_report
from .time_utils import isoformat_kst

logger = get_logger(__name__)


class ReportWorker:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        llm_client: LocalLlmClient,
    ) -> None:
        self.settings = settings
        self.database = database
        self.llm_client = llm_client

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
        try:
            generated_at = isoformat_kst()
            document = generate_report(
                self.settings,
                job,
                llm_client=self.llm_client,
                generated_at=generated_at,
            )
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
        except FileNotFoundError as exc:
            self._mark_failed(
                job_id,
                "ANALYSIS_RESULT_NOT_FOUND",
                str(exc),
                False,
            )
        except Exception as exc:
            logger.exception("보고서 worker 처리 중 예외 발생 (job_id=%s)", job_id)
            self._mark_failed(
                job_id,
                "REPORT_WORKER_ERROR",
                f"보고서 worker 오류: {exc}",
                True,
            )
        finally:
            self._write_manifest(job_id)

    def _mark_failed(
        self, job_id: str, code: str, message: str, retryable: bool
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

    def _write_manifest(self, job_id: str) -> None:
        from .service import EdgeService

        EdgeService(self.settings, self.database).write_manifest(job_id)


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
        logger.info("보고서 worker가 종료되었습니다.")


if __name__ == "__main__":
    main()

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5
import zipfile

from .database import Database
from .time_utils import isoformat_kst


class ReportQueueError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def enqueue_report_package(
    *,
    settings: Any,
    database: Database | None,
    report_id: str,
    package_path: Path,
    source_job_id: str | None,
    request_number: str,
    experiment_code: str,
    equipment_code: str,
    operator_id: str,
    report_options: Any = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Register a completed shared-storage ZIP for downstream LIMS delivery."""
    if database is None:
        raise ReportQueueError(
            "REPORT_QUEUE_DATABASE_UNAVAILABLE",
            "보고서 전송 큐를 등록할 데이터베이스가 연결되어 있지 않습니다.",
            retryable=True,
        )

    storage_root = Path(settings.storage_root).expanduser().resolve()
    resolved_package = package_path.expanduser().resolve()
    try:
        relative_path = resolved_package.relative_to(storage_root)
    except ValueError as exc:
        raise ReportQueueError(
            "REPORT_PACKAGE_OUTSIDE_SHARED_STORAGE",
            "보고서 ZIP은 공유 저장소 내부에 있어야 합니다.",
            retryable=False,
        ) from exc

    if not resolved_package.is_file():
        raise ReportQueueError(
            "REPORT_PACKAGE_NOT_FOUND",
            "전송 큐에 등록할 보고서 ZIP을 찾을 수 없습니다.",
            retryable=False,
        )
    if not zipfile.is_zipfile(resolved_package):
        raise ReportQueueError(
            "REPORT_PACKAGE_INVALID_ZIP",
            "보고서 패키지가 올바른 ZIP 파일이 아닙니다.",
            retryable=False,
        )

    normalized_relative_path = relative_path.as_posix()
    if normalized_relative_path.startswith("/") or ".." in relative_path.parts:
        raise ReportQueueError(
            "REPORT_PACKAGE_PATH_INVALID",
            "공유 저장소 상대 경로가 올바르지 않습니다.",
            retryable=False,
        )

    transfer_id = str(
        uuid5(NAMESPACE_URL, f"rist-report-transfer:{report_id}:LIMS")
    )
    try:
        transfer = database.enqueue_report_transfer(
            report_id=report_id,
            transfer_id=transfer_id,
            source_job_id=source_job_id,
            request_number=request_number,
            experiment_code=experiment_code,
            equipment_code=equipment_code,
            operator_id=operator_id,
            storage_key=str(settings.report_storage_key),
            package_relative_path=normalized_relative_path,
            package_file_name=resolved_package.name,
            package_size_bytes=resolved_package.stat().st_size,
            package_sha256=_sha256_file(resolved_package),
            report_options_json=_json_or_none(report_options),
            generated_at=generated_at or isoformat_kst(),
            max_attempts=max(1, int(settings.report_transfer_max_attempts)),
        )
    except ReportQueueError:
        raise
    except Exception as exc:
        raise ReportQueueError(
            "REPORT_QUEUE_REGISTRATION_FAILED",
            f"보고서 전송 큐 등록에 실패했습니다: {exc}",
            retryable=True,
        ) from exc

    return {
        "queued": True,
        "reportId": report_id,
        "transferId": str(transfer["transfer_id"]),
        "status": str(transfer["status"]),
        "storageKey": str(settings.report_storage_key),
        "packageRelativePath": normalized_relative_path,
        "packageSizeBytes": resolved_package.stat().st_size,
    }


def persist_preview_report_package(
    *,
    settings: Any,
    report_id: str,
    experiment_code: str,
    source_path: Path,
) -> Path:
    """Atomically publish a temporary preview ZIP into shared storage."""
    if not source_path.is_file():
        raise FileNotFoundError("보고서 파일이 만료되었습니다.")
    if not zipfile.is_zipfile(source_path):
        raise ReportQueueError(
            "REPORT_PACKAGE_INVALID_ZIP",
            "보고서 패키지가 올바른 ZIP 파일이 아닙니다.",
            retryable=False,
        )

    experiment_segment = _safe_segment(experiment_code)
    report_segment = _safe_segment(report_id)
    destination_dir = (
        Path(settings.storage_root)
        / "web-reports"
        / experiment_segment
        / report_segment
    )
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / "report-package.zip"
    temporary = destination_dir / f".{destination.name}.{uuid4().hex}.tmp"
    try:
        with source_path.open("rb") as source, temporary.open("xb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        if not zipfile.is_zipfile(temporary):
            raise ReportQueueError(
                "REPORT_PACKAGE_INVALID_ZIP",
                "공유 저장소에 기록된 보고서 패키지 검증에 실패했습니다.",
                retryable=True,
            )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _safe_segment(value: str) -> str:
    segment = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._")
    return segment or "report"

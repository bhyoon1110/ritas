from __future__ import annotations

import hashlib
import json
import threading
from datetime import timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Callable, TypeVar
from uuid import uuid4

from fastapi import UploadFile

from .config import Settings
from .database import Database
from .errors import ApiException
from .models import (
    CompleteUploadRequest,
    CreateJobRequest,
    GenerateReportRequest,
)
from .storage import (
    atomic_write_json,
    resolve_under,
    safe_component,
    stream_to_temp,
    validate_relative_path,
)
from .time_utils import isoformat_kst, now_kst, parse_datetime, timestamp_folder


MethodResult = TypeVar("MethodResult")


def synchronized(
    method: Callable[..., MethodResult],
) -> Callable[..., MethodResult]:
    @wraps(method)
    def wrapper(self: "EdgeService", *args: Any, **kwargs: Any) -> MethodResult:
        with self._mutation_lock:
            return method(self, *args, **kwargs)

    return wrapper


class EdgeService:
    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database
        self._mutation_lock = threading.RLock()
        self.settings.storage_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def request_hash(payload: Any) -> str:
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump(by_alias=True, exclude_none=True)
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def get_idempotent_response(
        self, endpoint: str, key: str, request_hash: str
    ) -> tuple[int, dict[str, Any]] | None:
        record = self.database.fetch_idempotency(endpoint, key)
        if not record:
            return None
        if record["request_hash"] != request_hash:
            raise ApiException(
                409,
                "IDEMPOTENCY_KEY_REUSED",
                "동일한 Idempotency-Key가 다른 요청 내용에 사용되었습니다.",
            )
        return record["response_status"], record["response"]

    def save_idempotent_response(
        self,
        endpoint: str,
        key: str,
        request_hash: str,
        status_code: int,
        response: dict[str, Any],
    ) -> None:
        self.database.insert_idempotency(
            endpoint,
            key,
            request_hash,
            status_code,
            response,
            isoformat_kst(),
        )

    @synchronized
    def create_job(
        self,
        request: CreateJobRequest,
        observed_remote_ip: str | None,
        idempotency_key: str,
    ) -> tuple[int, dict[str, Any]]:
        endpoint = "POST:/api/v1/jobs"
        request_hash = self.request_hash(request)
        cached = self.get_idempotent_response(
            endpoint, idempotency_key, request_hash
        )
        if cached:
            return cached

        pk = request.pk
        active = self.database.fetch_active_job(
            pk.request_number,
            pk.experiment_code,
            pk.equipment_code,
            pk.operator_id,
        )
        if active:
            raise ApiException(
                409,
                "ACTIVE_JOB_ALREADY_EXISTS",
                "동일 복합 PK의 활성 작업이 이미 존재합니다.",
                job_id=active["job_id"],
            )

        created = now_kst()
        expires = created + timedelta(hours=self.settings.upload_expiry_hours)
        job_id = str(uuid4())
        pk_folder = "_".join(
            safe_component(value)
            for value in (
                pk.request_number,
                pk.experiment_code,
                pk.equipment_code,
                pk.operator_id,
            )
        )
        root_relative_path = (
            Path(f"{created:%Y/%m/%d}")
            / f"{timestamp_folder(created)}_{job_id}"
            / pk_folder
        )
        job_root = self.settings.storage_root / root_relative_path
        for folder in ("input", "processed", "report", "logs", "queue"):
            (job_root / folder).mkdir(parents=True, exist_ok=True)

        job = {
            "job_id": job_id,
            "request_number": pk.request_number,
            "experiment_code": pk.experiment_code,
            "equipment_code": pk.equipment_code,
            "operator_id": pk.operator_id,
            "source_host_name": request.source_pc.host_name,
            "declared_ip_address": request.source_pc.declared_ip_address,
            "observed_remote_ip": observed_remote_ip,
            "client_version": request.source_pc.client_version,
            "expected_file_count": request.bundle.file_count,
            "expected_total_size_bytes": request.bundle.total_size_bytes,
            "status": "CREATED",
            "progress": 0,
            "created_at": isoformat_kst(created),
            "upload_expires_at": isoformat_kst(expires),
            "root_relative_path": root_relative_path.as_posix(),
        }
        self.database.insert_job(job)
        self.write_manifest(job_id)

        response = {
            "jobId": job_id,
            "status": "CREATED",
            "createdAt": job["created_at"],
            "uploadExpiresAt": job["upload_expires_at"],
        }
        self.save_idempotent_response(
            endpoint, idempotency_key, request_hash, 201, response
        )
        return 201, response

    def require_job(self, job_id: str) -> dict[str, Any]:
        job = self.database.fetch_job(job_id)
        if not job:
            raise ApiException(
                404, "JOB_NOT_FOUND", "작업을 찾을 수 없습니다.", job_id=job_id
            )
        return self.expire_if_needed(job)

    def expire_if_needed(self, job: dict[str, Any]) -> dict[str, Any]:
        if job["status"] in {"CREATED", "UPLOADING"}:
            if now_kst() >= parse_datetime(job["upload_expires_at"]):
                self.database.update_job(
                    job["job_id"], status="UPLOAD_EXPIRED", progress=0
                )
                job = self.database.fetch_job(job["job_id"]) or job
                self.write_manifest(job["job_id"])
        return job

    def ensure_upload_open(self, job: dict[str, Any]) -> None:
        if job["status"] == "UPLOAD_EXPIRED":
            raise ApiException(
                410,
                "UPLOAD_EXPIRED",
                "업로드 유효기간이 만료되었습니다.",
                job_id=job["job_id"],
            )
        if job["status"] not in {"CREATED", "UPLOADING"}:
            raise ApiException(
                409,
                "JOB_STATE_CONFLICT",
                f"현재 상태({job['status']})에서는 파일을 업로드할 수 없습니다.",
                job_id=job["job_id"],
            )

    @synchronized
    def upload_file(
        self,
        job_id: str,
        upload: UploadFile,
        relative_path: str,
        declared_size: int,
        declared_sha256: str,
        last_modified_at: str | None,
        idempotency_key: str,
    ) -> tuple[int, dict[str, Any]]:
        job = self.require_job(job_id)
        self.ensure_upload_open(job)
        relative_path = validate_relative_path(relative_path)
        declared_sha256 = declared_sha256.lower()
        if len(declared_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in declared_sha256
        ):
            raise ApiException(
                400,
                "INVALID_SHA256",
                "sha256은 소문자 64자리 16진수여야 합니다.",
                job_id=job_id,
            )
        if declared_size < 0:
            raise ApiException(
                400, "INVALID_FILE_SIZE", "파일 크기가 올바르지 않습니다.", job_id=job_id
            )

        endpoint = f"POST:/api/v1/jobs/{job_id}/files"
        metadata_hash = self.request_hash(
            {
                "relativePath": relative_path,
                "sizeBytes": declared_size,
                "sha256": declared_sha256,
                "lastModifiedAt": last_modified_at,
            }
        )
        cached = self.get_idempotent_response(
            endpoint, idempotency_key, metadata_hash
        )
        if cached:
            return cached

        existing = self.database.fetch_file(job_id, relative_path)
        if existing:
            if (
                existing["size_bytes"] == declared_size
                and existing["sha256"] == declared_sha256
            ):
                response = self.file_response(existing)
                self.save_idempotent_response(
                    endpoint, idempotency_key, metadata_hash, 201, response
                )
                return 201, response
            raise ApiException(
                409,
                "FILE_PATH_CONFLICT",
                "같은 상대 경로에 다른 내용의 파일이 이미 등록되어 있습니다.",
                job_id=job_id,
            )

        job_root = self.settings.storage_root / job["root_relative_path"]
        target = resolve_under(job_root / "input", relative_path)
        temp_path = target.with_name(f".{target.name}.{uuid4().hex}.upload")
        try:
            actual_size, actual_sha256 = stream_to_temp(
                upload.file, temp_path, self.settings.max_upload_bytes
            )
            if actual_size != declared_size:
                raise ApiException(
                    422,
                    "FILE_SIZE_MISMATCH",
                    "파일 크기가 요청값과 일치하지 않습니다.",
                    retryable=True,
                    job_id=job_id,
                    details={
                        "declaredSizeBytes": declared_size,
                        "actualSizeBytes": actual_size,
                    },
                )
            if actual_sha256 != declared_sha256:
                raise ApiException(
                    422,
                    "FILE_HASH_MISMATCH",
                    "파일 SHA-256이 요청값과 일치하지 않습니다.",
                    retryable=True,
                    job_id=job_id,
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            temp_path.replace(target)
        finally:
            temp_path.unlink(missing_ok=True)

        uploaded_at = isoformat_kst()
        file_record = {
            "file_id": str(uuid4()),
            "job_id": job_id,
            "relative_path": relative_path,
            "size_bytes": actual_size,
            "sha256": actual_sha256,
            "last_modified_at": last_modified_at,
            "uploaded_at": uploaded_at,
        }
        self.database.insert_file(file_record)
        if job["status"] == "CREATED":
            self.database.update_job(job_id, status="UPLOADING", progress=10)
        self.write_manifest(job_id)

        response = self.file_response(file_record)
        self.save_idempotent_response(
            endpoint, idempotency_key, metadata_hash, 201, response
        )
        return 201, response

    @staticmethod
    def file_response(file_record: dict[str, Any]) -> dict[str, Any]:
        return {
            "fileId": file_record["file_id"],
            "relativePath": file_record["relative_path"],
            "sizeBytes": file_record["size_bytes"],
            "sha256": file_record["sha256"],
            "status": "UPLOADED",
            "uploadedAt": file_record["uploaded_at"],
        }

    @synchronized
    def complete_upload(
        self,
        job_id: str,
        request: CompleteUploadRequest,
        idempotency_key: str,
    ) -> tuple[int, dict[str, Any]]:
        job = self.require_job(job_id)
        endpoint = f"POST:/api/v1/jobs/{job_id}/uploads/complete"
        request_hash = self.request_hash(request)
        cached = self.get_idempotent_response(
            endpoint, idempotency_key, request_hash
        )
        if cached:
            return cached
        self.ensure_upload_open(job)

        declared_paths = [item.relative_path for item in request.files]
        if len(set(declared_paths)) != len(declared_paths):
            raise ApiException(
                422,
                "DUPLICATE_BUNDLE_PATH",
                "bundle 파일 목록에 중복 상대 경로가 있습니다.",
                job_id=job_id,
            )

        if request.file_count != len(request.files):
            raise ApiException(
                422,
                "BUNDLE_FILE_COUNT_MISMATCH",
                "fileCount와 files 배열의 개수가 일치하지 않습니다.",
                job_id=job_id,
            )
        listed_total = sum(item.size_bytes for item in request.files)
        if request.total_size_bytes != listed_total:
            raise ApiException(
                422,
                "BUNDLE_TOTAL_SIZE_MISMATCH",
                "totalSizeBytes와 files 배열의 크기 합계가 일치하지 않습니다.",
                job_id=job_id,
            )
        if request.file_count != job["expected_file_count"]:
            raise ApiException(
                422,
                "DECLARED_FILE_COUNT_MISMATCH",
                "작업 등록 시 선언한 파일 개수와 일치하지 않습니다.",
                job_id=job_id,
            )
        if request.total_size_bytes != job["expected_total_size_bytes"]:
            raise ApiException(
                422,
                "DECLARED_TOTAL_SIZE_MISMATCH",
                "작업 등록 시 선언한 전체 크기와 일치하지 않습니다.",
                job_id=job_id,
            )

        uploaded = {
            row["relative_path"]: row for row in self.database.fetch_files(job_id)
        }
        requested = {
            validate_relative_path(item.relative_path): item for item in request.files
        }
        if set(uploaded) != set(requested):
            raise ApiException(
                422,
                "BUNDLE_FILE_SET_MISMATCH",
                "업로드된 파일과 완료 요청의 파일 목록이 일치하지 않습니다.",
                job_id=job_id,
                details={
                    "missing": sorted(set(requested) - set(uploaded)),
                    "unexpected": sorted(set(uploaded) - set(requested)),
                },
            )
        for path, item in requested.items():
            row = uploaded[path]
            if row["size_bytes"] != item.size_bytes or row["sha256"] != item.sha256:
                raise ApiException(
                    422,
                    "BUNDLE_FILE_METADATA_MISMATCH",
                    f"파일 메타데이터가 일치하지 않습니다: {path}",
                    job_id=job_id,
                )

        verified_at = isoformat_kst()
        self.database.update_job(
            job_id,
            status="FILES_VERIFIED",
            progress=35,
            verified_at=verified_at,
        )
        self.write_manifest(job_id)
        response = {
            "jobId": job_id,
            "status": "FILES_VERIFIED",
            "verifiedFileCount": request.file_count,
            "verifiedAt": verified_at,
        }
        self.save_idempotent_response(
            endpoint, idempotency_key, request_hash, 200, response
        )
        return 200, response

    @synchronized
    def request_report(
        self,
        job_id: str,
        request: GenerateReportRequest,
        idempotency_key: str,
    ) -> tuple[int, dict[str, Any]]:
        job = self.require_job(job_id)
        endpoint = f"POST:/api/v1/jobs/{job_id}/report"
        request_hash = self.request_hash(request)
        cached = self.get_idempotent_response(
            endpoint, idempotency_key, request_hash
        )
        if cached:
            return cached
        if job["status"] != "FILES_VERIFIED":
            raise ApiException(
                409,
                "JOB_STATE_CONFLICT",
                f"현재 상태({job['status']})에서는 보고서를 생성할 수 없습니다.",
                job_id=job_id,
            )
        supported = self.settings.supported_experiment_codes
        if supported and job["experiment_code"].upper() not in supported:
            raise ApiException(
                422,
                "PROCESSOR_NOT_FOUND",
                "해당 실험코드를 처리할 processor가 등록되어 있지 않습니다.",
                job_id=job_id,
            )

        accepted_at = isoformat_kst()
        options = request.options.model_dump(by_alias=True)
        queue_payload = {
            "jobId": job_id,
            "pk": {
                "requestNumber": job["request_number"],
                "experimentCode": job["experiment_code"],
                "equipmentCode": job["equipment_code"],
                "operatorId": job["operator_id"],
            },
            "requestedAt": request.requested_at or accepted_at,
            "acceptedAt": accepted_at,
            "options": options,
            "inputDirectory": (
                Path(job["root_relative_path"]) / "input"
            ).as_posix(),
            "processedDirectory": (
                Path(job["root_relative_path"]) / "processed"
            ).as_posix(),
            "reportDirectory": (
                Path(job["root_relative_path"]) / "report"
            ).as_posix(),
        }
        job_root = self.settings.storage_root / job["root_relative_path"]
        atomic_write_json(job_root / "queue" / "report-request.json", queue_payload)
        self.database.update_job(
            job_id,
            status="QUEUED",
            progress=40,
            report_requested_at=accepted_at,
            report_options_json=json.dumps(options, ensure_ascii=False),
        )
        self.write_manifest(job_id)
        response = {
            "jobId": job_id,
            "status": "QUEUED",
            "acceptedAt": accepted_at,
        }
        self.save_idempotent_response(
            endpoint, idempotency_key, request_hash, 202, response
        )
        return 202, response

    def status_response(self, job_id: str) -> dict[str, Any]:
        job = self.require_job(job_id)
        error = json.loads(job["error_json"]) if job["error_json"] else None
        return {
            "jobId": job_id,
            "pk": {
                "requestNumber": job["request_number"],
                "experimentCode": job["experiment_code"],
                "equipmentCode": job["equipment_code"],
                "operatorId": job["operator_id"],
            },
            "status": job["status"],
            "progress": job["progress"],
            "createdAt": job["created_at"],
            "processingStartedAt": job["processing_started_at"],
            "completedAt": job["completed_at"],
            "error": error,
        }

    def write_manifest(self, job_id: str) -> None:
        job = self.database.fetch_job(job_id)
        if not job:
            return
        files = self.database.fetch_files(job_id)
        manifest = {
            "jobId": job_id,
            "pk": {
                "requestNumber": job["request_number"],
                "experimentCode": job["experiment_code"],
                "equipmentCode": job["equipment_code"],
                "operatorId": job["operator_id"],
            },
            "sourcePc": {
                "hostName": job["source_host_name"],
                "declaredIpAddress": job["declared_ip_address"],
                "observedRemoteIp": job["observed_remote_ip"],
                "clientVersion": job["client_version"],
            },
            "bundle": {
                "expectedFileCount": job["expected_file_count"],
                "expectedTotalSizeBytes": job["expected_total_size_bytes"],
                "uploadedFileCount": len(files),
                "uploadedTotalSizeBytes": sum(row["size_bytes"] for row in files),
            },
            "status": job["status"],
            "progress": job["progress"],
            "createdAt": job["created_at"],
            "uploadExpiresAt": job["upload_expires_at"],
            "verifiedAt": job["verified_at"],
            "reportRequestedAt": job["report_requested_at"],
            "processingStartedAt": job["processing_started_at"],
            "completedAt": job["completed_at"],
            "files": [
                {
                    "fileId": row["file_id"],
                    "relativePath": row["relative_path"],
                    "sizeBytes": row["size_bytes"],
                    "sha256": row["sha256"],
                    "lastModifiedAt": row["last_modified_at"],
                    "uploadedAt": row["uploaded_at"],
                }
                for row in files
            ],
            "error": json.loads(job["error_json"]) if job["error_json"] else None,
        }
        job_root = self.settings.storage_root / job["root_relative_path"]
        atomic_write_json(job_root / "manifest.json", manifest)

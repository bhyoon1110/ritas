from __future__ import annotations

import json
from typing import Any

from .config import Settings
from .database import Database
from .storage import atomic_write_json


def write_manifest(settings: Settings, database: Database, job_id: str) -> None:
    """현재 작업 상태를 experiment PC가 읽을 수 있는 manifest로 기록한다."""
    job = database.fetch_job(job_id)
    if not job:
        return
    files = database.fetch_files(job_id)
    manifest: dict[str, Any] = {
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
    job_root = settings.storage_root / job["root_relative_path"]
    atomic_write_json(job_root / "manifest.json", manifest)

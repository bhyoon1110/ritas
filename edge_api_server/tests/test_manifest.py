from __future__ import annotations

import json

from app.config import Settings
from app.manifest import write_manifest


class _Database:
    def fetch_job(self, job_id: str):
        return {
            "job_id": job_id,
            "request_number": "REQ-1",
            "experiment_code": "FT-IR",
            "equipment_code": "FTIR-01",
            "operator_id": "operator",
            "source_host_name": "LAB-PC",
            "declared_ip_address": "10.0.0.1",
            "observed_remote_ip": "10.0.0.2",
            "client_version": "1.0",
            "expected_file_count": 1,
            "expected_total_size_bytes": 12,
            "status": "QUEUED",
            "progress": 50,
            "created_at": "2026-01-01T00:00:00+09:00",
            "upload_expires_at": "2026-01-02T00:00:00+09:00",
            "verified_at": None,
            "report_requested_at": None,
            "processing_started_at": None,
            "completed_at": None,
            "root_relative_path": "jobs/job-1",
            "error_json": None,
        }

    def fetch_files(self, _: str):
        return [
            {
                "file_id": "file-1",
                "relative_path": "raw/sample.csv",
                "size_bytes": 12,
                "sha256": "a" * 64,
                "last_modified_at": None,
                "uploaded_at": "2026-01-01T00:00:00+09:00",
            }
        ]


def test_write_manifest_is_independent_of_edge_service(tmp_path) -> None:
    settings = Settings(storage_root=tmp_path)

    write_manifest(settings, _Database(), "job-1")

    manifest = json.loads(
        (tmp_path / "jobs" / "job-1" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["jobId"] == "job-1"
    assert manifest["files"][0]["relativePath"] == "raw/sample.csv"

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from app.preview_report import PreviewReportSendRequest, send_preview_report_package
from app.report_queue import (
    ReportQueueError,
    enqueue_report_package,
    register_generated_report_package,
)


class CapturingDatabase:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}
        self.report_runs: list[dict[str, object]] = []
        self.artifacts: list[dict[str, object]] = []

    def fetch_job(self, _job_id: str) -> None:
        return None

    def register_report_run(self, **values: object) -> dict[str, object]:
        self.report_runs.append(values)
        return values

    def upsert_report_artifact(self, **values: object) -> None:
        self.artifacts.append(values)

    def enqueue_report_transfer(self, **values: object) -> dict[str, object]:
        self.values = values
        return {
            "transfer_id": values["transfer_id"],
            "status": "PENDING",
        }


def settings(storage_root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        storage_root=storage_root,
        report_storage_key="RIST_REPORTS",
        report_transfer_max_attempts=5,
    )


def write_zip(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("report.pptx", b"pptx")
        archive.writestr("report.md", "report")
    return path.read_bytes()


def test_enqueue_report_package_registers_relative_shared_path_and_integrity(
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "shared"
    package = storage_root / "jobs" / "job-1" / "report" / "report-package.zip"
    package_bytes = write_zip(package)
    database = CapturingDatabase()

    result = enqueue_report_package(
        settings=settings(storage_root),
        database=database,
        report_id="job-1",
        package_path=package,
        source_job_id="job-1",
        request_number="REQ-001",
        experiment_code="FT-IR",
        equipment_code="FTIR-01",
        operator_id="operator-1",
        report_options={"reportFormats": ["PPTX"]},
    )

    assert result["queued"] is True
    assert result["status"] == "PENDING"
    assert result["storageKey"] == "RIST_REPORTS"
    assert result["packageRelativePath"] == (
        "jobs/job-1/report/report-package.zip"
    )
    assert database.values["package_relative_path"] == result["packageRelativePath"]
    assert database.values["package_size_bytes"] == len(package_bytes)
    assert database.values["package_sha256"] == hashlib.sha256(package_bytes).hexdigest()
    assert database.values["max_attempts"] == 5
    assert database.report_runs[0]["report_id"] == "job-1"
    assert {item["artifact_type"] for item in database.artifacts} == {"ZIP"}


def test_register_generated_report_extracts_individual_artifacts(tmp_path: Path) -> None:
    storage_root = tmp_path / "shared"
    source = tmp_path / "temporary" / "report-package.zip"
    write_zip(source)
    database = CapturingDatabase()

    published = register_generated_report_package(
        settings=settings(storage_root),
        database=database,
        report_id="preview-1",
        package_path=source,
        experiment_code="FT-IR",
    )

    assert published.is_file()
    assert database.report_runs[0]["is_test"] is True
    assert {item["artifact_type"] for item in database.artifacts} == {"ZIP", "PPTX"}


def test_enqueue_report_package_rejects_path_outside_shared_root(
    tmp_path: Path,
) -> None:
    package = tmp_path / "outside" / "report-package.zip"
    write_zip(package)

    with pytest.raises(ReportQueueError) as captured:
        enqueue_report_package(
            settings=settings(tmp_path / "shared"),
            database=CapturingDatabase(),
            report_id="job-1",
            package_path=package,
            source_job_id="job-1",
            request_number="REQ-001",
            experiment_code="XRD",
            equipment_code="XRD-01",
            operator_id="operator-1",
        )

    assert captured.value.code == "REPORT_PACKAGE_OUTSIDE_SHARED_STORAGE"
    assert captured.value.retryable is False


def test_enqueue_report_package_rejects_invalid_zip(tmp_path: Path) -> None:
    storage_root = tmp_path / "shared"
    package = storage_root / "report-package.zip"
    package.parent.mkdir(parents=True)
    package.write_bytes(b"not-a-zip")

    with pytest.raises(ReportQueueError) as captured:
        enqueue_report_package(
            settings=settings(storage_root),
            database=CapturingDatabase(),
            report_id="job-1",
            package_path=package,
            source_job_id="job-1",
            request_number="REQ-001",
            experiment_code="TEM",
            equipment_code="TEM-01",
            operator_id="operator-1",
        )

    assert captured.value.code == "REPORT_PACKAGE_INVALID_ZIP"
    assert captured.value.retryable is False


def test_send_preview_report_package_publishes_and_queues(
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "shared"
    source = tmp_path / "temporary" / "report-package.zip"
    package_bytes = write_zip(source)
    database = CapturingDatabase()
    job = SimpleNamespace(
        job_id="tem-job-1",
        status="completed",
        package_path=source,
    )
    payload = PreviewReportSendRequest(
        requestNumber="REQ-001",
        experimentCode="TEM",
        equipmentCode="TEM-EDGE-01",
        operatorId="operator-1",
    )

    result = send_preview_report_package(
        settings=settings(storage_root),
        database=database,
        job=job,
        payload=payload,
    )

    published = (
        storage_root
        / "web-reports"
        / "TEM"
        / "tem-job-1"
        / "report-package.zip"
    )
    assert published.read_bytes() == package_bytes
    assert result["queued"] is True
    assert result["sent"] is False
    assert result["packageRelativePath"] == (
        "web-reports/TEM/tem-job-1/report-package.zip"
    )
    assert database.values["request_number"] == "REQ-001"
    assert database.values["experiment_code"] == "TEM"
    assert database.values["equipment_code"] == "TEM-EDGE-01"
    assert database.values["operator_id"] == "operator-1"
    assert database.values["package_sha256"] == hashlib.sha256(package_bytes).hexdigest()
    assert database.report_runs[0]["is_test"] is False
    assert any(item["artifact_type"] == "ZIP" for item in database.artifacts)

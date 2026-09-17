"""C# common-job adapters for the production XRD and TEM report builders."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .errors import ApiException
from .experiment_routing import analysis_type_for_job
from .path_bootstrap import add_project_package_paths
from .report.package import build_report_package

add_project_package_paths()


@dataclass(frozen=True)
class SpecializedReportResult:
    analysis_type: str
    package_path: Path
    primary_path: Path


def requested_report_options(job: dict[str, Any]) -> tuple[list[str], bool]:
    try:
        options = json.loads(job.get("report_options_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        options = {}
    formats = options.get("reportFormats")
    if not isinstance(formats, list) or not formats:
        formats = [options.get("reportFormat") or "PPTX"]
    return [str(item).upper() for item in formats], bool(options.get("includeRawFiles"))


def validate_specialized_report_request(
    analysis_type: str | None,
    formats: list[str],
) -> None:
    selected = set(formats)
    if analysis_type == "XRD" and selected != {"HTML"}:
        raise ApiException(
            422,
            "XRD_REPORT_FORMAT_REQUIRED",
            "XRD C# 작업은 reportFormats를 HTML 하나로 요청해야 합니다.",
        )
    if analysis_type == "TEM" and selected != {"PPTX"}:
        raise ApiException(
            422,
            "TEM_REPORT_FORMAT_REQUIRED",
            "TEM C# 작업은 reportFormats를 PPTX 하나로 요청해야 합니다.",
        )


def specialized_report_type(
    settings: Settings,
    job: dict[str, Any],
) -> str | None:
    """Return the built-in raw adapter type, preserving custom JSON processors."""

    analysis_type = analysis_type_for_job(job, settings)
    if analysis_type not in {"XRD", "TEM"}:
        return None
    job_root = settings.storage_root / str(job["root_relative_path"])
    processed_dir = job_root / "processed"
    if processed_dir.is_dir() and any(processed_dir.rglob("*.json")):
        return None
    return analysis_type


def generate_specialized_report(
    settings: Settings,
    job: dict[str, Any],
) -> SpecializedReportResult | None:
    analysis_type = specialized_report_type(settings, job)
    if analysis_type is None:
        return None
    formats, include_raw_files = requested_report_options(job)
    validate_specialized_report_request(analysis_type, formats)
    if analysis_type == "XRD":
        return _generate_xrd_report(settings, job, include_raw_files=include_raw_files)
    if analysis_type == "TEM":
        return _generate_tem_report(settings, job, include_raw_files=include_raw_files)
    return None


def _write_summary(
    report_dir: Path,
    job: dict[str, Any],
    *,
    analysis_type: str,
    primary_name: str,
) -> None:
    lines = [
        f"# {analysis_type} 분석 보고서",
        "",
        f"- 의뢰번호: {job.get('request_number') or '-'}",
        f"- LIMS 실험코드: {job.get('experiment_code') or '-'}",
        f"- 장비코드: {job.get('equipment_code') or '-'}",
        f"- 주 보고서: {primary_name}",
        "",
    ]
    (report_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def _generate_xrd_report(
    settings: Settings,
    job: dict[str, Any],
    *,
    include_raw_files: bool,
) -> SpecializedReportResult:
    # Import lazily so the background worker does not initialize the web routers
    # unless it actually processes an XRD job.
    from .xrd_portable_html import make_xrd_html_portable
    from .xrd_web import (
        _build_xrd_html_from_inputs_with_settings,
        _save_xrd_bundle_session_files,
    )

    job_root = settings.storage_root / str(job["root_relative_path"])
    input_dir = job_root / "input"
    processed_dir = job_root / "processed" / "xrd"
    report_dir = job_root / "report"
    shutil.rmtree(processed_dir, ignore_errors=True)
    shutil.rmtree(report_dir, ignore_errors=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    raw_paths, pdf_dir, table_paths, image_paths = _save_xrd_bundle_session_files(
        input_dir,
        processed_dir,
    )
    html_text = _build_xrd_html_from_inputs_with_settings(
        settings,
        root=processed_dir,
        raw_paths=raw_paths,
        pdf_dir=pdf_dir,
        table_paths=table_paths,
        image_paths=image_paths,
        origin=True,
    )
    primary_path = report_dir / "report.html"
    primary_path.write_text(make_xrd_html_portable(html_text), encoding="utf-8")
    _write_summary(
        report_dir,
        job,
        analysis_type="XRD",
        primary_name=primary_path.name,
    )
    package_path = build_report_package(
        report_dir,
        input_dir,
        include_raw_files=include_raw_files,
    )
    return SpecializedReportResult("XRD", package_path, primary_path)


def _generate_tem_report(
    settings: Settings,
    job: dict[str, Any],
    *,
    include_raw_files: bool,
) -> SpecializedReportResult:
    from ahn.processor import build_outputs

    from .ahn_web import (
        _extract_pending_zips,
        _find_ahn_input_root,
        _has_reportable_data,
        _validate_ahn_upload_files,
    )

    job_root = settings.storage_root / str(job["root_relative_path"])
    input_dir = job_root / "input"
    report_dir = job_root / "report"
    shutil.rmtree(report_dir, ignore_errors=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    _validate_ahn_upload_files(input_dir)
    _extract_pending_zips(input_dir)
    analysis_root = _find_ahn_input_root(input_dir)
    primary_path = report_dir / "report.pptx"
    manifest = build_outputs(
        input_dir=analysis_root,
        output_dir=report_dir,
        pptx_path=primary_path,
        copy_raw_spreadsheets=True,
    )
    summary = manifest.get("summary") if isinstance(manifest, dict) else None
    if not isinstance(summary, dict) or not _has_reportable_data(summary):
        raise ApiException(
            400,
            "TEM_NO_REPORT_DATA",
            "입력 폴더에서 TEM, STEM, EDS, 코팅층 분석 대상 데이터를 찾지 못했습니다.",
        )
    _write_summary(
        report_dir,
        job,
        analysis_type="TEM",
        primary_name=primary_path.name,
    )
    package_path = build_report_package(
        report_dir,
        input_dir,
        include_raw_files=include_raw_files,
    )
    return SpecializedReportResult("TEM", package_path, primary_path)

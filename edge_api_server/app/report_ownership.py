"""Durable report ownership derived only from authenticated server workspaces."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .errors import ApiException
from .experiment_routing import analysis_type_for_report

HEX_ID = re.compile(r"^[0-9a-f]{32}$")


def _metadata(path: Path, root: Path) -> dict:
    """Read small server metadata without following workspace symlinks."""
    path, root = path.absolute(), root.absolute()
    if not path.is_relative_to(root):
        return {}
    current = path
    while current != root.parent:
        if current.is_symlink():
            return {}
        current = current.parent
    try:
        with path.open("rb") as stream:
            raw = stream.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            return {}
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def legacy_owned_reports(request, owner: str, report_id: str | None = None):
    """Find historical web reports from trusted job ownership, including archived work.

    The browser's operator text, report options, filenames, and SSO identity are
    never proof of ownership. Unknown old reports remain administrator-only.
    """
    from .preview_report import PreviewReportJobStore
    from . import ahn_web, xrd_web, tem_history
    found = {}
    store = getattr(request.app.state, "preview_report_job_store", None)
    if isinstance(store, PreviewReportJobStore):
        with store._lock:
            for key, job in store._jobs.items():
                if job.owner_user_id == owner and (report_id is None or key == report_id):
                    project = {"ftir-report-package.zip": "FTIR", "raman-report-package.zip": "RAMAN"}.get(job.filename)
                    if project:
                        found[key] = project
    for module, attr, lock, project in (
        (ahn_web, "_ahn_report_jobs", ahn_web._ahn_report_jobs_lock, "TEM"),
        (xrd_web, "_xrd_report_jobs", xrd_web._xrd_report_jobs_lock, "XRD"),
    ):
        with lock:
            for key, job in getattr(module, attr).items():
                if getattr(job, "owner_user_id", None) == owner and (report_id is None or key == report_id):
                    if getattr(job, "settings", None) == request.app.state.settings:
                        found[key] = project
    settings = request.app.state.settings
    xrd_root = settings.storage_root / "xrd-workbench-v1"
    jobs = xrd_root / "users" / hashlib.sha256(owner.encode()).hexdigest() / "jobs"
    directories = [jobs / report_id] if report_id and HEX_ID.fullmatch(report_id) else ([] if report_id else jobs.glob("*"))
    for directory in directories:
        key = directory.name
        if not HEX_ID.fullmatch(key):
            continue
        row = _metadata(directory / "job.json", xrd_root)
        if row.get("job_id") == key and row.get("kind") == "zip":
            found[key] = "XRD"
    tem_root = tem_history.root(settings)
    directories = [tem_root / ("work-" + report_id)] if report_id and HEX_ID.fullmatch(report_id) else ([] if report_id else tem_root.glob("work-*"))
    for directory in directories:
        key = directory.name.removeprefix("work-")
        if not HEX_ID.fullmatch(key):
            continue
        row = _metadata(directory / "job.json", tem_root)
        if row.get("job_id") == key and row.get("owner_user_id") == owner:
            found[key] = "TEM"
    return found


def backfill_owned_reports(request, context, report_id: str | None = None) -> None:
    database = request.app.state.database
    for key, project in legacy_owned_reports(request, context.user_id, report_id).items():
        if database.fetch_report_owner(key) is not None:
            continue
        report = database.fetch_report_run(key)
        if report and analysis_type_for_report(report, request.app.state.settings) == project:
            database.bind_report_owner(key, context.user_id, project)


def require_report_owner(request, context, report) -> None:
    if context.is_admin:
        return
    database = request.app.state.database
    key = str(report["report_id"])
    owner = database.fetch_report_owner(key)
    if owner is None:
        backfill_owned_reports(request, context, key)
        owner = database.fetch_report_owner(key)
    if owner is not None and owner["owner_user_id"] == context.user_id:
        return
    # Existing machine/C# reports have no browser owner. Preserve their explicit
    # project-scoped review contract only when a real jobs row backs the report.
    if owner is None and report.get("source_job_id") and database.fetch_job(report["source_job_id"]):
        return
    raise ApiException(404, "REPORT_NOT_FOUND", "본인의 보고서 생성 기록을 찾을 수 없습니다.")


def require_report_job_owner(request, job, project: str) -> None:
    """Protect legacy job status/download/send aliases before reading artifacts."""
    if project in {"FTIR", "RAMAN"} and getattr(job, "filename", None) != project.lower() + "-report-package.zip":
        raise ApiException(404, project + "_REPORT_JOB_NOT_FOUND", "보고서 작업을 찾을 수 없습니다.")
    settings = getattr(request.app.state, "settings", None)
    if not getattr(settings, "auth_enabled", False):
        return
    from .auth import require_context
    context = require_context(request)
    if context.status != "ACTIVE":
        raise ApiException(403, "ACCOUNT_INACTIVE", "활성 회원만 보고서를 조회할 수 있습니다.")
    if not context.is_admin and project not in context.projects:
        raise ApiException(403, "PROJECT_ACCESS_DENIED", "이 프로젝트의 보고서를 조회할 권한이 없습니다.")
    if not context.is_admin and getattr(job, "owner_user_id", None) != context.user_id:
        raise ApiException(404, project + "_REPORT_JOB_NOT_FOUND", "본인의 보고서 작업을 찾을 수 없습니다.")

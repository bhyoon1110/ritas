"""Read-only, owner-scoped home dashboard. Never parse RAW/ZIP/PPT on home load."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .auth import PROJECT_CODES, _context_payload, require_context
from .time_utils import KST

router = APIRouter()
logger = logging.getLogger(__name__)
LIMIT = 1000
ID = re.compile(r"^[0-9a-f]{32}$")
LABELS = {"FTIR": "FT-IR", "RAMAN": "Raman", "XRD": "XRD", "TEM": "TEM"}
BASE = "/api/v1/xrd/workbench"


def timestamp(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        return datetime.fromtimestamp(0, timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def small_json(path, root):
    """Bound untrusted metadata; don't follow symlinks or load raw project arrays."""
    if path.is_symlink() or path.parent.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("unsafe metadata path")
    with path.open("rb") as stream:
        data = stream.read(128 * 1024 + 1)
    if len(data) > 128 * 1024:
        raise ValueError("oversized metadata")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("invalid metadata")
    return value


def folders(root, warnings, label, prefix=""):
    if root.is_symlink():
        raise ValueError("unsafe catalog path")
    if not root.exists():
        return []
    result = []
    with os.scandir(root) as entries:
        for entry in entries:
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                continue
            key = entry.name.removeprefix(prefix)
            if not entry.name.startswith(prefix) or not ID.fullmatch(key):
                continue
            if len(result) == LIMIT:
                warnings.append(label + ": 조회 한도 내 작업만 표시합니다. 전체 이력은 실험 화면에서 확인하세요.")
                break
            result.append(Path(entry.path))
    return result


def item(project, job_id, title, status, updated, request_number="", href=None, action=None, parent=None, completed=None):
    return dict(id=project + ":" + job_id, jobId=job_id, project=project, title=str(title or LABELS[project] + " 보고서")[:200],
                status=status, updatedAt=timestamp(updated).isoformat(), requestNumber=str(request_number or "")[:128],
                href=href, action=action, parent=parent,
                completedAt=timestamp(completed if completed is not None else updated).isoformat() if status == "completed" else None)


def spectra(request, owner, projects, warnings):
    from .preview_report import PreviewReportJobStore
    store = getattr(request.app.state, "preview_report_job_store", None)
    if not isinstance(store, PreviewReportJobStore):
        return [], set()
    with store._lock:
        jobs = [dict(vars(j)) for j in store._jobs.values() if j.owner_user_id == owner]
    rows, ids = [], set()
    for job in jobs:
        project = {"ftir-report-package.zip": "FTIR", "raman-report-package.zip": "RAMAN"}.get(job["filename"])
        if project not in projects:
            continue
        jid, status = job["job_id"], job["status"]
        ids.add(jid)
        transfer = job.get("transfer_metadata") or {}
        editable = status == "completed" and job.get("tmp_root") and (job["tmp_root"] / "job/report/report.json").is_file()
        if status == "completed" and not editable:
            continue
        rows.append(item(project, jid, LABELS[project] + " 보고서 · " + jid[:8], status, job["updated_at"], transfer.get("requestNumber"),
                         f"/api/v1/{project.lower()}/report/jobs/{quote(jid, safe='')}/edit" if editable else "/" + project.lower(),
                         "보고서 편집" if editable else "실험 화면", job.get("parent_job_id")))
    return rows, ids


def tem(request, owner, projects, warnings):
    if "TEM" not in projects:
        return [], set()
    from . import ahn_web, tem_history
    settings = request.app.state.settings
    root = tem_history.root(settings)
    records = {}
    for directory in folders(root, warnings, "TEM", "work-"):
        path = directory / "job.json"
        if not path.exists():
            continue  # Uploads do not have a report yet.
        try:
            record = small_json(path, root)
            if record.get("owner_user_id") == owner and ID.fullmatch(str(record.get("job_id", ""))):
                records[record["job_id"]] = record
                record["editable"] = (directory / "output" / "tem-report.pptx").is_file()
                # Output locations are relative and validated by the existing editor.
                if record.get("pptx_path"):
                    p = directory / record["pptx_path"]
                    record["editable"] = p.resolve().is_relative_to(directory.resolve()) and p.is_file()
                if record.get("status") in {"queued", "running"}:
                    record["status"] = "interrupted"
        except (OSError, ValueError, TypeError):
            warnings.append("TEM: 일부 이력을 읽지 못했습니다. 작업 이력에서 확인하세요.")
    with ahn_web._ahn_report_jobs_lock:
        for job in ahn_web._ahn_report_jobs.values():
            if job.owner_user_id == owner and tem_history.root(job.settings) == root:
                record = dict(vars(job))
                record["editable"] = bool(job.pptx_path and job.pptx_path.is_file())
                records[job.job_id] = record
    rows, ids = [], set()
    for jid, r in records.items():
        if r.get("history_deleted_at"):
            continue
        ids.add(jid)
        editable = r["status"] == "completed" and r.get("editable")
        transfer = r.get("transfer_metadata") or {}
        rows.append(item("TEM", jid, r.get("history_title") or "TEM 보고서", r["status"], r.get("updated_at"),
                         transfer.get("requestNumber"), f"/api/v1/tem/report/jobs/{jid}/edit" if editable else "/tem",
                         "보고서 편집" if editable else "작업 이력", r.get("parent_job_id"), r.get("history_finished_at") or r.get("created_at")))
    return rows, ids


def xrd(request, owner, projects, warnings):
    if "XRD" not in projects or not getattr(request.app.state, "xrd_workbench_enabled", False):
        return [], set()
    key = hashlib.sha256((owner or "development").encode()).hexdigest()
    root = request.app.state.settings.storage_root / "xrd-workbench-v1" / "users" / key
    if root.is_symlink():
        raise ValueError("unsafe owner directory")
    rows, ids, catalog = [], set(), {}
    for directory in folders(root / "uploads", warnings, "XRD"):
        sid = directory.name
        try:
            management = small_json(directory / "management.json", root) if (directory / "management.json").exists() else {}
            if management.get("archived_at") or not (directory / "project.json").is_file():
                continue
            summary = small_json(directory / "summary.json", root).get("item", {}) if (directory / "summary.json").exists() else {}
            title = management.get("label") or summary.get("label") or "XRD 프로젝트 " + sid[:8]
            updated = summary.get("created_at") or (directory / "project.json").stat().st_mtime
            catalog[sid] = item("XRD", sid, title, "ready", updated,
                                href=BASE + "/?project=" + sid + "&step=viewer", action="작업 이어하기")
        except (OSError, ValueError, TypeError):
            warnings.append("XRD: 일부 프로젝트 요약을 읽지 못했습니다. 프로젝트 관리에서 확인하세요.")
    for directory in folders(root / "jobs", warnings, "XRD"):
        try:
            r = small_json(directory / "job.json", root)
            if r.get("job_id") != directory.name or r.get("kind") not in {"import", "zip", "pdf", "pptx", "preview"}:
                continue
            sid, status = r.get("storage_id"), r.get("status")
            if sid and sid not in catalog:
                continue  # Archived/purged projects must not reappear.
            if r.get("kind") == "zip":
                ids.add(directory.name)
            if status in {"queued", "running"}:
                try:
                    os.kill(int(r["pid"]), 0)
                except (ProcessLookupError, ValueError, KeyError):
                    status = "interrupted"
            if status == "completed" and r.get("kind") != "zip":
                continue
            if sid and timestamp(r.get("created_at")) < timestamp(catalog[sid]["updatedAt"]):
                continue
            v = item("XRD", directory.name, catalog[sid]["title"] if sid else "XRD 처리 작업", status,
                     r.get("created_at"), (r.get("transfer") or {}).get("requestNumber"),
                     BASE + "/report.html?project=" + sid if sid else "/xrd", "보고서 편집" if sid else "실험 화면",
                     completed=(directory / "job.json").stat().st_mtime)
            if sid:
                catalog[sid] = v
            else:
                rows.append(v)
        except (OSError, ValueError, TypeError):
            warnings.append("XRD: 일부 처리 이력을 읽지 못했습니다.")
    return rows + list(catalog.values()), ids


def transfers(database, ids):
    rows = []
    if database is None:
        return None
    ordered_ids = sorted(ids)
    with database.transaction() as connection:
        for offset in range(0, len(ordered_ids), 200):
            group = ordered_ids[offset:offset+200]
            rows.extend(connection.execute(
                "SELECT r.report_id, r.request_number, r.experiment_code, t.status, t.requested_at FROM report_runs r "
                "JOIN report_transfers t ON t.report_id = r.report_id AND t.destination = 'LIMS' "
                "WHERE r.deleted_at IS NULL AND r.report_id IN (" + ",".join("?" for _ in group) + ") "
                "ORDER BY t.requested_at DESC", tuple(group)).fetchall())
    return sorted(rows, key=lambda r: timestamp(r["requested_at"]), reverse=True)


def feedback(database, context):
    if database is None or context is None:
        return None
    from .voc import VocService
    scope, params = VocService._scope(context)
    if context.is_admin:
        scope += " AND status IN ('OPEN','IN_PROGRESS')"
    else:
        scope += " AND status = 'RESOLVED' AND author_user_id = ?"
        params.append(context.user_id)
    with database.transaction() as connection:
        count = connection.execute("SELECT COUNT(*) AS n FROM voc_requests WHERE " + scope, tuple(params)).fetchone()["n"]
        rows = connection.execute("SELECT project, title, status FROM voc_requests WHERE " + scope +
                                  " ORDER BY updated_at DESC LIMIT 5", tuple(params)).fetchall()
    return dict(count=int(count), items=[dict(project=r["project"], title=r["title"], status=r["status"],
                                             href="/voc?project=" + quote(r["project"])) for r in rows])


def administration(database, settings):
    if database is None:
        return None
    with database.transaction() as connection:
        pending = connection.execute("SELECT COUNT(*) AS n FROM app_users WHERE status = 'PENDING'").fetchone()["n"]
        reports = connection.execute("SELECT COUNT(*) AS n FROM report_runs WHERE deleted_at IS NULL").fetchone()["n"]
        failed = connection.execute("SELECT COUNT(*) AS n FROM report_transfers t JOIN report_runs r ON r.report_id=t.report_id "
                                    "WHERE r.deleted_at IS NULL AND t.status='FAILED' AND t.destination='LIMS'").fetchone()["n"]
    path = settings.storage_root.resolve()
    while not path.exists() and path != path.parent:
        path = path.parent
    usage = shutil.disk_usage(path)
    return dict(pendingUsers=int(pending), reports=int(reports), failedTransfers=int(failed),
                diskFreeBytes=usage.free, diskTotalBytes=usage.total)


@router.get("/api/v1/workspace/dashboard", tags=["workspace"])
def dashboard(request: Request):
    settings = request.app.state.settings
    context = require_context(request) if settings.auth_enabled else None
    if context and context.status != "ACTIVE":
        from .errors import ApiException
        raise ApiException(403, "ACCOUNT_INACTIVE", "활성 회원만 작업 현황을 조회할 수 있습니다.")
    projects = set(PROJECT_CODES if context is None or context.is_admin else context.projects) & set(PROJECT_CODES)
    owner = context.user_id if context else None
    warnings, rows, ids, unavailable = [], [], set(), []
    for name, collect in (("FTIR/Raman", spectra), ("TEM", tem), ("XRD", xrd)):
        try:
            found, report_ids = collect(request, owner, projects, warnings)
            rows.extend(found)
            ids.update(report_ids)
        except Exception:
            logger.exception("Workspace dashboard source unavailable: %s", name)
            unavailable.append(name)
            warnings.append(name + ": 작업 현황을 불러오지 못했습니다. 실험 화면은 계속 이용할 수 있습니다.")
    # Only a successfully completed revision supersedes its parent. A failed
    # edit must not hide the last valid, editable report.
    superseded = {r["parent"] for r in rows if r["parent"] and r["status"] == "completed"}
    rows = [r for r in rows if r["jobId"] not in superseded]
    rows.sort(key=lambda r: timestamp(r["updatedAt"]), reverse=True)
    database = getattr(request.app.state, "database", None)
    queue, voc, admin = None, None, None
    for name, read in (("전송 상태", lambda: transfers(database, ids)), ("VOC", lambda: feedback(database, context)),
                       ("운영 현황", lambda: administration(database, settings) if context and context.is_admin else None)):
        try:
            value = read()
            if name == "전송 상태": queue = value
            elif name == "VOC": voc = value
            else: admin = value
        except Exception:
            logger.exception("Workspace dashboard section unavailable: %s", name)
            warnings.append(name + ": 정보를 불러오지 못했습니다. 새로고침으로 다시 확인하세요.")
    today = datetime.now(KST).date()
    counts = dict(active=sum(r["status"] in {"queued", "running"} for r in rows),
                  completedToday=sum(r["status"] == "completed" and timestamp(r["completedAt"]).astimezone(KST).date() == today for r in rows),
                  failed=sum(r["status"] in {"failed", "interrupted"} for r in rows),
                  pendingTransfers=None if queue is None else sum(r["status"] in {"PENDING", "PROCESSING", "RETRY_WAIT"} for r in queue),
                  failedTransfers=None if queue is None else sum(r["status"] == "FAILED" for r in queue),
                  voc=None if voc is None else voc["count"])
    queue_items = []
    for r in queue or []:
        if r["status"] in {"PENDING", "PROCESSING", "RETRY_WAIT", "FAILED", "CANCELLED"}:
            queue_items.append(dict(title=r["request_number"] or "의뢰번호 미입력", status=r["status"],
                                    href="/reports/" + quote(str(r["report_id"]), safe="")))
    for r in rows:
        r.pop("parent", None)
    return JSONResponse(dict(user=_context_payload(context, settings) if context else None,
        projects=[p for p in PROJECT_CODES if p in projects], counts=counts, recent=rows[:100], total=len(rows),
        transfers=queue_items[:8], transferAvailable=queue is not None, voc=voc, admin=admin,
        warnings=list(dict.fromkeys(warnings)), unavailable=unavailable,
        generatedAt=datetime.now(timezone.utc).isoformat()), headers={"Cache-Control": "private, no-store", "Pragma": "no-cache"})

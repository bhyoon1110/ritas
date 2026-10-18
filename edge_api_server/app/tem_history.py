"""Durable, owner-scoped TEM workspaces, separate from the LIMS shared store.

Only directories created here are managed. Legacy temporary jobs and shared
report packages are never migrated or removed by this retention policy.
The Edge application uses one process (as does the TEM executor/upload store).
"""
from contextlib import asynccontextmanager, contextmanager
import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import time
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import Request, Query
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, Field

from .errors import ApiException
from .storage import atomic_write_json

DAY = 86400
_loaded = set()
_last_cleanup = {}
_usage_cache = {}


def _ahn():
    from . import ahn_web
    return ahn_web


def root(settings):
    from .config import Settings
    return Path(getattr(settings, "storage_root", None) or Settings.from_env().storage_root).resolve() / "tem-work-history"


def policy():
    def number(key, default, maximum):
        try:
            return max(1, min(maximum, int(os.getenv(key, default))))
        except ValueError:
            return default
    return {"retentionDays": number("RIST_TEM_HISTORY_DAYS", 30, 3650),
            "trashDays": number("RIST_TEM_HISTORY_TRASH_DAYS", 7, 365),
            "limitBytes": number("RIST_TEM_HISTORY_LIMIT_GB", 50, 100000) * 1024**3}


def managed(job):
    directory = getattr(job, "work_dir", None)
    if directory is None:
        return False
    return (not directory.is_symlink() and directory.resolve().parent == root(job.settings)
            and re.fullmatch(r"work-[0-9a-f]{32}", directory.name) is not None)


def tree_bytes(directory):
    total = 0
    for current, dirs, files in os.walk(directory, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(current) / d).is_symlink()]
        for name in files:
            path = Path(current) / name
            try:
                if not path.is_symlink():
                    total += path.stat().st_size
            except FileNotFoundError:
                pass
    return total


def storage_usage(settings, *, fresh=False):
    directory = root(settings)
    cached = _usage_cache.get(directory)
    if fresh or not cached or time.monotonic() - cached[0] > 30:
        cached = (time.monotonic(), tree_bytes(directory))
        _usage_cache[directory] = cached
    return cached[1]


def require_capacity(settings, additional=0):
    if storage_usage(settings) + additional >= policy()["limitBytes"]:
        raise ApiException(507, "TEM_HISTORY_FULL", "TEM 작업 저장 공간 한도에 도달했습니다. 작업 이력의 휴지통을 정리하거나 관리자에게 용량 한도 조정을 요청하세요.")


def new_workspace(settings):
    require_capacity(settings)
    directory = root(settings) / ("work-" + uuid4().hex)
    directory.mkdir(parents=True, mode=0o700)
    # An interrupted upload has no job record; only this marker authorizes
    # later removal of its abandoned, incomplete workspace.
    atomic_write_json(directory / "upload-marker.json", {"createdAt": time.time()})
    return directory


def save(job, *, force=True):
    if not managed(job):
        return
    ahn = _ahn()
    with ahn._ahn_report_jobs_lock:
        if not force and time.monotonic() - job.history_saved_at < 2:
            return
        if not job.work_dir.is_dir():
            return
        data = {name: getattr(job, name) for name in (
            "job_id", "owner_user_id", "parent_job_id", "created_at", "updated_at", "status",
            "progress_pct", "message", "error", "error_event_id", "transfer_metadata", "review_error",
            "history_title", "history_pinned", "history_deleted_at", "history_finished_at", "history_bytes")}
        # Persist only relative local paths; never restore arbitrary paths or
        # Python objects from metadata. The published ZIP has a local original.
        for name in ("input_root", "output_dir", "pptx_path", "analysis_path", "manifest_path"):
            try:
                data[name] = getattr(job, name).resolve().relative_to(job.work_dir.resolve()).as_posix()
            except ValueError:
                data[name] = "input"  # built-in example input is read-only
        atomic_write_json(job.work_dir / "job.json", data)
        job.history_saved_at = time.monotonic()


def load(settings, database=None, error_archive=None, usage_archive=None):
    ahn = _ahn()
    directory = root(settings)
    with ahn._ahn_report_jobs_lock:
        if directory in _loaded:
            return
        for entry in directory.glob("work-*/job.json"):
            if entry.is_symlink() or entry.parent.is_symlink() or entry.stat().st_size > 4 * 1024**2:
                continue
            try:
                data = json.loads(entry.read_text(encoding="utf-8"))
                if not re.fullmatch(r"[0-9a-f]{32}", data["job_id"]):
                    continue
                if data["job_id"] in ahn._ahn_report_jobs:
                    continue
                paths = {}
                for name in ("input_root", "output_dir", "pptx_path", "analysis_path", "manifest_path"):
                    path = (entry.parent / data.pop(name)).resolve()
                    if not path.is_relative_to(entry.parent.resolve()):
                        raise ValueError("workspace path outside root")
                    paths[name] = path
                job = ahn.AhnReportJob(**data, **paths, work_dir=entry.parent,
                    package_path=paths["output_dir"] / "tem-report-package.zip", manifest=None,
                    settings=settings, database=database, error_archive=error_archive,
                    usage_archive=usage_archive, usage_client_context={})
                if not managed(job):
                    continue
                if job.manifest_path.is_file():
                    job.manifest = json.loads(job.manifest_path.read_text(encoding="utf-8"))
                if job.status in {"queued", "running"}:
                    job.status = "failed"
                    job.message = "서버 재시작으로 처리가 중단되었습니다. 원본 파일을 다시 업로드하세요."
                    job.error = {"code": "TEM_SERVER_RESTARTED", "message": job.message}
                    job.history_finished_at = time.time()
                ahn._ahn_report_jobs[job.job_id] = job
                save(job)
            except (OSError, ValueError, TypeError, KeyError):
                ahn.logger.warning("TEM history metadata could not be loaded: %s", entry)
        _loaded.add(directory)


def prepare(request):
    state = request.app.state
    load(getattr(state, "settings", None), getattr(state, "database", None),
         getattr(state, "error_archive", None), getattr(state, "usage_archive", None))
    cleanup(getattr(state, "settings", None))


def busy(job):
    return job.status in {"queued", "running"} or job.active_revisions > 0


@contextmanager
def lease(job):
    """Keep source files alive throughout server-side reading or sending."""
    ahn = _ahn()
    with ahn._ahn_report_jobs_lock:
        if getattr(job, "history_deleted_at", None):
            raise ApiException(409, "TEM_HISTORY_TRASHED", "휴지통에서 작업을 복원한 뒤 사용하세요.")
        job.active_revisions = getattr(job, "active_revisions", 0) + 1
    try:
        yield
    finally:
        with ahn._ahn_report_jobs_lock:
            job.active_revisions -= 1


class HistoryFileResponse(FileResponse):
    def __init__(self, job, path, **kwargs):
        self.job = job
        super().__init__(path, **kwargs)

    async def __call__(self, scope, receive, send):
        with lease(self.job):
            await super().__call__(scope, receive, send)


def purge(job):
    # Caller holds the registry lock. Only a known local workspace is removable.
    if not managed(job) or not job.history_deleted_at or busy(job):
        raise ApiException(409, "TEM_HISTORY_BUSY", "진행 중이거나 휴지통에 없는 작업은 영구 삭제할 수 없습니다.")
    if time.time() - job.history_accessed_at < 60:
        raise ApiException(409, "TEM_HISTORY_IN_USE", "방금 열거나 다운로드한 작업입니다. 1분 후 다시 시도하세요.")
    shutil.rmtree(job.work_dir)
    _ahn()._ahn_report_jobs.pop(job.job_id, None)
    _usage_cache.pop(root(job.settings), None)


def cleanup(settings, *, force=False):
    ahn = _ahn()
    directory = root(settings)
    now = time.time()
    with ahn._ahn_report_jobs_lock:
        if not force and now - _last_cleanup.get(directory, 0) < 600:
            return
        _last_cleanup[directory] = now
        rules = policy()
        for job in list(ahn._ahn_report_jobs.values()):
            if not managed(job) or root(job.settings) != directory or busy(job) or job.history_pinned:
                continue
            if job.history_deleted_at:
                if now - job.history_deleted_at >= rules["trashDays"] * DAY and now - job.history_accessed_at >= 60:
                    purge(job)
            elif job.history_finished_at and now - job.history_finished_at >= rules["retentionDays"] * DAY:
                job.history_deleted_at = now
                try:
                    save(job)
                except Exception:
                    job.history_deleted_at = None
                    raise
        # Remove only abandoned upload workspaces, never unknown old folders.
        with ahn._ahn_upload_sessions_lock:
            active = {s.work_dir for s in ahn._ahn_upload_sessions.values()}
        active.update(j.work_dir for j in ahn._ahn_report_jobs.values() if managed(j))
        for marker in directory.glob("work-*/upload-marker.json"):
            folder = marker.parent
            if folder in active or folder.is_symlink() or (folder / "job.json").exists():
                continue
            if not re.fullmatch(r"work-[0-9a-f]{32}", folder.name):
                continue
            if now - marker.stat().st_mtime >= ahn.AHN_UPLOAD_SESSION_TTL_SECONDS:
                shutil.rmtree(folder)
                _usage_cache.pop(directory, None)


@asynccontextmanager
async def maintenance(app):
    """Periodic retention also runs when nobody has the history page open."""
    state = app.state
    def sweep():
        load(state.settings, getattr(state, "database", None),
             getattr(state, "error_archive", None), getattr(state, "usage_archive", None))
        _ahn()._cleanup_old_upload_sessions()
        cleanup(state.settings)
    stop = asyncio.Event()
    async def run():
        while not stop.is_set():
            try:
                await asyncio.to_thread(sweep)
            except Exception:
                _ahn().logger.exception("TEM history maintenance failed; files left for retry")
            try:
                await asyncio.wait_for(stop.wait(), timeout=600)
            except asyncio.TimeoutError:
                pass
    task = asyncio.create_task(run())
    try:
        yield
    finally:
        stop.set()
        await task


def context(request):
    if getattr(getattr(request.app.state, "settings", None), "auth_enabled", False):
        from .auth import require_context
        actor = require_context(request)
        if not actor.is_admin and "TEM" not in actor.projects:
            raise ApiException(403, "TEM_HISTORY_FORBIDDEN", "TEM 이용 권한이 필요합니다.")
        return actor
    return None


class HistoryAction(BaseModel):
    action: str = Field(pattern="^(rename|pin|unpin|trash|restore|purge)$")
    title: str = Field(default="", max_length=120)


def install(router):
    @router.get("/api/v1/tem/history", tags=["tem"])
    def listing(request: Request, q: str = Query("", max_length=120), trash: bool = False,
                page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=50)):
        actor = context(request)
        prepare(request)
        settings = getattr(request.app.state, "settings", None)
        ahn = _ahn()
        with ahn._ahn_report_jobs_lock:
            jobs = [j for j in ahn._ahn_report_jobs.values() if managed(j) and root(j.settings) == root(settings)
                    and (actor is None or actor.is_admin or j.owner_user_id == actor.user_id)]
            own_bytes = sum(j.history_bytes for j in jobs)
            jobs = [j for j in jobs if bool(j.history_deleted_at) == trash and q.casefold() in
                    (j.history_title + " " + j.job_id + " " + str((j.transfer_metadata or {}).get("requestNumber", ""))).casefold()]
            jobs.sort(key=lambda j: j.created_at, reverse=True)
            total = len(jobs)
            rows = [{"jobId": j.job_id, "title": j.history_title, "status": j.status, "message": j.message,
                     "createdAt": j.created_at, "finishedAt": j.history_finished_at, "bytes": j.history_bytes,
                     "pinned": j.history_pinned, "deletedAt": j.history_deleted_at, "busy": bool(busy(j)),
                     "requestNumber": (j.transfer_metadata or {}).get("requestNumber", ""),
                     "parentJobId": j.parent_job_id,
                     "editUrl": f"/api/v1/tem/report/jobs/{j.job_id}/edit" if j.status == "completed" and not trash else None}
                    for j in jobs[(page-1)*limit:page*limit]]
        return JSONResponse({"items": rows, "total": total, "page": page, "limit": limit,
            "storage": {"myBytes": own_bytes, "usedBytes": storage_usage(settings), **policy()}},
            headers={"Cache-Control": "no-store"})

    @router.post("/api/v1/tem/history/{job_id}", tags=["tem"])
    def action(request: Request, job_id: str, payload: HistoryAction):
        actor = context(request)
        origin = request.headers.get("origin")
        if request.headers.get("x-requested-with") != "RIST-TEM-History" or (origin and
            (urlsplit(origin).scheme, urlsplit(origin).netloc) != (request.url.scheme, request.url.netloc)):
            raise ApiException(403, "TEM_HISTORY_ORIGIN_INVALID", "같은 사이트의 작업 이력에서 변경하세요.")
        prepare(request)
        ahn = _ahn()
        with ahn._ahn_report_jobs_lock:
            job = ahn._ahn_report_jobs.get(job_id)
            if (not job or not managed(job) or root(job.settings) != root(getattr(request.app.state, "settings", None))
                    or (actor and not actor.is_admin and job.owner_user_id != actor.user_id)):
                raise ApiException(404, "TEM_HISTORY_NOT_FOUND", "작업 이력을 찾을 수 없습니다.")
            if busy(job):
                raise ApiException(409, "TEM_HISTORY_BUSY", "보고서 생성·수정·전송 중인 작업은 변경할 수 없습니다.")
            if payload.action == "purge":
                purge(job)
                return {"deleted": True}
            previous = {name: getattr(job, name) for name in (
                "history_title", "history_pinned", "history_deleted_at", "history_finished_at")}
            if payload.action == "restore":
                if not job.history_deleted_at:
                    raise ApiException(409, "TEM_HISTORY_NOT_TRASHED", "휴지통의 작업만 복원할 수 있습니다.")
                job.history_deleted_at = None
                job.history_finished_at = time.time()
            elif job.history_deleted_at:
                raise ApiException(409, "TEM_HISTORY_TRASHED", "휴지통에서 먼저 복원하세요.")
            elif payload.action == "rename":
                if not payload.title.strip():
                    raise ApiException(422, "TEM_HISTORY_TITLE_REQUIRED", "작업 이름을 입력하세요.")
                job.history_title = payload.title.strip()
            elif payload.action in {"pin", "unpin"}:
                job.history_pinned = payload.action == "pin"
            elif payload.action == "trash":
                if job.history_pinned:
                    raise ApiException(409, "TEM_HISTORY_PINNED", "보존 지정을 해제한 뒤 휴지통으로 이동하세요.")
                job.history_deleted_at = time.time()
            try:
                save(job)
            except Exception:
                for name, value in previous.items():
                    setattr(job, name, value)
                raise
        return {"saved": True}

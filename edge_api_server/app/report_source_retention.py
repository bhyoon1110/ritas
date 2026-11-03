"""Server-selected generation copies that share a delivered report's retention.

Originals are selected from server-owned workspace records, never browser
paths. Newer drafts, active work and explicit retention pins prevent cleanup.
"""
from contextlib import ExitStack, contextmanager
import hashlib
import json
from pathlib import Path
import re
import time

from fastapi import HTTPException
from .errors import ApiException


def _root(settings):
    return Path(settings.storage_root).expanduser().resolve()


def _safe(root, relative):
    if not isinstance(relative, str):
        raise ValueError("Invalid source path")
    path = Path(relative)
    if path.is_absolute() or not path.parts or any(p in (".", "..") for p in path.parts):
        raise ValueError("Invalid source path")
    target = root
    for part in path.parts:
        target = target / part
        if target.is_symlink():
            raise ValueError("Source symlinks are not retained")
    if not target.resolve().is_relative_to(root):
        raise ValueError("Source path escaped storage")
    return target


def _relative(root, path):
    absolute = Path(path).expanduser().absolute()
    current = absolute
    while current.resolve() != root:
        if current.is_symlink() or current.parent == current:
            raise ValueError("Source path escaped storage or used a symlink")
        current = current.parent
    return absolute.resolve().relative_to(root).as_posix()


def tem_delivery_copies(job):
    """Select this generation and immutable inputs of its known parent chain."""
    from . import ahn_web, tem_history
    if not job.settings or not tem_history.managed(job):
        return [], None
    root = _root(job.settings)
    workspace = _relative(root, job.work_dir)
    output = _safe(root, workspace + "/output")
    if job.output_dir.resolve() != output.resolve():
        raise ValueError("TEM output ownership mismatch")
    files, ancestors = [], []
    roots = [output, _safe(root, workspace + "/input")]
    parent_id = getattr(job, "parent_job_id", None)
    visited = {job.job_id}
    with ahn_web._ahn_report_jobs_lock:
        while parent_id:
            if parent_id in visited or len(visited) >= 32:
                raise ValueError("Invalid TEM revision chain")
            visited.add(parent_id)
            parent = ahn_web._ahn_report_jobs.get(parent_id)
            if parent is None or not tem_history.managed(parent):
                break  # Never infer a missing workspace from an arbitrary ID.
            relative = _relative(root, parent.work_dir)
            ancestors.append({"workspace": relative, "jobId": parent.job_id})
            roots.append(_safe(root, relative + "/input"))
            parent_id = getattr(parent, "parent_job_id", None)
    for directory in roots:
        for path in sorted(directory.rglob("*")):
            safe = _safe(root, _relative(root, path))
            if safe.is_file():
                files.append(safe)
    return files, {"version": 1, "kind": "TEM_GENERATED", "workspace": workspace,
                   "jobId": job.job_id, "ancestors": ancestors}


def xrd_delivery_copies(state, directory, *, project=None, document=None):
    """Select this export and immutable source bytes for its exact project."""
    root = _root(state.settings)
    relative = _relative(root, directory)
    if not re.fullmatch(r"xrd-workbench-v1/users/[0-9a-f]{64}/jobs/[0-9a-f]{32}", relative):
        raise ValueError("XRD export ownership mismatch")
    paths = [_safe(root, relative + "/" + name) for name in ("report.zip", "document.json")]
    context = {"version": 1, "kind": "XRD_EXPORT", "workspace": relative}
    sid = (project or {}).get("storage_id")
    if sid and sid != "example":
        if not re.fullmatch(r"[0-9a-f]{32}", sid) or not isinstance(document, dict):
            raise ValueError("Invalid XRD source project")
        home = directory.parent.parent
        for record in project.get("sources", []):
            fid = record.get("id")
            if not isinstance(fid, str) or not re.fullmatch(r"[0-9a-f]{24,64}", fid):
                raise ValueError("Invalid XRD source ID")
            paths.append(_safe(root, _relative(root, home / "uploads" / sid / fid)))
        sessions = []
        for metadata in sorted((home / "sessions").glob("*/session.json")):
            if not re.fullmatch(r"[0-9a-f]{32}", metadata.parent.name):
                continue
            try:
                session = _read_owned_json(root, metadata)
            except (OSError, ValueError):
                continue
            jid = session.get("job_id")
            if not isinstance(jid, str) or not re.fullmatch(r"[0-9a-f]{32}", jid):
                continue
            try:
                imported = _read_owned_json(root, home / "jobs" / jid / "job.json")
            except (OSError, ValueError):
                continue
            if imported.get("kind") != "import" or imported.get("status") != "completed" or imported.get("storage_id") != sid:
                continue
            for entry in session.get("files", {}).values():
                if entry.get("completed") is not True:
                    raise ValueError("XRD source upload is incomplete")
                stored = Path(entry["stored_name"])
                if stored.is_absolute() or not stored.parts or ".." in stored.parts:
                    raise ValueError("Invalid XRD upload file path")
                paths.append(_safe(root, _relative(root, metadata.parent / "input" / stored)))
            sessions.append(metadata.parent.name)
        draft = home / "drafts" / (sid + ".json")
        context["project"] = {"storageId": sid, "projectId": project.get("id"),
                              "reportRevision": document.get("revision"),
                              "analysisRevision": project.get("analysis_revision", 0),
                              "draftSha256": _file_hash(draft) if draft.is_file() else None,
                              "sessionIds": sessions}
    return [p for p in paths if p.is_file()], context


def _read_owned_json(root, path):
    safe = _safe(root, _relative(root, path))
    if safe.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Source metadata too large")
    return json.loads(safe.read_text(encoding="utf-8"))


def _file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _context(row):
    raw = row.get("report_options_json")
    try:
        options = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {"invalid": True}
    context = options.get("_sourceRetention") if isinstance(options, dict) else None
    if isinstance(options, dict) and "_sourceRetention" in options and context is not None and not isinstance(context, dict):
        return {"invalid": True}
    return context if isinstance(context, dict) else None


def _blocked(message):
    raise HTTPException(409, message)


def _unchanged(root, row, prefix):
    for artifact in row.get("artifacts", []):
        relative = str(artifact.get("relative_path") or "")
        if not relative.startswith(prefix + "/") or artifact.get("deleted_at"):
            continue
        path = _safe(root, relative)
        if not path.exists():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if path.stat().st_size != int(artifact.get("size_bytes") or 0) or digest.hexdigest() != artifact.get("sha256"):
            _blocked("보고서 생성 이후 작업 파일이 변경되어 자동 정리를 보류합니다.")


@contextmanager
def source_cleanup_guard(settings, row):
    """Hold existing workspace locks while the caller moves registered copies.

    Unknown/legacy rows have no extra generation copies. Invalid contexts fail
    closed, rather than authorizing deletion outside a known workspace.
    """
    context = _context(row)
    if context is None:
        yield
        return
    if context.get("version") != 1:
        _blocked("원본 보관 정보의 버전을 확인해야 합니다.")
    root = _root(settings)
    relative = context.get("workspace")
    try:
        workspace = _safe(root, relative)
        if context.get("kind") == "TEM_GENERATED":
            if not re.fullmatch(r"tem-work-history/work-[0-9a-f]{32}", relative or ""):
                raise ValueError("Invalid TEM workspace")
            from . import ahn_web, tem_history
            with tem_history.runtime_owner(settings), ahn_web._ahn_report_jobs_lock:
                ancestors = context.get("ancestors", [])
                if not isinstance(ancestors, list) or len(ancestors) > 31:
                    raise ValueError("Invalid TEM parent chain")
                for entry in [context] + ancestors:
                    parent_relative = entry.get("workspace")
                    if not re.fullmatch(r"tem-work-history/work-[0-9a-f]{32}", parent_relative or ""):
                        raise ValueError("Invalid TEM parent workspace")
                    parent_workspace = _safe(root, parent_relative)
                    job = ahn_web._ahn_report_jobs.get(entry.get("jobId"))
                    if job is None:
                        metadata = parent_workspace / "job.json"
                        if metadata.is_symlink() or not metadata.is_file():
                            _blocked("TEM 작업 소유 정보를 확인하지 못해 정리를 보류합니다.")
                        saved = json.loads(metadata.read_text(encoding="utf-8"))
                        if saved.get("job_id") != entry.get("jobId") or saved.get("status") != "completed" or saved.get("history_pinned"):
                            _blocked("TEM 작업 상태 또는 보존 지정을 확인해야 합니다.")
                    else:
                        if not tem_history.managed(job) or job.work_dir.resolve() != parent_workspace.resolve():
                            _blocked("TEM 작업 소유 정보를 확인하지 못해 정리를 보류합니다.")
                        if tem_history.busy(job) or job.history_pinned or time.time() - job.history_accessed_at < 60:
                            _blocked("사용 중이거나 보존 지정한 TEM 작업의 파일은 정리하지 않습니다.")
                    _unchanged(root, row, parent_relative + "/input")
                _unchanged(root, row, relative + "/output")
                yield
                return
        if context.get("kind") == "XRD_EXPORT":
            if not re.fullmatch(r"xrd-workbench-v1/users/[0-9a-f]{64}/jobs/[0-9a-f]{32}", relative or ""):
                raise ValueError("Invalid XRD export workspace")
            from .xrd_workbench.locking import ProcessLock
            with ExitStack() as locks:
                locks.enter_context(ProcessLock(root / "xrd-workbench-v1" / ".render.lock"))
                metadata = workspace / "job.json"
                if metadata.is_symlink() or not metadata.is_file():
                    _blocked("XRD 내보내기 작업 기록을 확인하지 못해 정리를 보류합니다.")
                job = json.loads(metadata.read_text(encoding="utf-8"))
                if job.get("status") != "completed" or job.get("kind") != "zip":
                    _blocked("진행 중이거나 완료되지 않은 XRD 내보내기 파일은 정리하지 않습니다.")
                source = context.get("project")
                if source is not None:
                    home = workspace.parent.parent
                    locks.enter_context(ProcessLock(home / ".documents.lock"))
                    sid = source.get("storageId")
                    if not re.fullmatch(r"[0-9a-f]{32}", sid or ""):
                        raise ValueError("Invalid XRD project ID")
                    project = _read_owned_json(root, home / "uploads" / sid / "project.json")
                    report = _read_owned_json(root, home / "reports" / sid / "document.json")
                    review_path = home / "analysis" / sid / "review.json"
                    analysis = _read_owned_json(root, review_path) if review_path.is_file() else {"revision": 0}
                    draft = _safe(root, _relative(root, home / "drafts" / (sid + ".json")))
                    if (project.get("id") != source.get("projectId")
                            or report.get("revision") != source.get("reportRevision")
                            or analysis.get("revision", 0) != source.get("analysisRevision")
                            or (_file_hash(draft) if draft.is_file() else None) != source.get("draftSha256")):
                        _blocked("새로 편집한 XRD 프로젝트가 있어 원본 정리를 보류합니다.")
                    # Never remove source bytes while another export/import is queued.
                    for pending_path in (home / "jobs").glob("*/job.json"):
                        pending = _read_owned_json(root, pending_path)
                        if pending.get("status") in ("queued", "running"):
                            _blocked("진행 중인 XRD 작업이 있어 원본 정리를 보류합니다.")
                    prefix = _relative(root, home / "uploads" / sid)
                    _unchanged(root, row, prefix)
                    sessions = source.get("sessionIds", [])
                    if not isinstance(sessions, list) or len(sessions) > 10000:
                        raise ValueError("Invalid XRD session list")
                    for session_id in sessions:
                        if not re.fullmatch(r"[0-9a-f]{32}", session_id):
                            raise ValueError("Invalid XRD session ID")
                        session_root = _safe(root, _relative(root, home / "sessions" / session_id))
                        locks.enter_context(ProcessLock(session_root / ".lock"))
                        _unchanged(root, row, _relative(root, session_root / "input"))
                _unchanged(root, row, relative)
                yield
                return
        raise ValueError("Unknown source context")
    except ApiException as exc:
        _blocked(str(exc))
    except (OSError, TypeError, ValueError):
        _blocked("작업 파일 보관 정보를 확인하지 못해 정리를 보류합니다.")

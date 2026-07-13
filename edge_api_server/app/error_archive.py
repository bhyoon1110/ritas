from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import mimetypes
from pathlib import Path
import re
import shutil
from threading import Lock
import traceback
import unicodedata
from uuid import uuid4
import zipfile

from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from rist_common import get_logger

from .errors import ApiException, error_response


logger = get_logger(__name__)
router = APIRouter()
KST = timezone(timedelta(hours=9))
SAFE_FILE_RE = re.compile(r"[^\w.()\[\] \-\u3131-\u318e\uac00-\ud7a3]+")


@dataclass(frozen=True)
class ErrorArchiveSettings:
    root: Path
    retention_days: int = 30
    capture_files: bool = True
    max_file_bytes: int = 512 * 1024 * 1024
    max_total_bytes: int = 2 * 1024 * 1024 * 1024


class ErrorStatusUpdate(BaseModel):
    status: str


def _json_safe(value: object) -> object:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError):
        if isinstance(value, Mapping):
            return {str(key): _json_safe(item) for key, item in value.items()}
        if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
            return [_json_safe(item) for item in value]
        return str(value)


def _safe_part(value: str, fallback: str = "file") -> str:
    normalized = unicodedata.normalize("NFC", str(value or "")).strip()
    cleaned = SAFE_FILE_RE.sub("_", normalized).strip(" .")
    return (cleaned[:180] or fallback)


def _safe_relative(value: str, fallback: str = "file") -> Path:
    raw = str(value or "").replace("\\", "/")
    parts = [part for part in raw.split("/") if part not in {"", ".", ".."}]
    return Path(*(_safe_part(part, fallback) for part in parts)) if parts else Path(fallback)


def _project_from_path(path: str) -> str:
    lowered = path.lower()
    for marker, project in (
        ("/ftir", "FT-IR"),
        ("/raman", "RAMAN"),
        ("/xrd", "XRD"),
        ("/tem", "TEM"),
        ("/ahn", "TEM"),
    ):
        if marker in lowered:
            return project
    return "EDGE"


class ErrorArchive:
    def __init__(self, settings: ErrorArchiveSettings) -> None:
        self.settings = settings
        self.root = settings.root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def _event_dir(self, event_id: str) -> Path:
        if not re.fullmatch(r"[0-9A-Za-z_-]{8,80}", event_id):
            raise FileNotFoundError(event_id)
        path = (self.root / event_id).resolve()
        if self.root not in path.parents:
            raise FileNotFoundError(event_id)
        return path

    def cleanup(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=max(1, self.settings.retention_days)
        )
        for child in self.root.iterdir():
            if not child.is_dir():
                continue
            try:
                modified = datetime.fromtimestamp(child.stat().st_mtime, timezone.utc)
                if modified < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
            except OSError:
                logger.warning("오류 보관 폴더 정리 실패: %s", child, exc_info=True)

    def record(
        self,
        *,
        project: str,
        code: str,
        message: str,
        status_code: int = 500,
        retryable: bool = False,
        job_id: str | None = None,
        request_id: str | None = None,
        method: str | None = None,
        endpoint: str | None = None,
        client: str | None = None,
        user_agent: str | None = None,
        details: object | None = None,
        exception: BaseException | None = None,
        source_paths: Iterable[Path] = (),
        file_blobs: Iterable[tuple[str, bytes]] = (),
    ) -> dict[str, object]:
        now = datetime.now(KST).replace(microsecond=0)
        event_id = now.strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:10]
        event_dir = self.root / event_id
        files_dir = event_dir / "files"
        trace = ""
        if exception is not None:
            trace = "".join(
                traceback.format_exception(type(exception), exception, exception.__traceback__)
            )
        event: dict[str, object] = {
            "eventId": event_id,
            "timestamp": now.isoformat(),
            "project": str(project or "EDGE").upper(),
            "status": "open",
            "statusCode": int(status_code),
            "code": str(code or "UNKNOWN_ERROR"),
            "message": str(message or "오류가 발생했습니다."),
            "retryable": bool(retryable),
            "jobId": job_id,
            "requestId": request_id,
            "request": {
                "method": method,
                "endpoint": endpoint,
                "client": client,
                "userAgent": user_agent,
            },
            "details": _json_safe(details),
            "exceptionType": type(exception).__name__ if exception else None,
            "traceAvailable": bool(trace),
            "files": [],
            "filesTruncated": False,
            "capturedBytes": 0,
        }
        captured: list[dict[str, object]] = []
        total_bytes = 0
        with self._lock:
            self.cleanup()
            event_dir.mkdir(parents=True, exist_ok=False)
            if self.settings.capture_files:
                files_dir.mkdir(parents=True, exist_ok=True)
                for source in source_paths:
                    total_bytes = self._capture_path(
                        Path(source), files_dir, captured, total_bytes, event
                    )
                for name, data in file_blobs:
                    total_bytes = self._capture_blob(
                        name, data, files_dir, captured, total_bytes, event
                    )
            event["files"] = captured
            event["capturedBytes"] = total_bytes
            if trace:
                (event_dir / "traceback.txt").write_text(trace, encoding="utf-8")
            log_lines = [
                f"timestamp={event['timestamp']}",
                f"project={event['project']}",
                f"code={event['code']}",
                f"message={event['message']}",
                f"endpoint={endpoint or '-'}",
                f"job_id={job_id or '-'}",
                f"request_id={request_id or '-'}",
                "",
                trace,
            ]
            (event_dir / "error.log").write_text("\n".join(log_lines), encoding="utf-8")
            self._write_event(event_dir, event)
        logger.error(
            "오류 아카이브 저장 (event_id=%s, project=%s, code=%s, files=%s)",
            event_id,
            event["project"],
            event["code"],
            len(captured),
        )
        return event

    def _capture_path(
        self,
        source: Path,
        files_dir: Path,
        captured: list[dict[str, object]],
        total_bytes: int,
        event: dict[str, object],
    ) -> int:
        if not source.exists():
            return total_bytes
        if source.is_file():
            items = [(source, Path(_safe_part(source.name)))]
        else:
            base = Path(_safe_part(source.name or "input"))
            items = [
                (path, base / _safe_relative(path.relative_to(source).as_posix()))
                for path in source.rglob("*")
                if path.is_file() and not path.is_symlink()
            ]
        for path, relative in items:
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > self.settings.max_file_bytes or total_bytes + size > self.settings.max_total_bytes:
                event["filesTruncated"] = True
                continue
            target = self._unique_target(files_dir / relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            total_bytes += size
            captured.append(
                {
                    "path": target.relative_to(files_dir).as_posix(),
                    "sizeBytes": size,
                    "sourceName": path.name,
                }
            )
        return total_bytes

    def _capture_blob(
        self,
        name: str,
        data: bytes,
        files_dir: Path,
        captured: list[dict[str, object]],
        total_bytes: int,
        event: dict[str, object],
    ) -> int:
        size = len(data)
        if size > self.settings.max_file_bytes or total_bytes + size > self.settings.max_total_bytes:
            event["filesTruncated"] = True
            return total_bytes
        target = self._unique_target(files_dir / _safe_relative(name))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        captured.append(
            {"path": target.relative_to(files_dir).as_posix(), "sizeBytes": size, "sourceName": Path(name).name}
        )
        return total_bytes + size

    @staticmethod
    def _unique_target(path: Path) -> Path:
        if not path.exists():
            return path
        for index in range(2, 10_000):
            candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
            if not candidate.exists():
                return candidate
        raise OSError(f"파일 이름 충돌을 해결할 수 없습니다: {path.name}")

    @staticmethod
    def _write_event(event_dir: Path, event: dict[str, object]) -> None:
        target = event_dir / "event.json"
        temporary = event_dir / ".event.json.tmp"
        temporary.write_text(json.dumps(event, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)

    def get(self, event_id: str) -> dict[str, object]:
        event_dir = self._event_dir(event_id)
        path = event_dir / "event.json"
        if not path.is_file():
            raise FileNotFoundError(event_id)
        event = json.loads(path.read_text(encoding="utf-8"))
        trace_path = event_dir / "traceback.txt"
        if trace_path.is_file():
            event["traceback"] = trace_path.read_text(encoding="utf-8")
        return event

    def list(
        self,
        *,
        project: str = "",
        status: str = "",
        query: str = "",
        limit: int = 200,
    ) -> list[dict[str, object]]:
        self.cleanup()
        items: list[dict[str, object]] = []
        needle = query.casefold().strip()
        for child in sorted(self.root.iterdir(), reverse=True):
            event_path = child / "event.json"
            if not event_path.is_file():
                continue
            try:
                item = json.loads(event_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if project and str(item.get("project", "")).casefold() != project.casefold():
                continue
            if status and str(item.get("status", "")).casefold() != status.casefold():
                continue
            if needle:
                haystack = " ".join(
                    str(item.get(key, "")) for key in ("eventId", "project", "code", "message", "jobId", "requestId")
                ).casefold()
                if needle not in haystack:
                    continue
            items.append(item)
            if len(items) >= max(1, min(1000, limit)):
                break
        return items

    def update_status(self, event_id: str, status: str) -> dict[str, object]:
        normalized = status.strip().lower()
        if normalized not in {"open", "resolved"}:
            raise ValueError(status)
        with self._lock:
            event_dir = self._event_dir(event_id)
            event = self.get(event_id)
            event.pop("traceback", None)
            event["status"] = normalized
            event["resolvedAt"] = (
                datetime.now(KST).replace(microsecond=0).isoformat()
                if normalized == "resolved"
                else None
            )
            self._write_event(event_dir, event)
        return event

    def delete(self, event_id: str) -> None:
        with self._lock:
            event_dir = self._event_dir(event_id)
            if not event_dir.is_dir():
                raise FileNotFoundError(event_id)
            shutil.rmtree(event_dir)

    def file_path(self, event_id: str, relative: str) -> Path:
        files_root = (self._event_dir(event_id) / "files").resolve()
        target = (files_root / _safe_relative(relative)).resolve()
        if target != files_root and files_root not in target.parents:
            raise FileNotFoundError(relative)
        if not target.is_file():
            raise FileNotFoundError(relative)
        return target

    def build_zip(self, event_id: str) -> Path:
        event_dir = self._event_dir(event_id)
        if not event_dir.is_dir():
            raise FileNotFoundError(event_id)
        target = self.root / f".{event_id}-{uuid4().hex[:6]}.zip"
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for path in event_dir.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(event_dir).as_posix())
        return target


def error_archive(app: FastAPI) -> ErrorArchive | None:
    value = getattr(app.state, "error_archive", None)
    return value if isinstance(value, ErrorArchive) else None


def record_background_error(
    archive: ErrorArchive | None,
    *,
    project: str,
    code: str,
    message: str,
    exception: BaseException | None = None,
    job_id: str | None = None,
    details: object | None = None,
    source_paths: Iterable[Path] = (),
    file_blobs: Iterable[tuple[str, bytes]] = (),
) -> str | None:
    if archive is None:
        return None
    try:
        event = archive.record(
            project=project,
            code=code,
            message=message,
            status_code=500,
            job_id=job_id,
            details=details,
            exception=exception,
            source_paths=source_paths,
            file_blobs=file_blobs,
        )
        return str(event["eventId"])
    except Exception:
        logger.exception("백그라운드 오류 아카이브 저장 실패")
        return None


def _record_request(request: Request, exc: ApiException, original: BaseException | None = None) -> None:
    archive = error_archive(request.app)
    if archive is None:
        return
    source_paths = getattr(request.state, "error_source_paths", ())
    file_blobs = getattr(request.state, "error_file_blobs", ())
    try:
        event = archive.record(
            project=getattr(request.state, "error_project", None) or _project_from_path(request.url.path),
            code=exc.code,
            message=exc.message,
            status_code=exc.status_code,
            retryable=exc.retryable,
            job_id=exc.job_id or request.path_params.get("job_id"),
            request_id=request.headers.get("X-Request-Id"),
            method=request.method,
            endpoint=request.url.path,
            client=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            details=exc.details,
            exception=original,
            source_paths=source_paths,
            file_blobs=file_blobs,
        )
        request.state.error_event_id = event["eventId"]
        for cleanup_path in getattr(request.state, "error_cleanup_paths", ()):
            shutil.rmtree(Path(cleanup_path), ignore_errors=True)
    except Exception:
        logger.exception("HTTP 오류 아카이브 저장 실패 (%s %s)", request.method, request.url.path)


async def archived_api_exception_handler(request: Request, exc: ApiException):
    _record_request(request, exc, exc.__cause__)
    response = error_response(request, exc)
    event_id = getattr(request.state, "error_event_id", None)
    if event_id:
        response.headers["X-Error-Event-Id"] = str(event_id)
    return response


async def archived_validation_exception_handler(request: Request, exc: Exception):
    api_exc = ApiException(
        400,
        "REQUEST_VALIDATION_FAILED",
        "요청 형식이 올바르지 않습니다.",
        details=getattr(exc, "errors", lambda: [])(),
    )
    _record_request(request, api_exc, exc)
    response = error_response(request, api_exc)
    event_id = getattr(request.state, "error_event_id", None)
    if event_id:
        response.headers["X-Error-Event-Id"] = str(event_id)
    return response


async def archived_unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("처리되지 않은 서버 오류 (%s %s)", request.method, request.url.path)
    api_exc = ApiException(
        500,
        "INTERNAL_SERVER_ERROR",
        "서버 내부 오류가 발생했습니다.",
        retryable=True,
    )
    _record_request(request, api_exc, exc)
    response = error_response(request, api_exc)
    event_id = getattr(request.state, "error_event_id", None)
    if event_id:
        response.headers["X-Error-Event-Id"] = str(event_id)
    return response


def install_error_management(app: FastAPI, settings: object) -> ErrorArchive:
    configured_root = getattr(settings, "error_archive_root", None)
    root = Path(configured_root or (Path(getattr(settings, "storage_root")) / "errors"))
    archive = ErrorArchive(
        ErrorArchiveSettings(
            root=root,
            retention_days=int(getattr(settings, "error_retention_days", 30)),
            capture_files=bool(getattr(settings, "error_capture_files", True)),
            max_file_bytes=int(getattr(settings, "error_max_file_bytes", 512 * 1024 * 1024)),
            max_total_bytes=int(getattr(settings, "error_max_total_bytes", 2 * 1024 * 1024 * 1024)),
        )
    )
    app.state.error_archive = archive
    app.include_router(router)
    app.add_exception_handler(ApiException, archived_api_exception_handler)
    from fastapi.exceptions import RequestValidationError

    app.add_exception_handler(RequestValidationError, archived_validation_exception_handler)
    app.add_exception_handler(Exception, archived_unhandled_exception_handler)
    return archive


def _archive_or_404(request: Request) -> ErrorArchive:
    archive = error_archive(request.app)
    if archive is None:
        raise HTTPException(status_code=503, detail="오류 관리 저장소가 구성되지 않았습니다.")
    return archive


@router.get("/errors", response_class=HTMLResponse, include_in_schema=False)
def error_console() -> HTMLResponse:
    return HTMLResponse(_ERROR_CONSOLE_HTML)


@router.get("/api/v1/errors", tags=["errors"])
def list_errors(
    request: Request,
    project: str = Query(default=""),
    status: str = Query(default=""),
    q: str = Query(default=""),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, object]:
    items = _archive_or_404(request).list(project=project, status=status, query=q, limit=limit)
    return {"items": items, "count": len(items)}


@router.get("/api/v1/errors/{event_id}", tags=["errors"])
def get_error(request: Request, event_id: str) -> dict[str, object]:
    try:
        return _archive_or_404(request).get(event_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="오류 기록을 찾을 수 없습니다.") from exc


@router.patch("/api/v1/errors/{event_id}", tags=["errors"])
def update_error(request: Request, event_id: str, payload: ErrorStatusUpdate) -> dict[str, object]:
    try:
        return _archive_or_404(request).update_status(event_id, payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="status는 open 또는 resolved여야 합니다.") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="오류 기록을 찾을 수 없습니다.") from exc


@router.delete(
    "/api/v1/errors/{event_id}",
    status_code=204,
    response_class=Response,
    tags=["errors"],
)
def delete_error(request: Request, event_id: str) -> Response:
    try:
        _archive_or_404(request).delete(event_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="오류 기록을 찾을 수 없습니다.") from exc
    return Response(status_code=204)


@router.get("/api/v1/errors/{event_id}/files/{relative_path:path}", tags=["errors"])
def download_error_file(request: Request, event_id: str, relative_path: str) -> FileResponse:
    try:
        path = _archive_or_404(request).file_path(event_id, relative_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="보관 파일을 찾을 수 없습니다.") from exc
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.get("/api/v1/errors/{event_id}/archive", tags=["errors"])
def download_error_archive(
    request: Request,
    event_id: str,
    background_tasks: BackgroundTasks,
) -> FileResponse:
    try:
        path = _archive_or_404(request).build_zip(event_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="오류 기록을 찾을 수 없습니다.") from exc
    background_tasks.add_task(path.unlink, missing_ok=True)
    return FileResponse(path, media_type="application/zip", filename=f"rist-error-{event_id}.zip")


_ERROR_CONSOLE_HTML = r'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RIST 오류 관리</title><style>
:root{font-family:Arial,"Noto Sans KR",sans-serif;color:#172033;background:#f4f6f8}*{box-sizing:border-box}body{margin:0}.top{height:64px;background:#fff;border-bottom:1px solid #d8dee8;display:flex;align-items:center;justify-content:space-between;padding:0 24px}.top h1{font-size:21px;margin:0}.top a{color:#42526b;text-decoration:none}.wrap{padding:20px 24px 40px}.filters{display:grid;grid-template-columns:160px 160px minmax(220px,1fr) auto;gap:8px;margin-bottom:14px}.filters select,.filters input,.filters button{height:40px;border:1px solid #bcc7d6;border-radius:6px;background:#fff;padding:0 12px;font:inherit}.filters button{background:#183153;color:#fff;border-color:#183153;cursor:pointer}.summary{font-size:13px;color:#5f6b7a;margin:8px 0}.panel{background:#fff;border:1px solid #d8dee8;border-radius:7px;overflow:hidden}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%;min-width:960px}th,td{border-bottom:1px solid #e4e8ef;padding:11px 12px;text-align:left;vertical-align:top;font-size:13px}th{background:#eef2f6;color:#405069;position:sticky;top:0}tr{cursor:pointer}tr:hover{background:#f7f9fc}.badge{display:inline-block;border-radius:12px;padding:3px 8px;font-size:12px;background:#e8edf4}.badge.open{background:#fee2e2;color:#9b1c1c}.badge.resolved{background:#dcfce7;color:#166534}.code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}.empty{padding:54px;text-align:center;color:#738096}.detail{display:none;margin-top:16px;padding:18px}.detail.open{display:block}.detail-head{display:flex;align-items:flex-start;justify-content:space-between;gap:20px}.detail h2{font-size:18px;margin:0 0 5px}.actions{display:flex;gap:7px;flex-wrap:wrap}.actions button,.actions a{height:36px;border:1px solid #aeb9c8;border-radius:5px;background:#fff;color:#24364d;padding:0 11px;display:inline-flex;align-items:center;text-decoration:none;cursor:pointer;font:inherit}.actions .danger{color:#a11b1b}.meta{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:16px 0}.meta div{background:#f5f7fa;border-radius:5px;padding:10px}.meta b{display:block;font-size:11px;color:#687587;margin-bottom:5px}.message{border-left:4px solid #d64545;background:#fff4f4;padding:12px;margin:12px 0;white-space:pre-wrap}.files{display:grid;gap:6px}.file{display:flex;justify-content:space-between;align-items:center;gap:12px;border:1px solid #dce2ea;border-radius:5px;padding:8px 10px}.file a{word-break:break-all}.trace{white-space:pre-wrap;overflow:auto;max-height:320px;background:#111827;color:#dbe5f3;padding:12px;border-radius:5px;font:12px ui-monospace,SFMono-Regular,Menlo,monospace}.muted{color:#6b7788}@media(max-width:760px){.top{padding:0 14px}.wrap{padding:14px}.filters{grid-template-columns:1fr 1fr}.filters input{grid-column:1/-1}.meta{grid-template-columns:1fr 1fr}.detail-head{display:block}.actions{margin-top:12px}}
</style></head><body><header class="top"><h1>RIST 오류 관리</h1><a href="/">작업 화면</a></header><main class="wrap">
<section class="filters"><select id="project"><option value="">전체 프로젝트</option><option>FT-IR</option><option>RAMAN</option><option>XRD</option><option>TEM</option><option>EDGE</option></select><select id="status"><option value="">전체 상태</option><option value="open">미해결</option><option value="resolved">해결</option></select><input id="query" placeholder="코드, 메시지, 작업 ID 검색"><button id="refresh">조회</button></section><div class="summary" id="summary"></div>
<section class="panel table-wrap"><table><thead><tr><th>발생 시각</th><th>프로젝트</th><th>상태</th><th>오류 코드</th><th>메시지</th><th>파일</th></tr></thead><tbody id="rows"></tbody></table><div class="empty" id="empty" hidden>오류 기록이 없습니다.</div></section><section class="panel detail" id="detail"></section></main>
<script>
const $=id=>document.getElementById(id);const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));let items=[];
async function api(url,opt){const r=await fetch(url,opt);if(!r.ok){let m='요청 처리 실패';try{const p=await r.json();m=p.detail||p.message||m}catch(e){}throw new Error(m)}return r.status===204?null:r.json()}
function size(v){let n=Number(v||0),u=['B','KB','MB','GB'];let i=0;while(n>=1024&&i<3){n/=1024;i++}return (i?n.toFixed(1):n)+' '+u[i]}
async function load(){const p=new URLSearchParams({project:$('project').value,status:$('status').value,q:$('query').value,limit:'500'});const data=await api('/api/v1/errors?'+p);items=data.items||[];$('summary').textContent=items.length+'건';$('empty').hidden=!!items.length;$('rows').innerHTML=items.map(x=>`<tr data-id="${esc(x.eventId)}"><td>${esc(x.timestamp)}</td><td>${esc(x.project)}</td><td><span class="badge ${esc(x.status)}">${x.status==='resolved'?'해결':'미해결'}</span></td><td class="code">${esc(x.code)}</td><td>${esc(x.message)}</td><td>${(x.files||[]).length}개</td></tr>`).join('');document.querySelectorAll('tr[data-id]').forEach(r=>r.onclick=()=>show(r.dataset.id))}
async function show(id){const x=await api('/api/v1/errors/'+encodeURIComponent(id));const files=(x.files||[]).map(f=>`<div class="file"><a href="/api/v1/errors/${encodeURIComponent(id)}/files/${f.path.split('/').map(encodeURIComponent).join('/')}">${esc(f.path)}</a><span>${size(f.sizeBytes)}</span></div>`).join('')||'<div class="muted">보관된 실패 파일이 없습니다.</div>';const trace=x.traceback||'';$('detail').className='panel detail open';$('detail').innerHTML=`<div class="detail-head"><div><h2>${esc(x.project)} · <span class="code">${esc(x.code)}</span></h2><span class="muted">${esc(x.eventId)}</span></div><div class="actions"><a href="/api/v1/errors/${encodeURIComponent(id)}/archive">전체 ZIP</a><button id="toggle">${x.status==='resolved'?'미해결로 변경':'해결 처리'}</button><button class="danger" id="delete">삭제</button></div></div><div class="meta"><div><b>발생 시각</b>${esc(x.timestamp)}</div><div><b>작업 ID</b>${esc(x.jobId||'-')}</div><div><b>요청 ID</b>${esc(x.requestId||'-')}</div><div><b>경로</b>${esc((x.request||{}).endpoint||'-')}</div></div><div class="message">${esc(x.message)}</div><h3>실패 파일</h3><div class="files">${files}</div>${x.filesTruncated?'<p class="muted">보존 용량 제한으로 일부 파일은 제외되었습니다.</p>':''}${trace?'<h3>스택 트레이스</h3><pre class="trace">'+esc(trace)+'</pre>':''}`;$('toggle').onclick=async()=>{await api('/api/v1/errors/'+encodeURIComponent(id),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:x.status==='resolved'?'open':'resolved'})});await load();await show(id)};$('delete').onclick=async()=>{if(!confirm('이 오류 기록과 보관 파일을 삭제할까요?'))return;await api('/api/v1/errors/'+encodeURIComponent(id),{method:'DELETE'});$('detail').className='panel detail';await load()}}
$('refresh').onclick=load;$('query').onkeydown=e=>{if(e.key==='Enter')load()};load().catch(e=>$('summary').textContent=e.message);
</script></body></html>'''

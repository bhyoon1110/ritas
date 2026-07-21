from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
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
from pydantic import BaseModel, Field

from rist_common import get_logger

from .errors import ApiException, error_response
from .usage_archive import (
    UsageArchive,
    UsageArchiveSettings,
    usage_archive,
    usage_logging_middleware,
)


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


class ErrorCommentCreate(BaseModel):
    author: str = Field(default="고객", max_length=100)
    content: str = Field(min_length=1, max_length=4000)


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
        client_application: dict[str, object] | None = None,
        file_context: dict[str, object] | None = None,
        transfer_context: dict[str, object] | None = None,
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
            "clientApplication": _json_safe(client_application or {}),
            "file": _json_safe(file_context or {}),
            "transfer": _json_safe(transfer_context or {}),
            "details": _json_safe(details),
            "exceptionType": type(exception).__name__ if exception else None,
            "traceAvailable": bool(trace),
            "files": [],
            "filesTruncated": False,
            "capturedBytes": 0,
            "comments": [],
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
        event.setdefault("comments", [])
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
            item.setdefault("comments", [])
            if project and str(item.get("project", "")).casefold() != project.casefold():
                continue
            if status and str(item.get("status", "")).casefold() != status.casefold():
                continue
            if needle:
                client_application = item.get("clientApplication") or {}
                file_context = item.get("file") or {}
                haystack = " ".join(
                    str(item.get(key, "")) for key in ("eventId", "project", "code", "message", "jobId", "requestId")
                ) + " " + " ".join(
                    str(value)
                    for value in (
                        client_application.get("type", ""),
                        client_application.get("name", ""),
                        client_application.get("version", ""),
                        client_application.get("sourceHostName", ""),
                        file_context.get("relativePath", ""),
                        file_context.get("name", ""),
                        file_context.get("sha256", ""),
                    )
                )
                haystack = haystack.casefold()
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

    def add_comment(
        self,
        event_id: str,
        *,
        author: str,
        content: str,
        source: str = "customer",
    ) -> dict[str, object]:
        normalized_author = author.strip() or "고객"
        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("content")
        comment = {
            "commentId": uuid4().hex,
            "author": normalized_author[:100],
            "content": normalized_content[:4000],
            "source": source,
            "createdAt": datetime.now(KST).replace(microsecond=0).isoformat(),
        }
        with self._lock:
            event_dir = self._event_dir(event_id)
            event = self.get(event_id)
            event.pop("traceback", None)
            comments = event.setdefault("comments", [])
            if not isinstance(comments, list):
                comments = []
                event["comments"] = comments
            comments.append(comment)
            self._write_event(event_dir, event)
        return comment

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
    client_application = {
        "type": getattr(request.state, "usage_client_type", None)
        or request.headers.get("X-Client-Type"),
        "name": getattr(request.state, "usage_client_name", None)
        or request.headers.get("X-Client-Name"),
        "version": getattr(request.state, "usage_client_version", None)
        or request.headers.get("X-Client-Version"),
        "sourceHostName": getattr(request.state, "usage_source_host_name", None),
    }
    file_context = {
        "relativePath": getattr(request.state, "usage_file_relative_path", None),
        "name": getattr(request.state, "usage_file_name", None),
        "sizeBytes": getattr(request.state, "usage_file_size_bytes", None),
        "sha256": getattr(request.state, "usage_file_sha256", None),
    }
    transfer_context = {
        "fileCount": getattr(request.state, "usage_file_count", None),
        "totalSizeBytes": getattr(request.state, "usage_total_size_bytes", None),
    }
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
            client_application=client_application,
            file_context=file_context,
            transfer_context=transfer_context,
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
        response.headers["X-Error-Comment-Url"] = f"/error-feedback/{event_id}"
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
        response.headers["X-Error-Comment-Url"] = f"/error-feedback/{event_id}"
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
        response.headers["X-Error-Comment-Url"] = f"/error-feedback/{event_id}"
    return response


def install_error_management(app: FastAPI, settings: object) -> ErrorArchive:
    from .report_management import router as report_management_router

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
    configured_usage_root = getattr(settings, "usage_log_root", None)
    usage_root = Path(
        configured_usage_root
        or (Path(getattr(settings, "storage_root")) / "usage")
    )
    app.state.usage_archive = UsageArchive(
        UsageArchiveSettings(
            root=usage_root,
            retention_days=int(getattr(settings, "usage_log_retention_days", 90)),
        )
    )
    app.middleware("http")(usage_logging_middleware)
    app.include_router(router)
    app.include_router(report_management_router)
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
    return HTMLResponse(_operations_console_html("errors"))


@router.get("/operations", response_class=HTMLResponse, include_in_schema=False)
def operations_console() -> HTMLResponse:
    return HTMLResponse(_operations_console_html("usage"))


@router.get("/error-feedback/{event_id}", response_class=HTMLResponse, include_in_schema=False)
def error_feedback(request: Request, event_id: str) -> HTMLResponse:
    try:
        event = _archive_or_404(request).get(event_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="오류 기록을 찾을 수 없습니다.") from exc
    comments = event.get("comments") if isinstance(event.get("comments"), list) else []
    comment_items = "".join(
        "<article><div><b>"
        + escape(str(comment.get("author") or "고객"))
        + "</b><time>"
        + escape(str(comment.get("createdAt") or ""))
        + "</time></div><p>"
        + escape(str(comment.get("content") or ""))
        + "</p></article>"
        for comment in comments
        if isinstance(comment, dict)
    ) or '<p class="muted">등록된 코멘트가 없습니다.</p>'
    event_id_json = json.dumps(event_id, ensure_ascii=False)
    return HTMLResponse(
        f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>오류 코멘트</title><style>
        :root{{font-family:Arial,"Noto Sans KR",sans-serif;color:#172033;background:#f4f6f8}}*{{box-sizing:border-box}}body{{margin:0;padding:24px}}main{{max-width:760px;margin:auto;background:#fff;border:1px solid #d8dee8;border-radius:7px;padding:24px}}h1{{font-size:24px;margin:0 0 8px}}.meta{{color:#687587;margin-bottom:18px}}.message{{border-left:4px solid #d64545;background:#fff4f4;padding:12px;white-space:pre-wrap}}article{{border:1px solid #dce2ea;border-radius:6px;padding:12px;margin:8px 0}}article div{{display:flex;justify-content:space-between;gap:12px}}article time,.muted{{color:#6b7788;font-size:13px}}article p{{white-space:pre-wrap;margin:9px 0 0}}label{{display:block;font-weight:700;margin:14px 0 6px}}input,textarea{{width:100%;border:1px solid #aeb9c8;border-radius:5px;padding:10px;font:inherit}}textarea{{min-height:120px;resize:vertical}}button{{margin-top:12px;height:42px;border:0;border-radius:5px;background:#183153;color:#fff;padding:0 18px;font:inherit;font-weight:700}}#status{{margin-left:10px;color:#166534}}@media(max-width:640px){{body{{padding:12px}}main{{padding:18px}}article div{{display:block}}}}
        </style></head><body><main><h1>오류 코멘트</h1><div class="meta">{escape(str(event.get('project') or 'EDGE'))} · {escape(str(event.get('code') or 'UNKNOWN_ERROR'))}<br><code>{escape(event_id)}</code></div><div class="message">{escape(str(event.get('message') or '오류가 발생했습니다.'))}</div><h2>등록된 코멘트</h2><section id="comments">{comment_items}</section><label for="author">작성자</label><input id="author" maxlength="100" value="고객"><label for="content">코멘트</label><textarea id="content" maxlength="4000" placeholder="오류가 발생한 상황과 재현 방법을 적어주세요."></textarea><button id="submit" type="button">코멘트 등록</button><span id="status"></span></main><script>const eventId={event_id_json};document.getElementById('submit').onclick=async()=>{{const button=document.getElementById('submit'),status=document.getElementById('status'),content=document.getElementById('content').value.trim();if(!content){{status.textContent='코멘트를 입력하세요.';return}}button.disabled=true;status.textContent='등록 중...';try{{const response=await fetch('/api/v1/errors/'+encodeURIComponent(eventId)+'/comments',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{author:document.getElementById('author').value,content}})}});if(!response.ok)throw new Error('코멘트 등록에 실패했습니다.');location.reload()}}catch(error){{status.textContent=error.message;button.disabled=false}}}};</script></body></html>'''
    )


@router.get("/api/v1/usage-events", tags=["operations"])
def list_usage_events(
    request: Request,
    project: str = Query(default=""),
    result: str = Query(default=""),
    activity_type: str = Query(default="", alias="activityType"),
    q: str = Query(default=""),
    date_from: str = Query(default="", alias="dateFrom"),
    date_to: str = Query(default="", alias="dateTo"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, object]:
    archive = usage_archive(request.app)
    if archive is None:
        raise HTTPException(status_code=503, detail="사용 기록 저장소가 구성되지 않았습니다.")
    items = archive.list(
        project=project,
        result=result,
        activity_type=activity_type,
        query=q,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    return {"items": items, "count": len(items)}


@router.get("/api/v1/usage-events/{event_id}", tags=["operations"])
def get_usage_event(request: Request, event_id: str) -> dict[str, object]:
    archive = usage_archive(request.app)
    if archive is None:
        raise HTTPException(status_code=503, detail="사용 기록 저장소가 구성되지 않았습니다.")
    try:
        return archive.get(event_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="사용 기록을 찾을 수 없습니다.") from exc


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


@router.post("/api/v1/errors/{event_id}/comments", status_code=201, tags=["errors"])
def add_error_comment(
    request: Request,
    event_id: str,
    payload: ErrorCommentCreate,
) -> dict[str, object]:
    try:
        return _archive_or_404(request).add_comment(
            event_id,
            author=payload.author,
            content=payload.content,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="코멘트를 입력하세요.") from exc
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


def _operations_console_html(default_tab: str) -> str:
    normalized = "errors" if default_tab == "errors" else "usage"
    return _OPERATIONS_CONSOLE_HTML.replace("__DEFAULT_TAB__", normalized)


_OPERATIONS_CONSOLE_HTML = r'''<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>RIST 운영 관리</title>
  <style>
    :root{font-family:Arial,"Noto Sans KR",sans-serif;color:#172033;background:#f4f6f8}
    *{box-sizing:border-box}html,body{margin:0;max-width:100%;overflow-x:hidden}.top{min-height:64px;background:#fff;border-bottom:1px solid #d8dee8;display:flex;align-items:center;justify-content:space-between;padding:0 24px;gap:16px}.top h1{font-size:21px;margin:0}.top a{color:#42526b;text-decoration:none}.wrap{padding:20px 24px 40px;width:100%;max-width:100%;min-width:0;overflow:hidden}.tabs{display:flex;gap:4px;border-bottom:1px solid #ccd5e1;margin-bottom:16px;width:100%;max-width:100%}.tab{min-width:0;border:0;background:transparent;color:#657286;font:inherit;font-weight:700;padding:11px 18px;cursor:pointer;border-bottom:3px solid transparent}.tab.active{color:#183153;border-color:#2563eb}.filters{display:grid;grid-template-columns:150px 150px 170px 145px 145px minmax(220px,1fr) auto;gap:8px;width:100%;margin-bottom:12px;min-width:0}.filters select,.filters input,.filters button{height:40px;width:100%;max-width:100%;border:1px solid #bcc7d6;border-radius:6px;background:#fff;padding:0 11px;font:inherit;min-width:0}.filters button{background:#183153;color:#fff;border-color:#183153;cursor:pointer}.summary{font-size:13px;color:#5f6b7a;margin:8px 0}.panel{background:#fff;border:1px solid #d8dee8;border-radius:7px;overflow:hidden;max-width:100%;min-width:0}.table-wrap{width:100%;overflow-x:auto;overflow-y:hidden}table{border-collapse:collapse;width:100%;min-width:1180px}th,td{border-bottom:1px solid #e4e8ef;padding:11px 12px;text-align:left;vertical-align:top;font-size:13px}th{background:#eef2f6;color:#405069;position:sticky;top:0}tbody tr{cursor:pointer}tbody tr:hover{background:#f7f9fc}tbody tr.selected{background:#eaf2ff;box-shadow:inset 3px 0 #2563eb}.badge{display:inline-block;border-radius:12px;padding:3px 8px;font-size:12px;background:#e8edf4;white-space:nowrap}.badge.open,.badge.failure{background:#fee2e2;color:#9b1c1c}.badge.resolved,.badge.success{background:#dcfce7;color:#166534}.badge.report-complete{background:#dbeafe;color:#1e40af}.badge.screen-view{background:#f1f5f9;color:#475569}.code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}.empty{padding:54px;text-align:center;color:#738096}.detail-backdrop{display:none;position:fixed;inset:0;background:rgba(15,23,42,.38);z-index:1000}.detail-backdrop.open{display:block}.detail{display:none;position:fixed;z-index:1001;top:12px;right:12px;bottom:12px;width:min(820px,calc(100vw - 48px));padding:0;overflow-y:auto;box-shadow:0 18px 50px rgba(15,23,42,.24)}.detail.open{display:block}.detail-toolbar{position:sticky;top:0;z-index:2;min-height:52px;padding:0 16px;display:flex;align-items:center;justify-content:space-between;background:#fff;border-bottom:1px solid #d8dee8}.detail-toolbar b{font-size:15px}.detail-close{width:36px;height:36px;border:0;border-radius:5px;background:transparent;color:#42526b;font-size:26px;line-height:1;cursor:pointer}.detail-close:hover{background:#eef2f6}.detail-content{padding:18px}.detail-head{display:flex;align-items:flex-start;justify-content:space-between;gap:20px}.detail h2{font-size:18px;margin:0 0 5px}.actions{display:flex;gap:7px;flex-wrap:wrap}.actions button,.actions a{height:36px;border:1px solid #aeb9c8;border-radius:5px;background:#fff;color:#24364d;padding:0 11px;display:inline-flex;align-items:center;text-decoration:none;cursor:pointer;font:inherit}.actions .danger{color:#a11b1b}.meta{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:16px 0}.meta div{background:#f5f7fa;border-radius:5px;padding:10px;min-width:0;overflow-wrap:anywhere}.meta b{display:block;font-size:11px;color:#687587;margin-bottom:5px}.message{border-left:4px solid #d64545;background:#fff4f4;padding:12px;margin:12px 0;white-space:pre-wrap}.files{display:grid;gap:6px}.file{display:flex;justify-content:space-between;align-items:center;gap:12px;border:1px solid #dce2ea;border-radius:5px;padding:8px 10px}.file a{word-break:break-all}.comments{display:grid;gap:7px}.comment{border:1px solid #dce2ea;border-radius:5px;padding:10px}.comment-head{display:flex;justify-content:space-between;gap:12px}.comment p{margin:8px 0 0;white-space:pre-wrap}.comment-form{display:grid;grid-template-columns:minmax(120px,180px) minmax(240px,1fr) auto;gap:8px;margin-top:9px}.comment-form input,.comment-form textarea,.comment-form button{border:1px solid #aeb9c8;border-radius:5px;padding:9px;font:inherit}.comment-form textarea{min-height:42px;resize:vertical}.comment-form button{background:#183153;color:#fff;border-color:#183153;cursor:pointer}.trace{white-space:pre-wrap;overflow:auto;max-height:320px;background:#111827;color:#dbe5f3;padding:12px;border-radius:5px;font:12px ui-monospace,SFMono-Regular,Menlo,monospace}.muted{color:#6b7788}.usage-only.hidden{display:none}body.detail-open{overflow:hidden}
    @media(max-width:980px){.filters{grid-template-columns:1fr 1fr 1fr}.filters .query{grid-column:1/3}.meta{grid-template-columns:1fr 1fr}}
    @media(max-width:640px){.top{padding:12px 14px}.top h1{font-size:19px}.wrap{padding:14px}.tabs{margin-bottom:12px}.tab{flex:1;padding:10px 8px}.filters{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}.filters .query{grid-column:1/-1}.filters button{grid-column:1/-1}.detail{inset:0;width:100%;max-width:none;border:0;border-radius:0}.detail-content{padding:14px}.detail-head{display:block}.actions{margin-top:12px}.meta{grid-template-columns:1fr}.file{align-items:flex-start;flex-direction:column}.comment-form{grid-template-columns:1fr}.comment-head{display:block}}
  </style>
</head>
<body data-default-tab="__DEFAULT_TAB__">
  <header class="top"><h1>RIST 운영 관리</h1><a href="/">작업 화면</a></header>
  <main class="wrap">
    <nav class="tabs" aria-label="운영 관리 메뉴">
      <button type="button" class="tab" id="usage-tab" data-tab="usage">사용 기록</button>
      <button type="button" class="tab" id="errors-tab" data-tab="errors">오류 기록</button>
      <a class="tab" href="/report-management">보고서/파일 관리</a>
    </nav>
    <section class="filters">
      <select id="project" aria-label="프로젝트">
        <option value="">전체 프로젝트</option><option>FT-IR</option><option>RAMAN</option><option>XRD</option><option>TEM</option><option>EDGE</option>
      </select>
      <select id="state" aria-label="결과 또는 상태"></select>
      <select class="usage-only" id="activity-type" aria-label="사용 기록 유형">
        <option value="">전체 기록 유형</option>
        <option value="SCREEN_VIEW">화면 조회</option>
        <option value="LOOKUP">정보 조회</option>
        <option value="FILE_TRANSFER">파일 전송</option>
        <option value="REPORT_REQUEST">보고서 생성 요청</option>
        <option value="REPORT_COMPLETE">보고서 완료</option>
        <option value="REPORT_FAILED">보고서 실패</option>
        <option value="REPORT_DOWNLOAD">보고서 다운로드</option>
        <option value="REPORT_SEND">보고서 전송</option>
        <option value="ACTION">기타 업무 실행</option>
      </select>
      <input class="usage-only" id="date-from" type="date" aria-label="시작일">
      <input class="usage-only" id="date-to" type="date" aria-label="종료일">
      <input class="query" id="query" placeholder="의뢰번호, 작업 ID, 코드, 메시지 검색">
      <button type="button" id="refresh">조회</button>
    </section>
    <div class="summary" id="summary"></div>
    <section class="panel table-wrap">
      <table><thead><tr id="head-row"></tr></thead><tbody id="rows"></tbody></table>
      <div class="empty" id="empty" hidden>기록이 없습니다.</div>
    </section>
  </main>
  <div class="detail-backdrop" id="detail-backdrop" aria-hidden="true"></div>
  <section class="panel detail" id="detail" role="dialog" aria-modal="true" aria-label="기록 상세 정보"></section>
  <script>
  (function(){
    const $=id=>document.getElementById(id);
    const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
    let activeTab=document.body.dataset.defaultTab==='errors'?'errors':'usage';
    function localDate(value){if(!value)return '-';try{return new Date(value).toLocaleString('ko-KR')}catch(_error){return value}}
    function size(value){let n=Number(value||0),units=['B','KB','MB','GB'],index=0;while(n>=1024&&index<3){n/=1024;index+=1}return(index?n.toFixed(1):n)+' '+units[index]}
    function duration(value){const ms=Number(value||0);return ms>=1000?(ms/1000).toFixed(ms>=10000?1:2)+'초':ms+'ms'}
    function activityLabel(value){return({SCREEN_VIEW:'화면 조회',LOOKUP:'정보 조회',FILE_TRANSFER:'파일 전송',REPORT_REQUEST:'생성 요청',REPORT_COMPLETE:'보고서 완료',REPORT_FAILED:'보고서 실패',REPORT_DOWNLOAD:'다운로드',REPORT_SEND:'보고서 전송',ACTION:'업무 실행'})[value]||'업무 실행'}
    async function api(url,options){const response=await fetch(url,options);if(!response.ok){let message='요청 처리 실패';try{const payload=await response.json();message=payload.detail||payload.message||message}catch(_error){}throw new Error(message)}return response.status===204?null:response.json()}
    function closeDetail(){$('detail').className='panel detail';$('detail').innerHTML='';$('detail-backdrop').className='detail-backdrop';document.body.classList.remove('detail-open');document.querySelectorAll('tr[data-id]').forEach(row=>row.classList.remove('selected'))}
    function openDetail(content){$('detail').className='panel detail open';$('detail').innerHTML=`<div class="detail-toolbar"><b>상세 정보</b><button type="button" class="detail-close" id="detail-close" aria-label="상세 정보 닫기">&times;</button></div><div class="detail-content">${content}</div>`;$('detail-backdrop').className='detail-backdrop open';document.body.classList.add('detail-open');$('detail-close').onclick=closeDetail;$('detail-close').focus()}
    function setDefaultDates(){if($('date-from').value)return;const end=new Date(),start=new Date();start.setDate(end.getDate()-7);$('date-from').value=start.toISOString().slice(0,10);$('date-to').value=end.toISOString().slice(0,10)}
    function setTab(tab,loadNow){activeTab=tab==='errors'?'errors':'usage';document.querySelectorAll('.tab').forEach(button=>button.classList.toggle('active',button.dataset.tab===activeTab));document.querySelectorAll('.usage-only').forEach(element=>element.classList.toggle('hidden',activeTab!=='usage'));$('state').innerHTML=activeTab==='usage'?'<option value="">전체 결과</option><option value="success">성공</option><option value="failure">실패</option>':'<option value="">전체 상태</option><option value="open">미해결</option><option value="resolved">해결</option>';$('head-row').innerHTML=activeTab==='usage'?'<th>발생 시각</th><th>프로젝트</th><th>기록 유형</th><th>동작</th><th>결과</th><th>처리시간</th><th>의뢰번호</th><th>작업 ID</th><th>클라이언트 / 접속 위치</th>':'<th>발생 시각</th><th>프로젝트</th><th>상태</th><th>오류 코드</th><th>메시지</th><th>파일</th>';closeDetail();$('empty').textContent=activeTab==='usage'?'사용 기록이 없습니다.':'오류 기록이 없습니다.';history.replaceState(null,'',activeTab==='usage'?'/operations':'/errors');if(activeTab==='usage')setDefaultDates();if(loadNow)load()}
    async function load(){const params=new URLSearchParams({project:$('project').value,q:$('query').value,limit:'500'});let endpoint;if(activeTab==='usage'){params.set('result',$('state').value);params.set('activityType',$('activity-type').value);params.set('dateFrom',$('date-from').value);params.set('dateTo',$('date-to').value);endpoint='/api/v1/usage-events?'+params}else{params.set('status',$('state').value);endpoint='/api/v1/errors?'+params}const data=await api(endpoint);const items=data.items||[];$('summary').textContent=items.length+'건';$('empty').hidden=Boolean(items.length);$('rows').innerHTML=activeTab==='usage'?usageRows(items):errorRows(items);document.querySelectorAll('tr[data-id]').forEach(row=>row.onclick=()=>show(row.dataset.id))}
    function usageRows(items){return items.map(item=>{const app=item.clientApplication||{},request=item.request||{},client=[app.type,app.name].filter(Boolean).join(' · ')||'-',ip=request.clientIp||request.client||'',location=[app.sourceHostName?'PC '+app.sourceHostName:'',ip?'IP '+ip:''].filter(Boolean).join(' · ')||'-',type=item.activityType||'ACTION';return `<tr data-id="${esc(item.eventId)}"><td>${esc(localDate(item.timestamp))}</td><td>${esc(item.project)}</td><td><span class="badge ${type==='REPORT_COMPLETE'?'report-complete':type==='SCREEN_VIEW'?'screen-view':''}">${esc(activityLabel(type))}</span></td><td>${esc(item.action)}</td><td><span class="badge ${esc(item.result)}">${item.result==='failure'?'실패':'성공'}</span></td><td>${esc(duration(item.durationMs))}</td><td>${esc(item.requestNumber||'-')}</td><td class="code">${esc(item.jobId||'-')}</td><td><b>${esc(client)}</b><br><span class="muted">${esc(location)}</span></td></tr>`}).join('')}
    function errorRows(items){return items.map(item=>`<tr data-id="${esc(item.eventId)}"><td>${esc(localDate(item.timestamp))}</td><td>${esc(item.project)}</td><td><span class="badge ${esc(item.status)}">${item.status==='resolved'?'해결':'미해결'}</span></td><td class="code">${esc(item.code)}</td><td>${esc(item.message)}</td><td>${(item.files||[]).length}개</td></tr>`).join('')}
    async function show(id){document.querySelectorAll('tr[data-id]').forEach(row=>row.classList.toggle('selected',row.dataset.id===id));if(activeTab==='usage'){showUsage(await api('/api/v1/usage-events/'+encodeURIComponent(id)));return}showError(await api('/api/v1/errors/'+encodeURIComponent(id)))}
    function showUsage(item){const request=item.request||{},app=item.clientApplication||{},file=item.file||{},transfer=item.transfer||{};const fileLabel=file.relativePath||file.name||'-',peer=request.peerIp||request.client||'-';openDetail(`<div class="detail-head"><div><h2>${esc(item.project)} · ${esc(item.action)}</h2><span class="muted code">${esc(item.eventId)}</span></div><span class="badge ${esc(item.result)}">${item.result==='failure'?'실패':'성공'}</span></div><div class="meta"><div><b>기록 유형</b>${esc(activityLabel(item.activityType||'ACTION'))}</div><div><b>발생 시각</b>${esc(localDate(item.timestamp))}</div><div><b>처리시간</b>${esc(duration(item.durationMs))}</div><div><b>HTTP 상태</b>${esc(item.statusCode)}</div><div><b>요청 경로</b>${esc(request.method||'-')} ${esc(request.endpoint||'-')}</div><div><b>의뢰번호</b>${esc(item.requestNumber||'-')}</div><div><b>작업 ID</b>${esc(item.jobId||'-')}</div><div><b>실험코드 / 장비</b>${esc(item.experimentCode||'-')} / ${esc(item.equipmentCode||'-')}</div><div><b>실험자</b>${esc(item.operatorId||'-')}</div><div><b>클라이언트</b>${esc(app.type||'-')} · ${esc(app.name||'-')}</div><div><b>버전 / 실험 PC</b>${esc(app.version||'-')} / ${esc(app.sourceHostName||'-')}</div><div><b>접속 IP</b>${esc(request.clientIp||peer)}</div><div><b>서버 연결 IP</b>${esc(peer)}</div><div><b>프록시 전달 경로</b>${esc(request.forwardedFor||'-')}</div><div><b>파일</b>${esc(fileLabel)}</div><div><b>파일 크기</b>${file.sizeBytes==null?'-':esc(size(file.sizeBytes))}</div><div><b>SHA-256</b><span class="code">${esc(file.sha256||'-')}</span></div><div><b>전송 합계</b>${transfer.fileCount==null?'-':esc(transfer.fileCount+'개 / '+size(transfer.totalSizeBytes||0))}</div><div><b>요청 ID</b>${esc(item.requestId||'-')}</div><div><b>User-Agent</b>${esc(request.userAgent||'-')}</div></div>`)}
    function showError(item){const files=(item.files||[]).map(file=>`<div class="file"><a href="/api/v1/errors/${encodeURIComponent(item.eventId)}/files/${file.path.split('/').map(encodeURIComponent).join('/')}">${esc(file.path)}</a><span>${size(file.sizeBytes)}</span></div>`).join('')||'<div class="muted">보관된 실패 파일이 없습니다.</div>';const comments=(item.comments||[]).map(comment=>`<div class="comment"><div class="comment-head"><b>${esc(comment.author||'고객')}</b><span class="muted">${esc(localDate(comment.createdAt))}</span></div><p>${esc(comment.content)}</p></div>`).join('')||'<div class="muted">등록된 코멘트가 없습니다.</div>';const trace=item.traceback||'',app=item.clientApplication||{},file=item.file||{};openDetail(`<div class="detail-head"><div><h2>${esc(item.project)} · <span class="code">${esc(item.code)}</span></h2><span class="muted code">${esc(item.eventId)}</span></div><div class="actions"><a href="/error-feedback/${encodeURIComponent(item.eventId)}" target="_blank">고객 입력 화면</a><a href="/api/v1/errors/${encodeURIComponent(item.eventId)}/archive">전체 ZIP</a><button id="toggle">${item.status==='resolved'?'미해결로 변경':'해결 처리'}</button><button class="danger" id="delete">삭제</button></div></div><div class="meta"><div><b>발생 시각</b>${esc(localDate(item.timestamp))}</div><div><b>작업 ID</b>${esc(item.jobId||'-')}</div><div><b>요청 ID</b>${esc(item.requestId||'-')}</div><div><b>경로</b>${esc((item.request||{}).endpoint||'-')}</div><div><b>클라이언트</b>${esc(app.type||'-')} · ${esc(app.name||'-')}</div><div><b>버전 / 실험 PC</b>${esc(app.version||'-')} / ${esc(app.sourceHostName||'-')}</div><div><b>요청 파일</b>${esc(file.relativePath||file.name||'-')}</div><div><b>요청 파일 크기</b>${file.sizeBytes==null?'-':esc(size(Number(file.sizeBytes)))}</div></div><div class="message">${esc(item.message)}</div><h3>고객 코멘트</h3><div class="comments">${comments}</div><div class="comment-form"><input id="comment-author" maxlength="100" value="고객" aria-label="작성자"><textarea id="comment-content" maxlength="4000" placeholder="오류가 발생한 상황과 재현 방법" aria-label="코멘트"></textarea><button id="add-comment" type="button">코멘트 등록</button></div><h3>실패 파일</h3><div class="files">${files}</div>${item.filesTruncated?'<p class="muted">보존 용량 제한으로 일부 파일은 제외되었습니다.</p>':''}${trace?'<h3>스택 트레이스</h3><pre class="trace">'+esc(trace)+'</pre>':''}`);$('add-comment').onclick=async()=>{const content=$('comment-content').value.trim();if(!content){alert('코멘트를 입력하세요.');return}await api('/api/v1/errors/'+encodeURIComponent(item.eventId)+'/comments',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({author:$('comment-author').value,content})});await show(item.eventId)};$('toggle').onclick=async()=>{await api('/api/v1/errors/'+encodeURIComponent(item.eventId),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:item.status==='resolved'?'open':'resolved'})});await load();await show(item.eventId)};$('delete').onclick=async()=>{if(!confirm('이 오류 기록과 보관 파일을 삭제할까요?'))return;await api('/api/v1/errors/'+encodeURIComponent(item.eventId),{method:'DELETE'});closeDetail();await load()}}
    document.querySelectorAll('.tab[data-tab]').forEach(button=>button.onclick=()=>setTab(button.dataset.tab,true));$('refresh').onclick=()=>load().catch(error=>$('summary').textContent=error.message);$('query').onkeydown=event=>{if(event.key==='Enter')$('refresh').click()};$('detail-backdrop').onclick=closeDetail;document.addEventListener('keydown',event=>{if(event.key==='Escape'&&$('detail').classList.contains('open'))closeDetail()});setTab(activeTab,false);load().catch(error=>$('summary').textContent=error.message);
  })();
  </script>
</body>
</html>'''

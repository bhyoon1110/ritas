from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
from threading import Lock
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request

from rist_common import get_logger


logger = get_logger(__name__)
KST = timezone(timedelta(hours=9))
EVENT_ID_RE = re.compile(r"[0-9A-Za-z_-]{8,80}")
REPORT_JOB_STATUS_RE = re.compile(
    r"^/api/v1/(?:ftir|raman|xrd|tem)/report/jobs/[^/]+$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class UsageArchiveSettings:
    root: Path
    retention_days: int = 90


def project_from_path(path: str) -> str:
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


def action_from_request(method: str, path: str) -> str:
    upper_method = method.upper()
    lowered = path.lower()
    if path in {"/", "/ftir", "/raman", "/xrd", "/tem"}:
        return "작업 화면 조회"
    if lowered == "/api/v1/requests" and upper_method == "GET":
        return "의뢰 조회"
    if "/assignment-libraries" in lowered:
        if upper_method == "GET":
            return "피크 라이브러리 조회"
        if upper_method == "DELETE":
            return "피크 라이브러리 삭제"
        return "피크 라이브러리 저장"
    if "/upload-sessions" in lowered:
        if lowered.endswith("/complete"):
            return "업로드 완료 및 보고서 생성"
        return "업로드 시작"
    if lowered.endswith("/example"):
        return "예제 보고서 생성"
    if lowered.endswith("/send"):
        return "보고서 전송"
    if lowered.endswith("/render-pdf"):
        return "PDF 보고서 생성"
    if "/download" in lowered or lowered.endswith("/html"):
        return "보고서 다운로드"
    if lowered.endswith("/report") or lowered.endswith("/report/jobs"):
        return "보고서 생성 요청"
    if lowered.endswith("/analyze"):
        return "보고서 생성 요청"
    if "/files" in lowered:
        return {
            "GET": "파일 목록 조회",
            "POST": "파일 업로드",
            "PUT": "파일 수정",
            "DELETE": "파일 삭제",
        }.get(upper_method, "파일 관리")
    if lowered == "/api/v1/jobs" and upper_method == "POST":
        return "작업 등록"
    if lowered.endswith("/uploads/complete"):
        return "파일 전송 완료"
    return f"{upper_method} 요청"


def client_type_from_user_agent(user_agent: str | None) -> str | None:
    lowered = str(user_agent or "").casefold()
    if any(marker in lowered for marker in (".net", "dotnet", "csharp", "restsharp")):
        return "C#/.NET"
    if any(marker in lowered for marker in ("mozilla/", "chrome/", "safari/", "firefox/")):
        return "브라우저"
    return None


def should_record_usage(method: str, path: str) -> bool:
    lowered = path.lower()
    if lowered in {"/health", "/health/llm", "/operations", "/errors"}:
        return False
    if lowered.startswith("/api/v1/errors") or lowered.startswith("/api/v1/usage-events"):
        return False
    if "/assets/" in lowered or lowered.endswith("favicon.ico"):
        return False
    if "/chunks" in lowered:
        return False
    if method.upper() == "GET" and REPORT_JOB_STATUS_RE.fullmatch(path):
        return False
    return path == "/" or path.startswith("/api/") or path in {
        "/ftir",
        "/raman",
        "/xrd",
        "/tem",
    }


class UsageArchive:
    def __init__(self, settings: UsageArchiveSettings) -> None:
        self.settings = settings
        self.root = settings.root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def cleanup(self) -> None:
        cutoff = date.today() - timedelta(days=max(1, self.settings.retention_days))
        for path in self.root.glob("*.jsonl"):
            try:
                file_date = date.fromisoformat(path.stem)
            except ValueError:
                continue
            if file_date < cutoff:
                path.unlink(missing_ok=True)

    def record(
        self,
        *,
        project: str,
        action: str,
        result: str,
        status_code: int,
        duration_ms: int,
        method: str,
        endpoint: str,
        route_path: str | None = None,
        job_id: str | None = None,
        request_id: str | None = None,
        request_number: str | None = None,
        experiment_code: str | None = None,
        equipment_code: str | None = None,
        operator_id: str | None = None,
        client: str | None = None,
        user_agent: str | None = None,
        client_type: str | None = None,
        client_name: str | None = None,
        client_version: str | None = None,
        source_host_name: str | None = None,
        file_relative_path: str | None = None,
        file_name: str | None = None,
        file_size_bytes: int | None = None,
        file_sha256: str | None = None,
        file_count: int | None = None,
        total_size_bytes: int | None = None,
    ) -> dict[str, object]:
        now = datetime.now(KST).replace(microsecond=0)
        event: dict[str, object] = {
            "eventId": "usage-" + now.strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:8],
            "timestamp": now.isoformat(),
            "project": str(project or "EDGE").upper(),
            "action": str(action or "요청"),
            "result": "failure" if str(result).lower() == "failure" else "success",
            "statusCode": int(status_code),
            "durationMs": max(0, int(duration_ms)),
            "jobId": job_id,
            "requestId": request_id,
            "requestNumber": request_number,
            "experimentCode": experiment_code,
            "equipmentCode": equipment_code,
            "operatorId": operator_id,
            "clientApplication": {
                "type": client_type,
                "name": client_name,
                "version": client_version,
                "sourceHostName": source_host_name,
            },
            "file": {
                "relativePath": file_relative_path,
                "name": file_name,
                "sizeBytes": file_size_bytes,
                "sha256": file_sha256,
            },
            "transfer": {
                "fileCount": file_count,
                "totalSizeBytes": total_size_bytes,
            },
            "request": {
                "method": method.upper(),
                "endpoint": endpoint,
                "routePath": route_path,
                "client": client,
                "userAgent": (user_agent or "")[:500] or None,
            },
        }
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        target = self.root / f"{now.date().isoformat()}.jsonl"
        with self._lock:
            self.cleanup()
            with target.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        return event

    def list(
        self,
        *,
        project: str = "",
        result: str = "",
        query: str = "",
        date_from: str = "",
        date_to: str = "",
        limit: int = 200,
    ) -> list[dict[str, object]]:
        self.cleanup()
        needle = query.casefold().strip()
        items: list[dict[str, object]] = []
        maximum = max(1, min(1000, limit))
        for path in sorted(self.root.glob("*.jsonl"), reverse=True):
            if date_from and path.stem < date_from:
                continue
            if date_to and path.stem > date_to:
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if project and str(item.get("project", "")).casefold() != project.casefold():
                    continue
                if result and str(item.get("result", "")).casefold() != result.casefold():
                    continue
                if needle:
                    client_application = item.get("clientApplication") or {}
                    file_context = item.get("file") or {}
                    request_context = item.get("request") or {}
                    haystack = " ".join(
                        str(item.get(key, ""))
                        for key in (
                            "eventId",
                            "project",
                            "action",
                            "jobId",
                            "requestId",
                            "requestNumber",
                            "experimentCode",
                            "equipmentCode",
                            "operatorId",
                        )
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
                            request_context.get("client", ""),
                        )
                    )
                    haystack = haystack.casefold()
                    if needle not in haystack:
                        continue
                items.append(item)
                if len(items) >= maximum:
                    return items
        return items

    def get(self, event_id: str) -> dict[str, object]:
        if not EVENT_ID_RE.fullmatch(event_id):
            raise FileNotFoundError(event_id)
        for path in sorted(self.root.glob("*.jsonl"), reverse=True):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("eventId") == event_id:
                    return item
        raise FileNotFoundError(event_id)


def usage_archive(app: FastAPI) -> UsageArchive | None:
    value = getattr(app.state, "usage_archive", None)
    return value if isinstance(value, UsageArchive) else None


def set_usage_context(
    request: Request,
    *,
    project: str | None = None,
    action: str | None = None,
    job_id: str | None = None,
    request_number: str | None = None,
    experiment_code: str | None = None,
    equipment_code: str | None = None,
    operator_id: str | None = None,
    client_type: str | None = None,
    client_name: str | None = None,
    client_version: str | None = None,
    source_host_name: str | None = None,
    file_relative_path: str | None = None,
    file_name: str | None = None,
    file_size_bytes: int | None = None,
    file_sha256: str | None = None,
    file_count: int | None = None,
    total_size_bytes: int | None = None,
) -> None:
    values = {
        "usage_project": project,
        "usage_action": action,
        "usage_job_id": job_id,
        "usage_request_number": request_number,
        "usage_experiment_code": experiment_code,
        "usage_equipment_code": equipment_code,
        "usage_operator_id": operator_id,
        "usage_client_type": client_type,
        "usage_client_name": client_name,
        "usage_client_version": client_version,
        "usage_source_host_name": source_host_name,
        "usage_file_relative_path": file_relative_path,
        "usage_file_name": file_name,
        "usage_file_size_bytes": file_size_bytes,
        "usage_file_sha256": file_sha256,
        "usage_file_count": file_count,
        "usage_total_size_bytes": total_size_bytes,
    }
    for name, value in values.items():
        if value is not None and str(value).strip():
            setattr(request.state, name, str(value).strip())

    if project is not None and str(project).strip():
        request.state.error_project = str(project).strip()


def _state_value(request: Request, name: str) -> str | None:
    value = getattr(request.state, name, None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _state_int(request: Request, name: str) -> int | None:
    value = getattr(request.state, name, None)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _path_value(request: Request, name: str) -> str | None:
    value = request.path_params.get(name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def usage_logging_middleware(request: Request, call_next):
    path = request.url.path
    if not should_record_usage(request.method, path):
        return await call_next(request)
    started = perf_counter()
    response = None
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        archive = usage_archive(request.app)
        if archive is not None:
            duration_ms = round((perf_counter() - started) * 1000)
            route = request.scope.get("route")
            route_path = getattr(route, "path", None)
            project = _state_value(request, "usage_project") or _state_value(
                request, "error_project"
            ) or project_from_path(path)
            job_id = _state_value(request, "usage_job_id") or _path_value(request, "job_id")
            try:
                archive.record(
                    project=project,
                    action=_state_value(request, "usage_action")
                    or action_from_request(request.method, path),
                    result="failure" if status_code >= 400 else "success",
                    status_code=status_code,
                    duration_ms=duration_ms,
                    method=request.method,
                    endpoint=path,
                    route_path=route_path,
                    job_id=job_id,
                    request_id=request.headers.get("X-Request-Id"),
                    request_number=_state_value(request, "usage_request_number"),
                    experiment_code=_state_value(request, "usage_experiment_code"),
                    equipment_code=_state_value(request, "usage_equipment_code"),
                    operator_id=_state_value(request, "usage_operator_id")
                    or request.headers.get("X-Operator-Id"),
                    client=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent"),
                    client_type=_state_value(request, "usage_client_type")
                    or request.headers.get("X-Client-Type")
                    or client_type_from_user_agent(request.headers.get("user-agent")),
                    client_name=_state_value(request, "usage_client_name")
                    or request.headers.get("X-Client-Name"),
                    client_version=_state_value(request, "usage_client_version")
                    or request.headers.get("X-Client-Version"),
                    source_host_name=_state_value(request, "usage_source_host_name"),
                    file_relative_path=_state_value(
                        request, "usage_file_relative_path"
                    ),
                    file_name=_state_value(request, "usage_file_name"),
                    file_size_bytes=_state_int(request, "usage_file_size_bytes"),
                    file_sha256=_state_value(request, "usage_file_sha256"),
                    file_count=_state_int(request, "usage_file_count"),
                    total_size_bytes=_state_int(
                        request, "usage_total_size_bytes"
                    ),
                )
            except Exception:
                logger.exception("사용 기록 저장 실패 (%s %s)", request.method, path)

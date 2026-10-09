"""Project-scoped feedback and the administrator/author resolution workflow."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .auth import AuthContext, require_context
from .database import Database
from .errors import ApiException

ProjectCode = Literal["FTIR", "RAMAN", "XRD", "TEM"]
VocStatus = Literal["OPEN", "IN_PROGRESS", "RESOLVED", "CONFIRMED"]
PROJECT_PAGES = {"FTIR": "/ftir", "RAMAN": "/raman", "XRD": "/xrd", "TEM": "/tem"}
PROJECT_LABELS = {"FTIR": "FT-IR", "RAMAN": "Raman", "XRD": "XRD", "TEM": "TEM/STEM"}
STATUS_LABELS = {"OPEN": "접수", "IN_PROGRESS": "조치 중", "RESOLVED": "조치 완료 · 확인 대기", "CONFIRMED": "작성자 확인 완료"}
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class VocCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    project: ProjectCode
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=10000)


class VocVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    expected_version: int = Field(alias="expectedVersion", ge=1)


class VocAction(VocVersion):
    status: Literal["OPEN", "IN_PROGRESS", "RESOLVED"]
    note: str = Field(default="", max_length=10000)

    @model_validator(mode="after")
    def validate_resolution(self) -> "VocAction":
        self.note = self.note.strip()
        if self.status == "RESOLVED" and not self.note:
            raise ValueError("조치 완료 시 조치 내용을 입력해 주세요.")
        return self


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _timestamp(value: datetime | None) -> str | None:
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def _item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "vocId": row["voc_id"], "project": row["project"],
        "projectLabel": PROJECT_LABELS[row["project"]], "pageUrl": PROJECT_PAGES[row["project"]],
        "authorUserId": row["author_user_id"], "authorLoginId": row["author_login_id"],
        "authorName": row["author_display_name"], "title": row["title"], "content": row["content"],
        "status": row["status"], "statusLabel": STATUS_LABELS[row["status"]],
        "resolutionNote": row["resolution_note"], "resolvedByName": row["resolved_by_name"],
        "resolvedAt": _timestamp(row["resolved_at"]), "confirmedAt": _timestamp(row["confirmed_at"]),
        "createdAt": _timestamp(row["created_at"]), "updatedAt": _timestamp(row["updated_at"]),
        "version": row["version"],
    }


class VocService:
    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _scope(context: AuthContext) -> tuple[str, list[str]]:
        if context.is_admin:
            return "1 = 1", []
        projects = [project for project in PROJECT_PAGES if project in context.projects]
        if not projects:
            return "1 = 0", []
        return "project IN (" + ", ".join("?" for _ in projects) + ")", projects

    @staticmethod
    def _row(connection, voc_id: str, context: AuthContext, *, lock: bool = False) -> dict:
        scope, projects = VocService._scope(context)
        sql = "SELECT * FROM voc_requests WHERE voc_id = ? AND " + scope
        params = [voc_id, *projects]
        row = connection.execute(sql + (" FOR UPDATE" if lock else ""), tuple(params)).fetchone()
        if not row:
            # Do not reveal feedback from an experiment the member cannot access.
            raise ApiException(404, "VOC_NOT_FOUND", "VOC를 찾을 수 없습니다.")
        return row

    @staticmethod
    def _event(connection, voc_id: str, actor: AuthContext, action: str, previous: str | None, status: str, note: str, now: datetime) -> None:
        connection.execute(
            """INSERT INTO voc_events
               (voc_id, actor_user_id, actor_login_id, actor_display_name, action,
                previous_status, status, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (voc_id, actor.user_id, actor.login_id, actor.display_name, action, previous, status, note, now),
        )

    def create(self, context: AuthContext, payload: VocCreate, request_key: str) -> dict:
        if not context.is_admin and payload.project not in context.projects:
            raise ApiException(403, "PROJECT_ACCESS_DENIED", "승인된 실험 페이지에만 VOC를 등록할 수 있습니다.")
        voc_id, now = str(uuid4()), _now()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO voc_requests
                   (voc_id, project, author_user_id, author_login_id, author_display_name,
                    request_key, title, content, resolution_note, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)
                   ON DUPLICATE KEY UPDATE voc_id = voc_id""",
                (voc_id, payload.project, context.user_id, context.login_id, context.display_name,
                 request_key, payload.title, payload.content, now, now),
            )
            row = connection.execute(
                "SELECT * FROM voc_requests WHERE author_user_id = ? AND request_key = ?",
                (context.user_id, request_key),
            ).fetchone()
            if any(row[name] != getattr(payload, name) for name in ("project", "title", "content")):
                raise ApiException(409, "VOC_REQUEST_KEY_CONFLICT", "등록 요청 키가 다른 내용에 사용되었습니다. 새로 등록해 주세요.")
            if row["voc_id"] == voc_id:
                self._event(connection, voc_id, context, "CREATED", None, "OPEN", "", now)
            return _item(row)

    def list(self, context: AuthContext, *, project: str | None, status: str | None, query: str, page: int, page_size: int) -> dict:
        scope, params = self._scope(context)
        where = [scope]
        if project:
            where.append("project = ?")
            params.append(project)
        if query.strip():
            where.append("(LOCATE(?, title) > 0 OR LOCATE(?, content) > 0 OR LOCATE(?, author_display_name) > 0 OR LOCATE(?, author_login_id) > 0)")
            params.extend([query.strip()] * 4)
        base = " WHERE " + " AND ".join(where) if where else ""
        with self.database.transaction() as connection:
            counts = {key: 0 for key in STATUS_LABELS}
            for row in connection.execute("SELECT status, COUNT(*) AS count FROM voc_requests" + base + " GROUP BY status", tuple(params)).fetchall():
                counts[row["status"]] = int(row["count"])
            if status:
                where.append("status = ?")
                params.append(status)
            filtered = " WHERE " + " AND ".join(where) if where else ""
            rows = connection.execute(
                "SELECT * FROM voc_requests" + filtered + " ORDER BY updated_at DESC, voc_id DESC LIMIT ? OFFSET ?",
                tuple(params + [page_size, (page - 1) * page_size]),
            ).fetchall()
        return {"items": [_item(row) for row in rows], "counts": counts,
                "total": counts[status] if status else sum(counts.values()), "page": page, "pageSize": page_size}

    def get(self, context: AuthContext, voc_id: str) -> dict:
        with self.database.transaction() as connection:
            item = _item(self._row(connection, voc_id, context))
            events = connection.execute("SELECT * FROM voc_events WHERE voc_id = ? ORDER BY event_id", (voc_id,)).fetchall()
        item["events"] = [{"eventId": str(row["event_id"]), "actorName": row["actor_display_name"],
                           "actorLoginId": row["actor_login_id"], "action": row["action"],
                           "status": row["status"], "statusLabel": STATUS_LABELS[row["status"]],
                           "note": row["note"], "createdAt": _timestamp(row["created_at"])} for row in events]
        return item

    def act(self, context: AuthContext, voc_id: str, payload: VocAction) -> dict:
        if not context.is_admin:
            raise ApiException(403, "ADMIN_REQUIRED", "관리자만 조치 상태를 변경할 수 있습니다.")
        now = _now()
        with self.database.transaction() as connection:
            row = self._row(connection, voc_id, context, lock=True)
            if row["status"] == "CONFIRMED":
                raise ApiException(409, "VOC_ALREADY_CONFIRMED", "작성자가 확인한 VOC는 변경할 수 없습니다.")
            self._check_version(row, payload.expected_version)
            if row["status"] == payload.status and row["resolution_note"] == payload.note:
                return _item(row)
            resolved = payload.status == "RESOLVED"
            connection.execute(
                """UPDATE voc_requests SET status = ?, resolution_note = ?, resolved_by = ?,
                   resolved_by_name = ?, resolved_at = ?, updated_at = ?, version = version + 1
                   WHERE voc_id = ?""",
                (payload.status, payload.note, context.user_id if resolved else None,
                 context.display_name if resolved else None, now if resolved else None, now, voc_id),
            )
            self._event(connection, voc_id, context, "ADMIN_ACTION", row["status"], payload.status, payload.note, now)
            return _item(self._row(connection, voc_id, context))

    @staticmethod
    def _check_version(row: dict, expected: int) -> None:
        if row["version"] != expected:
            raise ApiException(409, "VOC_VERSION_CONFLICT", "다른 변경사항이 있습니다. 최신 내용을 확인한 뒤 다시 시도하세요.")

    def confirm(self, context: AuthContext, voc_id: str, expected_version: int) -> dict:
        now = _now()
        with self.database.transaction() as connection:
            row = self._row(connection, voc_id, context, lock=True)
            if row["author_user_id"] != context.user_id:
                raise ApiException(403, "VOC_AUTHOR_REQUIRED", "작성자 본인만 조치 내용을 확인할 수 있습니다.")
            if row["status"] == "CONFIRMED":
                return _item(row)
            self._check_version(row, expected_version)
            if row["status"] != "RESOLVED":
                raise ApiException(409, "VOC_NOT_RESOLVED", "관리자가 조치 완료로 표시한 후 확인할 수 있습니다.")
            connection.execute(
                "UPDATE voc_requests SET status = 'CONFIRMED', confirmed_at = ?, updated_at = ?, version = version + 1 WHERE voc_id = ?",
                (now, now, voc_id),
            )
            self._event(connection, voc_id, context, "AUTHOR_CONFIRMED", "RESOLVED", "CONFIRMED", "조치 내용을 확인했습니다.", now)
            return _item(self._row(connection, voc_id, context))


router = APIRouter(tags=["voc"])


def _context(request: Request, *, write: bool = False) -> AuthContext:
    context = require_context(request)
    if context.status != "ACTIVE":
        raise ApiException(401, "AUTHENTICATION_REQUIRED", "사용 가능한 회원으로 로그인해 주세요.")
    if write and request.headers.get("X-Requested-With") != "RIST-VOC":
        raise ApiException(403, "CSRF_CHECK_FAILED", "VOC 요청을 확인할 수 없습니다.")
    return context


def _service(request: Request) -> VocService:
    return VocService(request.app.state.database)


@router.get("/voc", response_class=HTMLResponse, include_in_schema=False)
def voc_board(request: Request, project: ProjectCode | None = None) -> HTMLResponse:
    from .voc_web import build_voc_page
    context = _context(request)
    if project and not context.is_admin and project not in context.projects:
        raise ApiException(403, "PROJECT_ACCESS_DENIED", "관리자에게 실험 페이지 접근 권한을 요청해 주세요.")
    return HTMLResponse(build_voc_page(context, project), headers=NO_STORE)


@router.get("/api/v1/voc")
def list_voc(request: Request, response: Response, project: ProjectCode | None = None,
             status: VocStatus | None = None, q: str = Query(default="", max_length=200),
             page: int = Query(default=1, ge=1, le=100000), page_size: int = Query(default=20, alias="pageSize", ge=1, le=100)) -> dict:
    response.headers.update(NO_STORE)
    return _service(request).list(_context(request), project=project, status=status, query=q, page=page, page_size=page_size)


@router.post("/api/v1/voc", status_code=201)
def create_voc(payload: VocCreate, request: Request, response: Response,
               request_key: UUID = Header(alias="Idempotency-Key")) -> dict:
    response.headers.update(NO_STORE)
    return _service(request).create(_context(request, write=True), payload, str(request_key))


@router.get("/api/v1/voc/{voc_id}")
def get_voc(voc_id: UUID, request: Request, response: Response) -> dict:
    response.headers.update(NO_STORE)
    return _service(request).get(_context(request), str(voc_id))


@router.post("/api/v1/voc/{voc_id}/status")
def act_voc(voc_id: UUID, payload: VocAction, request: Request, response: Response) -> dict:
    response.headers.update(NO_STORE)
    return _service(request).act(_context(request, write=True), str(voc_id), payload)


@router.post("/api/v1/voc/{voc_id}/confirm")
def confirm_voc(voc_id: UUID, payload: VocVersion, request: Request, response: Response) -> dict:
    response.headers.update(NO_STORE)
    return _service(request).confirm(_context(request, write=True), str(voc_id), payload.expected_version)

"""Personal report history; all reads are scoped to the authenticated owner."""
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.encoders import jsonable_encoder

from .auth import PROJECT_CODES, require_context
from .browser_support import with_browser_support_notice
from .errors import ApiException
from .report_delivery import _database, _state
from .report_ownership import backfill_owned_reports

router = APIRouter()
PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "Pragma": "no-cache"}
STATUSES = {"READY_FOR_REVIEW", "NOT_QUEUED", "PENDING", "PROCESSING", "RETRY_WAIT", "FAILED", "CANCELLED", "COMPLETED"}


def _actor(request):
    actor = require_context(request)
    if actor.status != "ACTIVE":
        raise ApiException(403, "ACCOUNT_INACTIVE", "활성 회원만 보고서 이력을 조회할 수 있습니다.")
    return actor


@router.get("/my/reports", response_class=HTMLResponse, include_in_schema=False)
def personal_report_page(request: Request):
    _actor(request)
    html = Path(__file__).with_name("report_history.html").read_text(encoding="utf-8")
    return HTMLResponse(with_browser_support_notice(html), headers=PRIVATE_HEADERS)


@router.get("/assets/report_history.js", include_in_schema=False)
def personal_report_script():
    return FileResponse(Path(__file__).with_name("report_history.js"), media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})


@router.get("/api/v1/my/reports", tags=["reports"])
def personal_reports(request: Request, q: str = Query(default="", max_length=128),
                     dateFrom: str = "", dateTo: str = "", project: str = "", transferStatus: str = "",
                     page: int = Query(default=1, ge=1), pageSize: int = Query(default=20, ge=1, le=100)):
    actor = _actor(request)
    for value in (dateFrom, dateTo):
        try:
            if value and date.fromisoformat(value).isoformat() != value:
                raise ValueError
        except ValueError as exc:
            raise ApiException(422, "REPORT_DATE_INVALID", "조회 날짜는 YYYY-MM-DD 형식으로 입력하세요.") from exc
    if dateFrom and dateTo and dateFrom > dateTo:
        raise ApiException(422, "REPORT_DATE_INVALID", "조회 시작일은 종료일보다 늦을 수 없습니다.")
    project = project.strip().upper().replace("-", "")
    status = transferStatus.strip().upper()
    if project and project not in PROJECT_CODES:
        raise ApiException(422, "REPORT_PROJECT_INVALID", "실험 종류를 확인하세요.")
    if status and status not in STATUSES:
        raise ApiException(422, "REPORT_STATUS_INVALID", "전송 상태를 확인하세요.")
    allowed = set(PROJECT_CODES if actor.is_admin else actor.projects) & set(PROJECT_CODES)
    if project:
        allowed &= {project}
    database = _database(request)
    backfill_owned_reports(request, actor)
    rows, total = database.list_personal_reports(owner_user_id=actor.user_id, projects=sorted(allowed),
        query=q.strip(), date_from=dateFrom, date_to=dateTo, transfer_status=status,
        limit=pageSize, offset=(page - 1) * pageSize)
    # _state uses the existing bounded public transfer serializer: no raw email,
    # password, internal path, owner IDs, or transfer control envelope is exposed.
    items = [_state(request, row, row["owner_analysis_type"]) for row in rows]
    return JSONResponse(jsonable_encoder({"items": items, "totalCount": total, "page": page, "pageSize": pageSize,
                         "totalPages": max(1, (total + pageSize - 1) // pageSize)}), headers=PRIVATE_HEADERS)

"""Authenticated review and LIMS queueing for generated report packages."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse

from .auth import authenticated_transfer_payload, require_context
from .config import Settings
from .database import Database
from .errors import ApiException
from .experiment_routing import resolve_analysis_type
from .preview_report import PreviewReportSendRequest
from .report_queue import (
    ReportQueueError,
    enqueue_registered_report_package,
)
from .usage_archive import set_usage_context


router = APIRouter()


def _settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if not isinstance(settings, Settings):
        raise ApiException(
            503,
            "REPORT_SETTINGS_UNAVAILABLE",
            "보고서 설정을 찾을 수 없습니다.",
            retryable=True,
        )
    return settings


def _database(request: Request) -> Database:
    database = getattr(request.app.state, "database", None)
    if not isinstance(database, Database):
        raise ApiException(
            503,
            "REPORT_DATABASE_UNAVAILABLE",
            "보고서 데이터베이스에 연결할 수 없습니다.",
            retryable=True,
        )
    return database


def _report_for_request(request: Request, report_id: str) -> tuple[dict[str, Any], str]:
    settings = _settings(request)
    report = _database(request).fetch_report_run(report_id)
    if not report or report.get("deleted_at") is not None:
        raise ApiException(
            404,
            "REPORT_NOT_FOUND",
            "보고서 생성 기록을 찾을 수 없습니다.",
        )
    project = resolve_analysis_type(
        experiment_code=report.get("experiment_code"),
        equipment_code=report.get("equipment_code"),
        configured_map=settings.analysis_type_map,
    )
    if project not in {"FTIR", "RAMAN", "XRD", "TEM"}:
        raise ApiException(
            422,
            "REPORT_PROJECT_UNRESOLVED",
            "보고서의 프로젝트를 판별할 수 없습니다. RIST_ANALYSIS_TYPE_MAP 설정을 확인하세요.",
            details={
                "experimentCode": report.get("experiment_code"),
                "equipmentCode": report.get("equipment_code"),
            },
        )
    if settings.auth_enabled:
        context = require_context(request)
        if not context.is_admin and project not in context.projects:
            raise ApiException(
                403,
                "PROJECT_ACCESS_DENIED",
                "이 프로젝트의 보고서를 조회할 권한이 없습니다.",
            )
    return report, project


def _package_path(settings: Settings, report: dict[str, Any]) -> Path:
    if str(report.get("storage_key") or "") != settings.report_storage_key:
        raise ApiException(
            409,
            "REPORT_STORAGE_KEY_MISMATCH",
            "보고서 저장소 키가 현재 설정과 일치하지 않습니다.",
        )
    relative = Path(str(report.get("package_relative_path") or ""))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise ApiException(
            409,
            "REPORT_PACKAGE_PATH_INVALID",
            "보고서 저장 경로가 올바르지 않습니다.",
        )
    root = settings.storage_root.expanduser().resolve()
    package = (root / relative).resolve()
    if package != root and root not in package.parents:
        raise ApiException(
            409,
            "REPORT_PACKAGE_PATH_INVALID",
            "보고서 저장 경로가 공유 저장소를 벗어났습니다.",
        )
    if not package.is_file():
        raise ApiException(410, "REPORT_PACKAGE_NOT_FOUND", "보고서 ZIP을 찾을 수 없습니다.")
    return package


def _state(request: Request, report: dict[str, Any], project: str) -> dict[str, Any]:
    transfer = _database(request).fetch_report_transfer_for_report(str(report["report_id"]))
    transfer_status = str(transfer["status"]) if transfer else "READY_FOR_REVIEW"
    return {
        "reportId": report["report_id"],
        "sourceJobId": report.get("source_job_id"),
        "requestNumber": report.get("request_number"),
        "analysisType": project,
        "experimentCode": report.get("experiment_code"),
        "equipmentCode": report.get("equipment_code"),
        "operatorId": report.get("operator_id"),
        "generationStatus": report.get("generation_status"),
        "transferStatus": transfer_status,
        "canQueue": transfer is None,
        "generatedAt": report.get("generated_at"),
        "downloadUrl": f"/api/v1/reports/{quote(str(report['report_id']), safe='')}/package",
        "reviewUrl": f"/reports/{quote(str(report['report_id']), safe='')}",
    }


@router.get("/api/v1/reports/{report_id}", tags=["reports"])
def get_generated_report(request: Request, report_id: str) -> dict[str, Any]:
    report, project = _report_for_request(request, report_id)
    return _state(request, report, project)


@router.get("/api/v1/reports/{report_id}/package", tags=["reports"])
def download_generated_report(request: Request, report_id: str) -> FileResponse:
    report, _project = _report_for_request(request, report_id)
    package = _package_path(_settings(request), report)
    return FileResponse(
        package,
        media_type="application/zip",
        filename=str(report.get("package_file_name") or "report-package.zip"),
    )


@router.post("/api/v1/reports/{report_id}/send", tags=["reports"])
def send_generated_report(request: Request, report_id: str) -> dict[str, Any]:
    report, project = _report_for_request(request, report_id)
    review_url = f"/reports/{quote(report_id, safe='')}"
    payload = PreviewReportSendRequest(
        requestNumber=str(report.get("request_number") or "(미지정)"),
        experimentCode=str(report.get("experiment_code") or project),
        equipmentCode=str(report.get("equipment_code") or "(미지정)"),
        operatorId=str(report.get("operator_id") or "(미지정)"),
    )
    effective = authenticated_transfer_payload(
        request,
        payload,
        project,
        return_to=review_url,
    )
    set_usage_context(
        request,
        project=project,
        job_id=report.get("source_job_id"),
        request_number=effective.request_number,
        experiment_code=effective.experiment_code,
        equipment_code=effective.equipment_code,
        operator_id=effective.operator_id,
    )
    try:
        queued = enqueue_registered_report_package(
            settings=_settings(request),
            database=_database(request),
            report=report,
            operator_id=effective.operator_id,
        )
    except ReportQueueError as exc:
        raise ApiException(
            503 if exc.retryable else 409,
            exc.code,
            str(exc),
            retryable=exc.retryable,
        ) from exc
    return {**queued, "sent": False, "reviewUrl": review_url}


@router.get("/reports/{report_id}", response_class=HTMLResponse, include_in_schema=False)
def generated_report_page(request: Request, report_id: str) -> HTMLResponse:
    report, project = _report_for_request(request, report_id)
    state = _state(request, report, project)
    encoded_id = quote(report_id, safe="")
    review_url = f"/reports/{encoded_id}"
    sso_url = "/auth/sso/start?" + urlencode({"returnTo": review_url})
    can_queue = bool(state["canQueue"])
    button_label = (
        "SSO 인증 후 LIMS 전송"
        if can_queue
        else f"전송 상태: {state['transferStatus']}"
    )
    disabled = "" if can_queue else " disabled"
    html = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Cache-Control" content="no-store"><title>{escape(project)} 보고서 검토</title>
<style>*{{box-sizing:border-box}}body{{margin:0;background:#f4f6f8;color:#172b4d;font:15px/1.55 system-ui,-apple-system,sans-serif}}main{{max-width:880px;margin:0 auto;padding:32px 18px}}.panel{{background:#fff;border:1px solid #d8e0eb;border-radius:10px;padding:24px}}h1{{margin:0 0 6px;font-size:26px}}.muted{{color:#637083}}.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:22px 0}}.item{{background:#f7f9fc;border-radius:7px;padding:12px;overflow-wrap:anywhere}}.item b{{display:block;font-size:12px;color:#637083;margin-bottom:4px}}.actions{{display:flex;gap:9px;flex-wrap:wrap}}button,a.button{{min-height:42px;border:1px solid #1769aa;border-radius:7px;padding:9px 14px;background:#1769aa;color:#fff;text-decoration:none;font:inherit;font-weight:700;cursor:pointer}}a.secondary{{background:#fff;color:#1769aa}}button:disabled{{opacity:.5;cursor:not-allowed}}#message{{margin-top:15px;white-space:pre-wrap}}@media(max-width:620px){{main{{padding:18px 12px}}.panel{{padding:18px}}.grid{{grid-template-columns:1fr}}}}</style></head>
<body><main><section class="panel"><h1>{escape(project)} 보고서 검토</h1><div class="muted">보고서 파일을 확인한 뒤 사내 SSO 인증으로 LIMS 전송을 승인합니다.</div>
<div class="grid"><div class="item"><b>의뢰번호</b>{escape(str(state['requestNumber'] or '-'))}</div><div class="item"><b>분석 / 실험코드</b>{escape(project)} / {escape(str(state['experimentCode'] or '-'))}</div><div class="item"><b>장비코드</b>{escape(str(state['equipmentCode'] or '-'))}</div><div class="item"><b>전송 상태</b><span id="status">{escape(str(state['transferStatus']))}</span></div><div class="item"><b>생성 시각</b>{escape(str(state['generatedAt'] or '-'))}</div><div class="item"><b>보고서 ID</b>{escape(report_id)}</div></div>
<div class="actions"><a class="button secondary" href="/api/v1/reports/{encoded_id}/package">ZIP 다운로드</a><a class="button secondary" href="{escape(sso_url)}">SSO 재인증</a><button id="send" type="button"{disabled}>{escape(button_label)}</button></div><div id="message" class="muted"></div></section></main>
<script>const button=document.getElementById('send'),message=document.getElementById('message'),statusNode=document.getElementById('status');if(button)button.addEventListener('click',async()=>{{button.disabled=true;message.textContent='전송 큐에 등록하는 중입니다.';try{{const response=await fetch('/api/v1/reports/{encoded_id}/send',{{method:'POST',headers:{{'X-Request-Id':'report-send-'+Date.now()}}}});const payload=await response.json();if(!response.ok){{if(payload.details&&payload.details.reauthUrl)location.href=payload.details.reauthUrl;throw new Error(payload.message||payload.detail||'전송 요청에 실패했습니다.')}}statusNode.textContent=payload.status||'PENDING';message.textContent='LIMS 전송 큐에 등록했습니다. 실제 완료 상태는 운영 화면에서 확인할 수 있습니다.';button.textContent='전송 요청됨'}}catch(error){{message.textContent=error.message||String(error);button.disabled=false}}}});</script></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})

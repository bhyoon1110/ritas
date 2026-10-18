"""Author-owned TEM review, immutable PPT revisions and final-package delivery."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import Field, ValidationError

from .errors import ApiException
from .report_editor import ReportEdit, SectionEdit, editor_page
from .report import tem_editable
from .report.direct_edit import slide_image
from .report.editor_variables import resolve_changes
from .report.renderers import convert_pptx_to_pdf
from .report_queue import register_generated_report_package
from .storage import atomic_write_json
from rist_common import get_logger


def _ahn():
    from . import ahn_web
    return ahn_web


class TemReportEdit(ReportEdit):
    sections: list[SectionEdit] = Field(max_length=5000)


def _state_path(job):
    return job.work_dir / "review/editor-state.json"


def _state(job):
    return json.loads(_state_path(job).read_text(encoding="utf-8"))


def _write_metadata(job, model):
    model = deepcopy(model)
    actual, _, _ = tem_editable.inspect_pptx(job.pptx_path)
    by_id = {s["sectionId"]: s for s in actual}
    for section in model["sections"]:
        section["columns"] = by_id[section["sectionId"]]["columns"]
        section["source"] = "user"
    value = resolve_changes(model)
    atomic_write_json(_state_path(job), model)
    atomic_write_json(job.output_dir / "report-review.json", {
        "reportId": job.job_id, "revisionOf": job.parent_job_id, "title": value["title"],
        "completionDate": value["completionDate"], "transfer": value["transfer"],
        "email": value["auxiliary"], "sections": value["sections"],
        "note": "분석자가 검토한 보고서 표시값입니다. 원본 분석 데이터는 analysis-result.json에 보존됩니다.",
    })
    auxiliary = value["auxiliary"]
    (job.output_dir / "email_body.md").write_text(
        "# " + auxiliary.get("email_subject", "") + "\n\n" + auxiliary.get("email_body", "") + "\n", encoding="utf-8")
    job.transfer_metadata = dict(value["transfer"])


def prepare_review(job, manifest):
    model = tem_editable.initial_model(job.pptx_path, manifest.get("summary") or {})
    _write_metadata(job, model)
    try:
        convert_pptx_to_pdf(job.pptx_path, job.output_dir / "tem-report.pdf")
    except Exception:
        # The generated PPT and all uploaded data remain available even if the
        # deployment lacks LibreOffice. A revision retries the conversion.
        job.review_error = "TEM PPT 미리보기를 만들지 못했습니다. 서버의 LibreOffice·한글 글꼴 설치를 확인한 뒤 ‘지금 반영 / 재시도’를 누르세요."
        get_logger(__name__).exception("TEM preview conversion failed (job=%s)", job.job_id)


def authorized_job(request, job_id, *, ready=True):
    ahn = _ahn()
    ahn.tem_history.prepare(request)
    ahn._cleanup_old_jobs()
    with ahn._ahn_report_jobs_lock:
        job = ahn._ahn_report_jobs.get(job_id)
        if job is None or getattr(job, "history_deleted_at", None):
            raise ApiException(404, "TEM_REPORT_NOT_FOUND", "TEM 보고서 작업을 찾을 수 없습니다.")
        settings = getattr(request.app.state, "settings", None)
        if settings and getattr(settings, "auth_enabled", False):
            from .auth import require_context
            context = require_context(request)
            if not context.is_admin and ("TEM" not in context.projects or job.owner_user_id != context.user_id):
                raise ApiException(403, "REPORT_EDIT_FORBIDDEN", "보고서 작성자 또는 관리자만 이 보고서를 확인·수정·전송할 수 있습니다.")
        job.updated_at = ahn.time.time()
        job.history_accessed_at = ahn.time.time()
    if ready and job.status != "completed":
        raise ApiException(409, "TEM_REPORT_NOT_READY", "TEM 보고서가 아직 완성되지 않았습니다.")
    return job


def _revision(new_job, old_job, changes):
    ahn = _ahn()
    with ahn._ahn_report_jobs_lock:
        new_job.active_revisions += 1
    try:
        with ahn._ahn_processing_slots:
            ahn._set_job_state(new_job, status="running", progress_pct=25, message="TEM 수정본을 만드는 중입니다. 원본은 유지됩니다.")
            new_job.output_dir.mkdir(parents=True)
            # Copy generated output only, never multi-GB raw inputs or an old ZIP.
            for source in old_job.output_dir.rglob("*"):
                if not source.is_file() or source.name in {"tem-report.pptx", "tem-report.pdf", "tem-report-package.zip", "report-review.json", "email_body.md"}:
                    continue
                target = new_job.output_dir / source.relative_to(old_job.output_dir)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            tem_editable.revise_pptx(old_job.pptx_path, new_job.pptx_path, changes)
            _write_metadata(new_job, changes)
            manifest = deepcopy(old_job.manifest or {})
            manifest.update(pptx="tem-report.pptx", revisionOf=old_job.job_id)
            atomic_write_json(new_job.manifest_path, manifest)
            ahn._set_job_state(new_job, progress_pct=65, message="수정된 PPT 미리보기와 전송 ZIP을 만드는 중입니다.")
            convert_pptx_to_pdf(new_job.pptx_path, new_job.output_dir / "tem-report.pdf")
            ahn._build_package(new_job.output_dir, new_job.package_path)
            transfer = new_job.transfer_metadata
            published_package = register_generated_report_package(settings=new_job.settings, database=new_job.database,
                report_id=new_job.job_id, package_path=new_job.package_path, experiment_code="TEM",
                request_number=transfer["requestNumber"], equipment_code=transfer["equipmentCode"], operator_id=transfer["operatorId"],
                report_options={"revisionOf": old_job.job_id, "reviewed": True, "transfer": transfer})
            if not ahn.tem_history.managed(new_job):
                new_job.package_path = published_package
            ahn._set_job_state(new_job, status="completed", progress_pct=100, message="TEM 수정본이 저장되었습니다.", manifest=manifest)
    except Exception as exc:
        get_logger(__name__).exception("TEM report revision failed (job=%s)", new_job.job_id)
        ahn._set_job_state(new_job, status="failed", progress_pct=100,
            message="TEM 수정본 생성에 실패했습니다. 원본과 입력 내용은 유지됩니다. LibreOffice·저장 공간을 확인한 뒤 재시도하세요.",
            error={"code": "TEM_REVISION_FAILED", "message": "TEM 수정본 생성에 실패했습니다. 원본은 유지되었습니다."})
        ahn.record_background_error(new_job.error_archive, project="TEM", code="TEM_REVISION_FAILED",
            message=new_job.message, exception=exc, job_id=new_job.job_id)
    finally:
        try:
            ahn.tem_history.save(new_job)
        finally:
            with ahn._ahn_report_jobs_lock:
                new_job.active_revisions -= 1
                old_job.active_revisions -= 1
                old_job.updated_at = ahn.time.time()
            ahn._ahn_pending_slots.release()


def install_tem_editor(router):
    prefix = "/api/v1/tem/report/jobs"

    @router.get(prefix + "/{job_id}/edit", response_class=HTMLResponse, tags=["tem"])
    def page(request: Request, job_id: str):
        authorized_job(request, job_id)
        return HTMLResponse(editor_page(prefix, job_id, project="TEM"), headers={"Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "SAMEORIGIN",
            "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self'; connect-src 'self'; frame-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"})

    @router.get(prefix + "/{job_id}/editor-data", tags=["tem"])
    def data(request: Request, job_id: str):
        job = authorized_job(request, job_id)
        if not _state_path(job).is_file():
            raise ApiException(410, "REPORT_EDIT_EXPIRED", "이전 버전의 보고서입니다. TEM 화면에서 다시 생성하세요.")
        with _ahn().tem_history.lease(job):
            result = {"document": _state(job), "job": _ahn()._job_payload(job), "previewError": job.review_error}
        context = getattr(request.state, "auth_context", None)
        identity = getattr(context, "sso_identity", None) or {}
        result["authenticatedOperatorId"] = str(identity.get("employee_id") or identity.get("subject") or
            (getattr(context, "login_id", "") if identity else ""))[:100]
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.get(prefix + "/{job_id}/edit-layout", tags=["tem"])
    def layout(request: Request, job_id: str):
        job = authorized_job(request, job_id)
        if job.review_error:
            raise ApiException(503, "TEM_PREVIEW_UNAVAILABLE", job.review_error)
        with _ahn().tem_history.lease(job):
            _, value, _ = tem_editable.inspect_pptx(job.pptx_path)
        return JSONResponse(value, headers={"Cache-Control": "no-store"})

    @router.get(prefix + "/{job_id}/edit-slides/{page}", tags=["tem"])
    def slide(request: Request, job_id: str, page: int):
        job = authorized_job(request, job_id)
        pdf = job.output_dir / "tem-report.pdf"
        if not pdf.is_file():
            raise ApiException(503, "TEM_PREVIEW_UNAVAILABLE", job.review_error or "미리보기를 다시 생성하세요.")
        try:
            if page < 0:
                raise IndexError(page)
            with _ahn().tem_history.lease(job):
                content = slide_image(str(pdf), pdf.stat().st_mtime_ns, page)
        except IndexError as exc:
            raise ApiException(404, "REPORT_PAGE_NOT_FOUND", "보고서 페이지를 찾을 수 없습니다.") from exc
        return Response(content, media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @router.get(prefix + "/{job_id}/preview", tags=["tem"])
    def preview(request: Request, job_id: str):
        # Same image-based UI on old browsers, without relying on a PDF plug-in.
        job = authorized_job(request, job_id)
        with _ahn().tem_history.lease(job):
            _, layout, _ = tem_editable.inspect_pptx(job.pptx_path)
        # Load one slide at a time, including Chrome 49 without loading=lazy.
        script = '''(function(){var page=0,count=__COUNT__,base=__BASE__;
function show(){document.getElementById('status').textContent=(page+1)+' / '+count;
document.getElementById('image').src=base+'/'+page;
document.getElementById('previous').disabled=page===0;document.getElementById('next').disabled=page===count-1;}
document.getElementById('previous').onclick=function(){if(page>0){page--;show();}};
document.getElementById('next').onclick=function(){if(page<count-1){page++;show();}};show();}());'''
        script = script.replace("__COUNT__", str(len(layout["pages"]))).replace("__BASE__", json.dumps(prefix + "/" + job.job_id + "/edit-slides"))
        return HTMLResponse('<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TEM 보고서</title><style>body{margin:20px;background:#eef2f6;font:16px Arial}img{max-width:100%;display:block}button{padding:10px;margin:8px;white-space:nowrap}</style><button id="previous">이전</button><button id="next">다음</button><span id="status"></span><img id="image" alt="TEM 보고서 페이지"><script>' + script + '</script></html>', headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"})

    @router.get(prefix + "/{job_id}/download", tags=["tem"])
    def download(request: Request, job_id: str):
        job = authorized_job(request, job_id)
        return _ahn().tem_history.HistoryFileResponse(job, job.package_path, media_type="application/zip", filename="tem-report-package.zip")

    @router.post(prefix + "/{job_id}/revisions", status_code=202, tags=["tem"])
    async def revise(request: Request, job_id: str):
        job = authorized_job(request, job_id)
        origin = request.headers.get("origin")
        if request.headers.get("x-requested-with") != "RIST-Report-Editor" or (origin and
                (urlsplit(origin).scheme, urlsplit(origin).netloc) != (request.url.scheme, request.url.netloc)):
            raise ApiException(403, "REPORT_EDIT_ORIGIN_INVALID", "같은 사이트의 보고서 미리보기에서 저장하세요.")
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 2 * 1024 * 1024:
                raise ApiException(413, "REPORT_EDIT_TOO_LARGE", "보고서 수정 내용은 2MB 이하로 입력하세요.")
        try:
            model = TemReportEdit.model_validate_json(content)
            values = model.model_dump()
            values = tem_editable.validate_changes(values, _state(job))
        except (ValidationError, ValueError) as exc:
            raise ApiException(422, "REPORT_EDIT_INVALID", "보고서 문구·표 구조·공통 항목을 확인하세요. " + (str(exc) if not isinstance(exc, ValidationError) else "")) from exc
        ahn = _ahn()
        if not ahn._ahn_pending_slots.acquire(blocking=False):
            raise ApiException(429, "REPORT_EDIT_BUSY", "TEM 처리 대기열이 가득 찼습니다. 잠시 후 다시 시도하세요.")
        new_job = None
        work_dir = None
        with ahn._ahn_report_jobs_lock:
            if job.history_deleted_at:
                ahn._ahn_pending_slots.release()
                raise ApiException(409, "TEM_HISTORY_TRASHED", "휴지통에서 작업을 복원한 뒤 수정하세요.")
            job.active_revisions += 1
        try:
            work_dir = ahn.tem_history.new_workspace(job.settings)
            new_job = ahn._create_ahn_job(work_dir / "input", work_dir, job.error_archive,
                job.settings, job.database, job.usage_archive, job.usage_client_context, owner_user_id=job.owner_user_id)
            new_job.parent_job_id = job.job_id
            new_job.history_title = (job.history_title + " (수정본)")[:120]
            ahn.tem_history.save(new_job)
            ahn._ahn_report_executor.submit(_revision, new_job, job, deepcopy(values))
        except Exception:
            with ahn._ahn_report_jobs_lock:
                job.active_revisions -= 1
                if new_job:
                    ahn._ahn_report_jobs.pop(new_job.job_id, None)
            ahn._ahn_pending_slots.release()
            if work_dir is not None:
                shutil.rmtree(work_dir, ignore_errors=True)
            raise
        return ahn._job_payload(new_job)

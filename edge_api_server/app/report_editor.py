"""Authenticated analyst review pages and immutable report revisions."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile
from threading import BoundedSemaphore, Thread
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import ApiException
from . import preview_report as reports
from .report.editable import (TRANSFER_FIELDS, apply_edits, document_from_dict, editor_document)
from .report.edited_analysis import analysis_preview
from .report.spectrum_viewer import build_spectrum_viewer
from .report.direct_edit import edit_pages, slide_image
from .report.editor_variables import resolve_changes
from .report.canvas import ObjectEdit
from .report.pages import AddedPage, PageEdits
from .report_queue import register_generated_report_package
from .storage import atomic_write_json
from .report_mail import mail_payload, edit_text_size
from rist_common.plotting.floating_panels import floating_panels_html
from rist_common.plotting.file_progress import file_progress_script

_REVISION_SLOT = BoundedSemaphore(1)
_MAX_EDIT_BYTES = 2 * 1024 * 1024


class SectionEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sectionId: str = Field(max_length=100)
    heading: str = Field(max_length=200)
    paragraphs: list[str] = Field(max_length=100)
    bullets: list[str] = Field(max_length=100)
    rows: list[list[str]] | None = Field(default=None, max_length=2000)


class CommonVariable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60)
    value: str = Field(max_length=1000)
    unit: str = Field(default="", max_length=40)


class ReportEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(max_length=200)
    transfer: dict[str, str]
    sections: list[SectionEdit] = Field(max_length=100)
    auxiliary: dict[str, str]
    deliveryEmail: dict[str, str] | None = None
    pptxProtection: dict[str, str] | None = None
    completionDate: str | None = Field(default=None, max_length=10)
    variables: list[CommonVariable] = Field(default_factory=list, max_length=32)
    pageEdits: PageEdits | None = None
    canvasEdits: dict[str, list[ObjectEdit]] | None = None


def job_response(job, prefix):
    result = job.to_dict(download_url=f"{prefix}/{job.job_id}/download")
    if job.status == "completed":
        result.update(previewUrl=f"{prefix}/{job.job_id}/preview", editUrl=f"{prefix}/{job.job_id}/edit")
    return result


def _authorized_job(request, job_id, kind):
    job = reports.preview_report_job_store(request.app).get(job_id)
    if job is None or job.filename != kind + "-report-package.zip":
        raise ApiException(404, "REPORT_JOB_NOT_FOUND", "보고서 작업을 찾을 수 없습니다.")
    settings = getattr(request.app.state, "settings", None)
    if settings and getattr(settings, "auth_enabled", False):
        from .auth import require_context
        context = require_context(request)
        if not context.is_admin and (kind.upper() not in context.projects or job.owner_user_id != context.user_id):
            raise ApiException(403, "REPORT_EDIT_FORBIDDEN", "보고서 작성자 또는 관리자만 미리보기 내용을 수정할 수 있습니다.")
    if job.status != "completed" or job.package_path is None:
        raise ApiException(409, "REPORT_NOT_READY", "보고서가 아직 완성되지 않았습니다.")
    if job.tmp_root is None or not (job.tmp_root / "job/report/report.json").is_file():
        raise ApiException(410, "REPORT_EDIT_EXPIRED", "편집용 보고서가 만료되었습니다. 분석 화면에서 보고서를 다시 생성하세요.")
    return job


def _load_document(job):
    report_dir = job.tmp_root / "job/report"
    value = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    return document_from_dict(value, image_path=report_dir / "current_graph.png")


def _validate_changes(changes, document):
    values = changes.model_dump()
    from .report.pptx_protection import validate_protection
    values['pptxProtection'] = validate_protection(values['pptxProtection'] if values['pptxProtection'] is not None else document.pptx_protection)
    from .report_mail import normalize_delivery
    values['deliveryEmail'] = normalize_delivery(values.get('deliveryEmail') if values.get('deliveryEmail') is not None else document.delivery_email)
    if values["canvasEdits"] is None:
        values["canvasEdits"] = deepcopy(document.canvas_edits)
    else:
        values["canvasEdits"] = {key: [obj.model_dump(exclude_none=True) for obj in items]
                                 for key,items in changes.canvasEdits.items()}
    if values["pageEdits"] is None:
        values["pageEdits"] = deepcopy(document.page_edits or {"hidden": [], "added": []})
    elif values["pageEdits"]["order"] is None:
        # Older clients know only inclusion/additions; never discard an author's order.
        values["pageEdits"].pop("order")
        if "order" in document.page_edits:
            values["pageEdits"]["order"] = deepcopy(document.page_edits["order"])
    if values["completionDate"] is None:
        values["completionDate"] = document.completion_date or ""
    if set(values["transfer"]) != set(TRANSFER_FIELDS):
        raise ValueError("전송 정보 항목이 올바르지 않습니다.")
    for key, value in values["transfer"].items():
        values["transfer"][key] = value.strip()
        if len(value) > (50 if key == "limsExperimentCode" else 100):
            raise ValueError("전송 정보가 너무 깁니다.")
    expected = {s.section_id: s for s in document.sections if s.section_id != "report_transfer"}
    ids = [item["sectionId"] for item in values["sections"]]
    if len(set(ids)) != len(ids) or set(ids) != set(expected):
        raise ValueError("보고서 항목이 변경되었습니다. 편집 화면을 다시 여세요.")
    for item in values["sections"]:
        section = expected[item["sectionId"]]
        texts = item["paragraphs"] + item["bullets"]
        if section.table:
            rows = item["rows"]
            if rows is None or len(rows) != len(section.table.rows) or any(len(row) != len(section.table.columns) for row in rows):
                raise ValueError("표의 행/열 구조는 변경할 수 없습니다. 빈칸에 내용을 입력하세요.")
            texts += [cell for row in rows for cell in row]
        elif item["rows"] is not None:
            raise ValueError("원본에 없는 표를 추가할 수 없습니다.")
        if any(len(text) > 4000 for text in texts):
            raise ValueError("문단 또는 표 셀은 4,000자 이하로 입력하세요.")
        if item["sectionId"] == "report_traceability" and any(len(text) > 200 for text in texts):
            raise ValueError("표지의 이력 정보는 항목당 200자 이하로 입력하세요. 상세 내용은 본문에 작성하세요.")
    if set(values["auxiliary"]) - {"email_subject", "email_body"} or any(len(v) > 4000 for v in values["auxiliary"].values()):
        raise ValueError("메일 문안이 올바르지 않습니다.")
    if edit_text_size(values) > 120000:
        raise ValueError("보고서 수정 내용이 너무 많습니다.")
    expanded = resolve_changes(values)
    for section in expanded["sections"]:
        if section["sectionId"] == "report_traceability" and any(len(cell) > 200 for row in section["rows"] for cell in row):
            raise ValueError("공통 항목을 반영한 표지 정보는 항목당 200자 이하여야 합니다.")
    if edit_text_size(expanded) > 120000:
        raise ValueError("공통 항목을 반영한 보고서 수정 내용이 너무 많습니다.")
    from .report.pages import validate
    candidate = deepcopy(document)
    apply_edits(candidate, expanded, document.experiment_code)
    validate(candidate, document)
    from .report.canvas import apply
    from .report.pages import compose
    from .report.renderers import _pptx_base_slide_payloads
    apply(candidate, compose(candidate, _pptx_base_slide_payloads(candidate)), validate_only=True,
          previous_edits=document.canvas_edits)
    return values


def _write_revision(store, new_job, old_job, changes, project, settings, database, actor_id=None):
    tmp_root = None
    try:
        tmp_root = Path(tempfile.mkdtemp(prefix="rist-preview-report-edit-"))
        store.update(new_job.job_id, status="running", stage="render", progress_pct=20,
                     message="수정 내용을 PPT/PDF에 반영하는 중입니다.")
        source = old_job.tmp_root / "job"
        report_dir = tmp_root / "job/report"
        processed_dir = tmp_root / "job/processed"
        report_dir.mkdir(parents=True)
        processed_dir.mkdir(parents=True)
        # Copy only our generated artifacts, not paths supplied by the browser.
        for name in ("current_graph.png", "raw_data.xlsx"):
            shutil.copyfile(source / "report" / name, report_dir / name)
        if reports._is_ftir_experiment(project):
            from .report.spectrum_viewer import FTIR_GRAPH_EXPORTS
            for name in FTIR_GRAPH_EXPORTS:
                # Text revisions do not change the issued graphs. Older reports
                # may lack the original RAW, so never fabricate it from the edits.
                if (source / "report" / name).is_file():
                    shutil.copyfile(source / "report" / name, report_dir / name)
        shutil.copyfile(source / "processed/analysis-result.json", processed_dir / "analysis-result.json")
        document = _load_document(old_job)
        if (source / "report/editor-assets").is_dir():
            shutil.copytree(source / "report/editor-assets", report_dir / "editor-assets")
        document.canvas_asset_root = str(report_dir / "editor-assets")
        document.job_id = new_job.job_id
        document.traceability = dict(document.traceability, revisionOf=old_job.job_id,
            savedAt=new_job.created_at.isoformat(), savedBy=actor_id)
        document.generated_at = changes.get("completionDate", document.completion_date) or ""
        for figure in document.figures:
            from .report.saved_graphs import figure_filename
            name = figure_filename(figure.figure_id)
            if name != "current_graph.png":
                shutil.copyfile(source / "report" / name, report_dir / name)
            figure.path = str(report_dir / name)
        apply_edits(document, resolve_changes(changes), project)
        # Authoring sources are never shipped in recipient packages (.json excluded).
        atomic_write_json(report_dir / "editor-state.json", changes)
        atomic_write_json(report_dir / "report.json", document.to_dict())
        (report_dir / "report.md").write_text(document.to_markdown(), encoding="utf-8")
        reports._write_email_body(document, report_dir)
        deck_pdf = bool(document.canvas_edits or any(document.page_edits.get(key) for key in ("hidden", "added", "order")))
        # Free/page-edited reports use the actual deck PDF. Do not render a
        # second prose PDF only to discard it immediately after conversion.
        formats = ["PPTX", "HTML"] if deck_pdf else ["PPTX", "PDF", "HTML"]
        reports.render_report_formats(document, report_dir, formats,
                                      pdf_font_path=reports._preview_pdf_font_path(settings))
        reports.convert_pptx_to_pdf(report_dir / "report.pptx", report_dir / "report-from-pptx.pdf")
        if deck_pdf:
            # Once the analyst edits the deck, both downloadable PDFs must use
            # that exact page list rather than the automatic prose summary.
            shutil.copyfile(report_dir / "report-from-pptx.pdf", report_dir / "report.pdf")
        analysis = json.loads((processed_dir / "analysis-result.json").read_text(encoding="utf-8"))
        build_spectrum_viewer(report_dir, title=document.title,
                              figure=analysis.get("viewerFigure") or analysis.get("figure") or {},
                              analysis=analysis_preview(document), graphs=analysis.get("reportGraphs") or [])
        package = reports.build_report_package(report_dir, tmp_root / "job/input", include_raw_files=False,
                                               pptx_protection=document.pptx_protection)
        transfer = changes["transfer"]
        package = register_generated_report_package(settings=settings, database=database,
            report_id=new_job.job_id, package_path=package, experiment_code=project,
            request_number=transfer["requestNumber"], equipment_code=transfer["equipmentCode"], operator_id=transfer["operatorId"],
            report_options={"revisionOf": old_job.job_id, "reviewed": True, "transfer": transfer,
                            "email": mail_payload(
                                document.auxiliary_texts.get('email_subject', ''), document.auxiliary_texts.get('email_body', ''), document.delivery_email)})
        new_job.transfer_metadata = dict(transfer)
        store.complete(new_job.job_id, tmp_root=tmp_root, package_path=package)
    except Exception:
        if tmp_root is not None:
            reports.cleanup_preview_report(tmp_root)
        store.fail(new_job.job_id, "수정본 생성에 실패했습니다. 원본은 유지되었습니다. 잠시 후 다시 저장하세요.")
        from rist_common import get_logger
        get_logger(__name__).exception("보고서 수정본 생성 실패 (job=%s)", new_job.job_id)
    finally:
        _REVISION_SLOT.release()


def editor_page(prefix, job_id, *, project=None):
    from .report_condition_options import condition_options

    project = project or ("RAMAN" if "/raman/" in prefix else "FT-IR")
    config = json.dumps({"prefix": prefix, "jobId": job_id, "project": project,
                         "conditionOptions": condition_options(project)}, ensure_ascii=False).replace("<", "\\u003c")
    template = Path(__file__).with_name("report_editor.html").read_text(encoding="utf-8")
    script = file_progress_script() + '\n' + (Path(__file__).parent / "xrd_workbench/web/shared/pptx_password.js").read_text(encoding="utf-8") + "\n"
    script += "\n".join((Path(__file__).parent / name).read_text(encoding="utf-8")
                       for name in ("file_save.js", "report_editor_icons.js", "report_page_sidebar.js", "report_cell_editor.js", "report_direct_editor.js", "report_canvas_editor.js", "report_saved_graphs.js", "report_condition_options.js", "xrd_workbench/web/shared/mail_editor_icons.js", "xrd_workbench/web/shared/mail_table_editor.js", "xrd_workbench/web/shared/mail_address_book.js", "xrd_workbench/web/shared/mail_template_context.js", "xrd_workbench/web/shared/mail_templates.js", "xrd_workbench/web/shared/mail_editor.js", "report_editor.js"))
    return template.replace("__CONFIG__", config).replace("__SCRIPT__", script).replace(
        "</body>", floating_panels_html() + "</body>", 1
    )


def install_report_editor(router, kind):
    prefix = f"/api/v1/{kind}/report/jobs"
    project = "FT-IR" if kind == "ftir" else "RAMAN"

    @router.post(prefix + "/{job_id}/pptx-password", tags=[kind])
    async def pptx_password(request: Request, job_id: str):
        _authorized_job(request, job_id, kind)
        from .report.pptx_protection import password_verifier_response
        return await password_verifier_response(request)

    @router.get(prefix + "/{job_id}/editor-status", tags=[kind])
    def editor_status(request: Request, job_id: str):
        # Check the same ownership, readiness and source files as the editor
        # before navigating an iframe (where API errors otherwise show as JSON).
        job = _authorized_job(request, job_id, kind)
        return JSONResponse(job_response(job, prefix), headers={"Cache-Control": "no-store"})

    @router.get(prefix + "/{job_id}/edit", response_class=HTMLResponse, tags=[kind])
    def page(request: Request, job_id: str):
        _authorized_job(request, job_id, kind)
        return HTMLResponse(editor_page(prefix, job_id), headers={"Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "SAMEORIGIN",
            "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; frame-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"})

    @router.get(prefix + "/{job_id}/editor-data", tags=[kind])
    def data(request: Request, job_id: str):
        job = _authorized_job(request, job_id, kind)
        document = _load_document(job)
        model = editor_document(document)
        model.update(completionDate=document.completion_date or "", variables=[])
        state_path = job.tmp_root / "job/report/editor-state.json"
        if state_path.is_file():
            source = json.loads(state_path.read_text(encoding="utf-8"))
            model.update({key: source[key] for key in ("title", "auxiliary", "deliveryEmail", "completionDate", "variables", "pageEdits", "canvasEdits") if key in source and source[key] is not None})
            by_id = {s["sectionId"]: s for s in source["sections"]}
            for section in model["sections"]:
                saved = by_id.get(section["sectionId"])
                if saved:
                    section.update({key: saved[key] for key in ("heading", "paragraphs", "bullets", "rows")})
        result = {"document": model, "job": job_response(job, prefix)}
        context = getattr(request.state, "auth_context", None)
        identity = getattr(context, "sso_identity", None) or {}
        result["authenticatedOperatorId"] = str(identity.get("employee_id") or identity.get("subject") or
            (getattr(context, "login_id", "") if identity else ""))[:100]
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.get(prefix + "/{job_id}/edit-layout", tags=[kind])
    def layout(request: Request, job_id: str):
        job = _authorized_job(request, job_id, kind)
        try:
            value = edit_pages(_load_document(job), job.tmp_root / "job/report/report.pptx")
        except ValueError as exc:
            raise ApiException(409, "REPORT_LAYOUT_CHANGED", str(exc)) from exc
        return JSONResponse(value, headers={"Cache-Control": "no-store"})

    @router.get(prefix + "/{job_id}/edit-slides/{page}", tags=[kind])
    def slide(request: Request, job_id: str, page: int, thumbnail: bool = False):
        job = _authorized_job(request, job_id, kind)
        pdf = job.tmp_root / "job/report/report-from-pptx.pdf"
        if not pdf.is_file():
            raise ApiException(410, "REPORT_EDIT_EXPIRED", "PPT 미리보기가 만료되었습니다.")
        try:
            if page < 0 or page >= 200:
                raise IndexError(page)
            content = slide_image(str(pdf), pdf.stat().st_mtime_ns, page, thumbnail=thumbnail)
        except IndexError as exc:
            raise ApiException(404, "REPORT_PAGE_NOT_FOUND", "보고서 페이지를 찾을 수 없습니다.") from exc
        # Always authorize and validate the page before revalidation, even for
        # cached images. Private/no-cache requires a server check on every use;
        # a revision has its own URL and thumbnails have their own validator.
        etag = '"' + sha256(content).hexdigest() + '"'
        headers = {"Cache-Control": "private, no-cache", "ETag": etag,
                   "Vary": "Cookie", "X-Content-Type-Options": "nosniff"}
        validators = [value.strip().removeprefix("W/") for value in request.headers.get("if-none-match", "").split(",")]
        if etag in validators or "*" in validators:
            return Response(status_code=304, headers=headers)
        return Response(content, media_type="image/jpeg", headers=headers)

    @router.post(prefix + "/{job_id}/editor-graphs", tags=[kind])
    @router.post(prefix + "/{job_id}/editor-images", tags=[kind])
    async def upload_image(request: Request, job_id: str):
        job = _authorized_job(request, job_id, kind)
        origin = request.headers.get("origin")
        if request.headers.get("x-requested-with") != "RIST-Report-Editor" or (origin and
                (urlsplit(origin).scheme, urlsplit(origin).netloc) != (request.url.scheme, request.url.netloc)):
            raise ApiException(403, "REPORT_EDIT_ORIGIN_INVALID", "같은 사이트에서 이미지를 첨부하세요.")
        saved_graph = request.url.path.endswith("/editor-graphs")
        max_bytes = (16 if saved_graph else 2) * 1024 * 1024
        content = bytearray()
        async for chunk in request.stream():
            if len(content)+len(chunk)>max_bytes:
                raise ApiException(413, "REPORT_IMAGE_TOO_LARGE", f"이미지는 {max_bytes // (1024 * 1024)}MB 이하로 첨부하세요.")
            content.extend(chunk)
        from .report.canvas import store_image
        from starlette.concurrency import run_in_threadpool
        try:
            return await run_in_threadpool(store_image, job.tmp_root / "job/report/editor-assets", bytes(content), saved_graph=saved_graph)
        except ValueError as exc:
            raise ApiException(422, "REPORT_IMAGE_INVALID", str(exc)) from exc

    @router.post(prefix + "/{job_id}/revisions", status_code=202, tags=[kind])
    async def revise(request: Request, job_id: str):
        job = _authorized_job(request, job_id, kind)
        origin = request.headers.get("origin")
        if request.headers.get("x-requested-with") != "RIST-Report-Editor" or (origin and
                (urlsplit(origin).scheme, urlsplit(origin).netloc) != (request.url.scheme, request.url.netloc)):
            raise ApiException(403, "REPORT_EDIT_ORIGIN_INVALID", "같은 사이트의 보고서 미리보기에서 저장하세요.")
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > _MAX_EDIT_BYTES:
                raise ApiException(413, "REPORT_EDIT_TOO_LARGE", "보고서 수정 내용은 2MB 이하로 입력하세요.")
        try:
            changes = ReportEdit.model_validate_json(content)
            values = _validate_changes(changes, _load_document(job))
        except ValidationError as exc:
            raise ApiException(422, "REPORT_EDIT_INVALID", "보고서 입력값 또는 표 구조를 확인하세요.") from exc
        except ValueError as exc:
            raise ApiException(422, "REPORT_EDIT_INVALID", str(exc)) from exc
        if not _REVISION_SLOT.acquire(blocking=False):
            raise ApiException(429, "REPORT_EDIT_BUSY", "다른 수정본을 생성 중입니다. 완료 후 다시 저장하세요.")
        store = reports.preview_report_job_store(request.app)
        new_job = None
        try:
            store.update(job.job_id, message="수정본을 생성 중입니다. 원본은 유지됩니다.")
            new_job = store.create(filename=job.filename, owner_user_id=job.owner_user_id, parent_job_id=job.job_id)
            Thread(target=_write_revision, args=(store, new_job, job, deepcopy(values), project,
                getattr(request.app.state, "settings", None), getattr(request.app.state, "database", None),
                getattr(getattr(request.state, "auth_context", None), "user_id", None)), daemon=True).start()
        except Exception:
            _REVISION_SLOT.release()
            if new_job is not None:
                store.remove(new_job.job_id)
            raise
        return job_response(new_job, prefix)

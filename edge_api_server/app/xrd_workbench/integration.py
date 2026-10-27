"""RIST adapters for the imported b1398ba XRD workbench.

No standalone app, authentication database or shared global project directory.
Existing XRD data/API remain untouched. Sources and documents are private to the
RIST account; reports enter the existing MariaDB queue only after its SSO guard.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import time
import threading
from types import SimpleNamespace
from urllib.parse import urlsplit
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from ..auth import authenticated_transfer_payload, require_context
from ..browser_compat import browser_profile, manifest, ASSET_PREFIX
from ..errors import ApiException
from ..error_archive import error_archive, record_background_error
from ..preview_report import PreviewReportSendRequest, send_preview_report_package
from ..report_queue import ReportQueueError, register_generated_report_package
from ..report_mail import mail_payload
from ..upload_sessions import ChunkUploadStore, UploadSession, UploadFileState
from ..usage_archive import set_usage_context, usage_archive, record_background_usage
from .core.report import validate_document
from .locking import ProcessLock
from .storage import Storage, _write_json_atomic
from .routes.analysis import build_analysis_router
from .routes.presets import build_preset_router
from .routes.projects import build_project_router
from .project_cleanup import build_cleanup_router
from .routes.reports import build_report_router
from .routes.drafts import build_draft_router

BASE = '/api/v1/xrd/workbench'
WEB = Path(__file__).parent / 'web'
ID = re.compile(r'^[0-9a-f]{32}$')
NO_STORE = {'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'}
current: ContextVar = ContextVar('rist_xrd_request')
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='rist-xrd-workbench')
logger = logging.getLogger(__name__)
# Drafts are ephemeral. At most one preview per worker waits for the shared
# native-render lock; do not fill the durable export queue on every keystroke.
draft_preview_slot = threading.BoundedSemaphore(1)


def checked_id(value):
    if not ID.fullmatch(value):
        raise HTTPException(404, '작업을 찾을 수 없습니다.')
    return value


async def bind_request(request: Request):
    settings = request.app.state.settings
    context = require_context(request) if settings.auth_enabled else None
    if context and not context.is_admin and 'XRD' not in context.projects:
        raise HTTPException(403, 'XRD 접근 권한이 필요합니다.')
    origin = request.headers.get('origin')
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin:
        if urlsplit(origin).netloc != request.url.netloc:
            raise HTTPException(403, '동일한 서버 화면에서 요청하세요.')
    if request.headers.get('content-type', '').startswith('application/json'):
        raw = bytearray()
        async for part in request.stream():
            raw.extend(part)
            if len(raw) > 8 * 1024 * 1024:
                raise HTTPException(413, '편집 정보는 8MB 이하로 저장하세요.')
        request._body = bytes(raw)
    root = settings.storage_root / 'xrd-workbench-v1'
    owner = hashlib.sha256((context.user_id if context else 'development').encode()).hexdigest()
    home = root / 'users' / owner
    value = SimpleNamespace(root=root, home=home, storage=Storage(home), request=request,
                            settings=settings, owner=owner, context=context)
    token = current.set(value)
    set_usage_context(request, project='XRD', experiment_code='XRD')
    try:
        yield
    finally:
        current.reset(token)


class StorageProxy:
    """Resolve storage *during* a request, including propagated worker contexts."""
    def __init__(self, parts=()):
        self.parts = parts

    def __getattr__(self, name):
        return StorageProxy(self.parts + (name,))

    def resolve(self):
        value = current.get().storage
        for name in self.parts:
            value = getattr(value, name)
        return value

    def __call__(self, *args, **kwargs):
        return self.resolve()(*args, **kwargs)

    def __enter__(self):
        return self.resolve().__enter__()

    def __exit__(self, *args):
        return self.resolve().__exit__(*args)


class BoundedRoute(APIRoute):
    """Bound bodies before Starlette spools multipart or FastAPI parses JSON."""
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def limited(request):
            limit = 5 * 1024 * 1024 if request.url.path.endswith('/chunks') else 8 * 1024 * 1024
            try:
                length = int(request.headers.get('content-length', '0'))
                if length < 0:
                    raise ValueError
            except ValueError as exc:
                raise HTTPException(400, '요청 크기 헤더가 올바르지 않습니다.') from exc
            if length > limit:
                raise HTTPException(413, '요청 크기가 한도를 초과했습니다. 분할 업로드를 사용하세요.')
            receive, count = request._receive, 0
            async def bounded_receive():
                nonlocal count
                message = await receive()
                count += len(message.get('body', b''))
                if count > limit:
                    raise HTTPException(413, '요청 크기가 한도를 초과했습니다.')
                return message
            request._receive = bounded_receive
            return await handler(request)
        return limited


router = APIRouter(prefix=BASE, dependencies=[Depends(bind_request)], tags=['xrd-workbench'], route_class=BoundedRoute)
proxy = StorageProxy()
# Import the domain editors, not the standalone all-in-memory upload/export routes.
projects = build_project_router(proxy)
projects.routes = [r for r in projects.routes if r.path not in ('/api/upload', '/api/example')]
reports = build_report_router(proxy.load_project, proxy.files.read_source, proxy.reports)
reports.routes = [r for r in reports.routes if not r.path.endswith(('/pdf', '/pptx', '/zip', '/preview'))]
router.include_router(projects)
router.include_router(build_cleanup_router(current.get, lambda *args: start_job(*args), lambda *args: read_job(*args)))
router.include_router(build_analysis_router(proxy.files.load_source, proxy.files.read_source, proxy.reviews))
router.include_router(reports)
router.include_router(build_draft_router(current.get))
router.include_router(build_preset_router(proxy.presets))


def upload_store():
    return ChunkUploadStore(code_prefix='XRD_WORKBENCH', temp_prefix='rist-xrd-',
        allowed_extensions={'.txt', '.pdf', '.png', '.jpg', '.jpeg'},
        max_file_bytes=80 * 1024 * 1024, max_total_bytes=1200 * 1024 * 1024)


def session_path(state, sid):
    return state.home / 'sessions' / checked_id(sid)


def read_session(state, sid):
    directory = session_path(state, sid)
    try:
        value = json.loads((directory / 'session.json').read_text())
    except FileNotFoundError:
        raise HTTPException(404, '업로드가 만료되었거나 본인의 작업이 아닙니다.')
    if value.get('canceled'):
        raise HTTPException(410, '취소한 업로드입니다. 새 작업으로 등록하세요.')
    if time.time() - value['updated_at'] > 7200 and not value.get('job_id'):
        raise HTTPException(410, '업로드 시간이 만료되었습니다. 다시 등록하세요.')
    files = {}
    for name, item in value['files'].items():
        # Never trust serialized absolute paths; reconstitute inside the owned session.
        stored = item['stored_name']
        if Path(stored).is_absolute() or '..' in Path(stored).parts:
            raise HTTPException(409, '업로드 경로가 손상되었습니다.')
        files[name] = UploadFileState(name, stored, stored + '.part',
                                     item['total_size'], item['uploaded_bytes'], item['completed'])
    session = UploadSession(sid, directory, directory / 'input', value['created_at'], time.time(), files)
    return session, value


def save_session(session, job_id=None):
    path = session.work_dir / 'session.json'
    value = json.loads(path.read_text()) if path.is_file() else {}
    value.update(created_at=session.created_at, updated_at=time.time(), job_id=job_id, files={})
    for name, item in session.files.items():
        value['files'][name] = dict(stored_name=str(item.stored_path),
            total_size=item.total_size, uploaded_bytes=item.uploaded_bytes, completed=item.completed)
    _write_json_atomic(session.work_dir / 'session.json', value)


@router.post('/api/upload-sessions', status_code=201)
def create_upload():
    state = current.get()
    sid = uuid.uuid4().hex
    directory = session_path(state, sid)
    (directory / 'input').mkdir(parents=True)
    now = time.time()
    save_session(UploadSession(sid, directory, directory / 'input', now, now))
    return dict(upload_id=sid)


@router.get('/api/upload-sessions')
def pending_uploads():
    state = current.get()
    results = []
    for path in sorted((state.home / 'sessions').glob('*/session.json'), key=lambda p:p.stat().st_mtime, reverse=True):
        value = json.loads(path.read_text())
        if value.get('canceled') or time.time()-value['updated_at'] > 7200:
            continue
        if value.get('job_id') and read_job(state, value['job_id'])['status'] == 'completed':
            continue
        results.append(dict(upload_id=path.parent.name, updated_at=value['updated_at'],
                            filenames=list(value['files'])))
        if len(results) == 20:
            break
    return JSONResponse(results, headers=NO_STORE)


def session_progress(session):
    """Recover an acknowledged offset from disk; hash only the stored prefix in bounded blocks."""
    result = []
    for name, item in session.files.items():
        destination, partial = session.input_root / item.stored_path, session.input_root / item.temp_path
        path = destination if destination.is_file() else partial
        size = path.stat().st_size if path.is_file() else 0
        if size > item.total_size or (path == destination and size != item.total_size):
            raise HTTPException(409, name + ': 서버 파일 크기가 다릅니다. 새 업로드로 등록하세요.')
        if path == partial and size == item.total_size:
            # A server interruption can occur after the last byte, before rename.
            partial.replace(destination)
            path = destination
        item.uploaded_bytes, item.completed = size, path == destination and size == item.total_size
        digest = hashlib.sha256()
        if size:
            with path.open('rb') as stream:
                while block := stream.read(256 * 1024):
                    digest.update(block)
        result.append(dict(name=name, size=item.total_size, uploaded_bytes=size,
                           completed=item.completed, prefix_sha256=digest.hexdigest()))
    return result


@router.get('/api/upload-sessions/{sid}')
def upload_progress(sid: str):
    state = current.get()
    with ProcessLock(session_path(state, sid) / '.lock'):
        session, value = read_session(state, sid)
        files = session_progress(session)
        save_session(session, value.get('job_id'))
        return JSONResponse(dict(upload_id=sid, files=files, mappings=value.get('mappings', {}),
                    job=read_job(state,value['job_id']) if value.get('job_id') else None), headers=NO_STORE)


@router.put('/api/upload-sessions/{sid}/mappings')
def upload_mappings(sid: str, payload: dict):
    state = current.get()
    if len(payload)>100 or any(not isinstance(v,dict) for v in payload.values()):
        raise HTTPException(422, '파일 연결은 최대 100개입니다.')
    with ProcessLock(session_path(state, sid) / '.lock'):
        session, value = read_session(state, sid)
        if value.get('job_id'):
            raise HTTPException(409, '이미 분석을 시작한 업로드입니다.')
        value['mappings'] = payload
        _write_json_atomic(session.work_dir / 'session.json', value)
    return dict(ok=True)


@router.post('/api/upload-sessions/{sid}/cancel')
def cancel_upload(sid: str):
    state = current.get()
    with ProcessLock(session_path(state, sid) / '.lock'):
        session, value = read_session(state, sid)
        if value.get('job_id'):
            raise HTTPException(409, '서버 분석이 시작되어 취소할 수 없습니다. 작업 상태를 확인하세요.')
        value.update(canceled=True, updated_at=time.time())
        _write_json_atomic(session.work_dir / 'session.json', value)
    return dict(canceled=True)  # Preserve partial files for investigation, never delete source data.


@router.post('/api/upload-sessions/{sid}/preflight')
def raw_preflight(sid: str):
    from .core.parsing.txt import parse_txt
    state = current.get()
    with ProcessLock(session_path(state, sid) / '.lock'):
        session, value = read_session(state, sid)
        if not session.files or any(not f.completed for f in session.files.values()):
            raise HTTPException(409, '파일 전송을 완료한 뒤 Raw 진단을 실행하세요.')
        results, count = [], 0
        for name, item in session.files.items():
            mapping = value.get('mappings', {}).get(name, {})
            if not name.lower().endswith('.txt') or mapping.get('role') == 'ignore':
                continue
            try:
                parsed = parse_txt((session.input_root/item.stored_path).read_bytes(), name)
                count += len(parsed['x'])
                results.append(dict(name=name, ok=True, points=len(parsed['x']),
                    x_range=[parsed['x'][0],parsed['x'][-1]], y_range=[min(parsed['y']),max(parsed['y'])],
                    unit=parsed['intensity_unit'] or mapping.get('intensity_unit') or '미확인',
                    axis='2θ (deg)' if mapping.get('axis_kind')=='two_theta' else 'PDF/사용자 확인 필요',
                    preview=[dict(line=n,x=x,y=y) for n,x,y in zip(parsed['line_numbers'][:6],parsed['x'][:6],parsed['y'][:6])]))
            except (ValueError, UnicodeError) as exc:
                results.append(dict(name=name,ok=False,error=str(exc),hint='장비 내보내기 설정의 두 숫자 열·구분자·각도 증가 순서와 해당 행을 확인하세요. 원본은 자동 수정하지 않습니다.'))
        errors = []
        if not results:
            errors.append('분석할 측정 TXT가 없습니다.')
        if count > 250000:
            errors.append('총 측정점 250,000개를 초과했습니다. 원본을 유지한 별도 작업으로 나누세요.')
        return dict(files=results,total_points=count,errors=errors,ok=bool(results) and all(r['ok'] for r in results) and not errors)


@router.post('/api/upload-sessions/{sid}/chunks')
async def upload_chunk(sid: str, relative_path: str = Form(...), offset: int = Form(...),
                       total_size: int = Form(...), chunk_index: int = Form(...),
                       chunk_count: int = Form(...), file: UploadFile = File(...)):
    state = current.get()
    if offset < 0 or offset > total_size or chunk_index < 0 or chunk_count <= 0 or chunk_index >= chunk_count:
        raise HTTPException(400, '업로드 조각 위치/번호가 올바르지 않습니다.')
    if file.size is not None and file.size > 4 * 1024 * 1024:
        raise HTTPException(413, '분할 조각은 4MB 이하로 전송하세요.')
    def write():
        with ProcessLock(session_path(state, sid) / '.lock'):
            session, value = read_session(state, sid)
            if value.get('job_id'):
                raise HTTPException(409, '이미 처리 중인 업로드입니다.')
            if relative_path not in session.files and len(session.files) >= 100:
                raise HTTPException(422, '한 작업에는 최대 100개 파일을 등록하세요.')
            store = upload_store()
            existing = session.files.get(relative_path)
            if existing and offset < existing.uploaded_bytes:
                # Idempotent retries must never truncate a later acknowledged chunk.
                path = session.input_root / (existing.stored_path if existing.completed else existing.temp_path)
                data = asyncio.run(file.read())
                asyncio.run(file.close())
                with path.open('rb') as stream:
                    stream.seek(offset)
                    if not data or total_size != existing.total_size or offset+len(data)>existing.uploaded_bytes or stream.read(len(data)) != data:
                        raise HTTPException(409, '이미 전송된 파일 내용과 다릅니다. 원본 파일을 다시 선택하거나 새 업로드로 등록하세요.')
                return dict(uploaded_bytes=existing.uploaded_bytes,completed=existing.completed)
            item = asyncio.run(store.write_chunk(session, relative_path=relative_path, offset=offset,
                total_size=total_size, chunk_index=chunk_index, chunk_count=chunk_count, upload=file))
            save_session(session)
            return dict(uploaded_bytes=item.uploaded_bytes, completed=item.completed)
    return await run_in_threadpool(write)


def job_path(state, jid):
    return state.home / 'jobs' / checked_id(jid)


def read_job(state, jid):
    try:
        value = json.loads((job_path(state, jid) / 'job.json').read_text())
    except FileNotFoundError:
        raise HTTPException(404, '본인의 작업을 찾을 수 없습니다.')
    if value['status'] in ('queued', 'running'):
        try:
            os.kill(value['pid'], 0)
        except ProcessLookupError:
            value.update(status='failed', error='서버가 재시작되어 작업이 중단되었습니다. 다시 생성하세요.')
    return value


def start_job(state, kind, work):
    # Bound outstanding jobs across workers; native PDF/Matplotlib rendering is
    # serialized below as those libraries also have process-global font state.
    with ProcessLock(state.root / '.queue.lock'):
        pending = 0
        for p in state.root.glob('users/*/jobs/*/job.json'):
            v = json.loads(p.read_text())
            if v['status'] in ('queued', 'running'):
                try:
                    os.kill(v['pid'], 0)
                    pending += 1
                except ProcessLookupError:
                    pass
        if pending >= 16:
            raise HTTPException(429, 'XRD 처리 대기열이 가득 찼습니다. 잠시 후 다시 시도하세요.')
        jid = uuid.uuid4().hex
        directory = job_path(state, jid)
        value = dict(job_id=jid, kind=kind, status='queued', pid=os.getpid(), created_at=time.time())
        _write_json_atomic(directory / 'job.json', value)
    def run():
        started = time.monotonic()
        try:
            with ProcessLock(state.root / '.render.lock'):
                value.update(status='running')
                _write_json_atomic(directory / 'job.json', value)
                result = work(directory)
                value.update(status='completed', **result)
        except Exception as exc:
            logger.exception('XRD workbench job failed: %s', jid)
            message = str(exc) if isinstance(exc, (ValueError, ApiException, HTTPException)) else '처리에 실패했습니다. 원본 파일과 서버 오류 기록을 확인하세요.'
            value.update(status='failed', error=message[:2000])
            value['error_event_id'] = record_background_error(
                error_archive(state.request.app), project='XRD', code='XRD_WORKBENCH_FAILED',
                message=value['error'], exception=exc, job_id=jid)
        record_background_usage(usage_archive(state.request.app), project='XRD',
            action='XRD_WORKBENCH_' + kind.upper(),
            result='success' if value['status'] == 'completed' else 'failed',
            duration_ms=int((time.monotonic()-started)*1000), job_id=jid,
            endpoint=BASE+'/api/jobs/'+jid, experiment_code='XRD',
            operator_id=state.context.login_id if state.context else None)
        _write_json_atomic(directory / 'job.json', value)
    executor.submit(run)
    return value.copy()


@router.post('/api/upload-sessions/{sid}/complete', status_code=202)
def complete_upload(sid: str, mappings: dict):
    state = current.get()
    with ProcessLock(session_path(state, sid) / '.lock'):
        session, value = read_session(state, sid)
        if value.get('job_id'):
            return read_job(state, value['job_id'])
        if not session.files or any(not f.completed for f in session.files.values()):
            raise HTTPException(409, '파일 전송이 모두 완료되지 않았습니다.')
        inputs = [(name, session.input_root / f.stored_path) for name, f in session.files.items()]
        def work(directory):
            project = state.storage.files.create(inputs, mappings)
            return dict(storage_id=project['storage_id'])
        job = start_job(state, 'import', work)
        save_session(session, job['job_id'])
        return job


@router.get('/api/jobs/{jid}')
def job_status(jid: str):
    return read_job(current.get(), jid)


def saved_report(state, sid, revision):
    with state.storage.lock:
        try:
            project = state.storage.load_project(sid)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(404, '본인의 프로젝트를 찾을 수 없습니다.') from exc
        document = state.storage.reports.load(sid)
        if not document or document['revision'] != revision:
            raise HTTPException(409, '현재 보고서를 저장한 후 다시 생성하세요.')
        if document.get('analysis_revision', 0) != project.get('analysis_revision', 0):
            raise HTTPException(409, '분석 검토값이 변경되었습니다. 보고서를 다시 검토·저장하세요.')
        if any(s.get('analysis_review_required') for s in document['samples'] if s['included']):
            raise HTTPException(409, '변경된 분석 결과 검토를 완료하세요.')
        return project, validate_document(project, document)


@router.post('/api/reports/{sid}/exports', status_code=202)
def export_report(sid: str, payload: dict):
    state = current.get()
    checked_id(sid)
    kind = payload.get('kind')
    if kind not in ('pdf', 'pptx', 'zip', 'preview'):
        raise HTTPException(422, '지원하지 않는 보고서 형식입니다.')
    project, document = saved_report(state, sid, payload.get('revision'))
    if payload.get('analysis_revision', project.get('analysis_revision', 0)) != project.get('analysis_revision', 0):
        raise HTTPException(409, '분석 버전이 변경되었습니다. 다시 여세요.')
    transfer = payload.get('transfer') or {}
    for key in ('requestNumber', 'experimentCode', 'equipmentCode', 'operatorId'):
        if not isinstance(transfer.get(key, ''), str) or len(transfer.get(key, '')) > 100:
            raise HTTPException(422, '전송 정보 형식을 확인하세요.')
    if transfer.get('requestNumber') and document['metadata'].get('report_number') != transfer['requestNumber']:
        raise HTTPException(409, '표지의 의뢰번호와 전송 의뢰번호를 같게 저장하세요.')
    for field, key in (('experimentCode', 'experiment_code'), ('equipmentCode', 'equipment_code'), ('operatorId', 'operator_id')):
        if transfer.get(field) and document['metadata'].get(key) != transfer[field]:
            raise HTTPException(409, '보고서 전송정보를 저장한 후 다시 생성하세요.')
    def work(directory):
        from .render.posco_preview import report_pages
        from .render.pdf import render_pdf
        from .render.posco_pptx import render_report_pptx
        from .render.package import build_package
        source = lambda fid: state.storage.files.read_source(sid, fid)
        asset_root = state.storage.reports.path(sid).parent / 'editor-assets'
        output = directory / ('report.html' if kind == 'preview' else 'report.' + kind)
        # Snapshot the exact document/analysis versions used for this artifact.
        _write_json_atomic(directory / 'document.json', document)
        if kind == 'zip':
            build_package(project, document, source, payload.get('viewer_state'),
                          output_path=output, asset_root=asset_root)
            register_generated_report_package(
                settings=state.settings, database=state.request.app.state.database,
                report_id=directory.name, package_path=output,
                experiment_code=transfer.get('experimentCode') or 'XRD',
                request_number=transfer.get('requestNumber', ''),
                equipment_code=transfer.get('equipmentCode', ''),
                operator_id=transfer.get('operatorId', ''),
                report_options={'analysisType': 'XRD', 'workbenchVersion': 1,
                                'documentRevision': document['revision'],
                                'email': mail_payload(document.get('email', {}).get('subject', ''),
                                    document.get('email', {}).get('body', ''), document.get('email', {}).get('delivery'))})
        else:
            if kind == 'preview':
                from .render.posco_preview import render_posco_preview
                content = render_posco_preview(project, document, source, storage_id=sid, asset_root=asset_root)
                if len(content.encode('utf-8')) > 32 * 1024 * 1024:
                    raise ValueError('미리보기가 32MB를 초과했습니다. 시료를 나누거나 PDF/PPTX로 다운로드하세요.')
                output.write_text(content, encoding='utf-8')
            else:
                base = state.settings.edge_public_base_url.rstrip('/') + BASE + '/'
                pages = report_pages(project, document, source, storage_id=sid, link_base=base, asset_root=asset_root) if kind == 'pdf' else None
                data = (render_pdf(pages, document, link_base=base, storage_id=sid) if kind == 'pdf'
                        else render_report_pptx(project, document, source, pages=pages,
                                                link_base=base, storage_id=sid, delivery=True, asset_root=asset_root))
                output.write_bytes(data)
        return dict(storage_id=sid, revision=document['revision'],
                    analysis_revision=project.get('analysis_revision', 0), filename=output.name,
                    transfer=transfer)
    # Share the project lock with permanent cleanup. A snapshot validated before
    # archiving must not enter the queue after its source files have been purged.
    with state.storage.lock:
        saved_report(state, sid, payload.get('revision'))
        return start_job(state, kind, work)


@router.post('/api/reports/{sid}/draft-preview')
def draft_preview(sid: str, payload: dict):
    state = current.get()
    checked_id(sid)
    if not draft_preview_slot.acquire(blocking=False):
        raise HTTPException(429, '다른 미리보기를 처리 중입니다. 잠시 후 다시 갱신하세요.')
    try:
        with ProcessLock(state.root / '.render.lock'):
            with state.storage.lock:
                try:
                    project = state.storage.load_project(sid)
                except (FileNotFoundError, ValueError) as exc:
                    raise HTTPException(404, '본인의 프로젝트를 찾을 수 없습니다.') from exc
                saved = state.storage.reports.load(sid)
                draft = payload.get('document')
                if not isinstance(draft, dict):
                    raise HTTPException(422, '보고서 초안 형식을 확인하세요.')
                if draft.get('revision') != (saved['revision'] if saved else 0):
                    raise HTTPException(409, '다른 창에서 보고서가 변경되었습니다. 분석값 갱신으로 병합 후 확인하세요.')
                if draft.get('analysis_revision', 0) != project.get('analysis_revision', 0):
                    raise HTTPException(409, '분석 검토값이 변경되었습니다. 분석값 갱신 후 확인하세요.')
                document = validate_document(project, draft)
            from .render.posco_preview import render_posco_preview
            from .render.figure import render_panel_png
            from .render.draft_cache import draft_images
            asset_root = state.storage.reports.path(sid).parent / 'editor-assets'
            if payload.get('format') == 'editor':
                from .render.editor import preview
                value = preview(project, document, lambda fid: state.storage.files.read_source(sid, fid),
                    storage_id=sid, asset_root=asset_root,
                    image_renderer=lambda *args: draft_images.render((state.home, sid), render_panel_png, *args))
                return JSONResponse(value, headers=NO_STORE)
            content = render_posco_preview(project, document,
                lambda fid: state.storage.files.read_source(sid, fid), storage_id=sid, edit_links=True,
                image_renderer=lambda *args: draft_images.render((state.home, sid), render_panel_png, *args), asset_root=asset_root)
            if len(content.encode('utf-8')) > 32 * 1024 * 1024:
                raise HTTPException(413, '미리보기가 32MB를 초과했습니다. 시료를 나누어 확인하세요.')
            return HTMLResponse(content, headers=NO_STORE)
    except ImportError as exc:
        raise ApiException(503, 'XRD_PREVIEW_DEPENDENCY_MISSING',
            'XRD 미리보기에 필요한 서버 라이브러리를 불러오지 못했습니다. 관리자가 배포 환경의 의존성 설치와 서버 재시작 여부를 확인해야 합니다.') from exc
    except MemoryError as exc:
        raise ApiException(503, 'XRD_PREVIEW_MEMORY_ERROR',
            '미리보기를 생성할 서버 메모리가 부족합니다. 포함 시료·이미지를 줄이거나 관리자에게 서버 자원 확인을 요청하세요.', retryable=True) from exc
    except OSError as exc:
        raise ApiException(503, 'XRD_PREVIEW_STORAGE_ERROR',
            '미리보기 생성에 필요한 서버 파일을 읽거나 쓸 수 없습니다. 관리자가 원자료·글꼴·임시 폴더의 존재 여부, 권한과 여유 공간을 확인해야 합니다.') from exc
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        draft_preview_slot.release()


@router.post('/api/reports/{sid}/editor-images')
async def editor_image(sid: str, request: Request):
    state=current.get(); checked_id(sid)
    if request.headers.get('x-requested-with') != 'RIST-Report-Editor':
        raise HTTPException(403, '보고서 편집 화면에서 이미지를 첨부하세요.')
    content=bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content)>2*1024*1024: raise HTTPException(413, '이미지는 2MB 이하로 첨부하세요.')
    from ..report.canvas import store_image
    def store():
        with state.storage.lock:
            try:
                state.storage.load_project(sid)
            except (FileNotFoundError, ValueError):
                raise HTTPException(404, '본인의 프로젝트를 찾을 수 없습니다.') from None
            return store_image(state.storage.reports.path(sid).parent / 'editor-assets', bytes(content))
    try:
        return JSONResponse(await run_in_threadpool(store),headers=NO_STORE)
    except FileNotFoundError:
        raise HTTPException(404,'본인의 프로젝트를 찾을 수 없습니다.') from None
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc


@router.get('/api/jobs/{jid}/file')
def job_file(jid: str):
    state = current.get()
    value = read_job(state, jid)
    if value['status'] != 'completed' or 'filename' not in value:
        raise HTTPException(409, '보고서가 아직 완성되지 않았습니다.')
    inline = value['kind'] == 'preview'
    return FileResponse(job_path(state, jid) / value['filename'], headers=NO_STORE,
                        filename=None if inline else 'XRD-' + value['filename'])


@router.post('/api/jobs/{jid}/send')
def send_report(jid: str, payload: PreviewReportSendRequest, request: Request):
    state = current.get()
    payload = authenticated_transfer_payload(request, payload, 'XRD')
    value = read_job(state, jid)
    if value['status'] != 'completed' or value['kind'] != 'zip':
        raise HTTPException(409, '완료된 전달 ZIP만 전송할 수 있습니다.')
    expected = dict(requestNumber=payload.request_number, experimentCode=payload.experiment_code,
                    equipmentCode=payload.equipment_code, operatorId=payload.operator_id)
    if value.get('transfer') != expected:
        raise HTTPException(409, '전송 정보가 생성 시점과 다릅니다. SSO 인증 후 정보 확인·ZIP 재생성을 해주세요.')
    project_code = payload.experiment_code.upper()
    from ..experiment_routing import resolve_analysis_type
    if resolve_analysis_type(experiment_code=project_code, equipment_code=payload.equipment_code,
                             configured_map=state.settings.analysis_type_map) != 'XRD':
        raise HTTPException(422, 'XRD 의뢰의 실험코드·장비코드를 확인하세요.')
    job = SimpleNamespace(job_id=jid, status='completed', package_path=job_path(state, jid) / value['filename'])
    try:
        return send_preview_report_package(settings=state.settings, database=request.app.state.database,
                                            job=job, payload=payload)
    except ReportQueueError as exc:
        raise ApiException(503, exc.code, exc.message, retryable=exc.retryable) from exc


@router.get('/api/platform')
def platform_info():
    context = current.get().context
    identity = context.sso_identity or {} if context else {}
    return dict(operatorId=(identity.get('employee_id') or identity.get('subject') or context.login_id) if context else '',
                llmModel=current.get().settings.llm_model)


@router.get('/vendor/plotly.min.js')
def plotly_bundle():
    from plotly.offline import get_plotlyjs
    return Response(get_plotlyjs(), media_type='application/javascript')


@router.get('/bootstrap.js')
def bootstrap():
    return Response('window.XRD_API_BASE=' + json.dumps(BASE) + ';', media_type='application/javascript', headers=NO_STORE)


@router.get('/ppt-tools/{name}', include_in_schema=False)
def ppt_tools(name: str):
    shared={'report_editor_icons.js','report_page_sidebar.js','report_cell_editor.js','report_canvas_editor.js','report_ppt_tools.css'}
    if name=='report_ppt_tools.legacy.css': name='report_ppt_tools.css'
    if name not in shared: raise HTTPException(404, '편집 도구를 찾을 수 없습니다.')
    return FileResponse(Path(__file__).parents[1]/name, headers=NO_STORE)


@router.get('/{asset_path:path}', include_in_schema=False)
def web_asset(asset_path: str, request: Request):
    path = (WEB / (asset_path or 'index.html')).resolve()
    if not path.is_relative_to(WEB.resolve()) or not path.is_file():
        raise HTTPException(404, '화면 파일을 찾을 수 없습니다.')
    if path.suffix == '.html':
        content = path.read_text()
        if path.name == 'report.html':
            content=content.replace('</head>','<link rel="stylesheet" href="/ppt-tools/report_ppt_tools.css"></head>')
            scripts=''.join('<script src="/ppt-tools/'+name+'"></script>' for name in
                ('report_editor_icons.js','report_page_sidebar.js','report_cell_editor.js','report_canvas_editor.js'))
            content=content.replace('<script type="module"',scripts+'<script type="module"',1)
        content = re.sub(r'(href|src)="/', lambda m: m[1] + '="' + BASE + '/', content)
        content = content.replace('<head>', '<head><script src="' + BASE + '/bootstrap.js"></script>')
        content = content.replace('</head>', '<link rel="stylesheet" href="' + BASE + '/shared/platform.css"></head>')
        if browser_profile(request.headers.get('user-agent', '')) == 'chrome49':
            content = re.sub(r'type="module" src="([^"]+)\.mjs"', r'src="\1.legacy.js"', content)
            content = re.sub(r'href="([^"]+)\.css"', r'href="\1.legacy.css"', content)
            runtime = ASSET_PREFIX + manifest()['runtime']
            content = content.replace('<head>', '<head><script src="' + runtime + '"></script>')
        return HTMLResponse(content, headers=NO_STORE)
    media = 'application/javascript' if path.suffix in ('.js', '.mjs') else None
    return FileResponse(path, media_type=media, headers=NO_STORE)

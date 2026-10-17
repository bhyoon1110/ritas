"""Project routes: example, upload, original files, project list/detail, offline viewer export."""

import json

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ..core.project import safe_filename
from ..core.errors import RevisionConflict
from ..render.viewer_export import export_html
from ..render.viewer_state import prepare_state, prepare_views
from ..storage import StorageBackend

MAX_FILES = 100
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 150 * 1024 * 1024
MAX_VIEWER_REQUEST_BYTES = 1024 * 1024


def build_project_router(storage: StorageBackend) -> APIRouter:
    router = APIRouter()

    @router.get('/api/example')
    def get_example():
        try:
            return storage.load_project('example')
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post('/api/upload')
    async def upload(files: list[UploadFile] = File(...), mappings: str = Form('{}')):
        if len(files) > MAX_FILES:
            raise HTTPException(422, '한 번에 최대 100개 파일을 등록할 수 있습니다.')
        try:
            mapping = json.loads(mappings)
            if not isinstance(mapping, dict):
                raise ValueError('파일 연결 정보 형식이 잘못되었습니다.')
            inputs = []
            total = 0
            for f in files:
                content = await f.read(MAX_FILE_BYTES + 1)
                total += len(content)
                if len(content) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                    raise ValueError('파일당 50MB, 전체 150MB 이하로 등록하세요.')
                inputs.append((safe_filename(f.filename or 'unnamed'), content))
            return storage.files.create(inputs, mapping)
        except (ValueError, UnicodeError, TypeError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                422, '자료를 읽거나 저장하지 못했습니다. 파일 형식과 폴더 권한을 확인하세요.'
            ) from exc

    @router.get('/api/files/{storage_id}/{file_id}')
    def original_file(storage_id: str, file_id: str):
        try:
            found = storage.files.source_file(storage_id, file_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if found is None:
            raise HTTPException(404, '원본 파일을 찾지 못했습니다.')
        path, filename = found
        return FileResponse(path, filename=filename, content_disposition_type='inline')

    @router.get('/api/projects')
    def list_projects(archived: bool = False, limit: int | None = Query(None, ge=1, le=50), current: str = ''):
        items = storage.files.list(archived=archived)
        if limit:
            selected = next((p for p in items if p['storage_id'] == current), None)
            items = items[:limit]
            if selected and selected not in items:
                items.append(selected)
        return JSONResponse(items, headers={'Cache-Control': 'private, no-store'})

    @router.get('/api/project-catalog')
    def project_catalog(archived: bool = False, q: str = Query('', max_length=100),
                        sort: str = 'newest', page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=50)):
        if sort not in ('newest', 'oldest', 'name', 'size'):
            raise HTTPException(422, '지원하지 않는 정렬입니다.')
        items = storage.files.list(archived=archived)
        total_in_tab = len(items)
        query = q.strip().casefold()
        items = [p for p in items if query in p['label'].casefold()]
        def key(p):
            if sort == 'name': return (p['label'].casefold(), p['storage_id'])
            if sort == 'size': return (p.get('total_bytes', 0), p['storage_id'])
            return (p.get('archived_at' if archived else 'created_at') or '', p['storage_id'])
        items.sort(key=key, reverse=sort in ('newest', 'size'))
        total = len(items)
        pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, pages)
        return JSONResponse(dict(items=items[(page-1)*per_page:page*per_page], total=total,
                                 total_in_tab=total_in_tab, page=page, pages=pages, per_page=per_page),
                            headers={'Cache-Control': 'private, no-store'})

    @router.post('/api/projects/manage-batch')
    def manage_batch(payload: dict):
        action, items = payload.get('action'), payload.get('items')
        if action not in ('archive', 'restore') or not isinstance(items, list) or not 1 <= len(items) <= 50:
            raise HTTPException(422, '선택 작업은 한 페이지의 1~50개 프로젝트만 가능합니다.')
        try:
            with storage.lock:
                ids = [p['storage_id'] for p in items]
                if len(set(ids)) != len(ids) or 'example' in ids:
                    raise ValueError('중복 프로젝트 또는 예제는 선택할 수 없습니다.')
                # Validate the full selection before changing any metadata.
                known = {p['storage_id']: p for p in storage.files.list(archived=action == 'restore')}
                for item in items:
                    old = known.get(item['storage_id'])
                    revision = item.get('expected_revision')
                    if not old or type(revision) is not int or revision != old['managed_revision']:
                        raise RevisionConflict('선택한 프로젝트가 변경되었습니다. 목록을 새로고침하세요.')
                results = [storage.files.manage(p['storage_id'], p['expected_revision'], archived=action == 'archive') for p in items]
                return dict(items=results, count=len(results))
        except RevisionConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, '선택한 프로젝트 정보를 확인하세요.') from exc

    def manage(storage_id, payload, **changes):
        try:
            value = storage.files.manage(storage_id, payload.get('expected_revision'), **changes)
            return JSONResponse(value, headers={'Cache-Control': 'private, no-store'})
        except RevisionConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.patch('/api/projects/{storage_id}')
    def rename_project(storage_id: str, payload: dict):
        if not isinstance(payload.get('label'), str):
            raise HTTPException(422, '프로젝트 이름이 필요합니다.')
        return manage(storage_id, payload, label=payload['label'])

    @router.delete('/api/projects/{storage_id}')
    def archive_project(storage_id: str, payload: dict):
        return manage(storage_id, payload, archived=True)

    @router.post('/api/projects/{storage_id}/restore')
    def restore_project(storage_id: str, payload: dict):
        return manage(storage_id, payload, archived=False)

    @router.get('/api/projects/{storage_id}')
    def get_project(storage_id: str):
        try:
            return storage.load_project(storage_id)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.post('/api/export/html')
    async def download_html(request: Request):
        raw = await request.body()
        if len(raw) > MAX_VIEWER_REQUEST_BYTES:
            raise HTTPException(422, '뷰어 설정 요청은 1MB 이하로 보내세요.')
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict) or not isinstance(payload.get('project'), dict):
                raise ValueError('프로젝트 식별 정보가 필요합니다.')
            identity = payload['project']
            project = storage.load_project(identity['storage_id'])
            revision = identity.get('analysis_revision', 0)
            if (
                identity.get('id') != project['id']
                or type(revision) is not int
                or revision != project.get('analysis_revision', 0)
            ):
                raise HTTPException(
                    409, '분석 검토값이 변경되었습니다. 뷰어를 다시 열어 확인한 뒤 저장하세요.'
                )
            content = await run_in_threadpool(export_html,
                project,
                prepare_state(project, payload.get('state')),
                prepare_views(project, payload.get('views')),
            )
        except (ValueError, TypeError, KeyError, FileNotFoundError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return Response(
            content,
            media_type='text/html',
            headers={'Content-Disposition': 'attachment; filename="XRD-Multi-Profile.html"'},
        )

    return router

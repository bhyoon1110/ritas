"""Project routes: example, upload, original files, project list/detail, offline viewer export."""

import json

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

from ..core.project import safe_filename
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
    def list_projects():
        return storage.files.list()

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

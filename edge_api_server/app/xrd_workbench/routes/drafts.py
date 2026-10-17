"""Private recovery drafts, separate from saved report revisions and delivery jobs."""
from copy import deepcopy
import json
import re
import time
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from ..core.report import validate_document
from ..storage import _write_json_atomic


def build_draft_router(get_state):
    router = APIRouter()

    def context(sid):
        state = get_state()
        if not re.fullmatch('[0-9a-f]{32}', sid):
            raise HTTPException(404, '프로젝트를 찾을 수 없습니다.')
        try:
            project = state.storage.load_project(sid)
        except (ValueError, FileNotFoundError):
            raise HTTPException(404, '본인의 프로젝트를 찾을 수 없습니다.')
        return state, project, state.home / 'drafts' / (sid + '.json')

    def load(path):
        return json.loads(path.read_text()) if path.is_file() else dict(revision=0, document=None)

    def response(value):
        return JSONResponse(value, headers={'Cache-Control': 'private, no-store'})

    @router.get('/api/reports/{sid}/draft')
    def get_draft(sid: str):
        state, _, path = context(sid)
        with state.storage.lock:
            return response(load(path))

    @router.put('/api/reports/{sid}/draft')
    def put_draft(sid: str, payload: dict):
        state, _, path = context(sid)
        with state.storage.lock:
            project = state.storage.load_project(sid)
            old = load(path)
            if type(payload.get('expected_revision')) is not int or payload['expected_revision'] != old['revision']:
                raise HTTPException(409, '다른 창에서 초안이 변경되었습니다. 새로 열어 복구 초안을 확인하세요.')
            try:
                # Empty title/name while typing is recoverable, not valid for final export.
                clean = []
                for key in ('document', 'baseline'):
                    value = deepcopy(payload[key])
                    blanks = []
                    if value['metadata']['title'] == '':
                        value['metadata']['title'] = '임시 제목'; blanks.append(value['metadata'])
                    blank_samples = []
                    for entry in value['samples']:
                        if entry['display_name'] == '':
                            entry['display_name'] = '임시 시료명'; blank_samples.append(entry['sample_id'])
                    value = validate_document(project, value)
                    if blanks:
                        value['metadata']['title'] = ''
                    for entry in value['samples']:
                        if entry['sample_id'] in blank_samples:
                            entry['display_name'] = ''
                    clean.append(value)
                if clean[0]['revision'] != clean[1]['revision']:
                    raise ValueError('초안의 기준 보고서 버전이 다릅니다.')
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                raise HTTPException(422, '초안 임시저장 실패: ' + str(exc)) from exc
            value = dict(revision=old['revision'] + 1, updated_at=time.time(),
                         document=clean[0], baseline=clean[1])
            _write_json_atomic(path, value)
            return response(dict(revision=value['revision'], updated_at=value['updated_at']))

    @router.post('/api/reports/{sid}/draft/clear')
    def clear_draft(sid: str, payload: dict):
        state, _, path = context(sid)
        with state.storage.lock:
            old = load(path)
            if type(payload.get('expected_revision')) is not int or payload['expected_revision'] != old['revision']:
                raise HTTPException(409, '다른 창의 초안은 지우지 않았습니다. 새로 열어 확인하세요.')
            value = dict(revision=old['revision'] + 1, document=None, updated_at=time.time())
            _write_json_atomic(path, value)
            return response(value)
    return router

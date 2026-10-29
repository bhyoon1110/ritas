"""Private PPT formatting templates, shared by the four authoring screens."""
import json
import re
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
import os
import shutil
import tempfile
from threading import BoundedSemaphore

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from .errors import ApiException
from .mail_signature_book import locked, write, NO_STORE
from .report.template_styles import content

router = APIRouter(prefix='/api/v1/report-ppt/templates')
MAX_TEMPLATES = 20
_IMPORT_SLOT = BoundedSemaphore(1)


def origin_check(request):
    origin = request.headers.get('origin')
    if request.headers.get('x-requested-with') != 'RIST-Report-Editor' or (origin and
            (urlsplit(origin).scheme, urlsplit(origin).netloc) != (request.url.scheme, request.url.netloc)):
        raise ApiException(403, 'PPT_TEMPLATE_ORIGIN', '같은 사이트의 보고서 편집 화면에서 템플릿을 변경하세요.')


def assets_root(path):
    return path.with_suffix('')


def external_data(path, data):
    root = assets_root(path) / data['source']
    if root.is_symlink() or not (root/'metadata.json').is_file():
        raise ApiException(410, 'PPT_TEMPLATE_SOURCE_MISSING', '양식 파일이 없습니다. PPTX를 다시 업로드하세요.')
    return json.loads((root/'metadata.json').read_text(encoding='utf-8'))


def validate_external(path, data):
    if data['version'] != 2: return
    metadata = external_data(path, data)
    if abs(data['ratio']-metadata['ratio']) > .000001: raise ValueError('원본 양식의 페이지 비율을 변경할 수 없습니다.')
    for binding in data['bindings']:
        if binding['slide'] >= len(metadata['pages']): raise ValueError('양식 페이지를 찾지 못했습니다.')
        ids = {o['id'] for o in metadata['pages'][binding['slide']]['objects']}
        if any(o['source'] not in ids for o in binding['objects']): raise ValueError('연결할 양식 개체를 찾지 못했습니다.')


def cleanup_assets(path, book):
    root = assets_root(path)
    active = {i['content'].get('source') for i in book['items']}
    if root.is_dir():
        for entry in root.iterdir():
            if re.fullmatch('[a-f0-9]{64}', entry.name) and entry.name not in active and entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)


def path_for(request):
    from .auth import require_context
    context = require_context(request)
    if context.status != 'ACTIVE':
        raise ApiException(403, 'PPT_TEMPLATE_FORBIDDEN', '활성 회원만 PPT 템플릿을 관리할 수 있습니다.')
    return Path(request.app.state.settings.storage_root) / 'user-ppt-templates' / (sha256(context.user_id.encode()).hexdigest() + '.json')


def load(path):
    if not path.is_file():
        return {'revision': 0, 'items': []}
    try:
        book = json.loads(path.read_text(encoding='utf-8'))
        if set(book) != {'revision', 'items'} or type(book['revision']) is not int or book['revision'] < 0 or not isinstance(book['items'], list) or len(book['items']) > MAX_TEMPLATES:
            raise ValueError()
        ids, names = set(), set()
        for item in book['items']:
            if not isinstance(item, dict) or set(item) != {'id', 'content'} or not isinstance(item['id'], str) or not re.fullmatch('[a-f0-9]{32}', item['id']):
                raise ValueError()
            data = content(item['content'])
            identity = (data['experiment'], data['name'].casefold())
            if item['id'] in ids or identity in names:
                raise ValueError()
            ids.add(item['id']); names.add(identity)
        return book
    except (ValueError, TypeError, KeyError):
        raise ApiException(500, 'PPT_TEMPLATE_STORAGE_INVALID', '저장된 PPT 템플릿을 읽지 못했습니다. 관리자에게 확인을 요청하세요.')


def listing(book):
    return dict(revision=book['revision'], items=[dict(id=i['id'], name=i['content']['name'],
                experiment=i['content']['experiment']) for i in book['items']])


@router.get('')
def list_templates(request: Request):
    path = path_for(request)
    with locked(path):
        return JSONResponse(listing(load(path)), headers=NO_STORE)


@router.get('/{template_id}')
def read_template(request: Request, template_id: str):
    path = path_for(request)
    with locked(path):
        book = load(path)
        item = next((i for i in book['items'] if i['id'] == template_id), None)
        if item is None:
            raise ApiException(404, 'PPT_TEMPLATE_NOT_FOUND', '템플릿을 찾지 못했습니다. 목록을 새로고침하세요.')
        result = dict(item, revision=book['revision'])
        if item['content']['version'] == 2: result['sourceDocument'] = external_data(path, item['content'])
        return JSONResponse(result, headers=NO_STORE)


@router.get('/{template_id}/source')
@router.get('/{template_id}/slides/{slide}/{kind}')
def source_file(request: Request, template_id: str, slide: int = 0, kind: str = 'source'):
    path = path_for(request)
    with locked(path):
        item = next((i for i in load(path)['items'] if i['id'] == template_id and i['content']['version'] == 2), None)
        if item is None: raise ApiException(404, 'PPT_TEMPLATE_NOT_FOUND', 'PPTX 양식을 찾지 못했습니다.')
        metadata = external_data(path, item['content'])
        if kind not in ('source','preview','background') or not 0 <= slide < len(metadata['pages']):
            raise ApiException(404, 'PPT_TEMPLATE_PAGE_NOT_FOUND', '양식 페이지를 찾지 못했습니다.')
        root = assets_root(path)/item['content']['source']
        file = root/('source.pptx' if kind == 'source' else f'{kind}-{slide}.png')
        if not file.is_file() or file.is_symlink(): raise ApiException(410, 'PPT_TEMPLATE_SOURCE_MISSING', '양식 파일을 다시 업로드하세요.')
        headers = dict(NO_STORE, **{'X-Content-Type-Options':'nosniff'})
        if kind == 'source': headers['Content-Disposition'] = 'attachment; filename="report-template.pptx"'
        return Response(file.read_bytes(), media_type='application/vnd.openxmlformats-officedocument.presentationml.presentation' if kind=='source' else 'image/png', headers=headers)


def import_file(path, staged, name, experiment, revision):
    from .report.ppt_template_import import prepare
    with locked(path):
        book = load(path)
        if revision != book['revision']: raise ApiException(409, 'PPT_TEMPLATE_CHANGED', '목록을 새로고침하고 다시 업로드하세요.')
        if len(book['items']) >= MAX_TEMPLATES: raise ValueError('템플릿은 최대 20개입니다.')
        if any(i['content']['experiment']==experiment and i['content']['name'].casefold()==name.casefold() for i in book['items']): raise ValueError('같은 이름의 템플릿이 있습니다.')
    digest = sha256((staged/'source.pptx').read_bytes()).hexdigest()
    metadata = prepare(staged/'source.pptx', staged)
    data = content(dict(version=2, name=name, experiment=experiment, ratio=metadata['ratio'], source=digest, bindings=[]))
    with locked(path):
        book = load(path)
        if revision != book['revision']: raise ApiException(409, 'PPT_TEMPLATE_CHANGED', '처리 중 목록이 변경되었습니다. 목록을 새로고침하고 다시 업로드하세요.')
        target = assets_root(path)/digest
        if not target.exists(): os.replace(staged, target)
        item = dict(id=uuid4().hex, content=data);book['items'].append(item);book['revision']+=1
        try: write(path, book)
        except Exception:
            cleanup_assets(path, load(path)); raise
        cleanup_assets(path, book)
        return dict(listing(book), selectedId=item['id'])


@router.post('/import')
async def upload_template(request: Request, name: str, experiment: str, revision: int):
    from .report.ppt_template_import import MAX_BYTES
    path = path_for(request);origin_check(request)
    try:
        stub = content(dict(version=2,name=name,experiment=experiment,ratio=1,source='0'*64,bindings=[]))
        name = stub['name']
        if revision < 0: raise ValueError('목록 버전을 확인하세요.')
    except (ValueError,TypeError) as exc: raise ApiException(422,'PPT_TEMPLATE_INVALID',str(exc))
    if not _IMPORT_SLOT.acquire(blocking=False):
        raise ApiException(409,'PPT_TEMPLATE_IMPORT_BUSY','다른 PPTX 양식을 처리 중입니다. 잠시 후 다시 업로드하세요.')
    staged = None
    try:
        root = assets_root(path);root.mkdir(parents=True,exist_ok=True,mode=0o700)
        staged = Path(tempfile.mkdtemp(prefix='.import-',dir=root));count=0
        with (staged/'source.pptx').open('wb') as output:
            async for chunk in request.stream():
                count+=len(chunk)
                if count > MAX_BYTES: raise ApiException(413,'PPT_TEMPLATE_TOO_LARGE','PPTX는 10MB 이하로 업로드하세요.')
                output.write(chunk)
        result = await run_in_threadpool(import_file,path,staged,name,experiment,revision)
        return JSONResponse(result,status_code=201,headers=NO_STORE)
    except ApiException: raise
    except Exception as exc:
        from .report.renderers import PptxPdfConversionError
        if isinstance(exc,PptxPdfConversionError):
            raise ApiException(422,'PPT_TEMPLATE_PREVIEW_FAILED','양식 변환에 실패했습니다. LibreOffice·한글 글꼴 설치와 PPTX 파일을 확인하세요.') from exc
        if isinstance(exc,(ValueError,TypeError,KeyError)):
            raise ApiException(422,'PPT_TEMPLATE_INVALID',str(exc)) from exc
        raise
    finally:
        if staged is not None and staged.exists(): shutil.rmtree(staged)
        _IMPORT_SLOT.release()


def mutate(path, method, value, template_id):
    with locked(path):
        book = load(path)
        if value['revision'] != book['revision']:
            raise ApiException(409, 'PPT_TEMPLATE_CHANGED', '다른 창에서 템플릿이 변경되었습니다. 입력은 유지됩니다. 목록을 새로고침하고 다시 저장하세요.')
        item = next((i for i in book['items'] if i['id'] == template_id), None)
        if method != 'POST' and item is None:
            raise ApiException(404, 'PPT_TEMPLATE_NOT_FOUND', '템플릿을 찾지 못했습니다.')
        if method != 'DELETE': validate_external(path, value['content'])
        if method != 'DELETE' and any(i['id'] != template_id and i['content']['experiment'] == value['content']['experiment'] and i['content']['name'].casefold() == value['content']['name'].casefold() for i in book['items']):
            raise ApiException(409, 'PPT_TEMPLATE_DUPLICATE', '같은 이름의 템플릿이 있습니다.')
        if method == 'POST':
            if len(book['items']) >= MAX_TEMPLATES:
                raise ApiException(422, 'PPT_TEMPLATE_LIMIT', '템플릿은 최대 20개까지 저장할 수 있습니다.')
            item = dict(id=uuid4().hex, content=value['content']); book['items'].append(item)
        elif method == 'PUT':
            item['content'] = value['content']
        else:
            book['items'].remove(item)
        book['revision'] += 1; write(path, book)
        cleanup_assets(path, book)
        return dict(listing(book), selectedId=item['id'] if method != 'DELETE' else None)


@router.post('')
@router.put('/{template_id}')
@router.delete('/{template_id}')
async def change_template(request: Request):
    path = path_for(request)
    origin_check(request)
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 2 * 1024 * 1024:
            raise ApiException(413, 'PPT_TEMPLATE_TOO_LARGE', '템플릿 입력 용량을 초과했습니다.')
    try:
        value = json.loads(data)
        fields = {'revision'} if request.method == 'DELETE' else {'revision', 'content'}
        if not isinstance(value, dict) or set(value) != fields or type(value['revision']) is not int or value['revision'] < 0:
            raise ValueError('템플릿 버전·입력 형식을 확인하세요.')
        if 'content' in fields:
            value['content'] = content(value['content'])
    except (ValueError, TypeError, KeyError) as exc:
        raise ApiException(422, 'PPT_TEMPLATE_INVALID', str(exc))
    try:
        result = await run_in_threadpool(mutate, path, request.method, value, request.path_params.get('template_id'))
    except ValueError as exc:
        raise ApiException(422, 'PPT_TEMPLATE_INVALID', str(exc)) from exc
    return JSONResponse(result, headers=NO_STORE, status_code=201 if request.method == 'POST' else 200)

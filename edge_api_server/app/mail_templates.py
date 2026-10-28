"""Private reusable mail prose. Templates never store recipients or signatures."""
import json
import re
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .errors import ApiException
from .mail_signature_book import locked, write, NO_STORE

router = APIRouter(prefix='/api/v1/report-mail/templates')
MAX_TEMPLATES = 20


def path_for(request):
    from .report_mail import _preferences_path
    private = _preferences_path(request)
    return private.parent.parent / 'user-mail-templates' / private.name


def content(value):
    from .report_mail import clean_html
    if not isinstance(value, dict) or set(value) != {'name', 'experiment', 'subject', 'body', 'bodyHtml'}:
        raise ValueError('템플릿 이름·실험·제목·본문 형식을 확인하세요.')
    for key, limit in (('name', 80), ('subject', 180), ('body', 4000)):
        if not isinstance(value[key], str) or len(value[key]) > limit or '\x00' in value[key]:
            raise ValueError(f'{key}: 최대 {limit}자로 입력하세요.')
    if not value['name'].strip() or re.search(r'[\x00-\x1f\x7f]', value['name'] + value['subject']):
        raise ValueError('이름·제목은 줄바꿈 없이 입력하세요.')
    if value['experiment'] not in ('ALL', 'FTIR', 'RAMAN', 'TEM', 'XRD'):
        raise ValueError('적용 실험을 선택하세요.')
    html, text = clean_html(value['bodyHtml'])
    if len(text) > 4000:
        raise ValueError('템플릿 본문은 4,000자 이하로 입력하세요.')
    return dict(value, name=value['name'].strip(), body=text if html else value['body'], bodyHtml=html)


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
            if item['id'] in ids or data['name'].casefold() in names:
                raise ValueError()
            ids.add(item['id']); names.add(data['name'].casefold())
        return book
    except (ValueError, TypeError, KeyError):
        raise ApiException(500, 'MAIL_TEMPLATE_STORAGE_INVALID', '저장된 메일 템플릿을 읽지 못했습니다. 관리자에게 확인을 요청하세요.')


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
            raise ApiException(404, 'MAIL_TEMPLATE_NOT_FOUND', '템플릿을 찾지 못했습니다. 목록을 새로고침하세요.')
        return JSONResponse(dict(item, revision=book['revision']), headers=NO_STORE)


def mutate(path, method, value, template_id):
    with locked(path):
        book = load(path)
        if value['revision'] != book['revision']:
            raise ApiException(409, 'MAIL_TEMPLATE_CHANGED', '다른 창에서 템플릿이 변경되었습니다. 입력은 유지됩니다. 목록을 새로고침하고 다시 저장하세요.')
        item = next((i for i in book['items'] if i['id'] == template_id), None)
        if method != 'POST' and item is None:
            raise ApiException(404, 'MAIL_TEMPLATE_NOT_FOUND', '템플릿을 찾지 못했습니다.')
        if method != 'DELETE' and any(i['id'] != template_id and i['content']['name'].casefold() == value['content']['name'].casefold() for i in book['items']):
            raise ApiException(409, 'MAIL_TEMPLATE_DUPLICATE', '같은 이름의 템플릿이 있습니다.')
        if method == 'POST':
            if len(book['items']) >= MAX_TEMPLATES:
                raise ApiException(422, 'MAIL_TEMPLATE_LIMIT', '템플릿은 최대 20개까지 저장할 수 있습니다.')
            item = dict(id=uuid4().hex, content=value['content']); book['items'].append(item)
        elif method == 'PUT':
            item['content'] = value['content']
        else:
            book['items'].remove(item)
        book['revision'] += 1; write(path, book)
        return dict(listing(book), selectedId=item['id'] if method != 'DELETE' else None)


@router.post('')
@router.put('/{template_id}')
@router.delete('/{template_id}')
async def change_template(request: Request):
    from .mail_address_book import _origin
    path = path_for(request); _origin(request)
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 2 * 1024 * 1024:
            raise ApiException(413, 'MAIL_TEMPLATE_TOO_LARGE', '템플릿 입력 용량을 초과했습니다.')
    try:
        value = json.loads(data)
        fields = {'revision'} if request.method == 'DELETE' else {'revision', 'content'}
        if not isinstance(value, dict) or set(value) != fields or type(value['revision']) is not int or value['revision'] < 0:
            raise ValueError('템플릿 버전·입력 형식을 확인하세요.')
        if 'content' in fields:
            value['content'] = content(value['content'])
    except (ValueError, TypeError, KeyError) as exc:
        raise ApiException(422, 'MAIL_TEMPLATE_INVALID', str(exc))
    result = await run_in_threadpool(mutate, path, request.method, value, request.path_params.get('template_id'))
    return JSONResponse(result, headers=NO_STORE, status_code=201 if request.method == 'POST' else 200)

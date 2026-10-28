"""Private named signatures, with atomic migration of the previous default signature."""
from contextlib import contextmanager
import fcntl
import json
import os
import re
import tempfile
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .errors import ApiException

router = APIRouter(prefix='/api/v1/report-mail/signatures')
MAX_SIGNATURES = 20
LEGACY_ID = '0' * 32
NO_STORE = {'Cache-Control': 'private, no-store'}


def path_for(request):
    from .report_mail import _preferences_path
    return _preferences_path(request)


def validate_content(value):
    from .report_mail import _signature_preferences
    return _signature_preferences(value)


def origin(request):
    from .mail_address_book import _origin
    _origin(request)


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path.with_suffix('.lock'), os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, 'a+b') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def write(path, value):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.signature-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 80 or re.search(r'[\x00-\x1f\x7f]', value):
        raise ValueError('서명 이름은 1~80자로 입력하세요.')
    return value.strip()


def load(path):
    empty = dict(revision=0, defaultId=None, items=[])
    if not path.is_file():
        return empty
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(value, dict) and 'signature' in value:
            content = validate_content(value)
            if content['signature'].strip() or content.get('signatureImage'):
                empty.update(defaultId=LEGACY_ID, items=[dict(id=LEGACY_ID, name='기본 서명', content=content)])
            return empty
        if not isinstance(value, dict) or set(value) != {'revision', 'defaultId', 'items'}:
            raise ValueError()
        if type(value['revision']) is not int or value['revision'] < 0 or not isinstance(value['items'], list) or len(value['items']) > MAX_SIGNATURES:
            raise ValueError()
        ids, names = set(), set()
        for item in value['items']:
            if not isinstance(item, dict) or set(item) != {'id', 'name', 'content'} or not re.fullmatch('[a-f0-9]{32}', item['id']):
                raise ValueError()
            label = name(item['name']).casefold()
            if item['id'] in ids or label in names:
                raise ValueError()
            validate_content(item['content']); ids.add(item['id']); names.add(label)
        if value['defaultId'] is not None and value['defaultId'] not in ids:
            raise ValueError()
        return value
    except (ValueError, TypeError, KeyError):
        raise ApiException(500, 'MAIL_SIGNATURE_STORAGE_INVALID', '저장된 서명 목록을 읽지 못했습니다. 관리자에게 확인을 요청하세요.')


def listing(book):
    # Images are loaded only when a signature is selected, not for the entire list.
    return dict(revision=book['revision'], defaultId=book['defaultId'], items=[
        dict(id=i['id'], name=i['name'], hasImage=bool(i['content'].get('signatureImage')),
             isDefault=i['id'] == book['defaultId']) for i in book['items']])


def get_default(path):
    with locked(path):
        book = load(path)
        return next((i['content'] for i in book['items'] if i['id'] == book['defaultId']), {'signature': ''})


def put_default(path, content):
    with locked(path):
        book = load(path)
        item = next((i for i in book['items'] if i['id'] == book['defaultId']), None)
        if item is None:
            if len(book['items']) >= MAX_SIGNATURES:
                raise ApiException(422, 'MAIL_SIGNATURE_LIMIT', '서명은 최대 20개입니다. 기존 서명을 기본값으로 지정하세요.')
            names = {i['name'].casefold() for i in book['items']}
            label = '기본 서명'; number = 2
            while label.casefold() in names:
                label = f'기본 서명 {number}'; number += 1
            item = dict(id=uuid4().hex, name=label, content=content)
            book['items'].append(item); book['defaultId'] = item['id']
        else:
            item['content'] = content
        book['revision'] += 1; write(path, book)
        return content


async def body(request, fields):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 400 * 1024:
            raise ApiException(413, 'MAIL_SIGNATURE_TOO_LARGE', '서명 입력 용량을 초과했습니다.')
    try:
        value = json.loads(data)
        if not isinstance(value, dict) or set(value) != fields or type(value['revision']) is not int or value['revision'] < 0:
            raise ValueError()
        if 'name' in fields:
            value['name'] = name(value['name']); value['content'] = validate_content(value['content'])
        return value
    except (ValueError, TypeError, KeyError) as exc:
        raise ApiException(422, 'MAIL_SIGNATURE_INVALID', str(exc) or '서명 입력 형식을 확인하세요.')


def mutate(path, method, value, signature_id=None):
    with locked(path):
        book = load(path)
        if value['revision'] != book['revision']:
            raise ApiException(409, 'MAIL_SIGNATURE_CHANGED', '다른 창에서 서명이 변경되었습니다. 목록을 새로고침 후 다시 저장하세요. 현재 입력은 유지됩니다.')
        item = next((i for i in book['items'] if i['id'] == signature_id), None)
        if method != 'POST' and item is None:
            raise ApiException(404, 'MAIL_SIGNATURE_NOT_FOUND', '서명을 찾지 못했습니다. 목록을 새로고침하세요.')
        if 'name' in value and any(i['id'] != signature_id and i['name'].casefold() == value['name'].casefold() for i in book['items']):
            raise ApiException(409, 'MAIL_SIGNATURE_DUPLICATE', '같은 이름의 서명이 있습니다. 다른 이름을 입력하세요.')
        if method == 'POST':
            if len(book['items']) >= MAX_SIGNATURES:
                raise ApiException(422, 'MAIL_SIGNATURE_LIMIT', '서명은 최대 20개까지 저장할 수 있습니다.')
            item = dict(id=uuid4().hex, name=value['name'], content=value['content'])
            if not book['items']:
                book['defaultId'] = item['id']
            book['items'].append(item)
        elif method == 'PUT':
            item.update(name=value['name'], content=value['content'])
        elif method == 'DEFAULT':
            book['defaultId'] = item['id']
        elif method == 'DELETE':
            book['items'].remove(item)
            if book['defaultId'] == signature_id:
                book['defaultId'] = None
        book['revision'] += 1; write(path, book)
        return listing(book)


@router.get('')
def list_signatures(request: Request):
    path = path_for(request)
    with locked(path):
        return JSONResponse(listing(load(path)), headers=NO_STORE)


@router.get('/{signature_id}')
def read_signature(request: Request, signature_id: str):
    path = path_for(request)
    with locked(path):
        book = load(path)
        item = next((i for i in book['items'] if i['id'] == signature_id), None)
        if item is None:
            raise ApiException(404, 'MAIL_SIGNATURE_NOT_FOUND', '서명을 찾지 못했습니다. 목록을 새로고침하세요.')
        return JSONResponse(dict(item, revision=book['revision']), headers=NO_STORE)


@router.post('')
@router.put('/{signature_id}')
@router.delete('/{signature_id}')
@router.put('/{signature_id}/default')
async def change_signature(request: Request, signature_id: str | None = None):
    path = path_for(request); origin(request)
    signature_id = request.path_params.get('signature_id')
    method = 'DEFAULT' if request.url.path.endswith('/default') else request.method
    value = await body(request, {'revision', 'name', 'content'} if method in {'POST', 'PUT'} else {'revision'})
    result = await run_in_threadpool(mutate, path, method, value, signature_id)
    return JSONResponse(result, headers=NO_STORE, status_code=201 if method == 'POST' else 200)

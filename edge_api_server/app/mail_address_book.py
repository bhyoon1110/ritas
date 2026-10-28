"""Private and administrator-managed RIST address books with server-side visibility."""
from contextlib import contextmanager
from hashlib import sha256
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .errors import ApiException

router = APIRouter(prefix='/api/v1/report-mail/contacts')
shared_router = APIRouter(prefix='/api/v1/report-mail/shared-contacts')
MAX_CONTACTS = 500
NO_STORE = {'Cache-Control': 'private, no-store'}


def _context(request):
    from .auth import require_context
    context = require_context(request)
    if context.status != 'ACTIVE' or not (context.is_admin or context.projects):
        raise ApiException(403, 'MAIL_CONTACTS_FORBIDDEN', '실험 이용 권한이 있는 회원만 주소록을 사용할 수 있습니다.')
    return context


def _path(request):
    context = _context(request)
    root = Path(request.app.state.settings.storage_root) / 'user-mail-address-books'
    return root / (sha256(context.user_id.encode()).hexdigest() + '.json')


def _origin(request):
    from urllib.parse import urlsplit
    origin = request.headers.get('origin')
    if request.headers.get('x-requested-with') != 'RIST-Mail-Editor' or (origin and
            (urlsplit(origin).scheme, urlsplit(origin).netloc) != (request.url.scheme, request.url.netloc)):
        raise ApiException(403, 'MAIL_CONTACTS_ORIGIN', '같은 사이트에서 주소록을 변경하세요.')


def _visibility(value):
    from .auth import PROJECT_CODES
    if not isinstance(value, dict) or set(value) != {'mode', 'projects'}:
        raise ValueError('공개 범위를 선택하세요.')
    mode, projects = value['mode'], value['projects']
    if not isinstance(mode, str) or mode not in {'all', 'projects', 'admins'} or not isinstance(projects, list):
        raise ValueError('공개 범위를 확인하세요.')
    if len(projects) > len(PROJECT_CODES) or any(not isinstance(p, str) or p not in PROJECT_CODES for p in projects):
        raise ValueError('공개할 실험을 확인하세요.')
    if (mode == 'projects' and not projects) or (mode != 'projects' and projects):
        raise ValueError('선택한 공개 범위에 맞는 실험을 지정하세요.')
    return {'mode': mode, 'projects': sorted(set(projects))}


def _contact(value, shared=False):
    from .report_mail import addresses, COPY_DOMAIN
    fields = {'name', 'department', 'email'} | ({'visibility'} if shared else set())
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('이름·부서·이메일을 확인하세요.')
    result = {}
    for key, limit in [('name', 100), ('department', 120), ('email', 254)]:
        text = value[key]
        if not isinstance(text, str) or len(text) > limit or re.search(r'[\x00-\x1f\x7f]', text):
            raise ValueError('주소록 입력 길이·형식을 확인하세요.')
        result[key] = text.strip()
    if not result['name']:
        raise ValueError('이름을 입력하세요.')
    emails = addresses(result['email'])
    if len(emails) != 1 or emails[0].rsplit('@', 1)[1].lower() != COPY_DOMAIN:
        raise ValueError('주소록에는 @rist.re.kr 이메일 한 개만 등록할 수 있습니다.')
    result['email'] = emails[0].split('@', 1)[0] + '@' + COPY_DOMAIN
    if shared:
        result['visibility'] = _visibility(value['visibility'])
    return result


async def _body(request, fields):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 8192:
            raise ApiException(413, 'MAIL_CONTACTS_TOO_LARGE', '주소록 입력 크기를 확인하세요.')
    try:
        value = json.loads(data)
        if not isinstance(value, dict) or set(value) != fields or type(value['revision']) is not int or value['revision'] < 0:
            raise ValueError()
    except (ValueError, TypeError):
        raise ApiException(422, 'MAIL_CONTACTS_INVALID', '주소록 입력 형식을 확인하세요.')
    return value


@contextmanager
def _locked(path):
    # Stable lock inode + atomic replace: protect threads and multiple API workers.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path.with_suffix('.lock'), os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, 'a+b') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _load(path, shared=False):
    if not path.exists():
        return {'revision': 0, 'contacts': []}
    try:
        book = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(book, dict) or type(book.get('revision')) is not int or book['revision'] < 0:
            raise ValueError()
        contacts = book['contacts']
        if not isinstance(contacts, list) or len(contacts) > MAX_CONTACTS:
            raise ValueError()
        ids, emails = set(), set()
        for item in contacts:
            if not isinstance(item, dict) or not re.fullmatch('[a-f0-9]{32}', item.get('id', '')):
                raise ValueError()
            clean = _contact({k: v for k, v in item.items() if k != 'id'}, shared)
            if item['id'] in ids or clean['email'].lower() in emails:
                raise ValueError()
            ids.add(item['id']); emails.add(clean['email'].lower())
        return book
    except (ValueError, KeyError, TypeError):
        # Never replace a damaged address book with an empty one.
        raise ApiException(500, 'MAIL_CONTACTS_STORAGE_INVALID', '저장된 주소록을 읽지 못했습니다. 관리자에게 확인을 요청하세요.')


def _write(path, book):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.contacts-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(book, stream, ensure_ascii=False)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _mutate(path, revision, method, contact_id=None, item=None, shared=False):
    with _locked(path):
        book = _load(path, shared)
        if revision != book['revision']:
            raise ApiException(409, 'MAIL_CONTACTS_CHANGED', '다른 창에서 주소록이 변경되었습니다. 새로고침 후 다시 저장하세요. 입력 내용은 유지됩니다.')
        contacts = book['contacts']
        index = next((i for i, v in enumerate(contacts) if v['id'] == contact_id), None)
        if method != 'POST' and index is None:
            raise ApiException(404, 'MAIL_CONTACT_NOT_FOUND', '주소록에서 해당 항목을 찾을 수 없습니다. 새로고침하세요.')
        if item and any(v['email'].lower() == item['email'].lower() and v['id'] != contact_id for v in contacts):
            raise ApiException(409, 'MAIL_CONTACT_DUPLICATE', '이미 등록된 이메일입니다. 기존 항목을 수정하세요.')
        if method == 'POST':
            if len(contacts) >= MAX_CONTACTS:
                raise ApiException(422, 'MAIL_CONTACTS_LIMIT', '주소록은 최대 500명까지 등록할 수 있습니다.')
            contacts.append(dict(item, id=uuid4().hex))
        elif method == 'PUT':
            contacts[index] = dict(item, id=contact_id)
        else:
            contacts.pop(index)
        contacts.sort(key=lambda v: (v['name'].casefold(), v['email'].lower()))
        book['revision'] += 1
        _write(path, book)
        return book


@router.get('')
def list_contacts(request: Request):
    path = _path(request)
    with _locked(path):
        return JSONResponse(_load(path), headers=NO_STORE)


@router.post('')
async def change_contact(request: Request):
    path = _path(request)
    _origin(request)
    contact_id = request.path_params.get('contact_id')
    if contact_id is not None and not re.fullmatch('[a-f0-9]{32}', contact_id):
        raise ApiException(404, 'MAIL_CONTACT_NOT_FOUND', '주소록 항목을 확인하세요.')
    value = await _body(request, {'revision'} if request.method == 'DELETE' else {'revision', 'contact'})
    item = None
    if request.method != 'DELETE':
        try:
            item = _contact(value['contact'])
        except ValueError as exc:
            raise ApiException(422, 'MAIL_CONTACT_INVALID', str(exc)) from exc
    book = await run_in_threadpool(_mutate, path, value['revision'], request.method, contact_id, item)
    return JSONResponse(book, headers=NO_STORE, status_code=201 if request.method == 'POST' else 200)


@router.put('/{contact_id}')
@router.delete('/{contact_id}')
async def update_contact(request: Request, contact_id: str):
    return await change_contact(request)


def _shared_path(request):
    return Path(request.app.state.settings.storage_root) / 'shared-mail-address-book' / 'contacts.json'


def _shared_view(book, context):
    if context.is_admin:
        return dict(book, canManage=True)
    contacts = []
    for contact in book['contacts']:
        scope = contact['visibility']
        if scope['mode'] == 'all' or (scope['mode'] == 'projects' and context.projects.intersection(scope['projects'])):
            contacts.append({k: contact[k] for k in ('id', 'name', 'department', 'email')})
    # Do not expose hidden rows, their ACLs, total counts or global modification revision.
    return {'revision': 0, 'contacts': contacts, 'canManage': False}


@shared_router.get('')
def list_shared_contacts(request: Request):
    context = _context(request)
    path = _shared_path(request)
    with _locked(path):
        return JSONResponse(_shared_view(_load(path, True), context), headers=NO_STORE)


@shared_router.post('')
async def change_shared_contact(request: Request):
    context = _context(request)
    if not context.is_admin:
        raise ApiException(403, 'MAIL_CONTACTS_ADMIN_REQUIRED', '공용 주소록은 관리자만 변경할 수 있습니다.')
    _origin(request)
    contact_id = request.path_params.get('contact_id')
    if contact_id is not None and not re.fullmatch('[a-f0-9]{32}', contact_id):
        raise ApiException(404, 'MAIL_CONTACT_NOT_FOUND', '주소록 항목을 확인하세요.')
    value = await _body(request, {'revision'} if request.method == 'DELETE' else {'revision', 'contact'})
    item = None
    if request.method != 'DELETE':
        try:
            item = _contact(value['contact'], True)
        except ValueError as exc:
            raise ApiException(422, 'MAIL_CONTACT_INVALID', str(exc)) from exc
    book = await run_in_threadpool(_mutate, _shared_path(request), value['revision'], request.method, contact_id, item, True)
    return JSONResponse(_shared_view(book, context), headers=NO_STORE, status_code=201 if request.method == 'POST' else 200)


@shared_router.put('/{contact_id}')
@shared_router.delete('/{contact_id}')
async def update_shared_contact(request: Request, contact_id: str):
    return await change_shared_contact(request)

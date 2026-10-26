"""Shared, bounded mail drafts. Delivery addresses stay out of recipient artifacts."""
from hashlib import sha256
from html import escape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .errors import ApiException

FIELDS = {"recipientName": 200, "to": 2000, "cc": 2000, "bcc": 2000,
          "bodyHtml": 32000, "signature": 2000}
TAGS = {"p", "div", "br", "strong", "b", "em", "i", "u", "ul", "ol", "li"}
BLOCKED = {"script", "style", "iframe", "object", "svg", "math", "form", "textarea"}


class _MailHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.html, self.text, self.stack, self.skip = [], [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in BLOCKED:
            self.skip += 1
        if self.skip:
            return
        if tag in TAGS:
            self.html.append('<' + tag + '>')
            if tag != 'br':
                self.stack.append(tag)
            if tag in {'p', 'div', 'li', 'br'}:
                self.text.append('\n')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag != 'br':
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.skip:
            if tag in BLOCKED:
                self.skip -= 1
            return
        if tag in self.stack:
            while self.stack:
                current = self.stack.pop()
                self.html.append('</' + current + '>')
                if current == tag:
                    break
        if tag in {'p', 'div', 'li'}:
            self.text.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.html.append(escape(data))
            self.text.append(data)


def clean_html(value):
    if not isinstance(value, str) or len(value) > 48000:
        raise ValueError('메일 서식 본문과 서명이 너무 깁니다.')
    parser = _MailHTML()
    parser.feed(value)
    parser.close()
    parser.html.extend('</' + tag + '>' for tag in reversed(parser.stack))
    return ''.join(parser.html), ''.join(parser.text).strip()


def normalize_delivery(value=None):
    value = {} if value is None else value
    if not isinstance(value, dict) or set(value) - set(FIELDS):
        raise ValueError('메일 수신 정보 형식을 확인하세요.')
    result = {}
    for key, limit in FIELDS.items():
        text = value.get(key, '')
        if not isinstance(text, str) or len(text) > limit or '\x00' in text:
            raise ValueError('메일 ' + key + ': 입력 길이·형식을 확인하세요.')
        result[key] = clean_html(text)[0] if key == 'bodyHtml' else text
    return result


def addresses(value):
    # A partial draft may be saved; only the queue boundary requires valid addresses.
    if not isinstance(value, str) or re.search(r'[\r\n\x00-\x1f\x7f]', value):
        raise ValueError('이메일 주소에 줄바꿈·제어문자를 넣을 수 없습니다.')
    items = [x.strip() for x in re.split('[,;]', value) if x.strip()]
    if len(items) > 50:
        raise ValueError('받는 사람·참조·비밀참조는 각각 50명 이하로 입력하세요.')
    result = []
    for item in items:
        if len(item) > 254 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}", item):
            raise ValueError('이메일 주소 형식을 확인하세요. 여러 주소는 쉼표 또는 세미콜론으로 구분하세요.')
        local = item.split('@', 1)[0]
        if len(local) > 64 or local.startswith('.') or local.endswith('.') or '..' in local:
            raise ValueError('이메일 주소 형식을 확인하세요.')
        if item.lower() not in [x.lower() for x in result]:
            result.append(item)
    return result


def mail_payload(subject, body, delivery=None, *, sending=False):
    d = normalize_delivery(delivery)
    if not isinstance(subject, str) or not isinstance(body, str) or len(subject) > 4000 or len(body) > 4000:
        raise ValueError('메일 제목·본문 길이를 확인하세요.')
    html, text = clean_html(d['bodyHtml'])
    # Old clients or resolved template fields must never send stale rich text.
    if not html or ' '.join(text.split()) != ' '.join(body.split()):
        html = escape(body).replace('\n', '<br>')
    if d['signature'].strip():
        body += '\n\n' + d['signature'].strip()
        html += '<br><br>' + escape(d['signature'].strip()).replace('\n', '<br>')
    result = dict(schemaVersion=1, recipientName=d['recipientName'], subject=subject,
                  bodyText=body, bodyHtml=html, **{k: d[k] for k in ('to', 'cc', 'bcc')})
    try:
        return validate_envelope(result)
    except ValueError:
        if sending:
            raise
        return result  # Preserve incomplete drafts; queueing is the validation boundary.


def validate_envelope(value):
    """Validate the trusted stored envelope at queueing, not a browser send override."""
    if not isinstance(value, dict):
        raise ValueError('메일 전송 정보 형식을 확인하세요.')
    result = dict(value)
    for field in ('recipientName', 'subject'):
        if not isinstance(result.get(field), str) or re.search(r'[\r\n\x00-\x1f\x7f]', result[field]):
            raise ValueError('수신자 이름과 메일 제목은 한 줄로 입력하세요.')
    for field in ('to', 'cc', 'bcc'):
        raw = result.get(field, '')
        if isinstance(raw, list) and not all(isinstance(item, str) for item in raw):
            raise ValueError('이메일 주소 형식을 확인하세요.')
        result[field] = addresses(','.join(raw) if isinstance(raw, list) else raw)
    if (result['cc'] or result['bcc'] or result['recipientName'].strip()) and not result['to']:
        raise ValueError('수신자 이메일 주소를 입력하세요.')
    # No To means the existing LIMS recipient lookup policy remains in charge.
    result['recipientPolicy'] = 'explicit' if result['to'] else 'lims'
    result['bodyHtml'] = clean_html(result.get('bodyHtml', ''))[0]
    return result


def write_mail_files(directory, subject, body, delivery=None):
    """Public body only: never write To/Cc/Bcc to the downloadable report package."""
    mail = mail_payload(subject, body, delivery)
    if not mail['subject'] and not mail['bodyText']:
        # These two generated sidecars belong to this writer; never keep stale drafts.
        for filename in ('email_body.md', 'email_body.html'):
            Path(directory, filename).unlink(missing_ok=True)
        return
    Path(directory, 'email_body.md').write_text('# ' + subject + '\n\n' + mail['bodyText'] + '\n', encoding='utf-8')
    Path(directory, 'email_body.html').write_text('<!doctype html><html lang="ko"><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'"><title>' + escape(subject) + '</title><body>' + mail['bodyHtml'] + '</body></html>', encoding='utf-8')


router = APIRouter()


def _preferences_path(request):
    from .auth import require_context
    context = require_context(request)
    if context.status != 'ACTIVE':
        raise ApiException(403, 'MAIL_SIGNATURE_FORBIDDEN', '활성 회원만 서명을 관리할 수 있습니다.')
    root = Path(request.app.state.settings.storage_root) / 'user-mail-preferences'
    return root / (sha256(context.user_id.encode()).hexdigest() + '.json')


@router.get('/api/v1/report-mail/signature')
def get_signature(request: Request):
    path = _preferences_path(request)
    value = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'signature': ''}
    return JSONResponse(value, headers={'Cache-Control': 'no-store'})


@router.put('/api/v1/report-mail/signature')
async def save_signature(request: Request):
    path = _preferences_path(request)
    origin = request.headers.get('origin')
    if request.headers.get('x-requested-with') != 'RIST-Mail-Editor' or (origin and
            (urlsplit(origin).scheme, urlsplit(origin).netloc) != (request.url.scheme, request.url.netloc)):
        raise ApiException(403, 'MAIL_SIGNATURE_ORIGIN', '같은 사이트에서 서명을 저장하세요.')
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > 16000:
            raise ApiException(413, 'MAIL_SIGNATURE_TOO_LARGE', '서명은 2,000자 이하로 입력하세요.')
    try:
        value = json.loads(content)
        if not isinstance(value, dict) or set(value) != {'signature'}:
            raise ValueError()
        signature = normalize_delivery(value)['signature']
    except (ValueError, TypeError):
        raise ApiException(422, 'MAIL_SIGNATURE_INVALID', '서명은 2,000자 이하의 텍스트로 입력하세요.')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Unique temporary files make parallel saves atomic; permissions remain private.
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.signature-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump({'signature': signature}, stream, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return JSONResponse({'signature': signature}, headers={'Cache-Control': 'no-store'})

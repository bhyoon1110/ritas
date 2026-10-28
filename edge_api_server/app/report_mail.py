"""Shared, bounded mail drafts. Delivery addresses stay out of recipient artifacts."""
from hashlib import sha256
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .errors import ApiException
from .mail_address_book import router as address_book_router, shared_router as shared_address_book_router
from . import mail_signature_book, mail_templates, ppt_templates
from .report_mail_media import MailImages, image_data, data_uri, MAX_HTML, MAX_TABLES, MAX_ROWS, MAX_COLS, MAX_IMAGE_BYTES

FIELDS = {"recipientName": 200, "to": 2000, "cc": 2000, "bcc": 2000,
          "bodyHtml": MAX_HTML, "signature": 2000}
SIGNATURE_FIELDS = {'signatureImage': (MAX_IMAGE_BYTES + 2) // 3 * 4 + 32, 'signatureImageWidth': 4}
COPY_DOMAIN = 'rist.re.kr'
TAGS = {"p", "div", "br", "strong", "b", "em", "i", "u", "ul", "ol", "li",
        "table", "thead", "tbody", "tfoot", "tr", "th", "td", "img"}
BLOCKED = {"script", "style", "iframe", "object", "svg", "math", "form", "textarea"}


def _formatting(tag, attrs, base=''):
    """Retain only bounded mail-layout values, never arbitrary author CSS."""
    attributes = dict(attrs)
    source = {}
    for part in (attributes.get('style') or '').lower().split(';'):
        pair = part.split(':')
        if len(pair) == 2:
            source[pair[0].strip()] = pair[1].strip()
    styles = dict(part.split(':', 1) for part in base.split(';') if part)
    extra = {}
    def value(prop, attr):
        return source.get(prop, (attributes.get(attr) or '').lower())
    align = value('text-align', 'align')
    if tag in {'p', 'div', 'li', 'td', 'th'} and align in {'left', 'center', 'right', 'justify'}:
        styles['text-align'] = extra['align'] = align
    if tag == 'table':
        width = value('width', 'width')
        if not (re.fullmatch(r'\d+(?:\.\d{1,6})?%', width) and 20 <= float(width[:-1]) <= 100):
            width = '100%'
        styles['width'] = extra['width'] = width
    if tag in {'td', 'th'}:
        vertical, width = value('vertical-align', 'valign'), value('width', 'width')
        styles['vertical-align'] = extra['valign'] = vertical if vertical in {'top', 'middle', 'bottom'} else 'top'
        if re.fullmatch(r'\d+(?:\.\d{1,6})?%', width) and 5 <= float(width[:-1]) <= 100:
            styles['width'] = extra['width'] = width
        color = value('background-color', 'bgcolor')
        rgb = re.fullmatch(r'rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)', color)
        if rgb and all(int(c) <= 255 for c in rgb.groups()):
            color = '#' + ''.join(f'{int(c):02x}' for c in rgb.groups())
        if re.fullmatch(r'#[0-9a-f]{6}', color):
            styles['background-color'] = extra['bgcolor'] = color
    height = value('height', 'height')
    if tag == 'tr' and re.fullmatch(r'\d+(?:px)?', height) and 24 <= int(height.removesuffix('px')) <= 400:
        extra['height'] = height.removesuffix('px')
        styles['height'] = extra['height'] + 'px'
    if styles:
        extra['style'] = ';'.join(key + ':' + value for key, value in styles.items())
    return ''.join(' ' + key + '="' + escape(value, quote=True) + '"' for key, value in extra.items())


class _MailHTML(HTMLParser):
    def __init__(self, images):
        super().__init__(convert_charrefs=True)
        self.html, self.text, self.stack, self.skip = [], [], [], 0
        self.images, self.tables, self.rows, self.cols = images, 0, 0, 0

    def handle_starttag(self, tag, attrs):
        if tag in BLOCKED:
            self.skip += 1
        if self.skip:
            return
        if tag in TAGS:
            extra = _formatting(tag, attrs)
            if tag == 'img':
                attributes = dict(attrs)
                source = attributes.get('src') or ''
                # Reject invalid embedded data, but drop external tracking images.
                if not source.startswith(('data:', 'cid:')):
                    return
                source, item = self.images.source(source)
                width = attributes.get('width') or str(min(item['width'], 640))
                if not width.isdigit() or not 1 <= int(width) <= 1280:
                    raise ValueError('메일 이미지 표시 폭은 1~1,280px입니다.')
                alt = (attributes.get('alt') or '메일 본문 이미지')[:200]
                extra = ' src="' + escape(source, quote=True) + '" width="' + width + '" alt="' + escape(alt, quote=True) + '" style="max-width:100%;height:auto"'
                self.text.append('\n[이미지]\n')
            elif tag == 'table':
                self.tables += 1
                if 'table' in self.stack or self.tables > MAX_TABLES:
                    raise ValueError('중첩 표는 지원하지 않으며 메일에는 표를 최대 3개 넣을 수 있습니다.')
                self.rows = self.cols = 0
                extra = ' border="1" cellpadding="6" cellspacing="0"' + _formatting(
                    tag, attrs, 'border-collapse:collapse;width:100%;table-layout:fixed')
            elif tag == 'tr':
                if 'table' not in self.stack:
                    raise ValueError('메일 표 구조를 확인하세요.')
                self.rows += 1
                self.cols = 0
                if self.rows > MAX_ROWS:
                    raise ValueError('표는 최대 20행입니다.')
            elif tag in {'td', 'th'}:
                if 'tr' not in self.stack:
                    raise ValueError('메일 표 구조를 확인하세요.')
                self.cols += 1
                if self.cols > MAX_COLS:
                    raise ValueError('표는 최대 8열입니다.')
                extra = _formatting(tag, attrs, 'border:1px solid #94a3b8;padding:6px;vertical-align:top;word-break:break-word')
            self.html.append('<' + tag + extra + '>')
            if tag not in {'br', 'img'}:
                self.stack.append(tag)
            if tag in {'p', 'div', 'li', 'br'}:
                self.text.append('\n')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in {'br', 'img'}:
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
        if tag in {'p', 'div', 'li', 'tr', 'table'}:
            self.text.append('\n')
        elif tag in {'td', 'th'}:
            self.text.append('\t')

    def handle_data(self, data):
        if not self.skip:
            self.html.append(escape(data))
            self.text.append(data)


def clean_html(value, *, images=None):
    if not isinstance(value, str) or len(value) > MAX_HTML:
        raise ValueError('메일 서식 본문과 서명이 너무 깁니다.')
    images = images if images is not None else MailImages()
    parser = _MailHTML(images)
    parser.feed(value)
    parser.close()
    parser.html.extend('</' + tag + '>' for tag in reversed(parser.stack))
    html, text = ''.join(parser.html), ''.join(parser.text).strip()
    if len(html) > MAX_HTML or len(text) > 8000:
        raise ValueError('메일 서식 본문이 너무 깁니다.')
    images.finish()
    return html, text


def expand_rich_text(value, expand):
    """Expand report variables in text only, never inside image data/attributes."""
    safe, _ = clean_html(value)
    parts = []
    class Expander(HTMLParser):
        def handle_starttag(self, tag, attrs):
            parts.append(self.get_starttag_text())
        def handle_endtag(self, tag):
            parts.append('</' + tag + '>')
        def handle_data(self, data):
            parts.append(escape(expand(data)).replace('\n', '<br>'))
    parser = Expander(convert_charrefs=True)
    parser.feed(safe)
    parser.close()
    return clean_html(''.join(parts))[0]


def normalize_delivery(value=None):
    value = {} if value is None else value
    if not isinstance(value, dict) or set(value) - (set(FIELDS) | set(SIGNATURE_FIELDS)):
        raise ValueError('메일 수신 정보 형식을 확인하세요.')
    result = {}
    for key, limit in FIELDS.items():
        text = value.get(key, '')
        if not isinstance(text, str) or len(text) > limit or '\x00' in text:
            raise ValueError('메일 ' + key + ': 입력 길이·형식을 확인하세요.')
        result[key] = clean_html(text)[0] if key == 'bodyHtml' else text
    if any(key in value for key in SIGNATURE_FIELDS):
        source, width = value.get('signatureImage', ''), value.get('signatureImageWidth', '320')
        if not isinstance(source, str) or len(source) > SIGNATURE_FIELDS['signatureImage']:
            raise ValueError('서명 이미지는 PNG/JPEG 한 장, 256KB 이하로 등록하세요.')
        if not isinstance(width, str) or not re.fullmatch(r'\d{2,4}', width) or not 40 <= int(width) <= 1280:
            raise ValueError('서명 이미지 표시 폭은 40~1,280px입니다.')
        result.update(signatureImage=data_uri(image_data(source)) if source else '', signatureImageWidth=str(int(width)))
        # The signature shares the existing mail image/count budget with the body.
        clean_html(result['bodyHtml'] + _signature_image_html(result))
    # These UI fields are display-only. Even a forged/old draft cannot redirect delivery.
    result['to'] = result['recipientName'] = ''
    return result


def _signature_image_html(value):
    source = value.get('signatureImage', '')
    if not source:
        return ''
    return '<img src="' + escape(source, quote=True) + '" width="' + value['signatureImageWidth'] + '" alt="메일 서명 이미지" style="max-width:100%;height:auto">'


def edit_text_size(value):
    """Keep the original document-text budget; raster bytes have their own budget."""
    content = dict(value)
    if content.get('deliveryEmail'):
        content['deliveryEmail'] = dict(content['deliveryEmail'])
        content['deliveryEmail']['bodyHtml'] = re.sub(
            r'data:image/(?:png|jpeg);base64,[A-Za-z0-9+/=]+', '[embedded-image]',
            content['deliveryEmail'].get('bodyHtml', ''))
        if content['deliveryEmail'].get('signatureImage'):
            content['deliveryEmail']['signatureImage'] = '[embedded-signature-image]'
    return len(json.dumps(content, ensure_ascii=False))


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
    if d.get('signatureImage'):
        body += '\n\n[서명 이미지]'
        html += '<br><br>' + _signature_image_html(d)
    result = dict(schemaVersion=2, recipientPolicy='lims', recipientName='', subject=subject,
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
    if not isinstance(result.get('subject'), str) or re.search(r'[\r\n\x00-\x1f\x7f]', result['subject']):
        raise ValueError('메일 제목은 한 줄로 입력하세요.')
    for field in ('cc', 'bcc'):
        raw = result.get(field, '')
        if isinstance(raw, list) and not all(isinstance(item, str) for item in raw):
            raise ValueError('이메일 주소 형식을 확인하세요.')
        result[field] = addresses(','.join(raw) if isinstance(raw, list) else raw)
        if any(item.rsplit('@', 1)[1].lower() != COPY_DOMAIN for item in result[field]):
            raise ValueError('참조·비밀참조는 @rist.re.kr 이메일 주소만 사용할 수 있습니다.')
    # Resolve the sole To recipient from the request in LIMS, never from the saved/browser
    # envelope. This also neutralizes explicit recipients in previously generated reports.
    result['recipientName'], result['to'], result['recipientPolicy'] = '', [], 'lims'
    images = MailImages(result.get('inlineImages'), cid=True)
    result['bodyHtml'] = clean_html(result.get('bodyHtml', ''), images=images)[0]
    result['inlineImages'] = images.finish()
    result['schemaVersion'] = 2
    return result


def write_mail_files(directory, subject, body, delivery=None):
    """Public body only: never write To/Cc/Bcc to the downloadable report package."""
    mail = mail_payload(subject, body, delivery)
    if not mail['subject'] and not mail['bodyText'] and not mail['bodyHtml']:
        # These two generated sidecars belong to this writer; never keep stale drafts.
        for filename in ('email_body.md', 'email_body.html'):
            Path(directory, filename).unlink(missing_ok=True)
        return
    Path(directory, 'email_body.md').write_text('# ' + subject + '\n\n' + mail['bodyText'] + '\n', encoding='utf-8')
    embedded = clean_html(mail['bodyHtml'], images=MailImages(mail.get('inlineImages')))[0]
    Path(directory, 'email_body.html').write_text('<!doctype html><html lang="ko"><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'"><title>' + escape(subject) + '</title><body>' + embedded + '</body></html>', encoding='utf-8')


router = APIRouter()
router.include_router(address_book_router)
router.include_router(shared_address_book_router)
router.include_router(mail_signature_book.router)
router.include_router(mail_templates.router)
router.include_router(ppt_templates.router)


@router.get('/api/v1/report-mail/recipient')
def get_recipient(request: Request, request_number: str = Query(alias='requestNumber', min_length=1, max_length=100)):
    from .auth import require_context
    context = require_context(request)
    if context.status != 'ACTIVE' or not (context.is_admin or context.projects):
        raise ApiException(403, 'MAIL_RECIPIENT_FORBIDDEN', '실험 이용 권한이 있는 회원만 의뢰자를 조회할 수 있습니다.')
    number = request_number.strip()
    if not number or re.search(r'[\x00-\x1f\x7f]', number):
        raise ApiException(422, 'MAIL_RECIPIENT_REQUEST_INVALID', '의뢰번호를 확인하세요.')
    database = getattr(request.app.state, 'database', None)
    if database is None:
        raise ApiException(503, 'MAIL_RECIPIENT_UNAVAILABLE', '의뢰자 조회에 필요한 데이터베이스가 연결되지 않았습니다.')
    try:
        rows = database.fetch_request_recipient(number)
    except Exception as exc:
        raise ApiException(503, 'MAIL_RECIPIENT_UNAVAILABLE', '의뢰자 정보를 조회하지 못했습니다. 잠시 후 다시 확인하세요.') from exc
    if len(rows) != 1:
        raise ApiException(409 if rows else 404, 'MAIL_RECIPIENT_NOT_RESOLVED',
                           '의뢰자를 한 명으로 확인할 수 없습니다. 의뢰번호와 LIMS 의뢰자 정보를 확인하세요.')
    # The synchronized table currently has requester identity, but no email column.
    # Do not infer an address from a name/id. Spring resolves the actual mailbox in LIMS.
    return JSONResponse(dict(requestNumber=number, recipientName=rows[0].get('req_user_name') or '',
                             email=None, recipientPolicy='lims'), headers={'Cache-Control': 'no-store'})


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
    value = mail_signature_book.get_default(path)
    return JSONResponse(value, headers={'Cache-Control': 'no-store'})


def _signature_preferences(value):
    if not isinstance(value, dict) or 'signature' not in value or set(value) - ({'signature'} | set(SIGNATURE_FIELDS)):
        raise ValueError('서명 입력 형식을 확인하세요.')
    normalized = normalize_delivery(value)
    return {key: normalized[key] for key in ('signature', *SIGNATURE_FIELDS) if key in normalized}


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
        if len(content) > 400 * 1024:
            raise ApiException(413, 'MAIL_SIGNATURE_TOO_LARGE', '서명은 텍스트 2,000자와 이미지 한 장(256KB)까지 등록하세요.')
    try:
        value = json.loads(content)
        value = _signature_preferences(value)
    except (ValueError, TypeError) as exc:
        raise ApiException(422, 'MAIL_SIGNATURE_INVALID', str(exc) or '서명 입력 형식을 확인하세요.')
    await run_in_threadpool(mail_signature_book.put_default, path, value)
    return JSONResponse(value, headers={'Cache-Control': 'no-store'})

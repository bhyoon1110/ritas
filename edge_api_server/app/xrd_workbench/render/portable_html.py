"""Recipient HTML that never needs permission to read sibling files."""
import base64
import json
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

from .items import sample_viewer_path
from .pdf_raster import page_images

ASSETS = Path(__file__).with_name('assets')
MAX_HTML_BYTES = 128 * 1024 * 1024


def b64(data):
    return base64.b64encode(data).decode('ascii')


def embedded_file(name, data, mime, *, preview=False):
    if len(data) > MAX_HTML_BYTES * 3 // 4:
        raise ValueError(f'독립형 보고서에 포함하기에는 원본 파일이 너무 큽니다: {name}. 시료를 나누어 생성하세요.')
    result = dict(name=name, mime=mime, data=b64(data))
    if preview and mime == 'application/pdf':
        try:
            result['pages'] = []
            size = len(result['data'])
            for image in page_images(data, max_edge=1800, image_format='JPEG'):
                encoded = b64(image)
                size += len(encoded)
                if size > MAX_HTML_BYTES:
                    raise ValueError('PDF 미리보기 용량 한도 초과')
                result['pages'].append(encoded)
        except MemoryError:
            raise
        except Exception:
            # Preserve the exact original even if an encrypted/damaged PDF cannot be projected.
            result['pages'] = []
            result['notice'] = '이 PDF는 내부 미리보기를 만들 수 없습니다. 원본 저장으로 확인하세요.'
    elif preview and mime == 'text/plain':
        for encoding in ('utf-8-sig', 'utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'cp949', 'cp949'):
            try:
                result['text'] = data.decode(encoding)
                break
            except UnicodeError:
                continue
        else:
            result['text'] = data.decode('utf-8', errors='replace')
            result['notice'] = '문자 인코딩을 확인할 수 없어 일부 문자가 다르게 보일 수 있습니다. 원본 저장 시 원래 바이트를 유지합니다.'
    return result


def make_portable(html, viewer, samples, files):
    payload = dict(viewer=viewer, samples={sample_viewer_path(s['id']):dict(id=s['id'],name=s['name']) for s in samples}, files=files)
    class LinkCheck(HTMLParser):
        def handle_starttag(self, tag, attrs):
            href = dict(attrs).get('href') if tag == 'a' else None
            if href is None: return
            url = urlsplit(href)
            target = unquote(url.path)
            if url.scheme or url.netloc or target not in (set(files) | set(payload['samples']) | {'뷰어.html'}):
                raise ValueError(f'독립형 HTML에 링크 대상이 포함되지 않았습니다: {href}')
    LinkCheck().feed(html)
    encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    for char, replacement in (('<','\\u003c'),('>','\\u003e'),('&','\\u0026'),('\u2028','\\u2028'),('\u2029','\\u2029')):
        encoded = encoded.replace(char, replacement)
    # No data URI navigation or external PDF plug-in is required: PDF pages are images,
    # TXT uses textContent, and the trusted local viewer runs in a sandboxed srcdoc frame.
    extra = '<style>' + (ASSETS/'portable_report.css').read_text() + '</style>'
    extra += '<script id="xrd-portable-data" type="application/json">' + encoded + '</script>'
    extra += (ASSETS/'portable_report.html').read_text()
    extra += '<script>' + (ASSETS/'portable_report.js').read_text() + '</script>'
    result = html.replace('</body>', extra + '</body>')
    if len(result.encode('utf-8')) > MAX_HTML_BYTES:
        raise ValueError('독립형 보고서 HTML이 128MB를 초과했습니다. 시료를 나누어 보고서를 생성하세요.')
    return result

"""Bounded embedded raster images; no URLs, SVG, downloads or mail sending."""
import base64
import binascii
from hashlib import sha256
from io import BytesIO
import re

from PIL import Image

MAX_IMAGE_BYTES = 256 * 1024
MAX_IMAGE_COUNT = 4
MAX_IMAGE_TOTAL = 1024 * 1024
MAX_IMAGE_EDGE = 1280
MAX_HTML = 1500 * 1024
MAX_TABLES, MAX_ROWS, MAX_COLS = 3, 20, 8


def image_data(source):
    match = re.fullmatch(r'data:image/(png|jpeg);base64,([A-Za-z0-9+/]*={0,2})', source)
    if not match or len(source) > (MAX_IMAGE_BYTES + 2) // 3 * 4 + 32:
        raise ValueError('메일 이미지는 PNG/JPEG, 장당 256KB 이하만 허용합니다.')
    mime = 'image/' + match[1]
    try:
        data = base64.b64decode(match[2], validate=True)
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ValueError()
        with Image.open(BytesIO(data)) as im:
            if im.format != {'png': 'PNG', 'jpeg': 'JPEG'}[match[1]] or getattr(im, 'n_frames', 1) != 1:
                raise ValueError()
            if min(im.size) < 1 or max(im.size) > MAX_IMAGE_EDGE:
                raise ValueError()
            im.load()  # Verify pixel data too, after bounding decoded dimensions.
            width, height = im.size
    except (ValueError, OSError, binascii.Error, Image.DecompressionBombError) as exc:
        raise ValueError('메일 이미지가 손상되었거나 크기 제한(긴 변 1,280px)을 초과했습니다.') from exc
    encoded = base64.b64encode(data).decode('ascii')
    return dict(contentId='rist-' + sha256(data).hexdigest()[:24] + '@report',
                contentType=mime, dataBase64=encoded, sizeBytes=len(data), width=width, height=height)


def data_uri(item):
    return 'data:' + item['contentType'] + ';base64,' + item['dataBase64']


class MailImages:
    def __init__(self, inline=None, *, cid=False):
        self.cid, self.known, self.used, self.count, self.total = cid, {}, {}, 0, 0
        if inline is not None:
            if not isinstance(inline, list) or len(inline) > MAX_IMAGE_COUNT:
                raise ValueError('메일 이미지는 최대 4장입니다.')
            for item in inline:
                if not isinstance(item, dict) or not all(isinstance(item.get(k), str) for k in ('contentId', 'contentType', 'dataBase64')):
                    raise ValueError('메일 인라인 이미지 형식을 확인하세요.')
                checked = image_data(data_uri(item))
                if item['contentId'] != checked['contentId'] or item.get('sizeBytes') != checked['sizeBytes'] or item['contentId'] in self.known:
                    raise ValueError('메일 인라인 이미지 식별자·용량이 맞지 않습니다.')
                self.known[item['contentId']] = checked

    def source(self, source):
        if source.startswith('cid:'):
            item = self.known.get(source[4:])
            if not item:
                raise ValueError('메일 본문에서 참조하는 이미지가 없습니다.')
        else:
            item = image_data(source)
        self.count += 1
        self.total += item['sizeBytes']
        if self.count > MAX_IMAGE_COUNT or self.total > MAX_IMAGE_TOTAL:
            raise ValueError('메일 이미지는 최대 4장, 합계 1MB 이하입니다.')
        self.used[item['contentId']] = item
        return ('cid:' + item['contentId'] if self.cid else data_uri(item)), item

    def finish(self):
        if set(self.known) - set(self.used):
            raise ValueError('본문에서 사용하지 않는 인라인 이미지가 있습니다.')
        return list(self.used.values())

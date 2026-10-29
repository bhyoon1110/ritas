"""Reusable formatting only: never retain text, evidence, links or job identity."""
import hashlib
import json
import re

from .canvas import ObjectEdit

STYLE = {'x', 'y', 'w', 'h', 'fontSize', 'bold', 'color', 'fill', 'stroke', 'lineWidth', 'dash', 'align'}
CELL_STYLE = STYLE - {'x', 'y', 'w', 'h'}


def layout_key(role, objects):
    # Row counts may change with new results. Columns and original shape roles
    # must match; added/hidden objects are never transplanted between reports.
    signature = sorted((o['id'], o['kind'], sorted({c['col'] for c in o.get('cells', [])}))
                       for o in objects if o['id'].startswith('shape-'))
    return hashlib.sha256(json.dumps([role, signature], sort_keys=True).encode()).hexdigest()


def content(value):
    if isinstance(value, dict) and value.get('version') == 2:
        from .ppt_template_import import validate_content
        return validate_content(value)
    if not isinstance(value, dict) or set(value) != {'version', 'name', 'experiment', 'ratio', 'pages'}:
        raise ValueError('PPT 템플릿 입력 형식을 확인하세요.')
    if type(value['version']) is not int or value['version'] != 1:
        raise ValueError('지원하지 않는 템플릿 버전입니다.')
    name = value['name']
    if not isinstance(name, str) or not name.strip() or len(name) > 80 or re.search(r'[\x00-\x1f\x7f]', name):
        raise ValueError('템플릿 이름은 줄바꿈 없이 1~80자로 입력하세요.')
    if value['experiment'] not in ('FTIR', 'RAMAN', 'TEM', 'XRD'):
        raise ValueError('적용 실험을 확인하세요.')
    if type(value['ratio']) not in (int, float) or not .2 <= value['ratio'] <= 5:
        raise ValueError('페이지 비율을 확인하세요.')
    pages = value['pages']
    if not isinstance(pages, list) or not 1 <= len(pages) <= 200:
        raise ValueError('페이지는 1~200개까지 저장할 수 있습니다.')
    count = 0
    for page in pages:
        if not isinstance(page, dict) or set(page) != {'key', 'objects'} or not isinstance(page['key'], str) or not re.fullmatch('[a-f0-9]{64}', page['key']):
            raise ValueError('페이지 양식 정보를 확인하세요.')
        if not isinstance(page['objects'], list) or len(page['objects']) > 100:
            raise ValueError('페이지당 개체는 최대 100개입니다.')
        ids = set()
        for obj in page['objects']:
            if not isinstance(obj, dict) or set(obj) - (STYLE | {'id', 'kind', 'tableCells'}):
                raise ValueError('템플릿에는 배치·서식만 저장할 수 있습니다.')
            parsed = ObjectEdit.model_validate(obj)
            if not parsed.id.startswith('shape-') or parsed.id in ids or not parsed.kind:
                raise ValueError('원본 개체 정보가 올바르지 않습니다.')
            ids.add(parsed.id)
            if parsed.tableCells is not None:
                if parsed.kind != 'table':
                    raise ValueError('표 서식을 확인하세요.')
                cells = set()
                for cell in obj['tableCells']:
                    if set(cell) - (CELL_STYLE | {'row', 'col'}) or (cell['row'], cell['col']) in cells:
                        raise ValueError('표에는 셀 서식만 저장할 수 있습니다.')
                    cells.add((cell['row'], cell['col']))
            for pos, size in (('x', 'w'), ('y', 'h')):
                if obj.get(pos) is not None and obj.get(size) is not None and obj[pos] + obj[size] > 1.001:
                    raise ValueError('개체가 페이지 영역을 벗어납니다.')
            count += 1
    if not 1 <= count <= 500:
        raise ValueError('자유편집에서 배치·서식을 변경한 후 저장하세요. 최대 500개 개체를 저장할 수 있습니다.')
    return dict(value, name=name.strip())

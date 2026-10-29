"""Bounded OOXML template inspection. Evidence is never imported into a report.

Only selected source geometry/styles and a raster of the master/layout artwork
are reused. Source slide text is a private, bounded mapping label, not a value.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import PurePosixPath
import posixpath
import re
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile
from urllib.parse import unquote

from lxml import etree as E
import pdfplumber

from . import canvas
from .template_styles import STYLE, CELL_STYLE

MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 12
NS = canvas.NS


def xml(data):
    if len(data) > 3 * 1024 * 1024:
        raise ValueError('PPT 내부 XML이 너무 큽니다.')
    root = E.fromstring(data, parser=E.XMLParser(resolve_entities=False, no_network=True))
    if root.getroottree().docinfo.doctype:
        raise ValueError('외부 정의가 포함된 XML은 사용할 수 없습니다.')
    return root


def read_package(path):
    try:
        with ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > 2000 or sum(i.file_size for i in infos) > 60 * 1024 * 1024:
                raise ValueError('압축 해제 용량은 60MB, 내부 파일은 2,000개 이하여야 합니다.')
            files = {}
            for info in infos:
                name = info.filename
                if name in files or '\\' in name or name.startswith('/') or '..' in PurePosixPath(name).parts or info.flag_bits & 1:
                    raise ValueError('PPT 내부 경로 또는 암호화를 확인하세요.')
                if any(word in name.lower() for word in ('vbaproject', 'embeddings/', 'activex/')):
                    raise ValueError('매크로·OLE·ActiveX 개체를 제거한 PPTX를 사용하세요.')
                if name.lower().endswith('.svg'):
                    raise ValueError('SVG 그림은 PNG로 바꿔 저장한 PPTX를 사용하세요.')
                if info.is_dir():
                    continue
                data = archive.read(info)
                if name.endswith(('.xml', '.rels')):
                    tree = xml(data)
                    if any(word in data.lower() for word in (b'macroenabled', b'vbaproject', b'activex', b'oleobject')):
                        raise ValueError('매크로 없는 PPTX를 사용하세요.')
                    if name.endswith('.rels'):
                        for rel in list(tree):
                            if rel.get('TargetMode') == 'External':
                                if not rel.get('Type', '').endswith('/hyperlink'):
                                    raise ValueError('외부 파일에 연결된 그림·자료는 PPTX에 포함해서 저장하세요.')
                                tree.remove(rel)  # No external navigation in private previews.
                    for link in tree.xpath('//*[local-name()="hlinkClick" or local-name()="hlinkMouseOver"]'):
                        link.getparent().remove(link)
                    data = E.tostring(tree, xml_declaration=True, encoding='UTF-8')
                files[name] = data
    except (BadZipFile, E.XMLSyntaxError, RuntimeError, KeyError) as exc:
        raise ValueError('읽을 수 없는 PPTX입니다. PowerPoint에서 PPTX로 다시 저장하세요.') from exc
    for required in ('ppt/presentation.xml', 'ppt/_rels/presentation.xml.rels', '[Content_Types].xml'):
        if required not in files:
            raise ValueError('PPTX 프레젠테이션 파일을 선택하세요.')
    for name in files:
        if name.endswith('.rels'):
            part = '' if name == '_rels/.rels' else posixpath.join(posixpath.dirname(posixpath.dirname(name)), posixpath.basename(name)[:-5])
            relationships(files, part)
    return files


def relationships(files, part):
    name = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels')
    tree = xml(files[name]) if name in files else []
    result = {}
    for rel in tree:
        raw = unquote(rel.get('Target', ''))
        target = posixpath.normpath(posixpath.join(posixpath.dirname(part), raw))
        if '\\' in raw or target.startswith('../') or target.startswith('/') or ':' in target or target not in files:
            raise ValueError('PPT 내부 연결 파일을 찾을 수 없습니다.')
        result[rel.get('Id')] = (target, rel.get('Type', ''))
    return result


def normalized_style(item):
    value = {k: v for k, v in item.items() if k in STYLE}
    for key in ('x', 'y', 'w', 'h'):
        if key not in value:
            raise ValueError('개체 위치를 읽지 못했습니다.')
    if min(value['x'], value['y']) < 0 or value['w'] <= 0 or value['h'] <= 0:
        raise ValueError('페이지 밖 개체')
    for key in ('w', 'h'):
        value[key] = max(.002, value[key])
    if value['x'] + value['w'] > 1.001 or value['y'] + value['h'] > 1.001:
        raise ValueError('페이지 밖 개체')
    if 'fontSize' in value: value['fontSize'] = max(8, min(72, value['fontSize']))
    if 'lineWidth' in value: value['lineWidth'] = max(.25, min(12, value['lineWidth']))
    if value.get('dash') not in (None, 'solid', 'dash', 'dot', 'dashDot'): value['dash'] = 'solid'
    return value


def explicit_style(shape, values, cell=False):
    """Do not substitute canvas defaults for unresolved theme inheritance."""
    result = dict(values)
    run = shape.find('.//a:rPr', NS)
    paragraph = shape.find(('a' if cell else 'p')+':txBody/a:p/a:pPr', NS)
    props = shape.find('a:tcPr' if cell else 'p:spPr', NS)
    for key, attr in (('fontSize','sz'),('bold','b')):
        if run is None or run.get(attr) is None: result.pop(key,None)
    if run is None or run.find('a:solidFill/a:srgbClr',NS) is None: result.pop('color',None)
    if paragraph is None or paragraph.get('algn') is None: result.pop('align',None)
    for key, part in (('fill',props),('stroke',props.find('a:ln',NS) if props is not None else None)):
        if part is None or (part.find('a:solidFill/a:srgbClr',NS) is None and part.find('a:noFill',NS) is None): result.pop(key,None)
    line = props.find('a:ln',NS) if props is not None else None
    if line is None or line.get('w') is None: result.pop('lineWidth',None)
    if line is None or line.find('a:prstDash',NS) is None: result.pop('dash',None)
    return result


def source_objects(files, part, size):
    root = xml(files[part])
    layout_name = next((p for p, kind in relationships(files, part).values() if kind.endswith('/slideLayout')), None)
    layout = xml(files[layout_name]) if layout_name else None
    # Resolve the common inherited placeholder geometry before inspecting it.
    for shape in root.findall('p:cSld/p:spTree/p:sp', NS):
        ph = shape.find('p:nvSpPr/p:nvPr/p:ph', NS)
        sp = shape.find('p:spPr', NS)
        if ph is not None and sp is not None and sp.find('a:xfrm', NS) is None and layout is not None:
            for candidate in layout.findall('p:cSld/p:spTree/p:sp', NS):
                other = candidate.find('p:nvSpPr/p:nvPr/p:ph', NS)
                transform = candidate.find('p:spPr/a:xfrm', NS)
                if other is not None and transform is not None and other.get('idx', '0') == ph.get('idx', '0'):
                    sp.append(deepcopy(transform)); break
    try:
        _, found = canvas.objects(dict(xml=E.tostring(root), size=size))
    except (AttributeError, TypeError) as exc:
        raise ValueError('양식 개체 구조를 읽지 못했습니다. PPTX로 다시 저장하세요.') from exc
    objects, omitted = [], 0
    tree = root.find('p:cSld/p:spTree', NS)
    if tree is None: raise ValueError('양식 페이지 구조를 읽지 못했습니다.')
    omitted += len(tree.findall('p:grpSp', NS)) + len(root.findall('.//a:graphicData/*', NS)) - sum(i['kind'] == 'table' for _, i in found)
    for shape, item in found:
        try:
            style = explicit_style(shape, normalized_style(item))
            # Rotated/flipped/grouped objects need an explicit limitation, not
            # guessed coordinates which silently distort the generated report.
            transform = shape.find('p:xfrm' if item['kind'] == 'table' else 'p:spPr/a:xfrm', NS)
            if transform is not None and any(transform.get(k) not in (None, '0', 'false') for k in ('rot', 'flipH', 'flipV')):
                raise ValueError('회전 개체')
            cells = []
            native_cells = {(r,c):node for r,c,node,_ in canvas.pptx_tables.cells(shape)} if item['kind']=='table' else {}
            for c in item.get('cells', []):
                if len(cells) >= 200: raise ValueError('표가 큼')
                props = normalized_style(dict(c, x=0, y=0, w=1, h=1))
                props = explicit_style(native_cells[(c['row'],c['col'])],props,cell=True)
                cells.append(dict(row=c['row'], col=c['col'], **{k:v for k,v in props.items() if k in CELL_STYLE}))
            objects.append(dict(id='slot-'+str(len(objects)), kind=item['kind'], label=(item.get('text') or item['name'])[:80], style=style, cells=cells))
        except (ValueError, TypeError, KeyError):
            omitted += 1
    if len(objects) > 80:
        raise ValueError('양식의 페이지당 개체는 80개 이하로 줄여 주세요.')
    return objects, max(0, omitted)


def write_deck(files, path):
    with ZipFile(path, 'w', ZIP_DEFLATED) as archive:
        for name, data in files.items(): archive.writestr(name, data)


def prepare(source, output):
    from .renderers import convert_pptx_to_pdf
    from .spectrum_viewer import _PDF_RENDER_LOCK
    files = read_package(source)
    presentation = xml(files['ppt/presentation.xml'])
    size_node = presentation.find('p:sldSz', NS)
    if size_node is None: raise ValueError('PPTX 페이지 크기를 읽지 못했습니다.')
    size = (int(size_node.get('cx')), int(size_node.get('cy')))
    if min(size) <= 0 or not .2 <= size[0]/size[1] <= 5: raise ValueError('페이지 비율을 확인하세요.')
    rels = relationships(files, 'ppt/presentation.xml')
    slides = [rels[n.get('{'+NS['r']+'}id')][0] for n in presentation.findall('p:sldIdLst/p:sldId', NS)]
    if not 1 <= len(slides) <= MAX_PAGES: raise ValueError('외부 양식은 1~12페이지로 준비하세요.')
    pages = []
    for index, part in enumerate(slides):
        objects, omitted = source_objects(files, part, size)
        pages.append(dict(index=index, objects=objects, omitted=omitted))
    if len(json.dumps(pages).encode()) > 1024 * 1024:
        raise ValueError('양식의 표·개체 수를 줄여 주세요.')
    write_deck(files, output/'preview.pptx')
    # Remove slide evidence and master/layout text, retaining just background
    # art, logos and decorative geometry. No source report values are baked in.
    for name in list(files):
        if name in slides or re.fullmatch(r'ppt/(slideLayouts/slideLayout|slideMasters/slideMaster)\d+\.xml', name):
            root = xml(files[name]); tree = root.find('p:cSld/p:spTree', NS)
            if tree is None: continue
            for shape in list(tree):
                if E.QName(shape).localname in ('nvGrpSpPr', 'grpSpPr'): continue
                if name in slides or shape.find('.//p:ph', NS) is not None or shape.find('.//a:t', NS) is not None or E.QName(shape).localname in ('graphicFrame', 'grpSp'):
                    tree.remove(shape)
            files[name] = E.tostring(root, encoding='UTF-8')
    write_deck(files, output/'background.pptx')
    total = 0
    for kind in ('preview', 'background'):
        pdf = output/(kind+'.pdf')
        convert_pptx_to_pdf(output/(kind+'.pptx'), pdf, timeout_seconds=60)
        with _PDF_RENDER_LOCK, pdfplumber.open(pdf) as document:
            if len(document.pages) != len(slides): raise ValueError('양식 페이지 수가 변환 결과와 다릅니다.')
            for index, page in enumerate(document.pages):
                rendered = page.to_image(width=1000 if kind=='preview' else 1600, antialias=True)
                image = rendered.original.convert('RGB')
                try:
                    path = output/f'{kind}-{index}.png'; image.save(path, 'PNG')
                    if path.stat().st_size > 2 * 1024 * 1024: raise ValueError('배경 그림을 단순화하거나 이미지 해상도를 낮춰 주세요.')
                    total += path.stat().st_size
                    if total > 24 * 1024 * 1024: raise ValueError('양식 미리보기 합계가 24MB를 초과했습니다.')
                    if kind == 'background': pages[index]['backgroundDigest'] = sha256(path.read_bytes()).hexdigest()
                finally:
                    image.close(); rendered.original.close(); page.close()
        pdf.unlink(); (output/(kind+'.pptx')).unlink()
    metadata = dict(ratio=size[0]/size[1], pages=pages)
    (output/'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False), encoding='utf-8')
    return metadata


def validate_content(value):
    if not isinstance(value, dict) or set(value) != {'version','name','experiment','ratio','source','bindings'} or value['version'] != 2:
        raise ValueError('외부 PPTX 템플릿 형식을 확인하세요.')
    from .template_styles import content
    # Reuse the v1 common name/experiment/ratio rules without accepting any text.
    content(dict(version=1, name=value['name'], experiment=value['experiment'], ratio=value['ratio'],
                 pages=[dict(key='0'*64, objects=[dict(id='shape-'+'0'*20, kind='text', bold=True)])]))
    if not isinstance(value['source'], str) or not re.fullmatch('[a-f0-9]{64}', value['source']): raise ValueError('양식 파일 식별자를 확인하세요.')
    if not isinstance(value['bindings'], list) or len(value['bindings']) > 200: raise ValueError('연결할 페이지가 너무 많습니다.')
    keys = set()
    for binding in value['bindings']:
        if not isinstance(binding, dict) or set(binding) != {'key','slide','objects','background'}: raise ValueError('페이지 연결 형식을 확인하세요.')
        if not isinstance(binding['key'], str) or not re.fullmatch('[a-f0-9]{64}', binding['key']) or binding['key'] in keys: raise ValueError('페이지 양식이 중복되거나 올바르지 않습니다.')
        keys.add(binding['key'])
        if type(binding['slide']) is not int or not 0 <= binding['slide'] < MAX_PAGES or type(binding['background']) is not bool: raise ValueError('양식 페이지를 확인하세요.')
        if not isinstance(binding['objects'], list) or len(binding['objects']) > 80: raise ValueError('개체 연결 수를 확인하세요.')
        targets = set()
        for obj in binding['objects']:
            if not isinstance(obj, dict) or set(obj) != {'source','target'} or not re.fullmatch(r'slot-\d{1,2}', str(obj['source'])) or not re.fullmatch('shape-[a-f0-9]{20}', str(obj['target'])) or obj['target'] in targets:
                raise ValueError('대상 개체가 중복되거나 올바르지 않습니다.')
            targets.add(obj['target'])
    return dict(value, name=value['name'].strip())

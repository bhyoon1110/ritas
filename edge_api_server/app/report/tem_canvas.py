"""Non-destructive page/object editing of native TEM PowerPoint packages.

The private base is never delivered. Every revision starts from it, so hiding,
restoring and resetting objects/pages works even after autosave or a restart.
Slide part numbers are stable; presentation relationships determine page order.
"""
from copy import deepcopy
from pathlib import Path
import os
import shutil
import tempfile
from types import SimpleNamespace
from lxml import etree as ET
from zipfile import ZipFile, ZIP_DEFLATED

from . import canvas, tem_editable as tem
from .editor_variables import resolve_changes
from .pages import text_layout

NS, REL = tem.NS, tem.REL_NS
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
ADDED = "rist-editor:"


def base_path(job):
    path = job.work_dir / "review/base.pptx"
    return path if path.is_file() else job.pptx_path  # Pre-upgrade reports.


def asset_root(job):
    return job.work_dir / "review/editor-assets"


def preserve_base(job, previous=None):
    path = job.work_dir / "review/base.pptx"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        source = base_path(previous) if previous else job.pptx_path
        if previous:
            _link_or_copy(source, path)
        else:
            shutil.copyfile(source, path)
    if previous and asset_root(previous).is_dir():
        shutil.copytree(asset_root(previous), asset_root(job), dirs_exist_ok=True, copy_function=_link_or_copy)


def _link_or_copy(source, target):
    # Both baseline and hashed assets are immutable. Same-volume revisions can
    # share their bytes; deleting one history entry cannot remove another's file.
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)
    return target


def _rels_name(name):
    return str(Path(name).parent / "_rels" / (Path(name).name + ".rels"))


def _page_id(name, root):
    title = root.find("p:cSld", NS).get("name", "")
    return title[len(ADDED):] if title.startswith(ADDED) else "tem-" + Path(name).stem


def _payload(archive, name, size):
    xml = archive.read(name)
    root = ET.fromstring(xml)
    locked = []
    front = root.find("p:cSld", NS).get("name") in tem.FRONT_PAGES
    for shape in root.findall("p:cSld/p:spTree/*", NS):
        info = shape.find(".//p:cNvPr", NS)
        if info is None:
            continue
        label = info.get("name", "")
        # Read-only facts and shared cover/transfer values are edited through
        # their canonical inputs, not a second canvas text override.
        if label in {"POSCO background", "RIST logo", "Experiment banner title"} or (front and
                (label.startswith(("Trace ", "Cover ", "Meta card ")) or
                 label in {"Report title", "Report eyebrow"})):
            locked.append(info.get("id"))
        table = shape.find("a:graphic/a:graphicData/a:tbl", NS)
        if table is not None and not any((n.text or '').strip() for n in table.findall('.//a:t', NS)):
            locked.append(info.get("id"))  # Template image grids, not data tables.
    return dict(pageId=_page_id(name, root), name=name, xml=xml, size=size,
                rels=archive.read(_rels_name(name)), media=[], lockedShapeIds=locked)


def _read(archive):
    presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
    size = presentation.find("p:sldSz", NS)
    size = (int(size.get("cx")), int(size.get("cy")))
    return [_payload(archive, name, size) for name in tem._slides(archive)]


def _added(item, prototype, number):
    from ahn.report_cover import shape_xml
    width, height = prototype['size']
    # Use the source's branded layout/master but no original evidence shapes.
    root = ET.fromstring(prototype['xml'])
    root.find('p:cSld', NS).set('name', ADDED + item['id'])
    tree = root.find('p:cSld/p:spTree', NS)
    for shape in list(tree):
        if shape.tag not in {'{'+NS['p']+'}nvGrpSpPr', '{'+NS['p']+'}grpSpPr'}:
            tree.remove(shape)
    # Remove transitions/timing that might refer to removed shape IDs.
    for node in list(root):
        if node.tag not in {'{'+NS['p']+'}cSld', '{'+NS['p']+'}clrMapOvr'}:
            root.remove(node)
    lines, font = text_layout(item['body'])
    for sid, label, x, y, w, h, text, fs in (
        (2, 'Slide title', .065, .13, .87, .1, [item['title']], 2200),
        (3, 'Content text', .075, .27, .85, .62, lines, font),
    ):
        spec = dict(shape_id=sid, name=label, x=round(x*width), y=round(y*height),
                    cx=round(w*width), cy=round(h*height), text=text, font_size=fs,
                    color='172B4D', bold=sid==2, inset=0)
        tree.append(ET.fromstring(shape_xml(spec)))
    rels = ET.fromstring(prototype['rels'])
    for rel in list(rels):
        if not rel.get('Type', '').endswith('/slideLayout'):
            rels.remove(rel)
    return dict(pageId=item['id'], name=f'ppt/slides/slide{number}.xml', size=prototype['size'],
                xml=ET.tostring(root), rels=ET.tostring(rels), media=[], lockedShapeIds=[])


def compose(base, edits):
    edits = edits or {}
    slides = list(base)
    allowed = {p['pageId'] for p in slides}
    maximum = max(int(Path(p['name']).stem[5:]) for p in slides)
    for index, item in enumerate(edits.get('added', []), 1):
        if item['id'] in allowed or item['after'] not in allowed:
            raise ValueError('추가 페이지의 위치 또는 식별자가 올바르지 않습니다.')
        after = next(i for i, p in enumerate(slides) if p['pageId'] == item['after'])
        slides.insert(after+1, _added(item, base[0], maximum+index))
        allowed.add(item['id'])
    hidden, order = edits.get('hidden', []), edits.get('order') or []
    for ids in (hidden, order):
        if len(ids) != len(set(ids)) or set(ids)-allowed:
            raise ValueError('페이지 목록에 중복되거나 알 수 없는 항목이 있습니다.')
    if order:
        by_id = {p['pageId']: p for p in slides}
        arranged = [by_id[key] for key in order]
        predecessor = None
        for page in slides:
            if page['pageId'] not in order:
                index = next((i+1 for i,p in enumerate(arranged) if p['pageId']==predecessor), 0)
                arranged.insert(index, page)
            predecessor = page['pageId']
        slides = arranged
    visible = [p for p in slides if p['pageId'] not in hidden]
    if not 1 <= len(visible) <= 200:
        raise ValueError('보고서는 최소 1페이지, 최대 200페이지를 유지해야 합니다.')
    return slides  # Validate hidden objects too, before excluding them on export.


def normalize(value, previous):
    for key, default in (('pageEdits', dict(hidden=[], order=[], added=[])), ('canvasEdits', {})):
        if value.get(key) is None:
            value[key] = deepcopy(previous.get(key) or default)
    if value['pageEdits'].get('order') is None:
        value['pageEdits']['order'] = deepcopy((previous.get('pageEdits') or {}).get('order') or [])
    value['canvasEdits'] = {key: [{k:v for k,v in patch.items() if v is not None} for patch in patches]
                            for key, patches in value['canvasEdits'].items()}
    return value


def validate(job, value):
    with ZipFile(base_path(job)) as archive:
        slides = compose(_read(archive), resolve_changes(value)['pageEdits'])
    document = SimpleNamespace(page_edits=value['pageEdits'], canvas_edits=value['canvasEdits'],
                               canvas_asset_root=asset_root(job))
    # Stable immutable IDs mean unknown/locked shapes are always rejected.
    canvas.apply(document, slides, validate_only=True)


def render(job, value):
    """Build only in the new job. No mutation of the old PPT/PDF/package."""
    with tempfile.TemporaryDirectory(prefix='rist-tem-canvas-') as directory:
        text_ppt = Path(directory)/'text.pptx'
        tem.revise_pptx(base_path(job), text_ppt, value)
        tem.stamp_record(text_ppt, job.job_id, job.parent_job_id)
        with ZipFile(text_ppt) as archive:
            base = _read(archive)
            slides = compose(base, resolve_changes(value)['pageEdits'])
            document = SimpleNamespace(page_edits=value['pageEdits'], canvas_edits=value['canvasEdits'],
                                       canvas_asset_root=asset_root(job))
            canvas.apply(document, slides)
            slides = [p for p in slides if p['pageId'] not in value['pageEdits']['hidden']]
            _write(archive, job.pptx_path, base, slides)


def _write(archive, target, base, slides):
    presentation = ET.fromstring(archive.read('ppt/presentation.xml'))
    rels = ET.fromstring(archive.read('ppt/_rels/presentation.xml.rels'))
    types = ET.fromstring(archive.read('[Content_Types].xml'))
    slide_list = presentation.find('p:sldIdLst', NS)
    slide_list.clear()
    for rel in list(rels):
        if rel.get('Type', '').endswith('/slide'):
            rels.remove(rel)
    removed = {p['name'] for p in base} - {p['name'] for p in slides}
    for node in list(types):
        if node.get('PartName', '').lstrip('/') in removed:
            types.remove(node)
    replacements = {}
    media = {}
    for i, page in enumerate(slides, 1):
        rid = 'ristSlide'+str(i)
        while any(r.get('Id') == rid for r in rels):
            rid += 'x'
        ET.SubElement(rels, '{'+REL+'}Relationship', Id=rid, Type=NS['r']+'/slide', Target=page['name'][4:])
        ET.SubElement(slide_list, '{'+NS['p']+'}sldId', {'id': str(255+i), '{'+NS['r']+'}id': rid})
        if not any(t.get('PartName') == '/'+page['name'] for t in types):
            ET.SubElement(types, '{'+CT+'}Override', PartName='/'+page['name'],
                          ContentType='application/vnd.openxmlformats-officedocument.presentationml.slide+xml')
        replacements[page['name']] = page['xml']
        replacements[_rels_name(page['name'])] = page['rels']
        for name, path in page['media']:
            media['ppt/media/'+name] = path
    if media and not any(t.get('Extension')=='png' for t in types):
        ET.SubElement(types, '{'+CT+'}Default', Extension='png', ContentType='image/png')
    replacements.update({'ppt/presentation.xml': ET.tostring(presentation),
                         'ppt/_rels/presentation.xml.rels': ET.tostring(rels),
                         '[Content_Types].xml': ET.tostring(types)})
    removed.update(_rels_name(name) for name in list(removed))
    with ZipFile(target, 'w', compression=ZIP_DEFLATED) as output:
        for info in archive.infolist():
            if info.filename in removed or info.filename in replacements or info.filename in media:
                continue
            with archive.open(info) as src, output.open(info, 'w') as dst:
                shutil.copyfileobj(src, dst, 1024*1024)
        for name, content in replacements.items():
            output.writestr(name, content)
        for name, path in media.items():
            output.write(path, name)


def layout(job, model):
    with ZipFile(base_path(job)) as archive:
        canonical = tem._trace_fields(archive)
        locks = {p['pageId']: p['lockedShapeIds'] for p in _read(archive)}
    _, result, _ = tem.inspect_pptx(job.pptx_path, trace_fields=canonical)
    with ZipFile(job.pptx_path) as archive:
        slides = _read(archive)
    valid = {s['sectionId'] for s in model['sections']}
    for page, payload in zip(result['pages'], slides):
        # A real table whose cells the author cleared remains editable. Only
        # grids that were empty in the immutable template are decorations.
        payload['lockedShapeIds'] = locks.get(payload['pageId'], payload['lockedShapeIds'])
        _, entries = canvas.objects(payload)
        page.update(id=payload['pageId'], objects=[item for _,item in entries])
        if not payload['pageId'].startswith('added-'):
            from .template_styles import layout_key
            front = ET.fromstring(payload['xml']).find('p:cSld', NS).get('name', '')
            page['templateKey'] = layout_key(front if front in tem.FRONT_PAGES else 'tem-native', page['objects'])
        # Ignore added canvas text as canonical source sections. Its text is
        # edited through canvas patches, never duplicate hidden source fields.
        page['regions'] = [r for r in page['regions'] if all(not ref.startswith('section/') or
                            ref.split('/')[1] in valid for ref in r['refs'])]
        if payload['pageId'].startswith('added-'):
            page['regions'] = []
            for shape, item in entries:
                name = shape.find('.//p:cNvPr', NS).get('name')
                key = {'Slide title': 'title', 'Content text': 'body'}.get(name)
                if key:
                    page['regions'].append({k: item[k] for k in ('x','y','w','h')} |
                        dict(refs=['page/'+payload['pageId']+'/'+key], empty=not item['text'].strip()))
            page['title'] = next((item['text'] for _,item in entries if item['name']=='Slide title'), '추가 페이지')
    result.update(pageEditing=True, canvasEditing=True)
    return result

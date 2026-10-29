"""Prepare the approved 2026-10-29 XRD design without example analysis data.

Run from edge_api_server with the supplied 보고서_양식.pptx path. The source is
read-only; the bundled result contains layout prototypes and branding only.
WPPF, absent from the reference, retains the existing compatible layout.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
from zipfile import ZipFile, ZIP_DEFLATED

from lxml import etree as E

ASSETS = Path(__file__).resolve().parents[1] / 'app/xrd_workbench/render/assets'
SOURCE_SHA256 = 'affcafddf024eba3451401793ce8e862a4d57416db212faf5c820842a30f6616'
NS = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}


def serialize(root):
    return E.tostring(root, encoding='UTF-8', xml_declaration=True, standalone=True)


def name(shape):
    prop = shape.find('.//p:cNvPr', NS)
    return prop.get('name', '') if prop is not None else ''


def clear_text(root):
    for text in root.findall('.//a:t', NS):
        text.text = ''
    for node in root.xpath('//*[local-name()="hlinkClick" or local-name()="hlinkMouseOver"]'):
        node.getparent().remove(node)


def copy_content_types(files, supplied, copied_parts):
    """Carry each imported part's OPC type, including layouts absent in the base."""
    types = E.fromstring(files['[Content_Types].xml'])
    source = E.fromstring(supplied['[Content_Types].xml'])
    overrides = {n.get('PartName'): n.get('ContentType') for n in source if n.get('PartName')}
    defaults = {n.get('Extension'): n.get('ContentType') for n in source if n.get('Extension')}
    for part in sorted(copied_parts):
        if part.endswith('.rels'):
            continue
        part_name = '/' + part
        content_type = overrides.get(part_name) or defaults.get(part.rsplit('.', 1)[-1])
        if not content_type:
            raise ValueError(f'Imported template part has no content type: {part}')
        existing = next((n for n in types if n.get('PartName') == part_name), None)
        if existing is not None:
            existing.set('ContentType', content_type)
        else:
            # Per-part declarations do not change the types of retained base parts.
            E.SubElement(types, '{'+types.nsmap[None]+'}Override',
                         PartName=part_name, ContentType=content_type)
    files['[Content_Types].xml'] = serialize(types)


def prepare(source, output):
    if sha256(Path(source).read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError('Use the approved 2026-10-29 보고서_양식.pptx; other decks need a new binding review.')
    with ZipFile(ASSETS/'posco_xrd_a4.pptx') as archive:
        files = {n: archive.read(n) for n in archive.namelist()}
    with ZipFile(source) as archive:
        supplied = {n: archive.read(n) for n in archive.namelist()}
    # Explicit bindings for this reviewed reference, not an arbitrary PPT importer.
    size = E.fromstring(supplied['ppt/presentation.xml']).find('p:sldSz', NS)
    if (size.get('cx'), size.get('cy')) != ('9906000', '6858000'):
        raise ValueError('The approved XRD reference must use its original A4 landscape size.')
    copied_parts = set()
    for part, data in supplied.items():
        if part.startswith(('ppt/slideMasters/', 'ppt/slideLayouts/', 'ppt/theme/')):
            files[part] = data
            copied_parts.add(part)
    # These are the header, RIST logo and cover artwork. Do not ship example plots.
    for part in list(files):
        if part.startswith('ppt/media/'):
            del files[part]
    for part in ('ppt/media/image1.jpeg', 'ppt/media/image2.png', 'ppt/media/image3.jpg'):
        files[part] = supplied[part]
        copied_parts.add(part)
    copy_content_types(files, supplied, copied_parts)
    summary = E.fromstring(supplied['ppt/slides/slide2.xml'])
    tree = summary.find('p:cSld/p:spTree', NS)
    keep = {'Title', 'Text 2', 'Page number', 'Sample frame', 'Overlay frame', 'Remarks box'}
    for shape in list(tree):
        if E.QName(shape).localname in ('nvGrpSpPr', 'grpSpPr'):
            continue
        if name(shape) not in keep:
            tree.remove(shape)
    old_summary = E.fromstring(files['ppt/slides/slide1.xml'])
    text = next(s for s in old_summary.find('p:cSld/p:spTree', NS) if name(s) == 'Text 11')
    tree.append(deepcopy(text))
    clear_text(summary)
    next(s for s in tree if name(s) == 'Text 2').find('.//a:t', NS).text = 'XRD'
    files['ppt/slides/slide1.xml'] = serialize(summary)

    phase = E.fromstring(supplied['ppt/slides/slide4.xml'])
    tree = phase.find('p:cSld/p:spTree', NS)
    keep = {'Title', 'Text 2', 'Page number', 'Text 5', 'Text 8', 'Match frame', 'Section label', 'Phase table'}
    for shape in list(tree):
        if E.QName(shape).localname in ('nvGrpSpPr', 'grpSpPr'):
            continue
        if name(shape) not in keep:
            tree.remove(shape)
    clear_text(phase)
    next(s for s in tree if name(s) == 'Text 2').find('.//a:t', NS).text = 'XRD'
    for table in phase.findall('.//a:tbl', NS):
        for row in table.findall('a:tr', NS)[2:]:
            table.remove(row)
        for cell, label in zip(table.findall('a:tr', NS)[0].findall('a:tc', NS),
                               ('상 (Phase)', '화학식', '# ICDD Card No.')):
            cell.find('.//a:t', NS).text = label
    for shape in tree:
        if name(shape) == 'Section label':
            shape.find('.//a:t', NS).text = '주요상 / 미량상 & 추정상'
    old_phase = E.fromstring(files['ppt/slides/slide3.xml'])
    for shape in old_phase.find('p:cSld/p:spTree', NS):
        if name(shape) in {'Source link frame', 'Text 12', 'Text 14', 'Text 16', 'Text 18', 'Text 20', 'Text 22'}:
            tree.append(deepcopy(shape))
    files['ppt/slides/slide3.xml'] = serialize(phase)

    cover = E.fromstring(supplied['ppt/slides/slide1.xml'])
    tree = cover.find('p:cSld/p:spTree', NS)
    shapes = list(tree.findall('p:sp', NS))
    if len(shapes) != 2:
        raise ValueError('The approved cover must contain title/requester and date boxes.')
    title, date = shapes
    requester = deepcopy(title)
    tree.insert(list(tree).index(date), requester)
    for shape, label, bounds in ((title, 'Title', (25.8, 72.34, 223.57, 14)),
                                  (requester, 'Cover requester', (25.8, 95.1, 223.57, 14)),
                                  (date, 'Cover completionDate', None)):
        shape.find('.//p:cNvPr', NS).set('name', label)
        if bounds:
            transform = shape.find('p:spPr/a:xfrm', NS)
            for node, keys, values in ((transform[0], ('x','y'), bounds[:2]), (transform[1], ('cx','cy'), bounds[2:])):
                for key, value in zip(keys, values): node.set(key, str(round(value*36000)))
        clear_text(shape)
    files['ppt/slides/slide4.xml'] = serialize(cover)
    files['ppt/slides/_rels/slide4.xml.rels'] = supplied['ppt/slides/_rels/slide1.xml.rels']
    # Prototype slides never keep source links or stale/duplicate shape IDs.
    for number in (1, 2, 3, 4):
        part = f'ppt/slides/slide{number}.xml'
        root = E.fromstring(files[part])
        for index, prop in enumerate(root.findall('.//p:cNvPr', NS), 1): prop.set('id', str(index))
        files[part] = serialize(root)
    # Cover artwork is used explicitly by the renderer/preview, while slide 4 is
    # also registered here so the prepared design remains a valid viewable PPTX.
    rels = E.fromstring(files['ppt/_rels/presentation.xml.rels'])
    rel_id = 'rIdXrdCover'
    E.SubElement(rels, '{'+rels.nsmap[None]+'}Relationship', Id=rel_id,
                 Type=NS['r']+'/slide', Target='slides/slide4.xml')
    files['ppt/_rels/presentation.xml.rels'] = serialize(rels)
    presentation = E.fromstring(files['ppt/presentation.xml'])
    E.SubElement(presentation.find('p:sldIdLst', NS), '{'+NS['p']+'}sldId',
                 {'id':'1000', '{'+NS['r']+'}id':rel_id})
    files['ppt/presentation.xml'] = serialize(presentation)
    types = E.fromstring(files['[Content_Types].xml'])
    E.SubElement(types, '{'+types.nsmap[None]+'}Override', PartName='/ppt/slides/slide4.xml',
                 ContentType='application/vnd.openxmlformats-officedocument.presentationml.slide+xml')
    if not any(n.get('Extension') == 'jpg' for n in types):
        E.SubElement(types, '{'+types.nsmap[None]+'}Default', Extension='jpg', ContentType='image/jpeg')
    files['[Content_Types].xml'] = serialize(types)
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for part, data in files.items(): archive.writestr(part, data)
    print(json.dumps(dict(source_sha256=sha256(Path(source).read_bytes()).hexdigest(),
                          output=str(output), bytes=Path(output).stat().st_size)))


if __name__ == '__main__':
    prepare(Path(sys.argv[1]), ASSETS/'xrd_default_20261029.pptx')

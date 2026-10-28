"""Embed source files in the report's existing buttons without adding slides.

Button icons are cropped from the same rendered report to preserve their
appearance. Opening requires a desktop OLE-capable app; portable HTML remains
the alternative for mobile viewers. No server links, macros or shell actions.
"""
from copy import deepcopy
import hashlib
import io
import math
import posixpath
from pathlib import PurePosixPath
from urllib.parse import unquote
from zipfile import ZipFile, ZIP_DEFLATED

from lxml import etree as E
from PIL import Image

from .items import link_target, viewer_target
from .layout import source_links
from .link_audit import read_linked_source, report_sources, ppt_click_targets_by_slide
from .package_names import package_paths
from .pdf_raster import page_images
from .ole_package import package_file

MAX_BYTES = 128 * 1024 * 1024


def resources(project, document, resolve_source, targets, viewer_state=None, views=None):
    from .viewer_export import export_html
    from .viewer_state import prepare_state, prepare_views
    paths = package_paths(project)
    included = {e['sample_id'] for e in document['samples'] if e['included']}
    samples = [s for s in project['samples'] if s['id'] in included]
    viewer_names = {PurePosixPath(path).name.casefold() for path in paths['viewers'].values()}
    comparison_name, suffix = '전체 비교 그래프.html', 2
    while comparison_name.casefold() in viewer_names:
        comparison_name = f'전체 비교 그래프 ({suffix}).html'
        suffix += 1
    result, used = {}, 0

    def add(target, filename, data):
        nonlocal used
        used += len(data)
        if used > MAX_BYTES:
            raise ValueError('PPT 첨부 자료가 128MB를 초과합니다. 시료를 나누어 생성하세요.')
        result[target] = dict(name=filename, data=data)

    records = {r['id']: r for r in report_sources(project, document)}
    for sample in samples:
        for link in source_links(sample, paths):
            target = link_target(link)
            if target not in targets or target in result:
                continue
            record = records[link['source_id']]
            add(target, unquote(PurePosixPath(link['href']).name), read_linked_source(record, resolve_source))
    for sample in [None, *samples]:
        target = viewer_target(sample['id'] if sample else None, paths=paths)
        if target not in targets:
            continue
        chosen = [sample] if sample else samples
        filtered = deepcopy(project)
        filtered['samples'] = deepcopy(chosen)
        filtered['sources'] = [r for s in chosen for r in s['sources']]
        display = prepare_state(project, viewer_state, filtered)
        display['viewMode'] = 'single' if sample else 'multi'
        display['visible'] = [s['id'] for s in chosen]
        html = export_html(filtered, display, prepare_views(project, views, filtered)).encode('utf-8')
        name = PurePosixPath(paths['viewers'][sample['id']]).name if sample else comparison_name
        add(target, name, html)
    if set(result) != set(targets):
        raise ValueError('PPT 링크에 포함할 원본 자료를 찾지 못했습니다. 보고서를 다시 생성하세요.')
    return result


def make_offline(content, project, document, resolve_source, *, preview_pdf, viewer_state=None, views=None):
    from .posco_pptx import NS, REL, CT, q, xml
    by_slide = ppt_click_targets_by_slide(content)
    targets = {t for values in by_slide.values() for t in values}
    if not targets:
        return content
    attachments = resources(project, document, resolve_source, targets, viewer_state, views)
    with ZipFile(io.BytesIO(content)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    presentation = E.fromstring(files['ppt/presentation.xml'])
    size = presentation.find('p:sldSz', NS)
    width, height = int(size.get('cx')), int(size.get('cy'))
    types = E.fromstring(files['[Content_Types].xml'])
    for number, record in enumerate(attachments.values(), 1):
        record['part'] = f'xrdAttachment{number}.bin'
        files['ppt/embeddings/'+record['part']] = package_file(record['name'], record['data'])

    def relationship(collection, kind, target):
        rid = 'rIdOffline' + str(len(collection)+1)
        while any(r.get('Id') == rid for r in collection): rid += 'x'
        E.SubElement(collection, '{'+REL+'}Relationship', Id=rid, Type=NS['r']+'/'+kind, Target=target)
        return rid

    parts = sorted(by_slide, key=lambda n: int(posixpath.basename(n)[5:-4]))
    count = 0
    for count, preview in enumerate(page_images(preview_pdf, max_edge=2600), 1):
        if count > len(parts):
            raise ValueError('PPT 첨부 버튼의 페이지 수가 보고서와 다릅니다.')
        name = parts[count-1]
        if not by_slide[name]:
            continue
        slide = E.fromstring(files[name])
        path = posixpath.dirname(name)+'/_rels/'+posixpath.basename(name)+'.rels'
        relationships = E.fromstring(files[path])
        links = {r.get('Id'): r for r in relationships}
        tree = slide.find('p:cSld/p:spTree', NS)
        sid = max(int(v) for v in tree.xpath('.//p:cNvPr/@id', namespaces=NS)) + 1
        converted = set()
        with Image.open(io.BytesIO(preview)) as image:
            for shape in list(tree):
                clicks = shape.findall('.//a:hlinkClick', NS)
                if not clicks:
                    continue
                destinations = {links[c.get(q('r:id'))].get('Target') for c in clicks}
                if len(destinations) != 1:
                    raise ValueError('PPT 한 버튼에 여러 첨부 파일이 연결되어 있습니다. 링크를 분리하세요.')
                record = attachments[destinations.pop()]
                xf = shape.find('p:spPr/a:xfrm', NS)
                if xf is None: xf = shape.find('p:xfrm', NS)
                if xf is None or int(xf.get('rot', '0')) or xf.get('flipH', '0') not in ('0', 'false') or xf.get('flipV', '0') not in ('0', 'false'):
                    raise ValueError('PPT 첨부 버튼의 위치를 변환하지 못했습니다.')
                start, extent = xf.find('a:off', NS), xf.find('a:ext', NS)
                x, y = int(start.get('x')), int(start.get('y'))
                w, h = int(extent.get('cx')), int(extent.get('cy'))
                if min(x, y) < 0 or min(w, h) <= 0 or x+w > width+1 or y+h > height+1:
                    raise ValueError('PPT 첨부 버튼이 페이지 밖에 있습니다.')
                box = (math.floor(x*image.width/width), math.floor(y*image.height/height),
                       math.ceil((x+w)*image.width/width), math.ceil((y+h)*image.height/height))
                buffer = io.BytesIO()
                with image.crop(box) as crop: crop.save(buffer, format='PNG')
                icon = buffer.getvalue()
                media = 'xrd-attachment-'+hashlib.sha256(icon).hexdigest()+'.png'
                files['ppt/media/'+media] = icon
                image_id = relationship(relationships, 'image', '../media/'+media)
                rid = relationship(relationships, 'oleObject', '../embeddings/'+record['part'])
                obj = E.fromstring(f'''<p:graphicFrame xmlns:p="{NS['p']}" xmlns:a="{NS['a']}" xmlns:r="{NS['r']}">
                  <p:nvGraphicFramePr><p:cNvPr id="{sid}" name="첨부 파일 열기"><a:hlinkClick r:id="" action="ppaction://ole?verb=0"/></p:cNvPr><p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>
                  <p:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{w}" cy="{h}"/></p:xfrm>
                  <a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/presentationml/2006/ole"><p:oleObj r:id="{rid}" name="Package" progId="Package" showAsIcon="1" imgW="{w}" imgH="{h}"><p:embed/>
                  <p:pic><p:nvPicPr><p:cNvPr id="{sid+1}" name="첨부 버튼"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr>
                  <p:blipFill><a:blip r:embed="{image_id}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>
                  <p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{w}" cy="{h}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>
                  </p:oleObj></a:graphicData></a:graphic></p:graphicFrame>''')
                obj.find('.//p:cNvPr', NS).set('descr', record['name'])
                obj.find('.//a:hlinkClick', NS).set('tooltip', record['name'])
                # Preserve native formatting underneath; replace the click only.
                for click in clicks:
                    converted.add(click.get(q('r:id')))
                    click.getparent().remove(click)
                tree.insert(tree.index(shape)+1, obj)
                sid += 2
        for rel in list(relationships):
            if rel.get('Id') in converted: relationships.remove(rel)
        files[name], files[path] = xml(slide), xml(relationships)
    if count != len(parts):
        raise ValueError('PPT 첨부 버튼의 페이지 수가 보고서와 다릅니다.')
    for ext, mime in (('bin','application/vnd.openxmlformats-officedocument.oleObject'), ('png','image/png')):
        if not any(n.get('Extension') == ext for n in types):
            E.SubElement(types, '{'+CT+'}Default', Extension=ext, ContentType=mime)
    files['[Content_Types].xml'] = xml(types)
    output = io.BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for name, data in files.items(): archive.writestr(name, data)
    result = output.getvalue()
    if any(ppt_click_targets_by_slide(result).values()):
        raise ValueError('배포 PPT에 외부 자료 링크가 남았습니다.')
    return result

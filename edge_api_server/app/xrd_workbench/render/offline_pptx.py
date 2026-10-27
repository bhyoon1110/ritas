"""Self-contained recipient PPT: internal preview slides + embedded original files.

Report pages keep their layout and order. Hidden appendix slides are reached by
the existing links, without a server, login, sibling folder or macro. OLE opening
is optional (Windows PowerPoint); previews do not depend on OLE support.
"""
from copy import deepcopy
import io
import hashlib
import posixpath
import re
from pathlib import PurePosixPath
from urllib.parse import unquote
from zipfile import ZipFile, ZIP_DEFLATED

from lxml import etree as E
from PIL import Image, ImageDraw, ImageFont

from .items import link_target, viewer_target
from .layout import source_links
from .link_audit import read_linked_source, report_sources, ppt_click_targets_by_slide
from .package_names import package_paths
from .pdf_raster import page_images
from .ole_package import package_file

MAX_BYTES = 128 * 1024 * 1024
MAX_PAGES = 200


def resources(project, document, resolve_source, targets, viewer_state=None, views=None):
    from .viewer_export import export_html
    from .viewer_state import prepare_state, prepare_views
    from .posco_pptx import render_panel_png
    from ..core.figure import build_plan
    paths = package_paths(project)
    included = {e['sample_id'] for e in document['samples'] if e['included']}
    samples = [s for s in project['samples'] if s['id'] in included]
    viewer_names = {PurePosixPath(path).name.casefold() for path in paths['viewers'].values()}
    comparison_name, suffix = '전체 비교 그래프.html', 2
    while comparison_name.casefold() in viewer_names:
        comparison_name = f'전체 비교 그래프 ({suffix}).html'
        suffix += 1
    result, used, count = {}, 0, 0

    def add(target, filename, data, previews):
        nonlocal used, count
        used += len(data)
        if used > MAX_BYTES:
            raise ValueError('PPT 첨부 자료가 128MB를 초과합니다. 시료를 나누어 생성하세요.')
        images = []
        for preview in previews:
            used += len(preview) if isinstance(preview, bytes) else len(preview.encode('utf-8'))
            count += 1
            if used > MAX_BYTES or count > MAX_PAGES:
                raise ValueError('PPT 첨부 미리보기가 128MB 또는 200페이지를 초과합니다. 시료를 나누어 생성하세요.')
            images.append(preview)
        result[target] = dict(name=filename, data=data, previews=images or ['미리보기를 만들 수 없습니다. 포함된 원본 파일을 열어 확인하세요.'])

    records = {r['id']: r for r in report_sources(project, document)}
    for sample in samples:
        for link in source_links(sample, paths):
            target = link_target(link)
            if target not in targets or target in result:
                continue
            record = records[link['source_id']]
            data = read_linked_source(record, resolve_source)
            filename = PurePosixPath(link['href']).name
            # archive href is URL-encoded; use the original portable filename.
            filename = unquote(filename)
            if record['role'] == 'measurement':
                text = None
                for encoding in ('utf-8-sig', 'utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'cp949', 'cp949'):
                    try:
                        text = data.decode(encoding); break
                    except UnicodeError:
                        continue
                text = text if text is not None else data.decode('utf-8', errors='replace')
                lines = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', ' ', text).splitlines()
                # This is explicitly an excerpt; the embedded TXT retains every byte.
                preview = '측정데이터 앞부분 (전체 내용은 원본 파일에 포함)\n\n' + '\n'.join(line[:130] for line in lines[:30])
                add(target, filename, data, [preview])
            else:
                def previews():
                    try:
                        yield from page_images(data, max_edge=1800, image_format='JPEG')
                    except MemoryError:
                        raise
                    except Exception:
                        yield '이 PDF의 미리보기를 만들 수 없습니다. 포함된 원본 PDF를 열어 확인하세요.'
                add(target, filename, data, previews())
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
        single = deepcopy(document)
        ids = {s['id'] for s in chosen}
        for entry in single['samples']: entry['included'] = entry['sample_id'] in ids
        single['figure']['include'] = True
        single['figure']['groups'] = []
        panel = next((p for p in build_plan(project, single)['panels'] if p['curves']), None)
        preview = render_panel_png(panel, 245, 133) if panel else '표시할 그래프가 없습니다.'
        name = PurePosixPath(paths['viewers'][sample['id']]).name if sample else comparison_name
        add(target, name, html, [preview])
    if set(result) != set(targets):
        raise ValueError('PPT 링크에 포함할 원본 자료를 찾지 못했습니다. 보고서를 다시 생성하세요.')
    return result


def make_offline(content, project, document, resolve_source, *, viewer_state=None, views=None):
    from .posco_pptx import Deck, NS, MM, REL, CT, q, xml
    from .posco_preview import deck_pages
    from .pdf import render_pdf
    from .delivery_pptx import flatten_pptx
    by_slide = ppt_click_targets_by_slide(content)
    targets = {t for values in by_slide.values() for t in values}
    if not targets:
        return content
    attachments = resources(project, document, resolve_source, targets, viewer_state, views)
    # Rasterize appendix text/images too: default recipient protection must not
    # accidentally leave editable tables or analysis text on the new pages.
    appendix, entries = Deck(), []
    for target, record in attachments.items():
        for index, preview in enumerate(record['previews']):
            slide = appendix.slide(blank=True)
            appendix.title(slide, '첨부 자료 · ' + record['name'])
            appendix.text(slide, 'PPT에 포함된 자료  ·  서버 연결 없이 확인', (8, 25, 260, 8), size=11, color='0A7489')
            if isinstance(preview, bytes):
                appendix.picture(slide, preview, (8, 35, 260, 112), record['name'])
            else:
                # Narrow text columns wrap predictably even for scientific TXT.
                from .fonts import wrap_lines
                lines = wrap_lines(preview, 252, 8)
                excerpt = '\n'.join(lines[:30])
                appendix.text(slide, excerpt, (12, 35, 252, 112), size=8)
            appendix.text(slide, f'{index+1} / {len(record["previews"])}', (130, 149, 40, 7), size=10)
            appendix.text(slide, '원본 열기: Windows PowerPoint에서 파일 아이콘을 더블클릭하세요.', (8, 157, 205, 7), size=9)
            appendix.text(slide, '첨부 열기를 지원하지 않는 앱에서는 이 미리보기 또는 패키지의 보고서.html을 이용하세요.', (8, 168, 260, 7), size=8)
            entries.append((target, index))
    native = appendix.finish(document)
    raster = flatten_pptx(native, render_pdf(deck_pages(appendix), document))
    with ZipFile(io.BytesIO(content)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    with ZipFile(io.BytesIO(raster)) as archive:
        extra = {name: archive.read(name) for name in archive.namelist()}
    presentation = E.fromstring(files['ppt/presentation.xml'])
    main_count = len(presentation.find('p:sldIdLst', NS))
    size = presentation.find('p:sldSz', NS)
    source_size = E.fromstring(extra['ppt/presentation.xml']).find('p:sldSz', NS)
    scale = min(int(size.get('cx'))/int(source_size.get('cx')),
                int(size.get('cy'))/int(source_size.get('cy')))
    dx = (int(size.get('cx'))-int(source_size.get('cx'))*scale)/2
    dy = (int(size.get('cy'))-int(source_size.get('cy'))*scale)/2
    ids = presentation.find('p:sldIdLst', NS)
    rels = E.fromstring(files['ppt/_rels/presentation.xml.rels'])
    types = E.fromstring(files['[Content_Types].xml'])
    first, last = {}, {}
    for n, (target, _) in enumerate(entries, main_count+1):
        first.setdefault(target, n); last[target] = n
    from ..config import BUNDLED_FONTS
    icon = Image.new('RGB', (440, 84), '#17627a')
    icon_font = ImageFont.truetype(str(BUNDLED_FONTS/'NotoSansKR-Regular.ttf'), 32)
    ImageDraw.Draw(icon).text((220, 42), '원본 파일 열기', anchor='mm', font=icon_font, fill='white')
    buffer = io.BytesIO(); icon.save(buffer, format='PNG'); icon.close()
    files['ppt/media/xrd-attachment-icon.png'] = buffer.getvalue()
    for number, (target, record) in enumerate(attachments.items(), 1):
        record['part'] = f'xrdAttachment{number}.bin'
        files['ppt/embeddings/'+record['part']] = package_file(record['name'], record['data'])
    layout = next(r.get('Target') for r in E.fromstring(files['ppt/slides/_rels/slide1.xml.rels']) if r.get('Type', '').endswith('/slideLayout'))

    def relationship(collection, kind, target):
        rid = 'rIdOffline' + str(len(collection)+1)
        while any(r.get('Id') == rid for r in collection): rid += 'x'
        E.SubElement(collection, '{'+REL+'}Relationship', Id=rid, Type=NS['r']+'/'+kind, Target=target)
        return rid

    def button(tree, relationships, label, x, width, destination):
        from ..config import BUNDLED_FONTS
        sid = max(int(v) for v in tree.xpath('.//p:cNvPr/@id', namespaces=NS)) + 1
        rid = relationship(relationships, 'slide', f'slide{destination}.xml')
        picture = Image.new('RGB', (int(width*8), 56), '#eaf5f7')
        draw = ImageDraw.Draw(picture)
        font = ImageFont.truetype(str(BUNDLED_FONTS/'NotoSansKR-Regular.ttf'), 25)
        draw.text((picture.width/2, 28), label, anchor='mm', font=font, fill='#17627a')
        image = io.BytesIO(); picture.save(image, format='PNG'); picture.close()
        media = 'xrd-nav-'+hashlib.sha256(image.getvalue()).hexdigest()+'.png'
        files['ppt/media/'+media] = image.getvalue()
        image_id = relationship(relationships, 'image', '../media/'+media)
        shape = E.fromstring(f'''<p:pic xmlns:p="{NS['p']}" xmlns:a="{NS['a']}" xmlns:r="{NS['r']}">
          <p:nvPicPr><p:cNvPr id="{sid}" name="자료 이동"><a:hlinkClick r:id="{rid}" action="ppaction://hlinksldjump"/></p:cNvPr><p:cNvPicPr/><p:nvPr/></p:nvPicPr>
          <p:blipFill><a:blip r:embed="{image_id}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>
          <p:spPr><a:xfrm><a:off x="{int(x*MM)}" y="{int(179*MM)}"/><a:ext cx="{int(width*MM)}" cy="{int(7*MM)}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>''')
        shape.find('.//p:cNvPr', NS).set('descr', label)
        tree.append(shape)

    for index, (target, page) in enumerate(entries, 1):
        number = main_count + index
        name = f'ppt/slides/slide{number}.xml'
        slide = E.fromstring(extra[f'ppt/slides/slide{index}.xml'])
        slide.set('show', '0')  # navigation appendix; normal report slide show stays unchanged
        relationships = E.fromstring(extra[f'ppt/slides/_rels/slide{index}.xml.rels'])
        for rel in relationships:
            if rel.get('Type').endswith('/slideLayout'):
                rel.set('Target', layout)
            elif rel.get('Type').endswith('/image'):
                old = posixpath.normpath('ppt/slides/' + rel.get('Target'))
                media = 'xrd-appendix-'+posixpath.basename(old)
                files['ppt/media/'+media] = extra[old]
                rel.set('Target', '../media/'+media)
        tree = slide.find('p:cSld/p:spTree', NS)
        back = next(int(posixpath.basename(s)[5:-4]) for s, links in by_slide.items() if target in links)
        button(tree, relationships, '보고서로 돌아가기', 68, 52, back)
        if page: button(tree, relationships, '이전 자료 페이지', 127, 45, number-1)
        if number < last[target]: button(tree, relationships, '다음 자료 페이지', 179, 45, number+1)
        record = attachments[target]
        rid = relationship(relationships, 'oleObject', '../embeddings/'+record['part'])
        image_id = relationship(relationships, 'image', '../media/xrd-attachment-icon.png')
        sid = max(int(v) for v in tree.xpath('.//p:cNvPr/@id', namespaces=NS)) + 1
        obj = E.fromstring(f'''<p:graphicFrame xmlns:p="{NS['p']}" xmlns:a="{NS['a']}" xmlns:r="{NS['r']}">
          <p:nvGraphicFramePr><p:cNvPr id="{sid}" name="첨부 원본"><a:hlinkClick r:id="" action="ppaction://ole?verb=0"/></p:cNvPr><p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>
          <p:xfrm><a:off x="{216*MM}" y="{155*MM}"/><a:ext cx="{52*MM}" cy="{10*MM}"/></p:xfrm>
          <a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/presentationml/2006/ole"><p:oleObj r:id="{rid}" name="Package" progId="Package" showAsIcon="1" imgW="{52*MM}" imgH="{10*MM}"><p:embed/>
          <p:pic><p:nvPicPr><p:cNvPr id="{sid+1}" name="원본 파일 열기"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr>
          <p:blipFill><a:blip r:embed="{image_id}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>
          <p:spPr><a:xfrm><a:off x="{216*MM}" y="{155*MM}"/><a:ext cx="{52*MM}" cy="{10*MM}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>
          </p:oleObj></a:graphicData></a:graphic></p:graphicFrame>''')
        obj.find('.//p:cNvPr', NS).set('descr', record['name'])
        tree.append(obj)
        # A site's custom template may use a different page size/orientation.
        # Fit the whole appendix scene, including navigation and OLE hit areas.
        for xf in slide.findall('.//a:xfrm', NS) + slide.findall('.//p:xfrm', NS):
            start, extent = xf.find('a:off', NS), xf.find('a:ext', NS)
            if start is not None:
                start.set('x', str(round(int(start.get('x'))*scale+dx)))
                start.set('y', str(round(int(start.get('y'))*scale+dy)))
            if extent is not None:
                for key in ('cx','cy'): extent.set(key, str(round(int(extent.get(key))*scale)))
        ole = obj.find('.//p:oleObj', NS)
        for key in ('imgW','imgH'): ole.set(key, str(round(int(ole.get(key))*scale)))
        files[name] = xml(slide)
        files[f'ppt/slides/_rels/slide{number}.xml.rels'] = xml(relationships)
        rid = relationship(rels, 'slide', f'slides/slide{number}.xml')
        new_id = max(int(n.get('id')) for n in ids) + 1
        E.SubElement(ids, q('p:sldId'), {'id':str(new_id), q('r:id'):rid})
        E.SubElement(types, '{'+CT+'}Override', PartName='/'+name, ContentType='application/vnd.openxmlformats-officedocument.presentationml.slide+xml')
    for name in by_slide:
        slide = E.fromstring(files[name])
        path = posixpath.dirname(name)+'/_rels/'+posixpath.basename(name)+'.rels'
        relationships = E.fromstring(files[path])
        converted = set()
        for rel in relationships:
            if rel.get('Type') == NS['r']+'/hyperlink' and rel.get('Target') in first:
                rel.set('Target', f'slide{first[rel.get("Target")]}.xml')
                rel.set('Type', NS['r']+'/slide')
                rel.attrib.pop('TargetMode', None)
                converted.add(rel.get('Id'))
        for click in slide.findall('.//a:hlinkClick', NS):
            if click.get(q('r:id')) in converted: click.set('action', 'ppaction://hlinksldjump')
        files[name], files[path] = xml(slide), xml(relationships)
    for ext, mime in (('bin','application/vnd.openxmlformats-officedocument.oleObject'), ('png','image/png')):
        if not any(n.get('Extension') == ext for n in types):
            E.SubElement(types, '{'+CT+'}Default', Extension=ext, ContentType=mime)
    files['ppt/presentation.xml'] = xml(presentation)
    files['ppt/_rels/presentation.xml.rels'] = xml(rels)
    files['[Content_Types].xml'] = xml(types)
    if 'docProps/app.xml' in files:
        props = E.fromstring(files['docProps/app.xml'])
        for node in props:
            if E.QName(node).localname == 'Slides': node.text = str(len(ids))
        files['docProps/app.xml'] = xml(props)
    output = io.BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for name, data in files.items(): archive.writestr(name, data)
    result = output.getvalue()
    if any(ppt_click_targets_by_slide(result).values()):
        raise ValueError('배포 PPT에 외부 자료 링크가 남았습니다.')
    return result

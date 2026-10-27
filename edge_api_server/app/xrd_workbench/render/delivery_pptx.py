"""Rasterized recipient deck; the native source deck remains available to the server editor.

This prevents direct text/table/chart editing, not deliberate image replacement or DRM bypass.
Only reachable package parts are retained, so native charts/workbooks/notes cannot leak through.
"""
import io
import posixpath
import shutil
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from lxml import etree as E

from .pdf_raster import page_images

P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
NS = dict(p=P, a=A, r=R)


def xml(node):
    return E.tostring(node, encoding='UTF-8', xml_declaration=True, standalone=True)


def custom_template_pdf(content):
    """Site overrides need Office projection to retain their actual custom master artwork."""
    executable = shutil.which('libreoffice') or shutil.which('soffice')
    if not executable:
        raise ValueError('사용자 지정 XRD 양식을 배포용 PPT로 만들려면 서버 LibreOffice가 필요합니다.')
    with tempfile.TemporaryDirectory(prefix='xrd-delivery-') as folder:
        root = Path(folder)
        (root/'source.pptx').write_bytes(content)
        try:
            result = subprocess.run([executable, '-env:UserInstallation='+(root/'profile').as_uri(),
                '--headless', '--convert-to', 'pdf', '--outdir', str(root), str(root/'source.pptx')],
                capture_output=True, timeout=180, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError('사용자 지정 XRD 배포용 PPT 변환에 실패했습니다. 서버 LibreOffice를 확인하세요.') from exc
        if result.returncode or not (root/'source.pdf').is_file():
            raise ValueError('사용자 지정 XRD 배포용 PPT 변환에 실패했습니다. 서버 LibreOffice를 확인하세요.')
        return (root/'source.pdf').read_bytes()


def flatten_pptx(content, pdf):
    with ZipFile(io.BytesIO(content)) as archive:
        files = {name:archive.read(name) for name in archive.namelist()}
    root_rels = E.fromstring(files['_rels/.rels'])
    for rel in list(root_rels):
        if rel.get('Type','').endswith('/thumbnail'): root_rels.remove(rel)
    files['_rels/.rels'] = xml(root_rels)
    presentation = E.fromstring(files['ppt/presentation.xml'])
    size = presentation.find('p:sldSz', NS)
    width, height = size.get('cx'), size.get('cy')
    parts = sorted((n for n in files if n.startswith('ppt/slides/slide') and n.endswith('.xml')),
                   key=lambda n:int(posixpath.basename(n)[5:-4]))
    count = 0
    for count, image in enumerate(page_images(pdf, max_edge=2600), 1):
        if count > len(parts):
            raise ValueError('배포용 PPT 페이지 수가 미리보기와 다릅니다.')
        name = parts[count-1]
        old = E.fromstring(files[name])
        relpath = posixpath.join(posixpath.dirname(name),'_rels',posixpath.basename(name)+'.rels')
        old_rels = {r.get('Id'):r for r in E.fromstring(files[relpath])}
        rels = E.Element('Relationships', nsmap={None:REL})
        for rel in old_rels.values():
            if rel.get('Type').endswith('/slideLayout'): rels.append(rel)
        root = E.Element('{'+P+'}sld', nsmap=NS, showMasterSp='0')
        tree = E.SubElement(E.SubElement(root,'{'+P+'}cSld'),'{'+P+'}spTree')
        tree.append(E.fromstring(f'<p:nvGrpSpPr xmlns:p="{P}"><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'))
        E.SubElement(tree,'{'+P+'}grpSpPr')
        image_name = f'xrd-delivery-{count}.png'
        files['ppt/media/'+image_name] = image
        E.SubElement(rels,'{'+REL+'}Relationship',Id='rIdPageImage',Type=R+'/image',Target='../media/'+image_name)
        picture = E.fromstring(f'''<p:pic xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}">
          <p:nvPicPr><p:cNvPr id="2" name="보고서 {count}페이지"/><p:cNvPicPr><a:picLocks noMove="1" noResize="1" noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>
          <p:blipFill><a:blip r:embed="rIdPageImage"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>
          <p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{width}" cy="{height}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>''')
        tree.append(picture)
        # Preserve click rectangles from the actual validated PPT; no text boxes or tables survive.
        for shape in old.findall('p:cSld/p:spTree/*', NS):
            clicks = shape.findall('.//a:hlinkClick', NS)
            if not clicks: continue
            xf = shape.find('p:spPr/a:xfrm', NS)
            if xf is None: xf = shape.find('p:xfrm', NS)
            if xf is None:
                raise ValueError('PPT 링크 영역을 변환하지 못했습니다.')
            for click in clicks:
                link = old_rels[click.get('{'+R+'}id')]
                rid = 'rIdClick'+str(len(rels))
                E.SubElement(rels,'{'+REL+'}Relationship',Id=rid,Type=R+'/hyperlink',Target=link.get('Target'),TargetMode='External')
                overlay = E.fromstring(f'''<p:sp xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}">
                  <p:nvSpPr><p:cNvPr id="{len(tree)+1}" name="자료 열기"><a:hlinkClick r:id="{rid}"/></p:cNvPr>
                  <p:cNvSpPr><a:spLocks noMove="1" noResize="1"/></p:cNvSpPr><p:nvPr/></p:nvSpPr>
                  <p:spPr><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:solidFill><a:srgbClr val="FFFFFF"><a:alpha val="0"/></a:srgbClr></a:solidFill><a:ln><a:noFill/></a:ln></p:spPr></p:sp>''')
                overlay.find('p:spPr',NS).insert(0,E.fromstring(E.tostring(xf)))
                tree.append(overlay)
        files[name], files[relpath] = xml(root), xml(rels)
    if count != len(parts):
        raise ValueError('배포용 PPT 페이지 수가 미리보기와 다릅니다.')
    # Traverse package relationships after replacement: drop orphaned native slide images,
    # editable charts, embedded XLSX and speaker notes rather than merely hiding them.
    keep = {'[Content_Types].xml'}
    def visit(part):
        if part in keep: return
        if part: keep.add(part)
        rp = posixpath.join(posixpath.dirname(part),'_rels',posixpath.basename(part)+'.rels') if part else '_rels/.rels'
        if rp not in files: return
        keep.add(rp)
        for rel in E.fromstring(files[rp]):
            if rel.get('TargetMode') != 'External':
                target = posixpath.normpath(posixpath.join(posixpath.dirname(part),rel.get('Target'))).lstrip('/')
                if target not in files: raise ValueError('배포용 PPT 내부 파일 연결이 올바르지 않습니다.')
                visit(target)
    visit('')
    types = E.fromstring(files['[Content_Types].xml'])
    for item in list(types):
        if item.get('PartName') and item.get('PartName').lstrip('/') not in keep: types.remove(item)
    if not any(t.get('Extension') == 'png' for t in types):
        E.SubElement(types,'{'+CT+'}Default',Extension='png',ContentType='image/png')
    files['[Content_Types].xml'] = xml(types)
    output = io.BytesIO()
    with ZipFile(output,'w',ZIP_DEFLATED) as archive:
        for name in sorted(keep): archive.writestr(name,files[name])
    return output.getvalue()

"""XRD's supplied POSCO A4 slide designs, filled with reviewed report data.

Clone the actual OOXML artwork/layouts rather than drawing a look-alike background.
The live PPT preview reads this same filled deck, including its original master artwork.
All request-specific parts live in a fresh archive, including native text and tables.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import math
import re
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from lxml import etree as E
from PIL import Image

from ..config import load_settings
from ..core.figure import build_plan
from ..core.phase_class import phase_ref
from ..core.parsing.values import central
from ..core.report import validate_document
from .figure import render_panel_png
from .fonts import wrap_lines
from .items import link_target
from .layout import build_pages, source_links
from .sample_page import remarks, quantity_status

TEMPLATE = Path(__file__).with_name('assets') / 'posco_xrd_a4.pptx'
NS = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
      'c': 'http://schemas.openxmlformats.org/drawingml/2006/chart'}
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
MM = 36000
CLASS_NAMES = {'major': '주요상', 'trace': '미량상', 'presumed': '추정상', None: '미분류'}
PIE_COLORS = ('0000FF', 'F08080', '008000', 'A5A5A5', 'FFC000', '5B9BD5', '8064A2', '264478')
SUMMARY_TITLE = '분석 결과 요약'
QUANTITY_TITLE = '상 동정 (Phase Identification) & WPPF 정량 (Phase Fraction) 결과'
PHASE_TITLE = '상 동정 (Phase Identification) 결과'


def q(tag):
    prefix, name = tag.split(':')
    return '{' + NS[prefix] + '}' + name


def xml(root):
    return E.tostring(root, encoding='UTF-8', xml_declaration=True, standalone=True)


def named(slide, name):
    return [s for s in slide.find('p:cSld/p:spTree', NS)
            if s.xpath('.//p:cNvPr/@name', namespaces=NS) == [name]]


def remove(shape):
    shape.getparent().remove(shape)


def box(shape):
    transform = shape.find('p:spPr/a:xfrm', NS)
    if transform is None:
        transform = shape.find('p:xfrm', NS)
    offset, extent = transform[0], transform[1]
    return tuple(int(node.get(key)) / MM for node, key in
                 ((offset, 'x'), (offset, 'y'), (extent, 'cx'), (extent, 'cy')))


def place(shape, bounds):
    transform = shape.find('p:spPr/a:xfrm', NS)
    if transform is None:
        transform = shape.find('p:xfrm', NS)
    for node, keys, values in ((transform[0], ('x', 'y'), bounds[:2]),
                               (transform[1], ('cx', 'cy'), bounds[2:])):
        for key, value in zip(keys, values):
            node.set(key, str(round(value * MM)))


def set_text(shape, text, *, size=10, color='102A35', bold=False, dimensions=None, minimum=8):
    """Fit complete editable text. Return False instead of silently clipping a long value."""
    body = shape.find('p:txBody', NS)
    if body is None:
        body = shape.find('a:txBody', NS)
    width, height = dimensions or box(shape)[2:]
    chosen, lines = size, []
    while chosen >= minimum:
        lines = wrap_lines(str(text), max(1, width - 1.2), chosen, bold)
        if len(lines) * chosen * 1.25 / 2.83465 <= height - .2:
            break
        chosen -= .5
    else:
        return False
    for child in list(body):
        if child.tag == q('a:p'):
            body.remove(child)
    props = body.find('a:bodyPr', NS)
    props.set('wrap', 'none')
    # Keep the template's vertical alignment (notably its centred title band).
    for attr in ('lIns', 'rIns', 'tIns', 'bIns'): props.set(attr, '0')
    for child in list(props):
        if E.QName(child).localname.endswith('Autofit'): props.remove(child)
    E.SubElement(props, q('a:noAutofit'))
    for line in lines or ['']:
        paragraph = E.SubElement(body, q('a:p'))
        pp = E.SubElement(paragraph, q('a:pPr'), marL='0', indent='0')
        spacing = E.SubElement(pp, q('a:lnSpc'))
        E.SubElement(spacing, q('a:spcPts'), val=str(round(chosen * 125)))
        E.SubElement(pp, q('a:buNone'))
        run = E.SubElement(paragraph, q('a:r'))
        rp = E.SubElement(run, q('a:rPr'), sz=str(round(chosen * 100)), b='1' if bold else '0')
        fill = E.SubElement(rp, q('a:solidFill'))
        E.SubElement(fill, q('a:srgbClr'), val=color)
        for script in ('latin', 'ea', 'cs'): E.SubElement(rp, q('a:' + script), typeface='Malgun Gothic')
        E.SubElement(run, q('a:t')).text = line
    return True


def fit_text(shape, text, **kwargs):
    """Keep the template frame, with an explicit ellipsis instead of an extra slide.

    The complete value remains in the saved report and in the shape's alternative text.
    Scientific values are never rewritten to make them fit.
    """
    text = str(text)
    if set_text(shape, text, **kwargs):
        return
    prop = shape.find('.//p:cNvPr', NS)
    if prop is not None:
        prop.set('descr', text)
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if set_text(shape, text[:middle] + '…', **kwargs):
            low = middle
        else:
            high = middle - 1
    set_text(shape, text[:low] + '…', **kwargs)


def subscript_formula(cell):
    """Style stoichiometric numbers without changing any source characters/values.

    Leading coefficients (5H2O), explicit charges (Fe3+) and ambiguous notation
    stay on the baseline. Only the formula column calls this, never phase names.
    """
    for paragraph in cell.findall('a:txBody/a:p', NS):
        for run in list(paragraph.findall('a:r', NS)):
            text = run.findtext('a:t', '', NS)
            spans = [m.span(1) for m in re.finditer(r'(?:[A-Z][a-z]?|[)\]])\s*(\d+(?:\.\d+)?)', text)
                     if not re.match(r'\s*[+−-]', text[m.end():])]
            if not spans:
                continue
            offset, index = 0, paragraph.index(run)
            pieces = []
            for start, end in spans:
                pieces.extend(((text[offset:start], False), (text[start:end], True)))
                offset = end
            pieces.append((text[offset:], False))
            for value, subscript in pieces:
                if not value:
                    continue
                part = deepcopy(run)
                part.find('a:t', NS).text = value
                if subscript:
                    props = part.find('a:rPr', NS)
                    props.set('baseline', '-25000')
                    props.set('sz', str(round(int(props.get('sz', '950')) * .75)))
                paragraph.insert(index, part)
                index += 1
            paragraph.remove(run)


def phase_table_data(sample, classification):
    """Use the actual common database as header, never the reference's example DB."""
    phases = sample['phases']
    databases = {str(p.get('db') or '').strip() for p in phases}
    common = next(iter(databases)) if len(databases) == 1 and '' not in databases else None
    rows = [(f"[{CLASS_NAMES[classification(sample, p)]}] {p['name']}", p.get('formula') or '—',
             (str(p.get('card') or '—') if common else
              ' '.join(str(v) for v in (p.get('db'), p.get('card')) if v) or '—')) for p in phases]
    return common or 'DB / Card No.', rows or [('상 목록 미제공', '—', '—')]


class Deck:
    def __init__(self):
        with ZipFile(TEMPLATE) as archive:
            self.files = {n: archive.read(n) for n in archive.namelist()}
        self.templates = [E.fromstring(self.files[f'ppt/slides/slide{i}.xml']) for i in (1, 2, 3)]
        self.text_shape = deepcopy(named(self.templates[0], 'Text 11')[0])
        self.pages = []
        self.images = {}
        self.charts = []
        self.edits = {}
        self.page_info = {}

    def slide(self, index=0, blank=False, *, kind='summary', sample_id=None):
        root = deepcopy(self.templates[index])
        if blank:
            for s in list(root.find('p:cSld/p:spTree', NS)):
                if E.QName(s).localname not in ('nvGrpSpPr', 'grpSpPr') and not any(s in named(root, n) for n in ('Title', 'Text 2', 'Page number')):
                    remove(s)
        rels = E.Element('Relationships', nsmap={None: REL})
        E.SubElement(rels, 'Relationship', Id='rId1', Type=NS['r'] + '/slideLayout', Target='../slideLayouts/slideLayout6.xml')
        self.pages.append((root, rels))
        self.page_info[root] = dict(kind=kind, sample_id=sample_id,
                                   edit=dict(tab=sample_id or 'overview'))
        return root

    def mark(self, shape, target):
        if target:
            self.edits[shape] = target
        return shape

    def rel(self, slide, kind, target, external=False):
        rels = next(r for s, r in self.pages if s is slide)
        rid = 'rId' + str(len(rels) + 1)
        attrs = dict(Id=rid, Type=NS['r'] + '/' + kind, Target=target)
        if external: attrs['TargetMode'] = 'External'
        E.SubElement(rels, 'Relationship', **attrs)
        return rid

    def append(self, slide, shape, name):
        tree = slide.find('p:cSld/p:spTree', NS)
        prop = shape.find('.//p:cNvPr', NS)
        prop.set('id', str(max(int(x) for x in slide.xpath('.//p:cNvPr/@id', namespaces=NS)) + 1))
        prop.set('name', name)
        tree.append(shape)
        return shape

    def text(self, slide, value, bounds, *, size=10, bold=False, color='102A35', name='Report text', href=None, edit=None):
        shape = deepcopy(self.text_shape)
        place(shape, bounds)
        if not set_text(shape, value, size=size, bold=bold, color=color, minimum=min(size, 8)):
            raise ValueError('PPT 텍스트가 지정 영역을 넘습니다. 내용을 나누어 주세요.')
        shape.find('p:txBody/a:bodyPr', NS).set('anchor', 't')
        self.append(slide, shape, name)
        self.mark(shape, edit)
        if href:
            rid = self.rel(slide, 'hyperlink', href, True)
            for prop in shape.findall('.//a:rPr', NS):
                E.SubElement(prop, q('a:hlinkClick'), {q('r:id'): rid})
        return shape

    def picture(self, slide, data, bounds, label, *, edit=None):
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            ext = 'jpg' if image.format == 'JPEG' else 'png'
        filename = 'xrd-' + hashlib.sha256(data).hexdigest() + '.' + ext
        self.images[filename] = data
        x, y, w, h = bounds
        scale = min(w / width, h / height)
        px, py = width * scale, height * scale
        shape = E.Element(q('p:pic'))
        nv = E.SubElement(shape, q('p:nvPicPr'))
        E.SubElement(nv, q('p:cNvPr'), id='1', name=label, descr=label)
        E.SubElement(nv, q('p:cNvPicPr'))
        E.SubElement(nv, q('p:nvPr'))
        fill = E.SubElement(shape, q('p:blipFill'))
        E.SubElement(fill, q('a:blip'), {q('r:embed'): self.rel(slide, 'image', '../media/' + filename)})
        E.SubElement(E.SubElement(fill, q('a:stretch')), q('a:fillRect'))
        sp = E.SubElement(shape, q('p:spPr'))
        xf = E.SubElement(sp, q('a:xfrm'))
        E.SubElement(xf, q('a:off'), x=str(round((x+(w-px)/2)*MM)), y=str(round((y+(h-py)/2)*MM)))
        E.SubElement(xf, q('a:ext'), cx=str(round(px*MM)), cy=str(round(py*MM)))
        E.SubElement(E.SubElement(sp, q('a:prstGeom'), prst='rect'), q('a:avLst'))
        self.append(slide, shape, label)
        self.mark(shape, edit)

    def title(self, slide, title):
        shape = named(slide, 'Title')[0]
        fit_text(shape, title, size=19 if title.startswith(QUANTITY_TITLE) else 24, bold=True, minimum=14)

    def pie(self, slide, quantities, bounds, *, edit=None):
        """Native chart + embedded workbook: original numerical values, never inferred zeros."""
        from openpyxl import Workbook
        values = [v.get('value') for v in quantities]
        units = {v.get('unit') for v in quantities}
        if (not 1 <= len(values) <= 8 or len(units) != 1 or
                any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or v < 0 for v in values) or sum(values) <= 0):
            return False
        if any(central(qty['raw']) is None or not math.isclose(value, central(qty['raw']), rel_tol=1e-9, abs_tol=1e-9)
               for qty, value in zip(quantities, values)):
            return False
        number = len(self.charts) + 1
        chart = E.Element(q('c:chartSpace'), nsmap={k:NS[k] for k in ('c','a','r')})
        E.SubElement(chart,q('c:lang'),val='ko-KR')
        body=E.SubElement(chart,q('c:chart'))
        E.SubElement(body,q('c:autoTitleDeleted'),val='1')
        plot=E.SubElement(body,q('c:plotArea'));E.SubElement(plot,q('c:layout'))
        pie=E.SubElement(plot,q('c:pieChart'));E.SubElement(pie,q('c:varyColors'),val='1')
        series=E.SubElement(pie,q('c:ser'))
        E.SubElement(series,q('c:idx'),val='0');E.SubElement(series,q('c:order'),val='0')
        for index in range(len(values)):
            point = E.SubElement(series, q('c:dPt'))
            E.SubElement(point, q('c:idx'), val=str(index))
            fill = E.SubElement(E.SubElement(point, q('c:spPr')), q('a:solidFill'))
            E.SubElement(fill, q('a:srgbClr'), val=PIE_COLORS[index])
        for tag,column,kind,items in (('cat','A','str',[v['phase'] for v in quantities]),('val','B','num',values)):
            ref=E.SubElement(E.SubElement(series,q('c:'+tag)),q('c:'+kind+'Ref'))
            E.SubElement(ref,q('c:f')).text=f"'Data'!${column}$2:${column}${len(values)+1}"
            cache=E.SubElement(ref,q('c:'+kind+'Cache'))
            if kind=='num':E.SubElement(cache,q('c:formatCode')).text='0.0###'
            E.SubElement(cache,q('c:ptCount'),val=str(len(items)))
            for i,value in enumerate(items):E.SubElement(E.SubElement(cache,q('c:pt'),idx=str(i)),q('c:v')).text=str(value)
        labels=E.SubElement(pie,q('c:dLbls'))
        E.SubElement(labels,q('c:dLblPos'),val='bestFit')
        for key,val in (('showLegendKey','0'),('showVal','1'),('showCatName','0'),('showSerName','0'),('showPercent','0'),('showLeaderLines','1')):
            E.SubElement(labels,q('c:'+key),val=val)
        legend=E.SubElement(body,q('c:legend'));E.SubElement(legend,q('c:legendPos'),val='t')
        E.SubElement(legend,q('c:overlay'),val='0');E.SubElement(body,q('c:plotVisOnly'),val='1')
        text=E.SubElement(chart,q('c:txPr'));E.SubElement(text,q('a:bodyPr'));E.SubElement(text,q('a:lstStyle'))
        props=E.SubElement(E.SubElement(text,q('a:p')),q('a:pPr'))
        run=E.SubElement(props,q('a:defRPr'),sz='900')
        for script in ('latin','ea'):E.SubElement(run,q('a:'+script),typeface='Malgun Gothic')
        ext=E.SubElement(chart,q('c:externalData'),{q('r:id'):'rId1'});E.SubElement(ext,q('c:autoUpdate'),val='0')
        workbook=Workbook();sheet=workbook.active;sheet.title='Data'
        sheet.append(['Phase',next(iter(units)) or 'Value','Original value'])
        for quantity in quantities:sheet.append([quantity['phase'],quantity['value'],quantity['raw']])
        # Phase labels are untrusted instrument text, never Excel formulas.
        for row in sheet:
            for cell in row:
                if isinstance(cell.value, str): cell.data_type = 's'
        content=io.BytesIO();workbook.save(content)
        self.charts.append((xml(chart),content.getvalue()))
        frame=E.Element(q('p:graphicFrame'))
        nv=E.SubElement(frame,q('p:nvGraphicFramePr'));E.SubElement(nv,q('p:cNvPr'),id='1',name='WPPF 정량 차트')
        E.SubElement(nv,q('p:cNvGraphicFramePr'));E.SubElement(nv,q('p:nvPr'))
        x,y,w,h=bounds;xf=E.SubElement(frame,q('p:xfrm'))
        E.SubElement(xf,q('a:off'),x=str(round(x*MM)),y=str(round(y*MM)))
        E.SubElement(xf,q('a:ext'),cx=str(round(w*MM)),cy=str(round(h*MM)))
        graphic=E.SubElement(frame,q('a:graphic'));data=E.SubElement(graphic,q('a:graphicData'),uri=NS['c'])
        E.SubElement(data,q('c:chart'),{q('r:id'):self.rel(slide,'chart',f'../charts/chart{number}.xml')})
        self.append(slide,frame,'WPPF 정량 차트')
        self.mark(frame,edit)
        return True

    def number_pages(self):
        for number, (slide, _) in enumerate(self.pages, 1):
            shape = named(slide, 'Page number')[0]
            set_text(shape, str(number), size=10, color='6B7F88')
            shape.find('p:txBody/a:p/a:pPr', NS).set('algn', 'r')

    def finish(self, document):
        self.number_pages()
        files = {n: v for n, v in self.files.items() if not n.startswith('ppt/slides/')}
        presentation = E.fromstring(files['ppt/presentation.xml'])
        ids = presentation.find('p:sldIdLst', NS)
        ids.clear()
        rels = E.fromstring(files['ppt/_rels/presentation.xml.rels'])
        for rel in list(rels):
            if rel.get('Type', '').endswith('/slide'): rels.remove(rel)
        next_id = max(int(x.get('Id')[3:]) for x in rels) + 1
        types = E.fromstring(files['[Content_Types].xml'])
        for t in list(types):
            if t.get('PartName', '').startswith('/ppt/slides/'): types.remove(t)
        for number, (slide, relationships) in enumerate(self.pages, 1):
            rid = f'rId{next_id + number}'
            E.SubElement(ids, q('p:sldId'), {'id': str(255+number), q('r:id'): rid})
            E.SubElement(rels, '{'+REL+'}Relationship', Id=rid, Type=NS['r']+'/slide', Target=f'slides/slide{number}.xml')
            E.SubElement(types, '{'+CT+'}Override', PartName=f'/ppt/slides/slide{number}.xml', ContentType='application/vnd.openxmlformats-officedocument.presentationml.slide+xml')
            files[f'ppt/slides/slide{number}.xml'] = xml(slide)
            files[f'ppt/slides/_rels/slide{number}.xml.rels'] = xml(relationships)
        for ext, mime in (('png','image/png'),('jpg','image/jpeg')):
            if not any(t.get('Extension') == ext for t in types):
                E.SubElement(types,'{'+CT+'}Default',Extension=ext,ContentType=mime)
        for name, data in self.images.items(): files['ppt/media/'+name] = data
        if self.charts:
            E.SubElement(types,'{'+CT+'}Default',Extension='xlsx',ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        for number,(chart,workbook) in enumerate(self.charts,1):
            files[f'ppt/charts/chart{number}.xml']=chart
            files[f'ppt/embeddings/quantity{number}.xlsx']=workbook
            relationships=E.Element('Relationships',nsmap={None:REL})
            E.SubElement(relationships,'Relationship',Id='rId1',Type=NS['r']+'/package',Target=f'../embeddings/quantity{number}.xlsx')
            files[f'ppt/charts/_rels/chart{number}.xml.rels']=xml(relationships)
            E.SubElement(types,'{'+CT+'}Override',PartName=f'/ppt/charts/chart{number}.xml',ContentType='application/vnd.openxmlformats-officedocument.drawingml.chart+xml')
        files['ppt/presentation.xml'] = xml(presentation)
        files['ppt/_rels/presentation.xml.rels'] = xml(rels)
        files['[Content_Types].xml'] = xml(types)
        core = E.fromstring(files['docProps/core.xml'])
        values = {'title': document['metadata']['title'], 'creator': document['metadata'].get('author',''),
                  'lastModifiedBy': document['metadata'].get('author',''),
                  'created': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                  'modified': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
        for child in core: child.text = values.get(E.QName(child).localname, '')
        files['docProps/core.xml'] = xml(core)
        app = E.fromstring(files['docProps/app.xml'])
        for child in list(app):
            if E.QName(child).localname == 'Slides': child.text = str(len(self.pages))
            elif E.QName(child).localname in ('HeadingPairs','TitlesOfParts'):
                app.remove(child)  # no stale reference slide titles in document metadata
        files['docProps/app.xml'] = xml(app)
        out = io.BytesIO()
        with ZipFile(out, 'w', ZIP_DEFLATED) as archive:
            for name, data in files.items(): archive.writestr(name, data)
        return out.getvalue()


def render_report_pptx(project, document, resolve_source, *, pages=None, link_base=None, storage_id=None, include_sources=True):
    """Production entry point. Explicit site templates keep their existing override behaviour."""
    document = validate_document(project, document)
    if load_settings().pptx_template:
        from .pptx import render_pptx
        pages = pages if pages is not None else build_pages(project, document, resolve_source)
        return render_pptx(pages, document, link_base=link_base, storage_id=storage_id)
    return build_posco_deck(project, document, resolve_source, pages=pages, link_base=link_base,
                            storage_id=storage_id, include_sources=include_sources).finish(document)


def build_posco_deck(project, document, resolve_source, *, pages=None, link_base=None,
                     storage_id=None, include_sources=True, image_renderer=None):
    """One filled OOXML scene for PPTX and its browser preview; no Office installation needed."""
    document = validate_document(project, document)
    image_renderer = image_renderer or render_panel_png
    deck = Deck()
    entries = [e for e in document['samples'] if e['included']]
    samples = {s['id']: s for s in project['samples']}
    labels = document['phase_classes']['labels']
    source_images, raw_images = {}, {}

    def classification(sample, phase):
        return next((v['class'] for v in labels if v['sample_id'] == sample['id'] and v['phase'] == phase_ref(phase)), None)

    def put(slide, name, content, size=10, edit=None, *, bold=False):
        target = named(slide, name)[0]
        deck.mark(target, edit)
        fit_text(target, content, size=size, bold=bold)

    def image(sample, role, entry):
        if not entry.get('include_' + role, False): return None
        key = (sample['id'], role)
        if key in source_images: return source_images[key]
        record = next((r for r in sample['sources'] if r['role'] == role), None)
        if record is None: return None
        data = resolve_source(record['id'])
        if hashlib.sha256(data).hexdigest() != record['sha256']:
            raise ValueError('등록 후 원본 이미지가 변경되었습니다.')
        source_images[key] = data
        return data

    # Only the three supplied page layouts belong in the default PPT/preview.
    # Do not render the legacy cover/figure/table/appendix pages just to discard them.
    summary_image = None
    if document['figure']['include']:
        panel=next((p for p in build_plan(project,document)['panels'] if p['curves']),None)
        # The reference's overview is a clean sample comparison, not a second phase panel.
        # Only display annotations change; original coordinates/intensities/offsets stay intact.
        if panel:
            summary_panel=dict(panel,show_reference=False,show_markers=False,sample_legend=True)
            summary_image=image_renderer(summary_panel,128,91)
    selected = [e for e in entries if e['summary_included']]
    for start in range(0, max(1,len(selected)),3):
        slide = deck.slide(0)
        deck.title(slide,SUMMARY_TITLE + (' (계속)' if start else ''))
        if summary_image:
            deck.picture(slide,summary_image,(15.7,32.7,128,91),'시료 비교 그래프',edit=dict(tab='figure'))
        else: deck.text(slide,'비교 그래프를 보고서에 포함하지 않았습니다.',(18,70,124,16),size=10)
        group = selected[start:start+3]
        deck.page_info[slide]['navigation_title'] = SUMMARY_TITLE + ' · ' + ', '.join(e['display_name'] for e in group)
        for index in range(3):
            slot = 11 + index*5
            if index >= len(group):
                for offset in range(4): put(slide,f'Text {slot+offset}','')
                continue
            entry=group[index]; sample=samples[entry['sample_id']]
            put(slide,f'Text {slot}','샘플명: '+entry['display_name'],11.5,dict(tab=entry['sample_id'],field='display_name'),bold=True)
            for offset, cls in enumerate(('major','trace','presumed'),1):
                names=[p['name'] for p in sample['phases'] if classification(sample,p)==cls]
                value='; '.join(names) or '—'
                if cls=='major' and any(classification(sample,p) is None for p in sample['phases']):
                    value += ' (미분류는 상 동정 결과 참조)'
                put(slide,f'Text {slot+offset}',CLASS_NAMES[cls]+': '+value,10,dict(tab='classes'))
        note = document['summary'].strip() or '\n'.join(
            entry['display_name']+': '+text for entry in group for text,_ in remarks(entry)) or '—'
        # Use the whole remarks frame, not the original single-line placeholder.
        place(named(slide,'Text 27')[0],(160.02,143.51,97.36,24))
        deck.mark(named(slide,'Text 27')[0],dict(tab='overview',field='summary'))
        put(slide,'Text 27',note,11,dict(tab='overview',field='summary'))
    # Detailed pages: WPPF alongside its matching sample, or two phase-only samples per page.
    plain=[]
    def details(batch, quantitative, starts=None):
        starts = starts or [0] * len(batch)
        table_sets = [phase_table_data(samples[e['sample_id']], classification) for e in batch]
        row_sets = [rows for _, rows in table_sets]
        # A WPPF result is shown once per sample. Remaining phase rows use layout 3.
        count = 1 if quantitative else max(len(rows)-offset for rows, offset in zip(row_sets, starts))
        source_columns = list(zip(batch, starts, table_sets))
        for step in range(0,count,3):
            active = [(entry, offset+step, table) for entry, offset, table in source_columns
                      if offset+step < len(table[1])]
            batch = [entry for entry, _, _ in active]
            page_starts = [offset for _, offset, _ in active]
            table_sets = [table for _, _, table in active]
            row_sets = [rows for _, rows in table_sets]
            slide=deck.slide(1 if quantitative else 2, kind='sample', sample_id=batch[0]['sample_id'])
            continued = not quantitative and any(page_starts)
            deck.title(slide,(QUANTITY_TITLE if quantitative else PHASE_TITLE)+(' (계속)' if continued else ''))
            deck.page_info[slide]['navigation_title'] = (
                ('상 동정 & WPPF 정량' if quantitative else '상 동정') + ' · ' + ', '.join(
                    e['display_name'] + (f' (상 {offset+1}–{min(offset+3,len(rows))}/{len(rows)})'
                                        if len(rows)>3 else '')
                    for e, offset, rows in zip(batch, page_starts, row_sets)))
            frames=named(slide,'Match frame'); tables=named(slide,'Phase table')
            # Fill the original horizontal link pills; never retain reference URLs.
            link_frames=named(slide,'Source link frame')
            link_names=('Text 9','Text 11','Text 13') if quantitative else ('Text 12','Text 14','Text 16','Text 18','Text 20','Text 22')
            for index in range(1 if quantitative else 2):
                entry=batch[index] if index<len(batch) else None
                links=source_links(samples[entry['sample_id']]) if entry and include_sources else []
                by_label={link['label']:link for link in links}
                for j,label in enumerate(('상매칭 Report','정량(WPPF) Report','Measured Data (TXT)')):
                    k=index*3+j;shape=named(slide,link_names[k])[0]
                    if label not in by_label:
                        remove(shape);remove(link_frames[k]);continue
                    link=by_label[label]
                    caption='WPPF Report' if label=='정량(WPPF) Report' else label
                    set_text(shape,caption+' ↗',size=8,color='0A7489',minimum=6)
                    shape.find('p:txBody/a:p/a:pPr',NS).set('algn','ctr')
                    rid=deck.rel(slide,'hyperlink',link_target(link,link_base,storage_id),True)
                    for prop in shape.findall('.//a:rPr',NS):
                        E.SubElement(prop,q('a:hlinkClick'),{q('r:id'):rid})
            for index, entry in enumerate(batch):
                sample=samples[entry['sample_id']]
                start = page_starts[index]
                put(slide,'Text 5' if index==0 else 'Text 8',entry['display_name']+' - 상 동정 (Phase Identification) 결과',12,dict(tab=entry['sample_id'],field='display_name'),bold=True)
                frame=frames[index]; x,y,w,h=box(frame)
                source=image(sample,'phase_image',entry)
                if source: deck.picture(slide,source,(x+2,y+2,w-4,h-4),entry['display_name']+' 상매칭',edit=dict(tab=entry['sample_id'],field='phase_caption'))
                else:
                    # Same physical RAW values and reference rows as the viewer's rendering path.
                    key=(entry['sample_id'],w,h)
                    if key not in raw_images:
                        single=deepcopy(document)
                        for e in single['samples']: e['included']=e['sample_id']==entry['sample_id']
                        single['figure']['include']=True; single['figure']['groups']=[]
                        panels=build_plan(project,single)['panels']
                        panel=next((p for p in panels if p['curves']),None)
                        raw_images[key]=image_renderer(panel,w-4,h-4) if panel else None
                    if raw_images[key]: deck.picture(slide,raw_images[key],(x+2,y+2,w-4,h-4),entry['display_name']+' RAW',edit=dict(tab=entry['sample_id'],field='phase_caption'))
                shape=tables[index]; table=shape.find('.//a:tbl',NS)
                deck.mark(shape,dict(tab='classes'))
                original=table.findall('a:tr',NS); prototype=deepcopy(original[1])
                for row in original[1:]: table.remove(row)
                widths=[int(c.get('w'))/MM for c in table.find('a:tblGrid',NS)]
                header = original[0].findall('a:tc',NS)[2]
                fit_text(header, table_sets[index][0], dimensions=(widths[2]-1,8.6), size=9.5, color='FFFFFF', bold=True)
                rows=row_sets[index][start:start+3]
                if not rows: rows=[('이전 페이지에 수록','—','—')]
                for values in rows:
                    row=deepcopy(prototype);row.set('h',str(9*MM))
                    for column,(cell,value,cw) in enumerate(zip(row.findall('a:tc',NS),values,widths)):
                        fit_text(cell,value,dimensions=(cw-1,8.6),size=9.5,bold=column==1)
                        if column==1: subscript_formula(cell)
                    table.append(row)
                x0,y0,w0,h0=box(shape)
                place(shape,(x0,y0,w0,sum(int(row.get('h')) for row in table.findall('a:tr',NS))/MM))
                if len(row_sets[index]) > 3 and start < len(row_sets[index]):
                    end=min(start+3,len(row_sets[index]))
                    caption=f'상 목록 {start+1}–{end} / {len(row_sets[index])}'
                    if end<len(row_sets[index]): caption+=' · 나머지는 상 동정 결과에 계속'
                    deck.text(slide,caption,(x0,y0+box(shape)[3]+1,w0,5),size=7.5,
                              name='Phase continuation',edit=dict(tab='classes'))
                if quantitative:
                    put(slide,'Text 14',entry['display_name']+' - WPPF 정량 (Phase Fraction) 결과',12,dict(tab=entry['sample_id'],field='display_name'),bold=True)
                    bx,by,bw,bh=box(named(slide,'Pie frame')[0])
                    quant_image=image(sample,'quantity_image',entry)
                    pie_drawn=False
                    if quant_image: deck.picture(slide,quant_image,(bx+2,by+2,bw-4,bh-(22 if sample.get('quantities') else 4)),entry['display_name']+' 정량 원본',edit=dict(tab=entry['sample_id'],field='quantity_caption'))
                    else: pie_drawn=deck.pie(slide,sample.get('quantities',[]),(bx+3,by+5,bw-6,bh-20),edit=dict(tab=entry['sample_id'],field='quantity_caption'))
                    if not quant_image and not pie_drawn:
                        text='\n'.join(f"{v['phase']}: {v['raw']} {v['unit']}" for v in sample.get('quantities',[])) or quantity_status(sample)
                        shape=deck.text(slide,'',(bx+4,by+4,bw-8,bh-8),size=11)
                        fit_text(shape,text,size=11)
                    if (pie_drawn or quant_image) and sample.get('quantities'):
                        caption=''
                        if pie_drawn:
                            total=sum(v['value'] for v in sample['quantities'])
                            caption=f"제공된 정량값의 상대 비율 (합계 {total:g} {sample['quantities'][0]['unit']})\n"
                        caption+='; '.join(f"{v['phase']}: {v['raw']} {v['unit']}" for v in sample['quantities'])
                        shape=deck.text(slide,'',(bx+3,by+bh-17,bw-6,15),size=8)
                        fit_text(shape,caption,size=8)
            if not quantitative and len(batch)==1:
                for shape in list(slide.find('p:cSld/p:spTree',NS)):
                    if shape in named(slide,'Text 2') or shape in named(slide,'Page number'): continue
                    try:
                        if box(shape)[0]>140: remove(shape)
                    except (TypeError,IndexError,AttributeError): pass
    # Match the supplied sequence: summary, phase+WPPF, then paired phase-only samples.
    quantitative_entries=[]
    for entry in entries:
        sample=samples[entry['sample_id']]
        has_quantity=bool(sample.get('quantities') or sample.get('reports',{}).get('wppf_report') or entry.get('include_quantity_image'))
        if has_quantity:
            quantitative_entries.append(entry)
        else:
            plain.append(entry)
    for entry in quantitative_entries: details([entry],True)
    continued = [e for e in quantitative_entries if len(samples[e['sample_id']]['phases'])>3]
    for start in range(0,len(continued),2):
        batch = continued[start:start+2]
        details(batch,False,[3]*len(batch))
    for start in range(0,len(plain),2): details(plain[start:start+2],False)
    # Request metadata, full notes and exclusions remain in the editable document.
    # Never append legacy information/figure/table/notes pages to this template.
    deck.number_pages()
    return deck

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
from ahn.report_cover import shape_xml
from ahn.report_traceability import cover_shapes, traceability_shapes, brief

from ..config import load_settings
from ..core.figure import build_plan
from ..core.parsing.values import central
from ..core.report import validate_document
from .figure import render_panel_png
from .fonts import wrap_lines
from .items import link_target, viewer_target
from .package_names import package_paths
from .link_audit import check_ppt_links, read_linked_source, report_sources
from .layout import build_pages, source_links
from .sample_page import quantity_status
from .summary_layout import classifier, notice, summary_pages

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


def set_text(shape, text, *, size=10, color='102A35', bold=False, dimensions=None, minimum=8, leading=1.25):
    """Fit complete editable text. Return False instead of silently clipping a long value."""
    body = shape.find('p:txBody', NS)
    if body is None:
        body = shape.find('a:txBody', NS)
    width, height = dimensions or box(shape)[2:]
    chosen, lines = size, []
    while chosen >= minimum:
        lines = wrap_lines(str(text), max(1, width - 1.2), chosen, bold)
        if len(lines) * chosen * leading / 2.83465 <= height - .2:
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
        E.SubElement(spacing, q('a:spcPts'), val=str(round(chosen * leading * 100)))
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


def phase_table_data(sample, classification, *, revised=False):
    """Use the actual common database as header, never the reference's example DB."""
    phases = sample['phases']
    databases = {str(p.get('db') or '').strip() for p in phases}
    common = next(iter(databases)) if len(databases) == 1 and '' not in databases else None
    rows = [((f"{p['name']} [{CLASS_NAMES[classification(sample, p)]}]" if revised else
              f"[{CLASS_NAMES[classification(sample, p)]}] {p['name']}"), p.get('formula') or '—',
             (str(p.get('card') or '—') if common else
              ' '.join(str(v) for v in (p.get('db'), p.get('card')) if v) or '—')) for p in phases]
    return ('# ICDD CARD ID' if revised else common or 'DB / Card No.'), rows or [('상 목록 미제공', '—', '—')]


def fit_phase_table(shape, database, rows, frame, heading, bottom):
    """Keep every phase on its graph's slide as an editable, fitted native table.

    Use cell spacing first. If 8pt text still needs more room, borrow height
    from the graph (preserving its aspect ratio when placed), then reduce type.
    Never abbreviate scientific names/formulas/cards or split off phase pages.
    """
    table = shape.find('.//a:tbl', NS)
    original = table.findall('a:tr', NS)
    header, prototype = deepcopy(original[0]), deepcopy(original[1])
    widths = [int(c.get('w')) / MM for c in table.find('a:tblGrid', NS)]
    headers = [''.join(c.xpath('.//a:t/text()', namespaces=NS)) for c in header.findall('a:tc', NS)]
    headers[2] = database
    values = [headers] + rows
    x, y, w, _ = box(shape)
    available = bottom - y

    def metrics(size):
        # Explicit margins match both PowerPoint and the shared HTML/PDF scene.
        pad = size * .025
        heights = []
        for index, cells in enumerate(values):
            font = max(7, size) if index == 0 else size
            lines = max(len(wrap_lines(str(value), width - 3.2, font, index == 0 or col == 1))
                        for col, (value, width) in enumerate(zip(cells, widths)))
            minimum = (7.5 if index == 0 else 9) if len(rows) <= 3 else 0
            heights.append(max(minimum, lines * font * 1.15 / 2.83465 + 2 * pad + .22))
        return pad, heights

    # Retain the template graph at ordinary row counts; expand only for dense tables.
    needed = sum(metrics(8)[1])
    gx, gy, gw, gh = box(frame)
    extra = min(max(0, gh - 44), max(0, needed - available))
    if extra:
        place(frame, (gx, gy, gw, gh - extra))
        hx, hy, hw, hh = box(heading)
        place(heading, (hx, hy - extra, hw, hh))
        y -= extra
        available += extra
    for quarter in range(38, 3, -1):
        size = quarter / 4
        pad, heights = metrics(size)
        if sum(heights) <= available:
            break
    else:
        raise ValueError('결정상 표의 내용이 한 페이지에 표시할 수 있는 양을 초과합니다. 상 이름과 항목을 확인하세요.')
    for row in original:
        table.remove(row)
    for index, (cells, height) in enumerate(zip(values, heights)):
        row = deepcopy(header if index == 0 else prototype)
        row.set('h', str(round(height * MM)))
        font = max(7, size) if index == 0 else size
        for col, (cell, value, width) in enumerate(zip(row.findall('a:tc', NS), cells, widths)):
            props = cell.find('a:tcPr', NS)
            for key, margin in (('marL', 1), ('marR', 1), ('marT', pad), ('marB', pad)):
                props.set(key, str(round(margin * MM)))
            props.set('anchor', 'ctr')
            if not set_text(cell, value, dimensions=(width - 2, height - 2 * pad),
                            size=font, minimum=font, bold=index == 0 or col == 1,
                            leading=1.15,
                            color='FFFFFF' if index == 0 else '102A35'):
                raise ValueError('결정상 표의 셀에 전체 내용을 배치하지 못했습니다.')
            if index and col == 1:
                subscript_formula(cell)
        table.append(row)
    place(shape, (x, y, w, sum(int(row.get('h')) for row in table.findall('a:tr', NS)) / MM))


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
        self.required_links = {}

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

    def frame(self, slide, bounds, *, name='Sample frame'):
        shape = deepcopy(named(self.templates[0], 'Sample frame')[0])
        place(shape, bounds)
        return self.append(slide, shape, name)

    def table(self, slide, headers, rows, bounds, *, name='Quantity table', edit=None, size=9, allow_empty=False):
        """Use the native template table, preserving every original value token."""
        x,y,w,max_h = bounds
        shape = deepcopy(named(self.templates[1], 'Phase table')[0])
        table = shape.find('.//a:tbl', NS)
        proto = deepcopy(table.findall('a:tr', NS)[1])
        head = deepcopy(table.findall('a:tr', NS)[0])
        for row in list(table.findall('a:tr', NS)): table.remove(row)
        widths = [w*.63,w*.37] if len(headers)==2 else [w/len(headers)]*len(headers)
        grid = table.find('a:tblGrid', NS); grid.clear()
        for width in widths: E.SubElement(grid,q('a:gridCol'),w=str(round(width*MM)))
        used, count = 0, 0
        for index, values in enumerate([headers]+rows):
            row = deepcopy(head if index==0 else proto)
            for cell in row.findall('a:tc',NS)[len(widths):]: row.remove(cell)
            lines = [wrap_lines(str(value),width-6.2,size,index==0) for value,width in zip(values,widths)]
            height = max(8,max(len(v) for v in lines)*size*1.25/2.83465+3.3)
            if used+height > max_h: break
            row.set('h',str(round(height*MM)))
            for cell,value,width in zip(row.findall('a:tc',NS),values,widths):
                if not set_text(cell,value,dimensions=(width-5,height-3),size=size,minimum=size,
                                bold=index==0,color='FFFFFF' if index==0 else '102A35'):
                    raise ValueError('정량 표 내용을 표시할 공간이 부족합니다.')
            table.append(row);used+=height
            if index: count+=1
        if rows and not count:
            if allow_empty: return 0  # the wider continuation page retains the entire row
            raise ValueError('정량 표의 상 이름이 너무 깁니다. 원본을 확인하고 표시명을 줄여 주세요.')
        place(shape,(x,y,w,used)); self.append(slide,shape,name);self.mark(shape,edit)
        return count

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
        # Long names remain complete in the table; do not squeeze a pie under an
        # unreadable legend or let its labels cover adjacent report sections.
        if sum(len(wrap_lines(v['phase'],bounds[2]-6,9)) for v in quantities)*4 > bounds[3]-25:
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
            if self.page_info[slide]['kind'] not in {'cover','traceability'}:
                shape = named(slide, 'Page number')[0]
                set_text(shape, str(number), size=10, color='6B7F88')
                shape.find('p:txBody/a:p/a:pPr', NS).set('algn', 'r')
            # Keep the source artwork visible even in viewers that suppress inherited
            # master pictures on populated slides. Do not redraw or replace the logo.
            if not named(slide, 'RIST logo'):
                master = E.fromstring(self.files['ppt/slideMasters/slideMaster1.xml'])
                logo = named(master, 'Picture 14')[0]
                blip = logo.find('p:blipFill/a:blip', NS)
                rels = E.fromstring(self.files['ppt/slideMasters/_rels/slideMaster1.xml.rels'])
                target = next(r.get('Target') for r in rels if r.get('Id') == blip.get(q('r:embed')))
                self.picture(slide,self.files['ppt/'+target.removeprefix('../')],box(logo),'RIST logo')
                place(named(slide,'RIST logo')[0],box(logo))

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


def render_report_pptx(project, document, resolve_source, *, pages=None, link_base=None, storage_id=None, include_sources=True, delivery=False, asset_root=None, viewer_state=None, views=None):
    """Production entry point. Explicit site templates keep their existing override behaviour."""
    document = validate_document(project, document)
    if delivery:
        # Recipient PPTs must work when detached from the server and the ZIP.
        # Canonical local targets become embedded OLE buttons on these same pages.
        link_base = storage_id = None
    paths = package_paths(project)
    custom_template = bool(load_settings().pptx_template)
    expected = {}
    expected_pages = None
    included = {entry['sample_id'] for entry in document['samples'] if entry['included']}
    if include_sources:
        for sample in project['samples']:
            if sample['id'] in included:
                for link in source_links(sample, paths):
                    expected[link_target(link, link_base, storage_id)] = link['filename']
                if not custom_template:
                    expected[viewer_target(sample['id'], link_base, storage_id, paths=paths)] = sample['name'] + ' 그래프'
        if not custom_template:
            expected[viewer_target(link_base=link_base, storage_id=storage_id)] = '전체 비교 그래프'
        # A standalone PPT links to the server, so verify its originals before delivery.
        # ZIP export checks these bytes while packing them, avoiding a second full read.
        if link_base and storage_id:
            for record in report_sources(project, document):
                read_linked_source(record, resolve_source)
    if custom_template:
        from .editor import check_template
        check_template(document)
        from .pptx import render_pptx
        pages = pages if pages is not None else build_pages(project, document, resolve_source)
        if not include_sources:
            pages = [dict(page, items=[item for item in page['items'] if item['type'] != 'link']) for page in pages]
        content = render_pptx(pages, document, link_base=link_base, storage_id=storage_id)
    else:
        deck = build_posco_deck(project, document, resolve_source, pages=pages, link_base=link_base,
                               storage_id=storage_id, include_sources=include_sources, asset_root=asset_root)
        content = deck.finish(document)
        expected_pages = {f'ppt/slides/slide{index}.xml': deck.required_links.get(slide, set())
                          for index, (slide, _) in enumerate(deck.pages, 1)}
        # Pages deliberately removed by the author no longer require their links.
        visible_links = set().union(*expected_pages.values())
        expected = {key:value for key,value in expected.items() if key in visible_links}
    check_ppt_links(content, expected, expected_pages)
    from ...report.pptx_protection import validate_protection, protect_pptx
    protection = validate_protection(document.get('pptxProtection'))
    if delivery and (not protection or include_sources):
        from .delivery_pptx import flatten_pptx, custom_template_pdf
        from .posco_preview import deck_pages
        from .pdf import render_pdf
        pdf = custom_template_pdf(content) if custom_template else render_pdf(deck_pages(deck), document)
        if not protection:
            content = flatten_pptx(content, pdf)
            check_ppt_links(content, expected, expected_pages)
    if delivery and include_sources:
        from .offline_pptx import make_offline
        content = make_offline(content, project, document, resolve_source,
                               preview_pdf=pdf, viewer_state=viewer_state, views=views)
    if delivery and protection:
        # Apply after embedding; keep native report objects so a correct modify
        # password enables editing without requiring a password to read the PPT.
        content = protect_pptx(content, protection)
    return content


def build_posco_deck(project, document, resolve_source, *, pages=None, link_base=None,
                     storage_id=None, include_sources=True, image_renderer=None, asset_root=None):
    """One filled OOXML scene for PPTX and its browser preview; no Office installation needed."""
    document = validate_document(project, document)
    paths = package_paths(project)
    image_renderer = image_renderer or render_panel_png
    deck = Deck()
    entries = [e for e in document['samples'] if e['included']]
    samples = {s['id']: s for s in project['samples']}
    source_images, raw_images = {}, {}
    classification = classifier(document)

    def put(slide, name, content, size=10, edit=None, *, bold=False):
        target = named(slide, name)[0]
        deck.mark(target, edit)
        fit_text(target, content, size=size, bold=bold)

    def image(sample, role, entry):
        # Phase-identification panels show the uploaded evidence whenever present.
        # Old reports may have include_phase_image=False; that must not silently
        # replace the source artwork with the viewer's reconstructed RAW plot.
        if role != 'phase_image' and not entry.get('include_' + role, False): return None
        key = (sample['id'], role)
        if key in source_images: return source_images[key]
        record = next((r for r in sample['sources'] if r['role'] == role), None)
        if record is None: return None
        data = resolve_source(record['id'])
        if hashlib.sha256(data).hexdigest() != record['sha256']:
            raise ValueError('등록 후 원본 이미지가 변경되었습니다.')
        source_images[key] = data
        return data

    # Common title + traceability pages; all supplied XRD body layouts stay intact.
    meta = document['metadata']
    fields = dict(requestNumber='report_number',experimentCode='experiment_code',
        equipmentCode='equipment_code',operatorId='operator_id',completionDate='date',
        client='client',requester='requester',receivedDate='received_date',analysisPeriod='analysis_period',
        analyst='author',reviewer='reviewer',purpose='purpose',equipmentName='equipment_name',
        measuredDate='measured_date',conditions='conditions',preparation='preparation',environment='environment')
    values = {key:meta.get(field,'') for key,field in fields.items()}
    included = [entry for entry in document['samples'] if entry['included']]
    values.update(sampleNames=brief(e['display_name'] for e in included),
        sourceSummary=brief(r['filename'] for e in included for r in samples[e['sample_id']]['sources']),
        recordId=project.get('storage_id') or project['id'],
        revision=f"보고서 v{document['revision']} · 분석 검토 v{document.get('analysis_revision',0)}")
    size = E.fromstring(deck.files['ppt/presentation.xml']).find('p:sldSz', NS)
    for kind,layout in (('cover',cover_shapes),('traceability',traceability_shapes)):
        page = deck.slide(blank=True,kind=kind)
        deck.page_info[page]['navigation_title'] = '표지' if kind=='cover' else '분석 이력 요약'
        for shape in list(page.find('p:cSld/p:spTree',NS)):
            if E.QName(shape).localname not in ('nvGrpSpPr','grpSpPr'): remove(shape)
        for spec in layout('XRD',meta['title'],values,int(size.get('cx')),int(size.get('cy'))):
            shape = E.fromstring(shape_xml(spec))
            point_size = math.ceil(spec['font_size']/50)*.5
            if not set_text(shape,'\n'.join(spec['text']),size=point_size,minimum=5,
                    bold=spec.get('bold',False),color=spec['color'],dimensions=(spec['cx']/MM,spec['cy']/MM)):
                raise ValueError(f'표지·이력 항목({spec["name"]})이 너무 깁니다. 내용을 줄이고 상세 내용은 본문에 작성하세요.')
            name = 'Title' if spec['name']=='Experiment banner title' else spec['name']
            deck.append(page,shape,name)
            if name=='Report title':
                deck.mark(shape,dict(tab='overview',field='title'))
            elif name.startswith(('Trace ','Cover ')) and name[6:] in fields:
                key = fields[name[6:]]
                deck.mark(shape,dict(tab='overview',field='transfer' if key in
                    {'experiment_code','equipment_code','operator_id'} else key))

    # Adaptive summary: two columns of reviewed phase information, never 3/4 fixed samples.
    summary_image = None
    if document['figure']['include']:
        panel=next((p for p in build_plan(project,document)['panels'] if p['curves']),None)
        # The reference's overview is a clean sample comparison, not a second phase panel.
        # Only display annotations change; original coordinates/intensities/offsets stay intact.
        if panel:
            summary_panel=dict(panel,show_reference=False,show_markers=False,sample_legend=True)
            summary_image=image_renderer(summary_panel,128,91)
    selected = [e for e in entries if e['summary_included']]
    for page_index, cards in enumerate(summary_pages(selected,samples,classification)):
        slide = deck.slide(0,blank=True)
        deck.title(slide,SUMMARY_TITLE + (' (계속)' if page_index else ''))
        group = list({c['entry']['sample_id']:c['entry'] for c in cards}.values())
        statement, disclaimer = notice(group,samples,classification)
        note = statement+'\n\n'+disclaimer if statement else ''
        note_h = max(27, len(wrap_lines(note,120.8,8.5))*8.5*1.25/2.83465+5)
        extra_h = note_h-27
        deck.text(slide,'측정 결과',(8,25,128,7),size=12,bold=True,color='0A7489',edit=dict(tab='figure'))
        deck.text(slide,'샘플별 결정상 (Phase) 정보',(143,25,125,7),size=12,bold=True,color='0A7489',edit=dict(tab='classes'))
        deck.frame(slide,(8,34,128,94-extra_h),name='Overlay frame')
        if summary_image:
            deck.picture(slide,summary_image,(10,36,124,90-extra_h),'시료 비교 그래프',edit=dict(tab='figure'))
        else: deck.text(slide,'비교 그래프를 보고서에 포함하지 않았습니다.',(12,70,120,16),size=10)
        viewer_href = viewer_target(link_base=link_base, storage_id=storage_id)
        deck.required_links[slide] = {viewer_href} if include_sources else set()
        # include_sources=False is also used for detached, non-linked report output.
        if include_sources:
            deck.frame(slide,(47,130-extra_h,53,8),name='Viewer link frame')
            link_shape=deck.text(slide,'데이터 확인하기 ↗',(49,131-extra_h,49,6),size=9,color='0A7489',href=viewer_href)
            link_shape.find('p:txBody/a:p/a:pPr',NS).set('algn','ctr')
        deck.page_info[slide]['navigation_title'] = SUMMARY_TITLE + ' · ' + ', '.join(e['display_name'] for e in group)
        for card in cards:
            entry=card['entry'];x=143+64*card['column'];y=34+card['top']
            deck.frame(slide,(x,y,61,card['height']))
            title='\n'.join(card['title'])
            deck.text(slide,title,(x+2,y+2,57,len(card['title'])*4.5+.5),size=10,bold=True,
                      edit=dict(tab=entry['sample_id'],field='display_name'))
            ty=y+4+len(card['title'])*4.5
            if card['continued']:
                deck.text(slide,'(계속)',(x+2,ty,57,4),size=8);ty+=4
            for line,bold in card['lines']:
                deck.text(slide,line,(x+2,ty,57,4),size=8.5,bold=bold,edit=dict(tab='classes'));ty+=4
        deck.text(slide,'특이사항',(8,140-extra_h,128,7),size=12,bold=True,color='0A7489',edit=dict(tab='classes'))
        deck.frame(slide,(8,148-extra_h,128,note_h),name='Remarks box')
        if statement:
            deck.text(slide,note,(11,150-extra_h,122,note_h-4),size=8.5,edit=dict(tab='classes'))
    # Detailed pages: WPPF alongside its matching sample, or two phase-only samples per page.
    plain=[]
    quantity_overflow=[]
    def details(batch, quantitative):
        table_sets = [phase_table_data(samples[e['sample_id']], classification, revised=quantitative) for e in batch]
        row_sets = [rows for _, rows in table_sets]
        slide=deck.slide(1 if quantitative else 2, kind='sample', sample_id=batch[0]['sample_id'])
        deck.page_info[slide]['editor_samples'] = [e['sample_id'] for e in batch]
        # Record every required destination independently of the visible link shapes.
        deck.required_links[slide] = {
            target for entry in batch for target in
            [viewer_target(entry['sample_id'], link_base, storage_id, paths=paths)] +
            [link_target(link, link_base, storage_id) for link in source_links(samples[entry['sample_id']], paths)]
        } if include_sources else set()
        deck.title(slide, QUANTITY_TITLE if quantitative else PHASE_TITLE)
        deck.page_info[slide]['navigation_title'] = (
            ('상 동정 & WPPF 정량' if quantitative else '상 동정') + ' · ' + ', '.join(
                e['display_name'] + (f' (결정상 {len(rows)}개)' if len(rows)>3 else '')
                for e, rows in zip(batch, row_sets)))
        frames=named(slide,'Match frame'); tables=named(slide,'Phase table')
        # Fill the original horizontal link pills; never retain reference URLs.
        link_frames=named(slide,'Source link frame')
        link_names=('Text 9','Text 11','Text 13') if quantitative else ('Text 12','Text 14','Text 16','Text 18','Text 20','Text 22')
        for index in range(1 if quantitative else 2):
            entry=batch[index] if index<len(batch) else None
            links=source_links(samples[entry['sample_id']], paths) if entry and include_sources else []
            by_label={link['label']:link for link in links}
            left=12.7+129.5*index
            if entry and include_sources:
                bounds=(10.5,168,52,8) if quantitative else (left,162.4,48,8)
                frame=deck.append(slide,deepcopy(link_frames[index*3]),'Sample viewer link frame')
                place(frame,bounds)
                shape=deck.text(slide,'그래프(확대, 축소) 확인하기 ↗',bounds,size=8,color='0A7489',
                                name='Sample viewer link',
                                href=viewer_target(entry['sample_id'],link_base,storage_id,paths=paths))
                shape.find('p:txBody/a:bodyPr',NS).set('anchor','ctr')
                shape.find('p:txBody/a:p/a:pPr',NS).set('algn','ctr')
            for j,label in enumerate(('상매칭 Report','정량(WPPF) Report','Measured Data (TXT)')):
                k=index*3+j;shape=named(slide,link_names[k])[0]
                if label not in by_label:
                    remove(shape);remove(link_frames[k]);continue
                link=by_label[label]
                caption={'상매칭 Report':'상매칭 Report 열기','정량(WPPF) Report':'WPPF Report 열기',
                         'Measured Data (TXT)':'측정 데이터(TXT) 열기'}[label]
                if quantitative:
                    bounds=((65,168,43,8),(189,168,53,8),(111,168,48,8))[j]
                else:
                    bounds=((left+50.5,162.4,31,8),(left+45,173,48,7),(left+84,162.4,36,8))[j]
                place(shape,bounds);place(link_frames[k],bounds)
                set_text(shape,caption+' ↗',size=8,color='0A7489',minimum=6)
                shape.find('p:txBody/a:p/a:pPr',NS).set('algn','ctr')
                rid=deck.rel(slide,'hyperlink',link_target(link,link_base,storage_id),True)
                for prop in shape.findall('.//a:rPr',NS):
                    E.SubElement(prop,q('a:hlinkClick'),{q('r:id'):rid})
        for index, entry in enumerate(batch):
            sample=samples[entry['sample_id']]
            put(slide,'Text 5' if index==0 else 'Text 8',entry['display_name']+' - 상 동정 (Phase Identification) 결과',12,dict(tab=entry['sample_id'],field='display_name'),bold=True)
            frame=frames[index]
            shape=tables[index]
            deck.mark(shape,dict(tab='classes'))
            fit_phase_table(shape, table_sets[index][0], row_sets[index], frame,
                            named(slide,'Section label')[index], 166 if quantitative else 160.4)
            x,y,w,h=box(frame)
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
            if quantitative:
                put(slide,'Text 14',entry['display_name']+' - WPPF 정량 (Phase Fraction) 결과',12,dict(tab=entry['sample_id'],field='display_name'),bold=True)
                place(named(slide,'Pie frame')[0],(167.6,39.6,94.8,74.9))
                bx,by,bw,bh=box(named(slide,'Pie frame')[0])
                quant_image=image(sample,'quantity_image',entry)
                pie_drawn=False
                if quant_image: deck.picture(slide,quant_image,(bx+2,by+2,bw-4,bh-4),entry['display_name']+' 정량 원본',edit=dict(tab=entry['sample_id'],field='quantity_caption'))
                else: pie_drawn=deck.pie(slide,sample.get('quantities',[]),(bx+3,by+3,bw-6,bh-6),edit=dict(tab=entry['sample_id'],field='quantity_caption'))
                if not quant_image and not pie_drawn:
                    text='정량값은 아래 표에서 확인하세요.' if sample.get('quantities') else quantity_status(sample)
                    shape=deck.text(slide,'',(bx+4,by+4,bw-8,bh-8),size=11)
                    fit_text(shape,text,size=11)
                deck.text(slide,'결정상분율 (Phase Fraction)',(bx,117.3,bw,7),size=12,bold=True,color='0A7489')
                rows=[(v['phase'],f"{v.get('raw') or '확인 필요'} {v.get('unit') or ''}".strip())
                      for v in sample.get('quantities',[])]
                if rows:
                    units={v.get('unit') for v in sample['quantities']}
                    header='결정상분율 (wt%)' if units=={'wt%'} else '결정상분율 (원문 단위)'
                    used=deck.table(slide,['상 (Phase)',header],rows,(bx,126.5,bw,35),size=8.5,
                                    edit=dict(tab=entry['sample_id']),allow_empty=True)
                    if not used:
                        deck.text(slide,'긴 정량 표는 뒤쪽 상 동정 결과의 계속 페이지에 수록합니다.',
                                  (bx,128,bw,25),size=9)
                    if used<len(rows): quantity_overflow.append((entry,header,rows[used:]))
                    if pie_drawn:
                        total=sum(v['value'] for v in sample['quantities'])
                        deck.text(slide,f'원그래프: 제공값의 상대 비율 · 합계 {total:g} {next(iter(units))}',
                                  (bx,162,bw,4.5),size=7)
                else:
                    deck.text(slide,quantity_status(sample),(bx,128,bw,25),size=9)
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
    for start in range(0,len(plain),2): details(plain[start:start+2],False)
    for entry,header,rows in quantity_overflow:
        while rows:
            slide=deck.slide(2,blank=True,kind='sample',sample_id=entry['sample_id'])
            deck.title(slide,PHASE_TITLE+' (계속)')
            deck.text(slide,entry['display_name']+' · 결정상분율 표 (계속)',(14,29,245,12),size=12,bold=True)
            used=deck.table(slide,['상 (Phase)',header],rows,(14,45,245,122),size=10,
                            edit=dict(tab=entry['sample_id']))
            rows=rows[used:]
    deck.number_pages()
    from .editor import apply
    return apply(deck, document, asset_root)

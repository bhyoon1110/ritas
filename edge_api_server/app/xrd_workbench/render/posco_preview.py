"""Browser projection of our filled POSCO deck (not a second report layout).

Only the shape subset used by the bundled, trusted template is supported. Coordinates,
text wrapping, tables, images and edit targets come from the exact deck exported to PPTX.
The original master images are embedded locally; no Office, CDN or client-side rasterizer
is needed on the offline server or a low-resource Supermium PC.
"""
import base64
import io
import math
import posixpath
from html import escape

from lxml import etree as E
from PIL import Image

from .fonts import wrap_lines
from .posco_pptx import NS, MM, PIE_COLORS, box, named, build_posco_deck


def color(parent, path, default):
    node = parent.find(path + '/a:srgbClr', NS)
    return '#' + node.get('val') if node is not None else default


def relationships(files, part):
    path = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels')
    return {r.get('Id'): posixpath.normpath(posixpath.join(posixpath.dirname(part), r.get('Target')))
            for r in E.fromstring(files[path]) if r.get('TargetMode') != 'External'}


def svg_image(svg, bounds, label):
    x, y, w, h = bounds
    return dict(type='image', data=svg.encode(), mime='image/svg+xml', filename=label,
                x=x, y=y, w=w, h=h)


def image_item(data, bounds, label, crop=None):
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
        mime = 'image/jpeg' if image.format == 'JPEG' else 'image/png'
    x, y, w, h = bounds
    if crop is not None:
        left, top, right, bottom = [int(crop.get(k, '0')) / 100000 for k in ('l', 't', 'r', 'b')]
        uri = 'data:' + mime + ';base64,' + base64.b64encode(data).decode()
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="{width*left} {height*top} '
               f'{width*(1-left-right)} {height*(1-top-bottom)}" preserveAspectRatio="none">'
               f'<image width="{width}" height="{height}" xlink:href="{uri}"/></svg>')
        return svg_image(svg, bounds, label)
    return dict(type='image', data=data, mime=mime, filename=label, x=x, y=y, w=w, h=h)


def text_items(body, bounds, edit=None):
    x, y, w, h = bounds
    props = body.find('a:bodyPr', NS)
    inset = {key: int(props.get(key, '0')) / MM for key in ('lIns', 'rIns', 'tIns', 'bIns')}
    x += inset['lIns']; y += inset['tIns']
    w -= inset['lIns'] + inset['rIns']; h -= inset['tIns'] + inset['bIns']
    items, used = [], 0
    for paragraph in body.findall('a:p', NS):
        text = ''.join(paragraph.xpath('./a:r/a:t/text() | ./a:fld/a:t/text()', namespaces=NS))
        if not text: continue
        run = paragraph.find('a:r/a:rPr', NS)
        size = float(run.get('sz', '1000')) / 100 if run is not None else 10
        bold = run is not None and run.get('b') == '1'
        pp = paragraph.find('a:pPr', NS)
        before = paragraph.find('a:pPr/a:spcBef/a:spcPts', NS)
        after = paragraph.find('a:pPr/a:spcAft/a:spcPts', NS)
        used += float(before.get('val'))/100/2.83465 if before is not None else 0
        align = pp.get('algn', 'l') if pp is not None else 'l'
        space = paragraph.find('a:pPr/a:lnSpc/a:spcPts', NS)
        leading = (float(space.get('val'))/100 if space is not None else size*1.25) / 2.83465
        lines = [text] if props.get('wrap') == 'none' else wrap_lines(text, max(1,w), size, bold)
        item = dict(type='text', x=x, y=y+used, w=w, h=len(lines)*leading, lines=lines,
                    size=size, bold=bold, color=color(run,'a:solidFill','#102A35') if run is not None else '#102A35',
                    leading=leading, align={'r':'right','ctr':'center'}.get(align,'left'))
        # Generated formula paragraphs already carry the PPT's exact line breaks.
        # Preserve baseline offsets in HTML instead of flattening subscripts to plain text.
        if props.get('wrap') == 'none' and paragraph.find('a:r/a:rPr[@baseline]', NS) is not None:
            item['runs'] = []
            for part in paragraph.findall('a:r', NS):
                rp = part.find('a:rPr', NS)
                item['runs'].append(dict(text=part.findtext('a:t','',NS),
                    size=float(rp.get('sz','1000'))/100, baseline=int(rp.get('baseline','0'))/100000,
                    bold=rp.get('b')=='1', color=color(rp,'a:solidFill','#102A35')))
        if edit: item['edit'] = edit
        items.append(item); used += item['h']
        used += float(after.get('val'))/100/2.83465 if after is not None else 0
    anchor = props.get('anchor', 't')
    offset = max(0,h-used) * (.5 if anchor == 'ctr' else 1 if anchor == 'b' else 0)
    for item in items: item['y'] += offset
    return items


def pie_image(chart, bounds):
    """Same explicit series colors and unnormalised values as the native PPT chart/cache."""
    names = chart.xpath('.//c:strCache/c:pt/c:v/text()', namespaces=NS)
    values = [float(v) for v in chart.xpath('.//c:numCache/c:pt/c:v/text()', namespaces=NS)]
    w,h = bounds[2:]
    legend_h = len(names)*5
    radius = max(1,min(w/2-4,(h-legend_h)/2-3))
    cx,cy = w/2,legend_h+(h-legend_h)/2
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
           'font-family="Malgun Gothic,Arial,sans-serif" font-size="3.2">']
    angle, total = -math.pi/2, sum(values)
    for index,(name,value) in enumerate(zip(names,values)):
        fill = '#' + PIE_COLORS[index]
        out.append(f'<rect x="1" y="{index*5+1}" width="3" height="3" fill="{fill}"/>')
        out.append(f'<text x="5" y="{index*5+3.8}">{escape(name)}</text>')
        end = angle + value/total*2*math.pi
        if value == total:
            out.append(f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="{fill}"/>')
        elif value > 0:
            x1,y1 = cx+radius*math.cos(angle),cy+radius*math.sin(angle)
            x2,y2 = cx+radius*math.cos(end),cy+radius*math.sin(end)
            out.append(f'<path d="M {cx} {cy} L {x1} {y1} A {radius} {radius} 0 '
                       f'{int(end-angle>math.pi)} 1 {x2} {y2} Z" fill="{fill}"/>')
        middle = (angle+end)/2
        out.append(f'<text x="{cx+radius*.66*math.cos(middle)}" y="{cy+radius*.66*math.sin(middle)}" '
                   f'text-anchor="middle" fill="#102A35">{value:g}</text>')
        angle = end
    return svg_image(''.join(out)+'</svg>', bounds, 'WPPF 정량 차트')


def deck_pages(deck):
    """Project only trusted OOXML generated by build_posco_deck, never uploaded PPT XML."""
    size = E.fromstring(deck.files['ppt/presentation.xml']).find('p:sldSz', NS)
    width = int(size.get('cx'))/MM
    master_part = 'ppt/slideMasters/slideMaster1.xml'
    master = E.fromstring(deck.files[master_part])
    master_rels = relationships(deck.files,master_part)

    def shapes(tree, rels, transform=(0,0,1,1)):
        output=[]
        ox,oy,sx,sy=transform
        for shape in tree:
            tag=E.QName(shape).localname
            if tag=='grpSp':
                xf=shape.find('p:grpSpPr/a:xfrm',NS)
                off,extent,child,child_extent=list(xf)
                gx,gy=int(off.get('x'))/MM,int(off.get('y'))/MM
                fx,fy=int(extent.get('cx'))/int(child_extent.get('cx')),int(extent.get('cy'))/int(child_extent.get('cy'))
                output+=shapes(shape,rels,(ox+sx*(gx-fx*int(child.get('x'))/MM),
                                           oy+sy*(gy-fy*int(child.get('y'))/MM),sx*fx,sy*fy))
                continue
            if tag not in ('sp','pic','graphicFrame'): continue
            x,y,w,h=box(shape);bounds=(ox+x*sx,oy+y*sy,w*sx,h*sy)
            x,y,w,h=bounds
            label=shape.find('.//p:cNvPr',NS).get('name','')
            edit=deck.edits.get(shape)
            if tag=='pic':
                rid=shape.find('p:blipFill/a:blip',NS).get('{'+NS['r']+'}embed')
                target=rels[rid]
                data=deck.images.get(posixpath.basename(target))
                if data is None: data=deck.files[target]
                item=image_item(data,bounds,label,shape.find('p:blipFill/a:srcRect',NS))
                if edit:item['edit']=edit
                output.append(item)
            elif tag=='sp':
                sp=shape.find('p:spPr',NS)
                fill=color(sp,'a:solidFill','transparent')
                stroke=color(sp,'a:ln/a:solidFill','transparent')
                if fill!='transparent' or stroke!='transparent':
                    line=sp.find('a:ln',NS)
                    geom=sp.find('a:prstGeom',NS)
                    output.append(dict(type='rect',x=x,y=y,w=w,h=max(h,.1),fill=fill,stroke=stroke,
                        stroke_width=int(line.get('w','9525'))/MM if line is not None else 0,
                        radius=2.5 if geom is not None and geom.get('prst')=='roundRect' else 0))
                body=shape.find('p:txBody',NS)
                if body is not None: output+=text_items(body,bounds,edit)
            else:
                chart=shape.find('.//c:chart',NS)
                if chart is not None:
                    rid=chart.get('{'+NS['r']+'}id');number=int(posixpath.basename(rels[rid])[5:-4])
                    item=pie_image(E.fromstring(deck.charts[number-1][0]),bounds)
                    if edit:item['edit']=edit
                    output.append(item)
                    continue
                table=shape.find('.//a:tbl',NS)
                if table is None: continue
                widths=[int(c.get('w'))/MM for c in table.find('a:tblGrid',NS)]
                ty=y
                for row in table.findall('a:tr',NS):
                    height=int(row.get('h'))/MM;tx=x
                    for cell,cw in zip(row.findall('a:tc',NS),widths):
                        props=cell.find('a:tcPr',NS)
                        output.append(dict(type='rect',x=tx,y=ty,w=cw,h=height,
                            fill=color(props,'a:solidFill','#FFFFFF'),stroke='#D5E0E3',stroke_width=.2))
                        ml,mr,mt,mb=[int(props.get(k,d))/MM for k,d in
                                     (('marL','91440'),('marR','91440'),('marT','45720'),('marB','45720'))]
                        cells=text_items(cell.find('a:txBody',NS),(tx+ml,ty+mt,cw-ml-mr,height-mt-mb),edit)
                        anchor=props.get('anchor','t')
                        used=sum(i['h'] for i in cells)
                        if anchor in ('ctr','b'):
                            shift=max(0,height-mt-mb-used)*(.5 if anchor=='ctr' else 1)
                            for item in cells:item['y']+=shift
                        output+=cells
                        tx+=cw
                    ty+=height
        return output

    background=shapes(master.find('p:cSld/p:spTree',NS),master_rels)
    for item in background:
        if item['type']=='image': item['role']='template-artwork'
    pages=[]
    for slide,rels in deck.pages:
        info=deck.page_info[slide]
        title=''.join(named(slide,'Title')[0].xpath('.//a:t/text()',namespaces=NS))
        targets={r.get('Id'):posixpath.normpath(posixpath.join('ppt/slides',r.get('Target')))
                 for r in rels if r.get('TargetMode')!='External'}
        items=shapes(slide.find('p:cSld/p:spTree',NS),targets)
        # Hyperlinks keep the same destination as the generated PPT, restricted to source links.
        links={r.get('Id'):r.get('Target') for r in rels if r.get('TargetMode')=='External'}
        for shape in slide.find('p:cSld/p:spTree',NS):
            link=shape.find('.//a:hlinkClick',NS)
            if link is not None:
                x,y,w,h=box(shape)
                items.append(dict(type='link',x=x,y=y,w=w,h=h,size=8,label='',
                    href=links[link.get('{'+NS['r']+'}id')],source_id=None))
        pages.append(dict(info,title=title,items=background+items,width=width,height=int(size.get('cy'))/MM))
    return pages


def render_posco_preview(project, document, resolve_source, *, storage_id=None,
                         image_renderer=None, edit_links=False):
    from ..config import load_settings
    from .layout import build_pages
    from .html import render_html
    if load_settings().pptx_template:
        # Site-specific template behaviour remains separate from the bundled POSCO design.
        return render_html(build_pages(project,document,resolve_source,image_renderer=image_renderer),
                           document,'app',storage_id,edit_links=edit_links)
    deck=build_posco_deck(project,document,resolve_source,storage_id=storage_id,
                          link_base='/api/v1/xrd/workbench',image_renderer=image_renderer)
    return render_html(deck_pages(deck),document,'app',storage_id,edit_links=edit_links)

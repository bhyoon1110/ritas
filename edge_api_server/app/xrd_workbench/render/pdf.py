import io
import math

from reportlab.lib.colors import HexColor
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from ..render.fonts import draw_string, font_names, text_width
from ..render.items import link_target
from ..render.theme import PAGE_H, PAGE_W


def draw_image(pdf, item, page_height):
    x,y,w,h=item['x'],item['y'],item['w'],item['h']
    if item.get('pie_data'):
        # Draw the same chart data/geometry as the HTML SVG, without a new SVG dependency.
        from .posco_pptx import PIE_COLORS
        pie=item['pie_data'];r=pie['radius'];cx=x+pie['cx'];cy=page_height-y-pie['cy']
        total=sum(pie['values']);angle=90
        for index,(name,value) in enumerate(zip(pie['names'],pie['values'])):
            row=pie['legend'][index]
            pdf.setFillColor(HexColor('#'+PIE_COLORS[index]))
            pdf.rect((x+1)*mm,(page_height-y-row['top']-4)*mm,3*mm,3*mm,fill=1,stroke=0)
            extent=-value/total*360
            if value==total: pdf.circle(cx*mm,cy*mm,r*mm,stroke=0,fill=1)
            elif value>0: pdf.wedge((cx-r)*mm,(cy-r)*mm,(cx+r)*mm,(cy+r)*mm,angle,extent,stroke=0,fill=1)
            pdf.setFillColor(HexColor('#102A35'))
            for line_index,line in enumerate(row['lines']):
                draw_string(pdf,(x+5)*mm,(page_height-y-row['top']-3.8-line_index*4)*mm,line,3.2*mm)
            middle=math.radians(angle+extent/2);label=f'{value:g}'
            tx=(cx+r*.66*math.cos(middle))*mm-text_width(label,3.2*mm,False)/2
            draw_string(pdf,tx,(cy+r*.66*math.sin(middle))*mm,label,3.2*mm)
            angle+=extent
        return
    if item.get('raster_source'):
        from PIL import Image
        with Image.open(io.BytesIO(item['raster_source'])) as image:
            iw,ih=image.size;l,t,r,b=item['crop']
            cropped=image.crop((round(iw*l),round(ih*t),round(iw*(1-r)),round(ih*(1-b))))
            reader=ImageReader(cropped)
            pdf.drawImage(reader,x*mm,(page_height-y-h)*mm,width=w*mm,height=h*mm,mask='auto')
        return
    pdf.drawImage(ImageReader(io.BytesIO(item['data'])),x*mm,(page_height-y-h)*mm,
                  width=w*mm,height=h*mm,mask='auto')


def render_pdf(pages, document, link_base=None, storage_id=None):
    """The shared page model drawn as PDF. Title band, headings and footer are page items like
    any other, so this draws nothing that the model does not contain."""
    font_names()  # validates the configured fonts before drawing
    out = io.BytesIO()
    pdf = canvas.Canvas(out, pagesize=(PAGE_W * mm, PAGE_H * mm), pageCompression=1)
    pdf.setTitle(document['metadata']['title'])
    pdf.setAuthor(document['metadata'].get('author') or '')
    for page in pages:
        page_height=page.get('height',PAGE_H)
        pdf.setPageSize((page.get('width',PAGE_W)*mm,page_height*mm))
        for item in page['items']:
            if item['type'] in ('rect','ellipse','line'):
                pdf.saveState()
                fill=item['fill']!='transparent'
                stroke=bool(item.get('stroke') and item['stroke']!='transparent')
                if fill: pdf.setFillColor(HexColor(item['fill']))
                if stroke:
                    pdf.setStrokeColor(HexColor(item['stroke']))
                    pdf.setLineWidth(item.get('stroke_width',.2)*mm)
                pdf.setDash({'dash':[4,3],'sysDot':[1,2],'dot':[1,2],'dashDot':[4,2,1,2]}.get(item.get('dash'),[]))
                if item['type']=='line':
                    if stroke: pdf.line(item['x']*mm,(page_height-item['y'])*mm,(item['x']+item['w'])*mm,(page_height-item['y']-item['h'])*mm)
                elif item['type']=='ellipse':
                    pdf.ellipse(item['x']*mm,(page_height-item['y']-item['h'])*mm,(item['x']+item['w'])*mm,(page_height-item['y'])*mm,stroke=int(stroke),fill=int(fill))
                else: pdf.roundRect(
                    item['x'] * mm,
                    (page_height - item['y'] - item['h']) * mm,
                    item['w'] * mm,
                    item['h'] * mm,
                    item.get('radius',0)*mm,
                    stroke=int(stroke),
                    fill=int(fill),
                )
                pdf.restoreState()
            elif item['type'] == 'text':
                pdf.setFillColor(HexColor(item['color']))
                for i, line in enumerate(item['lines']):
                    baseline = (page_height - item['y'] - (i + 0.8) * item['leading']) * mm
                    x = item['x'] * mm
                    if item.get('align') in ('right','center'):
                        x += (item['w']*mm-text_width(line,item['size'],item['bold'])) * (1 if item['align']=='right' else .5)
                    if item.get('runs'):
                        for run in item['runs']:
                            pdf.setFillColor(HexColor(run['color']))
                            draw_string(pdf,x,baseline+run['baseline']*run['size'],run['text'],run['size'],bold=run['bold'])
                            x+=text_width(run['text'],run['size'],run['bold'])
                    else:
                        draw_string(pdf, x, baseline, line, item['size'], bold=item['bold'])
            elif item['type'] == 'image':
                draw_image(pdf,item,page_height)
            elif item['type'] == 'link':
                pdf.setFillColor(HexColor('#367ab1'))
                baseline = (page_height - item['y'] - 4) * mm
                if item['label']:
                    draw_string(pdf, item['x'] * mm, baseline, item['label'] + ' →', item['size'])
                pdf.linkURL(
                    link_target(item, link_base, storage_id),
                    (
                        item['x'] * mm,
                        (page_height - item['y'] - item['h']) * mm,
                        (item['x'] + item['w']) * mm,
                        (page_height - item['y']) * mm,
                    ),
                    relative=0,
                    thickness=0,
                )
        pdf.showPage()
    pdf.save()
    return out.getvalue()

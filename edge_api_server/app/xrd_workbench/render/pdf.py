import io

from reportlab.lib.colors import HexColor
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from ..render.fonts import draw_string, font_names, text_width
from ..render.items import link_target
from ..render.theme import PAGE_H, PAGE_W


def render_pdf(pages, document, link_base=None, storage_id=None):
    """The shared page model drawn as PDF. Title band, headings and footer are page items like
    any other, so this draws nothing that the model does not contain."""
    font_names()  # validates the configured fonts before drawing
    out = io.BytesIO()
    pdf = canvas.Canvas(out, pagesize=(PAGE_W * mm, PAGE_H * mm), pageCompression=1)
    pdf.setTitle(document['metadata']['title'])
    pdf.setAuthor(document['metadata'].get('author') or '')
    for page in pages:
        for item in page['items']:
            if item['type'] == 'rect':
                pdf.setFillColor(HexColor(item['fill']))
                pdf.rect(
                    item['x'] * mm,
                    (PAGE_H - item['y'] - item['h']) * mm,
                    item['w'] * mm,
                    item['h'] * mm,
                    stroke=0,
                    fill=1,
                )
            elif item['type'] == 'text':
                pdf.setFillColor(HexColor(item['color']))
                for i, line in enumerate(item['lines']):
                    baseline = (PAGE_H - item['y'] - (i + 0.8) * item['leading']) * mm
                    x = item['x'] * mm
                    if item.get('align') == 'right':
                        x = (item['x'] + item['w']) * mm - text_width(
                            line, item['size'], item['bold']
                        )
                    draw_string(pdf, x, baseline, line, item['size'], bold=item['bold'])
            elif item['type'] == 'image':
                pdf.drawImage(
                    ImageReader(io.BytesIO(item['data'])),
                    item['x'] * mm,
                    (PAGE_H - item['y'] - item['h']) * mm,
                    width=item['w'] * mm,
                    height=item['h'] * mm,
                    mask='auto',
                )
            elif item['type'] == 'link':
                pdf.setFillColor(HexColor('#367ab1'))
                baseline = (PAGE_H - item['y'] - 4) * mm
                draw_string(pdf, item['x'] * mm, baseline, item['label'] + ' →', item['size'])
                pdf.linkURL(
                    link_target(item, link_base, storage_id),
                    (
                        item['x'] * mm,
                        (PAGE_H - item['y'] - item['h']) * mm,
                        (item['x'] + item['w']) * mm,
                        (PAGE_H - item['y']) * mm,
                    ),
                    relative=0,
                    thickness=0,
                )
        pdf.showPage()
    pdf.save()
    return out.getvalue()

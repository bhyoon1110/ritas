import base64
from html import escape

from ..render.theme import PAGE_H, PAGE_W, PX_PER_MM

STYLE = """*{box-sizing:border-box}body{margin:0;background:#edf0f4;font-family:"Malgun Gothic",Arial,sans-serif;color:#394758}.reportNav{padding:14px 24px;background:white;border-bottom:1px solid #ddd;font-size:13px;position:sticky;top:0;z-index:1}.reportNav a{color:#366da9;margin-right:20px}.page{background:white;width:{PAGE_W}mm;height:{PAGE_H}mm;position:relative;margin:18px auto;box-shadow:0 2px 14px #20334d14}.item{position:absolute}.textLine{white-space:pre}.image{object-fit:contain}.sourceLink{color:#367ab1;text-decoration:underline} @media print{.page,.page *{-webkit-print-color-adjust:exact;print-color-adjust:exact}body{background:white}.reportNav{display:none}.page{margin:0;box-shadow:none;page-break-after:always}@page{size:{PAGE_W}mm {PAGE_H}mm;margin:0}}""".replace(
    '{PAGE_W}', str(PAGE_W)
).replace('{PAGE_H}', str(PAGE_H))


def render_html(pages, document, link_mode='package', storage_id=None):
    output = [
        '<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>'
        + escape(document['metadata']['title'])
        + '</title><style>'
        + STYLE
        + '</style></head><body>'
    ]
    if link_mode == 'package':
        output.append(
            '<nav class="reportNav"><a href="'
            + escape('보고서.pdf')
            + '">보고서 PDF</a><a href="'
            + escape('보고서.pptx')
            + '">편집 가능한 PPTX</a><a href="'
            + escape('뷰어.html')
            + '">Multi Profile Viewer</a><span>압축을 풀고 열어주세요. 원본 PDF: Report · TXT: Measured Data</span></nav>'
        )
    for page in pages:
        output.append('<section class="page">')
        for item in page['items']:
            pos = f"left:{item['x']}mm;top:{item['y']}mm;width:{item['w']}mm;height:{item['h']}mm;"
            if item['type'] == 'rect':
                output.append(
                    '<div class="item" style="' + pos + f"background:{item['fill']};" + '"></div>'
                )
            elif item['type'] == 'text':
                style = (
                    pos
                    + f"font-size:{item['size']}pt;color:{item['color']};font-weight:{'700' if item['bold'] else '400'};line-height:{item['leading']}mm;"
                    + ('text-align:right;' if item.get('align') == 'right' else '')
                )
                output.append(
                    '<div class="item" style="'
                    + style
                    + '">'
                    + ''.join(
                        '<div class="textLine">' + escape(line) + '</div>' for line in item['lines']
                    )
                    + '</div>'
                )
            elif item['type'] == 'image':
                uri = 'data:' + item['mime'] + ';base64,' + base64.b64encode(item['data']).decode()
                output.append(
                    '<img class="item image" style="'
                    + pos
                    + '" src="'
                    + uri
                    + '" alt="'
                    + escape(item['filename'], quote=True)
                    + '">'
                )
            elif item['type'] == 'link':
                href = (
                    item['href']
                    if link_mode == 'package'
                    else f"/api/v1/xrd/workbench/api/files/{storage_id}/{item['source_id']}"
                )
                output.append(
                    '<a class="item sourceLink" target="_blank" rel="noopener" style="'
                    + pos
                    + f"font-size:{item['size']}pt"
                    + '" href="'
                    + escape(href, quote=True)
                    + '">'
                    + escape(item['label'])
                    + ' ↗</a>'
                )
        output.append('</section>')
    if link_mode == 'app':
        output.append(
            '<script>function fit(){var z=Math.min(1,(innerWidth-30)/'
            + str(round(PAGE_W * PX_PER_MM))
            + ');var pages=document.querySelectorAll(".page");for(var i=0;i<pages.length;i++){pages[i].style.zoom=z;}}addEventListener("resize",fit);fit();</script>'
        )
    output.append('</body></html>')
    return ''.join(output)

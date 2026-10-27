import base64
import hashlib
import json
from html import escape

from ..render.theme import PAGE_H, PAGE_W, PX_PER_MM

STYLE = """*{box-sizing:border-box}body{margin:0;background:#edf0f4;font-family:"Malgun Gothic",Arial,sans-serif;color:#394758}.reportNav{padding:14px 24px;background:white;border-bottom:1px solid #ddd;font-size:13px;position:sticky;top:0;z-index:1}.reportNav a{color:#366da9;margin-right:20px}.page{background:white;width:{PAGE_W}mm;height:{PAGE_H}mm;position:relative;margin:18px auto;box-shadow:0 2px 14px #20334d14}.item{position:absolute}.textLine{white-space:pre}.image{object-fit:contain}.sourceLink{color:#367ab1;text-decoration:underline} @media print{.page,.page *{-webkit-print-color-adjust:exact;print-color-adjust:exact}body{background:white}.reportNav{display:none}.page{margin:0;box-shadow:none;page-break-after:always}@page{size:{PAGE_W}mm {PAGE_H}mm;margin:0}}""".replace(
    '{PAGE_W}', str(PAGE_W)
).replace('{PAGE_H}', str(PAGE_H))


EDITOR_SCRIPT = """<script>
(function(){
var choice=document.getElementById('previewPages'), scrollTimer=0;
function pages(){return document.querySelectorAll('[data-preview-page]');}
function navHeight(){return document.querySelector('.editorNav').getBoundingClientRect().height;}
function position(){var list=pages(),top=navHeight(),index=0;for(var i=0;i<list.length;i++){if(list[i].getBoundingClientRect().bottom>top){index=i;break;}index=i;}if(!list.length)return;var r=list[index].getBoundingClientRect();choice.value=String(index);parent.postMessage({type:'xrd-preview-position',position:{key:list[index].getAttribute('data-preview-key'),index:index,offset:Math.max(0,Math.min(1,(top-r.top)/Math.max(1,r.height)))}},'*');}
function restore(value){var list=pages();if(!list.length)return;var index=Math.max(0,Math.min(list.length-1,Number(value.index)||0));for(var i=0;i<list.length;i++)if(value.key&&list[i].getAttribute('data-preview-key')===value.key){index=i;break;}var r=list[index].getBoundingClientRect();window.scrollTo(0,window.pageYOffset+r.top-navHeight()+Math.max(0,Math.min(1,Number(value.offset)||0))*r.height);position();}
function go(index){restore({index:Number(index),offset:0});}
choice.onchange=function(){go(choice.value);};
addEventListener('scroll',function(){clearTimeout(scrollTimer);scrollTimer=setTimeout(position,120);});
function edit(event){var node=event.target.closest('[data-edit]');if(!node)return;if(event.type==='keydown'&&event.key!=='Enter'&&event.key!==' ')return;event.preventDefault();parent.postMessage({type:'xrd-preview-edit',target:JSON.parse(node.getAttribute('data-edit'))},'*');}
document.addEventListener('click',edit);document.addEventListener('keydown',edit);
addEventListener('message',function(event){
 if(event.source!==parent||!event.data)return;
 if(event.data.type==='xrd-preview-page'&&Number.isInteger(event.data.page))go(event.data.page);
 if(event.data.type==='xrd-preview-restore'&&event.data.position)restore(event.data.position);
 if(event.data.type==='xrd-preview-locate'){
  var target=event.data.target||{},nodes=document.querySelectorAll('[data-edit]');
  for(var i=0;i<nodes.length;i++)nodes[i].classList.remove('linked');
  for(var i=0;i<nodes.length;i++){var value=JSON.parse(nodes[i].getAttribute('data-edit'));if(value.tab===target.tab&&(!target.field||value.field===target.field)&&(!target.id||value.id===target.id)){nodes[i].classList.add('linked');nodes[i].scrollIntoView({block:'center'});break;}}
 }
});
parent.postMessage({type:'xrd-preview-ready'},'*');
})();</script>"""


def edit_attrs(target):
    return ' data-edit="' + escape(json.dumps(target, ensure_ascii=False), quote=True) + '" tabindex="0" role="button" title="클릭하면 해당 입력 항목으로 이동"'


def render_html(pages, document, link_mode='package', storage_id=None, *, edit_links=False):
    # Editor links are opt-in and NEVER included in delivered HTML/PDF/PPTX.
    edit_links = edit_links and link_mode == 'app'
    width = pages[0].get('width', PAGE_W) if pages else PAGE_W
    height = pages[0].get('height', PAGE_H) if pages else PAGE_H
    style = STYLE.replace(str(PAGE_W)+'mm',str(width)+'mm').replace(str(PAGE_H)+'mm',str(height)+'mm')
    output = [
        '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>'
        + escape(document['metadata']['title'])
        + '</title><style>'
        + style
        + '</style></head><body>'
    ]
    # Repeated master artwork is embedded ONCE, not repeated for every preview slide.
    # Keep the file self-contained and avoid inflating the response on low-resource PCs.
    artwork = {}
    for page in pages:
        for item in page['items']:
            if item.get('role') == 'template-artwork':
                key = hashlib.sha256(item['data']).hexdigest()
                if key not in artwork:
                    cls = 'artwork-' + str(len(artwork))
                    artwork[key] = cls
                    uri = 'data:' + item['mime'] + ';base64,' + base64.b64encode(item['data']).decode()
                    output.append('<style>.'+cls+'{background:url("'+uri+'") center/100% 100% no-repeat}</style>')
    if edit_links:
        output.append('<style>.editorNav{position:sticky;top:0;z-index:5;background:#fff;padding:10px;border-bottom:1px solid #ccd7e4;font:13px Arial,sans-serif}.editorNav select{max-width:60%;padding:5px}.editPageTools{padding:8px 15px;font:13px Arial,sans-serif}.editPageTools button{padding:6px 10px;margin-right:8px;border:1px solid #ccd7e4;background:white;border-radius:4px;color:#163e62}[data-edit]{cursor:pointer}[data-edit]:hover,[data-edit]:focus,.linked{outline:2px solid #1768a0;outline-offset:2px}@media print{.editorNav,.editPageTools{display:none}}</style>')
        output.append('<nav class="editorNav"><b>초안 · 저장 전 확인</b> <select id="previewPages" aria-label="미리보기 페이지">' + ''.join(
            f'<option value="{i}">{i + 1}. {escape(p.get("navigation_title") or p["title"])}</option>' for i, p in enumerate(pages)
        ) + '</select></nav>')
    if link_mode == 'package':
        output.append(
            '<nav class="reportNav"><a href="'
            + escape('보고서.pdf')
            + '">보고서 PDF</a><a href="'
            + escape('보고서.pptx')
            + '">열람용 PPTX</a><a href="'
            + escape('뷰어.html')
            + '">전체 그래프</a><span>그래프·원본 자료는 이 HTML 안에서 열립니다. PPT의 파일 링크는 ZIP 전체 압축 해제가 필요합니다.</span></nav>'
        )
    page_keys = {}
    for index, page in enumerate(pages):
        group = page['kind'] + ':' + (page.get('sample_id') or '')
        ordinal = page_keys.get(group, 0)
        page_keys[group] = ordinal + 1
        page_key = escape(group + ':' + str(ordinal), quote=True)
        if edit_links:
            output.append('<div class="editPageTools"><button' + edit_attrs(page.get('edit', dict(tab='overview'))) + '>이 페이지 입력 항목</button><button' + edit_attrs(dict(tab='classes')) + '>상 분류 수정</button><span>테두리가 표시되는 내용을 클릭해 수정 위치로 이동하세요.</span></div>')
        output.append(f'<section class="page" data-preview-page="{index}" data-preview-key="{page_key}">')
        for item in page['items']:
            target = item.get('edit')
            if not target and page['kind'] == 'figure' and item['type'] == 'image':
                target = dict(tab='figure')
            attrs = edit_attrs(target) if edit_links and target else ''
            pos = f"left:{item['x']}mm;top:{item['y']}mm;width:{item['w']}mm;height:{item['h']}mm;"
            if item['type'] in ('ellipse','line'):
                geom = (f'<ellipse cx="{item["w"]/2}" cy="{item["h"]/2}" rx="{item["w"]/2}" ry="{item["h"]/2}"/>'
                        if item['type']=='ellipse' else f'<line x1="0" y1="0" x2="{item["w"]}" y2="{item["h"]}"/>')
                dash={'dash':'1.4 1','sysDot':'.3 .7','dot':'.3 .7','dashDot':'1.4 .7 .3 .7'}.get(item.get('dash'),'none')
                output.append(f'<svg class="item" style="{pos}overflow:visible" viewBox="0 0 {item["w"]} {item["h"]}" preserveAspectRatio="none" fill="{item["fill"]}" stroke="{item["stroke"]}" stroke-width="{item.get("stroke_width",.2)}" stroke-dasharray="{dash}">{geom}</svg>')
            elif item['type'] == 'rect':
                border = {'solid':'solid','dot':'dotted','sysDot':'dotted'}.get(item.get('dash','solid'),'dashed')
                output.append(
                    '<div class="item" style="' + pos + f"background:{item['fill']};"
                    + (f"border:{item.get('stroke_width',.2)}mm {border} {item['stroke']};border-radius:{item.get('radius',0)}mm;" if item.get('stroke') else '') + '"></div>'
                )
            elif item['type'] == 'text':
                style = (
                    pos
                    + f"font-size:{item['size']}pt;color:{item['color']};font-weight:{'700' if item['bold'] else '400'};line-height:{item['leading']}mm;"
                    + ('text-align:'+item['align']+';' if item.get('align') in ('right','center') else '')
                )
                if item.get('runs'):
                    content = '<div class="textLine">' + ''.join(
                        '<span style="font-size:'+str(r['size'])+'pt;font-weight:'+('700' if r['bold'] else '400')
                        +';color:'+r['color']+';position:relative;top:'+str(-r['baseline']*r['size'])
                        +'pt;line-height:0">'+escape(r['text'])+'</span>' for r in item['runs'])+'</div>'
                else:
                    content = ''.join('<div class="textLine">' + escape(line) + '</div>' for line in item['lines'])
                output.append(
                    '<div class="item"' + attrs + ' style="'
                    + style
                    + '">'
                    + content
                    + '</div>'
                )
            elif item['type'] == 'image':
                if item.get('role') == 'template-artwork':
                    cls = artwork[hashlib.sha256(item['data']).hexdigest()]
                    output.append('<div class="item '+cls+'" aria-hidden="true" style="'+pos+'"></div>')
                    continue
                uri = 'data:' + item['mime'] + ';base64,' + base64.b64encode(item['data']).decode()
                output.append(
                    '<img class="item image"' + attrs + ' style="'
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
                    if link_mode == 'package' or item.get('source_id') is None
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
                    + (' ↗' if item['label'] else '') + '</a>'
                )
        output.append('</section>')
    if link_mode in ('app', 'package'):
        output.append(
            '<script>function fit(){var z=Math.min(1,(innerWidth-30)/'
            + str(round(width * PX_PER_MM))
            + ');var pages=document.querySelectorAll(".page");for(var i=0;i<pages.length;i++){pages[i].style.zoom=z;}}addEventListener("resize",fit);addEventListener("beforeprint",function(){var p=document.querySelectorAll(".page");for(var i=0;i<p.length;i++)p[i].style.zoom=1;});addEventListener("afterprint",fit);fit();</script>'
        )
    if edit_links:
        output.append(EDITOR_SCRIPT)
    output.append('</body></html>')
    return ''.join(output)

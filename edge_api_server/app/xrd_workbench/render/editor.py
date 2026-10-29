"""Adapter from the XRD scene to the shared author-only PPT page/object editor."""
import base64
from collections import defaultdict
import hashlib
import io
import json
import re
from types import SimpleNamespace

from lxml import etree as E
from pydantic import TypeAdapter, ValidationError

from ...report import canvas
from ...report.pages import PageEdits, text_layout

PAGE_ID = re.compile(r'^(generated-[a-f0-9]{20}-\d+|added-[A-Za-z0-9-]{1,64})$')


def clean_edits(document):
    try:
        pages = PageEdits.model_validate(document.get('pageEdits') or {}).model_dump()
        objects = TypeAdapter(dict[str, list[canvas.ObjectEdit]]).validate_python(document.get('canvasEdits') or {})
        if len(objects) > 200 or sum(len(v) for v in objects.values()) > 500:
            raise ValueError('개체 편집은 보고서당 500개 이내로 사용하세요.')
        ids = pages['hidden'] + (pages['order'] or []) + list(objects) + [p['after'] for p in pages['added']]
        if any(not PAGE_ID.fullmatch(key) for key in ids):
            raise ValueError('PPT 페이지 식별자가 올바르지 않습니다.')
        for key in ('hidden', 'order'):
            values = pages[key] or []
            if len(set(values)) != len(values): raise ValueError('페이지 목록에 중복이 있습니다.')
        if len({p['id'] for p in pages['added']}) != len(pages['added']):
            raise ValueError('추가 페이지 식별자가 중복됩니다.')
        for page in pages['added']: text_layout(page['body'])
        return dict(pageEdits=pages, canvasEdits={key:[p.model_dump(exclude_none=True) for p in patches] for key, patches in objects.items()})
    except ValidationError:
        raise ValueError('PPT 페이지·개체 편집 값의 형식 또는 한도를 확인하세요.') from None


def validate_assets(document, asset_root):
    holder = SimpleNamespace(canvas_asset_root=asset_root)
    for patches in document.get('canvasEdits', {}).values():
        for patch in patches:
            if patch.get('asset'): canvas.asset_path(holder, patch['asset'])


def _identity(deck):
    counts = defaultdict(int)
    for slide, _ in deck.pages:
        info = deck.page_info[slide]
        key = json.dumps([info['kind'], info.get('editor_samples', info.get('sample_id'))], sort_keys=True)
        index = counts[key]; counts[key] += 1
        info['id'] = 'generated-' + hashlib.sha256(key.encode()).hexdigest()[:20] + '-' + str(index)


def check_template(document):
    from ..config import load_settings
    pages = document.get('pageEdits') or {}
    if load_settings().pptx_template and (document.get('canvasEdits') or any(pages.values())):
        raise ValueError('공통 PPT 자유 편집은 기본 포스코 보고서 양식에서 지원합니다. 서버의 별도 PPT 템플릿 설정을 확인하세요.')


def apply(deck, document, asset_root=None):
    from .posco_pptx import NS
    _identity(deck)
    edits = document.get('pageEdits') or {}
    original = list(deck.pages)
    allowed = {deck.page_info[s]['id'] for s, _ in original}
    for page in edits.get('added', []):
        if page['id'] in allowed:
            raise ValueError('추가 페이지 식별자가 중복됩니다.')
        slide = deck.slide(2, blank=True, kind='added')
        deck.title(slide, page['title'])
        lines, font = text_layout(page['body'])
        deck.text(slide, '\n'.join(lines), (17, 45, 240, 120), size=font/100, name='Added body')
        deck.page_info[slide].update(id=page['id'], navigation_title=page['title'] or '추가 페이지')
        pair = deck.pages.pop()
        # A generated anchor can disappear when a sample is excluded. Retain
        # the author's page and edits at the end until that sample returns.
        index = next((i for i, (s, _) in enumerate(deck.pages) if deck.page_info[s]['id'] == page['after']), len(deck.pages)-1)
        deck.pages.insert(index+1, pair); allowed.add(page['id'])
    # Generated pages may temporarily disappear after sample inclusion/pagination
    # changes. Keep their patches dormant so restoring evidence restores the edits.
    order = edits.get('order') or []
    lookup = {deck.page_info[s]['id']:(s,r) for s,r in deck.pages}
    arranged = [lookup[key] for key in order if key in lookup]
    previous = None
    for pair in deck.pages:
        key = deck.page_info[pair[0]]['id']
        if key not in order:
            at = next((i+1 for i,(s,_) in enumerate(arranged) if deck.page_info[s]['id']==previous), 0)
            arranged.insert(at,pair)
        previous = key
    size = E.fromstring(deck.files['ppt/presentation.xml']).find('p:sldSz', NS)
    size = (int(size.get('cx')), int(size.get('cy')))
    holder = SimpleNamespace(page_edits=edits, canvas_edits=document.get('canvasEdits') or {}, canvas_asset_root=asset_root)
    for slide, rels in arranged:
        locked=[]
        targets={}
        for shape in slide.findall('p:cSld/p:spTree/*', NS):
            info=shape.find('.//p:cNvPr', NS)
            if info is None: continue
            if info.get('name') in {'RIST logo','Page number','Template cover artwork'} or shape.find('.//a:hlinkClick',NS) is not None:
                locked.append(info.get('id'))
            if shape in deck.edits: targets[info.get('id')]=deck.edits[shape]
        payload=dict(pageId=deck.page_info[slide]['id'], xml=E.tostring(slide), rels=E.tostring(rels),
                     size=size, media=[], lockedShapeIds=locked)
        canvas.apply(holder, [payload], validate_only=True, previous_edits=holder.canvas_edits)
        # Reject attempts to alter branding/links even when submitted directly.
        _, objects=canvas.objects(payload)
        known={item['id'] for _,item in objects}
        _, all_objects=canvas.objects(dict(payload,lockedShapeIds=[]))
        protected={item['id'] for _,item in all_objects}-known
        for patch in holder.canvas_edits.get(payload['pageId'], []):
            if patch['id'] in protected:
                raise ValueError('편집할 수 없는 개체입니다. 페이지의 개체 편집을 초기화하세요.')
        # A sample/phase table can temporarily disappear during analysis edits.
        # The shared renderer keeps absent shape patches dormant until it returns.
        canvas.apply(holder,[payload])
        updated=E.fromstring(payload['xml']); new_rels=E.fromstring(payload['rels'])
        # Keep original element identity used by link audits and page metadata.
        slide[:]=list(updated); rels[:]=list(new_rels)
        for shape in slide.findall('p:cSld/p:spTree/*', NS):
            info=shape.find('.//p:cNvPr',NS)
            if info is not None and info.get('id') in targets: deck.edits[shape]=targets[info.get('id')]
        for name,path in payload['media']: deck.images[name]=path.read_bytes()
        _, objects=canvas.objects(dict(payload,xml=E.tostring(slide)))
        deck.page_info[slide]['objects']=[item for _,item in objects]
    deck.pages=[pair for pair in arranged if deck.page_info[pair[0]]['id'] not in edits.get('hidden',[])]
    if not 1 <= len(deck.pages) <= 200: raise ValueError('보고서는 최소 1페이지, 최대 200페이지를 유지해야 합니다.')
    deck.number_pages()
    return deck


def preview(project, document, resolve_source, *, storage_id, image_renderer, asset_root):
    from .posco_pptx import build_posco_deck
    from .posco_preview import deck_pages
    from .pdf import render_pdf
    from .pdf_raster import page_images
    from PIL import Image
    from ..config import load_settings
    editable = not load_settings().pptx_template
    if editable:
        deck=build_posco_deck(project,document,resolve_source,storage_id=storage_id,
                              image_renderer=image_renderer,asset_root=asset_root)
        pages=deck_pages(deck)
    else:
        # Keep the former site-template preview available without pretending
        # that presentation edits can be applied to an arbitrary external deck.
        from .posco_preview import report_pages
        from .theme import PAGE_W, PAGE_H
        pages=report_pages(project,document,resolve_source,image_renderer=image_renderer)
        for i,page in enumerate(pages):
            page.update(id='generated-'+hashlib.sha256(('custom:'+str(i)).encode()).hexdigest()[:20]+'-0',objects=[])
            page.setdefault('width',PAGE_W);page.setdefault('height',PAGE_H)
    result=dict(pageEditing=editable,canvasEditing=editable,ratio=pages[0]['width']/pages[0]['height'],pages=[])
    total=0
    for page,data in zip(pages,page_images(render_pdf(pages,document),max_edge=1100)):
        item=dict(id=page['id'],title=page.get('navigation_title') or page['title'],regions=[],objects=page['objects'])
        if editable and not page['id'].startswith('added-'):
            from ...report.template_styles import layout_key
            item['templateKey'] = layout_key(page.get('kind'), page['objects'])
        item['targets']=[dict(x=v['x']/page['width'],y=v['y']/page['height'],w=v['w']/page['width'],h=v['h']/page['height'],target=v['edit'])
                         for v in page['items'] if v.get('edit')]
        total+=len(data)
        if total>20*1024*1024: raise ValueError('PPT 미리보기 크기가 큽니다. 보고서에 포함할 시료를 나누어 주세요.')
        item['image']='data:image/png;base64,'+base64.b64encode(data).decode()
        with Image.open(io.BytesIO(data)) as image:
            image.thumbnail((240,240)); out=io.BytesIO();image.save(out,format='PNG')
            item['thumbnail']='data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()
        result['pages'].append(item)
    return result

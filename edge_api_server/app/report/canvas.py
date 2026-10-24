"""Bounded author-only PPT object edits. No HTML, external URLs or client paths."""
import hashlib
from io import BytesIO
from pathlib import Path
import os
import re
import tempfile
from threading import Lock
from typing import Literal
from xml.etree import ElementTree as ET

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

NS = {"p":"http://schemas.openxmlformats.org/presentationml/2006/main",
      "a":"http://schemas.openxmlformats.org/drawingml/2006/main",
      "r":"http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
ASSET = re.compile(r"^[a-f0-9]{64}$")
COLOR = r"^(#[A-Fa-f0-9]{6}|none)$"
_ASSET_LOCK = Lock()


class ObjectEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(pattern=r"^(shape-[a-f0-9]{20}|object-[A-Za-z0-9-]{1,64})$")
    kind: Literal["text", "image", "rect", "ellipse", "line"] | None = None
    x: float | None = Field(default=None, ge=0, le=1)
    y: float | None = Field(default=None, ge=0, le=1)
    w: float | None = Field(default=None, ge=.002, le=1)
    h: float | None = Field(default=None, ge=.002, le=1)
    text: str | None = Field(default=None, max_length=4000)
    fontSize: float | None = Field(default=None, ge=8, le=72)
    bold: bool | None = None
    color: str | None = Field(default=None, pattern=r"^#[A-Fa-f0-9]{6}$")
    fill: str | None = Field(default=None, pattern=COLOR)
    stroke: str | None = Field(default=None, pattern=COLOR)
    lineWidth: float | None = Field(default=None, ge=.25, le=12)
    dash: Literal["solid", "dash", "dot", "dashDot"] | None = None
    align: Literal["left", "center", "right"] | None = None
    asset: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    hidden: bool | None = None
    layer: Literal["front", "back"] | None = None


def _node(tag, attrs=None):
    prefix, local = tag.split(":")
    return ET.Element("{" + NS[prefix] + "}" + local, attrs or {})


def objects(payload):
    from .renderers import PPTX_SLIDE_W, PPTX_SLIDE_H
    tree = ET.fromstring(payload["xml"])
    found, counts = [], {}
    for shape in tree.findall("p:cSld/p:spTree/*", NS):
        if shape.tag not in {"{"+NS['p']+"}sp", "{"+NS['p']+"}pic"}:
            continue
        info = shape.find(".//p:cNvPr", NS)
        name = info.get("name", "") if info is not None else ""
        # Branding lives in the master; its per-slide caption is locked too.
        if name in {"Experiment banner title", "Footer", "Experiment label"}:
            continue
        xfrm = shape.find("p:spPr/a:xfrm", NS)
        if xfrm is None:
            continue
        count = counts.get(name, 0); counts[name] = count + 1
        oid = name if name.startswith('object-') else 'shape-' + hashlib.sha256((name+':'+str(count)).encode()).hexdigest()[:20]
        off, ext = xfrm.find('a:off', NS), xfrm.find('a:ext', NS)
        if off is None or ext is None:
            continue
        text = '\n'.join(''.join(t.text or '' for t in p.findall('.//a:t', NS)) for p in shape.findall('p:txBody/a:p', NS))
        geom = shape.find('p:spPr/a:prstGeom', NS)
        kind = 'image' if shape.tag.endswith('pic') else 'text' if shape.find('p:txBody', NS) is not None else (geom.get('prst') if geom is not None else 'rect')
        if kind not in {'text','image','line','ellipse'}: kind = 'rect'
        run = shape.find('.//a:rPr', NS)
        def rgb(path, default):
            value = shape.find(path+'/a:solidFill/a:srgbClr', NS)
            return '#'+value.get('val') if value is not None else default
        line = shape.find('p:spPr/a:ln', NS)
        dash = shape.find('p:spPr/a:ln/a:prstDash', NS)
        props = shape.find('p:txBody/a:p/a:pPr', NS)
        found.append((shape, dict(id=oid, kind=kind, name=name, text=text,
            x=int(off.get('x'))/PPTX_SLIDE_W, y=int(off.get('y'))/PPTX_SLIDE_H,
            w=int(ext.get('cx'))/PPTX_SLIDE_W, h=int(ext.get('cy'))/PPTX_SLIDE_H,
            fontSize=float(run.get('sz',1800))/100 if run is not None else 18,
            bold=run is not None and run.get('b')=='1', color=rgb('.//a:rPr','#172B4D'),
            fill=rgb('p:spPr','none'),stroke=rgb('p:spPr/a:ln','none'),
            lineWidth=float(line.get('w',12700))/12700 if line is not None else 1,
            dash={'sysDot':'dot'}.get(dash.get('val'),dash.get('val')) if dash is not None else 'solid',
            align={'ctr':'center','r':'right'}.get(props.get('algn'),'left') if props is not None else 'left')))
    return tree, found


def asset_path(document, asset):
    if not ASSET.fullmatch(asset or '') or not document.canvas_asset_root:
        raise ValueError('이미지를 다시 첨부하세요.')
    path = Path(document.canvas_asset_root) / (asset+'.png')
    if not path.is_file() or path.is_symlink():
        raise ValueError('첨부 이미지가 만료되었습니다. 다시 첨부하세요.')
    return path


def store_image(root, data):
    if len(data)>2*1024*1024:
        raise ValueError('이미지는 2MB 이하로 첨부하세요.')
    try:
        with Image.open(BytesIO(data)) as src:
            if src.format not in {'PNG','JPEG','WEBP'} or src.width*src.height>12_000_000:
                raise ValueError('PNG/JPEG/WebP, 1,200만 화소 이하 이미지를 사용하세요.')
            src.load()
            with ImageOps.exif_transpose(src) as oriented:
                image=oriented.convert('RGBA'); image.thumbnail((1920,1920)); image.info.clear()
                out=BytesIO(); image.save(out,format='PNG'); width,height=image.size; image.close()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('읽을 수 없는 이미지입니다.') from exc
    data=out.getvalue()
    if len(data)>8*1024*1024: raise ValueError('이미지 용량이 너무 큽니다. 크기를 줄여 첨부하세요.')
    root.mkdir(parents=True,exist_ok=True)
    asset=hashlib.sha256(data).hexdigest(); path=root/(asset+'.png')
    with _ASSET_LOCK:
        if not path.exists():
            if sum(p.stat().st_size for p in root.glob('*.png'))+len(data)>32*1024*1024:
                raise ValueError('보고서 이미지 보관 한도(32MB)에 도달했습니다.')
            with tempfile.NamedTemporaryFile(dir=root,delete=False) as temp:
                temp.write(data); temporary=temp.name
            try: os.replace(temporary,path)
            finally:
                if os.path.exists(temporary): os.unlink(temporary)
    return dict(asset=asset,width=width,height=height)


def _fill(parent, color):
    for child in list(parent):
        if child.tag in {'{'+NS['a']+'}solidFill','{'+NS['a']+'}noFill'}: parent.remove(child)
    fill=_node('a:noFill') if color=='none' else _node('a:solidFill')
    if color!='none': fill.append(_node('a:srgbClr',{'val':color.lstrip('#')}))
    # DrawingML has ordered children: geometry -> fill -> outline; run fill
    # must precede the Latin/East-Asian font elements (PowerPoint validates it).
    index=0
    if parent.tag=='{'+NS['p']+'}spPr':
        for i, child in enumerate(parent):
            if child.tag in {'{'+NS['a']+'}xfrm','{'+NS['a']+'}prstGeom','{'+NS['a']+'}custGeom'}: index=i+1
    elif parent.tag=='{'+NS['a']+'}rPr' and len(parent) and parent[0].tag=='{'+NS['a']+'}ln':
        index=1
    parent.insert(index,fill)


def apply(document, payloads, *, validate_only=False, previous_edits=None):
    from . import renderers as r
    edits=document.canvas_edits or {}
    if not edits: return payloads
    if len(edits)>200 or sum(len(v) for v in edits.values())>500:
        raise ValueError('개체 편집은 보고서당 500개 이내로 사용하세요.')
    known={p['pageId'] for p in payloads} | set((document.page_edits or {}).get('hidden',[]))
    previous_edits=previous_edits or {}
    if validate_only and set(edits)-known-set(previous_edits):
        raise ValueError('편집할 페이지가 없습니다. 미리보기를 다시 불러오세요.')
    for payload in payloads:
        patches=edits.get(payload['pageId'],[])
        if not patches: continue
        if len(patches)>100 or len({v['id'] for v in patches})!=len(patches):
            raise ValueError('페이지당 개체는 100개 이하이며 식별자가 중복될 수 없습니다.')
        tree, entries=objects(payload); by_id={item['id']:(shape,item) for shape,item in entries}
        parent=tree.find('p:cSld/p:spTree',NS)
        rels=ET.fromstring(payload['rels']); media=list(payload.get('media',[]))
        next_id=max(int(n.get('id','0')) for n in tree.findall('.//p:cNvPr',NS))+1
        for patch in patches:
            patch={k:v for k,v in patch.items() if v is not None}
            oid=patch['id']; original=by_id.get(oid)
            if not original and not oid.startswith('object-'):
                old_ids={v['id'] for v in previous_edits.get(payload['pageId'],[])}
                if validate_only and oid not in old_ids:
                    raise ValueError('편집할 개체가 없습니다. 해당 페이지 편집을 초기화하세요.')
                continue  # Retain dormant edits after automatic text/table repagination.
            item=dict(original[1]) if original else dict(kind=patch.get('kind'),x=.1,y=.2,w=.3,h=.15,text='',fontSize=20,
                color='#172B4D',fill='none',stroke='#172B4D',lineWidth=1,dash='solid',bold=False,align='left')
            if original and patch.get('kind',item['kind'])!=item['kind']: raise ValueError('개체 종류를 변경할 수 없습니다.')
            item.update(patch)
            if item['kind'] not in {'text','image','rect','ellipse','line'}: raise ValueError('개체 종류가 올바르지 않습니다.')
            if item['x']+item['w']>1.001 or item['y']+item['h']>1.001: raise ValueError('개체를 페이지 안에 배치하세요.')
            if patch.get('hidden'):
                if original: parent.remove(original[0])
                continue
            if original: shape=original[0]
            elif item['kind']=='image':
                shape=ET.fromstring('<p:pic xmlns:p="'+NS['p']+'" xmlns:a="'+NS['a']+'" xmlns:r="'+NS['r']+'"><p:nvPicPr><p:cNvPr id="'+str(next_id)+'" name="'+oid+'"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr><p:blipFill><a:blip/><a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr><a:xfrm><a:off/><a:ext/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>')
                parent.append(shape); next_id+=1
            else:
                shape=ET.fromstring('<root xmlns:p="'+NS['p']+'" xmlns:a="'+NS['a']+'">'+r._pptx_text_shape(next_id,oid,x=0,y=0,cx=1,cy=1,lines=[''])+'</root>')[0]
                if item['kind']!='text': shape.remove(shape.find('p:txBody',NS))
                shape.find('p:spPr/a:prstGeom',NS).set('prst', 'rect' if item['kind']=='text' else item['kind'])
                parent.append(shape);next_id+=1
            xfrm=shape.find('p:spPr/a:xfrm',NS)
            xfrm.find('a:off',NS).attrib.update(x=str(round(item['x']*r.PPTX_SLIDE_W)),y=str(round(item['y']*r.PPTX_SLIDE_H)))
            xfrm.find('a:ext',NS).attrib.update(cx=str(round(item['w']*r.PPTX_SLIDE_W)),cy=str(round(item['h']*r.PPTX_SLIDE_H)))
            if item['kind']=='image' and (not original or patch.get('asset')):
                path=asset_path(document,item.get('asset'))
                name='canvas-'+hashlib.sha256((payload['pageId']+oid).encode()).hexdigest()[:20]+'.png'
                rid='rIdCanvas'+str(next_id);next_id+=1
                ET.SubElement(rels,'{'+REL+'}Relationship',{'Id':rid,'Type':NS['r']+'/image','Target':'../media/'+name})
                shape.find('p:blipFill/a:blip',NS).set('{'+NS['r']+'}embed',rid);media.append((name,path))
            sp=shape.find('p:spPr',NS)
            if 'fill' in patch or not original: _fill(sp,item['fill'])
            if any(k in patch for k in ('stroke','lineWidth','dash')) or not original:
                old=sp.find('a:ln',NS)
                if old is not None: sp.remove(old)
                line=_node('a:ln',{'w':str(round(item['lineWidth']*12700))});_fill(line,item['stroke'])
                line.append(_node('a:prstDash',{'val':{'dot':'sysDot'}.get(item['dash'],item['dash'])}));sp.append(line)
            if item['kind']=='text':
                body=shape.find('p:txBody',NS)
                if 'text' in patch or not original:
                    for p in list(body):
                        if p.tag=='{'+NS['a']+'}p': body.remove(p)
                    wrapper=ET.fromstring('<root xmlns:a="'+NS['a']+'">'+r._pptx_paragraphs(item['text'].split('\n'),font_size=round(item['fontSize']*100),color=item['color'].lstrip('#'),bold=item['bold'],align=item['align'])+'</root>')
                    body.extend(list(wrapper))
                for run in body.findall('.//a:rPr',NS):
                    if 'fontSize' in patch: run.set('sz',str(round(item['fontSize']*100)))
                    if 'bold' in patch: run.set('b','1' if item['bold'] else '0')
                    if 'color' in patch: _fill(run,item['color'])
                if 'align' in patch:
                    for prop in body.findall('a:p/a:pPr',NS): prop.set('algn',{'left':'l','center':'ctr','right':'r'}[item['align']])
                # Native PPT autofit keeps long text inside its box after resizing.
                bp=body.find('a:bodyPr',NS)
                if bp is not None:
                    for child in list(bp): bp.remove(child)
                    bp.append(_node('a:normAutofit'))
            if patch.get('layer'):
                parent.remove(shape)
                if patch['layer']=='front': parent.append(shape)
                else: parent.insert(2,shape)
        if not validate_only:
            payload.update(xml=ET.tostring(tree,encoding='unicode'),rels=ET.tostring(rels,encoding='unicode'),media=media)
    return payloads

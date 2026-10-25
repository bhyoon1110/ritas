from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from PIL import Image
import pytest

from app.report import canvas, renderers
from app.report.model import ReportDocument
from app.report.direct_edit import edit_pages
from app.report.editable import document_from_dict
from app.report_editor import ReportEdit, _validate_changes
from tests.test_report_editor import prepared, edit_request, wait_revision


def image_bytes():
    stream=BytesIO()
    with Image.new('RGB',(120,60),'red') as image: image.save(stream,format='PNG')
    return stream.getvalue()


def test_native_objects_existing_text_image_styles_and_roundtrip(tmp_path):
    doc=ReportDocument('canvas','원본 제목','FT-IR',{},'2026-10-24')
    doc.canvas_asset_root=str(tmp_path/'assets')
    asset=canvas.store_image(Path(doc.canvas_asset_root), image_bytes())['asset']
    payloads=renderers._pptx_slide_payloads(doc);page=payloads[0]['pageId']
    _, objects=canvas.objects(payloads[0])
    title=next(item for _,item in objects if item['name']=='Report title')
    doc.canvas_edits={page:[dict(id=title['id'],x=.15,y=.3,w=.7,h=.12,text='편집한 표지 제목',fontSize=26,bold=True,color='#AA2233'),
        dict(id='object-text',kind='text',x=.1,y=.5,w=.5,h=.1,text='<확인> 한글',fontSize=18),
        dict(id='object-line',kind='line',x=.1,y=.7,w=.6,h=.01,stroke='#005599',dash='dashDot',lineWidth=3),
        dict(id='object-ellipse',kind='ellipse',x=.6,y=.55,w=.15,h=.15,fill='#FFEEDD'),
        dict(id='object-image',kind='image',x=.6,y=.15,w=.2,h=.2,asset=asset)]}
    # Normal Pydantic patch filtering is applied on save.
    doc.canvas_edits={page:[canvas.ObjectEdit(**v).model_dump(exclude_none=True) for v in doc.canvas_edits[page]]}
    target=tmp_path/'report.pptx';renderers.render_pptx(doc,target)
    layout=edit_pages(doc,target)
    assert layout['canvasEditing']
    items={v['id']:v for v in layout['pages'][0]['objects']}
    assert items[title['id']]['text']=='편집한 표지 제목'
    assert items[title['id']]['fontSize']==26
    assert items[title['id']]['color']=='#AA2233'
    assert items['object-line']['dash']=='dashDot'
    assert items['object-ellipse']['fill']=='#FFEEDD'
    with ZipFile(target) as archive:
        assert any(n.startswith('ppt/media/canvas-') for n in archive.namelist())
        root=ET.fromstring(archive.read('ppt/slides/slide1.xml'))
        assert root.find('.//a:prstGeom[@prst="ellipse"]',canvas.NS) is not None
        title_shape=next(shape for shape,item in canvas.objects(renderers._pptx_slide_payloads(doc)[0])[1]
                         if item['id']==title['id'])
        run=title_shape.find('.//a:rPr',canvas.NS)
        assert list(run).index(run.find('a:solidFill',canvas.NS)) < list(run).index(run.find('a:latin',canvas.NS))
    saved=doc.to_dict();assert 'canvas_asset_root' not in saved
    loaded=document_from_dict(saved,image_path=tmp_path/'report/current_graph.png')
    assert loaded.canvas_edits==doc.canvas_edits
    # Non-destructive object deletion, then exact restoration.
    doc.canvas_edits[page][0]['hidden']=True
    renderers.render_pptx(doc,target)
    assert title['id'] not in {v['id'] for v in edit_pages(doc,target)['pages'][0]['objects']}


@pytest.mark.parametrize('patch',[
    dict(id='object-test',kind='rect',x=.9,w=.5),
    dict(id='shape-'+'1'*20,x=.1),
    dict(id='object-test',kind='image',asset='1'*64),
])
def test_invalid_or_missing_objects_and_assets_fail_closed(tmp_path,patch):
    doc=ReportDocument('canvas','검증','FT-IR',{},'2026-10-24')
    doc.canvas_asset_root=str(tmp_path)
    payloads=renderers._pptx_slide_payloads(doc)
    doc.canvas_edits={payloads[0]['pageId']:[patch]}
    with pytest.raises(ValueError):canvas.apply(doc,payloads,validate_only=True)


@pytest.mark.parametrize('values',[dict(x=float('nan')),dict(asset='../outside'),dict(color='url(javascript:x)'),dict(text='x'*4001),dict(kind='script')])
def test_schema_rejects_nonfinite_paths_scripts_and_oversize(values):
    with pytest.raises(ValueError):canvas.ObjectEdit(id='object-test',**values)


def test_image_normalization_strips_metadata_and_preserves_orientation(tmp_path):
    stream=BytesIO()
    with Image.new('RGB',(120,60),'blue') as image:
        exif=Image.Exif();exif[274]=6;exif[270]='private camera note'
        image.save(stream,format='JPEG',exif=exif)
    result=canvas.store_image(tmp_path,stream.getvalue())
    assert (result['width'],result['height'])==(60,120)
    with Image.open(tmp_path/(result['asset']+'.png')) as image:
        assert not image.getexif() and not image.info
    assert canvas.store_image(tmp_path,stream.getvalue())==result


def test_dormant_saved_edits_survive_pagination_but_unknown_new_objects_fail():
    doc=ReportDocument('canvas','검증','FT-IR',{},'2026-10-24')
    payloads=renderers._pptx_slide_payloads(doc);page=payloads[0]['pageId']
    doc.canvas_edits={page:[dict(id='shape-'+'1'*20,text='기존 편집')],
                      'former-page':[dict(id='object-note',kind='text',text='이전 페이지 메모')]}
    canvas.apply(doc,payloads,validate_only=True,previous_edits=doc.canvas_edits)
    with pytest.raises(ValueError):canvas.apply(doc,payloads,validate_only=True)


@pytest.mark.parametrize('endpoint', ['editor-images', 'editor-graphs'])
def test_upload_and_revision_preserve_images_and_final_pdf(prepared, endpoint):
    h=prepared;header={'X-Requested-With':'RIST-Report-Editor'}
    before=h.job.package_path.read_bytes()
    assert h.client.post(h.url+'/editor-images',content=image_bytes()).status_code==403
    assert h.client.post(h.url+'/editor-images',content=image_bytes(),headers=dict(header,Origin='https://outside.example')).status_code==403
    assert h.client.post(h.url+'/editor-images',content=b'<svg/>',headers=header).status_code==422
    assert h.client.post(h.url+'/editor-images',content=b'x'*(2*1024*1024+1),headers=header).status_code==413
    response=h.client.post(h.url+'/'+endpoint,content=image_bytes(),headers=header)
    assert response.status_code==200,response.text
    asset=response.json()['asset']
    page=h.client.get(h.url+'/edit-layout').json()['pages'][0]['id']
    edits=edit_request(h.client.get(h.url+'/editor-data').json()['document'])
    edits['canvasEdits']={page:[dict(id='object-image',kind='image',x=.6,y=.2,w=.2,h=.2,asset=asset),
                                   dict(id='object-note',kind='text',x=.1,y=.8,w=.5,h=.1,text='추가 검토 의견')]}
    response=h.client.post(h.url+'/revisions',json=edits,headers=header)
    assert response.status_code==202,response.text
    result=wait_revision(h,response.json()['jobId']);assert result['status']=='completed',result
    job=h.store.get(result['jobId']);url=h.prefix+'/'+result['jobId']
    assert (job.tmp_root/'job/report/editor-assets'/(asset+'.png')).is_file()
    model=h.client.get(url+'/editor-data').json()['document']
    assert model['canvasEdits']==edits['canvasEdits']
    with ZipFile(BytesIO(h.client.get(result['downloadUrl']).content)) as package:
        assert package.read('report.pdf')==package.read('report-from-pptx.pdf')
        assert not any('editor-assets' in name for name in package.namelist())
    assert h.job.package_path.read_bytes()==before
    # Backward-compatible text-only clients cannot erase the canvas work.
    old=edit_request(model);old.pop('canvasEdits')
    from app.report_editor import _load_document
    assert _validate_changes(ReportEdit(**old),_load_document(job))['canvasEdits']==edits['canvasEdits']


def test_saved_graph_larger_than_2mb_has_bounded_png_only_upload(prepared):
    h=prepared;header={'X-Requested-With':'RIST-Report-Editor'}
    # Low-compression PNG to model the large graph PNGs already accepted by report generation.
    stream=BytesIO()
    with Image.new('RGB',(1200,800),'blue') as image: image.save(stream,format='PNG',compress_level=0)
    data=stream.getvalue();assert len(data)>2*1024*1024
    assert h.client.post(h.url+'/editor-graphs',content=data).status_code==403
    assert h.client.post(h.url+'/editor-graphs',content=data,headers=dict(header,Origin='https://outside.example')).status_code==403
    assert h.client.post(h.url+'/editor-images',content=data,headers=header).status_code==413
    response=h.client.post(h.url+'/editor-graphs',content=data,headers=header)
    assert response.status_code==200,response.text
    assert (response.json()['width'],response.json()['height'])==(1200,800)
    assert h.client.post(h.url+'/editor-graphs',content=b'x'*(16*1024*1024+1),headers=header).status_code==413
    stream=BytesIO()
    with Image.new('RGB',(100,100),'blue') as image: image.save(stream,format='JPEG')
    assert h.client.post(h.url+'/editor-graphs',content=stream.getvalue(),headers=header).status_code==422

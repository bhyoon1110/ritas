from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile
import base64
import json

from fastapi.testclient import TestClient
from lxml import etree as E
from PIL import Image
from pypdf import PdfReader
import pytest

from app.xrd_workbench import integration as api
from app.xrd_workbench.render.posco_pptx import build_posco_deck, render_report_pptx, NS, named
from app.xrd_workbench.render.posco_preview import deck_pages
from app.xrd_workbench.render.package import build_package
from app.xrd_workbench.render.link_audit import ppt_click_targets, check_package_links
from app.xrd_workbench.core.report import new_document, validate_document
from app.report.canvas import store_image
from .test_integration import make_app, upload, poll
from .test_draft_preview import draft_fixture
from .test_package_names import collision_project


def png():
    out=BytesIO();Image.new('RGB',(80,40),'red').save(out,format='PNG');return out.getvalue()


def test_page_and_object_edits_reach_all_delivered_formats(collision_project,tmp_path):
    project,document,data=collision_project
    original=deepcopy(project)
    baseline=build_posco_deck(project,document,data.__getitem__)
    pages=deck_pages(baseline)
    first=pages[0]['id'];deleted=pages[-1]['id'];table_page=next(p for p in pages if any(o['kind']=='table' for o in p['objects']))
    table=next(o for o in table_page['objects'] if o['kind']=='table')
    doc=deepcopy(document)
    asset=store_image(tmp_path,png())['asset']
    doc['pageEdits']={'added':[dict(id='added-test',after=first,title='Additional findings',body='Native content')],
                      'hidden':[deleted], 'order':['added-test',first]+[p['id'] for p in pages[1:]]}
    doc['canvasEdits']={'added-test':[
        dict(id='object-text',kind='text',text='Reviewed XRD results',x=.1,y=.4,w=.6,h=.15,fontSize=22,color='#194478'),
        dict(id='object-image',kind='image',asset=asset,x=.7,y=.4,w=.2,h=.2),
        dict(id='object-line',kind='line',x=.1,y=.7,w=.6,h=.01,stroke='#ff6600',dash='dash'),
        dict(id='object-oval',kind='ellipse',x=.7,y=.7,w=.15,h=.1,fill='#00ccff',stroke='#112233')],
        table_page['id']:[dict(id=table['id'],tableCells=[dict(row=0,col=0,text='Reviewed phase')])],
    }
    edited=build_posco_deck(project,doc,data.__getitem__,asset_root=tmp_path)
    projected=deck_pages(edited)
    assert [p['id'] for p in projected]==['added-test',first]+[p['id'] for p in pages[1:] if p['id']!=deleted]
    assert {'line','ellipse'} <= {i['type'] for i in projected[0]['items']}
    native=render_report_pptx(project,doc,data.__getitem__,asset_root=tmp_path)
    with ZipFile(BytesIO(native)) as ppt:
        slide=ppt.read('ppt/slides/slide1.xml').decode()
        assert 'Reviewed XRD results' in slide and 'dash' in slide and 'ellipse' in slide
        assert any(n.startswith('ppt/media/canvas-') for n in ppt.namelist())
        assert any('Reviewed phase' in ppt.read(n).decode() for n in ppt.namelist() if n.startswith('ppt/slides/slide') and n.endswith('.xml'))
    content=build_package(project,doc,data.__getitem__,asset_root=tmp_path)
    with ZipFile(BytesIO(content)) as package:
        pdf=PdfReader(BytesIO(package.read('보고서.pdf')))
        assert len(pdf.pages)==len(projected)
        assert 'Reviewed XRD results' in pdf.pages[0].extract_text()
        assert '보고서.html' not in package.namelist()
        check_package_links(ppt_click_targets(package.read('보고서.pptx')),package)
    assert project==original


def test_editor_api_upload_draft_save_reload_export_and_owner_boundary(tmp_path):
    client,sid,doc=draft_fixture(tmp_path,auth=True)
    url=api.BASE+f'/api/reports/{sid}'
    headers={'X-Requested-With':'RIST-Report-Editor','Content-Type':'image/png'}
    response=client.post(url+'/editor-images',content=png(),headers=headers)
    assert response.status_code==200,response.text
    asset=response.json()['asset']
    response=client.post(url+'/draft-preview',json={'document':doc,'format':'editor'})
    assert response.status_code==200,response.text
    value=response.json(); assert value['pageEditing'] and value['canvasEditing']
    assert all(len(p['templateKey'])==64 for p in value['pages'])
    page=value['pages'][0];assert base64.b64decode(page['image'].split(',')[1]).startswith(b'\x89PNG')
    doc['canvasEdits']={page['id']:[dict(id='object-test',kind='image',asset=asset,x=.1,y=.3,w=.2,h=.2)]}
    response=client.put(url,json={'document':doc,'expected_revision':0});assert response.status_code==200,response.text
    saved=response.json(); assert saved['canvasEdits']==doc['canvasEdits']
    assert client.get(url).json()['document']['canvasEdits']==doc['canvasEdits']
    # An old client cannot discard the editor state by omitting unknown fields.
    old=deepcopy(saved);del old['canvasEdits'];del old['pageEdits']
    response=client.put(url,json={'document':old,'expected_revision':saved['revision']});assert response.status_code==200,response.text
    saved=response.json();assert saved['canvasEdits']==doc['canvasEdits']
    response=client.post(url+'/exports',json={'kind':'zip','revision':saved['revision']})
    job=poll(client,response.json());assert client.get(api.BASE+'/api/jobs/'+job['job_id']+'/file').status_code==200
    assert client.post(url+'/editor-images',content=png(),headers=dict(headers,Origin='https://evil.test')).status_code==403
    assert client.post(url+'/editor-images',content=b'x'* (2*1024*1024+1),headers=headers).status_code==413
    client.headers['test-user']='two'
    assert client.post(url+'/editor-images',content=png(),headers=headers).status_code==404
    assert client.post(url+'/draft-preview',json={'document':saved,'format':'editor'}).status_code==404


def test_invalid_editor_data_and_missing_images_fail_before_delivery(tmp_path):
    client,sid,doc=draft_fixture(tmp_path)
    url=api.BASE+f'/api/reports/{sid}'
    for patch in [dict(id='object-test',kind='text',x=float('nan')),dict(id='object-test',kind='image',asset='../escape')]:
        draft=deepcopy(doc);draft['canvasEdits']={'generated-'+'a'*20+'-0':[patch]}
        with pytest.raises(ValueError): validate_document(client.get(api.BASE+f'/api/projects/{sid}').json(),draft)
    doc['canvasEdits']={'generated-'+'a'*20+'-0':[dict(id='object-test',kind='image',asset='a'*64)]}
    assert client.put(url,json={'document':doc,'expected_revision':0}).status_code==422
    response=client.post(url+'/draft-preview',json={'document':dict(doc,canvasEdits={}), 'format':'editor'})
    all_pages=[p['id'] for p in response.json()['pages']]
    doc['canvasEdits']={};doc['pageEdits']={'hidden':all_pages,'added':[]}
    response=client.post(url+'/draft-preview',json={'document':doc,'format':'editor'})
    assert response.status_code==422 and '최소 1페이지' in response.text


def test_hidden_title_and_missing_generated_anchor_keep_author_page(collision_project):
    project,doc,data=collision_project
    pages=deck_pages(build_posco_deck(project,doc,data.__getitem__))
    title=next(o for o in pages[0]['objects'] if o['name']=='Title')
    doc['canvasEdits']={pages[0]['id']:[dict(id=title['id'],hidden=True)]}
    doc['canvasEdits'][pages[0]['id']].append(dict(id='shape-'+'f'*20,text='Dormant sample label'))
    doc['pageEdits']={'added':[dict(id='added-kept',after='generated-'+'f'*20+'-0',title='Kept review',body='Kept text')]}
    result=deck_pages(build_posco_deck(project,doc,data.__getitem__))
    assert result[-1]['id']=='added-kept'
    assert not any(o['id']==title['id'] for o in result[0]['objects'])


def test_branding_and_source_links_cannot_be_changed_by_direct_canvas_payload(collision_project):
    from app.report.canvas import objects
    project,doc,data=collision_project
    deck=build_posco_deck(project,doc,data.__getitem__)
    slide,rels=next((s,r) for s,r in deck.pages if named(s,'RIST logo'))
    size=E.fromstring(deck.files['ppt/presentation.xml']).find('p:sldSz',NS)
    _,values=objects(dict(xml=E.tostring(slide),rels=E.tostring(rels),size=(int(size.get('cx')),int(size.get('cy')))))
    logo=next(value for _,value in values if value['name']=='RIST logo')
    doc['canvasEdits']={deck.page_info[slide]['id']:[dict(id=logo['id'],hidden=True)]}
    with pytest.raises(ValueError,match='편집할 수 없는'):
        build_posco_deck(project,doc,data.__getitem__)


def test_custom_site_template_keeps_preview_without_discarding_edits(tmp_path,monkeypatch):
    from .test_pptx_template import make_template
    template=make_template(tmp_path)
    monkeypatch.setenv('XRD_PPTX_TEMPLATE',str(template))
    client,sid,doc=draft_fixture(tmp_path/'store')
    url=api.BASE+f'/api/reports/{sid}/draft-preview'
    response=client.post(url,json={'document':doc,'format':'editor'})
    assert response.status_code==200,response.text
    value=response.json();assert value['pages'] and not value['canvasEditing'] and not value['pageEditing']
    doc['canvasEdits']={value['pages'][0]['id']:[dict(id='object-text',kind='text',text='Must not be lost')]}
    response=client.post(url,json={'document':doc,'format':'editor'})
    assert response.status_code==422 and '기본 포스코' in response.text


@pytest.mark.parametrize('legacy',[False,True])
def test_shared_editor_assets_are_available_in_authenticated_report_route(tmp_path,legacy):
    client,sid,_=draft_fixture(tmp_path,auth=True)
    if legacy:client.headers['user-agent']='Mozilla/5.0 Chrome/49.0.2623.75 Safari/537.36'
    response=client.get(api.BASE+'/report.html?project='+sid)
    assert response.status_code==200
    for name in ('report_editor_icons.js','report_page_sidebar.js','report_cell_editor.js','report_canvas_editor.js'):
        path=api.BASE+'/ppt-tools/'+name
        assert path in response.text and client.get(path).status_code==200
    css=api.BASE+'/ppt-tools/report_ppt_tools'+('.legacy' if legacy else '')+'.css'
    assert css in response.text and client.get(css).status_code==200
    assert client.get(api.BASE+'/ppt-tools/config.py').status_code==404

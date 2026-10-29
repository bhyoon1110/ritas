from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json

import pytest

from tests.test_mail_address_book import client
from app.report.template_styles import content, layout_key
from app.report import renderers
from app.report.direct_edit import edit_pages
from app.report.model import ReportDocument

URL = '/api/v1/report-ppt/templates'
SHAPE = 'shape-' + 'a'*20
HEADERS = {'x-test-user': 'one', 'X-Requested-With': 'RIST-Report-Editor'}


def value(**changes):
    return dict(version=1, name='검토용 양식', experiment='FTIR', ratio=16/9,
                pages=[dict(key='a'*64, objects=[dict(id=SHAPE, kind='text', fontSize=24, color='#123456')])], **changes)


def create(client, revision=0, data=None):
    return client.post(URL, json=dict(revision=revision, content=data or value()), headers=HEADERS)


def test_private_crud_concurrency_and_durable_metadata(client, tmp_path):
    response=create(client); assert response.status_code==201, response.text
    item=response.json()['items'][0]; assert set(item)=={'id','name','experiment'}
    assert 'no-store' in response.headers['cache-control']
    assert client.get(URL+'/'+item['id'], headers=HEADERS).json()['content']==value()
    assert all(i.get('builtin') for i in client.get(URL, headers={**HEADERS,'x-test-user':'two'}).json()['items'])
    assert client.get(URL+'/'+item['id'], headers={**HEADERS,'x-test-user':'two'}).status_code==404
    changed=value(); changed['name']='양식 수정'
    response=client.put(URL+'/'+item['id'],json=dict(revision=1,content=changed),headers=HEADERS)
    assert response.status_code==200
    assert create(client,2,changed).status_code==409
    def concurrent(i):
        data=value(); data['name']=f'새 양식{i}'
        return create(client,2,data).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(concurrent,range(2)))==[201,409]
    assert client.request('DELETE',URL+'/'+item['id'],json={'revision':2},headers=HEADERS).status_code==409
    assert client.request('DELETE',URL+'/'+item['id'],json={'revision':3},headers=HEADERS).status_code==200
    path=next((tmp_path/'user-ppt-templates').glob('*.json'))
    assert path.stat().st_mode & 0o077==0
    assert len(json.loads(path.read_text())['items'])==1
    path.write_text('{broken')
    assert client.get(URL,headers=HEADERS).status_code==500
    assert create(client,4).status_code==500
    assert path.read_text()=='{broken'


def test_authorization_origin_and_size(client):
    assert client.get(URL).status_code==401
    assert client.get(URL,headers={**HEADERS,'x-test-user':'pending'}).status_code==403
    assert client.post(URL,json={},headers={**HEADERS,'Origin':'https://other.invalid'}).status_code==403
    assert client.post(URL,content=b'x'*(2*1024*1024+1),headers=HEADERS).status_code==413


def test_names_are_experiment_scoped_and_capacity_is_bounded(client, monkeypatch):
    from app import ppt_templates
    monkeypatch.setattr(ppt_templates, 'MAX_TEMPLATES', 2)
    assert create(client).status_code==201
    second=value();second['experiment']='RAMAN'
    assert create(client,1,second).status_code==201
    third=value();third['name']='세 번째'
    assert create(client,2,third).status_code==422


@pytest.mark.parametrize('patch',[
    {'text':'이전 시료 결과'}, {'asset':'b'*64}, {'hidden':True}, {'layer':'front'},
    {'url':'https://example.invalid'}, {'id':'object-copy'}, {'fontSize':float('nan')},
    {'x':.9,'w':.5}, {'tableCells':[{'row':0,'col':0,'text':'이전 분석값'}]},
    {'kind':'table','tableCells':[{'row':0,'col':0,'text':'이전 분석값'}]},
    {'kind':'table','tableCells':[{'row':0,'col':0,'bold':True}]*2},
])
def test_old_content_or_invalid_formatting_cannot_enter_template(patch):
    data=value();data['pages'][0]['objects'][0].update(patch)
    with pytest.raises(ValueError):content(data)


def test_layout_key_ignores_analysis_but_separates_structure_and_roles():
    old=[dict(id=SHAPE,kind='table',text='old',cells=[dict(row=0,col=0,text='old')])]
    new=deepcopy(old);new[0]['text']='new';new[0]['cells'].append(dict(row=1,col=0,text='new'))
    assert layout_key('result',old)==layout_key('result',new)
    assert layout_key('result',old)!=layout_key('cover',old)
    new[0]['cells'].append(dict(row=0,col=1,text='other'))
    assert layout_key('result',old)!=layout_key('result',new)


@pytest.mark.parametrize('project',['FT-IR','RAMAN'])
def test_real_ppt_template_changes_style_preserving_new_report_values(tmp_path, project):
    source=ReportDocument('source','이전 시료의 분석 결과',project,{},'2026-10-20')
    target=ReportDocument('target','새 시료의 분석 결과',project,{},'2026-10-29')
    a=tmp_path/'source.pptx';b=tmp_path/'target.pptx'
    renderers.render_pptx(source,a);renderers.render_pptx(target,b)
    old=edit_pages(source,a)['pages'][0];new=edit_pages(target,b)['pages'][0]
    assert old['templateKey']==new['templateKey']
    title=next(o for o in new['objects'] if o['name']=='Report title')
    target.canvas_edits={new['id']:[dict(id=title['id'],kind='text',fontSize=27,color='#115577',x=.12,y=.22,w=.7,h=.2)]}
    renderers.render_pptx(target,b)
    actual=next(o for o in edit_pages(target,b)['pages'][0]['objects'] if o['id']==title['id'])
    assert actual['text']=='새 시료의 분석 결과'
    assert actual['color']=='#115577' and actual['fontSize']==27
    assert actual['x']==pytest.approx(.12,abs=1e-6)

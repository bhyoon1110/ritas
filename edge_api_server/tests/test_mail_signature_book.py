from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json

from tests.test_mail_address_book import client, HEADERS
from tests.test_report_mail import embedded_image
from app import mail_signature_book as books

URL = '/api/v1/report-mail/signatures'
DEFAULT = '/api/v1/report-mail/signature'


def create(client, revision=0, name='명함', content=None, headers=HEADERS):
    return client.post(URL, json=dict(revision=revision,name=name,content=content or {'signature':'윤병훈'}),headers=headers)


def test_legacy_migration_named_crud_default_and_private_metadata(client, tmp_path):
    folder=tmp_path/'user-mail-preferences';folder.mkdir()
    path=folder/(sha256(b'one').hexdigest()+'.json')
    legacy=dict(signature='기존 서명',signatureImage=embedded_image(),signatureImageWidth='280')
    path.write_text(json.dumps(legacy))
    # Listing migrates logically, keeping the original on disk until the first write.
    result=client.get(URL,headers=HEADERS);assert result.status_code==200
    view=result.json();assert len(view['items'])==1 and view['items'][0]['isDefault']
    assert 'data:image' not in result.text and 'signatureImage' not in result.text
    assert json.loads(path.read_text())==legacy
    assert client.get(DEFAULT,headers=HEADERS).json()==legacy
    other={**HEADERS,'x-test-user':'two'}
    assert client.get(URL,headers=other).json()['items']==[]
    old_id=view['defaultId'];assert client.get(URL+'/'+old_id,headers=other).status_code==404
    created=create(client,0,'영문 서명',dict(legacy,signature='Researcher')).json()
    new_id=next(i['id'] for i in created['items'] if i['name']=='영문 서명')
    content=dict(legacy,signature='Revised',signatureImageWidth='200')
    changed=client.put(URL+'/'+new_id,json=dict(revision=1,name='영문 명함',content=content),headers=HEADERS)
    assert changed.status_code==200
    assert client.get(URL+'/'+new_id,headers=HEADERS).json()['content']==content
    assert client.get(DEFAULT,headers=HEADERS).json()==legacy
    assert client.put(URL+'/'+new_id+'/default',json={'revision':2},headers=HEADERS).status_code==200
    assert client.get(DEFAULT,headers=HEADERS).json()==content
    # Older default endpoint updates only the chosen default, preserving every other entry.
    assert client.put(DEFAULT,json={'signature':'기본 갱신'},headers=HEADERS).status_code==200
    assert client.get(URL+'/'+old_id,headers=HEADERS).json()['content']==legacy
    assert len(client.get(URL,headers=HEADERS).json()['items'])==2
    assert client.request('DELETE',URL+'/'+new_id,json={'revision':4},headers=HEADERS).status_code==200
    assert client.get(DEFAULT,headers=HEADERS).json()=={'signature':''}
    assert len(client.get(URL,headers=HEADERS).json()['items'])==1
    assert path.stat().st_mode & 0o077==0


def test_signature_permissions_validation_and_limits(client,monkeypatch):
    assert client.get(URL).status_code==401
    assert client.get(URL,headers={**HEADERS,'x-test-user':'pending'}).status_code==403
    for headers in [{'x-test-user':'one'},{**HEADERS,'Origin':'https://other.invalid'}]:
        assert create(client,headers=headers).status_code==403
    for name in ['', 'x'*81, 'name\nextra']:
        assert create(client,name=name).status_code==422
    assert create(client,content={'signature':'bad','signatureImage':'https://remote.invalid/card.png'}).status_code==422
    assert create(client,revision=True).status_code==422
    assert client.post(URL,content=b'x'*(400*1024+1),headers=HEADERS).status_code==413
    created=create(client).json();first=created['items'][0]['id']
    assert created['defaultId']==first
    assert create(client,1,'명함').status_code==409
    assert client.request('DELETE',URL+'/'+first,json={'revision':0},headers={**HEADERS,'x-test-user':'two'}).status_code==404
    monkeypatch.setattr(books,'MAX_SIGNATURES',1)
    assert create(client,1,'또 다른 서명').status_code==422
    # Query parameters cannot select another signature during POST.
    assert client.post(URL+'?signature_id='+first,json=dict(revision=1,name='명함',content={'signature':'x'}),headers=HEADERS).status_code==409


def test_concurrent_writes_and_corruption_preserve_saved_signatures(client,tmp_path):
    create(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda i:create(client,1,f'서명{i}'),range(2)))
    assert sorted(r.status_code for r in results)==[201,409]
    assert len(client.get(URL,headers=HEADERS).json()['items'])==2
    path=next((tmp_path/'user-mail-preferences').glob('*.json'))
    path.write_text('{damaged')
    assert client.get(URL,headers=HEADERS).status_code==500
    assert client.get(DEFAULT,headers=HEADERS).status_code==500
    assert create(client,2,'새 서명').status_code==500
    assert client.put(DEFAULT,json={'signature':'overwrite'},headers=HEADERS).status_code==500
    assert path.read_text()=='{damaged'

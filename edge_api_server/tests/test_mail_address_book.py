from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.auth import AuthContext
from app.errors import ApiException
from app.report_mail import router
from app import mail_address_book as books

URL = '/api/v1/report-mail/contacts'
HEADERS = {'x-test-user': 'one', 'X-Requested-With': 'RIST-Mail-Editor'}


@pytest.fixture
def client(tmp_path):
    app = FastAPI(); app.include_router(router)
    app.state.settings = SimpleNamespace(storage_root=tmp_path)
    @app.exception_handler(ApiException)
    async def errors(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({'code': exc.code, 'message': exc.message}, status_code=exc.status_code)
    @app.middleware('http')
    async def identity(request, call_next):
        user = request.headers.get('x-test-user')
        if user:
            allowed = {'ftir':{'FTIR'},'raman':{'RAMAN'},'tem':{'TEM'},'xrd':{'XRD'}}.get(user,{'FTIR','RAMAN','TEM','XRD'})
            request.state.auth_context = AuthContext(user, user, None, user,
                'PENDING' if user == 'pending' else 'ACTIVE', 'session', datetime.now(timezone.utc), None,
                frozenset() if user in {'no-project','admin','admin2'} else frozenset(allowed),
                frozenset({'ADMIN'}) if user in {'admin','admin2'} else frozenset(), None)
        return await call_next(request)
    with TestClient(app) as client:
        yield client


def create(client, revision=0, **contact):
    return client.post(URL, json={'revision': revision, 'contact': {'name':'김연구', 'department':'소재연구', 'email':'research@rist.re.kr', **contact}}, headers=HEADERS)


def test_crud_persists_private_book_across_experiments_without_changing_signature(client, tmp_path):
    assert client.get(URL, headers=HEADERS).json() == {'revision': 0, 'contacts': []}
    signature_url = '/api/v1/report-mail/signature'
    assert client.put(signature_url, json={'signature':'RIST 연구자'}, headers=HEADERS).status_code == 200
    response = create(client); assert response.status_code == 201, response.text
    book = response.json(); item = book['contacts'][0]
    assert book['revision'] == 1 and item['email'] == 'research@rist.re.kr'
    assert 'no-store' in response.headers['cache-control']
    assert client.get(URL, headers=HEADERS).json() == book
    # All four editors use this same user-scoped endpoint, no experiment or owner override.
    for experiment in ['ftir','raman','tem','xrd']:
        assert client.get(URL, params={'experiment':experiment}, headers=HEADERS).json() == book
    edited = client.put(URL+'/'+item['id'], json={'revision':1,'contact':{'name':'김분석','department':'분석실','email':'ANALYSIS@RIST.RE.KR'}}, headers=HEADERS)
    assert edited.status_code == 200 and edited.json()['contacts'][0]['email'] == 'ANALYSIS@rist.re.kr'
    duplicate = create(client, 2, email='analysis@rist.re.kr')
    assert duplicate.status_code == 409 and duplicate.json()['code'] == 'MAIL_CONTACT_DUPLICATE'
    deleted = client.request('DELETE',URL+'/'+item['id'],json={'revision':2},headers=HEADERS)
    assert deleted.json() == {'revision':3,'contacts':[]}
    assert client.get(signature_url,headers=HEADERS).json() == {'signature':'RIST 연구자'}
    assert all(p.stat().st_mode & 0o077 == 0 for p in (tmp_path/'user-mail-address-books').iterdir())


def test_identity_permissions_and_origin_cannot_override_book_owner(client):
    assert client.get(URL).status_code == 401
    for user in ['pending','no-project']:
        assert client.get(URL,headers={**HEADERS,'x-test-user':user}).status_code == 403
    first = create(client).json()['contacts'][0]
    other = {**HEADERS,'x-test-user':'two'}
    assert client.get(URL,params={'userId':'one'},headers=other).json()['contacts'] == []
    assert client.request('DELETE',URL+'/'+first['id'],json={'revision':0},headers=other).status_code == 404
    for headers in [{'x-test-user':'one'}, {**HEADERS,'Origin':'https://other.invalid'}]:
        assert client.post(URL,json={'revision':1,'contact':{}},headers=headers).status_code == 403
    assert client.get(URL,headers=HEADERS).json()['contacts'] == [first]


@pytest.mark.parametrize('email', ['outside@example.com','a@sub.rist.re.kr','a@rist.re.kr.evil.com','one@rist.re.kr;two@rist.re.kr',
                                  'a@rist.re.kr\r\nBcc:extra@rist.re.kr','.name@rist.re.kr','n..m@rist.re.kr','name'])
def test_invalid_or_external_addresses_never_save(client,email):
    response=create(client,email=email)
    assert response.status_code == 422
    assert client.get(URL,headers=HEADERS).json()['contacts'] == []


def test_bad_payload_limits_and_missing_contact(client):
    assert create(client,name=' ').status_code == 422
    assert create(client,name='x'*101).status_code == 422
    assert create(client,department='a\nb').status_code == 422
    for revision in [True,-1,'0',1.5]:
        assert create(client,revision).status_code == 422
    assert client.post(URL,content=b'x'*8193,headers=HEADERS).status_code == 413
    assert client.post(URL,json={'revision':0,'contact':{},'ownerId':'two'},headers=HEADERS).status_code == 422
    assert client.request('DELETE',URL+'/bad-id',json={'revision':0},headers=HEADERS).status_code == 404


def test_stale_and_concurrent_edits_cannot_silently_overwrite(client):
    item=create(client).json()['contacts'][0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda i:create(client,1,email=f'worker{i}@rist.re.kr'),range(2)))
    assert sorted(r.status_code for r in responses) == [201,409]
    book=client.get(URL,headers=HEADERS).json()
    assert book['revision'] == 2 and len(book['contacts']) == 2
    assert client.request('DELETE',URL+'/'+item['id'],json={'revision':1},headers=HEADERS).status_code == 409
    assert client.get(URL,headers=HEADERS).json() == book


def test_cap_and_corrupted_storage_fail_without_overwriting(client,tmp_path,monkeypatch):
    monkeypatch.setattr(books,'MAX_CONTACTS',1)
    create(client)
    assert create(client,1,email='second@rist.re.kr').status_code == 422
    path=next((tmp_path/'user-mail-address-books').glob('*.json'))
    path.write_text('{damaged',encoding='utf-8')
    assert client.get(URL,headers=HEADERS).status_code == 500
    assert create(client,1).status_code == 500
    assert path.read_text() == '{damaged'


SHARED = '/api/v1/report-mail/shared-contacts'
ADMIN = {**HEADERS, 'x-test-user': 'admin'}


def create_shared(client, revision=0, mode='admins', projects=None, **contact):
    return client.post(SHARED, json={'revision':revision, 'contact':{
        'name':'공용 연구자','department':'분석실','email':f'shared{revision}@rist.re.kr',
        'visibility':{'mode':mode,'projects':projects or []}, **contact}}, headers=ADMIN)


def test_shared_visibility_filters_on_server_and_admin_controls_only_shared_book(client):
    create(client)
    all_contact=create_shared(client,0,'all').json()['contacts'][0]
    assert create_shared(client,1,'projects',['FTIR','TEM']).status_code == 201
    assert create_shared(client,2,'projects',['RAMAN','XRD']).status_code == 201
    assert create_shared(client,3,'admins').status_code == 201
    for user, emails in [('ftir',{'shared0@rist.re.kr','shared1@rist.re.kr'}),('tem',{'shared0@rist.re.kr','shared1@rist.re.kr'}),
                         ('raman',{'shared0@rist.re.kr','shared2@rist.re.kr'}),('xrd',{'shared0@rist.re.kr','shared2@rist.re.kr'})]:
        response=client.get(SHARED,headers={**HEADERS,'x-test-user':user},params={'admin':'true','projects':'FTIR,RAMAN,TEM,XRD'})
        view=response.json();assert view['canManage'] is False and view['revision']==0
        assert {c['email'] for c in view['contacts']}==emails
        assert all(set(c)=={'id','name','department','email'} for c in view['contacts'])
        assert 'shared3@rist.re.kr' not in response.text
        assert 'no-store' in response.headers['cache-control']
    admin=client.get(SHARED,headers=ADMIN).json()
    assert admin['canManage'] and len(admin['contacts'])==4 and admin['revision']==4
    assert client.get(URL,headers=ADMIN).json()['contacts']==[]
    assert len(client.get(URL,headers=HEADERS).json()['contacts'])==1
    for method, url, payload in [('POST',SHARED,{'revision':4,'contact':{}}),
                                 ('PUT',SHARED+'/'+all_contact['id'],{'revision':4,'contact':{}}),
                                 ('DELETE',SHARED+'/'+all_contact['id'],{'revision':4})]:
        response=client.request(method,url,json=payload,headers=HEADERS)
        assert response.status_code==403 and response.json()['code']=='MAIL_CONTACTS_ADMIN_REQUIRED'
    assert client.get(SHARED,headers=ADMIN).json()==admin
    assert client.get(SHARED).status_code==401
    for user in ['pending','no-project']:
        assert client.get(SHARED,headers={**HEADERS,'x-test-user':user}).status_code==403


def test_shared_publication_revocation_and_concurrent_admin_edits(client):
    created=create_shared(client,mode='all').json();item=created['contacts'][0]
    def edit(mode, revision=1, headers=ADMIN):
        return client.put(SHARED+'/'+item['id'],json={'revision':revision,'contact':{
            **{k:v for k,v in item.items() if k!='id'},'visibility':{'mode':mode,'projects':[]}}},headers=headers)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda user:edit('admins',headers={**HEADERS,'x-test-user':user}),['admin','admin2']))
    assert sorted(r.status_code for r in results)==[200,409]
    assert client.get(SHARED,headers=HEADERS).json()['contacts']==[]
    assert len(client.get(SHARED,headers=ADMIN).json()['contacts'])==1
    assert edit('all',revision=2).status_code==200
    assert len(client.get(SHARED,headers=HEADERS).json()['contacts'])==1
    assert client.request('DELETE',SHARED+'/'+item['id'],json={'revision':3},headers=ADMIN).status_code==200
    assert client.get(SHARED,headers=HEADERS).json()['contacts']==[]


@pytest.mark.parametrize('visibility', [None,{}, {'mode':'all','projects':['FTIR']}, {'mode':'projects','projects':[]},
    {'mode':'projects','projects':['UNKNOWN']}, {'mode':'projects','projects':'FTIR'}, {'mode':'public','projects':[]},
    {'mode':'admins','projects':[],'userId':'one'}, {'mode':True,'projects':[]}])
def test_invalid_shared_scope_is_never_treated_as_public(client,visibility):
    assert create_shared(client,visibility=visibility).status_code==422
    assert client.get(SHARED,headers=ADMIN).json()['contacts']==[]


def test_shared_origin_domain_duplicates_and_storage_fail_closed(client,tmp_path):
    assert create_shared(client,email='outsider@example.com').status_code==422
    item=create_shared(client).json()['contacts'][0]
    assert create_shared(client,1,email=item['email'].upper()).status_code==409
    assert client.request('DELETE',SHARED+'/'+item['id'],json={'revision':1},headers={**ADMIN,'Origin':'https://other.invalid'}).status_code==403
    path=tmp_path/'shared-mail-address-book/contacts.json'
    stored=json.loads(path.read_text());stored['contacts'][0].pop('visibility');path.write_text(json.dumps(stored))
    assert client.get(SHARED,headers=HEADERS).status_code==500
    assert create_shared(client,1).status_code==500
    assert json.loads(path.read_text())==stored

"""Legacy server-linked PPTs still resume after login; new recipient PPTs are offline."""
from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.auth import AuthContext, AUTH_FORM_COOKIE, SESSION_COOKIE, install_auth
from app.config import Settings
from app.errors import ApiException, api_exception_handler
from app.xrd_workbench import integration as api
from .synthetic import sample_files
from .test_integration import upload, poll
from .test_report_source_links import ppt_links


@pytest.fixture
def report(tmp_path):
    app=FastAPI()
    settings=Settings(storage_root=tmp_path,auth_enabled=True,auth_cookie_secure=False,
                      edge_public_base_url='http://testserver')
    app.state.settings=settings;app.state.database=None
    install_auth(app,settings,Mock())
    app.add_exception_handler(ApiException,api_exception_handler)
    app.include_router(api.router)
    owner=AuthContext(user_id='owner',login_id='owner01',email=None,display_name='XRD author',
        status='ACTIVE',session_id='fixture',session_expires_at=datetime.now()+timedelta(hours=1),
        sso_authenticated_at=None,sso_identity=None,projects=frozenset({'XRD'}),roles=frozenset())
    service=app.state.auth_service
    contexts={'owner-token':owner,'other-token':replace(owner,user_id='other'),
              'unapproved-token':replace(owner,projects=frozenset()),'pending-token':replace(owner,status='PENDING')}
    service.context_from_token=Mock(side_effect=contexts.get)
    service.login=Mock(return_value=(owner,'owner-token'))
    with TestClient(app,follow_redirects=False) as client:
        client.cookies.set(SESSION_COOKIE,'owner-token')
        files=sample_files('SampleA')
        sid=upload(client,files)
        project=client.get(api.BASE+f'/api/projects/{sid}').json()
        doc=client.get(api.BASE+f'/api/reports/{sid}').json()['document']
        saved=client.put(api.BASE+f'/api/reports/{sid}',json={'document':doc,'expected_revision':0}).json()
        from app.xrd_workbench.render.posco_pptx import render_report_pptx
        records={r['id']:r for r in project['sources']}
        legacy=render_report_pptx(project,doc,lambda fid:files[records[fid]['filename']],
                                 link_base='http://testserver'+api.BASE,storage_id=sid)
        links=ppt_links(legacy)
        yield client,links,project,files


@pytest.mark.parametrize('headers',[
    {'Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'},
    {'Accept':'text/html','Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document'},
])
def test_every_legacy_ppt_link_resumes_exact_destination_after_login(report,headers):
    client,links,project,files=report
    seen=set();views=set()
    for target in set(links):
        client.cookies.clear()  # Office opened a different/default browser.
        response=client.get(target,headers=headers)
        assert response.status_code==303,(target,response.text)
        login=response.headers['location'];query=parse_qs(urlsplit(login).query)
        original=urlsplit(target)
        expected=original.path+('?' + original.query if original.query else '')
        assert query['returnTo']==[expected]
        assert response.headers['cache-control']=='no-store'
        assert client.get(login,headers=headers).status_code==200
        response=client.post('/login',headers={'Origin':'http://testserver'},data={
            'loginId':'owner01','password':'fixture-password','csrfToken':client.cookies[AUTH_FORM_COOKIE],
            'returnTo':query['returnTo'][0]})
        assert response.status_code==303 and response.headers['location']==expected
        opened=client.get(response.headers['location'],headers=headers)
        assert opened.status_code==200,opened.text
        if '/api/files/' in expected:
            source=next(r for r in project['sources'] if expected.endswith('/'+r['id']))
            assert opened.content==files[source['filename']];seen.add(source['role'])
        else:
            params=parse_qs(urlsplit(expected).query);views.add(params['view'][0])
            assert params['project']==[project['storage_id']]
            if params['view']==['single']:assert params['sample']==[project['samples'][0]['id']]
    assert seen=={'measurement','phase_report','wppf_report'}
    assert views=={'single','multi'}


def test_link_login_keeps_api_errors_ownership_and_project_permissions(report):
    client,links,project,_=report
    file=next(u for u in links if '/api/files/' in u)
    client.cookies.clear()
    for headers in ({},{'Accept':'application/json'},{'Accept':'text/html','Sec-Fetch-Mode':'cors'},
                    {'Accept':'text/html','X-Requested-With':'XMLHttpRequest'}):
        response=client.get(file,headers=headers)
        assert response.status_code==401 and response.json()['code']=='AUTHENTICATION_REQUIRED'
    assert client.get(api.BASE+'/api/projects',headers={'Accept':'text/html'}).status_code==401
    assert client.post(api.BASE+'/api/projects',headers={'Accept':'text/html'}).status_code==401
    client.cookies.set(SESSION_COOKIE,'unapproved-token')
    assert client.get(file,headers={'Accept':'text/html'}).status_code==403
    client.cookies.set(SESSION_COOKIE,'other-token')
    assert client.get(file,headers={'Accept':'text/html'}).status_code==404
    client.cookies.set(SESSION_COOKIE,'pending-token')
    response=client.get(file,headers={'Accept':'text/html'})
    assert response.status_code==303
    assert client.get(response.headers['location']).status_code==200  # no login/return redirect loop

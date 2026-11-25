"""Legacy artifact URLs cannot bypass personal report ownership."""
from dataclasses import replace
from types import SimpleNamespace
import time

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.testclient import TestClient

from app import ftir_web, raman_web, xrd_web
from app.config import Settings
from app.errors import ApiException
from app.preview_report import PreviewReportJobStore
from tests.test_workspace_dashboard import context


@pytest.fixture
def web(tmp_path,monkeypatch):
    app=FastAPI();app.state.settings=Settings(storage_root=tmp_path,auth_enabled=True)
    app.state.actor=context();app.state.preview_report_job_store=PreviewReportJobStore()
    sent=[]
    for module in [ftir_web,raman_web,xrd_web]:
        monkeypatch.setattr(module,'authenticated_transfer_payload',lambda request,payload,project:payload)
        monkeypatch.setattr(module,'send_preview_report_package',lambda **kwargs:sent.append(kwargs['job'].job_id) or {'queued':True})
        app.include_router(module.router)
    from app.report import spectrum_viewer
    monkeypatch.setattr(spectrum_viewer,'spectrum_preview_response',lambda _:HTMLResponse('owned preview'))
    monkeypatch.setattr(xrd_web,'_xrd_report_jobs',{})
    monkeypatch.setattr(xrd_web,'_cleanup_xrd_report_jobs',lambda:None)
    @app.middleware('http')
    async def actor(request,call_next):
        request.state.auth_context=app.state.actor
        return await call_next(request)
    @app.exception_handler(ApiException)
    async def error(request,exc):return JSONResponse({'code':exc.code},status_code=exc.status_code)
    client=TestClient(app)
    yield SimpleNamespace(app=app,client=client,root=tmp_path,sent=sent)
    client.close()


def job(w,kind,owner):
    package=w.root/(kind+'-'+str(owner)+'.zip');package.write_bytes(b'private package')
    if kind=='xrd':
        key='xrd-'+str(owner)
        value=xrd_web.XrdReportJob(job_id=key,work_dir=w.root,input_root=w.root,package_path=package,
            settings=w.app.state.settings,database=None,error_archive=None,usage_archive=None,usage_client_context={},
            origin=True,created_at=time.time(),updated_at=time.time(),status='completed',html_result='<p>private</p>',owner_user_id=owner)
        xrd_web._xrd_report_jobs[key]=value
    else:
        value=w.app.state.preview_report_job_store.create(filename=kind+'-report-package.zip',owner_user_id=owner)
        w.app.state.preview_report_job_store.complete(value.job_id,tmp_root=w.root,package_path=package)
    return value.job_id


@pytest.mark.parametrize('kind',['ftir','raman','xrd'])
@pytest.mark.parametrize('role,owner,status',[('own','alice',200),('other','bob',404),('unknown',None,404),('admin','bob',200),('development','bob',200)])
def test_status_preview_download_send_share_owner_policy(web,kind,role,owner,status):
    w=web;key=job(w,kind,owner)
    if role=='admin':w.app.state.actor=context(admin=True)
    if role=='development':w.app.state.settings=replace(w.app.state.settings,auth_enabled=False);w.app.state.actor=None
    prefix='/api/v1/'+kind+'/report/jobs/'+key
    for suffix in ['', '/html' if kind=='xrd' else '/preview', '/package' if kind=='xrd' else '/download']:
        assert w.client.get(prefix+suffix).status_code==status,(kind,role,suffix)
    response=w.client.post(prefix+'/send',json={'requestNumber':'REQ','experimentCode':'A11111','equipmentCode':'E','operatorId':'SSO'})
    assert response.status_code==status
    assert len(w.sent)==int(status==200)


@pytest.mark.parametrize('kind',['ftir','raman','xrd'])
def test_revoked_project_cannot_use_legacy_alias(web,kind):
    key=job(web,kind,'alice');web.app.state.actor=context(projects=())
    assert web.client.get('/api/v1/'+kind+'/report/jobs/'+key).status_code==403


def test_other_spectrum_kind_cannot_use_alias(web):
    key=job(web,'ftir','alice')
    assert web.client.get('/api/v1/raman/report/jobs/'+key).status_code==404

"""Exercise actual owner/filter SQL and HTTP authorization with isolated SQLite."""
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import re
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app import report_history, report_delivery
from app.config import Settings
from app.database import Database
from app.errors import ApiException
from app.preview_report import PreviewReportJobStore
from tests.test_workspace_dashboard import context


class Connection:
    def __init__(self, raw): self.raw = raw
    def execute(self, sql, params=()):
        sql=sql.replace('ON DUPLICATE KEY UPDATE report_id = VALUES(report_id)', 'ON CONFLICT(report_id) DO NOTHING')
        return self.raw.execute(sql, params)
    def __enter__(self): return self
    def __exit__(self, *args): pass


class HistoryDB(Database):
    def __init__(self, path=':memory:'):
        self.raw=sqlite3.connect(path,check_same_thread=False)
        self.raw.row_factory=lambda c,r:dict(zip([x[0] for x in c.description],r))
        self.raw.executescript('''
        CREATE TABLE IF NOT EXISTS report_runs(report_id TEXT PRIMARY KEY, source_job_id TEXT,
          request_number TEXT, experiment_code TEXT, equipment_code TEXT, operator_id TEXT,
          generation_status TEXT, storage_key TEXT, package_relative_path TEXT, package_file_name TEXT,
          package_size_bytes INTEGER, report_options_json TEXT, generated_at TEXT, created_at TEXT, deleted_at TEXT);
        CREATE TABLE IF NOT EXISTS report_owners(report_id TEXT PRIMARY KEY, owner_user_id TEXT, analysis_type TEXT);
        CREATE TABLE IF NOT EXISTS report_transfers(transfer_id TEXT, report_id TEXT, destination TEXT, status TEXT,
          delivery_state_json TEXT, document_status TEXT, mail_status TEXT, last_error_code TEXT, last_error_message TEXT);
        CREATE TABLE IF NOT EXISTS jobs(job_id TEXT PRIMARY KEY);
        ''')
    def _connect(self): return Connection(self.raw)
    @contextmanager
    def transaction(self):
        with self.raw: yield self._connect()
    def report(self, key, owner='alice', project='XRD', state='FAILED', deleted=None, created='2026-11-25', request='2022T00793', source=None):
        self.raw.execute('INSERT INTO report_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
          (key,source,request,project,'XRD-LIMS-TEST','forged-operator','READY','RIST_REPORTS',key+'/report.zip','report.zip',12,
           json.dumps({'owner_user_id':'alice','testMtdCode':'A11111'}),created,created,deleted))
        if state:
            self.raw.execute('INSERT INTO report_transfers VALUES(?,?,?,?,?,?,?,?,?)',(key,key,'LIMS',state,
              json.dumps({'email':{'to':'secret@example.com'},'password':'secret-password','phase':'FILE_TRANSFER'}),
              'PENDING', 'PENDING', 'UNKNOWN_KEY', '<b>RITAS rejected</b>'))
        if owner: self.bind_report_owner(key,owner,project)
        self.raw.commit()


@pytest.fixture
def web(tmp_path, monkeypatch):
    app=FastAPI();app.state.settings=Settings(storage_root=tmp_path/'storage',auth_enabled=True)
    app.state.database=HistoryDB(tmp_path / "history.sqlite3");app.state.actor=context();app.state.preview_report_job_store=PreviewReportJobStore()
    @app.middleware('http')
    async def actor(request, call_next):
        request.state.auth_context=app.state.actor
        return await call_next(request)
    @app.exception_handler(ApiException)
    async def error(request, exc): return JSONResponse({'code':exc.code},status_code=exc.status_code)
    app.include_router(report_history.router);app.include_router(report_delivery.router)
    client=TestClient(app)
    yield SimpleNamespace(app=app,client=client,db=app.state.database,path=tmp_path)
    client.close();app.state.database.raw.close()


def test_history_filters_before_pagination_and_public_state(web):
    w=web
    for i,state in enumerate(['FAILED','COMPLETED','PENDING',None]): w.db.report(str(i),state=state,created=f'2026-11-{20+i}')
    w.db.report('bob',owner='bob');w.db.report('tem',project='TEM')
    result=w.client.get('/api/v1/my/reports?project=XRD&pageSize=2').json()
    assert result['totalCount']==4 and result['totalPages']==2
    assert [r['reportId'] for r in result['items']]==['3','2']
    result=w.client.get('/api/v1/my/reports?project=XRD&pageSize=2&page=2').json()
    assert [r['reportId'] for r in result['items']]==['1','0']
    assert result['items'][1]['lastErrorCode']=='UNKNOWN_KEY'
    assert 'secret@example.com' not in json.dumps(result) and 'secret-password' not in json.dumps(result)
    assert w.client.get('/api/v1/my/reports?transferStatus=COMPLETED').json()['totalCount']==1
    assert w.client.get('/api/v1/my/reports?transferStatus=READY_FOR_REVIEW').json()['totalCount']==1
    assert w.client.get('/api/v1/my/reports?q=missing').json()['totalCount']==0
    assert w.client.get('/api/v1/my/reports?dateFrom=2026-11-21&dateTo=2026-11-22').json()['totalCount']==2


@pytest.mark.parametrize('method,path',[('get',''),('get','/package'),('get','/preview'),('post','/send')])
def test_foreign_report_cannot_be_read_downloaded_or_sent(web,method,path):
    web.db.report('foreign',owner='bob')
    assert getattr(web.client,method)('/api/v1/reports/foreign'+path).status_code==404


def test_admin_personal_list_stays_personal_but_detail_is_global(web):
    web.db.report('mine');web.db.report('foreign',owner='bob')
    web.app.state.actor=context(admin=True)
    assert [i['reportId'] for i in web.client.get('/api/v1/my/reports').json()['items']]==['mine']
    assert web.client.get('/api/v1/reports/foreign').status_code==200


def test_project_revocation_and_untrusted_owner_query(web):
    web.db.report('mine');web.db.report('tem',project='TEM');web.db.report('other',owner='bob')
    web.app.state.actor=context(projects=('TEM',))
    result=web.client.get('/api/v1/my/reports?owner=bob&admin=true').json()
    assert [r['reportId'] for r in result['items']]==['tem']
    assert web.client.get('/api/v1/reports/mine').status_code==403


def test_authentication_required_even_when_auth_disabled(web):
    from dataclasses import replace
    web.app.state.actor=None;web.app.state.settings=replace(web.app.state.settings,auth_enabled=False)
    assert web.client.get('/api/v1/my/reports').status_code==401
    assert web.client.get('/my/reports').status_code==401
    web.app.state.actor=replace(context(),status='SUSPENDED')
    assert web.client.get('/api/v1/my/reports').status_code==403


def test_legacy_xrd_backfill_survives_workspace_removal_and_restart(web):
    import shutil
    w=web;key='a'*32;w.db.report(key,owner=None,state='COMPLETED')
    root=w.app.state.settings.storage_root/'xrd-workbench-v1'/'users'/hashlib.sha256(b'alice').hexdigest()/'jobs'/key
    root.mkdir(parents=True);(root/'job.json').write_text(json.dumps({'job_id':key,'kind':'zip','storage_id':'archived-project'}))
    assert w.client.get('/api/v1/my/reports').json()['totalCount']==1
    assert w.db.fetch_report_owner(key)['owner_user_id']=='alice'
    shutil.rmtree(root.parents[2]);w.app.state.preview_report_job_store=PreviewReportJobStore()
    w.db.raw.close();w.db=HistoryDB(w.path / "history.sqlite3");w.app.state.database=w.db
    # Persistent DB ownership survives process-local state and file cleanup.
    w.db.raw.execute('UPDATE report_runs SET deleted_at=? WHERE report_id=?',('2026-11-26',key));w.db.raw.commit()
    result=w.client.get('/api/v1/my/reports').json()['items'][0]
    assert result['localFilesRetained'] is False and result['downloadUrl'] is None
    assert w.client.get('/api/v1/reports/'+key).status_code==200
    assert w.client.get('/api/v1/reports/'+key+'/package').status_code==410
    assert w.db.bind_report_owner(key,'bob','XRD') is False


def test_forged_options_operator_and_symlink_do_not_claim_old_report(web):
    key='b'*32;web.db.report(key,owner=None)
    assert web.client.get('/api/v1/my/reports').json()['totalCount']==0
    assert web.client.get('/api/v1/reports/'+key).status_code==404
    base=web.app.state.settings.storage_root/'xrd-workbench-v1'/'users'
    foreign=base/hashlib.sha256(b'bob').hexdigest()/'jobs'/key
    foreign.mkdir(parents=True);(foreign/'job.json').write_text(json.dumps({'job_id':key,'kind':'zip'}))
    (base/hashlib.sha256(b'alice').hexdigest()).symlink_to(foreign.parents[1],target_is_directory=True)
    assert web.client.get('/api/v1/my/reports').json()['totalCount']==0
    assert web.db.fetch_report_owner(key) is None


def test_verified_machine_report_keeps_project_review_contract(web):
    web.db.report('machine',owner=None,source='job1');web.db.raw.execute("INSERT INTO jobs VALUES('job1')")
    assert web.client.get('/api/v1/reports/machine').status_code==200
    assert web.client.get('/api/v1/my/reports').json()['totalCount']==0
    web.db.report('fake-machine',owner=None,source='does-not-exist')
    assert web.client.get('/api/v1/reports/fake-machine').status_code==404


@pytest.mark.parametrize('query',['page=0','pageSize=101','dateFrom=wrong','dateFrom=2026-11-30&dateTo=2026-11-01','project=OTHER','transferStatus=BAD'])
def test_invalid_filters_fail_clearly(web,query):
    assert web.client.get('/api/v1/my/reports?'+query).status_code==422


def test_relative_metadata_root_is_safe_and_does_not_loop(tmp_path,monkeypatch):
    from app.report_ownership import _metadata
    monkeypatch.chdir(tmp_path)
    from pathlib import Path
    Path('owned/job').mkdir(parents=True)
    Path('owned/job/job.json').write_text('{"kind":"zip"}')
    assert _metadata(Path('owned/job/job.json'),Path('owned'))=={'kind':'zip'}
    assert _metadata(Path('owned/job/job.json').absolute(),Path('owned'))=={'kind':'zip'}
    assert _metadata(Path('../other/job.json'),Path('owned'))=={}


def test_generated_owner_cannot_come_from_options_and_real_method_preserves_kind(tmp_path):
    from app.report_queue import register_generated_report_package
    from app.preview_report import PreviewReportSendRequest, send_preview_report_package
    from tests.test_report_queue import CapturingDatabase, authored_mail_options, settings, write_zip
    class DB(CapturingDatabase):
        def fetch_report_run(self,key):
            return next((r for r in reversed(self.report_runs) if r['report_id']==key),None)
    db=DB();config=settings(tmp_path/'storage');package=tmp_path/'source.zip';write_zip(package)
    register_generated_report_package(settings=config,database=db,report_id='mine',package_path=package,
        experiment_code='FTIR',request_number='REQ',equipment_code='E',operator_id='fake',owner_user_id='alice',
        report_options=authored_mail_options(owner_user_id='mallory',owner_analysis_type='TEM'))
    assert db.report_runs[-1]['owner_user_id']=='alice' and db.report_runs[-1]['owner_analysis_type']=='FTIR'
    job=SimpleNamespace(job_id='mine',status='completed',package_path=package,owner_user_id='alice')
    result=send_preview_report_package(settings=config,database=db,job=job,
        payload=PreviewReportSendRequest(requestNumber='REQ',experimentCode='A11111',equipmentCode='E',operatorId='verified-sso'))
    assert result['queued'] and db.values['experiment_code']=='FTIR'
    assert json.loads(db.values['report_options_json'])['testMtdCode']=='A11111'
    assert db.report_runs[-1]['owner_user_id']=='alice' and db.report_runs[-1]['owner_analysis_type']=='FTIR'
    register_generated_report_package(settings=config,database=db,report_id='unknown',package_path=package,
        experiment_code='XRD',report_options={'owner_user_id':'alice'})
    assert 'owner_user_id' not in db.report_runs[-1]

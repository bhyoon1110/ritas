import io
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.path_bootstrap import add_project_package_paths
add_project_package_paths()
from app.auth import AuthContext
from app.config import Settings
from app.errors import ApiException
from app.xrd_workbench import integration as api
from app.xrd_workbench.core.report import new_document_with_defaults
from app.xrd_workbench.core.figure import group_key
from app.xrd_workbench.storage import Storage
from app.xrd_workbench.core.errors import RevisionConflict
from .synthetic import sample_files


def make_app(root, auth=False):
    app = FastAPI()
    app.state.settings = Settings(storage_root=root, auth_enabled=auth)
    app.state.database = None
    @app.middleware('http')
    async def identity(request: Request, next_handler):
        if auth and request.headers.get('test-user'):
            request.state.auth_context = AuthContext(
                user_id=request.headers['test-user'], login_id='user01', email=None,
                display_name='Analyst', status='ACTIVE', session_id='test',
                session_expires_at=datetime.utcnow()+timedelta(hours=1),
                sso_authenticated_at=(datetime.utcnow()-timedelta(minutes=int(request.headers.get('test-sso-age', '0')))) if request.headers.get('test-sso') else None,
                sso_identity={'employee_id':'SSO-01'} if request.headers.get('test-sso') else None,
                projects=frozenset({request.headers.get('test-project', 'XRD')}),
                roles=frozenset({'REPORT_SENDER'}))
        return await next_handler(request)
    @app.exception_handler(ApiException)
    async def error(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({'error':{'code':exc.code, 'message':exc.message}}, status_code=exc.status_code)
    app.include_router(api.router)
    return app


def poll(client, job):
    deadline = time.monotonic()+40
    while job['status'] in ('queued', 'running') and time.monotonic() < deadline:
        time.sleep(.02)
        r = client.get(api.BASE + '/api/jobs/' + job['job_id'])
        assert r.status_code == 200, r.text
        job = r.json()
    assert job['status'] == 'completed', job
    return job


@pytest.mark.parametrize('legacy', [False, True])
def test_shared_manager_style_is_served_on_all_workbench_pages(tmp_path, legacy):
    client = TestClient(make_app(tmp_path))
    headers = {'user-agent': 'Mozilla/5.0 Chrome/49.0.2623.75 Safari/537.36'} if legacy else {}
    stylesheet = '/shared/project_manager' + ('.legacy' if legacy else '') + '.css'
    for page in ('/', '/analysis.html', '/report.html'):
        response = client.get(api.BASE + page, headers=headers)
        assert response.status_code == 200
        assert api.BASE + stylesheet in response.text
    response = client.get(api.BASE + stylesheet, headers=headers)
    assert response.status_code == 200
    assert '.ristWorkManager' in response.text


def upload(client, files):
    sid = client.post(api.BASE+'/api/upload-sessions').json()['upload_id']
    for name, data in files.items():
        pieces = [data[:len(data)//2], data[len(data)//2:]]
        offset = 0
        for n, part in enumerate(pieces):
            args = dict(relative_path=name, offset=offset, total_size=len(data), chunk_index=n, chunk_count=2)
            r = client.post(api.BASE+f'/api/upload-sessions/{sid}/chunks', data=args,
                            files={'file':(name, part, 'application/octet-stream')})
            assert r.status_code == 200, r.text
            # Retry the identical chunk; this must not duplicate data.
            r = client.post(api.BASE+f'/api/upload-sessions/{sid}/chunks', data=args,
                            files={'file':(name, part, 'application/octet-stream')})
            assert r.status_code == 200, r.text
            offset += len(part)
    first = client.post(api.BASE+f'/api/upload-sessions/{sid}/complete', json={})
    assert first.status_code == 202, first.text
    again = client.post(api.BASE+f'/api/upload-sessions/{sid}/complete', json={})
    assert again.json()['job_id'] == first.json()['job_id']
    job = poll(client, first.json())
    return job['storage_id']


def test_chunk_import_review_and_all_exports(tmp_path):
    client = TestClient(make_app(tmp_path))
    files = sample_files('SampleA')
    sid = upload(client, files)
    project = client.get(api.BASE+f'/api/projects/{sid}').json()
    assert project['samples'][0]['measurement']['intensity_unit'] == 'cps'
    assert project['samples'][0]['quantities'][0]['raw'] == '60.0(1)'
    for record in project['sources']:
        r = client.get(api.BASE+f'/api/files/{sid}/{record["id"]}')
        assert r.content == files[record['filename']]
    doc = client.get(api.BASE+f'/api/reports/{sid}').json()['document']
    assert doc['phase_classes']['labels'] == []
    assert doc['phase_classes']['rule'] is None
    doc['metadata']['report_number'] = 'QA-001'
    saved = client.put(api.BASE+f'/api/reports/{sid}', json={'document':doc, 'expected_revision':0})
    assert saved.status_code == 200, saved.text
    assert client.put(api.BASE+f'/api/reports/{sid}', json={'document':doc, 'expected_revision':0}).status_code == 409
    for kind in ('preview', 'pdf', 'pptx', 'zip'):
        response = client.post(api.BASE+f'/api/reports/{sid}/exports', json={'kind':kind, 'revision':1})
        assert response.status_code == 202, response.text
        job = poll(client, response.json())
        result = client.get(api.BASE+f'/api/jobs/{job["job_id"]}/file')
        assert result.status_code == 200, result.text[:100]
        if kind == 'pdf':
            assert result.content.startswith(b'%PDF')
        elif kind == 'preview':
            assert 'contenteditable' not in result.text
            assert '분석 결과 요약' in result.text and 'Phase Fraction' in result.text
            assert '보고서 정보' not in result.text
            assert result.text.count('<section class="page"') == 4  # cover, traceability, summary, one quantitative sample
            assert api.BASE+'/api/files/' in result.text
        elif kind == 'zip':
            with zipfile.ZipFile(io.BytesIO(result.content)) as z:
                assert {name.split('/')[0] for name in z.namelist()} == {
                    'Measured Data', 'Report', '보고서.pdf', '보고서.pptx', '보고서.html', '뷰어.html',
                }
                assert b'XRD_EMBEDDED' in z.read('뷰어.html')
                assert b'<script src="/' not in z.read('뷰어.html')
                assert b'<script nomodule>' in z.read('뷰어.html')
                assert b'<script type="module">' in z.read('뷰어.html')
                with zipfile.ZipFile(io.BytesIO(z.read('보고서.pptx'))) as ppt:
                    assert b'cx="9906000"' in ppt.read('ppt/presentation.xml')
        elif kind == 'pptx':
            with zipfile.ZipFile(io.BytesIO(result.content)) as ppt:
                assert b'cx="9906000"' in ppt.read('ppt/presentation.xml')
                assert b'cy="6858000"' in ppt.read('ppt/presentation.xml')


def test_access_is_scoped_for_projects_files_sessions_jobs_and_presets(tmp_path):
    client = TestClient(make_app(tmp_path, auth=True), headers={'test-user':'one'})
    sid = upload(client, {'A.txt':b'#Intensity_unit=cps\n10 1\n20 8\n30 3\n'})
    project = client.get(api.BASE+f'/api/projects/{sid}').json()
    session = client.post(api.BASE+'/api/upload-sessions').json()['upload_id']
    preset = client.post(api.BASE+'/api/presets', json={'name':'Private', 'settings':{}}).json()
    client.headers['test-user'] = 'two'
    assert client.get(api.BASE+'/api/projects').json() == []
    assert client.get(api.BASE+f'/api/projects/{sid}').status_code == 404
    assert client.get(api.BASE+f'/api/reports/{sid}').status_code == 404
    assert client.get(api.BASE+f'/api/files/{sid}/{project["sources"][0]["id"]}').status_code == 404
    assert client.post(api.BASE+f'/api/upload-sessions/{session}/complete', json={}).status_code == 404
    assert client.post(api.BASE+f'/api/reports/{sid}/exports', json={'kind':'pdf', 'revision':1}).status_code == 404
    assert client.get(api.BASE+'/api/presets').json() == []
    assert client.delete(api.BASE+'/api/presets/'+preset['id']).status_code == 404
    client.headers['test-project'] = 'FTIR'
    assert client.get(api.BASE+'/').status_code == 403
    del client.headers['test-user']
    assert client.get(api.BASE+'/api/projects').status_code == 401


def test_supermium144_native_and_chrome49_bundle_and_origin_guard(tmp_path):
    client = TestClient(make_app(tmp_path))
    r = client.get(api.BASE+'/', headers={'User-Agent':'Mozilla/5.0 (Windows NT 5.1) Chrome/144.0.0.0 Supermium/144.0'})
    assert r.status_code == 200
    assert 'type="module"' in r.text and '.legacy.js' not in r.text
    r = client.get(api.BASE+'/', headers={'User-Agent':'Mozilla/5.0 (Windows NT 5.1) Chrome/49.0.2623.75'})
    assert '.legacy.js' in r.text and 'type="module"' not in r.text
    assert client.get(api.BASE+'/viewer/viewer.legacy.js').status_code == 200
    assert '.legacy.css' in r.text
    assert client.post(api.BASE+'/api/upload-sessions', headers={'Origin':'https://foreign.invalid'}).status_code == 403
    assert client.get(api.BASE+'/../storage.py').status_code == 404
    assert client.post(api.BASE+'/api/upload-sessions/00000000000000000000000000000000/chunks',
                       content=b'x', headers={'Content-Length':str(6*1024*1024)}).status_code == 413
    assert client.post(api.BASE+'/api/upload-sessions', content=b'x'*(8*1024*1024+1)).status_code == 413
    for length in ('invalid', '-1'):
        assert client.post(api.BASE+'/api/upload-sessions',
                           headers={'Content-Length':length}).status_code == 400


def test_independent_storage_instances_enforce_atomic_revisions(tmp_path):
    stores = [Storage(tmp_path), Storage(tmp_path)]
    p = stores[0].files.create([('A.txt', b'10 1\n20 2\n30 1\n')], {})
    doc = new_document_with_defaults(p)
    def save(store):
        try:
            return store.reports.save(p['storage_id'], p, doc, 0)['revision']
        except RevisionConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, stores))
    assert sorted(map(str, results)) == ['1','conflict']


def test_different_cards_or_stoichiometry_never_auto_merge():
    a = dict(name='FeO', card='A', db='ICDD', formula='FeO')
    b = dict(name='Fe2O3', card='B', db='ICDD', formula='Fe2O3')
    assert group_key(a) != group_key(b)


def test_sso_guard_registration_and_existing_queue_adapter(tmp_path, monkeypatch):
    registered, sent = [], []
    monkeypatch.setattr(api, 'register_generated_report_package', lambda **kw: registered.append(kw))
    monkeypatch.setattr(api, 'send_preview_report_package', lambda **kw: sent.append(kw) or {'sent':False, 'status':'PENDING'})
    client = TestClient(make_app(tmp_path, auth=True), headers={'test-user':'one'})
    sid = upload(client, {'A.txt':b'10 1\n20 8\n30 3\n'})
    doc = client.get(api.BASE+f'/api/reports/{sid}').json()['document']
    doc['metadata'].update(report_number='REQ-01', experiment_code='XRD', equipment_code='XRD-01', operator_id='SSO-01')
    assert client.put(api.BASE+f'/api/reports/{sid}', json={'document':doc,'expected_revision':0}).status_code == 200
    transfer = {'requestNumber':'REQ-01','experimentCode':'XRD','equipmentCode':'XRD-01','operatorId':'SSO-01'}
    job = poll(client, client.post(api.BASE+f'/api/reports/{sid}/exports', json={'kind':'zip','revision':1,'transfer':transfer}).json())
    assert len(registered) == 1 and not sent
    assert registered[0]['request_number'] == 'REQ-01'
    route = api.BASE+f'/api/jobs/{job["job_id"]}/send'
    assert client.post(route, json=transfer).json()['error']['code'] == 'SSO_LINK_REQUIRED'
    client.headers['test-sso'] = 'yes'
    client.headers['test-sso-age'] = '9999'
    assert client.post(route, json=transfer).json()['error']['code'] == 'SSO_REAUTH_REQUIRED'
    client.headers['test-sso-age'] = '0'
    assert client.post(route, json={**transfer, 'requestNumber':'different'}).status_code == 409
    # Client operator spoofing never overrides the authenticated SSO identity.
    result = client.post(route, json={**transfer, 'operatorId':'spoofed'})
    assert result.status_code == 200, result.text
    assert sent[0]['payload'].operator_id == 'SSO-01'
    assert sent[0]['job'].package_path.is_file()
    client.headers['test-user'] = 'two'
    assert client.get(api.BASE+f'/api/jobs/{job["job_id"]}').status_code == 404
    assert client.get(api.BASE+f'/api/jobs/{job["job_id"]}/file').status_code == 404
    assert client.post(route, json=transfer).status_code == 404


def test_source_mutation_and_resource_limits_are_explicit(tmp_path):
    from app.xrd_workbench.core.parsing.txt import parse_txt, MAX_MEASUREMENT_POINTS
    store = Storage(tmp_path)
    project = store.files.create([('A.txt', b'10 1\n20 8\n')], {})
    source = project['sources'][0]
    path, _ = store.files.source_file(project['storage_id'], source['id'])
    path.write_bytes(b'changed')
    with pytest.raises(ValueError, match='변경'):
        store.files.source_file(project['storage_id'], source['id'])
    with pytest.raises(ValueError, match='측정점'):
        parse_txt('\n'.join(f'{n} 1' for n in range(MAX_MEASUREMENT_POINTS+1)).encode(), 'large.txt')


def test_job_state_survives_app_recreation_and_marks_dead_workers(tmp_path, monkeypatch):
    client = TestClient(make_app(tmp_path))
    sid = upload(client, {'A.txt':b'10 1\n20 2\n'})
    restarted = TestClient(make_app(tmp_path))
    assert restarted.get(api.BASE+f'/api/projects/{sid}').status_code == 200
    record = next(tmp_path.glob('xrd-workbench-v1/users/*/jobs/*/job.json'))
    import json
    job = json.loads(record.read_text())
    job['status'] = 'running'
    record.write_text(json.dumps(job))
    monkeypatch.setattr(api.os, 'kill', lambda *_: (_ for _ in ()).throw(ProcessLookupError()))
    assert restarted.get(api.BASE+f'/api/jobs/{job["job_id"]}').json()['status'] == 'failed'

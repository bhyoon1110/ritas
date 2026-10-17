"""Project metadata only: owner isolation, reversible removal, original/report preservation."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from app.xrd_workbench import integration as api
from app.xrd_workbench.core.errors import RevisionConflict
from app.xrd_workbench.core.report import new_document_with_defaults
from app.xrd_workbench.core.review import new_review
from app.xrd_workbench.storage import Storage
from .test_integration import make_app, upload

TXT = b'#Intensity_unit=cps\n10 1\n20 8\n30 3\n'


def test_archive_restore_preserves_all_data_and_report_revisions(tmp_path):
    store = Storage(tmp_path)
    project = store.files.create([('Sample.txt', TXT)], {})
    sid = project['storage_id']
    review = store.reviews.save(sid, project, new_review(project), 0)
    effective = store.load_project(sid)
    report = store.reports.save(sid, effective, new_document_with_defaults(effective), 0)
    files = [p for p in tmp_path.rglob('*') if p.is_file() and p.suffix != '.lock']
    before = {p: p.read_bytes() for p in files}
    meta = store.files.manage(sid, 0, label='새 이름 <test>')
    assert meta['label'] == '새 이름 <test>'
    assert store.load_project(sid)['samples'][0]['name'] == 'Sample'
    assert meta['sample_count'] == 1 and meta['file_count'] == 1 and meta['total_bytes'] == len(TXT)
    archived = store.files.manage(sid, 1, archived=True)
    assert store.files.list() == []
    assert store.files.list(archived=True) == [archived]
    with pytest.raises(ValueError, match='휴지통'):
        store.load_project(sid)
    assert store.files.source_file(sid, project['sources'][0]['id']) is None
    # A queued export with an existing document snapshot can still finish.
    assert store.files.read_source(sid, project['sources'][0]['id']) == TXT
    restored = Storage(tmp_path).files.manage(sid, 2, archived=False)
    assert restored['managed_revision'] == 3 and restored['archived_at'] is None
    assert restored['created_at'] == meta['created_at']
    assert store.files.list(archived=True) == []
    assert store.reports.load(sid) == report
    assert store.reviews.load(sid) == review
    assert {p: p.read_bytes() for p in files} == before


@pytest.mark.parametrize('label', ['', '   ', 'x'*101, '\nname', 'a\x00b', 123])
def test_invalid_names_cannot_mutate_files(tmp_path, label):
    store = Storage(tmp_path)
    sid = store.files.create([('Sample.txt', TXT)], {})['storage_id']
    with pytest.raises(ValueError):
        store.files.manage(sid, 0, label=label)
    assert not (tmp_path/'uploads'/sid/'management.json').exists()


def test_atomic_management_conflicts_across_storage_instances(tmp_path):
    first, second = Storage(tmp_path), Storage(tmp_path)
    sid = first.files.create([('Sample.txt', TXT)], {})['storage_id']
    def edit(store):
        try:
            store.files.manage(sid, 0, archived=True)
            return 'ok'
        except RevisionConflict:
            return 'conflict'
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(edit, [first, second])) == ['conflict', 'ok']
    assert first.files.list(archived=True)[0]['managed_revision'] == 1


def test_legacy_projects_have_default_metadata_and_examples_are_readonly(tmp_path):
    store = Storage(tmp_path)
    sid = store.files.create([('Old.txt', TXT)], {})['storage_id']
    assert store.files.list()[0]['managed_revision'] == 0
    assert store.files.list()[0]['label'] == 'Old'
    with pytest.raises(ValueError, match='예제'):
        store.files.manage('example', 0, archived=True)
    with pytest.raises(ValueError):
        store.files.manage('../outside', 0, archived=True)
    with pytest.raises(RevisionConflict):
        store.files.manage(sid, True, archived=True)


def test_api_delete_restore_guards_edits_and_preserves_drafts_jobs(tmp_path):
    client = TestClient(make_app(tmp_path))
    sid = upload(client, {'Sample.txt': TXT})
    url = api.BASE + '/api/projects/' + sid
    report_url = api.BASE + '/api/reports/' + sid
    doc = client.get(report_url).json()['document']
    assert client.put(report_url, json={'document': doc, 'expected_revision': 0}).status_code == 200
    doc = client.get(report_url).json()['document']
    assert client.put(report_url + '/draft', json={'document': doc, 'baseline': doc, 'expected_revision': 0}).status_code == 200
    root = tmp_path/'xrd-workbench-v1'/'users'/hashlib.sha256(b'development').hexdigest()
    # Completed artifacts are intentionally independent from project list membership.
    jid = 'd'*32
    jobdir = root/'jobs'/jid
    jobdir.mkdir()
    (jobdir/'report.zip').write_bytes(b'previously-generated-artifact')
    (jobdir/'job.json').write_text(json.dumps(dict(job_id=jid, status='completed', kind='zip', filename='report.zip', storage_id=sid)))
    before = {p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    renamed = client.patch(url, json={'label': '관리 이름', 'expected_revision': 0})
    assert renamed.status_code == 200 and renamed.json()['managed_revision'] == 1
    assert client.request('DELETE', url, json={'expected_revision': 0}).status_code == 409
    assert client.request('DELETE', url, json={'expected_revision': 1}).status_code == 200
    assert client.get(api.BASE+'/api/projects').json() == []
    trash = client.get(api.BASE+'/api/projects?archived=true')
    assert trash.headers['cache-control'] == 'private, no-store'
    assert trash.json()[0]['label'] == '관리 이름'
    for endpoint in (url, report_url, report_url+'/draft', api.BASE+'/api/analysis/'+sid):
        assert client.get(endpoint).status_code == 404
    assert client.put(report_url, json={'document': doc, 'expected_revision': 1}).status_code == 404
    assert client.get(api.BASE+f'/api/jobs/{jid}/file').content == b'previously-generated-artifact'
    assert {p:p.read_bytes() for p in before} == before
    assert client.post(url+'/restore', json={'expected_revision': 2}).status_code == 200
    assert client.get(report_url).json()['document'] == doc
    assert client.get(report_url+'/draft').json()['document'] == doc
    assert client.get(url).status_code == 200


def test_project_management_owner_permission_and_origin_isolation(tmp_path):
    client = TestClient(make_app(tmp_path, auth=True))
    client.headers.update({'test-user': 'owner', 'test-project': 'XRD'})
    sid = upload(client, {'Private.txt': TXT})
    url = api.BASE + '/api/projects/' + sid
    client.headers['test-user'] = 'another-user'
    assert client.get(api.BASE+'/api/projects').json() == []
    assert client.get(api.BASE+'/api/projects?archived=true').json() == []
    for method, path, data in [('PATCH', url, {'label':'stolen','expected_revision':0}), ('DELETE',url,{'expected_revision':0}), ('POST',url+'/restore',{'expected_revision':0})]:
        assert client.request(method, path, json=data).status_code == 422
    client.headers['test-project'] = 'FTIR'
    assert client.request('DELETE', url, json={'expected_revision':0}).status_code == 403
    client.headers.update({'test-user':'owner','test-project':'XRD', 'Origin':'https://untrusted.invalid'})
    assert client.request('DELETE', url, json={'expected_revision':0}).status_code == 403
    del client.headers['Origin']
    assert client.get(url).status_code == 200
    client.headers.clear()
    assert client.request('DELETE', url, json={'expected_revision':0}).status_code == 401


def test_export_queued_before_archive_can_still_finish(tmp_path, monkeypatch):
    from types import SimpleNamespace
    client = TestClient(make_app(tmp_path))
    sid = upload(client, {'Sample.txt': TXT})
    report_url = api.BASE + '/api/reports/' + sid
    doc = client.get(report_url).json()['document']
    assert client.put(report_url, json={'document': doc, 'expected_revision':0}).status_code == 200
    pending = []
    monkeypatch.setattr(api, 'executor', SimpleNamespace(submit=lambda work: pending.append(work)))
    response = client.post(report_url+'/exports', json={'kind':'preview','revision':1})
    assert response.status_code == 202
    jid = response.json()['job_id']
    assert client.request('DELETE',api.BASE+'/api/projects/'+sid,json={'expected_revision':0}).status_code == 200
    pending[0]()
    assert client.get(api.BASE+'/api/jobs/'+jid).json()['status'] == 'completed'
    response = client.get(api.BASE+'/api/jobs/'+jid+'/file')
    assert response.status_code == 200 and 'html' in response.headers['content-type']

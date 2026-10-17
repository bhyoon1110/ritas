"""Permanent deletion tests operate only on synthetic per-test temporary storage."""
import hashlib
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.xrd_workbench import integration as api
from app.xrd_workbench import project_cleanup as module
from app.xrd_workbench.project_cleanup import ProjectCleanup
from app.xrd_workbench.storage import Storage, _write_json_atomic
from .test_integration import make_app, upload
from .test_project_management import TXT


def root_at(tmp_path, user='development'):
    return tmp_path/'xrd-workbench-v1'/'users'/hashlib.sha256(user.encode()).hexdigest()


def cleanup_at(root):
    return ProjectCleanup(SimpleNamespace(home=root, storage=Storage(root)))


def create(cleanup, name='Sample', archived=True):
    sid = cleanup.state.storage.files.create([(name+'.txt', TXT)], {})['storage_id']
    if archived:
        cleanup.state.storage.files.manage(sid, 0, archived=True)
    return sid


def wait(client, response):
    assert response.status_code == 202, response.text
    job = response.json()
    for _ in range(200):
        job = client.get(api.BASE+'/api/jobs/'+job['job_id']).json()
        if job['status'] not in ('queued', 'running'):
            return job
        time.sleep(.01)
    pytest.fail('cleanup job did not finish')


def confirm(plan):
    return dict(plan_id=plan['plan_id'], expected_count=plan['count'], confirmation='영구 삭제')


def test_catalog_pagination_search_sort_and_recent_dropdown(tmp_path):
    client = TestClient(make_app(tmp_path))
    cleanup = cleanup_at(root_at(tmp_path))
    ids = [create(cleanup, f'Sample {i:02}', archived=False) for i in range(63)]
    path = api.BASE+'/api/project-catalog'
    first = client.get(path).json()
    assert first['total'] == 63 and len(first['items']) == 20 and first['pages'] == 4
    last = client.get(path+'?page=999').json()
    assert last['page'] == 4 and len(last['items']) == 3
    named = client.get(path+'?q=sample%200&sort=name&per_page=10').json()
    assert [p['label'] for p in named['items']] == [f'Sample {i:02}' for i in range(10)]
    assert named['total_in_tab'] == 63 and named['total'] == 10
    for query in ('sort=invalid', 'page=0', 'per_page=100', 'q='+'x'*101):
        assert client.get(path+'?'+query).status_code == 422
    choices = client.get(api.BASE+'/api/projects?limit=20&current='+ids[0]).json()
    assert len(choices) == 21 and choices[-1]['storage_id'] == ids[0]
    assert len(client.get(api.BASE+'/api/projects?limit=20&current='+ids[-1]).json()) == 20
    oldest = client.get(path+'?sort=oldest').json()
    assert oldest['items'][0]['storage_id'] == ids[0]


def test_summary_cache_does_not_reparse_raw_arrays(tmp_path, monkeypatch):
    cleanup = cleanup_at(tmp_path)
    sid = create(cleanup, archived=False)
    before = cleanup.state.storage.files.list()
    read = Path.read_text
    def read_checked(path, *args, **kwargs):
        if path.name == 'project.json':
            raise AssertionError('large raw arrays reparsed')
        return read(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read_checked)
    assert cleanup.state.storage.files.list() == before
    assert (tmp_path/'uploads'/sid/'summary.json').exists()


def test_unwritable_summary_cache_does_not_hide_readable_projects(tmp_path, monkeypatch):
    from app.xrd_workbench import storage
    cleanup = cleanup_at(tmp_path)
    sid = create(cleanup, archived=False)
    monkeypatch.setattr(storage, '_write_json_atomic', lambda *args: (_ for _ in ()).throw(OSError('disk full')))
    assert cleanup.state.storage.files.list()[0]['storage_id'] == sid


def test_batch_validates_whole_selection_then_archive_restore(tmp_path):
    client = TestClient(make_app(tmp_path))
    cleanup = cleanup_at(root_at(tmp_path))
    ids = [create(cleanup, str(i), archived=False) for i in range(3)]
    entries = [dict(storage_id=sid, expected_revision=0) for sid in ids]
    url = api.BASE+'/api/projects/manage-batch'
    entries[-1]['expected_revision'] = 9
    assert client.post(url, json=dict(action='archive', items=entries)).status_code == 409
    assert len(cleanup.state.storage.files.list()) == 3
    entries[-1]['expected_revision'] = 0
    assert client.post(url, json=dict(action='archive', items=entries)).json()['count'] == 3
    assert cleanup.state.storage.files.list() == []
    for item in entries: item['expected_revision'] = 1
    assert client.post(url, json=dict(action='restore', items=entries)).status_code == 200
    assert len(cleanup.state.storage.files.list()) == 3
    for items in ([None], [{'storage_id':[]}], entries*2, [], entries*18):
        assert client.post(url, json=dict(action='archive', items=items)).status_code == 422


def test_cleanup_removes_only_archived_working_data_and_session_copies(tmp_path):
    client = TestClient(make_app(tmp_path))
    sid = upload(client, {'Sample.txt':TXT})
    root = root_at(tmp_path)
    cleanup = cleanup_at(root)
    cleanup.state.storage.files.manage(sid, 0, archived=True)
    other = create(cleanup, 'Keep', archived=False)
    for path in (root/'analysis'/sid/'analysis.json', root/'reports'/sid/'report.json', root/'drafts'/(sid+'.json')):
        _write_json_atomic(path, {'keep_until_purge':True})
    preserved = [root/'jobs'/('a'*32)/'report.zip', tmp_path/'shared-lims'/'report.zip',
                 tmp_path/'TEM'/'data.bin', root_at(tmp_path,'another')/'data.bin']
    for path in preserved:
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'untouched')
    _write_json_atomic(preserved[0].with_name('job.json'), dict(status='completed',kind='zip',storage_id=sid))
    plan_response = client.post(api.BASE+'/api/project-purges/plan', json={'all_trash':True})
    assert plan_response.status_code == 200
    plan = plan_response.json()
    assert plan['count'] == 1 and plan['total_bytes'] > len(TXT)
    job = wait(client, client.post(api.BASE+'/api/project-purges', json=confirm(plan)))
    assert job['status'] == 'completed', job
    assert job['deleted_count'] == 1
    assert not (root/'uploads'/sid).exists()
    assert not (root/'analysis'/sid).exists() and not (root/'reports'/sid).exists()
    assert not (root/'drafts'/(sid+'.json')).exists()
    assert list((root/'sessions').iterdir()) == []
    assert (root/'uploads'/other/'project.json').exists()
    assert all(p.read_bytes() == b'untouched' for p in preserved)
    assert client.post(api.BASE+'/api/project-purges',json=confirm(plan)).json()['job_id'] == job['job_id']
    assert client.get(api.BASE+'/api/projects?archived=true').json() == []


def test_confirmation_snapshot_restore_race_and_expiry(tmp_path):
    client = TestClient(make_app(tmp_path))
    cleanup = cleanup_at(root_at(tmp_path))
    first = create(cleanup)
    plan = cleanup.prepare()
    url = api.BASE+'/api/project-purges'
    for change in ({'confirmation':'yes'}, {'expected_count':2}, {'expected_count':True}):
        response = client.post(url, json={**confirm(plan), **change})
        assert response.status_code in (409,422)
    cleanup.state.storage.files.manage(first,1,archived=False)
    assert client.post(url,json=confirm(plan)).status_code == 409
    assert (cleanup.home/'uploads'/first).exists()
    cleanup.state.storage.files.manage(first,2,archived=True)
    plan = cleanup.prepare()
    second = create(cleanup,'Later')
    assert wait(client,client.post(url,json=confirm(plan)))['status'] == 'completed'
    assert (cleanup.home/'uploads'/second).exists()  # snapshot excludes newly trashed projects
    plan = cleanup.prepare()
    path = cleanup.plan_path(plan['plan_id'])
    value = json.loads(path.read_text()); value['created_at'] -= 901; _write_json_atomic(path,value)
    assert client.post(url,json=confirm(plan)).status_code == 409
    assert (cleanup.home/'uploads'/second).exists()


def test_owner_auth_origin_and_active_project_protection(tmp_path):
    client = TestClient(make_app(tmp_path,auth=True))
    client.headers.update({'test-user':'owner','test-project':'XRD'})
    cleanup = cleanup_at(root_at(tmp_path,'owner'))
    active = create(cleanup,archived=False)
    url = api.BASE+'/api/project-purges'
    assert client.post(url+'/plan',json={'items':[dict(storage_id=active,expected_revision=0)]}).status_code == 409
    sid = create(cleanup)
    plan = cleanup.prepare()
    client.headers['test-user'] = 'other'
    assert client.post(url,json=confirm(plan)).status_code == 409
    assert client.post(url+'/plan',json={'items':[dict(storage_id=sid,expected_revision=1)]}).status_code == 409
    client.headers['test-project']='FTIR'
    assert client.post(url,json=confirm(plan)).status_code == 403
    client.headers.update({'test-user':'owner','test-project':'XRD','Origin':'https://bad.invalid'})
    assert client.post(url,json=confirm(plan)).status_code == 403
    client.headers.clear()
    assert client.post(url,json=confirm(plan)).status_code == 401
    assert (cleanup.home/'uploads'/sid).exists()


@pytest.mark.parametrize('status',['queued','running'])
def test_pending_work_blocks_plan_and_execution(tmp_path,status):
    cleanup = cleanup_at(tmp_path)
    sid = create(cleanup)
    plan = cleanup.prepare()
    path = tmp_path/'jobs'/('a'*32)/'job.json'
    _write_json_atomic(path,dict(status=status,pid=os.getpid()))
    with pytest.raises(ValueError,match='진행 중'):
        cleanup.prepare()
    with pytest.raises(ValueError,match='진행 중'):
        cleanup.execute(plan['plan_id'],'b'*32)
    assert (tmp_path/'uploads'/sid).exists()


@pytest.mark.parametrize('target',['../outside','example','/'+'f'*32,123])
def test_invalid_ids_fail_closed(tmp_path,target):
    cleanup = cleanup_at(tmp_path)
    with pytest.raises(ValueError):
        cleanup.prepare([dict(storage_id=target,expected_revision=1)])
    with pytest.raises(ValueError):
        cleanup.plan_path(target)


def test_symlinks_are_never_followed_for_deletion(tmp_path):
    cleanup = cleanup_at(tmp_path/'owner')
    sid = create(cleanup)
    outside = tmp_path/'outside'; outside.mkdir(); (outside/'keep').write_bytes(b'keep')
    (cleanup.home/'uploads'/sid/'linked').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match='연결'):
        cleanup.prepare()
    assert (outside/'keep').read_bytes() == b'keep'


def test_stage_rename_failure_rolls_back_all_sources(tmp_path,monkeypatch):
    cleanup = cleanup_at(tmp_path)
    sid = create(cleanup)
    _write_json_atomic(tmp_path/'reports'/sid/'report.json',{'test':True})
    plan = cleanup.prepare()
    rename = Path.rename
    def fail_second(source,destination):
        if source == tmp_path/'reports'/sid: raise OSError('simulated disk error')
        return rename(source,destination)
    monkeypatch.setattr(Path,'rename',fail_second)
    with pytest.raises(OSError): cleanup.execute(plan['plan_id'],'b'*32)
    assert (tmp_path/'uploads'/sid/'project.json').exists()
    assert (tmp_path/'reports'/sid/'report.json').exists()
    assert json.loads(cleanup.plan_path(plan['plan_id']).read_text())['phase'] == 'planned'


def test_committed_cleanup_failure_can_resume_without_touching_other_data(tmp_path,monkeypatch):
    cleanup = cleanup_at(tmp_path)
    sid = create(cleanup)
    plan = cleanup.prepare()
    real = module.shutil.rmtree
    monkeypatch.setattr(module.shutil,'rmtree',lambda path: (_ for _ in ()).throw(OSError('simulated error')))
    with pytest.raises(OSError): cleanup.execute(plan['plan_id'],'b'*32)
    assert not (tmp_path/'uploads'/sid).exists()
    assert json.loads(cleanup.plan_path(plan['plan_id']).read_text())['phase'] == 'committed'
    keep = create(cleanup,'Later')
    monkeypatch.setattr(module.shutil,'rmtree',real)
    assert cleanup.execute(plan['plan_id'],'b'*32)['deleted_count'] == 1
    assert (tmp_path/'uploads'/keep).exists()


def test_interrupted_staging_is_preserved_for_admin(tmp_path):
    cleanup = cleanup_at(tmp_path)
    sid = create(cleanup)
    plan = cleanup.prepare()
    path = cleanup.plan_path(plan['plan_id'])
    value = json.loads(path.read_text()); value['phase']='staging'; _write_json_atomic(path,value)
    with pytest.raises(ValueError,match='관리자'):
        cleanup.execute(plan['plan_id'],'b'*32)
    assert (tmp_path/'uploads'/sid).exists()


def test_restore_after_queueing_cancels_purge_before_any_files_move(tmp_path, monkeypatch):
    client = TestClient(make_app(tmp_path))
    cleanup = cleanup_at(root_at(tmp_path))
    first, second = create(cleanup), create(cleanup,'Second')
    plan = cleanup.prepare()
    pending = []
    monkeypatch.setattr(api,'executor',SimpleNamespace(submit=lambda work: pending.append(work)))
    response = client.post(api.BASE+'/api/project-purges',json=confirm(plan))
    assert response.status_code == 202
    cleanup.state.storage.files.manage(second,1,archived=False)
    pending[0]()
    assert wait(client,response)['status'] == 'failed'
    assert all((cleanup.home/'uploads'/sid/'project.json').exists() for sid in [first,second])


def test_staging_receipt_write_failure_preserves_original_and_allows_retry(tmp_path,monkeypatch):
    cleanup = cleanup_at(tmp_path)
    sid = create(cleanup)
    plan = cleanup.prepare()
    real = module._write_json_atomic
    def failing(path, value):
        if value.get('phase') == 'staging': raise OSError('disk full')
        return real(path,value)
    monkeypatch.setattr(module,'_write_json_atomic',failing)
    with pytest.raises(OSError): cleanup.execute(plan['plan_id'],'b'*32)
    assert (tmp_path/'uploads'/sid/'project.json').exists()
    monkeypatch.setattr(module,'_write_json_atomic',real)
    assert cleanup.execute(plan['plan_id'],'b'*32)['deleted_count'] == 1

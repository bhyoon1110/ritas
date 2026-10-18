import json
import asyncio
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app import ahn_web as ahn, tem_history as history
from app.errors import ApiException
from app.storage import atomic_write_json
from test_tem_resume import actor

HEADERS = {"X-Requested-With": "RIST-TEM-History"}


@pytest.fixture
def workspace(tmp_path, monkeypatch, tem_unit_runtime):
    app = ahn.create_tem_preview_app()
    app.state.settings = replace(app.state.settings, auth_enabled=True)
    app.state.actor = actor("author")
    @app.middleware("http")
    async def identity(request, call_next):
        request.state.auth_context = app.state.actor
        return await call_next(request)
    with TestClient(app) as client:
        yield SimpleNamespace(app=app, client=client, settings=app.state.settings)
    with ahn._ahn_report_jobs_lock:
        for key, job in list(ahn._ahn_report_jobs.items()):
            if history.managed(job) and history.root(job.settings) == history.root(app.state.settings):
                ahn._ahn_report_jobs.pop(key)


def make(w, title="Sample A", status="completed", owner="author"):
    directory = history.new_workspace(w.settings)
    source = directory / "input/reports"
    source.mkdir(parents=True)
    (source / "sample.docx").write_bytes(b"test source")
    job = ahn._create_ahn_job(directory / "input", directory, None, w.settings, owner_user_id=owner)
    job.output_dir.mkdir()
    job.pptx_path.write_bytes(b"pptx fixture")
    job.package_path.write_bytes(b"zip fixture")
    job.analysis_path.write_text("{}")
    atomic_write_json(job.manifest_path, {"summary": {"reportCount": 1}})
    job.history_title = title
    job.transfer_metadata = {"requestNumber": "REQ-001"}
    ahn._set_job_state(job, status=status, manifest={"summary": {"reportCount": 1}})
    return job


def action(w, job, name, **values):
    return w.client.post("/api/v1/tem/history/" + job.job_id,
                         json={"action": name, **values}, headers=HEADERS)


def test_owner_filter_search_pagination_and_admin(workspace):
    w = workspace
    own = make(w, "검색 시료 A")
    other = make(w, "다른 사용자 비공개", owner="other")
    for n in range(24):
        make(w, f"작업 {n}")
    result = w.client.get("/api/v1/tem/history?limit=20").json()
    assert result["total"] == 25 and len(result["items"]) == 20
    assert "비공개" not in json.dumps(result, ensure_ascii=False)
    assert len(w.client.get("/api/v1/tem/history?page=2").json()["items"]) == 5
    assert w.client.get("/api/v1/tem/history?q=검색").json()["items"][0]["jobId"] == own.job_id
    assert w.client.get("/api/v1/tem/history?q=REQ-001").json()["total"] == 25
    assert action(w, other, "trash").status_code == 404
    w.app.state.actor = actor("author", projects=frozenset())
    assert w.client.get("/api/v1/tem/history").status_code == 403
    assert action(w, own, "trash").status_code == 403
    w.app.state.actor = actor("admin", roles=frozenset({"ADMIN"}))
    assert w.client.get("/api/v1/tem/history").json()["total"] == 26
    assert action(w, other, "pin").status_code == 200


def test_mutations_persist_and_protect_pinned_jobs(workspace):
    w = workspace; job = make(w)
    assert action(w, job, "rename", title="검토 완료").status_code == 200
    assert json.loads((job.work_dir / "job.json").read_text())["history_title"] == "검토 완료"
    assert action(w, job, "rename", title=" ").status_code == 422
    assert action(w, job, "pin").status_code == 200
    assert action(w, job, "trash").status_code == 409
    assert action(w, job, "purge").status_code == 409
    assert action(w, job, "unpin").status_code == 200
    assert action(w, job, "trash").status_code == 200
    assert w.client.get("/api/v1/tem/history").json()["total"] == 0
    assert w.client.get("/api/v1/tem/history?trash=true").json()["total"] == 1
    assert w.client.get(f"/api/v1/tem/report/jobs/{job.job_id}").status_code == 404
    assert action(w, job, "rename", title="변경").status_code == 409
    assert action(w, job, "restore").status_code == 200
    assert w.client.get(f"/api/v1/tem/report/jobs/{job.job_id}").status_code == 200


def test_retention_uses_completion_not_polling_and_gives_full_trash_grace(workspace):
    w = workspace; job = make(w)
    job.history_finished_at = time.time() - 31 * history.DAY
    job.updated_at = time.time()
    history.cleanup(w.settings, force=True)
    assert job.history_deleted_at and job.work_dir.exists()
    assert time.time() - job.history_deleted_at < 1
    assert action(w, job, "restore").status_code == 200
    history.cleanup(w.settings, force=True)
    assert not job.history_deleted_at
    job.history_finished_at = time.time() - 31 * history.DAY
    job.history_pinned = True
    history.cleanup(w.settings, force=True)
    assert not job.history_deleted_at


@pytest.mark.parametrize("status,revisions", [("queued", 0), ("running", 0), ("completed", 1)])
def test_active_work_is_not_mutated_or_expired(workspace, status, revisions):
    job = make(workspace, status=status)
    job.active_revisions = revisions
    job.history_finished_at = time.time() - 100 * history.DAY
    history.cleanup(workspace.settings, force=True)
    assert not job.history_deleted_at
    for name in ("rename", "pin", "trash", "purge"):
        assert action(workspace, job, name, title="new").status_code == 409
    assert job.work_dir.exists()


def test_purge_removes_only_local_workspace_never_shared_package_or_symlink_target(workspace, tmp_path):
    w = workspace; job = make(w)
    shared = tmp_path / "shared-LIMS.zip"; shared.write_bytes(b"published report")
    job.package_path = shared
    unrelated = tmp_path / "outside"; unrelated.mkdir(); (unrelated / "keep").write_bytes(b"raw")
    (job.work_dir / "linked").symlink_to(unrelated, target_is_directory=True)
    assert action(w, job, "trash").status_code == 200
    assert action(w, job, "purge").status_code == 200
    assert not job.work_dir.exists()
    assert shared.read_bytes() == b"published report" and (unrelated / "keep").read_bytes() == b"raw"


def test_auto_purge_and_reading_lease(workspace):
    w = workspace; job = make(w)
    with history.lease(job):
        assert action(w, job, "trash").status_code == 409
    assert job.active_revisions == 0
    assert action(w, job, "trash").status_code == 200
    job.history_accessed_at = time.time()
    assert action(w, job, "purge").status_code == 409
    job.history_accessed_at = 0
    job.history_deleted_at = time.time() - 8 * history.DAY
    history.cleanup(w.settings, force=True)
    assert not job.work_dir.exists()


def test_file_response_protects_download_until_finished(workspace):
    job = make(workspace)
    async def run():
        messages = []
        async def send(message):
            assert history.busy(job)
            messages.append(message)
        async def receive():
            return {"type": "http.disconnect"}
        response = history.HistoryFileResponse(job, job.package_path)
        await response({"type":"http", "method":"GET", "headers":[]}, receive, send)
        assert messages[-1]["body"] == b"zip fixture"
    asyncio.run(run())
    assert not history.busy(job)


def test_progress_writes_are_throttled_but_final_state_is_durable(workspace, monkeypatch):
    job = make(workspace, status="running")
    writes = []
    original = history.atomic_write_json
    def spy(path, data):
        writes.append(data["status"])
        original(path, data)
    monkeypatch.setattr(history, "atomic_write_json", spy)
    for n in range(50):
        ahn._set_job_state(job, progress_pct=n)
    assert writes == []
    ahn._set_job_state(job, status="completed")
    assert writes == ["completed"]


def test_failed_metadata_write_does_not_change_live_retention_state(workspace, monkeypatch):
    w = workspace; job = make(w)
    def fail(*args, **kwargs):
        raise OSError("test disk full")
    monkeypatch.setattr(history, "save", fail)
    with pytest.raises(OSError, match="test disk full"):
        action(w, job, "trash")
    assert not job.history_deleted_at and job.work_dir.exists()
    job.history_finished_at = time.time() - 31 * history.DAY
    with pytest.raises(OSError, match="test disk full"):
        history.cleanup(w.settings, force=True)
    assert not job.history_deleted_at and job.work_dir.exists()


def test_maintenance_removes_expired_upload_without_a_browser_request(workspace):
    session = ahn._create_upload_session(workspace.settings)
    session.updated_at = time.time() - ahn.AHN_UPLOAD_SESSION_TTL_SECONDS - 1
    async def run():
        async with history.maintenance(workspace.app):
            for _ in range(200):
                if not session.work_dir.exists():
                    break
                await asyncio.sleep(.01)
    asyncio.run(run())
    assert not session.work_dir.exists()
    assert session.upload_id not in ahn._ahn_upload_sessions


def test_real_process_restart_restores_records_downloads_and_marks_interrupted_failed(workspace):
    w = workspace
    completed = make(w); interrupted = make(w, status="running")
    # New interpreter: no in-memory registry or inherited _loaded cache.
    code = '''
import json
from fastapi.testclient import TestClient
from app.ahn_web import create_tem_preview_app
app = create_tem_preview_app()
with TestClient(app) as client:
    listing = client.get('/api/v1/tem/history').json()
    result = {'items': listing['items']}
    ready = next(r for r in listing['items'] if r['status'] == 'completed')
    response = client.get('/api/v1/tem/report/jobs/' + ready['jobId'] + '/download/package')
    result['download'] = response.content.decode()
    result['downloadStatus'] = response.status_code
print(json.dumps(result))
'''
    env = dict(os.environ, RIST_AUTH_ENABLED="false")
    output = subprocess.check_output([sys.executable, "-c", code], env=env, text=True)
    result = json.loads(output.strip().splitlines()[-1])
    by_id = {row["jobId"]: row for row in result["items"]}
    assert by_id[completed.job_id]["status"] == "completed"
    assert by_id[interrupted.job_id]["status"] == "failed"
    assert "재시작" in by_id[interrupted.job_id]["message"]
    assert result["downloadStatus"] == 200 and result["download"] == "zip fixture"


def test_csrf_validation_and_readonly_get(workspace):
    w = workspace; job = make(w)
    url = "/api/v1/tem/history/" + job.job_id
    assert w.client.post(url, json={"action":"trash"}).status_code == 403
    assert w.client.post(url, json={"action":"trash"}, headers={**HEADERS, "Origin":"https://other.test"}).status_code == 403
    assert w.client.post(url, json={"action":"purge-everything"}, headers=HEADERS).status_code == 400
    assert w.client.get("/api/v1/tem/history?limit=10000").status_code == 400
    assert job.work_dir.exists() and not job.history_deleted_at


def test_capacity_guard_and_invalid_config(workspace, monkeypatch):
    monkeypatch.setenv("RIST_TEM_HISTORY_DAYS", "bad")
    assert history.policy()["retentionDays"] == 30
    monkeypatch.setattr(history, "storage_usage", lambda *a, **k: history.policy()["limitBytes"])
    r = workspace.client.post("/api/v1/tem/upload-sessions", json={"files":[{"path":"a.docx","size":1}]})
    assert r.status_code == 507 and r.json()["code"] == "TEM_HISTORY_FULL"


def test_abandoned_upload_cleanup_does_not_touch_live_or_unknown_folders(workspace):
    w = workspace
    # Do not let the lifespan sweep snapshot sessions before this fixture is
    # registered, then discover a marker that this test artificially ages.
    with ahn._ahn_report_jobs_lock:
        abandoned = history.new_workspace(w.settings)
        old = time.time() - 3 * 3600
        os.utime(abandoned / "upload-marker.json", (old, old))
        unknown = history.root(w.settings) / "user-data"; unknown.mkdir(); (unknown / "keep").write_text("keep")
        active = ahn._create_upload_session(w.settings)
        os.utime(active.work_dir / "upload-marker.json", (old, old))
        try:
            history.cleanup(w.settings, force=True)
            assert not abandoned.exists() and active.work_dir.exists() and (unknown / "keep").exists()
        finally:
            ahn._ahn_upload_sessions.pop(active.upload_id)


def test_invalid_metadata_cannot_restore_external_paths(workspace, tmp_path):
    w = workspace; job = make(w)
    record = job.work_dir / "job.json"
    data = json.loads(record.read_text()); data["output_dir"] = str(tmp_path)
    atomic_write_json(record, data)
    ahn._ahn_report_jobs.pop(job.job_id)
    history._loaded.discard(history.root(w.settings))
    history.load(w.settings)
    assert job.job_id not in ahn._ahn_report_jobs and job.work_dir.exists()

from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
import time
import zlib

from fastapi.testclient import TestClient
import pytest

from app import ahn_web as ahn
from app.auth import AuthContext


def crc(data):
    return f"{zlib.crc32(data) & 0xffffffff:08x}"


@pytest.fixture
def workspace(monkeypatch, tmp_path, tem_unit_runtime):
    app = ahn.create_tem_preview_app()
    app.state.actor = None
    @app.middleware("http")
    async def identity(request, call_next):
        request.state.auth_context = app.state.actor
        return await call_next(request)
    # Exercise upload/queue state without invoking Office or an operational DB.
    monkeypatch.setattr(ahn, "_run_ahn_job", lambda job: None)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, app=app)


def create(w, files=None):
    if files is None:
        files = [{"path": "Bundle/reports/A.docx", "size": 6, "lastModified": 123}]
    r = w.client.post("/api/v1/tem/upload-sessions", json={"files": files})
    assert r.status_code == 200, r.text
    return "/api/v1/tem/upload-sessions/" + r.json()["uploadId"]


def upload(w, url, data=b"abc", offset=0, size=6, path="Bundle/reports/A.docx"):
    return w.client.post(url + "/chunks", data={"relative_path": path,
        "offset": offset, "total_size": size, "chunk_index": offset // 3,
        "chunk_count": 2, "chunk_crc32": crc(data)}, files={"file": ("A.docx", data)})


def test_resume_reports_committed_offsets_and_complete_is_idempotent(workspace):
    w = workspace
    url = create(w)
    assert upload(w, url).status_code == 200
    row = w.client.get(url).json()["files"][0]
    assert row == {"path": "Bundle/reports/A.docx", "size": 6, "lastModified": 123,
                   "uploadedBytes": 3, "chunks": [{"offset": 0, "size": 3, "crc32": crc(b"abc")}]}
    assert upload(w, url, b"def", 3).status_code == 200
    # A delayed retry of an earlier chunk must not truncate later stored bytes.
    assert upload(w, url).status_code == 200
    session = ahn._get_upload_session(url.rsplit("/", 1)[1])
    state = next(iter(session.files.values()))
    assert (session.input_root / state.stored_path).read_bytes() == b"abcdef"
    assert len(state.chunks) == 2
    first = w.client.post(url + "/complete?defer_validation=true")
    second = w.client.post(url + "/complete?defer_validation=true")
    assert first.status_code == second.status_code == 200
    assert first.json()["jobId"] == second.json()["jobId"]
    assert w.client.get(url).json()["job"]["jobId"] == first.json()["jobId"]


def test_wrong_content_size_or_path_never_overwrites_existing_bytes(workspace):
    w = workspace
    url = create(w)
    assert upload(w, url).status_code == 200
    assert upload(w, url, b"xyz").status_code == 409
    assert upload(w, url, b"abc", size=9).status_code == 409
    assert upload(w, url, b"abc", path="other.docx").status_code == 409
    row = w.client.get(url).json()["files"][0]
    assert row["uploadedBytes"] == 3 and row["chunks"][0]["crc32"] == crc(b"abc")


def test_missing_not_started_files_cannot_produce_partial_report(workspace):
    w = workspace
    url = create(w, [{"path": name, "size": 6} for name in ["Bundle/reports/A.docx", "B.docx"]])
    assert upload(w, url, b"abcdef").status_code == 200
    response = w.client.post(url + "/complete?defer_validation=true")
    assert response.status_code == 409
    assert "B.docx" in response.json()["message"]
    assert w.client.get(url).json()["files"][1]["uploadedBytes"] == 0


@pytest.mark.parametrize("files", [
    [{"path": "a?.docx", "size": 1}, {"path": "a*.docx", "size": 1}],
    [{"path": "same.docx", "size": 1}, {"path": "same.docx", "size": 2}],
])
def test_colliding_server_paths_rejected(workspace, files):
    r = workspace.client.post("/api/v1/tem/upload-sessions", json={"files": files})
    assert r.status_code == 400 and r.json()["code"] == "TEM_UPLOAD_PATH_COLLISION"


def test_expired_session_not_revived_by_lookup(workspace):
    url = create(workspace)
    session = ahn._get_upload_session(url.rsplit("/", 1)[1])
    session.updated_at = time.time() - ahn.AHN_UPLOAD_SESSION_TTL_SECONDS - 1
    assert workspace.client.get(url).status_code == 404


def actor(user, projects=frozenset({"TEM"}), roles=frozenset()):
    return AuthContext(user_id=user, login_id=user, display_name=user, email=None,
                       status="ACTIVE", projects=projects, roles=roles, session_id="session",
                       session_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                       sso_authenticated_at=None, sso_identity=None)


def test_owner_checked_for_status_chunks_and_both_complete_paths(workspace):
    w = workspace
    w.app.state.settings = SimpleNamespace(auth_enabled=True)
    w.app.state.actor = actor("author")
    url = create(w)
    assert upload(w, url, b"abcdef").status_code == 200
    w.app.state.actor = actor("other")
    assert w.client.get(url).status_code == 403
    assert upload(w, url).status_code == 403
    assert w.client.post(url + "/complete?defer_validation=true").status_code == 403
    w.app.state.actor = actor("author", projects=frozenset())
    assert w.client.get(url).status_code == 403
    w.app.state.actor = actor("author")
    completed = w.client.post(url + "/complete?defer_validation=true")
    assert completed.status_code == 200
    w.app.state.actor = actor("other")
    assert w.client.get(url).status_code == 403
    assert w.client.post(url + "/complete?defer_validation=true").status_code == 403
    w.app.state.actor = actor("admin", roles=frozenset({"ADMIN"}))
    assert w.client.get(url).status_code == 200


def test_busy_status_returns_retryable_without_partial_snapshot(workspace):
    url = create(workspace)
    session = ahn._get_upload_session(url.rsplit("/", 1)[1])
    with session.lock:
        r = workspace.client.get(url)
    assert r.status_code == 409 and r.json()["retryable"]


def test_completion_between_status_lookups_returns_existing_job(workspace, monkeypatch):
    w = workspace
    url = create(w)
    assert upload(w, url, b"abcdef").status_code == 200
    job = w.client.post(url + "/complete?defer_validation=true").json()
    lookup = ahn._job_for_completed_upload
    calls = 0
    def concurrent_lookup(upload_id):
        nonlocal calls
        calls += 1
        return None if calls == 1 else lookup(upload_id)
    monkeypatch.setattr(ahn, "_job_for_completed_upload", concurrent_lookup)
    r = w.client.get(url)
    assert r.status_code == 200 and r.json()["job"]["jobId"] == job["jobId"]


def test_identity_is_in_markup_not_a_dynamic_script():
    html = ahn.build_ahn_page('a"<script>')
    assert 'data-tem-resume-owner="a&quot;&lt;script&gt;"' in html
    assert 'a"<script>' not in html
    from app.browser_compat import compatible_html
    legacy = compatible_html(html)
    assert 'data-tem-resume-owner="a&quot;&lt;script&gt;"' in legacy

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from app.auth import AuthContext
from app.config import Settings
from app.errors import ApiException
from app.preview_report import PreviewReportJobStore
from app import workspace_dashboard as dashboard


class DashboardDB:
    """Execute the dashboard's actual read SQL against isolated fixtures."""
    def __init__(self):
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.connection.row_factory = lambda cursor, row: dict(zip([c[0] for c in cursor.description], row))
        self.connection.executescript("""
        CREATE TABLE report_runs(report_id TEXT, request_number TEXT, experiment_code TEXT, deleted_at TEXT);
        CREATE TABLE report_transfers(report_id TEXT, status TEXT, destination TEXT, requested_at TEXT);
        CREATE TABLE voc_requests(project TEXT, title TEXT, status TEXT, author_user_id TEXT, updated_at TEXT);
        CREATE TABLE app_users(status TEXT);
        """)
        self.queries = []
        self.connection.set_trace_callback(self.queries.append)

    @contextmanager
    def transaction(self):
        yield self.connection

    def report(self, key, status, request="REQ-001", deleted=None):
        self.connection.execute("INSERT INTO report_runs VALUES(?,?,?,?)", (key, request, "FTIR", deleted))
        self.connection.execute("INSERT INTO report_transfers VALUES(?,?,?,?)", (key, status, "LIMS", "2026-10-26"))


def context(name="alice", projects=("FTIR", "RAMAN", "XRD", "TEM"), admin=False):
    return AuthContext(user_id=name, login_id=name, email=None, display_name="시험 " + name, status="ACTIVE",
        session_id=name, session_expires_at=datetime.now(timezone.utc)+timedelta(hours=1),
        sso_authenticated_at=None, projects=frozenset(projects), roles=frozenset({"ADMIN"} if admin else ()), sso_identity=None)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from app import ahn_web
    monkeypatch.setattr(ahn_web, "_ahn_report_jobs", {})
    app = FastAPI()
    app.state.settings = Settings(storage_root=tmp_path / "storage", auth_enabled=True)
    app.state.database = DashboardDB()
    app.state.preview_report_job_store = PreviewReportJobStore()
    app.state.xrd_workbench_enabled = True
    app.state.actor = context()

    @app.middleware("http")
    async def actor(request, call_next):
        request.state.auth_context = app.state.actor
        return await call_next(request)

    @app.exception_handler(ApiException)
    async def error(request, exc):
        return JSONResponse({"code": exc.code}, status_code=exc.status_code)

    app.include_router(dashboard.router)
    client = TestClient(app)
    yield SimpleNamespace(app=app, client=client, root=tmp_path, db=app.state.database,
                          get=lambda: client.get("/api/v1/workspace/dashboard"))
    client.close()
    app.state.database.connection.close() if isinstance(app.state.database, DashboardDB) else None


def spectrum(w, owner="alice", kind="ftir", state="completed", parent=None):
    store = w.app.state.preview_report_job_store
    job = store.create(filename=kind+"-report-package.zip", owner_user_id=owner, parent_job_id=parent)
    job.transfer_metadata = {"requestNumber": (owner or "development")+"-request"}
    if state == "completed":
        root = w.root / job.job_id
        (root / "job/report").mkdir(parents=True)
        (root / "job/report/report.json").write_text("do not parse this file")
        store.complete(job.job_id, tmp_root=root, package_path=root / "report.zip")
    else:
        store.update(job.job_id, status=state)
    return job


def tem_record(w, name="a", **values):
    jid = name * 32
    folder = w.app.state.settings.storage_root / "tem-work-history" / ("work-"+jid)
    (folder / "output").mkdir(parents=True)
    (folder / "output/tem-report.pptx").write_bytes(b"not opened by dashboard")
    data = dict(job_id=jid, owner_user_id="alice", status="completed", updated_at=datetime.now().timestamp(),
                created_at=datetime.now().timestamp(), history_finished_at=datetime.now().timestamp(),
                history_title="TEM LTO201", pptx_path="output/tem-report.pptx", transfer_metadata={"requestNumber":"TEM-1"})
    data.update(values)
    (folder / "job.json").write_text(json.dumps(data))
    return folder, data


def xrd_project(w, name="a", owner="alice", archived=False):
    home = w.app.state.settings.storage_root / "xrd-workbench-v1/users" / hashlib.sha256(owner.encode()).hexdigest()
    sid = name*32
    folder = home / "uploads" / sid
    folder.mkdir(parents=True)
    (folder / "project.json").write_text("RAW arrays must not be parsed on the dashboard")
    (folder / "summary.json").write_text(json.dumps({"item":{"label":"XRD 시료 "+name,"created_at":"2026-10-25T10:00:00Z"}}))
    (folder / "management.json").write_text(json.dumps({"archived_at":"yesterday" if archived else None}))
    return home, sid


def test_home_api_requires_active_context_and_never_accepts_owner_query(workspace):
    w=workspace
    spectrum(w, owner="bob")
    result=w.client.get("/api/v1/workspace/dashboard?owner=bob&admin=true")
    assert result.status_code==200 and result.json()["recent"]==[]
    assert result.headers["cache-control"]=="private, no-store"
    w.app.state.actor=None
    assert w.get().status_code==401
    w.app.state.actor=replace(context(),status="SUSPENDED")
    assert w.get().status_code==403


def test_spectra_owner_and_project_permissions_and_expired_editor(workspace):
    w=workspace
    own=spectrum(w);spectrum(w,owner="bob");spectrum(w,kind="raman")
    expired=spectrum(w);(expired.tmp_root / "job/report/report.json").unlink()
    w.app.state.actor=context(projects=("FTIR",))
    value=w.get().json()
    assert value["projects"]==["FTIR"]
    assert [r["jobId"] for r in value["recent"]]==[own.job_id]
    assert value["recent"][0]["href"]==f"/api/v1/ftir/report/jobs/{own.job_id}/edit"
    assert "bob" not in json.dumps(value)
    w.app.state.actor=context(projects=())
    assert w.get().json()["recent"]==[]


def test_successful_revision_supersedes_parent_but_failed_edit_retains_valid_parent(workspace):
    w=workspace;first=spectrum(w);revision=spectrum(w,parent=first.job_id)
    failed=spectrum(w,state="failed",parent=revision.job_id)
    value=w.get().json()
    assert {r["jobId"] for r in value["recent"]}=={revision.job_id,failed.job_id}
    assert value["counts"]["failed"]==1 and value["counts"]["completedToday"]==1


def test_tem_disk_ownership_trash_and_completed_date_are_not_last_access_date(workspace):
    w=workspace
    old=(datetime.now(timezone.utc)-timedelta(days=2)).timestamp()
    tem_record(w,history_finished_at=old)
    tem_record(w,"b",owner_user_id="bob")
    tem_record(w,"c",history_deleted_at=1234)
    value=w.get().json()
    assert len(value["recent"])==1 and value["recent"][0]["title"]=="TEM LTO201"
    assert value["recent"][0]["action"]=="보고서 편집"
    assert value["counts"]["completedToday"]==0


def test_tem_live_status_overrides_disk_without_loading_or_cleanup(workspace,monkeypatch):
    from app import ahn_web,tem_history
    w=workspace;folder,r=tem_record(w,status="running")
    live=SimpleNamespace(**r,settings=w.app.state.settings)
    live.pptx_path=folder / "output/tem-report.pptx"
    ahn_web._ahn_report_jobs[r["job_id"]]=live
    monkeypatch.setattr(tem_history,"prepare",lambda *args:pytest.fail("home must not clean or load user work"))
    value=w.get().json()
    assert value["counts"]["active"]==1
    del ahn_web._ahn_report_jobs[r["job_id"]]
    assert w.get().json()["recent"][0]["status"]=="interrupted"


def test_xrd_reads_only_owned_small_summaries_hides_trash_and_deleted_jobs(workspace):
    w=workspace;home,sid=xrd_project(w);xrd_project(w,"b",owner="bob");_,trashed=xrd_project(w,"c",archived=True)
    for name,project in [("d",sid),("e",trashed)]:
        job=home / "jobs" / (name*32);job.mkdir(parents=True)
        (job / "job.json").write_text(json.dumps(dict(job_id=name*32,storage_id=project,kind="zip",status="completed",created_at=1793000000)))
    before={p: p.read_bytes() for p in w.app.state.settings.storage_root.rglob("*.json")}
    value=w.get().json()
    assert len(value["recent"])==1 and value["recent"][0]["title"]=="XRD 시료 a"
    assert value["recent"][0]["href"]==f"/api/v1/xrd/workbench/report.html?project={sid}"
    assert before=={p:p.read_bytes() for p in w.app.state.settings.storage_root.rglob("*.json")}


def test_xrd_owner_symlink_is_rejected_and_error_detail_not_exposed(workspace):
    w=workspace;other,_=xrd_project(w,owner="bob")
    home=other.parent / hashlib.sha256(b"alice").hexdigest();home.symlink_to(other,target_is_directory=True)
    value=w.get().json()
    assert value["recent"]==[] and "XRD" in value["unavailable"]
    assert str(w.root) not in json.dumps(value)


def test_personal_transfer_queue_is_filtered_by_server_known_owned_ids_not_operator_text(workspace):
    w=workspace;first=spectrum(w);revision=spectrum(w,parent=first.job_id);other=spectrum(w,owner="bob")
    w.db.report(first.job_id,"FAILED","own-old")
    w.db.report(revision.job_id,"PROCESSING","own-new")
    w.db.report(other.job_id,"FAILED","secret-request")
    value=w.get().json()
    assert value["counts"]["pendingTransfers"]==1 and value["counts"]["failedTransfers"]==1
    assert {r["title"] for r in value["transfers"]}=={"own-old","own-new"}
    assert "secret-request" not in json.dumps(value)
    assert all("SELECT r.report_id" not in q or " IN (" in q for q in w.db.queries)


def test_completed_or_deleted_transfers_never_show_as_pending(workspace):
    w=workspace;a=spectrum(w);b=spectrum(w)
    w.db.report(a.job_id,"COMPLETED");w.db.report(b.job_id,"FAILED",deleted="today")
    value=w.get().json()
    assert value["transfers"]==[] and value["counts"]["pendingTransfers"]==0 and value["counts"]["failedTransfers"]==0


def test_transfer_batches_keep_global_newest_first(workspace):
    for i in range(201):
        workspace.db.report(f"{i:032x}", "PENDING", request=str(i))
    newest = f"{200:032x}"
    workspace.db.connection.execute("UPDATE report_transfers SET requested_at='2026-10-27' WHERE report_id=?", (newest,))
    rows = dashboard.transfers(workspace.db, {f"{i:032x}" for i in range(201)})
    assert len(rows) == 201 and rows[0]["report_id"] == newest


def test_voc_action_items_require_author_and_experiment_permission(workspace):
    w=workspace;w.app.state.actor=context(projects=("FTIR",))
    for p,author,status,title in [("FTIR","alice","RESOLVED","mine"),("FTIR","bob","RESOLVED","other"),
                                  ("TEM","alice","RESOLVED","denied"),("FTIR","alice","OPEN","waiting")]:
        w.db.connection.execute("INSERT INTO voc_requests VALUES(?,?,?,?,?)",(p,title,status,author,"2026-10-26"))
    value=w.get().json()
    assert value["voc"]["count"]==1 and value["voc"]["items"][0]["title"]=="mine"
    assert value["admin"] is None
    w.app.state.actor=context(admin=True)
    value=w.get().json()
    assert value["voc"]["count"]==1 and value["voc"]["items"][0]["title"]=="waiting"


def test_admin_sees_global_aggregates_but_recent_work_remains_personal(workspace):
    w=workspace;spectrum(w,owner="bob");w.db.report("unowned","FAILED")
    w.db.connection.execute("INSERT INTO app_users VALUES('PENDING')")
    assert w.get().json()["admin"] is None
    w.app.state.actor=context(admin=True)
    value=w.get().json()
    assert value["recent"]==[] and value["admin"]["reports"]==1
    assert value["admin"]["pendingUsers"]==1 and value["admin"]["failedTransfers"]==1
    assert value["admin"]["diskTotalBytes"]>=value["admin"]["diskFreeBytes"]>0


def test_db_failure_is_unknown_not_zero_and_does_not_block_experiments(workspace):
    w=workspace;spectrum(w)
    class Broken:
        def transaction(self):raise RuntimeError("secret-db-password")
    w.app.state.database=Broken()
    value=w.get().json()
    assert len(value["recent"])==1 and value["projects"]==list(dashboard.PROJECT_CODES)
    assert value["counts"]["pendingTransfers"] is None and value["voc"] is None
    assert "secret-db-password" not in json.dumps(value)


def test_corrupt_and_oversize_metadata_is_skipped_with_visible_notice(workspace):
    w=workspace;folder,_=tem_record(w);(folder / "job.json").write_text("x"*(128*1024+1))
    value=w.get().json()
    assert value["recent"]==[] and any("TEM" in v for v in value["warnings"])


def test_catalog_scan_limit_is_explicit(workspace,monkeypatch):
    w=workspace;monkeypatch.setattr(dashboard,"LIMIT",2)
    for name in "abc":xrd_project(w,name)
    value=w.get().json()
    assert len(value["recent"])==2 and any("조회 한도" in v for v in value["warnings"])


def test_no_auth_environment_has_no_admin_or_personal_voc(workspace):
    w=workspace;w.app.state.settings=replace(w.app.state.settings,auth_enabled=False)
    spectrum(w,owner=None);spectrum(w,owner="alice")
    value=w.get().json()
    assert value["user"] is None and value["admin"] is None and value["voc"] is None
    assert len(value["recent"])==1


def test_actual_auth_middleware_rechecks_session_and_revoked_permissions(workspace,monkeypatch):
    from app.auth import install_auth,SESSION_COOKIE
    from app.errors import api_exception_handler
    w=workspace;spectrum(w)
    app=FastAPI();app.state.settings=w.app.state.settings;app.state.database=w.db
    app.state.preview_report_job_store=w.app.state.preview_report_job_store
    app.state.xrd_workbench_enabled=True
    install_auth(app,app.state.settings,w.db)
    app.add_exception_handler(ApiException,api_exception_handler);app.include_router(dashboard.router)
    current=[context(projects=("FTIR",))]
    monkeypatch.setattr(app.state.auth_service,"context_from_token",lambda token:current[0] if token=="dashboard-test-session" else None)
    with TestClient(app) as client:
        assert client.get("/api/v1/workspace/dashboard").status_code==401
        client.cookies.set(SESSION_COOKIE,"dashboard-test-session")
        assert len(client.get("/api/v1/workspace/dashboard").json()["recent"])==1
        current[0]=context(projects=())
        assert client.get("/api/v1/workspace/dashboard").json()["recent"]==[]
        current[0]=None
        assert client.get("/api/v1/workspace/dashboard").status_code==401


def test_real_mariadb_schema_scoped_queue_and_admin_aggregates(workspace, mariadb):
    from app.database import Database, DatabaseConfig
    w = workspace
    database = Database(DatabaseConfig(**mariadb))
    w.app.state.database = database
    try:
        own = spectrum(w)
        other = spectrum(w, owner="bob")
        for job in (own, other):
            database.enqueue_report_transfer(
                report_id=job.job_id, transfer_id=job.job_id, source_job_id=None,
                request_number=job.owner_user_id + "-request", experiment_code="FTIR",
                equipment_code="test-equipment", operator_id="editable-not-owner",
                storage_key="RIST_REPORTS", package_relative_path=job.job_id + "/report.zip",
                package_file_name="report.zip", package_size_bytes=1, package_sha256="0" * 64,
                report_options_json=None, generated_at=datetime.now(timezone.utc).isoformat(), max_attempts=3,
            )
        with database.transaction() as c:
            c.execute("UPDATE report_transfers SET status='FAILED' WHERE report_id=?", (other.job_id,))
            c.execute("INSERT INTO app_users(user_id,login_id,password_hash,display_name,status) VALUES('alice','alice','test-only','Alice','PENDING')")
            c.execute("INSERT INTO voc_requests(voc_id,project,author_user_id,author_login_id,author_display_name,request_key,title,content,status,resolution_note,created_at,updated_at) VALUES('voc1','FTIR','alice','alice','Alice','req1','Resolved VOC','test','RESOLVED','done',UTC_TIMESTAMP(),UTC_TIMESTAMP())")
        value = w.get().json()
        assert value["warnings"] == []
        assert value["counts"]["pendingTransfers"] == 1 and value["counts"]["failedTransfers"] == 0
        assert value["voc"]["count"] == 1 and value["admin"] is None
        assert [r["title"] for r in value["transfers"]] == ["alice-request"]
        w.app.state.actor = context(admin=True)
        value = w.get().json()
        assert value["admin"]["reports"] == 2 and value["admin"]["failedTransfers"] == 1
        assert value["admin"]["pendingUsers"] == 1 and value["voc"]["count"] == 0
        assert "bob-request" not in json.dumps(value)
    finally:
        database.close()
        w.app.state.database = w.db

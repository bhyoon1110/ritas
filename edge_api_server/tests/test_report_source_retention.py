import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.report_source_retention import source_cleanup_guard, tem_delivery_copies, xrd_delivery_copies


def artifact(root, path):
    content = path.read_bytes()
    return {"relative_path": path.relative_to(root).as_posix(), "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest()}


def tem(tmp_path):
    settings = SimpleNamespace(storage_root=tmp_path)
    directory = tmp_path / "tem-work-history" / ("work-" + "a" * 32)
    output = directory / "output"
    output.mkdir(parents=True)
    package = output / "tem-report-package.zip"
    package.write_bytes(b"generated package")
    original = directory / "input" / "raw.tif"
    original.parent.mkdir()
    original.write_bytes(b"editable original")
    job = SimpleNamespace(settings=settings, work_dir=directory, output_dir=output,
                          job_id="b" * 32, status="completed", active_revisions=0,
                          history_pinned=False, history_accessed_at=0)
    (directory / "job.json").write_text(json.dumps({"job_id": job.job_id, "status": "completed", "history_pinned": False}))
    return settings, job, package, original


def test_tem_external_web_owner_defers_cleanup_even_with_completed_saved_metadata(tmp_path, monkeypatch):
    from app import ahn_web
    settings, job, package, original = tem(tmp_path)
    paths, context = tem_delivery_copies(job)
    row = {"report_options_json": {"_sourceRetention": context},
           "artifacts": [artifact(tmp_path, path) for path in paths]}
    monkeypatch.setattr(ahn_web, "_ahn_report_jobs", {})
    code = "import fcntl,sys; f=open(sys.argv[1],'a+b'); fcntl.flock(f,fcntl.LOCK_EX); print('ready',flush=True); sys.stdin.read()"
    child = subprocess.Popen([sys.executable, "-c", code, str(job.work_dir.parent / ".runtime.lock")],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(HTTPException) as exc:
            with source_cleanup_guard(settings, row):
                pytest.fail("A different web process owns this workspace")
        assert exc.value.status_code == 409
        assert original.is_file() and package.is_file()
    finally:
        child.communicate(input="", timeout=5)
    # The stopped web process released its lock; restart-safe metadata now suffices.
    with source_cleanup_guard(settings, row):
        assert original.is_file()


def test_tem_runtime_owner_allows_local_guard_and_blocks_external_owner(tmp_path, monkeypatch):
    from app import ahn_web, tem_history
    settings, job, package, original = tem(tmp_path)
    paths, context = tem_delivery_copies(job)
    row = {"report_options_json": {"_sourceRetention": context},
           "artifacts": [artifact(tmp_path, path) for path in paths]}
    monkeypatch.setattr(ahn_web, "_ahn_report_jobs", {job.job_id: job})
    code = "import fcntl,sys; f=open(sys.argv[1],'a+b'); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)"
    with tem_history.runtime_owner(settings):
        with source_cleanup_guard(settings, row):
            assert package.is_file()
        probe = subprocess.run([sys.executable, "-c", code, str(job.work_dir.parent / ".runtime.lock")],
                               capture_output=True, timeout=5)
        assert probe.returncode != 0
    probe = subprocess.run([sys.executable, "-c", code, str(job.work_dir.parent / ".runtime.lock")],
                           capture_output=True, timeout=5)
    assert probe.returncode == 0


def test_tem_queued_worker_keeps_runtime_owner_after_app_shutdown(tmp_path):
    from concurrent.futures import Future
    from app import tem_history
    settings, job, _, _ = tem(tmp_path)
    queued = Future()
    executor = SimpleNamespace(submit=lambda *_args: queued)
    with tem_history.runtime_owner(settings):
        tem_history.submit_owned(executor, job, lambda: None)
    assert tem_history.root(settings) in tem_history._runtime_owners
    queued.set_result(None)
    assert tem_history.root(settings) not in tem_history._runtime_owners


def test_tem_linkage_is_generation_scoped_and_restart_safe(tmp_path, monkeypatch):
    from app import ahn_web
    settings, job, package, original = tem(tmp_path)
    paths, context = tem_delivery_copies(job)
    assert paths == [package, original]
    draft = job.work_dir / "review" / "editor-state.json"
    draft.parent.mkdir()
    draft.write_text("reusable draft")
    row = {"report_options_json": json.dumps({"_sourceRetention": context}), "artifacts": [artifact(tmp_path, path) for path in paths]}
    monkeypatch.setattr(ahn_web, "_ahn_report_jobs", {})
    with source_cleanup_guard(settings, row):
        for path in paths:
            path.unlink()
    assert draft.read_text() == "reusable draft"
    assert (job.work_dir / "job.json").is_file()


@pytest.mark.parametrize("condition", ["busy", "pinned", "changed"])
def test_tem_guard_keeps_in_use_pinned_or_edited_generation(tmp_path, monkeypatch, condition):
    from app import ahn_web
    settings, job, package, _ = tem(tmp_path)
    _, context = tem_delivery_copies(job)
    row = {"report_options_json": {"_sourceRetention": context}, "artifacts": [artifact(tmp_path, package)]}
    monkeypatch.setattr(ahn_web, "_ahn_report_jobs", {job.job_id: job})
    if condition == "busy":
        job.active_revisions = 1
    elif condition == "pinned":
        job.history_pinned = True
    else:
        package.write_bytes(b"edited package after generation")
    with pytest.raises(HTTPException) as error:
        with source_cleanup_guard(settings, row):
            pytest.fail("protected generation was authorized for deletion")
    assert error.value.status_code == 409
    assert package.is_file()


def test_tem_selection_rejects_source_symlink(tmp_path):
    _, job, package, _ = tem(tmp_path)
    outside = tmp_path / "unrelated.txt"
    outside.write_text("keep")
    (job.output_dir / "linked.txt").symlink_to(outside)
    with pytest.raises(ValueError):
        tem_delivery_copies(job)
    assert outside.read_text() == "keep"


def test_xrd_export_copy_cleanup_preserves_project_and_other_exports(tmp_path):
    settings = SimpleNamespace(storage_root=tmp_path)
    home = tmp_path / "xrd-workbench-v1" / "users" / ("a" * 64)
    directory = home / "jobs" / ("b" * 32)
    directory.mkdir(parents=True)
    (directory / "report.zip").write_bytes(b"package")
    (directory / "document.json").write_text("{}")
    (directory / "job.json").write_text(json.dumps({"kind": "zip", "status": "completed"}))
    original = home / "uploads" / ("c" * 32) / "raw.txt"
    original.parent.mkdir(parents=True)
    original.write_text("project source")
    other = home / "jobs" / ("d" * 32) / "report.zip"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"another export")
    paths, context = xrd_delivery_copies(SimpleNamespace(settings=settings), directory)
    row = {"report_options_json": {"_sourceRetention": context}, "artifacts": [artifact(tmp_path, p) for p in paths]}
    with source_cleanup_guard(settings, row):
        for p in paths:
            p.unlink()
    assert original.read_text() == "project source"
    assert other.read_bytes() == b"another export"
    assert (directory / "job.json").is_file()


@pytest.mark.parametrize("workspace", ["../outside", "/tmp/arbitrary", "web-reports/unrelated"])
def test_cleanup_context_never_authorizes_arbitrary_paths(tmp_path, workspace):
    row = {"report_options_json": {"_sourceRetention": {"version": 1, "kind": "XRD_EXPORT", "workspace": workspace}}}
    with pytest.raises(HTTPException) as error:
        with source_cleanup_guard(SimpleNamespace(storage_root=tmp_path), row):
            pytest.fail("invalid context authorized")
    assert error.value.status_code == 409


def test_tem_revision_links_ancestor_input_and_respects_parent_pin(tmp_path, monkeypatch):
    from app import ahn_web
    settings, parent, _, original = tem(tmp_path)
    directory = tmp_path / "tem-work-history" / ("work-" + "c" * 32)
    output = directory / "output"
    output.mkdir(parents=True)
    generated = output / "result.pptx"
    generated.write_bytes(b"revision")
    child = SimpleNamespace(**{**vars(parent), "job_id": "d" * 32, "work_dir": directory,
                               "output_dir": output, "parent_job_id": parent.job_id})
    monkeypatch.setattr(ahn_web, "_ahn_report_jobs", {parent.job_id: parent, child.job_id: child})
    paths, context = tem_delivery_copies(child)
    assert paths == [generated, original]
    row = {"report_options_json": {"_sourceRetention": context}, "artifacts": [artifact(tmp_path, path) for path in paths]}
    parent.history_pinned = True
    with pytest.raises(HTTPException):
        with source_cleanup_guard(settings, row):
            pytest.fail("parent retention pin ignored")
    parent.history_pinned = False
    with source_cleanup_guard(settings, row):
        for path in paths:
            path.unlink()
    assert (parent.output_dir / "tem-report-package.zip").is_file()


def xrd_project(tmp_path):
    settings = SimpleNamespace(storage_root=tmp_path)
    home = tmp_path / "xrd-workbench-v1" / "users" / ("a" * 64)
    sid, fid, job_id, session_id, import_id = "b" * 32, "c" * 24, "d" * 32, "e" * 32, "f" * 32
    directory = home / "jobs" / job_id
    directory.mkdir(parents=True)
    (directory / "report.zip").write_bytes(b"zip")
    (directory / "document.json").write_text('{}')
    (directory / "job.json").write_text(json.dumps({"kind": "zip", "status": "completed"}))
    raw = home / "uploads" / sid / fid
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"XRD original")
    project = {"storage_id": sid, "id": "project-fingerprint", "sources": [{"id": fid}], "analysis_revision": 0}
    (raw.parent / "project.json").write_text(json.dumps(project))
    document_path = home / "reports" / sid / "document.json"
    document_path.parent.mkdir(parents=True)
    document = {"revision": 1}
    document_path.write_text(json.dumps(document))
    session = home / "sessions" / session_id
    duplicate = session / "input" / "input.txt"
    duplicate.parent.mkdir(parents=True)
    duplicate.write_bytes(raw.read_bytes())
    (session / "session.json").write_text(json.dumps({"job_id": import_id, "files": {
        "raw.txt": {"completed": True, "stored_name": "input.txt"}}}))
    imported = home / "jobs" / import_id / "job.json"
    imported.parent.mkdir(parents=True)
    imported.write_text(json.dumps({"status": "completed", "kind": "import", "storage_id": sid}))
    state = SimpleNamespace(settings=settings)
    paths, context = xrd_delivery_copies(state, directory, project=project, document=document)
    row = {"report_options_json": {"_sourceRetention": context}, "artifacts": [artifact(tmp_path, p) for p in paths]}
    return settings, home, raw, duplicate, document_path, paths, row


def test_xrd_exact_immutable_sources_and_upload_duplicate_are_linked(tmp_path):
    settings, home, raw, duplicate, document, paths, row = xrd_project(tmp_path)
    assert raw in paths and duplicate in paths
    unrelated = home / "uploads" / ("9" * 32) / "original.txt"
    unrelated.parent.mkdir()
    unrelated.write_text("unrelated")
    with source_cleanup_guard(settings, row):
        for path in paths:
            path.unlink()
    assert not raw.exists() and not duplicate.exists()
    assert unrelated.read_text() == "unrelated"
    assert document.is_file()


@pytest.mark.parametrize("change", ["new-report", "new-draft", "source-changed", "active-job"])
def test_xrd_newer_edits_or_active_jobs_protect_originals(tmp_path, change):
    settings, home, raw, duplicate, document, paths, row = xrd_project(tmp_path)
    if change == "new-report":
        document.write_text('{"revision":2}')
    elif change == "new-draft":
        draft = home / "drafts" / (raw.parent.name + ".json")
        draft.parent.mkdir()
        draft.write_text('{"editing":true}')
    elif change == "source-changed":
        raw.write_bytes(b"changed")
    else:
        pending = home / "jobs" / ("8" * 32) / "job.json"
        pending.parent.mkdir()
        pending.write_text('{"status":"queued"}')
    with pytest.raises(HTTPException):
        with source_cleanup_guard(settings, row):
            pytest.fail("newer work authorized for cleanup")
    assert raw.is_file() and duplicate.is_file()

"""Real Git fixture coverage; no operational services, DB or network are touched."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

DEPLOY = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("edge_update", DEPLOY / "install_edge_update.py")
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)
build_spec = importlib.util.spec_from_file_location("edge_builder", DEPLOY / "package_edge_update.py")
builder = importlib.util.module_from_spec(build_spec)
build_spec.loader.exec_module(builder)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.PIPE, text=True).strip()


def commit(root, message):
    git(root, "add", ".")
    git(root, "commit", "-qm", message)
    return git(root, "rev-parse", "HEAD")


def checksums(package):
    (package / "SHA256SUMS").write_text("".join(
        f"{update.digest(p)}  {p.name}\n" for p in sorted(package.iterdir()) if p.name != "SHA256SUMS"
    ))


@pytest.fixture
def release(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-b", "main")
    git(source, "config", "user.name", "Test")
    git(source, "config", "user.email", "fixture@example.invalid")
    (source / ".gitignore").write_text("edge.env\n.venv/\n")
    (source / "app.py").write_text("VERSION = 1\n")
    (source / "requirements.txt").write_text("test-package==1\n")
    old = commit(source, "old")
    (source / "app.py").write_text("VERSION = 2\n")
    target = commit(source, "new")
    live = tmp_path / "live"
    subprocess.run(["git", "clone", "-q", str(source), str(live)], check=True)
    git(live, "reset", "--hard", old)
    git(live, "config", "user.name", "Test")
    git(live, "config", "user.email", "fixture@example.invalid")
    (live / "edge.env").write_text("RIST_SSO_READ_TIMEOUT_SECONDS=40\nPRIVATE_TEST_VALUE=preserve\n")
    (live / ".venv").mkdir()
    (live / ".venv/pyvenv.cfg").write_text("unchanged venv\n")
    package = tmp_path / "package"
    package.mkdir()
    data = {"format": 1, "kind": "rist-python-edge-update", "commit": target,
            "bundleRef": "HEAD", "schemaPolicy": "additive-only"}
    (package / "release.json").write_text(json.dumps(data))
    (package / "README.md").write_text("Fixture\n")
    shutil.copyfile(DEPLOY / "install_edge_update.py", package / "install_edge_update.py")
    git(source, "bundle", "create", str(package / "rist-edge.bundle"), "HEAD")
    checksums(package)
    return source, live, package, data, old


def test_verified_bundle_uses_exact_commit(release):
    _, _, package, data, _ = release
    assert update.verify_package(package) == data


@pytest.mark.parametrize("target", ["rist-edge.bundle", "install_edge_update.py", "release.json", "README.md"])
def test_tampered_payload_rejected(release, target):
    package = release[2]
    with (package / target).open("ab") as stream:
        stream.write(b"tamper")
    with pytest.raises(update.Blocked, match="무결성"):
        update.verify_package(package)


def test_symlink_payload_rejected(release, tmp_path):
    package = release[2]
    data = (package / "README.md").read_bytes()
    outside = tmp_path / "outside"
    outside.write_bytes(data)
    (package / "README.md").unlink()
    (package / "README.md").symlink_to(outside)
    with pytest.raises(update.Blocked, match="무결성"):
        update.verify_package(package)


@pytest.mark.parametrize("line", ["bad\n", "a" * 64 + "  ../outside\n", "a" * 64 + "  /etc/passwd\n"])
def test_unsafe_checksums_rejected(release, line):
    (release[2] / "SHA256SUMS").write_text(line)
    with pytest.raises(update.Blocked, match="형식"):
        update.verify_package(release[2])


def test_manifest_cannot_claim_other_commit(release):
    _, _, package, data, old = release
    (package / "release.json").write_text(json.dumps({**data, "commit": old}))
    checksums(package)
    with pytest.raises(update.Blocked, match="커밋"):
        update.verify_package(package)


def test_read_only_plan_leaves_live_head_files_and_refs(release, tmp_path):
    _, live, package, data, old = release
    stage = tmp_path / "plan"
    stage.mkdir()
    refs = git(live, "show-ref")
    env = (live / "edge.env").read_bytes()
    assert update.compare_bundle(live, package, data, stage) == (old, ["app.py"])
    assert git(live, "rev-parse", "HEAD") == old
    assert git(live, "show-ref") == refs
    assert (live / "edge.env").read_bytes() == env


def test_diverged_history_rejected(release, tmp_path):
    _, live, package, data, _ = release
    (live / "app.py").write_text("LOCAL = True\n")
    local = commit(live, "local")
    stage = tmp_path / "plan"
    stage.mkdir()
    with pytest.raises(update.Blocked, match="분기"):
        update.compare_bundle(live, package, data, stage)
    assert git(live, "rev-parse", "HEAD") == local


def test_dependency_change_requires_separate_offline_package(release, tmp_path):
    source, live, package, data, old = release
    (source / "requirements.txt").write_text("new-package==2\n")
    data["commit"] = commit(source, "dependency")
    git(source, "bundle", "create", str(package / "rist-edge.bundle"), "HEAD")
    stage = tmp_path / "plan"
    stage.mkdir()
    with pytest.raises(update.Blocked, match="의존성"):
        update.compare_bundle(live, package, data, stage)
    assert git(live, "rev-parse", "HEAD") == old


def test_live_tracked_edits_are_never_overwritten(release):
    live = release[1]
    name = "app.py"
    (live / name).write_text("operator edit\n")
    with pytest.raises(update.Blocked, match="미커밋"):
        update.require_clean(live)
    assert (live / name).read_text() == "operator edit\n"


@pytest.fixture
def services(monkeypatch):
    states = {"rist-edge-api.service": "active", "rist-edge-worker.service": "inactive"}
    calls = []
    original_run = update.run

    def run(args, **kwargs):
        if str(args[0]) == "sudo":
            return subprocess.CompletedProcess(args, 0, "", "")
        return original_run(args, **kwargs)

    def action(verb, units):
        calls.append((verb, tuple(units)))
        for unit in units:
            states[unit] = "active" if verb == "start" else "inactive"

    monkeypatch.setattr(update, "run", run)
    monkeypatch.setattr(update, "authorize_sudo", lambda: None)
    monkeypatch.setattr(update, "service_action", action)
    monkeypatch.setattr(update, "service_properties", lambda unit: {"ActiveState": states[unit]})
    monkeypatch.setattr(update, "check_imports", lambda *args: None)
    monkeypatch.setattr(update, "wait_health", lambda *args: None)
    return states, calls


def test_success_preserves_environment_and_inactive_worker(release, services):
    _, live, package, data, old = release
    states, calls = services
    before = update.protected_hashes(live)
    backup = update.apply_update(live, package, data, old, dict(states), before)
    assert git(live, "rev-parse", "HEAD") == data["commit"]
    assert update.protected_hashes(live) == before
    assert states == {"rist-edge-api.service": "active", "rist-edge-worker.service": "inactive"}
    assert ("start", ("rist-edge-api.service",)) in calls
    assert json.loads((backup / "state.json").read_text())["status"] == "applied"
    assert git(live, "rev-parse", "refs/rist-backups/" + backup.name) == old


def test_import_failure_restores_original_code_and_services(release, services, monkeypatch):
    _, live, package, data, old = release
    states, _ = services
    original = dict(states)
    before = update.protected_hashes(live)

    def fail(*_):
        raise update.Blocked("fixture import failure")

    monkeypatch.setattr(update, "check_imports", fail)
    with pytest.raises(update.Blocked, match="fixture import"):
        update.apply_update(live, package, data, old, original, before)
    assert git(live, "rev-parse", "HEAD") == old
    assert states == original
    assert update.protected_hashes(live) == before
    receipts = list((live.parent / "rist-edge-code-backups").glob("*/state.json"))
    assert json.loads(receipts[0].read_text())["status"] == "rolled-back"


def test_health_failure_rolls_back_only_installed_commit(release, services, monkeypatch):
    _, live, package, data, old = release
    states, _ = services
    health = []

    def probe(*_):
        health.append(True)
        if len(health) == 1:
            raise update.Blocked("fixture health failure")

    monkeypatch.setattr(update, "wait_health", probe)
    with pytest.raises(update.Blocked, match="fixture health"):
        update.apply_update(live, package, data, old, dict(states), update.protected_hashes(live))
    assert git(live, "rev-parse", "HEAD") == old
    assert len(health) == 2


def test_concurrent_operator_edit_prevents_rollback_clobber(release, services, monkeypatch):
    _, live, package, data, old = release
    states, _ = services

    def edit_then_fail(*_):
        (live / "app.py").write_text("operator concurrent edit\n")
        raise update.Blocked("fixture failure")

    monkeypatch.setattr(update, "check_imports", edit_then_fail)
    with pytest.raises(update.Blocked, match="fixture failure"):
        update.apply_update(live, package, data, old, dict(states), update.protected_hashes(live))
    assert git(live, "rev-parse", "HEAD") == data["commit"]
    assert (live / "app.py").read_text() == "operator concurrent edit\n"
    receipts = list((live.parent / "rist-edge-code-backups").glob("*/state.json"))
    assert json.loads(receipts[0].read_text())["status"] == "rollback-failed"


def test_protected_setting_change_stops_before_service_mutation(release, services):
    _, live, package, data, old = release
    states, calls = services
    before = update.protected_hashes(live)
    (live / "edge.env").write_text("operator new setting\n")
    with pytest.raises(update.Blocked, match="변경"):
        update.apply_update(live, package, data, old, dict(states), before)
    assert calls == []
    assert git(live, "rev-parse", "HEAD") == old


@pytest.mark.parametrize("path,blocked", [("edge.env", True), ("x/.env", True),
    ("x/.env.production", True), ("x/.env.example", False), ("edge.env.example", False),
    ("x/server.key", True), ("x/node_modules/a.js", True), ("edge_api_server/app/auth.py", False)])
def test_builder_excludes_credentials_and_machine_artifacts(path, blocked):
    assert builder.forbidden(path) is blocked


def test_builder_refuses_dirty_sources(release, tmp_path):
    source = release[0]
    (source / "app.py").write_text("pending change\n")
    output = tmp_path / "release.zip"
    with pytest.raises(ValueError, match="커밋"):
        builder.build(source, output)
    assert not output.exists()


def test_roundtrip_builder_contains_only_committed_package(release, tmp_path):
    source = release[0]
    deploy = source / "edge_api_server/deploy"
    deploy.mkdir(parents=True)
    shutil.copyfile(DEPLOY / "install_edge_update.py", deploy / "install_edge_update.py")
    shutil.copyfile(DEPLOY / "OFFLINE_EDGE_UPDATE.md", deploy / "OFFLINE_EDGE_UPDATE.md")
    expected = commit(source, "installer")
    (source / "edge.env").write_text("DO_NOT_PACKAGE=local-secret\n")
    output = tmp_path / "release.zip"
    builder.build(source, output)
    import zipfile
    with zipfile.ZipFile(output) as archive:
        assert len(archive.namelist()) == 5
        assert all("edge.env" not in n for n in archive.namelist())
        archive.extractall(tmp_path / "unpack")
    package = next((tmp_path / "unpack").iterdir())
    assert update.verify_package(package)["commit"] == expected
    assert output.with_suffix(".zip.sha256").read_text().startswith(update.digest(output))


def test_service_inspection_requires_actual_python_root_and_preserves_worker_absence(tmp_path, monkeypatch):
    root = tmp_path / "project"
    props = {"LoadState": "loaded", "ActiveState": "active", "User": "rist",
             "WorkingDirectory": str(root / "edge_api_server"),
             "ExecStart": "{ path=" + str(root / ".venv/bin/python") + " ; argv[]=" + str(root / ".venv/bin/python") + " -m app.run ; }",
             "EnvironmentFiles": str(root / "edge.env") + " (ignore_errors=yes)"}
    monkeypatch.setattr(update, "service_properties", lambda unit: props if unit.endswith("api.service") else {"LoadState": "not-found"})
    assert update.inspect_services(root, "rist") == {"rist-edge-api.service": "active"}
    props["WorkingDirectory"] = "/other/edge_api_server"
    with pytest.raises(update.Blocked, match="경로"):
        update.inspect_services(root, "rist")


@pytest.mark.parametrize("key,value", [("User", "other"), ("ActiveState", "failed"),
    ("ExecStart", "{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -m app.run ; }"),
    ("EnvironmentFiles", "/other/edge.env")])
def test_service_mismatch_stops_before_update(tmp_path, monkeypatch, key, value):
    root = tmp_path / "project"
    props = {"LoadState": "loaded", "ActiveState": "active", "User": "rist",
             "WorkingDirectory": str(root / "edge_api_server"),
             "ExecStart": "{ path=" + str(root / ".venv/bin/python") + " ; argv[]=" + str(root / ".venv/bin/python") + " -m app.run ; }",
             "EnvironmentFiles": str(root / "edge.env")}
    props[key] = value
    monkeypatch.setattr(update, "service_properties", lambda unit: props)
    with pytest.raises(update.Blocked):
        update.inspect_services(root, "rist")


def test_unrelated_untracked_and_ignored_files_survive_update(release, services):
    _, live, package, data, old = release
    states, _ = services
    (live / "operator-notes.txt").write_text("keep notes\n")
    (live / ".venv/operator-note.txt").write_text("keep ignored\n")
    update.require_clean(live)
    update.apply_update(live, package, data, old, dict(states), update.protected_hashes(live))
    assert (live / "operator-notes.txt").read_text() == "keep notes\n"
    assert (live / ".venv/operator-note.txt").read_text() == "keep ignored\n"


@pytest.mark.parametrize("local,target,ignored", [
    ("addition.py", "addition.py", False),
    ("addition.py", "addition.py", True),
    ("folder", "folder/addition.py", False),
    ("folder/data.txt", "folder", False),
    ("folder/data.txt", "folder", True),
])
def test_new_tracked_path_collisions_fail_before_service_stop(release, tmp_path, local, target, ignored):
    source, live, package, data, old = release
    new_file=source / target
    new_file.parent.mkdir(parents=True, exist_ok=True)
    new_file.write_text("new source\n")
    data["commit"] = commit(source, "add file")
    git(source, "bundle", "create", str(package / "rist-edge.bundle"), "HEAD")
    local_file=live / local
    local_file.parent.mkdir(parents=True, exist_ok=True)
    local_file.write_text("operator content\n")
    if ignored:
        (live / ".git/info/exclude").write_text(local+"\n")
    update.require_clean(live)
    stage=tmp_path / "collision-plan"
    stage.mkdir()
    with pytest.raises(update.Blocked, match="충돌"):
        update.compare_bundle(live, package, data, stage)
    assert local_file.read_text()=="operator content\n"
    assert git(live,"rev-parse","HEAD")==old


def test_rollback_rejects_new_untracked_collision(release, services, tmp_path):
    source, live, package, data, old = release
    # The newer version deletes a previously tracked file. A later local file at
    # that path must survive a rollback, which would otherwise reset --hard it.
    (source / "app.py").unlink()
    data["commit"] = commit(source, "remove file")
    git(source, "bundle", "create", str(package / "rist-edge.bundle"), "HEAD")
    states, calls = services
    backup=update.apply_update(live, package, data, old, dict(states), update.protected_hashes(live))
    (live / "app.py").write_text("operator replacement\n")
    before=len(calls)
    with pytest.raises(update.Blocked, match="충돌"):
        update.restore(live,json.loads((backup / "state.json").read_text()))
    assert len(calls)==before
    assert (live / "app.py").read_text()=="operator replacement\n"


def test_observed_field_untracked_files_pass_plan_and_survive_apply_and_rollback(release, services, tmp_path):
    """Mirror the field status at base 43f8bcb without copying any live secrets."""
    _, live, package, data, old = release
    states, _ = services
    names = (".rist-xrd-offline.lock", "edge.env.261030", "edge.env.bak.timeout.20261124-182458")
    for index, name in enumerate(names):
        path = live / name
        path.write_bytes(b"" if index == 0 else f"SYNTHETIC_BACKUP_{index}=preserve\n".encode())
        path.chmod(0o600)
    before = {name: ((live / name).read_bytes(), (live / name).stat().st_mode) for name in names}
    status = git(live, "status", "--short")
    assert status.splitlines() == ["?? " + name for name in names]
    update.require_clean(live)
    stage = tmp_path / "field-plan"
    stage.mkdir()
    assert update.compare_bundle(live, package, data, stage) == (old, ["app.py"])
    assert git(live, "status", "--short") == status
    backup = update.apply_update(live, package, data, old, dict(states), update.protected_hashes(live))
    assert git(live, "status", "--short") == status
    assert {name: ((live / name).read_bytes(), (live / name).stat().st_mode) for name in names} == before
    update.restore(live, json.loads((backup / "state.json").read_text()))
    assert git(live, "rev-parse", "HEAD") == old
    assert git(live, "status", "--short") == status
    assert {name: ((live / name).read_bytes(), (live / name).stat().st_mode) for name in names} == before

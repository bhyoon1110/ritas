"""Exercise the real Bash control flow, with privileged operations replaced by mocks."""
import hashlib
import os
from pathlib import Path
import shlex
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "install_offline_reports.sh"


def shell(source, *, stdin="", env=None):
    return subprocess.run(
        ["bash", "-c", f"source {shlex.quote(str(SCRIPT))}\n{source}"],
        input=stdin, text=True, capture_output=True, env={**os.environ, **(env or {})},
    )


def test_syntax_and_help():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    result = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "--install" in result.stdout and "검사만" in result.stdout


@pytest.mark.parametrize("args", ["", "--unknown", "/tmp --project-root", "/tmp /another"])
def test_invalid_arguments(args):
    assert shell(f"reset_options; parse_options {args}").returncode != 0


def test_default_is_check_and_changes_require_separate_flags():
    result = shell(
        "reset_options; parse_options '/tmp/USB files'; "
        'printf "%s|%s|%s|%s" "$R_INSTALL" "$R_PY_CHANGES" "$R_OS_CHANGES" "$R_PACKAGE_DIR"'
    )
    assert result.returncode == 0 and result.stdout == "0|0|0|/tmp/USB files"
    result = shell(
        "reset_options; parse_options /tmp --install --allow-python-version-changes "
        "--allow-os-version-changes --project-root '/tmp/repo with spaces'; "
        'printf "%s|%s|%s|%s" "$R_INSTALL" "$R_PY_CHANGES" "$R_OS_CHANGES" "$R_PROJECT"'
    )
    assert result.returncode == 0 and result.stdout == "1|1|1|/tmp/repo with spaces"


@pytest.mark.parametrize("case", ["valid", "damaged", "missing", "symlink"])
def test_archive_sha(case, tmp_path):
    archive = tmp_path / "archive with spaces"
    data = b"verified distribution bytes"
    archive.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    if case == "damaged":
        archive.write_bytes(b"other data")
    elif case == "missing":
        archive.unlink()
    elif case == "symlink":
        target = tmp_path / "target"
        archive.rename(target)
        archive.symlink_to(target)
    result = shell(f"check_sha {shlex.quote(str(archive))} {digest}")
    assert (result.returncode == 0) == (case == "valid")


def test_both_archive_checks_precede_any_extraction():
    result = shell(r"""
reset_options
R_PACKAGE_DIR=/fixture
check_sha() { [[ $1 != *.tar.gz ]] || fail 'second archive damaged'; }
unzip() { printf 'UNEXPECTED EXTRACTION'; }
tar() { printf 'UNEXPECTED EXTRACTION'; }
prepare_archives
""")
    assert result.returncode != 0
    assert "UNEXPECTED" not in result.stdout


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.mark.parametrize("relation,update", [("same", 0), ("newer", 0), ("older", 1), ("diverged", None)])
def test_real_git_bundle_ancestry_without_mutating_live_repo(tmp_path, relation, update):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Offline Test")
    git(repo, "config", "user.email", "offline-test@example.invalid")
    git(repo, "commit", "--allow-empty", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "commit", "--allow-empty", "-m", "bundled")
    bundled = git(repo, "rev-parse", "HEAD")
    bundle = tmp_path / "fixture.bundle"
    git(repo, "bundle", "create", str(bundle), "main")
    if relation == "newer":
        git(repo, "commit", "--allow-empty", "-m", "newer")
    elif relation in {"older", "diverged"}:
        git(repo, "checkout", "-b", "fixture", base)
        if relation == "diverged":
            git(repo, "commit", "--allow-empty", "-m", "independent")
    original = git(repo, "rev-parse", "HEAD")
    stage = tmp_path / "stage"
    stage.mkdir()
    result = shell(
        f"reset_options\nR_PROJECT={shlex.quote(str(repo))}\nR_STAGE={shlex.quote(str(stage))}\n"
        f"R_BUNDLE={shlex.quote(str(bundle))}\nR_BUNDLE_COMMIT={bundled}\n"
        'plan_code\nprintf "UPDATE=%s" "$R_UPDATE"'
    )
    assert git(repo, "rev-parse", "HEAD") == original
    assert not (repo / ".git/FETCH_HEAD").exists()
    if update is None:
        assert result.returncode != 0 and "분기" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert f"UPDATE={update}" in result.stdout


TRANSACTION = r"""
reset_options
R_PROJECT=/fixture R_PYTHON=/fixture/.venv/bin/python R_USER=rist
R_ORIGINAL_HEAD=original R_BUNDLE=/fixture.bundle R_BUNDLE_COMMIT=bundled
R_UPDATE=$TEST_UPDATE R_PY_CHANGES=$TEST_PY_CHANGES R_OS_CHANGES=$TEST_OS_CHANGES
R_SERVICES=(rist-edge-api.service rist-edge-worker.service)
R_STATES=(active inactive)
R_START=(rist-edge-api.service)
api_state=active worker_state=inactive current_head=original
if [[ $TEST_INACTIVE == 1 ]]; then
    api_state=inactive; R_STATES=(inactive inactive); R_START=()
fi
trap on_exit EXIT
record() { printf '%s\n' "$*" >> "$TEST_LOG"; }
check_repository() { record repository-check; }
service_property() {
    if [[ $1 == rist-edge-api.service ]]; then printf '%s' "$api_state"
    else printf '%s' "$worker_state"; fi
}
git() {
    case "$*" in
        *rev-parse*)
            [[ $TEST_CHANGED == 0 ]] || current_head=changed
            printf '%s' "$current_head" ;;
        *pull*) record "git:$*"; current_head=bundled ;;
        *) record "git:$*" ;;
    esac
}
sudo() {
    record "sudo:$*"
    if [[ $* == 'systemctl stop '* ]]; then
        [[ $TEST_FAIL != stop ]] || return 19
        api_state=inactive; worker_state=inactive
    elif [[ $* == 'systemctl start '* ]]; then
        api_state=active
    fi
}
systemctl() { record "systemctl:$*"; }
xrd() {
    record "xrd:$*"
    [[ $TEST_FAIL != "xrd-$1" ]] || return 17
}
preview_install() {
    record "preview:$*"
    [[ $TEST_FAIL != "preview-$1" ]] || return 18
}
apply_changes
"""


def transaction(tmp_path, *, stdin="INSTALL\n", **options):
    log = tmp_path / "events.log"
    env = dict(
        TEST_LOG=str(log), TEST_FAIL="", TEST_UPDATE="0", TEST_CHANGED="0",
        TEST_PY_CHANGES="0", TEST_OS_CHANGES="0", TEST_INACTIVE="0",
    )
    env.update(options)
    result = shell(TRANSACTION, stdin=stdin, env=env)
    return result, log.read_text().splitlines() if log.exists() else []


def test_success_keeps_newer_code_and_only_restarts_previously_active_service(tmp_path):
    result, events = transaction(tmp_path)
    assert result.returncode == 0, result.stderr
    assert not any("pull" in e for e in events)
    assert "xrd:install --confirm-services-stopped" in events
    assert "preview:install --confirm-services-stopped" in events
    assert events.count("xrd:verify") == 2
    start = "sudo:systemctl start rist-edge-api.service"
    assert events.index("preview:verify") < events.index(start)
    assert events.index("xrd:verify") < events.index(start)
    assert not any(e.startswith("sudo:systemctl start") and "worker" in e for e in events)


def test_explicit_version_approval_and_fast_forward_after_stopping(tmp_path):
    result, events = transaction(tmp_path, TEST_UPDATE="1", TEST_PY_CHANGES="1", TEST_OS_CHANGES="1")
    assert result.returncode == 0, result.stderr
    stop = "sudo:systemctl stop rist-edge-api.service rist-edge-worker.service"
    pull = "git:-C /fixture pull --ff-only /fixture.bundle main"
    assert events.index(stop) < events.index(pull) < events.index("xrd:check")
    assert "xrd:install --confirm-services-stopped --allow-version-changes" in events
    assert "preview:install --confirm-services-stopped --allow-version-changes" in events


@pytest.mark.parametrize("failure", ["stop", "xrd-check", "xrd-install", "xrd-verify", "preview-install", "preview-verify"])
def test_failure_never_restarts_services(tmp_path, failure):
    result, events = transaction(tmp_path, TEST_FAIL=failure)
    assert result.returncode != 0
    assert not any(e.startswith("sudo:systemctl start") for e in events)
    assert "실패 단계" in result.stderr
    if failure.startswith("xrd-"):
        assert not any(e.startswith("preview:") for e in events)


@pytest.mark.parametrize("answer", ["", "yes\n", "취소\n"])
def test_cancellation_does_not_change_services_or_code(tmp_path, answer):
    result, events = transaction(tmp_path, stdin=answer)
    assert result.returncode != 0 and events == []


def test_code_change_after_plan_blocks_before_service_stop(tmp_path):
    result, events = transaction(tmp_path, TEST_CHANGED="1")
    assert result.returncode != 0
    assert not any(e.startswith("sudo:systemctl") for e in events)
    assert "검사 후 코드가 변경" in result.stderr


def test_inactive_services_are_not_started(tmp_path):
    result, events = transaction(tmp_path, TEST_INACTIVE="1")
    assert result.returncode == 0, result.stderr
    assert not any(e.startswith("sudo:systemctl start") for e in events)


@pytest.mark.parametrize("case", ["valid", "worker-missing", "api-missing", "owner", "executable", "failed"])
def test_service_identity_and_state_guards(case):
    result = shell(r"""
reset_options
R_PYTHON=/fixture/.venv/bin/python
id() { printf 'rist'; }
service_property() {
    case "$2" in
        LoadState)
            if [[ $TEST_SERVICE == worker-missing && $1 == rist-edge-worker.service ]] ||
               [[ $TEST_SERVICE == api-missing && $1 == rist-edge-api.service ]]; then
                printf 'not-found'
            else printf 'loaded'; fi ;;
        ActiveState)
            if [[ $TEST_SERVICE == failed ]]; then printf 'failed'; else printf 'active'; fi ;;
        User)
            if [[ $TEST_SERVICE == owner ]]; then printf 'root'; else printf 'rist'; fi ;;
        ExecStart)
            if [[ $TEST_SERVICE == executable ]]; then printf '{ path=/another/python ; }'
            else printf '{ path=/fixture/.venv/bin/python ; argv[]=/fixture/.venv/bin/python -m app.run ; }'; fi ;;
    esac
}
inspect_services
""", env={"TEST_SERVICE": case})
    assert (result.returncode == 0) == (case in {"valid", "worker-missing"})


def test_main_without_install_never_calls_mutations(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    preflight = repo / "edge_api_server/app/xrd_workbench/preflight.py"
    preflight.parent.mkdir(parents=True)
    preflight.write_text("# fixture")
    result = shell(r"""
check_host() { R_PROJECT=$TEST_REPO; R_PACKAGE_DIR=/fixture; }
check_repository() { :; }
flock() { :; }
inspect_services() { :; }
prepare_archives() { :; }
plan_code() { R_UPDATE=0; }
xrd() { printf 'XRD:%s\n' "$*"; }
preview_plan() { printf 'PREVIEW:plan\n'; }
apply_changes() { fail 'UNEXPECTED MUTATION'; }
main /fixture
""", env={"TEST_REPO": str(repo), "TMPDIR": str(tmp_path)})
    assert result.returncode == 0, result.stderr
    assert "XRD:check" in result.stdout and "PREVIEW:plan" in result.stdout
    assert "UNEXPECTED" not in result.stderr
    assert not (repo / ".git/FETCH_HEAD").exists()
    assert len(list(tmp_path.glob("rist-offline-reports.*/install.log"))) == 1


def test_preview_child_uses_readable_smoke_file_without_weakening_private_parent(tmp_path):
    helper = tmp_path / "preview helper.sh"
    helper.write_text(
        'printf "%s\\n" "$@"\numask\n'
        'printf "synthetic smoke PPT" > "$TEST_SMOKE"\n'
    )
    smoke = tmp_path / "smoke.pptx"
    result = shell(r"""
R_PREVIEW=$TEST_HELPER R_USER=rist
sudo() { "$@"; }
preview_install verify
printf 'parent-mask:'
umask
""", env={"TEST_HELPER": str(helper), "TEST_SMOKE": str(smoke)})
    assert result.returncode == 0, result.stderr
    assert "verify\n--service-user\nrist\n0022" in result.stdout
    assert "parent-mask:0077" in result.stdout
    assert smoke.stat().st_mode & 0o777 == 0o644

#!/usr/bin/env python3
"""Apply a verified, code-only Git bundle to an existing offline Python Edge."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request


class Blocked(RuntimeError):
    pass


def run(args, *, cwd=None, check=True, timeout=120, env=None):
    result = subprocess.run([str(x) for x in args], cwd=cwd, text=True,
                            capture_output=True, timeout=timeout, env=env)
    if check and result.returncode:
        # A command's stderr can contain local paths/configuration. Never dump it.
        raise Blocked(f"{Path(str(args[0])).name} 실행 실패 (종료 {result.returncode})")
    return result


def git(root, *args, check=True):
    return run(["git", "-c", "core.hooksPath=/dev/null", "-C", root, *args], check=check)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def verify_package(package):
    package = Path(package).resolve()
    sums = package / "SHA256SUMS"
    if not sums.is_file() or sums.is_symlink():
        raise Blocked("SHA256SUMS 파일이 없습니다.")
    names = set()
    for line in sums.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", line)
        if not match or match[2] in names or match[2] in {".", "..", "SHA256SUMS"}:
            raise Blocked("잘못된 SHA256SUMS 형식입니다.")
        expected, name = match.groups()
        path = package / name
        if not path.is_file() or path.is_symlink() or digest(path) != expected:
            raise Blocked(f"배포 파일 무결성 오류: {name}")
        names.add(name)
    required = {"release.json", "rist-edge.bundle", "install_edge_update.py", "README.md"}
    if names != required:
        raise Blocked("배포 파일 목록이 예상과 다릅니다.")
    release = json.loads((package / "release.json").read_text(encoding="utf-8"))
    if (release.get("format") != 1 or release.get("kind") != "rist-python-edge-update"
            or not re.fullmatch(r"[0-9a-f]{40}", str(release.get("commit", "")))
            or release.get("bundleRef") != "HEAD" or release.get("schemaPolicy") != "additive-only"):
        raise Blocked("지원하지 않는 배포 메타데이터입니다.")
    heads = run(["git", "bundle", "list-heads", package / "rist-edge.bundle"]).stdout.strip()
    if heads != release["commit"] + " HEAD":
        raise Blocked("Git bundle 커밋이 배포 메타데이터와 다릅니다.")
    return release


def require_clean(root):
    if git(root, "status", "--porcelain", "--untracked-files=no").stdout:
        raise Blocked("저장소에 추적 파일의 미커밋 변경이 있습니다. 기존 작업을 보존한 뒤 다시 실행하세요.")


def require_no_collisions(root, target, *, tree_root=None):
    """Keep unrelated operator files, including ignored files, without clobbering."""
    tracked = set(git(tree_root or root, "ls-tree", "-r", "--name-only", "-z", target).stdout.split("\0")) - {""}
    parents = {str(parent) for name in tracked for parent in Path(name).parents if str(parent) != "."}
    # No --exclude-standard: ignored environment, reports and local scripts also count.
    others = git(root, "ls-files", "--others", "-z").stdout.split("\0")
    collisions = []
    for name in others:
        if not name:
            continue
        if name in tracked or name in parents or any(str(parent) in tracked for parent in Path(name).parents):
            collisions.append(name)
    if collisions:
        raise Blocked("기존 미추적/무시 파일이 배포 경로와 충돌합니다. 파일을 보존한 채 확인하세요: "
                      + ", ".join(collisions[:10]))


def service_properties(unit):
    result = run(["systemctl", "show", unit, "--property=LoadState,ActiveState,User,WorkingDirectory,ExecStart,EnvironmentFiles"])
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def inspect_services(root, owner):
    states = {}
    python = root / ".venv/bin/python"
    for unit in ("rist-edge-api.service", "rist-edge-worker.service"):
        props = service_properties(unit)
        if unit.endswith("worker.service") and props.get("LoadState") == "not-found":
            continue
        if props.get("LoadState") != "loaded" or props.get("ActiveState") not in {"active", "inactive"}:
            raise Blocked(f"{unit} 서비스 상태를 먼저 확인하세요.")
        if props.get("User") != owner or Path(props.get("WorkingDirectory", "/")) != root / "edge_api_server":
            raise Blocked(f"{unit} 실행 계정/작업 경로가 예상과 다릅니다.")
        start = props.get("ExecStart", "")
        module = "app.run" if unit.endswith("api.service") else "app.report_worker"
        if f"path={python} ;" not in start or f"-m {module}" not in start:
            raise Blocked(f"{unit}의 Python 직접 실행 경로가 예상과 다릅니다.")
        if str(root / "edge.env") not in props.get("EnvironmentFiles", ""):
            raise Blocked(f"{unit} 환경파일 경로가 예상과 다릅니다.")
        states[unit] = props["ActiveState"]
    return states


def check_host(root):
    if sys.platform != "linux" or os.geteuid() == 0:
        raise Blocked("Linux Edge에서 sudo 없이 서비스 계정(rist)으로 실행하세요.")
    for command in ("git", "sudo", "systemctl"):
        if not shutil.which(command):
            raise Blocked(f"필수 명령이 없습니다: {command}")
    if not (root / ".venv/bin/python").is_file() or not (root / "edge.env").is_file():
        raise Blocked("기존 .venv/bin/python 또는 edge.env가 없습니다.")
    if git(root, "rev-parse", "--show-toplevel").stdout.strip() != str(root):
        raise Blocked("정확한 저장소 루트를 지정하세요.")
    if git(root, "branch", "--show-current").stdout.strip() != "main":
        raise Blocked("운영 저장소가 main 브랜치가 아닙니다.")
    if root.stat().st_uid != os.getuid():
        raise Blocked("저장소 소유자와 현재 실행 계정이 다릅니다.")
    return inspect_services(root, pwd.getpwuid(os.getuid()).pw_name)


def protected_hashes(root):
    paths = [root / "edge.env", root / ".venv/pyvenv.cfg",
             Path("/opt/ritasax-web/lims-dev.known_hosts")]
    result = {}
    for path in paths:
        if path.is_file():
            if not os.access(path, os.R_OK):
                raise Blocked("기존 설정 파일 읽기 권한을 확인하세요.")
            result[str(path)] = digest(path)
    return result


def require_protected(before):
    if any(not Path(p).is_file() or digest(p) != h for p, h in before.items()):
        raise Blocked("검사 중 환경/가상환경 설정/서버 키 파일이 변경되었습니다.")


def dependency_path(path):
    name = Path(path).name
    return name == "pyproject.toml" or (name.startswith("requirements") and not name.startswith("requirements-dev"))


def compare_bundle(root, package, release, stage):
    old = git(root, "rev-parse", "HEAD").stdout.strip()
    compare = stage / "compare.git"
    run(["git", "clone", "--quiet", "--bare", "--shared", root, compare])
    git(compare, "fetch", "--quiet", "--no-tags", package / "rist-edge.bundle", "HEAD:refs/heads/rist-update")
    target = release["commit"]
    if git(compare, "merge-base", "--is-ancestor", old, target, check=False).returncode:
        raise Blocked("현재 코드가 배포본보다 최신이거나 이력이 분기되었습니다. 강제 덮어쓰지 않습니다.")
    require_no_collisions(root, target, tree_root=compare)
    changed = git(compare, "diff", "--name-only", old, target).stdout.splitlines()
    dependencies = [p for p in changed if dependency_path(p)]
    if dependencies:
        raise Blocked("Python 의존성 정의가 달라 별도 오프라인 의존성 패키지가 필요합니다: " + ", ".join(dependencies))
    return old, changed


def authorize_sudo():
    # Inherit the terminal so the operator can see and answer the sudo prompt.
    result = subprocess.run(["sudo", "-v"], timeout=120)
    if result.returncode:
        raise Blocked("sudo 인증을 완료하지 못했습니다.")


def service_action(action, units):
    if units:
        run(["sudo", "systemctl", action, *units], timeout=180)


def require_states(states):
    for unit, expected in states.items():
        if service_properties(unit).get("ActiveState") != expected:
            raise Blocked(f"{unit} 상태가 {expected}가 아닙니다.")


def wait_health(root, backup, seconds=45):
    # Private direct loopback probe, never follows browser/system proxy settings.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with opener.open("http://127.0.0.1:8000/health", timeout=3) as response:
                body = json.loads(response.read(16384))
                if response.status == 200 and body.get("status") == "ok":
                    return
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise Blocked("Edge /health 응답 확인 실패")


def check_imports(root, backup):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(str(root / name) for name in ("edge_api_server", "common", "sune", "rin", "ahn", "lim"))
    env["MPLCONFIGDIR"] = str(backup / "matplotlib-cache")
    env["TMPDIR"] = str(backup)
    result = run([root / ".venv/bin/python", "-B", "-c",
                  "from app.main import create_app; import app.report_worker; print('IMPORT_OK')"],
                 cwd=root / "edge_api_server", check=False, env=env, timeout=120)
    (backup / "import-check.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise Blocked("새 코드 import 검사 실패 (개인 백업 폴더의 import-check.log 참조)")


def restore(root, state):
    target, old = state["newCommit"], state["oldCommit"]
    head = git(root, "rev-parse", "HEAD").stdout.strip()
    if head not in {target, old}:
        raise Blocked("배포 후 다른 커밋으로 변경되어 자동 복원을 중단합니다.")
    require_clean(root)
    require_no_collisions(root, old)
    require_protected(state["protected"])
    service_action("stop", list(state["services"]))
    require_states({unit: "inactive" for unit in state["services"]})
    if head == target and target != old:
        # Only the exact just-installed clean checkout can reach this reset.
        git(root, "reset", "--hard", old)
    service_action("start", [u for u, s in state["services"].items() if s == "active"])
    require_states(state["services"])
    if state["services"].get("rist-edge-api.service") == "active":
        wait_health(root, Path(state["backup"]))


def apply_update(root, package, release, old, states, before):
    require_clean(root)
    if git(root, "rev-parse", "HEAD").stdout.strip() != old:
        raise Blocked("검사 후 Git HEAD가 변경되었습니다.")
    require_states(states)
    require_protected(before)
    backup_base = root.parent / "rist-edge-code-backups"
    backup_base.mkdir(mode=0o700, exist_ok=True)
    if backup_base.is_symlink() or backup_base.stat().st_uid != os.getuid():
        raise Blocked("안전하지 않은 백업 폴더입니다.")
    backup = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-"), dir=backup_base))
    state = {"format": 1, "projectRoot": str(root), "oldCommit": old, "newCommit": release["commit"],
             "services": states, "protected": before, "backup": str(backup), "status": "prepared"}
    state_path = backup / "state.json"
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"코드 복구 기록: {backup}", flush=True)
    authorize_sudo()
    git(root, "update-ref", "refs/rist-backups/" + backup.name, old)
    touched = False
    try:
        touched = True
        service_action("stop", list(states))
        require_states({unit: "inactive" for unit in states})
        require_clean(root)
        git(root, "fetch", "--quiet", "--no-tags", package / "rist-edge.bundle", "HEAD")
        if git(root, "rev-parse", "FETCH_HEAD").stdout.strip() != release["commit"]:
            raise Blocked("검사 후 bundle 커밋이 변경되었습니다.")
        require_no_collisions(root, release["commit"])
        git(root, "merge", "--ff-only", "--no-edit", "--no-overwrite-ignore", release["commit"])
        if git(root, "rev-parse", "HEAD").stdout.strip() != release["commit"]:
            raise Blocked("설치된 커밋이 배포본과 다릅니다.")
        check_imports(root, backup)
        require_protected(before)
        service_action("start", [unit for unit, value in states.items() if value == "active"])
        require_states(states)
        if states.get("rist-edge-api.service") == "active":
            wait_health(root, backup)
        require_states(states)
        state["status"] = "applied"
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except BaseException:
        if touched:
            try:
                restore(root, state)
                state["status"] = "rolled-back"
                print("새 코드 적용 실패: 이전 코드와 서비스 상태를 복원했습니다.", flush=True)
            except Exception as exc:
                state["status"] = "rollback-failed"
                print(f"복원 확인 필요: {exc}; 기록 {backup}", file=sys.stderr)
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise
    print(f"EDGE_UPDATE_OK commit={release['commit']}")
    print("기존 환경·DB 데이터·Spring·known_hosts는 유지했습니다. 새 SSO 설정 테이블은 앱 시작 시 생성됩니다.")
    return backup


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("/home/rist/ritas"))
    parser.add_argument("--package", type=Path, default=Path(__file__).resolve().parent)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true", help="검사 후 코드 적용 및 서비스 재시작")
    action.add_argument("--rollback", type=Path, help="출력된 코드 복구 기록으로 복원")
    args = parser.parse_args(argv)
    os.umask(0o077)
    root = args.project_root.resolve(strict=True)
    states = check_host(root)
    require_clean(root)
    if not args.rollback and states.get("rist-edge-api.service") == "active":
        wait_health(root, root.parent)
    git_dir = Path(git(root, "rev-parse", "--absolute-git-dir").stdout.strip())
    lock_path = git_dir / "rist-edge-update.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Blocked("다른 코드 배포가 진행 중입니다.") from None
        if args.rollback:
            backup = args.rollback.resolve(strict=True)
            if backup.is_symlink() or backup.stat().st_uid != os.getuid():
                raise Blocked("복구 기록 소유자가 다릅니다.")
            state = json.loads((backup / "state.json").read_text(encoding="utf-8"))
            if state.get("projectRoot") != str(root) or state.get("backup") != str(backup):
                raise Blocked("복구 대상 경로가 일치하지 않습니다.")
            authorize_sudo()
            restore(root, state)
            print("EDGE_ROLLBACK_OK")
            return
        package = args.package.resolve(strict=True)
        release = verify_package(package)
        before = protected_hashes(root)
        with tempfile.TemporaryDirectory(prefix="rist-edge-plan-", dir=root.parent) as directory:
            old, changed = compare_bundle(root, package, release, Path(directory))
        print(f"현재 코드: {old}\n배포 코드: {release['commit']}\n변경 파일: {len(changed)}개")
        for unit, state in states.items():
            print(f"{unit}: {state}")
        if old == release["commit"]:
            print("ALREADY_INSTALLED: 같은 커밋입니다. 서비스를 재시작하지 않았습니다.")
            return
        if args.apply:
            apply_update(root, package, release, old, states, before)
        else:
            print("CHECK_OK: 검사만 완료했습니다. 적용하려면 같은 명령에 --apply를 추가하세요.")


if __name__ == "__main__":
    try:
        main()
    except (Blocked, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"중단: {exc}", file=sys.stderr)
        raise SystemExit(1)

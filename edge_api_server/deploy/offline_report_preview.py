"""Verified Ubuntu ARM64 local-only installation of PPT preview system packages."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import re
import shutil
import subprocess
import tempfile

BUNDLE = Path(__file__).resolve().parent / "offline/report-preview-noble-arm64"
TARGET = {"id": "ubuntu", "version": "24.04", "architecture": "arm64"}
ROOTS = ["libreoffice-impress", "fontconfig", "fonts-noto-cjk", "fonts-nanum", "fonts-liberation", "poppler-utils"]
EXPECTED_TEXT = ["RIST TEM", "한글 미리보기 검증", "시료명: 검증시료", "두께: 2.50 nm"]


def run(command, *, env=None, timeout=180):
    result = subprocess.run(command, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"명령 실패({result.returncode}): {' '.join(map(str, command))}\n{result.stdout}")
    return result.stdout


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_bundle(bundle):
    bundle = bundle.resolve()
    if (bundle / "manifest.json").is_symlink():
        raise ValueError("심볼릭 링크 manifest는 허용하지 않습니다.")
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != 1 or manifest.get("target") != TARGET or manifest.get("roots") != ROOTS:
        raise ValueError("지원하지 않는 패키지 대상 또는 구성입니다.")
    paths, names = set(), set()
    for entry in manifest["packages"] + manifest["files"]:
        relative = entry["file"]
        path = bundle / relative
        if not re.fullmatch(r"[A-Za-z0-9_+.%:/~-]+", relative) or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("안전하지 않은 파일 경로입니다.")
        if relative in paths or any(p.is_symlink() for p in [path, *path.parents] if p != bundle.parent):
            raise ValueError("중복 파일 또는 심볼릭 링크는 허용하지 않습니다.")
        paths.add(relative)
        if not path.is_file() or path.stat().st_size != entry["size"] or sha256(path) != entry["sha256"]:
            raise ValueError(f"누락/손상 파일: {relative}")
        if "package" in entry:
            if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", entry["package"]) or entry["package"] in names:
                raise ValueError("잘못된 또는 중복 패키지 이름입니다.")
            if entry["architecture"] not in {"arm64", "all"} or not relative.startswith("pool/") or not relative.endswith(".deb"):
                raise ValueError("지원하지 않는 deb 아키텍처/경로입니다.")
            names.add(entry["package"])
    actual = {str(p.relative_to(bundle)) for p in bundle.rglob("*") if p.is_file() and p != bundle / "manifest.json"}
    if actual != paths or not set(ROOTS) <= names or not {"Packages", "smoke.pptx"} <= paths:
        raise ValueError("패키지 목록이 일치하지 않거나 필수 파일이 없습니다.")
    # The local APT index itself is hash-locked, and must point ONLY at verified files.
    entries = {}
    for paragraph in (bundle / "Packages").read_text().strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in paragraph.splitlines() if ": " in line and not line.startswith(" "))
        if fields.get("Filename") in entries:
            raise ValueError("중복 APT 색인 항목입니다.")
        entries[fields.get("Filename")] = fields
    if set(entries) != {p["file"] for p in manifest["packages"]}:
        raise ValueError("APT 색인 파일 목록이 일치하지 않습니다.")
    for p in manifest["packages"]:
        record = entries[p["file"]]
        if any(record.get(key) != str(p[name]) for key, name in [("Package", "package"), ("Version", "version"), ("Architecture", "architecture"), ("SHA256", "sha256"), ("Size", "size")]):
            raise ValueError("APT 색인 메타데이터 불일치")
    return manifest


def check_environment():
    if platform.system() != "Linux":
        raise ValueError("환경 불일치: Ubuntu 24.04 ARM64 전용입니다.")
    release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    if (platform.system() != "Linux" or release.get("ID", "").strip('"') != "ubuntu"
            or release.get("VERSION_ID", "").strip('"') != "24.04"
            or run(["/usr/bin/dpkg", "--print-architecture"]).strip() != "arm64"):
        raise ValueError("환경 불일치: Ubuntu 24.04 ARM64 전용입니다. 강제 설치하지 마세요.")
    audit = run(["/usr/bin/dpkg", "--audit"])
    if audit.strip():
        raise ValueError("기존 dpkg 미완료 상태를 먼저 점검하세요:\n" + audit)


def safe_env():
    return {"PATH":"/usr/sbin:/usr/bin:/sbin:/bin", "LANG":"C.UTF-8", "LC_ALL":"C.UTF-8", "DEBIAN_FRONTEND":"noninteractive"}


@contextmanager
def apt_context(bundle):
    with tempfile.TemporaryDirectory(prefix="rist-preview-apt-") as tmp:
        root = Path(tmp)
        for name in ("lists/partial", "archives/partial"):
            (root / name).mkdir(parents=True)
        (root / "sources.list").write_text(f"deb [trusted=yes] {bundle.as_uri()} ./\n")
        config = root / "apt.conf"
        config.write_text(f'''Dir::Etc::parts "/dev/null";
Dir::Etc::main "/dev/null";
Dir::Etc::sourcelist "{root}/sources.list";
Dir::Etc::sourceparts "/dev/null";
Dir::State::lists "{root}/lists";
Dir::Cache::archives "{root}/archives";
Dir::Cache::pkgcache "";
Dir::Cache::srcpkgcache "";
Acquire::Languages "none";
Acquire::Retries "0";
APT::Install-Recommends "false";
APT::Install-Suggests "false";
''')
        env = dict(safe_env(), APT_CONFIG=str(config))
        run(["/usr/bin/apt-get", "update"], env=env)
        yield root, env


def installation_plan(env):
    plan = run(["/usr/bin/apt-get", "--simulate", "--no-remove", "--no-install-recommends", "install", *ROOTS], env=env)
    if re.search(r"^Remv ", plan, re.M):
        raise ValueError("기존 패키지 삭제가 필요한 계획은 허용하지 않습니다.")
    changes = re.findall(r"^Inst (\S+) \[([^]]+)\]", plan, re.M)
    return plan, changes


def check_changes(changes, allow):
    for package, _ in changes:
        name = package.split(":")[0]
        if name in {"apt", "dpkg", "udev"} or name.startswith(("libc6", "libc-bin", "systemd", "libsystemd", "libudev", "nvidia-", "libnvidia-", "linux-", "cuda-")):
            raise ValueError(f"OS/GPU 핵심 패키지 변경은 자동 처리하지 않습니다: {name}")
    if changes and not allow:
        raise ValueError("기존 버전 변경이 필요합니다. plan 내용을 검토한 뒤 --allow-version-changes로 명시 승인하세요.")


def services_stopped():
    if Path("/run/systemd/system").exists():
        for name in ("rist-edge-api.service", "rist-edge-worker.service"):
            result = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True)
            if result.stdout.strip() not in {"inactive", "failed", "unknown"}:
                raise ValueError(f"서비스를 먼저 중지하세요: {name} ({result.stdout.strip()})")


def smoke(bundle, user=None):
    prefix = []
    account = None
    if user:
        account = pwd.getpwnam(user)
        if os.geteuid() != 0 and account.pw_uid != os.geteuid():
            raise ValueError("다른 서비스 사용자 검증에는 sudo가 필요합니다.")
        if os.geteuid() == 0:
            prefix = ["/usr/sbin/runuser", "-u", user, "--"]
    env = safe_env()
    fonts = run(prefix + ["/usr/bin/fc-list", ":lang=ko", "family"], env=env)
    if not any(name in fonts for name in ("Noto Sans CJK KR", "NanumGothic")):
        raise ValueError("한글 글꼴을 찾지 못했습니다.")
    with tempfile.TemporaryDirectory(prefix="rist-preview-smoke-") as tmp:
        work = Path(tmp)
        shutil.copyfile(bundle / "smoke.pptx", work / "smoke.pptx")
        if account and os.geteuid() == 0:
            os.chown(work, account.pw_uid, account.pw_gid)
        version = run(prefix + ["/usr/bin/soffice", "--version"], env=env).strip()
        run(prefix + ["/usr/bin/soffice", "-env:UserInstallation=" + (work/"profile").as_uri(),
            "--headless", "--convert-to", "pdf:impress_pdf_Export", "--outdir", str(work), str(work/"smoke.pptx")], env=env, timeout=120)
        pdf = work / "smoke.pdf"
        if not pdf.is_file() or pdf.stat().st_size < 1000:
            raise ValueError("PPT 변환이 PDF를 만들지 못했습니다.")
        text = run(["/usr/bin/pdftotext", str(pdf), "-"], env=env)
        if not all(re.sub(r"\s+", "", label) in re.sub(r"\s+", "", text) for label in EXPECTED_TEXT):
            raise ValueError("PDF의 한글/숫자 검증에 실패했습니다.")
        embedded = run(["/usr/bin/pdffonts", str(pdf)], env=env)
        if not any(any(name in line for name in ("NotoSansCJK", "NanumGothic"))
                   and len(line.split()) >= 8 and line.split()[-5] == "yes"
                   for line in embedded.splitlines()):
            raise ValueError("PDF에 한글 글꼴이 포함되지 않았습니다.")
        print(f"검증 성공: {version}; 한글 PPT→PDF·문자 추출·글꼴 포함 확인 (사용자: {user or pwd.getpwuid(os.geteuid()).pw_name})")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", default="plan", choices=["plan", "install", "verify", "check-bundle"])
    parser.add_argument("--bundle", type=Path, default=BUNDLE)
    parser.add_argument("--service-user", default="rist")
    parser.add_argument("--confirm-services-stopped", action="store_true")
    parser.add_argument("--allow-version-changes", action="store_true")
    args = parser.parse_args(argv)
    bundle = args.bundle.resolve()
    manifest = verify_bundle(bundle)
    print(f"번들 검증 성공: {len(manifest['packages'])}개 deb, Ubuntu 24.04 ARM64")
    if args.mode == "check-bundle":
        return
    check_environment()
    if args.mode == "verify":
        smoke(bundle, args.service_user)
        return
    if args.mode == "install":
        if os.geteuid() != 0 or not args.confirm_services_stopped:
            raise ValueError("sudo 및 --confirm-services-stopped가 필요합니다. 자동으로 서비스를 중지하지 않습니다.")
        pwd.getpwnam(args.service_user)
        services_stopped()
        needed = sum(p["size"] for p in manifest["packages"]) * 3 + 1024**3
        if min(shutil.disk_usage("/").free, shutil.disk_usage(tempfile.gettempdir()).free) < needed:
            raise ValueError(f"설치/검증 공간이 부족합니다. 여유 공간 {needed // 1024**2}MiB 이상을 확보하세요.")
    with apt_context(bundle) as (work, env):
        plan, changes = installation_plan(env)
        print(plan)
        if args.mode == "plan":
            if changes:
                print("기존 버전 변경 예정:", changes)
            print("계획만 확인했습니다. OS·서비스·DB·가상환경은 변경하지 않았습니다.")
            return
        check_changes(changes, args.allow_version_changes)
        backups = Path("/var/backups/rist-report-preview")
        if backups.is_symlink():
            raise ValueError("백업 디렉터리에 심볼릭 링크는 허용하지 않습니다.")
        backups.mkdir(mode=0o700, exist_ok=True)
        if backups.stat().st_uid != 0 or backups.stat().st_mode & 0o077:
            raise ValueError("백업 디렉터리는 root 소유·권한 700이어야 합니다.")
        audit = Path(tempfile.mkdtemp(prefix="install-", dir=backups))
        shutil.copyfile("/var/lib/dpkg/status", audit / "dpkg-status-before")
        (audit / "plan.txt").write_text(plan)
        shutil.copyfile(bundle / "manifest.json", audit / "bundle-manifest.json")
        print(f"설치 전 상태/계획: {audit} (자동 롤백 파일은 아닙니다)", flush=True)
        for entry in manifest["packages"]:
            shutil.copyfile(bundle / entry["file"], work / "archives" / Path(entry["file"]).name)
        try:
            result = run(["/usr/bin/apt-get", "--no-download", "--no-remove", "--no-install-recommends", "--yes", "install", *ROOTS], env=env, timeout=1800)
            (audit / "install.txt").write_text(result)
            print(result)
            run(["/usr/bin/fc-cache", "-f"], env=safe_env())
            smoke(bundle, args.service_user)
        except Exception as exc:
            (audit / "failure.txt").write_text(str(exc))
            raise RuntimeError(f"설치/검증 실패. 부분 설치 가능성이 있으므로 서비스를 시작하지 말고 {audit}을 확인하세요. 자동 롤백하지 않습니다.") from exc
    print("설치 완료. 서비스·DB·환경설정·Python 가상환경은 변경하지 않았습니다. 서비스 시작은 관리자가 진행하세요.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError, KeyError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"중단: {exc}")

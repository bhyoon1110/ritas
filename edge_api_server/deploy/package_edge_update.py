#!/usr/bin/env python3
"""Build a committed, code-only offline Edge bundle; never package the live checkout."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.PIPE)


def sha256(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def forbidden(path):
    parts = Path(path).parts
    name = Path(path).name
    return (any(p in {".venv", "node_modules", ".aws", ".ssh", "__pycache__"} for p in parts)
            or (name in {"edge.env", ".env", "known_hosts", "id_rsa", "id_ed25519"})
            or (name.startswith(".env.") and not name.endswith(".example"))
            or name.endswith((".pem", ".p12", ".pfx", ".key")))


def build(root, output):
    root, output = root.resolve(), output.resolve()
    checksum = output.with_name(output.name + ".sha256")
    if output.exists() or checksum.exists() or output.suffix != ".zip":
        raise ValueError("존재하지 않는 새 .zip 파일 경로를 지정하세요.")
    # Only committed sources enter the archive, but a dirty tree could mislead a
    # release author into believing pending fixes were included.
    if git(root, "status", "--porcelain", "--untracked-files=no").strip():
        raise ValueError("추적 파일 변경을 검증·커밋한 후 패키지를 생성하세요.")
    commit = git(root, "rev-parse", "HEAD").decode().strip()
    historical = git(root, "rev-list", "--objects", "HEAD").decode().splitlines()
    bad = [line.split(" ", 1)[1] for line in historical if " " in line and forbidden(line.split(" ", 1)[1])]
    if bad:
        raise ValueError("배포하면 안 되는 설정/키 경로가 Git 이력에 있습니다: " + ", ".join(sorted(set(bad))[:10]))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rist-edge-build-", dir=output.parent) as temporary:
        package = Path(temporary) / ("RIST-EDGE-" + commit[:12])
        package.mkdir()
        for target, source in {
            "install_edge_update.py": "edge_api_server/deploy/install_edge_update.py",
            "README.md": "edge_api_server/deploy/OFFLINE_EDGE_UPDATE.md",
        }.items():
            (package / target).write_bytes(git(root, "show", commit + ":" + source))
        subprocess.run(["git", "-C", str(root), "bundle", "create", str(package / "rist-edge.bundle"), "HEAD"],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        release = {"format": 1, "kind": "rist-python-edge-update", "commit": commit,
                   "bundleRef": "HEAD", "schemaPolicy": "additive-only",
                   "builtAt": datetime.now(timezone.utc).isoformat(),
                   "service": "rist-edge-api.service", "springIncluded": False}
        (package / "release.json").write_text(json.dumps(release, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        files = sorted(package.iterdir())
        (package / "SHA256SUMS").write_text("".join(f"{sha256(p)}  {p.name}\n" for p in files), encoding="ascii")
        with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_STORED) as archive:
            for path in sorted(package.iterdir()):
                archive.write(path, package.name + "/" + path.name)
    checksum.write_text(f"{sha256(output)}  {output.name}\n", encoding="ascii")
    print(f"PACKAGE_OK commit={commit} bytes={output.stat().st_size}")
    print(f"SHA256={sha256(output)}\n{output}\n{checksum}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    build(args.repository, args.output)


if __name__ == "__main__":
    main()

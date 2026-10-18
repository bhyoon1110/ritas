"""Run only in a disposable Ubuntu 24.04 ARM64 container with Internet access.

Download the complete dependency closure using an EMPTY dpkg status, so packages
already present in the builder cannot silently disappear from the offline kit.
APT verifies the Ubuntu signed repository metadata and package hashes.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOTS = ["libreoffice-impress", "fontconfig", "fonts-noto-cjk", "fonts-nanum",
         "fonts-liberation", "poppler-utils"]


def run(args):
    return subprocess.check_output(args, text=True)


def fields(control):
    return dict(line.split(": ", 1) for line in control.splitlines() if ": " in line and not line.startswith(" "))


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    os_release = fields(Path("/etc/os-release").read_text().replace("=", ": ").replace('"', ''))
    if os_release.get("ID") != "ubuntu" or os_release.get("VERSION_ID") != "24.04" or run(["dpkg", "--print-architecture"]).strip() != "arm64":
        raise SystemExit("Builder must be Ubuntu 24.04 arm64")
    root = args.output.resolve()
    pool = root / "pool"
    pool.mkdir(parents=True, exist_ok=True)
    if list(pool.glob("*.deb")):
        raise SystemExit("Output already contains packages; use a new staging directory")
    subprocess.run(["apt-get", "update"], check=True)
    with tempfile.TemporaryDirectory(prefix="rist-deb-resolve-") as tmp:
        status = Path(tmp) / "empty-status"
        status.touch()
        subprocess.run(["apt-get", "--download-only", "--no-install-recommends", "--yes",
            "-o", f"Dir::State::status={status}", "-o", f"Dir::Cache::archives={pool}",
            "-o", "APT::Get::AllowUnauthenticated=false", "install", *ROOTS], check=True)
    packages, stanzas = [], []
    for deb in sorted(pool.glob("*.deb")):
        control = run(["dpkg-deb", "--field", str(deb)])
        meta = fields(control)
        source = fields(run(["apt-cache", "show", f"{meta['Package']}={meta['Version']}"]).split("\n\n")[0])
        sha = digest(deb)
        if sha != source.get("SHA256"):
            raise SystemExit(f"Ubuntu index hash mismatch: {deb.name}")
        entry = dict(package=meta["Package"], version=meta["Version"], architecture=meta["Architecture"],
                     file="pool/" + deb.name, size=deb.stat().st_size, sha256=sha,
                     source_url="http://ports.ubuntu.com/ubuntu-ports/" + source["Filename"])
        packages.append(entry)
        stanzas.append(control.rstrip() + f"\nFilename: {entry['file']}\nSize: {entry['size']}\nSHA256: {sha}\n")
    (root / "Packages").write_text("\n".join(stanzas), encoding="utf-8")
    # Keep the signed index provenance; local file repository is trusted only
    # after the installer checks every file against this Git-distributed manifest.
    provenance = root / "provenance"
    provenance.mkdir()
    releases = []
    for source in sorted(Path("/var/lib/apt/lists").glob("*InRelease")):
        target = provenance / source.name
        target.write_bytes(source.read_bytes())
        releases.append(dict(file=str(target.relative_to(root)), sha256=digest(target), size=target.stat().st_size))
    shutil.copyfile(Path(__file__).with_name("report_preview_smoke.pptx"), root / "smoke.pptx")
    manifest = dict(schema=1, target={"id":"ubuntu", "version":"24.04", "architecture":"arm64"},
        created_at=datetime.now(timezone.utc).isoformat(), roots=ROOTS, packages=packages,
        files=[dict(file=name, sha256=digest(root/name), size=(root/name).stat().st_size)
               for name in ("Packages", "smoke.pptx")] + releases,
        notes="Official Ubuntu signed APT indexes; complete Depends/Pre-Depends closure, no recommends. Original .deb copyright/license files are retained.")
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # APT's empty lock/partial cache is not part of the distributable.
    (pool / "lock").unlink(missing_ok=True)
    (pool / "partial").rmdir()
    print(json.dumps({"packages":len(packages), "bytes":sum(p["size"] for p in packages)}))


if __name__ == "__main__":
    main()

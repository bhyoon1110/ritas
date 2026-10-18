"""Destructive ONLY inside a disposable --network none Ubuntu ARM64 container."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import fcntl
import socket
import struct

DEPLOY = Path("/src")
OUTPUT = Path("/out")
OUTPUT.mkdir(exist_ok=True)
SCRIPT = ["bash", str(DEPLOY / "install_offline_report_preview.sh")]
ARGS = ["--bundle", "/bundle"]


def run(args, name, expected=0):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (OUTPUT / (name + ".log")).write_text(result.stdout)
    if result.returncode != expected:
        raise RuntimeError(f"{name}: exit {result.returncode}\n{result.stdout[-7000:]}")
    return result.stdout


def status_hash():
    return hashlib.sha256(Path("/var/lib/dpkg/status").read_bytes()).hexdigest()


assert os.geteuid() == 0
# Some Docker kernels expose dormant tunnel devices even with --network none.
# Check namespace-local link flags and routes, not host-visible sysfs names.
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
    for _, name in socket.if_nameindex():
        flags = struct.unpack_from("H", fcntl.ioctl(probe, 0x8913, struct.pack("256s", name.encode())), 16)[0]
        assert name == "lo" or not flags & 1, "run with --network none"
assert not Path("/proc/net/route").read_text().splitlines()[1:], "external route detected"
assert not Path("/usr/bin/soffice").exists(), "use a clean test image"
run(["useradd", "--create-home", "--shell", "/usr/sbin/nologin", "rist"], "create-test-user")
before = status_hash()
run([*SCRIPT, "plan", *ARGS], "plan")
assert status_hash() == before
run(["runuser", "-u", "rist", "--", *SCRIPT, "plan", *ARGS], "unprivileged-plan")
assert status_hash() == before
run([*SCRIPT, "verify", *ARGS], "missing-dependencies", expected=1)
run([*SCRIPT, "install", *ARGS], "unconfirmed-install", expected=1)
assert status_hash() == before
run([*SCRIPT, "install", "--confirm-services-stopped", *ARGS], "install")
run(["runuser", "-u", "rist", "--", *SCRIPT, "verify", *ARGS], "verify-as-service-user")
installed = status_hash()
run([*SCRIPT, "install", "--confirm-services-stopped", *ARGS], "repeat-install")
assert status_hash() == installed
assert not run(["dpkg", "--audit"], "dpkg-audit").strip()
# Keep a rendered smoke PDF so the developer can visually inspect it too.
run(["soffice", "-env:UserInstallation=file:///tmp/rist-offline-test-profile", "--headless", "--convert-to", "pdf", "--outdir", str(OUTPUT), "/bundle/smoke.pptx"], "smoke-render")
if Path("/qa/revised.pptx").exists():
    run(["soffice", "-env:UserInstallation=file:///tmp/rist-offline-tem-profile", "--headless", "--convert-to", "pdf", "--outdir", str(OUTPUT), "/qa/revised.pptx"], "tem-render")
manifest = json.loads(Path("/bundle/manifest.json").read_text())
summary = dict(result="PASS", package_count=len(manifest["packages"]), network="none",
    checks=["fresh install", "service-user Korean PPT conversion", "font embedding", "repeat install", "no plan mutation", "dpkg audit", "confirmation guard"])
(OUTPUT / "result.json").write_text(json.dumps(summary, indent=2))
print(json.dumps(summary))

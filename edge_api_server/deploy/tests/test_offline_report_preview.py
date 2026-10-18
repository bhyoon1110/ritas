"""Unit safety checks; actual offline ARM64 install is tested in Docker separately."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "offline_report_preview.py"
spec = importlib.util.spec_from_file_location("offline_preview", SCRIPT)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def digest(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "bundle"
    (root / "pool").mkdir(parents=True)
    entries, stanzas = [], []
    for name in installer.ROOTS:
        content = name.encode()
        file = f"pool/{name}_1_all.deb"
        (root / file).write_bytes(content)
        entries.append(dict(package=name, version="1", architecture="all", file=file, size=len(content), sha256=digest(content)))
        stanzas.append(f"Package: {name}\nVersion: 1\nArchitecture: all\nFilename: {file}\nSize: {len(content)}\nSHA256: {digest(content)}\n")
    (root / "Packages").write_text("\n".join(stanzas))
    (root / "smoke.pptx").write_bytes(b"unit test fixture")
    manifest = dict(schema=1, target=installer.TARGET, roots=installer.ROOTS, packages=entries,
        files=[dict(file=n, size=(root/n).stat().st_size, sha256=digest((root/n).read_bytes())) for n in ("Packages", "smoke.pptx")])
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root


def test_actual_bundle_has_office_korean_fonts_and_only_arm64_or_all():
    manifest = installer.verify_bundle(installer.BUNDLE)
    names = {p["package"] for p in manifest["packages"]}
    assert {"libreoffice-core", "libreoffice-impress", "fonts-noto-cjk", "fonts-nanum", "poppler-utils"} <= names
    assert len(names) > 100


@pytest.mark.parametrize("damage", ["bytes", "missing", "extra", "link", "manifest_link", "traversal", "target", "architecture", "index", "root", "index_remote"])
def test_invalid_package_kit_rejected(bundle, damage):
    manifest = json.loads((bundle/"manifest.json").read_text())
    file = bundle / manifest["packages"][0]["file"]
    if damage == "bytes": file.write_bytes(b"wrong")
    if damage == "missing": file.unlink()
    if damage == "extra": (bundle/"pool/extra.deb").write_bytes(b"extra")
    if damage == "link":
        outside = bundle.parent/"outside.deb"
        file.rename(outside)
        file.symlink_to(outside)
    if damage == "manifest_link":
        outside = bundle.parent/"outside.json"
        (bundle/"manifest.json").rename(outside)
        (bundle/"manifest.json").symlink_to(outside)
    if damage == "traversal": manifest["packages"][0]["file"] = "../escape.deb"
    if damage == "target": manifest["target"]["version"] = "22.04"
    if damage == "architecture": manifest["packages"][0]["architecture"] = "amd64"
    if damage == "root": manifest["roots"] = ["--allow-unauthenticated"]
    if damage == "index": (bundle/"Packages").write_text("corrupted")
    if damage == "index_remote":
        text = (bundle/"Packages").read_text().replace("Filename: pool/", "Filename: https://example.invalid/")
        (bundle/"Packages").write_text(text)
        manifest["files"][0].update(size=len(text.encode()), sha256=digest(text.encode()))
    if damage != "manifest_link": (bundle/"manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError): installer.verify_bundle(bundle)


def test_valid_hashes_and_manifest(bundle):
    assert len(installer.verify_bundle(bundle)["packages"]) == len(installer.ROOTS)


def test_no_network_sources_or_global_apt_hooks(bundle, monkeypatch):
    commands = []
    monkeypatch.setattr(installer, "run", lambda command, **kw: commands.append((command, kw)) or "")
    monkeypatch.setenv("APT_CONFIG", "/untrusted/config")
    monkeypatch.setenv("LD_PRELOAD", "/untrusted/lib.so")
    with installer.apt_context(bundle) as (work, env):
        assert "LD_PRELOAD" not in env and env["APT_CONFIG"] != "/untrusted/config"
        assert (work/"sources.list").read_text() == f"deb [trusted=yes] {bundle.as_uri()} ./\n"
        config = (work/"apt.conf").read_text()
        for option in ("parts", "main", "sourceparts"):
            assert f'Dir::Etc::{option} "/dev/null";' in config
        assert 'APT::Install-Recommends "false"' in config
        assert commands[0][0] == ["/usr/bin/apt-get", "update"]
    assert not work.exists()


def test_installation_plan_forbids_removal(monkeypatch):
    monkeypatch.setattr(installer, "run", lambda *a, **kw: "Remv nvidia-driver [1]\n")
    with pytest.raises(ValueError): installer.installation_plan({})


@pytest.mark.parametrize("package", ["libc6", "libc-bin", "dpkg", "apt", "systemd", "libsystemd0", "libudev1", "udev", "linux-image-generic", "nvidia-driver-580", "libnvidia-compute-580", "cuda-toolkit-13-0"])
def test_critical_system_changes_are_never_forced(package):
    with pytest.raises(ValueError): installer.check_changes([(package, "old")], True)


def test_other_existing_package_changes_need_explicit_approval():
    with pytest.raises(ValueError): installer.check_changes([("libreoffice-core", "old")], False)
    installer.check_changes([("libreoffice-core", "old")], True)
    installer.check_changes([], False)


def test_wrong_os_stops_before_running_apt(monkeypatch):
    monkeypatch.setattr(installer.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(installer, "run", lambda *a, **kw: pytest.fail("must not run apt/dpkg on macOS"))
    with pytest.raises(ValueError, match="환경 불일치"): installer.check_environment()


@pytest.mark.parametrize("release,architecture,audit", [
    ('ID=ubuntu\nVERSION_ID="22.04"', "arm64", ""),
    ('ID=debian\nVERSION_ID="24.04"', "arm64", ""),
    ('ID=ubuntu\nVERSION_ID="24.04"', "amd64", ""),
    ('ID=ubuntu\nVERSION_ID="24.04"', "arm64", "unconfigured package"),
])
def test_wrong_linux_target_or_existing_dpkg_damage_rejected(monkeypatch, release, architecture, audit):
    monkeypatch.setattr(installer.platform, "system", lambda: "Linux")
    monkeypatch.setattr(installer.Path, "read_text", lambda *a, **kw: release)
    monkeypatch.setattr(installer, "run", lambda cmd: architecture if "--print-architecture" in cmd else audit)
    with pytest.raises(ValueError): installer.check_environment()


@pytest.mark.parametrize("state", ["active", "activating", "deactivating", ""])
def test_running_or_unknown_service_state_blocks_install(monkeypatch, state):
    from types import SimpleNamespace
    monkeypatch.setattr(installer.Path, "exists", lambda *a: True)
    monkeypatch.setattr(installer.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=state))
    with pytest.raises(ValueError): installer.services_stopped()

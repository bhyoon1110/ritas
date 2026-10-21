"""Run on Linux as root in a disposable container, never on a production Edge.

Most service calls are deterministic doubles. The final test uses real Nginx,
OpenSSL, curl and a loopback HTTP server; systemd alone remains a double.
"""
from __future__ import annotations

import json
import fcntl
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "setup_https.sh"
pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() != 0,
    reason="Use a disposable Linux container as root (no production service access).",
)


def run(*args: str, env=None):
    return subprocess.run(
        ["bash", str(SCRIPT), *map(str, args)],
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )


def ok(result):
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.fixture(scope="module")
def certs(tmp_path_factory):
    root = tmp_path_factory.mktemp("https-certs")
    password = root / "pass"
    password.write_text("synthetic-test-only-passphrase\n")
    password.chmod(0o600)
    ca = root / "ca"
    tls = root / "tls"
    ok(run("init-ca", "--ca-dir", ca, "--ca-pass-file", password))
    ok(run("csr", "--tls-dir", tls))
    cert = root / "edge.crt"
    ok(run("sign", "--ca-dir", ca, "--ca-pass-file", password,
           "--csr", tls / "edge.csr", "--out", cert))
    return {"root": root, "ca": ca, "tls": tls, "cert": cert, "password": password}


SYSTEMCTL = r'''#!/usr/bin/python3
import json, os, pathlib, subprocess, sys
p = pathlib.Path(os.environ["FAKE_STATE"])
state = json.loads(p.read_text())
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write("systemctl " + " ".join(args) + "\n")
action = args[0]
nginx = "nginx" in args
if action == "show":
    print(os.environ["FAKE_ENV_FILE"] + " (ignore_errors=yes)")
elif action == "is-active":
    sys.exit(0 if (state["active"] if nginx else True) else 3)
elif action == "is-enabled":
    sys.exit(0 if state["enabled"] else 1)
elif action == "restart" and os.environ.get("FAKE_RESTART_FAIL") and not state.get("failed"):
    state["failed"] = True
    p.write_text(json.dumps(state))
    sys.exit(1)
else:
    if nginx and action in ("start", "reload", "stop") and os.environ.get("REAL_NGINX"):
        command = ["nginx"]
        if action != "start": command += ["-s", "reload" if action == "reload" else "quit"]
        subprocess.run(command, check=True)
    if nginx and action == "start": state["active"] = True
    if nginx and action == "stop": state["active"] = False
    if nginx and action == "enable": state["enabled"] = True
    if nginx and action == "disable": state["enabled"] = False
    p.write_text(json.dumps(state))
'''

NGINX = r'''#!/usr/bin/python3
import os, pathlib, sys
config = pathlib.Path(os.environ["FAKE_NGINX_CONFIG"])
if "-T" in sys.argv:
    if os.environ.get("FAKE_CONFLICT"):
        port = os.environ.get("FAKE_CONFLICT_PORT", "443")
        print("# configuration file /etc/nginx/conf.d/another.conf:\nserver {\n listen " + port + ";\n}")
    if config.exists() and not os.environ.get("FAKE_OMIT_INCLUDE"):
        print("# configuration file " + str(config) + ":")
        print(config.read_text())
elif "-t" in sys.argv and config.exists() and os.environ.get("FAKE_NGINX_FAIL"):
    failed = config.parent / "failed-once"
    if not failed.exists():
        failed.touch()
        sys.exit(1)
'''

SS = r'''#!/usr/bin/python3
import os, sys
if "-ltnp" in sys.argv:
    selected = " ".join(sys.argv[1:])
    port = os.environ.get("FAKE_LISTENER_PORT", "443")
    print(os.environ.get("FAKE_TLS_LISTENER", "") if selected.endswith(":" + port) else "")
else:
    address = "0.0.0.0" if os.environ.get("FAKE_PUBLIC_API") else "127.0.0.1"
    print("LISTEN 0 128 " + address + ":" + os.environ.get("FAKE_API_PORT", "8000") + " 0.0.0.0:*")
'''

CURL = r'''#!/usr/bin/python3
import os, sys
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write("curl " + " ".join(sys.argv[1:]) + "\n")
if os.environ.get("FAKE_CURL_FAIL"): sys.exit(22)
if "--data" in sys.argv: print(os.environ.get("FAKE_SSO_CODE", "401"), end="")
'''


def executable(path: Path, content: str):
    path.write_text(content)
    path.chmod(0o755)


@pytest.fixture
def layout(tmp_path, certs):
    project = tmp_path / "project"
    project.mkdir()
    env_file = project / "edge.env"
    env_file.write_text(
        '# operator-owned settings\nRIST_DB_PASSWORD="$(touch /tmp/never-execute-rist-env)"\n'
        'RIST_SSO_SID="preserve-synthetic-sid"\nRIST_SSO_CA_BUNDLE=/etc/posco/ca.pem\n'
        'RIST_STORAGE_ROOT=/data/important-reports\n'
        'RIST_EDGE_PUBLIC_BASE_URL=http://old:8000\n'
        'RIST_AUTH_COOKIE_SECURE=false\nRIST_AUTH_ENABLED=false\n'
        'RIST_AUTH_COOKIE_SECURE=false\n'
    )
    env_file.chmod(0o640)
    state = tmp_path / "service-state.json"
    state.write_text(json.dumps({"active": False, "enabled": False}))
    log = tmp_path / "calls.log"
    log.touch()
    tls = tmp_path / "tls"
    config = tmp_path / "nginx-conf" / "rist.conf"
    config.parent.mkdir()
    backup = tmp_path / "backups"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, content in {"systemctl": SYSTEMCTL, "nginx": NGINX, "ss": SS, "curl": CURL,
                          "sleep": "#!/bin/sh\nexit 0\n"}.items():
        executable(bin_dir / name, content)
    env = dict(os.environ, PATH=str(bin_dir) + ":" + os.environ["PATH"],
               FAKE_STATE=str(state), FAKE_LOG=str(log), FAKE_ENV_FILE=str(env_file),
               FAKE_NGINX_CONFIG=str(config))
    args = ["--project-root", project, "--tls-dir", tls,
            "--nginx-config", config, "--backup-root", backup]
    apply_args = ["--cert", certs["cert"], "--key", certs["tls"] / "edge.key",
                  "--ca-cert", certs["ca"] / "root-ca.crt"]
    return dict(project=project, env_file=env_file, env=env, args=args, apply_args=apply_args,
                tls=tls, config=config, backup=backup, state=state, log=log, bin_dir=bin_dir,
                original=env_file.read_bytes())


def apply(layout, *extra):
    return run("apply", *layout["args"], *layout["apply_args"], *extra, env=layout["env"])


def apply_http(layout, *extra):
    return run("apply", *layout["args"], "--mode", "http", *extra, env=layout["env"])


def backups(layout):
    return sorted(layout["backup"].glob("backup.*"))


def assert_unchanged(layout):
    assert layout["env_file"].read_bytes() == layout["original"]
    assert not layout["config"].exists()
    assert not (layout["tls"] / "edge.key").exists()
    assert json.loads(layout["state"].read_text())["active"] is False


def test_bash_syntax_and_help():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    assert "rollback" in ok(run("--help")).stdout


def test_default_is_read_only_plan(layout):
    result = ok(run(*layout["args"], env=layout["env"]))
    assert "파일/서비스 변경 없음" in result.stdout
    assert not layout["backup"].exists()
    assert layout["log"].read_text() == ""
    assert_unchanged(layout)


@pytest.mark.parametrize("host", ["999.1.1.1", "010.1.1.1", "host;touch", "x\nlisten", "https://host", "-bad", "a..b", "a.", "[::1]", "a_1"])
def test_rejects_invalid_or_injected_hosts(host):
    assert run("plan", "--host", host).returncode != 0


@pytest.mark.parametrize("host", ["10.10.1.31", "rist.lab.example", "localhost"])
def test_accepts_hosts(host):
    assert host in ok(run("plan", "--host", host)).stdout


@pytest.mark.parametrize("args", [["--tls-dir", "/"], ["--tls-dir", "/etc"],
                                  ["--tls-dir", "/tmp/../etc/ssl"], ["--https-port", "0"],
                                  ["--https-port", "8000"], ["--https-port", "65536"],
                                  ["--http-port", "0"], ["--http-port", "65536"],
                                  ["--mode", "http", "--http-port", "8000"],
                                  ["--mode", "both", "--http-port", "443"],
                                  ["--mode", "both", "--https-port", "80"],
                                  ["--mode", "unknown"],
                                  ["--host"], ["--invalid"]])
def test_rejects_unsafe_options(args):
    assert run("plan", *args).returncode != 0


def test_ca_private_key_is_encrypted_and_not_replaced(certs):
    key = certs["ca"] / "root-ca.key"
    assert "ENCRYPTED PRIVATE KEY" in key.read_text()
    before = key.read_bytes()
    assert run("init-ca", "--ca-dir", certs["ca"], "--ca-pass-file", certs["password"]).returncode != 0
    assert key.read_bytes() == before


def test_csr_is_not_replaced(certs):
    before = (certs["tls"] / "edge.key").read_bytes()
    assert run("csr", "--tls-dir", certs["tls"]).returncode != 0
    assert (certs["tls"] / "edge.key").read_bytes() == before


def test_ca_password_file_must_be_private(tmp_path):
    password = tmp_path / "public-pass"
    password.write_text("test-only\n")
    password.chmod(0o644)
    result = run("init-ca", "--ca-dir", tmp_path / "ca", "--ca-pass-file", password)
    assert result.returncode != 0
    assert not (tmp_path / "ca").exists()


def test_wrong_ca_password_does_not_publish_certificate(certs, tmp_path):
    password = tmp_path / "wrong-pass"
    password.write_text("not-the-test-ca-password\n")
    password.chmod(0o600)
    output = tmp_path / "must-not-exist.crt"
    result = run("sign", "--ca-dir", certs["ca"], "--csr", certs["tls"] / "edge.csr",
                 "--out", output, "--ca-pass-file", password)
    assert result.returncode != 0
    assert not output.exists()


def test_no_private_material_in_git(tmp_path):
    (tmp_path / ".git").mkdir()
    result = run("csr", "--tls-dir", tmp_path / "certificates")
    assert result.returncode != 0
    assert not (tmp_path / "certificates").exists()


def test_no_private_material_in_project_without_git(tmp_path):
    project = tmp_path / "copied-project"
    project.mkdir()
    result = run("csr", "--project-root", project, "--tls-dir", project / "certificates")
    assert result.returncode != 0
    assert not (project / "certificates").exists()


def test_no_symlink_targets(layout, tmp_path):
    destination = tmp_path / "unrelated"
    destination.mkdir()
    layout["tls"].symlink_to(destination, target_is_directory=True)
    assert apply(layout).returncode != 0
    assert not list(destination.iterdir())
    assert layout["env_file"].read_bytes() == layout["original"]


def test_broken_key_symlink_is_not_followed(tmp_path):
    tls = tmp_path / "tls"
    tls.mkdir()
    target = tmp_path / "unrelated-key"
    (tls / "edge.key").symlink_to(target)
    assert run("csr", "--tls-dir", tls).returncode != 0
    assert not target.exists()


def test_success_preserves_secrets_and_permissions(layout, certs):
    result = ok(apply(layout))
    text = layout["env_file"].read_text()
    for line in layout["original"].decode().splitlines()[:5]:
        assert line in text
    assert text.count("RIST_AUTH_COOKIE_SECURE=true") == 1
    assert text.count("RIST_SSO_ALLOW_HTTP=false") == 1
    assert "RIST_EDGE_PUBLIC_BASE_URL=https://10.10.1.31" in text
    assert "RIST_EDGE_BIND_HOST=127.0.0.1" in text
    assert layout["env_file"].stat().st_mode & 0o777 == 0o640
    assert layout["tls"].stat().st_mode & 0o777 == 0o700
    assert (layout["tls"] / "edge.key").stat().st_mode & 0o777 == 0o600
    assert backups(layout)[0].stat().st_mode & 0o777 == 0o700
    assert "preserve-synthetic-sid" not in result.stdout + result.stderr
    assert not Path("/tmp/never-execute-rist-env").exists()
    config = layout["config"].read_text()
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in config
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in config
    assert "proxy_request_buffering off;" in config
    assert "regenerate/?$ { return 403; }" in config
    assert "TLSv1.2 TLSv1.3" in config
    assert "TLSv1 " not in config
    assert not (layout["tls"] / "root-ca.key").exists()
    assert (layout["tls"] / "client-ca.cer").exists()
    calls = layout["log"].read_text()
    assert "--cacert" in calls and "--resolve" in calls and "--noproxy *" in calls
    assert "worker" not in calls and "ufw" not in calls
    assert "passphrase" not in calls


def test_repeat_apply_reuses_certificates_and_ca(layout):
    ok(apply(layout))
    first = {p.name: p.read_bytes() for p in layout["tls"].iterdir() if p.is_file()}
    env = layout["env_file"].read_bytes()
    ok(apply(layout))
    assert {p.name: p.read_bytes() for p in layout["tls"].iterdir() if p.is_file()} == first
    assert layout["env_file"].read_bytes() == env
    assert len(backups(layout)) == 2


@pytest.mark.parametrize("mode,urls", [
    ("http", ["http://10.10.1.31"]),
    ("https", ["https://10.10.1.31"]),
    ("both", ["http://10.10.1.31", "https://10.10.1.31"]),
])
def test_plan_describes_selected_listeners_without_mutation(layout, mode, urls):
    result = ok(run("plan", *layout["args"], "--mode", mode, env=layout["env"]))
    for url in urls:
        assert url in result.stdout
    assert_unchanged(layout)
    assert not layout["backup"].exists()
    assert layout["log"].read_text() == ""


def test_http_apply_needs_no_certificate_and_checks_http_without_ca(layout):
    ok(apply_http(layout))
    config = layout["config"].read_text()
    text = layout["env_file"].read_text()
    assert "listen 80;" in config
    assert "ssl_certificate" not in config
    assert "listen 443" not in config
    assert "return 301" not in config and "return 308" not in config
    assert "proxy_pass http://127.0.0.1:8000;" in config
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in config
    assert "regenerate/?$ { return 403; }" in config
    for setting in ("RIST_EDGE_PUBLIC_BASE_URL=http://10.10.1.31",
                    "RIST_SSO_ALLOW_HTTP=true", "RIST_AUTH_COOKIE_SECURE=false",
                    "RIST_AUTH_ENABLED=true", "RIST_EDGE_BIND_HOST=127.0.0.1",
                    "FORWARDED_ALLOW_IPS=127.0.0.1"):
        assert text.count(setting) == 1
    for line in layout["original"].decode().splitlines()[:5]:
        assert line in text
    assert not layout["tls"].exists()
    assert "--cacert" not in layout["log"].read_text()
    ok(run("check", *layout["args"], "--mode", "http", env=layout["env"]))
    ok(run("rollback", "--backup", backups(layout)[0], env=layout["env"]))
    assert_unchanged(layout)


def test_http_custom_port_is_used_by_proxy_public_url_and_check(layout):
    ok(apply_http(layout, "--http-port", "8080"))
    assert "listen 8080;" in layout["config"].read_text()
    assert "RIST_EDGE_PUBLIC_BASE_URL=http://10.10.1.31:8080" in layout["env_file"].read_text()
    assert "http://10.10.1.31:8080/health" in layout["log"].read_text()
    ok(run("check", *layout["args"], "--mode", "http", "--http-port", "8080", env=layout["env"]))


def test_both_mode_enables_both_listeners_and_checks_each(layout):
    ok(apply(layout, "--mode", "both"))
    config = layout["config"].read_text()
    text = layout["env_file"].read_text()
    assert "listen 80;" in config and "listen 443 ssl;" in config
    assert "return 301" not in config and "return 308" not in config
    assert "RIST_SSO_ALLOW_HTTP=true" in text
    assert "RIST_AUTH_COOKIE_SECURE=false" in text
    calls = layout["log"].read_text()
    assert "http://10.10.1.31/health" in calls
    assert "https://10.10.1.31/health" in calls
    assert "--cacert" in calls


def test_http_keeps_existing_tls_material_on_apply_and_rollback(layout):
    layout["tls"].mkdir(mode=0o700)
    original = {name: ("existing material " + name).encode() for name in
                ("edge.key", "edge-chain.pem", "ca.pem", "client-ca.cer", "edge.csr")}
    for name, content in original.items():
        target = layout["tls"] / name
        target.write_bytes(content)
        target.chmod(0o600)
    ok(apply_http(layout))
    assert {p.name: p.read_bytes() for p in layout["tls"].iterdir()} == original
    ok(run("rollback", "--backup", backups(layout)[0], env=layout["env"]))
    assert {p.name: p.read_bytes() for p in layout["tls"].iterdir()} == original
    assert layout["env_file"].read_bytes() == layout["original"]
    assert not layout["config"].exists()


def test_http_rejected_by_application_rolls_back(layout):
    layout["env"]["FAKE_SSO_CODE"] = "426"
    result = apply_http(layout)
    assert result.returncode != 0
    assert "이전 파일/서비스 상태 복원 완료" in result.stderr
    assert_unchanged(layout)
    assert not layout["tls"].exists()


def test_http_to_both_to_https_and_rollback_preserves_previous_mode(layout):
    ok(apply_http(layout))
    ok(apply(layout, "--mode", "both"))
    both_env = layout["env_file"].read_bytes()
    both_config = layout["config"].read_bytes()
    both_tls = {p.name: p.read_bytes() for p in layout["tls"].iterdir()}
    before = set(backups(layout))
    ok(apply(layout))
    assert "listen 80;" not in layout["config"].read_text()
    text = layout["env_file"].read_text()
    assert "RIST_SSO_ALLOW_HTTP=false" in text
    assert "RIST_AUTH_COOKIE_SECURE=true" in text
    last_backup, = set(backups(layout)) - before
    ok(run("rollback", "--backup", last_backup, env=layout["env"]))
    assert layout["env_file"].read_bytes() == both_env
    assert layout["config"].read_bytes() == both_config
    assert {p.name: p.read_bytes() for p in layout["tls"].iterdir()} == both_tls


@pytest.mark.parametrize("mode,port", [("http", "80"), ("both", "80"), ("both", "443")])
@pytest.mark.parametrize("kind", ["nginx", "process"])
def test_each_selected_port_is_checked_before_mutation(layout, mode, port, kind):
    if kind == "nginx":
        layout["env"].update(FAKE_CONFLICT="1", FAKE_CONFLICT_PORT=port)
    else:
        layout["env"].update(FAKE_LISTENER_PORT=port,
                             FAKE_TLS_LISTENER=f'LISTEN 0 128 0.0.0.0:{port} *:* users:(("java",pid=1,fd=1))')
    result = apply_http(layout) if mode == "http" else apply(layout, "--mode", mode)
    assert result.returncode != 0
    assert_unchanged(layout)
    assert not backups(layout)


@pytest.mark.parametrize("mode,unselected_port", [("http", "443"), ("https", "80")])
def test_unselected_nginx_port_does_not_block_apply(layout, mode, unselected_port):
    layout["env"].update(FAKE_CONFLICT="1", FAKE_CONFLICT_PORT=unselected_port)
    ok(apply_http(layout) if mode == "http" else apply(layout))


def test_v1_backup_remains_restorable(layout):
    ok(apply(layout))
    backup = backups(layout)[0]
    metadata = (backup / "metadata").read_text().splitlines()
    assert metadata[0] == "RIST_HTTPS_BACKUP_V2" and len(metadata) == 9
    metadata[0] = "RIST_HTTPS_BACKUP_V1"
    (backup / "metadata").write_text("\n".join(metadata[:7]) + "\n")
    ok(run("rollback", "--backup", backup, env=layout["env"]))
    assert_unchanged(layout)


def test_wrong_certificate_host_does_not_modify_settings(layout):
    result = apply(layout, "--host", "10.10.1.32")
    assert result.returncode != 0
    assert_unchanged(layout)
    assert not backups(layout)


def test_wrong_private_key_does_not_modify_settings(layout, tmp_path):
    tls = tmp_path / "other-key"
    ok(run("csr", "--tls-dir", tls))
    assert apply(layout, "--key", tls / "edge.key").returncode != 0
    assert_unchanged(layout)


def test_multiple_root_certificates_are_not_exported_to_clients(layout, certs, tmp_path):
    bundle = tmp_path / "two-roots.pem"
    bundle.write_bytes((certs["ca"] / "root-ca.crt").read_bytes() * 2)
    result = apply(layout, "--ca-cert", bundle)
    assert result.returncode != 0
    assert_unchanged(layout)


def test_dns_certificate(layout, certs, tmp_path):
    cert = tmp_path / "dns.crt"
    ok(run("sign", "--ca-dir", certs["ca"], "--ca-pass-file", certs["password"],
           "--csr", certs["tls"] / "edge.csr", "--out", cert, "--host", "rist.lab.example"))
    ok(apply(layout, "--host", "rist.lab.example", "--cert", cert))
    assert "https://rist.lab.example" in layout["env_file"].read_text()


def test_apply_lock_prevents_concurrent_changes(layout):
    layout["backup"].mkdir(mode=0o700)
    with (layout["backup"] / ".rist-https.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = apply(layout)
    assert result.returncode != 0
    assert "다른 HTTPS 설정 작업" in result.stderr
    assert_unchanged(layout)


def test_existing_directory_permissions_are_not_changed(layout):
    layout["tls"].mkdir(mode=0o755)
    result = apply(layout)
    assert result.returncode != 0
    assert layout["tls"].stat().st_mode & 0o777 == 0o755
    assert_unchanged(layout)
    assert not backups(layout)


def test_unrelated_nginx_file_is_preserved(layout):
    layout["config"].write_text("# unrelated administrator configuration\n")
    assert apply(layout).returncode != 0
    assert layout["config"].read_text() == "# unrelated administrator configuration\n"
    assert layout["env_file"].read_bytes() == layout["original"]


@pytest.mark.parametrize("setting,value", [
    ("FAKE_CONFLICT", "1"),
    ("FAKE_TLS_LISTENER", 'LISTEN 0 128 0.0.0.0:443 *:* users:(("java",pid=1,fd=1))'),
])
def test_port_conflicts_fail_before_mutation(layout, setting, value):
    layout["env"][setting] = value
    assert apply(layout).returncode != 0
    assert_unchanged(layout)
    assert not backups(layout)


@pytest.mark.parametrize("setting,value", [
    ("FAKE_NGINX_FAIL", "1"), ("FAKE_OMIT_INCLUDE", "1"),
    ("FAKE_RESTART_FAIL", "1"), ("FAKE_CURL_FAIL", "1"),
    ("FAKE_SSO_CODE", "426"), ("FAKE_SSO_CODE", "200"),
    ("FAKE_PUBLIC_API", "1"),
])
def test_failure_rolls_back_files_and_services(layout, setting, value):
    layout["env"][setting] = value
    result = apply(layout)
    assert result.returncode != 0
    assert "이전 파일/서비스 상태 복원 완료" in result.stderr
    assert_unchanged(layout)
    backup = backups(layout)[0]
    assert (backup / "0.before").read_bytes() == layout["original"]
    assert (backup / "key.new").exists()  # Nothing is lost; backup is root-only.


def test_manual_rollback_is_itself_recoverable(layout):
    ok(apply(layout))
    original_backup = backups(layout)[0]
    after = layout["env_file"].read_bytes()
    ok(run("rollback", "--backup", original_backup, env=layout["env"]))
    assert_unchanged(layout)
    recovery = next(p for p in backups(layout) if p != original_backup)
    assert (recovery / "0.before").read_bytes() == after
    ok(run("rollback", "--backup", recovery, env=layout["env"]))
    assert layout["env_file"].read_bytes() == after
    assert json.loads(layout["state"].read_text())["active"] is True


@pytest.mark.parametrize("setting", ["FAKE_NGINX_FAIL", "FAKE_RESTART_FAIL", "FAKE_SSO_CODE"])
def test_failure_preserves_previously_running_https(layout, setting):
    ok(apply(layout))
    before_env = layout["env_file"].read_bytes()
    before_config = layout["config"].read_bytes()
    before_key = (layout["tls"] / "edge.key").read_bytes()
    layout["env"][setting] = "426" if setting == "FAKE_SSO_CODE" else "1"
    result = apply(layout)
    assert result.returncode != 0
    assert "이전 파일/서비스 상태 복원 완료" in result.stderr
    assert layout["env_file"].read_bytes() == before_env
    assert layout["config"].read_bytes() == before_config
    assert (layout["tls"] / "edge.key").read_bytes() == before_key
    state = json.loads(layout["state"].read_text())
    assert state["active"] is True and state["enabled"] is True


def test_incomplete_or_public_backup_is_not_restored(layout):
    ok(apply(layout))
    backup = backups(layout)[0]
    backup.chmod(0o755)
    assert run("rollback", "--backup", backup, env=layout["env"]).returncode != 0
    backup.chmod(0o700)
    (backup / "0.before").unlink()
    assert run("rollback", "--backup", backup, env=layout["env"]).returncode != 0
    assert "https://10.10.1.31" in layout["env_file"].read_text()


def test_apply_requires_root():
    if not shutil.which("setpriv"):
        pytest.skip("setpriv is unavailable")
    result = subprocess.run(
        ["setpriv", "--reuid=65534", "--regid=65534", "--clear-groups", "bash", str(SCRIPT),
         "apply", "--cert", "/tmp/absent-cert", "--ca-cert", "/tmp/absent-ca"],
        text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert "sudo/root" in result.stderr


@pytest.mark.parametrize("mode", ["http", "https", "both"])
def test_real_nginx_proxy_and_control_api_block(layout, certs, tmp_path, mode):
    binary = shutil.which("nginx", path="/usr/sbin:/usr/bin:/sbin:/bin")
    if not binary:
        pytest.skip("Install nginx for the real TLS integration test")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            if self.path == "/headers":
                self.wfile.write(json.dumps({
                    "scheme": self.headers.get("X-Forwarded-Proto"),
                    "forwarded_for": self.headers.get("X-Forwarded-For"),
                    "real_ip": self.headers.get("X-Real-IP"),
                    "authorization": self.headers.get("Authorization"),
                }).encode())
            else:
                self.wfile.write(b'{"status":"ok"}')

        def do_POST(self):
            scheme = self.headers.get("X-Forwarded-Proto")
            allowed = scheme == "https" or (scheme == "http" and mode in ("http", "both"))
            self.send_response(401 if allowed else 426)
            self.end_headers()

        def log_message(self, *_):
            pass

    backend = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=backend.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as probe_http, socket.socket() as probe_https:
        probe_http.bind(("127.0.0.1", 0))
        probe_https.bind(("127.0.0.1", 0))
        http_port = probe_http.getsockname()[1]
        https_port = probe_https.getsockname()[1]
    api_port = backend.server_address[1]
    main_config = tmp_path / "nginx-main.conf"
    pid_file = tmp_path / "nginx.pid"
    main_config.write_text(
        f"pid {pid_file};\nerror_log {tmp_path}/error.log;\nevents {{}}\n"
        f"http {{ access_log off; include {layout['config'].parent}/*.conf; }}\n"
    )
    for name in ("ss", "curl", "sleep"):
        (layout["bin_dir"] / name).unlink()
    executable(layout["bin_dir"] / "nginx", f'#!/bin/sh\nexec {binary} -c "{main_config}" "$@"\n')
    layout["env"]["REAL_NGINX"] = "1"
    try:
        options = ["--mode", mode, "--http-port", str(http_port),
                   "--https-port", str(https_port), "--api-port", str(api_port)]
        certificate_args = [] if mode == "http" else layout["apply_args"]
        result = ok(run("apply", *layout["args"], *certificate_args, *options, env=layout["env"]))
        assert f"Nginx {mode} 설정/검사 완료" in result.stdout
        ok(run("check", *layout["args"], *options, env=layout["env"]))
        frontends = []
        if mode in ("http", "both"):
            frontends.append(("http", http_port))
        if mode in ("https", "both"):
            frontends.append(("https", https_port))
        for scheme, port in frontends:
            command = ["curl", "--silent", "--show-error", "--noproxy", "*",
                       "--resolve", f"10.10.1.31:{port}:127.0.0.1"]
            if scheme == "https":
                command += ["--cacert", str(certs["ca"] / "root-ca.crt")]
            base_url = f"{scheme}://10.10.1.31:{port}"

            def request(*args):
                result = subprocess.run([*command, *args], capture_output=True, text=True,
                                        timeout=10, env=layout["env"])
                assert result.returncode == 0, result.stderr
                return result.stdout

            # A client cannot forge the trusted proxy's scheme/source address.
            spoofed_scheme = "https" if scheme == "http" else "http"
            observed = json.loads(request(
                "-H", f"X-Forwarded-Proto: {spoofed_scheme}",
                "-H", "X-Forwarded-For: 203.0.113.99",
                "-H", "X-Real-IP: 203.0.113.99",
                "-H", "Authorization: Bearer synthetic-proxy-test", base_url + "/headers",
            ))
            assert observed == {"scheme": scheme, "forwarded_for": "127.0.0.1",
                                "real_ip": "127.0.0.1",
                                "authorization": "Bearer synthetic-proxy-test"}
            assert request("-o", "/dev/null", "-w", "%{http_code}", "--data", "{}",
                           base_url + "/api/v1/auth/sso/verify") == "401"
            assert request("-o", "/dev/null", "-w", "%{http_code}", "--data", "{}",
                           base_url + "/api/v1/reports/synthetic/regenerate") == "403"
        ok(run("rollback", "--backup", backups(layout)[0], env=layout["env"]))
        assert_unchanged(layout)
    finally:
        if pid_file.exists():
            subprocess.run([binary, "-c", str(main_config), "-s", "quit"], capture_output=True)
        backend.shutdown()
        backend.server_close()
        thread.join(timeout=2)

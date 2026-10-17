"""Safety guards run anywhere; real Linux/no-network installation is documented separately."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'offline_xrd.py'
spec = importlib.util.spec_from_file_location('offline_xrd', SCRIPT)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / 'bundle'
    (root / 'wheels').mkdir(parents=True)
    wheel = root / 'wheels' / 'example-1.0-py3-none-any.whl'
    wheel.write_bytes(b'synthetic-wheel-integrity-test')
    sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
    lock = f'example==1.0 --hash=sha256:{sha}\n'
    (root / 'requirements.lock').write_text(lock)
    value = dict(schema=1, target=dict(system='Linux', machine='aarch64', implementation='CPython',
                                     python='3.12', glibc_min='2.28'),
                 requirements_sha256=hashlib.sha256(lock.encode()).hexdigest(),
                 wheels=[dict(name='example', version='1.0', filename=wheel.name,
                              size=wheel.stat().st_size, sha256=sha)])
    (root / 'manifest.json').write_text(json.dumps(value))
    return root


def test_committed_bundle_integrity_and_complete_locked_dependencies():
    manifest = installer.verify_bundle(installer.BUNDLE)
    assert len(manifest['wheels']) == 11
    versions = {installer.canonical(e['name']): e['version'] for e in manifest['wheels']}
    assert versions['matplotlib'] == '3.11.2'
    assert versions['numpy'] == '2.2.6'


@pytest.mark.parametrize('damage', ['bytes', 'missing', 'extra', 'symlink', 'lock', 'traversal', 'package_option', 'target'])
def test_corruption_and_unsafe_manifest_rejected(bundle, damage):
    manifest_path = bundle / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    wheel = next((bundle / 'wheels').iterdir())
    if damage == 'bytes':
        wheel.write_bytes(b'changed')
    elif damage == 'missing':
        wheel.unlink()
    elif damage == 'extra':
        (bundle / 'wheels' / 'extra.whl').write_bytes(b'extra')
    elif damage == 'symlink':
        target = bundle / 'target'
        wheel.rename(target)
        wheel.symlink_to(target)
    elif damage == 'lock':
        (bundle / 'requirements.lock').write_text('https://unexpected.example/pkg.whl')
    elif damage == 'traversal':
        manifest['wheels'][0]['filename'] = '../escape.whl'
    elif damage == 'package_option':
        manifest['wheels'][0]['name'] = '--extra-index-url'
    elif damage == 'target':
        manifest['target']['machine'] = 'x86_64'
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        installer.verify_bundle(bundle)


@pytest.mark.parametrize('field,value', [('system','Darwin'), ('machine','x86_64'), ('python','3.11'), ('implementation','PyPy')])
def test_wrong_runtime_rejected(bundle, monkeypatch, field, value):
    target = installer.verify_bundle(bundle)['target']
    monkeypatch.setattr(installer.platform, 'system', lambda: value if field == 'system' else 'Linux')
    monkeypatch.setattr(installer.platform, 'machine', lambda: value if field == 'machine' else 'aarch64')
    monkeypatch.setattr(installer.platform, 'python_implementation', lambda: value if field == 'implementation' else 'CPython')
    from types import SimpleNamespace
    monkeypatch.setattr(installer.sys, 'version_info', SimpleNamespace(major=3, minor=11 if field == 'python' else 12))
    with pytest.raises(ValueError, match='환경 불일치'):
        installer.check_environment(target)


def test_pip_is_hash_locked_and_offline(bundle, monkeypatch):
    command = installer.pip_command(bundle)
    for flag in ('--isolated', '--no-index', '--require-hashes', '--only-binary=:all:'):
        assert flag in command
    assert '--no-deps' not in command
    monkeypatch.setenv('PIP_EXTRA_INDEX_URL', 'https://unexpected.invalid')
    monkeypatch.setenv('PYTHONPATH', '/unexpected/path')
    env = installer.offline_env()
    assert 'PIP_EXTRA_INDEX_URL' not in env and 'PYTHONPATH' not in env
    assert env['PIP_CONFIG_FILE'] == os.devnull


def prepare_main(tmp_path, bundle, monkeypatch, installed=None):
    project = tmp_path / 'project'
    file = project / 'edge_api_server/app/xrd_workbench/preflight.py'
    file.parent.mkdir(parents=True)
    file.write_text('# synthetic')
    venv = tmp_path / '.venv'
    venv.mkdir()
    (venv / 'original').write_text('preserve me')
    monkeypatch.setattr(installer, 'check_environment', lambda _: venv)
    monkeypatch.setattr(installer, 'installed_versions', lambda: installed or {})
    monkeypatch.setattr(installer, 'services_stopped', lambda: None)
    calls = []
    monkeypatch.setattr(installer, 'run', lambda command, **kwargs: calls.append(command))
    monkeypatch.setattr(installer, 'smoke', lambda _: None)
    args = ['--bundle', str(bundle), '--project-root', str(project)]
    return args, venv, calls


def test_check_is_read_only_and_install_requires_maintenance(tmp_path, bundle, monkeypatch):
    args, venv, calls = prepare_main(tmp_path, bundle, monkeypatch)
    assert installer.main(['check', *args]) == 0
    assert any('--dry-run' in command for command in calls)
    assert not list(tmp_path.glob('.venv.xrd-*'))
    assert installer.main(['install', *args]) == 1
    assert not list(tmp_path.glob('.venv.xrd-*'))


def test_existing_version_change_requires_explicit_flag(tmp_path, bundle, monkeypatch):
    args, venv, calls = prepare_main(tmp_path, bundle, monkeypatch, {'example': '0.9'})
    assert installer.main(['install', *args, '--confirm-services-stopped']) == 1
    assert not list(tmp_path.glob('.venv.xrd-*'))
    assert installer.main(['install', *args, '--confirm-services-stopped', '--allow-version-changes']) == 0
    assert list(tmp_path.glob('.venv.xrd-backup-*/venv/original'))


def test_failure_restores_original_venv_and_preserves_failed_copy(tmp_path, bundle, monkeypatch):
    args, venv, calls = prepare_main(tmp_path, bundle, monkeypatch)
    def fail(_):
        (venv / 'original').write_text('modified during install')
        (venv / 'new-file').write_text('new')
        raise ValueError('simulated smoke failure')
    monkeypatch.setattr(installer, 'smoke', fail)
    assert installer.main(['install', *args, '--confirm-services-stopped']) == 1
    assert (venv / 'original').read_text() == 'preserve me'
    assert not (venv / 'new-file').exists()
    assert list(tmp_path.glob('.venv.xrd-failed-*/venv/new-file'))


def test_running_services_block_install(monkeypatch):
    monkeypatch.setattr(installer.shutil, 'which', lambda _: '/bin/systemctl')
    monkeypatch.setattr(installer.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0, 'active\n', ''))
    with pytest.raises(ValueError, match='실행 중'):
        installer.services_stopped()


def test_low_disk_space_preserves_environment(tmp_path, monkeypatch):
    from types import SimpleNamespace
    venv = tmp_path / '.venv'
    venv.mkdir()
    (venv / 'original').write_text('unchanged')
    monkeypatch.setattr(installer.shutil, 'disk_usage', lambda _: SimpleNamespace(free=1))
    with pytest.raises(ValueError, match='여유 공간'):
        installer.backup_venv(venv)
    assert not list(tmp_path.glob('.venv.xrd-*'))
    assert (venv / 'original').read_text() == 'unchanged'


def test_concurrent_package_change_stops_before_backup(tmp_path, bundle, monkeypatch):
    args, venv, calls = prepare_main(tmp_path, bundle, monkeypatch)
    snapshots = iter([{}, {'example': '0.9'}])
    monkeypatch.setattr(installer, 'installed_versions', lambda: next(snapshots))
    assert installer.main(['install', *args, '--confirm-services-stopped']) == 1
    assert not list(tmp_path.glob('.venv.xrd-*'))
    assert all('--dry-run' in c for c in calls if 'install' in c)


@pytest.mark.parametrize('invalid', ['system', 'symlink', 'old_glibc'])
def test_unsafe_environment_rejected(tmp_path, bundle, monkeypatch, invalid):
    from types import SimpleNamespace
    venv = tmp_path / '.venv'
    venv.mkdir()
    (venv / 'pyvenv.cfg').touch()
    prefix = venv
    if invalid == 'symlink':
        prefix = tmp_path / 'linked'
        prefix.symlink_to(venv, target_is_directory=True)
    monkeypatch.setattr(installer.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(installer.platform, 'machine', lambda: 'aarch64')
    monkeypatch.setattr(installer.platform, 'python_implementation', lambda: 'CPython')
    monkeypatch.setattr(installer.platform, 'libc_ver', lambda: ('glibc', '2.17' if invalid == 'old_glibc' else '2.39'))
    monkeypatch.setattr(installer.sys, 'version_info', SimpleNamespace(major=3, minor=12))
    monkeypatch.setattr(installer.sys, 'prefix', str(prefix))
    monkeypatch.setattr(installer.sys, 'base_prefix', str(prefix) if invalid == 'system' else '/usr')
    with pytest.raises(ValueError):
        installer.check_environment(installer.verify_bundle(bundle)['target'])

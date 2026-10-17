"""Offline matplotlib repair for an existing Edge virtualenv. No service/DB changes."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile

BUNDLE = Path(__file__).resolve().parent / 'offline' / 'xrd-cp312-linux-aarch64'
SERVICES = ('rist-edge-api.service', 'rist-edge-worker.service')


def canonical(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_bundle(bundle):
    manifest = json.loads((bundle / 'manifest.json').read_text())
    if manifest.get('schema') != 1 or manifest.get('target') != {
        'system': 'Linux', 'machine': 'aarch64', 'implementation': 'CPython',
        'python': '3.12', 'glibc_min': '2.28',
    }:
        raise ValueError('지원하지 않는 번들 형식/대상 환경입니다.')
    entries = manifest['wheels']
    if not entries or len({canonical(e['name']) for e in entries}) != len(entries):
        raise ValueError('wheel 목록이 비었거나 패키지가 중복되었습니다.')
    expected = set()
    lines = []
    for entry in entries:
        filename = entry['filename']
        if Path(filename).name != filename or not filename.endswith('.whl') or '\\' in filename:
            raise ValueError('안전하지 않은 wheel 파일명입니다.')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', entry['name']) or not re.fullmatch(r'[0-9][A-Za-z0-9_.+!-]*', entry['version']):
            raise ValueError('패키지 이름/버전 형식이 올바르지 않습니다.')
        if not re.fullmatch(r'[0-9a-f]{64}', entry['sha256']):
            raise ValueError('올바르지 않은 SHA-256입니다.')
        path = bundle / 'wheels' / filename
        if path.is_symlink() or not path.is_file() or path.stat().st_size != entry['size'] or digest(path) != entry['sha256']:
            raise ValueError(f'wheel 무결성 검증 실패: {filename}')
        expected.add(filename)
        lines.append(f"{entry['name']}=={entry['version']} --hash=sha256:{entry['sha256']}")
    if {p.name for p in (bundle / 'wheels').iterdir()} != expected:
        raise ValueError('wheel 폴더에 누락 또는 미등록 파일이 있습니다.')
    lock = bundle / 'requirements.lock'
    if lock.is_symlink() or lock.read_text() != '\n'.join(lines) + '\n':
        raise ValueError('requirements.lock과 manifest가 일치하지 않습니다.')
    if digest(lock) != manifest['requirements_sha256']:
        raise ValueError('requirements.lock 무결성 검증 실패')
    return manifest


def check_environment(target):
    actual = {'system': platform.system(), 'machine': platform.machine(),
              'implementation': platform.python_implementation(),
              'python': f'{sys.version_info.major}.{sys.version_info.minor}'}
    for key, value in actual.items():
        if value != target[key]:
            raise ValueError(f'환경 불일치: {key}={value}, 필요={target[key]}. 다른 환경용 wheel을 준비하세요.')
    libc, version = platform.libc_ver()
    if libc != 'glibc' or tuple(map(int, version.split('.')[:2])) < (2, 28):
        raise ValueError('glibc 2.28 이상이 필요합니다. musl/Alpine 환경은 지원하지 않습니다.')
    venv = Path(sys.prefix).absolute()
    if sys.prefix == sys.base_prefix or not (venv / 'pyvenv.cfg').is_file() or venv.is_symlink():
        raise ValueError('실제 서비스의 독립 가상환경(.venv) Python으로 실행하세요.')
    if venv in (Path('/'), Path.home()) or venv.parent == venv:
        raise ValueError('안전하지 않은 가상환경 경로입니다.')
    return venv


def installed_versions():
    return {canonical(d.metadata['Name']): d.version for d in importlib.metadata.distributions()
            if d.metadata.get('Name')}


def version_changes(manifest, installed):
    return [(e['name'], installed.get(canonical(e['name'])), e['version']) for e in manifest['wheels']
            if installed.get(canonical(e['name'])) != e['version']]


def run(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def pip_command(bundle):
    return [sys.executable, '-m', 'pip', '--isolated', '--disable-pip-version-check',
            'install', '--no-index', '--no-cache-dir', '--only-binary=:all:',
            '--find-links', str(bundle / 'wheels'), '--require-hashes', '-r', str(bundle / 'requirements.lock')]


def offline_env():
    # No configured index/find-links/proxy should influence this operation.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith('PIP_') and k not in ('PYTHONPATH', 'PYTHONHOME')}
    env['PIP_CONFIG_FILE'] = os.devnull
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return env


def smoke(project):
    run([sys.executable, '-m', 'pip', '--isolated', 'check'], env=offline_env())
    with tempfile.TemporaryDirectory(prefix='rist-xrd-smoke-') as temp:
        env = dict(offline_env(), MPLCONFIGDIR=temp)
        run([sys.executable, '-c',
             'import io; from matplotlib.figure import Figure; '
             'from matplotlib.backends.backend_agg import FigureCanvasAgg; '
             'from PIL import Image; f=Figure(); FigureCanvasAgg(f); '
             'f.add_subplot().plot([1,2,3],[2,5,1]); b=io.BytesIO(); f.savefig(b,format="png"); '
             'b.seek(0); im=Image.open(b); im.verify(); print("PNG 렌더링 정상")'], env=env)
        run([sys.executable, '-m', 'app.xrd_workbench.preflight'], cwd=project / 'edge_api_server', env=env)


def services_stopped():
    if not shutil.which('systemctl'):
        return
    for service in SERVICES:
        result = subprocess.run(['systemctl', 'is-active', service], capture_output=True, text=True)
        if result.stdout.strip() in ('active', 'activating', 'reloading', 'deactivating'):
            raise ValueError(f'{service}가 실행 중입니다. 진행 중인 작업 완료 후 API/worker를 중지하세요.')


def backup_venv(venv):
    size = sum(p.stat().st_size for p in venv.rglob('*') if p.is_file() and not p.is_symlink())
    if shutil.disk_usage(venv.parent).free < 2 * size + 512 * 1024 ** 2:
        raise ValueError('가상환경 백업/복구를 위한 디스크 여유 공간이 부족합니다.')
    backup = Path(tempfile.mkdtemp(prefix=venv.name + '.xrd-backup-', dir=venv.parent))
    shutil.copytree(venv, backup / 'venv', symlinks=True)
    (backup / 'versions-before.json').write_text(json.dumps(installed_versions(), indent=2))
    print(f'가상환경 백업: {backup}', flush=True)
    return backup


def restore_venv(venv, backup):
    # Keep both the failed environment and the untouched backup; never delete either.
    failed = Path(tempfile.mkdtemp(prefix=venv.name + '.xrd-failed-', dir=venv.parent))
    venv.rename(failed / 'venv')
    shutil.copytree(backup / 'venv', venv, symlinks=True)
    print(f'원래 가상환경 경로로 복원했습니다. 실패 환경 보존: {failed}', file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('check', 'install', 'verify'), nargs='?', default='check')
    parser.add_argument('--bundle', type=Path, default=BUNDLE)
    parser.add_argument('--project-root', type=Path, default=Path('/home/rist/ritas'))
    parser.add_argument('--confirm-services-stopped', action='store_true', help='API/worker 및 수동 실행 프로세스가 중지되었음을 확인')
    parser.add_argument('--allow-version-changes', action='store_true', help='check에 표시된 기존 패키지 버전 변경도 허용')
    args = parser.parse_args(argv)
    try:
        bundle = args.bundle.resolve()
        manifest = verify_bundle(bundle)
        venv = check_environment(manifest['target'])
        project = args.project_root.resolve()
        if not (project / 'edge_api_server/app/xrd_workbench/preflight.py').is_file():
            raise ValueError('프로젝트에 preflight.py가 없습니다. 수정 코드를 먼저 Git bundle로 적용하세요.')
        changes = version_changes(manifest, installed_versions())
        print(f'대상 Python: {sys.executable}\n프로젝트: {project}\n검증된 wheel: {len(manifest["wheels"])}개', flush=True)
        for name, before, after in changes:
            print(f'  {name}: {before or "미설치"} -> {after}', flush=True)
        if args.mode == 'verify' or not changes:
            if changes:
                raise ValueError('번들 버전과 설치 상태가 다릅니다. check/install 결과를 확인하세요.')
            smoke(project)
            print('검증 완료. 실제 보고서 미리보기/내보내기는 별도 확인하세요.')
            return 0
        run([sys.executable, '-m', 'pip', '--isolated', 'check'], env=offline_env())
        with tempfile.TemporaryDirectory(prefix='rist-xrd-plan-') as temp:
            run(pip_command(bundle) + ['--dry-run', '--report', str(Path(temp) / 'plan.json')], env=offline_env())
        if args.mode == 'check':
            print('설치 계획 확인 완료(설치/서비스 변경 없음). 기존 버전 변경이 있으면 --allow-version-changes가 필요합니다.')
            return 0
        if not args.confirm_services_stopped:
            raise ValueError('유지보수 시간에 모든 API/worker 프로세스를 중지하고 --confirm-services-stopped를 지정하세요.')
        services_stopped()
        if any(before for _, before, _ in changes) and not args.allow_version_changes:
            raise ValueError('기존 패키지 버전 변경은 기본 차단합니다. 계획 검토 후 --allow-version-changes로 명시적으로 허용하세요.')
        import fcntl
        with (venv.parent / '.rist-xrd-offline.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # A concurrent administrator may have changed packages/services after check.
            if version_changes(manifest, installed_versions()) != changes:
                raise ValueError('설치 계획 확인 후 패키지 상태가 변경되었습니다. check부터 다시 실행하세요.')
            services_stopped()
            backup = backup_venv(venv)
            try:
                run(pip_command(bundle), env=offline_env())
                smoke(project)
            except BaseException:
                restore_venv(venv, backup)
                raise
            (backup / 'installation.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        print('설치/검증 완료. 백업을 보존했습니다. 서비스는 자동 시작하지 않습니다.')
        return 0
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as exc:
        print(f'중단: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

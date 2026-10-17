"""Run only in the disposable Dockerfile.offline-xrd container with --network none."""
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path('/repo')
DEPLOY = ROOT / 'edge_api_server/deploy'
WORK = Path('/work')


def main():
    # This path is intentionally confined to a disposable container, not the host.
    if not Path('/.dockerenv').exists() or not Path('/opt/baseline/pyvenv.cfg').is_file():
        raise SystemExit('Run in the documented disposable Docker container only.')
    WORK.mkdir(exist_ok=False)
    venv = WORK / 'venv'
    shutil.copytree('/opt/baseline', venv, symlinks=True)
    env = dict(os.environ, RIST_OFFLINE_PYTHON=str(venv / 'bin/python'))

    def run(args, expected=0, **kw):
        result = subprocess.run(args, env=kw.pop('env', env), **kw)
        assert result.returncode == expected, (args, result.returncode)

    script = ['bash', str(DEPLOY / 'install_offline_xrd.sh')]
    project = ['--project-root', str(ROOT)]
    run([str(venv / 'bin/python'), '-c', 'import matplotlib'], expected=1)
    run(script + ['check', *project])
    assert not list(WORK.glob('venv.xrd-backup-*'))
    run(script + ['install', *project, '--confirm-services-stopped'])
    run(script + ['verify', *project])
    run(script + ['install', *project, '--confirm-services-stopped'])
    assert len(list(WORK.glob('venv.xrd-backup-*'))) == 1
    print('PASS: offline check/install/verify/idempotence', flush=True)

    # Real pip mutation followed by intentionally failing application preflight.
    bad_project = WORK / 'bad-project'
    bad_module = bad_project / 'edge_api_server/app/xrd_workbench/preflight.py'
    bad_module.parent.mkdir(parents=True)
    bad_module.write_text('raise SystemExit("synthetic post-install failure")\n')
    rollback = WORK / 'rollback'
    shutil.copytree('/opt/baseline', rollback, symlinks=True)
    bad_env = dict(env, RIST_OFFLINE_PYTHON=str(rollback / 'bin/python'))
    run(script + ['install', '--project-root', str(bad_project), '--confirm-services-stopped'], expected=1, env=bad_env)
    run([str(rollback / 'bin/python'), '-c', 'import matplotlib'], expected=1, env=bad_env)
    assert len(list(WORK.glob('rollback.xrd-failed-*/venv'))) == 1
    assert len(list(WORK.glob('rollback.xrd-backup-*/venv'))) == 1
    print('PASS: failed installation rollback, backup and failed environment preserved', flush=True)

    # Prove that no transitive dependencies are missing from the wheelhouse.
    run(['python3.12', '-m', 'venv', '/work/clean'])
    run(['/work/clean/bin/python', '-m', 'pip', '--isolated', 'install', '--no-index', '--no-cache-dir',
         '--find-links', str(DEPLOY / 'offline/xrd-cp312-linux-aarch64/wheels'), '--require-hashes',
         '-r', str(DEPLOY / 'offline/xrd-cp312-linux-aarch64/requirements.lock')])
    run(['/work/clean/bin/python', '-m', 'pip', 'check'])
    run(['/work/clean/bin/python', '-c', 'import matplotlib; from matplotlib.backends.backend_agg import FigureCanvasAgg'])
    print('PASS: complete 11-wheel dependency closure in an empty venv', flush=True)
    run([str(venv / 'bin/python'), '-m', 'pytest', '-q', '-p', 'no:cacheprovider', str(DEPLOY / 'tests/test_offline_xrd.py')])


if __name__ == '__main__':
    main()

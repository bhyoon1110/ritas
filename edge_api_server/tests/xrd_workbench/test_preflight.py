import os
import subprocess
import sys
from pathlib import Path

from app.xrd_workbench import preflight


def test_preflight_imports_the_renderers():
    assert preflight.check_runtime() == []


def test_preflight_reports_missing_dependency_without_installing(monkeypatch, capsys):
    def load(name):
        if name.startswith('matplotlib'):
            raise ModuleNotFoundError("No module named 'matplotlib'")
    monkeypatch.setattr(preflight.importlib, 'import_module', load)
    assert preflight.main() == 1
    text = capsys.readouterr()
    assert sys.executable in text.out
    assert 'matplotlib' in text.err
    assert 'requirements.txt' in text.err


def test_preflight_reports_binary_import_failure(monkeypatch):
    def load(name):
        if name == 'PIL.Image':
            raise OSError('shared library missing')
    monkeypatch.setattr(preflight.importlib, 'import_module', load)
    assert 'Pillow' in preflight.check_runtime()[0]


def test_first_preview_without_matplotlib_has_actionable_error(tmp_path):
    # Fresh interpreter: exercise the actual lazy import in draft_preview, not
    # an exception injected into a renderer already loaded in this process.
    program = '''
import sys
from pathlib import Path
from fastapi.testclient import TestClient
from tests.xrd_workbench.test_integration import make_app, upload
from app.error_archive import install_error_management
from app.xrd_workbench import integration as api
app = make_app(Path(sys.argv[1]))
archive = install_error_management(app, app.state.settings)
client = TestClient(app, raise_server_exceptions=False)
sid = upload(client, {'A.txt': b'#Intensity_unit=cps\\n10 1\\n20 8\\n30 3\\n'})
doc = client.get(api.BASE + f'/api/reports/{sid}').json()['document']
assert 'app.xrd_workbench.render.layout' not in sys.modules
sys.modules['matplotlib'] = None
response = client.post(api.BASE + f'/api/reports/{sid}/draft-preview', json={'document': doc})
assert response.status_code == 503, response.text
assert response.json()['code'] == 'XRD_PREVIEW_DEPENDENCY_MISSING'
trace = (archive.root / response.json()['errorEventId'] / 'traceback.txt').read_text()
assert 'matplotlib' in trace and 'ModuleNotFoundError' in trace
assert 'render/figure.py' in trace
assert client.get(api.BASE + f'/api/reports/{sid}').json()['document'] == doc
del sys.modules['matplotlib']
assert client.post(api.BASE + f'/api/reports/{sid}/draft-preview', json={'document': doc}).status_code == 200
'''
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
               RIST_ERROR_ARCHIVE_ROOT=str(tmp_path / 'errors'),
               MPLCONFIGDIR=str(tmp_path / 'mpl'))
    result = subprocess.run([sys.executable, '-c', program, str(tmp_path / 'data')],
                            cwd=Path(__file__).resolve().parents[2], env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

import pytest
from fastapi.testclient import TestClient

from app.error_archive import install_error_management
from app.xrd_workbench import integration as api
from app.xrd_workbench.render import posco_pptx
from .test_integration import make_app, upload


@pytest.mark.parametrize('exception,code,status', [
    (ModuleNotFoundError, 'XRD_PREVIEW_DEPENDENCY_MISSING', 503),
    (PermissionError, 'XRD_PREVIEW_STORAGE_ERROR', 503),
    (MemoryError, 'XRD_PREVIEW_MEMORY_ERROR', 503),
    (RuntimeError, 'INTERNAL_SERVER_ERROR', 500),
])
def test_preview_environment_failure_keeps_trace_and_sources_and_releases_lock(tmp_path, monkeypatch, exception, code, status):
    app = make_app(tmp_path)
    archive = install_error_management(app, app.state.settings)
    client = TestClient(app, raise_server_exceptions=False)
    sid = upload(client, {'A.txt': b'#Intensity_unit=cps\n10 1\n20 8\n30 3\n'})
    doc = client.get(api.BASE + f'/api/reports/{sid}').json()['document']
    endpoint = api.BASE + f'/api/reports/{sid}/draft-preview'
    def fail(*args, **kwargs):
        raise exception('private-server-path-and-details')
    with monkeypatch.context() as patch:
        patch.setattr(posco_pptx, 'build_plan', fail)
        response = client.post(endpoint, json={'document': doc})
    assert response.status_code == status, response.text[:300]
    assert response.json()['code'] == code
    event_id = response.json()['errorEventId']
    assert response.headers['X-Error-Event-Id'] == event_id
    assert 'private-server-path-and-details' not in response.text
    trace = (archive.root / event_id / 'traceback.txt').read_text()
    assert exception.__name__ in trace
    assert 'private-server-path-and-details' in trace
    assert client.get(api.BASE + f'/api/reports/{sid}').json()['document'] == doc
    assert client.post(endpoint, json={'document': doc}).status_code == 200

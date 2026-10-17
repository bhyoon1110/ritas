import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from .fixture_data import example_project

from app.xrd_workbench.routes.reports import build_report_router
from app.xrd_workbench.storage import ReportStore

pytestmark = pytest.mark.real_data


@pytest.fixture
def client(tmp_path):
    app = FastAPI()
    project = example_project()

    def loader(sid):
        if sid != 'example':
            raise ValueError('project missing')
        return project

    app.include_router(build_report_router(loader, lambda sid, fid: b'', ReportStore(tmp_path)))
    return TestClient(app)


def project_names(project):
    return [s['name'] for s in project['samples']]


def test_save_reopen_and_conflict_use_canonical_project(client):
    value = client.get('/api/reports/example').json()
    assert value['project']['samples'][0]['name'] == project_names(value['project'])[0]
    doc = value['document']
    doc['metadata']['title'] = '의뢰자 보고서'
    response = client.put('/api/reports/example', json={'document': doc, 'expected_revision': 0})
    assert response.status_code == 200
    assert response.json()['revision'] == 1
    assert (
        client.get('/api/reports/example').json()['document']['metadata']['title']
        == '의뢰자 보고서'
    )
    assert (
        client.put(
            '/api/reports/example', json={'document': doc, 'expected_revision': 0}
        ).status_code
        == 409
    )


def test_generation_saves_rules_without_claiming_llm_and_keeps_manual_edit(client):
    doc = client.get('/api/reports/example').json()['document']
    doc['samples'][0]['notes'][0].update(text='수동 검토 문장', edited=True)
    saved = client.put(
        '/api/reports/example', json={'document': doc, 'expected_revision': 0}
    ).json()
    sid = doc['samples'][0]['sample_id']
    response = client.post(
        f'/api/reports/example/notes/{sid}', json={'expected_revision': saved['revision']}
    )
    assert response.status_code == 200
    result = response.json()['samples'][0]
    assert result['generation']['status'] == 'rules'
    assert result['notes'][0]['text'] == '수동 검토 문장'


def test_export_unsaved_document_is_rejected(client):
    assert client.get('/api/reports/example/pdf?revision=0').status_code == 409
    assert client.get('/api/reports/example/pptx?revision=0').status_code == 409


def test_saved_pptx_export_requires_the_saved_revision(tmp_path):
    import io
    import zipfile

    from .fixture_data import resolve_source

    project = example_project()
    app = FastAPI()
    app.include_router(
        build_report_router(lambda sid: project, resolve_source, ReportStore(tmp_path))
    )
    client = TestClient(app)
    doc = client.get('/api/reports/example').json()['document']
    doc['metadata']['title'] = 'PPTX 저장 검토본'
    saved = client.put(
        '/api/reports/example', json={'document': doc, 'expected_revision': 0}
    ).json()
    assert client.get('/api/reports/example/pptx?revision=0').status_code == 409
    response = client.get('/api/reports/example/pptx?revision=' + str(saved['revision']))
    assert response.status_code == 200
    assert (
        response.headers['content-type']
        == 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
    )
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        assert '분석 결과 요약' in z.read('ppt/slides/slide1.xml').decode('utf-8')
        assert any('PPTX 저장 검토본' in z.read(name).decode('utf-8')
                   for name in z.namelist() if name.startswith('ppt/slides/slide') and name.endswith('.xml'))

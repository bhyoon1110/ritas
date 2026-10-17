import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from .figure_fixtures import make_project, make_sample

from app.xrd_workbench.routes.reports import build_report_router
from app.xrd_workbench.storage import ReportStore


def quantity(phase, raw):
    return {'phase': phase, 'raw': raw, 'unit': 'wt%', 'origin': 'pdf', 'page': 1}


@pytest.fixture
def client(tmp_path):
    a = make_sample('a', 'a', [], [])
    a['quantities'] = [quantity('PhaseA', '12.34(5)')]
    a['reports'] = {'wppf_report': {'filename': 'w.pdf'}}
    b = make_sample('b', 'b', [], [])
    project = make_project([a, b])

    def loader(sid):
        if sid != 'example':
            raise ValueError('project missing')
        return project

    store = ReportStore(tmp_path)
    app = FastAPI()
    app.include_router(build_report_router(loader, lambda sid, fid: b'', store))
    test_client = TestClient(app)
    test_client.store = store
    return test_client


def saved_revision(client):
    doc = client.get('/api/reports/example').json()['document']
    return client.put(
        '/api/reports/example', json={'document': doc, 'expected_revision': 0}
    ).json()['revision']


def test_the_preview_returns_the_same_table_the_report_prints(client):
    revision = saved_revision(client)
    body = client.get(f'/api/reports/example/quantity-table?revision={revision}').json()
    assert [c['name'] for c in body['columns']] == ['PhaseA']
    assert [r['cells'][0]['text'] for r in body['rows']] == ['12.34(5) wt%', '정량 자료 없음']
    assert body['footnotes'] and body['empty_message'] == ''


def test_it_needs_the_saved_revision(client):
    saved_revision(client)
    assert client.get('/api/reports/example/quantity-table?revision=99').status_code == 409


def test_a_document_saved_before_the_table_opens_with_it_off(client):
    saved_revision(client)
    path = client.store.path('example')
    value = json.loads(path.read_text(encoding='utf-8'))
    del value['quantity_table']
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    opened = client.get('/api/reports/example').json()['document']
    assert opened['quantity_table'] == {'include': False}

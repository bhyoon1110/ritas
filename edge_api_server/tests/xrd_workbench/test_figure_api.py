import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from .figure_fixtures import make_project, make_sample

from app.xrd_workbench.routes.reports import build_report_router
from app.xrd_workbench.storage import ReportStore

A = ('PhaseA', '00-001', 'DB1')
B = ('PhaseB', '00-002', 'DB1')


@pytest.fixture
def store_and_client(tmp_path):
    project = make_project(
        [
            make_sample('a', 'a', [A], [(1, 21.0, ['PhaseA'])]),
            make_sample('b', 'b', [B], [(1, 22.0, ['PhaseB'])]),
            make_sample('c', 'c', [A], [(1, 23.0, ['PhaseA'])]),
        ]
    )

    def loader(sid):
        if sid != 'example':
            raise ValueError('project missing')
        return project

    store = ReportStore(tmp_path)
    app = FastAPI()
    app.include_router(build_report_router(loader, lambda sid, fid: b'', store))
    return store, TestClient(app)


def save(client):
    doc = client.get('/api/reports/example').json()['document']
    return client.put('/api/reports/example', json={'document': doc, 'expected_revision': 0}).json()


def test_similarity_lists_included_samples_in_similarity_order_with_identical_groups(
    store_and_client,
):
    _, client = store_and_client
    saved = save(client)
    body = client.get(f'/api/reports/example/figure/similarity?revision={saved["revision"]}').json()
    assert body['order'] == ['a', 'c', 'b']
    assert body['identical'] == [['a', 'c']]
    assert body['notes']['b'] == '+PhaseB, −PhaseA'


def test_similarity_needs_the_saved_revision(store_and_client):
    _, client = store_and_client
    save(client)
    assert client.get('/api/reports/example/figure/similarity?revision=99').status_code == 409


def test_a_document_saved_before_figures_opens_with_the_figure_off(store_and_client):
    store, client = store_and_client
    save(client)
    path = store.path('example')
    value = json.loads(path.read_text(encoding='utf-8'))
    del value['figure']
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    document = client.get('/api/reports/example').json()['document']
    assert document['figure']['include'] is False

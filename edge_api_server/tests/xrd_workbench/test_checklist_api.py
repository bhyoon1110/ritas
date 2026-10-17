import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from .figure_fixtures import make_project, make_sample

from app.xrd_workbench.routes.reports import build_report_router
from app.xrd_workbench.storage import ReportStore

A = ('PhaseA', 'c1', 'DB')


@pytest.fixture
def client(tmp_path):
    a = make_sample('a', 'a', [A], [(1, 21.0, ['PhaseA'])])
    a['reports'] = {'phase_report': {'filename': 'p.pdf'}}
    b = make_sample('b', 'b', [A], [(1, 22.0, ['PhaseA'])])
    b['reports'] = {}
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


def save(client, change=None):
    doc = client.get('/api/reports/example').json()['document']
    doc['figure']['include'] = False
    for entry in doc['samples']:
        entry['reviewed'] = True
    if change:
        change(doc)
    response = client.put('/api/reports/example', json={'document': doc, 'expected_revision': 0})
    assert response.status_code == 200, response.text
    return response.json()['revision']


def checklist(client, revision):
    return client.get(f'/api/reports/example/checklist?revision={revision}')


def test_the_checklist_lists_the_saved_reports_samples_and_items(client):
    body = checklist(client, save(client)).json()
    assert [s['sample_id'] for s in body['samples']] == ['a', 'b']
    codes = {s['sample_id']: [i['code'] for i in s['items']] for s in body['samples']}
    assert 'no_phase_report' in codes['b'] and 'no_phase_report' not in codes['a']
    assert body['summary']['samples'] == 2 and body['summary']['reviewed'] == 2


def test_it_needs_the_saved_revision(client):
    save(client)
    assert checklist(client, 99).status_code == 409


def test_it_can_be_read_while_a_sample_still_needs_review(client):
    def need_review(doc):
        doc['samples'][0]['reviewed'] = False
        doc['samples'][0]['analysis_review_required'] = True

    revision = save(client, need_review)
    response = checklist(client, revision)
    assert response.status_code == 200
    assert response.json()['summary']['blockers'] == 1


def test_a_blocker_means_the_export_is_refused_and_no_blocker_means_it_works(client):
    def need_review(doc):
        doc['samples'][0]['reviewed'] = False
        doc['samples'][0]['analysis_review_required'] = True

    revision = save(client, need_review)
    assert checklist(client, revision).json()['summary']['blockers'] >= 1
    assert client.get(f'/api/reports/example/pdf?revision={revision}').status_code == 409


def test_without_blockers_the_same_report_exports(tmp_path, client):
    revision = save(client)
    assert checklist(client, revision).json()['summary']['blockers'] == 0
    assert client.get(f'/api/reports/example/pdf?revision={revision}').status_code == 200


def test_a_document_saved_before_the_newer_blocks_still_has_a_checklist(client):
    revision = save(client)
    path = client.store.path('example')
    value = json.loads(path.read_text(encoding='utf-8'))
    for key in ('figure', 'quantity_table', 'phase_classes'):
        del value[key]
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    assert checklist(client, revision).status_code == 200

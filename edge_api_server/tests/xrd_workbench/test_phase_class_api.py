import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from .figure_fixtures import make_project, make_trace_sample

from app.xrd_workbench.routes.reports import build_report_router
from app.xrd_workbench.storage import ReportStore

PHASE = {'name': 'PhaseA', 'card': '00-001', 'db': 'DB1'}


@pytest.fixture
def client(tmp_path):
    weak = make_trace_sample('weak', heights=(20.0, 10.0, 1000.0))
    weak['phases'][0]['quantity'] = {'raw': '12.34(5)', 'unit': 'wt%'}
    strong = make_trace_sample('strong', heights=(900.0, 800.0, 1000.0))
    project = make_project([weak, strong])

    def loader(sid):
        if sid != 'example':
            raise ValueError('project missing')
        return project

    app = FastAPI()
    store = ReportStore(tmp_path)
    app.include_router(build_report_router(loader, lambda sid, fid: b'', store))
    test_client = TestClient(app)
    test_client.store = store
    return test_client


def saved_revision(client):
    doc = client.get('/api/reports/example').json()['document']
    return client.put(
        '/api/reports/example', json={'document': doc, 'expected_revision': 0}
    ).json()['revision']


def url(revision, percent=None):
    suffix = '' if percent is None else f'&percent={percent}'
    return f'/api/reports/example/phase-classes?revision={revision}{suffix}'


def test_without_a_percent_each_phase_lists_its_quantity_and_no_verdict(client):
    body = client.get(url(saved_revision(client))).json()
    weak = next(s for s in body['samples'] if s['sample_id'] == 'weak')
    assert weak['phases'] == [{'phase': PHASE, 'quantity': '12.34(5) wt%', 'trace': None}]
    strong = next(s for s in body['samples'] if s['sample_id'] == 'strong')
    assert strong['phases'][0]['quantity'] is None


def test_with_a_percent_each_phase_carries_the_verdict_and_peak_ratios(client):
    body = client.get(url(saved_revision(client), 2)).json()
    verdicts = {s['sample_id']: s['phases'][0]['trace'] for s in body['samples']}
    assert verdicts['weak']['status'] == 'trace'
    assert [p['ratio'] for p in verdicts['weak']['peaks']] == [2.0, 1.0]
    assert verdicts['strong']['status'] == 'not_trace'


@pytest.mark.parametrize('percent', ['-1', '101', 'x', 'nan'])
def test_an_invalid_percent_is_a_422(client, percent):
    assert client.get(url(saved_revision(client), percent)).status_code == 422


def test_it_needs_the_saved_revision(client):
    saved_revision(client)
    assert client.get(url(99)).status_code == 409


def test_a_document_saved_before_classes_opens_unclassified(client):
    import json

    revision = saved_revision(client)
    path = client.store.path('example')
    value = json.loads(path.read_text(encoding='utf-8'))
    del value['phase_classes']
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    opened = client.get('/api/reports/example').json()['document']
    assert opened['phase_classes'] == {
        'criteria_text': '',
        'rule': None,
        'labels': [],
        'retained': [],
    }
    assert client.get(url(revision)).status_code == 200

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.xrd_workbench.routes.presets import build_preset_router
from app.xrd_workbench.storage import PresetStore

SETTINGS = {
    'figure': {'spacing': 9, 'normalize': True},
    'quantity_table': {'include': False},
    'phase_classes': {'criteria_text': '기준', 'trace_percent': 2},
}


@pytest.fixture
def client(tmp_path):
    app = FastAPI()
    app.include_router(build_preset_router(PresetStore(tmp_path)))
    return TestClient(app)


def test_create_list_and_delete(client):
    created = client.post('/api/presets', json={'name': '내 설정', 'settings': SETTINGS})
    assert created.status_code == 200
    body = created.json()
    assert body['name'] == '내 설정' and body['settings']['figure']['spacing'] == 9
    assert body['settings']['figure']['normalize'] is False  # normalization was removed
    listing = client.get('/api/presets').json()
    assert [p['id'] for p in listing] == [body['id']]
    assert client.delete(f"/api/presets/{body['id']}").status_code == 200
    assert client.get('/api/presets').json() == []
    assert client.delete(f"/api/presets/{body['id']}").status_code == 404


def test_the_same_name_is_a_409_until_the_client_says_overwrite(client):
    client.post('/api/presets', json={'name': '이름', 'settings': SETTINGS})
    clash = client.post('/api/presets', json={'name': '이름', 'settings': SETTINGS})
    assert clash.status_code == 409 and '같은 이름' in clash.json()['detail']
    ok = client.post('/api/presets', json={'name': '이름', 'settings': SETTINGS, 'overwrite': True})
    assert ok.status_code == 200 and len(client.get('/api/presets').json()) == 1


@pytest.mark.parametrize(
    'payload',
    [
        {'name': '', 'settings': SETTINGS},
        {'name': 'x' * 81, 'settings': SETTINGS},
        {'name': '이름', 'settings': {'figure': {'order_mode': 'random'}}},
        {'name': '이름', 'settings': {'figure': {'groups': [{'title': 'g', 'samples': ['a']}]}}},
        {'name': '이름', 'settings': {'phase_classes': {'trace_percent': 500}}},
        {'name': '이름', 'settings': 'bad'},
        {'settings': SETTINGS},
        {'name': '이름', 'settings': SETTINGS, 'overwrite': 'yes'},
    ],
)
def test_invalid_requests_are_422_and_save_nothing(client, payload):
    assert client.post('/api/presets', json=payload).status_code == 422
    assert client.get('/api/presets').json() == []


def test_a_body_that_is_not_json_or_not_an_object_is_422(client):
    assert client.post('/api/presets', content=b'nope').status_code == 422
    assert client.post('/api/presets', json=[1, 2]).status_code == 422


def test_a_bad_id_is_a_404_not_a_server_error(client):
    assert client.delete('/api/presets/not-an-id').status_code == 404

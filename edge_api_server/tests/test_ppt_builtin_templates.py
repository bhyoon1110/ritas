"""Native defaults are metadata-only, authorized and independent of private books."""
from copy import deepcopy
import json

import pytest

from app.ppt_templates import BUILTIN_XRD_ID, BUILTIN_XRD
from tests.test_mail_address_book import client
from tests.test_ppt_templates import URL, HEADERS, create, value


@pytest.mark.parametrize('user', ['one', 'two', 'xrd', 'admin'])
def test_builtin_is_visible_without_importing_or_seeding_private_storage(client, tmp_path, monkeypatch, user):
    from app.report import ppt_template_import
    def forbidden(*args):
        raise AssertionError('native defaults must not inspect/import sample decks')
    monkeypatch.setattr(ppt_template_import, 'prepare', forbidden)
    headers = {**HEADERS, 'x-test-user': user}
    response = client.get(URL, headers=headers)
    assert response.status_code == 200
    assert response.json() == {'revision': 0, 'items': [dict(
        id=BUILTIN_XRD_ID, name=BUILTIN_XRD['content']['name'], experiment='XRD',
        builtin=True, default=True, readOnly=True)]}
    read = client.get(URL+'/'+BUILTIN_XRD_ID, headers=headers)
    assert read.json() == dict(BUILTIN_XRD, revision=0)
    assert 'no-store' in read.headers['cache-control']
    assert not list(tmp_path.rglob('*.json'))
    assert not list(tmp_path.rglob('*.pptx'))
    for tail in ('/source', '/slides/0/preview', '/slides/0/background'):
        assert client.get(URL+'/'+BUILTIN_XRD_ID+tail, headers=headers).status_code == 404


@pytest.mark.parametrize('user', ['ftir', 'raman', 'tem', 'no-project'])
def test_builtin_requires_xrd_permission_even_with_guessed_id(client, user):
    headers = {**HEADERS, 'x-test-user': user}
    assert client.get(URL, headers=headers).json()['items'] == []
    assert client.get(URL+'/'+BUILTIN_XRD_ID, headers=headers).status_code == 404
    assert client.request('DELETE', URL+'/'+BUILTIN_XRD_ID, json={'revision': 0}, headers=headers).status_code == 404


def test_builtin_preserves_private_capacity_names_revisions_and_existing_files(client, tmp_path, monkeypatch):
    from app import ppt_templates
    monkeypatch.setattr(ppt_templates, 'MAX_TEMPLATES', 1)
    private = value()
    private.update(name=BUILTIN_XRD['content']['name'], experiment='XRD')
    response = create(client, data=private)
    assert response.status_code == 201
    selected = response.json()['selectedId']
    assert selected != BUILTIN_XRD_ID and len(response.json()['items']) == 2
    path = next((tmp_path/'user-ppt-templates').glob('*.json'))
    before = path.read_bytes()
    for _ in range(2):
        assert client.get(URL, headers=HEADERS).json()['revision'] == 1
        assert client.get(URL+'/'+BUILTIN_XRD_ID, headers=HEADERS).json()['revision'] == 1
    assert path.read_bytes() == before
    assert json.loads(before)['items'] == [{'id': selected, 'content': private}]
    extra = deepcopy(private); extra['name'] = '다른 개인 양식'
    assert create(client, revision=1, data=extra).status_code == 422
    for method, body in [('PUT', {'revision': 1, 'content': private}), ('DELETE', {'revision': 1})]:
        blocked = client.request(method, URL+'/'+BUILTIN_XRD_ID, json=body, headers=HEADERS)
        assert blocked.status_code == 403 and blocked.json()['code'] == 'PPT_TEMPLATE_READ_ONLY'
    assert create(client, revision=1, data=BUILTIN_XRD['content']).status_code == 422
    assert path.read_bytes() == before
    deleted = client.request('DELETE', URL+'/'+selected, json={'revision': 1}, headers=HEADERS)
    assert deleted.status_code == 200
    assert [i['id'] for i in deleted.json()['items']] == [BUILTIN_XRD_ID]
    assert deleted.json()['revision'] == 2


@pytest.mark.parametrize('headers, code', [({}, 401), ({**HEADERS, 'x-test-user': 'pending'}, 403)])
def test_builtin_requires_active_authentication(client, headers, code):
    assert client.get(URL+'/'+BUILTIN_XRD_ID, headers=headers).status_code == code

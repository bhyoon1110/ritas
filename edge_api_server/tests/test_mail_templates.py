from concurrent.futures import ThreadPoolExecutor
import json

from tests.test_mail_address_book import client, HEADERS
from tests.test_report_mail import embedded_image
from app import mail_templates

URL = '/api/v1/report-mail/templates'


def create(client, revision=0, **changes):
    value = dict(name='기본 결과 안내', experiment='ALL', subject='{{실험종류}} · {{의뢰번호}}',
                 body='ignored stale plaintext', bodyHtml='<p>{{실험장비}}</p><p>{{분석결과}}</p>')
    value.update(changes)
    return client.post(URL, json=dict(revision=revision, content=value), headers=HEADERS)


def test_private_template_crud_metadata_and_sanitized_rich_body(client, tmp_path):
    image = embedded_image()
    html = '<p onclick="bad()"><b>{{실험장비}}</b></p><table><tr><td>{{공통.온도}}</td></tr></table><img src="'+image+'" width="120"><script>bad()</script>'
    response = create(client, bodyHtml=html)
    assert response.status_code == 201, response.text
    book = response.json(); item = book['items'][0]
    assert 'bodyHtml' not in response.text and 'data:image' not in response.text
    value = client.get(URL+'/'+item['id'], headers=HEADERS).json()['content']
    assert 'script' not in value['bodyHtml'] and 'onclick' not in value['bodyHtml']
    assert '<table' in value['bodyHtml'] and image in value['bodyHtml']
    assert '{{실험장비}}' in value['body'] and 'ignored' not in value['body']
    assert client.get(URL, headers={**HEADERS, 'x-test-user':'two'}).json()['items'] == []
    assert client.get(URL+'/'+item['id'], headers={**HEADERS, 'x-test-user':'two'}).status_code == 404
    value.update(name='수정 템플릿', experiment='FTIR')
    response = client.put(URL+'/'+item['id'], json=dict(revision=1, content=value), headers=HEADERS)
    assert response.status_code == 200
    assert response.json()['items'][0]['name'] == '수정 템플릿'
    assert client.request('DELETE', URL+'/'+item['id'], json={'revision':2}, headers=HEADERS).json()['items'] == []
    assert all(p.stat().st_mode & 0o077 == 0 for p in (tmp_path/'user-mail-templates').iterdir())
    assert not (tmp_path/'user-mail-preferences').exists()


def test_auth_origin_and_bounded_template_fields(client, monkeypatch):
    assert client.get(URL).status_code == 401
    assert client.get(URL, headers={**HEADERS,'x-test-user':'pending'}).status_code == 403
    assert client.post(URL, json={}, headers={**HEADERS,'Origin':'https://bad.invalid'}).status_code == 403
    for changes in [dict(name=''), dict(subject='x\nBcc:other'), dict(body='x'*4001), dict(experiment='OTHER'), dict(to='outside@evil.invalid'), dict(bodyHtml='<p>'+'x'*4001+'</p>'), dict(bodyHtml='<img src="data:image/png;base64,bad">')]:
        assert create(client, **changes).status_code == 422, changes
    assert create(client, revision=True).status_code == 422
    assert client.post(URL, content=b'x'*(2*1024*1024+1), headers=HEADERS).status_code == 413
    first = create(client).json()['items'][0]
    assert create(client, 1).status_code == 409
    assert client.put(URL+'/'+first['id'], json={'revision':0,'content':{}}, headers=HEADERS).status_code == 422
    monkeypatch.setattr(mail_templates, 'MAX_TEMPLATES', 1)
    assert create(client, 1, name='다른 템플릿').status_code == 422


def test_concurrent_and_corrupt_templates_never_overwrite(client, tmp_path):
    create(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda i: create(client, 1, name=f'템플릿{i}'), range(2)))
    assert sorted(r.status_code for r in results) == [201,409]
    path = next((tmp_path/'user-mail-templates').glob('*.json'))
    assert len(json.loads(path.read_text())['items']) == 2
    path.write_text('{broken')
    assert client.get(URL, headers=HEADERS).status_code == 500
    assert create(client, 2, name='이후 저장').status_code == 500
    assert path.read_text() == '{broken'

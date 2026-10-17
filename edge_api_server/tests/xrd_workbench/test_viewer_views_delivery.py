"""Named views travel with the delivery viewer; nothing else about the export changes."""

import io
import json
import zipfile
from html.parser import HTMLParser

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from .figure_fixtures import make_document, make_project, make_sample

from app.xrd_workbench.render.package import build_package
from app.xrd_workbench.render.viewer_export import export_html
from app.xrd_workbench.render.viewer_state import prepare_state
from app.xrd_workbench.routes.reports import build_report_router
from app.xrd_workbench.storage import ReportStore

A = ('PhaseA', 'c1', 'DB')
B = ('PhaseB', 'c2', 'DB')


class DataBlock(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found = None
        self._capture = False

    def handle_starttag(self, tag, attrs):
        self._capture = tag == 'script' and dict(attrs).get('id') == 'xrd-data'

    def handle_data(self, data):
        if self._capture:
            self.found = (self.found or '') + data

    def handle_endtag(self, tag):
        self._capture = False


def embedded(html):
    parser = DataBlock()
    parser.feed(html)
    return json.loads(parser.found)


def project():
    return make_project(
        [
            make_sample('a', 'a', [A], [(1, 21.0, ['PhaseA'])]),
            make_sample('b', 'b', [B], [(1, 22.0, ['PhaseB'])]),
        ]
    )


def view(name, **extra):
    return {
        'name': name,
        'state': {'version': 1, 'projectId': 'fig-project', 'markers': True, **extra},
    }


def test_without_views_the_exported_viewer_is_exactly_what_it_was():
    p = project()
    plain = export_html(p, {})
    assert 'views' not in embedded(plain)
    assert export_html(p, {}, None) == plain and export_html(p, {}, []) == plain


def test_views_are_embedded_and_change_the_snapshot_identity():
    p = project()
    views = [view('A계열', visible=['a'])]
    with_views = embedded(export_html(p, {}, views))
    assert with_views['views'] == views
    assert with_views['snapshotId'] != embedded(export_html(p, {}))['snapshotId']
    other = embedded(export_html(p, {}, [view('다른 이름')]))
    assert other['snapshotId'] != with_views['snapshotId']


def test_origin_style_and_marker_size_travel_in_saved_viewer():
    p = project()
    state = prepare_state(p, {'version':1, 'projectId':p['id'], 'originStyle':True, 'markerSize':17})
    html = export_html(p,state)
    assert embedded(html)['state']['originStyle'] is True
    assert embedded(html)['state']['markerSize'] == 17
    assert 'Origin 스타일로 저장' not in html
    assert 'Origin 스타일로 보기' in html


def make_package(views, included=('a', 'b')):
    p = project()
    document = make_document(p, include=False)
    for entry in document['samples']:
        entry['included'] = entry['sample_id'] in included
    data = build_package(p, document, lambda fid: b'', None, views)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return embedded(z.read('뷰어.html').decode('utf-8'))


def test_the_package_embeds_views_for_the_delivered_project_only():
    payload = make_package(
        [
            view('둘 다', visible=['a', 'b']),
            view('B 강조', highlight={'name': 'PhaseB', 'card': None, 'db': None}),
        ],
        included=('a',),
    )
    delivered_id = payload['project']['id']
    assert [v['name'] for v in payload['views']] == ['둘 다', 'B 강조']
    assert all(v['state']['projectId'] == delivered_id for v in payload['views'])
    assert payload['views'][0]['state']['visible'] == ['a']
    # PhaseB is not in the delivered report: its highlight is dropped, the delivery is not refused.
    assert payload['views'][1]['state']['highlight'] is None


@pytest.mark.parametrize(
    'bad',
    [
        'x',
        [view('같음'), view('같음')],
        [view(f'v{i}') for i in range(21)],
        [{'name': '', 'state': {'version': 1, 'projectId': 'fig-project', 'markers': True}}],
        [{'name': 'x', 'state': {'version': 1, 'projectId': 'other', 'markers': True}}],
        [{'name': 'x', 'state': {'version': 1, 'projectId': 'fig-project', 'visible': ['zzz']}}],
    ],
)
def test_invalid_views_refuse_the_package(bad):
    with pytest.raises(ValueError):
        make_package(bad)


@pytest.fixture
def client(tmp_path):
    p = project()

    def loader(sid):
        if sid != 'example':
            raise ValueError('project missing')
        return p

    store = ReportStore(tmp_path)
    app = FastAPI()
    app.include_router(build_report_router(loader, lambda sid, fid: b'', store))
    return TestClient(app)


def saved_revision(client):
    doc = client.get('/api/reports/example').json()['document']
    doc['figure']['include'] = False
    return client.put(
        '/api/reports/example', json={'document': doc, 'expected_revision': 0}
    ).json()['revision']


def post_zip(client, revision, body):
    return client.post(
        f'/api/reports/example/zip?revision={revision}', json={'analysis_revision': 0, **body}
    )


def test_the_zip_route_passes_views_to_the_package_and_refuses_bad_ones_with_422(client):
    revision = saved_revision(client)
    ok = post_zip(client, revision, {'views': [view('내 보기')]})
    assert ok.status_code == 200
    with zipfile.ZipFile(io.BytesIO(ok.content)) as z:
        names = [v['name'] for v in embedded(z.read('뷰어.html').decode('utf-8'))['views']]
    assert names == ['내 보기']
    bad = post_zip(client, revision, {'views': [view('같음'), view('같음')]})
    assert bad.status_code == 422 and '이름' in bad.json()['detail']
    assert post_zip(client, revision, {'views': 'x'}).status_code == 422


def test_the_offline_viewer_carries_no_app_shell():
    html = export_html(project(), {})
    assert '/shared/shell' not in html and 'data-step=' not in html
    # The next-step button stays (the script refers to it) but is hidden and never shown offline.
    assert '<button id="nextStep" class="nextStep" hidden>' in html


def test_every_element_the_viewer_script_uses_exists_in_the_offline_viewer():
    import re as regex

    from app.xrd_workbench.config import ROOT

    source = (ROOT / 'web' / 'viewer' / 'viewer.mjs').read_text(encoding='utf-8')
    used = set(regex.findall(r"\$\('([A-Za-z]+)'\)", source))
    html = export_html(project(), {})
    missing = sorted(i for i in used if f'id="{i}"' not in html)
    assert missing == []

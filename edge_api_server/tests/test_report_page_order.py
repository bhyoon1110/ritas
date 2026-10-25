from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile

from PIL import Image
import pytest

from app import report_editor
from app.report import pages, renderers
from app.report.model import ReportDocument
from tests.test_report_editor import prepared, edit_request, wait_revision


def test_order_only_revision_updates_ppt_pdf_and_preserves_original(prepared):
    h = prepared
    original = h.job.package_path.read_bytes()
    before = h.client.get(h.url + '/edit-layout').json()['pages']
    expected = [p['id'] for p in reversed(before)]
    edits = edit_request(h.client.get(h.url + '/editor-data').json()['document'])
    edits['pageEdits'] = {'hidden': [], 'added': [], 'order': expected}
    response = h.client.post(h.url + '/revisions', json=edits, headers={'X-Requested-With': 'RIST-Report-Editor'})
    assert response.status_code == 202, response.text
    job = wait_revision(h, response.json()['jobId'])
    assert job['status'] == 'completed', job
    url = h.prefix + '/' + job['jobId']
    after = h.client.get(url + '/edit-layout').json()['pages']
    assert [p['id'] for p in after] == expected
    assert [p['title'] for p in after] == [p['title'] for p in reversed(before)]
    saved = h.client.get(url + '/editor-data').json()['document']
    assert saved['pageEdits']['order'] == expected
    assert h.job.package_path.read_bytes() == original
    with ZipFile(BytesIO(h.client.get(job['downloadUrl']).content)) as package:
        assert package.read('report.pdf') == package.read('report-from-pptx.pdf')
        assert package.read('raw_data.xlsx') == ZipFile(BytesIO(original)).read('raw_data.xlsx')
    old_client = edit_request(saved)
    old_client['pageEdits'].pop('order')
    values = report_editor._validate_changes(report_editor.ReportEdit(**old_client), report_editor._load_document(h.store.get(job['jobId'])))
    assert values['pageEdits']['order'] == expected


def test_order_validation_and_authenticated_small_thumbnails(prepared):
    h = prepared
    ids = [p['id'] for p in h.client.get(h.url + '/edit-layout').json()['pages']]
    edits = edit_request(h.client.get(h.url + '/editor-data').json()['document'])
    for order in [[ids[0], ids[0]], ['unknown-page'], ['../outside']]:
        edits['pageEdits'] = {'hidden': [], 'added': [], 'order': order}
        response = h.client.post(h.url + '/revisions', json=edits, headers={'X-Requested-With': 'RIST-Report-Editor'})
        assert response.status_code == 422, response.text
    for suffix, width in [('?thumbnail=true', 240), ('', 1280)]:
        response = h.client.get(h.url + '/edit-slides/0' + suffix)
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'private, no-cache'
        with Image.open(BytesIO(response.content)) as image:
            assert image.width == width
        cached = h.client.get(h.url + '/edit-slides/0' + suffix,
                              headers={'If-None-Match': response.headers['etag']})
        assert cached.status_code == 304 and cached.content == b''
        assert cached.headers['cache-control'] == 'private, no-cache'
    assert h.client.get(h.url + '/edit-slides/200?thumbnail=true').status_code == 404
    assert h.client.get(h.prefix + '/missing/edit-slides/0?thumbnail=true').status_code == 404


def test_order_keeps_hidden_added_pages_and_handles_new_pagination(monkeypatch):
    doc = ReportDocument('order', '순서 검증', 'FT-IR', {}, '2026-10-25')
    def base(count=2):
        return [{'editing': {'kind': 'title'}}] + [{'editing': {'kind': 'text', 'sectionId': 'summary'}} for _ in range(count)]
    original = pages.identify(base())
    cover, first, second = [p['pageId'] for p in original]
    doc.page_edits = {'order': [second, cover, 'added-note', first], 'hidden': [cover],
                      'added': [{'id': 'added-note', 'after': cover, 'title': '검토', 'body': '원자료 유지'}]}
    monkeypatch.setattr(renderers, '_pptx_base_slide_payloads', lambda _: base())
    pages.validate(doc, doc)
    assert [p['pageId'] for p in pages.compose(doc, base())] == [second, 'added-note', first]
    previous = deepcopy(doc)
    doc.page_edits['hidden'] = []
    assert [p['pageId'] for p in pages.compose(doc, base())] == [second, cover, 'added-note', first]
    grown = pages.compose(doc, base(3))
    assert [p['pageId'] for p in grown] == [second, pages.identify(base(3))[-1]['pageId'], cover, 'added-note', first]
    # A temporarily absent continuation does not erase the author's saved position.
    monkeypatch.setattr(renderers, '_pptx_base_slide_payloads', lambda _: base(1))
    pages.validate(doc, previous)
    assert [p['pageId'] for p in pages.compose(doc, base(1))] == [cover, 'added-note', first]
    assert doc.page_edits['order'][0] == second
    with pytest.raises(ValueError, match='중복'):
        doc.page_edits['order'] = [cover, cover]
        pages.validate(doc, previous)

from copy import deepcopy

from fastapi.testclient import TestClient

from app.xrd_workbench import integration as api
from app.xrd_workbench.render.html import render_html
from .test_integration import make_app, upload


def draft_fixture(tmp_path, auth=False):
    client = TestClient(make_app(tmp_path, auth=auth), headers={'test-user': 'one'} if auth else {})
    sid = upload(client, {'A.txt': b'#Intensity_unit=cps\n10 1\n20 8\n30 3\n'})
    doc = client.get(api.BASE + f'/api/reports/{sid}').json()['document']
    return client, sid, doc


def test_draft_preview_is_ephemeral_exact_and_not_a_report_send(tmp_path):
    client, sid, doc = draft_fixture(tmp_path)
    doc['metadata']['title'] = '초안 제목 <script>alert(1)</script>'
    doc['metadata']['report_number'] = 'QA-DRAFT'
    doc['summary'] = '검토 중 요약'
    before = sorted(str(p) for p in tmp_path.rglob('job.json'))
    response = client.post(api.BASE + f'/api/reports/{sid}/draft-preview', json={'document': doc})
    assert response.status_code == 200, response.text
    assert 'private, no-store' in response.headers['cache-control']
    assert 'data-edit=' in response.text and 'xrd-preview-edit' in response.text
    assert '검토 중 요약' not in response.text  # only phase classifications determine the notice
    assert 'QA-DRAFT' in response.text  # request metadata now appears on the POSCO cover
    assert doc['metadata']['report_number'] == 'QA-DRAFT'
    assert '<script>alert(1)</script>' not in response.text
    assert 'contenteditable' not in response.text
    current = client.get(api.BASE + f'/api/reports/{sid}').json()['document']
    assert current['revision'] == 0 and current['metadata']['title'] != doc['metadata']['title']
    assert sorted(str(p) for p in tmp_path.rglob('job.json')) == before
    assert not list(tmp_path.rglob('report.html'))


def test_preview_rejects_foreign_stale_invalid_and_busy_drafts(tmp_path):
    client, sid, doc = draft_fixture(tmp_path, auth=True)
    endpoint = api.BASE + f'/api/reports/{sid}/draft-preview'
    assert client.post(endpoint, json={'document': {}}).status_code == 409
    invalid = deepcopy(doc); invalid['metadata']['title'] = ''
    assert client.post(endpoint, json={'document': invalid}).status_code == 422
    wrong_analysis = deepcopy(doc); wrong_analysis['analysis_revision'] = 100
    assert client.post(endpoint, json={'document': wrong_analysis}).status_code == 409
    assert client.put(api.BASE + f'/api/reports/{sid}', json={'document':doc,'expected_revision':0}).status_code == 200
    assert client.post(endpoint, json={'document': doc}).status_code == 409
    client.headers['test-user'] = 'two'
    assert client.post(endpoint, json={'document': doc}).status_code == 404
    client.headers['test-project'] = 'FTIR'
    assert client.post(endpoint, json={'document': doc}).status_code == 403
    client.headers['test-project'] = 'XRD'
    api.draft_preview_slot.acquire()
    try:
        assert client.post(endpoint, json={'document': doc}).status_code == 429
    finally:
        api.draft_preview_slot.release()


def test_delivered_html_never_has_editor_links():
    page = {'kind':'cover', 'title':'Cover', 'edit':{'tab':'overview'}, 'items':[
        {'type':'text','x':1,'y':2,'w':3,'h':4,'size':12,'color':'#000','bold':False,'leading':4,
         'lines':['test'], 'edit':{'tab':'overview','field':'title'}}]}
    document = {'metadata':{'title':'test'}}
    for mode in ('package','app'):
        html = render_html([page],document,mode,'id')
        assert 'data-edit=' not in html and 'xrd-preview-edit' not in html
    assert 'data-edit=' not in render_html([page],document,'package',edit_links=True)


def test_links_identify_exact_notes_and_captions_even_with_same_wording():
    from .fixture_data import example_project
    from .test_report_export import sources
    from app.xrd_workbench.core.report import new_document
    from app.xrd_workbench.render.layout import build_pages
    from app.xrd_workbench.render.sample_page import remark_targets
    project = example_project()
    doc = new_document(project)
    entry = next(s for s in doc['samples'] if len(s['notes']) > 1)
    entry['manual_note'] = '직접 작성한 문장'
    entry['phase_caption'] = entry['quantity_caption'] = '같은 캡션'
    for summary in (True, False):
        entry['summary_included'] = summary
        pages = build_pages(project, doc, sources(project))
        links = [i['edit'] for p in pages for i in p['items'] if i.get('edit')]
        assert all(target in links for target in remark_targets(entry))
        assert {'tab':entry['sample_id'],'field':'phase_caption'} in links
        assert {'tab':entry['sample_id'],'field':'quantity_caption'} in links

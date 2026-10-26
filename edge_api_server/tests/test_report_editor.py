from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import base64
from io import BytesIO
import json
import time
from types import SimpleNamespace
import xml.etree.ElementTree as ET
import zipfile

from fastapi.testclient import TestClient
import pytest

from app import preview_report, report_editor
from app.auth import AuthContext
from app.ftir_web import create_ftir_preview_app
from app.raman_web import create_raman_preview_app
from app.report.editable import ensure_editable_fields, transfer_values, document_from_dict
from app.report.model import ReportDocument, ReportSection, ReportTable
from app.report.renderers import render_pptx, _document_metadata, _report_readable_text
from app.report.edited_analysis import analysis_preview
from edge_api_server.tests.test_edited_report_analysis import edited_payload
from edge_api_server.tests.test_ftir_web import TINY_PNG_DATA_URL, use_fake_pptx_pdf_converter


def edit_request(document):
    value = deepcopy(document)
    for section in value['sections']:
        section.pop('columns')
        section.pop('source')
    return value


@pytest.fixture(params=['ftir', 'raman'])
def prepared(request, tmp_path, monkeypatch):
    use_fake_pptx_pdf_converter(monkeypatch)
    from app.report import spectrum_viewer
    monkeypatch.setattr(spectrum_viewer, 'get_plotlyjs', lambda: '')
    kind = request.param
    app = create_ftir_preview_app(tmp_path / 'libraries') if kind == 'ftir' else create_raman_preview_app()
    app.state.settings = None
    @app.middleware('http')
    async def identity(request, call_next):
        request.state.auth_context=getattr(app.state,'actor',None)
        return await call_next(request)
    payload = edited_payload()
    payload['reportConditionFields'] = [{'target':'공통', 'label':'검토 메모', 'value':''}]
    root, package = preview_report.build_preview_report_package(
        experiment_code='FT-IR' if kind == 'ftir' else 'RAMAN', analysis_payload=payload,
        raw_series=[preview_report.RawSeries('sample.dpt',[1500,1700,1900],[1,3,1])],
        figure_image=base64.b64decode(TINY_PNG_DATA_URL.split(',')[1]))
    store = preview_report.preview_report_job_store(app)
    job = store.create(filename=kind + '-report-package.zip', owner_user_id='owner')
    job.transfer_metadata = transfer_values(payload)
    store.complete(job.job_id, tmp_root=root, package_path=package)
    # Simulate the output of a successful LLM call, without contacting a real model.
    path = root / 'job/report/report.json'
    document = json.loads(path.read_text())
    for section in document['sections']:
        if section['sectionId'] == 'summary':
            section.update(paragraphs=['모의 AI 초안 · 검토 전'], source='llm')
    path.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
    def unexpected_llm(*args, **kwargs):
        raise AssertionError('Revision must not call LLM')
    monkeypatch.setattr(preview_report, 'LocalLlmClient', unexpected_llm)
    prefix = '/api/v1/' + kind + '/report/jobs'
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, app=app, store=store, job=job, prefix=prefix, kind=kind,
                              url=prefix + '/' + job.job_id)
    for job_id in list(store._jobs):
        store.remove(job_id)


def wait_revision(h, job_id):
    for _ in range(300):
        job = h.client.get(h.prefix + '/' + job_id).json()
        if job['status'] in {'completed', 'failed'}:
            return job
        time.sleep(.02)
    pytest.fail('revision timed out')


def test_page_add_delete_restore_revision_package_and_original_integrity(prepared, monkeypatch):
    h = prepared
    original = h.job.package_path.read_bytes()
    layout = h.client.get(h.url + '/edit-layout').json()
    assert layout['pageEditing'] is True
    pages = layout['pages']
    assert len({p['id'] for p in pages}) == len(pages)
    removed = pages[1]['id']
    edits = edit_request(h.client.get(h.url + '/editor-data').json()['document'])
    edits['variables'] = [{'name': '검토자', 'value': '분석 담당자', 'unit': ''}]
    edits['pageEdits'] = {'hidden': [removed], 'added': [
        {'id': 'added-review', 'after': removed, 'title': '{{검토자}} 의견',
         'body': '수정본 검토 내용\n<script>문자가 그대로 표시됩니다.</script>'}]}
    registered = []
    formats = []
    original_render = preview_report.render_report_formats
    def render(document, directory, requested, **kwargs):
        formats.append(requested)
        return original_render(document, directory, requested, **kwargs)
    monkeypatch.setattr(preview_report, 'render_report_formats', render)
    monkeypatch.setattr(report_editor, 'register_generated_report_package', lambda **kw: registered.append(kw) or kw['package_path'])
    response = h.client.post(h.url + '/revisions', json=edits, headers={'X-Requested-With': 'RIST-Report-Editor'})
    assert response.status_code == 202, response.text
    result = wait_revision(h, response.json()['jobId'])
    assert result['status'] == 'completed', result
    assert formats == [['PPTX', 'HTML']]  # The prose PDF would immediately be overwritten.
    url = h.prefix + '/' + result['jobId']
    updated = h.client.get(url + '/edit-layout').json()['pages']
    assert len(updated) == len(pages)
    assert updated[1]['id'] == 'added-review' and updated[1]['title'] == '분석 담당자 의견'
    assert removed not in [p['id'] for p in updated]
    assert any('page/added-review/body' in r['refs'] for r in updated[1]['regions'])
    saved = h.client.get(url + '/editor-data').json()['document']
    assert saved['pageEdits'] == edits['pageEdits']  # Preserve variable bindings, not expanded prose.
    assert registered[0]['report_id'] == result['jobId']
    assert h.job.package_path.read_bytes() == original
    with zipfile.ZipFile(BytesIO(h.client.get(result['downloadUrl']).content)) as package:
        assert package.read('report.pdf') == package.read('report-from-pptx.pdf')
        with zipfile.ZipFile(BytesIO(package.read('report.pptx'))) as ppt:
            assert len([n for n in ppt.namelist() if n.startswith('ppt/slides/slide') and n.endswith('.xml')]) == len(updated)
            assert '분석 담당자 의견' in ppt_text(ppt)
            assert '&lt;script&gt;' in ppt.read('ppt/slides/slide2.xml').decode()
        assert package.read('raw_data.xlsx') == zipfile.ZipFile(BytesIO(original)).read('raw_data.xlsx')
    # Older clients omitting the new field must retain the author's page list.
    omitted = edit_request(saved)
    omitted.pop('pageEdits')
    values = report_editor._validate_changes(report_editor.ReportEdit(**omitted), report_editor._load_document(h.store.get(result['jobId'])))
    assert values['pageEdits']['hidden'] == [removed]
    restored = edit_request(saved)
    restored['pageEdits']['hidden'] = []
    response = h.client.post(url + '/revisions', json=restored, headers={'X-Requested-With': 'RIST-Report-Editor'})
    assert response.status_code == 202, response.text
    result = wait_revision(h, response.json()['jobId'])
    assert result['status'] == 'completed', result
    final = h.client.get(h.prefix + '/' + result['jobId'] + '/edit-layout').json()['pages']
    assert len(final) == len(pages) + 1 and final[1]['id'] == removed and final[2]['id'] == 'added-review'


def test_page_validation_prevents_empty_deck_unknown_ids_cycles_and_overflow(prepared):
    h = prepared
    document = report_editor._load_document(h.job)
    edits = edit_request(h.client.get(h.url + '/editor-data').json()['document'])
    pages = h.client.get(h.url + '/edit-layout').json()['pages']
    added = {'id': 'added-test', 'after': pages[0]['id'], 'title': '제목', 'body': '본문'}
    for invalid in [
        {'hidden': [p['id'] for p in pages], 'added': []},
        {'hidden': ['unknown'], 'added': []},
        {'hidden': [pages[0]['id']] * 2, 'added': []},
        {'hidden': [], 'added': [dict(added, after='added-test')]},
        {'hidden': [], 'added': [added, added]},
        {'hidden': [], 'added': [dict(added, body='\n' * 30)]},
    ]:
        edits['pageEdits'] = invalid
        with pytest.raises(ValueError):
            report_editor._validate_changes(report_editor.ReportEdit(**edits), document)
    edits['pageEdits'] = {'hidden': [p['id'] for p in pages], 'added': [added]}
    values = report_editor._validate_changes(report_editor.ReportEdit(**edits), document)
    assert values['pageEdits']['added'] == [added]  # A text-only one-page report is valid.


def test_blank_fields_edit_revision_and_recipient_integrity(prepared, monkeypatch):
    h = prepared
    before = h.job.package_path.read_bytes()
    page = h.client.get(h.url + '/edit')
    assert page.status_code == 200 and page.headers['cache-control'] == 'no-store'
    assert '자동 저장' in page.text and "frame-ancestors 'self'" in page.headers['content-security-policy']
    policy = page.headers['content-security-policy']
    assert "img-src 'self' blob: data:;" in policy  # Local thumbnails/embedded raster mail images, no external URLs.
    assert "default-src 'none';" in policy and "connect-src 'self';" in policy
    assert "script-src 'unsafe-inline';" in policy and "base-uri 'none';" in policy
    response = h.client.get(h.url + '/editor-data')
    assert response.headers['cache-control'] == 'no-store'
    model = response.json()['document']
    assert all(not v for v in model['transfer'].values())
    condition = next(s for s in model['sections'] if s['sectionId'] == 'experiment_conditions')
    assert len(condition['rows']) >= 7 and any(r[-1] == '' for r in condition['rows'])
    assert ['공통','실험환경/비고',''] in condition['rows']
    edits = edit_request(model)
    edits['title'] = '검토 완료 분석 보고서'
    edits['transfer'] = dict(requestNumber='REQ-REVIEW', limsExperimentCode='LIMS-001', equipmentCode='LAB-1', operatorId='operator1')
    summary = next(s for s in edits['sections'] if s['sectionId'] == 'summary')
    summary['paragraphs'] = ['분석자가 확인한 피크입니다. <script>alert(1)</script> 물질 확정은 추가 검증이 필요합니다.']
    next(s for s in edits['sections'] if s['sectionId'] == 'experiment_conditions')['rows'][0][-1] = '온도 23°C · 습도 45%'
    edits['auxiliary']['email_subject'] = '[RIST] 분석자가 검토한 보고서 안내'
    edits['deliveryEmail'] = dict(recipientName='의뢰 담당자',to='to@example.com',cc='cc@example.com',bcc='secret@example.com',signature='윤병훈 · RIST',bodyHtml='')
    edits['auxiliary']['email_body'] = '검토 완료된 보고서를 첨부합니다.\n\n' + '피크의 후보 해석과 확인 사항을 검토해 주세요.\n' * 25
    from html import escape
    from app.report_mail import clean_html
    from tests.test_report_mail import embedded_image
    large_image = embedded_image((600,600), kind='JPEG', noise=True)
    assert len(large_image) > 120000  # Image bytes must not consume the old text-only budget.
    rich, plain = clean_html('<p>' + escape(edits['auxiliary']['email_body']) + '</p><table><tr><td>시료 A</td><td>3.2</td></tr></table><img src="' + large_image + '">')
    edits['deliveryEmail']['bodyHtml'] = rich
    edits['auxiliary']['email_body'] = plain
    registered = []
    def register(**kwargs):
        registered.append(kwargs)
        return kwargs['package_path']
    monkeypatch.setattr(report_editor, 'register_generated_report_package', register)
    response = h.client.post(h.url + '/revisions', json=edits, headers={'X-Requested-With':'RIST-Report-Editor'})
    assert response.status_code == 202, response.text
    revised = wait_revision(h, response.json()['jobId'])
    assert revised['status'] == 'completed', revised
    assert revised['parentJobId'] == h.job.job_id and revised['jobId'] != h.job.job_id
    assert revised['transfer'] == edits['transfer']
    assert registered[0]['report_id'] == revised['jobId']
    assert registered[0]['request_number'] == 'REQ-REVIEW'
    assert registered[0]['report_options']['revisionOf'] == h.job.job_id
    assert registered[0]['report_options']['email']['bcc'] == ['secret@example.com']
    assert registered[0]['report_options']['email']['bodyText'].endswith('윤병훈 · RIST')
    assert len(registered[0]['report_options']['email']['inlineImages']) == 1
    assert 'cid:' in registered[0]['report_options']['email']['bodyHtml']
    assert h.job.package_path.read_bytes() == before
    trace = json.loads((h.store.get(revised['jobId']).tmp_root / 'job/report/report.json').read_text())['traceability']
    assert trace['revisionOf'] == h.job.job_id
    assert trace['savedBy'] is None  # Auth disabled: never substitute owner as the actual editor.
    assert trace['savedAt']
    saved = h.client.get(h.prefix + '/' + revised['jobId'] + '/editor-data').json()['document']
    assert saved['transfer'] == edits['transfer'] and saved['title'] == edits['title']
    assert saved['auxiliary'] == edits['auxiliary']  # Never regenerate or shorten an edited AI draft.
    assert saved['deliveryEmail'] == edits['deliveryEmail']
    assert next(s for s in saved['sections'] if s['sectionId']=='summary')['source']=='user'
    original = zipfile.ZipFile(BytesIO(before))
    with zipfile.ZipFile(BytesIO(h.client.get(revised['downloadUrl']).content)) as archive:
        assert all(b'secret@example.com' not in archive.read(name) for name in archive.namelist())
        assert '윤병훈 · RIST' in archive.read('email_body.html').decode()
        assert 'data:image/jpeg;base64,' in archive.read('email_body.html').decode()
        assert '<table' in archive.read('email_body.html').decode()
        for name in ['current_graph.png','raw_data.xlsx']:
            assert archive.read(name) == original.read(name)
        html = archive.read('report.html').decode()
        assert '분석자가 확인한 피크' in html and '사용자 수정' in html
        assert '<script>alert(1)</script>' not in html
        assert 'XMLHttpRequest' not in html and 'contenteditable' not in html
        assert '온도 23°C' in archive.read('report-summary.html').decode()
        assert 'REQ-REVIEW' in archive.read('report.md').decode()
        assert edits['auxiliary']['email_body'] in archive.read('email_body.md').decode()
        with zipfile.ZipFile(BytesIO(archive.read('report.pptx'))) as ppt:
            texts = ppt_text(ppt)
            assert 'REQ-REVIEW' in texts and 'LIMS-001' in texts
            assert '분석자가확인한피크' in ''.join(texts.split())
            assert 'ppt/media/posco-banner.jpeg' in ppt.namelist()
            assert 'ppt/media/rist-logo.png' in ppt.namelist()
            assert 'Experiment banner title' in ppt.read('ppt/slides/slide1.xml').decode()
    revision_document = report_editor._load_document(h.store.get(revised['jobId']))
    assert revision_document.experiment_code == ('FT-IR' if h.kind == 'ftir' else 'RAMAN')


def test_editor_validation_csrf_and_expiration(prepared):
    h=prepared
    edits=edit_request(h.client.get(h.url+'/editor-data').json()['document'])
    assert h.client.post(h.url+'/revisions',json=edits).status_code==403
    headers={'X-Requested-With':'RIST-Report-Editor','Origin':'https://untrusted.example'}
    assert h.client.post(h.url+'/revisions',json=edits,headers=headers).status_code==403
    headers.pop('Origin')
    for mutation in ['extra','sections','table']:
        bad=deepcopy(edits)
        if mutation=='extra': bad['filePath']='/etc/passwd'
        if mutation=='sections': bad['sections']=[]
        if mutation=='table': next(s for s in bad['sections'] if s['rows'] is not None)['rows'].append(['extra'])
        assert h.client.post(h.url+'/revisions',json=bad,headers=headers).status_code==422
    assert h.client.post(h.url+'/revisions',content=b'x'*(2*1024*1024+1),headers=headers).status_code==413
    assert report_editor._REVISION_SLOT.acquire(False)
    try:
        assert h.client.post(h.url+'/revisions',json=edits,headers=headers).status_code==429
    finally:
        report_editor._REVISION_SLOT.release()
    other='raman' if h.kind=='ftir' else 'ftir'
    assert report_editor._load_document(h.job).title
    h.job.filename=other+'-report-package.zip'
    assert h.client.get(h.url+'/edit').status_code==404
    h.job.filename=h.kind+'-report-package.zip'
    (h.job.tmp_root/'job/report/report.json').unlink()
    assert h.client.get(h.url+'/editor-data').status_code==410


def test_editor_owner_project_and_sso(prepared):
    h=prepared
    h.app.state.settings=SimpleNamespace(auth_enabled=True)
    context=AuthContext(user_id='other',login_id='sso-login',email=None,display_name='Reviewer',status='ACTIVE',
        session_id='session',session_expires_at=datetime.now(timezone.utc),sso_authenticated_at=None,
        projects=frozenset({h.kind.upper()}),roles=frozenset(),sso_identity=None)
    h.app.state.actor=context
    assert h.client.get(h.url+'/editor-status').status_code==403
    assert h.client.get(h.url+'/editor-data').status_code==403
    assert h.client.get(h.url+'/edit-layout').status_code==403
    assert h.client.get(h.url+'/edit-slides/0').status_code==403
    assert h.client.post(h.url+'/editor-images',content=b'private',headers={'X-Requested-With':'RIST-Report-Editor'}).status_code==403
    assert h.client.post(h.url+'/editor-graphs',content=b'private',headers={'X-Requested-With':'RIST-Report-Editor'}).status_code==403
    h.app.state.actor=replace(context,user_id='owner',projects=frozenset())
    assert h.client.get(h.url+'/editor-status').status_code==403
    assert h.client.get(h.url+'/editor-data').status_code==403
    assert h.client.post(h.url+'/editor-images',content=b'private',headers={'X-Requested-With':'RIST-Report-Editor'}).status_code==403
    assert h.client.post(h.url+'/editor-graphs',content=b'private',headers={'X-Requested-With':'RIST-Report-Editor'}).status_code==403
    h.app.state.actor=replace(context,user_id='owner',sso_identity={'employee_id':'sso-operator'})
    assert h.client.get(h.url+'/editor-status').status_code==200
    response=h.client.get(h.url+'/editor-data')
    assert response.status_code==200 and response.json()['authenticatedOperatorId']=='sso-operator'
    h.app.state.actor=replace(context,user_id='admin',projects=frozenset(),roles=frozenset({'ADMIN'}))
    assert h.client.get(h.url+'/edit').status_code==200


def test_editor_preflight_rejects_stale_wrong_kind_pending_and_expired_jobs(prepared):
    h = prepared
    response = h.client.get(h.url + '/editor-status')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['jobId'] == h.job.job_id
    assert response.json()['editUrl'] == h.url + '/edit'
    other_kind = 'raman' if h.kind == 'ftir' else 'ftir'
    h.job.filename = other_kind + '-report-package.zip'
    response = h.client.get(h.url + '/editor-status')
    assert response.status_code == 404 and response.json()['code'] == 'REPORT_JOB_NOT_FOUND'
    h.job.filename = h.kind + '-report-package.zip'
    h.job.status = 'running'
    response = h.client.get(h.url + '/editor-status')
    assert response.status_code == 409 and response.json()['code'] == 'REPORT_NOT_READY'
    h.job.status = 'completed'
    root = h.job.tmp_root
    h.job.tmp_root = None
    response = h.client.get(h.url + '/editor-status')
    assert response.status_code == 410 and response.json()['code'] == 'REPORT_EDIT_EXPIRED'
    h.job.tmp_root = root
    h.store.remove(h.job.job_id)
    for suffix in ('/editor-status', '/edit'):
        response = h.client.get(h.url + suffix)
        assert response.status_code == 404 and response.json()['code'] == 'REPORT_JOB_NOT_FOUND'


def test_failed_revision_keeps_original_and_releases_capacity(prepared, monkeypatch):
    h=prepared
    before=h.job.package_path.read_bytes()
    edits=edit_request(h.client.get(h.url+'/editor-data').json()['document'])
    def fail(*args, **kwargs): raise RuntimeError('render unavailable')
    monkeypatch.setattr(preview_report,'render_report_formats',fail)
    response=h.client.post(h.url+'/revisions',json=edits,headers={'X-Requested-With':'RIST-Report-Editor'})
    assert wait_revision(h,response.json()['jobId'])['status']=='failed'
    assert h.job.package_path.read_bytes()==before
    assert report_editor._REVISION_SLOT.acquire(False)
    report_editor._REVISION_SLOT.release()


def test_transfer_metadata_mismatch_rejected(prepared):
    h=prepared
    payload=preview_report.PreviewReportSendRequest(requestNumber='REQ',experimentCode='FTIR',equipmentCode='EQ',operatorId='OP')
    with pytest.raises(ValueError,match='보고서 내용과 다릅니다'):
        preview_report.send_preview_report_package(job=h.job,payload=payload,settings=SimpleNamespace(),database=None)


def ppt_text(archive):
    return '\n'.join(''.join(ET.fromstring(archive.read(name)).itertext())
        for name in archive.namelist() if name.startswith('ppt/slides/slide') and name.endswith('.xml'))


def test_complete_conditions_and_user_prose_survive_ppt(tmp_path):
    document=ReportDocument('test','검토 보고서','FT-IR',{},'2026-10-15')
    long_text='장비 환경과 측정 조건을 확인하였습니다. '*45+'최종 검토 문장입니다.'
    document.sections=[ReportSection('summary','해석',paragraphs=[long_text],source='user'),
                       ReportSection('experiment_conditions','실험조건 및 실험환경',table=ReportTable(['대상','항목','값'],
                         [[f'시료{i}',f'조건{i}',f'값{i}'] for i in range(24)]))]
    ensure_editable_fields(document,{},'FT-IR',transfer_values({}))
    assert _document_metadata(document)[:4]==[('의뢰번호',''),('실험코드',''),('실험장비',''),('실험자','')]
    document.section('experiment_conditions').table.rows.append(['공통','비고',long_text])
    path=tmp_path/'test.pptx';render_pptx(document,path)
    with zipfile.ZipFile(path) as archive:
        text=ppt_text(archive)
    assert '연동 예정' not in text and '실험조건 기준' not in text
    for i in range(24): assert f'값{i}' in text
    assert text.count('최종 검토 문장입니다.') >= 2  # Both prose and table endings, over multiple pages.
    assert analysis_preview(document)['status']=='reviewed'
    copy=document_from_dict(document.to_dict(),image_path=tmp_path/'graph.png')
    assert copy.to_dict()==document.to_dict()


def test_explicit_blank_conditions_override_raw_metadata():
    document=ReportDocument('test','test','FT-IR',{},'2026-10-15',sections=[
        ReportSection('experiment_conditions','조건',table=ReportTable(['대상','항목','값'],[['sample','Resolution','4 cm-1']]))])
    ensure_editable_fields(document,{'reportConditionFields':[{'target':'공통','label':'Resolution','value':''}]},'FT-IR',transfer_values({}))
    assert [r for r in document.section('experiment_conditions').table.rows if r[1]=='Resolution']==[['공통','Resolution','']]


@pytest.mark.parametrize('project', ['FT-IR', 'RAMAN'])
def test_initial_lims_code_does_not_replace_project_or_posco_template(tmp_path, project):
    document = ReportDocument('test', '의뢰 조회 보고서', project, {}, '2026-10-15')
    transfer = transfer_values({'reportContext': {'requestNumber': 'REQ-LOOKUP', 'limsExperimentCode': 'TST-123'}})
    ensure_editable_fields(document, {}, project, transfer)
    assert document.experiment_code == project
    assert document.pk['experimentCode'] == 'TST-123'
    assert ('실험코드', 'TST-123') in _document_metadata(document)
    path = tmp_path / 'initial.pptx'
    render_pptx(document, path)
    with zipfile.ZipFile(path) as ppt:
        assert 'ppt/media/posco-banner.jpeg' in ppt.namelist()
        assert 'ppt/media/rist-logo.png' in ppt.namelist()
        assert 'TST-123' in ppt_text(ppt)


def test_named_fields_survive_reopen_and_update_linked_outputs_only(prepared):
    h = prepared
    edits = edit_request(h.client.get(h.url + '/editor-data').json()['document'])
    edits['completionDate'] = '2026-10-14'
    edits['variables'] = [{'name':'측정온도','value':'23','unit':'°C'}]
    edits['transfer']['requestNumber'] = 'REQ-LINK'
    edits['title'] = '{{의뢰번호}} 분석'
    summary = next(s for s in edits['sections'] if s['sectionId'] == 'summary')
    summary['paragraphs'] = ['{{측정온도}}에서 분석했습니다. 완료일: {{완료일}}', '23 °C 고정 문구']
    condition = next(s for s in edits['sections'] if s['sectionId'] == 'experiment_conditions')
    condition['rows'][0][2] = '{{측정온도}}'
    condition['rows'][1][2] = '23 °C'
    edits['auxiliary']['email_body'] = '조건: {{측정온도}}'
    original_id = h.job.job_id
    for expected in ('23', '27'):
        edits['variables'][0]['value'] = expected
        response = h.client.post(h.prefix+'/'+original_id+'/revisions',json=edits,headers={'X-Requested-With':'RIST-Report-Editor'})
        assert response.status_code == 202, response.text
        job = wait_revision(h, response.json()['jobId'])
        assert job['status'] == 'completed', job
        saved = h.client.get(h.prefix+'/'+job['jobId']+'/editor-data').json()['document']
        assert saved['title'] == '{{의뢰번호}} 분석' and saved['completionDate'] == '2026-10-14'
        assert saved['variables'] == edits['variables']
        assert next(s for s in saved['sections'] if s['sectionId']=='summary')['paragraphs'][0] == summary['paragraphs'][0]
        with zipfile.ZipFile(BytesIO(h.client.get(job['downloadUrl']).content)) as archive:
            assert 'editor-state.json' not in archive.namelist()
            md = archive.read('report.md').decode()
            assert '{{' not in md
            assert expected + ' °C에서 분석했습니다.' in md
            assert '23 °C 고정 문구' in md
            assert '2026-10-14' in md
            assert expected+' °C' in archive.read('email_body.md').decode()
            assert 'RistDirectEditor' not in archive.read('report.html').decode()
            with zipfile.ZipFile(BytesIO(archive.read('report.pptx'))) as ppt:
                text = ppt_text(ppt)
                assert expected + ' °C' in text and '{{' not in text
        layout = h.client.get(h.prefix+'/'+job['jobId']+'/edit-layout')
        assert layout.status_code == 200, layout.text
        assert any('title' in r['refs'] for r in layout.json()['pages'][0]['regions'])
        image = h.client.get(h.prefix+'/'+job['jobId']+'/edit-slides/0')
        assert image.status_code == 200 and image.content.startswith(b'\xff\xd8')
        assert image.headers['cache-control'] == 'private, no-cache'
        assert h.client.get(h.prefix+'/'+job['jobId']+'/edit-slides/-1').status_code == 404
        assert h.client.get(h.prefix+'/'+job['jobId']+'/edit-slides/200').status_code == 404
        original_id = job['jobId']
        edits = edit_request(saved)


def test_slide_cache_revalidates_permissions_expiry_and_representation(prepared):
    h = prepared
    url = h.url + '/edit-slides/0'
    response = h.client.get(url)
    assert response.status_code == 200
    etag = response.headers['etag']
    assert response.headers['cache-control'] == 'private, no-cache'
    assert response.headers['vary'] == 'Cookie'
    for validator in (etag, 'W/' + etag, '"outdated", ' + etag):
        cached = h.client.get(url, headers={'If-None-Match': validator})
        assert cached.status_code == 304 and cached.content == b''
        assert cached.headers['etag'] == etag
    fresh = h.client.get(url, headers={'If-None-Match': '"outdated"'})
    assert fresh.status_code == 200 and fresh.content == response.content
    thumbnail = h.client.get(url + '?thumbnail=true', headers={'If-None-Match': etag})
    assert thumbnail.status_code == 200 and thumbnail.headers['etag'] != etag
    assert h.client.get(h.url+'/edit-slides/199', headers={'If-None-Match': etag}).status_code == 404
    # A previous viewer cannot bypass ownership checks by presenting a cached ETag.
    h.app.state.settings = SimpleNamespace(auth_enabled=True)
    h.app.state.actor = AuthContext(user_id='other', login_id='other', email=None, display_name='Other',
        status='ACTIVE', session_id='session', session_expires_at=datetime.now(timezone.utc),
        sso_authenticated_at=None, projects=frozenset({h.kind.upper()}), roles=frozenset(), sso_identity=None)
    assert h.client.get(url, headers={'If-None-Match': etag}).status_code == 403
    h.app.state.actor = replace(h.app.state.actor, user_id='owner', projects=frozenset())
    assert h.client.get(url, headers={'If-None-Match': etag}).status_code == 403
    h.app.state.actor = replace(h.app.state.actor, projects=frozenset({h.kind.upper()}))
    (h.job.tmp_root/'job/report/report-from-pptx.pdf').unlink()
    assert h.client.get(url, headers={'If-None-Match': etag}).status_code == 410


def test_package_filename_is_download_only_and_never_a_storage_path(prepared):
    from urllib.parse import unquote
    h = prepared
    original = h.job.package_path.read_bytes()
    response = h.client.get(h.url+'/download', params={'filename':'시험 보고서'})
    assert response.status_code == 200 and response.content == original
    assert '시험 보고서.zip' in unquote(response.headers['content-disposition'])
    assert h.job.filename == h.kind+'-report-package.zip'
    assert h.job.package_path.read_bytes() == original
    for name in ('../other.zip', 'C:\\report.zip', 'invalid\r\nHeader.zip', 'NUL.zip', '.zip', 'x'*151):
        assert h.client.get(h.url+'/download', params={'filename':name}).status_code == 400
    assert h.client.get(h.url+'/download').headers['content-disposition'].endswith('"'+h.kind+'-report-package.zip"')


def test_invalid_variables_do_not_create_revisions(prepared):
    h = prepared
    original = edit_request(h.client.get(h.url+'/editor-data').json()['document'])
    cases = [
        {'variables':[{'name':'의뢰번호','value':'collision'}]},
        {'variables':[{'name':'a','value':'{{b}}'},{'name':'b','value':'{{a}}'}]},
        {'variables':[{'name':'a','value':'x'},{'name':' a ','value':'y'}]},
        {'title':'{{없음}}'},
        {'completionDate':'2026-02-31'},
        {'completionDate':'20261015'},
        {'variables':[{'name':'a','value':'X'*1000}], 'title':'{{a}}'},
    ]
    count = len(h.store._jobs)
    for mutation in cases:
        value = deepcopy(original); value.update(mutation)
        response = h.client.post(h.url+'/revisions',json=value,headers={'X-Requested-With':'RIST-Report-Editor'})
        assert response.status_code == 422, response.text
        assert len(h.store._jobs) == count

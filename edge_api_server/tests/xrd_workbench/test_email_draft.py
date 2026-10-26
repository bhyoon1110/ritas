from copy import deepcopy
import io
import json
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from app import llm_client
from app.config import Settings
from app.xrd_workbench.core.email import (
    empty_email, validate_email, email_evidence, evidence_fingerprint, bounded_evidence, rule_email,
)
from app.xrd_workbench.core.report import validate_document, sync_analysis
from app.xrd_workbench.integration import BASE, current
from app.xrd_workbench.rist_llm import generate_email
from app.xrd_workbench.routes import reports
from .test_posco_pptx import fixture
from .test_integration import make_app, upload


def test_email_facts_follow_current_inclusion_names_classes_quantities_and_notes():
    project, doc = fixture()
    doc['samples'][1]['included'] = False
    doc['samples'][0]['display_name'] = '수정 시료명'
    doc['summary'] = '수정한 전체 요약'
    doc['samples'][0]['notes'][0]['selected'] = False
    before = deepcopy((project, doc))
    facts = email_evidence(project, doc)
    assert [s['name'] for s in facts['samples']] == ['수정 시료명', '시료 Mix']
    assert [p['classification'] for p in facts['samples'][0]['phases']] == ['주요상', '미량상', '추정상']
    assert facts['samples'][1]['quantities'][0]['raw'] == '10.0(1)'
    assert doc['samples'][0]['notes'][0]['text'] not in facts['samples'][0]['selected_notes']
    draft = rule_email(facts)
    assert all(t in draft['body'] for t in ('수정한 전체 요약', '주요상: Phase0', '10.0(1) wt%', 'REQ-123'))
    assert '시료 B' not in draft['body']
    assert validate_email(draft) == draft
    assert (project, doc) == before
    first = evidence_fingerprint(facts)
    doc['email']['body'] = '직접 수정한 이메일'
    assert evidence_fingerprint(email_evidence(project, doc)) == first
    doc['samples'][0]['manual_note'] = '변경된 분석 의견'
    assert evidence_fingerprint(email_evidence(project, doc)) != first


@pytest.mark.parametrize('change', [dict(subject='x\ny'), dict(body='x'*4001), dict(subject='x'*181),
                                   dict(body=None), dict(source_fingerprint='bad'), dict(status='sent')])
def test_email_validation_rejects_invalid_or_header_injection(change):
    with pytest.raises(ValueError):
        validate_email(dict(empty_email(), **change))


def test_old_documents_and_analysis_refresh_keep_manual_mail():
    project, doc = fixture()
    doc.pop('email')
    assert validate_document(project, doc)['email'] == empty_email()
    doc['email'] = dict(empty_email(), subject='직접 제목', body='직접 본문', status='manual')
    assert validate_document(project, doc)['email'] == doc['email']
    project['analysis_revision'] = 1
    merged, updated = sync_analysis(project, doc)
    assert updated and merged['email'] == doc['email']


@pytest.mark.parametrize('mode', ['good', 'numeric', 'invalid', 'timeout', 'large'])
def test_model_uses_only_admin_endpoint_closes_and_falls_back(tmp_path, monkeypatch, mode):
    calls = []
    class Client:
        def __init__(self, *args): calls.append(args)
        def get_model_info(self): return {'max_model_len': 32768}
        def chat_completion(self, payload):
            calls.append(payload)
            evidence = json.loads(payload['messages'][1]['content'])
            assert '시료 B' not in [s['name'] for s in evidence['samples']]
            assert evidence['summary'] == '최종 검토 요약'
            if mode == 'timeout': raise llm_client.LlmError('TIMEOUT', 'private path', retryable=True)
            if mode == 'invalid': return '{}', {}
            return json.dumps(dict(email_subject='[RIST] XRD 결과 안내',
                                   email_body='시료 A의 주요상은 Phase0입니다. ' + ('99999 wt%' if mode == 'numeric' else '상세 결과를 검토해 주십시오.'))), {}
        def close(self): calls.append('closed')
    monkeypatch.setattr(llm_client, 'LocalLlmClient', Client)
    project, doc = fixture()
    doc['samples'][1]['included'] = False
    doc['summary'] = '최종 검토 요약'
    doc['llm'] = dict(provider='lmstudio', base_url='http://untrusted.invalid', model='client-model')
    config = Settings(storage_root=tmp_path, llm_model='admin-model', llm_context_window=32768,
                      llm_max_input_chars=1 if mode == 'large' else 200000)
    token = current.set(SimpleNamespace(settings=config))
    try:
        value = generate_email(project, doc)
    finally:
        current.reset(token)
    assert calls[0][:2] == (config.llm_base_url, 'admin-model')
    assert calls[-1] == 'closed'
    assert value['status'] == ('llm' if mode == 'good' else 'fallback')
    assert value['subject'] and value['body']
    assert '99999' not in value['body'] and 'private path' not in value['message']
    if mode == 'large': assert len(calls) == 2


def test_large_evidence_is_explicitly_bounded_and_rules_fit():
    project, doc = fixture()
    facts = email_evidence(project, doc)
    facts['samples'] *= 20
    facts['samples'][0]['manual_note'] = '아주 긴 문장 ' * 3000
    short = bounded_evidence(facts)
    assert len(short['samples']) == 12 and short['omitted_samples'] == 48
    assert short['samples'][0]['omitted_manual_note']
    assert validate_email(rule_email(facts))['body'].endswith('감사합니다.')


def test_rules_keep_review_warnings_and_blank_mail_is_not_exported():
    from app.xrd_workbench.core.email import email_markdown
    project, doc = fixture()
    facts = email_evidence(project, doc)
    facts['samples'][0]['warnings'] = ['정량 단위 확인 필요']
    facts['samples'][0]['review_required'] = True
    facts['samples'][0]['retained_previous_notes'] = ['이전 문장: 99999 wt%']
    mail = rule_email(facts)
    assert '경고' in mail['body'] and '대조 검토' in mail['body']
    assert '99999' not in mail['body']
    assert email_markdown(doc) == ''
    doc['email']['body'] = '본문만 있는 메일'
    assert email_markdown(doc) == '본문만 있는 메일\n'
    doc['email']['body'] = '  '
    assert email_markdown(doc) == ''


def draft_api(tmp_path, monkeypatch, auth=False):
    app = make_app(tmp_path, auth=auth)
    client = TestClient(app, headers={'test-user': 'one'} if auth else {})
    sid = upload(client, {'A.txt': b'#Intensity_unit=cps\n10 1\n20 8\n30 3\n'})
    doc = client.get(BASE + f'/api/reports/{sid}').json()['document']
    monkeypatch.setattr(reports, 'generate_email', lambda p, d: rule_email(email_evidence(p, d)))
    return client, sid, doc


def test_open_returns_analysis_seed_without_changing_saved_document_or_calling_model(tmp_path, monkeypatch):
    client, sid, doc = draft_api(tmp_path, monkeypatch)
    def no_model(*args):
        raise AssertionError('Opening a report must not call the model')
    monkeypatch.setattr(reports, 'generate_email', no_model)
    url = BASE + f'/api/reports/{sid}'
    for status in ('empty', 'manual', 'fallback'):
        doc['email'] = dict(empty_email(), subject='  ', body='\n', status=status)
        doc['summary'] = '현재 저장한 분석 결과'
        saved = client.put(url, json={'document': doc, 'expected_revision': doc['revision']}).json()
        value = client.get(url).json()
        assert value['document'] == saved
        assert value['email_default']['subject']
        assert '현재 저장한 분석 결과' in value['email_default']['body']
        assert value['email_default']['status'] == 'rules'
        doc = saved
    doc['email'] = dict(empty_email(), subject='직접 작성한 제목', status='manual')
    saved = client.put(url, json={'document': doc, 'expected_revision': doc['revision']}).json()
    value = client.get(url).json()
    assert value['document'] == saved and value['email_default'] is None


def test_draft_generation_is_ephemeral_editable_and_preserved_in_recovery(tmp_path, monkeypatch):
    client, sid, doc = draft_api(tmp_path, monkeypatch)
    baseline = deepcopy(doc)
    doc['summary'] = '미저장 최종 분석 결과'
    before = list(tmp_path.rglob('job.json'))
    response = client.post(BASE + f'/api/reports/{sid}/email-draft', json={'document': doc})
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'private, no-store'
    assert '미저장 최종 분석 결과' in response.json()['body']
    assert list(tmp_path.rglob('job.json')) == before
    assert client.get(BASE + f'/api/reports/{sid}').json()['document'] == baseline
    from app.report_mail import normalize_delivery
    doc['email'] = dict(response.json(), subject='사용자 제목', body='사용자 본문', status='manual',
                        delivery=normalize_delivery(dict(to='to@example.com', bcc='secret@example.com',
                            bodyHtml='<strong>사용자 본문</strong>', signature='담당자 서명')))
    recovered = client.put(BASE + f'/api/reports/{sid}/draft', json=dict(document=doc, baseline=baseline, expected_revision=0))
    assert recovered.status_code == 200, recovered.text
    assert client.get(BASE + f'/api/reports/{sid}/draft').json()['document']['email'] == doc['email']
    saved = client.put(BASE + f'/api/reports/{sid}', json={'document': doc, 'expected_revision': 0})
    assert saved.status_code == 200, saved.text
    loaded = client.get(BASE + f'/api/reports/{sid}').json()
    assert loaded['document']['email'] == doc['email'] and not loaded['email_stale']
    changed = saved.json(); changed['summary'] = '새로운 분석 결과'
    client.put(BASE + f'/api/reports/{sid}', json={'document': changed, 'expected_revision': 1})
    assert client.get(BASE + f'/api/reports/{sid}').json()['email_stale']
    assert client.post(BASE + f'/api/reports/{sid}/email-draft', json={'document': doc}).status_code == 409


def test_mail_generation_is_scoped_to_xrd_owner_not_sso_send_authority(tmp_path, monkeypatch):
    client, sid, doc = draft_api(tmp_path, monkeypatch, auth=True)
    path = BASE + f'/api/reports/{sid}/email-draft'
    assert client.post(path, json={'document': doc}).status_code == 200  # no SSO required for drafting
    client.headers['test-user'] = 'two'
    assert client.post(path, json={'document': doc}).status_code == 404
    client.headers['test-user'] = 'one'; client.headers['test-project'] = 'FTIR'
    assert client.post(path, json={'document': doc}).status_code == 403


def test_concurrent_save_and_saturated_model_do_not_overwrite(tmp_path, monkeypatch):
    client, sid, doc = draft_api(tmp_path, monkeypatch)
    path = BASE + f'/api/reports/{sid}/email-draft'
    assert reports.email_slot.acquire(blocking=False)
    try:
        assert client.post(path, json={'document': doc}).status_code == 429
    finally:
        reports.email_slot.release()
    def generate(p, d):
        other = deepcopy(d); other['email']['body'] = '다른 창의 문안'
        current.get().storage.reports.save(sid, p, other, d['revision'])
        return rule_email(email_evidence(p, d))
    monkeypatch.setattr(reports, 'generate_email', generate)
    assert client.post(path, json={'document': doc}).status_code == 409
    assert client.get(BASE + f'/api/reports/{sid}').json()['document']['email']['body'] == '다른 창의 문안'


def test_package_excludes_email_sidecar_without_deleting_the_saved_document_email(tmp_path):
    from app.xrd_workbench.render.package import build_package
    from .fixture_data import example_project, resolve_source
    from app.xrd_workbench.core.report import new_document
    project = example_project(); doc = new_document(project)
    doc['email'] = dict(empty_email(), subject='메일 전용 제목', body='직접 수정한 메일\n본문 <plain text>', status='manual')
    doc['email']['delivery'] = dict(to='to@example.com', bcc='secret@example.com', signature='담당자 서명')
    before = deepcopy(doc)
    with ZipFile(io.BytesIO(build_package(project, doc, lambda fid: resolve_source('example', fid)))) as archive:
        assert {name.split('/')[0] for name in archive.namelist()} == {
            'Measured Data', 'Report', '보고서.pdf', '보고서.pptx', '보고서.html', '뷰어.html',
        }
        for name in archive.namelist():
            assert b'secret@example.com' not in archive.read(name)
        with ZipFile(io.BytesIO(archive.read('보고서.pptx'))) as ppt:
            assert not any('메일 전용 제목' in ppt.read(n).decode() for n in ppt.namelist() if n.startswith('ppt/slides/') and n.endswith('.xml'))
    assert doc == before

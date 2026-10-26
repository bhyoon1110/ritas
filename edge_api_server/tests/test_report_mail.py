from datetime import datetime, timezone
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.auth import AuthContext
from app.errors import ApiException
from app.report_mail import clean_html, normalize_delivery, mail_payload, validate_envelope, write_mail_files, router
from app.report_queue import ReportQueueError
from app.preview_report import send_preview_report_package, PreviewReportSendRequest
from tests.test_report_queue import CapturingDatabase, settings, write_zip


def test_rich_mail_is_safe_and_signature_is_composed_once(tmp_path):
    delivery = dict(recipientName='의뢰자', to='first@example.com; first@example.com',
        cc='review@example.com', bcc='secret@example.com', signature='윤병훈\nRIST <연구소>',
        bodyHtml='<p onclick="x()"><strong>분석 결과</strong><img src="https://invalid/x"><script>alert(1)</script></p>')
    saved = normalize_delivery(delivery)
    assert saved['bodyHtml'] == '<p><strong>분석 결과</strong></p>'
    value = mail_payload('결과 안내', '분석 결과', saved, sending=True)
    assert value['to'] == ['first@example.com'] and value['bcc'] == ['secret@example.com']
    assert value['bodyText'].count('윤병훈') == 1 and value['bodyHtml'].count('윤병훈') == 1
    assert '&lt;연구소&gt;' in value['bodyHtml'] and '<strong>' in value['bodyHtml']
    write_mail_files(tmp_path, '결과 안내', '분석 결과', saved)
    for path in tmp_path.iterdir():
        assert 'secret@example.com' not in path.read_text() and 'first@example.com' not in path.read_text()
    assert '윤병훈' in (tmp_path/'email_body.md').read_text()
    assert '<strong>' in (tmp_path/'email_body.html').read_text()
    assert mail_payload('결과', '새 본문', saved)['bodyHtml'].startswith('새 본문')


@pytest.mark.parametrize('address', ['bad', 'user@example.com\r\nBcc:evil@example.com', 'x\x00@example.com', 'a@example.com,'*51,
                                   '.user@example.com', 'user..name@example.com', 'user@example..com', 'user@-example.com'])
def test_invalid_addresses_can_be_drafts_but_not_queued(address):
    if '\x00' in address:
        with pytest.raises(ValueError): normalize_delivery({'to': address})
        return
    draft = mail_payload('제목', '본문', {'to': address})
    with pytest.raises(ValueError): validate_envelope(draft)


def test_blank_recipients_keep_lims_policy_and_headers_are_not_interpreted():
    assert mail_payload('제목', '본문', sending=True)['recipientPolicy'] == 'lims'
    with pytest.raises(ValueError): mail_payload('제목\nBcc:evil@example.com', '본문', sending=True)
    with pytest.raises(ValueError): mail_payload('제목', '본문', {'bcc':'hidden@example.com'}, sending=True)
    assert clean_html('<svg><script>x()</script></svg><i>ok</i>')[0] == '<i>ok</i>'


def test_empty_mail_does_not_leave_previous_generated_body(tmp_path):
    write_mail_files(tmp_path, '제목', '이전 본문')
    write_mail_files(tmp_path, '', '')
    assert not (tmp_path/'email_body.md').exists()
    assert not (tmp_path/'email_body.html').exists()


def test_bcc_validation_errors_are_redacted():
    from app.errors import redact_validation_errors
    errors = [{'loc': ('body','deliveryEmail','bcc'), 'input':'secret@example.com', 'msg':'invalid'},
              {'loc': ('body',), 'input':{'deliveryEmail':{'bcc':'secret@example.com'}}}]
    assert 'secret@example.com' not in json.dumps(redact_validation_errors(errors))


@pytest.mark.parametrize('kind', ['FT-IR','RAMAN','TEM','XRD'])
def test_all_experiments_preserve_stored_mail_when_queueing(tmp_path, kind):
    db=CapturingDatabase(); config=settings(tmp_path/'storage'); path=tmp_path/'original.zip';write_zip(path)
    envelope=mail_payload('메일 제목','본문',{'to':'to@example.com','bcc':'secret@example.com','signature':'서명'})
    db.fetch_report_run=lambda report_id:{'report_options_json':json.dumps({'email':envelope,'reviewed':True})}
    job=SimpleNamespace(job_id='mail-job',status='completed',package_path=path)
    payload=PreviewReportSendRequest(requestNumber='REQ-1',experimentCode=kind,equipmentCode='LAB',operatorId='USER')
    result=send_preview_report_package(settings=config,database=db,job=job,payload=payload)
    assert result['queued'] and not result['sent']
    options=json.loads(db.values['report_options_json'])
    assert options['reviewed'] and options['email']['bcc']==['secret@example.com']
    assert options['email']['bodyText']=='본문\n\n서명'
    envelope['to']='broken'
    with pytest.raises(ReportQueueError, match='이메일 주소'):
        send_preview_report_package(settings=config,database=db,job=job,payload=payload)


def test_signature_is_authenticated_private_and_shared_across_experiments(tmp_path):
    app=FastAPI();app.include_router(router);app.state.settings=SimpleNamespace(storage_root=tmp_path)
    @app.exception_handler(ApiException)
    async def errors(request,exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({'message':exc.message},status_code=exc.status_code)
    @app.middleware('http')
    async def identity(request,call_next):
        user=request.headers.get('x-test-user')
        if user:request.state.auth_context=AuthContext(user,user,None,user,'ACTIVE','session',datetime.now(timezone.utc),None,frozenset({'FTIR','RAMAN','TEM','XRD'}),frozenset(),None)
        return await call_next(request)
    with TestClient(app) as client:
        url='/api/v1/report-mail/signature';headers={'x-test-user':'one','X-Requested-With':'RIST-Mail-Editor'}
        assert client.get(url).status_code==401
        assert client.put(url,json={'signature':'서명'},headers={'x-test-user':'one'}).status_code==403
        assert client.put(url,json={'signature':'서명'},headers={**headers,'Origin':'https://other.invalid'}).status_code==403
        assert client.put(url,json={'signature':'윤병훈\nRIST'},headers=headers).status_code==200
        assert client.get(url,headers=headers).json()=={'signature':'윤병훈\nRIST'}
        assert client.get(url,headers={'x-test-user':'two'}).json()=={'signature':''}
        assert client.put(url,json={'signature':'x'*2001},headers=headers).status_code==422
        assert client.put(url,json={'signature':''},headers=headers).status_code==200
        assert all((p.stat().st_mode & 0o077)==0 for p in (tmp_path/'user-mail-preferences').iterdir())

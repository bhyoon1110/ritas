from datetime import datetime, timezone
import json
import base64
from io import BytesIO
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from PIL import Image

from app.auth import AuthContext
from app.errors import ApiException
from app.report_mail import clean_html, normalize_delivery, mail_payload, validate_envelope, write_mail_files, router
from app.report_queue import ReportQueueError
from app.preview_report import send_preview_report_package, PreviewReportSendRequest
from tests.test_report_queue import CapturingDatabase, settings, write_zip


def embedded_image(size=(80, 40), kind='PNG', noise=False):
    stream = BytesIO()
    image = Image.effect_noise(size, 100).convert('RGB') if noise else Image.new('RGB', size, '#205080')
    image.save(stream, format=kind, quality=70)
    return 'data:image/' + ('jpeg' if kind == 'JPEG' else kind.lower()) + ';base64,' + base64.b64encode(stream.getvalue()).decode()


def test_mail_table_and_image_roundtrip_to_cid_and_self_contained_html(tmp_path):
    source = '<p><b>결과</b></p><table><tr><td>시료</td><td>값</td></tr><tr><td>A</td><td>3.2</td></tr></table><img src="' + embedded_image() + '" width="320" onerror="alert(1)">'
    clean, plain = clean_html(source)
    assert '[이미지]' in plain and '시료\t값' in plain
    assert 'onerror' not in clean and 'border-collapse:collapse' in clean
    payload = mail_payload('분석 안내', plain, dict(bodyHtml=source, to='to@example.com', bcc='hidden@example.com', signature='담당자'), sending=True)
    assert payload['schemaVersion'] == 2 and len(payload['inlineImages']) == 1
    image = payload['inlineImages'][0]
    assert 'cid:' + image['contentId'] in payload['bodyHtml'] and 'data:image' not in payload['bodyHtml']
    assert '<table' in payload['bodyHtml'] and payload['bodyText'].endswith('담당자')
    assert validate_envelope(payload) == payload
    write_mail_files(tmp_path, '분석 안내', plain, dict(bodyHtml=source, bcc='hidden@example.com'))
    html = (tmp_path/'email_body.html').read_text()
    assert 'data:image/png;base64,' in html and 'cid:' not in html
    assert 'img-src data:' in html and 'hidden@example.com' not in html
    assert 'data:image' not in (tmp_path/'email_body.md').read_text()


@pytest.mark.parametrize('source', [
    'data:image/svg+xml;base64,PHN2Zz4=', 'data:image/png;base64,eA==',
    'data:image/jpeg;base64,' + 'A' * (350 * 1024),
])
def test_invalid_and_oversized_embedded_images_fail_closed(source):
    with pytest.raises(ValueError): clean_html('<img src="' + source + '">')


def test_image_dimensions_mime_total_count_and_cid_integrity():
    with pytest.raises(ValueError): clean_html('<img src="' + embedded_image((1281, 1)) + '">')
    with pytest.raises(ValueError): clean_html('<img src="' + embedded_image().replace('image/png','image/jpeg') + '">')
    assert clean_html('<img src="https://invalid/tracker"><img src="file:///etc/passwd">')[0] == ''
    source = '<img src="' + embedded_image() + '">'
    assert clean_html(source * 4)[0].count('<img') == 4
    with pytest.raises(ValueError): clean_html(source * 5)
    with pytest.raises(ValueError): clean_html('<img src="cid:missing">')
    _, plain = clean_html(source)
    payload = mail_payload('제목', plain, {'bodyHtml':source})
    payload['inlineImages'][0]['sizeBytes'] += 1
    with pytest.raises(ValueError): validate_envelope(payload)
    payload['inlineImages'] = []
    with pytest.raises(ValueError): validate_envelope(payload)


def test_table_bounds_and_injected_attributes_are_sanitized():
    table = '<table><tr><td>x</td></tr></table>'
    clean, _ = clean_html('<table style="background:url(https://invalid)"><tr><td onclick="evil()" colspan="99">x</td></tr></table>')
    assert 'invalid' not in clean and 'onclick' not in clean and 'colspan' not in clean
    for html in (table*4, '<table><tr><td>'+table+'</td></tr></table>',
                 '<table>'+('<tr><td>x</td></tr>'*21)+'</table>',
                 '<table><tr>'+('<td>x</td>'*9)+'</tr></table>'):
        with pytest.raises(ValueError): clean_html(html)


def test_mail_alignment_and_table_geometry_survive_storage_and_delivery(tmp_path):
    default, _ = clean_html('<table><tr><td>기본</td></tr></table>')
    assert clean_html(default)[0] == default
    source = ('<p style="text-align:right;position:fixed">결과</p><table><tr style="height:80px">'
              '<td style="text-align:center;vertical-align:middle;width:65%;background-color:rgb(232,242,250);background-image:url(https://invalid)">시료</td>'
              '<td style="width:35%;vertical-align:bottom">A</td></tr></table>')
    html, body = clean_html(source)
    assert 'text-align:right' in html and 'vertical-align:middle' in html and 'vertical-align:bottom' in html
    assert 'width:65%' in html and 'height:80px' in html and 'bgcolor="#e8f2fa"' in html
    assert 'position' not in html and 'invalid' not in html
    assert clean_html(html)[0] == html
    payload = mail_payload('결과', body, {'bodyHtml':html}, sending=True)
    assert validate_envelope(payload)['bodyHtml'] == html
    write_mail_files(tmp_path, '결과', body, {'bodyHtml':html})
    assert html in (tmp_path/'email_body.html').read_text()
    bad, _ = clean_html('<table><tr style="height:9999px"><td style="width:1%;text-align:expression(x);vertical-align:expression(x);background-color:red">x</td></tr></table>')
    assert '9999' not in bad and 'expression' not in bad and 'width:1%' not in bad and 'background-color' not in bad


def test_inline_contract_can_build_one_mime_message_without_external_files():
    from email.message import EmailMessage
    from email import policy
    from email.parser import BytesParser
    source = '<p>결과</p><img src="' + embedded_image(kind='JPEG') + '">'
    _, plain = clean_html(source)
    payload = mail_payload('제목', plain, {'bodyHtml':source})
    message = EmailMessage()
    message.set_content(payload['bodyText'])
    message.add_alternative(payload['bodyHtml'], subtype='html')
    for image in payload['inlineImages']:
        maintype, subtype = image['contentType'].split('/')
        message.get_payload()[-1].add_related(base64.b64decode(image['dataBase64']), maintype=maintype,
            subtype=subtype, cid='<'+image['contentId']+'>', disposition='inline')
    parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
    assert parsed.get_body(preferencelist=('html',)).get_content_type() == 'text/html'
    images = [p for p in parsed.walk() if p.get_content_maintype() == 'image']
    assert len(images) == 1 and images[0].get_content_disposition() == 'inline'
    assert images[0]['Content-Transfer-Encoding'] == 'base64'
    assert 'cid:'+images[0]['Content-ID'][1:-1] in parsed.get_body(preferencelist=('html',)).get_content()


def test_rich_variables_preserve_tables_images_and_escape_values():
    from app.report_mail import expand_rich_text
    source = '<table><tr><td><b>{{시료}}</b></td></tr></table><img src="' + embedded_image() + '">'
    expanded = expand_rich_text(source, lambda text: text.replace('{{시료}}','<script>x</script>'))
    assert '<b>&lt;script&gt;x&lt;/script&gt;</b>' in expanded
    assert 'data:image/png;base64,' in expanded and '<table' in expanded


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
    for key in ('bodyHtml', 'dataBase64'):
        errors = [{'loc': ('body', 'deliveryEmail', key), 'input': 'private-image-bytes', 'msg': 'invalid'}]
        assert 'private-image-bytes' not in json.dumps(redact_validation_errors(errors))


@pytest.mark.parametrize('kind', ['FT-IR','RAMAN','TEM','XRD'])
def test_all_experiments_preserve_stored_mail_when_queueing(tmp_path, kind):
    db=CapturingDatabase(); config=settings(tmp_path/'storage'); path=tmp_path/'original.zip';write_zip(path)
    html = '<p>본문</p><table><tr><td>시료</td><td>A</td></tr></table><img src="' + embedded_image() + '">'
    _, body = clean_html(html)
    envelope=mail_payload('메일 제목',body,{'bodyHtml':html,'to':'to@example.com','bcc':'secret@example.com','signature':'서명'})
    db.fetch_report_run=lambda report_id:{'report_options_json':json.dumps({'email':envelope,'reviewed':True})}
    job=SimpleNamespace(job_id='mail-job',status='completed',package_path=path)
    payload=PreviewReportSendRequest(requestNumber='REQ-1',experimentCode=kind,equipmentCode='LAB',operatorId='USER')
    result=send_preview_report_package(settings=config,database=db,job=job,payload=payload)
    assert result['queued'] and not result['sent']
    options=json.loads(db.values['report_options_json'])
    assert options['reviewed'] and options['email']['bcc']==['secret@example.com']
    assert options['email']['bodyText']==body+'\n\n서명'
    assert len(options['email']['inlineImages']) == 1
    assert options['email']['inlineImages'] == envelope['inlineImages']
    assert '<table' in options['email']['bodyHtml'] and 'cid:' in options['email']['bodyHtml']
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

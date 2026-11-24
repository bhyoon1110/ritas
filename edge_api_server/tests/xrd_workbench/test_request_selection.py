"""ALL selection keeps the authored XRD report separate from its LIMS method."""
import hashlib
import json
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from app.xrd_workbench import integration as api
from .test_integration import make_app, poll, upload


@pytest.mark.parametrize('saved_scope,sent_scope,status', [
    (None, None, 422),             # Default scope still rejects an unrelated method/equipment.
    (None, 'all', 409),            # ALL must already belong to the generated artifact.
    ('all', None, 409),            # An old ALL artifact cannot silently become current scope.
    ('all', 'all', 200),
    ('all', 'invalid', 422),
])
def test_send_scope_is_bound_to_generated_transfer(tmp_path, monkeypatch, saved_scope, sent_scope, status):
    app = make_app(tmp_path, auth=True)
    transfer = dict(requestNumber='2022T00793', experimentCode='A11112', equipmentCode='100000128', operatorId='SSO-01')
    saved = {**transfer, **({'requestScope':saved_scope} if saved_scope else {})}
    jid = 'b' * 32
    job_dir = tmp_path/'xrd-workbench-v1'/'users'/hashlib.sha256(b'one').hexdigest()/'jobs'/jid
    job_dir.mkdir(parents=True)
    with ZipFile(job_dir/'report.zip','w') as archive:
        archive.writestr('report.txt','Synthetic XRD report')
    (job_dir/'job.json').write_text(json.dumps(dict(status='completed',kind='zip',filename='report.zip',transfer=saved)))
    calls=[]
    monkeypatch.setattr(api,'send_preview_report_package',lambda **kw: calls.append(kw) or {'queued':True})
    sent = {**transfer, **({'requestScope':sent_scope} if sent_scope else {})}
    with TestClient(app) as client:
        response=client.post(api.BASE+f'/api/jobs/{jid}/send',json=sent,headers={'test-user':'one','test-sso':'yes'})
    assert response.status_code == status, response.text
    assert len(calls) == (1 if status == 200 else 0)
    if calls:
        assert calls[0]['payload'].experiment_code == 'A11112'
        assert calls[0]['payload'].request_number == '2022T00793'
        assert calls[0]['payload'].request_scope == 'all'


def test_all_export_keeps_xrd_report_type_actual_method_and_sso_guard(tmp_path, monkeypatch):
    registered, sent = [], []
    monkeypatch.setattr(api,'register_generated_report_package',lambda **kw: registered.append(kw))
    monkeypatch.setattr(api,'send_preview_report_package',lambda **kw: sent.append(kw) or {'queued':True})
    with TestClient(make_app(tmp_path,auth=True),headers={'test-user':'one'}) as client:
        sid=upload(client,{'A.txt':b'10 1\n20 8\n30 3\n'})
        doc=client.get(api.BASE+f'/api/reports/{sid}').json()['document']
        doc['metadata'].update(report_number='2022T00793',experiment_code='A11112',equipment_code='100000128',operator_id='SSO-01')
        assert client.put(api.BASE+f'/api/reports/{sid}',json={'document':doc,'expected_revision':0}).status_code == 200
        transfer=dict(requestNumber='2022T00793',experimentCode='A11112',equipmentCode='100000128',operatorId='SSO-01',requestScope='all')
        bad=client.post(api.BASE+f'/api/reports/{sid}/exports',json={'kind':'zip','revision':1,'transfer':{**transfer,'requestScope':'invalid'}})
        assert bad.status_code == 422 and not registered
        response=client.post(api.BASE+f'/api/reports/{sid}/exports',json={'kind':'zip','revision':1,'transfer':transfer})
        assert response.status_code == 202, response.text
        job=poll(client,response.json())
        assert job['transfer'] == transfer
        assert len(registered) == 1 and not sent
        assert registered[0]['experiment_code'] == 'XRD'
        assert registered[0]['report_options']['analysisType'] == 'XRD'
        assert registered[0]['report_options']['transfer'] == transfer
        route=api.BASE+f'/api/jobs/{job["job_id"]}/send'
        assert client.post(route,json=transfer).json()['error']['code'] == 'SSO_LINK_REQUIRED'
        client.headers['test-sso']='yes'
        changed=client.post(route,json={**transfer,'experimentCode':'OTHER'})
        assert changed.status_code == 409 and not sent
        result=client.post(route,json=transfer)
        assert result.status_code == 200, result.text
        assert sent[0]['report_project'] == 'XRD'
        assert sent[0]['payload'].experiment_code == 'A11112'
        assert sent[0]['payload'].operator_id == 'SSO-01'

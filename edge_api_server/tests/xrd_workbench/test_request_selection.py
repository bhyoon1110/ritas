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


@pytest.mark.parametrize('analysis_map,method_map,name,equipment,scope,status', [
    ((('A11111', 'XRD'),), (), None, '100000128', 'current', 200),
    ((), (('A11111', 'XRD'),), None, '100000128', 'current', 200),
    ((), (), 'XRD 정성분석', '100000128', 'current', 200),
    ((), (), None, 'XRD-01', 'current', 200),
    ((), (('A11111', 'TEM'),), 'XRD 정성분석', 'XRD-01', 'current', 409),
    ((('A11111', 'TEM'),), (), 'XRD 정성분석', 'XRD-01', 'current', 409),
    ((('100000128', 'TEM'),), (), 'XRD 정성분석', '100000128', 'current', 409),
    ((), (), 'TEM 투과전자현미경', 'XRD-01', 'current', 409),
    ((), (), None, '100000128', 'current', 409),
    ((('A11111', 'TEM'),), (), 'TEM', '100000128', 'all', 200),
])
def test_current_ax_send_checks_live_original_scope_once(
        tmp_path, monkeypatch, analysis_map, method_map, name, equipment, scope, status):
    from dataclasses import replace
    from app.lims_requests import LimsRequestsClient
    from tests.test_lims_method_mapping import Lookup, QueueDatabase, row

    app = make_app(tmp_path, auth=True)
    app.state.settings = replace(app.state.settings, lims_requests_enabled=True,
                                 analysis_type_map=analysis_map, request_method_map=method_map)
    transfer = dict(requestNumber='2022T00793', experimentCode='2022A11111',
                    equipmentCode=equipment, operatorId='SSO-01')
    if scope == 'all':
        transfer['requestScope'] = 'all'
    jid = 'c' * 32
    job_dir = tmp_path/'xrd-workbench-v1'/'users'/hashlib.sha256(b'one').hexdigest()/'jobs'/jid
    job_dir.mkdir(parents=True)
    with ZipFile(job_dir/'report.zip', 'w') as archive:
        archive.writestr('report.txt', 'Synthetic XRD report')
    (job_dir/'job.json').write_text(json.dumps(dict(status='completed', kind='zip', filename='report.zip', transfer=transfer)))
    database = QueueDatabase()
    database.fetch_report_run = lambda report_id: {'report_options_json': json.dumps({
        'analysisType': 'XRD', 'workbenchVersion': 1, 'transfer': transfer,
        'email': {'subject': '분석 결과', 'bodyHtml': '<p>검토한 결과입니다.</p>'},
    })}
    app.state.database = database
    upstream = row()
    upstream['testMtdName'] = name
    lookup = Lookup([upstream])
    monkeypatch.setattr(LimsRequestsClient, 'from_settings', classmethod(lambda cls, settings: lookup))
    with TestClient(app) as client:
        # Client display metadata cannot override a conflicting original LIMS row.
        response = client.post(api.BASE+f'/api/jobs/{jid}/send',
                               json={**transfer, 'testMethodCode': 'XRD', 'testMethodName': 'XRD'},
                               headers={'test-user': 'one', 'test-sso': 'yes'})
    assert response.status_code == status, response.text
    assert lookup.calls == [(1, 1000, {'reqNumber': '2022T00793', 'matchMode': 'EXACT'})]
    if status == 200:
        queued = next(values for kind, values in database.writes if kind == 'transfer')
        assert queued['experiment_code'] == 'XRD'
        assert json.loads(queued['report_options_json'])['testMtdCode'] == '2022A11111'
    else:
        assert response.json()['error']['code'] == 'REPORT_REQUEST_SCOPE_MISMATCH'
        assert not database.writes

"""The rendered report kind controls access; the selected method controls LIMS."""
import json
from types import SimpleNamespace

import pytest
from fastapi import Request

from app import report_delivery
from app.errors import ApiException
from app.experiment_routing import analysis_type_for_report
from app.preview_report import PreviewReportSendRequest, send_preview_report_package
from app.report_management import _analysis_type
from app.report_queue import register_generated_report_package, registered_test_method
from tests.test_report_queue import CapturingDatabase, authored_mail_options, settings, write_zip


class RegisteredDatabase(CapturingDatabase):
    def fetch_report_run(self, report_id):
        return next((row for row in reversed(self.report_runs) if row["report_id"] == report_id), None)


def test_xrd_queue_preserves_server_kind_and_actual_lims_method(tmp_path):
    config=settings(tmp_path/'shared')
    source=tmp_path/'generated.zip'
    write_zip(source)
    database=RegisteredDatabase()
    transfer=dict(requestNumber='2022T00793',experimentCode='A11112',equipmentCode='100000128',operatorId='SSO-01',requestScope='all')
    package=register_generated_report_package(
        settings=config,database=database,report_id='xrd-all',package_path=source,experiment_code='XRD',
        request_number=transfer['requestNumber'],equipment_code=transfer['equipmentCode'],operator_id=transfer['operatorId'],
        report_options=authored_mail_options(analysisType='XRD',transfer=transfer),
    )
    result=send_preview_report_package(
        settings=config,database=database,job=SimpleNamespace(job_id='xrd-all',status='completed',package_path=package),
        payload=PreviewReportSendRequest(**transfer),report_project='XRD',
    )
    assert result['queued'] is True
    row=database.fetch_report_run('xrd-all')
    assert row['experiment_code'] == database.values['experiment_code'] == 'XRD'
    assert registered_test_method(row) == 'A11112'
    options=json.loads(database.values['report_options_json'])
    assert options['testMtdCode'] == options['transfer']['experimentCode'] == 'A11112'
    assert options['transfer']['requestScope'] == 'all'
    assert options['analysisType'] == 'XRD'


@pytest.mark.parametrize('stored_code,expected', [('XRD','XRD'),('FT-IR','FTIR'),('LEGACY-X','XRD')])
def test_report_kind_precedes_other_equipment_mapping_and_legacy_codes_still_resolve(stored_code, expected):
    config=SimpleNamespace(analysis_type_map=(('OTHER-EQUIPMENT','TEM'),('LEGACY-X','XRD')))
    row={'experiment_code':stored_code,'equipment_code':'OTHER-EQUIPMENT'}
    assert analysis_type_for_report(row,config) == expected
    assert _analysis_type(row,config) == expected


@pytest.mark.parametrize('user_projects,status', [({'XRD'},200),({'TEM'},403)])
def test_review_uses_xrd_report_access_even_when_lims_method_is_unrelated(tmp_path, monkeypatch, user_projects, status):
    config=SimpleNamespace(auth_enabled=True,analysis_type_map=())
    row={'report_id':'xrd-all','experiment_code':'XRD','equipment_code':'100000128',
         'report_options_json':json.dumps({'analysisType':'TEM','testMtdCode':'A11112'})}
    database=SimpleNamespace(fetch_report_run=lambda _:row, fetch_report_owner=lambda _:{"owner_user_id":"alice"})
    request=Request({'type':'http','app':SimpleNamespace(state=SimpleNamespace(database=database))})
    monkeypatch.setattr(report_delivery,'_settings',lambda _:config)
    monkeypatch.setattr(report_delivery,'_database',lambda _:database)
    monkeypatch.setattr(report_delivery,'require_context',lambda _:SimpleNamespace(is_admin=False,projects=user_projects,user_id="alice"))
    if status == 403:
        with pytest.raises(ApiException) as error:report_delivery._report_for_request(request,'xrd-all')
        assert error.value.code == 'PROJECT_ACCESS_DENIED'
    else:
        result,project=report_delivery._report_for_request(request,'xrd-all')
        assert project == 'XRD' and registered_test_method(result) == 'A11112'


def test_untrusted_options_cannot_reclassify_unknown_report(monkeypatch):
    config=SimpleNamespace(auth_enabled=False,analysis_type_map=())
    row={'experiment_code':'A11112','equipment_code':'100000128',
         'report_options_json':json.dumps({'analysisType':'XRD','_reportProject':'XRD'})}
    monkeypatch.setattr(report_delivery,'_settings',lambda _:config)
    monkeypatch.setattr(report_delivery,'_database',lambda _:SimpleNamespace(fetch_report_run=lambda _:row))
    with pytest.raises(ApiException) as error:report_delivery._report_for_request(None,'generic')
    assert error.value.code == 'REPORT_PROJECT_UNRESOLVED'

import hashlib
import json
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from app.path_bootstrap import add_project_package_paths
add_project_package_paths()
from app.errors import ApiException, api_exception_handler
from app.report_queue import ReportQueueError
from app.xrd_workbench import integration as api
from .test_integration import make_app


@pytest.mark.parametrize('code,message,retryable,status', [
    ('REPORT_MAIL_ATTACHMENT_TOO_LARGE', '첨부파일이 설정된 단일 파일 한도를 초과했습니다.', False, 409),
    ('REPORT_QUEUE_DATABASE_UNAVAILABLE', '보고서 전송 큐 데이터베이스가 연결되어 있지 않습니다.', True, 503),
])
def test_send_route_preserves_queue_error_response(tmp_path, monkeypatch, code, message, retryable, status):
    app = make_app(tmp_path, auth=True)
    app.add_exception_handler(ApiException, api_exception_handler)
    transfer = {'requestNumber': 'REQ-01', 'experimentCode': 'XRD',
                'equipmentCode': 'XRD-01', 'operatorId': 'SSO-01'}
    jid = 'a' * 32
    owner = hashlib.sha256(b'one').hexdigest()
    job_dir = tmp_path / 'xrd-workbench-v1' / 'users' / owner / 'jobs' / jid
    job_dir.mkdir(parents=True)
    package = job_dir / 'report.zip'
    with ZipFile(package, 'w') as archive:
        archive.writestr('report.txt', 'Synthetic report')
    (job_dir / 'job.json').write_text(json.dumps({
        'status': 'completed', 'kind': 'zip', 'filename': package.name, 'transfer': transfer,
    }), encoding='utf-8')
    calls = []

    def fail_queue(**kwargs):
        calls.append(kwargs)
        raise ReportQueueError(code, message, retryable=retryable)

    monkeypatch.setattr(api, 'send_preview_report_package', fail_queue)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(api.BASE + f'/api/jobs/{jid}/send', json=transfer,
                               headers={'test-user': 'one', 'test-sso': 'yes', 'X-Request-Id': 'queue-error'})

    assert response.status_code == status, response.text
    body = response.json()
    assert body['status'] == status
    assert body['code'] == code
    assert body['message'] == message
    assert body['retryable'] is retryable
    assert body['requestId'] == 'queue-error'
    assert len(calls) == 1
    assert calls[0]['job'].package_path == package
    assert calls[0]['payload'].operator_id == 'SSO-01'
    assert package.is_file()

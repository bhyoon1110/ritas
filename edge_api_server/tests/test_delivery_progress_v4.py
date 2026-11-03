import json

from app.report_delivery import _generated_report_page
from app.report_transfer_state import public_delivery_state


def test_public_progress_bounds_errors_and_omits_retry_control_and_envelope():
    state = dict(phase="MAIL_PENDING", tryCount=0, mailPollCount=10,
                 needsReview=True, reviewReason="delay", pollDelaySeconds=600,
                 mailErrorMessage="bad\x00response" + "x" * 3000,
                 mailRetryRequested=True, mailRetryGeneration=2,
                 email={"to": "private@example.com"}, password="secret")
    result = public_delivery_state({"delivery_state_json": json.dumps(state), "mail_status": "PENDING"})
    assert result["needsReview"] is True
    assert result["mailTryCount"] == 0
    assert result["nextPollSeconds"] == 600
    assert len(result["mailErrorMessage"]) <= 2000
    assert "\x00" not in result["mailErrorMessage"]
    assert "secret" not in json.dumps(result)
    assert "private@example.com" not in json.dumps(result)
    assert "mailRetryRequested" not in result
    assert "mailRetryGeneration" not in result


def test_progress_rejects_malformed_counter_and_boolean_values():
    result = public_delivery_state({"delivery_state_json": {
        "mailTryCount": True, "mailPollCount": -1, "documentPollCount": "10",
        "needsReview": "true", "mailRetryUncertain": 1, "requestId": "x" * 256,
    }})
    assert result["mailTryCount"] is None
    assert result["mailPollCount"] is None
    assert result["documentPollCount"] is None
    assert result["mailRequestId"] is None
    assert result["needsReview"] is False
    assert result["mailRetryUncertain"] is False


def test_review_page_keeps_stable_script_and_hides_expired_download():
    state = dict(canQueue=False, localFilesRetained=False, transferStatus="RETRY_WAIT",
                 requestNumber="REQ", experimentCode="TEM", equipmentCode="TEM1", generatedAt="2026-11-04")
    html = _generated_report_page("report-1", "TEM", state).body.decode()
    assert '/assets/report_delivery_progress.js' in html
    assert 'data-status-url="/api/v1/reports/report-1"' in html
    assert '상태 새로고침' in html
    assert '로컬 파일 보관 기간이 만료' in html
    assert 'href="/api/v1/reports/report-1/package"' not in html
    assert 'LIMS 전송 큐에 등록했습니다' not in html

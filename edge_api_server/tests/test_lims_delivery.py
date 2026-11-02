from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import os

import pytest

from app.database import Database
from app.report_mail import mail_payload, validate_envelope
from app.report_management import _decorate_row, _purge_old_trash, cleanup_expired_reports, retry_transfer
from app.report_transfer_state import public_delivery_state
from tests.test_report_mail import embedded_image
from tests.test_report_management import PolicyDatabase, report_row, settings


def test_lims_html_is_derived_and_browser_recipient_readiness_is_ignored():
    source = '<p>분석 결과</p><img src="' + embedded_image() + '">'
    envelope = mail_payload('결과', '분석 결과 [이미지]', {'bodyHtml': source})
    envelope.update(to=['spoof@example.com'], recipientReady=True, htmlBody='<script>evil()</script>')
    validated = validate_envelope(envelope)
    assert validated['to'] == []
    assert validated['recipientReady'] is False
    assert 'data:image/png;base64,' in validated['htmlBody']
    assert 'cid:' not in validated['htmlBody']
    assert '<script>' not in validated['htmlBody']
    assert validate_envelope(validated) == validated


def test_delivery_progress_excludes_private_state_and_survives_invalid_json():
    row = {
        'document_status': 'COMPLETED', 'mail_status': 'PENDING',
        'document_completed_at': '2026-11-02 10:00:00',
        'delivery_state_json': json.dumps({
            'phase': 'MAIL_PENDING', 'axDocNum': 1001, 'regenSeq': 0,
            'fileNo': 55001, 'requestId': 'mail-id', 'alarmResultNo': 19300,
            'to': 'private@example.com', 'htmlBody': 'private body', 'apiKey': 'secret',
        }),
    }
    result = public_delivery_state(row)
    assert result['documentStatus'] == 'COMPLETED'
    assert result['mailStatus'] == 'PENDING'
    assert result['fileNo'] == 55001
    assert result['mailRequestId'] == 'mail-id'
    assert 'private' not in json.dumps(result)
    assert 'secret' not in json.dumps(result)
    assert public_delivery_state({'delivery_state_json': '{broken'})['fileNo'] is None
    assert public_delivery_state({'delivery_state_json': '[]'})['deliveryPhase'] is None


@pytest.mark.parametrize('status', ['COMPLETED', 'FAILED', 'CANCELLED', 'RETRY_WAIT'])
def test_registration_or_error_never_authorizes_cleanup_before_durable_ingestion(tmp_path, status):
    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    row = report_row(transfer_status=status, transfer_completed_at=old,
                     retention_until=old, document_status='PENDING', document_completed_at=None)
    result = _decorate_row(row, settings(tmp_path), include_artifacts=False)
    assert result['cleanupEligible'] is False
    assert result['autoCleanupEligible'] is False


def test_durable_document_can_expire_while_mail_waits_and_pin_still_wins(tmp_path):
    old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    row = report_row(transfer_status='RETRY_WAIT', document_status='COMPLETED',
                     document_completed_at=old, mail_status='PENDING')
    result = _decorate_row(row, settings(tmp_path), include_artifacts=False)
    assert result['cleanupEligible'] is True
    assert result['retentionPolicyKey'] == 'COMPLETED'
    assert result['mailStatus'] == 'PENDING'
    assert _decorate_row({**row, 'pinned': True}, settings(tmp_path), include_artifacts=False)['cleanupEligible'] is False
    recent = datetime.now(timezone.utc).isoformat()
    assert _decorate_row({**row, 'document_completed_at': recent}, settings(tmp_path), include_artifacts=False)['cleanupEligible'] is False


def test_cleanup_scans_beyond_one_thousand_without_skipping_deleted_rows(tmp_path):
    class CleanupDatabase(PolicyDatabase):
        def __init__(self):
            super().__init__()
            self.rows = {}
            self.reports = {f'report-{i:05}': report_row(report_id=f'report-{i:05}') for i in range(2005)}
            self.pages = []

        def list_report_cleanup_candidates(self, *, after_report_id, limit):
            self.pages.append(after_report_id)
            return [r for key, r in sorted(self.reports.items()) if key > after_report_id and not r['deleted_at']][:limit]

        def fetch_report_management(self, report_id):
            return self.reports[report_id]

        def mark_report_trashed(self, report_id, **kwargs):
            self.reports[report_id]['deleted_at'] = 'now'

    database = CleanupDatabase()
    results = cleanup_expired_reports(settings=settings(tmp_path), database=database)
    assert len(results) == 2005
    assert len({row['reportId'] for row in results}) == 2005
    assert database.pages == ['', 'report-00999', 'report-01999', 'report-02004']


def test_manual_retry_keeps_history_and_document_checkpoint_but_resets_error_budget():
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    connection.execute('''CREATE TABLE report_transfers (
        report_id TEXT, status TEXT, attempt_count INTEGER, failure_count INTEGER,
        next_retry_at TEXT, lease_owner TEXT, lease_until TEXT, completed_at TEXT,
        last_error_code TEXT, last_error_message TEXT, updated_at TEXT,
        delivery_state_json TEXT, document_completed_at TEXT)''')
    checkpoint = json.dumps({'phase': 'MAIL_PENDING', 'fileNo': 55001, 'requestId': 'stable'})
    connection.execute('''INSERT INTO report_transfers
        (report_id,status,attempt_count,failure_count,delivery_state_json,document_completed_at)
        VALUES (?,?,?,?,?,?)''', ('report-1', 'FAILED', 37, 5, checkpoint, '2026-11-02'))

    class Adapter:
        def execute(self, sql, params):
            return connection.execute(sql.replace('CURRENT_TIMESTAMP(6)', 'CURRENT_TIMESTAMP'), params)

    @contextmanager
    def transaction():
        with connection:
            yield Adapter()

    database = Database.__new__(Database)
    database.transaction = transaction
    assert database.retry_report_transfer('report-1') is True
    row = dict(connection.execute('SELECT * FROM report_transfers').fetchone())
    assert row['status'] == 'PENDING'
    assert row['attempt_count'] == 37
    assert row['failure_count'] == 0
    assert row['delivery_state_json'] == checkpoint
    assert row['document_completed_at'] == '2026-11-02'
    assert database.retry_report_transfer('report-1') is False
    connection.close()


def test_mail_retry_route_does_not_require_deleted_local_package():
    from types import SimpleNamespace
    class RetryDatabase:
        def retry_report_transfer(self, report_id):
            assert report_id == 'expired-report'
            return True
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(database=RetryDatabase())))
    assert retry_transfer(request, 'expired-report') == {'reportId': 'expired-report', 'status': 'PENDING'}


def test_legacy_trash_without_durable_confirmation_is_not_physically_deleted(tmp_path):
    for report_id in ['unconfirmed', 'durable', 'orphan']:
        path = tmp_path / '.report-trash' / report_id
        path.mkdir(parents=True)
        (path / 'report.zip').write_bytes(b'zip')
        old = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
        os.utime(path, (old, old))
    class TrashDatabase:
        def fetch_report_management(self, report_id):
            if report_id == 'orphan':
                return None
            return dict(transfer_status='COMPLETED',
                        document_status='COMPLETED' if report_id == 'durable' else None,
                        document_completed_at='2026-01-01' if report_id == 'durable' else None)
    assert _purge_old_trash(settings(tmp_path), database=TrashDatabase()) == 1
    assert (tmp_path / '.report-trash' / 'unconfirmed' / 'report.zip').is_file()
    assert (tmp_path / '.report-trash' / 'orphan' / 'report.zip').is_file()
    assert not (tmp_path / '.report-trash' / 'durable').exists()

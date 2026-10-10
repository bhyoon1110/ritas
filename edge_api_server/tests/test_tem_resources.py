"""Resource regressions use synthetic data and isolated preview apps only."""
from concurrent.futures import Future, ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from threading import BoundedSemaphore, Event
import asyncio
import time
import tracemalloc
import zipfile
import zlib

from fastapi.testclient import TestClient
from fastapi import UploadFile
from PIL import Image
import pytest

import app.ahn_web as tem
from app.errors import ApiException


def image_bytes(size=(80, 60)):
    buffer = BytesIO()
    Image.new("RGB", size, (40, 60, 80)).save(buffer, "TIFF")
    return buffer.getvalue()


def post_chunk(client, upload_id, data, *, total=None, offset=0, checksum=None, name="stem/sample.tif"):
    return client.post(f"/api/v1/tem/upload-sessions/{upload_id}/chunks", data={
        "relative_path": name, "offset": offset, "total_size": total or len(data),
        "chunk_index": 0, "chunk_count": 1,
        "chunk_crc32": checksum or f"{zlib.crc32(data):08x}",
    }, files={"file": ("chunk", data)})


def wait_job(client, payload):
    deadline = time.monotonic() + 8
    while payload["status"] not in {"failed", "completed"} and time.monotonic() < deadline:
        time.sleep(.03)
        payload = client.get(f'/api/v1/tem/report/jobs/{payload["jobId"]}').json()
    assert payload["status"] in {"failed", "completed"}
    return payload


def test_chunk_hard_limit_and_empty_chunk_preserve_partial_file(monkeypatch):
    monkeypatch.setattr(tem, "MAX_AHN_CHUNK_BYTES", 4)
    with TestClient(tem.create_tem_preview_app()) as client:
        uid = client.post('/api/v1/tem/upload-sessions').json()['uploadId']
        assert post_chunk(client, uid, b'abcd', total=12).status_code == 200
        session = tem._get_upload_session(uid)
        path = session.input_root / session.files['stem/sample.tif'].temp_path
        assert post_chunk(client, uid, b'12345', total=12, offset=4).status_code == 400
        assert path.read_bytes() == b'abcd'
        assert post_chunk(client, uid, b'', total=12, offset=4).json()['code'] == 'TEM_EMPTY_CHUNK'
        assert post_chunk(client, uid, b'xxxx', total=12, checksum='00000000').status_code == 400
        assert path.read_bytes() == b'abcd'
        assert not session.lock.locked()


def test_busy_upload_and_low_disk_do_not_corrupt_existing_data(monkeypatch):
    with TestClient(tem.create_tem_preview_app()) as client:
        uid = client.post('/api/v1/tem/upload-sessions').json()['uploadId']
        session = tem._get_upload_session(uid)
        with session.lock:
            response = post_chunk(client, uid, b'1234')
        assert response.status_code == 409
        assert response.json()['retryable'] is True
        monkeypatch.setattr(tem.shutil, 'disk_usage', lambda path: type('Usage', (), {'free': 0})())
        response = post_chunk(client, uid, b'1234')
        assert response.status_code == 507
        assert not list(session.input_root.rglob('*.part'))


def test_zip_limits_checked_before_decompression(monkeypatch, tmp_path):
    source = tmp_path / 'large.zip'
    with zipfile.ZipFile(source, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('stem/sample.tif', image_bytes())
    monkeypatch.setattr(tem, 'MAX_AHN_UPLOAD_TOTAL_BYTES', 100)
    monkeypatch.setattr(zipfile.ZipFile, 'open', lambda *a, **k: pytest.fail('must reject metadata before inflation'))
    assert '처리 한도' in tem._validate_zip_archive(source, source.name)[0]['reason']


def test_many_archives_share_expansion_budget_even_with_unknown_extension(monkeypatch, tmp_path):
    for index in range(2):
        with zipfile.ZipFile(tmp_path / f'{index}.payload', 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr(f'{index}.dat', b'a' * 6000)
    monkeypatch.setattr(tem, 'MAX_AHN_UPLOAD_TOTAL_BYTES', 10000)
    with pytest.raises(ApiException) as info:
        tem._validate_ahn_upload_files(tmp_path)
    assert info.value.code == 'TEM_EXPANDED_BUNDLE_TOO_LARGE'


def test_zip_count_and_office_size_limits(monkeypatch, tmp_path):
    source = tmp_path / 'office.docx'
    with zipfile.ZipFile(source, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        z.writestr('word/document.xml', 'x' * 4000)
    monkeypatch.setattr(tem, 'MAX_AHN_OFFICE_EXPANDED_BYTES', 1000)
    assert '처리 한도' in tem._validate_ooxml(source, '.docx', source.name)[0]['reason']
    monkeypatch.setattr(tem, 'MAX_AHN_UPLOAD_FILES', 1)
    assert '항목 수' in tem._validate_zip_archive(source, source.name)[0]['reason']


def test_pixel_limit_checks_loose_and_word_embedded_images(monkeypatch, tmp_path):
    monkeypatch.setattr(tem, 'MAX_AHN_IMAGE_PIXELS', 100)
    assert '해상도' in tem._validate_image(BytesIO(image_bytes()), 'big.tif')[0]['reason']
    source = tmp_path / 'report.docx'
    with zipfile.ZipFile(source, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        z.writestr('word/document.xml', '<document/>')
        z.writestr('word/media/image.tif', image_bytes())
    assert '해상도' in tem._validate_ooxml(source, '.docx', source.name)[0]['reason']


def test_zip_member_verification_does_not_allocate_the_entire_member(tmp_path):
    source = tmp_path / '32mb.zip'
    with zipfile.ZipFile(source, 'w') as z:
        with z.open('raw.dat', 'w') as member:
            for _ in range(32):
                member.write(b'x' * 1024 * 1024)
    tracemalloc.start()
    try:
        assert tem._validate_zip_archive(source, source.name) == []
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # Inspection probes use at most 9MB; no 32MB archive.read() buffer.
    assert peak < 24 * 1024 * 1024, peak
    assert not list(tmp_path.glob('rist-tem-verify-*'))


def test_chunk_staging_memory_is_bounded(tmp_path):
    root = tmp_path / 'input'
    root.mkdir()
    session = tem.AhnUploadSession('test', tmp_path, root, time.time(), time.time())
    data = b'x' * (8 * 1024 * 1024)
    upload = UploadFile(file=BytesIO(data), filename='raw.dat')
    tracemalloc.start()
    try:
        state = asyncio.run(tem._write_upload_chunk(session, relative_path='raw.dat', offset=0,
            total_size=len(data), chunk_index=0, chunk_count=1, chunk_crc32=f'{zlib.crc32(data):08x}', upload=upload))
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert state.completed
    assert peak < 5 * 1024 * 1024, peak
    assert (root / 'raw.dat').read_bytes() == data


def test_finalized_session_rejects_late_chunk_with_stale_reference(tmp_path):
    root = tmp_path / 'input'
    root.mkdir()
    session = tem.AhnUploadSession('test', tmp_path, root, time.time(), time.time(), finalized=True)
    upload = UploadFile(file=BytesIO(b'late'), filename='raw.dat')
    with pytest.raises(ApiException) as info:
        asyncio.run(tem._write_upload_chunk(session, upload=upload))
    assert info.value.code == 'TEM_UPLOAD_FINALIZED'
    assert upload.file.closed
    assert not list(root.iterdir())


def test_upload_close_failure_still_releases_session_lock(tmp_path):
    session = tem.AhnUploadSession('test', tmp_path, tmp_path, time.time(), time.time(), finalized=True)
    class BrokenClose:
        async def close(self):
            raise OSError('close failed')
    with pytest.raises(OSError, match='close failed'):
        asyncio.run(tem._write_upload_chunk(session, upload=BrokenClose()))
    assert not session.lock.locked()


def test_deferred_validation_returns_job_before_work_and_retries_same_job(monkeypatch):
    entered, release = Event(), Event()
    original = tem._validate_ahn_upload_files
    def slow(root):
        entered.set()
        assert release.wait(5)
        return original(root)
    monkeypatch.setattr(tem, '_validate_ahn_upload_files', slow)
    with TestClient(tem.create_tem_preview_app()) as client:
        uid = client.post('/api/v1/tem/upload-sessions').json()['uploadId']
        assert post_chunk(client, uid, image_bytes()).status_code == 200
        url = f'/api/v1/tem/upload-sessions/{uid}/complete?defer_validation=true'
        try:
            with ThreadPoolExecutor(2) as executor:
                requests = [executor.submit(client.post, url) for _ in range(2)]
                responses = [request.result(timeout=3) for request in requests]
            assert entered.wait(2)
            assert all(r.status_code == 200 for r in responses)
            assert responses[0].json()['jobId'] == responses[1].json()['jobId']
            assert responses[0].json()['status'] in {'queued', 'running'}
        finally:
            release.set()
        assert wait_job(client, responses[0].json())['status'] == 'completed'


def test_deferred_integrity_failure_never_runs_report_builder(monkeypatch):
    monkeypatch.setattr(tem, 'build_outputs', lambda **k: pytest.fail('invalid raw must not reach report builder'))
    with TestClient(tem.create_tem_preview_app()) as client:
        response = client.post('/api/v1/tem/analyze?defer_validation=true', files={'files': ('broken.tif', b'broken')})
        assert response.status_code == 200
        result = wait_job(client, response.json())
        assert result['status'] == 'failed'
        assert result['error']['code'] == 'TEM_UPLOAD_INTEGRITY_FAILED'
        assert result.get('downloads') is None


def test_bounded_queue_recovers_capacity_after_success_or_submission_error(monkeypatch, tmp_path):
    pending = []
    def submit(*args):
        future = Future()
        pending.append(future)
        return future
    monkeypatch.setattr(tem, '_ahn_pending_slots', BoundedSemaphore(1))
    monkeypatch.setattr(tem._ahn_report_executor, 'submit', submit)
    tem._submit_ahn_job(tmp_path, tmp_path)
    with pytest.raises(ApiException) as info:
        tem._submit_ahn_job(tmp_path, tmp_path)
    assert info.value.code == 'TEM_REPORT_QUEUE_FULL'
    pending[0].set_result(None)
    def fail(*args):
        raise RuntimeError('executor unavailable')
    monkeypatch.setattr(tem._ahn_report_executor, 'submit', fail)
    with pytest.raises(RuntimeError):
        tem._submit_ahn_job(tmp_path, tmp_path)
    monkeypatch.setattr(tem._ahn_report_executor, 'submit', submit)
    tem._submit_ahn_job(tmp_path, tmp_path)
    pending[-1].set_result(None)


def test_session_is_retained_when_report_queue_full(monkeypatch):
    slot = BoundedSemaphore(1)
    slot.acquire()
    monkeypatch.setattr(tem, '_ahn_pending_slots', slot)
    with TestClient(tem.create_tem_preview_app()) as client:
        uid = client.post('/api/v1/tem/upload-sessions').json()['uploadId']
        assert post_chunk(client, uid, image_bytes()).status_code == 200
        url = f'/api/v1/tem/upload-sessions/{uid}/complete?defer_validation=true'
        assert client.post(url).status_code == 503
        assert tem._get_upload_session(uid).files['stem/sample.tif'].completed
        slot.release()
        response = client.post(url)
        assert response.status_code == 200
        assert wait_job(client, response.json())['status'] == 'completed'

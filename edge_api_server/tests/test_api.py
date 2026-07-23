from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pymysql
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm_client import LocalLlmClient
from app.main import create_app
from app.models import CreateJobRequest
from app.report_worker import ReportWorker


def headers(idempotency_key: str | None = None) -> dict[str, str]:
    result = {"X-Request-Id": str(uuid4())}
    if idempotency_key:
        result["Idempotency-Key"] = idempotency_key
    return result


def create_client(tmp_path: Path, db: dict) -> TestClient:
    settings = Settings(
        storage_root=tmp_path / "jobs",
        db_host=db["host"],
        db_port=db["port"],
        db_name=db["name"],
        db_user=db["user"],
        db_password=db["password"],
        upload_expiry_hours=24,
        max_upload_bytes=1024 * 1024,
        supported_experiment_codes=frozenset({"XRD", "FT-IR"}),
    )
    return TestClient(create_app(settings))


def job_payload() -> dict:
    return {
        "pk": {
            "requestNumber": "REQ-2026-00123",
            "experimentCode": "XRD",
            "equipmentCode": "XRD-01",
            "operatorId": "user01",
        },
        "sourcePc": {
            "hostName": "LAB-PC-XRD-01",
            "declaredIpAddress": "10.10.20.31",
            "clientVersion": "1.0.0",
        },
    }


def seed_lims_request_search(db: dict) -> None:
    connection = pymysql.connect(
        host=db["host"],
        port=db["port"],
        user=db["user"],
        password=db["password"],
        database=db["name"],
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE lims_req_ax_search (
                    req_result_no BIGINT NOT NULL,
                    req_number VARCHAR(100),
                    req_date VARCHAR(32),
                    req_state INT NOT NULL,
                    req_state_name VARCHAR(100),
                    req_type_no BIGINT NOT NULL,
                    req_type_code VARCHAR(100),
                    req_type_name VARCHAR(100) NOT NULL,
                    project_code VARCHAR(100),
                    cust_req_name VARCHAR(255),
                    customer_no BIGINT,
                    customer_name VARCHAR(255),
                    req_user_no BIGINT,
                    req_user_name VARCHAR(255),
                    smp_result_no BIGINT NOT NULL,
                    smp_result_name VARCHAR(255) NOT NULL,
                    smp_result_state INT NOT NULL,
                    test_mtd_result_no BIGINT NOT NULL,
                    test_mtd_no BIGINT NOT NULL,
                    test_mtd_code VARCHAR(100),
                    test_mtd_name VARCHAR(255) NOT NULL,
                    test_state INT NOT NULL,
                    test_charger_name VARCHAR(255),
                    output_order INT,
                    synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO lims_req_ax_search (
                    req_result_no,
                    req_number,
                    req_date,
                    req_state,
                    req_state_name,
                    req_type_no,
                    req_type_code,
                    req_type_name,
                    project_code,
                    cust_req_name,
                    customer_no,
                    customer_name,
                    req_user_no,
                    req_user_name,
                    smp_result_no,
                    smp_result_name,
                    smp_result_state,
                    test_mtd_result_no,
                    test_mtd_no,
                    test_mtd_code,
                    test_mtd_name,
                    test_state,
                    test_charger_name,
                    output_order,
                    synced_at
                ) VALUES (
                    270846,
                    '2025M01309',
                    '2026-05-19',
                    12,
                    '시험완료',
                    123,
                    'M1',
                    '그룹사',
                    NULL,
                    '조용민',
                    NULL,
                    NULL,
                    102,
                    'EP_IF',
                    458465,
                    '시료',
                    15,
                    485993,
                    3912,
                    'A23141',
                    'XRD 데이터 해석',
                    9,
                    '이현재',
                    NULL,
                    '2026-07-03 18:10:35'
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO lims_req_ax_search (
                    req_result_no,
                    req_number,
                    req_date,
                    req_state,
                    req_state_name,
                    req_type_no,
                    req_type_code,
                    req_type_name,
                    project_code,
                    cust_req_name,
                    customer_no,
                    customer_name,
                    req_user_no,
                    req_user_name,
                    smp_result_no,
                    smp_result_name,
                    smp_result_state,
                    test_mtd_result_no,
                    test_mtd_no,
                    test_mtd_code,
                    test_mtd_name,
                    test_state,
                    test_charger_name,
                    output_order,
                    synced_at
                ) VALUES (
                    270849,
                    '2025M01312',
                    '2026-05-22',
                    8,
                    '접수',
                    126,
                    'M4',
                    '분석의뢰',
                    'P-TEM',
                    '코팅층 두께 분석',
                    11,
                    'RIST 고객',
                    105,
                    '이티이엠',
                    458468,
                    'TEM 시료',
                    3,
                    485996,
                    3915,
                    'B54123',
                    '투과전자현미경 코팅층 분석',
                    2,
                    '한분석',
                    1,
                    '2026-07-03 18:13:35'
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO lims_req_ax_search (
                    req_result_no,
                    req_number,
                    req_date,
                    req_state,
                    req_state_name,
                    req_type_no,
                    req_type_code,
                    req_type_name,
                    project_code,
                    cust_req_name,
                    customer_no,
                    customer_name,
                    req_user_no,
                    req_user_name,
                    smp_result_no,
                    smp_result_name,
                    smp_result_state,
                    test_mtd_result_no,
                    test_mtd_no,
                    test_mtd_code,
                    test_mtd_name,
                    test_state,
                    test_charger_name,
                    output_order,
                    synced_at
                ) VALUES (
                    270847,
                    '2025M01310',
                    '2026-05-20',
                    8,
                    '접수',
                    124,
                    'M2',
                    '분석의뢰',
                    'P-FTIR',
                    '첨가제 정성 분석',
                    9,
                    'RIST 고객',
                    103,
                    '김의뢰',
                    458466,
                    'FTIR 시료',
                    3,
                    485994,
                    3913,
                    'FTIR-QUAL',
                    'FT-IR 정성분석',
                    2,
                    '박분석',
                    1,
                    '2026-07-03 18:11:35'
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO lims_req_ax_search (
                    req_result_no,
                    req_number,
                    req_date,
                    req_state,
                    req_state_name,
                    req_type_no,
                    req_type_code,
                    req_type_name,
                    project_code,
                    cust_req_name,
                    customer_no,
                    customer_name,
                    req_user_no,
                    req_user_name,
                    smp_result_no,
                    smp_result_name,
                    smp_result_state,
                    test_mtd_result_no,
                    test_mtd_no,
                    test_mtd_code,
                    test_mtd_name,
                    test_state,
                    test_charger_name,
                    output_order,
                    synced_at
                ) VALUES (
                    270848,
                    '2025M01311',
                    '2026-05-21',
                    8,
                    '접수',
                    125,
                    'M3',
                    '분석의뢰',
                    'P-RAMAN',
                    'Raman 피크 분석',
                    10,
                    'RIST 고객',
                    104,
                    '이라만',
                    458467,
                    'Raman 시료',
                    3,
                    485995,
                    3914,
                    'RAMAN-QUAL',
                    'Raman 정성분석',
                    2,
                    '최분석',
                    1,
                    '2026-07-03 18:12:35'
                )
                """
            )
    finally:
        connection.close()


def test_create_job_accepts_no_legacy_bundle() -> None:
    request = CreateJobRequest.model_validate(job_payload())

    assert request.bundle is None


def test_file_crud_and_request_list(tmp_path: Path, mariadb: dict) -> None:
    seed_lims_request_search(mariadb)
    client = create_client(tmp_path, mariadb)
    payload = job_payload()
    created = client.post("/api/v1/jobs", json=payload, headers=headers(str(uuid4())))
    assert created.status_code == 201
    job_id = created.json()["jobId"]
    assert created.json()["reused"] is False

    reused = client.post("/api/v1/jobs", json=payload, headers=headers(str(uuid4())))
    assert reused.status_code == 200
    assert reused.json()["jobId"] == job_id
    assert reused.json()["status"] == "CREATED"
    assert reused.json()["reused"] is True

    first = b"first"
    first_digest = hashlib.sha256(first).hexdigest()
    upload_key = f"{job_id}:raw/sample.txt:{first_digest}"
    uploaded = client.post(
        f"/api/v1/jobs/{job_id}/files",
        files={"file": ("sample.txt", first, "text/plain")},
        data={"relativePath": "raw/sample.txt", "sizeBytes": str(len(first)), "sha256": first_digest},
        headers=headers(upload_key),
    )
    assert uploaded.status_code == 201

    second = b"second"
    second_digest = hashlib.sha256(second).hexdigest()
    replaced = client.put(
        f"/api/v1/jobs/{job_id}/files/raw/sample.txt",
        files={"file": ("sample.txt", second, "text/plain")},
        data={"sizeBytes": str(len(second)), "sha256": second_digest},
        headers=headers(str(uuid4())),
    )
    assert replaced.status_code == 201
    assert replaced.json()["sha256"] == second_digest

    listed = client.get(f"/api/v1/jobs/{job_id}/files", headers=headers())
    assert listed.status_code == 200
    assert listed.json()["files"][0]["sizeBytes"] == len(second)

    requests = client.get("/api/v1/requests", headers=headers())
    assert requests.status_code == 200
    all_items = requests.json()["items"]
    assert {item["experimentCode"] for item in all_items} == {
        "B54123",
        "FTIR-QUAL",
        "RAMAN-QUAL",
    }
    assert all("완료" not in (item["requestStateName"] or "") for item in all_items)

    completed_requests = client.get(
        "/api/v1/requests?includeCompleted=true", headers=headers()
    )
    assert completed_requests.status_code == 200
    completed_items = completed_requests.json()["items"]
    assert {item["experimentCode"] for item in completed_items} == {
        "A23141",
        "B54123",
        "FTIR-QUAL",
        "RAMAN-QUAL",
    }

    ftir_requests = client.get(
        "/api/v1/requests?experimentType=FT-IR", headers=headers()
    )
    assert ftir_requests.status_code == 200
    ftir_item = ftir_requests.json()["items"][0]
    assert ftir_requests.json()["experimentType"] == "FT-IR"
    assert ftir_item["requestNumber"] == "2025M01310"
    assert ftir_item["experimentCode"] == "FTIR-QUAL"
    assert ftir_item["experimentName"] == "FT-IR 정성분석"
    assert ftir_item["sampleName"] == "FTIR 시료"
    assert ftir_item["testChargerName"] == "박분석"

    raman_requests = client.get(
        "/api/v1/requests?experimentType=RAMAN", headers=headers()
    )
    assert raman_requests.status_code == 200
    raman_item = raman_requests.json()["items"][0]
    assert raman_item["requestNumber"] == "2025M01311"
    assert raman_item["experimentCode"] == "RAMAN-QUAL"
    assert raman_item["experimentName"] == "Raman 정성분석"
    assert raman_item["sampleName"] == "Raman 시료"
    assert raman_item["testChargerName"] == "최분석"

    xrd_requests = client.get(
        "/api/v1/requests?experimentType=XRD", headers=headers()
    )
    assert xrd_requests.status_code == 200
    assert xrd_requests.json()["items"] == []

    completed_xrd_requests = client.get(
        "/api/v1/requests?experimentType=XRD&includeCompleted=true",
        headers=headers(),
    )
    assert completed_xrd_requests.status_code == 200
    completed_xrd_item = completed_xrd_requests.json()["items"][0]
    assert completed_xrd_item["requestNumber"] == "2025M01309"
    assert completed_xrd_item["experimentCode"] == "A23141"
    assert completed_xrd_item["experimentName"] == "XRD 데이터 해석"

    tem_requests = client.get(
        "/api/v1/requests?experimentType=TEM", headers=headers()
    )
    assert tem_requests.status_code == 200
    tem_item = tem_requests.json()["items"][0]
    assert tem_item["requestNumber"] == "2025M01312"
    assert tem_item["experimentCode"] == "B54123"
    assert tem_item["experimentName"] == "투과전자현미경 코팅층 분석"
    assert tem_item["testChargerName"] == "한분석"

    deleted = client.delete(
        f"/api/v1/jobs/{job_id}/files/raw/sample.txt",
        headers=headers(str(uuid4())),
    )
    assert deleted.status_code == 200
    assert client.get(f"/api/v1/jobs/{job_id}/files", headers=headers()).json()["files"] == []

    reuploaded = client.post(
        f"/api/v1/jobs/{job_id}/files",
        files={"file": ("sample.txt", first, "text/plain")},
        data={
            "relativePath": "raw/sample.txt",
            "sizeBytes": str(len(first)),
            "sha256": first_digest,
        },
        headers=headers(upload_key),
    )
    assert reuploaded.status_code == 201
    reupload_listed = client.get(f"/api/v1/jobs/{job_id}/files", headers=headers())
    assert reupload_listed.status_code == 200
    assert reupload_listed.json()["files"][0]["relativePath"] == "raw/sample.txt"


def test_full_upload_and_report_flow(tmp_path: Path, mariadb: dict) -> None:
    client = create_client(tmp_path, mariadb)
    content = b"#Intensity_unit=cps\n5.000 1115.000\n"
    digest = hashlib.sha256(content).hexdigest()

    create_key = str(uuid4())
    create_response = client.post(
        "/api/v1/jobs",
        json=job_payload(),
        headers=headers(create_key),
    )
    assert create_response.status_code == 201
    job_id = create_response.json()["jobId"]

    repeated = client.post(
        "/api/v1/jobs",
        json=job_payload(),
        headers=headers(create_key),
    )
    assert repeated.status_code == 201
    assert repeated.json()["jobId"] == job_id

    upload_response = client.post(
        f"/api/v1/jobs/{job_id}/files",
        files={"file": ("Mix2.txt", content, "text/plain")},
        data={
            "relativePath": "raw/Mix2.txt",
            "sizeBytes": str(len(content)),
            "sha256": digest,
        },
        headers=headers(f"{job_id}:raw/Mix2.txt:{digest}"),
    )
    assert upload_response.status_code == 201

    complete_response = client.post(
        f"/api/v1/jobs/{job_id}/uploads/complete",
        json={
            "fileCount": 1,
            "totalSizeBytes": len(content),
            "files": [
                {
                    "relativePath": "raw/Mix2.txt",
                    "sizeBytes": len(content),
                    "sha256": digest,
                }
            ],
        },
        headers=headers(f"{job_id}:uploads-complete"),
    )
    assert complete_response.status_code == 200
    assert complete_response.json()["status"] == "FILES_VERIFIED"

    blocked_update = client.put(
        f"/api/v1/jobs/{job_id}/files/raw/Mix2.txt",
        files={"file": ("Mix2.txt", content, "text/plain")},
        data={"sizeBytes": str(len(content)), "sha256": digest},
        headers=headers(str(uuid4())),
    )
    assert blocked_update.status_code == 409
    assert blocked_update.json()["code"] == "JOB_STATE_CONFLICT"

    report_response = client.post(
        f"/api/v1/jobs/{job_id}/report",
        json={
            "options": {
                "reportFormat": "PPTX",
                "includeRawFiles": False,
            }
        },
        headers=headers(f"{job_id}:generate-report"),
    )
    assert report_response.status_code == 202
    assert report_response.json()["status"] == "QUEUED"

    status_response = client.get(
        f"/api/v1/jobs/{job_id}", headers=headers()
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "QUEUED"

    queue_files = list((tmp_path / "jobs").rglob("report-request.json"))
    manifests = list((tmp_path / "jobs").rglob("manifest.json"))
    assert len(queue_files) == 1
    assert len(manifests) == 1


def test_report_regeneration_signal_is_received_without_execution(
    tmp_path: Path, mariadb: dict
) -> None:
    client = create_client(tmp_path, mariadb)
    database = client.app.state.database
    report_id = str(uuid4())
    database.register_report_run(
        report_id=report_id,
        source_job_id=None,
        request_number="REQ-2026-REGENERATE",
        experiment_code="XRD",
        equipment_code="XRD-01",
        operator_id="spring-boot",
        storage_key="RIST_REPORTS",
        package_relative_path=f"web-reports/XRD/{report_id}/report-package.zip",
        package_file_name="report-package.zip",
        package_size_bytes=1024,
        package_sha256="a" * 64,
        report_options_json=None,
        generated_at="2026-07-23T10:00:00+09:00",
    )

    idempotency_key = str(uuid4())
    payload = {
        "requestedAt": "2026-07-23T10:30:00+09:00",
        "requestedBy": "local-spring-boot",
        "reason": "태블릿에서 재생성 요청",
        "prompt": "고객 요약을 세 문장으로 줄여 보고서를 다시 생성해 주세요.",
    }
    received = client.post(
        f"/api/v1/reports/{report_id}/regenerate",
        json=payload,
        headers=headers(idempotency_key),
    )
    assert received.status_code == 202
    assert received.json() == {
        "signalId": received.json()["signalId"],
        "reportId": report_id,
        "sourceJobId": None,
        "status": "RECEIVED",
        "receivedAt": received.json()["receivedAt"],
        "executionQueued": False,
    }
    assert database.fetch_report_transfer_for_report(report_id) is None
    regeneration = database.fetch_report_regeneration_request(
        received.json()["signalId"]
    )
    assert regeneration is not None
    assert regeneration["prompt"] == payload["prompt"]

    repeated = client.post(
        f"/api/v1/reports/{report_id}/regenerate",
        json=payload,
        headers=headers(idempotency_key),
    )
    assert repeated.status_code == 202
    assert repeated.json() == received.json()

    reused = client.post(
        f"/api/v1/reports/{report_id}/regenerate",
        json={**payload, "reason": "다른 재생성 요청"},
        headers=headers(idempotency_key),
    )
    assert reused.status_code == 409
    assert reused.json()["code"] == "IDEMPOTENCY_KEY_REUSED"

    missing = client.post(
        f"/api/v1/reports/{uuid4()}/regenerate",
        json={"prompt": "보고서를 다시 생성해 주세요."},
        headers=headers(str(uuid4())),
    )
    assert missing.status_code == 404

    missing_prompt = client.post(
        f"/api/v1/reports/{report_id}/regenerate",
        json={"reason": "프롬프트 누락"},
        headers=headers(str(uuid4())),
    )
    assert missing_prompt.status_code == 400
    assert missing_prompt.json()["code"] == "REQUEST_VALIDATION_FAILED"

    blank_prompt = client.post(
        f"/api/v1/reports/{report_id}/regenerate",
        json={"prompt": "   "},
        headers=headers(str(uuid4())),
    )
    assert blank_prompt.status_code == 400
    assert blank_prompt.json()["code"] == "REQUEST_VALIDATION_FAILED"
    assert missing.json()["code"] == "REPORT_NOT_FOUND"
    assert not list((tmp_path / "jobs").rglob("report-request.json"))


def test_rejects_hash_mismatch(tmp_path: Path, mariadb: dict) -> None:
    client = create_client(tmp_path, mariadb)
    content = b"test"
    create_response = client.post(
        "/api/v1/jobs",
        json=job_payload(),
        headers=headers(str(uuid4())),
    )
    job_id = create_response.json()["jobId"]

    response = client.post(
        f"/api/v1/jobs/{job_id}/files",
        files={"file": ("result.txt", content, "text/plain")},
        data={
            "relativePath": "result.txt",
            "sizeBytes": str(len(content)),
            "sha256": "0" * 64,
        },
        headers=headers(str(uuid4())),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "FILE_HASH_MISMATCH"


def test_requires_headers(tmp_path: Path, mariadb: dict) -> None:
    client = create_client(tmp_path, mariadb)
    response = client.post("/api/v1/jobs", json=job_payload())
    assert response.status_code == 400
    assert response.json()["code"] in {
        "MISSING_REQUEST_ID",
        "MISSING_IDEMPOTENCY_KEY",
    }


def test_rejects_oversized_idempotency_key(
    tmp_path: Path, mariadb: dict
) -> None:
    client = create_client(tmp_path, mariadb)
    response = client.post(
        "/api/v1/jobs",
        json=job_payload(),
        headers={"X-Request-Id": str(uuid4()), "Idempotency-Key": "k" * 129},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_IDEMPOTENCY_KEY"


def test_rejects_idempotency_key_with_different_request(
    tmp_path: Path, mariadb: dict
) -> None:
    client = create_client(tmp_path, mariadb)
    key = str(uuid4())
    first = client.post(
        "/api/v1/jobs",
        json=job_payload(),
        headers=headers(key),
    )
    assert first.status_code == 201

    changed = job_payload()
    changed["pk"]["requestNumber"] = "REQ-2026-99999"
    second = client.post(
        "/api/v1/jobs",
        json=changed,
        headers=headers(key),
    )
    assert second.status_code == 409
    assert second.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_expired_upload_returns_gone(tmp_path: Path, mariadb: dict) -> None:
    settings = Settings(
        storage_root=tmp_path / "jobs",
        db_host=mariadb["host"],
        db_port=mariadb["port"],
        db_name=mariadb["name"],
        db_user=mariadb["user"],
        db_password=mariadb["password"],
        upload_expiry_hours=-1,
        max_upload_bytes=1024,
        supported_experiment_codes=frozenset(),
    )
    client = TestClient(create_app(settings))
    content = b"expired"
    digest = hashlib.sha256(content).hexdigest()
    create_response = client.post(
        "/api/v1/jobs",
        json=job_payload(),
        headers=headers(str(uuid4())),
    )
    job_id = create_response.json()["jobId"]

    upload_response = client.post(
        f"/api/v1/jobs/{job_id}/files",
        files={"file": ("expired.txt", content, "text/plain")},
        data={
            "relativePath": "expired.txt",
            "sizeBytes": str(len(content)),
            "sha256": digest,
        },
        headers=headers(str(uuid4())),
    )
    assert upload_response.status_code == 410
    assert upload_response.json()["code"] == "UPLOAD_EXPIRED"

    status_response = client.get(
        f"/api/v1/jobs/{job_id}", headers=headers()
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "UPLOAD_EXPIRED"


def test_worker_calls_local_llm_and_saves_report(
    tmp_path: Path, mariadb: dict
) -> None:
    client = create_client(tmp_path, mariadb)
    content = b"xrd data"
    digest = hashlib.sha256(content).hexdigest()
    create_response = client.post(
        "/api/v1/jobs",
        json=job_payload(),
        headers=headers(str(uuid4())),
    )
    job_id = create_response.json()["jobId"]
    client.post(
        f"/api/v1/jobs/{job_id}/files",
        files={"file": ("Mix2.txt", content, "text/plain")},
        data={
            "relativePath": "raw/Mix2.txt",
            "sizeBytes": str(len(content)),
            "sha256": digest,
        },
        headers=headers(str(uuid4())),
    )
    client.post(
        f"/api/v1/jobs/{job_id}/uploads/complete",
        json={
            "fileCount": 1,
            "totalSizeBytes": len(content),
            "files": [
                {
                    "relativePath": "raw/Mix2.txt",
                    "sizeBytes": len(content),
                    "sha256": digest,
                }
            ],
        },
        headers=headers(str(uuid4())),
    )
    client.post(
        f"/api/v1/jobs/{job_id}/report",
        json={"options": {"reportFormat": "PPTX"}},
        headers=headers(str(uuid4())),
    )

    database = client.app.state.database
    settings = client.app.state.settings
    job = database.fetch_job(job_id)
    assert job is not None
    job_root = settings.storage_root / job["root_relative_path"]
    analysis_path = job_root / "processed" / "analysis-result.json"
    analysis_path.write_text(
        json.dumps(
            {
                "sample": "Mix2",
                "peakCount": 3,
                "finding": "TiO2 후보 피크가 관찰됨",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (job_root / "processed" / "spectrum.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    captured: dict = {}

    def llm_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {
                            "id": "local-model",
                            "object": "model",
                            "max_model_len": 8192,
                        }
                    ],
                },
            )
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "summary": "요약 문장 1. 요약 문장 2. 요약 문장 3.",
                                    "narrative": "보조 설명입니다.",
                                    "caption": "발표용 캡션",
                                },
                                ensure_ascii=False,
                            ),
                        }
                    }
                ]
            },
        )

    llm_client = LocalLlmClient(
        "http://127.0.0.1:8001",
        "local-model",
        10,
        0.2,
        transport=httpx.MockTransport(llm_handler),
    )
    worker = ReportWorker(settings, database, llm_client)
    try:
        assert worker.run_once() is True
    finally:
        llm_client.close()

    updated = database.fetch_job(job_id)
    assert updated is not None
    assert updated["status"] == "COMPLETED"
    assert updated["progress"] == 100
    assert updated["completed_at"] is not None
    report_run = database.fetch_report_run_by_source_job(job_id)
    assert report_run is not None
    assert report_run["generation_status"] == "READY"
    assert report_run["package_relative_path"].endswith("report/report-package.zip")
    transfer = database.fetch_report_transfer_for_report(report_run["report_id"])
    assert transfer is not None
    assert transfer["status"] == "PENDING"
    assert transfer["attempt_count"] == 0
    assert captured["url"].endswith("/v1/chat/completions")
    assert captured["body"]["model"] == "local-model"
    assert captured["body"]["temperature"] == 0.2
    assert captured["body"]["response_format"] == {"type": "json_object"}
    user_content = captured["body"]["messages"][1]["content"]
    assert isinstance(user_content, list)
    assert "TiO2 후보 피크" in user_content[0]["text"]
    assert user_content[1]["type"] == "image_url"
    assert user_content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert (job_root / "logs" / "llm-request.json").exists()
    assert (job_root / "logs" / "llm-response.json").exists()
    assert (job_root / "report" / "report.json").exists()
    assert (job_root / "report" / "report.md").exists()
    assert (job_root / "report" / "report.pptx").exists()
    report_doc = json.loads(
        (job_root / "report" / "report.json").read_text(encoding="utf-8")
    )
    assert report_doc["llm"]["used"] is True
    summary_section = next(
        section
        for section in report_doc["sections"]
        if section["sectionId"] == "summary"
    )
    assert summary_section["source"] == "llm"
    assert summary_section["paragraphs"][0].startswith("요약 문장 1")
    logged_request = json.loads(
        (job_root / "logs" / "llm-request.json").read_text(encoding="utf-8")
    )
    assert "<base64 omitted:" in (
        logged_request["messages"][1]["content"][1]["image_url"]["url"]
    )


def test_worker_completes_report_without_llm(
    tmp_path: Path, mariadb: dict
) -> None:
    client = create_client(tmp_path, mariadb)
    content = b"xrd data"
    digest = hashlib.sha256(content).hexdigest()
    create_response = client.post(
        "/api/v1/jobs",
        json=job_payload(),
        headers=headers(str(uuid4())),
    )
    job_id = create_response.json()["jobId"]
    client.post(
        f"/api/v1/jobs/{job_id}/files",
        files={"file": ("Mix2.txt", content, "text/plain")},
        data={
            "relativePath": "raw/Mix2.txt",
            "sizeBytes": str(len(content)),
            "sha256": digest,
        },
        headers=headers(str(uuid4())),
    )
    client.post(
        f"/api/v1/jobs/{job_id}/uploads/complete",
        json={
            "fileCount": 1,
            "totalSizeBytes": len(content),
            "files": [
                {
                    "relativePath": "raw/Mix2.txt",
                    "sizeBytes": len(content),
                    "sha256": digest,
                }
            ],
        },
        headers=headers(str(uuid4())),
    )
    client.post(
        f"/api/v1/jobs/{job_id}/report",
        json={"options": {"reportFormat": "PPTX"}},
        headers=headers(str(uuid4())),
    )

    database = client.app.state.database
    settings = client.app.state.settings
    job = database.fetch_job(job_id)
    assert job is not None
    job_root = settings.storage_root / job["root_relative_path"]
    analysis_path = job_root / "processed" / "analysis-result.json"
    analysis_path.parent.mkdir(parents=True, exist_ok=True)
    analysis_path.write_text(
        json.dumps(
            {"sample": "Mix2", "finding": "TiO2 후보 피크"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def llm_down(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("LLM unreachable")

    llm_client = LocalLlmClient(
        "http://127.0.0.1:8001",
        "local-model",
        10,
        0.2,
        validate_model=False,
        transport=httpx.MockTransport(llm_down),
    )
    worker = ReportWorker(settings, database, llm_client)
    try:
        assert worker.run_once() is True
    finally:
        llm_client.close()

    updated = database.fetch_job(job_id)
    assert updated is not None
    assert updated["status"] == "COMPLETED"
    assert updated["progress"] == 100
    report_run = database.fetch_report_run_by_source_job(job_id)
    assert report_run is not None
    transfer = database.fetch_report_transfer_for_report(report_run["report_id"])
    assert transfer is not None
    assert transfer["status"] == "PENDING"
    assert (job_root / "report" / "report.pptx").exists()
    report_doc = json.loads(
        (job_root / "report" / "report.json").read_text(encoding="utf-8")
    )
    assert report_doc["llm"]["used"] is False
    assert report_doc["llm"]["error"] is not None
    summary_section = next(
        section
        for section in report_doc["sections"]
        if section["sectionId"] == "summary"
    )
    assert summary_section["source"] == "rule"
    assert summary_section["paragraphs"][0]



def test_worker_fails_without_structured_analysis(
    tmp_path: Path, mariadb: dict
) -> None:
    client = create_client(tmp_path, mariadb)
    content = b"xrd data"
    digest = hashlib.sha256(content).hexdigest()
    create_response = client.post(
        "/api/v1/jobs",
        json=job_payload(),
        headers=headers(str(uuid4())),
    )
    job_id = create_response.json()["jobId"]
    client.post(
        f"/api/v1/jobs/{job_id}/files",
        files={"file": ("Mix2.txt", content, "text/plain")},
        data={
            "relativePath": "raw/Mix2.txt",
            "sizeBytes": str(len(content)),
            "sha256": digest,
        },
        headers=headers(str(uuid4())),
    )
    client.post(
        f"/api/v1/jobs/{job_id}/uploads/complete",
        json={
            "fileCount": 1,
            "totalSizeBytes": len(content),
            "files": [
                {
                    "relativePath": "raw/Mix2.txt",
                    "sizeBytes": len(content),
                    "sha256": digest,
                }
            ],
        },
        headers=headers(str(uuid4())),
    )
    client.post(
        f"/api/v1/jobs/{job_id}/report",
        json={},
        headers=headers(str(uuid4())),
    )

    def unexpected_call(_: httpx.Request) -> httpx.Response:
        raise AssertionError("분석 JSON이 없으면 LLM을 호출하면 안 됩니다.")

    llm_client = LocalLlmClient(
        "http://127.0.0.1:8001",
        "local-model",
        10,
        0.2,
        transport=httpx.MockTransport(unexpected_call),
    )
    worker = ReportWorker(
        client.app.state.settings,
        client.app.state.database,
        llm_client,
    )
    try:
        assert worker.run_once() is True
    finally:
        llm_client.close()

    updated = client.app.state.database.fetch_job(job_id)
    assert updated is not None
    assert updated["status"] == "FAILED"
    error = json.loads(updated["error_json"])
    assert error["code"] == "ANALYSIS_RESULT_NOT_FOUND"

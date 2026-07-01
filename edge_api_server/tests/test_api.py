from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import httpx
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


def test_create_job_accepts_no_legacy_bundle() -> None:
    request = CreateJobRequest.model_validate(job_payload())

    assert request.bundle is None


def test_file_crud_and_request_list(tmp_path: Path, mariadb: dict) -> None:
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
    uploaded = client.post(
        f"/api/v1/jobs/{job_id}/files",
        files={"file": ("sample.txt", first, "text/plain")},
        data={"relativePath": "raw/sample.txt", "sizeBytes": str(len(first)), "sha256": first_digest},
        headers=headers(str(uuid4())),
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
    assert requests.json()["items"][0]["requestNumber"] == "REQ-2026-00123"

    deleted = client.delete(
        f"/api/v1/jobs/{job_id}/files/raw/sample.txt",
        headers=headers(str(uuid4())),
    )
    assert deleted.status_code == 200
    assert client.get(f"/api/v1/jobs/{job_id}/files", headers=headers()).json()["files"] == []


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

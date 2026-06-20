from __future__ import annotations

import httpx
import pytest

from app.spring_callback import SpringCallbackClient, SpringCallbackError


def _job() -> dict[str, str]:
    return {
        "job_id": "job-1",
        "request_number": "REQ-1",
        "experiment_code": "FT-IR",
        "equipment_code": "FTIR-01",
        "operator_id": "user-1",
    }


def test_spring_callback_sends_zip_multipart(tmp_path) -> None:
    package = tmp_path / "report-package.zip"
    package.write_bytes(b"PK\x03\x04")
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["idempotency"] = request.headers["Idempotency-Key"]
        captured["content_type"] = request.headers["Content-Type"]
        captured["body"] = request.content.decode("latin-1")
        return httpx.Response(201)

    client = SpringCallbackClient(
        "http://127.0.0.1:8080/api/v1/results",
        10,
        1,
        transport=httpx.MockTransport(handler),
    )
    try:
        client.deliver(_job(), package)
    finally:
        client.close()

    assert captured["idempotency"] == "job-1:report-package"
    assert "multipart/form-data" in captured["content_type"]
    assert "report-package.zip" in captured["body"]
    assert "REQ-1" in captured["body"]
    assert "8dcc7e601606217f3b754766511182a916b17e9a26a94c9d887104eba92e9bb2" in captured["body"]


def test_spring_callback_raises_for_non_retryable_response(tmp_path) -> None:
    package = tmp_path / "report-package.zip"
    package.write_bytes(b"PK\x03\x04")
    client = SpringCallbackClient(
        "http://127.0.0.1:8080/api/v1/results",
        10,
        1,
        transport=httpx.MockTransport(lambda _: httpx.Response(400)),
    )
    try:
        with pytest.raises(SpringCallbackError) as raised:
            client.deliver(_job(), package)
    finally:
        client.close()

    assert raised.value.code == "SPRING_CALLBACK_HTTP_ERROR"
    assert raised.value.retryable is False

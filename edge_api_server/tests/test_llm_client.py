from __future__ import annotations

import httpx
import pytest

from app.llm_client import LlmError, LocalLlmClient


def test_model_discovery_reads_vllm_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {
                        "id": "gemma4-e4b",
                        "object": "model",
                        "max_model_len": 8192,
                        "owned_by": "vllm",
                    }
                ],
            },
        )

    client = LocalLlmClient(
        "http://127.0.0.1:8001",
        "gemma4-e4b",
        10,
        0.1,
        1200,
        True,
        transport=httpx.MockTransport(handler),
    )
    try:
        model = client.get_model_info()
    finally:
        client.close()

    assert model["id"] == "gemma4-e4b"
    assert model["max_model_len"] == 8192


def test_model_discovery_rejects_unknown_model() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"object": "list", "data": [{"id": "gemma4-e4b"}]},
        )

    client = LocalLlmClient(
        "http://127.0.0.1:8001",
        "missing-model",
        10,
        0.1,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(LlmError) as raised:
            client.get_model_info()
    finally:
        client.close()

    assert raised.value.code == "LLM_MODEL_NOT_FOUND"


def test_context_error_message_is_preserved() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "gemma4-e4b", "max_model_len": 8192}
                    ]
                },
            )
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": (
                        "This model's maximum context length is 8192 tokens. "
                        "Please reduce the length of the input prompt."
                    )
                }
            },
        )

    client = LocalLlmClient(
        "http://127.0.0.1:8001",
        "gemma4-e4b",
        10,
        0.1,
        transport=httpx.MockTransport(handler),
    )
    payload = client.build_request_payload("system", "user")
    try:
        with pytest.raises(LlmError) as raised:
            client.chat_completion(payload)
    finally:
        client.close()

    assert raised.value.code == "LLM_CONTEXT_LENGTH_EXCEEDED"
    assert "8192" in raised.value.message

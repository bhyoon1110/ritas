from __future__ import annotations

from typing import Any

import httpx


class LlmError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class LocalLlmClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: float,
        temperature: float,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.endpoint = f"{base_url.rstrip('/')}/v1/chat/completions"
        self.model = model
        self.temperature = temperature
        self.client = httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    def close(self) -> None:
        self.client.close()

    def build_request_payload(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
        }

    def chat_completion(
        self,
        request_payload: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        try:
            response = self.client.post(self.endpoint, json=request_payload)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LlmError(
                "LLM_TIMEOUT",
                "로컬 LLM 응답 시간이 초과되었습니다.",
                retryable=True,
            ) from exc
        except httpx.NetworkError as exc:
            raise LlmError(
                "LLM_CONNECTION_FAILED",
                "로컬 LLM 서버에 연결할 수 없습니다.",
                retryable=True,
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise LlmError(
                "LLM_HTTP_ERROR",
                f"로컬 LLM 서버가 HTTP {status}를 반환했습니다.",
                retryable=status in {408, 429, 500, 502, 503, 504},
            ) from exc

        try:
            response_payload = response.json()
            content = response_payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LlmError(
                "LLM_RESPONSE_INVALID",
                "로컬 LLM 응답 형식이 OpenAI 호환 형식이 아닙니다.",
                retryable=False,
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise LlmError(
                "LLM_RESPONSE_EMPTY",
                "로컬 LLM이 빈 보고서 내용을 반환했습니다.",
                retryable=False,
            )
        return content.strip(), response_payload

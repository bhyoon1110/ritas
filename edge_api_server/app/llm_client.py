from __future__ import annotations

from copy import deepcopy
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
        max_tokens: int = 1200,
        validate_model: bool = True,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        root = base_url.rstrip("/")
        self.endpoint = f"{root}/v1/chat/completions"
        self.models_endpoint = f"{root}/v1/models"
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.validate_model = validate_model
        self._model_info: dict[str, Any] | None = None
        self.client = httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    def close(self) -> None:
        self.client.close()

    def get_model_info(self, *, force: bool = False) -> dict[str, Any]:
        if self._model_info is not None and not force:
            return self._model_info
        try:
            response = self.client.get(self.models_endpoint)
            response.raise_for_status()
            payload = response.json()
            models = payload["data"]
        except httpx.TimeoutException as exc:
            raise LlmError(
                "LLM_MODELS_TIMEOUT",
                "로컬 LLM 모델 조회 시간이 초과되었습니다.",
                retryable=True,
            ) from exc
        except httpx.NetworkError as exc:
            raise LlmError(
                "LLM_CONNECTION_FAILED",
                "로컬 LLM 서버에 연결할 수 없습니다.",
                retryable=True,
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise LlmError(
                "LLM_MODELS_HTTP_ERROR",
                f"로컬 LLM 모델 조회가 HTTP {exc.response.status_code}를 반환했습니다.",
                retryable=exc.response.status_code >= 500,
            ) from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise LlmError(
                "LLM_MODELS_RESPONSE_INVALID",
                "로컬 LLM의 /v1/models 응답 형식이 올바르지 않습니다.",
                retryable=False,
            ) from exc

        for model in models:
            if isinstance(model, dict) and model.get("id") == self.model:
                self._model_info = model
                return model
        available = [
            model.get("id")
            for model in models
            if isinstance(model, dict) and model.get("id")
        ]
        raise LlmError(
            "LLM_MODEL_NOT_FOUND",
            f"로컬 LLM에 모델 '{self.model}'이 없습니다. "
            f"사용 가능 모델: {', '.join(available) or '(없음)'}",
            retryable=False,
        )

    def build_request_payload(
        self,
        system_prompt: str,
        user_content: str | list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

    def chat_completion(
        self,
        request_payload: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        if self.validate_model:
            self.get_model_info()
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
            error_message = self._error_message(exc.response)
            if status == 400 and "maximum context length" in error_message.lower():
                raise LlmError(
                    "LLM_CONTEXT_LENGTH_EXCEEDED",
                    error_message,
                    retryable=False,
                ) from exc
            raise LlmError(
                "LLM_HTTP_ERROR",
                f"로컬 LLM 서버가 HTTP {status}를 반환했습니다: {error_message}",
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

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
            message = payload.get("error", {}).get("message")
            if isinstance(message, str) and message:
                return message
        except ValueError:
            pass
        text = response.text.strip()
        return text or "상세 오류 없음"

    @staticmethod
    def request_for_log(request_payload: dict[str, Any]) -> dict[str, Any]:
        sanitized = deepcopy(request_payload)
        for message in sanitized.get("messages", []):
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for item in content:
                image_url = item.get("image_url")
                if not isinstance(image_url, dict):
                    continue
                url = image_url.get("url")
                if isinstance(url, str) and url.startswith("data:"):
                    header, _, data = url.partition(",")
                    image_url["url"] = f"{header},<base64 omitted: {len(data)} chars>"
        return sanitized

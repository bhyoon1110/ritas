"""RIST 공통 LLM 호출 모듈.

- LlmClient: OpenAI 호환 로컬 LLM(chat.completions) 클라이언트 (httpx 필요)
- payload 헬퍼: 요청 본문 생성/정제/컨텍스트 예산 추정 (httpx 불필요)
- LlmError: 표준 오류 타입

각 프로젝트(lim, sune, ahn 등)는 LlmClient를 공통으로 사용하되,
프롬프트/JSON 포맷은 프로젝트별 모듈에서 payload 헬퍼로 직접 구성한다.

client는 httpx 의존성을 끌어오므로, 페이로드만 다루는 코드가 httpx 없이도
동작하도록 LlmClient는 지연(lazy) 로딩한다.
"""

from __future__ import annotations

from typing import Any

from .errors import LlmError
from .payload import (
    build_multimodal_content,
    build_request_payload,
    ensure_context_budget,
    estimate_context_tokens,
    sanitize_request_for_log,
)

__all__ = [
    "LlmError",
    "LlmClient",
    "build_request_payload",
    "build_multimodal_content",
    "sanitize_request_for_log",
    "estimate_context_tokens",
    "ensure_context_budget",
]


def __getattr__(name: str) -> Any:
    if name == "LlmClient":
        from .client import LlmClient

        return LlmClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

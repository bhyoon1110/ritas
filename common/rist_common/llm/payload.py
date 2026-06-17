"""LLM 요청 페이로드 생성/정제/컨텍스트 예산 추정 헬퍼.

이 모듈은 httpx에 의존하지 않으므로(순수 dict 가공) 각 프로젝트에서
HTTP 클라이언트 없이도 요청 본문을 구성하고 검증할 수 있다.
"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from .errors import LlmError


def build_request_payload(
    *,
    model: str,
    system_prompt: str,
    user_content: str | list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    response_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """OpenAI 호환 chat.completions 요청 본문을 만든다.

    response_format이 주어지면 구조화 출력(JSON schema 등)을 함께 요청한다.
    서버가 미지원이면 호출자가 None으로 두면 된다(기본값).
    """

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format is not None:
        payload["response_format"] = response_format
    return payload


def build_multimodal_content(
    text: str,
    image_data_urls: list[str],
) -> str | list[dict[str, Any]]:
    """텍스트 + 이미지(data URL) 목록을 멀티모달 content로 합친다.

    이미지가 없으면 텍스트 문자열을 그대로 반환해 단순 요청을 유지한다.
    """

    if not image_data_urls:
        return text
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for url in image_data_urls:
        content.append({"type": "image_url", "image_url": {"url": url}})
    return content


def sanitize_request_for_log(request_payload: dict[str, Any]) -> dict[str, Any]:
    """로그 저장용으로 이미지 base64 본문을 마스킹한 사본을 반환한다."""

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


def estimate_context_tokens(
    *,
    system_prompt: str,
    user_prompt: str,
    image_count: int,
    max_tokens: int,
    context_margin: int,
    image_token_cost: int = 512,
) -> int:
    """토크나이저 없이 컨텍스트 사용량을 보수적으로 추정한다.

    로컬 토크나이저 의존을 피하기 위해 바이트 기반 근사치를 사용한다.
    """

    text_bytes = len((system_prompt + user_prompt).encode("utf-8"))
    estimated_input_tokens = math.ceil(text_bytes / 2)
    estimated_image_tokens = image_count * image_token_cost
    return (
        estimated_input_tokens
        + estimated_image_tokens
        + max_tokens
        + context_margin
    )


def ensure_context_budget(
    *,
    system_prompt: str,
    user_prompt: str,
    image_count: int,
    max_tokens: int,
    context_window: int,
    context_margin: int,
    reported_window: int | None = None,
) -> None:
    """예상 컨텍스트 사용량이 한도를 넘으면 LlmError를 발생시킨다."""

    window = context_window
    if isinstance(reported_window, int) and reported_window > 0:
        window = min(window, reported_window)

    estimated_total = estimate_context_tokens(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        image_count=image_count,
        max_tokens=max_tokens,
        context_margin=context_margin,
    )
    if estimated_total > window:
        raise LlmError(
            "LLM_CONTEXT_BUDGET_EXCEEDED",
            "LLM 컨텍스트 예상 사용량이 한도를 초과합니다. "
            f"estimated={estimated_total}, limit={window}, "
            f"max_tokens={max_tokens}, images={image_count}. "
            "분석 JSON을 축약하거나 출력 토큰 수를 줄여야 합니다.",
            retryable=False,
        )

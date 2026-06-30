"""LLM 슬롯 주석기.

규칙 기반 보고서의 자유서술 슬롯(summary/narrative/caption)만 LLM으로 채운다.
보고서 본문/판정/수치는 규칙이 이미 작성했으므로 LLM은 보조 역할만 한다.
하드 실패(연결/타임아웃/형식 오류)는 LlmError로 전달되며, 파이프라인이
이를 잡아 규칙 기반 기본 문안으로 대체한다(작업은 성공 처리).
"""

from __future__ import annotations

import base64
import json
import mimetypes
import math
import re
from pathlib import Path
from typing import Any

from rist_common import get_logger
from rist_common.llm import build_multimodal_content

from ..config import Settings
from ..llm_client import LlmError, LocalLlmClient
from ..storage import atomic_write_json
from .builders import LlmSlotSpec

logger = get_logger(__name__)

_MIN_LLM_OUTPUT_TOKENS = 256
_SMALL_CONTEXT_OUTPUT_CAP = 900
_LIBRARY_OUTPUT_TERMS = ("라이브러리", "library")


def _load_images(settings: Settings, processed_dir: Path) -> list[str]:
    if not settings.llm_include_images or not processed_dir.exists():
        return []
    candidates = sorted(
        path
        for path in processed_dir.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    )
    data_urls: list[str] = []
    for path in candidates:
        if len(data_urls) >= settings.llm_max_images:
            break
        if path.stat().st_size > settings.llm_max_image_bytes:
            continue
        media_type = mimetypes.guess_type(path.name)[0] or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        data_urls.append(f"data:{media_type};base64,{encoded}")
    return data_urls


def _content_text(content: str | list[dict[str, Any]]) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def _model_context_window(settings: Settings, llm_client: LocalLlmClient) -> int:
    window = settings.llm_context_window
    try:
        model_info = llm_client.get_model_info()
    except LlmError:
        return window
    for key in ("max_model_len", "max_sequence_length", "context_length"):
        value = model_info.get(key)
        if isinstance(value, int) and value > 0:
            return min(window, value)
    return window


def _estimate_input_tokens(
    system_prompt: str,
    user_content: str | list[dict[str, Any]],
    *,
    image_count: int,
) -> int:
    text = system_prompt + "\n" + _content_text(user_content)
    ascii_chars = sum(1 for char in text if ord(char) < 128)
    non_ascii_chars = len(text) - ascii_chars
    byte_estimate = math.ceil(len(text.encode("utf-8")) / 2.4)
    char_estimate = math.ceil((ascii_chars / 3.2) + (non_ascii_chars * 1.25))
    return max(byte_estimate, char_estimate) + image_count * 768 + 96


def _safe_max_tokens(
    settings: Settings,
    llm_client: LocalLlmClient,
    system_prompt: str,
    user_content: str | list[dict[str, Any]],
    *,
    image_count: int,
) -> int:
    window = _model_context_window(settings, llm_client)
    estimated_input = _estimate_input_tokens(
        system_prompt,
        user_content,
        image_count=image_count,
    )
    available = window - settings.llm_context_margin - estimated_input
    if available < _MIN_LLM_OUTPUT_TOKENS:
        raise LlmError(
            "LLM_CONTEXT_BUDGET_EXCEEDED",
            "LLM 입력이 모델 컨텍스트 한도에 가까워 보조 설명 생성을 생략합니다.",
            retryable=False,
        )
    max_tokens = min(settings.llm_max_tokens, available)
    if (
        window <= 8192
        and estimated_input + settings.llm_max_tokens + settings.llm_context_margin > window
    ):
        max_tokens = min(max_tokens, _SMALL_CONTEXT_OUTPUT_CAP)
    return max(_MIN_LLM_OUTPUT_TOKENS, int(max_tokens))


def _retry_max_tokens_from_context_error(message: str, current: int) -> int | None:
    window_match = re.search(r"maximum context length is\s+(\d+)", message)
    input_match = re.search(r"at least\s+(\d+)\s+input tokens", message)
    if window_match and input_match:
        window = int(window_match.group(1))
        input_tokens = int(input_match.group(1))
        available = window - input_tokens - 64
        if available >= _MIN_LLM_OUTPUT_TOKENS and available < current:
            return int(available)
    fallback = current // 2
    if fallback >= _MIN_LLM_OUTPUT_TOKENS and fallback < current:
        return fallback
    return None


def _forbidden_output_terms(spec: LlmSlotSpec) -> tuple[str, ...]:
    if "라이브러리 이름" in spec.system_prompt and "쓰지 마세요" in spec.system_prompt:
        return _LIBRARY_OUTPUT_TERMS
    return ()


def _contains_forbidden_output_term(text: str, terms: tuple[str, ...]) -> bool:
    normalized = text.lower()
    return any(term.lower() in normalized for term in terms)


def annotate(
    settings: Settings,
    llm_client: LocalLlmClient,
    spec: LlmSlotSpec,
    *,
    processed_dir: Path,
    logs_dir: Path,
) -> dict[str, str]:
    """LLM에게 슬롯 문안을 요청해 {slot: text} 를 반환한다(요청 슬롯 한정).

    실패 시 LlmError를 발생시킨다(상위에서 기본 문안으로 대체).
    """
    facts_json = json.dumps(spec.facts, ensure_ascii=False, separators=(",", ":"))
    if len(facts_json) > settings.llm_max_input_chars:
        raise LlmError(
            "LLM_INPUT_TOO_LARGE",
            "분석 근거가 LLM 최대 입력 크기를 초과했습니다.",
            retryable=False,
        )

    slot_list = ", ".join(spec.requested_slots)
    instruction = (
        "다음 JSON은 분석 프로그램이 산출한 근거입니다. "
        f"이 근거만 사용해 {slot_list} 슬롯을 작성하고, "
        "키가 정확히 그 슬롯들인 JSON 객체 하나로만 응답하세요.\n\n"
        f"{facts_json}"
    )

    images = _load_images(settings, processed_dir)
    user_content: str | list[dict[str, Any]]
    if images:
        user_content = build_multimodal_content(
            instruction
            + "\n\n첨부 이미지는 분석 산출물입니다. 직접 관찰 가능한 내용만 보조적으로 활용하세요.",
            images,
        )
    else:
        user_content = instruction

    request_payload = llm_client.build_request_payload(
        spec.system_prompt,
        user_content,
        response_format={"type": "json_object"},
        max_tokens=_safe_max_tokens(
            settings,
            llm_client,
            spec.system_prompt,
            user_content,
            image_count=len(images),
        ),
    )
    logs_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        logs_dir / "llm-request.json",
        llm_client.request_for_log(request_payload),
    )
    try:
        content, response_payload = llm_client.chat_completion(request_payload)
    except LlmError as exc:
        retry_max_tokens = (
            _retry_max_tokens_from_context_error(
                exc.message,
                int(request_payload.get("max_tokens") or settings.llm_max_tokens),
            )
            if exc.code == "LLM_CONTEXT_LENGTH_EXCEEDED"
            else None
        )
        if retry_max_tokens is None:
            raise
        retry_payload = dict(request_payload)
        retry_payload["max_tokens"] = retry_max_tokens
        atomic_write_json(
            logs_dir / "llm-request-retry.json",
            llm_client.request_for_log(retry_payload),
        )
        content, response_payload = llm_client.chat_completion(retry_payload)
    atomic_write_json(logs_dir / "llm-response.json", response_payload)

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LlmError(
            "LLM_RESPONSE_INVALID",
            "LLM 응답을 JSON 슬롯으로 파싱할 수 없습니다.",
            retryable=False,
        ) from exc
    if not isinstance(parsed, dict):
        raise LlmError(
            "LLM_RESPONSE_INVALID",
            "LLM 응답이 JSON 객체가 아닙니다.",
            retryable=False,
        )

    slots: dict[str, str] = {}
    forbidden_terms = _forbidden_output_terms(spec)
    for key in spec.requested_slots:
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            if not _contains_forbidden_output_term(text, forbidden_terms):
                slots[key] = text
        elif isinstance(value, list):
            lines = [str(item).strip() for item in value if str(item).strip()]
            if lines:
                text = "\n".join(lines)
                if not _contains_forbidden_output_term(text, forbidden_terms):
                    slots[key] = text
    if not slots:
        raise LlmError(
            "LLM_RESPONSE_EMPTY",
            "LLM 응답에 유효한 슬롯 문안이 없습니다.",
            retryable=False,
        )
    logger.info("LLM 슬롯 주석 완료 (slots=%s)", ",".join(slots))
    return slots

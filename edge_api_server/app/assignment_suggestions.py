"""LLM-assisted draft assignment-library suggestions."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from ftir.assignment_libraries import (
    AssignmentLibraryError,
    parse_assignment_library,
)

from .llm_client import LlmError, LocalLlmClient


SUGGESTION_WARNING = (
    "LLM 추천 초안입니다. 저장 전 표준 스펙트럼 또는 문헌값으로 검토하십시오."
)
_SAFE_ID_TEXT = re.compile(r"[^a-z0-9]+")
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass(frozen=True)
class AssignmentSuggestionRequest:
    experiment_code: str
    material: str
    library_id: str | None = None
    library_name: str | None = None


def suggest_assignment_library(
    settings: Any,
    request: AssignmentSuggestionRequest,
) -> dict[str, Any]:
    material = request.material.strip()
    if not material:
        raise AssignmentLibraryError(
            "ASSIGNMENT_SUGGESTION_MATERIAL_REQUIRED",
            "추천할 물질명 또는 계열명을 입력하세요.",
        )
    experiment = _experiment_label(request.experiment_code)
    library_id = _suggested_library_id(
        request.library_id,
        material,
        experiment,
    )
    library_name = (
        request.library_name.strip()
        if request.library_name and request.library_name.strip()
        else f"{material} {experiment}"
    )
    try:
        payload = _call_llm(settings, material=material, experiment=experiment)
    except LlmError as exc:
        raise map_llm_error(exc) from exc
    library_payload = _normalise_payload(
        payload,
        library_id=library_id,
        library_name=library_name,
        material=material,
        experiment=experiment,
    )
    library = parse_assignment_library(
        f"{library_id}.json",
        json.dumps(library_payload, ensure_ascii=False).encode("utf-8"),
    )
    detail = library.detail()
    detail["id"] = library.library_id
    return {
        "library": detail,
        "warning": SUGGESTION_WARNING,
    }


def _call_llm(settings: Any, *, material: str, experiment: str) -> dict[str, Any]:
    system_prompt = (
        "당신은 재료분석 실험실의 피크 assignment 라이브러리 초안 작성 보조자입니다.\n"
        "제공된 물질명/계열명과 분석법에 대해 일반적으로 알려진 주요 피크 후보만 제안하세요.\n"
        "정확한 표준 라이브러리가 아니므로 과도하게 많은 피크를 만들지 말고, 저장 전 검토가 필요함을 note에 남기세요.\n"
        "응답은 JSON 객체 하나로만 작성하세요.\n"
        "키: description, assignments.\n"
        "assignments 항목 키: centerWavenumber(number), tolerance(number), name(string), color(#RRGGBB), note(string).\n"
        "FT-IR이면 centerWavenumber는 cm-1 파수, Raman이면 Raman shift cm-1 값입니다.\n"
        "색상은 피크 계열별로 구분 가능한 #RRGGBB 형식이어야 합니다."
    )
    user_prompt = {
        "experiment": experiment,
        "material": material,
        "requirements": {
            "assignmentCount": "5-12 major peaks",
            "tolerance": "typical matching window, usually 10-120 cm-1",
            "doNotInventCertainty": True,
        },
    }
    with LocalLlmClient(
        settings.llm_base_url,
        settings.llm_model,
        settings.llm_timeout_seconds,
        settings.llm_temperature,
        min(settings.llm_max_tokens, 900),
        settings.llm_validate_model,
    ) as client:
        request_payload = client.build_request_payload(
            system_prompt,
            json.dumps(user_prompt, ensure_ascii=False),
            response_format={"type": "json_object"},
            max_tokens=min(settings.llm_max_tokens, 900),
        )
        content, _ = client.chat_completion(request_payload)
    parsed = _parse_json_content(content)
    if not isinstance(parsed, dict):
        raise AssignmentLibraryError(
            "ASSIGNMENT_SUGGESTION_INVALID_JSON",
            "LLM 추천 응답의 최상위 값은 객체여야 합니다.",
        )
    return parsed


def _normalise_payload(
    payload: dict[str, Any],
    *,
    library_id: str,
    library_name: str,
    material: str,
    experiment: str,
) -> dict[str, Any]:
    rows = payload.get("assignments")
    if not isinstance(rows, list):
        raise AssignmentLibraryError(
            "ASSIGNMENT_SUGGESTION_INVALID_PAYLOAD",
            "LLM 추천 응답에 assignments 배열이 필요합니다.",
        )
    assignments: list[dict[str, Any]] = []
    for row in rows[:24]:
        if not isinstance(row, dict):
            continue
        center = _number_or_none(_first_value(
            row,
            "centerWavenumber",
            "center_wn",
            "wavenumber",
            "wavenumber_cm1",
            "shift",
            "ramanShift",
        ))
        tolerance = _number_or_none(row.get("tolerance", 30))
        name = str(row.get("name") or "").strip()
        if center is None or tolerance is None or not name:
            continue
        assignments.append(
            {
                "centerWavenumber": center,
                "tolerance": tolerance,
                "name": name[:200],
                "color": _color_text(row.get("color")),
                "note": _note_text(row.get("note")),
            }
        )
    description = str(payload.get("description") or "").strip()
    if not description:
        description = (
            f"{material} {experiment} 피크 assignment LLM 추천 초안. "
            "저장 전 표준 스펙트럼 또는 문헌값 검토 필요."
        )
    return {
        "id": library_id,
        "name": library_name,
        "description": description[:1000],
        "assignments": assignments,
    }


def _parse_json_content(content: str) -> Any:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError as exc:
                raise AssignmentLibraryError(
                    "ASSIGNMENT_SUGGESTION_INVALID_JSON",
                    "LLM 추천 응답이 JSON 형식이 아닙니다.",
                ) from exc
        raise AssignmentLibraryError(
            "ASSIGNMENT_SUGGESTION_INVALID_JSON",
            "LLM 추천 응답이 JSON 형식이 아닙니다.",
        )


def _first_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row:
            return row[key]
    return None


def _number_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _color_text(value: Any) -> str:
    text = str(value or "").strip()
    return text.lower() if _HEX_COLOR.fullmatch(text) else "#64748b"


def _note_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        text = "LLM 추천 초안. 저장 전 문헌/표준 라이브러리 확인 필요."
    return text[:1000]


def _experiment_label(experiment_code: str) -> str:
    upper = experiment_code.strip().upper()
    if upper in {"FTIR", "FT-IR", "IR"}:
        return "FT-IR"
    if upper in {"RAMAN", "RIN", "RIN-RAMAN"}:
        return "Raman"
    return upper or "Analysis"


def _suggested_library_id(
    requested_id: str | None,
    material: str,
    experiment: str,
) -> str:
    if requested_id and requested_id.strip():
        return requested_id.strip().lower()
    slug = _SAFE_ID_TEXT.sub("-", material.casefold()).strip("-")
    suffix = "ftir" if experiment == "FT-IR" else "raman"
    if not slug:
        digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:8]
        slug = f"suggested-{digest}"
    if suffix not in slug.split("-"):
        slug = f"{slug}-{suffix}"
    return slug[:80].strip("-") or f"suggested-{suffix}"


def map_llm_error(exc: LlmError) -> AssignmentLibraryError:
    return AssignmentLibraryError(
        exc.code,
        f"LLM 피크 추천에 실패했습니다: {exc.message}",
    )

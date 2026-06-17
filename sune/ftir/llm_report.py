# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: FT-IR 분석 결과(verdict)를 로컬 LLM 보고서 요청으로 변환하는
#            프로젝트 전용 포맷 모듈. 공통 LLM 클라이언트(rist_common.llm)는
#            전송만 담당하고, 프롬프트/입력 JSON 포맷은 이 파일에서 관리한다.
# 실행 방법: 모듈 — import해서 사용 (from ftir.llm_report import generate_ftir_report)
# ─────────────────────────────────────────────────────────────────────────────
"""FT-IR verdict → 로컬 LLM 보고서 요청 빌더.

개선 포인트:
1. 충돌 우선순위 규칙 명시 (라이브러리 미동정 vs 룰 동정).
2. 입력 경량화 (rule_matches.subtype_all/aliases/상세 배열 제거).
3. 미동정 시 '확정 동정 아님' 필수 문구 강제.
4. schema_version / prompt_version 버전 필드 주입.
5. 구조화 출력(response_format) 선택 지원.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from rist_common.llm import build_request_payload

# 프롬프트/입력 스키마 버전 — 회귀 분석과 재현성을 위해 요청에 함께 기록한다.
PROMPT_VERSION = "ftir-2026.06"
INPUT_SCHEMA_VERSION = "ftir-verdict-1"

FTIR_SYSTEM_PROMPT = """당신은 재료분석 실험실의 FT-IR 보고서 작성 보조자입니다.

작성 원칙:
1. Python 전처리 결과와 룰 기반 판정 결과를 우선 참고하세요.
2. 라이브러리 매칭 결과와 룰 기반 판정 결과를 명확히 구분하세요.
3. 신호가 충돌할 때 우선순위: (1) tier / library_support, (2) 룰 기반 판정,
   (3) combined_verdict 는 보조 근거로만 사용. 'is_identified' 가 true 라도
   tier 가 '미동정' 이면 라이브러리 기반 확정 동정은 아님으로 서술하세요.
4. 룰 기반 근거가 높더라도 물질명을 단정하지 말고 '가능성', '시사함',
   '검토 필요' 중심으로 작성하세요.
5. 피크 하나만으로 작용기나 물질을 확정하지 마세요.
6. tier 가 '미동정' 이면 보고서에 '라이브러리 기반 확정 동정 아님 — 추가 분석
   필요' 취지의 문구를 반드시 포함하세요.
7. 고객 보고서에 과도한 화학 구조 추정이나 단정적 표현을 쓰지 마세요.
8. 제공된 JSON에 없는 물질/피크/판정/원인을 새로 만들지 마세요.
9. 최종 출력은 한국어로 작성하세요.

출력 형식:
1. 고객 보고서용 요약: 정확히 3문장
2. 주요 근거: bullet 4개 이내
3. 해석 한계 및 검토 필요사항: bullet 3개 이내
4. PPT caption: 1문장
"""

# 구조화 출력을 지원하는 서버에서 사용할 JSON 스키마(선택).
FTIR_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "ftir_report",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["summary", "evidence", "limitations", "ppt_caption"],
            "properties": {
                "summary": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 3,
                    "maxItems": 3,
                },
                "evidence": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 4,
                },
                "limitations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 3,
                },
                "ppt_caption": {"type": "string"},
            },
        },
    },
}


def slim_verdict_for_llm(verdict: dict[str, Any]) -> dict[str, Any]:
    """LLM 입력용으로 verdict JSON을 경량화한다.

    rule_matches의 subtype_all/aliases/상세 매칭 배열 등 토큰을 크게
    잡아먹는 항목을 요약본으로 대체해 컨텍스트 예산을 절약한다.
    """

    v = deepcopy(verdict)

    slim_rules: list[dict[str, Any]] = []
    for rule in v.get("rule_matches", []) or []:
        slim_rules.append(
            {
                "compound": rule.get("compound"),
                "compound_display": rule.get("compound_display"),
                "family": rule.get("family"),
                "category": rule.get("category"),
                "score_pct": rule.get("score_pct"),
                "verdict": rule.get("verdict"),
                "required_fraction": rule.get("required_fraction"),
                "matched_required_count": len(rule.get("matched_required") or []),
                "missed_required": [
                    m.get("label") for m in (rule.get("missed_required") or [])
                ],
                "matched_context_markers": [
                    {
                        "label": m.get("label"),
                        "interpretation": m.get("interpretation"),
                    }
                    for m in (rule.get("matched_context_markers") or [])
                ],
                "triggered_warnings": [
                    {"label": w.get("label"), "assignment": w.get("assignment")}
                    for w in (rule.get("triggered_warnings") or [])
                ],
                "subtype_label": rule.get("subtype_label"),
                "subtype_summary": rule.get("subtype_summary"),
            }
        )
    if "rule_matches" in v:
        v["rule_matches"] = slim_rules

    findings = v.get("findings")
    if isinstance(findings, dict):
        # 최상위 tier와 중복되는 필드 제거.
        findings.pop("tier", None)

    return v


def build_ftir_messages(verdict: dict[str, Any]) -> tuple[str, str]:
    """(system_prompt, user_content) 튜플을 만든다."""

    payload = {
        "schema_version": INPUT_SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "verdict": slim_verdict_for_llm(verdict),
    }
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    user_content = (
        "다음은 FT-IR 분석 프로그램이 생성한 JSON 결과입니다.\n"
        "이 결과만 근거로 지정된 출력 형식의 고객 보고서 문안을 작성하세요.\n\n"
        f"분석 JSON:\n{serialized}"
    )
    return FTIR_SYSTEM_PROMPT, user_content


def build_ftir_request(
    verdict: dict[str, Any],
    *,
    model: str,
    temperature: float = 0.1,
    max_tokens: int = 1200,
    structured: bool = False,
) -> dict[str, Any]:
    """OpenAI 호환 chat.completions 요청 본문을 만든다.

    structured=True 이면 JSON 스키마 기반 구조화 출력을 요청한다(서버 지원 필요).
    """

    system_prompt, user_content = build_ftir_messages(verdict)
    return build_request_payload(
        model=model,
        system_prompt=system_prompt,
        user_content=user_content,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=FTIR_RESPONSE_FORMAT if structured else None,
    )


def generate_ftir_report(
    client: Any,
    verdict: dict[str, Any],
    *,
    structured: bool = False,
) -> tuple[str, dict[str, Any]]:
    """공통 LlmClient로 FT-IR 보고서 문안을 생성한다.

    client 는 rist_common.llm.LlmClient 인스턴스이며 연결/모델 설정을 보유한다.
    포맷(프롬프트/입력 JSON)은 이 모듈이 담당한다.
    """

    system_prompt, user_content = build_ftir_messages(verdict)
    request_payload = client.build_request_payload(
        system_prompt,
        user_content,
        response_format=FTIR_RESPONSE_FORMAT if structured else None,
    )
    return client.chat_completion(request_payload)

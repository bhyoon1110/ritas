"""실험 종류별 규칙 기반 보고서 작성기.

각 작성기는 업로드된 구조화 분석 결과(verdict JSON)를 '고정 양식' 섹션으로
결정론적으로 매핑한다. 분석을 재실행하지 않으며, LLM도 호출하지 않는다.
LLM이 채울 자유서술 슬롯(summary/narrative/caption)에는 규칙 기반 기본 문안을
미리 넣어 두어, LLM이 없거나 실패해도 보고서가 완성되도록 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .model import ReportDocument, ReportSection, ReportTable

# 분석 결과 항목: {"relativePath": str, "data": <json>}
AnalysisItem = dict[str, Any]


@dataclass
class LlmSlotSpec:
    """LLM 주석 단계에 전달할 명세(실험 종류별 프롬프트/근거/기본 문안)."""

    system_prompt: str
    facts: dict[str, Any]
    requested_slots: list[str]
    fallback: dict[str, str] = field(default_factory=dict)


def _pct(value: Any) -> str:
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "-"


def _select_verdict(analysis: list[AnalysisItem]) -> dict[str, Any] | None:
    """tier/findings 를 가진 verdict JSON 을 우선 선택한다."""
    for item in analysis:
        data = item.get("data")
        if isinstance(data, dict) and ("tier" in data or "findings" in data):
            return data
    for item in analysis:
        data = item.get("data")
        if isinstance(data, dict):
            return data
    return None


class ReportBuilder:
    """보고서 작성기 베이스."""

    experiment_codes: frozenset[str] = frozenset()

    def _meta_sections(
        self, job: dict[str, Any], verdict: dict[str, Any]
    ) -> list[ReportSection]:
        sample = str(verdict.get("sample", "")) if verdict else ""
        return [
            ReportSection(
                section_id="sample_info",
                heading="시료 정보",
                bullets=[
                    f"시료: {sample}" if sample else "시료: (미기재)",
                    f"요청번호: {job['request_number']}",
                    f"실험코드: {job['experiment_code']}",
                    f"장비코드: {job['equipment_code']}",
                    f"작업자: {job['operator_id']}",
                ],
            )
        ]

    def build(
        self, job: dict[str, Any], analysis: list[AnalysisItem]
    ) -> ReportDocument:  # pragma: no cover - 추상
        raise NotImplementedError

    def llm_slots(
        self, job: dict[str, Any], analysis: list[AnalysisItem]
    ) -> LlmSlotSpec | None:  # pragma: no cover - 추상
        raise NotImplementedError


class FtirReportBuilder(ReportBuilder):
    experiment_codes = frozenset({"FTIR", "FT-IR", "IR"})

    SYSTEM_PROMPT = (
        "당신은 재료분석 실험실의 FT-IR 보고서 작성 보조자입니다.\n"
        "제공된 피크 정보와 라이브러리 매칭 결과(JSON)만 근거로 한국어 문안을 작성하세요.\n"
        "수치를 재계산하거나 제공되지 않은 물질, 피크, 작용기를 추측하지 마세요.\n"
        "라이브러리 점수가 임계값 미만이면 '라이브러리 기반 확정 동정은 아님'으로 표현하세요.\n"
        "물질명을 단정하지 말고 '가능성', '시사함', '검토 필요' 중심으로 표현하세요.\n"
        "출력은 반드시 JSON 객체 하나로만, 키는 summary/narrative/caption 입니다.\n"
        "- summary: 고객 보고서용 요약 정확히 3문장\n"
        "- narrative: 주요 근거와 해석에 대한 보조 설명(4문장 이내)\n"
        "- caption: 발표자료용 한 문장 캡션"
    )

    def build(
        self, job: dict[str, Any], analysis: list[AnalysisItem]
    ) -> ReportDocument:
        verdict = _select_verdict(analysis) or {}
        document = ReportDocument(
            job_id=job["job_id"],
            title="FT-IR 분석 보고서",
            experiment_code=job["experiment_code"],
            pk={
                "requestNumber": job["request_number"],
                "experimentCode": job["experiment_code"],
                "equipmentCode": job["equipment_code"],
                "operatorId": job["operator_id"],
            },
            generated_at=job.get("_generated_at", ""),
            sections=self._meta_sections(job, verdict),
        )

        document.sections.append(self._verdict_section(verdict))
        document.sections.append(self._library_section(verdict))
        document.sections.append(self._functional_groups_section(verdict))

        fallback = self._fallback_texts(verdict)
        document.sections.append(
            ReportSection("summary", "고객 보고서용 요약", paragraphs=[fallback["summary"]])
        )
        document.sections.append(
            ReportSection("narrative", "보조 설명", paragraphs=[fallback["narrative"]])
        )
        document.sections.append(self._limitations_section(verdict))
        document.sections.append(
            ReportSection("caption", "발표자료 캡션", paragraphs=[fallback["caption"]])
        )
        return document

    # --- 규칙 섹션 ----------------------------------------------------
    def _verdict_section(self, verdict: dict[str, Any]) -> ReportSection:
        bullets = []
        if verdict.get("tier"):
            bullets.append(f"신뢰도 판정: {verdict['tier']}")
        if verdict.get("reason"):
            bullets.append(f"근거: {verdict['reason']}")
        cv = verdict.get("combined_verdict") or {}
        if cv.get("verdict"):
            confidence = cv.get("confidence")
            suffix = f" (신뢰도 {confidence})" if confidence else ""
            bullets.append(f"종합 판정: {cv['verdict']}{suffix}")
        if cv.get("action"):
            bullets.append(f"권고 조치: {cv['action']}")
        if not bullets:
            bullets.append("판정 정보가 분석 결과에 포함되어 있지 않습니다.")
        return ReportSection("verdict", "판정 결과", bullets=bullets)

    def _library_section(self, verdict: dict[str, Any]) -> ReportSection:
        top = verdict.get("top_candidate")
        if not isinstance(top, dict):
            return ReportSection(
                "library_match",
                "라이브러리 매칭",
                paragraphs=["라이브러리 매칭 후보가 없습니다."],
            )
        table = ReportTable(
            columns=["항목", "값"],
            rows=[
                ["후보 물질", str(top.get("material", "-"))],
                ["카테고리", str(top.get("category", "-"))],
                ["종합 점수", _pct(top.get("composite_pct"))],
                ["코사인", _pct(top.get("cosine_pct"))],
                ["미분", _pct(top.get("deriv_pct"))],
                ["피크", _pct(top.get("peak_pct"))],
                ["겹침", _pct(top.get("overlap_pct"))],
            ],
        )
        return ReportSection("library_match", "라이브러리 매칭(최상위 후보)", table=table)

    def _functional_groups_section(self, verdict: dict[str, Any]) -> ReportSection:
        findings = verdict.get("findings") or {}
        groups = findings.get("functional_groups") if isinstance(findings, dict) else None
        if not isinstance(groups, list) or not groups:
            return ReportSection(
                "functional_groups",
                "작용기 소견",
                paragraphs=["작용기 소견 정보가 없습니다."],
            )
        rows = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            rows.append(
                [
                    str(group.get("group", "-")),
                    _pct(group.get("confidence_pct")),
                    str(group.get("evidence", "")),
                ]
            )
        table = ReportTable(columns=["작용기", "신뢰도", "근거"], rows=rows)
        return ReportSection("functional_groups", "작용기 소견", table=table)

    def _limitations_section(self, verdict: dict[str, Any]) -> ReportSection:
        bullets: list[str] = []
        top = verdict.get("top_candidate") or {}
        composite = top.get("composite_pct")
        if not verdict.get("is_library_identified", False):
            bullets.append(
                "라이브러리 기반 확정 동정이 아니므로 후보 물질은 참고용입니다."
            )
        if isinstance(composite, (int, float)) and composite < 65:
            bullets.append(
                f"최고 종합 점수({_pct(composite)})가 임계값(65%) 미만이라 추가 검토가 필요합니다."
            )
        bullets.append(
            "단일 피크/단일 이미지만으로 작용기나 물질을 확정하지 않았습니다."
        )
        bullets.append("정량 분석 및 혼합물 분리는 본 보고 범위에 포함되지 않습니다.")
        return ReportSection("limitations", "해석 한계 및 검토 필요사항", bullets=bullets)

    # --- LLM 슬롯 -----------------------------------------------------
    def _fallback_texts(self, verdict: dict[str, Any]) -> dict[str, str]:
        sample = verdict.get("sample", "시료")
        tier = verdict.get("tier", "판정 미상")
        top = verdict.get("top_candidate") or {}
        material = top.get("material", "후보 물질")
        composite = _pct(top.get("composite_pct"))
        summary = (
            f"{sample} 시료에 대한 FT-IR 분석 결과 신뢰도 판정은 '{tier}'입니다. "
            f"라이브러리 최상위 후보는 '{material}'(종합 {composite})로 나타났습니다. "
            "확정 동정이 아니므로 결과는 참고용으로 해석해야 합니다."
        )
        narrative = (
            "규칙 기반 작용기 소견과 라이브러리 매칭 점수를 종합한 결과입니다. "
            "구체적 근거는 작용기 소견 및 라이브러리 매칭 표를 참고하십시오."
        )
        caption = f"{sample} FT-IR 분석 — 최상위 후보 {material}(참고용)"
        return {"summary": summary, "narrative": narrative, "caption": caption}

    def llm_slots(
        self, job: dict[str, Any], analysis: list[AnalysisItem]
    ) -> LlmSlotSpec | None:
        verdict = _select_verdict(analysis)
        if not verdict:
            return None
        findings = verdict.get("findings") or {}
        groups = (
            findings.get("functional_groups")
            if isinstance(findings, dict)
            else None
        )
        cv = verdict.get("combined_verdict") or {}
        facts = {
            "sample": verdict.get("sample"),
            "tier": verdict.get("tier"),
            "reason": verdict.get("reason"),
            "is_library_identified": verdict.get("is_library_identified"),
            "top_candidate": verdict.get("top_candidate"),
            "functional_groups": groups,
            "combined_verdict": {
                "verdict": cv.get("verdict"),
                "confidence": cv.get("confidence"),
                "action": cv.get("action"),
                "explanation": cv.get("explanation"),
            },
        }
        return LlmSlotSpec(
            system_prompt=self.SYSTEM_PROMPT,
            facts=facts,
            requested_slots=["summary", "narrative", "caption"],
            fallback=self._fallback_texts(verdict),
        )


class GenericReportBuilder(ReportBuilder):
    """FT-IR 외 실험 종류 기본 작성기."""

    SYSTEM_PROMPT = (
        "당신은 재료분석 실험실의 보고서 작성 보조자입니다.\n"
        "제공된 구조화 분석 결과(JSON)만 근거로 한국어 문안을 작성하세요.\n"
        "제공되지 않은 수치, 물질, 원인을 추측하지 마세요.\n"
        "출력은 반드시 JSON 객체 하나로만, 키는 summary/narrative/caption 입니다.\n"
        "- summary: 고객 보고서용 요약 정확히 3문장\n"
        "- narrative: 주요 근거 보조 설명(4문장 이내)\n"
        "- caption: 발표자료용 한 문장 캡션"
    )

    def build(
        self, job: dict[str, Any], analysis: list[AnalysisItem]
    ) -> ReportDocument:
        verdict = _select_verdict(analysis) or {}
        document = ReportDocument(
            job_id=job["job_id"],
            title=f"{job['experiment_code']} 분석 보고서",
            experiment_code=job["experiment_code"],
            pk={
                "requestNumber": job["request_number"],
                "experimentCode": job["experiment_code"],
                "equipmentCode": job["equipment_code"],
                "operatorId": job["operator_id"],
            },
            generated_at=job.get("_generated_at", ""),
            sections=self._meta_sections(job, verdict),
        )
        bullets = [
            f"{key}: {value}"
            for key, value in verdict.items()
            if isinstance(value, (str, int, float, bool))
        ][:12]
        document.sections.append(
            ReportSection(
                "analysis",
                "분석 결과",
                bullets=bullets or ["구조화 분석 결과 항목이 없습니다."],
            )
        )
        fallback = self._fallback_texts(job, verdict)
        document.sections.append(
            ReportSection("summary", "고객 보고서용 요약", paragraphs=[fallback["summary"]])
        )
        document.sections.append(
            ReportSection("narrative", "보조 설명", paragraphs=[fallback["narrative"]])
        )
        document.sections.append(
            ReportSection("caption", "발표자료 캡션", paragraphs=[fallback["caption"]])
        )
        return document

    def _fallback_texts(
        self, job: dict[str, Any], verdict: dict[str, Any]
    ) -> dict[str, str]:
        sample = verdict.get("sample", "시료")
        summary = (
            f"{sample} 시료에 대한 {job['experiment_code']} 분석 결과를 정리했습니다. "
            "세부 수치는 분석 결과 표를 참고하십시오. "
            "결과는 참고용으로 해석해야 합니다."
        )
        narrative = "구조화 분석 결과를 바탕으로 한 규칙 기반 요약입니다."
        caption = f"{sample} {job['experiment_code']} 분석 결과(참고용)"
        return {"summary": summary, "narrative": narrative, "caption": caption}

    def llm_slots(
        self, job: dict[str, Any], analysis: list[AnalysisItem]
    ) -> LlmSlotSpec | None:
        verdict = _select_verdict(analysis)
        if not verdict:
            return None
        return LlmSlotSpec(
            system_prompt=self.SYSTEM_PROMPT,
            facts={"analysis": verdict},
            requested_slots=["summary", "narrative", "caption"],
            fallback=self._fallback_texts(job, verdict),
        )


_FTIR_BUILDER = FtirReportBuilder()
_GENERIC_BUILDER = GenericReportBuilder()


def get_builder(experiment_code: str) -> ReportBuilder:
    normalized = experiment_code.upper().replace("_", "-")
    if normalized in _FTIR_BUILDER.experiment_codes:
        return _FTIR_BUILDER
    return _GENERIC_BUILDER

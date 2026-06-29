"""실험 종류별 규칙 기반 보고서 작성기.

각 작성기는 업로드된 구조화 분석 결과(verdict JSON)를 '고정 양식' 섹션으로
결정론적으로 매핑한다. 분석을 재실행하지 않으며, LLM도 호출하지 않는다.
LLM이 채울 자유서술 슬롯(summary/narrative/caption)에는 규칙 기반 기본 문안을
미리 넣어 두어, LLM이 없거나 실패해도 보고서가 완성되도록 한다.
"""

from __future__ import annotations

import html
import re
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


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean_html_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"<br\s*/?>", " / ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return " ".join(text.split()) or "-"


def _trace_is_visible(trace: dict[str, Any]) -> bool:
    return trace.get("visible", True) not in {False, "legendonly"}


def _first_numeric(value: Any) -> float | None:
    items = value if isinstance(value, list) else [value]
    for item in items:
        try:
            return float(item)
        except (TypeError, ValueError):
            continue
    return None


def _rounded_number(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 6)


def _figure_peak_facts(
    payload: dict[str, Any],
    *,
    x_label: str,
    max_items: int = 80,
    compact: bool = False,
) -> list[dict[str, Any]]:
    figure = payload.get("figure")
    if not isinstance(figure, dict):
        return []
    data = _as_list(figure.get("data"))
    sample_names: dict[str, str] = {}
    hidden_sample_groups: set[str] = set()
    facts: list[dict[str, Any]] = []
    for trace in data:
        if not isinstance(trace, dict):
            continue
        meta = trace.get("meta")
        if not isinstance(meta, dict):
            continue
        group = meta.get("rist_sample_group")
        if group and meta.get("rist_sample_parent"):
            sample_names[str(group)] = _clean_html_text(trace.get("name") or group)
            if not _trace_is_visible(trace):
                hidden_sample_groups.add(str(group))
    for trace in data:
        if not isinstance(trace, dict):
            continue
        if not _trace_is_visible(trace):
            continue
        meta = trace.get("meta")
        peak = meta.get("rist_peak") if isinstance(meta, dict) else None
        if not isinstance(peak, dict):
            continue
        assignments = _as_list(peak.get("assignments"))
        libraries = []
        for item in assignments:
            if not isinstance(item, dict):
                continue
            libraries.append(str(item.get("library_name") or item.get("library_id") or ""))
        libraries = [item for item in dict.fromkeys(libraries) if item]
        assignment_names = [
            _clean_html_text(item.get("name"))
            for item in assignments
            if isinstance(item, dict) and item.get("name")
        ]
        x_value = peak.get("x")
        try:
            x_number = float(x_value)
            x_text = f"{x_number:.1f}"
        except (TypeError, ValueError):
            x_number = None
            x_text = str(x_value or "-")
        sample_group = str(peak.get("sample_group") or "")
        if sample_group and sample_group in hidden_sample_groups:
            continue
        sample = sample_names.get(sample_group, sample_group or "-")
        label = _clean_html_text(trace.get("name") or peak.get("label") or "-")
        original_label = _clean_html_text(peak.get("label") or trace.get("name") or "-")
        display_intensity = _first_numeric(trace.get("y"))
        base_intensity = _first_numeric(peak.get("base_y"))
        if base_intensity is None:
            base_intensity = display_intensity
        if compact:
            fact: dict[str, Any] = {
                "sample": sample,
                "pos": _rounded_number(x_number),
                "unit": x_label,
                "label": label,
                "source": peak.get("source") or "detected",
            }
            if base_intensity is not None:
                fact["intensity"] = _rounded_number(base_intensity)
            if display_intensity is not None and display_intensity != base_intensity:
                fact["display_intensity"] = _rounded_number(display_intensity)
            if original_label != label:
                fact["original_label"] = original_label
            if assignment_names:
                fact["assignments"] = list(dict.fromkeys(assignment_names))[:3]
            if libraries:
                fact["libraries"] = libraries[:3]
            if peak.get("group_name"):
                fact["group"] = _clean_html_text(peak.get("group_name"))
            if peak.get("user"):
                fact["user_added"] = True
            facts.append(fact)
        else:
            facts.append(
                {
                    "sample": sample,
                    "sample_group": sample_group or None,
                    "position": x_number,
                    "position_text": f"{x_text} {x_label}",
                    "display_intensity": display_intensity,
                    "base_intensity": base_intensity,
                    "label": label,
                    "original_label": original_label,
                    "assignment_names": list(dict.fromkeys(assignment_names)),
                    "libraries": libraries,
                    "group_name": _clean_html_text(peak.get("group_name"))
                    if peak.get("group_name")
                    else None,
                    "group_color": peak.get("group_color"),
                    "is_user_added": bool(peak.get("user")),
                    "source": peak.get("source") or "detected",
                }
            )
        if len(facts) >= max_items:
            break
    return facts


def _figure_peak_rows(payload: dict[str, Any], *, x_label: str) -> list[list[str]]:
    rows = []
    for peak in _figure_peak_facts(payload, x_label=x_label, max_items=18):
        rows.append(
            [
                str(peak["sample"]),
                str(peak["position_text"]),
                str(peak["label"]),
                ", ".join(peak["libraries"]) or "-",
            ]
        )
        if len(rows) >= 18:
            break
    return rows


def _stringify_metadata_value(value: Any) -> str:
    if value is None:
        return "(미기재)"
    if isinstance(value, (str, int, float, bool)):
        text = str(value).strip()
        return text or "(미기재)"
    return str(value)


def _metadata_items(value: Any) -> list[tuple[str, str]]:
    if not isinstance(value, dict):
        return []
    rows = []
    for key, item in value.items():
        key_text = str(key).strip()
        if not key_text:
            continue
        rows.append((key_text, _stringify_metadata_value(item)))
    return rows


def _experiment_condition_rows(payload: dict[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    for key in (
        "experimentConditions",
        "experiment_conditions",
        "conditions",
        "metadata",
        "environment",
        "experimentEnvironment",
        "experiment_environment",
    ):
        for item_key, item_value in _metadata_items(payload.get(key)):
            rows.append(["공통", item_key, item_value])

    samples = payload.get("samples")
    if isinstance(samples, list):
        seen: set[tuple[str, str, str]] = set()
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            source = str(
                sample.get("label")
                or sample.get("fileName")
                or sample.get("sample")
                or "시료"
            )
            for item_key, item_value in _metadata_items(sample.get("metadata")):
                row = (source, item_key, item_value)
                if row not in seen:
                    seen.add(row)
                    rows.append([source, item_key, item_value])
    return rows


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

    def _experiment_conditions_section(self, payload: dict[str, Any]) -> ReportSection:
        rows = _experiment_condition_rows(payload)
        if rows:
            return ReportSection(
                "experiment_conditions",
                "실험조건 및 실험환경",
                table=ReportTable(["대상", "항목", "값"], rows),
            )
        return ReportSection(
            "experiment_conditions",
            "실험조건 및 실험환경",
            paragraphs=["원본 분석 데이터에 실험조건/실험환경 정보가 포함되어 있지 않습니다."],
        )

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
        "current_peaks는 보고서 생성 시점의 그래프 화면에서 사용자가 편집/삭제/숨김 처리한 뒤 남은 피크입니다.\n"
        "피크명은 current_peaks.label을 우선 사용하고, original_label은 변경 전 추적용으로만 참고하세요.\n"
        "라이브러리 점수가 임계값 미만이면 '라이브러리 기반 확정 동정은 아님'으로 표현하세요.\n"
        "물질명을 단정하지 말고 '가능성', '시사함', '검토 필요' 중심으로 표현하세요.\n"
        "출력은 반드시 JSON 객체 하나로만 응답하세요.\n"
        "- summary: 고객 보고서용 요약 정확히 3문장\n"
        "- key_findings: 핵심 관찰사항 3~5개를 짧은 문장으로 작성\n"
        "- interpretation: 피크/라이브러리 근거를 연결한 해석(4문장 이내)\n"
        "- qc_notes: 해석 한계, 품질 확인, 추가 검토 사항(3문장 이내)\n"
        "- narrative: 주요 근거와 해석에 대한 보조 설명(4문장 이내)\n"
        "- caption: 발표자료용 한 문장 캡션\n"
        "- email_subject: 메일 제목 1줄\n"
        "- email_body: 첨부 보고서 안내 메일 본문. Markdown 형식으로 8문장 이내"
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

        document.sections.append(self._experiment_conditions_section(verdict))
        document.sections.append(self._verdict_section(verdict))
        document.sections.append(self._library_section(verdict))
        document.sections.append(self._functional_groups_section(verdict))
        document.sections.append(self._current_peak_section(verdict))

        fallback = self._fallback_texts(verdict)
        document.sections.append(
            ReportSection("summary", "고객 보고서용 요약", paragraphs=[fallback["summary"]])
        )
        document.sections.append(
            ReportSection("key_findings", "핵심 관찰사항", paragraphs=[fallback["key_findings"]])
        )
        document.sections.append(
            ReportSection("interpretation", "피크 해석", paragraphs=[fallback["interpretation"]])
        )
        document.sections.append(
            ReportSection("qc_notes", "품질 확인 및 검토사항", paragraphs=[fallback["qc_notes"]])
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

    def _current_peak_section(self, verdict: dict[str, Any]) -> ReportSection:
        rows = _figure_peak_rows(verdict, x_label="cm-1")
        if not rows:
            return ReportSection(
                "current_peaks",
                "현재 그래프 피크",
                paragraphs=["현재 그래프에 표시된 보고서용 피크 정보가 없습니다."],
            )
        table = ReportTable(columns=["시료", "Wavenumber", "피크 이름", "라이브러리"], rows=rows)
        return ReportSection("current_peaks", "현재 그래프 피크", table=table)

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
        key_findings = (
            f"신뢰도 판정은 {tier}입니다. "
            f"최상위 라이브러리 후보는 {material}이며 종합 점수는 {composite}입니다. "
            "작용기 소견과 라이브러리 매칭 결과를 함께 검토해야 합니다."
        )
        interpretation = (
            "검출 피크와 작용기 후보를 라이브러리 매칭 결과와 대조한 해석입니다. "
            "제시된 후보는 자동 분석 결과이며 분석자 검토 전 확정 동정으로 사용하지 않습니다."
        )
        qc_notes = (
            "정량 분석, 혼합물 분리, 단일 피크 기반 확정 동정은 보고 범위에 포함하지 않습니다. "
            "필요 시 원시 스펙트럼, 반복 측정, 보완 분석으로 확인하십시오."
        )
        narrative = (
            "규칙 기반 작용기 소견과 라이브러리 매칭 점수를 종합한 결과입니다. "
            "구체적 근거는 작용기 소견 및 라이브러리 매칭 표를 참고하십시오."
        )
        caption = f"{sample} FT-IR 분석 — 최상위 후보 {material}(참고용)"
        email_subject = f"[RIST] {sample} FT-IR 분석 보고서"
        email_body = (
            f"{sample} 시료의 FT-IR 분석 보고서를 첨부드립니다.\n\n"
            f"- 신뢰도 판정: {tier}\n"
            f"- 최상위 후보: {material} (종합 {composite})\n"
            "- 본 결과는 자동 분석 기반 참고 소견이며, 확정 동정은 분석자 검토 후 판단해 주십시오."
        )
        return {
            "summary": summary,
            "key_findings": key_findings,
            "interpretation": interpretation,
            "qc_notes": qc_notes,
            "narrative": narrative,
            "caption": caption,
            "email_subject": email_subject,
            "email_body": email_body,
        }

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
            "experiment_conditions": _experiment_condition_rows(verdict),
            "current_peaks": _figure_peak_facts(
                verdict,
                x_label="cm-1",
                max_items=40,
                compact=True,
            ),
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
            requested_slots=[
                "summary",
                "key_findings",
                "interpretation",
                "qc_notes",
                "narrative",
                "caption",
                "email_subject",
                "email_body",
            ],
            fallback=self._fallback_texts(verdict),
        )


class RamanReportBuilder(ReportBuilder):
    experiment_codes = frozenset({"RAMAN", "RIN", "RIN-RAMAN"})

    SYSTEM_PROMPT = (
        "당신은 재료분석 실험실의 Raman 보고서 작성 보조자입니다.\n"
        "제공된 피크, intensity 비율, Raman assignment 라이브러리 결과(JSON)만 근거로 한국어 문안을 작성하세요.\n"
        "제공되지 않은 상, 물질명, 조성, 원인을 새로 추측하지 마세요.\n"
        "current_peaks는 보고서 생성 시점의 그래프 화면에서 사용자가 편집/삭제/숨김 처리한 뒤 남은 피크입니다.\n"
        "피크명은 current_peaks.label을 우선 사용하고, original_label은 변경 전 추적용으로만 참고하세요.\n"
        "Raman 피크 assignment는 후보 소견으로 표현하고, 라이브러리 미적용/미매칭 피크는 단정하지 마세요.\n"
        "출력은 반드시 JSON 객체 하나로만 응답하세요.\n"
        "- summary: 고객 보고서용 요약 정확히 3문장\n"
        "- key_findings: 핵심 관찰사항 3~5개를 짧은 문장으로 작성\n"
        "- interpretation: Raman band/비율/라이브러리 근거를 연결한 해석(4문장 이내)\n"
        "- qc_notes: baseline, 스택 표시, 강도비 해석 한계 등 검토사항(3문장 이내)\n"
        "- narrative: 주요 근거 보조 설명(4문장 이내)\n"
        "- caption: 발표자료용 한 문장 캡션\n"
        "- email_subject: 메일 제목 1줄\n"
        "- email_body: 첨부 보고서 안내 메일 본문. Markdown 형식으로 8문장 이내"
    )

    def build(self, job: dict[str, Any], analysis: list[AnalysisItem]) -> ReportDocument:
        payload = _select_verdict(analysis) or {}
        document = ReportDocument(
            job_id=job["job_id"],
            title="Raman 분석 보고서",
            experiment_code=job["experiment_code"],
            pk={
                "requestNumber": job["request_number"],
                "experimentCode": job["experiment_code"],
                "equipmentCode": job["equipment_code"],
                "operatorId": job["operator_id"],
            },
            generated_at=job.get("_generated_at", ""),
            sections=self._meta_sections(job, payload),
        )
        document.sections.append(self._experiment_conditions_section(payload))
        document.sections.append(self._sample_section(payload))
        document.sections.append(self._library_section(payload))
        document.sections.append(self._peak_section(payload))

        fallback = self._fallback_texts(job, payload)
        document.sections.append(
            ReportSection("summary", "고객 보고서용 요약", paragraphs=[fallback["summary"]])
        )
        document.sections.append(
            ReportSection("key_findings", "핵심 관찰사항", paragraphs=[fallback["key_findings"]])
        )
        document.sections.append(
            ReportSection("interpretation", "Raman 피크 해석", paragraphs=[fallback["interpretation"]])
        )
        document.sections.append(
            ReportSection("qc_notes", "품질 확인 및 검토사항", paragraphs=[fallback["qc_notes"]])
        )
        document.sections.append(
            ReportSection("narrative", "보조 설명", paragraphs=[fallback["narrative"]])
        )
        document.sections.append(
            ReportSection("caption", "발표자료 캡션", paragraphs=[fallback["caption"]])
        )
        return document

    def _sample_section(self, payload: dict[str, Any]) -> ReportSection:
        samples = _as_list(payload.get("samples"))
        rows = []
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            rows.append(
                [
                    str(sample.get("label") or sample.get("fileName") or "-"),
                    str(sample.get("fileName") or "-"),
                    str(sample.get("pointCount") or "-"),
                    str(sample.get("peakCount") or "-"),
                ]
            )
        if not rows:
            return ReportSection("raman_samples", "시료 및 피크 수", paragraphs=["Raman 시료 요약 정보가 없습니다."])
        return ReportSection(
            "raman_samples",
            "시료 및 피크 수",
            table=ReportTable(["시료", "원본 파일", "데이터 포인트", "피크 수"], rows),
        )

    def _library_section(self, payload: dict[str, Any]) -> ReportSection:
        settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
        libraries = _as_list(settings.get("assignmentLibraries"))
        rows = []
        for library in libraries:
            if not isinstance(library, dict):
                continue
            rows.append(
                [
                    str(library.get("name") or library.get("id") or "-"),
                    str(library.get("id") or "-"),
                    str(library.get("assignmentCount") or 0),
                ]
            )
        if not rows:
            return ReportSection("raman_libraries", "적용 피크 라이브러리", paragraphs=["적용된 Raman 피크 라이브러리가 없습니다."])
        return ReportSection(
            "raman_libraries",
            "적용 피크 라이브러리",
            table=ReportTable(["라이브러리", "ID", "Assignment 수"], rows),
        )

    def _peak_section(self, payload: dict[str, Any]) -> ReportSection:
        rows = _figure_peak_rows(payload, x_label="cm-1")
        if not rows:
            return ReportSection("raman_peaks", "주요 Raman 피크", paragraphs=["보고서용 피크 상세 정보가 없습니다."])
        return ReportSection(
            "raman_peaks",
            "주요 Raman 피크",
            table=ReportTable(["시료", "Raman shift", "Assignment", "라이브러리"], rows),
        )

    def _fallback_texts(self, job: dict[str, Any], payload: dict[str, Any]) -> dict[str, str]:
        samples = _as_list(payload.get("samples"))
        sample_count = len(samples)
        sample_names = [
            str(item.get("label") or item.get("fileName"))
            for item in samples
            if isinstance(item, dict) and (item.get("label") or item.get("fileName"))
        ]
        sample_text = ", ".join(sample_names[:3]) if sample_names else "시료"
        if len(sample_names) > 3:
            sample_text += f" 외 {len(sample_names) - 3}개"
        total_peaks = sum(
            int(item.get("peakCount") or 0)
            for item in samples
            if isinstance(item, dict)
        )
        settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
        libraries = _as_list(settings.get("assignmentLibraries"))
        library_text = ", ".join(
            str(item.get("name") or item.get("id"))
            for item in libraries
            if isinstance(item, dict)
        ) or "미적용"
        summary = (
            f"{sample_text}에 대한 Raman 분석 결과를 정리했습니다. "
            f"총 {sample_count}개 시료에서 {total_peaks}개 피크 후보가 검출되었습니다. "
            "피크 assignment는 선택된 라이브러리 기반 후보 소견으로 해석해야 합니다."
        )
        key_findings = (
            f"분석 시료 수는 {sample_count}개입니다. "
            f"검출 피크 후보는 총 {total_peaks}개입니다. "
            f"적용 라이브러리는 {library_text}입니다."
        )
        interpretation = (
            "Raman band 위치와 상대 intensity를 기준으로 라이브러리 후보를 대조한 결과입니다. "
            "스택 표시는 시료 간 가독성을 위한 평행이동이므로 절대 intensity 비교에는 사용하지 않습니다."
        )
        qc_notes = (
            "Baseline 보정, smoothing, 피크 민감도 설정에 따라 약한 band 검출 수가 달라질 수 있습니다. "
            "정량적 강도비 해석은 동일 조건 측정과 분석자 검토가 필요합니다."
        )
        narrative = "구조화된 Raman 피크 분석 결과를 바탕으로 한 규칙 기반 요약입니다."
        caption = f"{sample_text} Raman 피크 분석 결과"
        email_subject = f"[RIST] {sample_text} Raman 분석 보고서"
        email_body = (
            f"{sample_text} Raman 분석 보고서를 첨부드립니다.\n\n"
            f"- 시료 수: {sample_count}\n"
            f"- 검출 피크 후보: {total_peaks}개\n"
            f"- 적용 라이브러리: {library_text}\n"
            "- 본 결과는 자동 피크 검출 및 라이브러리 후보 기반 참고 소견입니다."
        )
        return {
            "summary": summary,
            "key_findings": key_findings,
            "interpretation": interpretation,
            "qc_notes": qc_notes,
            "narrative": narrative,
            "caption": caption,
            "email_subject": email_subject,
            "email_body": email_body,
        }

    def llm_slots(self, job: dict[str, Any], analysis: list[AnalysisItem]) -> LlmSlotSpec | None:
        payload = _select_verdict(analysis)
        if not payload:
            return None
        settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
        facts = {
            "experiment": "RAMAN",
            "samples": payload.get("samples"),
            "settings": {
                "sensitivity": settings.get("sensitivity"),
                "height": settings.get("height"),
                "prominence": settings.get("prominence"),
                "baseline": settings.get("baseline"),
                "smooth": settings.get("smooth"),
                "assignmentLibraries": settings.get("assignmentLibraries"),
            },
            "experiment_conditions": _experiment_condition_rows(payload),
            "current_peaks": _figure_peak_facts(
                payload,
                x_label="cm-1",
                max_items=40,
                compact=True,
            ),
            "peak_assignments": _figure_peak_rows(payload, x_label="cm-1")[:12],
        }
        return LlmSlotSpec(
            system_prompt=self.SYSTEM_PROMPT,
            facts=facts,
            requested_slots=[
                "summary",
                "key_findings",
                "interpretation",
                "qc_notes",
                "narrative",
                "caption",
                "email_subject",
                "email_body",
            ],
            fallback=self._fallback_texts(job, payload),
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
        document.sections.append(self._experiment_conditions_section(verdict))
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
_RAMAN_BUILDER = RamanReportBuilder()
_GENERIC_BUILDER = GenericReportBuilder()


def get_builder(experiment_code: str) -> ReportBuilder:
    normalized = experiment_code.upper().replace("_", "-")
    if normalized in _FTIR_BUILDER.experiment_codes:
        return _FTIR_BUILDER
    if normalized in _RAMAN_BUILDER.experiment_codes:
        return _RAMAN_BUILDER
    return _GENERIC_BUILDER

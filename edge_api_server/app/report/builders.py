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

_FTIR_CONDITION_ORDER = [
    "장비모델",
    "Type",
    "Detector",
    "Crystal",
    "Resolution",
    "Scan time",
    "Range",
]
_FTIR_CONDITION_ALIASES = {
    "장비모델": [
        "장비모델",
        "장비 모델",
        "equipment model",
        "instrument model",
        "instrument",
        "spectrometer",
        "model",
    ],
    "Type": [
        "type",
        "measurement type",
        "method",
        "technique",
        "measurement mode",
        "sampling mode",
        "sampling method",
        "accessory",
        "측정조건",
        "분석방법",
    ],
    "Detector": ["detector", "검출기"],
    "Crystal": ["crystal", "atr crystal", "crystal type", "크리스탈"],
    "Resolution": ["resolution", "spectral resolution", "resolving power", "해상도"],
    "Scan time": [
        "scan time",
        "scan times",
        "scans",
        "number of scans",
        "scan number",
        "sample scans",
        "accumulation",
        "스캔",
        "스캔수",
    ],
    "Range": [
        "range",
        "spectral range",
        "wavenumber range",
        "data range",
        "측정범위",
        "범위",
    ],
}
_RAMAN_CONDITION_ORDER = [
    "Excitation Wavelength",
    "Laser current",
    "Excitation Power",
    "Excitation Power density",
    "ND filter",
    "Spectrograph Center wavelength",
    "Grating",
    "Slit width",
    "기타",
]
_RAMAN_CONDITION_ALIASES = {
    "Excitation Wavelength": [
        "excitation wavelength",
        "laser wavelength",
        "wavelength",
        "laser",
        "excitation",
        "여기 파장",
        "레이저 파장",
        "레이저",
    ],
    "Laser current": [
        "laser current",
        "current",
        "laser diode current",
        "diode current",
        "레이저 전류",
        "전류",
    ],
    "Excitation Power": [
        "excitation power",
        "laser power",
        "power",
        "laser output",
        "레이저 파워",
        "레이저 출력",
        "출력",
    ],
    "Excitation Power density": [
        "excitation power density",
        "laser power density",
        "power density",
        "irradiance",
        "레이저 파워 밀도",
        "출력 밀도",
    ],
    "ND filter": [
        "nd filter",
        "neutral density filter",
        "neutral density",
        "nd",
        "n.d. filter",
        "필터",
        "nd 필터",
    ],
    "Spectrograph Center wavelength": [
        "spectrograph center wavelength",
        "spectrograph centre wavelength",
        "center wavelength",
        "centre wavelength",
        "central wavelength",
        "spectrograph center",
        "spectrograph centre",
        "분광기 중심 파장",
        "중심 파장",
    ],
    "Grating": [
        "grating",
        "grating density",
        "grating groove",
        "grooves",
        "그레이팅",
    ],
    "Slit width": [
        "slit width",
        "slit",
        "entrance slit",
        "slit size",
        "슬릿 폭",
        "슬릿",
    ],
    "기타": [
        "기타",
        "other",
        "notes",
        "condition detail",
        "experiment detail",
        "experimental detail",
        "실험환경 상세",
        "비고",
    ],
}

_PLACEHOLDER_JOB_VALUES = {"", "-", "WEB-PREVIEW", "web-preview", "None", "none", "null"}


def _is_placeholder_job_value(value: Any) -> bool:
    return str(value or "").strip() in _PLACEHOLDER_JOB_VALUES


def _job_meta_value(
    job: dict[str, Any],
    key: str,
    *,
    fallback: str = "",
) -> str:
    value = str(job.get(key) or "").strip()
    if _is_placeholder_job_value(value):
        return fallback
    return value


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


def _sample_names(payload: dict[str, Any]) -> list[str]:
    samples = _as_list(payload.get("samples"))
    names = [
        str(item.get("label") or item.get("fileName") or "").strip()
        for item in samples
        if isinstance(item, dict) and (item.get("label") or item.get("fileName"))
    ]
    if not names and payload.get("sample"):
        names.append(str(payload.get("sample")).strip())
    return [name for name in names if name]


def _sample_text(payload: dict[str, Any], *, empty: str = "시료") -> str:
    names = _sample_names(payload)
    if not names:
        return empty
    text = ", ".join(names[:3])
    if len(names) > 3:
        text += f" 외 {len(names) - 3}개"
    return text


def _total_sample_peak_count(payload: dict[str, Any]) -> int:
    total = 0
    for sample in _as_list(payload.get("samples")):
        if not isinstance(sample, dict):
            continue
        try:
            total += int(sample.get("peakCount") or 0)
        except (TypeError, ValueError):
            continue
    return total


def _current_peak_count(payload: dict[str, Any], *, x_label: str = "cm-1") -> int:
    return len(_figure_peak_facts(payload, x_label=x_label, max_items=500))


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


def _format_cm1(value: Any) -> str:
    try:
        return f"{float(value):.1f} cm⁻¹"
    except (TypeError, ValueError):
        return "-"


def _clean_report_text(value: Any) -> str:
    text = _clean_html_text(value)
    if text == "-":
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _truncate_text(value: Any, *, max_chars: int = 220) -> str:
    text = _clean_report_text(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _has_library_term(value: Any) -> bool:
    text = str(value or "").lower()
    return "라이브러리" in text or "library" in text


def _first_dict(items: Any) -> dict[str, Any]:
    for item in _as_list(items):
        if isinstance(item, dict):
            return item
    return {}


def _primary_rule_match(verdict: dict[str, Any]) -> dict[str, Any]:
    rule_matches = _as_list(verdict.get("rule_matches"))
    for item in rule_matches:
        if isinstance(item, dict) and "Rule Match" in str(item.get("verdict") or ""):
            return item
    return _first_dict(rule_matches)


def _rule_material_name(verdict: dict[str, Any]) -> str:
    cv = verdict.get("combined_verdict") if isinstance(verdict.get("combined_verdict"), dict) else {}
    profile = cv.get("product_profile") if isinstance(cv.get("product_profile"), dict) else {}
    if profile.get("summary"):
        return _material_display_text(_clean_report_text(profile.get("summary")))
    if profile.get("base_material"):
        return _material_display_text(_clean_report_text(profile.get("base_material")))
    rule = _primary_rule_match(verdict)
    return _material_display_text(_clean_report_text(
        rule.get("compound_display")
        or rule.get("compound")
        or cv.get("verdict")
        or "후보 물질"
    ))


def _rule_base_material_name(verdict: dict[str, Any]) -> str:
    cv = verdict.get("combined_verdict") if isinstance(verdict.get("combined_verdict"), dict) else {}
    profile = cv.get("product_profile") if isinstance(cv.get("product_profile"), dict) else {}
    if profile.get("base_material"):
        return _material_display_text(_clean_report_text(profile.get("base_material")))
    rule = _primary_rule_match(verdict)
    return _material_display_text(
        _clean_report_text(rule.get("compound_display") or rule.get("compound") or "후보 물질")
    )


def _material_display_text(value: str) -> str:
    if not value:
        return value
    return value.replace("Phenolic Foam", "페놀폼(Phenolic Foam)")


def _ftir_label_text(value: Any) -> str:
    text = _clean_report_text(value)
    replacements = {
        "O-H / N-H stretching": "O-H/N-H 신축",
        "CH2 asymmetric & symmetric stretching": "CH₂ 신축",
        "C=C aromatic ring stretching": "방향족 C=C 신축",
        "CH2 bending (scissoring)": "CH₂ 굽힘",
        "C-O stretching (phenolic)": "페놀릭 C-O 신축",
        "C-H out-of-plane bending": "방향족 C-H 면외 굽힘",
        "S=O stretching (sulfonic acid)": "S=O 신축",
        "S=O stretch": "S=O 신축",
        "C=O stretching (ester/ketone — strong)": "C=O 신축",
        "C=O stretch": "카보닐기(C=O) 신축",
    }
    return replacements.get(text, text)


def _rule_score_text(verdict: dict[str, Any]) -> str:
    rule = _primary_rule_match(verdict)
    score = rule.get("score_pct")
    try:
        return f"{float(score):.1f}%"
    except (TypeError, ValueError):
        return ""


def _rule_required_evidence(verdict: dict[str, Any], *, max_items: int = 6) -> list[str]:
    rule = _primary_rule_match(verdict)
    evidence: list[str] = []
    for item in _as_list(rule.get("matched_required")):
        if not isinstance(item, dict):
            continue
        label = _ftir_label_text(item.get("label"))
        center = _format_cm1(item.get("center"))
        if label and center != "-":
            evidence.append(f"{label}({center})")
        elif label:
            evidence.append(label)
        if len(evidence) >= max_items:
            break
    return evidence


def _marker_evidence_text(marker: dict[str, Any]) -> str:
    evidence = _first_dict(marker.get("evidence"))
    assignment = _ftir_label_text(
        evidence.get("assignment")
        or marker.get("assignment")
        or marker.get("label")
        or marker.get("name")
    )
    wn = evidence.get("wn", marker.get("center"))
    wn_text = _format_cm1(wn)
    if assignment and wn_text != "-":
        return f"{assignment} 피크({wn_text})"
    if wn_text != "-":
        return f"{wn_text} 피크"
    return assignment or _clean_report_text(marker.get("name"))


def _ftir_process_markers(verdict: dict[str, Any]) -> list[dict[str, str]]:
    findings = verdict.get("findings") if isinstance(verdict.get("findings"), dict) else {}
    markers: list[dict[str, str]] = []
    for item in _as_list(findings.get("additives_and_process_markers")):
        if not isinstance(item, dict):
            continue
        markers.append(
            {
                "name": _clean_report_text(item.get("name")),
                "confidence": _clean_report_text(item.get("confidence")),
                "evidence": _marker_evidence_text(item),
                "interpretation": _truncate_text(item.get("interpretation")),
            }
        )
    rule = _primary_rule_match(verdict)
    for item in _as_list(rule.get("matched_context_markers")):
        if not isinstance(item, dict):
            continue
        marker_name = _clean_report_text(item.get("assignment") or item.get("label"))
        if any(marker["name"] == marker_name for marker in markers):
            continue
        markers.append(
            {
                "name": marker_name,
                "confidence": "",
                "evidence": _marker_evidence_text(item),
                "interpretation": _truncate_text(item.get("interpretation")),
            }
        )
    return [marker for marker in markers if marker.get("name")]


def _ftir_warning_markers(verdict: dict[str, Any]) -> list[dict[str, str]]:
    findings = verdict.get("findings") if isinstance(verdict.get("findings"), dict) else {}
    warnings: list[dict[str, str]] = []
    for item in _as_list(findings.get("mismatch_warnings")):
        if not isinstance(item, dict):
            continue
        note = _clean_report_text(item.get("note"))
        note = re.sub(r"최상위 후보[^—.。]*[—-]?\s*", "", note).strip()
        warnings.append(
            {
                "name": _clean_report_text(item.get("region")),
                "evidence": f"{_format_cm1(item.get('sample_peak_cm'))} 피크",
                "interpretation": _truncate_text(note),
            }
        )
    rule = _primary_rule_match(verdict)
    for item in _as_list(rule.get("triggered_warnings")):
        if not isinstance(item, dict):
            continue
        warnings.append(
            {
                "name": _clean_report_text(item.get("label")),
                "evidence": _marker_evidence_text(item),
                "interpretation": _truncate_text(item.get("assignment")),
            }
        )
    return [item for item in warnings if item.get("name") or item.get("evidence")]


def _ftir_functional_group_lines(verdict: dict[str, Any], *, max_items: int = 4) -> list[str]:
    findings = verdict.get("findings") if isinstance(verdict.get("findings"), dict) else {}
    lines: list[str] = []
    for item in _as_list(findings.get("functional_groups")):
        if not isinstance(item, dict):
            continue
        group = _clean_report_text(item.get("group"))
        confidence = _pct(item.get("confidence_pct"))
        evidence = _truncate_text(item.get("evidence"), max_chars=140)
        if group and evidence:
            lines.append(f"{group}({confidence}): {evidence}")
        elif group:
            lines.append(f"{group}({confidence})")
        if len(lines) >= max_items:
            break
    return lines


def _compact_rule_evidence(verdict: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = []
    cv = verdict.get("combined_verdict") if isinstance(verdict.get("combined_verdict"), dict) else {}
    for item in _as_list(cv.get("rule_evidence_summary"))[:14]:
        if not isinstance(item, dict):
            continue
        evidence.append(
            {
                "role": item.get("role"),
                "label": item.get("label"),
                "center": _rounded_number(_first_numeric(item.get("center"))),
                "intensity": _rounded_number(_first_numeric(item.get("intensity"))),
                "interpretation": item.get("interpretation"),
            }
        )
    if evidence:
        return evidence
    rule = _primary_rule_match(verdict)
    for role, key in (("required", "matched_required"), ("supporting", "matched_supporting")):
        for item in _as_list(rule.get(key))[:8]:
            if not isinstance(item, dict):
                continue
            evidence.append(
                {
                    "role": role,
                    "label": item.get("label"),
                    "center": _rounded_number(_first_numeric(item.get("center"))),
                    "intensity": _rounded_number(_first_numeric(item.get("intensity"))),
                }
            )
    return evidence[:14]


def _raman_peak_label(peak: dict[str, Any]) -> str:
    label = _clean_report_text(peak.get("label"))
    original = _clean_report_text(peak.get("original_label"))
    assignments = [
        _clean_report_text(item)
        for item in _as_list(peak.get("assignment_names"))
        if _clean_report_text(item)
    ]
    if label and label != "-":
        return _raman_formula_text(label)
    if assignments:
        return _raman_formula_text(assignments[0])
    return _raman_formula_text(original) or "미지정 band"


def _raman_formula_text(value: str) -> str:
    replacements = {
        "Li2CO3": "Li₂CO₃",
        "Li2SO4": "Li₂SO₄",
        "Li2S": "Li₂S",
        "CO3": "CO₃",
        "SO4": "SO₄",
    }
    text = value
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def _raman_peak_phrase(peak: dict[str, Any]) -> str:
    position = _format_cm1(peak.get("position"))
    label = _raman_peak_label(peak)
    if position != "-":
        return f"{position} {label}"
    return label


def _raman_peaks_by_sample(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for peak in _figure_peak_facts(payload, x_label="cm-1", max_items=500):
        sample = _clean_report_text(peak.get("sample")) or "시료"
        grouped.setdefault(sample, []).append(peak)
    for peaks in grouped.values():
        peaks.sort(
            key=lambda item: (
                -float(item.get("base_intensity") or item.get("display_intensity") or 0),
                float(item.get("position") or 0),
            )
        )
    return grouped


def _raman_primary_peak_lines(payload: dict[str, Any], *, max_samples: int = 3, max_peaks: int = 3) -> list[str]:
    lines: list[str] = []
    for sample, peaks in list(_raman_peaks_by_sample(payload).items())[:max_samples]:
        phrases = [_raman_peak_phrase(peak) for peak in peaks[:max_peaks]]
        if phrases:
            lines.append(f"{sample}: " + ", ".join(phrases))
    return lines


def _raman_assignment_count(payload: dict[str, Any]) -> int:
    count = 0
    for peak in _figure_peak_facts(payload, x_label="cm-1", max_items=500):
        if peak.get("assignment_names") or _raman_peak_label(peak) not in {"미지정 band", "peak"}:
            count += 1
    return count


def _raman_ratio_annotations(payload: dict[str, Any]) -> list[str]:
    figure = payload.get("figure") if isinstance(payload.get("figure"), dict) else {}
    layout = figure.get("layout") if isinstance(figure.get("layout"), dict) else {}
    ratios: list[str] = []
    for annotation in _as_list(layout.get("annotations")):
        if not isinstance(annotation, dict):
            continue
        name = str(annotation.get("name") or "")
        if not name.startswith("rist_raman_ratio:"):
            continue
        text = _clean_report_text(annotation.get("text"))
        if text:
            ratios.append(text)
    return list(dict.fromkeys(ratios))[:6]


def _raman_condition_summary(payload: dict[str, Any]) -> str:
    rows = _experiment_condition_rows(
        payload,
        aliases=_RAMAN_CONDITION_ALIASES,
        preferred_order=_RAMAN_CONDITION_ORDER,
    )
    if not rows:
        return ""
    preferred = {_metadata_label_key(item) for item in _RAMAN_CONDITION_ORDER[:-1]}
    selected: list[str] = []
    for _source, key, value in rows:
        key_text = _clean_report_text(key)
        if _metadata_label_key(key_text) in preferred:
            selected.append(f"{key_text} {value}")
        if len(selected) >= 4:
            break
    if not selected:
        selected = [f"{key} {value}" for _source, key, value in rows[:4]]
    return ", ".join(selected)


def _raman_display_settings(payload: dict[str, Any]) -> dict[str, Any]:
    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
    figure = payload.get("figure") if isinstance(payload.get("figure"), dict) else {}
    layout = figure.get("layout") if isinstance(figure.get("layout"), dict) else {}
    meta = layout.get("meta") if isinstance(layout.get("meta"), dict) else {}
    stack = meta.get("ristRamanStack") if isinstance(meta.get("ristRamanStack"), dict) else {}
    return {
        "sensitivity": settings.get("sensitivity"),
        "height": settings.get("height"),
        "prominence": settings.get("prominence"),
        "baseline": settings.get("baseline"),
        "smooth": settings.get("smooth"),
        "stack_enabled": bool(stack.get("enabled")),
        "stack_gap": stack.get("gap"),
    }


def _raman_has_carbon_dg(payload: dict[str, Any]) -> bool:
    labels = " ".join(
        _raman_peak_label(peak).lower()
        for peak in _figure_peak_facts(payload, x_label="cm-1", max_items=500)
    )
    return "d band" in labels and "g band" in labels


def _raman_has_lithium_compound(payload: dict[str, Any]) -> bool:
    labels = " ".join(
        _raman_peak_label(peak).lower()
        for peak in _figure_peak_facts(payload, x_label="cm-1", max_items=500)
    )
    return any(token in labels for token in ("lioh", "li2co3", "li₂co₃", "li2s", "lpscl", "li2so4"))


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
            ]
        )
        if len(rows) >= 18:
            break
    return rows


def _figure_layout(payload: dict[str, Any]) -> dict[str, Any]:
    figure = payload.get("figure")
    if not isinstance(figure, dict):
        return {}
    layout = figure.get("layout")
    return layout if isinstance(layout, dict) else {}


def _figure_data(payload: dict[str, Any]) -> list[Any]:
    figure = payload.get("figure")
    if not isinstance(figure, dict):
        return []
    return _as_list(figure.get("data"))


def _axis_title_text(layout: dict[str, Any], axis: str) -> str:
    axis_data = layout.get(axis)
    if not isinstance(axis_data, dict):
        return ""
    title = axis_data.get("title")
    if isinstance(title, dict):
        return _clean_report_text(title.get("text"))
    return _clean_report_text(title)


def _number_text(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value or "").strip()
    if abs(number) >= 100:
        return f"{number:.0f}"
    return f"{number:.3g}"


def _axis_range_text(layout: dict[str, Any]) -> str:
    parts: list[str] = []
    for axis, label in (("xaxis", "x"), ("yaxis", "y")):
        axis_data = layout.get(axis)
        if not isinstance(axis_data, dict):
            continue
        value = axis_data.get("range")
        if not isinstance(value, list) or len(value) < 2:
            continue
        first = _number_text(value[0])
        second = _number_text(value[1])
        if first and second:
            parts.append(f"{label} {first}~{second}")
    return ", ".join(parts)


def _label_indices_from_layout(layout: dict[str, Any]) -> tuple[set[int], set[int]]:
    meta = layout.get("meta") if isinstance(layout.get("meta"), dict) else {}
    labels = _as_list(meta.get("ristPeakLabels"))
    annotation_indices: set[int] = set()
    shape_indices: set[int] = set()
    for item in labels:
        if not isinstance(item, dict):
            continue
        try:
            annotation_indices.add(int(item["annotationIndex"]))
        except (KeyError, TypeError, ValueError):
            pass
        try:
            shape_indices.add(int(item["shapeIndex"]))
        except (KeyError, TypeError, ValueError):
            pass
    return annotation_indices, shape_indices


def _named_layout_item_is_ratio(item: Any) -> bool:
    return isinstance(item, dict) and str(item.get("name") or "").startswith("rist_raman_ratio:")


def _user_annotation_summary(layout: dict[str, Any]) -> dict[str, Any]:
    peak_annotations, peak_shapes = _label_indices_from_layout(layout)
    annotations = _as_list(layout.get("annotations"))
    shapes = _as_list(layout.get("shapes"))
    text_boxes: list[str] = []
    for index, annotation in enumerate(annotations):
        if not isinstance(annotation, dict):
            continue
        if index in peak_annotations or _named_layout_item_is_ratio(annotation):
            continue
        text = _truncate_text(annotation.get("text"), max_chars=80)
        if text:
            text_boxes.append(text)
    shape_count = 0
    for index, shape in enumerate(shapes):
        if not isinstance(shape, dict):
            continue
        if index in peak_shapes or _named_layout_item_is_ratio(shape):
            continue
        shape_count += 1
    return {
        "text_box_count": len(text_boxes),
        "text_boxes": text_boxes[:5],
        "shape_count": shape_count,
    }


def _sample_visibility_summary(payload: dict[str, Any]) -> dict[str, Any]:
    visible: list[str] = []
    seen: set[str] = set()
    for trace in _figure_data(payload):
        if not isinstance(trace, dict):
            continue
        meta = trace.get("meta")
        if not isinstance(meta, dict):
            continue
        group = str(meta.get("rist_sample_group") or "")
        if not group or not meta.get("rist_sample_parent") or group in seen:
            continue
        seen.add(group)
        name = _clean_html_text(trace.get("name") or group)
        if _trace_is_visible(trace):
            visible.append(name)
    if not visible:
        visible = _sample_names(payload)
    return {"visible": visible}


def _grouped_peak_summary(peaks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for peak in peaks:
        group = _clean_report_text(peak.get("group_name"))
        if not group:
            continue
        item = groups.setdefault(
            group,
            {"name": group, "count": 0, "color": peak.get("group_color")},
        )
        item["count"] += 1
        if not item.get("color") and peak.get("group_color"):
            item["color"] = peak.get("group_color")
    return list(groups.values())


def _trace_color(trace: dict[str, Any]) -> str:
    for key in ("line", "marker"):
        value = trace.get(key)
        if isinstance(value, dict) and value.get("color"):
            return str(value.get("color"))
    if trace.get("fillcolor"):
        return str(trace.get("fillcolor"))
    return ""


def _trace_color_summary(payload: dict[str, Any]) -> dict[str, Any]:
    sample_colors: list[dict[str, str]] = []
    peak_colors: list[dict[str, str]] = []
    for trace in _figure_data(payload):
        if not isinstance(trace, dict):
            continue
        color = _trace_color(trace)
        if not color:
            continue
        meta = trace.get("meta")
        if not isinstance(meta, dict):
            continue
        name = _clean_html_text(trace.get("name"))
        if meta.get("rist_sample_parent"):
            sample_colors.append({"name": name, "color": color})
        elif isinstance(meta.get("rist_peak"), dict):
            peak_colors.append({"name": name, "color": color})
    return {
        "sample_colors": sample_colors[:8],
        "peak_colors": peak_colors[:8],
    }


def _graph_interpretation_points(summary: dict[str, Any]) -> list[str]:
    points: list[str] = []
    grouped = _as_list(summary.get("grouped_peaks"))
    for item in grouped[:4]:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        points.append(
            f"피크 그룹 '{item['name']}'은 사용자가 같은 해석 축으로 묶은 피크 {item.get('count', 0)}개입니다."
        )
    for ratio in _as_list(summary.get("ratio_annotations"))[:4]:
        points.append(f"강도비 '{ratio}'는 사용자가 선택한 두 피크의 상대 intensity 비교 지표입니다.")
    for text in _as_list(summary.get("text_boxes"))[:4]:
        points.append(f"텍스트 박스 주석 '{text}'는 사용자가 그래프 위에 추가한 해석 메모입니다.")
    shape_count = int(summary.get("shape_count") or 0)
    if shape_count:
        points.append(f"사각형/도형 {shape_count}개는 사용자가 표시한 관심 영역 또는 강조 구간입니다.")
    if summary.get("sample_colors") or summary.get("peak_colors") or any(
        isinstance(item, dict) and item.get("color") for item in grouped
    ):
        points.append("색상 변경은 시료, 피크 또는 그룹을 시각적으로 구분하기 위한 표시 상태입니다.")
    return points[:10]


def _graph_display_mode(payload: dict[str, Any], *, experiment_code: str) -> str:
    layout = _figure_layout(payload)
    y_title = _axis_title_text(layout, "yaxis").lower()
    normalized = experiment_code.upper().replace("_", "-")
    if normalized in {"FTIR", "FT-IR", "IR"}:
        if "transmittance" in y_title or "투과" in y_title:
            return "투과도 보기"
        if "absorbance" in y_title or "흡광" in y_title:
            return "흡광도 보기"
    if normalized in {"RAMAN", "RIN", "RIN-RAMAN"}:
        display = _raman_display_settings(payload)
        if display.get("stack_enabled"):
            gap = display.get("stack_gap")
            return f"스택 보기(gap {gap})" if gap not in {None, ""} else "스택 보기"
        return "일반 보기"
    return ""


def _graph_operation_summary(
    payload: dict[str, Any],
    *,
    experiment_code: str,
    x_label: str,
) -> dict[str, Any]:
    layout = _figure_layout(payload)
    samples = _sample_visibility_summary(payload)
    visible_peaks = _figure_peak_facts(payload, x_label=x_label, max_items=500)
    renamed = [
        {
            "sample": peak.get("sample"),
            "position": _rounded_number(_first_numeric(peak.get("position"))),
            "from": peak.get("original_label"),
            "to": peak.get("label"),
        }
        for peak in visible_peaks
        if peak.get("label")
        and peak.get("original_label")
        and peak.get("label") != peak.get("original_label")
    ]
    user_added = [
        {
            "sample": peak.get("sample"),
            "position": _rounded_number(_first_numeric(peak.get("position"))),
            "label": peak.get("label"),
        }
        for peak in visible_peaks
        if peak.get("is_user_added")
    ]
    annotations = _user_annotation_summary(layout)
    color_summary = _trace_color_summary(payload)
    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
    result = {
        "display_mode": _graph_display_mode(payload, experiment_code=experiment_code),
        "sensitivity": settings.get("sensitivity"),
        "visible_samples": samples["visible"][:10],
        "visible_peak_count": len(visible_peaks),
        "renamed_peaks": renamed[:8],
        "user_added_peaks": user_added[:8],
        "grouped_peaks": _grouped_peak_summary(visible_peaks)[:8],
        "ratio_annotations": _raman_ratio_annotations(payload)
        if experiment_code.upper().replace("_", "-") in {"RAMAN", "RIN", "RIN-RAMAN"}
        else [],
        "axis_range": _axis_range_text(layout),
        "text_box_count": annotations["text_box_count"],
        "text_boxes": annotations["text_boxes"],
        "shape_count": annotations["shape_count"],
        "sample_colors": color_summary["sample_colors"],
        "peak_colors": color_summary["peak_colors"],
        "display_settings": _raman_display_settings(payload)
        if experiment_code.upper().replace("_", "-") in {"RAMAN", "RIN", "RIN-RAMAN"}
        else {},
    }
    result["interpretation_points"] = _graph_interpretation_points(result)
    return result


def _graph_operation_lines(
    payload: dict[str, Any],
    *,
    experiment_code: str,
    x_label: str,
) -> list[str]:
    summary = _graph_operation_summary(payload, experiment_code=experiment_code, x_label=x_label)
    lines: list[str] = []
    visible_samples = _as_list(summary.get("visible_samples"))
    if visible_samples:
        lines.append(f"현재 표시 상태: 시료 {len(visible_samples)}개 표시")

    visible_peaks = int(summary.get("visible_peak_count") or 0)
    if visible_peaks:
        lines.append(f"보고서 반영 기준: 현재 그래프 표시 피크 {visible_peaks}개")

    display_parts = []
    if summary.get("display_mode"):
        display_parts.append(str(summary["display_mode"]))
    if summary.get("sensitivity") not in {None, ""}:
        display_parts.append(f"피크 민감도 {summary['sensitivity']}")
    if display_parts:
        lines.append("표시/전처리 설정: " + ", ".join(display_parts))

    edits: list[str] = []
    user_added = _as_list(summary.get("user_added_peaks"))
    renamed = _as_list(summary.get("renamed_peaks"))
    grouped = _as_list(summary.get("grouped_peaks"))
    if user_added:
        edits.append(f"사용자 추가 {len(user_added)}개")
    if renamed:
        edits.append(f"이름 수정 {len(renamed)}개")
    if grouped:
        edits.append(f"그룹 적용 {len(grouped)}개")
    if edits:
        lines.append("사용자 피크 편집: " + ", ".join(edits))
    if renamed:
        examples = [
            f"{item.get('from')} -> {item.get('to')}"
            for item in renamed[:3]
            if isinstance(item, dict) and item.get("from") and item.get("to")
        ]
        if examples:
            lines.append("피크명 수정 예: " + "; ".join(examples))
    if grouped:
        groups = [
            f"{item.get('name')} {item.get('count')}개"
            for item in grouped[:4]
            if isinstance(item, dict) and item.get("name")
        ]
        if groups:
            lines.append("피크 그룹 해석: " + ", ".join(groups) + "를 같은 해석 축으로 비교하도록 지정")

    if summary.get("sample_colors") or summary.get("peak_colors") or any(
        isinstance(item, dict) and item.get("color") for item in grouped
    ):
        lines.append("색상 표시: 시료/피크 trace 색상과 그룹 색상은 현재 그래프 이미지에 반영됨")

    ratios = _as_list(summary.get("ratio_annotations"))
    if ratios:
        lines.append("강도비 해석: " + ", ".join(str(item) for item in ratios[:3]) + "를 상대 intensity 비교 지표로 반영")

    text_count = int(summary.get("text_box_count") or 0)
    shape_count = int(summary.get("shape_count") or 0)
    text_boxes = _as_list(summary.get("text_boxes"))
    if text_count:
        examples = "; ".join(str(item) for item in text_boxes[:3])
        suffix = f" ({examples})" if examples else ""
        lines.append(f"텍스트 박스 주석: {text_count}개{suffix}를 사용자가 추가한 해석 메모로 반영")
    if shape_count:
        lines.append(f"사각형/도형 강조: {shape_count}개 영역을 사용자가 지정한 관심 구간으로 반영")

    if summary.get("axis_range"):
        lines.append(f"현재 축 범위: {summary['axis_range']}")

    if not lines:
        lines.append("사용자 그룹, 강도비, 텍스트 박스, 사각형/도형 주석이 없어 raw 분석 결과와 현재 표시 피크를 기준으로 작성되었습니다.")
    return lines


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


def _metadata_label_key(value: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", value.lower())


def _condition_alias_lookup(aliases: dict[str, list[str]] | None) -> dict[str, str]:
    lookup: dict[str, str] = {}
    if not aliases:
        return lookup
    for canonical, names in aliases.items():
        lookup[_metadata_label_key(canonical)] = canonical
        for name in names:
            lookup[_metadata_label_key(name)] = canonical
    return lookup


def _condition_label(label: str, lookup: dict[str, str]) -> str:
    key = _metadata_label_key(label)
    if key in lookup:
        return lookup[key]
    for alias_key, canonical in lookup.items():
        if alias_key and (alias_key in key or key in alias_key):
            return canonical
    return label


def _sort_condition_rows(
    rows: list[list[str]],
    preferred_order: list[str] | None,
) -> list[list[str]]:
    if not preferred_order:
        return rows
    order = {label: index for index, label in enumerate(preferred_order)}
    indexed = list(enumerate(rows))
    indexed.sort(
        key=lambda item: (
            0 if item[1][1] in order and item[1][0] == "공통" else 1,
            order.get(item[1][1], len(order)),
            item[0],
        )
    )
    return [row for _, row in indexed]


def _experiment_condition_rows(
    payload: dict[str, Any],
    *,
    aliases: dict[str, list[str]] | None = None,
    preferred_order: list[str] | None = None,
) -> list[list[str]]:
    rows: list[list[str]] = []
    lookup = _condition_alias_lookup(aliases)
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
            rows.append(["공통", _condition_label(item_key, lookup), item_value])

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
                label = _condition_label(item_key, lookup)
                row = (source, label, item_value)
                if row not in seen:
                    seen.add(row)
                    rows.append([source, label, item_value])
    return _sort_condition_rows(rows, preferred_order)


class ReportBuilder:
    """보고서 작성기 베이스."""

    experiment_codes: frozenset[str] = frozenset()

    def _meta_sections(
        self, job: dict[str, Any], verdict: dict[str, Any]
    ) -> list[ReportSection]:
        sample = _sample_text(verdict, empty="(미기재)") if verdict else "(미기재)"
        request_number = _job_meta_value(
            job,
            "request_number",
            fallback="Spring Boot 연동 후 확정",
        )
        equipment = _job_meta_value(job, "equipment_code")
        operator = _job_meta_value(
            job,
            "operator_id",
            fallback="회원/SSO 연동 후 확정",
        )
        bullets = [
            f"시료: {sample}",
            f"의뢰번호: {request_number}",
            f"실험코드: {job['experiment_code']}",
        ]
        if equipment:
            bullets.append(f"장비: {equipment}")
        if operator:
            bullets.append(f"실험자: {operator}")
        return [
            ReportSection(
                section_id="sample_info",
                heading="시료 정보",
                bullets=bullets,
            )
        ]

    def _experiment_conditions_section(
        self,
        payload: dict[str, Any],
        *,
        aliases: dict[str, list[str]] | None = None,
        preferred_order: list[str] | None = None,
    ) -> ReportSection:
        rows = _experiment_condition_rows(
            payload,
            aliases=aliases,
            preferred_order=preferred_order,
        )
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

    def _graph_state_section(
        self,
        payload: dict[str, Any],
        *,
        experiment_code: str,
        x_label: str = "cm-1",
    ) -> ReportSection:
        return ReportSection(
            "graph_state",
            "그래프 주석 및 해석 포인트",
            bullets=_graph_operation_lines(
                payload,
                experiment_code=experiment_code,
                x_label=x_label,
            ),
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
        "제공된 피크 정보, assignment 결과, 실험조건(JSON)만 근거로 한국어 문안을 작성하세요.\n"
        "수치를 재계산하거나 제공되지 않은 물질, 피크, 작용기를 추측하지 마세요.\n"
        "current_peaks는 보고서 생성 시점의 그래프 화면에서 사용자가 표시 중인 최종 피크입니다.\n"
        "피크명은 current_peaks.label을 우선 사용하고, original_label은 변경 전 추적용으로만 참고하세요.\n"
        "graph_operations는 보고서 생성 시점의 그래프 표시모드, 민감도, 이름수정, 사용자 추가 피크, 그룹, 도형/텍스트 주석을 요약한 값입니다.\n"
        "graph_operations.interpretation_points가 있으면 사용자가 그래프에서 강조한 해석 포인트로 보고서 문안에 반영하세요.\n"
        "combined_verdict, rule_matches, rule_evidence, process_markers가 있으면 룰 기반 동정/공정 마커/변성 가능성을 우선 설명하세요.\n"
        "key_findings와 qc_notes는 '룰 기반 동정: ...'처럼 짧은 제목이 있는 여러 줄 문장으로 작성하세요.\n"
        "보고서 문안에는 라이브러리 이름, 라이브러리 적용 여부, 라이브러리 매칭 섹션을 쓰지 마세요.\n"
        "top_candidate가 없으면 현재 FT-IR 웹 그래프 기반 보고서이므로 samples, settings, current_peaks만 근거로 작성하세요.\n"
        "물질명을 단정하지 말고 '가능성', '시사함', '검토 필요' 중심으로 표현하세요.\n"
        "수식은 LaTeX/Markdown 수식 문법을 쓰지 말고 I_842/I_518 = 0.631, cm⁻¹처럼 일반 텍스트로 쓰세요. 화학식은 Li₂CO₃, CO₃²⁻처럼 유니코드 아래첨자/위첨자로 쓰세요.\n"
        "출력은 반드시 JSON 객체 하나로만 응답하세요.\n"
        "- summary: 고객 보고서용 요약 정확히 3문장\n"
        "- key_findings: 핵심 관찰사항 3~5개를 짧은 문장으로 작성\n"
        "- interpretation: 피크와 assignment 근거를 연결한 해석(4문장 이내)\n"
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

        document.sections.append(
            self._experiment_conditions_section(
                verdict,
                aliases=_FTIR_CONDITION_ALIASES,
                preferred_order=_FTIR_CONDITION_ORDER,
            )
        )
        document.sections.append(self._verdict_section(verdict))
        document.sections.append(self._functional_groups_section(verdict))
        document.sections.append(self._current_peak_section(verdict))
        document.sections.append(
            self._graph_state_section(verdict, experiment_code="FT-IR")
        )

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
        reason = _clean_report_text(verdict.get("reason"))
        if reason and not isinstance(verdict.get("top_candidate"), dict) and not _has_library_term(reason):
            bullets.append(f"근거: {reason}")
        cv = verdict.get("combined_verdict") or {}
        if cv.get("verdict"):
            confidence = cv.get("confidence")
            suffix = f" (신뢰도 {confidence})" if confidence else ""
            bullets.append(f"종합 판정: {cv['verdict']}{suffix}")
        action = _clean_report_text(cv.get("action"))
        if action and not _has_library_term(action):
            bullets.append(f"권고 조치: {action}")
        if not bullets:
            sample_text = _sample_text(verdict)
            sample_count = len(_sample_names(verdict))
            current_peaks = _current_peak_count(verdict, x_label="cm-1")
            total_detected = _total_sample_peak_count(verdict)
            if sample_count:
                bullets.append(f"분석 시료: {sample_text} ({sample_count}개)")
            if total_detected:
                bullets.append(f"전처리 단계 검출 피크 후보: {total_detected}개")
            bullets.append(f"현재 그래프 표시 피크: {current_peaks}개")
            bullets.append("현재 판정은 그래프에 남아 있는 피크와 피크 assignment 기반 후보 소견입니다.")
            return ReportSection("verdict", "분석 결과 요약", bullets=bullets)
        return ReportSection("verdict", "판정 결과", bullets=bullets)

    def _functional_groups_section(self, verdict: dict[str, Any]) -> ReportSection:
        findings = verdict.get("findings") or {}
        groups = findings.get("functional_groups") if isinstance(findings, dict) else None
        if not isinstance(groups, list) or not groups:
            rows = []
            for peak in _figure_peak_facts(verdict, x_label="cm-1", max_items=60):
                assignments = peak.get("assignment_names") or []
                if not assignments:
                    continue
                rows.append(
                    [
                        str(peak.get("sample") or "-"),
                        str(peak.get("position_text") or "-"),
                        ", ".join(str(item) for item in assignments),
                    ]
                )
            if rows:
                return ReportSection(
                    "functional_groups",
                    "작용기 소견",
                    table=ReportTable(["시료", "Wavenumber", "피크/작용기 assignment"], rows),
                )
            return ReportSection(
                "functional_groups",
                "작용기 소견",
                paragraphs=["현재 표시 피크 중 작용기 assignment가 연결된 항목이 없습니다."],
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
        table = ReportTable(columns=["시료", "Wavenumber", "피크 이름"], rows=rows)
        return ReportSection("current_peaks", "현재 그래프 피크", table=table)

    def _limitations_section(self, verdict: dict[str, Any]) -> ReportSection:
        bullets: list[str] = []
        top = verdict.get("top_candidate") or {}
        if not isinstance(top, dict) or not top:
            return ReportSection(
                "limitations",
                "해석 한계 및 검토 필요사항",
                bullets=[
                    "현재 그래프에 표시된 피크와 피크 assignment만 근거로 작성되었습니다.",
                    "Assignment가 없는 피크는 물질명이나 작용기를 단정하지 않았습니다.",
                    "FT-IR 단일 스펙트럼만으로 혼합물 분리, 정량 분석, 최종 물질 확정은 수행하지 않습니다.",
                    "필요 시 표준품, 반복 측정, 보완 분석 및 분석자 검토가 필요합니다.",
                ],
            )
        if not verdict.get("is_library_identified", False):
            bullets.append(
                "자동 분석 결과만으로 확정 동정하지 않으며 후보 소견은 참고용입니다."
            )
        bullets.append(
            "단일 피크/단일 이미지만으로 작용기나 물질을 확정하지 않았습니다."
        )
        bullets.append("정량 분석 및 혼합물 분리는 본 보고 범위에 포함되지 않습니다.")
        return ReportSection("limitations", "해석 한계 및 검토 필요사항", bullets=bullets)

    # --- LLM 슬롯 -----------------------------------------------------
    def _fallback_texts(self, verdict: dict[str, Any]) -> dict[str, str]:
        rich_texts = self._rich_rule_fallback_texts(verdict)
        if rich_texts is not None:
            return rich_texts
        sample = _sample_text(verdict)
        sample_count = len(_sample_names(verdict))
        total_detected = _total_sample_peak_count(verdict)
        current_peaks = _current_peak_count(verdict, x_label="cm-1")
        if not isinstance(verdict.get("top_candidate"), dict):
            summary = (
                f"{sample}에 대한 FT-IR 그래프 기반 피크 분석 결과를 정리했습니다. "
                f"현재 보고서에는 그래프에 표시된 피크 {current_peaks}개와 피크 assignment가 반영되었습니다. "
                "본 결과는 자동 피크 검출과 사용자 편집 상태를 반영한 후보 소견입니다."
            )
            key_findings = (
                f"분석 시료 수는 {sample_count}개입니다. "
                f"전처리 단계 검출 피크 후보는 {total_detected}개이며 현재 표시 피크는 {current_peaks}개입니다."
            )
            interpretation = (
                "현재 그래프에 남아 있는 피크 위치와 피크 assignment를 기준으로 해석했습니다. "
                "사용자가 그래프에서 표시 중인 피크와 이름 수정 상태가 보고서에 반영됩니다."
            )
            qc_notes = (
                "피크 민감도, smoothing, baseline 처리에 따라 피크 수와 assignment가 달라질 수 있습니다. "
                "확정 동정은 분석자 검토와 필요 시 표준품/반복 측정으로 확인해야 합니다."
            )
            narrative = "현재 그래프 화면의 피크 정보와 raw 데이터 기반 전처리 결과를 바탕으로 한 규칙 기반 설명입니다."
            caption = f"{sample} FT-IR 현재 그래프 피크 분석 결과"
            email_subject = f"[RIST] {sample} FT-IR 분석 보고서"
            email_body = (
                f"{sample} FT-IR 분석 보고서를 첨부드립니다.\n\n"
                f"- 현재 표시 피크: {current_peaks}개\n"
                "- 본 결과는 자동 피크 검출 및 assignment 후보 기반 참고 소견입니다."
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
        tier = verdict.get("tier", "판정 미상")
        summary = (
            f"{sample} 시료에 대한 FT-IR 분석 결과 신뢰도 판정은 '{tier}'입니다. "
            "관찰된 피크와 작용기 소견을 기준으로 후보 해석을 정리했습니다. "
            "자동 분석 결과만으로 확정 동정하지 않으며 결과는 참고용으로 해석해야 합니다."
        )
        key_findings = (
            f"신뢰도 판정은 {tier}입니다. "
            "주요 작용기 소견과 검출 피크 위치를 함께 검토해야 합니다."
        )
        interpretation = (
            "검출 피크와 작용기 후보를 연결한 해석입니다. "
            "제시된 후보는 자동 분석 결과이며 분석자 검토 전 확정 동정으로 사용하지 않습니다."
        )
        qc_notes = (
            "정량 분석, 혼합물 분리, 단일 피크 기반 확정 동정은 보고 범위에 포함하지 않습니다. "
            "필요 시 원시 스펙트럼, 반복 측정, 보완 분석으로 확인하십시오."
        )
        narrative = (
            "규칙 기반 작용기 소견과 검출 피크를 종합한 결과입니다. "
            "구체적 근거는 작용기 소견 및 현재 그래프 피크 표를 참고하십시오."
        )
        caption = f"{sample} FT-IR 피크 및 작용기 분석 결과"
        email_subject = f"[RIST] {sample} FT-IR 분석 보고서"
        email_body = (
            f"{sample} 시료의 FT-IR 분석 보고서를 첨부드립니다.\n\n"
            f"- 신뢰도 판정: {tier}\n"
            "- 본 결과는 자동 피크/작용기 분석 기반 참고 소견이며, 확정 동정은 분석자 검토 후 판단해 주십시오."
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

    def _rich_rule_fallback_texts(self, verdict: dict[str, Any]) -> dict[str, str] | None:
        rule = _primary_rule_match(verdict)
        cv = verdict.get("combined_verdict") if isinstance(verdict.get("combined_verdict"), dict) else {}
        if not rule and not isinstance(cv.get("product_profile"), dict):
            return None

        sample = _sample_text(verdict)
        material = _rule_material_name(verdict)
        base_material = _rule_base_material_name(verdict)
        score = _rule_score_text(verdict)
        score_suffix = f"({score})" if score else ""
        required = _rule_required_evidence(verdict)
        process_markers = _ftir_process_markers(verdict)
        warning_markers = _ftir_warning_markers(verdict)
        group_lines = _ftir_functional_group_lines(verdict, max_items=3)

        first_marker = process_markers[0] if process_markers else {}
        marker_sentence = ""
        if first_marker:
            marker_sentence = (
                f"또한 {first_marker.get('name')}를 시사하는 "
                f"{first_marker.get('evidence')}가 관찰되어, "
            )
        first_warning = warning_markers[0] if warning_markers else {}
        warning_sentence = ""
        if first_warning:
            warning_sentence = (
                f"{first_warning.get('name') or '추가 피크'}는 "
                f"{first_warning.get('interpretation') or '피크 중첩 또는 변성 가능성 검토가 필요합니다'}."
            )

        if marker_sentence:
            marker_summary = (
                f"{marker_sentence}시료의 공정 흔적 또는 변성 가능성을 함께 보여줍니다. "
            )
        else:
            marker_summary = "관찰된 작용기 및 피크 패턴은 시료의 구조적 특징을 보여줍니다. "
        summary = (
            f"본 시료는 룰 기반 분석 결과, {material} 후보로 강하게 시사됩니다. "
            f"{marker_summary}"
            "본 결과는 FT-IR 피크 패턴을 종합한 후보 소견이며, 최종 판단에는 분석자 검토와 필요 시 보완 분석이 필요합니다."
        )

        required_text = ", ".join(required[:4])
        key_lines: list[str] = []
        if required_text:
            key_lines.append(
                f"룰 기반 동정: {base_material} 필수 특징 피크 {required_text}가 검출되어 룰 기반 동정{score_suffix}을 뒷받침합니다."
            )
        else:
            key_lines.append(f"룰 기반 동정: {base_material} 후보가 구조화 분석 결과에서 확인되었습니다.")
        for marker in process_markers[:2]:
            interpretation = marker.get("interpretation")
            suffix = f" {interpretation}" if interpretation else ""
            key_lines.append(
                f"공정/첨가제 마커: {marker.get('name')} 관련 {marker.get('evidence')}가 확인되었습니다.{suffix}"
            )
        if first_warning:
            key_lines.append(
                f"화학적 변성 가능성: {first_warning.get('evidence')}는 {first_warning.get('interpretation') or first_warning.get('name')}을 시사합니다."
            )
        if group_lines:
            key_lines.append(f"주요 작용기 패턴: {group_lines[0]}")
        key_findings = "\n".join(key_lines[:5])

        interpretation_parts = [
            f"{base_material} 후보는 단일 피크가 아니라 필수 피크 조합과 작용기 패턴을 함께 만족해 도출되었습니다."
        ]
        if process_markers:
            marker_names = ", ".join(marker["name"] for marker in process_markers[:3])
            interpretation_parts.append(f"{marker_names} 등은 상업용 처방 또는 제조 공정의 흔적으로 해석할 수 있습니다.")
        if warning_sentence:
            interpretation_parts.append(warning_sentence)
        interpretation_parts.append("따라서 본 시료는 기본 골격과 부가적인 공정/변성 흔적이 공존하는 복합적 특성으로 보는 것이 타당합니다.")
        interpretation = " ".join(part for part in interpretation_parts if part)

        qc_lines = [
            "단일 스펙트럼 한계: FT-IR만으로 혼합물 분리, 정량 분석, 최종 물질 확정은 수행하지 않습니다.",
        ]
        if first_warning:
            qc_lines.append(
                f"피크 중첩 검토: {first_warning.get('evidence')}는 수분, 인접 작용기 또는 변성 피크와 중첩될 수 있어 추가 확인이 필요합니다."
            )
        if process_markers:
            qc_lines.append(
                "변성/공정 마커 확인: 공정 흔적이나 변성 물질의 정확한 종류는 표준품, 반복 측정 또는 보완 분석으로 확인하는 것이 좋습니다."
            )
        else:
            qc_lines.append("검토 필요사항: 주요 피크 assignment는 분석자 검토와 필요 시 표준품 비교로 확인하는 것이 좋습니다.")
        qc_notes = "\n".join(qc_lines[:3])

        narrative = (
            f"룰 기반 근거를 종합하면 {sample}는 {material} 계열 후보로 해석됩니다. "
            f"주요 근거는 {required_text or '필수 피크 조합'}이며, "
            "공정 마커와 변성 가능성은 별도 검토 항목으로 분리해 판단해야 합니다."
        )
        caption = (
            f"룰 기반 분석 결과, 본 시료는 공정/변성 흔적이 관찰되는 {base_material} 계열 후보로 추정됩니다."
        )
        email_subject = f"[RIST] {sample} FT-IR 분석 보고서"
        email_body = (
            f"{sample} 시료의 FT-IR 분석 보고서를 첨부드립니다.\n\n"
            f"- 룰 기반 후보: {material}\n"
            f"- 주요 근거: {required_text or '작용기 및 피크 패턴'}\n"
            "- 공정/변성 마커와 해석 한계는 보고서 본문을 확인해 주십시오."
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
            "experiment": "FT-IR",
            "sample": verdict.get("sample"),
            "samples": verdict.get("samples"),
            "tier": verdict.get("tier"),
            "reason": None if _has_library_term(verdict.get("reason")) else verdict.get("reason"),
            "settings": {
                "sensitivity": (verdict.get("settings") or {}).get("sensitivity")
                if isinstance(verdict.get("settings"), dict)
                else None,
                "height": (verdict.get("settings") or {}).get("height")
                if isinstance(verdict.get("settings"), dict)
                else None,
                "prominence": (verdict.get("settings") or {}).get("prominence")
                if isinstance(verdict.get("settings"), dict)
                else None,
                "smooth": (verdict.get("settings") or {}).get("smooth")
                if isinstance(verdict.get("settings"), dict)
                else None,
            },
            "functional_groups": groups,
            "rule_based_identification": {
                "material": _rule_material_name(verdict),
                "score": _rule_score_text(verdict),
                "required_evidence": _rule_required_evidence(verdict),
            }
            if _primary_rule_match(verdict)
            else None,
            "rule_evidence": _compact_rule_evidence(verdict),
            "process_markers": _ftir_process_markers(verdict),
            "modification_warnings": _ftir_warning_markers(verdict),
            "experiment_conditions": _experiment_condition_rows(
                verdict,
                aliases=_FTIR_CONDITION_ALIASES,
                preferred_order=_FTIR_CONDITION_ORDER,
            ),
            "current_peaks": _figure_peak_facts(
                verdict,
                x_label="cm-1",
                max_items=40,
                compact=True,
            ),
            "graph_operations": _graph_operation_summary(
                verdict,
                experiment_code="FT-IR",
                x_label="cm-1",
            ),
            "combined_verdict": {
                "verdict": cv.get("verdict"),
                "confidence": cv.get("confidence"),
                "action": None if _has_library_term(cv.get("action")) else cv.get("action"),
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
        "제공된 피크, intensity 비율, Raman assignment 결과, 실험조건(JSON)만 근거로 한국어 문안을 작성하세요.\n"
        "제공되지 않은 상, 물질명, 조성, 원인을 새로 추측하지 마세요.\n"
        "current_peaks는 보고서 생성 시점의 그래프 화면에서 사용자가 표시 중인 최종 피크입니다.\n"
        "피크명은 current_peaks.label을 우선 사용하고, original_label은 변경 전 추적용으로만 참고하세요.\n"
        "graph_operations는 보고서 생성 시점의 그래프 표시모드, 민감도, 이름수정, 사용자 추가 피크, 그룹, 강도비, 도형/텍스트 주석을 요약한 값입니다.\n"
        "graph_operations.interpretation_points가 있으면 사용자가 그래프에서 강조한 해석 포인트로 보고서 문안에 반영하세요.\n"
        "sample_peak_summary, ratio_annotations, display_settings가 있으면 주요 band, 강도비, 스택/전처리 조건을 우선 설명하세요.\n"
        "key_findings와 qc_notes는 '주요 band: ...'처럼 짧은 제목이 있는 여러 줄 문장으로 작성하세요.\n"
        "보고서 문안에는 라이브러리 이름, 라이브러리 적용 여부, 라이브러리 매칭 섹션을 쓰지 마세요.\n"
        "Raman 피크 assignment는 후보 소견으로 표현하고, assignment가 없는 피크는 단정하지 마세요.\n"
        "수식은 LaTeX/Markdown 수식 문법을 쓰지 말고 I_D/I_G = 0.84, cm⁻¹처럼 일반 텍스트로 쓰세요. 화학식은 Li₂CO₃, CO₃²⁻처럼 유니코드 아래첨자/위첨자로 쓰세요.\n"
        "출력은 반드시 JSON 객체 하나로만 응답하세요.\n"
        "- summary: 고객 보고서용 요약 정확히 3문장\n"
        "- key_findings: 핵심 관찰사항 3~5개를 짧은 문장으로 작성\n"
        "- interpretation: Raman band/비율/assignment 근거를 연결한 해석(4문장 이내)\n"
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
        document.sections.append(
            self._experiment_conditions_section(
                payload,
                aliases=_RAMAN_CONDITION_ALIASES,
                preferred_order=_RAMAN_CONDITION_ORDER,
            )
        )
        document.sections.append(self._sample_section(payload))
        document.sections.append(self._peak_section(payload))
        document.sections.append(
            self._graph_state_section(payload, experiment_code="RAMAN")
        )

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

    def _peak_section(self, payload: dict[str, Any]) -> ReportSection:
        rows = _figure_peak_rows(payload, x_label="cm-1")
        if not rows:
            return ReportSection("raman_peaks", "주요 Raman 피크", paragraphs=["보고서용 피크 상세 정보가 없습니다."])
        return ReportSection(
            "raman_peaks",
            "주요 Raman 피크",
            table=ReportTable(["시료", "Raman shift", "Assignment"], rows),
        )

    def _fallback_texts(self, job: dict[str, Any], payload: dict[str, Any]) -> dict[str, str]:
        samples = _as_list(payload.get("samples"))
        sample_count = len(samples)
        sample_text = _sample_text(payload)
        total_peaks = _total_sample_peak_count(payload)
        current_peaks = _current_peak_count(payload, x_label="cm-1")
        assigned_count = _raman_assignment_count(payload)
        peak_lines = _raman_primary_peak_lines(payload)
        ratio_lines = _raman_ratio_annotations(payload)
        condition_text = _raman_condition_summary(payload)
        display_settings = _raman_display_settings(payload)
        stack_text = "스택 표시가 적용되어 샘플 간 피크 패턴 비교가 쉽도록 Y축 평행이동이 반영되었습니다." if display_settings["stack_enabled"] else ""
        primary_peak_sentence = (
            f"주요 band는 {peak_lines[0]} 중심으로 확인됩니다."
            if peak_lines
            else "현재 표시된 주요 band는 보고서용 그래프 상태를 기준으로 정리되었습니다."
        )
        ratio_clause = (
            f"그래프에서 선택된 강도비는 {', '.join(ratio_lines[:2])}이고"
            if ratio_lines
            else "강도비는 동일 조건에서 선택한 피크 쌍을 기준으로 검토해야 하며"
        )
        summary = (
            f"{sample_text}에 대한 Raman 분석 결과, 현재 그래프 기준 {current_peaks}개의 주요 band가 보고서에 반영되었습니다. "
            f"{primary_peak_sentence} "
            f"{ratio_clause}, 피크 assignment는 후보 소견이므로 최종 해석은 실험조건과 전처리 상태를 함께 검토해야 합니다."
        )
        key_lines = [
            f"현재 그래프 기준: 분석 시료 {sample_count}개, 전처리 검출 피크 후보 {total_peaks}개, 현재 표시 피크 {current_peaks}개가 반영되었습니다.",
        ]
        if peak_lines:
            key_lines.extend(f"주요 band: {line}" for line in peak_lines[:3])
        if assigned_count:
            key_lines.append(f"Assignment 후보: 현재 표시 피크 중 {assigned_count}개에 assignment 후보가 연결되어 있습니다.")
        if ratio_lines:
            key_lines.append(f"강도비: {', '.join(ratio_lines[:3])}")
        elif _raman_has_carbon_dg(payload):
            key_lines.append("강도비: Carbon D/G band 쌍은 결함도/graphitic 특성 비교에 활용할 수 있습니다.")
        if condition_text:
            key_lines.append(f"실험조건: {condition_text}")
        key_findings = "\n".join(key_lines[:5])

        interpretation_parts = [
            "현재 그래프에 남아 있는 Raman band 위치, 상대 intensity, 피크 assignment 후보를 기준으로 해석했습니다.",
            "사용자가 그래프에서 표시 중인 피크와 이름 수정 상태가 보고서에 반영됩니다.",
        ]
        if _raman_has_lithium_compound(payload):
            interpretation_parts.append("LiOH/Li₂CO₃ 등 리튬 화합물 관련 band 후보는 시료 내 반응 생성물 또는 표면종 가능성을 검토하는 근거가 됩니다.")
        if _raman_has_carbon_dg(payload):
            interpretation_parts.append("Carbon D/G band는 탄소계 시료의 disorder/graphitic 특성을 비교하는 지표로 사용할 수 있습니다.")
        if ratio_lines:
            interpretation_parts.append("선택된 강도비는 같은 샘플과 동일 전처리 조건 안에서 상대 비교 지표로 해석해야 합니다.")
        interpretation = " ".join(interpretation_parts[:4])

        qc_lines = [
            "전처리 영향: baseline 보정, smoothing, 피크 민감도 설정에 따라 약한 band 검출 수와 intensity가 달라질 수 있습니다.",
            "강도비 한계: Raman intensity 비율은 동일 장비/동일 조건/동일 전처리 기준의 상대 비교로 해석해야 합니다.",
        ]
        if stack_text:
            qc_lines.append("스택 표시: Y축 평행이동은 가독성용 표시이므로 절대 intensity 비교에는 사용하지 않습니다.")
        else:
            qc_lines.append("검토 필요사항: assignment 후보는 표준품, 반복 측정 또는 보완 분석으로 확인하는 것이 좋습니다.")
        qc_notes = "\n".join(qc_lines[:3])
        narrative = (
            "현재 그래프 화면의 Raman band, 사용자가 편집한 피크 상태, raw 데이터 기반 전처리 결과를 종합한 설명입니다. "
            f"{stack_text or '피크 위치와 상대 intensity 패턴을 중심으로 시료 간 차이를 검토합니다.'}"
        )
        caption = f"{sample_text} Raman 주요 band 및 강도비 후보 분석 결과"
        email_subject = f"[RIST] {sample_text} Raman 분석 보고서"
        email_body = (
            f"{sample_text} Raman 분석 보고서를 첨부드립니다.\n\n"
            f"- 시료 수: {sample_count}\n"
            f"- 현재 표시 피크: {current_peaks}개\n"
            f"- 주요 band: {peak_lines[0] if peak_lines else '보고서 본문 참조'}\n"
            "- 본 결과는 자동 피크 검출, 사용자 편집 상태, assignment 후보 기반 참고 소견입니다."
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
            },
            "experiment_conditions": _experiment_condition_rows(
                payload,
                aliases=_RAMAN_CONDITION_ALIASES,
                preferred_order=_RAMAN_CONDITION_ORDER,
            ),
            "current_peaks": _figure_peak_facts(
                payload,
                x_label="cm-1",
                max_items=40,
                compact=True,
            ),
            "peak_assignments": _figure_peak_rows(payload, x_label="cm-1")[:12],
            "sample_peak_summary": _raman_primary_peak_lines(payload, max_samples=5, max_peaks=4),
            "ratio_annotations": _raman_ratio_annotations(payload),
            "display_settings": _raman_display_settings(payload),
            "graph_operations": _graph_operation_summary(
                payload,
                experiment_code="RAMAN",
                x_label="cm-1",
            ),
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
        "수식은 LaTeX/Markdown 수식 문법을 쓰지 말고 일반 텍스트로 쓰세요. 화학식은 유니코드 아래첨자/위첨자로 쓰세요.\n"
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

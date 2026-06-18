from __future__ import annotations

from app.report.builders import FtirReportBuilder, get_builder
from app.report.model import ReportDocument


def _job() -> dict:
    return {
        "job_id": "job-123",
        "request_number": "REQ-2026-00999",
        "experiment_code": "FT-IR",
        "equipment_code": "FTIR-01",
        "operator_id": "user01",
        "root_relative_path": "2026/06/18/x",
        "_generated_at": "2026-06-18T10:00:00+09:00",
    }


def _verdict() -> dict:
    return {
        "sample": "5_Melamine Cyanurate.0",
        "tier": "미동정 (No reliable match)",
        "reason": "최고 점수 64.5% < 임계 65%",
        "is_identified": True,
        "is_library_identified": False,
        "library_size": 589,
        "top_candidate": {
            "material": "m-Xylene",
            "category": "Steel Coating",
            "composite_pct": 64.54,
            "cosine_pct": 92.3,
            "deriv_pct": 65.46,
            "peak_pct": 26.67,
            "overlap_pct": 99.9,
        },
        "findings": {
            "functional_groups": [
                {
                    "group": "페놀릭/PF 수지",
                    "confidence_pct": 100.0,
                    "evidence": "CH2 bridge 1475",
                },
                {
                    "group": "에스터",
                    "confidence_pct": 93.0,
                    "evidence": "C=O ~1735",
                },
            ]
        },
        "combined_verdict": {
            "verdict": "미동정",
            "confidence": "중",
            "action": "추가 분석 권고",
            "explanation": "라이브러리 신뢰 후보 없음",
        },
    }


def test_get_builder_routes_ftir() -> None:
    assert isinstance(get_builder("FT-IR"), FtirReportBuilder)
    assert isinstance(get_builder("ftir"), FtirReportBuilder)
    assert isinstance(get_builder("IR"), FtirReportBuilder)


def test_ftir_builder_maps_verdict_to_fixed_sections() -> None:
    analysis = [{"relativePath": "verdict.json", "data": _verdict()}]
    document = FtirReportBuilder().build(_job(), analysis)

    assert isinstance(document, ReportDocument)
    section_ids = [section.section_id for section in document.sections]
    assert section_ids == [
        "sample_info",
        "verdict",
        "library_match",
        "functional_groups",
        "summary",
        "narrative",
        "limitations",
        "caption",
    ]

    library = document.section("library_match")
    assert library is not None and library.table is not None
    assert ["후보 물질", "m-Xylene"] in library.table.rows

    groups = document.section("functional_groups")
    assert groups is not None and groups.table is not None
    assert len(groups.table.rows) == 2

    # 점수 64.5% < 65% 이면 한계 섹션에 임계값 경고가 들어가야 한다.
    limitations = document.section("limitations")
    assert limitations is not None
    assert any("임계값" in bullet for bullet in limitations.bullets)

    # LLM 슬롯은 규칙 기본 문안으로 미리 채워져 있어야 한다.
    for slot_id in ("summary", "narrative", "caption"):
        section = document.section(slot_id)
        assert section is not None
        assert section.source == "rule"
        assert section.paragraphs and section.paragraphs[0].strip()


def test_ftir_llm_slots_spec_contains_facts() -> None:
    analysis = [{"relativePath": "verdict.json", "data": _verdict()}]
    spec = FtirReportBuilder().llm_slots(_job(), analysis)

    assert spec is not None
    assert spec.requested_slots == ["summary", "narrative", "caption"]
    assert spec.facts["sample"] == "5_Melamine Cyanurate.0"
    assert spec.facts["top_candidate"]["material"] == "m-Xylene"
    assert set(spec.fallback) == {"summary", "narrative", "caption"}


def test_apply_llm_slots_replaces_rule_text() -> None:
    analysis = [{"relativePath": "verdict.json", "data": _verdict()}]
    document = FtirReportBuilder().build(_job(), analysis)
    document.apply_llm_slots({"caption": "LLM 캡션", "summary": "  "})

    caption = document.section("caption")
    assert caption is not None
    assert caption.source == "llm"
    assert caption.paragraphs == ["LLM 캡션"]

    # 빈 문자열 슬롯은 무시되어 규칙 문안을 유지한다.
    summary = document.section("summary")
    assert summary is not None
    assert summary.source == "rule"


def test_markdown_render_includes_headings() -> None:
    analysis = [{"relativePath": "verdict.json", "data": _verdict()}]
    document = FtirReportBuilder().build(_job(), analysis)
    markdown = document.to_markdown()

    assert "# FT-IR 분석 보고서" in markdown
    assert "## 라이브러리 매칭(최상위 후보)" in markdown
    assert "| 작용기 | 신뢰도 | 근거 |" in markdown

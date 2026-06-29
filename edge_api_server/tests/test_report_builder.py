from __future__ import annotations

import json
import zipfile

from app.models import ReportOptions
from app.config import Settings
from app.report.builders import FtirReportBuilder, RamanReportBuilder, get_builder
from app.report.model import ReportDocument, ReportFigure
from app.report.package import build_report_package
from app.report.pipeline import generate_report
from app.report.renderers import render_report_formats, render_requested_report


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


def test_get_builder_routes_raman() -> None:
    assert isinstance(get_builder("RAMAN"), RamanReportBuilder)
    assert isinstance(get_builder("RIN"), RamanReportBuilder)


def test_ftir_builder_maps_verdict_to_fixed_sections() -> None:
    analysis = [{"relativePath": "verdict.json", "data": _verdict()}]
    document = FtirReportBuilder().build(_job(), analysis)

    assert isinstance(document, ReportDocument)
    section_ids = [section.section_id for section in document.sections]
    assert section_ids == [
        "sample_info",
        "experiment_conditions",
        "verdict",
        "library_match",
        "functional_groups",
        "current_peaks",
        "summary",
        "key_findings",
        "interpretation",
        "qc_notes",
        "narrative",
        "limitations",
        "caption",
    ]
    conditions = document.section("experiment_conditions")
    assert conditions is not None
    assert conditions.paragraphs

    library = document.section("library_match")
    assert library is not None and library.table is not None
    assert ["후보 물질", "m-Xylene"] in library.table.rows

    groups = document.section("functional_groups")
    assert groups is not None and groups.table is not None
    assert len(groups.table.rows) == 2

    current_peaks = document.section("current_peaks")
    assert current_peaks is not None
    assert current_peaks.paragraphs

    # 점수 64.5% < 65% 이면 한계 섹션에 임계값 경고가 들어가야 한다.
    limitations = document.section("limitations")
    assert limitations is not None
    assert any("임계값" in bullet for bullet in limitations.bullets)

    # LLM 슬롯은 규칙 기본 문안으로 미리 채워져 있어야 한다.
    for slot_id in (
        "summary",
        "key_findings",
        "interpretation",
        "qc_notes",
        "narrative",
        "caption",
    ):
        section = document.section(slot_id)
        assert section is not None
        assert section.source == "rule"
        assert section.paragraphs and section.paragraphs[0].strip()


def test_ftir_llm_slots_spec_contains_facts() -> None:
    analysis = [{"relativePath": "verdict.json", "data": _verdict()}]
    spec = FtirReportBuilder().llm_slots(_job(), analysis)

    assert spec is not None
    assert spec.requested_slots == [
        "summary",
        "key_findings",
        "interpretation",
        "qc_notes",
        "narrative",
        "caption",
        "email_subject",
        "email_body",
    ]
    assert spec.facts["sample"] == "5_Melamine Cyanurate.0"
    assert spec.facts["top_candidate"]["material"] == "m-Xylene"
    assert spec.facts["current_peaks"] == []
    assert {"summary", "narrative", "caption", "email_body"} <= set(spec.fallback)


def test_ftir_report_uses_current_edited_visible_peaks_for_llm() -> None:
    verdict = _verdict()
    verdict["figure"] = {
        "data": [
            {
                "name": "Edited Sample",
                "meta": {
                    "rist_sample_group": "sample:0",
                    "rist_sample_parent": True,
                },
            },
            {
                "name": "사용자 수정 N-H peak",
                "y": [0.421],
                "meta": {
                    "rist_peak": {
                        "x": 3381.6,
                        "label": "N-H stretch (primary amine)",
                        "sample_group": "sample:0",
                        "group_name": "멜라민 후보군",
                        "group_color": "#ef4444",
                        "assignments": [
                            {
                                "name": "N-H stretch",
                                "library_id": "general-ftir",
                                "library_name": "General FTIR",
                            }
                        ],
                    }
                },
            },
            {
                "name": "숨긴 피크",
                "visible": "legendonly",
                "meta": {
                    "rist_peak": {
                        "x": 1475.0,
                        "label": "C-H bend",
                        "sample_group": "sample:0",
                    }
                },
            },
        ]
    }
    analysis = [{"relativePath": "verdict.json", "data": verdict}]
    document = FtirReportBuilder().build(_job(), analysis)

    peaks = document.section("current_peaks")
    assert peaks is not None and peaks.table is not None
    assert peaks.table.rows == [
        ["Edited Sample", "3381.6 cm-1", "사용자 수정 N-H peak", "General FTIR"]
    ]

    spec = FtirReportBuilder().llm_slots(_job(), analysis)
    assert spec is not None
    assert spec.facts["current_peaks"] == [
        {
            "sample": "Edited Sample",
            "sample_group": "sample:0",
            "position": 3381.6,
            "position_text": "3381.6 cm-1",
            "display_intensity": 0.421,
            "base_intensity": 0.421,
            "label": "사용자 수정 N-H peak",
            "original_label": "N-H stretch (primary amine)",
            "assignment_names": ["N-H stretch"],
            "libraries": ["General FTIR"],
            "group_name": "멜라민 후보군",
            "group_color": "#ef4444",
            "is_user_added": False,
            "source": "detected",
        }
    ]


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


def test_markdown_table_cells_are_escaped() -> None:
    verdict = _verdict()
    verdict["top_candidate"]["material"] = "A|B"
    verdict["top_candidate"]["category"] = "line1\nline2"
    analysis = [{"relativePath": "verdict.json", "data": verdict}]
    document = FtirReportBuilder().build(_job(), analysis)
    markdown = document.to_markdown()

    assert "A\\|B" in markdown
    assert "line1<br>line2" in markdown


def test_pptx_renderer_creates_openxml_package(tmp_path) -> None:
    analysis = [{"relativePath": "verdict.json", "data": _verdict()}]
    document = FtirReportBuilder().build(_job(), analysis)
    image = tmp_path / "spectrum.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    document.figures.append(ReportFigure("figure-1", "Spectrum", str(image)))

    rendered = render_requested_report(document, tmp_path, "PPTX")

    assert rendered.name == "report.pptx"
    with zipfile.ZipFile(rendered) as archive:
        names = set(archive.namelist())
        assert "[Content_Types].xml" in names
        assert "ppt/presentation.xml" in names
        assert "ppt/slides/slide1.xml" in names
        assert "ppt/media/image1.png" in names


def test_pdf_renderer_creates_pdf_file(tmp_path) -> None:
    analysis = [{"relativePath": "verdict.json", "data": _verdict()}]
    document = FtirReportBuilder().build(_job(), analysis)

    rendered = render_requested_report(document, tmp_path, "PDF")
    content = rendered.read_bytes()

    assert rendered.name == "report.pdf"
    assert content.startswith(b"%PDF-")
    assert content.endswith(b"%%EOF\n")


def test_report_package_excludes_internal_json_and_optionally_includes_raw(tmp_path) -> None:
    analysis = [{"relativePath": "verdict.json", "data": _verdict()}]
    document = FtirReportBuilder().build(_job(), analysis)
    report_dir = tmp_path / "report"
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "raw.csv").write_text("raw", encoding="utf-8")
    (report_dir / "report.json").parent.mkdir(parents=True)
    (report_dir / "report.json").write_text("internal", encoding="utf-8")

    rendered = render_report_formats(document, report_dir, ["HTML", "PPTX"])
    (report_dir / "email_body.md").write_text("메일 본문", encoding="utf-8")
    package = build_report_package(report_dir, input_dir, include_raw_files=True)

    assert {path.name for path in rendered} == {"report.html", "report.pptx"}
    with zipfile.ZipFile(package) as archive:
        names = set(archive.namelist())
    assert {"report.html", "report.pptx", "email_body.md", "raw/raw.csv"} <= names
    assert "report.json" not in names


def test_generate_report_writes_email_body_and_image_slide(tmp_path) -> None:
    settings = Settings(storage_root=tmp_path)
    job = {
        **_job(),
        "root_relative_path": "jobs/job-123",
        "report_options_json": '{"reportFormats":["PPTX"],"includeRawFiles":false}',
    }
    job_root = tmp_path / job["root_relative_path"]
    processed = job_root / "processed"
    processed.mkdir(parents=True)
    (processed / "verdict.json").write_text(
        json.dumps(_verdict(), ensure_ascii=False),
        encoding="utf-8",
    )
    (processed / "spectrum.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    document = generate_report(
        settings,
        job,
        llm_client=None,
        generated_at="2026-06-18T10:00:00+09:00",
    )

    report_dir = job_root / "report"
    assert document.llm_used is False
    assert (report_dir / "email_body.md").exists()
    assert "FT-IR 분석 보고서" in (report_dir / "email_body.md").read_text(encoding="utf-8")
    with zipfile.ZipFile(report_dir / "report.pptx") as archive:
        names = set(archive.namelist())
    assert "ppt/media/image1.png" in names
    with zipfile.ZipFile(report_dir / "report-package.zip") as archive:
        package_names = set(archive.namelist())
    assert "email_body.md" in package_names


def test_raman_builder_maps_web_analysis_payload() -> None:
    payload = {
        "samples": [
            {
                "fileName": "LiOH.txt",
                "label": "LiOH_1",
                "pointCount": 1200,
                "peakCount": 2,
                "metadata": {
                    "Excitation Wavelength": "532.06 nm",
                    "Exposure Time": "3 s",
                },
            }
        ],
        "settings": {
            "sensitivity": 25,
            "assignmentLibraries": [
                {"id": "general-raman", "name": "General Raman", "assignmentCount": 10}
            ],
        },
        "figure": {
            "data": [
                {
                    "name": "LiOH_1",
                    "meta": {
                        "rist_sample_group": "sample:0",
                        "rist_sample_parent": True,
                    },
                },
                {
                    "name": "사용자 수정 LiOH peak",
                    "y": [1.42],
                    "meta": {
                        "rist_peak": {
                            "x": 518.0,
                            "base_y": 0.42,
                            "label": "LiOH Li-O stretching",
                            "sample_group": "sample:0",
                            "assignments": [
                                {
                                    "library_id": "general-raman",
                                    "library_name": "General Raman",
                                }
                            ],
                        }
                    },
                },
                {
                    "name": "숨긴 Raman peak",
                    "visible": False,
                    "meta": {
                        "rist_peak": {
                            "x": 1095.0,
                            "label": "Li2CO3 nu1 symmetric stretching",
                            "sample_group": "sample:0",
                        }
                    },
                },
            ]
        },
    }
    analysis = [{"relativePath": "raman-analysis.json", "data": payload}]
    document = RamanReportBuilder().build({**_job(), "experiment_code": "RAMAN"}, analysis)

    assert document.title == "Raman 분석 보고서"
    conditions = document.section("experiment_conditions")
    assert conditions is not None and conditions.table is not None
    assert ["LiOH_1", "Excitation Wavelength", "532.06 nm"] in conditions.table.rows
    assert document.section("raman_samples") is not None
    peaks = document.section("raman_peaks")
    assert peaks is not None and peaks.table is not None
    assert peaks.table.rows[0][1] == "518.0 cm-1"
    assert peaks.table.rows[0][2] == "사용자 수정 LiOH peak"
    assert len(peaks.table.rows) == 1
    spec = RamanReportBuilder().llm_slots({**_job(), "experiment_code": "RAMAN"}, analysis)
    assert spec is not None
    assert spec.facts["experiment_conditions"][0] == [
        "LiOH_1",
        "Excitation Wavelength",
        "532.06 nm",
    ]
    assert spec.facts["peak_assignments"][0][2] == "사용자 수정 LiOH peak"
    assert spec.facts["current_peaks"][0]["label"] == "사용자 수정 LiOH peak"
    assert spec.facts["current_peaks"][0]["original_label"] == "LiOH Li-O stretching"
    assert spec.facts["current_peaks"][0]["display_intensity"] == 1.42
    assert spec.facts["current_peaks"][0]["base_intensity"] == 0.42


def test_report_options_support_legacy_and_multiple_formats() -> None:
    legacy = ReportOptions(reportFormat="PDF")
    multiple = ReportOptions(reportFormats=["PDF", "HTML"])

    assert legacy.report_formats == ["PDF"]
    assert multiple.report_formats == ["PDF", "HTML"]

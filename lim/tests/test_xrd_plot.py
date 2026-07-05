from __future__ import annotations

import plotly.graph_objects as go

from lim.xrd_plot import (
    XRD_DOWNLOAD_IMAGE_FORMAT,
    XRD_IMAGE_FORMAT_SELECTOR,
    assign_relative_phase_categories,
    build_report_html,
    phase_label_from_metadata,
    pdf_peak_warning,
)


def test_xrd_download_plot_uses_fixed_jpeg_format() -> None:
    assert XRD_DOWNLOAD_IMAGE_FORMAT == "jpeg"
    assert XRD_IMAGE_FORMAT_SELECTOR is False


def test_pdf_peak_warning_explains_missing_pdf_files() -> None:
    warning = pdf_peak_warning("cards", pdf_count=0, parsed_count=0)

    assert warning is not None
    assert "PDF 파일" in warning


def test_pdf_peak_warning_explains_parse_failure() -> None:
    warning = pdf_peak_warning("cards", pdf_count=2, parsed_count=0)

    assert warning is not None
    assert "추출하지 못했습니다" in warning


def test_pdf_peak_warning_is_empty_when_peaks_exist() -> None:
    assert pdf_peak_warning("cards", pdf_count=2, parsed_count=1) is None


def test_phase_label_uses_pdf_card_metadata() -> None:
    label = phase_label_from_metadata(
        {
            "phase_name": "Anatase, syn",
            "formula": "Ti O2",
            "card_no": "00-064-0863",
            "quality_mark": "S",
        },
        "TiO2 00-064-0863(S)",
    )

    assert label == "Anatase, syn / TiO2 00-064-0863 QM:S"


def test_relative_phase_categories_keep_only_top_candidates_major() -> None:
    items = [
        {"match": {"score": 100}, "category": "major"},
        {"match": {"score": 92}, "category": "major"},
        {"match": {"score": 88}, "category": "major"},
        {"match": {"score": 5}, "category": "uncertain"},
    ]

    assign_relative_phase_categories(items)

    assert [item["category"] for item in items] == [
        "major",
        "major",
        "uncertain",
        "minor",
    ]


def test_build_report_html_contains_xrd_template_sections() -> None:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[10, 20], y=[1, 2], mode="lines", name="Mix2"))
    item = {
        "label": "Anatase, syn / TiO2 00-064-0863 QM:S",
        "color": "#e41a1c",
        "peaks": [
            {"no": "1", "two_theta": 25.309, "d": "3.516", "norm": 100.0, "hkl": "1 0 1"},
            {"no": "2", "two_theta": 37.876, "d": "2.373", "norm": 21.7, "hkl": "0 0 4"},
        ],
        "trace_idx": 1,
        "metadata": {
            "phase_name": "Anatase, syn",
            "formula": "Ti O2",
            "card_no": "00-064-0863",
            "quality_mark": "S",
            "crystal_system": "Tetragonal",
            "space_group": "141 : I41/amd",
            "two_theta_range": "10.00000 - 140.00000",
        },
        "match": {"score": 83.5, "matched_count": 4, "important_count": 5},
        "category": "major",
    }

    html = build_report_html(
        fig,
        sample_name="Mix2",
        groups=[("Mix2", "#000000", [item])],
        group_map={"Mix2": [0, 1]},
        warnings=[],
        origin=False,
        first_stem="Mix2",
        raw_line_indices=[0],
        highlight_groups={0: [0, 1]},
    )

    assert "Mix2 Report" in html
    assert "그래프 영역" in html
    assert "특이사항 / LLM 코멘트 영역" in html
    assert "피크 정보" in html
    assert "결정상(Phase) 정보" in html
    assert "주요 상 (Major Phases)" in html
    assert "xrd-rank-1" in html
    assert "plotly" in html.lower()

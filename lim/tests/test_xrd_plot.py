from __future__ import annotations

import base64
import zipfile

import plotly.graph_objects as go

from lim.xrd_plot import (
    XRD_DOWNLOAD_IMAGE_FORMAT,
    XRD_IMAGE_FORMAT_SELECTOR,
    assign_relative_phase_categories,
    build_phase_info_html,
    build_report_html,
    build_xrd_html,
    phase_category_from_pdf_path,
    phase_label_from_metadata,
    pdf_peak_warning,
    parse_peak_list_table,
    read_xlsx_preview,
    sort_phase_candidates,
)


TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB"
    "/gL+X7sAAAAASUVORK5CYII="
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

    assert label == "Anatase, syn (TiO2) / 00-064-0863(S)"


def test_phase_category_uses_pdf_folder_names(tmp_path) -> None:
    pdf_root = tmp_path / "ICDD Card"
    similar = pdf_root / "유사상 1"
    minor = pdf_root / "미량상"
    similar.mkdir(parents=True)
    minor.mkdir()

    assert phase_category_from_pdf_path(str(pdf_root / "Al2O3.pdf"), str(pdf_root)) == (
        "major",
        "주요상",
        "folder",
    )
    assert phase_category_from_pdf_path(str(similar / "TiO2.pdf"), str(pdf_root)) == (
        "uncertain",
        "유사상 1",
        "folder",
    )
    assert phase_category_from_pdf_path(str(minor / "Trace.pdf"), str(pdf_root)) == (
        "minor",
        "미량상",
        "folder",
    )


def test_phase_category_prefers_specific_folder_below_mixed_case_parent(tmp_path) -> None:
    pdf_root = tmp_path / "pdf"
    card_root = (
        pdf_root
        / "예제 데이터 2 (주요상 & 유사상 Case)"
        / "ICDD Card (라이브러리 pdf)"
    )
    similar = card_root / "유사상 2"
    similar.mkdir(parents=True)

    assert phase_category_from_pdf_path(str(card_root / "Calcite.pdf"), str(pdf_root)) == (
        "major",
        "주요상",
        "folder",
    )
    assert phase_category_from_pdf_path(str(similar / "TiO2.pdf"), str(pdf_root)) == (
        "uncertain",
        "유사상 2",
        "folder",
    )


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


def test_phase_candidates_are_sorted_by_category_and_similarity() -> None:
    items = [
        {
            "label": "Beta / B",
            "category": "minor",
            "metadata": {"phase_name": "Beta", "formula": "B"},
            "match": {"score": 90},
        },
        {
            "label": "Anatase / TiO2",
            "category": "major",
            "metadata": {"phase_name": "Anatase", "formula": "Ti O2"},
            "match": {"score": 40},
        },
        {
            "label": "Rutile / TiO2",
            "category": "major",
            "metadata": {"phase_name": "Rutile", "formula": "Ti O2"},
            "match": {"score": 85},
        },
        {
            "label": "Alpha / A",
            "category": "uncertain",
            "metadata": {"phase_name": "Alpha", "formula": "A"},
            "match": {"score": 70},
        },
    ]

    sorted_items = sort_phase_candidates(items)

    assert [item["category"] for item in sorted_items] == [
        "major",
        "major",
        "uncertain",
        "minor",
    ]
    assert [item["metadata"]["formula"] for item in sorted_items[:2]] == [
        "Ti O2",
        "Ti O2",
    ]


def test_build_report_html_contains_xrd_template_sections(tmp_path) -> None:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[10, 20], y=[1, 2], mode="lines", name="Mix2"))
    table_path = tmp_path / "peaks.csv"
    table_path.write_text(
        "No.,2theta,Phase Name,Chemical Formula,Card No,Norm. I.\n"
        "1,25.309,Anatase,Ti O2,00-064-0863,100\n",
        encoding="utf-8",
    )
    image_path = tmp_path / "phase-match.png"
    image_path.write_bytes(TINY_PNG)
    item = {
        "label": "Anatase, syn (TiO2) / 00-064-0863(S)",
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
        table_files=[str(table_path)],
        peak_tables=[parse_peak_list_table(str(table_path))],
        image_files=[str(image_path)],
        origin=False,
        first_stem="Mix2",
        raw_line_indices=[0],
        highlight_groups={0: [0, 1]},
    )

    assert "Mix2 Report" in html
    assert 'id="xrd-report-pdf-export"' in html
    assert 'id="xrd-report-landscape-graph" checked' in html
    assert "그래프 가로형" in html
    assert "window.print()" in html
    assert ".xrd-table-scroll," in html
    assert ".xrd-section-head h2 { flex: 0 0 min(260px, 36%);" in html
    assert ".xrd-section-head p { flex: 1 1 auto; min-width: 0;" in html
    assert "max-height: none !important" in html
    assert "overflow: visible !important" in html
    assert "position: static !important" in html
    assert "display: table-header-group" in html
    assert "상 동정 (Phase Identification) 결과" in html
    assert "분석결과" in html
    assert 'id="xrd-analysis-result" contenteditable="true"' in html
    assert ".xrd-comment-box[contenteditable=\"true\"]:focus" in html
    assert "#xrd-plot .modebar," in html
    assert "preparePrintLegend" in html
    assert "xrd-print-legend" in html
    assert 'aria-label="XRD print legend"' in html
    assert "refreshPrintLegend" in html
    assert 'gd.closest(".xrd-graph-frame")' in html
    assert 'graphFrame.querySelector(".xrd-print-legend")' in html
    assert "PRINT_PLOT_HEIGHT = 390" in html
    assert "PRINT_PLOT_WIDTH = 590" in html
    assert "PRINT_LANDSCAPE_PLOT_HEIGHT = 420" in html
    assert "PRINT_LANDSCAPE_PLOT_WIDTH = 960" in html
    assert "applyReportPlotLayout" in html
    assert "applyGraphPageMode" in html
    assert "restoreScreenPlotLayout" in html
    assert "window.Plotly.Plots.resize(gd)" in html
    assert "computePrintYRange" in html
    assert 'layout["yaxis.range"] = yRange' in html
    assert 'layout["width"] = Math.min(Math.floor(plotWidth), plotWidthLimit)' in html
    assert '"title.text": ""' in html
    assert 'handle.textContent = "범례"' in html
    assert "#xrd-plot .rist-legend-drag-handle" in html
    assert "#xrd-plot .rist-xrd-legend-checkbox" in html
    assert "#xrd-plot .rist-xrd-legend-branch" in html
    assert "#xrd-plot .legend {" in html
    assert ".xrd-report-header { display: none !important; }" in html
    assert "@page xrd-graph-landscape { size: A4 landscape;" in html
    assert "body.xrd-report-graph-landscape #xrd-graph-section" in html
    assert ".xrd-graph-frame {\n      overflow: visible !important;" in html
    assert "width: 84% !important" in html
    assert "max-width: 158mm !important" in html
    assert "margin: 0 auto !important" in html
    assert "padding: 8px 14px 12px !important" in html
    assert ".xrd-graph-frame::after" in html
    assert "pointer-events: none;\n      z-index: 3;" in html
    assert "#xrd-plot .plot-container,\n    #xrd-plot .svg-container," in html
    assert "width: 100% !important" in html
    assert "overflow: visible !important" in html
    assert "width: min(590px, calc(100% - 28px)) !important" in html
    assert "body.xrd-report-graph-landscape .xrd-graph-frame" in html
    assert "width: 96% !important" in html
    assert "max-width: 260mm !important" in html
    assert "width: min(960px, calc(100% - 32px)) !important" in html
    assert "column-count: 3" in html
    assert "margin: 0 auto 6px" in html
    assert "margin: 8px 12px 0 6px" in html
    assert "graphFrame.getBoundingClientRect().width" in html
    assert "landscape ? 64 : 56" in html
    assert "#xrd-plot { height: 560px !important; min-height: 500px; }" in html
    assert "height: 390px !important" in html
    assert 'style="height:560px; width:100%;"' in html
    assert "#xrd-peak-info" in html
    assert "page-break-before: always" in html
    assert "column-count: 2" in html
    assert ".xrd-file-table {\n      break-inside: auto;" in html
    assert ".xrd-phase-group {\n      break-inside: avoid;" in html
    assert ".xrd-phase-group summary,\n    .xrd-phase-subgroup-title," in html
    assert ".xrd-phase-subgroup-title + .xrd-similar-phase-cluster" in html
    assert "page-break-inside: auto" in html
    assert "page-break-after: always" in html
    assert "피크 정보" in html
    assert "결정상(Phase) 정보" in html
    assert "주요상 (Major Phases)" in html
    assert "Peak list Excel Display" in html
    assert "peaks.csv" in html
    assert "그래프/상매칭 보조 이미지" in html
    assert "phase-match.png" in html
    assert "data:image/png;base64" in html
    assert "xrd-rank-1" in html
    assert "xrd-tool-toggle" in html
    assert "xrd-tool-panel" in html
    assert "xrd-tool-opacity-slider" in html
    assert "function controlRank(node)" in html
    assert 'node.matches(".rist-history-controls")' in html
    assert "xrd-phase-group-button" in html
    assert "상 그룹 편집" in html
    assert "plotly" in html.lower()


def test_phase_info_displays_db_peaks_and_highlights_similar_overlaps() -> None:
    base = {
        "color": "#e41a1c",
        "metadata": {
            "phase_name": "Anatase",
            "formula": "Ti O2",
            "card_no": "00-064-0863",
            "quality_mark": "S",
        },
        "match": {"score": 80.0, "matched_count": 3, "important_count": 4},
        "category": "uncertain",
        "folder_group": "유사상 1",
    }
    first = {
        **base,
        "label": "Anatase (TiO2) / 00-064-0863(S)",
        "trace_idx": 1,
        "peaks": [
            {"no": "1", "two_theta": 25.309, "d": "3.516", "norm": 100.0, "hkl": "1 0 1"},
            {"no": "2", "two_theta": 37.876, "d": "2.373", "norm": 21.7, "hkl": "0 0 4"},
        ],
    }
    second = {
        **base,
        "label": "TiNF / 01-078-2004(I)",
        "color": "#4daf4a",
        "trace_idx": 2,
        "peaks": [
            {"no": "1", "two_theta": 25.289, "d": "3.519", "norm": 100.0, "hkl": "1 0 1"},
        ],
    }

    peak_tables = [
        {
            "peaks": [
                {
                    "no": "2",
                    "two_theta": 37.876,
                    "card_numbers": ["000640863"],
                    "is_overlap": True,
                }
            ]
        }
    ]

    html = build_phase_info_html([("Mix3", "#d62728", [first, second])], peak_tables=peak_tables)

    assert "유사상 1" in html
    assert "유사/불확실상 2건" in html
    assert "xrd-similar-phase-cluster" in html
    assert "xrd-phase-meta-chip" in html
    assert "xrd-db-peak-table" in html
    assert "d-value" in html
    assert "Norm. I." in html
    assert "xrd-phase-overlap-row" in html
    assert html.count('class="xrd-phase-overlap-row"') == 3


def test_xrd_html_does_not_draw_peak_number_markers_from_peak_list(tmp_path) -> None:
    raw_path = tmp_path / "Mix3.txt"
    raw_path.write_text("10 1\n25.309 100\n30 3\n37.876 40\n", encoding="utf-8")
    pdf_dir = tmp_path / "pdf"
    pdf_dir.mkdir()
    table_path = tmp_path / "Peak list.csv"
    table_path.write_text(
        "No.,2theta,Phase Name,Chemical Formula,Card No,Norm. I.\n"
        "1,25.309,Anatase,Ti O2,00-064-0863,100\n",
        encoding="utf-8",
    )

    result = build_xrd_html([(str(raw_path), str(pdf_dir))], table_files=[str(table_path)])

    assert "xrd_peak_list_marker" not in result["html"]
    assert "Peak No." not in result["html"]


def test_read_xlsx_preview_reads_first_sheet(tmp_path) -> None:
    xlsx_path = tmp_path / "peaks.xlsx"
    with zipfile.ZipFile(xlsx_path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
              <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
              <Default Extension="xml" ContentType="application/xml"/>
              <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
              <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
              <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
            </Types>""",
        )
        archive.writestr(
            "xl/workbook.xml",
            """<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
               xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>
            </workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
            </Relationships>""",
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            """<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <si><t>No.</t></si><si><t>Phase Name</t></si><si><t>Anatase</t></si>
            </sst>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData>
                <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>
                <row r="2"><c r="A2"><v>1</v></c><c r="B2" t="s"><v>2</v></c></row>
              </sheetData>
            </worksheet>""",
        )

    assert read_xlsx_preview(str(xlsx_path)) == [["No.", "Phase Name"], ["1", "Anatase"]]


def test_parse_peak_list_table_filters_esd_and_extracts_card_numbers(tmp_path) -> None:
    csv_path = tmp_path / "Peak list.csv"
    csv_path.write_text(
        "No.,\"2θ, °\",e.s.d.,Phase Name,Chemical Formula,Card No,Norm. I.\n"
        "1,25.289,0.001,Anatase,Ti O2,00-064-0863,100\n"
        "2,30.917,0.002,\"Anatase,Brookite\",\"Ti O2,Ti O2\",\"00-064-0863,01-075-2548\",5\n",
        encoding="utf-8",
    )

    table = parse_peak_list_table(str(csv_path))

    assert "e.s.d." not in table["display_headers"]
    assert table["peaks"][0]["card_numbers"] == ["000640863"]
    assert table["peaks"][1]["is_overlap"] is True

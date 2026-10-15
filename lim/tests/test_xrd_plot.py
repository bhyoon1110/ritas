from __future__ import annotations

import base64
import csv
import json
import re
import unicodedata
import zipfile
from xml.sax.saxutils import escape

import plotly.graph_objects as go
import pytest
from lxml import html as html_tree

from lim.xrd_plot import (
    XRD_DOWNLOAD_IMAGE_FORMAT,
    XRD_IMAGE_FORMAT_SELECTOR,
    XRD_SIMILAR_PHASE_TOLERANCE,
    _phase_overlap_peak_indices,
    assign_relative_phase_categories,
    build_auto_interpretation_html,
    build_image_display_html,
    build_peak_info_html,
    build_phase_info_html,
    build_report_html,
    build_xrd_print_legend_html,
    build_xrd_html,
    phase_category_from_pdf_path,
    phase_label_from_metadata,
    pdf_peak_warning,
    parse_peak_list_table,
    read_table_preview,
    read_xlsx_preview,
    repair_korean_mojibake,
    sort_phase_candidates,
)


TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB"
    "/gL+X7sAAAAASUVORK5CYII="
)


def test_xrd_download_plot_uses_fixed_jpeg_format() -> None:
    assert XRD_DOWNLOAD_IMAGE_FORMAT == "jpeg"
    assert XRD_IMAGE_FORMAT_SELECTOR is False


def test_xrd_print_legend_uses_unformatted_phase_source_label() -> None:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=[1, 2],
            y=[1, 2],
            name="Anatase<br><span>ID: 00-064-0863</span>",
            meta={
                "xrd_phase_candidate": True,
                "xrd_phase_label": "Anatase / 00-064-0863",
            },
        )
    )

    legend = build_xrd_print_legend_html(fig)

    assert "Anatase / 00-064-0863" in legend
    assert "&lt;span" not in legend


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


def test_build_xrd_html_hides_internal_pdf_parse_errors(tmp_path) -> None:
    raw_path = tmp_path / "sample.txt"
    raw_path.write_text("10 1\n20 3\n30 2\n", encoding="utf-8")
    pdf_dir = tmp_path / "pdf"
    pdf_dir.mkdir()
    (pdf_dir / "not-a-card.pdf").write_bytes(b"%PDF-1.4\nbroken")

    html = build_xrd_html([(str(raw_path), str(pdf_dir))])["html"]

    assert "not-a-card.pdf" in html
    assert "자동 피크 표 추출에는 실패" in html
    assert "No /Root object" not in html
    assert "Is this really a PDF" not in html


def test_xrd_main_graph_keeps_all_measured_coordinates(tmp_path) -> None:
    raw_path = tmp_path / "sample.txt"
    points = [(10, -20), (10.001, 50000), (10.015, 7), (20.22, 82), (60, 31)]
    raw_path.write_text("\n".join(f"{x} {y}" for x, y in points), encoding="utf-8")
    pdf_dir = tmp_path / "pdf"
    pdf_dir.mkdir()
    html = build_xrd_html([(str(raw_path), str(pdf_dir))])["html"]
    start = re.search(r'Plotly\.newPlot\(\s*"xrd-plot",\s*', html).end()
    traces, _ = json.JSONDecoder().raw_decode(html[start:])
    raw = next(t for t in traces if t.get("meta", {}).get("xrd_raw"))
    assert raw["x"] == [x for x, _ in points]
    assert raw["y"] == [y for _, y in points]
    assert raw["line"]["shape"] == "linear"
    assert raw["line"]["simplify"] is False


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


def test_phase_category_repairs_cp949_zip_folder_mojibake(tmp_path) -> None:
    pdf_root = tmp_path / "ICDD Card"
    garbled_similar = "유사상 1".encode("cp949").decode("cp437")
    similar = pdf_root / garbled_similar
    similar.mkdir(parents=True)

    assert repair_korean_mojibake(garbled_similar) == "유사상 1"
    assert phase_category_from_pdf_path(str(similar / "TiO2.pdf"), str(pdf_root)) == (
        "uncertain",
        "유사상 1",
        "folder",
    )


def test_korean_path_repair_keeps_normal_names() -> None:
    assert repair_korean_mojibake("ICDD Card (라이브러리 pdf)/유사상 1") == (
        "ICDD Card (라이브러리 pdf)/유사상 1"
    )


def test_phase_category_repairs_utf8_nfd_zip_folder_mojibake(tmp_path) -> None:
    pdf_root = tmp_path / "ICDD Card"
    garbled_similar = unicodedata.normalize("NFD", "유사상 2").encode("utf-8").decode("cp437")
    similar = pdf_root / garbled_similar
    similar.mkdir(parents=True)

    assert repair_korean_mojibake(garbled_similar) == "유사상 2"
    assert phase_category_from_pdf_path(str(similar / "ZnO.pdf"), str(pdf_root)) == (
        "uncertain",
        "유사상 2",
        "folder",
    )


def test_image_display_repairs_utf8_nfd_zip_filename_mojibake(tmp_path) -> None:
    garbled_name = unicodedata.normalize("NFD", "상매칭 보조 이미지.png").encode("utf-8").decode("cp437")
    image_path = tmp_path / garbled_name
    image_path.write_bytes(TINY_PNG)

    html = build_image_display_html([str(image_path)])

    assert "<figcaption>상매칭 보조 이미지</figcaption>" in html
    assert "상매칭 보조 이미지.png" not in html
    assert garbled_name not in html


def test_image_display_repairs_latin1_utf8_filename_mojibake(tmp_path) -> None:
    garbled_name = "상매칭 보조 이미지.png".encode("utf-8").decode("latin1")
    image_path = tmp_path / garbled_name
    image_path.write_bytes(TINY_PNG)

    html = build_image_display_html([str(image_path)])

    assert "<figcaption>상매칭 보조 이미지</figcaption>" in html
    assert "상매칭 보조 이미지.png" not in html
    assert garbled_name not in html


def test_image_display_adds_phase_review_heading_only_when_requested(tmp_path) -> None:
    image_path = tmp_path / "phase-match.png"
    image_path.write_bytes(TINY_PNG)

    plain_html = build_image_display_html([str(image_path)])
    phase_review_html = build_image_display_html(
        [str(image_path)],
        show_phase_review_heading=True,
    )

    assert "유사상 미량상 확인" not in plain_html
    assert "유사상 미량상 확인" in phase_review_html


@pytest.mark.parametrize("with_image", [True, False])
def test_report_exchanges_image_graph_contents_and_preserves_peak_table(tmp_path, with_image):
    image_path = tmp_path / "상매칭 이미지.png"
    image_path.write_bytes(TINY_PNG)
    table_path = tmp_path / "peaks.csv"
    table_path.write_text("No.,2theta,Card No\n1,20,00-001-0001\n", encoding="utf-8")
    figure = go.Figure(go.Scatter(x=[10, 20, 30], y=[1, 3, 1], name="Raw"))
    report = build_report_html(
        figure, sample_name="배치 검증", groups=[], group_map={}, warnings=[],
        image_files=[str(image_path)] if with_image else [],
        peak_tables=[parse_peak_list_table(str(table_path))],
        origin=True, first_stem="sample", raw_line_indices=[0], highlight_groups={},
    )
    tree = html_tree.fromstring(report)
    graph = tree.get_element_by_id("xrd-graph-section")
    assert len(graph.xpath('.//*[@id="xrd-plot"]')) == 1
    if with_image:
        section = tree.get_element_by_id("xrd-image-info")
        assert section.xpath(".//h2/text()") == ["상 동정 (Phase Identification) 결과"]
        assert section.xpath(".//figcaption/text()") == ["상매칭 이미지"]
        assert not section.xpath('.//*[@id="xrd-plot"]')
        assert graph.xpath(".//h2/text()") == ["유사상 미량상 확인"]
        assert report.index('id="xrd-image-info"') < report.index('id="xrd-graph-section"')
    else:
        assert not tree.xpath('//*[@id="xrd-image-info"]')
        assert graph.xpath(".//h2/text()") == ["상 동정 (Phase Identification) 결과"]
    assert tree.xpath('//h2[@class="xrd-phase-identification-title"]')
    assert 'white-space: nowrap' in report
    assert '.xrd-phase-card-grid { grid-template-columns: minmax(0,1fr); }' in report
    assert tree.xpath('//div[@class="xrd-provided-block"]/h3/text()') == ["그래프 피크 번호"]
    assert tree.xpath('//table[contains(@class,"xrd-peak-list-display")]//tbody/tr/td/text()')
    assert "Excel Peak list의 2θ/Card No/Phase Name" not in report
    assert "Peak list Excel Display" not in report
    assert tree.get_element_by_id("xrd-analysis-result").get("contenteditable") == "true"


def test_rule_based_analysis_uses_major_minor_estimated_headings_without_reclassification():
    items = [{"category": category, "metadata": {"phase_name": name}, "label": name}
             for category, name in [("major", "Major-A"), ("uncertain", "Estimate-B"), ("minor", "Minor-C")]]
    tree = html_tree.fragment_fromstring(build_auto_interpretation_html("sample", [("raw", "red", items)], []), create_parent=True)
    paragraphs = tree.xpath("./p")
    assert [p.xpath("./strong/text()")[0] for p in paragraphs] == ["주요상", "미량상", "추정상"]
    for paragraph, name in zip(paragraphs, ["Major-A", "Minor-C", "Estimate-B"]):
        assert name in paragraph.text_content()
    assert items[1]["category"] == "uncertain"


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
    assert "window.__ristColorPaletteInstalled" in html
    assert "기본 색상표" in html
    assert "xrd-phase-group-color" in html
    assert 'id="xrd-report-pdf-export"' in html
    assert "xrd-report-pdf-button-label" in html
    assert "xrd-report-pdf-spinner" in html
    assert "PDF 생성 중..." in html
    assert "setPdfExportBusy" in html
    assert "if (button.disabled) return;" in html
    assert '<script charset="utf-8" src="/xrd/assets/plotly.min.js"></script>' in html
    graph_frame_html = html.split('<div class="xrd-graph-frame">', 1)[1].split("</section>", 1)[0]
    assert "/xrd/assets/plotly.min.js" not in graph_frame_html
    assert 'id="xrd-report-landscape-graph" checked' in html
    assert "그래프 가로형" in html
    assert ".xrd-report-pdf-option.is-hidden" in html
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
    assert "xrd-print-plot-image" in html
    assert ".xrd-print-plot-image { display: none; }" in html
    assert "createPrintPlotImage" in html
    assert "window.Plotly.toImage" in html
    assert "frame.classList.add(\"has-print-plot\")" in html
    assert 'aria-label="XRD print legend"' in html
    assert "refreshPrintLegend" in html
    assert 'gd.closest(".xrd-graph-frame")' in html
    assert 'graphFrame.querySelector(".xrd-print-legend")' in html
    assert "PRINT_PLOT_HEIGHT = 350" in html
    assert "PRINT_PLOT_WIDTH = 620" in html
    assert "PRINT_LANDSCAPE_PLOT_HEIGHT = 420" in html
    assert "PRINT_LANDSCAPE_PLOT_WIDTH = 1040" in html
    assert "applyReportPlotLayout" in html
    assert "applyGraphPageMode" in html
    assert "applyPrintPageStyle" in html
    assert "@page { size: A4 portrait;" in html
    assert "data-xrd-print-page-style" in html
    assert "effectivePrintLandscapeEnabled" in html
    assert "return graphPageLandscapeEnabled();" in html
    assert "function isMobileClient" in html
    assert 'matchMedia("(max-width: 900px)")' in html
    assert "function syncLandscapeOptionAvailability" in html
    assert "return !isMobileClient() && (!landscapeOption || landscapeOption.checked);" in html
    assert 'landscapeOption.disabled = true;' in html
    assert 'if (label) label.classList.add("is-hidden");' in html
    assert "max-width: 178mm !important" in html
    assert "max-width: 268mm !important" in html
    assert "@page:first" not in html
    assert "width: 190mm !important" in html
    assert "width: 277mm !important" in html
    assert "page: xrd-report-portrait !important" in html
    assert "@page { size: landscape;" not in html
    assert "body.xrd-report-graph-landscape #xrd-image-info {" in html
    assert "body.xrd-report-graph-landscape #xrd-llm-comment {" in html
    assert "restoreScreenPlotLayout" in html
    assert "window.Plotly.Plots.resize(gd)" in html
    assert "serverRenderPdf" in html
    assert "/api/v1/xrd/render-pdf" in html
    assert "shouldUseBrowserPrint" in html
    assert "window.readOnlyReport === true" in html
    assert '!/^https?:$/.test(window.location.protocol || "")' in html
    assert "exportHtmlSnapshot" in html
    assert 'clone.querySelectorAll("script")' in html
    assert "node.remove();" in html
    assert "서버 PDF 생성 요청이 연결 중 끊겼습니다" in html
    assert "openBrowserPrintFallback" in html
    assert "대상에서 PDF 저장을 선택" in html
    assert 'landscapeOption.removeAttribute("checked")' in html
    assert 'landscapeOption.setAttribute("checked", "checked")' in html
    assert "downloadPdfBlob" in html
    assert "Chrome/Chromium 렌더러 설정" in html
    assert "computePrintYRange" in html
    assert 'layout["yaxis.range"] = yRange' in html
    assert 'layout["width"] = Math.min(Math.floor(plotWidth), plotWidthLimit)' in html
    assert '"title.text": ""' in html
    assert 'handle.textContent = "범례"' in html
    assert 'src="/xrd/assets/plotly.min.js"' in html
    assert "https://cdn.plot.ly" not in html
    assert "#xrd-plot .rist-legend-drag-handle" in html
    assert "#xrd-plot .rist-xrd-legend-checkbox" in html
    assert "#xrd-plot .rist-xrd-legend-branch" in html
    assert "rist-xrd-legend-size-toggle" in html
    assert "rist-xrd-legend-hide" in html
    assert "rist-xrd-legend-restore" in html
    assert "범례 · 축소" in html
    assert "전체 범례 보기" in html
    assert "ID: " in html
    assert 'gd.dataset.xrdLegendMode = mode' in html
    assert 'layout["legend.x"] = 0.965' in html
    assert 'layout["legend.y"] = 0.91' in html
    assert 'path.setAttribute("d", "M5,0h12")' in html
    assert 'path.setAttribute("d", "M0 -8 V8 M0 0 H10")' in html
    assert '",0)")' in html
    assert '",-6)")' in html
    assert 'html[data-read-only-report="true"] #xrd-plot .rist-plot-control-row' not in html
    assert "@media screen and (max-width: 900px), screen and (pointer: coarse)" in html
    assert 'html[data-read-only-report="true"] #xrd-plot .rist-legend-drag-handle' in html
    assert 'data-xrd-downloaded-report' not in html
    assert "height: min(380px, 46vh) !important;" in html
    assert "min-height: 300px !important;" in html
    assert "isMobileReadOnlyScreen" in html
    assert "applyReadOnlyMobilePlotLayout" in html
    assert "scheduleReadOnlyMobilePlotLayout" in html
    assert '"width": availableWidth' in html
    assert "if (window.readOnlyReport !== true) return false;" in html
    assert "Math.max(300, Math.min(380" in html
    assert 'gd.style.minHeight = "300px";' in html
    assert '"legend.x": 0.98' in html
    assert '"legend.y": 0.98' in html
    assert '"legend.xanchor": "right"' in html
    assert '"legend.orientation": "v"' in html
    assert '"legend.font.size": 10' in html
    assert "#xrd-plot .legend {" in html
    assert ".xrd-report-header { display: none !important; }" in html
    assert "@page:first" not in html
    assert "@page xrd-graph-landscape { size: A4 landscape;" in html
    assert "body.xrd-report-graph-landscape #xrd-graph-section" in html
    assert "body.xrd-report-graph-landscape #xrd-image-info" in html
    assert "body.xrd-report-graph-landscape #xrd-llm-comment" in html
    assert "body.xrd-report-graph-landscape #xrd-peak-info" in html
    assert "body.xrd-report-graph-landscape #xrd-phase-info" in html
    assert "page: auto;" in html
    assert "#xrd-graph-section .xrd-section-head + .xrd-graph-frame" in html
    assert "#xrd-graph-section {\n      page: auto;" in html
    assert ".xrd-graph-frame {\n      overflow: visible !important;" in html
    assert "width: 92% !important" in html
    assert "max-width: 178mm !important" in html
    assert "margin: 0 auto !important" in html
    assert "padding: 8px 12px 12px !important" in html
    assert "border: 0 !important" in html
    assert "box-shadow: inset 0 0 0 2px #111827" in html
    assert ".xrd-graph-frame::after" in html
    assert "content: none !important" in html
    assert "#xrd-plot .plot-container,\n    #xrd-plot .svg-container," in html
    assert "width: 100% !important" in html
    assert "overflow: visible !important" in html
    assert ".xrd-graph-frame.has-print-plot #xrd-plot" in html
    assert ".xrd-graph-frame.has-print-plot .xrd-print-plot-image" in html
    assert "body.xrd-report-graph-landscape .xrd-graph-frame" in html
    assert "width: 98% !important" in html
    assert "max-width: 268mm !important" in html
    assert "body.xrd-report-graph-landscape #xrd-plot" in html
    assert "transform: none !important" in html
    assert "column-count: 3" in html
    assert "margin: 0 auto 6px" in html
    assert "margin: 8px 12px 0 6px" in html
    assert "graphFrame.getBoundingClientRect().width" in html
    assert "landscape ? 40 : 56" in html
    assert "#xrd-plot { height: 500px !important; min-height: 420px; }" in html
    assert "@media (min-width: 761px) and (max-width: 1280px)" in html
    assert "#xrd-plot { height: 430px !important; min-height: 380px; }" in html
    assert "height: 350px !important" in html
    assert 'style="height:500px; width:100%;"' in html
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
    assert "Peak list Excel Display" not in html
    assert "그래프 피크 번호" in html
    assert "peaks.csv" in html
    assert "유사상 미량상 확인" in html
    assert "<figcaption>phase-match</figcaption>" in html
    assert "data:image/png;base64" in html
    assert "안내 사항" in html
    assert "추가 검토 안내" not in html
    assert "주요 원소 정보(XRF/ICP/EDS)를 공유해주시면" in html
    assert "원소 성분 분석을 권장드립니다." in html
    assert "안내사항" not in html
    assert "xrd-rank-1" in html
    assert "xrd-tool-toggle" in html
    assert 'toggle.setAttribute("aria-label", "그래프 도구")' in html
    assert "setLegendTextX(row, textNode, tx + offset + 18)" in html
    assert "xrd-tool-panel" in html
    assert "xrd-tool-opacity-slider" in html
    assert "function controlRank(node)" in html
    assert 'node.matches(".rist-history-controls")' in html
    assert "xrd-phase-group-button" in html
    assert "상 그룹 편집" in html
    assert "plotly" in html.lower()


def test_phase_info_highlights_only_common_peaks_from_similar_folder_pdfs() -> None:
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
        "category_source": "folder",
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

    html = build_phase_info_html([("Mix3", "#d62728", [first, second])])

    assert "유사상 1" in html
    assert "유사/불확실상 2건" in html
    assert "xrd-similar-phase-cluster" in html
    assert "xrd-phase-meta-chip" in html
    assert "xrd-db-peak-table" in html
    assert "d-value" in html
    assert "Norm. I." in html
    assert "xrd-phase-overlap-row" in html
    assert html.count('class="xrd-phase-overlap-row"') == 2
    assert "±0.06°" in html
    assert "Peak list Excel에서 겹치는" not in html


def test_similar_phase_overlap_requires_every_phase_within_tolerance() -> None:
    items = [
        {
            "trace_idx": 1,
            "peaks": [
                {"two_theta": 25.300},
                {"two_theta": 37.000},
            ],
        },
        {
            "trace_idx": 2,
            "peaks": [
                {"two_theta": 25.340},
                {"two_theta": 37.055},
            ],
        },
        {
            "trace_idx": 3,
            "peaks": [
                {"two_theta": 25.359},
                {"two_theta": 37.080},
            ],
        },
    ]

    overlaps = _phase_overlap_peak_indices(items)

    assert XRD_SIMILAR_PHASE_TOLERANCE == 0.06
    assert overlaps == {1: {0}, 2: {0}, 3: {0}}


def test_phase_info_does_not_highlight_auto_classified_uncertain_phases() -> None:
    base = {
        "label": "Candidate",
        "color": "#e41a1c",
        "metadata": {
            "phase_name": "Candidate",
            "formula": "AB2",
            "card_no": "00-000-0001",
            "quality_mark": "S",
        },
        "match": {"score": 70.0, "matched_count": 2, "important_count": 3},
        "category": "uncertain",
        "category_source": "score",
        "folder_group": "자동 분류",
        "peaks": [
            {
                "no": "1",
                "two_theta": 25.300,
                "d": "3.500",
                "norm": 100.0,
                "hkl": "1 0 0",
            },
        ],
    }
    first = {**base, "trace_idx": 1}
    second = {**base, "trace_idx": 2, "color": "#4daf4a"}

    html = build_phase_info_html([("Mix3", "#d62728", [first, second])])

    assert "xrd-phase-overlap-row" not in html


def test_xrd_html_draws_numbered_peak_graph_from_peak_list(tmp_path) -> None:
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

    assert "xrd_peak_list_marker" in result["html"]
    assert "Peak No." in result["html"]
    assert "그래프 피크 번호" in result["html"]
    assert "xrd-numbered-peak-plot" in result["html"]
    numbered_graph_html = result["html"].split('id="xrd-numbered-peak-plot"', 1)[1]
    numbered_graph_html = numbered_graph_html.split("</section>", 1)[0]
    assert '"showlegend":false' in numbered_graph_html
    assert '"legend":{"orientation"' not in numbered_graph_html


def test_peak_list_does_not_add_missing_columns_from_icdd_candidates(tmp_path) -> None:
    raw_path = tmp_path / "Mix3.txt"
    raw_path.write_text("10 1\n25.309 100\n30 3\n", encoding="utf-8")
    pdf_dir = tmp_path / "pdf"
    pdf_dir.mkdir()
    table_path = tmp_path / "Peak list.csv"
    table_path.write_text("No.,2theta,Card No\n1,25.309,00-064-0863\n", encoding="utf-8")
    candidate = {
        "label": "Anatase, syn (TiO2) / 00-064-0863(S)",
        "color": "#e41a1c",
        "peaks": [
            {
                "no": "1",
                "two_theta": 25.309,
                "d": "3.516",
                "norm": 100.0,
                "hkl": "1 0 1",
            }
        ],
        "trace_idx": 1,
        "metadata": {
            "phase_name": "Anatase, syn",
            "formula": "Ti O2",
            "card_no": "00-064-0863",
        },
        "match": {"score": 90.0, "matched_count": 1, "important_count": 1},
        "category": "major",
    }
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=[10, 25.309, 30],
            y=[1, 100, 3],
            mode="lines",
            name="Mix3",
            meta={"xrd_raw": True},
        )
    )

    html = build_report_html(
        fig,
        sample_name="Mix3",
        groups=[("Mix3", "#d62728", [candidate])],
        group_map={"Mix3": [0, 1]},
        warnings=[],
        table_files=[str(table_path)],
        peak_tables=[parse_peak_list_table(str(table_path))],
        image_files=[],
        origin=False,
        first_stem="Mix3",
        raw_line_indices=[0],
        highlight_groups={0: [0, 1]},
    )

    table = html_tree.fromstring(html).find('.//table[@class="xrd-report-table xrd-peak-list-display"]')
    assert table is not None
    assert [cell.text_content() for cell in table.findall("thead/tr/th")] == ["No.", "2theta", "Card No"]
    assert [cell.text_content() for cell in table.findall("tbody/tr/td")] == ["1", "25.309", "00-064-0863"]
    assert not table.findall(".//input")
    # Candidate information still belongs to the separate phase section.
    assert "Anatase, syn" in html
    assert "ICDD Card PDF에서 추출한 후보 피크" not in html


@pytest.mark.parametrize("source_format", ["csv", "tsv", "xlsx", "xlsx-absolute"])
@pytest.mark.parametrize("wide", [False, True], ids=["compact", "columns-after-12"])
def test_peak_list_report_preserves_source_values_and_blanks(tmp_path, source_format, wide) -> None:
    # Match the instrument export: target fields occur at columns 22–25.
    leading = ["No.", "2θ, °"] + (["e.s.d."] * 19 if wide else [])
    headers = leading + ["Phase Name", "Chemical Formula", "Card No", "Norm. I."]
    values = [
        ["Anatase, syn: 1 0 1,Brookite: 2 1 0", "Ti O2,Ti O2", "00-064-0863,01-075-2548", "4.0500"],
        ["", "", "00-064-0863", ""],
        ["User <phase> & name", "Ca ( C O3 )", "00-064-0863", "0"],
    ]
    rows = [headers] + [
        [str(index), "25.309"] + (["0.001"] * 19 if wide else []) + row
        for index, row in enumerate(values, start=1)
    ]
    source = tmp_path / ("Peak list." + source_format.split("-")[0])
    if source.suffix != ".xlsx":
        with source.open("w", encoding="utf-8-sig", newline="") as stream:
            csv.writer(stream, delimiter="\t" if source.suffix == ".tsv" else ",").writerows(rows)
    else:
        xml_rows = []
        for row_index, row in enumerate(rows, start=1):
            cells = []
            for col_index, value in enumerate(row):
                ref = f"{chr(65 + col_index)}{row_index}"
                if row_index > 1 and not value:
                    continue  # Sparse XLSX cells must stay empty, not shift columns.
                if row_index > 1 and col_index in (0, 1, len(headers) - 1):
                    cells.append(f'<c r="{ref}"><v>{escape(value)}</v></c>')
                elif row_index > 1 and col_index == len(headers) - 3:
                    # Rich-text/subscript chemical formula uses multiple text runs.
                    cells.append(f'<c r="{ref}" t="inlineStr"><is><r><t>{escape(value[:2])}</t></r>'
                                 f'<r><t>{escape(value[2:])}</t></r></is></c>')
                else:
                    cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
            xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
        target = "/xl/worksheets/peaks.xml" if source_format == "xlsx-absolute" else "worksheets/peaks.xml"
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                             'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                             '<sheets><sheet name="Peaks" sheetId="1" r:id="rId1"/></sheets></workbook>')
            archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                             f'<Relationship Id="rId1" Target="{target}"/></Relationships>')
            archive.writestr("xl/worksheets/peaks.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                             f'<sheetData>{"".join(xml_rows)}</sheetData></worksheet>')
    before = source.read_bytes()
    table = parse_peak_list_table(str(source))
    assert not table["error"]
    expected_headers = ["No.", "2θ, °", "Phase Name", "Chemical Formula", "Card No", "Norm. I."]
    assert table["display_headers"] == expected_headers
    assert table["peaks"][0]["phase_name"] == values[0][0]
    assert table["peaks"][0]["formula"] == values[0][1]
    assert table["peaks"][0]["norm_i"] == 4.05
    assert table["peaks"][1]["norm_i"] is None
    assert table["peaks"][2]["norm_i"] == 0.0
    # Even a matching ICDD card/angle must never overwrite blank source fields.
    candidate = {"label": "Different candidate", "trace_idx": 1,
                 "metadata": {"phase_name": "ICDD inferred name", "formula": "Different formula", "card_no": "00-064-0863"},
                 "peaks": [{"no": "1", "two_theta": 25.309, "d": "3.516", "norm": 100.0, "hkl": "1 0 1"}]}
    report = build_peak_info_html([("Sample", "#000", [candidate])], peak_tables=[table])
    root = html_tree.fromstring(report)
    rendered = root.find('.//table[@class="xrd-report-table xrd-peak-list-display"]')
    assert [cell.text_content() for cell in rendered.findall("thead/tr/th")] == expected_headers
    actual = [[cell.text_content() for cell in row.findall("td")] for row in rendered.findall("tbody/tr")]
    assert actual == [[str(i), "25.309"] + row for i, row in enumerate(values, start=1)]
    assert len(rendered.findall('tbody/tr[@class="xrd-overlap-row"]')) == 1
    assert not rendered.findall(".//input")
    assert "Card No<br>연동" not in report
    assert source.read_bytes() == before
    # General previews retain their bounded width; only Peak list selection reads all columns.
    assert len(read_table_preview(str(source))["rows"][0]) == min(len(headers), 12)


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
                <row r="2"><c r="A2"><v>1</v></c><c r="B2" t="s"><v>2</v></c>
                  <c r="ZZZZ2"><v>999</v></c></row>
              </sheetData>
            </worksheet>""",
        )

    assert read_xlsx_preview(str(xlsx_path)) == [["No.", "Phase Name"], ["1", "Anatase"]]
    assert read_xlsx_preview(str(xlsx_path), max_cols=None) == [["No.", "Phase Name"], ["1", "Anatase"]]


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

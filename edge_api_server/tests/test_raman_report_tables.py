from copy import deepcopy
import json
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from app.report.builders import FtirReportBuilder, RamanReportBuilder
from app.report.edited_analysis import prepare_edited_report, analysis_preview
from app.report.editable import document_from_dict, editor_document, apply_edits
from app.report import renderers, spectrum_viewer


JOB = dict(job_id="raman-tables", request_number="REQ-001", experiment_code="RAMAN",
           equipment_code="LAB-01", operator_id="analyst", _generated_at="2026-10-15T17:00:00+09:00")


def payload_for_tables(count=2):
    traces, ratios, annotations = [], [], []
    for sample in range(count):
        group = f"sample:{sample}"
        traces.append(dict(name=f"시료 {sample+1}", x=[1350.125, 1580.25], y=[12, 14],
                           meta=dict(rist_sample_parent=True, rist_sample_group=group, rist_view_offset=10)))
        for x, y, label in [(1350.125, 12, "D band"), (1580.25, 14, "G band")]:
            traces.append(dict(name=label, x=[x], y=[y], meta=dict(
                rist_sample_group=group, rist_view_offset=10,
                rist_peak=dict(x=x, base_y=999, sample_group=group, label=label))))
        ratios.append(dict(id=sample+1, title="D/G 비율", numeratorKey=json.dumps([group, 1350.125]),
                           denominatorKey=json.dumps([group, 1580.25]), numeratorCurve=999, denominatorCurve=998))
        annotations.append(dict(name=f"rist_raman_ratio:{sample+1}", text="D/G<br>I(1350)/I(1580) = 0.500"))
    return dict(viewerFigure=dict(data=traces, layout=dict(meta=dict(ristRamanRatios=ratios), annotations=annotations)))


def build(payload):
    return RamanReportBuilder().build(JOB, [{"data": prepare_edited_report(payload)}])


def test_report_tables_use_final_peaks_unshifted_intensities_and_stable_ratio_keys():
    payload = payload_for_tables()
    original = deepcopy(payload)
    document = build(payload)
    peaks = document.section("raman_peaks").table
    assert peaks.columns == ["시료", "피크명", "Position (cm⁻¹)", "Intensity (a.u.)"]
    assert peaks.rows == [[f"시료 {i}", label, x, y] for i in (1, 2)
                         for label, x, y in [("D band", "1350.125", "2"), ("G band", "1580.25", "4")]]
    assert document.section("raman_ratios").table.rows == [
        [f"시료 {i}", "D/G 비율", "D band (1350.125 cm⁻¹)", "G band (1580.25 cm⁻¹)", "0.500"] for i in (1, 2)]
    assert payload == original


@pytest.mark.parametrize("kind", ["hidden_sample", "hidden_peak", "deleted_peak", "hidden_ratio", "cross_sample", "bad_key"])
def test_invalid_or_hidden_pairs_do_not_reappear_in_ratio_table(kind):
    payload = payload_for_tables()
    figure = payload["viewerFigure"]
    if kind == "hidden_sample":
        figure["data"][0]["visible"] = "legendonly"
    elif kind == "hidden_peak":
        figure["data"][1]["visible"] = False
    elif kind == "deleted_peak":
        del figure["data"][1]
    elif kind == "hidden_ratio":
        figure["layout"]["annotations"][0]["visible"] = False
    else:
        figure["layout"]["meta"]["ristRamanRatios"][0]["denominatorKey"] = (
            json.dumps(["sample:1", 1580.25]) if kind == "cross_sample" else "invalid")
    rows = build(payload).section("raman_ratios").table.rows
    assert len(rows) == 1 and rows[0][0] == "시료 2"


@pytest.mark.parametrize("n,d,expected", [(0, 4, "0.000"), (2, 0, "계산 불가"),
                                         (2, 1e-13, "계산 불가"), (-2, 4, "-0.500")])
def test_zero_negative_and_near_zero_intensities(n, d, expected):
    payload = payload_for_tables(1)
    for trace, value in zip(payload["viewerFigure"]["data"][1:], [n, d]):
        trace["meta"]["rist_view_offset"] = 0
        trace["y"] = [value]
    document = build(payload)
    assert document.section("raman_ratios").table.rows[0][-1] == expected
    assert document.section("raman_peaks").table.rows[0][-1] == format(n, ".10g")


def test_legacy_annotations_keep_all_original_values_without_guessing_sample():
    payload = payload_for_tables(9)
    del payload["viewerFigure"]["layout"]["meta"]["ristRamanRatios"]
    rows = build(payload).section("raman_ratios").table.rows
    assert len(rows) == 9  # Equal labels in different annotations must not be deduplicated.
    assert all(row == ["-", "D/G / I(1350)/I(1580) = 0.500", "-", "-", "원문 참조"] for row in rows)


def test_empty_and_nonfinite_data_do_not_invent_peak_or_ratio_values():
    document = build({"viewerFigure": {"data": [], "layout": {}}})
    assert document.section("raman_peaks").table is None
    assert document.section("raman_ratios").table is None
    payload = {"figure": {"data": [dict(name="unknown", meta={"rist_peak": dict(x=float("nan"), base_y=float("inf"))})]}}
    document = RamanReportBuilder().build(JOB, [{"data": payload}])
    assert document.section("raman_peaks").table.rows[0][-2:] == ["-", "-"]
    ftir = FtirReportBuilder().build({**JOB, "experiment_code": "FTIR"}, [])
    assert ftir.section("raman_peaks") is None and ftir.section("raman_ratios") is None


def test_many_peaks_and_ratios_are_paginated_without_truncation_in_all_formats(tmp_path, monkeypatch):
    document = build(payload_for_tables(24))
    assert len(document.section("raman_peaks").table.rows) == 48
    assert len(document.section("raman_ratios").table.rows) == 24
    renderers.render_pptx(document, tmp_path / "report.pptx")
    with ZipFile(tmp_path / "report.pptx") as archive:
        slides = [" ".join(ET.fromstring(archive.read(name)).itertext()) for name in archive.namelist()
                  if name.startswith("ppt/slides/slide") and name.endswith(".xml")]
    for heading in ("Raman 피크 분석표", "Raman 분석 비율표"):
        pages = [slide for slide in slides if heading in slide]
        assert len(pages) > 1
        for i in range(1, 25):
            assert f"시료 {i}" in " ".join(pages)
        assert "생략" not in " ".join(pages)
    renderers.render_html(document, tmp_path / "report.html")
    story = []
    monkeypatch.setattr(renderers.SimpleDocTemplate, "build", lambda self, items, **kw: story.extend(items))
    renderers.render_pdf(document, tmp_path / "report.pdf")
    from edge_api_server.tests.test_report_completion_date import pdf_story_text
    for text in [document.to_markdown(), (tmp_path / "report.html").read_text(), pdf_story_text(story)]:
        assert "시료 24" in text and "Intensity" in text and "0.500" in text


def test_preview_and_saved_revision_keep_tables_readonly_and_escape_cell_values(tmp_path, monkeypatch):
    document = build(payload_for_tables(1))
    document = document_from_dict(document.to_dict(), image_path=tmp_path / "unused.png")
    changes = editor_document(document)
    section = next(s for s in changes["sections"] if s["sectionId"] == "raman_ratios")
    section["rows"][0][1] = '<img src=x onerror=alert(1)>'
    apply_edits(document, changes, "RAMAN")
    document.apply_llm_slots({name: "해석 초안" for name in ("summary", "key_findings", "interpretation", "qc_notes")})
    preview = analysis_preview(document)
    assert preview["status"] == "generated"  # Rule tables do not downgrade the LLM status.
    monkeypatch.setattr(spectrum_viewer, "get_plotlyjs", lambda: "")
    html = spectrum_viewer.viewer_html(title=document.title, figure={}, slides=[], analysis=preview)
    assert html.count("<table>") == 2 and "Intensity (a.u.)" in html and "0.500" in html
    assert '<img src=x' not in html and '&lt;img src=x' in html
    assert "contenteditable" not in html
    assert "overflow-x:auto" in html


def test_ppt_table_wraps_long_names_inside_the_same_row_and_stays_in_bounds(tmp_path):
    payload = payload_for_tables(1)
    sample = "시료 A - 탄소 코팅 전후 비교"
    payload["viewerFigure"]["data"][0]["name"] = sample
    document = build(payload)
    renderers.render_pptx(document, tmp_path / "report.pptx")
    ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main",
          "p": "http://schemas.openxmlformats.org/presentationml/2006/main"}
    with ZipFile(tmp_path / "report.pptx") as archive:
        for name in archive.namelist():
            if not name.startswith("ppt/slides/slide") or not name.endswith(".xml"):
                continue
            slide = ET.fromstring(archive.read(name))
            text = " ".join(slide.itertext())
            if "Raman 피크 분석표" not in text and "Raman 분석 비율표" not in text:
                continue
            cells = {shape.find("p:nvSpPr/p:cNvPr", ns).get("name"): shape
                     for shape in slide.findall(".//p:sp", ns)}
            first = cells["Cell 0-0"]
            assert " ".join(first.find("p:txBody", ns).itertext()) == sample
            # Wrapped names do not create empty data rows or detach their numeric values.
            if "Raman 피크 분석표" in text:
                assert len([key for key in cells if key.startswith("Cell ")]) == 8
            else:
                assert len([key for key in cells if key.startswith("Cell ")]) == 5
            for key, cell in cells.items():
                if not key.startswith("Cell "):
                    continue
                off = cell.find("p:spPr/a:xfrm/a:off", ns)
                ext = cell.find("p:spPr/a:xfrm/a:ext", ns)
                assert int(off.get("y")) + int(ext.get("cy")) <= 5852160


def test_very_long_edited_cells_continue_without_exceeding_page_height():
    document = build(payload_for_tables(1))
    section = document.section("raman_peaks")
    section.table.rows[0][:2] = ["시료명 " * 60, "분석자 설명 " * 180]
    pages = list(renderers._pptx_raman_table_payloads(document, section))
    assert len(pages) > 1
    ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
    for page in pages:
        slide = ET.fromstring(page["xml"])
        for transform in slide.findall(".//a:xfrm", ns):
            off, ext = transform.find("a:off", ns), transform.find("a:ext", ns)
            assert int(off.get("y")) + int(ext.get("cy")) <= 6858000
        assert "1350.125" in page["xml"] or "1580.25" in page["xml"]


def range_payload():
    xs, ys = [1200, 1300, 1350, 1400, 1450, 1500, 1580, 1650, 1700], [1, 3, 8, 3, 1, 2, 4, 2, 1]
    ratio = dict(id=1, title="ID/IG", method="raw-range-max", sampleGroup="raw-a",
                 rawRanges=dict(numerator=[1300, 1450], denominator=[1500, 1700]),
                 numeratorPoint=dict(x=1350, intensity=8, index=2), denominatorPoint=dict(x=1580, intensity=4, index=6),
                 numeratorKey=json.dumps(["raw-a", 1350]), denominatorKey=json.dumps(["raw-a", 1580]),
                 warnings=["D 최고점이 구간 경계 · 범위 확인"])
    parent = dict(name="Raw A", x=xs, y=[y+20 for y in ys], customdata=ys,
                  meta=dict(rist_sample_parent=True, rist_sample_group="raw-a", rist_view_offset=20))
    return dict(viewerFigure=dict(data=[parent], layout=dict(meta=dict(ristRamanRatios=[ratio]),
                annotations=[dict(name="rist_raman_ratio:1", text="ID/IG = 2.000")])) )


def test_range_ratios_publish_original_positions_intensities_ranges_and_llm_evidence_without_peaks(tmp_path):
    payload = range_payload()
    original = deepcopy(payload)
    document = build(payload)
    row = document.section("raman_ratios").table.rows[0]
    assert row[0] == "Raw A" and row[-1] == "2.000"
    assert "1350 cm⁻¹" in row[2] and "I = 8" in row[2] and "1300–1450" in row[2]
    assert "1580 cm⁻¹" in row[3] and "I = 4" in row[3]
    assert "검토 필요" in row[1]
    readonly = spectrum_viewer.readonly_figure(payload["viewerFigure"])
    assert readonly["layout"]["meta"]["ratioGroups"]["rist_raman_ratio:1"] == "raw-a"
    from app.report.edited_analysis import edited_graph_spec
    prepared = prepare_edited_report(payload)
    spec = edited_graph_spec(RamanReportBuilder().llm_slots(JOB, [{"data": prepared}]), prepared, "RAMAN")
    for spec in [spec]:
        facts = spec.facts
        ratio = facts["graph_notes"]["ratio_measurements"][0]
        assert facts["visible_peak_count"] == 0
        assert ratio["numerator"]["intensity"] == 8
        assert ratio["denominator"]["range_cm-1"] == [1500, 1700]
        assert ratio["value"] == 2 and ratio["warnings"]
        assert "raw-range-max" in ratio["method"]
    renderers.render_html(document, tmp_path / "batch.html")
    assert "I = 8" in (tmp_path / "batch.html").read_text()
    renderers.render_pptx(document, tmp_path / "batch.pptx")
    with ZipFile(tmp_path / "batch.pptx") as archive:
        slides = "".join(archive.read(n).decode() for n in archive.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
        assert "1350 cm⁻¹" in slides and "I = 8" in slides and "2.000" in slides
    assert payload == original


@pytest.mark.parametrize("invalid", ["missing", "hidden", "stale", "out_of_range", "overlap", "cross_sample", "nonfinite"])
def test_range_ratio_rejects_stale_invalid_or_deleted_sample(invalid):
    from app.report.builders import _raman_ratio_facts
    payload = range_payload()
    figure = payload["viewerFigure"]
    ratio = figure["layout"]["meta"]["ristRamanRatios"][0]
    if invalid == "missing":
        figure["data"] = []
    elif invalid == "hidden":
        figure["data"][0]["visible"] = False
    elif invalid == "stale":
        figure["data"][0]["customdata"][2] = 100
    elif invalid == "out_of_range":
        ratio["rawRanges"]["numerator"] = [100, 200]
    elif invalid == "overlap":
        ratio["rawRanges"]["numerator"] = [1300, 1700]
    elif invalid == "cross_sample":
        ratio["denominatorKey"] = json.dumps(["other", 1580])
    else:
        ratio["numeratorPoint"]["intensity"] = float("nan")
    assert _raman_ratio_facts(prepare_edited_report(payload)) == []


def test_compact_range_ratio_snapshot_and_stack_fallback_agree():
    from app.report.builders import _raman_ratio_facts
    payload = range_payload()
    figure = payload["viewerFigure"]
    del figure["data"][0]["customdata"]
    assert _raman_ratio_facts(prepare_edited_report(payload))[0]["value"] == 2
    for field in ("x", "y"):
        del figure["data"][0][field]
    assert _raman_ratio_facts(dict(figure=figure))[0]["value"] == 2

from copy import deepcopy
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import pytest

from app.report.builders import FtirReportBuilder, RamanReportBuilder, _RAMAN_CONDITION_ORDER
from app.report.editable import ensure_editable_fields, transfer_values, editor_document, apply_edits
from app.report.renderers import render_pptx, render_html, render_pdf


JOB = dict(job_id="conditions", request_number="REQ-001", experiment_code="RAMAN",
           equipment_code="LAB-01", operator_id="analyst", _generated_at="2026-10-15")
METADATA = dict(zip(_RAMAN_CONDITION_ORDER, ["532 nm", "10 mA", "1 mW", "0.1 mW/um2",
                                           "ND 1.0", "650 nm", "1800 g/mm", "50 um", "23°C"]))


def payload_for_samples(count=6):
    return {"samples": [dict(label=f"S{i+1}", fileName="six-samples.txt", metadata=dict(METADATA),
                             pointCount=100, peakCount=0) for i in range(count)]}


def build(payload, builder=None):
    return (builder or RamanReportBuilder()).build(JOB, [{"data": payload}])


def slide_texts(path):
    with ZipFile(path) as archive:
        names = [n for n in archive.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")]
        return [" ".join(ET.fromstring(archive.read(n)).itertext()) for n in names]


def test_six_txt_samples_share_one_condition_set_and_llm_evidence():
    payload = payload_for_samples()
    payload["experimentConditions"] = [dict(target=s["label"], label=k, value=v)
                                       for s in payload["samples"] for k, v in METADATA.items()]
    original = deepcopy(payload)
    document = build(payload)
    section = document.section("experiment_conditions")
    assert document.sections[0] is section
    assert section.heading == "공통 실험환경/조건"
    assert section.table.rows == [["공통", k, v] for k, v in METADATA.items()]
    spec = RamanReportBuilder().llm_slots(JOB, [{"data": payload}])
    assert spec.facts["experiment_conditions"] == section.table.rows
    assert "실험조건:" not in spec.fallback["key_findings"]
    assert payload == original


def test_multiple_txt_conditions_are_grouped_by_file_without_dropping_differences():
    payload = payload_for_samples(4)
    for sample in payload["samples"][2:]:
        sample["fileName"] = "other.txt"
        sample["metadata"]["Excitation Wavelength"] = "785 nm"
    rows = build(payload).section("experiment_conditions").table.rows
    assert [r for r in rows if r[1] == "Excitation Wavelength"] == [
        ["six-samples.txt", "Excitation Wavelength", "532 nm"],
        ["other.txt", "Excitation Wavelength", "785 nm"]]
    assert [r for r in rows if r[1] == "Grating"] == [["공통", "Grating", "1800 g/mm"]]


def test_conflicting_manual_overrides_in_same_txt_keep_sample_identity():
    payload = payload_for_samples(2)
    payload["reportConditionFields"] = [
        dict(target="공통", label="Excitation Wavelength", value="532 nm"),
        dict(target="S1", label="Excitation Wavelength", value="532 nm"),
        dict(target="S2", label="Excitation Wavelength", value="785 nm")]
    rows = build(payload).section("experiment_conditions").table.rows
    assert [r for r in rows if r[1] == "Excitation Wavelength"] == [
        ["S1", "Excitation Wavelength", "532 nm"], ["S2", "Excitation Wavelength", "785 nm"]]


@pytest.mark.parametrize("value", ["", "633 nm"])
def test_explicit_common_values_and_blanks_override_raw_and_remain_editable(value):
    payload = payload_for_samples()
    payload["reportConditionFields"] = [dict(target="공통", label="laser wavelength", value=value)]
    document = build(payload)
    ensure_editable_fields(document, payload, "RAMAN", transfer_values(payload))
    rows = document.section("experiment_conditions").table.rows
    assert [r for r in rows if r[1] == "Excitation Wavelength"] == [["공통", "Excitation Wavelength", value]]
    assert ["공통", "실험환경/비고", ""] in rows
    changes = editor_document(document)
    section = next(s for s in changes["sections"] if s["sectionId"] == "experiment_conditions")
    next(r for r in section["rows"] if r[1] == "Excitation Wavelength")[2] = "785 nm"
    apply_edits(document, changes, "RAMAN")
    assert [r for r in document.section("experiment_conditions").table.rows if r[1] == "Excitation Wavelength"] == [
        ["공통", "Excitation Wavelength", "785 nm"]]


def test_blank_sample_fields_inherit_common_without_duplicating_rows():
    payload = payload_for_samples()
    payload["reportConditionFields"] = [dict(target="공통", label="Grating", value="600 g/mm")] + [
        dict(target=s["label"], label="Grating", value="") for s in payload["samples"]]
    assert [r for r in build(payload).section("experiment_conditions").table.rows if r[1] == "Grating"] == [
        ["공통", "Grating", "600 g/mm"]]


def test_common_conditions_are_before_results_once_in_ppt_and_html(tmp_path):
    payload = payload_for_samples()
    document = build(payload)
    ensure_editable_fields(document, payload, "RAMAN", transfer_values(payload))
    # The LIMS experiment code may replace RAMAN; ordering must still work.
    document.experiment_code = "LIMS-RAMAN-001"
    render_pptx(document, tmp_path / "report.pptx")
    slides = slide_texts(tmp_path / "report.pptx")
    assert "공통 실험환경/조건" in slides[1]
    assert sum("공통 실험환경/조건" in s for s in slides) == 1
    assert "분석 요약" in slides[2]
    assert "Excitation Wavelength" in slides[1] and "실험환경/비고" in slides[1]
    assert "S1" not in slides[1] and "S6" not in slides[1]
    assert "시료 및 피크 수" in " ".join(slides[3:])
    render_html(document, tmp_path / "report.html")
    html = (tmp_path / "report.html").read_text()
    assert html.count("Excitation Wavelength") == 1
    assert html.index("공통 실험환경/조건") < html.index("시료 정보")


def test_pdf_places_common_conditions_before_graph_and_does_not_repeat_them(tmp_path, monkeypatch):
    from app.report import renderers
    from reportlab.platypus import Paragraph
    from reportlab.lib.styles import getSampleStyleSheet
    graph = Paragraph("GRAPH_SENTINEL", getSampleStyleSheet()["Normal"])
    monkeypatch.setattr(renderers, "_pdf_figure_story", lambda *a: [graph])
    story = []
    monkeypatch.setattr(renderers.SimpleDocTemplate, "build", lambda self, items, **kw: story.extend(items))
    render_pdf(build(payload_for_samples()), tmp_path / "report.pdf")
    headings = [getattr(item, "text", "") for item in story]
    assert headings.count("공통 실험환경/조건") == 1
    assert headings.index("공통 실험환경/조건") < headings.index("GRAPH_SENTINEL") < headings.index("시료 정보")


def test_ftir_keeps_sample_specific_conditions():
    payload = payload_for_samples(2)
    document = build(payload, FtirReportBuilder())
    rows = document.section("experiment_conditions").table.rows
    assert any(r[0] == "S1" for r in rows) and any(r[0] == "S2" for r in rows)
    assert document.sections[0].section_id == "sample_info"

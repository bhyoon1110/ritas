from dataclasses import replace
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from app.report import renderers
from app.report.builders import FtirReportBuilder, RamanReportBuilder
from app.report.editable import apply_transfer, document_from_dict
from app.report.model import ReportDocument


TIMESTAMP = "2026-10-15T17:42:35+09:00"
DATE = "2026-10-15"


def simple_document(code, timestamp=TIMESTAMP):
    return ReportDocument("date-test", "분석 보고서", code, {}, timestamp)


def pptx_text(path):
    with ZipFile(path) as archive:
        slides = [name for name in archive.namelist()
                  if name.startswith("ppt/slides/slide") and name.endswith(".xml")]
        texts = [" ".join(ET.fromstring(archive.read(name)).itertext()) for name in slides]
        core = archive.read("docProps/core.xml").decode()
    return texts, core


def pdf_story_text(item):
    if isinstance(item, (list, tuple)):
        return " ".join(pdf_story_text(child) for child in item)
    if hasattr(item, "_cellvalues"):
        return pdf_story_text(item._cellvalues)
    return getattr(item, "text", "")


@pytest.mark.parametrize("code", ["FTIR", "FT-IR", "IR", "RAMAN", "RIN", "RIN-RAMAN", " ftir "])
def test_spectrum_aliases_use_completion_date_without_changing_stored_timestamp(code):
    document = simple_document(code)
    assert document.completion_date == DATE
    assert ("완료일", DATE) in renderers._document_metadata(document)
    assert f"- 완료일: {DATE}" in document.to_markdown()
    assert document.to_dict()["generatedAt"] == TIMESTAMP


@pytest.mark.parametrize("timestamp, expected", [
    ("2026-10-15T00:05:01+09:00", DATE),
    ("2026-10-15T23:59:59Z", DATE),
    ("2026-10-15 17:42:35", DATE),
    (DATE, DATE),
    ("", ""), ("-", ""), ("WEB-PREVIEW", ""), ("invalid", ""),
])
def test_completion_date_keeps_recorded_calendar_date_or_blank(timestamp, expected):
    document = simple_document("FTIR", timestamp)
    assert document.completion_date == expected
    assert document.generated_at == timestamp


@pytest.mark.parametrize("builder, project", [(FtirReportBuilder, "FT-IR"), (RamanReportBuilder, "RAMAN")])
@pytest.mark.parametrize("lims_code", ["", "LIMS-123"])
def test_all_report_formats_and_restored_edits_show_only_completion_date(tmp_path, monkeypatch, builder, project, lims_code):
    document = builder().build(dict(job_id="date-test", request_number="REQ-001", experiment_code=project,
                                   equipment_code="EQ-01", operator_id="analyst", _generated_at=TIMESTAMP), [])
    apply_transfer(document, dict(requestNumber="REQ-001", limsExperimentCode=lims_code,
                                  equipmentCode="EQ-01", operatorId="analyst"), project)
    # Preview edits restore server-owned report.json and can replace the title/code.
    document.title = "사용자가 수정한 보고서 제목"
    document = document_from_dict(document.to_dict(), image_path=tmp_path / "unused.png")
    assert document.completion_date == DATE
    renderers.render_pptx(document, tmp_path / "report.pptx")
    slides, core = pptx_text(tmp_path / "report.pptx")
    assert "완료일" in slides[0]
    assert all(DATE in slide for slide in slides)  # Every slide footer.
    assert TIMESTAMP in core  # Machine-readable audit timestamp is preserved.
    renderers.render_html(document, tmp_path / "report.html")
    story = []
    monkeypatch.setattr(renderers.SimpleDocTemplate, "build", lambda self, items, **kwargs: story.extend(items))
    renderers.render_pdf(document, tmp_path / "report.pdf")
    for text in [" ".join(slides), (tmp_path / "report.html").read_text(),
                 document.to_markdown(), pdf_story_text(story)]:
        assert "완료일" in text and DATE in text
        assert "생성시각" not in text and "생성 시각" not in text
        assert "17:42" not in text and TIMESTAMP not in text
    # A new revision uses its own generation date, not the previous cover date.
    revised = replace(document, generated_at="2026-10-16T00:01:02+09:00")
    assert revised.completion_date == "2026-10-16"
    assert revised.to_dict()["generatedAt"] == "2026-10-16T00:01:02+09:00"


@pytest.mark.parametrize("code", ["XRD", "TEM", "OTHER"])
def test_other_report_types_keep_existing_timestamp_display(tmp_path, monkeypatch, code):
    document = simple_document(code)
    assert document.completion_date is None
    assert ("생성시각", "2026-10-15 17:42") in renderers._document_metadata(document)
    renderers.render_pptx(document, tmp_path / "report.pptx")
    slides, _ = pptx_text(tmp_path / "report.pptx")
    assert "17:42" in slides[0]
    renderers.render_html(document, tmp_path / "report.html")
    story = []
    monkeypatch.setattr(renderers.SimpleDocTemplate, "build", lambda self, items, **kwargs: story.extend(items))
    renderers.render_pdf(document, tmp_path / "report.pdf")
    for text in [(tmp_path / "report.html").read_text(), document.to_markdown(), pdf_story_text(story)]:
        assert "생성시각" in text and TIMESTAMP in text
        assert "완료일" not in text

from __future__ import annotations

import posixpath
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import pytest

from app.report import posco_template, renderers
from app.report.model import ReportDocument, ReportFigure, ReportSection, ReportTable


NS = posco_template.NS


def sample_document(code: str, image: Path) -> ReportDocument:
    return ReportDocument(
        job_id="posco-report-test", title=f"{code} 분석 보고서", experiment_code=code,
        pk={"requestNumber": "REQ-POSCO-001", "equipmentCode": "SPEC-01", "operatorId": "analyst"},
        generated_at="2026-10-15T13:00:00+09:00",
        figures=[ReportFigure("graph", "스펙트럼 분석 결과", str(image))],
        sections=[
            ReportSection("summary", "고객 보고서용 요약", paragraphs=["분석자가 검토한 요약입니다."], source="user"),
            ReportSection("key_findings", "핵심 관찰사항", paragraphs=["선택한 피크를 비교하였습니다."]),
            ReportSection("interpretation", "피크 해석", paragraphs=["추가 실험으로 성분을 확인하십시오."], source="llm"),
            ReportSection("report_transfer", "보고서 전송 정보", table=ReportTable(
                ["항목", "값"], [["의뢰번호", "REQ-POSCO-001"], ["실험자", "analyst"]])),
            ReportSection("experiment_conditions", "실험환경 및 조건", table=ReportTable(
                ["항목", "값"], [["측정 온도", ""], ["측정 방식", "ATR"]])),
            ReportSection("peaks", "피크 정보", table=ReportTable(
                ["피크", "해석"], [["1700 cm⁻¹", "C=O"]])),
            ReportSection("notes", "검토 메모", paragraphs=["편집한 문안을 유지합니다."]),
        ],
    )


@pytest.mark.parametrize("code", ["FTIR", "FT-IR", "IR", "RAMAN", "RIN", "RIN-RAMAN"])
def test_spectrum_ppt_reuses_tem_background_without_sample_payload(tmp_path, code):
    image = tmp_path / "graph.jpeg"
    image.write_bytes(b"report graph fixture")
    output = tmp_path / "report.pptx"
    renderers.render_pptx(sample_document(code, image), output)
    with ZipFile(posco_template.TEMPLATE_PATH) as source, ZipFile(output) as report:
        # Byte-for-byte reuse, once per report, under a non-conflicting name.
        assert report.read(f"ppt/media/{posco_template.MEDIA_NAME}") == source.read("ppt/media/image1.jpeg")
        assert report.read("ppt/media/image1.jpg") == image.read_bytes()
        assert len([n for n in report.namelist() if n.startswith("ppt/media/")]) == 2
        assert not any("notesSlides/" in n or n.endswith(".tiff") for n in report.namelist())
        layout = ET.fromstring(report.read("ppt/slideLayouts/slideLayout1.xml"))
        assert not layout.findall(".//a:t", NS)  # No TEM sample text or / 22 footer.
        picture = layout.find("p:cSld/p:spTree/p:pic", NS)
        assert picture.find("p:blipFill/a:srcRect", NS).get("b") == "88292"
        assert picture.find("p:spPr/a:xfrm", NS).get("flipH") == "1"
        assert picture.find("p:spPr/a:xfrm/a:ext", NS).get("cx") == str(renderers.PPTX_SLIDE_W)
        slide_names = [n for n in report.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")]
        assert len(slide_names) >= 8
        all_text = []
        for name in slide_names:
            root = ET.fromstring(report.read(name))
            assert root.find("p:cSld/p:bg/p:bgPr/a:solidFill/a:srgbClr", NS).get("val") == "FFFFFF"
            text = " ".join(node.text or "" for node in root.findall(".//a:t", NS))
            all_text.append(text)
            assert ("Raman" if code in {"RAMAN", "RIN", "RIN-RAMAN"} else "FT-IR") + " 분석 결과" in text
            assert "TEM" not in text and "AlGn5" not in text and "/ 22" not in text
            names = [node.get("name") for node in root.findall(".//p:cNvPr", NS)]
            assert "Left accent" not in names and "Top accent" not in names
            ids = [node.get("id") for node in root.findall(".//p:cNvPr", NS)]
            assert len(set(ids)) == len(ids)
            for shape in root.findall("p:cSld/p:spTree/p:sp", NS):
                transform = shape.find("p:spPr/a:xfrm", NS)
                if transform is None:
                    continue
                offset, extent = transform.find("a:off", NS), transform.find("a:ext", NS)
                if shape.find("p:nvSpPr/p:cNvPr", NS).get("name") != "Experiment banner title":
                    assert int(offset.get("y")) >= 684000  # Content cannot cover the banner.
                assert int(offset.get("x")) + int(extent.get("cx")) <= renderers.PPTX_SLIDE_W
                assert int(offset.get("y")) + int(extent.get("cy")) <= renderers.PPTX_SLIDE_H
        content = "\n".join(all_text)
        for expected in ("REQ-POSCO-001", "측정 온도", "측정 방식", "C=O", "편집한 문안을 유지합니다."):
            assert expected in content
        # Every relationship resolves inside the generated package, including
        # all figure/layout/media links used by LibreOffice and the HTML preview.
        for name in report.namelist():
            if not name.endswith(".rels"):
                continue
            base = posixpath.dirname(posixpath.dirname(name))
            for rel in ET.fromstring(report.read(name)):
                if rel.get("TargetMode") != "External":
                    target = posixpath.normpath(posixpath.join(base, rel.get("Target")))
                    assert target in report.namelist(), (name, target)


@pytest.mark.parametrize("code", ["XRD", "TEM", "OTHER"])
def test_other_report_types_keep_their_original_style(tmp_path, monkeypatch, code):
    def unexpected(*args):
        raise AssertionError("Unrelated reports must not load the TEM template")

    monkeypatch.setattr(renderers, "load_posco_layout", unexpected)
    output = tmp_path / "report.pptx"
    renderers.render_pptx(sample_document(code, tmp_path / "absent.png"), output)
    with ZipFile(output) as report:
        assert f"ppt/media/{posco_template.MEDIA_NAME}" not in report.namelist()
        assert b'Left accent' in report.read("ppt/slides/slide1.xml")


def test_missing_template_fails_before_writing_report(tmp_path, monkeypatch):
    posco_template.load_posco_layout.cache_clear()
    monkeypatch.setattr(posco_template, "TEMPLATE_PATH", tmp_path / "missing.pptx")
    output = tmp_path / "report.pptx"
    with pytest.raises(FileNotFoundError):
        renderers.render_pptx(sample_document("FTIR", tmp_path / "absent.png"), output)
    assert not output.exists()

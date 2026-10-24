"""Real PNG/PPT geometry: graph ratio must survive the report frame."""
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from PIL import Image
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Image as PdfImage

from app.report.model import ReportDocument, ReportFigure
from app.report.renderers import render_pptx, _pdf_figure_story


@pytest.mark.parametrize("experiment", ["FTIR", "RAMAN"])
@pytest.mark.parametrize("size", [(1200, 900), (1600, 900), (900, 1200), (1100, 820), (2100, 900), (800, 800)])
def test_report_preserves_canvas_aspect_in_ppt_and_pdf(tmp_path, experiment, size):
    image = tmp_path / "spectrum.png"
    Image.new("RGB", size, "white").save(image)
    document = ReportDocument("sizing", "크기 검증", experiment, {}, "2026-10-24")
    document.figures = [ReportFigure("graph", "그래프", str(image))]
    pptx = tmp_path / "report.pptx"
    render_pptx(document, pptx)
    ns = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
          "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
    found = 0
    with ZipFile(pptx) as archive:
        for name in archive.namelist():
            if not name.startswith("ppt/slides/slide") or not name.endswith(".xml"):
                continue
            for pic in ET.fromstring(archive.read(name)).findall(".//p:pic", ns):
                if pic.find("p:nvPicPr/p:cNvPr", ns).get("name") != "image1.png":
                    continue
                found += 1
                ext = pic.find("p:spPr/a:xfrm/a:ext", ns)
                off = pic.find("p:spPr/a:xfrm/a:off", ns)
                w, h = int(ext.get("cx")), int(ext.get("cy"))
                assert w / h == pytest.approx(size[0] / size[1], abs=1e-6)
                assert 701040 <= int(off.get("x")) <= 701040 + 10789920 - w
                assert 1127760 <= int(off.get("y")) <= 1127760 + 4389120 - h
                assert pic.find("p:nvPicPr/p:cNvPicPr/a:picLocks", ns).get("noChangeAspect") == "1"
    assert found == 1
    styles = getSampleStyleSheet()
    story = _pdf_figure_story(document, {"section": styles["Heading2"], "caption": styles["Normal"]}, 500)
    pictures = [item for item in story if isinstance(item, PdfImage)]
    assert len(pictures) == 1
    assert pictures[0].drawWidth / pictures[0].drawHeight == pytest.approx(size[0] / size[1])

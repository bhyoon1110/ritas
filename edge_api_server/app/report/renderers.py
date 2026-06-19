"""ReportDocument 렌더러.

PPTX는 Open XML 패키지로, PDF는 ReportLab으로 만든다. 렌더링의 기준 데이터는
report.json과 동일한 ReportDocument이며, 사람이 검토할 수 있는 요약형 결과물을
제공하는 것을 목표로 한다.
"""

from __future__ import annotations

import html
import textwrap
import zipfile
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from .model import ReportDocument, ReportSection


def render_requested_report(
    document: ReportDocument,
    report_dir: Path,
    report_format: str,
    *,
    pdf_font_path: Path | None = None,
) -> Path:
    selected = report_format.upper()
    if selected == "PDF":
        path = report_dir / "report.pdf"
        render_pdf(document, path, font_path=pdf_font_path)
        return path
    path = report_dir / "report.pptx"
    render_pptx(document, path)
    return path


def _section_lines(section: ReportSection) -> list[str]:
    lines: list[str] = []
    lines.extend(section.paragraphs)
    lines.extend(f"- {bullet}" for bullet in section.bullets)
    if section.table is not None:
        lines.append(" / ".join(section.table.columns))
        for row in section.table.rows:
            lines.append(" | ".join(row))
    return [line for line in lines if line]


def _plain_lines(document: ReportDocument) -> list[str]:
    lines = [
        document.title,
        f"요청번호: {document.pk.get('requestNumber', '')}",
        f"실험: {document.experiment_code}",
        f"장비: {document.pk.get('equipmentCode', '')}",
        f"작업자: {document.pk.get('operatorId', '')}",
        f"생성시각: {document.generated_at}",
        "",
    ]
    for section in document.sections:
        lines.append(section.heading)
        lines.extend(_section_lines(section))
        lines.append("")
    if document.llm_error:
        lines.append(f"LLM 보조 설명 생성 실패: {document.llm_error}")
    return lines


def _pdf_font_name(font_path: Path | None) -> str:
    if font_path is None:
        name = "HYSMyeongJo-Medium"
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
        return name
    if not font_path.is_file():
        raise ValueError(f"PDF 임베드 폰트를 찾을 수 없습니다: {font_path}")
    name = "RISTEmbeddedKorean"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(name, str(font_path)))
    return name


def render_pdf(
    document: ReportDocument,
    path: Path,
    *,
    font_path: Path | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    font_name = _pdf_font_name(font_path)
    _, page_height = A4
    left = 50
    top = page_height - 50
    line_height = 16
    document_canvas = canvas.Canvas(str(path), pagesize=A4, pageCompression=1)
    document_canvas.setTitle(document.title)
    document_canvas.setFont(font_name, 12)
    y = top
    for line in _plain_lines(document):
        for part in textwrap.wrap(line, width=62) or [""]:
            if y < 50:
                document_canvas.showPage()
                document_canvas.setFont(font_name, 12)
                y = top
            document_canvas.drawString(left, y, part)
            y -= line_height
    document_canvas.save()


def render_pptx(document: ReportDocument, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slides = _pptx_slide_payloads(document)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _pptx_content_types(len(slides)))
        archive.writestr("_rels/.rels", _pptx_root_rels())
        archive.writestr("ppt/presentation.xml", _pptx_presentation(len(slides)))
        archive.writestr("ppt/_rels/presentation.xml.rels", _pptx_presentation_rels(len(slides)))
        archive.writestr("ppt/theme/theme1.xml", _pptx_theme())
        archive.writestr("ppt/slideMasters/slideMaster1.xml", _pptx_empty_master())
        archive.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", _pptx_master_rels())
        archive.writestr("ppt/slideLayouts/slideLayout1.xml", _pptx_empty_layout())
        archive.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", _pptx_layout_rels())
        for idx, slide in enumerate(slides, start=1):
            archive.writestr(f"ppt/slides/slide{idx}.xml", slide)
            archive.writestr(f"ppt/slides/_rels/slide{idx}.xml.rels", _pptx_slide_rels())


def _pptx_slide_payloads(document: ReportDocument) -> list[str]:
    slides = [_pptx_slide(document.title, [
        f"요청번호: {document.pk.get('requestNumber', '')}",
        f"실험: {document.experiment_code}",
        f"장비: {document.pk.get('equipmentCode', '')}",
        f"작업자: {document.pk.get('operatorId', '')}",
        f"생성시각: {document.generated_at}",
    ])]
    for section in document.sections:
        lines = []
        for line in _section_lines(section):
            lines.extend(textwrap.wrap(line, width=54) or [""])
        slides.append(_pptx_slide(section.heading, lines[:14]))
    if document.llm_error:
        slides.append(_pptx_slide("LLM 보조 설명", [document.llm_error]))
    return slides


def _pptx_paragraphs(lines: list[str]) -> str:
    if not lines:
        return "<a:p/>"
    return "".join(
        "<a:p><a:r><a:rPr lang=\"ko-KR\" sz=\"1800\"/>"
        f"<a:t>{html.escape(line)}</a:t></a:r></a:p>"
        for line in lines
    )


def _pptx_slide(title: str, lines: list[str]) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld><p:spTree>
    <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
    <p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="457200" y="274320"/><a:ext cx="8229600" cy="685800"/></a:xfrm></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="ko-KR" sz="3200" b="1"/><a:t>{html.escape(title)}</a:t></a:r></a:p></p:txBody></p:sp>
    <p:sp><p:nvSpPr><p:cNvPr id="3" name="Content"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="609600" y="1143000"/><a:ext cx="7924800" cy="5486400"/></a:xfrm></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/>{_pptx_paragraphs(lines)}</p:txBody></p:sp>
  </p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>"""


def _pptx_content_types(slide_count: int) -> str:
    slides = "".join(
        f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        for i in range(1, slide_count + 1)
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
  <Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>
  <Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>
  <Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>
  {slides}
</Types>"""


def _pptx_root_rels() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
</Relationships>"""


def _pptx_presentation(slide_count: int) -> str:
    slide_ids = "".join(f'<p:sldId id="{255 + i}" r:id="rId{i}"/>' for i in range(1, slide_count + 1))
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId{slide_count + 1}"/></p:sldMasterIdLst>
  <p:sldIdLst>{slide_ids}</p:sldIdLst>
  <p:sldSz cx="9144000" cy="6858000" type="screen4x3"/>
  <p:notesSz cx="6858000" cy="9144000"/>
</p:presentation>"""


def _pptx_presentation_rels(slide_count: int) -> str:
    rels = [
        f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i}.xml"/>'
        for i in range(1, slide_count + 1)
    ]
    rels.append(
        f'<Relationship Id="rId{slide_count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{''.join(rels)}</Relationships>"""


def _pptx_slide_rels() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
</Relationships>"""


def _pptx_empty_master() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld><p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst><p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles></p:sldMaster>"""


def _pptx_master_rels() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>
</Relationships>"""


def _pptx_empty_layout() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="blank"><p:cSld name="Blank"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld></p:sldLayout>"""


def _pptx_layout_rels() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>
</Relationships>"""


def _pptx_theme() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="RIST"><a:themeElements><a:clrScheme name="RIST"><a:dk1><a:srgbClr val="1F2933"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="334E68"/></a:dk2><a:lt2><a:srgbClr val="F0F4F8"/></a:lt2><a:accent1><a:srgbClr val="2F80ED"/></a:accent1><a:accent2><a:srgbClr val="27AE60"/></a:accent2><a:accent3><a:srgbClr val="F2994A"/></a:accent3><a:accent4><a:srgbClr val="9B51E0"/></a:accent4><a:accent5><a:srgbClr val="EB5757"/></a:accent5><a:accent6><a:srgbClr val="56CCF2"/></a:accent6><a:hlink><a:srgbClr val="2F80ED"/></a:hlink><a:folHlink><a:srgbClr val="9B51E0"/></a:folHlink></a:clrScheme><a:fontScheme name="RIST"><a:majorFont><a:latin typeface="Arial"/><a:ea typeface="맑은 고딕"/></a:majorFont><a:minorFont><a:latin typeface="Arial"/><a:ea typeface="맑은 고딕"/></a:minorFont></a:fontScheme><a:fmtScheme name="RIST"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst><a:lnStyleLst><a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>"""

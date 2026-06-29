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

from .model import LLM_FALLBACK_NOTICE, ReportDocument, ReportFigure, ReportSection


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


def render_report_formats(
    document: ReportDocument,
    report_dir: Path,
    report_formats: list[str],
    *,
    pdf_font_path: Path | None = None,
) -> list[Path]:
    """요청한 사용자용 산출물을 모두 렌더링한다."""
    rendered: list[Path] = []
    for report_format in report_formats:
        selected = report_format.upper()
        if selected == "HTML":
            path = report_dir / "report.html"
            render_html(document, path)
        else:
            path = render_requested_report(
                document,
                report_dir,
                selected,
                pdf_font_path=pdf_font_path,
            )
        rendered.append(path)
    return rendered


def _section_lines(section: ReportSection) -> list[str]:
    lines: list[str] = []
    for paragraph in section.paragraphs:
        lines.extend(part for part in paragraph.splitlines() if part)
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
        lines.append(LLM_FALLBACK_NOTICE)
    return lines


def render_html(document: ReportDocument, path: Path) -> None:
    """외부 자산 없이 열 수 있는 사용자용 HTML 보고서를 만든다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sections: list[str] = []
    for section in document.sections:
        body = "".join(
            f"<p>{html.escape(paragraph)}</p>" for paragraph in section.paragraphs
        )
        if section.bullets:
            body += "<ul>" + "".join(
                f"<li>{html.escape(bullet)}</li>" for bullet in section.bullets
            ) + "</ul>"
        if section.table:
            header = "".join(
                f"<th>{html.escape(column)}</th>" for column in section.table.columns
            )
            rows = "".join(
                "<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>"
                for row in section.table.rows
            )
            body += f"<table><thead><tr>{header}</tr></thead><tbody>{rows}</tbody></table>"
        sections.append(f"<section><h2>{html.escape(section.heading)}</h2>{body}</section>")
    metadata = " · ".join(
        f"{label}: {html.escape(document.pk.get(key, ''))}"
        for label, key in (("요청번호", "requestNumber"), ("실험", "experimentCode"), ("장비", "equipmentCode"), ("작업자", "operatorId"))
    )
    path.write_text(
        "<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(document.title)}</title><style>"
        "body{font-family:Arial,sans-serif;line-height:1.6;margin:40px;max-width:920px}"
        "h1{margin-bottom:4px}h2{margin-top:32px}table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #bbb;padding:7px;text-align:left}th{background:#f0f4f8}"
        "</style></head><body>"
        f"<h1>{html.escape(document.title)}</h1><p>{metadata}</p>"
        f"<p>생성시각: {html.escape(document.generated_at)}</p>{''.join(sections)}"
        "</body></html>",
        encoding="utf-8",
    )


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
            archive.writestr(f"ppt/slides/slide{idx}.xml", slide["xml"])
            archive.writestr(f"ppt/slides/_rels/slide{idx}.xml.rels", slide["rels"])
            for media_name, media_path in slide.get("media", []):
                archive.write(media_path, f"ppt/media/{media_name}")


def _pptx_slide_payloads(document: ReportDocument) -> list[dict[str, object]]:
    slides: list[dict[str, object]] = [_pptx_text_payload(document.title, [
        f"요청번호: {document.pk.get('requestNumber', '')}",
        f"실험: {document.experiment_code}",
        f"장비: {document.pk.get('equipmentCode', '')}",
        f"작업자: {document.pk.get('operatorId', '')}",
        f"생성시각: {document.generated_at}",
    ])]
    for section in document.sections:
        slides.append(_pptx_text_payload(section.heading, _section_lines(section)[:14]))
    media_index = 1
    for figure in document.figures:
        figure_path = Path(figure.path)
        if not figure_path.is_file() or figure_path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            continue
        suffix = ".jpg" if figure_path.suffix.lower() == ".jpeg" else figure_path.suffix.lower()
        media_name = f"image{media_index}{suffix}"
        media_index += 1
        caption_section = document.section(figure.caption_slot)
        caption = ""
        if caption_section is not None and caption_section.paragraphs:
            caption = caption_section.paragraphs[0]
        slides.append(_pptx_image_payload(figure, media_name, figure_path, caption))
    if document.llm_error:
        slides.append(_pptx_text_payload("LLM 보조 설명", [LLM_FALLBACK_NOTICE]))
    return slides


def _pptx_text_payload(title: str, lines: list[str]) -> dict[str, object]:
    return {"xml": _pptx_slide(title, lines), "rels": _pptx_slide_rels(), "media": []}


def _pptx_image_payload(
    figure: ReportFigure,
    media_name: str,
    media_path: Path,
    caption: str,
) -> dict[str, object]:
    lines = [caption] if caption else []
    return {
        "xml": _pptx_image_slide(figure.title, media_name, lines),
        "rels": _pptx_slide_rels(media_name),
        "media": [(media_name, media_path)],
    }


def _pptx_body_pr(*, anchor: str = "t") -> str:
    return (
        f'<a:bodyPr wrap="square" anchor="{anchor}" rtlCol="0">'
        '<a:normAutofit fontScale="85000" lnSpcReduction="20000"/>'
        "</a:bodyPr>"
    )


def _pptx_paragraphs(lines: list[str], *, font_size: int = 1800) -> str:
    if not lines:
        return "<a:p/>"
    return "".join(
        f"<a:p><a:pPr marL=\"0\" indent=\"0\"/><a:r><a:rPr lang=\"ko-KR\" sz=\"{font_size}\"/>"
        f"<a:t>{html.escape(line)}</a:t></a:r></a:p>"
        for line in lines
    )


def _pptx_slide(title: str, lines: list[str]) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld><p:spTree>
    <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
    <p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="457200" y="274320"/><a:ext cx="8229600" cy="685800"/></a:xfrm></p:spPr><p:txBody>{_pptx_body_pr()}<a:lstStyle/><a:p><a:r><a:rPr lang="ko-KR" sz="3200" b="1"/><a:t>{html.escape(title)}</a:t></a:r></a:p></p:txBody></p:sp>
    <p:sp><p:nvSpPr><p:cNvPr id="3" name="Content"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="609600" y="1143000"/><a:ext cx="7924800" cy="5486400"/></a:xfrm></p:spPr><p:txBody>{_pptx_body_pr()}<a:lstStyle/>{_pptx_paragraphs(lines)}</p:txBody></p:sp>
  </p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>"""


def _pptx_image_slide(title: str, media_name: str, lines: list[str]) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld><p:spTree>
    <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
    <p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="457200" y="274320"/><a:ext cx="8229600" cy="548640"/></a:xfrm></p:spPr><p:txBody>{_pptx_body_pr()}<a:lstStyle/><a:p><a:r><a:rPr lang="ko-KR" sz="3000" b="1"/><a:t>{html.escape(title)}</a:t></a:r></a:p></p:txBody></p:sp>
    <p:pic><p:nvPicPr><p:cNvPr id="3" name="{html.escape(media_name)}"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="rId2"/><a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr><a:xfrm><a:off x="609600" y="1005840"/><a:ext cx="7924800" cy="4389120"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>
    <p:sp><p:nvSpPr><p:cNvPr id="4" name="Caption"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="609600" y="5623560"/><a:ext cx="7924800" cy="822960"/></a:xfrm></p:spPr><p:txBody>{_pptx_body_pr()}<a:lstStyle/>{_pptx_paragraphs(lines, font_size=1600)}</p:txBody></p:sp>
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
  <Default Extension="png" ContentType="image/png"/>
  <Default Extension="jpg" ContentType="image/jpeg"/>
  <Default Extension="jpeg" ContentType="image/jpeg"/>
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


def _pptx_slide_rels(media_name: str | None = None) -> str:
    media_rel = ""
    if media_name:
        media_rel = (
            '<Relationship Id="rId2" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            f'Target="../media/{html.escape(media_name)}"/>'
        )
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
  """ + media_rel + """
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

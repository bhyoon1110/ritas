"""ReportDocument 렌더러.

PPTX는 Open XML 패키지로, PDF는 ReportLab으로 만든다. 렌더링의 기준 데이터는
report.json과 동일한 ReportDocument이며, 사람이 검토할 수 있는 요약형 결과물을
제공하는 것을 목표로 한다.
"""

from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .model import LLM_FALLBACK_NOTICE, ReportDocument, ReportFigure, ReportSection

PPTX_SLIDE_W = 12192000
PPTX_SLIDE_H = 6858000
PPTX_MARGIN_X = 609600
PPTX_TITLE_Y = 365760
PPTX_TITLE_H = 548640
PPTX_CONTENT_Y = 1188720
PPTX_FOOTER_Y = 6492240
PPTX_BLUE = "2F80ED"
PPTX_NAVY = "172B4D"
PPTX_TEXT = "243B53"
PPTX_MUTED = "627D98"
PPTX_LINE = "D9E2EC"
PPTX_BG = "F8FAFC"
PPTX_CARD = "FFFFFF"
PPTX_GREEN = "27AE60"
PPTX_ORANGE = "F2994A"
PPTX_RED = "EB5757"
_INSIGHT_SECTION_IDS = frozenset({"interpretation", "qc_notes", "narrative"})


class PptxPdfConversionError(RuntimeError):
    """PPTX를 PDF로 변환하지 못했을 때의 보고서 렌더링 오류."""


def convert_pptx_to_pdf(
    pptx_path: Path,
    pdf_path: Path,
    *,
    timeout_seconds: int = 120,
) -> Path:
    """LibreOffice/soffice headless로 PPTX 파일을 실제 PDF로 변환한다."""
    converter = (
        os.getenv("RIST_PPTX_TO_PDF_CONVERTER")
        or shutil.which("soffice")
        or shutil.which("libreoffice")
    )
    if not converter:
        raise PptxPdfConversionError(
            "PPTX PDF 변환 도구를 찾을 수 없습니다. soffice 또는 libreoffice를 설치하세요."
        )
    if not pptx_path.is_file():
        raise PptxPdfConversionError(f"PPTX 파일을 찾을 수 없습니다: {pptx_path}")

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rist-pptx-pdf-") as tmp_name:
        tmp_dir = Path(tmp_name)
        profile_dir = tmp_dir / "lo-profile"
        output_dir = tmp_dir / "output"
        profile_dir.mkdir()
        output_dir.mkdir()
        command = [
            converter,
            "--headless",
            f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(output_dir),
            str(pptx_path),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                check=False,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise PptxPdfConversionError(
                f"PPTX PDF 변환 시간이 초과되었습니다: {pptx_path.name}"
            ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise PptxPdfConversionError(
                f"PPTX PDF 변환 실패(returnCode={completed.returncode}): {detail}"
            )

        converted = output_dir / f"{pptx_path.stem}.pdf"
        if not converted.is_file():
            candidates = sorted(output_dir.glob("*.pdf"))
            converted = candidates[0] if candidates else converted
        if not converted.is_file():
            detail = (completed.stdout or completed.stderr or "").strip()
            raise PptxPdfConversionError(
                f"PPTX PDF 변환 결과 파일이 생성되지 않았습니다: {detail}"
            )
        shutil.copyfile(converted, pdf_path)
    return pdf_path


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
        for candidate in _default_pdf_font_candidates():
            if not candidate.is_file():
                continue
            try:
                return _register_pdf_ttf(candidate)
            except Exception:
                continue
        name = "HYSMyeongJo-Medium"
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
        return name
    if not font_path.is_file():
        raise ValueError(f"PDF 임베드 폰트를 찾을 수 없습니다: {font_path}")
    return _register_pdf_ttf(font_path)


def _default_pdf_font_candidates() -> list[Path]:
    return [
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]


def _register_pdf_ttf(font_path: Path) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9]+", "", font_path.stem) or "Font"
    name = f"RISTEmbeddedKorean-{safe_name[:40]}"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(name, str(font_path)))
    return name


def _pdf_text(value: object) -> str:
    return html.escape(str(value or "")).replace("\n", "<br/>")


def _pdf_color(hex_value: str):
    return colors.HexColor(f"#{hex_value.lstrip('#')}")


def _pdf_style(
    name: str,
    font_name: str,
    *,
    size: float,
    leading: float,
    color: str = PPTX_TEXT,
    space_before: float = 0,
    space_after: float = 0,
    left_indent: float = 0,
    first_line_indent: float = 0,
    alignment: int = 0,
) -> ParagraphStyle:
    return ParagraphStyle(
        name,
        fontName=font_name,
        fontSize=size,
        leading=leading,
        textColor=_pdf_color(color),
        spaceBefore=space_before,
        spaceAfter=space_after,
        leftIndent=left_indent,
        firstLineIndent=first_line_indent,
        alignment=alignment,
        wordWrap="CJK",
    )


def _pdf_styles(font_name: str) -> dict[str, ParagraphStyle]:
    return {
        "title": _pdf_style(
            "RISTTitle",
            font_name,
            size=20,
            leading=25,
            color=PPTX_NAVY,
            space_after=4 * mm,
        ),
        "subtitle": _pdf_style(
            "RISTSubtitle",
            font_name,
            size=9,
            leading=12,
            color=PPTX_MUTED,
            space_after=3 * mm,
        ),
        "section": _pdf_style(
            "RISTSection",
            font_name,
            size=14,
            leading=18,
            color=PPTX_NAVY,
            space_before=5 * mm,
            space_after=2 * mm,
        ),
        "paragraph": _pdf_style(
            "RISTParagraph",
            font_name,
            size=10.5,
            leading=15,
            color=PPTX_TEXT,
            space_after=2 * mm,
        ),
        "bullet": _pdf_style(
            "RISTBullet",
            font_name,
            size=10,
            leading=14,
            color=PPTX_TEXT,
            space_after=1.5 * mm,
            left_indent=5 * mm,
            first_line_indent=-3 * mm,
        ),
        "meta_label": _pdf_style(
            "RISTMetaLabel",
            font_name,
            size=8.5,
            leading=11,
            color=PPTX_MUTED,
        ),
        "meta_value": _pdf_style(
            "RISTMetaValue",
            font_name,
            size=9,
            leading=12,
            color=PPTX_TEXT,
        ),
        "table_head": _pdf_style(
            "RISTTableHead",
            font_name,
            size=8,
            leading=10,
            color=PPTX_NAVY,
        ),
        "table_cell": _pdf_style(
            "RISTTableCell",
            font_name,
            size=8,
            leading=10,
            color=PPTX_TEXT,
        ),
        "caption": _pdf_style(
            "RISTCaption",
            font_name,
            size=8.5,
            leading=11,
            color=PPTX_MUTED,
            space_before=1.5 * mm,
            space_after=3 * mm,
            alignment=1,
        ),
        "notice": _pdf_style(
            "RISTNotice",
            font_name,
            size=9,
            leading=12,
            color=PPTX_ORANGE,
            space_after=3 * mm,
        ),
    }


def _pdf_meta_table(
    document: ReportDocument,
    styles: dict[str, ParagraphStyle],
    available_width: float,
) -> Table:
    metadata = [
        ("요청번호", document.pk.get("requestNumber", "")),
        ("실험", document.experiment_code),
        ("장비", document.pk.get("equipmentCode", "")),
        ("작업자", document.pk.get("operatorId", "")),
        ("생성시각", document.generated_at),
    ]
    rows = [
        [
            Paragraph(_pdf_text(label), styles["meta_label"]),
            Paragraph(_pdf_text(value or "-"), styles["meta_value"]),
        ]
        for label, value in metadata
    ]
    table = Table(rows, colWidths=[24 * mm, available_width - 24 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _pdf_color(PPTX_CARD)),
                ("BOX", (0, 0), (-1, -1), 0.5, _pdf_color(PPTX_LINE)),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, _pdf_color(PPTX_LINE)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def _pdf_report_table(
    section: ReportSection,
    styles: dict[str, ParagraphStyle],
    available_width: float,
) -> Table | None:
    if section.table is None or not section.table.columns:
        return None

    columns = section.table.columns
    column_count = len(columns)
    col_widths = [available_width / column_count] * column_count
    rows = [
        [Paragraph(_pdf_text(column), styles["table_head"]) for column in columns]
    ]
    for row in section.table.rows:
        rows.extend(
            _pdf_split_table_row(
                row,
                column_count=column_count,
                col_widths=col_widths,
                style=styles["table_cell"],
            )
        )

    table = Table(
        rows,
        colWidths=col_widths,
        hAlign="LEFT",
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _pdf_color("EAF2FD")),
                ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                ("BOX", (0, 0), (-1, -1), 0.5, _pdf_color(PPTX_LINE)),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, _pdf_color(PPTX_LINE)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _pdf_split_table_row(
    row: list[str],
    *,
    column_count: int,
    col_widths: list[float],
    style: ParagraphStyle,
) -> list[list[Paragraph]]:
    normalized = list(row[:column_count]) + [""] * max(0, column_count - len(row))
    split_cells = [
        _pdf_split_table_cell(cell, style, col_widths[idx])
        for idx, cell in enumerate(normalized)
    ]
    row_count = max(len(parts) for parts in split_cells)
    rendered_rows: list[list[Paragraph]] = []
    for row_idx in range(row_count):
        rendered_row = []
        for parts in split_cells:
            value = parts[row_idx] if row_idx < len(parts) else ""
            rendered_row.append(Paragraph(_pdf_text(value), style))
        rendered_rows.append(rendered_row)
    return rendered_rows


def _pdf_split_table_cell(
    value: str,
    style: ParagraphStyle,
    col_width: float,
) -> list[str]:
    text = str(value or "")
    max_height = 115 * mm
    if _pdf_paragraph_height(text, style, col_width) <= max_height:
        return [text]

    parts: list[str] = []
    remaining = text
    while remaining:
        if _pdf_paragraph_height(remaining, style, col_width) <= max_height:
            parts.append(remaining)
            break
        cut_at = _pdf_find_table_cell_cut(remaining, style, col_width, max_height)
        parts.append(remaining[:cut_at].rstrip())
        remaining = remaining[cut_at:].lstrip()
    return parts or [""]


def _pdf_paragraph_height(
    text: str,
    style: ParagraphStyle,
    width: float,
) -> float:
    _, height = Paragraph(_pdf_text(text), style).wrap(width, 10000)
    return height


def _pdf_find_table_cell_cut(
    text: str,
    style: ParagraphStyle,
    width: float,
    max_height: float,
) -> int:
    low = 1
    high = len(text)
    best = 1
    while low <= high:
        mid = (low + high) // 2
        if _pdf_paragraph_height(text[:mid], style, width) <= max_height:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return _pdf_preferred_table_cell_cut(text, best)


def _pdf_preferred_table_cell_cut(text: str, best: int) -> int:
    if best >= len(text):
        return best
    floor = max(1, int(best * 0.65))
    candidates = [
        text.rfind(separator, floor, best + 1)
        for separator in ("\n", ". ", "; ", ", ", " ")
    ]
    cut_at = max(candidates)
    if cut_at >= floor:
        return cut_at + 1
    return best


def _pdf_figure_story(
    document: ReportDocument,
    styles: dict[str, ParagraphStyle],
    available_width: float,
) -> list[object]:
    story: list[object] = []
    max_height = 95 * mm
    for figure in document.figures:
        figure_path = Path(figure.path)
        if not figure_path.is_file():
            continue
        try:
            reader = ImageReader(str(figure_path))
            image_width, image_height = reader.getSize()
        except Exception:
            continue
        if image_width <= 0 or image_height <= 0:
            continue
        scale = min(available_width / image_width, max_height / image_height)
        story.append(Paragraph(_pdf_text(figure.title), styles["section"]))
        story.append(
            Image(
                str(figure_path),
                width=image_width * scale,
                height=image_height * scale,
                hAlign="CENTER",
            )
        )
        caption_section = document.section(figure.caption_slot)
        caption = ""
        if caption_section is not None and caption_section.paragraphs:
            caption = caption_section.paragraphs[0]
        if caption:
            story.append(Paragraph(_pdf_text(caption), styles["caption"]))
    return story


def _pdf_footer(document: ReportDocument, font_name: str):
    def draw_footer(pdf_canvas, doc) -> None:
        pdf_canvas.saveState()
        pdf_canvas.setFont(font_name, 8)
        pdf_canvas.setFillColor(_pdf_color(PPTX_MUTED))
        pdf_canvas.drawString(doc.leftMargin, 12 * mm, "RIST Edge Report")
        right_text = f"{document.experiment_code} · {pdf_canvas.getPageNumber()}"
        pdf_canvas.drawRightString(A4[0] - doc.rightMargin, 12 * mm, right_text)
        pdf_canvas.restoreState()

    return draw_footer


def render_pdf(
    document: ReportDocument,
    path: Path,
    *,
    font_path: Path | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    font_name = _pdf_font_name(font_path)
    pdf_doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=document.title,
    )
    styles = _pdf_styles(font_name)
    available_width = A4[0] - pdf_doc.leftMargin - pdf_doc.rightMargin
    story: list[object] = [
        Paragraph(_pdf_text(document.title), styles["title"]),
        Paragraph(
            _pdf_text(f"{document.experiment_code} · {document.generated_at}"),
            styles["subtitle"],
        ),
        _pdf_meta_table(document, styles, available_width),
        Spacer(1, 4 * mm),
    ]

    story.extend(_pdf_figure_story(document, styles, available_width))

    for section in document.sections:
        story.append(Paragraph(_pdf_text(section.heading), styles["section"]))
        for paragraph in section.paragraphs:
            story.append(Paragraph(_pdf_text(paragraph), styles["paragraph"]))
        for bullet in section.bullets:
            story.append(
                Paragraph(_pdf_text(bullet), styles["bullet"], bulletText="-")
            )
        table = _pdf_report_table(section, styles, available_width)
        if table is not None:
            story.append(table)
            story.append(Spacer(1, 2 * mm))

    if document.llm_error:
        story.append(Paragraph(_pdf_text(LLM_FALLBACK_NOTICE), styles["notice"]))

    footer = _pdf_footer(document, font_name)
    pdf_doc.build(story, onFirstPage=footer, onLaterPages=footer)


def render_pptx(document: ReportDocument, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slides = _pptx_slide_payloads(document)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _pptx_content_types(len(slides)))
        archive.writestr("_rels/.rels", _pptx_root_rels())
        archive.writestr("docProps/core.xml", _pptx_core_properties(document))
        archive.writestr("docProps/app.xml", _pptx_app_properties(len(slides)))
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
    slides: list[dict[str, object]] = [_pptx_title_payload(document)]
    if document.section("summary") is not None or document.section("key_findings") is not None:
        slides.append(_pptx_overview_payload(document))

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
        slides.append(_pptx_image_payload(document, figure, media_name, figure_path, caption))

    if any(document.section(section_id) is not None for section_id in _INSIGHT_SECTION_IDS):
        slides.append(_pptx_insights_payload(document))

    summarized_sections = {"summary", "key_findings", "caption", *_INSIGHT_SECTION_IDS}
    for section in document.sections:
        if section.section_id in summarized_sections:
            continue
        if section.table is not None:
            slides.append(_pptx_table_payload(document, section))
        else:
            slides.append(_pptx_text_payload(document, section.heading, _section_lines(section)[:10]))
    if document.llm_error:
        slides.append(_pptx_text_payload(document, "LLM 보조 설명", [LLM_FALLBACK_NOTICE]))
    return slides


def _pptx_title_payload(document: ReportDocument) -> dict[str, object]:
    return {"xml": _pptx_title_slide(document), "rels": _pptx_slide_rels(), "media": []}


def _pptx_overview_payload(document: ReportDocument) -> dict[str, object]:
    return {"xml": _pptx_overview_slide(document), "rels": _pptx_slide_rels(), "media": []}


def _pptx_insights_payload(document: ReportDocument) -> dict[str, object]:
    return {"xml": _pptx_insights_slide(document), "rels": _pptx_slide_rels(), "media": []}


def _pptx_text_payload(
    document: ReportDocument,
    title: str,
    lines: list[str],
) -> dict[str, object]:
    return {"xml": _pptx_text_slide(document, title, lines), "rels": _pptx_slide_rels(), "media": []}


def _pptx_table_payload(document: ReportDocument, section: ReportSection) -> dict[str, object]:
    return {"xml": _pptx_table_slide(document, section), "rels": _pptx_slide_rels(), "media": []}


def _pptx_image_payload(
    document: ReportDocument,
    figure: ReportFigure,
    media_name: str,
    media_path: Path,
    caption: str,
) -> dict[str, object]:
    lines = [caption] if caption else []
    return {
        "xml": _pptx_image_slide(document, figure.title, media_name, lines),
        "rels": _pptx_slide_rels(media_name),
        "media": [(media_name, media_path)],
    }


def _pptx_body_pr(
    *,
    anchor: str = "t",
    inset: int = 91440,
) -> str:
    anchor_value = "ctr" if anchor == "mid" else anchor
    return (
        f'<a:bodyPr wrap="square" anchor="{anchor_value}" rtlCol="0" '
        f'lIns="{inset}" tIns="{inset}" rIns="{inset}" bIns="{inset}">'
        '<a:normAutofit fontScale="85000" lnSpcReduction="20000"/>'
        "</a:bodyPr>"
    )


def _pptx_rpr(
    *,
    font_size: int,
    color: str = PPTX_TEXT,
    bold: bool = False,
) -> str:
    bold_attr = ' b="1"' if bold else ""
    return (
        f'<a:rPr lang="ko-KR" sz="{font_size}"{bold_attr}>'
        f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
        '<a:latin typeface="Arial"/><a:ea typeface="맑은 고딕"/>'
        "</a:rPr>"
    )


def _pptx_paragraph(
    text: str,
    *,
    font_size: int = 1800,
    color: str = PPTX_TEXT,
    bold: bool = False,
    bullet: bool = False,
) -> str:
    bullet_pr = (
        '<a:pPr marL="228600" indent="-171450"><a:buChar char="•"/></a:pPr>'
        if bullet
        else '<a:pPr marL="0" indent="0"/>'
    )
    return (
        f"<a:p>{bullet_pr}<a:r>{_pptx_rpr(font_size=font_size, color=color, bold=bold)}"
        f"<a:t>{html.escape(text)}</a:t></a:r></a:p>"
    )


def _pptx_paragraphs(
    lines: list[str],
    *,
    font_size: int = 1800,
    color: str = PPTX_TEXT,
    bold: bool = False,
    bullet: bool = False,
) -> str:
    if not lines:
        return "<a:p/>"
    return "".join(
        _pptx_paragraph(
            line,
            font_size=font_size,
            color=color,
            bold=bold,
            bullet=bullet,
        )
        for line in lines
    )


def _pptx_fill(color: str | None) -> str:
    if not color:
        return "<a:noFill/>"
    return f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'


def _pptx_line(color: str | None = PPTX_LINE, *, width: int = 9525) -> str:
    if not color:
        return '<a:ln><a:noFill/></a:ln>'
    return (
        f'<a:ln w="{width}"><a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
        '<a:prstDash val="solid"/></a:ln>'
    )


def _pptx_shape(
    shape_id: int,
    name: str,
    *,
    x: int,
    y: int,
    cx: int,
    cy: int,
    fill: str | None = None,
    line: str | None = None,
    text: list[str] | None = None,
    font_size: int = 1600,
    color: str = PPTX_TEXT,
    bold: bool = False,
    bullet: bool = False,
    anchor: str = "t",
    inset: int = 91440,
) -> str:
    paragraphs = _pptx_paragraphs(
        text or [],
        font_size=font_size,
        color=color,
        bold=bold,
        bullet=bullet,
    )
    return f"""
    <p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="{html.escape(name)}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
      <p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom>{_pptx_fill(fill)}{_pptx_line(line)}</p:spPr>
      <p:txBody>{_pptx_body_pr(anchor=anchor, inset=inset)}<a:lstStyle/>{paragraphs}</p:txBody>
    </p:sp>"""


def _pptx_text_shape(
    shape_id: int,
    name: str,
    *,
    x: int,
    y: int,
    cx: int,
    cy: int,
    lines: list[str],
    font_size: int = 1600,
    color: str = PPTX_TEXT,
    bold: bool = False,
    bullet: bool = False,
    anchor: str = "t",
    inset: int = 45720,
) -> str:
    return _pptx_shape(
        shape_id,
        name,
        x=x,
        y=y,
        cx=cx,
        cy=cy,
        fill=None,
        line=None,
        text=lines,
        font_size=font_size,
        color=color,
        bold=bold,
        bullet=bullet,
        anchor=anchor,
        inset=inset,
    )


def _pptx_frame(shapes: list[str], *, background: str = PPTX_BG) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld><p:bg><p:bgPr>{_pptx_fill(background)}</p:bgPr></p:bg><p:spTree>
    <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
    {''.join(shapes)}
  </p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>"""


def _pptx_header_shapes(document: ReportDocument, title: str) -> list[str]:
    return [
        _pptx_shape(
            2,
            "Top accent",
            x=0,
            y=0,
            cx=PPTX_SLIDE_W,
            cy=91440,
            fill=PPTX_BLUE,
            line=None,
        ),
        _pptx_text_shape(
            3,
            "Slide title",
            x=PPTX_MARGIN_X,
            y=PPTX_TITLE_Y,
            cx=8846820,
            cy=PPTX_TITLE_H,
            lines=[title],
            font_size=3400,
            color=PPTX_NAVY,
            bold=True,
            inset=0,
        ),
        _pptx_text_shape(
            4,
            "Experiment label",
            x=9850000,
            y=411480,
            cx=1737360,
            cy=365760,
            lines=[document.experiment_code],
            font_size=1600,
            color=PPTX_MUTED,
            bold=True,
            anchor="mid",
            inset=0,
        ),
    ]


def _pptx_footer_shape(document: ReportDocument, shape_id: int) -> str:
    footer = " · ".join(
        item
        for item in [
            "RIST Edge Report",
            document.pk.get("requestNumber", ""),
            document.generated_at,
        ]
        if item
    )
    return _pptx_text_shape(
        shape_id,
        "Footer",
        x=PPTX_MARGIN_X,
        y=PPTX_FOOTER_Y,
        cx=10972800,
        cy=274320,
        lines=[footer],
        font_size=1100,
        color=PPTX_MUTED,
        inset=0,
    )


def _pptx_title_slide(document: ReportDocument) -> str:
    meta = [
        ("요청번호", document.pk.get("requestNumber", "")),
        ("실험", document.experiment_code),
        ("장비", document.pk.get("equipmentCode", "")),
        ("작업자", document.pk.get("operatorId", "")),
        ("생성시각", document.generated_at),
    ]
    shapes = [
        _pptx_shape(2, "Left accent", x=0, y=0, cx=365760, cy=PPTX_SLIDE_H, fill=PPTX_BLUE, line=None),
        _pptx_text_shape(
            3,
            "Report eyebrow",
            x=762000,
            y=777240,
            cx=7620000,
            cy=365760,
            lines=["분석 결과 보고서"],
            font_size=1700,
            color=PPTX_BLUE,
            bold=True,
            inset=0,
        ),
        _pptx_text_shape(
            4,
            "Report title",
            x=762000,
            y=1219200,
            cx=9753600,
            cy=1219200,
            lines=[document.title],
            font_size=5000,
            color=PPTX_NAVY,
            bold=True,
            inset=0,
        ),
        _pptx_text_shape(
            5,
            "Report subtitle",
            x=762000,
            y=2438400,
            cx=9601200,
            cy=609600,
            lines=["피크 분석 · 라이브러리 후보 · 보고서용 그래프 화면을 통합 정리"],
            font_size=2000,
            color=PPTX_MUTED,
            inset=0,
        ),
    ]
    x = 762000
    y = 3962400
    card_w = 2057400
    card_h = 731520
    gap = 182880
    for idx, (label, value) in enumerate(meta):
        row = idx // 4
        col = idx % 4
        card_x = x + col * (card_w + gap)
        card_y = y + row * (card_h + gap)
        shapes.append(
            _pptx_shape(
                10 + idx,
                f"Meta card {idx}",
                x=card_x,
                y=card_y,
                cx=card_w,
                cy=card_h,
                fill=PPTX_CARD,
                line=PPTX_LINE,
                text=[label, str(value or "-")],
                font_size=1450,
                color=PPTX_TEXT,
                bold=False,
                inset=137160,
            )
        )
    return _pptx_frame(shapes, background=PPTX_BG)


def _sentence_lines(text: str, *, max_items: int = 5) -> list[str]:
    chunks = []
    for raw_line in text.splitlines():
        chunks.extend(
            item.strip()
            for item in re.split(r"(?<=[.!?。])\s+", raw_line.strip())
            if item.strip()
        )
    return chunks[:max_items]


def _section_text_lines(section: ReportSection | None, *, max_items: int = 5) -> list[str]:
    if section is None:
        return []
    lines: list[str] = []
    for paragraph in section.paragraphs:
        lines.extend(_sentence_lines(paragraph, max_items=max_items))
    lines.extend(section.bullets)
    return [line for line in lines if line][:max_items]


def _pptx_overview_slide(document: ReportDocument) -> str:
    summary_lines = _section_text_lines(document.section("summary"), max_items=4)
    finding_lines = _section_text_lines(document.section("key_findings"), max_items=5)
    verdict_lines = _section_text_lines(document.section("verdict"), max_items=4)
    if not summary_lines:
        summary_lines = ["요약 문안이 제공되지 않았습니다."]
    if not finding_lines:
        finding_lines = verdict_lines or ["핵심 관찰사항이 제공되지 않았습니다."]
    shapes = _pptx_header_shapes(document, "분석 요약")
    shapes.extend(
        [
            _pptx_shape(
                10,
                "Summary card",
                x=PPTX_MARGIN_X,
                y=PPTX_CONTENT_Y,
                cx=5181600,
                cy=3261360,
                fill=PPTX_CARD,
                line=PPTX_LINE,
                text=["고객 보고서용 요약"],
                font_size=1700,
                color=PPTX_NAVY,
                bold=True,
                inset=182880,
            ),
            _pptx_text_shape(
                11,
                "Summary text",
                x=792480,
                y=1645920,
                cx=4815840,
                cy=2499360,
                lines=summary_lines,
                font_size=1750,
                color=PPTX_TEXT,
                inset=0,
            ),
            _pptx_shape(
                12,
                "Findings card",
                x=6096000,
                y=PPTX_CONTENT_Y,
                cx=5486400,
                cy=3261360,
                fill=PPTX_CARD,
                line=PPTX_LINE,
                text=["핵심 관찰사항"],
                font_size=1700,
                color=PPTX_NAVY,
                bold=True,
                inset=182880,
            ),
            _pptx_text_shape(
                13,
                "Findings text",
                x=6278880,
                y=1645920,
                cx=5120640,
                cy=2499360,
                lines=finding_lines,
                font_size=1650,
                color=PPTX_TEXT,
                bullet=True,
                inset=0,
            ),
        ]
    )
    cards = [
        ("요청번호", document.pk.get("requestNumber", "-"), PPTX_BLUE),
        ("장비", document.pk.get("equipmentCode", "-"), PPTX_GREEN),
        ("작업자", document.pk.get("operatorId", "-"), PPTX_ORANGE),
        ("LLM", "사용" if document.llm_used else "규칙 기반", PPTX_RED if document.llm_error else PPTX_GREEN),
    ]
    for idx, (label, value, color) in enumerate(cards):
        shapes.append(
            _pptx_shape(
                20 + idx,
                f"Info card {idx}",
                x=PPTX_MARGIN_X + idx * 2743200,
                y=4724400,
                cx=2499360,
                cy=1005840,
                fill=PPTX_CARD,
                line=PPTX_LINE,
                text=[label, str(value or "-")],
                font_size=1450,
                color=PPTX_TEXT,
                inset=137160,
            )
        )
        shapes.append(
            _pptx_shape(
                30 + idx,
                f"Info accent {idx}",
                x=PPTX_MARGIN_X + idx * 2743200,
                y=4724400,
                cx=76200,
                cy=1005840,
                fill=color,
                line=None,
            )
        )
    shapes.append(_pptx_footer_shape(document, 40))
    return _pptx_frame(shapes)


def _pptx_insights_slide(document: ReportDocument) -> str:
    cards = [
        ("interpretation", "피크 해석", PPTX_BLUE),
        ("qc_notes", "품질 확인 및 검토사항", PPTX_ORANGE),
        ("narrative", "보조 설명", PPTX_GREEN),
    ]
    shapes = _pptx_header_shapes(document, "해석 및 검토사항")
    card_x = PPTX_MARGIN_X
    card_w = 10972800
    card_h = 1371600
    card_gap = 274320
    shape_id = 10
    for idx, (section_id, heading, accent) in enumerate(cards):
        section = document.section(section_id)
        if section is None:
            continue
        y = PPTX_CONTENT_Y + idx * (card_h + card_gap)
        lines = _section_text_lines(section, max_items=3)
        if not lines:
            lines = ["해당 항목의 보고서용 문안이 없습니다."]
        shapes.extend(
            [
                _pptx_shape(
                    shape_id,
                    f"{section_id} card",
                    x=card_x,
                    y=y,
                    cx=card_w,
                    cy=card_h,
                    fill=PPTX_CARD,
                    line=PPTX_LINE,
                ),
                _pptx_shape(
                    shape_id + 1,
                    f"{section_id} accent",
                    x=card_x,
                    y=y,
                    cx=91440,
                    cy=card_h,
                    fill=accent,
                    line=None,
                ),
                _pptx_text_shape(
                    shape_id + 2,
                    f"{section_id} heading",
                    x=card_x + 274320,
                    y=y + 182880,
                    cx=2743200,
                    cy=365760,
                    lines=[heading],
                    font_size=1650,
                    color=PPTX_NAVY,
                    bold=True,
                    inset=0,
                ),
                _pptx_text_shape(
                    shape_id + 3,
                    f"{section_id} text",
                    x=card_x + 274320,
                    y=y + 594360,
                    cx=10363200,
                    cy=640080,
                    lines=lines,
                    font_size=1500,
                    color=PPTX_TEXT,
                    inset=0,
                ),
            ]
        )
        shape_id += 4
    shapes.append(_pptx_footer_shape(document, shape_id + 1))
    return _pptx_frame(shapes)


def _pptx_text_slide(document: ReportDocument, title: str, lines: list[str]) -> str:
    shapes = _pptx_header_shapes(document, title)
    display_lines = lines or ["보고서용 상세 문안이 없습니다."]
    has_bullets = any(line.startswith("- ") for line in display_lines)
    display_lines = [
        line[2:].strip() if line.startswith("- ") else line for line in display_lines
    ]
    line_count = max(1, len(display_lines))
    card_h = min(4937760, max(1600200, 914400 + line_count * 411480))
    text_h = max(822960, card_h - 640080)
    shapes.extend(
        [
            _pptx_shape(
                10,
                "Content card",
                x=PPTX_MARGIN_X,
                y=PPTX_CONTENT_Y,
                cx=10972800,
                cy=card_h,
                fill=PPTX_CARD,
                line=PPTX_LINE,
            ),
            _pptx_text_shape(
                11,
                "Content text",
                x=914400,
                y=1463040,
                cx=10363200,
                cy=text_h,
                lines=display_lines,
                font_size=1750,
                color=PPTX_TEXT,
                bullet=has_bullets,
                inset=0,
            ),
        ]
    )
    shapes.append(_pptx_footer_shape(document, 20))
    return _pptx_frame(shapes)


def _pptx_table_slide(document: ReportDocument, section: ReportSection) -> str:
    table = section.table
    if table is None:
        return _pptx_text_slide(document, section.heading, _section_lines(section)[:10])
    max_rows = 9
    rows = table.rows[:max_rows]
    omitted = max(0, len(table.rows) - len(rows))
    shapes = _pptx_header_shapes(document, section.heading)
    x = PPTX_MARGIN_X
    y = PPTX_CONTENT_Y
    w = 10972800
    h = 4663440
    row_count = max(1, len(rows) + 1)
    row_h = min(548640, max(396240, h // min(row_count, max_rows + 1)))
    col_count = max(1, len(table.columns))
    col_w = w // col_count
    shape_id = 10
    for col_idx, column in enumerate(table.columns):
        shapes.append(
            _pptx_shape(
                shape_id,
                f"Header {col_idx}",
                x=x + col_idx * col_w,
                y=y,
                cx=col_w,
                cy=row_h,
                fill=PPTX_NAVY,
                line="FFFFFF",
                text=[str(column)],
                font_size=1450,
                color="FFFFFF",
                bold=True,
                anchor="mid",
                inset=91440,
            )
        )
        shape_id += 1
    for row_idx, row in enumerate(rows):
        fill = "FFFFFF" if row_idx % 2 == 0 else "F8FAFC"
        for col_idx in range(col_count):
            value = row[col_idx] if col_idx < len(row) else ""
            shapes.append(
                _pptx_shape(
                    shape_id,
                    f"Cell {row_idx}-{col_idx}",
                    x=x + col_idx * col_w,
                    y=y + (row_idx + 1) * row_h,
                    cx=col_w,
                    cy=row_h,
                    fill=fill,
                    line=PPTX_LINE,
                    text=[str(value)],
                    font_size=1300,
                    color=PPTX_TEXT,
                    inset=68580,
                )
            )
            shape_id += 1
    if omitted:
        shapes.append(
            _pptx_text_shape(
                shape_id,
                "Table note",
                x=PPTX_MARGIN_X,
                y=y + (len(rows) + 1) * row_h + 137160,
                cx=10972800,
                cy=274320,
                lines=[f"표시는 상위 {len(rows)}개 행으로 제한했습니다. 전체 데이터는 report.html 또는 raw_data.xlsx를 확인하십시오. (생략 {omitted}행)"],
                font_size=1200,
                color=PPTX_MUTED,
                inset=0,
            )
        )
        shape_id += 1
    shapes.append(_pptx_footer_shape(document, shape_id + 1))
    return _pptx_frame(shapes)


def _pptx_image_slide(
    document: ReportDocument,
    title: str,
    media_name: str,
    lines: list[str],
) -> str:
    shapes = _pptx_header_shapes(document, title)
    shapes.append(
        _pptx_shape(
            10,
            "Image frame",
            x=PPTX_MARGIN_X,
            y=1036320,
            cx=10972800,
            cy=4572000,
            fill=PPTX_CARD,
            line=PPTX_LINE,
        )
    )
    image = f"""
    <p:pic><p:nvPicPr><p:cNvPr id="11" name="{html.escape(media_name)}"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr>
      <p:blipFill><a:blip r:embed="rId2"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>
      <p:spPr><a:xfrm><a:off x="701040" y="1127760"/><a:ext cx="10789920" cy="4389120"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom>{_pptx_line(PPTX_LINE)}</p:spPr>
    </p:pic>"""
    shapes.append(image)
    if lines:
        shapes.append(
            _pptx_shape(
                12,
                "Caption card",
                x=PPTX_MARGIN_X,
                y=5745480,
                cx=10972800,
                cy=548640,
                fill=PPTX_CARD,
                line=PPTX_LINE,
                text=lines[:2],
                font_size=1500,
                color=PPTX_TEXT,
                inset=137160,
            )
        )
    shapes.append(_pptx_footer_shape(document, 20))
    return _pptx_frame(shapes)


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
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
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
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""


def _pptx_core_properties(document: ReportDocument) -> str:
    title = html.escape(document.title)
    created = html.escape(document.generated_at or "2026-06-30T00:00:00Z")
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>{title}</dc:title>
  <dc:creator>RIST Edge</dc:creator>
  <cp:lastModifiedBy>RIST Edge</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{created}</dcterms:modified>
</cp:coreProperties>"""


def _pptx_app_properties(slide_count: int) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>RIST Edge</Application>
  <PresentationFormat>On-screen Show (16:9)</PresentationFormat>
  <Slides>{slide_count}</Slides>
  <Company>RIST</Company>
  <AppVersion>16.0000</AppVersion>
</Properties>"""


def _pptx_presentation(slide_count: int) -> str:
    slide_ids = "".join(f'<p:sldId id="{255 + i}" r:id="rId{i}"/>' for i in range(1, slide_count + 1))
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId{slide_count + 1}"/></p:sldMasterIdLst>
  <p:sldIdLst>{slide_ids}</p:sldIdLst>
  <p:sldSz cx="{PPTX_SLIDE_W}" cy="{PPTX_SLIDE_H}" type="screen16x9"/>
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
<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld>
  <p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>
  <p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>
  <p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles>
</p:sldMaster>"""


def _pptx_master_rels() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>
</Relationships>"""


def _pptx_empty_layout() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="blank" preserve="1">
  <p:cSld name="Blank"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld>
  <p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sldLayout>"""


def _pptx_layout_rels() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>
</Relationships>"""


def _pptx_theme() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="RIST">
  <a:themeElements>
    <a:clrScheme name="RIST">
      <a:dk1><a:srgbClr val="1F2933"/></a:dk1>
      <a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>
      <a:dk2><a:srgbClr val="334E68"/></a:dk2>
      <a:lt2><a:srgbClr val="F0F4F8"/></a:lt2>
      <a:accent1><a:srgbClr val="2F80ED"/></a:accent1>
      <a:accent2><a:srgbClr val="27AE60"/></a:accent2>
      <a:accent3><a:srgbClr val="F2994A"/></a:accent3>
      <a:accent4><a:srgbClr val="9B51E0"/></a:accent4>
      <a:accent5><a:srgbClr val="EB5757"/></a:accent5>
      <a:accent6><a:srgbClr val="56CCF2"/></a:accent6>
      <a:hlink><a:srgbClr val="2F80ED"/></a:hlink>
      <a:folHlink><a:srgbClr val="9B51E0"/></a:folHlink>
    </a:clrScheme>
    <a:fontScheme name="RIST">
      <a:majorFont><a:latin typeface="Arial"/><a:ea typeface="맑은 고딕"/><a:cs typeface=""/></a:majorFont>
      <a:minorFont><a:latin typeface="Arial"/><a:ea typeface="맑은 고딕"/><a:cs typeface=""/></a:minorFont>
    </a:fontScheme>
    <a:fmtScheme name="RIST">
      <a:fillStyleLst>
        <a:solidFill><a:schemeClr val="phClr"/></a:solidFill>
        <a:gradFill rotWithShape="1"><a:gsLst><a:gs pos="0"><a:schemeClr val="phClr"/></a:gs><a:gs pos="100000"><a:schemeClr val="phClr"><a:tint val="50000"/></a:schemeClr></a:gs></a:gsLst><a:lin ang="5400000" scaled="0"/></a:gradFill>
        <a:solidFill><a:schemeClr val="phClr"><a:tint val="75000"/></a:schemeClr></a:solidFill>
      </a:fillStyleLst>
      <a:lnStyleLst>
        <a:ln w="9525" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln>
        <a:ln w="25400" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln>
        <a:ln w="38100" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln>
      </a:lnStyleLst>
      <a:effectStyleLst>
        <a:effectStyle><a:effectLst/></a:effectStyle>
        <a:effectStyle><a:effectLst/></a:effectStyle>
        <a:effectStyle><a:effectLst/></a:effectStyle>
      </a:effectStyleLst>
      <a:bgFillStyleLst>
        <a:solidFill><a:schemeClr val="phClr"/></a:solidFill>
        <a:solidFill><a:schemeClr val="phClr"><a:tint val="95000"/></a:schemeClr></a:solidFill>
        <a:solidFill><a:schemeClr val="phClr"><a:tint val="85000"/></a:schemeClr></a:solidFill>
      </a:bgFillStyleLst>
    </a:fmtScheme>
  </a:themeElements>
  <a:objectDefaults/>
  <a:extraClrSchemeLst/>
</a:theme>"""

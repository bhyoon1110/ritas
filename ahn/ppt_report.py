"""PowerPoint report builder for AHN TEM/STEM/EDS/coating-layer analysis."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageOps

from .docx_extract import extract_docx


def _require_pptx():
    try:
        from pptx import Presentation
        from pptx.dml.color import RGBColor
        from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
        from pptx.util import Inches, Pt
    except Exception as exc:  # pragma: no cover - depends on runtime packaging.
        raise RuntimeError(
            "AHN PPT 보고서를 생성하려면 python-pptx 패키지가 필요합니다."
        ) from exc
    return Presentation, RGBColor, PP_ALIGN, MSO_ANCHOR, Inches, Pt


Presentation, RGBColor, PP_ALIGN, MSO_ANCHOR, Inches, Pt = _require_pptx()

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)
NAVY = RGBColor(31, 55, 87)
BLUE = RGBColor(47, 125, 211)
LIGHT_GRAY = RGBColor(245, 247, 250)
GRID_LINE = RGBColor(210, 220, 232)
TEXT = RGBColor(30, 47, 70)


def _chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for index in range(0, len(items), size):
        yield items[index:index + size]


def _path(input_root: Path, relative: str) -> Path:
    return input_root / relative


def _new_slide(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


def _set_run(run, *, size: int, bold: bool = False, color=TEXT) -> None:
    run.font.name = "Malgun Gothic"
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color


def _add_text(
    slide,
    text: str,
    left,
    top,
    width,
    height,
    *,
    size: int = 14,
    bold: bool = False,
    color=TEXT,
    align=None,
):
    box = slide.shapes.add_textbox(left, top, width, height)
    frame = box.text_frame
    frame.clear()
    frame.margin_left = Inches(0.04)
    frame.margin_right = Inches(0.04)
    frame.margin_top = Inches(0.02)
    frame.margin_bottom = Inches(0.02)
    para = frame.paragraphs[0]
    if align is not None:
        para.alignment = align
    run = para.add_run()
    run.text = text
    _set_run(run, size=size, bold=bold, color=color)
    return box


def _add_header(slide, section_title: str) -> None:
    _add_text(
        slide,
        "TEM 분석 결과",
        Inches(10.35),
        Inches(0.18),
        Inches(2.5),
        Inches(0.35),
        size=15,
        bold=True,
        color=NAVY,
    )
    _add_text(
        slide,
        f"■ {section_title}",
        Inches(0.35),
        Inches(0.42),
        Inches(9.6),
        Inches(0.48),
        size=20,
        bold=True,
        color=NAVY,
    )
    line = slide.shapes.add_shape(1, Inches(0.35), Inches(1.02), Inches(12.55), Inches(0.02))
    line.fill.solid()
    line.fill.fore_color.rgb = BLUE
    line.line.color.rgb = BLUE


def _convert_image(path: Path, tmp_dir: Path) -> Path:
    cache_dir = tmp_dir / "converted-images"
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:16]
    target = cache_dir / f"{digest}.jpg"
    if target.exists():
        return target
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode == "RGBA":
            background = Image.new("RGB", image.size, (255, 255, 255))
            background.paste(image, mask=image.getchannel("A"))
            image = background
        elif image.mode != "RGB":
            image = image.convert("RGB")
        image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        image.save(target, format="JPEG", quality=88, optimize=True)
    return target


def _image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def _add_fit_picture(slide, path: Path, left, top, width, height, tmp_dir: Path):
    converted = _convert_image(path, tmp_dir)
    image_w, image_h = _image_size(converted)
    if image_w <= 0 or image_h <= 0:
        return None
    scale = min(width / image_w, height / image_h)
    pic_w = int(image_w * scale)
    pic_h = int(image_h * scale)
    pic_left = int(left + (width - pic_w) / 2)
    pic_top = int(top + (height - pic_h) / 2)
    return slide.shapes.add_picture(str(converted), pic_left, pic_top, width=pic_w, height=pic_h)


def _add_image_grid(
    slide,
    image_items: list[dict[str, Any]],
    input_root: Path,
    tmp_dir: Path,
    *,
    left,
    top,
    width,
    height,
    cols: int,
    rows: int,
) -> None:
    gap = Inches(0.1)
    label_h = Inches(0.22)
    cell_w = (width - gap * (cols - 1)) / cols
    cell_h = (height - gap * (rows - 1)) / rows
    for index, item in enumerate(image_items[: cols * rows]):
        col = index % cols
        row = index // cols
        cell_left = left + col * (cell_w + gap)
        cell_top = top + row * (cell_h + gap)
        image_h = cell_h - label_h
        _add_fit_picture(slide, _path(input_root, item["path"]), cell_left, cell_top, cell_w, image_h, tmp_dir)
        label = item.get("magnification") or Path(item["file_name"]).stem
        if label and not label.startswith("["):
            label = f"[{label}]"
        _add_text(
            slide,
            label,
            cell_left,
            cell_top + image_h,
            cell_w,
            label_h,
            size=9,
            color=TEXT,
            align=PP_ALIGN.CENTER,
        )


def _add_image_grid_slides(
    prs,
    title: str,
    image_items: list[dict[str, Any]],
    input_root: Path,
    tmp_dir: Path,
    *,
    per_slide: int = 8,
) -> None:
    if not image_items:
        slide = _new_slide(prs)
        _add_header(slide, title)
        _add_text(slide, "표시할 이미지가 없습니다.", Inches(0.6), Inches(1.5), Inches(5), Inches(0.4))
        return
    for page_index, chunk in enumerate(_chunks(image_items, per_slide), start=1):
        suffix = f" ({page_index})" if len(image_items) > per_slide else ""
        slide = _new_slide(prs)
        _add_header(slide, title + suffix)
        _add_image_grid(
            slide,
            chunk,
            input_root,
            tmp_dir,
            left=Inches(0.65),
            top=Inches(1.25),
            width=Inches(12.0),
            height=Inches(5.95),
            cols=4,
            rows=2,
        )


def _add_table(slide, rows: list[list[str]], left, top, width, height, *, font_size: int = 9):
    if not rows:
        return None
    cols = max(len(row) for row in rows)
    table_shape = slide.shapes.add_table(len(rows), cols, left, top, width, height)
    table = table_shape.table
    for row_index, row in enumerate(rows):
        for col_index in range(cols):
            cell = table.cell(row_index, col_index)
            value = row[col_index] if col_index < len(row) else ""
            cell.text = str(value)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            para = cell.text_frame.paragraphs[0]
            para.alignment = PP_ALIGN.CENTER
            run = para.runs[0] if para.runs else para.add_run()
            _set_run(run, size=font_size, bold=row_index == 0, color=TEXT)
            if row_index == 0:
                cell.fill.solid()
                cell.fill.fore_color.rgb = NAVY
                run.font.color.rgb = RGBColor(255, 255, 255)
            else:
                cell.fill.solid()
                cell.fill.fore_color.rgb = RGBColor(255, 255, 255)
    return table_shape


def _build_tem(prs, data: dict[str, Any], input_root: Path, tmp_dir: Path) -> None:
    for sample in data.get("tem_samples") or []:
        _add_image_grid_slides(
            prs,
            f"TEM 이미지 분석결과 : [{sample['sample_name']}]",
            sample.get("images") or [],
            input_root,
            tmp_dir,
        )


def _build_stem(prs, data: dict[str, Any], input_root: Path, tmp_dir: Path) -> None:
    for sample in data.get("stem_samples") or []:
        if sample.get("images"):
            _add_image_grid_slides(
                prs,
                f"STEM 이미지 분석결과 : [{sample['sample_name']}]",
                sample.get("images") or [],
                input_root,
                tmp_dir,
            )
        if sample.get("bf_images"):
            _add_image_grid_slides(
                prs,
                f"STEM BF 이미지 분석결과 : [{sample['sample_name']}]",
                sample.get("bf_images") or [],
                input_root,
                tmp_dir,
            )


def _add_eds_first_slide(prs, title: str, images: list[Path], tmp_dir: Path) -> None:
    slide = _new_slide(prs)
    _add_header(slide, f"STEM EDS 분석결과 : [{title}]")
    if not images:
        _add_text(slide, "Word 보고서에서 추출된 이미지가 없습니다.", Inches(0.6), Inches(1.5), Inches(7), Inches(0.4))
        return
    _add_fit_picture(slide, images[0], Inches(0.55), Inches(1.35), Inches(5.6), Inches(5.55), tmp_dir)
    right_images = [{"path": str(path), "file_name": path.name, "magnification": ""} for path in images[1:7]]
    _add_absolute_image_grid(slide, right_images, tmp_dir, Inches(6.35), Inches(1.35), Inches(6.3), Inches(5.55), 3, 2)


def _add_absolute_image_grid(slide, image_items: list[dict[str, Any]], tmp_dir: Path, left, top, width, height, cols: int, rows: int) -> None:
    gap = Inches(0.08)
    cell_w = (width - gap * (cols - 1)) / cols
    cell_h = (height - gap * (rows - 1)) / rows
    for index, item in enumerate(image_items[: cols * rows]):
        col = index % cols
        row = index // cols
        _add_fit_picture(
            slide,
            Path(item["path"]),
            left + col * (cell_w + gap),
            top + row * (cell_h + gap),
            cell_w,
            cell_h,
            tmp_dir,
        )


def _add_eds_image_pages(prs, title: str, images: list[Path], tmp_dir: Path, *, start_page: int = 1) -> None:
    for page, chunk in enumerate(_chunks(images, 6), start=start_page):
        slide = _new_slide(prs)
        _add_header(slide, f"STEM EDS 분석결과 : [{title}_Data{page}]")
        image_items = [{"path": str(path), "file_name": path.name, "magnification": ""} for path in chunk]
        _add_absolute_image_grid(slide, image_items, tmp_dir, Inches(0.8), Inches(1.35), Inches(11.7), Inches(5.65), 3, 2)


def _add_eds_tables_slide(prs, title: str, tables: list[list[list[str]]]) -> None:
    slide = _new_slide(prs)
    _add_header(slide, f"STEM EDS 분석결과 : [{title}]")
    if not tables:
        _add_text(slide, "Word 보고서에서 추출된 표가 없습니다.", Inches(0.6), Inches(1.5), Inches(7), Inches(0.4))
        return
    top = Inches(1.3)
    for table in tables[:2]:
        normalized = [row[:10] for row in table[:12]]
        _add_table(slide, normalized, Inches(0.65), top, Inches(12.0), Inches(2.45), font_size=7)
        top += Inches(2.7)


def _build_eds(prs, data: dict[str, Any], input_root: Path, tmp_dir: Path) -> None:
    for report in data.get("eds_reports") or []:
        docx_path = _path(input_root, report["path"])
        extract_dir = tmp_dir / "docx" / docx_path.stem
        extracted = extract_docx(docx_path, extract_dir)
        images = extracted.media_paths
        analysis_type = str(report.get("analysis_type") or "").upper()
        title = report.get("title") or docx_path.stem
        if analysis_type == "LINE":
            _add_eds_first_slide(prs, title, images[:3], tmp_dir)
            _add_eds_image_pages(prs, title, images[3:], tmp_dir)
        elif analysis_type == "POINT":
            _add_eds_first_slide(prs, title, images[:1], tmp_dir)
            _add_eds_tables_slide(prs, title, extracted.tables)
            _add_eds_image_pages(prs, title, images[1:], tmp_dir)
        else:
            _add_eds_first_slide(prs, title, images[:7], tmp_dir)
            _add_eds_image_pages(prs, title, images[7:], tmp_dir)


def _coating_grid_shape(count: int) -> tuple[int, int, int]:
    if count <= 9:
        return 3, 3, 9
    if count <= 12:
        return 4, 3, 12
    return 4, 4, 16


def _format_nm(value: Any) -> str:
    if value is None:
        return "검토 필요"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "검토 필요"


def _coating_rows(measurements: list[dict[str, Any]]) -> list[list[str]]:
    rows = [["측정개소", "두께(nm)", "비고"]]
    values: list[float] = []
    for item in measurements:
        item_values = item.get("thickness_values_nm") or []
        if not item_values and item.get("thickness_nm") is not None:
            item_values = [item.get("thickness_nm")]
        note = str(item.get("note") or "")
        warnings = item.get("ocr_warnings") or []
        if warnings:
            note = f"{note} / {', '.join(str(value) for value in warnings)}".strip(" /")
        if not item_values:
            rows.append([str(item.get("index") or ""), "검토 필요", note])
            continue
        for value_index, value in enumerate(item_values, start=1):
            try:
                numeric_value = float(value)
            except (TypeError, ValueError):
                rows.append([f"{item.get('index') or ''}-{value_index}", "검토 필요", note])
                continue
            values.append(numeric_value)
            row_note = note if value_index == 1 else ""
            rows.append([f"{item.get('index') or ''}-{value_index}", _format_nm(numeric_value), row_note])
    average = sum(values) / len(values) if values else None
    rows.append(["전체 평균", _format_nm(average), f"{len(values)}개 라벨"])
    return rows


def _build_coating(prs, data: dict[str, Any], input_root: Path, tmp_dir: Path) -> None:
    for sample in data.get("coating_samples") or []:
        measurements = sample.get("measurements") or []
        cols, rows, per_slide = _coating_grid_shape(len(measurements))
        for page_index, chunk in enumerate(_chunks(measurements, per_slide), start=1):
            suffix = f" ({page_index})" if len(measurements) > per_slide else ""
            slide = _new_slide(prs)
            _add_header(slide, f"TEM 코팅층 두께 분석 결과 : [{sample['sample_name']}]{suffix}")
            image_items = [
                {
                    "path": item["path"],
                    "file_name": item["file_name"],
                    "magnification": item.get("magnification") or "",
                }
                for item in chunk
            ]
            _add_image_grid(
                slide,
                image_items,
                input_root,
                tmp_dir,
                left=Inches(0.55),
                top=Inches(1.35),
                width=Inches(8.15),
                height=Inches(5.75),
                cols=cols,
                rows=rows,
            )
            table_rows = _coating_rows(chunk)
            table_height = min(Inches(5.75), Inches(0.32 * len(table_rows)))
            _add_table(
                slide,
                table_rows,
                Inches(9.0),
                Inches(1.35),
                Inches(3.55),
                table_height,
                font_size=7 if len(table_rows) > 18 else 8 if len(table_rows) > 14 else 9,
            )


def build_pptx(project: Any, output_path: str | Path) -> Path:
    """Build an AHN PowerPoint report from collected project data."""
    data = project.to_dict() if hasattr(project, "to_dict") else dict(project)
    input_root = Path(data["input_root"])
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    with tempfile.TemporaryDirectory(prefix="ahn-pptx-") as tmp:
        tmp_dir = Path(tmp)
        _build_tem(prs, data, input_root, tmp_dir)
        _build_stem(prs, data, input_root, tmp_dir)
        _build_eds(prs, data, input_root, tmp_dir)
        _build_coating(prs, data, input_root, tmp_dir)
        if not prs.slides:
            slide = _new_slide(prs)
            _add_header(slide, "AHN 분석결과")
            _add_text(slide, "입력 폴더에서 보고서 생성 대상 데이터를 찾지 못했습니다.", Inches(0.7), Inches(1.6), Inches(8), Inches(0.5))
        prs.save(output)
    return output

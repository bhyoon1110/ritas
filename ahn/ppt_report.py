"""PowerPoint report builder for AHN TEM/STEM/EDS/coating-layer analysis."""

from __future__ import annotations

import hashlib
import tempfile
from copy import deepcopy
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
TEMPLATE_PATH = Path(__file__).resolve().parent / "resources" / "templates" / "ahn_tem_template.pptx"
EMU_PER_INCH = 914400
PICTURE_SHAPE_TYPE = 13
TABLE_SHAPE_TYPE = 19
TEMPLATE_SLIDES = {
    "tem": 0,
    "stem": 5,
    "stem_bf": 6,
    "eds_map": 7,
    "eds_line_first": 9,
    "eds_line_page": 10,
    "eds_table": 16,
    "coating": 20,
}


def _chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for index in range(0, len(items), size):
        yield items[index:index + size]


def _path(input_root: Path, relative: str) -> Path:
    return input_root / relative


def _new_slide(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


def _remove_shape(shape) -> None:
    shape.element.getparent().remove(shape.element)


def _clear_slides(prs) -> None:
    slide_id_list = prs.slides._sldIdLst
    for slide_id in list(slide_id_list):
        prs.part.drop_rel(slide_id.rId)
        slide_id_list.remove(slide_id)


def _shape_text(shape) -> str:
    if not getattr(shape, "has_text_frame", False):
        return ""
    return " ".join(shape.text.split())


def _shape_bounds(shape) -> tuple[int, int, int, int]:
    return int(shape.left), int(shape.top), int(shape.width), int(shape.height)


def _sort_slots(slots: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    return sorted(slots, key=lambda slot: (round(slot[1] / EMU_PER_INCH, 1), slot[0]))


def _fit_slot_to_canvas(slot: tuple[int, int, int, int], slide_width: int, slide_height: int) -> tuple[int, int, int, int]:
    left, top, width, height = slot
    margin = Inches(0.08)
    right = min(left + width, slide_width - margin)
    bottom = min(top + height, slide_height - margin)
    left = max(left, margin)
    top = max(top, margin)
    return left, top, max(1, right - left), max(1, bottom - top)


class AhnTemplate:
    def __init__(self, path: Path):
        self.path = path
        self.source = Presentation(path)
        self.output = Presentation(path)
        _strip_layout_footer(self.output)
        _clear_slides(self.output)

    def source_slide(self, key: str):
        return self.source.slides[TEMPLATE_SLIDES[key]]

    def picture_slots(self, key: str) -> list[tuple[int, int, int, int]]:
        return _sort_slots(
            [
                _fit_slot_to_canvas(_shape_bounds(shape), self.output.slide_width, self.output.slide_height)
                for shape in self.source_slide(key).shapes
                if shape.shape_type == PICTURE_SHAPE_TYPE
            ]
        )

    def label_slots(self, key: str) -> list[tuple[int, int, int, int]]:
        return _sort_slots(
            [
                _shape_bounds(shape)
                for shape in self.source_slide(key).shapes
                if getattr(shape, "has_text_frame", False)
                and _shape_text(shape).startswith("[")
                and _shape_text(shape).endswith("]")
            ]
        )

    def table_slots(self, key: str) -> list[tuple[int, int, int, int]]:
        return _sort_slots(
            [
                _shape_bounds(shape)
                for shape in self.source_slide(key).shapes
                if shape.shape_type == TABLE_SHAPE_TYPE
            ]
        )

    def new_slide(self, key: str, title: str):
        slide = self.output.slides.add_slide(self.output.slide_layouts[0])
        for shape in list(slide.shapes):
            _remove_shape(shape)

        for shape in self.source_slide(key).shapes:
            slide.shapes._spTree.insert_element_before(deepcopy(shape.element), "p:extLst")

        _strip_template_payload(slide, key)
        _replace_template_title(slide, title)
        return slide


def _strip_layout_footer(prs) -> None:
    for layout in prs.slide_layouts:
        for shape in list(layout.shapes):
            text = _shape_text(shape)
            if "‹#›" in text or "<#>" in text or ("/ 22" in text and shape.top > Inches(6.5)):
                _remove_shape(shape)


def _strip_template_payload(slide, key: str) -> None:
    for shape in list(slide.shapes):
        text = _shape_text(shape)
        if shape.shape_type == PICTURE_SHAPE_TYPE:
            _remove_shape(shape)
            continue
        if getattr(shape, "has_text_frame", False) and text.startswith("[") and text.endswith("]"):
            _remove_shape(shape)
            continue
        if shape.shape_type == TABLE_SHAPE_TYPE:
            if key == "coating" and shape.left < Inches(7.8):
                continue
            _remove_shape(shape)


def _replace_template_title(slide, title: str) -> None:
    title_text = f"■ {title}"
    for shape in slide.shapes:
        if not getattr(shape, "has_text_frame", False):
            continue
        if _shape_text(shape).startswith("■"):
            frame = shape.text_frame
            frame.clear()
            para = frame.paragraphs[0]
            run = para.add_run()
            run.text = title_text
            _set_run(run, size=9, bold=True, color=TEXT)
            return
    _add_text(slide, title_text, Inches(0.09), Inches(0.76), Inches(10.66), Inches(0.45), size=9, bold=True)


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


def _add_label(slide, label: str, slot: tuple[int, int, int, int]) -> None:
    _add_text(
        slide,
        label,
        slot[0],
        slot[1],
        slot[2],
        slot[3],
        size=7,
        color=TEXT,
        align=PP_ALIGN.CENTER,
    )


def _add_template_image_items(
    slide,
    image_items: list[dict[str, Any]],
    image_slots: list[tuple[int, int, int, int]],
    label_slots: list[tuple[int, int, int, int]],
    input_root: Path,
    tmp_dir: Path,
) -> None:
    for index, item in enumerate(image_items[: len(image_slots)]):
        slot = image_slots[index]
        _add_fit_picture(slide, _path(input_root, item["path"]), *slot, tmp_dir)
        if index < len(label_slots):
            label = item.get("magnification") or Path(item["file_name"]).stem
            if label and not label.startswith("["):
                label = f"[{label}]"
            _add_label(slide, label, label_slots[index])


def _add_template_paths(
    slide,
    paths: list[Path],
    image_slots: list[tuple[int, int, int, int]],
    tmp_dir: Path,
) -> None:
    for index, path in enumerate(paths[: len(image_slots)]):
        _add_fit_picture(slide, path, *image_slots[index], tmp_dir)


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
    template: AhnTemplate | None,
    template_key: str,
    title: str,
    image_items: list[dict[str, Any]],
    input_root: Path,
    tmp_dir: Path,
    *,
    per_slide: int = 8,
) -> None:
    if not image_items:
        return
    for page_index, chunk in enumerate(_chunks(image_items, per_slide), start=1):
        suffix = f" ({page_index})" if len(image_items) > per_slide else ""
        if template:
            slide = template.new_slide(template_key, title + suffix)
            _add_template_image_items(
                slide,
                chunk,
                template.picture_slots(template_key),
                template.label_slots(template_key),
                input_root,
                tmp_dir,
            )
        else:
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


def _build_tem(prs, template: AhnTemplate | None, data: dict[str, Any], input_root: Path, tmp_dir: Path) -> None:
    for sample in data.get("tem_samples") or []:
        _add_image_grid_slides(
            prs,
            template,
            "tem",
            f"TEM 이미지 분석결과 : [{sample['sample_name']}]",
            sample.get("images") or [],
            input_root,
            tmp_dir,
        )


def _build_stem(prs, template: AhnTemplate | None, data: dict[str, Any], input_root: Path, tmp_dir: Path) -> None:
    for sample in data.get("stem_samples") or []:
        if sample.get("images"):
            _add_image_grid_slides(
                prs,
                template,
                "stem",
                f"STEM 이미지 분석결과 : [{sample['sample_name']}]",
                sample.get("images") or [],
                input_root,
                tmp_dir,
            )
        if sample.get("bf_images"):
            _add_image_grid_slides(
                prs,
                template,
                "stem_bf",
                f"STEM BF 이미지 분석결과 : [{sample['sample_name']}]",
                sample.get("bf_images") or [],
                input_root,
                tmp_dir,
            )


def _add_eds_first_slide(prs, template: AhnTemplate | None, title: str, images: list[Path], tmp_dir: Path) -> None:
    if not images:
        return
    if template:
        slide = template.new_slide("eds_map", f"STEM EDS 분석결과 : [{title}]")
    else:
        slide = _new_slide(prs)
        _add_header(slide, f"STEM EDS 분석결과 : [{title}]")
    if template:
        _add_template_paths(slide, images[:7], template.picture_slots("eds_map"), tmp_dir)
    else:
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


def _add_eds_image_pages(
    prs,
    template: AhnTemplate | None,
    title: str,
    images: list[Path],
    tmp_dir: Path,
    *,
    start_page: int = 1,
    template_key: str = "eds_line_page",
) -> None:
    for page, chunk in enumerate(_chunks(images, 6), start=start_page):
        if template:
            slide = template.new_slide(template_key, f"STEM EDS 분석결과 : [{title}_Data{page}]")
            _add_template_paths(slide, chunk, template.picture_slots(template_key), tmp_dir)
        else:
            slide = _new_slide(prs)
            _add_header(slide, f"STEM EDS 분석결과 : [{title}_Data{page}]")
            image_items = [{"path": str(path), "file_name": path.name, "magnification": ""} for path in chunk]
            _add_absolute_image_grid(slide, image_items, tmp_dir, Inches(0.8), Inches(1.35), Inches(11.7), Inches(5.65), 3, 2)


def _add_eds_tables_slide(prs, template: AhnTemplate | None, title: str, images: list[Path], tables: list[list[list[str]]], tmp_dir: Path) -> None:
    if not images and not tables:
        return
    if template:
        slide = template.new_slide("eds_table", f"STEM EDS 분석결과 : [{title}]")
        _add_template_paths(slide, images[:1], template.picture_slots("eds_table")[:1], tmp_dir)
    else:
        slide = _new_slide(prs)
        _add_header(slide, f"STEM EDS 분석결과 : [{title}]")
    if not tables:
        return
    if template:
        slots = template.table_slots("eds_table")
        for table, slot in zip(tables[:2], slots[:2]):
            normalized = [row[:10] for row in table[:12]]
            _add_table(slide, normalized, *slot, font_size=6)
    else:
        top = Inches(1.3)
        for table in tables[:2]:
            normalized = [row[:10] for row in table[:12]]
            _add_table(slide, normalized, Inches(0.65), top, Inches(12.0), Inches(2.45), font_size=7)
            top += Inches(2.7)


def _build_eds(prs, template: AhnTemplate | None, data: dict[str, Any], input_root: Path, tmp_dir: Path) -> None:
    for report in data.get("eds_reports") or []:
        docx_path = _path(input_root, report["path"])
        extract_dir = tmp_dir / "docx" / docx_path.stem
        extracted = extract_docx(docx_path, extract_dir)
        images = extracted.media_paths
        analysis_type = str(report.get("analysis_type") or "").upper()
        title = report.get("title") or docx_path.stem
        if analysis_type == "LINE":
            if template:
                slide = template.new_slide("eds_line_first", f"STEM EDS 분석결과 : [{title}_Data1]")
                _add_template_paths(slide, images[:3], template.picture_slots("eds_line_first"), tmp_dir)
                _add_eds_image_pages(prs, template, title, images[3:], tmp_dir, start_page=2)
            else:
                _add_eds_first_slide(prs, template, title, images[:3], tmp_dir)
                _add_eds_image_pages(prs, template, title, images[3:], tmp_dir)
        elif analysis_type == "POINT":
            _add_eds_tables_slide(prs, template, title, images[:1], extracted.tables, tmp_dir)
            _add_eds_image_pages(prs, template, title, images[1:], tmp_dir, template_key="eds_line_page")
        else:
            _add_eds_first_slide(prs, template, title, images[:7], tmp_dir)
            _add_eds_image_pages(prs, template, title, images[7:], tmp_dir)


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


def _build_coating(prs, template: AhnTemplate | None, data: dict[str, Any], input_root: Path, tmp_dir: Path) -> None:
    for sample in data.get("coating_samples") or []:
        measurements = sample.get("measurements") or []
        if not measurements:
            continue
        if template:
            per_slide = max(1, len(template.picture_slots("coating")))
        else:
            cols, rows, per_slide = _coating_grid_shape(len(measurements))
        for page_index, chunk in enumerate(_chunks(measurements, per_slide), start=1):
            suffix = f" ({page_index})" if len(measurements) > per_slide else ""
            title = f"TEM 코팅층 두께 분석 결과 : [{sample['sample_name']}]{suffix}"
            if template:
                slide = template.new_slide("coating", title)
            else:
                slide = _new_slide(prs)
                _add_header(slide, title)
            image_items = [
                {
                    "path": item["path"],
                    "file_name": item["file_name"],
                    "magnification": item.get("magnification") or "",
                }
                for item in chunk
            ]
            if template:
                _add_template_image_items(
                    slide,
                    image_items,
                    template.picture_slots("coating"),
                    [],
                    input_root,
                    tmp_dir,
                )
                table_rows = _coating_rows(measurements)
                table_slots = template.table_slots("coating")
                table_slot = table_slots[1] if len(table_slots) > 1 else (Inches(8.01), Inches(1.21), Inches(2.36), Inches(5.77))
                _add_table(
                    slide,
                    table_rows,
                    *table_slot,
                    font_size=5 if len(table_rows) > 22 else 6 if len(table_rows) > 16 else 7,
                )
            else:
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

    template = AhnTemplate(TEMPLATE_PATH) if TEMPLATE_PATH.exists() else None
    if template:
        prs = template.output
    else:
        prs = Presentation()
        prs.slide_width = SLIDE_W
        prs.slide_height = SLIDE_H

    with tempfile.TemporaryDirectory(prefix="ahn-pptx-") as tmp:
        tmp_dir = Path(tmp)
        _build_tem(prs, template, data, input_root, tmp_dir)
        _build_stem(prs, template, data, input_root, tmp_dir)
        _build_eds(prs, template, data, input_root, tmp_dir)
        _build_coating(prs, template, data, input_root, tmp_dir)
        if not prs.slides:
            if template:
                slide = template.new_slide("tem", "AHN 분석결과")
            else:
                slide = _new_slide(prs)
                _add_header(slide, "AHN 분석결과")
            _add_text(slide, "입력 폴더에서 보고서 생성 대상 데이터를 찾지 못했습니다.", Inches(0.7), Inches(1.6), Inches(8), Inches(0.5))
        prs.save(output)
    return output

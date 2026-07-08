"""AHN TEM/STEM/EDS/coating-layer input scanner.

The AHN project receives a folder bundle with four logical folders:

* ``tem``: TEM images, grouped by sample-name subfolders.
* ``stem``: STEM and BF-STEM images, grouped by filename.
* ``report``/``reports``: STEM EDS Word reports and raw spreadsheets.
* ``scale``: coating-layer thickness TEM images.

This module keeps the scanner deterministic and dependency-light so it can run
as an Edge processor before the heavier PPT renderer is invoked.
"""

from __future__ import annotations

import json
import re
import shutil
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageOps

IMAGE_EXTENSIONS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp", ".webp"}
DOCX_EXTENSIONS = {".docx"}
SPREADSHEET_EXTENSIONS = {".xlsx", ".xls", ".csv", ".tsv"}
MAX_COATING_THICKNESS_NM = 40.0


@dataclass
class ImageRecord:
    path: str
    file_name: str
    sample_name: str
    magnification: str = ""
    sequence: int | None = None
    kind: str = ""


@dataclass
class TemSample:
    sample_name: str
    images: list[ImageRecord] = field(default_factory=list)


@dataclass
class StemSample:
    sample_name: str
    images: list[ImageRecord] = field(default_factory=list)
    bf_images: list[ImageRecord] = field(default_factory=list)


@dataclass
class EdsReport:
    path: str
    file_name: str
    title: str
    sample_name: str
    analysis_type: str


@dataclass
class SpreadsheetFile:
    path: str
    file_name: str


@dataclass
class CoatingMeasurement:
    index: int
    path: str
    file_name: str
    magnification: str = ""
    thickness_nm: float | None = None
    thickness_values_nm: list[float] = field(default_factory=list)
    ocr_text: str = ""
    note: str = ""
    ocr_review_required: bool = False
    ocr_warnings: list[str] = field(default_factory=list)


@dataclass
class CoatingOcrResult:
    values_nm: list[float] = field(default_factory=list)
    ocr_text: str = ""
    note: str = ""
    review_required: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass
class CoatingSample:
    sample_name: str
    measurements: list[CoatingMeasurement] = field(default_factory=list)


@dataclass
class AhnProjectData:
    experiment: str
    input_root: str
    generated_at: str
    folders: dict[str, str | None]
    tem_samples: list[TemSample]
    stem_samples: list[StemSample]
    eds_reports: list[EdsReport]
    spreadsheets: list[SpreadsheetFile]
    coating_samples: list[CoatingSample]
    summary: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def natural_key(value: str | Path) -> list[Any]:
    text = unicodedata.normalize("NFC", str(value))
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def find_named_dir(root: Path, name: str) -> Path | None:
    """Find a direct child folder case-insensitively."""
    expected = name.lower()
    for child in root.iterdir() if root.exists() else []:
        if child.is_dir() and child.name.lower() == expected:
            return child
    return None


def find_named_dirs(root: Path, *names: str) -> list[Path]:
    """Find direct child folders case-insensitively, preserving name priority."""
    expected = {name.lower(): index for index, name in enumerate(names)}
    matches = [
        child
        for child in root.iterdir() if root.exists()
        if child.is_dir() and child.name.lower() in expected
    ]
    return sorted(matches, key=lambda path: (expected[path.name.lower()], natural_key(path.name)))


def _visible_files(directory: Path, extensions: set[str]) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        [
            child
            for child in directory.iterdir()
            if child.is_file()
            and not child.name.startswith(".")
            and child.suffix.lower() in extensions
            and not child.name.startswith("~$")
        ],
        key=lambda path: natural_key(path.name),
    )


def _image_files_recursive(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        [
            child
            for child in directory.rglob("*")
            if child.is_file()
            and not child.name.startswith(".")
            and child.suffix.lower() in IMAGE_EXTENSIONS
        ],
        key=lambda path: natural_key(path.as_posix()),
    )


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def extract_magnification(file_name: str) -> str:
    match = re.search(r"(\d+(?:\.\d+)?)\s*([kKmM]?)\s*[xX](?=$|[^0-9A-Za-z])", file_name)
    if not match:
        return ""
    value = match.group(1)
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    suffix = match.group(2).lower()
    return f"x{value}{suffix}"


def _last_int(file_name: str) -> int | None:
    numbers = re.findall(r"(\d+)", Path(file_name).stem)
    if not numbers:
        return None
    try:
        return int(numbers[-1])
    except ValueError:
        return None


def _record(path: Path, root: Path, sample_name: str, *, kind: str = "") -> ImageRecord:
    return ImageRecord(
        path=_relative(path, root),
        file_name=path.name,
        sample_name=sample_name,
        magnification=extract_magnification(path.name),
        sequence=_last_int(path.name),
        kind=kind,
    )


def collect_tem_samples(root: Path) -> list[TemSample]:
    tem_dir = find_named_dir(root, "tem")
    if tem_dir is None:
        return []

    samples: list[TemSample] = []
    subdirs = sorted(
        [child for child in tem_dir.iterdir() if child.is_dir() and not child.name.startswith(".")],
        key=lambda path: natural_key(path.name),
    )
    if not subdirs:
        images = _visible_files(tem_dir, IMAGE_EXTENSIONS)
        if images:
            samples.append(
                TemSample(
                    sample_name=tem_dir.name,
                    images=[_record(path, root, tem_dir.name, kind="TEM") for path in images],
                )
            )
        return samples

    for sample_dir in subdirs:
        images = _visible_files(sample_dir, IMAGE_EXTENSIONS)
        if not images:
            continue
        samples.append(
            TemSample(
                sample_name=unicodedata.normalize("NFC", sample_dir.name),
                images=[
                    _record(path, root, unicodedata.normalize("NFC", sample_dir.name), kind="TEM")
                    for path in images
                ],
            )
        )
    return samples


def _parse_stem_file(path: Path, root: Path) -> ImageRecord | None:
    stem = unicodedata.normalize("NFC", path.stem)
    is_bf = stem.lower().startswith("bf_")
    core = stem[3:] if is_bf else stem
    parts = [part for part in core.split("_") if part]
    if not parts:
        return None
    sample_name = parts[0]
    return _record(path, root, sample_name, kind="BF-STEM" if is_bf else "STEM")


def collect_stem_samples(root: Path) -> list[StemSample]:
    stem_dir = find_named_dir(root, "stem")
    if stem_dir is None:
        return []

    grouped: dict[str, StemSample] = {}
    for path in _visible_files(stem_dir, IMAGE_EXTENSIONS):
        record = _parse_stem_file(path, root)
        if record is None:
            continue
        sample = grouped.setdefault(record.sample_name, StemSample(sample_name=record.sample_name))
        if record.kind == "BF-STEM":
            sample.bf_images.append(record)
        else:
            sample.images.append(record)

    for sample in grouped.values():
        sample.images.sort(key=lambda item: natural_key(item.file_name))
        sample.bf_images.sort(key=lambda item: natural_key(item.file_name))
    return [grouped[key] for key in sorted(grouped, key=natural_key)]


def _eds_title(path: Path) -> str:
    title = unicodedata.normalize("NFC", path.stem)
    return re.sub(r"^Project\s*1_\s*", "", title, flags=re.IGNORECASE).strip()


def _eds_type(title: str) -> str:
    lowered = title.lower()
    if "map" in lowered:
        return "MAP"
    if "line" in lowered:
        return "LINE"
    if "point" in lowered:
        return "POINT"
    return "UNKNOWN"


def _sample_from_eds_title(title: str) -> str:
    parts = title.split()
    return parts[0] if parts else title


def collect_eds_reports(root: Path) -> tuple[list[EdsReport], list[SpreadsheetFile]]:
    report_dirs = find_named_dirs(root, "report", "reports")
    if not report_dirs:
        return [], []

    reports = []
    spreadsheets = []
    for report_dir in report_dirs:
        for path in _visible_files(report_dir, DOCX_EXTENSIONS):
            title = _eds_title(path)
            reports.append(
                EdsReport(
                    path=_relative(path, root),
                    file_name=path.name,
                    title=title,
                    sample_name=_sample_from_eds_title(title),
                    analysis_type=_eds_type(title),
                )
            )
        spreadsheets.extend(
            SpreadsheetFile(path=_relative(path, root), file_name=path.name)
            for path in _visible_files(report_dir, SPREADSHEET_EXTENSIONS)
        )
    return reports, spreadsheets


def _unit_nearby(text: str, start: int, end: int) -> bool:
    context = text[max(0, start - 6): min(len(text), end + 12)].lower()
    return bool(re.search(r"(?:n\s*m|r\s*[iyu]?\s*[mn]|r\s*n|m\b)", context))


def _candidate_values_from_text(text: str, *, require_unit: bool = True) -> list[float]:
    """Extract plausible coating thickness values from noisy OCR text."""
    normalized = unicodedata.normalize("NFKC", text).replace(",", ".")
    normalized = re.sub(r"(?<=\d)\s+\.\s+(?=\d)", ".", normalized)
    values: list[float] = []

    def add_value(value: float) -> None:
        if abs(value - round(value)) < 0.001 and round(value) in {5, 10, 50}:
            return
        if 0.1 <= value <= MAX_COATING_THICKNESS_NM:
            values.append(value)

    for match in re.finditer(r"(?<!\d)(\d{1,2})\s*\.\s*(\d{1,3})(?!\d)", normalized):
        if require_unit and not _unit_nearby(normalized, match.start(), match.end()):
            continue
        integer = int(match.group(1))
        fraction = match.group(2)
        value = float(f"{integer}.{fraction}")
        if 50 <= integer <= 59:
            value = float(f"{integer - 50}.{fraction}")
        add_value(value)

    # Tesseract often reads the leading "11" as "LL" or "II" on TEM labels.
    for match in re.finditer(r"(?<![A-Za-z0-9])([lLiI]{1,2})\s*\.\s*(\d{1,3})", normalized):
        if require_unit and not _unit_nearby(normalized, match.start(), match.end()):
            continue
        integer = 11 if len(match.group(1)) == 2 else 1
        add_value(float(f"{integer}.{match.group(2)}"))

    # Some labels come back as "2. IS rm" for "2.18 nm".
    digit_like = str.maketrans({"I": "1", "i": "1", "l": "1", "L": "1", "S": "8", "s": "8", "O": "0", "o": "0"})
    for match in re.finditer(r"(?<!\d)(\d{1,2})\s*\.\s*([IiLlSsOo]{1,3})(?![A-Za-z0-9])", normalized):
        if require_unit and not _unit_nearby(normalized, match.start(), match.end()):
            continue
        fraction = match.group(2).translate(digit_like)
        if fraction.isdigit():
            add_value(float(f"{int(match.group(1))}.{fraction}"))

    deduped: list[float] = []
    for value in values:
        if not any(abs(value - existing) < 0.025 for existing in deduped):
            deduped.append(value)
    return deduped


def _run_tesseract(pytesseract: Any, image: Image.Image, config: str, timeout: int = 3) -> str:
    try:
        return pytesseract.image_to_string(image, config=config, timeout=timeout)
    except TypeError:  # Older pytesseract versions do not support timeout.
        return pytesseract.image_to_string(image, config=config)


def _ocr_label_box(image: Image.Image, box: tuple[int, int, int, int], pytesseract: Any) -> tuple[list[float], list[str]]:
    width, height = image.size
    x, y, box_w, box_h = box
    pad = 12
    crop = image.crop((
        max(0, x - pad),
        max(0, y - pad),
        min(width, x + box_w + pad),
        min(height, y + box_h + pad),
    ))
    crop = ImageOps.autocontrast(crop)
    scale = 5 if max(crop.size) < 450 else 3
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS)

    variants = [crop]
    try:
        import numpy as np  # type: ignore

        arr = np.array(crop)
        variants.append(Image.fromarray(np.where(arr < 200, 0, 255).astype("uint8")))
    except Exception:  # pragma: no cover - optional OCR enhancement.
        pass

    texts: list[str] = []
    values: list[float] = []
    config = "--psm 8 -c tessedit_char_whitelist=0123456789.nmriuy"
    for variant in variants:
        try:
            text = _run_tesseract(pytesseract, variant, config=config, timeout=2).strip()
        except RuntimeError:
            continue
        if not text:
            continue
        texts.append(text)
        values.extend(_candidate_values_from_text(text, require_unit=False))
    low_values = [value for value in values if value < 10]
    if low_values:
        values = low_values
    return values, texts


def _ocr_label_boxes(image: Image.Image, pytesseract: Any) -> tuple[list[float], str]:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        return [], ""

    gray = np.array(image)
    height, width = gray.shape
    _, thresholded = cv2.threshold(gray, 235, 255, cv2.THRESH_BINARY)
    closed = cv2.morphologyEx(
        thresholded,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (17, 7)),
    )
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(closed, 8)
    boxes: list[tuple[int, int, int, int]] = []
    for index in range(1, count):
        x, y, box_w, box_h, area = [int(value) for value in stats[index]]
        fill = area / max(1, box_w * box_h)
        if not (45 <= box_w <= 380 and 18 <= box_h <= 100 and area >= 700 and fill >= 0.25):
            continue
        if y <= height * 0.08 or x <= 5 or x + box_w >= width - 5:
            continue
        # The microscope scale bar labels live in the lower-left corner and
        # should not be counted as coating-thickness measurements.
        if y > height * 0.84 and x < width * 0.28:
            continue
        center_x = x + box_w / 2
        center_y = y + box_h / 2
        if any(abs(center_x - (bx + bw / 2)) < 18 and abs(center_y - (by + bh / 2)) < 18 for bx, by, bw, bh in boxes):
            continue
        boxes.append((x, y, box_w, box_h))

    values: list[float] = []
    text_parts: list[str] = []
    for box in sorted(boxes, key=lambda item: (item[1], item[0]))[:10]:
        box_values, texts = _ocr_label_box(image, box, pytesseract)
        values.extend(box_values)
        text_parts.extend(texts)
    return values, "\n".join(text_parts)


def _ocr_full_image(image: Image.Image, pytesseract: Any) -> tuple[list[float], str]:
    variants: list[tuple[str, Image.Image]] = [("full", ImageOps.autocontrast(image))]
    if max(image.size) > 1400:
        scale = 1400 / max(image.size)
        resized = image.resize((int(image.width * scale), int(image.height * scale)), Image.Resampling.LANCZOS)
        variants.append(("resized", ImageOps.autocontrast(resized)))
    variants.append(("sharp", ImageOps.autocontrast(image).filter(ImageFilter.SHARPEN)))

    text_parts: list[str] = []
    variant_values: list[list[float]] = []
    for label, variant in variants:
        try:
            text = _run_tesseract(pytesseract, variant, config="--psm 11", timeout=5).strip()
        except RuntimeError:
            continue
        if not text:
            continue
        text_parts.append(f"[{label}]\n{text}")
        values = _candidate_values_from_text(text, require_unit=True)
        if not values:
            values = _candidate_values_from_text(text, require_unit=False)
        if values:
            variant_values.append(values)
    return _select_supported_ocr_values(variant_values), "\n\n".join(text_parts)


def _select_supported_ocr_values(variant_values: list[list[float]]) -> list[float]:
    if not variant_values:
        return []
    primary = max(enumerate(variant_values), key=lambda item: (len(item[1]), -item[0]))[1]
    supported: list[float] = []
    for index, value in enumerate(primary):
        close_values = [value]
        for candidate_values in variant_values:
            if candidate_values is primary or index >= len(candidate_values):
                continue
            candidate = candidate_values[index]
            if abs(candidate - value) <= 0.25:
                close_values.append(candidate)
        if len(close_values) >= 2:
            close_values.sort()
            supported.append(round(close_values[len(close_values) // 2], 3))
        else:
            supported.append(value)
    return supported


def _dedupe_values(values: list[float]) -> list[float]:
    deduped: list[float] = []
    for value in values:
        if not any(abs(value - existing) < 0.025 for existing in deduped):
            deduped.append(value)
    return deduped


def _mean_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def _ocr_thickness_nm(path: Path) -> CoatingOcrResult:
    """Try OCR for a ``{number nm}`` coating thickness label.

    The runtime may not have the tesseract binary installed. In that case the
    measurement remains reviewable instead of failing report generation.
    """
    if shutil.which("tesseract") is None:
        return CoatingOcrResult(note="자동 판독 엔진 없음", review_required=True)
    try:
        import pytesseract  # type: ignore
    except Exception:
        return CoatingOcrResult(note="자동 판독 모듈 없음", review_required=True)

    try:
        image = Image.open(path)
        image = ImageOps.exif_transpose(image).convert("L")
        values, text = _ocr_label_boxes(image, pytesseract)
        if not values:
            values, text = _ocr_full_image(image, pytesseract)
    except Exception as exc:  # pragma: no cover - depends on OCR runtime.
        return CoatingOcrResult(note=f"자동 판독 실패: {exc}", review_required=True)

    values = _dedupe_values(values)
    if not values:
        return CoatingOcrResult(
            ocr_text=text.strip(),
            note="두께값 미검출",
            review_required=True,
            warnings=["후보값 없음"],
        )

    warnings: list[str] = []
    if len(values) > 8:
        warnings.append("후보값 과다")
    if max(values) / max(0.001, min(values)) > 12:
        warnings.append("후보값 편차 큼")

    note = f"라벨 {len(values)}개 추출" if len(values) > 1 else ""
    ocr_text = f"candidates_nm={', '.join(f'{value:.3g}' for value in values)}\n{text}".strip()
    return CoatingOcrResult(
        values_nm=values,
        ocr_text=ocr_text,
        note=note,
        review_required=bool(warnings),
        warnings=warnings,
    )


def collect_coating_samples(root: Path) -> list[CoatingSample]:
    scale_dir = find_named_dir(root, "scale")
    if scale_dir is None:
        return []

    sample_dirs = sorted(
        [child for child in scale_dir.iterdir() if child.is_dir() and not child.name.startswith(".")],
        key=lambda path: natural_key(path.name),
    )
    sources: list[tuple[str, list[Path]]]
    if sample_dirs:
        sources = [
            (unicodedata.normalize("NFC", sample_dir.name), _visible_files(sample_dir, IMAGE_EXTENSIONS))
            for sample_dir in sample_dirs
        ]
    else:
        sources = [(scale_dir.name, _visible_files(scale_dir, IMAGE_EXTENSIONS))]

    samples: list[CoatingSample] = []
    for sample_name, images in sources:
        measurements = []
        for index, path in enumerate(images, start=1):
            ocr = _ocr_thickness_nm(path)
            measurements.append(
                CoatingMeasurement(
                    index=index,
                    path=_relative(path, root),
                    file_name=path.name,
                    magnification=extract_magnification(path.name),
                    thickness_nm=_mean_or_none(ocr.values_nm),
                    thickness_values_nm=ocr.values_nm,
                    ocr_text=ocr.ocr_text,
                    note=ocr.note,
                    ocr_review_required=ocr.review_required,
                    ocr_warnings=ocr.warnings,
                )
            )
        if measurements:
            samples.append(CoatingSample(sample_name=sample_name, measurements=measurements))
    return samples


def collect_project(input_dir: str | Path) -> AhnProjectData:
    root = Path(input_dir).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"입력 폴더를 찾을 수 없습니다: {root}")

    tem_samples = collect_tem_samples(root)
    stem_samples = collect_stem_samples(root)
    eds_reports, spreadsheets = collect_eds_reports(root)
    coating_samples = collect_coating_samples(root)
    folders = {
        name: _relative(path, root) if path else None
        for name, path in {
            "tem": find_named_dir(root, "tem"),
            "stem": find_named_dir(root, "stem"),
            "report": next(iter(find_named_dirs(root, "report", "reports")), None),
            "scale": find_named_dir(root, "scale"),
        }.items()
    }
    summary = {
        "temSampleCount": len(tem_samples),
        "temImageCount": sum(len(sample.images) for sample in tem_samples),
        "stemSampleCount": len(stem_samples),
        "stemImageCount": sum(len(sample.images) for sample in stem_samples),
        "stemBfImageCount": sum(len(sample.bf_images) for sample in stem_samples),
        "edsReportCount": len(eds_reports),
        "spreadsheetCount": len(spreadsheets),
        "coatingSampleCount": len(coating_samples),
        "coatingImageCount": sum(len(sample.measurements) for sample in coating_samples),
    }
    return AhnProjectData(
        experiment="AHN-TEM",
        input_root=str(root),
        generated_at=datetime.now(timezone.utc).isoformat(),
        folders=folders,
        tem_samples=tem_samples,
        stem_samples=stem_samples,
        eds_reports=eds_reports,
        spreadsheets=spreadsheets,
        coating_samples=coating_samples,
        summary=summary,
    )


def write_project_json(project: AhnProjectData, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(project.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path

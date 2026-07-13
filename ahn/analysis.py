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

from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import shutil
import threading
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageOps

IMAGE_EXTENSIONS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp", ".webp"}
DOCX_EXTENSIONS = {".docx"}
SPREADSHEET_EXTENSIONS = {".xlsx", ".xls", ".xlsm", ".xlsb", ".csv", ".tsv"}
MAX_COATING_THICKNESS_NM = 500.0
MICROSCOPE_SCALE_VALUES_NM = {5, 10, 20, 50, 100, 200, 500}
DEFAULT_COATING_OCR_WORKERS = 2
MAX_COATING_OCR_WORKERS = 4
COATING_LABEL_DETECTION_MAX_DIMENSION = 1800
RAPID_OCR_MIN_CONFIDENCE = 0.72

_rapid_ocr_engine: Any | None = None
_rapid_ocr_lock = threading.Lock()


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


@dataclass(frozen=True)
class OcrCandidate:
    value_nm: float
    text: str
    confidence: float
    box: tuple[int, int, int, int] | None = None


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
        return [], collect_spreadsheets(root, [])

    reports = []
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
    spreadsheets = collect_spreadsheets(root, report_dirs)
    return reports, spreadsheets


def collect_spreadsheets(root: Path, preferred_dirs: list[Path] | None = None) -> list[SpreadsheetFile]:
    """Collect raw spreadsheet attachments from the AHN bundle.

    EDS raw spreadsheets are usually placed under ``report`` or ``reports``,
    but the browser bundle can also include them at the top level or in a raw
    subfolder. They are not interpreted as standalone report data; they are
    copied into the final ZIP package and used as a fallback for Point tables.
    """
    preferred = [path for path in (preferred_dirs or []) if path.exists()]
    seen: set[Path] = set()
    ordered_paths: list[Path] = []

    def add(paths: list[Path]) -> None:
        for path in paths:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            ordered_paths.append(path)

    for directory in preferred:
        add(_visible_files(directory, SPREADSHEET_EXTENSIONS))

    if root.exists():
        recursive = sorted(
            [
                child
                for child in root.rglob("*")
                if child.is_file()
                and not child.name.startswith(".")
                and not child.name.startswith("~$")
                and child.suffix.lower() in SPREADSHEET_EXTENSIONS
            ],
            key=lambda path: natural_key(path.as_posix()),
        )
        add(recursive)

    return [
        SpreadsheetFile(path=_relative(path, root), file_name=path.name)
        for path in ordered_paths
    ]


def _unit_nearby(text: str, start: int, end: int) -> bool:
    context = text[max(0, start - 6): min(len(text), end + 12)].lower()
    return bool(re.search(r"(?:n\s*m|r\s*[iyu]?\s*[mn]|r\s*n|m\b)", context))


def _candidate_values_from_text(
    text: str,
    *,
    require_unit: bool = True,
    exclude_microscope_scale: bool = True,
) -> list[float]:
    """Extract plausible coating thickness values from noisy OCR text."""
    normalized = unicodedata.normalize("NFKC", text).replace(",", ".")
    normalized = re.sub(r"(?<=\d)\s+\.\s+(?=\d)", ".", normalized)
    values: list[float] = []

    def add_value(value: float) -> None:
        if (
            exclude_microscope_scale
            and abs(value - round(value)) < 0.001
            and round(value) in MICROSCOPE_SCALE_VALUES_NM
        ):
            return
        if 0.1 <= value <= MAX_COATING_THICKNESS_NM:
            values.append(value)

    # A decimal point on a narrow italic label is occasionally read as a
    # hyphen (for example, ``15-74 rn``). Accept that form only when an OCR
    # approximation of the nm unit is adjacent, so ranges and file names do
    # not become thickness candidates.
    for match in re.finditer(r"(?<!\d)(\d{1,3})\s*([.\-])\s*(\d{1,3})(?!\d)", normalized):
        unit_nearby = _unit_nearby(normalized, match.start(), match.end())
        if (require_unit or match.group(2) == "-") and not unit_nearby:
            continue
        integer = int(match.group(1))
        fraction = match.group(3)
        value = float(f"{integer}.{fraction}")
        add_value(value)

    # Integer coating measurements are valid too. They are only accepted by
    # callers that can reject the microscope scale bar from its position.
    if not exclude_microscope_scale:
        for match in re.finditer(
            r"(?<![\d.])(\d{1,3})(?!\d)(?!\s*[.\-]\s*\d)",
            normalized,
        ):
            if require_unit and not _unit_nearby(normalized, match.start(), match.end()):
                continue
            add_value(float(match.group(1)))

    # A zero before the decimal point is frequently recognized as the letter
    # O on the white measurement labels (for example, ``O.72 rm``).
    for match in re.finditer(r"(?<![A-Za-z0-9])[Oo]\s*\.\s*(\d{1,3})(?!\d)", normalized):
        if require_unit and not _unit_nearby(normalized, match.start(), match.end()):
            continue
        add_value(float(f"0.{match.group(1)}"))

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
    config = "--psm 8 -c tessedit_char_whitelist=0123456789.Oonmriuy"
    for variant in variants:
        try:
            text = _run_tesseract(pytesseract, variant, config=config, timeout=2).strip()
        except RuntimeError:
            continue
        if not text:
            continue
        texts.append(text)
        parsed_values = _candidate_values_from_text(
            text,
            require_unit=False,
            exclude_microscope_scale=False,
        )
        # Each detected box represents one white measurement label. Keep the
        # first readable (grayscale) variant; threshold OCR is only a fallback
        # and can invent a leading digit from the measurement line.
        if parsed_values and not values:
            values = parsed_values

    return values, texts


def _ocr_label_boxes(image: Image.Image, pytesseract: Any) -> tuple[list[float], str]:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        return [], ""

    detection_image = image
    if max(image.size) > COATING_LABEL_DETECTION_MAX_DIMENSION:
        scale = COATING_LABEL_DETECTION_MAX_DIMENSION / max(image.size)
        detection_image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
        )

    gray = np.array(detection_image)
    height, width = gray.shape
    _, thresholded = cv2.threshold(gray, 235, 255, cv2.THRESH_BINARY)
    masks = (
        # Isolate the horizontal white label when it is connected to one or
        # more diagonal measurement lines. A wider opening removes those
        # lines while preserving labels such as ``14.72 nm``.
        (cv2.morphologyEx(
            thresholded,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (17, 5)),
        ), 450, 0.30),
        # Remove the diagonal measurement line while retaining the compact
        # white label. This is the reliable path for small labels such as
        # ``15.74 nm`` on a noisy TEM background.
        (cv2.morphologyEx(
            thresholded,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
        ), 450, 0.10),
        # Keep the previous joining pass as a fallback for fragmented labels.
        (cv2.morphologyEx(
            thresholded,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (17, 7)),
        ), 700, 0.25),
    )
    max_box_width = max(380, round(width * 0.48))
    max_box_height = max(100, round(height * 0.14))
    values: list[float] = []
    text_parts: list[str] = []
    successful_boxes: list[tuple[int, int, int, int]] = []

    def substantially_overlaps(box: tuple[int, int, int, int]) -> bool:
        x, y, box_w, box_h = box
        for other_x, other_y, other_w, other_h in successful_boxes:
            overlap_w = max(0, min(x + box_w, other_x + other_w) - max(x, other_x))
            overlap_h = max(0, min(y + box_h, other_y + other_h) - max(y, other_y))
            overlap = overlap_w * overlap_h
            if overlap / max(1, min(box_w * box_h, other_w * other_h)) >= 0.35:
                return True
        return False

    for mask, min_area, min_fill in masks:
        count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, 8)
        boxes: list[tuple[int, int, int, int]] = []
        for index in range(1, count):
            x, y, box_w, box_h, area = [int(value) for value in stats[index]]
            fill = area / max(1, box_w * box_h)
            if not (
                45 <= box_w <= max_box_width
                and 18 <= box_h <= max_box_height
                and box_w / max(1, box_h) >= 1.35
                and area >= min_area
                and fill >= min_fill
            ):
                continue
            if y <= height * 0.08 or x <= 5 or x + box_w >= width - 5:
                continue
            # The microscope scale bar labels live in the lower-left corner
            # and should not be counted as coating-thickness measurements.
            if y > height * 0.84 and x < width * 0.28:
                continue
            boxes.append((x, y, box_w, box_h))

        for box in sorted(boxes, key=lambda item: (item[1], item[0]))[:12]:
            if substantially_overlaps(box):
                continue
            box_values, texts = _ocr_label_box(detection_image, box, pytesseract)
            text_parts.extend(texts)
            if not box_values:
                continue
            values.extend(box_values)
            successful_boxes.append(box)
    return values, "\n".join(text_parts)


def _ocr_full_image(image: Image.Image, pytesseract: Any) -> tuple[list[float], str]:
    variants: list[tuple[str, Image.Image]] = [("full", ImageOps.autocontrast(image))]
    if max(image.size) > 1400:
        scale = 1400 / max(image.size)
        resized = image.resize((int(image.width * scale), int(image.height * scale)), Image.Resampling.LANCZOS)
        variants.append(("resized", ImageOps.autocontrast(resized)))
    variants.append(("sharp", ImageOps.autocontrast(image).filter(ImageFilter.SHARPEN)))

    text_parts: list[str] = []
    for label, variant in variants:
        try:
            text = _run_tesseract(pytesseract, variant, config="--psm 11", timeout=5).strip()
        except RuntimeError:
            continue
        if not text:
            continue
        text_parts.append(f"[{label}]\n{text}")
        values = _candidate_values_from_text(text, require_unit=True)
        if values:
            # The full image preserves italic leading digits that can be cut
            # off by a tight white-label crop. A unit-confirmed result is
            # therefore enough; avoid running the slower fallback variants.
            return values, "\n\n".join(text_parts)
    return [], "\n\n".join(text_parts)


def _box_from_quad(box: Any) -> tuple[int, int, int, int] | None:
    try:
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
    except (TypeError, ValueError, IndexError):
        return None
    if not xs or not ys:
        return None
    left = max(0, int(min(xs)))
    top = max(0, int(min(ys)))
    width = max(1, int(max(xs)) - left)
    height = max(1, int(max(ys)) - top)
    return left, top, width, height


def _is_microscope_scale_box(
    box: tuple[int, int, int, int] | None,
    image_size: tuple[int, int],
) -> bool:
    if box is None:
        return False
    x, y, width, height = box
    image_width, image_height = image_size
    center_x = x + width / 2
    center_y = y + height / 2
    return center_x < image_width * 0.32 and center_y > image_height * 0.82


def _get_rapid_ocr_engine() -> Any:
    global _rapid_ocr_engine
    if _rapid_ocr_engine is None:
        from rapidocr import RapidOCR  # type: ignore

        _rapid_ocr_engine = RapidOCR()
    return _rapid_ocr_engine


def _run_rapid_ocr(image: Image.Image) -> tuple[list[OcrCandidate], str]:
    """Detect and recognize measurement labels without relying on white boxes."""
    try:
        import numpy as np  # type: ignore
        import rapidocr  # noqa: F401  # type: ignore
    except Exception:
        return [], ""

    variants = [("original", image)]
    contrasted = ImageOps.autocontrast(image)
    if max(image.size) < 1800:
        scale = 1800 / max(image.size)
        contrasted = contrasted.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
    variants.append(("contrast", contrasted))

    text_parts: list[str] = []
    candidates: list[OcrCandidate] = []
    for variant_name, variant in variants:
        try:
            rgb = np.array(variant.convert("RGB"))
            # RapidOCR's ONNX sessions are shared to avoid loading three model
            # files for every image. Serialize inference because coating OCR
            # itself already runs in a small thread pool.
            with _rapid_ocr_lock:
                result = _get_rapid_ocr_engine()(
                    rgb,
                    text_score=0.55,
                    box_thresh=0.35,
                )
        except Exception:
            continue

        texts = tuple(getattr(result, "txts", ()) or ())
        scores = tuple(getattr(result, "scores", ()) or ())
        boxes = getattr(result, "boxes", None)
        for index, (raw_text, raw_score) in enumerate(zip(texts, scores)):
            text = str(raw_text).strip()
            score = float(raw_score)
            box = _box_from_quad(boxes[index]) if boxes is not None and index < len(boxes) else None
            text_parts.append(f"[{variant_name} {score:.3f}] {text}")
            if score < RAPID_OCR_MIN_CONFIDENCE:
                continue

            values = _candidate_values_from_text(
                text,
                require_unit=True,
                exclude_microscope_scale=False,
            )
            if not values:
                continue

            # The image may have been upscaled for the contrast pass. Scale
            # boxes back before using their location or passing them to the
            # Tesseract verifier.
            if box is not None and variant.size != image.size:
                scale_x = image.width / variant.width
                scale_y = image.height / variant.height
                box = (
                    round(box[0] * scale_x),
                    round(box[1] * scale_y),
                    max(1, round(box[2] * scale_x)),
                    max(1, round(box[3] * scale_y)),
                )
            if _is_microscope_scale_box(box, image.size):
                continue

            candidates.extend(
                OcrCandidate(value_nm=value, text=text, confidence=score, box=box)
                for value in values
            )

        if candidates:
            # A second pass is only needed when the original image did not
            # yield a measurement. This keeps large reports responsive.
            break

    unique: list[OcrCandidate] = []
    for candidate in candidates:
        if not any(abs(candidate.value_nm - existing.value_nm) < 0.025 for existing in unique):
            unique.append(candidate)
    return unique, "\n".join(text_parts)


def _reconcile_coating_ocr_ensemble(
    rapid_candidates: list[OcrCandidate],
    label_values: list[float],
    full_values: list[float],
) -> list[float]:
    """Merge OCR engines while protecting leading digits from crop loss."""
    if not rapid_candidates:
        return _reconcile_coating_ocr_values(label_values, full_values)

    # RapidOCR detects the complete label and provides a confidence score.
    # Narrow Tesseract crops are retained in the audit text, but must not add
    # values once a high-confidence neural reading exists: measurement lines
    # can otherwise become false integers (7, 4, 8) or duplicate a label with
    # a damaged leading digit (12.21 -> 2.21/22.21).
    return _dedupe_values([candidate.value_nm for candidate in rapid_candidates])


def _reconcile_coating_ocr_values(label_values: list[float], full_values: list[float]) -> list[float]:
    """Prefer unit-confirmed full-image readings over narrow label crops."""
    if not full_values:
        return label_values
    if not label_values:
        return full_values

    matched_full_values = [
        value
        for value in full_values
        if any(abs(value - label_value) <= 0.25 for label_value in label_values)
    ]
    # When every localized label has a matching full-image reading, discard
    # additional full-image candidates produced by measurement-line artifacts.
    if len(matched_full_values) >= len(label_values):
        return matched_full_values
    return full_values


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
    pytesseract: Any | None = None
    if shutil.which("tesseract") is not None:
        try:
            import pytesseract as pytesseract_module  # type: ignore

            pytesseract = pytesseract_module
        except Exception:
            pass

    try:
        image = Image.open(path)
        image = ImageOps.exif_transpose(image).convert("L")
        rapid_candidates, rapid_text = _run_rapid_ocr(image)

        label_values: list[float] = []
        label_text = ""
        full_values: list[float] = []
        full_text = ""
        if pytesseract is not None:
            # Keep the independent white-label detector as a verifier and as
            # a fallback for labels missed by the neural text detector.
            label_values, label_text = _ocr_label_boxes(image, pytesseract)
            if not rapid_candidates:
                full_values, full_text = _ocr_full_image(image, pytesseract)

        values = _reconcile_coating_ocr_ensemble(
            rapid_candidates,
            label_values,
            full_values,
        )
        text = "\n\n".join(
            part
            for part in (
                f"[rapidocr]\n{rapid_text}" if rapid_text else "",
                f"[tesseract-label]\n{label_text}" if label_text else "",
                f"[tesseract-full]\n{full_text}" if full_text else "",
            )
            if part
        )
    except Exception as exc:  # pragma: no cover - depends on OCR runtime.
        return CoatingOcrResult(note=f"자동 판독 실패: {exc}", review_required=True)

    if not text and pytesseract is None:
        return CoatingOcrResult(note="자동 판독 엔진 없음", review_required=True)

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


def _coating_ocr_workers() -> int:
    raw = os.getenv("RIST_TEM_OCR_WORKERS", str(DEFAULT_COATING_OCR_WORKERS))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = DEFAULT_COATING_OCR_WORKERS
    return max(1, min(MAX_COATING_OCR_WORKERS, value))


def _coating_measurement(path_item: tuple[int, Path], root: Path) -> CoatingMeasurement:
    index, path = path_item
    ocr = _ocr_thickness_nm(path)
    return CoatingMeasurement(
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


def _coating_measurements(images: list[Path], root: Path) -> list[CoatingMeasurement]:
    indexed_images = list(enumerate(images, start=1))
    workers = min(_coating_ocr_workers(), len(indexed_images))
    if workers <= 1:
        return [_coating_measurement(item, root) for item in indexed_images]
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ahn-coating-ocr") as executor:
        return list(executor.map(lambda item: _coating_measurement(item, root), indexed_images))


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
        measurements = _coating_measurements(images, root)
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

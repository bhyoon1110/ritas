"""AHN TEM/STEM/EDS/coating-layer input scanner.

The AHN project receives a folder bundle with four logical folders:

* ``tem``: TEM images, grouped by sample-name subfolders.
* ``stem``: STEM and BF-STEM images, grouped by filename.
* ``report``: STEM EDS Word reports and raw spreadsheets.
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

from PIL import Image, ImageOps

IMAGE_EXTENSIONS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp", ".webp"}
DOCX_EXTENSIONS = {".docx"}
SPREADSHEET_EXTENSIONS = {".xlsx", ".xls", ".csv", ".tsv"}


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
    ocr_text: str = ""
    note: str = ""


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
    report_dir = find_named_dir(root, "report")
    if report_dir is None:
        return [], []

    reports = []
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

    spreadsheets = [
        SpreadsheetFile(path=_relative(path, root), file_name=path.name)
        for path in _visible_files(report_dir, SPREADSHEET_EXTENSIONS)
    ]
    return reports, spreadsheets


def _ocr_thickness_nm(path: Path) -> tuple[float | None, str, str]:
    """Try OCR for a ``{number nm}`` coating thickness label.

    The runtime may not have the tesseract binary installed. In that case the
    measurement remains reviewable instead of failing report generation.
    """
    if shutil.which("tesseract") is None:
        return None, "", "OCR 엔진 없음"
    try:
        import pytesseract  # type: ignore
    except Exception:
        return None, "", "pytesseract 없음"

    try:
        image = Image.open(path)
        image = ImageOps.exif_transpose(image).convert("L")
        width, height = image.size
        crop = image.crop((0, int(height * 0.55), width, height))
        crop = ImageOps.autocontrast(crop)
        text = pytesseract.image_to_string(crop, config="--psm 6")
    except Exception as exc:  # pragma: no cover - depends on OCR runtime.
        return None, "", f"OCR 실패: {exc}"

    match = re.search(r"(\d+(?:\.\d+)?)\s*nm", text, flags=re.IGNORECASE)
    if not match:
        return None, text.strip(), "두께값 미검출"
    return float(match.group(1)), text.strip(), ""


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
            thickness, text, note = _ocr_thickness_nm(path)
            measurements.append(
                CoatingMeasurement(
                    index=index,
                    path=_relative(path, root),
                    file_name=path.name,
                    magnification=extract_magnification(path.name),
                    thickness_nm=thickness,
                    ocr_text=text,
                    note=note,
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
            "report": find_named_dir(root, "report"),
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

"""Content-based file type and protection inspection helpers."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re
from typing import BinaryIO
import zipfile


OLE_COMPOUND_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
PDF_HEADER = b"%PDF-"
ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")

_OFFICE_PROTECTION_MARKERS = (
    "EncryptedPackage",
    "EncryptionInfo",
    "DRMContent",
    "DataSpaces",
)
_PDF_PROTECTION_PATTERNS = (
    re.compile(rb"/Encrypt\b"),
    re.compile(rb"/Filter\s*/Adobe\.PubSec\b"),
    re.compile(rb"/SubFilter\s*/(?:adbe|ETSI)\.", re.IGNORECASE),
    re.compile(rb"/EBX_HANDLER\b"),
)
_PDF_PROTECTION_MARKERS = (
    b"/Encrypt",
    b"/Adobe.PubSec",
    b"/EBX_HANDLER",
    b"/adbe.",
    b"/ETSI.",
)
_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image", ".png"),
    (b"\xff\xd8\xff", "image", ".jpg"),
    (b"II*\x00", "image", ".tif"),
    (b"MM\x00*", "image", ".tif"),
    (b"BM", "image", ".bmp"),
    (b"GIF87a", "image", ".gif"),
    (b"GIF89a", "image", ".gif"),
)


@dataclass(frozen=True)
class FileInspection:
    kind: str
    canonical_suffix: str | None = None
    protected: bool = False
    protection_reason: str | None = None
    encrypted_members: tuple[str, ...] = ()

    @property
    def recognized(self) -> bool:
        return self.kind != "unknown"


def _contains_office_protection_marker(data: bytes) -> bool:
    return any(
        marker.encode("ascii") in data or marker.encode("utf-16le") in data
        for marker in _OFFICE_PROTECTION_MARKERS
    )


def _contains_pdf_protection_marker(data: bytes) -> bool:
    return any(pattern.search(data) for pattern in _PDF_PROTECTION_PATTERNS)


def _looks_like_text(data: bytes) -> bool:
    if not data or b"\x00" in data[:8192]:
        return False
    sample = data[:65536]
    for encoding in ("utf-8-sig", "cp949", "latin-1"):
        try:
            decoded = sample.decode(encoding)
        except UnicodeDecodeError:
            continue
        if not decoded:
            return False
        printable = sum(character.isprintable() or character in "\r\n\t" for character in decoded)
        return printable / len(decoded) >= 0.85
    return False


def _text_kind(data: bytes) -> FileInspection:
    sample = data[:65536]
    for encoding in ("utf-8-sig", "cp949", "latin-1"):
        try:
            text = sample.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        return FileInspection("unknown")
    lines = [line for line in text.splitlines() if line.strip()][:20]
    if lines and sum("\t" in line for line in lines) >= max(1, len(lines) // 2):
        return FileInspection("tsv", ".tsv")
    if lines and sum("," in line for line in lines) >= max(1, len(lines) // 2):
        return FileInspection("csv", ".csv")
    return FileInspection("text", ".txt")


def _ooxml_kind(names: set[str], content_types: bytes) -> tuple[str, str] | None:
    if "word/document.xml" in names:
        return "docx", ".docx"
    if "xl/workbook.bin" in names:
        return "xlsb", ".xlsb"
    if "xl/workbook.xml" in names:
        if b"macroEnabled" in content_types or "xl/vbaProject.bin" in names:
            return "xlsm", ".xlsm"
        return "xlsx", ".xlsx"
    if "ppt/presentation.xml" in names:
        if b"macroEnabled" in content_types or "ppt/vbaProject.bin" in names:
            return "pptm", ".pptm"
        return "pptx", ".pptx"
    return None


def _inspect_zip(source: str | Path | BinaryIO) -> FileInspection:
    try:
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            encrypted = tuple(item.filename for item in members if item.flag_bits & 0x1)
            names = {item.filename for item in members}
            content_types = b""
            if "[Content_Types].xml" in names:
                try:
                    content_types = archive.read("[Content_Types].xml")
                except (RuntimeError, OSError, KeyError):
                    content_types = b""
            office_kind = _ooxml_kind(names, content_types)
            if encrypted:
                kind, suffix = office_kind or ("zip", ".zip")
                return FileInspection(
                    kind,
                    suffix,
                    protected=True,
                    protection_reason="encrypted_zip_members",
                    encrypted_members=encrypted,
                )
            if office_kind:
                return FileInspection(*office_kind)
            return FileInspection("zip", ".zip")
    except (zipfile.BadZipFile, RuntimeError, OSError, ValueError):
        return FileInspection("unknown")


def _inspect_ole(data: bytes) -> FileInspection:
    protected = _contains_office_protection_marker(data)
    if protected:
        return FileInspection(
            "encrypted_office",
            protected=True,
            protection_reason="office_encrypted_package",
        )
    markers = (
        ("Workbook", "xls", ".xls"),
        ("Book", "xls", ".xls"),
        ("WordDocument", "doc", ".doc"),
        ("PowerPoint Document", "ppt", ".ppt"),
    )
    for marker, kind, suffix in markers:
        if marker.encode("ascii") in data or marker.encode("utf-16le") in data:
            return FileInspection(kind, suffix)
    return FileInspection("ole", protected=False)


def inspect_file_bytes(data: bytes, *, filename: str | None = None) -> FileInspection:
    """Inspect a complete in-memory file without trusting its filename."""
    if not data:
        return FileInspection("empty")
    if data.startswith(OLE_COMPOUND_MAGIC):
        return _inspect_ole(data)
    if data.startswith(ZIP_MAGICS):
        return _inspect_zip(BytesIO(data))
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return FileInspection("image", ".webp")
    for signature, kind, suffix in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return FileInspection(kind, suffix)
    pdf_index = data[:1024].find(PDF_HEADER)
    if pdf_index >= 0:
        protected = _contains_pdf_protection_marker(data)
        return FileInspection(
            "pdf",
            ".pdf",
            protected=protected,
            protection_reason="pdf_encrypted" if protected else None,
        )
    if _contains_office_protection_marker(data):
        return FileInspection(
            "encrypted_office",
            protected=True,
            protection_reason="office_protection_marker",
        )
    if _looks_like_text(data):
        return _text_kind(data)
    return FileInspection("unknown")


def _read_probe(path: Path, *, chunk_size: int = 1024 * 1024) -> bytes:
    size = path.stat().st_size
    with path.open("rb") as stream:
        prefix = stream.read(min(size, 8 * chunk_size))
        if size <= len(prefix):
            return prefix
        stream.seek(max(0, size - chunk_size))
        return prefix + stream.read(chunk_size)


def _path_contains_markers(path: Path, markers: tuple[bytes, ...]) -> bool:
    overlap = max((len(marker) for marker in markers), default=1) - 1
    previous = b""
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                return False
            data = previous + chunk
            if any(marker in data for marker in markers):
                return True
            previous = data[-overlap:] if overlap else b""


def inspect_file_path(path: str | Path) -> FileInspection:
    """Inspect a file efficiently, including container and protection metadata."""
    source = Path(path)
    if not source.is_file() or source.stat().st_size <= 0:
        return FileInspection("empty")
    probe = _read_probe(source)
    if probe.startswith(OLE_COMPOUND_MAGIC):
        marker_bytes = tuple(
            encoded
            for marker in _OFFICE_PROTECTION_MARKERS
            for encoded in (marker.encode("ascii"), marker.encode("utf-16le"))
        )
        if _path_contains_markers(source, marker_bytes):
            return FileInspection(
                "encrypted_office",
                protected=True,
                protection_reason="office_encrypted_package",
            )
        return _inspect_ole(probe)
    if probe.startswith(ZIP_MAGICS):
        return _inspect_zip(source)
    if probe[:1024].find(PDF_HEADER) >= 0:
        protected = _path_contains_markers(source, _PDF_PROTECTION_MARKERS)
        return FileInspection(
            "pdf",
            ".pdf",
            protected=protected,
            protection_reason="pdf_encrypted" if protected else None,
        )
    if _contains_office_protection_marker(probe):
        return FileInspection(
            "encrypted_office",
            protected=True,
            protection_reason="office_protection_marker",
        )
    return inspect_file_bytes(probe, filename=source.name)

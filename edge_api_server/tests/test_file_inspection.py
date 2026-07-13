from __future__ import annotations

from io import BytesIO
from pathlib import Path
import zipfile

from app.file_inspection import OLE_COMPOUND_MAGIC, inspect_file_bytes, inspect_file_path


def _ooxml_bytes(member: str) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(member, "<root/>")
    return buffer.getvalue()


def test_inspection_detects_ooxml_from_content_without_extension() -> None:
    document = inspect_file_bytes(_ooxml_bytes("word/document.xml"), filename="payload.bin")
    workbook = inspect_file_bytes(_ooxml_bytes("xl/workbook.xml"), filename="payload.dat")

    assert document.kind == "docx"
    assert document.canonical_suffix == ".docx"
    assert workbook.kind == "xlsx"
    assert workbook.canonical_suffix == ".xlsx"


def test_inspection_detects_pdf_and_image_without_extension() -> None:
    pdf = inspect_file_bytes(b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\n")
    image = inspect_file_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    assert pdf.kind == "pdf"
    assert pdf.protected is False
    assert image.kind == "image"
    assert image.canonical_suffix == ".png"


def test_inspection_marks_pdf_and_office_protection() -> None:
    pdf = inspect_file_bytes(b"%PDF-1.7\n1 0 obj\n<< /Encrypt 9 0 R >>\n")
    office = inspect_file_bytes(
        OLE_COMPOUND_MAGIC + b"\x00" * 128 + "EncryptedPackage".encode("utf-16le")
    )

    assert pdf.kind == "pdf"
    assert pdf.protected is True
    assert office.kind == "encrypted_office"
    assert office.protected is True


def test_path_inspection_scans_pdf_protection_beyond_initial_probe(tmp_path: Path) -> None:
    path = tmp_path / "protected.data"
    path.write_bytes(b"%PDF-1.7\n" + b"0" * (9 * 1024 * 1024) + b"\n/Encrypt 9 0 R\n")

    inspection = inspect_file_path(path)

    assert inspection.kind == "pdf"
    assert inspection.protected is True

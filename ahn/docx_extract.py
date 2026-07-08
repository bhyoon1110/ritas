"""Minimal DOCX media/table extraction for AHN EDS report slides."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from zipfile import ZipFile

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}


@dataclass
class DocxExtract:
    media_paths: list[Path] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)


def _relationship_map(zip_file: ZipFile) -> dict[str, str]:
    try:
        rels = ET.fromstring(zip_file.read("word/_rels/document.xml.rels"))
    except KeyError:
        return {}
    result = {}
    for rel in rels.findall("rel:Relationship", NS):
        rid = rel.attrib.get("Id", "")
        target = rel.attrib.get("Target", "")
        if rid and target:
            result[rid] = "word/" + target.lstrip("/")
    return result


def _media_order(zip_file: ZipFile) -> list[str]:
    rels = _relationship_map(zip_file)
    try:
        document = ET.fromstring(zip_file.read("word/document.xml"))
    except KeyError:
        return []
    ordered = []
    for blip in document.findall(".//a:blip", NS):
        rid = blip.attrib.get(f"{{{NS['r']}}}embed")
        target = rels.get(rid or "")
        if target and target not in ordered:
            ordered.append(target)
    return ordered


def _cell_text(cell: ET.Element) -> str:
    texts = [node.text or "" for node in cell.findall(".//w:t", NS)]
    return re.sub(r"\s+", " ", " ".join(texts)).strip()


def _tables(zip_file: ZipFile) -> list[list[list[str]]]:
    try:
        document = ET.fromstring(zip_file.read("word/document.xml"))
    except KeyError:
        return []
    tables = []
    for table in document.findall(".//w:tbl", NS):
        rows = []
        for row in table.findall("./w:tr", NS):
            rows.append([_cell_text(cell) for cell in row.findall("./w:tc", NS)])
        if rows:
            tables.append(rows)
    return tables


def extract_docx(path: str | Path, output_dir: str | Path) -> DocxExtract:
    """Extract embedded images in document order and basic tables."""
    source = Path(path)
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    media_paths: list[Path] = []
    with ZipFile(source) as zip_file:
        for index, member in enumerate(_media_order(zip_file), start=1):
            try:
                data = zip_file.read(member)
            except KeyError:
                continue
            suffix = Path(member).suffix or ".png"
            target = target_dir / f"{source.stem}-{index:03d}{suffix}"
            target.write_bytes(data)
            media_paths.append(target)
        tables = _tables(zip_file)
    return DocxExtract(media_paths=media_paths, tables=tables)

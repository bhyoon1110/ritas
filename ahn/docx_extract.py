"""Minimal DOCX media/table extraction for AHN EDS report slides."""

from __future__ import annotations

import re
import posixpath
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote
from zipfile import ZipFile

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "v": "urn:schemas-microsoft-com:vml",
    "o": "urn:schemas-microsoft-com:office:office",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
}

# Word's Strict Open XML uses different URIs for the same document concepts.
STRICT_NS = {
    "http://purl.oclc.org/ooxml/wordprocessingml/main": NS["w"],
    "http://purl.oclc.org/ooxml/drawingml/main": NS["a"],
    "http://purl.oclc.org/ooxml/officeDocument/relationships": NS["r"],
    "http://purl.oclc.org/ooxml/drawingml/wordprocessingDrawing": NS["wp"],
}


@dataclass
class DocxImage:
    path: Path
    label: str = ""
    display_size: tuple[int, int] | None = None


@dataclass
class DocxExtract:
    media_paths: list[Path] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    image_items: list[DocxImage] = field(default_factory=list)


def _electron_label(text: str) -> str:
    match = re.search(r"\bElectron\s*Image[\s_:#-]*(\d+)\b", text, re.IGNORECASE)
    return f"Electron Image{int(match[1])}" if match else ""


def _image_items(document: ET.Element, relationships: dict[str, str], paths: dict[str, Path]) -> list[DocxImage]:
    """Keep placement/caption and repeated occurrences, without duplicating Word fallbacks."""
    items: list[DocxImage] = []
    pending_label = ""

    def target(node: ET.Element) -> str | None:
        if node.tag == f"{{{NS['a']}}}blip":
            rid = node.get(f"{{{NS['r']}}}embed", "")
        elif node.tag == f"{{{NS['v']}}}imagedata":
            rid = node.get(f"{{{NS['r']}}}id") or node.get(f"{{{NS['o']}}}relid", "")
        else:
            return None
        return relationships.get(rid)

    def visit(node: ET.Element, figure: ET.Element | None = None) -> None:
        nonlocal pending_label
        if node.tag == f"{{{NS['mc']}}}AlternateContent":
            # Only one rendering branch is displayed by Word. Prefer the first
            # supported branch; do not count modern and VML copies as two maps.
            for branch in node:
                if any(target(child) in paths for child in branch.iter()):
                    visit(branch, figure)
                    break
            return
        if node.tag == f"{{{NS['w']}}}p":
            text = _cell_text(node)
            if text:
                pending_label = _electron_label(text)
        if node.tag in {f"{{{NS['w']}}}drawing", f"{{{NS['w']}}}pict"}:
            figure = node
        member = target(node)
        if member in paths:
            label, size = pending_label, None
            pending_label = ""
            if figure is not None:
                descriptions = " ".join(value for child in figure.iter() for key, value in child.attrib.items()
                                        if key in {"name", "descr", "title", "alt"})
                label = _electron_label(descriptions) or label
                extent = figure.find(".//wp:extent", NS)
                if extent is not None:
                    try:
                        width, height = int(extent.get("cx", "0")), int(extent.get("cy", "0"))
                        if width > 0 and height > 0:
                            size = (width, height)
                    except ValueError:
                        pass
            items.append(DocxImage(paths[member], label, size))
        for child in node:
            visit(child, figure)

    visit(document)
    return items


def _document(zip_file: ZipFile) -> ET.Element:
    try:
        document = ET.fromstring(zip_file.read("word/document.xml"))
    except KeyError:
        return ET.Element("missing-document")
    def canonical(name: str) -> str:
        if name.startswith("{"):
            namespace, local = name[1:].split("}", 1)
            return "{" + STRICT_NS.get(namespace, namespace) + "}" + local
        return name
    for element in document.iter():
        element.tag = canonical(element.tag)
        element.attrib = {canonical(key): value for key, value in element.attrib.items()}
    return document


def _relationship_map(zip_file: ZipFile) -> dict[str, str]:
    try:
        rels = ET.fromstring(zip_file.read("word/_rels/document.xml.rels"))
    except KeyError:
        return {}
    result = {}
    for rel in rels.findall("rel:Relationship", NS):
        rid = rel.attrib.get("Id", "")
        target = rel.attrib.get("Target", "")
        if rid and target and rel.attrib.get("TargetMode", "").lower() != "external":
            target = unquote(target)
            member = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "word/" + target)
            # Some instrument reports store their images at package-root
            # /media/, referenced explicitly by the document relationships.
            # Resolve only those references, never scan in logos/orphan media.
            if member.startswith(("word/media/", "media/")):
                result[rid] = member
    return result


def _media_order(zip_file: ZipFile, document: ET.Element | None = None) -> list[str]:
    rels = _relationship_map(zip_file)
    if document is None:
        document = _document(zip_file)
    ordered = []
    # Instrument-generated Word reports may mix DrawingML and older VML.
    # Iteration preserves document order and deduplicates AlternateContent's
    # modern/fallback references to the same image.
    for blip in document.iter():
        if blip.tag == f"{{{NS['a']}}}blip":
            rid = blip.attrib.get(f"{{{NS['r']}}}embed")
        elif blip.tag == f"{{{NS['v']}}}imagedata":
            rid = blip.attrib.get(f"{{{NS['r']}}}id") or blip.attrib.get(f"{{{NS['o']}}}relid")
        else:
            continue
        target = rels.get(rid or "")
        if target and target not in ordered:
            ordered.append(target)
    return ordered


def _cell_text(cell: ET.Element) -> str:
    texts = [node.text or "" for node in cell.findall(".//w:t", NS)]
    return re.sub(r"\s+", " ", " ".join(texts)).strip()


def _tables(zip_file: ZipFile, document: ET.Element | None = None) -> list[list[list[str]]]:
    if document is None:
        document = _document(zip_file)
    tables = []
    for table in document.findall(".//w:tbl", NS):
        rows = []
        for row in table.findall("./w:tr", NS):
            rows.append([_cell_text(cell) for cell in row.findall("./w:tc", NS)])
        if rows:
            tables.append(rows)
    return tables


def _empty_diagnostics(zip_file: ZipFile, document: ET.Element) -> list[str]:
    """Explain unsupported content without fetching links or inventing images.

    Unreferenced package media may be logos, deleted content or thumbnails;
    never substitute those for an instrument's missing measurement images.
    """
    media_count = sum(name.startswith(("word/media/", "media/")) and not name.endswith("/") for name in zip_file.namelist())
    image_count = sum(node.tag in {f"{{{NS['a']}}}blip", f"{{{NS['v']}}}imagedata"} for node in document.iter())
    diagnostics = [f"내장 미디어 {media_count}개, 본문 이미지 참조 {image_count}개"]
    if document.find(".//w:altChunk", NS) is not None:
        diagnostics.append("본문이 외부 형식 삽입(altChunk)입니다. Word에서 열어 일반 DOCX로 다시 저장하세요")
    elif image_count:
        diagnostics.append("본문 이미지가 외부 링크이거나 내장 파일/관계 정보가 누락되었을 수 있습니다. 그림을 문서에 포함하여 다시 저장하세요")
    elif media_count:
        diagnostics.append("내장 미디어를 본문 이미지와 연결하지 못했습니다. 원본 DOCX 구조 확인이 필요합니다")
    else:
        diagnostics.append("추출 가능한 본문 그림/표가 없습니다. Word에서 내용이 보이는지 확인하고 원본 DOCX를 전달해 주세요")
    return diagnostics


def extract_docx(path: str | Path, output_dir: str | Path) -> DocxExtract:
    """Extract embedded images in document order and basic tables."""
    source = Path(path)
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    media_paths: list[Path] = []
    member_paths: dict[str, Path] = {}
    with ZipFile(source) as zip_file:
        document = _document(zip_file)
        for index, member in enumerate(_media_order(zip_file, document), start=1):
            try:
                data = zip_file.read(member)
            except KeyError:
                continue
            suffix = Path(member).suffix or ".png"
            target = target_dir / f"{source.stem}-{index:03d}{suffix}"
            target.write_bytes(data)
            media_paths.append(target)
            member_paths[member] = target
        tables = _tables(zip_file, document)
        image_items = _image_items(document, _relationship_map(zip_file), member_paths)
        diagnostics = _empty_diagnostics(zip_file, document) if not media_paths and not tables else []
    return DocxExtract(media_paths=media_paths, tables=tables, diagnostics=diagnostics, image_items=image_items)

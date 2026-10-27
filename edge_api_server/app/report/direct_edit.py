"""Author-only edit regions from explicit renderer bindings and actual PPT geometry."""
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pdfplumber

from . import renderers
from . import pptx_tables
from .editable import TRANSFER_FIELDS
from .spectrum_viewer import _PDF_RENDER_LOCK

NS = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}


def section_ref(section_id, kind, *indices):
    return "/".join(["section", section_id, kind, *map(str, indices)])


def body_refs(document, section_id):
    section = document.section(section_id)
    if not section:
        return []
    refs = [section_ref(section_id, key, i) for key in ("paragraphs", "bullets")
            for i in range(len(getattr(section, key)))]
    return refs or ([section_ref(section_id, "paragraphs", 0)] if section.table is None else [])


def edit_pages(document, pptx_path):
    payloads = renderers._pptx_slide_payloads(document)
    pages = []
    with ZipFile(pptx_path) as archive:
        size = ET.fromstring(archive.read("ppt/presentation.xml")).find("p:sldSz", NS)
        width, height = int(size.get("cx")), int(size.get("cy"))
        count = len([n for n in archive.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")])
        if count != len(payloads) or count > 200:
            raise ValueError("보고서 페이지 구성이 변경되었습니다. ‘지금 반영’으로 미리보기를 다시 생성하세요.")
        for index, payload in enumerate(payloads):
            root = ET.fromstring(archive.read(f"ppt/slides/slide{index + 1}.xml"))
            meta = payload.get("editing", {})
            kind, sid = meta.get("kind"), meta.get("sectionId")
            bindings = {"Footer": ["transfer/requestNumber", "completionDate"]}
            if kind in {"title", "traceability"}:
                bindings["Report title"] = ["title"]
                from .traceability import values_and_refs
                bindings.update({"Trace " + key: refs for key,refs in values_and_refs(document)[1].items()})
                bindings.update({"Cover " + key: refs for key,refs in values_and_refs(document)[1].items()})
                labels = {label: "transfer/" + key for key, label in TRANSFER_FIELDS.items()}
                labels["완료일"] = "completionDate"
                for n, (label, _) in enumerate(renderers._document_metadata(document)):
                    bindings[f"Meta card {n}"] = [labels[label]] if label in labels else []
            if kind == "overview":
                for name, section_id in (("Summary", "summary"), ("Findings", "key_findings")):
                    bindings[name + " text"] = body_refs(document, section_id)
                    # The card contains its heading plus the overlapping body; crop below.
                    bindings[name + " card"] = [section_ref(section_id, "heading")] if document.section(section_id) else []
                for n, key in enumerate(("requestNumber", "limsExperimentCode", "equipmentCode")):
                    bindings[f"Info card {n}"] = ["transfer/" + key]
            if kind == "insights":
                for section_id in ("interpretation", "qc_notes", "narrative"):
                    bindings[section_id + " text"] = body_refs(document, section_id)
                    bindings[section_id + " heading"] = [section_ref(section_id, "heading")] if document.section(section_id) else []
            if kind in {"text", "table"} and sid:
                bindings["Slide title"] = [section_ref(sid, "heading")] if sid != "report_transfer" else []
                bindings["Content text"] = body_refs(document, sid)
                bindings["Table explanation"] = body_refs(document, sid)
            if kind == "image":
                bindings["Caption card"] = body_refs(document, sid)
            if kind == "added":
                bindings["Slide title"] = ["page/" + meta["pageId"] + "/title"]
                bindings["Content text"] = ["page/" + meta["pageId"] + "/body"]
            regions = []
            def cell_refs(row, col):
                rows, cols = meta.get("rows", []), meta.get("columns", [])
                if row >= len(rows) or col >= len(cols):
                    return []
                if sid == "report_transfer":
                    return ["transfer/" + list(TRANSFER_FIELDS)[rows[row]]] if cols[col] == 1 else []
                return [section_ref(sid, "rows", rows[row], cols[col])]

            if kind == "table":
                for frame in root.findall("p:cSld/p:spTree/p:graphicFrame", NS):
                    for row, col, cell, (x, y, w, h) in pptx_tables.cells(frame):
                        if row == 0:
                            continue  # Column headings are edited in free-edit mode.
                        refs = list(dict.fromkeys(ref for ri in range(row - 1, row - 1 + int(cell.get("rowSpan", 1)))
                                                  for ref in cell_refs(ri, col)))
                        if refs:
                            regions.append({"refs": refs, "cell": True, "x": x / width, "y": y / height,
                                            "w": w / width, "h": h / height,
                                            "empty": not pptx_tables.text(cell).strip()})
            for shape in root.findall("p:cSld/p:spTree/p:sp", NS):
                name = shape.find("p:nvSpPr/p:cNvPr", NS).get("name")
                refs = bindings.get(name, [])
                if kind == "table" and name.startswith("Cell "):
                    row, col = map(int, name[5:].split("-"))
                    refs = cell_refs(row, col)
                if not refs:
                    continue
                xfrm = shape.find("p:spPr/a:xfrm", NS)
                if xfrm is None:
                    continue
                off, extent = xfrm.find("a:off", NS), xfrm.find("a:ext", NS)
                x, y, w, h = int(off.get("x")), int(off.get("y")), int(extent.get("cx")), int(extent.get("cy"))
                if kind == "overview" and name in {"Summary card", "Findings card"}:
                    h = 500000  # Heading only, never obscure the body's edit region.
                regions.append({"refs": refs, "x": x / width, "y": y / height,
                                "w": w / width, "h": h / height,
                                "empty": not any((node.text or "").strip() for node in shape.findall(".//a:t", NS))})
            title = next((" ".join(t.text or "" for t in s.findall(".//a:t", NS)) for s in root.findall("p:cSld/p:spTree/p:sp", NS)
                          if s.find("p:nvSpPr/p:cNvPr", NS).get("name") in {"Report title", "Slide title"}), "PPT")
            if kind == "traceability": title = "분석 이력 요약"
            if kind == "added" and not title.strip(): title = "제목 없는 페이지"
            from .canvas import objects
            pages.append({"id": payload["pageId"], "title": title, "regions": regions,
                          "legacyTableLayout": bool(payload.get("legacyTableLayout")),
                          # Use the actual archive, including older shape-based reports.
                          "objects": [item for _, item in objects({"xml": ET.tostring(root, encoding="unicode")})[1]]})
    return {"pages": pages, "ratio": width / height, "pageEditing": True, "canvasEditing": True}


@lru_cache(maxsize=8)
def slide_image(pdf_path: str, mtime: int, page: int, thumbnail: bool = False) -> bytes:
    """Decode only the requested page; at most eight JPEGs cached across jobs."""
    with _PDF_RENDER_LOCK, pdfplumber.open(Path(pdf_path)) as pdf:
        if page < 0 or page >= len(pdf.pages):
            raise IndexError(page)
        rendered = pdf.pages[page].to_image(width=240 if thumbnail else 1280, antialias=True)
        image = rendered.original.convert("RGB")
        try:
            output = BytesIO()
            image.save(output, format="JPEG", quality=72 if thumbnail else 86)
            return output.getvalue()
        finally:
            image.close()
            rendered.original.close()

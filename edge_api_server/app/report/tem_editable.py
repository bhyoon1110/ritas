"""Edit generated TEM PPT text/cells without rebuilding images or re-running OCR."""
from copy import deepcopy
from datetime import date
from pathlib import Path
import json
import shutil
import tempfile
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from .editable import TRANSFER_FIELDS
from .editor_variables import resolve_changes
from ahn.report_cover import COVER_NAME, TEM_FIELDS

NS = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
COATING_TAG = "rist-coating-table:"


def _text(body):
    return "\n".join("".join("\n" if n.tag == f"{{{NS['a']}}}br" else n.text or ""
                            for n in p.iter() if n.tag in {f"{{{NS['a']}}}t", f"{{{NS['a']}}}br"})
                     for p in body.findall("a:p", NS))


def _slides(archive):
    return sorted((name for name in archive.namelist() if name.startswith("ppt/slides/slide")
                   and name.endswith(".xml")), key=lambda name: int(Path(name).stem[5:]))


def inspect_pptx(path):
    """Only expose server-owned generated text frames and table cells, never paths."""
    sections, pages, bindings = [], [], {}
    with ZipFile(path) as archive:
        size = ET.fromstring(archive.read("ppt/presentation.xml")).find("p:sldSz", NS)
        width, height = int(size.get("cx")), int(size.get("cy"))
        for index, name in enumerate(_slides(archive)):
            root = ET.fromstring(archive.read(name))
            is_cover = root.find("p:cSld", NS).get("name") == COVER_NAME
            regions = []
            title = f"TEM {index + 1}페이지"
            for shape in root.findall("p:cSld/p:spTree/*", NS):
                coating_rows = None
                if shape.tag == f"{{{NS['p']}}}sp":
                    body = shape.find("p:txBody", NS)
                    transform = shape.find("p:spPr/a:xfrm", NS)
                    identity = shape.find("p:nvSpPr/p:cNvPr", NS)
                    table = None
                elif shape.tag == f"{{{NS['p']}}}graphicFrame":
                    table = shape.find("a:graphic/a:graphicData/a:tbl", NS)
                    body = None
                    transform = shape.find("p:xfrm", NS)
                    identity = shape.find("p:nvGraphicFramePr/p:cNvPr", NS)
                elif shape.tag == f"{{{NS['p']}}}pic":
                    identity = shape.find("p:nvPicPr/p:cNvPr", NS)
                    description = identity.get("descr", "") if identity is not None else ""
                    if not description.startswith(COATING_TAG):
                        continue
                    coating_rows = json.loads(description[len(COATING_TAG):])
                    transform = shape.find("p:spPr/a:xfrm", NS)
                    body, table = None, None
                else:
                    continue
                if transform is None or identity is None or (body is None and table is None and coating_rows is None):
                    continue
                off, ext = transform.find("a:off", NS), transform.find("a:ext", NS)
                x, y, w, h = (int(off.get("x")), int(off.get("y")), int(ext.get("cx")), int(ext.get("cy")))
                shape_name = identity.get("name", "")
                if is_cover and body is not None:
                    ref = _cover_ref(shape_name)
                    if ref:
                        regions.append(_region(x, y, w, h, width, height, [ref], False))
                        if ref == "title":
                            title = _text(body)
                        continue
                    if shape_name in {"Experiment banner title", "Report eyebrow"}:
                        continue
                sid = f"slide-{index + 1}-shape-{identity.get('id')}"
                section = dict(sectionId=sid, heading=f"{index + 1}페이지 · {identity.get('name', '내용')}",
                               paragraphs=[], bullets=[], columns=None, rows=None, source="user")
                refs = []
                if coating_rows is not None:
                    from ahn.ppt_report import _coating_column_widths
                    section["rows"] = coating_rows
                    section["columns"] = ["측정개소", "두께(nm)"]
                    widths = _coating_column_widths(w)
                    for r, row in enumerate(coating_rows):
                        left = x
                        for c, value in enumerate(row):
                            regions.append(_region(left, y + r*h/len(coating_rows), widths[c], h/len(coating_rows),
                                width, height, [f"section/{sid}/rows/{r}/{c}"], not value.strip()))
                            left += widths[c]
                elif body is not None:
                    value = _text(body)
                    section["paragraphs"] = [value]
                    refs = [f"section/{sid}/paragraphs/0"]
                    if value.strip() and y < height * .2:
                        title = value.replace("\n", " ")[:100]
                else:
                    rows = table.findall("a:tr", NS)
                    section["rows"] = [[_text(cell.find("a:txBody", NS)) for cell in row.findall("a:tc", NS)] for row in rows]
                    # Empty native grids in the TEM template frame the images;
                    # they are not result tables and must not cover the images
                    # with invisible edit controls.
                    if not any(text.strip() for row in section["rows"] for text in row):
                        continue
                    section["columns"] = [f"{c + 1}열" for c in range(len(section["rows"][0]))]
                    col_widths = [int(col.get("w")) for col in table.findall("a:tblGrid/a:gridCol", NS)]
                    top = y
                    for r, row in enumerate(rows):
                        left, row_height = x, int(row.get("h"))
                        for c, cell in enumerate(row.findall("a:tc", NS)):
                            if cell.get("hMerge") != "1" and cell.get("vMerge") != "1":
                                cell_width = sum(col_widths[c:c + int(cell.get("gridSpan", "1"))])
                                cell_height = sum(int(item.get("h")) for item in rows[r:r + int(cell.get("rowSpan", "1"))])
                                regions.append(_region(left, top, cell_width, cell_height, width, height,
                                    [f"section/{sid}/rows/{r}/{c}"], not section["rows"][r][c].strip()))
                            left += col_widths[c]
                        top += row_height
                if refs:
                    regions.append(_region(x, y, w, h, width, height, refs, not section["paragraphs"][0].strip()))
                sections.append(section)
                bindings[sid] = (name, identity.get("id"), "coating" if coating_rows is not None else "text" if body is not None else "table")
            pages.append({"title": title, "regions": [r for r in regions if r["w"] > 0 and r["h"] > 0]})
    return sections, {"ratio": width / height, "pages": pages}, bindings


def _region(x, y, w, h, width, height, refs, empty):
    left, top = max(0, x), max(0, y)
    return dict(x=left / width, y=top / height, w=max(0, min(width, x+w)-left) / width,
                h=max(0, min(height, y+h)-top) / height, refs=refs, empty=empty)


def _cover_ref(name):
    if name == "Report title":
        return "title"
    if name.startswith("Meta card "):
        index = int(name.rsplit(" ", 1)[1])
        if 0 <= index < len(TEM_FIELDS):
            key = TEM_FIELDS[index][0]
            return key if key == "completionDate" else "transfer/" + key
    return None


def _cover_values(path):
    values = {}
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read(_slides(archive)[0]))
        if root.find("p:cSld", NS).get("name") != COVER_NAME:
            return values  # Existing reports without a cover remain editable unchanged.
        for shape in root.findall("p:cSld/p:spTree/p:sp", NS):
            ref = _cover_ref(shape.find("p:nvSpPr/p:cNvPr", NS).get("name", ""))
            if ref:
                text = _text(shape.find("p:txBody", NS))
                values[ref] = text if ref == "title" else text.partition("\n")[2]
    return values


def initial_model(path, summary):
    sections, _, _ = inspect_pptx(path)
    cover = _cover_values(path)
    counts = [("TEM", "temSampleCount"), ("STEM", "stemSampleCount"),
              ("EDS", "edsReportCount"), ("코팅층", "coatingSampleCount")]
    details = ", ".join(f"{label} {summary.get(key, 0)}건" for label, key in counts if summary.get(key))
    return dict(title=cover.get("title", "TEM/STEM/EDS 분석 보고서"),
        completionDate=cover.get("completionDate", date.today().isoformat()), variables=[],
        transfer={key: cover.get("transfer/" + key, "TEM-EDGE-01" if key == "equipmentCode" else "") for key in TRANSFER_FIELDS},
        sections=sections, auxiliary={"email_subject": "TEM/STEM/EDS 분석 결과 보고서 안내",
        "email_body": "안녕하세요.\n\n요청하신 TEM/STEM/EDS 분석 보고서를 보내드립니다.\n"
            + (f"첨부 보고서는 {details}의 분석 결과를 포함합니다.\n" if details else "")
            + "\n시료별 이미지와 분석 결과를 첨부 보고서에서 확인해 주십시오.\n"
              "추가 확인이 필요한 사항은 담당 실험자에게 문의해 주십시오.\n\n감사합니다."})


def validate_changes(value, source):
    if set(value["transfer"]) != set(TRANSFER_FIELDS):
        raise ValueError("전송 정보 항목이 올바르지 않습니다.")
    for key, text in value["transfer"].items():
        if len(text) > (50 if key == "limsExperimentCode" else 100):
            raise ValueError("전송 정보가 너무 깁니다.")
        value["transfer"][key] = text.strip()
    expected = {item["sectionId"]: item for item in source["sections"]}
    ids = [item["sectionId"] for item in value["sections"]]
    if len(ids) != len(set(ids)) or set(ids) != set(expected):
        raise ValueError("보고서의 페이지 또는 항목이 변경되었습니다. 화면을 다시 여세요.")
    for item in value["sections"]:
        old = expected[item["sectionId"]]
        if len(item["paragraphs"]) != len(old["paragraphs"]) or item["bullets"]:
            raise ValueError("보고서의 문구 구조는 변경할 수 없습니다.")
        if old["rows"] is None:
            if item["rows"] is not None:
                raise ValueError("원본에 없는 표는 추가할 수 없습니다.")
        elif item["rows"] is None or len(item["rows"]) != len(old["rows"]) or any(
                len(a) != len(b) for a, b in zip(item["rows"], old["rows"])):
            raise ValueError("표의 행과 열은 유지하고 셀 내용만 수정하세요.")
        texts = item["paragraphs"] + [t for row in item["rows"] or [] for t in row]
        if any(len(t) > 4000 for t in texts):
            raise ValueError("문구와 표 셀은 4,000자 이하로 입력하세요.")
    if set(value["auxiliary"]) - {"email_subject", "email_body"} or any(len(t) > 4000 for t in value["auxiliary"].values()):
        raise ValueError("메일 문안은 항목당 4,000자 이하로 입력하세요.")
    resolve_changes(value)  # Validate dates and variable references before queuing.
    return value


def _replace_text(body, value):
    if _text(body) == value:
        return
    paragraphs = body.findall("a:p", NS)
    prototype = paragraphs[0] if paragraphs else ET.Element(f"{{{NS['a']}}}p")
    style = prototype.find("a:pPr", NS)
    run_style = prototype.find("a:r/a:rPr", NS)
    end_style = prototype.find("a:endParaRPr", NS)
    for paragraph in paragraphs:
        body.remove(paragraph)
    for line in value.split("\n"):
        p = ET.SubElement(body, f"{{{NS['a']}}}p")
        if style is not None:
            p.append(deepcopy(style))
        run = ET.SubElement(p, f"{{{NS['a']}}}r")
        if run_style is not None:
            run.append(deepcopy(run_style))
        ET.SubElement(run, f"{{{NS['a']}}}t").text = line
        if end_style is not None:
            p.append(deepcopy(end_style))
    props = body.find("a:bodyPr", NS)
    if props is not None:
        for tag in ("noAutofit", "spAutoFit", "normAutofit"):
            for child in props.findall("a:" + tag, NS):
                props.remove(child)
        ET.SubElement(props, f"{{{NS['a']}}}normAutofit")


def revise_pptx(source, target, changes):
    """Stream untouched binary assets; retain geometry, charts and original branding."""
    _, _, bindings = inspect_pptx(source)
    replacements, images = {}, {}
    values = resolve_changes(changes)
    with ZipFile(source) as archive, tempfile.TemporaryDirectory(prefix="rist-tem-table-") as temp:
        for item in values["sections"]:
            name, shape_id, kind = bindings[item["sectionId"]]
            if name not in replacements:
                replacements[name] = ET.fromstring(archive.read(name))
            root = replacements[name]
            for shape in root.findall("p:cSld/p:spTree/*", NS):
                identity = shape.find({"text": "p:nvSpPr/p:cNvPr", "table": "p:nvGraphicFramePr/p:cNvPr", "coating": "p:nvPicPr/p:cNvPr"}[kind], NS)
                if identity is None or identity.get("id") != shape_id:
                    continue
                if kind == "coating":
                    if json.loads(identity.get("descr")[len(COATING_TAG):]) == item["rows"]:
                        continue
                    from ahn.ppt_report import _coating_table_image_path
                    extent = shape.find("p:spPr/a:xfrm/a:ext", NS)
                    image_path = _coating_table_image_path(item["rows"], int(extent.get("cx")), int(extent.get("cy")), Path(temp))
                    media = f"tem-review-{Path(name).stem}-{shape_id}.png"
                    images["ppt/media/" + media] = image_path
                    rel_name = str(Path(name).parent / "_rels" / (Path(name).name + ".rels"))
                    if rel_name not in replacements:
                        replacements[rel_name] = ET.fromstring(archive.read(rel_name))
                    rels = replacements[rel_name]
                    rid = "ristReviewImage" + shape_id
                    rel = next((r for r in rels if r.get("Id") == rid), None)
                    if rel is None:
                        rel = ET.SubElement(rels, f"{{{REL_NS}}}Relationship", Id=rid, Type=NS["r"] + "/image")
                    rel.set("Target", "../media/" + media)
                    shape.find("p:blipFill/a:blip", NS).set(f"{{{NS['r']}}}embed", rid)
                    identity.set("descr", COATING_TAG + json.dumps(item["rows"], ensure_ascii=False))
                elif kind == "text":
                    _replace_text(shape.find("p:txBody", NS), item["paragraphs"][0])
                else:
                    table = shape.find("a:graphic/a:graphicData/a:tbl", NS)
                    for row, texts in zip(table.findall("a:tr", NS), item["rows"]):
                        for cell, text in zip(row.findall("a:tc", NS), texts):
                            _replace_text(cell.find("a:txBody", NS), text)
        # Cover fields share the editor's authoritative title/transfer/date values.
        # Revisions update the existing cover; they never insert another page.
        first_slide = _slides(archive)[0]
        root = replacements.get(first_slide)
        if root is None:
            root = ET.fromstring(archive.read(first_slide))
        if root.find("p:cSld", NS).get("name") == COVER_NAME:
            for shape in root.findall("p:cSld/p:spTree/p:sp", NS):
                name = shape.find("p:nvSpPr/p:cNvPr", NS).get("name", "")
                ref = _cover_ref(name)
                if ref == "title":
                    text = values["title"]
                elif ref:
                    key, label = TEM_FIELDS[int(name.rsplit(" ", 1)[1])]
                    text = label + "\n" + (values.get(key, "") if key == "completionDate" else values["transfer"][key])
                else:
                    continue
                _replace_text(shape.find("p:txBody", NS), text)
            replacements[first_slide] = root
        with ZipFile(target, "w") as output:
            for info in archive.infolist():
                if info.filename in images:
                    continue
                if info.filename in replacements:
                    output.writestr(info, ET.tostring(replacements[info.filename], encoding="utf-8", xml_declaration=True))
                elif info.filename == "docProps/core.xml":
                    core = ET.fromstring(archive.read(info))
                    title = core.find("{http://purl.org/dc/elements/1.1/}title")
                    if title is None:
                        title = ET.SubElement(core, "{http://purl.org/dc/elements/1.1/}title")
                    title.text = values["title"]
                    output.writestr(info, ET.tostring(core, encoding="utf-8", xml_declaration=True))
                else:
                    with archive.open(info) as src, output.open(info, "w") as dst:
                        shutil.copyfileobj(src, dst, 1024 * 1024)
            for name, path in images.items():
                output.write(path, name)

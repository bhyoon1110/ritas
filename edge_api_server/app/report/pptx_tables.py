"""Native DrawingML tables shared by spectrum rendering and author editing."""
from xml.etree import ElementTree as ET

NS = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
TABLE_PATH = "a:graphic/a:graphicData/a:tbl"


def table_cell(*, text, fill, line, font_size, color, bold=False, anchor="t",
               inset=68580, align="left", line_spacing_pct=108000,
               space_after_pts=100, row_span=1, covered=False):
    from .renderers import _pptx_paragraphs, _pptx_fill
    attrs = ' vMerge="1"' if covered else f' rowSpan="{row_span}"' if row_span > 1 else ""
    paragraphs = _pptx_paragraphs(text, font_size=font_size, color=color, bold=bold,
                                 align=align, line_spacing_pct=line_spacing_pct,
                                 space_after_pts=space_after_pts)
    borders = "".join(f'<a:ln{side} w="9525">{_pptx_fill(line)}<a:prstDash val="solid"/></a:ln{side}>'
                      for side in ("L", "R", "T", "B"))
    return (f'<a:tc{attrs}><a:txBody><a:bodyPr wrap="square"><a:normAutofit/></a:bodyPr>'
            f'<a:lstStyle/>{paragraphs}</a:txBody>'
            f'<a:tcPr marL="{inset}" marR="{inset}" marT="{inset}" marB="{inset}" '
            f'anchor="{"ctr" if anchor == "mid" else anchor}">{borders}{_pptx_fill(fill)}</a:tcPr></a:tc>')


def table_frame(shape_id, name, x, y, widths, rows):
    from html import escape
    grid = "".join(f'<a:gridCol w="{w}"/>' for w in widths)
    body = "".join(f'<a:tr h="{h}">{"".join(cells)}</a:tr>' for h, cells in rows)
    return (f'<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="{shape_id}" name="{escape(name, quote=True)}"/>'
            '<p:cNvGraphicFramePr><a:graphicFrameLocks noGrp="1"/></p:cNvGraphicFramePr><p:nvPr/></p:nvGraphicFramePr>'
            f'<p:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{sum(widths)}" cy="{sum(h for h, _ in rows)}"/></p:xfrm>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table">'
            f'<a:tbl><a:tblPr/><a:tblGrid>{grid}</a:tblGrid>{body}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>')


def cells(frame):
    """Yield visible cells and their actual rectangle, including merged spans."""
    table = frame.find(TABLE_PATH, NS)
    if table is None:
        return
    off = frame.find("p:xfrm/a:off", NS)
    widths = [int(c.get("w")) for c in table.findall("a:tblGrid/a:gridCol", NS)]
    rows = table.findall("a:tr", NS)
    heights = [int(row.get("h")) for row in rows]
    y = int(off.get("y"))
    for ri, row in enumerate(rows):
        x = int(off.get("x"))
        for ci, cell in enumerate(row.findall("a:tc", NS)):
            if cell.get("vMerge") not in {"1", "true"} and cell.get("hMerge") not in {"1", "true"}:
                yield ri, ci, cell, (x, y, sum(widths[ci:ci + int(cell.get("gridSpan", 1))]),
                                     sum(heights[ri:ri + int(cell.get("rowSpan", 1))]))
            x += widths[ci]
        y += heights[ri]


def resize(frame, width, height):
    table = frame.find(TABLE_PATH, NS)
    for nodes, attr, total in ((table.findall("a:tblGrid/a:gridCol", NS), "w", width),
                               (table.findall("a:tr", NS), "h", height)):
        previous = sum(int(n.get(attr)) for n in nodes)
        sizes = [max(1, round(int(n.get(attr)) * total / previous)) for n in nodes]
        sizes[-1] += total - sum(sizes)
        for node, size in zip(nodes, sizes):
            node.set(attr, str(size))


def text(cell):
    return "\n".join("".join(t.text or "" for t in p.findall(".//a:t", NS))
                     for p in cell.findall("a:txBody/a:p", NS))


def set_text(cell, value):
    """Keep the cell's typography; never convert a table cell to a shape."""
    from copy import deepcopy
    body = cell.find("a:txBody", NS)
    first = body.find("a:p", NS)
    ppr = first.find("a:pPr", NS) if first is not None else None
    rpr = cell.find(".//a:rPr", NS)
    for p in body.findall("a:p", NS):
        body.remove(p)
    for line in value.split("\n"):
        p = ET.SubElement(body, "{" + NS["a"] + "}p")
        if ppr is not None:
            p.append(deepcopy(ppr))
        run = ET.SubElement(p, "{" + NS["a"] + "}r")
        if rpr is not None:
            run.append(deepcopy(rpr))
        ET.SubElement(run, "{" + NS["a"] + "}t").text = line

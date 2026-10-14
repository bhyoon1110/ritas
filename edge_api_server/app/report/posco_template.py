"""Reuse the bundled TEM template's original POSCO banner, without sample data.

Spectrum reports keep their existing 16:9 content geometry. Only the decorative
banner's width changes; its crop, horizontal flip and source pixels are retained.
No slide payload, notes, sample images or fixed '/ 22' footer is imported.
"""

from functools import lru_cache
from pathlib import Path
from zipfile import ZipFile

from lxml import etree as ET


TEMPLATE_PATH = Path(__file__).resolve().parents[3] / "ahn/resources/templates/ahn_tem_template.pptx"
MEDIA_NAME = "posco-tem-banner.jpeg"
NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
SPECTRUM_CODES = frozenset({"FTIR", "FT-IR", "IR", "RAMAN", "RIN", "RIN-RAMAN"})


def uses_posco_template(experiment_code: str) -> bool:
    return experiment_code.strip().upper() in SPECTRUM_CODES


@lru_cache(maxsize=1)
def load_posco_layout(slide_width: int) -> tuple[bytes, bytes]:
    """Return a background-only layout and the unmodified original JPEG."""
    with ZipFile(TEMPLATE_PATH) as source:
        layout = ET.fromstring(source.read("ppt/slideLayouts/slideLayout1.xml"))
        tree = layout.find("p:cSld/p:spTree", NS)
        picture = tree.find("p:pic", NS)
        if picture is None:
            raise ValueError("TEM 포스코 양식에서 배경 이미지를 찾을 수 없습니다.")
        blip = picture.find("p:blipFill/a:blip", NS)
        relationship_id = blip.get("{%s}embed" % NS["r"])
        relationships = ET.fromstring(source.read("ppt/slideLayouts/_rels/slideLayout1.xml.rels"))
        image_relationship = next(rel for rel in relationships if rel.get("Id") == relationship_id)
        # The bundled template is trusted; allow only its known banner asset.
        if image_relationship.get("Target") != "../media/image1.jpeg":
            raise ValueError("TEM 포스코 양식의 배경 이미지 경로가 변경되었습니다.")
        image = source.read("ppt/media/image1.jpeg")

    for shape in list(tree):
        if shape is not picture and shape.tag not in {
            "{%s}nvGrpSpPr" % NS["p"], "{%s}grpSpPr" % NS["p"],
        }:
            tree.remove(shape)
    # Refer to the separately named media so report figure image1.jpeg cannot
    # replace the banner. The renderer supplies this layout's relationships.
    blip.set("{%s}embed" % NS["r"], "rId2")
    transform = picture.find("p:spPr/a:xfrm", NS)
    transform.find("a:off", NS).set("x", "0")
    transform.find("a:off", NS).set("y", "0")
    transform.find("a:ext", NS).set("cx", str(slide_width))
    picture.find("p:nvPicPr/p:cNvPr", NS).set("name", "POSCO TEM background")
    layout.find("p:cSld", NS).set("name", "POSCO spectrum report")
    return ET.tostring(layout, xml_declaration=True, encoding="UTF-8", standalone=True), image


def posco_layout_rels() -> str:
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{REL_NS}">
  <Relationship Id="rId1" Type="{NS['r']}/slideMaster" Target="../slideMasters/slideMaster1.xml"/>
  <Relationship Id="rId2" Type="{NS['r']}/image" Target="../media/{MEDIA_NAME}"/>
</Relationships>'''

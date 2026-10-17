"""Original POSCO/RIST artwork shared by TEM and spectrum report generators.

Assets are byte-for-byte copies of the supplied XRD deck's master images.
Keep them with AHN's report resources so standalone AHN needs no Edge/XRD import.
The banner adapts to the page width; the RIST logo keeps its original proportions.
"""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from xml.etree import ElementTree as ET


ASSET_DIR = Path(__file__).resolve().parent / "resources/branding"
BANNER_NAME = "posco-banner.jpeg"
LOGO_NAME = "rist-logo.png"
BANNER_HEIGHT = 659027
BANNER_CROP_BOTTOM = 88460
NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


@dataclass(frozen=True)
class BrandPicture:
    name: str
    media: str
    x: int
    y: int
    width: int
    height: int
    crop_bottom: int = 0

    def xml(self, shape_id: int, relationship_id: str) -> bytes:
        # Native picture geometry from the source master, not a redrawn logo.
        return f'''<p:pic xmlns:p="{NS['p']}" xmlns:a="{NS['a']}" xmlns:r="{NS['r']}">
<p:nvPicPr><p:cNvPr id="{shape_id}" name="{self.name}"/><p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>
<p:blipFill><a:blip r:embed="{relationship_id}"/><a:srcRect b="{self.crop_bottom}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>
<p:spPr><a:xfrm><a:off x="{self.x}" y="{self.y}"/><a:ext cx="{self.width}" cy="{self.height}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr>
</p:pic>'''.encode("utf-8")


def brand_pictures(width: int, height: int) -> tuple[BrandPicture, ...]:
    return (
        BrandPicture("POSCO background", BANNER_NAME, 0, 0, width, BANNER_HEIGHT, BANNER_CROP_BOTTOM),
        BrandPicture("RIST logo", LOGO_NAME, 68429, height - 227174, 489993, 147717),
    )


@lru_cache(maxsize=1)
def brand_assets() -> dict[str, bytes]:
    return {name: (ASSET_DIR / name).read_bytes() for name in (BANNER_NAME, LOGO_NAME)}


def brand_layout(width: int, height: int) -> bytes:
    layout = ET.fromstring(f'''<p:sldLayout xmlns:p="{NS['p']}" xmlns:a="{NS['a']}" type="blank" preserve="1">
<p:cSld name="POSCO RIST report"><p:spTree>
<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>
</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>''')
    tree = layout.find("p:cSld/p:spTree", NS)
    for i, picture in enumerate(brand_pictures(width, height), 2):
        tree.append(ET.fromstring(picture.xml(i, f"rId{i}")))
    return ET.tostring(layout, encoding="UTF-8", xml_declaration=True)

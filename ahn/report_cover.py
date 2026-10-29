"""FTIR/Raman cover geometry shared by the four report generators.

Only the cover uses this layout. Page dimensions and every experiment's body
layouts remain unchanged; the cover scales to the existing canvas.
"""
from html import escape
from math import ceil

from .report_branding import NS

COVER_NAME = "RIST report cover"
WIDTH, HEIGHT = 12192000, 6858000
TEM_FIELDS = (("requestNumber", "의뢰번호"), ("limsExperimentCode", "실험코드"),
              ("equipmentCode", "실험장비"), ("operatorId", "실험자"),
              ("completionDate", "완료일"))

SUPPLIED_COVER_MEDIA = "posco-cover-20261029.jpg"
SUPPLIED_COVER_NAME = "Template POSCO background"


def supplied_cover_asset():
    from .report_branding import ASSET_DIR
    return (ASSET_DIR / SUPPLIED_COVER_MEDIA).read_bytes()


def supplied_cover_picture(width=WIDTH, height=HEIGHT):
    from .report_branding import BrandPicture
    return BrandPicture(SUPPLIED_COVER_NAME, SUPPLIED_COVER_MEDIA, 0, 0, width, height)


def supplied_cover_logo(width=WIDTH, height=HEIGHT):
    from dataclasses import replace
    from .report_branding import brand_pictures
    # Existing protected names keep the common editor's policy unchanged.
    return replace(brand_pictures(width, height)[1], name="Template RIST logo")


def cover_shapes(experiment, title, subtitle, metadata, width=WIDTH, height=HEIGHT, *, columns=4):
    """Return the spectrum cover's native editable shape specifications."""
    specs = [
        dict(shape_id=2, name="Experiment banner title", x=254000, y=160020,
             cx=8500000, cy=400050, text=[experiment + " 분석 결과"],
             font_size=2000, color="172B4D", bold=True, inset=0),
        dict(shape_id=3, name="Report eyebrow", x=762000, y=777240,
             cx=7620000, cy=365760, text=["분석 결과 보고서"],
             font_size=1700, color="2F80ED", bold=True, inset=0),
        dict(shape_id=4, name="Report title", x=762000, y=1219200,
             cx=9753600, cy=1219200, text=[title],
             font_size=5000, color="172B4D", bold=True, inset=0),
        dict(shape_id=5, name="Report subtitle", x=762000, y=2438400,
             cx=9601200, cy=609600, text=[subtitle],
             font_size=2000, color="627D98", inset=0),
    ]
    rows = max(1, ceil(len(metadata) / columns))
    top = 3200000 if columns == 2 else 3962400
    gap = 50000 if columns == 2 else 152400 if rows <= 2 else 100000
    card_w = 4800600 if columns == 2 else 2057400
    bottom = 6500000 if columns == 2 else 6400000
    card_h = min(822960, (bottom - top - gap * (rows - 1)) // rows)
    for index, (label, value) in enumerate(metadata):
        specs.append(dict(shape_id=10 + index, name=f"Meta card {index}",
            x=762000 + index % columns * (card_w + 152400),
            y=top + index // columns * (card_h + gap), cx=card_w, cy=card_h,
            fill="FFFFFF", line="D9E2EC", text=[label, str(value or "")],
            font_size=1250, color="243B53", inset=45720 if columns == 2 else 91440,
            line_spacing_pct=108000, space_after_pts=100))
    for spec in specs:
        for key in ("x", "cx"):
            spec[key] = round(spec[key] * width / WIDTH)
        for key in ("y", "cy"):
            spec[key] = round(spec[key] * height / HEIGHT)
        scale = min(width / WIDTH, height / HEIGHT)
        spec["font_size"] = round(spec["font_size"] * scale)
        spec["inset"] = round(spec["inset"] * scale)
    return specs


def shape_xml(spec):
    """Same DrawingML typography as the existing spectrum cover, no raster text."""
    def fill(color):
        return f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>' if color else '<a:noFill/>'

    paragraphs = []
    for value in spec["text"]:
        paragraphs.append(
            f'<a:p><a:pPr marL="0" indent="0" algn="l"><a:lnSpc><a:spcPct val="{spec.get("line_spacing_pct", 125000)}"/>'
            f'</a:lnSpc><a:spcAft><a:spcPts val="{spec.get("space_after_pts", 500)}"/></a:spcAft></a:pPr>'
            f'<a:r><a:rPr lang="ko-KR" sz="{spec["font_size"]}" b="{int(spec.get("bold", False))}">'
            f'{fill(spec["color"])}<a:latin typeface="Arial"/><a:ea typeface="맑은 고딕"/>'
            f'</a:rPr><a:t>{escape(str(value))}</a:t></a:r></a:p>')
    inset = spec["inset"]
    return (f'<p:sp xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:nvSpPr>'
        f'<p:cNvPr id="{spec["shape_id"]}" name="{escape(spec["name"])}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
        f'<p:spPr><a:xfrm><a:off x="{spec["x"]}" y="{spec["y"]}"/><a:ext cx="{spec["cx"]}" cy="{spec["cy"]}"/></a:xfrm>'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>{fill(spec.get("fill"))}'
        f'<a:ln w="9525">{fill(spec.get("line"))}</a:ln></p:spPr>'
        f'<p:txBody><a:bodyPr wrap="square" anchor="t" rtlCol="0" lIns="{inset}" tIns="{inset}" rIns="{inset}" bIns="{inset}">'
        '<a:normAutofit fontScale="90000" lnSpcReduction="0"/></a:bodyPr><a:lstStyle/>'
        + ''.join(paragraphs) + '</p:txBody></p:sp>').encode('utf-8')

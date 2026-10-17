"""POSCO background and RIST logo for the existing 16:9 spectrum reports."""

from functools import lru_cache
from base64 import b64encode
from ahn.report_branding import BANNER_NAME, LOGO_NAME, NS, brand_assets, brand_layout

MEDIA_NAME = BANNER_NAME
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
SPECTRUM_CODES = frozenset({"FTIR", "FT-IR", "IR", "RAMAN", "RIN", "RIN-RAMAN"})


def uses_posco_template(experiment_code: str) -> bool:
    return experiment_code.strip().upper() in SPECTRUM_CODES


@lru_cache(maxsize=4)
def load_posco_layout(slide_width: int, slide_height: int) -> tuple[bytes, dict[str, bytes]]:
    return brand_layout(slide_width, slide_height), brand_assets()


def posco_layout_rels() -> str:
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{REL_NS}">
  <Relationship Id="rId1" Type="{NS['r']}/slideMaster" Target="../slideMasters/slideMaster1.xml"/>
  <Relationship Id="rId2" Type="{NS['r']}/image" Target="../media/{MEDIA_NAME}"/>
  <Relationship Id="rId3" Type="{NS['r']}/image" Target="../media/{LOGO_NAME}"/>
</Relationships>'''


def html_branding() -> tuple[str, str]:
    """Self-contained branding for the non-slide HTML export path."""
    assets = brand_assets()
    banner = b64encode(assets[BANNER_NAME]).decode("ascii")
    logo = b64encode(assets[LOGO_NAME]).decode("ascii")
    header = (
        '<div class="report-brand-banner" aria-hidden="true">'
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        'viewBox="0 0 2000 173.1" preserveAspectRatio="none" width="100%" height="70">'
        f'<image xlink:href="data:image/jpeg;base64,{banner}" width="2000" height="1500"/>'
        '</svg></div>'
    )
    footer = f'<footer><img alt="RIST" width="53" height="17" src="data:image/png;base64,{logo}"></footer>'
    return header, footer

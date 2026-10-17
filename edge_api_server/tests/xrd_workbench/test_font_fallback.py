"""Characters the primary font lacks are drawn with a bundled fallback instead of empty boxes."""

import io

from pypdf import PdfReader

from app.xrd_workbench.core.report import new_document
from app.xrd_workbench.render import fonts
from app.xrd_workbench.render.layout import build_pages
from app.xrd_workbench.render.pdf import render_pdf

# Subscript digits, Latin-Extended letters, Greek, an arrow and a math operator.
SAMPLE = 'Al₂O₃ Ti₀₅ Š ł α β → ∼ ± ≈ Å'
LACKING = 'Al₂O₃ Š ł ∼'


def test_segments_split_text_by_font_coverage(monkeypatch):
    for key in ('XRD_FONT_REGULAR', 'XRD_FONT_BOLD', 'XRD_KOREAN_FONT', 'XRD_KOREAN_BOLD_FONT'):
        monkeypatch.delenv(key, raising=False)
    regular, _ = fonts.font_names()
    parts = fonts.segments('한글 Al₂O₃', bold=False)
    assert ''.join(text for _, text in parts) == '한글 Al₂O₃'
    assert parts[0][0] == regular
    assert any(name != regular and '₂' in text for name, text in parts)


def test_width_includes_fallback_glyphs(monkeypatch):
    for key in ('XRD_FONT_REGULAR', 'XRD_FONT_BOLD', 'XRD_KOREAN_FONT', 'XRD_KOREAN_BOLD_FONT'):
        monkeypatch.delenv(key, raising=False)
    assert fonts.text_width('₂₃', 10, bold=False) > 0
    assert fonts.text_width('Al₂O₃', 10, bold=False) > fonts.text_width('AlO', 10, bold=False)


def test_pdf_text_keeps_every_character(monkeypatch):
    for key in ('XRD_FONT_REGULAR', 'XRD_FONT_BOLD', 'XRD_KOREAN_FONT', 'XRD_KOREAN_BOLD_FONT'):
        monkeypatch.delenv(key, raising=False)
    from app.xrd_workbench.core.project import build_project
    from app.xrd_workbench.core.review import apply_review

    txt = '#Intensity_unit=cps\n10 1\n11 2\n'.encode()
    project = apply_review(build_project([('시료A.txt', txt)]))
    document = new_document(project)
    document['revision'] = 1
    document['samples'][0]['manual_note'] = SAMPLE
    sources = {r['id']: txt for r in project['sources']}
    pages = build_pages(project, document, sources.__getitem__)
    text = ''.join(
        p.extract_text() or '' for p in PdfReader(io.BytesIO(render_pdf(pages, document))).pages
    )
    for char in LACKING.replace(' ', ''):
        assert char in text, f'{char!r} was not drawn'


def test_pptx_declares_the_fallback_family_for_characters_the_primary_font_lacks(monkeypatch):
    import zipfile
    from xml.etree import ElementTree as ET

    from app.xrd_workbench.core.project import build_project
    from app.xrd_workbench.core.review import apply_review
    from app.xrd_workbench.render.pptx import render_pptx

    for key in ('XRD_FONT_REGULAR', 'XRD_FONT_BOLD', 'XRD_KOREAN_FONT', 'XRD_PPTX_FONT_FAMILY'):
        monkeypatch.delenv(key, raising=False)
    txt = '#Intensity_unit=cps\n10 1\n11 2\n'.encode()
    project = apply_review(build_project([('시료A.txt', txt)]))
    document = new_document(project)
    document['revision'] = 1
    document['samples'][0]['manual_note'] = '화학식 Al₂O₃ 확인'
    sources = {r['id']: txt for r in project['sources']}
    pages = build_pages(project, document, sources.__getitem__)
    ns = {'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}
    with zipfile.ZipFile(io.BytesIO(render_pptx(pages, document))) as deck:
        families = {}
        for name in deck.namelist():
            if name.startswith('ppt/slides/slide') and name.endswith('.xml'):
                for run in ET.fromstring(deck.read(name)).iter(f'{{{ns["a"]}}}r'):
                    text = run.find('a:t', ns).text or ''
                    latin = run.find('a:rPr/a:latin', ns)
                    for char in text:
                        families.setdefault(char, set()).add(latin.attrib['typeface'])
    assert families['₂'] == {'Noto Sans'} and families['₃'] == {'Noto Sans'}
    assert families['한'] == {'Noto Sans KR'} and families['A'] == {'Noto Sans KR'}

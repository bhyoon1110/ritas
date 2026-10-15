"""Real PDF checks when a local Chrome renderer is available (not XP validation)."""
from __future__ import annotations

from io import BytesIO

import pytest

from app import xrd_web
from lim.xrd_plot import xrd_print_page_css, xrd_report_css


@pytest.mark.parametrize("landscape", [False, True])
def test_print_rules_fix_portrait_text_width_without_global_first_page(landscape):
    css = xrd_print_page_css(landscape=landscape)
    assert "@page:first" not in css
    assert ".xrd-report-page > .xrd-report-section" in css
    assert "page: xrd-report-portrait !important" in css
    assert "width: 190mm !important" in css
    assert "margin: 0 !important" in css
    assert "overflow: visible !important" in css
    assert ("width: 277mm !important" in css) is landscape
    assert ("page: xrd-graph-landscape !important" in css) is landscape
    assert "transform:" not in css
    assert "zoom:" not in css


@pytest.mark.parametrize("server_css", [True, False], ids=["server", "standalone-html"])
@pytest.mark.parametrize("with_image", [True, False], ids=["with-image", "graph-first"])
def test_rendered_pdf_keeps_text_geometry_when_graph_is_landscape(tmp_path, monkeypatch, server_css, with_image):
    pdfplumber = pytest.importorskip("pdfplumber")
    if not xrd_web._find_xrd_pdf_chrome():
        pytest.skip("Chrome/Chromium is needed for real PDF layout validation")
    monkeypatch.setenv("RIST_XRD_PDF_RENDER_DIR", str(tmp_path / "renders"))
    if not server_css:
        # Exercise the CSS shipped in the offline HTML, without server overrides.
        monkeypatch.setattr(xrd_web, "_inject_xrd_print_page_css", lambda source, **_: source)
    graphic = '<svg viewBox="0 0 1000 300" style="width:100%;height:auto"><rect x="1" y="1" width="998" height="298" fill="white" stroke="black"/></svg>'
    paragraphs = "".join(
        f"<p>Line {i:02}. XRD analysis uses measured peaks and reference cards. "
        "The text must retain its font size, width and line wrapping in both orientations. "
        "The original measurements and phase information remain unchanged.</p>"
        for i in range(24)
    )
    image_section = (
        '<section id="xrd-image-info" class="xrd-report-section"><h2>Image page</h2>' + graphic + '</section>'
        if with_image else ""
    )
    documents = []
    for landscape in (False, True):
        body_class = "xrd-report-graph-landscape" if landscape else ""
        source = (
            '<html><head><meta charset="utf-8">' + xrd_report_css() + '</head>'
            f'<body class="{body_class}"><main class="xrd-report-page">' + image_section
            + '<section id="xrd-graph-section" class="xrd-report-section"><h2>Graph page</h2><div class="xrd-graph-frame">' + graphic + '</div></section>'
            + '<section id="xrd-llm-comment" class="xrd-report-section"><h2>Analysis text</h2><div class="xrd-comment-box">' + paragraphs + '</div></section>'
            + '<section id="xrd-element-guidance" class="xrd-report-section"><h2>Guidance</h2><p>Guidance stays in portrait width.</p></section>'
            + '<section id="xrd-peak-info" class="xrd-report-section"><h2>Peak information</h2><p>Source peaks.</p></section>'
            + '<section id="xrd-phase-info" class="xrd-report-section"><h2>Phase information</h2><p>Reference phases.</p></section>'
            + '</main></body></html>'
        )
        data = xrd_web._render_xrd_html_pdf(source, landscape=landscape)
        with pdfplumber.open(BytesIO(data)) as pdf:
            pages = [(p.width, p.height, p.extract_text() or "", p.chars) for p in pdf.pages]
        start = next(i for i, p in enumerate(pages) if "Analysis text" in p[2])
        text_pages = pages[start:]
        assert len(text_pages) >= 3  # Includes flowing paragraphs and tables/phase sections.
        for width, height, text, chars in text_pages:
            assert width == pytest.approx(595, abs=1)
            assert height == pytest.approx(842, abs=1)
            assert text.strip()  # No blank page on orientation transitions.
            assert min(c["x0"] for c in chars) >= 27
            assert max(c["x1"] for c in chars) <= width - 27
        graph = next(p for p in pages if "Graph page" in p[2])
        assert (graph[0] > graph[1]) is landscape
        if with_image:
            image = next(p for p in pages if "Image page" in p[2])
            assert (image[0] > image[1]) is landscape
        assert "Line 23." in "\n".join(p[2] for p in text_pages)
        assert "Guidance" in "\n".join(p[2] for p in text_pages)
        documents.append(text_pages)
    portrait, landscape = documents
    assert len(portrait) == len(landscape)
    for first, second in zip(portrait, landscape):
        assert first[2] == second[2]  # Identical line breaks and page breaks.
        assert len(first[3]) == len(second[3])
        for a, b in zip(first[3], second[3]):
            assert a["text"] == b["text"]
            for metric in ("x0", "x1", "top", "bottom", "size", "width", "height"):
                assert a[metric] == pytest.approx(b[metric], abs=0.05), metric

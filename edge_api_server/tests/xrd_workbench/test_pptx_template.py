"""A company slide template (a .pptx/.potx the author keeps outside the repository) can replace
the temporary look: its slide master supplies the design, our title band is not drawn, and the
content keeps its place scaled to the template's slide size."""

import io

import pytest
from pptx import Presentation
from pptx.util import Mm
from .test_sample_slide import build, sample

from app.xrd_workbench.config import load_settings
from app.xrd_workbench.render.pptx import render_pptx
from app.xrd_workbench.render.theme import PAGE_H, PAGE_W


def make_template(tmp_path, width_mm=254, height_mm=143, with_slides=2, name='company.pptx'):
    deck = Presentation()
    deck.slide_width, deck.slide_height = Mm(width_mm), Mm(height_mm)
    # The company's own blank layout (renamed so the test can recognise it).
    deck.slide_layouts[6]._element.cSld.set('name', 'Company blank')
    for _ in range(with_slides):
        deck.slides.add_slide(deck.slide_layouts[1])  # title + body placeholders
    path = tmp_path / name
    deck.save(path)
    return path


def slides(data):
    return list(Presentation(io.BytesIO(data)).slides)


def names(slide):
    return [shape.name for shape in slide.shapes]


def test_without_a_template_the_temporary_look_is_unchanged():
    pages, document = build([sample('a')])
    deck = Presentation(io.BytesIO(render_pptx(pages, document, template=None)))
    assert deck.slide_width == Mm(PAGE_W) and deck.slide_height == Mm(PAGE_H)
    assert any(n.startswith('Band') for n in names(deck.slides[1]))


def test_the_template_master_replaces_the_look_and_its_own_slides_are_dropped(tmp_path):
    path = make_template(tmp_path)
    pages, document = build([sample('a')])
    data = render_pptx(pages, document, template=path)
    deck = Presentation(io.BytesIO(data))
    assert len(deck.slides) == len(pages)
    assert deck.slide_width == Mm(254)
    assert all(slide.slide_layout.name == 'Company blank' for slide in deck.slides)
    for slide in deck.slides:
        assert not any(n.startswith('Band') for n in names(slide))
        assert not slide.placeholders, 'no empty placeholder is left on a slide'
    # The sample slide keeps its text, now scaled into the template's page.
    body = ' '.join(
        shape.text_frame.text for shape in deck.slides[1].shapes if shape.has_text_frame
    )
    assert '상 동정' in body or 'Phase Identification' in body


def test_content_is_scaled_to_the_template_slide_size(tmp_path):
    path = make_template(tmp_path, width_mm=254, height_mm=143)
    pages, document = build([sample('a')])
    plain = Presentation(io.BytesIO(render_pptx(pages, document, template=None)))
    scaled = Presentation(io.BytesIO(render_pptx(pages, document, template=path)))
    for before, after in zip(plain.slides[0].shapes, scaled.slides[0].shapes, strict=False):
        if before.name.startswith('Text'):
            assert after.left == pytest.approx(before.left * 254 / PAGE_W, abs=5000)
            assert after.top == pytest.approx(before.top * 143 / PAGE_H, abs=5000)
            break
    else:
        raise AssertionError('no text on the cover')


def test_a_named_layout_is_used_and_a_missing_name_is_a_message(tmp_path):
    path = make_template(tmp_path)
    pages, document = build([sample('a')])
    data = render_pptx(pages, document, template=path, template_layout='Title Only')
    assert slides(data)[0].slide_layout.name == 'Title Only'
    with pytest.raises(ValueError, match='레이아웃'):
        render_pptx(pages, document, template=path, template_layout='없는 레이아웃')


def test_a_missing_or_broken_template_is_a_message_not_a_traceback(tmp_path):
    pages, document = build([sample('a')])
    with pytest.raises(ValueError, match='양식'):
        render_pptx(pages, document, template=tmp_path / 'nope.pptx')
    bad = tmp_path / 'bad.pptx'
    bad.write_bytes(b'not a deck')
    with pytest.raises(ValueError, match='양식'):
        render_pptx(pages, document, template=bad)


def test_the_template_comes_from_the_settings(monkeypatch, tmp_path):
    monkeypatch.delenv('XRD_PPTX_TEMPLATE', raising=False)
    assert load_settings().pptx_template is None
    monkeypatch.setenv('XRD_PPTX_TEMPLATE', str(tmp_path / 't.pptx'))
    monkeypatch.setenv('XRD_PPTX_LAYOUT', 'Blank')
    settings = load_settings()
    assert settings.pptx_template == tmp_path / 't.pptx' and settings.pptx_layout == 'Blank'


def test_a_template_of_another_shape_keeps_proportions_and_centres_the_content(tmp_path):
    path = make_template(tmp_path, width_mm=254, height_mm=190.5)  # 4:3
    pages, document = build([sample('a', images=True)])
    plain = Presentation(io.BytesIO(render_pptx(pages, document, template=None)))
    fitted = Presentation(io.BytesIO(render_pptx(pages, document, template=path)))
    slide = 2  # a slide with the two source pictures
    for before, after in zip(
        [s for s in plain.slides[slide].shapes if s.shape_type == 13],
        [s for s in fitted.slides[slide].shapes if s.shape_type == 13],
        strict=True,
    ):
        assert after.width / after.height == pytest.approx(before.width / before.height, rel=0.01)
    assert all(s.left >= 0 and s.top >= 0 for s in fitted.slides[slide].shapes)


def test_a_bad_title_colour_or_a_template_without_layouts_is_a_message(monkeypatch, tmp_path):
    monkeypatch.setenv('XRD_PPTX_TITLE_COLOR', 'white')
    with pytest.raises(ValueError, match='XRD_PPTX_TITLE_COLOR'):
        load_settings()
    monkeypatch.setenv('XRD_PPTX_TITLE_COLOR', '#FFF')
    with pytest.raises(ValueError, match='XRD_PPTX_TITLE_COLOR'):
        load_settings()
    monkeypatch.setenv('XRD_PPTX_TITLE_COLOR', '#336699')
    assert load_settings().pptx_title_color == '#336699'

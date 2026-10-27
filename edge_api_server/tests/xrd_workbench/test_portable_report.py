"""Recipient files work without neighboring filesystem access or editable PPT content."""
import base64
import hashlib
import io
import json
from html.parser import HTMLParser
from zipfile import ZipFile

from lxml import etree as E
from PIL import Image
from pptx import Presentation
import pytest

from app.xrd_workbench.core.project import build_project
from app.xrd_workbench.core.report import new_document
from app.xrd_workbench.render import portable_html
from app.xrd_workbench.render.package import build_package
from app.xrd_workbench.render.posco_pptx import render_report_pptx, NS
from app.xrd_workbench.render.link_audit import ppt_click_targets_by_slide
from app.xrd_workbench.render.delivery_pptx import flatten_pptx
from app.xrd_workbench.render.pdf import render_pdf
from app.xrd_workbench.render.posco_preview import report_pages
from .synthetic import sample_files


class Payload(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.active = False
        self.text = ''
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.active = tag == 'script' and dict(attrs).get('id') == 'xrd-portable-data'

    def handle_data(self, value):
        if self.active: self.text += value

    def handle_endtag(self, tag):
        if tag == 'script': self.active = False


@pytest.fixture(scope='module')
def report():
    originals = sample_files('SampleA')
    project = build_project(list(originals.items()))
    doc = new_document(project)
    records = {r['id']:r for r in project['sources']}
    source = lambda fid: originals[records[fid]['filename']]
    return project, doc, source


@pytest.fixture(scope='module')
def package(report):
    return build_package(*report)


def assert_flat(content):
    presentation = Presentation(io.BytesIO(content))
    assert len(presentation.slides) > 0
    with ZipFile(io.BytesIO(content)) as archive:
        assert not any(n.startswith(('ppt/charts/', 'ppt/embeddings/', 'ppt/notesSlides/')) for n in archive.namelist())
        for name in archive.namelist():
            if name.startswith('ppt/slides/slide') and name.endswith('.xml'):
                slide = E.fromstring(archive.read(name))
                assert not slide.findall('.//a:t', NS)
                assert not slide.findall('.//a:tbl', NS)
                assert len(slide.findall('.//p:pic', NS)) == 1
    return presentation


def test_package_embeds_every_original_and_download_byte_for_byte(package):
    with ZipFile(io.BytesIO(package)) as archive:
        payload = json.loads(Payload(archive.read('보고서.html').decode()).text)
        for name in archive.namelist():
            if name.endswith('/'): continue
            if name.startswith(('Report/', 'Measured Data/')) or name in ('보고서.pdf','보고서.pptx'):
                entry = payload['files'][name]
                assert base64.b64decode(entry['data']) == archive.read(name)
                if name.endswith('.pdf'):
                    assert entry['pages'] and not entry.get('notice')
                    for image in entry['pages']:
                        with Image.open(io.BytesIO(base64.b64decode(image))) as page:
                            assert max(page.size) <= 1801
                elif name.endswith('.txt'): assert entry['text']
        assert payload['viewer'] == archive.read('뷰어.html').decode()
        assert payload['samples']
        assert_flat(archive.read('보고서.pptx'))


def test_delivery_keeps_exact_per_page_links_without_native_analysis_objects(report):
    native = render_report_pptx(*report)
    delivered = render_report_pptx(*report, delivery=True)
    assert_flat(delivered)
    assert ppt_click_targets_by_slide(delivered) == ppt_click_targets_by_slide(native)
    # The editor continues to use the native scene.
    with ZipFile(io.BytesIO(native)) as archive:
        assert b'<a:tbl>' in b''.join(archive.read(n) for n in archive.namelist() if n.startswith('ppt/slides/slide'))


def test_flattening_refuses_page_loss(report):
    project, doc, source = report
    native = render_report_pptx(*report)
    pages = report_pages(*report)
    with pytest.raises(ValueError, match='페이지 수'):
        flatten_pptx(native, render_pdf(pages[:-1], doc))


def test_source_opt_out_does_not_embed_originals_or_create_source_links(report):
    with ZipFile(io.BytesIO(build_package(*report, include_sources=False))) as archive:
        payload = json.loads(Payload(archive.read('보고서.html').decode()).text)
        assert set(payload['files']) == {'보고서.pdf', '보고서.pptx'}
        assert all(not targets for targets in ppt_click_targets_by_slide(archive.read('보고서.pptx')).values())


@pytest.mark.parametrize('encoding', ['utf-8-sig', 'cp949', 'utf-16'])
def test_text_keeps_original_encoding_and_escapes_markup(encoding):
    text = '한글 <script>alert(1)</script>\r\n20\t1.25'
    raw = text.encode(encoding)
    entry = portable_html.embedded_file('측정.txt',raw,'text/plain',preview=True)
    assert entry['text'] == text
    assert base64.b64decode(entry['data']) == raw
    html = portable_html.make_portable('<html><body></body></html>','</script><img src=x>',[],{'Measured Data/a.txt':entry})
    parsed = json.loads(Payload(html).text)
    assert parsed['files']['Measured Data/a.txt']['text'] == text
    assert '<script>alert(1)</script>' not in html
    assert '<img src=x>' not in html


def test_unrenderable_pdf_keeps_download_and_explains_preview_limit():
    original = b'not-a-valid-pdf'
    entry = portable_html.embedded_file('report.pdf',original,'application/pdf',preview=True)
    assert entry['notice'] and entry['pages'] == []
    assert base64.b64decode(entry['data']) == original


def test_oversize_html_is_not_silently_incomplete(monkeypatch):
    monkeypatch.setattr(portable_html,'MAX_HTML_BYTES',32)
    with pytest.raises(ValueError, match='128MB'):
        portable_html.make_portable('<body></body>','viewer',[],{})


def test_missing_html_attachment_is_not_delivered_as_broken_link():
    with pytest.raises(ValueError, match='링크 대상'):
        portable_html.make_portable('<body><a href="Report/missing.pdf">원본</a></body>','viewer',[],{})

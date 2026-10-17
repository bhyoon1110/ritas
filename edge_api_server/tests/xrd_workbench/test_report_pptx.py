"""Native PPTX output: editable line text boxes, exact source images, relative links, 16:9 geometry."""

import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from xml.etree import ElementTree as ET

import pytest
from .fixture_data import example_project, resolve_source
from PIL import Image

from app.xrd_workbench.core.report import new_document
from app.xrd_workbench.render.layout import build_pages
from app.xrd_workbench.render.pptx import EMU_PER_MM, render_pptx

NS = {
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
}
FOOTER_RULE_MM = 278


def slide_names(package):
    names = [
        n for n in package.namelist() if n.startswith('ppt/slides/slide') and n.endswith('.xml')
    ]
    return sorted(names, key=lambda n: int(n.removeprefix('ppt/slides/slide').removesuffix('.xml')))


@pytest.fixture(scope='module')
def actual_pptx():
    project = example_project()
    doc = new_document(project)
    doc['revision'] = 1
    doc['metadata']['title'] = 'XRD 의뢰자 편집 검토본'
    doc['samples'][0]['manual_note'] = '담당자가 작성한 검토 문장입니다.'
    pages = build_pages(project, doc, lambda fid: resolve_source('example', fid))
    return project, pages, render_pptx(pages, doc, font_family='Test Family')


@pytest.mark.real_data
def test_native_text_preserves_korean_author_edits_and_raw_quantities(actual_pptx):
    project, pages, data = actual_pptx
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        slides = slide_names(z)
        assert len(slides) == len(pages) >= 2
        native = ' '.join(
            t.text or ''
            for name in slides
            for t in ET.fromstring(z.read(name)).iter(f'{{{NS["a"]}}}t')
        )
        assert 'XRD 의뢰자 편집 검토본' in native
        assert '담당자가 작성한 검토 문장입니다.' in native
        flat = ' '.join(native.split())
        for sample in project['samples']:
            assert all(q['raw'] in flat for q in sample['quantities'])
        size = ET.fromstring(z.read('ppt/presentation.xml')).find('p:sldSz', NS)
        assert int(size.attrib['cx']) == 12192000 and int(size.attrib['cy']) == 6858000


@pytest.mark.real_data
def test_every_run_uses_the_configured_latin_and_east_asian_typeface(actual_pptx):
    with zipfile.ZipFile(io.BytesIO(actual_pptx[2])) as z:
        for name in slide_names(z):
            for run in ET.fromstring(z.read(name)).iter(f'{{{NS["a"]}}}rPr'):
                assert run.find('a:latin', NS).attrib['typeface'] == 'Test Family'
                assert run.find('a:ea', NS).attrib['typeface'] == 'Test Family'


@pytest.mark.real_data
def test_source_images_remain_separate_exact_bytes_with_original_proportions(actual_pptx):
    project, pages, data = actual_pptx
    expected = [i for page in pages for i in page['items'] if i['type'] == 'image']
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        media = [z.read(n) for n in sorted(z.namelist()) if n.startswith('ppt/media/')]
        assert sorted(media) == sorted(i['data'] for i in expected)
        boxes = []
        for name in slide_names(z):
            for pic in ET.fromstring(z.read(name)).iter(f'{{{NS["p"]}}}pic'):
                ext = pic.find('p:spPr/a:xfrm/a:ext', NS)
                boxes.append(int(ext.attrib['cx']) / int(ext.attrib['cy']))
    # Four registered source images plus the generated comparison figure(s), none of them merged.
    assert len(boxes) == len(expected)
    assert len([i for i in expected if i['source_id']]) == 4
    assert len([i for i in expected if not i['source_id']]) == len(boxes) - 4
    ratios = []
    for item in expected:
        with Image.open(io.BytesIO(item['data'])) as image:
            ratios.append(image.width / image.height)
    assert boxes == pytest.approx(ratios, rel=1e-3)


@pytest.mark.real_data
def test_pptx_source_links_are_relative_report_and_measured_data(actual_pptx):
    with zipfile.ZipFile(io.BytesIO(actual_pptx[2])) as z:
        rels = [n for n in z.namelist() if n.startswith('ppt/slides/_rels/')]
        links = [
            r.attrib['Target']
            for n in rels
            for r in ET.fromstring(z.read(n))
            if r.attrib.get('TargetMode') == 'External'
        ]
        assert len(links) == 6
        assert sum(s.startswith('Report/') for s in links) == 4
        assert sum(s.startswith('Measured%20Data/') for s in links) == 2
        assert not any('127.0.0.1' in s for s in links)
        notes = [n for n in z.namelist() if n.startswith('ppt/notesSlides/') and n.endswith('.xml')]
        assert any('Report/' in z.read(n).decode('utf-8') for n in notes)


@pytest.mark.real_data
def test_long_korean_author_text_stays_above_the_footer_rule():
    project = example_project()
    doc = new_document(project)
    doc['revision'] = 1
    doc['samples'][0]['manual_note'] = '긴한국어검토문장입니다. ' * 250
    pages = build_pages(project, doc, lambda fid: resolve_source('example', fid))
    data = render_pptx(pages, doc)
    footer_top = FOOTER_RULE_MM * EMU_PER_MM
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        body = ''
        for name in slide_names(z):
            root = ET.fromstring(z.read(name))
            for shape in root.iter(f'{{{NS["p"]}}}sp'):
                name_attr = shape.find('p:nvSpPr/p:cNvPr', NS).attrib['name']
                if name_attr.startswith(('Report number', 'Page number', 'Footer rule')):
                    continue
                off = shape.find('p:spPr/a:xfrm/a:off', NS)
                ext = shape.find('p:spPr/a:xfrm/a:ext', NS)
                assert int(off.attrib['y']) + int(ext.attrib['cy']) <= footer_top, name_attr
            body += ''.join(t.text or '' for t in root.iter(f'{{{NS["a"]}}}t'))
    assert body.count('긴한국어검토문장입니다.') == 250


@pytest.mark.real_data
def test_concurrent_exports_keep_their_own_titles():
    project = example_project()
    pages_by_title = {}
    for title in ('동시 출력 A', '동시 출력 B'):
        doc = deepcopy(new_document(project))
        doc['revision'] = 1
        doc['metadata']['title'] = title
        pages_by_title[title] = (
            build_pages(project, doc, lambda fid: resolve_source('example', fid)),
            doc,
        )
    with ThreadPoolExecutor(2) as pool:
        results = dict(
            zip(pages_by_title, pool.map(lambda v: render_pptx(*v), pages_by_title.values()))
        )
    for title, data in results.items():
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            first = z.read(slide_names(z)[0]).decode('utf-8')
        other = next(t for t in results if t != title)
        assert title in first and other not in first

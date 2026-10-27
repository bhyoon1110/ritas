import io
import zipfile
from copy import deepcopy

import pytest
from .fixture_data import example_project, fixtures_dir
from pypdf import PdfReader

from app.xrd_workbench.core.report import new_document
from app.xrd_workbench.render.html import render_html
from app.xrd_workbench.render.layout import BOTTOM, build_pages
from app.xrd_workbench.render.package import build_package
from app.xrd_workbench.render.pdf import render_pdf

pytestmark = pytest.mark.real_data


def sources(project):
    paths = {s['id']: fixtures_dir() / s['filename'] for s in project['sources']}
    return lambda file_id: paths[file_id].read_bytes()


def test_layout_and_pdf_preserve_content_and_original_links():
    project = example_project()
    doc = new_document(project)
    doc['metadata']['title'] = '의뢰 결과 <검토>'
    pages = build_pages(project, doc, sources(project))
    assert [p['kind'] for p in pages][:2] == ['cover', 'figure']
    assert any(item['type'] == 'image' for p in pages for item in p['items'])
    data = render_pdf(pages, doc)
    reader = PdfReader(io.BytesIO(data))
    text = ' '.join(p.extract_text() or '' for p in reader.pages)
    assert '의뢰 결과 <검토>' in text
    flat = ' '.join(text.split())
    for sample in project['samples']:
        assert all(q['raw'] in flat for q in sample['quantities'])
    assert len(reader.pages) == len(pages)
    uris = [
        str(a.get_object().get('/A', {}).get('/URI', ''))
        for p in reader.pages
        for a in p.get('/Annots', [])
    ]
    assert any(u.startswith('Report/') for u in uris)
    assert any(u.startswith('Measured%20Data/') for u in uris)
    assert not any('127.0.0.1' in u for u in uris)


def test_long_notes_continue_without_clipping_and_html_escapes_manual_text():
    project = example_project()
    doc = new_document(project)
    sid = project['samples'][0]['id']
    doc['samples'][0]['manual_note'] = (
        '긴 한국어 검토 문장입니다. ' * 250 + '<script>alert(1)</script>'
    )
    pages = build_pages(project, doc, sources(project))
    assert len([p for p in pages if p.get('sample_id') == sid]) == 1
    assert len([p for p in pages if p['kind'] == 'remarks']) >= 1
    flat = ''.join(i['text'] for p in pages for i in p['items'] if i['type'] == 'text' and not i.get('role'))
    assert ''.join(flat.split()).count('긴한국어검토문장입니다.') == 250
    assert all(
        item['y'] + item['h'] <= BOTTOM
        for p in pages
        for item in p['items']
        if not item.get('role')
    )
    html = render_html(pages, doc, 'package')
    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html


def test_zip_has_requested_folders_exact_bytes_and_selected_samples_only():
    project = example_project()
    doc = new_document(project)
    doc['samples'][1]['included'] = False
    data = build_package(project, doc, sources(project))
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        assert {name.split('/')[0] for name in names} == {
            'Measured Data', 'Report', '보고서.pdf', '보고서.pptx', '보고서.html', '뷰어.html', 'Viewer',
        }
        assert 'Report/' in names and 'Measured Data/' in names
        with zipfile.ZipFile(io.BytesIO(z.read('보고서.pptx'))) as pptx:
            from xml.etree import ElementTree as ET

            native = ' '.join(
                t.text or ''
                for n in pptx.namelist()
                if n.startswith('ppt/slides/slide') and n.endswith('.xml')
                for t in ET.fromstring(pptx.read(n)).iter()
                if t.tag.endswith('}t')
            )
            kept = project['samples'][0]['name']
            left_out = project['samples'][1]['name']
            assert not native  # Delivered PPT has page images, never editable report text.
            from app.xrd_workbench.render.pdf_raster import page_images
            for number, image in enumerate(page_images(z.read('보고서.pdf'), max_edge=2600), 1):
                assert pptx.read(f'ppt/media/xrd-delivery-{number}.png') == image
            text = ' '.join(p.extract_text() or '' for p in PdfReader(io.BytesIO(z.read('보고서.pdf'))).pages)
            assert kept in text and left_out not in text
        assert '보고서.pptx' in z.read('보고서.html').decode('utf-8')
        assert any(n.startswith('Report/') and n.endswith('.pdf') for n in names)
        assert any(n.startswith('Measured Data/') and n.endswith('.txt') for n in names)
        assert not any(left_out in n for n in names)
        for record in project['samples'][0]['sources']:
            if record['role'] in ('phase_report', 'wppf_report', 'measurement'):
                entry = next(n for n in names if n.endswith('-' + record['filename']))
                assert z.read(entry) == sources(project)(record['id'])
        assert '127.0.0.1' not in z.read('보고서.html').decode('utf-8')


def test_package_still_rejects_changed_raw_bytes_without_a_manifest_file():
    project = example_project()
    read_source = sources(project)
    raw = next(r for r in project['sources'] if r['role'] == 'measurement')
    with pytest.raises(ValueError, match='원본 파일 내용이 등록 후 변경'):
        build_package(
            project, new_document(project),
            lambda fid: b'altered raw data' if fid == raw['id'] else read_source(fid),
        )


def test_long_sample_header_and_report_number_are_complete_and_clear_of_content():
    project = example_project()
    doc = new_document(project)
    long_name = '가' * 120
    long_number = '나' * 80
    doc['samples'][0]['display_name'] = long_name
    doc['metadata']['report_number'] = long_number
    pages = build_pages(project, doc, sources(project))
    sample_page = next(p for p in pages if p.get('sample_id') == project['samples'][0]['id'])
    assert ''.join(sample_page['header_lines']) == long_name
    assert len(sample_page['header_lines']) == 2
    body = [i for i in sample_page['items'] if not i.get('role')]
    assert min(i['y'] for i in body) >= sample_page['header_bottom'] + 5
    # The footer is part of the page items and also complete.
    assert (
        ''.join(
            i['text']
            for i in sample_page['items']
            if i.get('role') == 'footer' and i['align'] == 'left'
        ).replace(chr(10), '')
        == long_number
    )
    assert ''.join(sample_page['footer_lines']) == long_number
    reader = PdfReader(io.BytesIO(render_pdf(pages, doc)))
    assert long_name in reader.pages[sample_page['number'] - 1].extract_text().replace('\n', '')
    assert long_number in reader.pages[sample_page['number'] - 1].extract_text().replace('\n', '')


def test_summary_distinguishes_registered_but_unparsed_wppf():
    project = deepcopy(example_project())
    project['samples'][0]['quantities'] = []
    doc = new_document(project)
    pages = build_pages(project, doc, sources(project))
    summary = next(p for p in pages if p['kind'] == 'summary')
    text = ' '.join(i.get('text', '') for i in summary['items'])
    assert '정량 자료 미제공' not in text
    assert '추출 확인' in text


def test_changed_image_source_is_not_used_with_old_parsed_results():
    project = example_project()
    doc = new_document(project)
    from PIL import Image

    altered = io.BytesIO()
    Image.new('RGB', (2, 2), 'white').save(altered, format='PNG')
    with pytest.raises(ValueError, match='변경'):
        build_pages(project, doc, lambda fid: altered.getvalue())


def test_evidence_does_not_become_an_orphan_first_line_on_next_page():
    project = example_project()
    pages = build_pages(project, new_document(project), sources(project))
    assert not any(p['items'] and p['items'][0].get('text', '').startswith('근거:') for p in pages)


def test_manual_quantity_summary_and_retained_prose_have_accurate_labels():
    project = deepcopy(example_project())
    sample = project['samples'][0]
    sample['quantities'] = [
        {
            'phase': 'PhaseA',
            'raw': '12.34(5)',
            'unit': 'wt%',
            'origin': 'manual',
            'source_filename': 'sample_quantity.png',
            'page': None,
            'value': 13,
        }
    ]
    sample['analysis_review'] = {'quantity_source': 'manual', 'changes': []}
    doc = new_document(project)
    doc['samples'][0]['retained_notes'] = [
        {
            'id': 'previous',
            'title': '이전 문장',
            'text': '보존한 사용자 문장',
            'selected': True,
            'evidence': [],
        }
    ]
    pages = build_pages(project, doc, sources(project))
    text = ' '.join(i.get('text', '') for p in pages for i in p['items'])
    assert '정량 (직접 입력): PhaseA 12.34(5) wt%' in text
    assert '이전 분석에 대한 사용자 문장: 보존한 사용자 문장' in text

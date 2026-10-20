"""Blue template annotations: summary comparison and exact per-sample viewer links."""
from copy import deepcopy
import io
from urllib.parse import parse_qs, urlsplit, unquote
from zipfile import ZipFile

from lxml import etree as E
from pypdf import PdfReader

from app.xrd_workbench.core.report import new_document
from app.xrd_workbench.render.items import viewer_target
from app.xrd_workbench.render.package import build_package
from app.xrd_workbench.render.posco_pptx import build_posco_deck, NS, box, named
from app.xrd_workbench.render.posco_preview import deck_pages
from app.xrd_workbench.render.html import render_html
from app.xrd_workbench.render.pdf import render_pdf
from .figure_fixtures import make_project
from .test_posco_pptx import fixture
from .test_sample_slide import sample, IMAGE
from .test_viewer_views_delivery import embedded


def links(deck, slide):
    rels = next(rels for s, rels in deck.pages if s is slide)
    return [r.get('Target') for r in rels if r.get('TargetMode') == 'External']


def selection(href):
    return parse_qs(urlsplit(href).fragment)


def test_viewer_target_encodes_ids_and_preserves_online_namespace_or_offline_path():
    sid = '시료 + &/#?'
    for base, storage in [(None, None), ('https://edge.test/api/v1/xrd/workbench/', 'store &/+')]:
        url = urlsplit(viewer_target(sid, base, storage))
        assert parse_qs(url.fragment) == {'view': ['single'], 'sample': [sid]}
        if base:
            assert url.path == '/api/v1/xrd/workbench/'
            assert parse_qs(url.query) == {'project': [storage], 'step': ['viewer']}
        else:
            assert url.path == '뷰어.html' and not url.query
    assert viewer_target() == '뷰어.html#view=multi'


def test_summary_and_each_result_column_share_native_ppt_pdf_and_preview_targets():
    project, doc = fixture()
    # Editable names are not identifiers: even identical names must open different samples.
    for entry in doc['samples']:
        entry['display_name'] = '같은 이름'
    before = deepcopy((project, doc))
    for kwargs in ({}, dict(link_base='/api/v1/xrd/workbench', storage_id='storage-qa')):
        deck = build_posco_deck(project, doc, lambda _: IMAGE, **kwargs)
        all_links = []
        for slide, _ in deck.pages:
            kind = deck.page_info[slide]['kind']
            targets = [u for u in links(deck, slide) if selection(u).get('view')]
            all_links += targets
            if kind == 'summary':
                assert targets == [viewer_target(**kwargs)]
            elif kind == 'sample':
                headings = [
                    deck.edits[s]['tab'] for s in slide.findall('.//p:sp', NS)
                    if deck.edits.get(s, {}).get('field') == 'display_name'
                ]
                ids = [selection(u)['sample'][0] for u in targets]
                assert sorted(ids) == sorted(set(headings))
                assert len(named(slide, 'Sample viewer link')) == len(ids)
                # Every pill stays on one row, inside its own column and on the canvas.
                frames = named(slide, 'Source link frame') + named(slide, 'Sample viewer link frame')
                for index, frame in enumerate(frames):
                    x, y, w, h = box(frame)
                    assert x >= 0 and x+w <= 275.17 and y+h <= 180
                    for other in frames[index+1:]:
                        a, b, c, d = box(other)
                        assert min(x+w, a+c) <= max(x, a) or min(y+h, b+d) <= max(y, b)
            else:
                assert not targets
        assert {selection(u).get('sample', [''])[0] for u in all_links} == {'', 'A', 'B', 'Mix'}
        pages = deck_pages(deck)
        html = render_html(pages, doc, 'app' if kwargs else 'package', storage_id=kwargs.get('storage_id'))
        pdf = PdfReader(io.BytesIO(render_pdf(pages, doc, **kwargs)))
        pdf_links = [str(a.get_object().get('/A', {}).get('/URI', ''))
                     for p in pdf.pages for a in p.get('/Annots', [])]
        from html import escape
        for target in all_links:
            assert target in pdf_links
            assert 'href="' + escape(target, quote=True) + '"' in html
        # Actual serialized PPT relationships carry the same targets.
        with ZipFile(io.BytesIO(deck.finish(doc))) as archive:
            ppt_links = [r.get('Target') for name in archive.namelist()
                         if name.startswith('ppt/slides/_rels/') for r in E.fromstring(archive.read(name))
                         if r.get('TargetMode') == 'External']
        assert all(u in ppt_links for u in all_links)
    assert before == (project, doc)


def test_links_on_continuations_use_the_correct_sample_and_do_not_require_source_files():
    project = make_project([sample('long', n_phases=10, quantities=0), sample('short', quantities=0)])
    for s in project['samples']:
        s['sources'] = []
    doc = new_document(project)
    doc['figure']['include'] = False
    deck = build_posco_deck(project, doc, lambda _: IMAGE, image_renderer=lambda *a: IMAGE)
    details = [slide for slide, _ in deck.pages if deck.page_info[slide]['kind'] == 'sample']
    assert len(details) == 4
    assert [selection(u)['sample'][0] for u in links(deck, details[0])] == ['long', 'short']
    for slide in details[1:]:
        assert links(deck, slide) == [viewer_target('long')]
    detached = build_posco_deck(project, doc, lambda _: IMAGE, include_sources=False,
                               image_renderer=lambda *a: IMAGE)
    assert not any(links(detached, slide) for slide, _ in detached.pages)


def test_package_sample_links_resolve_to_included_embedded_ids_only():
    project, doc = fixture()
    doc['samples'][1]['included'] = False
    archive = ZipFile(io.BytesIO(build_package(project, doc, lambda _: b'x')))
    viewer = archive.read('뷰어.html').decode()
    ids = {s['id'] for s in embedded(viewer)['project']['samples']}
    assert ids == {'A', 'Mix'}
    pdf = PdfReader(io.BytesIO(archive.read('보고서.pdf')))
    targets = [str(a.get_object().get('/A', {}).get('/URI', ''))
               for p in pdf.pages for a in p.get('/Annots', [])]
    single_ids = set()
    for target in targets:
        assert unquote(urlsplit(target).path) in archive.namelist()
        params = selection(target)
        if params.get('view') == ['single']:
            single_ids.add(params['sample'][0])
    assert single_ids == ids

"""A PDF or PPTX saved on its own has no Report/ folder beside it: its source links then point at
the running app. Inside the delivery ZIP they stay relative and every one names a packed file."""

import io
import zipfile
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from pptx import Presentation
from pypdf import PdfReader
from .test_sample_slide import build, sample

from app.xrd_workbench.render.package import build_package
from app.xrd_workbench.render.pdf import render_pdf
from app.xrd_workbench.render.pptx import render_pptx

BASE = 'http://127.0.0.1:8765'


def pdf_uris(data):
    uris = []
    for page in PdfReader(io.BytesIO(data)).pages:
        for annot in page.get('/Annots') or []:
            action = annot.get_object().get('/A')
            if action and '/URI' in action:
                uris.append(str(action['/URI']))
    return uris


def pptx_targets(data):
    targets = []
    for slide in Presentation(io.BytesIO(data)).slides:
        for rel in slide.part.rels.values():
            if rel.is_external and 'hyperlink' in rel.reltype:
                targets.append(rel.target_ref)
    return targets


def test_relative_links_by_default_and_app_links_for_a_standalone_export():
    pages, document = build([sample('a')])
    ids = {i['source_id'] for p in pages for i in p['items'] if i['type'] == 'link'}
    assert ids
    for uris in (pdf_uris(render_pdf(pages, document)), pptx_targets(render_pptx(pages, document))):
        assert uris and all(u.startswith(('Report/', 'Measured%20Data/')) for u in uris)
    app = dict(link_base=BASE, storage_id='store1')
    for uris in (
        pdf_uris(render_pdf(pages, document, **app)),
        pptx_targets(render_pptx(pages, document, **app)),
    ):
        assert {u.rsplit('/', 1)[-1] for u in uris} == ids
        assert all(u.startswith(f'{BASE}/api/files/store1/') for u in uris)


def test_every_link_in_the_zip_names_a_file_in_the_zip():
    pages, document = build([sample('a')])
    project = pages and None  # the project is rebuilt below with the same sample
    from .figure_fixtures import make_project

    project = make_project([sample('a')])
    from app.xrd_workbench.core.report import new_document

    doc = new_document(project)
    doc['figure']['include'] = False
    doc['quantity_table']['include'] = False
    data = {r['id']: b'x' for s in project['samples'] for r in s['sources']}
    for s in project['samples']:
        for r in s['sources']:
            import hashlib

            r['sha256'] = hashlib.sha256(b'x').hexdigest()
    archive = zipfile.ZipFile(io.BytesIO(build_package(project, doc, data.__getitem__)))
    names = set(archive.namelist())
    inside = zipfile.ZipFile(io.BytesIO(archive.read('보고서.pptx')))
    links = []
    for name in inside.namelist():
        if name.startswith('ppt/slides/_rels/'):
            root = ET.fromstring(inside.read(name))
            links += [r.get('Target') for r in root if r.get('TargetMode') == 'External']
    uris = pdf_uris(archive.read('보고서.pdf'))
    assert not links and uris  # recipient PPT opens embedded originals directly
    from .test_offline_pptx import embedded_files
    assert embedded_files(archive.read('보고서.pptx'))
    assert '보고서.html' not in names
    for target in links + uris:
        assert unquote(urlsplit(target).path) in names, target

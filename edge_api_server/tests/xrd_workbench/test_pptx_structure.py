"""Read the entire master/layout graph, not just visible slides or ZIP integrity."""
from copy import deepcopy
import io
from zipfile import ZipFile

from lxml import etree as E
from pptx import Presentation
import pytest

from app.report.pptx_protection import create_protection
from app.xrd_workbench.core.project import build_project
from app.xrd_workbench.core.report import new_document
from app.xrd_workbench.render.posco_pptx import (
    DEFAULT_TEMPLATE, TEMPLATE, NS, q, set_text, render_report_pptx,
)
from scripts.prepare_xrd_default_template import copy_content_types
from .synthetic import sample_files


def read_layouts(content):
    deck = Presentation(io.BytesIO(content))
    # python-pptx creates a generic Part for an undeclared layout; reading its
    # master reference then fails, just as strict Office package readers do.
    for master in deck.slide_masters:
        for layout in master.slide_layouts:
            assert layout.slide_master.part is master.part
    return deck


@pytest.mark.parametrize('path', [TEMPLATE, DEFAULT_TEMPLATE])
def test_registered_template_parts_have_specific_office_types(path):
    content = path.read_bytes()
    read_layouts(content)
    with ZipFile(io.BytesIO(content)) as z:
        types = E.fromstring(z.read('[Content_Types].xml'))
        names = [n.get('PartName') for n in types if n.get('PartName')]
        assert len(names) == len(set(names))
        declared = {n.get('PartName'): n.get('ContentType') for n in types}
        for path in z.namelist():
            if path.startswith('ppt/slideLayouts/') and path.endswith('.xml'):
                assert declared.get('/'+path) == 'application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml'


def test_preparation_retains_types_for_new_layouts_without_changing_artwork():
    with ZipFile(DEFAULT_TEMPLATE) as z:
        source = {n:z.read(n) for n in z.namelist()}
    files = deepcopy(source)
    types = E.fromstring(files['[Content_Types].xml'])
    layout = '/ppt/slideLayouts/slideLayout7.xml'
    for node in list(types):
        if node.get('PartName') == layout: types.remove(node)
    files['[Content_Types].xml'] = E.tostring(types)
    copy_content_types(files, source, {layout[1:]})
    copy_content_types(files, source, {layout[1:]})  # no duplicate registrations
    assert len([n for n in E.fromstring(files['[Content_Types].xml']) if n.get('PartName') == layout]) == 1
    assert all(files[n] == data for n,data in source.items() if n != '[Content_Types].xml')
    output = io.BytesIO()
    with ZipFile(output,'w') as z:
        for n,data in files.items(): z.writestr(n,data)
    read_layouts(output.getvalue())


@pytest.mark.parametrize('fit', ['spAutoFit', 'normAutofit', 'noAutofit'])
def test_setting_text_replaces_the_exclusive_autofit_choice_in_schema_order(fit):
    with ZipFile(DEFAULT_TEMPLATE) as z:
        cover = E.fromstring(z.read('ppt/slides/slide4.xml'))
    shape = cover.find('p:cSld/p:spTree/p:sp', NS)
    props = shape.find('p:txBody/a:bodyPr', NS)
    for child in list(props): props.remove(child)
    E.SubElement(props,q('a:'+fit))
    E.SubElement(props,q('a:extLst'))
    assert set_text(shape, '분석 결과 보고서')
    assert [child.tag for child in props] == [q('a:noAutofit'),q('a:extLst')]


@pytest.fixture(scope='module')
def source_project():
    files = sample_files('SampleA')
    project = build_project(list(files.items()))
    records = {r['id']:r for r in project['sources']}
    return project, lambda sid: files[records[sid]['filename']]


@pytest.mark.parametrize('version', ['legacy', '2026-10-29'])
@pytest.mark.parametrize('password', [False, True])
@pytest.mark.parametrize('sources', [False, True])
def test_delivered_deck_preserves_readable_layouts_and_valid_text_choices(source_project, version, password, sources):
    project, resolve = source_project
    doc = new_document(project)
    doc['template_version'] = version
    if password: doc['pptxProtection'] = create_protection('test-only')
    content = render_report_pptx(project,doc,resolve,delivery=True,include_sources=sources)
    deck = read_layouts(content)
    assert len(deck.slides) >= 3
    with ZipFile(io.BytesIO(content)) as z:
        assert z.testzip() is None
        for part in z.namelist():
            if part.startswith('ppt/slides/') and part.endswith('.xml'):
                root = E.fromstring(z.read(part))
                for body in root.findall('.//a:bodyPr',NS):
                    fits = [n for n in body if n.tag in {q('a:spAutoFit'),q('a:normAutofit'),q('a:noAutofit')}]
                    assert len(fits) <= 1
        verifier = E.fromstring(z.read('ppt/presentation.xml')).find('p:modifyVerifier',NS)
        assert (verifier is not None) == password
        assert any(n.startswith('ppt/embeddings/xrdAttachment') for n in z.namelist()) == sources

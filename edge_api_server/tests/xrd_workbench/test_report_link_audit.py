"""Delivery must fail if any required PPT click or its original file is missing."""
from copy import deepcopy
import hashlib
import io
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as E
from zipfile import ZipFile

import pytest

from app.xrd_workbench.core.report import new_document
from app.xrd_workbench.render import package, posco_pptx
from app.xrd_workbench.render.items import viewer_target
from app.xrd_workbench.render.package_names import package_paths
from app.xrd_workbench.render.layout import source_links
from app.xrd_workbench.render.link_audit import (
    DRAWING, RELATIONSHIP, check_package_links, check_ppt_links,
    ppt_click_targets, ppt_click_targets_by_slide, read_linked_source, report_sources,
)
from .figure_fixtures import make_project
from .test_sample_slide import IMAGE, sample, source


@pytest.fixture
def inputs():
    project = make_project([sample('A', quantities=3), sample('B', quantities=0)])
    project['samples'][0]['sources'].append(source('A', 'wppf_report', '시료 A #1 (50%)_정량_Report.pdf'))
    data = {}
    for s in project['samples']:
        for record in s['sources']:
            content = ('원본 ' + record['id']).encode()
            record['sha256'] = hashlib.sha256(content).hexdigest()
            data[record['id']] = content
    doc = new_document(project)
    doc['figure']['include'] = False
    return project, doc, data


@pytest.fixture
def ppt(inputs, monkeypatch):
    project, doc, data = inputs
    monkeypatch.setattr(posco_pptx, 'render_panel_png', lambda *a: IMAGE)
    result = posco_pptx.render_report_pptx(project, doc, data.__getitem__)
    expected = {link['href']: link['filename'] for s in project['samples'] for link in source_links(s)}
    expected.update({viewer_target(s['id'], paths=package_paths(project)): s['name'] for s in project['samples']})
    expected[viewer_target()] = '전체 그래프'
    return result, expected


def alter_ppt(content, change):
    output = io.BytesIO()
    with ZipFile(io.BytesIO(content)) as original, ZipFile(output, 'w') as modified:
        for name in original.namelist():
            data = original.read(name)
            if name.startswith('ppt/slides/') and name.endswith(('.xml', '.rels')):
                root = E.fromstring(data)
                change(name, root)
                data = E.tostring(root)
            modified.writestr(name, data)
    return output.getvalue()


def test_final_ppt_covers_all_source_roles_and_viewers(ppt):
    data, expected = ppt
    assert set(check_ppt_links(data, expected)) == set(expected)


def test_unused_relationship_does_not_count_as_a_clickable_link(ppt, inputs, monkeypatch):
    data, expected = ppt
    def delete_clicks(name, root):
        for parent in root.iter():
            for child in list(parent):
                if child.tag == '{' + DRAWING + '}hlinkClick':
                    parent.remove(child)
    broken = alter_ppt(data, delete_clicks)
    with ZipFile(io.BytesIO(broken)) as archive:
        assert any(b'/hyperlink' in archive.read(n) for n in archive.namelist() if n.endswith('.rels'))
    with pytest.raises(ValueError, match='필수 링크'):
        check_ppt_links(broken, expected)
    monkeypatch.setattr(posco_pptx.Deck, 'finish', lambda *a: broken)
    project, doc, sources = inputs
    with pytest.raises(ValueError, match='필수 링크'):
        posco_pptx.render_report_pptx(project, doc, sources.__getitem__)


@pytest.mark.parametrize('fault', ['missing_relationship', 'wrong_type', 'stale_target'])
def test_invalid_serialized_relationship_is_refused(ppt, fault):
    data, expected = ppt
    def corrupt(name, root):
        if not name.endswith('.rels'): return
        for rel in list(root):
            if rel.get('Type') != RELATIONSHIP + '/hyperlink': continue
            if fault == 'missing_relationship': root.remove(rel)
            elif fault == 'wrong_type': rel.set('Type', RELATIONSHIP + '/image')
            else: rel.set('Target', 'https://old-template.invalid/missing.pdf')
    with pytest.raises(ValueError, match='PPT'):
        check_ppt_links(alter_ppt(data, corrupt), expected)


def test_swapping_source_links_between_sample_slides_is_refused(ppt):
    data, expected = ppt
    required = ppt_click_targets_by_slide(data)
    phase_targets = [link for link in expected if link.startswith('Report/') and unquote(link).endswith('_상매칭_Report.pdf')]
    left, right = phase_targets[:2]
    def swap(name, root):
        if not name.endswith('.rels'): return
        for rel in root:
            target = rel.get('Target')
            if target in (left, right): rel.set('Target', right if target == left else left)
    broken = alter_ppt(data, swap)
    assert set(ppt_click_targets(broken)) == set(expected)  # globally complete, but wrong samples
    with pytest.raises(ValueError, match='페이지'):
        check_ppt_links(broken, expected, required)


@pytest.mark.parametrize('fault', ['missing', 'empty', 'changed'])
def test_standalone_export_refuses_broken_originals_before_generating_ppt(inputs, monkeypatch, fault):
    project, doc, data = inputs
    record = project['samples'][0]['sources'][0]
    if fault == 'missing': del data[record['id']]
    else: data[record['id']] = b'' if fault == 'empty' else b'changed'
    monkeypatch.setattr(posco_pptx, 'build_posco_deck', lambda *a, **k: pytest.fail('preflight must run first'))
    with pytest.raises(ValueError, match=record['filename']):
        posco_pptx.render_report_pptx(project, doc, data.__getitem__,
                                     link_base='https://edge.test/api/v1/xrd/workbench', storage_id='id')


def test_excluded_samples_are_never_read_or_linked(inputs, monkeypatch):
    project, doc, data = inputs
    doc['samples'][1]['included'] = False
    for record in project['samples'][1]['sources']: del data[record['id']]
    before = deepcopy((project, doc))
    monkeypatch.setattr(posco_pptx, 'render_panel_png', lambda *a: IMAGE)
    content = posco_pptx.render_report_pptx(project, doc, data.__getitem__,
                                          link_base='https://edge.test', storage_id='id')
    targets = ppt_click_targets(content)
    assert not any('/B-' in t or 'sample=B' in t for t in targets)
    assert {r['id'] for r in report_sources(project, doc)} == set(data)
    assert before == (project, doc)


def test_package_checks_final_ppt_against_actual_written_files(inputs, monkeypatch):
    project, doc, data = inputs
    monkeypatch.setattr(posco_pptx, 'render_panel_png', lambda *a: IMAGE)
    content = package.build_package(project, doc, data.__getitem__)
    with ZipFile(io.BytesIO(content)) as archive:
        targets = ppt_click_targets(archive.read('보고서.pptx'))
        check_package_links(targets, archive)
        for target in targets:
            assert archive.read(unquote(urlsplit(target).path))
    # Simulate a packaging regression that writes a viewer under the wrong name.
    monkeypatch.setattr(package, 'sample_viewer_path', lambda sid, paths=None: 'Viewer/wrong-' + sid + '.html')
    with pytest.raises(ValueError, match='대상 파일'):
        package.build_package(project, doc, data.__getitem__)


def test_source_id_conflicts_are_not_silently_deduplicated(inputs):
    project, doc, _ = inputs
    project['samples'][1]['sources'][0]['id'] = project['samples'][0]['sources'][0]['id']
    with pytest.raises(ValueError, match='식별자'):
        report_sources(project, doc)


@pytest.mark.parametrize('names, target', [
    ({'Report/data.pdf': b''}, 'Report/data.pdf'),
    ({'Report/data.pdf': b'x'}, 'Report/missing.pdf'),
    ({'Report/data.pdf': b'x', 'Report/DATA.pdf': b'y'}, 'Report/data.pdf'),
    ({'Report/data.pdf': b'x'}, '../Report/data.pdf'),
])
def test_empty_missing_colliding_or_nonportable_package_targets_fail(names, target):
    with ZipFile(io.BytesIO(), 'w') as archive:
        for name, value in names.items(): archive.writestr(name, value)
        with pytest.raises(ValueError): check_package_links([target], archive)


def test_empty_source_is_rejected_even_if_its_registered_hash_matches():
    record = source('A', 'measurement', 'empty.txt', b'')
    with pytest.raises(ValueError, match='비어'):
        read_linked_source(record, lambda _: b'')


@pytest.mark.parametrize('include_sources', [True, False])
def test_site_template_obeys_source_link_policy_without_changing_prebuilt_pages(inputs, monkeypatch, tmp_path, include_sources):
    from .test_pptx_template import make_template
    project, doc, data = inputs
    monkeypatch.setenv('XRD_PPTX_TEMPLATE', str(make_template(tmp_path)))
    pages = posco_pptx.build_pages(project, doc, data.__getitem__, image_renderer=lambda *a: IMAGE)
    before = deepcopy(pages)
    content = posco_pptx.render_report_pptx(project, doc, data.__getitem__, pages=pages,
                                          include_sources=include_sources)
    expected = {link['href'] for s in project['samples'] for link in source_links(s)} if include_sources else set()
    assert set(ppt_click_targets(content)) == expected
    assert before == pages

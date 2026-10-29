"""Uploaded phase-matching evidence wins over old image flags and viewer plots."""
from copy import deepcopy
import base64
import hashlib
import io
import posixpath
from zipfile import ZipFile

from fastapi.testclient import TestClient
from lxml import etree as E
from PIL import Image
import pytest

from app.xrd_workbench.render.layout import build_pages
from app.xrd_workbench.render.posco_pptx import build_posco_deck, box, named, NS, render_report_pptx
from app.xrd_workbench.render.posco_preview import deck_pages
from .synthetic import _png
from .test_posco_pptx import fixture, deck
from .test_sample_slide import source, IMAGE


def artwork_fixture(fmt='PNG'):
    project, document = fixture()
    data, expected = {}, {}
    for index, sample in enumerate(project['samples']):
        original = _png(sample['id'] + ' uploaded phase matching', 80 + index * 50)
        if fmt == 'JPEG':
            output = io.BytesIO()
            Image.open(io.BytesIO(original)).save(output, 'JPEG')
            original = output.getvalue()
        record = source(sample['id'], 'phase_image', sample['id'] + '_상매칭.' + fmt.lower(), original)
        sample['sources'].append(record)
        expected[sample['id']] = original
        data.update({r['id']: original if r is record else b'x' for r in sample['sources']})
    # This fixture changes source membership; omit unrelated source-derived prose.
    for entry in document['samples']:
        entry['notes'] = []
    # Reproduce an old report whose include flag hides an already uploaded image.
    assert all(not entry['include_phase_image'] for entry in document['samples'])
    return project, document, data, expected


@pytest.mark.parametrize('fmt', ['PNG', 'JPEG'])
@pytest.mark.parametrize('legacy_included', [False, True])
def test_source_is_exact_on_both_phase_layouts_in_ppt_and_preview(fmt, legacy_included):
    project, document, data, expected = artwork_fixture(fmt)
    document['figure']['include'] = True
    for entry in document['samples']:
        entry['include_phase_image'] = legacy_included
    before = deepcopy((project, document))
    panels = []

    def renderer(panel, *args):
        panels.append(panel)
        return IMAGE

    filled = build_posco_deck(project, document, data.__getitem__, image_renderer=renderer)
    # The summary remains a viewer comparison; neither detail layout reconstructs RAW.
    assert len(panels) == 1 and panels[0]['sample_legend']
    pages = deck_pages(filled)
    found = {}
    for page in pages:
        if page['kind'] != 'sample':
            continue
        for item in page['items']:
            edit = item.get('edit', {})
            if item['type'] == 'image' and edit.get('field') == 'phase_caption':
                sid = edit['tab']
                assert item['data'] == expected[sid]
                assert item['w'] / item['h'] == pytest.approx(640 / 420, rel=1e-5)
                found[sid] = page['title']
    assert set(found) == set(expected)
    assert 'WPPF' in found['Mix'] and 'WPPF' not in found['A']
    archive, slides, _ = deck(render_report_pptx(project, document, data.__getitem__, include_sources=False))
    for sid, original in expected.items():
        located = []
        for index, slide in enumerate(slides, 1):
            for picture in named(slide, f'시료 {sid} 상매칭'):
                rid = picture.find('p:blipFill/a:blip', NS).get('{'+NS['r']+'}embed')
                rels = E.fromstring(archive.read(f'ppt/slides/_rels/slide{index}.xml.rels'))
                target = next(r.get('Target') for r in rels if r.get('Id') == rid)
                located.append(archive.read(posixpath.normpath('ppt/slides/' + target)))
                assert box(picture)[2] / box(picture)[3] == pytest.approx(640 / 420, rel=1e-5)
                assert not named(slide, f'시료 {sid} RAW')
        assert located == [original]
    assert before == (project, document)


def test_only_missing_source_falls_back_and_quantity_exclusion_is_preserved():
    project, document, data, expected = artwork_fixture()
    project['samples'][1]['sources'] = [r for r in project['samples'][1]['sources'] if r['role'] != 'phase_image']
    quantity = _png('quantity excluded by author', 90)
    record = source('Mix', 'quantity_image', 'Mix_정량.png', quantity)
    project['samples'][2]['sources'].append(record)
    data[record['id']] = quantity
    document['samples'][2]['include_quantity_image'] = False
    calls = []
    filled = build_posco_deck(project, document, data.__getitem__,
                             image_renderer=lambda panel, *args: calls.append(panel) or IMAGE)
    assert len(calls) == 1
    assert quantity not in filled.images.values()
    pictures = [item for page in deck_pages(filled) if page['kind'] == 'sample'
                for item in page['items'] if item['type'] == 'image' and item.get('edit', {}).get('field') == 'phase_caption']
    assert {item['edit']['tab']: item['data'] for item in pictures} == dict(A=expected['A'], B=IMAGE, Mix=expected['Mix'])


def test_site_template_layout_also_keeps_uploaded_phase_artwork():
    project, document, data, expected = artwork_fixture()
    pages = build_pages(project, document, data.__getitem__)
    for page in pages:
        if page['kind'] != 'sample':
            continue
        images = [item for item in page['items'] if item['type'] == 'image']
        assert any(item['data'] == expected[page['sample_id']] for item in images)


def test_changed_original_is_rejected_instead_of_silently_using_viewer():
    project, document, data, _ = artwork_fixture()
    record = next(r for r in project['samples'][0]['sources'] if r['role'] == 'phase_image')
    data[record['id']] = IMAGE
    assert hashlib.sha256(IMAGE).hexdigest() != record['sha256']
    with pytest.raises(ValueError, match='원본 이미지가 변경'):
        build_posco_deck(project, document, data.__getitem__, image_renderer=lambda *args: pytest.fail('RAW fallback'))


def test_uploaded_images_survive_old_flags_through_preview_and_delivery(tmp_path, monkeypatch):
    from app.xrd_workbench import integration as api
    from app.xrd_workbench.render import figure, posco_pptx
    from .synthetic import sample_files
    from .test_integration import make_app, upload, poll

    files = sample_files('SampleA')
    files.update({name: data for name, data in sample_files('SampleB').items() if '정량' not in name})
    originals = [data for name, data in files.items() if name.endswith('_상매칭.png')]
    with TestClient(make_app(tmp_path)) as client:
        sid = upload(client, files)
        url = api.BASE + f'/api/reports/{sid}'
        document = client.get(url).json()['document']
        document['figure']['include'] = False
        for entry in document['samples']:
            entry['include_phase_image'] = False

        def unexpected_raw(*args):
            raise AssertionError('Uploaded phase artwork must not be replaced by a RAW rendering')

        monkeypatch.setattr(figure, 'render_panel_png', unexpected_raw)
        monkeypatch.setattr(posco_pptx, 'render_panel_png', unexpected_raw)
        preview = client.post(url + '/draft-preview', json={'document': document})
        assert preview.status_code == 200, preview.text
        assert all(base64.b64encode(image).decode() in preview.text for image in originals)
        canvas = client.post(url + '/draft-preview', json={'document': document, 'format': 'editor'})
        assert canvas.status_code == 200, canvas.text
        assert len(canvas.json()['pages']) == 5
        saved = client.put(url, json={'document': document, 'expected_revision': 0})
        assert saved.status_code == 200, saved.text
        for kind in ('pptx', 'pdf', 'zip'):
            response = client.post(url + '/exports', json={'kind': kind, 'revision': 1})
            assert response.status_code == 202, response.text
            job = poll(client, response.json())
            result = client.get(api.BASE + f'/api/jobs/{job["job_id"]}/file')
            assert result.status_code == 200 and result.content
            if kind == 'pdf':
                assert result.content.startswith(b'%PDF')
            elif kind == 'pptx':
                with ZipFile(io.BytesIO(result.content)) as archive:
                    # Final recipient PPT follows the same source-based page scene.
                    assert archive.read('ppt/media/xrd-delivery-5.png').startswith(b'\x89PNG')
            else:
                with ZipFile(io.BytesIO(result.content)) as archive:
                    assert '보고서.html' not in archive.namelist()
                    from app.xrd_workbench.render.pdf_raster import page_images
                    with ZipFile(io.BytesIO(archive.read('보고서.pptx'))) as ppt:
                        # Source-based preview/PDF and recipient PPT retain the same scene.
                        for index, image in enumerate(page_images(archive.read('보고서.pdf'), max_edge=2600), 1):
                            assert ppt.read(f'ppt/media/xrd-delivery-{index}.png') == image

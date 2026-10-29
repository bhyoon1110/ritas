"""The supplied 2026-10-29 design is a default, never a source of scientific values."""
from copy import deepcopy
from hashlib import sha256
import io
import posixpath
from zipfile import ZipFile

from lxml import etree as E
import pytest

from app.xrd_workbench.core.report import new_document, validate_document
from app.xrd_workbench.render.posco_pptx import (
    DEFAULT_TEMPLATE, TEMPLATE, NS, build_posco_deck, named, box, render_report_pptx,
)
from app.xrd_workbench.render.posco_preview import deck_pages
from .test_posco_pptx import fixture
from .test_sample_slide import IMAGE


def test_registered_template_retains_only_the_approved_artwork_and_no_example_evidence():
    with ZipFile(DEFAULT_TEMPLATE) as archive:
        media = {n: archive.read(n) for n in archive.namelist() if n.startswith('ppt/media/')}
        assert {n: sha256(data).hexdigest()[:12] for n, data in media.items()} == {
            'ppt/media/image1.jpeg': '987ec62ed7d6',
            'ppt/media/image2.png': '16bbb10699ed',
            'ppt/media/image3.jpg': 'cbd60c4f3c8a',
        }
        for name in archive.namelist():
            if name.endswith(('.xml', '.rels')):
                text = archive.read(name).decode()
                assert all(value not in text for value in ('Example 1', 'Example 5', 'OO연구소', '26.10.22', 'Graphite', '01-071-9645'))
            if name.endswith('.rels'):
                parent = posixpath.dirname(name).replace('/_rels', '') if name != '_rels/.rels' else ''
                for rel in E.fromstring(archive.read(name)):
                    assert rel.get('TargetMode') != 'External'
                    assert posixpath.normpath(posixpath.join(parent, rel.get('Target'))) in archive.namelist()


def test_new_reports_use_the_registered_default_and_old_reports_keep_the_old_design():
    project, doc = fixture()
    assert doc['template_version'] == '2026-10-29'
    assert validate_document(project, doc)['template_version'] == '2026-10-29'
    old = deepcopy(doc); old.pop('template_version')
    cleaned = validate_document(project, old)
    assert cleaned['template_version'] == 'legacy'
    for document in (old, cleaned):
        output = build_posco_deck(project, document, lambda _: IMAGE)
        assert not output.supplied_design
        assert not named(output.pages[0][0], 'Template cover artwork')
        with ZipFile(TEMPLATE) as template:
            assert output.files['ppt/slideMasters/slideMaster1.xml'] == template.read('ppt/slideMasters/slideMaster1.xml')
    assert 'template_version' not in old
    doc['template_version'] = 'missing'
    with pytest.raises(ValueError, match='양식 버전'):
        validate_document(project, doc)


def test_default_cover_values_geometry_and_background_agree_in_ppt_and_preview():
    project, doc = fixture()
    doc['metadata'].update(title='실제 시료 XRD 결과 보고서', client='시험연구소', requester='검토자', date='2026-10-29')
    output = build_posco_deck(project, doc, lambda _: IMAGE)
    cover = output.pages[0][0]
    values = {name: ''.join(named(cover, name)[0].xpath('.//a:t/text()', namespaces=NS))
              for name in ('Title','Cover requester','Cover completionDate')}
    assert values == {'Title':'실제 시료 XRD 결과 보고서','Cover requester':'의뢰자: 시험연구소 검토자',
                      'Cover completionDate':'26.10.29'}
    assert box(named(cover,'Title')[0]) == pytest.approx((25.8,72.34,223.57,14))
    assert box(named(cover,'Template cover artwork')[0]) == pytest.approx((0,0,9906000/36000,190.5))
    page = deck_pages(output)[0]
    pictures = [i for i in page['items'] if i['type']=='image' and i['filename']=='Template cover artwork']
    assert len(pictures)==1
    with ZipFile(DEFAULT_TEMPLATE) as template:
        assert pictures[0]['data']==template.read('ppt/media/image3.jpg')
    assert not any(o['name']=='Template cover artwork' for o in page['objects'])
    assert all(value in '\n'.join(''.join(i.get('lines', [])) for i in page['items']) for value in values.values())
    with ZipFile(io.BytesIO(render_report_pptx(project,doc,lambda _:IMAGE,include_sources=False))) as ppt:
        assert pictures[0]['data'] in [ppt.read(n) for n in ppt.namelist() if n.startswith('ppt/media/')]
        assert all(value in ''.join(E.fromstring(ppt.read('ppt/slides/slide1.xml')).xpath('.//a:t/text()',namespaces=NS)) for value in values.values())


def test_existing_canvas_edits_remain_on_legacy_shapes_after_validation_and_resave():
    project, doc = fixture()
    doc.pop('template_version')
    before = deck_pages(build_posco_deck(project,doc,lambda _:IMAGE))
    cover = before[0]
    title = next(o for o in cover['objects'] if o['name']=='Report title')
    doc['canvasEdits'] = {cover['id']:[dict(id=title['id'],text='보존할 기존 편집 제목',x=.12,y=.38)]}
    saved = validate_document(project,doc)
    after = deck_pages(build_posco_deck(project,saved,lambda _:IMAGE))
    assert [p['id'] for p in after]==[p['id'] for p in before]
    edited = next(o for o in after[0]['objects'] if o['id']==title['id'])
    assert edited['text']=='보존할 기존 편집 제목'
    assert (edited['x'],edited['y'])==pytest.approx((.12,.38))


def test_summary_uses_full_width_remarks_without_overlapping_sample_cards():
    project, doc = fixture()
    output = build_posco_deck(project,doc,lambda _:IMAGE)
    for slide, _ in output.pages:
        if output.page_info[slide]['kind'] != 'summary': continue
        remarks = box(named(slide,'Remarks box')[0])
        assert remarks[2] == pytest.approx(247.65)
        assert remarks[1]+remarks[3] < 183
        assert all(box(card)[1]+box(card)[3] <= remarks[1]-2 for card in named(slide,'Sample frame'))


def test_blank_requester_and_date_never_copy_the_reference_example():
    project, doc = fixture()
    doc['metadata'].update(client='',requester='',date='')
    slide = build_posco_deck(project,doc,lambda _:IMAGE).pages[0][0]
    assert ''.join(named(slide,'Cover requester')[0].xpath('.//a:t/text()',namespaces=NS)).strip() == '의뢰자:'
    assert ''.join(named(slide,'Cover completionDate')[0].xpath('.//a:t/text()',namespaces=NS)) == ''

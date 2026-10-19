"""The spectrum cover is the reference; TEM adds a cover, not a replacement body."""
from copy import deepcopy
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import pytest
from pptx import Presentation

from ahn import ppt_report
from ahn.report_branding import NS
from ahn.report_cover import COVER_NAME, TEM_FIELDS, cover_shapes
from ahn.tests.test_ppt_report import _project_with_sections
from app.report import renderers, tem_editable
from test_posco_report_template import sample_document
from xrd_workbench.test_posco_pptx import fixture
from xrd_workbench.test_sample_slide import IMAGE
from app.xrd_workbench.render.posco_pptx import build_posco_deck, named
from app.xrd_workbench.render.posco_preview import deck_pages


def shape_map(root):
    return {s.find('p:nvSpPr/p:cNvPr', NS).get('name'): s
            for s in root.findall('p:cSld/p:spTree/p:sp', NS)}


@pytest.mark.parametrize('code', ['FTIR', 'RAMAN'])
def test_reference_spectrum_cover_keeps_geometry_typography_and_direct_edit_names(tmp_path, code):
    document = sample_document(code, tmp_path / 'absent.png')
    root = ET.fromstring(renderers._pptx_title_slide(document))
    shapes = shape_map(root)
    for name, bounds, size in (
            ('Report title', (762000,1219200,9753600,1219200),5000),
            ('Report eyebrow', (762000,777240,7620000,365760),1700),
            ('Report subtitle', (762000,2438400,9601200,609600),2000),
            ('Meta card 0', (762000,3962400,2057400,822960),1250)):
        xf = shapes[name].find('p:spPr/a:xfrm',NS)
        assert tuple(int(xf.find(tag,NS).get(key)) for tag,key in
                     [('a:off','x'),('a:off','y'),('a:ext','cx'),('a:ext','cy')]) == bounds
        assert int(shapes[name].find('.//a:rPr',NS).get('sz')) == size


@pytest.mark.parametrize('template_available', [True,False])
def test_tem_cover_precedes_original_body_and_revision_updates_one_cover(tmp_path, monkeypatch, template_available):
    if not template_available:
        monkeypatch.setattr(ppt_report,'TEMPLATE_PATH',tmp_path/'absent.pptx')
    project = _project_with_sections(tmp_path, {'tem','stem','coating'})
    original = deepcopy(project)
    path = ppt_report.build_pptx(project,tmp_path/'tem.pptx')
    prs = Presentation(path)
    assert len(prs.slides) == 4
    assert prs.slides[0].element.cSld.get('name') == COVER_NAME
    assert all(s.element.cSld.get('name') != COVER_NAME for s in list(prs.slides)[1:])
    assert any('TEM 이미지 분석결과' in s.text for s in prs.slides[1].shapes if s.has_text_frame)
    model = tem_editable.initial_model(path,{})
    assert model['completionDate'] == '2026-07-08'
    _,layout,_ = tem_editable.inspect_pptx(path)
    refs = {ref for r in layout['pages'][0]['regions'] for ref in r['refs']}
    assert {'title','completionDate','transfer/requestNumber','transfer/operatorId'} <= refs
    # Repeated review never re-runs OCR or inserts another cover. A blank value
    # remains blank instead of becoming a placeholder or fabricated request.
    for index in range(2):
        model.update(title=f'TEM 검토 완료 {index}', completionDate='2026-10-20')
        model['transfer'].update(requestNumber='REQ-NEW',operatorId='검토자',equipmentCode='')
        revised = tmp_path/f'revision-{index}.pptx'
        tem_editable.revise_pptx(path,revised,model)
        with ZipFile(path) as before, ZipFile(revised) as after:
            for n in before.namelist():
                if n.startswith('ppt/media/') or (n.startswith('ppt/slides/slide') and n.endswith('.xml') and n!='ppt/slides/slide1.xml'):
                    left,right = before.read(n),after.read(n)
                    if n.endswith('.xml'):
                        # The existing editor reserializes XML namespace prefixes.
                        left,right = ET.tostring(ET.fromstring(left)),ET.tostring(ET.fromstring(right))
                    assert left==right, n
        prs = Presentation(revised)
        assert len(prs.slides)==4
        content = '\n'.join(s.text for s in prs.slides[0].shapes if s.has_text_frame)
        assert model['title'] in content and 'REQ-NEW' in content and '2026-10-20' in content
        assert 'TEM-EDGE-01' not in content
        path = revised
        model = tem_editable.initial_model(path,{})
    assert project == original


def test_xrd_cover_uses_reference_cards_preserves_all_metadata_and_edit_targets():
    project,doc = fixture()
    doc['metadata'].update(client='검증 의뢰기관',author='작성자',analysis_period='2026-10-01 ~ 2026-10-20')
    before=deepcopy((project,doc))
    deck=build_posco_deck(project,doc,lambda _:IMAGE)
    cover=deck.pages[0][0]
    for name in ('Report title','Report eyebrow','Report subtitle'):
        assert len(named(cover,name))==1
    assert len([s for s in cover.findall('p:cSld/p:spTree/p:sp',NS)
                if s.find('p:nvSpPr/p:cNvPr',NS).get('name','').startswith('Meta card ')])==10
    text=''.join(cover.itertext())
    for value in ('REQ-123','XRD-01','QA-OP','검증 의뢰기관','작성자','2026-10-01 ~ 2026-10-20'):
        assert value in text
    assert deck.edits[named(cover,'Report title')[0]]==dict(tab='overview',field='title')
    pages=deck_pages(deck)
    assert len(pages)==4 and pages[0]['kind']=='cover'
    assert (project,doc)==before
    for spec in cover_shapes('XRD','제목','설명',[(str(i),'') for i in range(10)],9906000,6858000):
        assert spec['x']+spec['cx']<=9906000
        assert spec['y']+spec['cy']<6630826  # Leave the original RIST logo clear.


def test_old_tem_report_without_cover_remains_editable(tmp_path):
    prs=Presentation()
    slide=prs.slides.add_slide(prs.slide_layouts[6])
    from pptx.util import Inches
    slide.shapes.add_textbox(Inches(1),Inches(1),Inches(5),Inches(1)).text='기존 TEM 결과'
    source=tmp_path/'old.pptx';target=tmp_path/'revised.pptx'
    prs.save(source)
    model=tem_editable.initial_model(source,{})
    model['sections'][0]['paragraphs'][0]='기존 보고서 수정'
    tem_editable.revise_pptx(source,target,model)
    revised=Presentation(target)
    assert len(revised.slides)==1
    assert revised.slides[0].shapes[0].text=='기존 보고서 수정'


def test_long_xrd_cover_fields_fit_without_truncation_or_an_extra_page():
    project,doc=fixture()
    doc['metadata'].update(client='긴'*120,report_number='의'*80,
                           equipment_code='장'*100,operator_id='실'*100)
    deck=build_posco_deck(project,doc,lambda _:IMAGE)
    assert len(deck.pages)==4
    cover=deck.pages[0][0]
    text=''.join(cover.itertext())
    for key in ('client','report_number','equipment_code','operator_id'):
        assert doc['metadata'][key] in text
    for shape in cover.findall('p:cSld/p:spTree/p:sp',NS):
        xf=shape.find('p:spPr/a:xfrm',NS)
        assert int(xf.find('a:off',NS).get('y'))+int(xf.find('a:ext',NS).get('cy'))<6630826

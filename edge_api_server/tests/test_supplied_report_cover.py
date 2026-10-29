"""Only the approved XRD cover background is shared; text and geometry stay intact."""
from copy import deepcopy
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from ahn.report_cover import SUPPLIED_COVER_MEDIA, SUPPLIED_COVER_NAME, supplied_cover_asset
from app.report import renderers, editable, direct_edit, canvas
from app.report.traceability import FIELDS, values_and_refs
from ahn.report_traceability import cover_shapes
from test_posco_report_template import sample_document, NS


@pytest.mark.parametrize('code', ['FTIR', 'RAMAN'])
def test_spectrum_cover_uses_xrd_artwork_and_keeps_canonical_edit_bindings(tmp_path, code):
    doc = sample_document(code, tmp_path/'missing.png')
    editable.ensure_editable_fields(doc, {'reportContext': {'client': '소재연구소', 'requester': '홍길동',
        'analyst': '분석자', 'equipmentName': '장비 모델 A'}}, code,
        editable.transfer_values({}, 'REQ-42', 'EQ-1', 'SSO-ID'))
    doc.generated_at = '2026-10-29'
    before = deepcopy(doc.to_dict())
    pptx = tmp_path/'report.pptx'
    renderers.render_pptx(doc, pptx)
    with ZipFile(pptx) as archive:
        root = ET.fromstring(archive.read('ppt/slides/slide1.xml'))
        image = root.find('p:cSld/p:spTree/p:pic', NS)
        assert image.find('p:nvPicPr/p:cNvPr', NS).get('name') == SUPPLIED_COVER_NAME
        assert image.find('p:spPr/a:xfrm/a:off', NS).attrib == dict(x='0', y='0')
        assert image.find('p:spPr/a:xfrm/a:ext', NS).attrib == dict(cx=str(renderers.PPTX_SLIDE_W), cy=str(renderers.PPTX_SLIDE_H))
        assert archive.read('ppt/media/'+SUPPLIED_COVER_MEDIA) == supplied_cover_asset()
        text = ' '.join(n.text or '' for n in root.findall('.//a:t', NS))
        assert doc.title in text and '의뢰기관: 소재연구소' in text and '2026-10-29' in text and 'REQ-42' in text
        # This is a background-only change. Keep every original text shape byte-equivalent.
        expected = [ET.fromstring(renderers._pptx_shape(**spec).replace('<p:sp>',
            f'<p:sp xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}">',1))
            for spec in cover_shapes('Raman' if code=='RAMAN' else 'FT-IR',doc.title,values_and_refs(doc)[0])]
        assert [ET.tostring(s).strip() for s in root.findall('p:cSld/p:spTree/p:sp',NS)] == [ET.tostring(s).strip() for s in expected]
        assert root.findall('p:cSld/p:spTree/p:pic',NS)[1].find('p:nvPicPr/p:cNvPr',NS).get('name')=='Template RIST logo'
        detail = ' '.join(ET.fromstring(archive.read('ppt/slides/slide2.xml')).itertext())
        assert all(v in detail for v in ['EQ-1', 'REQ-42', '장비 모델 A', '2026-10-29', '분석자'])
    payload = renderers._pptx_slide_payloads(doc)[0]
    assert SUPPLIED_COVER_NAME not in {o['name'] for _,o in canvas.objects(payload)[1]}
    layout = direct_edit.edit_pages(doc, pptx)
    rows = dict((key, i) for i,(key,_) in enumerate(FIELDS))
    for key in ['client','analyst']:
        assert any(r['refs'] == [f'section/report_traceability/rows/{rows[key]}/1'] for r in layout['pages'][0]['regions'])
    assert doc.to_dict() == before


def test_common_cover_artwork_is_exactly_the_registered_xrd_source():
    template = Path(__file__).parents[1]/'app/xrd_workbench/render/assets/xrd_default_20261029.pptx'
    with ZipFile(template) as archive:
        assert archive.read('ppt/media/image3.jpg') == supplied_cover_asset()


def test_spectrum_cover_empty_values_and_reordered_pages(tmp_path):
    doc = sample_document('FTIR', tmp_path/'missing.png')
    doc.generated_at = ''
    payloads = renderers._pptx_slide_payloads(doc)
    cover = payloads[0]
    root = ET.fromstring(cover['xml'])
    text = ' '.join(n.text or '' for n in root.findall('.//a:t', NS))
    assert '완료일: 미기재' in text and '2026-10-15' not in text
    doc.page_edits = {'order': [p['pageId'] for p in payloads[1:]]+[cover['pageId']]}
    out = tmp_path/'reordered.pptx'
    renderers.render_pptx(doc,out)
    with ZipFile(out) as archive:
        assert SUPPLIED_COVER_NAME.encode() in archive.read(f'ppt/slides/slide{len(payloads)}.xml')
        assert SUPPLIED_COVER_MEDIA.encode() in archive.read(f'ppt/slides/_rels/slide{len(payloads)}.xml.rels')

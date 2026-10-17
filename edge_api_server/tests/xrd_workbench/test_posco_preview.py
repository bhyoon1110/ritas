"""Preview and PowerPoint use one scene, not independently approximated report layouts."""
from copy import deepcopy

from lxml import etree as E

from app.xrd_workbench.render.posco_pptx import build_posco_deck, named, NS, box
from app.xrd_workbench.render.posco_preview import deck_pages, render_posco_preview
from app.xrd_workbench.render.sample_page import remark_targets
from .test_posco_pptx import fixture, IMAGE, deck as read_deck


def test_preview_exact_page_sequence_size_text_and_original_master_artwork():
    project,doc=fixture()
    before=deepcopy((project,doc))
    filled=build_posco_deck(project,doc,lambda _:IMAGE)
    pages=deck_pages(filled)
    _,slides,_=read_deck(filled.finish(doc))
    assert len(pages)==len(slides)
    assert [p['kind'] for p in pages[:4]]==['summary','sample','sample','cover']
    assert 'WPPF' in pages[1]['title'] and 'Phase Identification' in pages[2]['title']
    for page,slide in zip(pages,slides):
        assert page['width']==9906000/36000 and page['height']==190.5
        assert page['title']==''.join(named(slide,'Title')[0].xpath('.//a:t/text()',namespaces=NS))
        preview=''.join(''.join(i['lines']) for i in page['items'] if i['type']=='text')
        for text in slide.xpath('.//a:t/text()',namespaces=NS):
            assert ''.join(text.split()) in ''.join(preview.split()),text
        images=[i for i in page['items'] if i['type']=='image']
        assert [i['filename'] for i in images[:2]]==['Picture 7','Picture 14']
        # Master artwork matches across every page, not a look-alike gradient/logo.
        assert images[:2]==[i for i in pages[0]['items'] if i['type']=='image'][:2]
    assert before==(project,doc)


def test_template_table_positions_and_native_chart_are_shared_with_preview():
    project,doc=fixture()
    filled=build_posco_deck(project,doc,lambda _:IMAGE)
    pages=deck_pages(filled)
    for index in (1,2):
        for table in named(filled.pages[index][0],'Phase table'):
            x,y,w,h=box(table)
            assert any(i['type']=='rect' and i['x']==x and i['y']==y for i in pages[index]['items'])
    chart=next(i for i in pages[1]['items'] if i.get('filename')=='WPPF 정량 차트')
    assert b'10' in chart['data'] and b'11.1' in chart['data'] and b'12.2' in chart['data']
    native=E.fromstring(filled.charts[0][0])
    assert '#'+native.find('.//c:dPt/c:spPr/a:solidFill/a:srgbClr',NS).get('val') in chart['data'].decode()
    for slide,_ in filled.pages:
        for shape in slide.findall('.//p:sp',NS):
            if shape.find('.//a:hlinkClick',NS) is not None:
                assert len(shape.findall('p:txBody/a:p',NS))==1  # no two-line link pills


def test_edit_targets_safe_text_and_source_links_survive_posco_projection():
    project,doc=fixture()
    doc['samples'][0]['phase_caption']='수정한 상 설명'
    doc['samples'][0]['quantity_caption']='수정한 정량 설명'
    doc['summary']='<script>bad()</script> & 요약'
    filled=build_posco_deck(project,doc,lambda _:IMAGE,storage_id='qa',link_base='/api/v1/xrd/workbench')
    targets=[i['edit'] for p in deck_pages(filled) for i in p['items'] if i.get('edit')]
    for entry in doc['samples']:
        assert dict(tab=entry['sample_id'],field='display_name') in targets
        assert all(t in targets for t in remark_targets(entry))
    for field in ('phase_caption','quantity_caption'):
        assert dict(tab=doc['samples'][0]['sample_id'],field=field) in targets
    assert dict(tab='overview',field='report_number') in targets
    html=render_posco_preview(project,doc,lambda _:IMAGE,storage_id='qa',edit_links=True)
    assert '<script>bad()</script>' not in html and '&lt;script&gt;' in html
    assert 'data-edit=' in html and '/api/v1/xrd/workbench/api/files/qa/' in html
    assert html.count('background:url("data:image/svg+xml;base64,')==2  # master assets only once
    saved=render_posco_preview(project,doc,lambda _:IMAGE,storage_id='qa')
    assert 'data-edit=' not in saved and 'xrd-preview-edit' not in saved


def test_preview_graphs_use_scoped_renderer_and_do_not_modify_inputs():
    project,doc=fixture();doc['figure']['include']=True
    calls=[]
    def image(*args): calls.append(args);return IMAGE
    filled=build_posco_deck(project,doc,lambda _:IMAGE,image_renderer=image)
    assert len(calls)>=5  # comparison page, summary, every raw fallback
    overview=next(args[0] for args in calls if args[0].get('sample_legend'))
    assert not overview['show_markers'] and not overview['show_reference']
    comparison=calls[0][0]
    assert overview['curves']==comparison['curves']
    assert overview['spacing']==comparison['spacing']
    assert filled.pages

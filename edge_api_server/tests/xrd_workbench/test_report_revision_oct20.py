"""October template: cover, adaptive summary, rules-only notice and native WPPF table."""
from copy import deepcopy
import io

import pytest
from pypdf import PdfReader

from app.xrd_workbench.core.report import new_document, validate_document
from app.xrd_workbench.core.phase_class import phase_ref
from app.xrd_workbench.core.checklist import build_checklist
from app.xrd_workbench.render.posco_pptx import build_posco_deck, named, NS, box
from app.xrd_workbench.render.posco_preview import deck_pages
from app.xrd_workbench.render.pdf import render_pdf
from app.xrd_workbench.render.html import render_html
from app.xrd_workbench.render.summary_layout import classifier, notice, summary_pages, NOTICE
from .test_posco_pptx import fixture
from .test_sample_slide import sample, IMAGE
from .figure_fixtures import make_project


def text(slide):
    return ''.join(slide.xpath('.//a:t/text()',namespaces=NS))


def test_rules_use_reviewed_classes_only_and_empty_means_really_empty():
    project,doc=fixture();samples={s['id']:s for s in project['samples']}
    entries=doc['samples']
    statement,disclaimer=notice(entries,samples,classifier(doc))
    assert statement=='[시료 A] 샘플에서 미량상 및 추정상이 존재합니다.'
    assert disclaimer==NOTICE
    doc['phase_classes']['labels']=[v for v in doc['phase_classes']['labels'] if v['class']=='trace']
    statement,_=notice(entries,samples,classifier(doc))
    assert statement=='[시료 A] 샘플에서 미량상이 존재합니다.' and '추정상' not in statement
    doc['phase_classes']['labels']=[]
    doc['summary']='LLM이 작성했지만 사용하면 안 되는 문장'
    assert notice(entries,samples,classifier(doc))==('','')
    filled=build_posco_deck(project,doc,lambda _:IMAGE)
    summary=next(s for s,_ in filled.pages if filled.page_info[s]['kind']=='summary')
    assert 'LLM이' not in text(summary) and NOTICE not in text(summary)
    assert '미량상' not in text(summary) and '추정상' not in text(summary)


def test_all_twelve_samples_paginate_without_omission_or_a_four_sample_blocker():
    project=make_project([sample(str(i),n_phases=(i%4)+2,quantities=0) for i in range(12)])
    doc=new_document(project);doc['figure']['include']=False
    assert all(e['summary_included'] for e in doc['samples'])
    validate_document(project,doc)
    assert not any(i['code']=='too_many_summary_samples' for i in build_checklist(project,doc)['report'])
    before=deepcopy((project,doc))
    filled=build_posco_deck(project,doc,lambda _:IMAGE,image_renderer=lambda *a:IMAGE)
    summaries=[s for s,_ in filled.pages if filled.page_info[s]['kind']=='summary']
    assert len(summaries)>1
    all_titles=[text(shape) for slide in summaries for shape in slide.find('p:cSld/p:spTree',NS)
                if filled.edits.get(shape,{}).get('field')=='display_name']
    assert sorted(all_titles)==sorted('샘플명: '+e['display_name'] for e in doc['samples'])
    for slide in summaries:
        frames=named(slide,'Sample frame')
        assert {round(box(s)[0]) for s in frames} <= {143,207}
        assert all(box(s)[1]+box(s)[3]<=175 for s in frames)
    assert before==(project,doc)


def test_huge_phase_lists_continue_without_ellipsis_or_losing_last_phase():
    project=make_project([sample('Long',n_phases=90,quantities=0)])
    doc=new_document(project);doc['figure']['include']=False
    filled=build_posco_deck(project,doc,lambda _:IMAGE,image_renderer=lambda *a:IMAGE)
    summaries=[s for s,_ in filled.pages if filled.page_info[s]['kind']=='summary']
    assert len(summaries)>1
    body=''.join(text(s) for s in summaries)
    for i in range(90): assert f'Phase{i}' in body
    assert '…' not in body


@pytest.mark.parametrize('counts', [(1, 6, 2, 4, 1), (1, 24, 2), (2, 1)])
def test_summary_neighbors_match_tallest_box_in_ppt_and_preview(counts):
    project = make_project([sample(str(i), n_phases=count, quantities=0)
                            for i, count in enumerate(counts)])
    doc = new_document(project)
    doc['figure']['include'] = False
    doc['samples'][1]['display_name'] = '긴 시료명: 결정상 분석 결과를 확인하는 두 번째 샘플'
    samples = {s['id']: s for s in project['samples']}
    classify = classifier(doc)
    before = deepcopy((project, doc))
    # The row must grow to its longest neighbor, not shrink or clip that content.
    natural = [card for entry in doc['samples']
               for page in summary_pages([entry], samples, classify) for card in page]
    plan = summary_pages(doc['samples'], samples, classify)
    fitted = [card for page in plan for card in page]
    assert [c['lines'] for c in fitted] == [c['lines'] for c in natural]
    for start in range(0, len(fitted), 2):
        expected = max(c['height'] for c in natural[start:start+2])
        assert all(c['height'] == expected for c in fitted[start:start+2])

    deck = build_posco_deck(project, doc, lambda _: IMAGE, image_renderer=lambda *a: IMAGE)
    summaries = [slide for slide, _ in deck.pages if deck.page_info[slide]['kind'] == 'summary']
    previews = [page for page in deck_pages(deck) if page['kind'] == 'summary']
    assert len(summaries) == len(plan) == len(previews)
    for slide, page, cards in zip(summaries, previews, plan):
        frames = named(slide, 'Sample frame')
        assert len(frames) == len(cards)
        previous_bottom = None
        for top in sorted({c['top'] for c in cards}):
            row = [box(frame) for frame, card in zip(frames, cards) if card['top'] == top]
            assert len({(y, h) for _, y, _, h in row}) == 1
            _, y, _, h = row[0]
            if previous_bottom is not None:
                assert y == pytest.approx(previous_bottom + 3)
            previous_bottom = y + h
            assert previous_bottom <= 173
        for frame in frames:
            bounds = box(frame)
            assert any(item['type'] == 'rect' and
                       tuple(item[k] for k in ('x', 'y', 'w', 'h')) == bounds
                       for item in page['items'])
    assert before == (project, doc)


def test_wppf_table_and_links_match_reference_and_keep_unit_precision():
    project,doc=fixture()
    for phase in project['samples'][2]['phases']:
        doc['phase_classes']['labels'].append(dict(sample_id='Mix',phase=phase_ref(phase),origin='manual',**{'class':'major'}))
    filled=build_posco_deck(project,doc,lambda _:IMAGE)
    slide=next(s for s,_ in filled.pages if 'WPPF' in text(named(s,'Title')[0]))
    assert 'Phase0 [주요상]' in text(slide) and '# ICDD CARD ID' in text(slide)
    table=named(slide,'Quantity table')[0]
    rows=table.findall('.//a:tr',NS)
    assert len(rows)==4 and all(len(r.findall('a:tc',NS))==2 for r in rows)
    assert all(v['raw']+' wt%' in text(table) for v in project['samples'][2]['quantities'])
    assert box(table)[1]>box(named(slide,'Pie frame')[0])[1]
    for s,_ in filled.pages:
        if filled.page_info[s]['kind']=='sample':
            assert 'Measured Data (TXT)' not in text(s)
            assert '상매칭 Report 열기' in text(s)
            assert '측정 데이터(TXT) 열기' in text(s)


def test_pdf_html_and_ppt_scene_share_cover_geometry_values_and_portable_viewer_link():
    project,doc=fixture()
    filled=build_posco_deck(project,doc,lambda _:IMAGE)
    pages=deck_pages(filled)
    pdf=PdfReader(io.BytesIO(render_pdf(pages,doc)))
    assert len(pdf.pages)==len(filled.pages)
    for page in pdf.pages:
        assert abs(float(page.mediabox.width)-275.1666667*72/25.4)<.01
        assert abs(float(page.mediabox.height)-190.5*72/25.4)<.01
    assert all(len(named(slide,'RIST logo'))==1 for slide,_ in filled.pages)
    filled.number_pages()
    assert all(len(named(slide,'RIST logo'))==1 for slide,_ in filled.pages)
    full=''.join(p.extract_text() for p in pdf.pages)
    assert 'REQ-123' in full and 'XRD-01' in full and 'QA-OP' in full
    assert '10.0(1) wt%' in full
    uris=[str(a.get_object().get('/A',{}).get('/URI','')) for p in pdf.pages for a in p.get('/Annots',[])]
    assert '뷰어.html#view=multi' in uris
    html=render_html(pages,doc,'package')
    assert html.count('<section class="page"')==len(pdf.pages)
    assert 'href="뷰어.html#view=multi"' in html and 'contenteditable' not in html


def test_many_quantity_rows_continue_without_repeating_pie_or_losing_values():
    project=make_project([sample('Q',n_phases=3,quantities=32)])
    doc=new_document(project);doc['figure']['include']=False
    before=deepcopy((project,doc))
    filled=build_posco_deck(project,doc,lambda _:IMAGE,image_renderer=lambda *a:IMAGE)
    tables=[table for slide,_ in filled.pages for table in named(slide,'Quantity table')]
    assert len(tables)>1
    all_text=''.join(text(t) for t in tables)
    for q in project['samples'][0]['quantities']:assert q['raw']+' wt%' in all_text
    assert sum('WPPF' in text(named(s,'Title')[0]) for s,_ in filled.pages)==1
    assert before==(project,doc)


def test_maximum_length_sample_names_expand_notice_without_clipping_or_failure():
    project=make_project([sample(str(i),n_phases=1,quantities=0) for i in range(6)])
    doc=new_document(project);doc['figure']['include']=False
    for i,(s,e) in enumerate(zip(project['samples'],doc['samples'])):
        e['display_name']=str(i)+'긴시료명'*49
        doc['phase_classes']['labels'].append(dict(sample_id=s['id'],phase=phase_ref(s['phases'][0]),origin='manual',**{'class':'trace'}))
    filled=build_posco_deck(project,doc,lambda _:IMAGE,image_renderer=lambda *a:IMAGE)
    pages=[s for s,_ in filled.pages if filled.page_info[s]['kind']=='summary']
    assert len(pages)>1
    for s in pages:
        assert ''.join(NOTICE.split()) in ''.join(text(s).split())
        assert box(named(s,'Remarks box')[0])[3]>27
        assert box(named(s,'Overlay frame')[0])[3]>30
        assert all(box(f)[1]+box(f)[3]<=175 for f in named(s,'Sample frame'))


def test_long_quantity_names_move_to_wider_continuation_without_losing_tokens():
    project,doc=fixture()
    value='긴 정량 결정상 이름 '+('Oxide '*90)
    project['samples'][2]['quantities'][0]['phase']=value
    doc=new_document(project);doc['figure']['include']=False
    filled=build_posco_deck(project,doc,lambda _:IMAGE)
    tables=[t for s,_ in filled.pages for t in named(s,'Quantity table')]
    assert any(box(t)[2]>200 for t in tables)
    assert ''.join(value.split()) in ''.join(''.join(text(t) for t in tables).split())

from copy import deepcopy

from app.xrd_workbench.core.phase_class import phase_ref
from app.xrd_workbench.render.posco_pptx import build_posco_deck, render_report_pptx
from app.xrd_workbench.render.posco_preview import deck_pages
from .test_posco_pptx import fixture, IMAGE, deck as read_deck


def test_phase_table_cells_link_the_actual_sample_phase_and_native_cell():
    project, doc = fixture()
    pages=deck_pages(build_posco_deck(project,doc,lambda _:IMAGE))
    seen=set()
    for page in pages:
        objects={obj['id']:obj for obj in page['objects']}
        for item in page['items']:
            target=item.get('edit',{})
            if target.get('field') not in {'phase_class','ppt_cell'}:continue
            assert item['type'] == 'rect'  # The complete cell, not just a fragment of its text.
            cell=target.get('cell',target)
            table=objects[cell['objectId']]
            assert table['kind'] == 'table'
            actual=next(c for c in table['cells'] if (c['row'],c['col'])==(cell['row'],cell['col']))
            if target['field']=='phase_class':
                sample=next(s for s in project['samples'] if s['id']==target['sampleId'])
                assert target['phase'] == phase_ref(sample['phases'][cell['row']-1])
                assert cell['col']==0 and target['phase']['name'] in actual['text']
                seen.add(target['sampleId'])
            else:assert cell['row']==0 or cell['col'] in (1,2)
    assert seen == {sample['id'] for sample in project['samples']}


def test_targeted_formula_override_reaches_preview_and_ppt_without_changing_source():
    project,doc=fixture();before=deepcopy(project)
    page=next(p for p in deck_pages(build_posco_deck(project,doc,lambda _:IMAGE))
              if any(i.get('edit',{}).get('field')=='ppt_cell' and i['edit']['row']==1 and i['edit']['col']==1 for i in p['items']))
    cell=next(i['edit'] for i in page['items'] if i.get('edit',{}).get('field')=='ppt_cell' and i['edit']['row']==1 and i['edit']['col']==1)
    doc['canvasEdits']={page['id']:[dict(id=cell['objectId'],tableCells=[dict(row=1,col=1,text='Reviewed formula')])]}
    preview=deck_pages(build_posco_deck(project,doc,lambda _:IMAGE))
    assert any('Reviewed formula' in ''.join(i.get('lines',[])) for p in preview for i in p['items'])
    _,_,text=read_deck(render_report_pptx(project,doc,lambda _:IMAGE))
    assert 'Reviewed formula' in text and project==before


def test_criteria_text_is_in_both_preview_and_saved_ppt_with_its_own_edit_target():
    project,doc=fixture();doc['phase_classes']['criteria_text']='관측 피크와 원소 분석 결과를 함께 검토한 기준'
    pages=deck_pages(build_posco_deck(project,doc,lambda _:IMAGE))
    criteria=[i for p in pages for i in p['items'] if i.get('edit',{}).get('field')=='criteria_text']
    assert criteria and all(i['edit']=={'tab':'classes','field':'criteria_text'} for i in criteria)
    assert doc['phase_classes']['criteria_text'] in ''.join(''.join(i['lines']) for i in criteria)
    _,_,text=read_deck(render_report_pptx(project,doc,lambda _:IMAGE))
    assert doc['phase_classes']['criteria_text'] in text


def test_long_criteria_paginate_without_losing_lines_or_overlapping_result_tables():
    project,doc=fixture()
    lines=[f'검토 기준 {i:02} — 원자료 대조' for i in range(60)]
    doc['phase_classes']['criteria_text']='\n'.join(lines)
    pages=deck_pages(build_posco_deck(project,doc,lambda _:IMAGE))
    criteria=[p for p in pages if p['kind']=='classification']
    assert len(criteria)==3
    for page in criteria:
        for item in page['items']:
            if item.get('edit',{}).get('field')=='criteria_text':
                assert item['y']+item['h'] <= page['height']
    _,_,text=read_deck(render_report_pptx(project,doc,lambda _:IMAGE))
    for line in lines: assert line in text

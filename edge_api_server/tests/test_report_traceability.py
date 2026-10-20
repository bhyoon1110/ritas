from copy import deepcopy
import json
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import pytest
from ahn.report_traceability import FIELDS, traceability_shapes
from ahn.tests.test_ppt_report import _project_with_sections
from ahn.ppt_report import build_pptx
from app.report import editable, renderers, tem_editable
from app.report.traceability import values_and_refs
from app.report.direct_edit import edit_pages
from test_posco_report_template import sample_document


@pytest.mark.parametrize('code',['FTIR','RAMAN'])
def test_cover_fields_roundtrip_without_inferred_measurement_or_review(tmp_path,code):
    doc=sample_document(code,tmp_path/'absent.png')
    editable.ensure_editable_fields(doc,{'reportContext':{'client':'소재연구소','purpose':'구조 확인'}},code,
        editable.transfer_values({},'REQ-1','INSTRUMENT-42','measurer'))
    doc.traceability={'samples':['시료 A','시료 B'],'sourceSummary':'raw_data.xlsx','revisionOf':'old-job'}
    model=editable.editor_document(doc)
    section=next(s for s in model['sections'] if s['sectionId']=='report_traceability')
    row=dict((key,i) for i,(key,_) in enumerate(FIELDS))
    section['rows'][row['equipmentName']][1]='현장 장비 모델'
    section['rows'][row['measuredDate']][1]='2026-10-18'
    editable.apply_edits(doc,model,code)
    restored=editable.document_from_dict(doc.to_dict(),image_path=tmp_path/'absent.png')
    assert restored.traceability==doc.traceability
    values,refs=values_and_refs(restored)
    assert values['measuredDate']=='2026-10-18'
    assert values['equipmentName']=='현장 장비 모델'
    assert values['reviewer']=='' and values['analyst']==''
    assert values['operatorId']=='measurer'
    assert values['completionDate']=='2026-10-15'
    path=tmp_path/'report.pptx'; renderers.render_pptx(restored,path)
    regions=edit_pages(restored,path)['pages'][1]['regions']
    all_refs={ref for region in regions for ref in region['refs']}
    assert set(refs['measuredDate']) <= all_refs
    assert 'transfer/equipmentCode' in all_refs
    assert all('recordId' not in ref and 'revisionOf' not in ref for ref in all_refs)
    assert not any(p.get('editing',{}).get('sectionId')=='report_traceability'
        for p in renderers._pptx_slide_payloads(doc))  # Cover only, no duplicate body pages.


def test_tem_fact_edit_and_server_record_do_not_touch_body(tmp_path):
    path=build_pptx(_project_with_sections(tmp_path,{'tem'}),tmp_path/'original.pptx')
    tem_editable.stamp_record(path,'job-1')
    model=tem_editable.initial_model(path,{})
    assert model['transfer']['equipmentCode']==''  # No invented equipment identity.
    measured=next(s for s in model['sections'] if s['heading']=='표지 · 측정일 / 기간')
    assert measured['paragraphs']==['']
    measured['paragraphs']=['2026-10-18']
    target=tmp_path/'revised.pptx'
    tem_editable.revise_pptx(path,target,model)
    tem_editable.stamp_record(target,'job-2','job-1')
    reopened=tem_editable.initial_model(target,{})
    assert next(s for s in reopened['sections'] if s['sectionId']==measured['sectionId'])['paragraphs']==['2026-10-18']
    assert not any(s['heading'].endswith('작업 / 프로젝트 ID') for s in reopened['sections'])
    with ZipFile(path) as before,ZipFile(target) as after:
        assert b'job-2' in after.read('ppt/slides/slide2.xml')
        assert b'job-1' in after.read('ppt/slides/slide2.xml')
        assert ET.tostring(ET.fromstring(before.read('ppt/slides/slide3.xml')))==ET.tostring(ET.fromstring(after.read('ppt/slides/slide3.xml')))


def test_xrd_history_preserves_previous_facts_and_uses_server_actor(tmp_path):
    from app.xrd_workbench.storage import Storage, report_actor
    from app.xrd_workbench.core.report import new_document,validate_document
    store=Storage(tmp_path)
    project=store.files.create([('S1.txt',b'#Intensity_unit=cps\n10 1\n11 2\n')],{})
    sid=project['storage_id']
    doc=new_document(project)
    doc['metadata'].update(measured_date='2026-10-18',equipment_name='XRD model',reviewer='')
    doc['audit']={'saved_by':'forged-user'}
    token=report_actor.set('actual-user')
    try: first=store.reports.save(sid,project,doc,0)
    finally: report_actor.reset(token)
    history=store.reports.path(sid).parent/'history'
    assert json.loads((history/'revision-1.audit.json').read_text())['saved_by']=='actual-user'
    second=deepcopy(first);second['metadata']['reviewer']='실제 검토자'
    second=store.reports.save(sid,project,second,1)
    old=json.loads((store.reports.path(sid).parent/'history/revision-1.json').read_text())
    assert old==first and old['metadata']['reviewer']==''
    audit=json.loads((history/'revision-2.audit.json').read_text())
    assert audit['saved_by'] is None  # Unknown is not the owner/previous editor.
    assert audit['previous_revision']==1
    assert validate_document(project,second)['metadata']['measured_date']=='2026-10-18'


def test_shared_cover_fields_keep_brand_area_clear_and_no_overlap():
    for width in (9906000,12192000):
        specs=traceability_shapes('XRD','검증 보고서',{'requestNumber':'R'*80,'client':'긴'*120},width)
        fields=[s for s in specs if s['name'].startswith('Trace ') and s['name'][6:] in
                {'requestNumber','client','requester','receivedDate','purpose'}]
        for a,b in zip(fields,fields[1:]): assert a['y']+a['cy']<=b['y']
        for spec in specs:
            assert spec['x']+spec['cx']<=width
            assert spec['y']+spec['cy']<6630826

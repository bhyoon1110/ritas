from collections import Counter
from zipfile import ZipFile

import pytest

from app.report.direct_edit import edit_pages
from app.report.editable import ensure_editable_fields, transfer_values
from app.report.model import ReportDocument, ReportSection, ReportTable
from app.report.renderers import render_pptx
from app.report.editor_variables import resolve_changes


@pytest.mark.parametrize('project', ['FT-IR', 'RAMAN'])
def test_actual_ppt_regions_keep_source_indices_across_wrapping_and_pages(tmp_path, project):
    document = ReportDocument('test','직접 편집 검증',project,{},'2026-10-15')
    long_text = '장비 환경과 측정 조건을 확인합니다. '*80
    document.sections = [ReportSection('summary','분석 요약',paragraphs=[long_text],source='user'),
        ReportSection('experiment_conditions','실험조건',table=ReportTable(['대상','항목','값'],
            [['공통','온도','23°C'],['공통','긴 조건',long_text],['공통','빈값','']])),
        ReportSection('raman_peaks','피크 정보',table=ReportTable(['시료','위치','강도'],
            [[str(n),'1000',long_text if n == 1 else '123'] for n in range(12)]))]
    ensure_editable_fields(document,{'reportConditionFields':[
        {'target':'공통','label':'온도','value':'23°C'},
        {'target':'공통','label':'긴 조건','value':long_text},
        {'target':'공통','label':'빈값','value':''}]},project,transfer_values({}))
    path = tmp_path/'report.pptx'
    render_pptx(document,path)
    layout = edit_pages(document,path)
    assert len(layout['pages']) > 10
    counts = Counter()
    for page in layout['pages']:
        for region in page['regions']:
            assert 0 <= region['x'] < 1 and 0 <= region['y'] < 1
            assert 0 < region['w'] <= 1 and 0 < region['h'] <= 1
            assert region['x'] + region['w'] <= 1.001
            assert region['y'] + region['h'] <= 1.001
            for ref in region['refs']:
                counts[ref] += 1
                if not ref.startswith('section/'):
                    continue
                _, sid, key, *indices = ref.split('/')
                section = document.section(sid)
                assert section
                if key == 'rows':
                    r,c = map(int,indices)
                    assert r < len(section.table.rows) and c < len(section.table.columns)
    assert counts['section/experiment_conditions/rows/1/2'] > 1
    assert counts['section/raman_peaks/rows/1/2'] > 1
    assert counts['section/raman_peaks/rows/11/2'] >= 1
    assert counts['section/summary/paragraphs/0'] > 1
    assert counts['completionDate'] == 1  # Cover date remains editable, without footer hotspots.
    if project == 'RAMAN':
        assert counts['section/experiment_conditions/rows/0/0'] == 0  # Common target column omitted in PPT.
        assert counts['section/experiment_conditions/rows/0/2'] >= 1
    with ZipFile(path) as archive:
        assert 'ppt/media/posco-banner.jpeg' in archive.namelist()
        assert 'ppt/media/rist-logo.png' in archive.namelist()


def test_named_fields_are_literal_bounded_and_keep_sample_specific_values():
    value = {'title':'{{공통.온도}}', 'transfer':dict(requestNumber='',limsExperimentCode='',equipmentCode='',operatorId=''),
        'completionDate':'', 'variables':[{'name':'설정','value':'{{공통.온도}}','unit':''}], 'auxiliary':{},
        'sections':[{'sectionId':'experiment_conditions','heading':'조건','paragraphs':[], 'bullets':[],
                     'rows':[['공통','온도','23°C'],['sample','온도','18°C']]},
                    {'sectionId':'summary','heading':'요약','paragraphs':['<script> {{설정}}'], 'bullets':[], 'rows':None}]}
    resolved = resolve_changes(value)
    assert resolved['title'] == '23°C'
    assert resolved['sections'][1]['paragraphs'] == ['<script> 23°C']  # Escaped by format renderers, never evaluated.
    assert resolved['sections'][0]['rows'][1][2] == '18°C'
    assert value['title'] == '{{공통.온도}}'
    value['variables'] = [{'name':'a','value':'x'*1000,'unit':''},{'name':'b','value':'{{a}}'*5,'unit':''}]
    with pytest.raises(ValueError,match='4,000'):
        resolve_changes(value)

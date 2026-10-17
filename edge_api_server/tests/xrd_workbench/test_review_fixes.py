"""Findings of the A–D review: viewer offset rule survives delivery, normalization stays off in
old reports, the slide link row never loses links, and model sentences cannot change facts."""

import pytest
from .figure_fixtures import make_project, make_sample

from app.xrd_workbench.core.notes import verify_note_text
from app.xrd_workbench.core.report import merge_notes, new_document, validate_document
from app.xrd_workbench.render.sample_page import link_row
from app.xrd_workbench.render.theme import LEFT, WIDTH
from app.xrd_workbench.render.viewer_state import prepare_state


def test_the_offset_rule_marker_survives_a_prepared_viewer_state():
    project = make_project([make_sample('s1', '시료 1')])
    state = {'version': 1, 'projectId': project['id'], 'offsetRule': 2}
    assert prepare_state(project, state)['offsetRule'] == 2
    with pytest.raises(ValueError):
        prepare_state(project, dict(state, offsetRule=3))


def test_an_old_report_with_normalization_on_opens_with_it_off():
    project = make_project([make_sample('s1', '시료 1')])
    document = new_document(project)
    document['figure']['normalize'] = True
    assert validate_document(project, document)['figure']['normalize'] is False


LINKS = [
    dict(
        label='Measured Data (TXT)', source_id='m', filename='a.txt', href='Measured%20Data/a.txt'
    ),
    dict(label='상매칭 Report', source_id='p', filename='a.pdf', href='Report/a.pdf'),
    dict(label='정량(WPPF) Report', source_id='w', filename='b.pdf', href='Report/b.pdf'),
]


@pytest.mark.parametrize('name', ['시료', '아주 긴 시료 표시명 ' * 20])
def test_the_link_row_keeps_every_link_and_shortens_only_the_name(name):
    row = link_row(name, LINKS, 170)
    assert len(row) == 4
    assert row[0]['href'] == 'Report/a.pdf'
    assert row[0]['label'].endswith('의 Report 및 데이터 확인하기')
    assert all(i['x'] + i['w'] <= LEFT + WIDTH + 1e-6 for i in row)
    if len(name) > 100:
        assert '…' in row[0]['label']


PHASES = ['PhaseA', 'PhaseB', 'Si', 'SiO2']
QUANT = {
    'text': '정량(WPPF) Report에 기재된 값은 PhaseA 30.1(2) wt%; PhaseB 69.6(2) wt%입니다.',
    'evidence': [
        {'filename': 'r1.pdf', 'page': 2, 'field': 'Weight fraction', 'raw': '30.1(2)'},
        {'filename': 'r1.pdf', 'page': 3, 'field': 'Weight fraction', 'raw': '69.6(2)'},
    ],
}
SUM = {
    'text': '보고된 중량 분율 중심값의 합계는 99.70 wt%입니다. 원문 값을 100 wt%로 재조정하지 않았습니다.',
    'evidence': [],
}
PHASE_LIST = {'text': '상매칭 Report에는 SiO2의 1개 상이 기재되어 있습니다.', 'evidence': []}


@pytest.mark.parametrize(
    'candidate, text',
    [
        (QUANT, 'PhaseA는 69.6(2) wt%, PhaseB는 30.1(2) wt%로 기재되어 있습니다.'),  # swapped
        (QUANT, 'PhaseA 30.1(2) wt%, PhaseB 69.6(2) wt%이며 PhaseZ 1 wt%도 있습니다.'),
        (QUANT, 'PhaseA 30.1(2) wt%, PhaseB 69.6(2) wt%이며 PhaseZ도 기재되어 있습니다.'),
        (SUM, '중심값 합계 99.70 wt%를 100 wt%로 재조정했습니다.'),  # negation flipped
        (PHASE_LIST, '상매칭 Report에는 Si의 1개 상이 기재되어 있습니다.'),  # substring phase
        (PHASE_LIST, '상매칭 Report에는 SiO2의 2개 상이 기재되어 있습니다.'),  # page-like number
    ],
)
def test_a_sentence_that_changes_the_facts_is_rejected(candidate, text):
    assert verify_note_text(candidate, text, PHASES)


def test_a_reworded_sentence_with_the_same_facts_in_order_passes():
    text = '정량(WPPF) Report에 따르면 PhaseA는 30.1(2) wt%, PhaseB는 69.6(2) wt%입니다.'
    assert verify_note_text(QUANT, text, PHASES) is None
    assert (
        verify_note_text(
            SUM, '중심값 합계는 99.70 wt%이며 100 wt%로 재조정하지 않았습니다.', PHASES
        )
        is None
    )


def test_a_saved_model_sentence_that_fails_the_check_reverts_to_the_template():
    sample = make_sample('s1', '시료 1')
    sample['reports'] = {'wppf_report': {'filename': 'r.pdf'}}
    sample['quantities'] = [
        dict(phase='PhaseA', raw='12.34(5)', value=12.34, unit='wt%', page=2),
        dict(phase='PhaseB', raw='87.66(5)', value=87.66, unit='wt%', page=2),
    ]
    project = make_project([sample])
    document = new_document(project)
    entry = document['samples'][0]
    note = next(n for n in entry['notes'] if n['kind'] == 'reported_quantities')
    template = note['text']
    merge_notes(
        entry,
        dict(
            candidates=[dict(n) for n in entry['notes']],
            selected_ids=[n['id'] for n in entry['notes']],
            texts={note['id']: 'PhaseA 50 wt%'},
            status='llm',
            message='m',
            model_used='m',
        ),
    )
    clean = validate_document(project, document)
    kept = next(n for n in clean['samples'][0]['notes'] if n['id'] == note['id'])
    assert kept['text'] == template and kept['origin'] == 'rules' and not kept['edited']

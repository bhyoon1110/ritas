"""The model writes the 특이사항 sentences; code accepts a sentence only when every number and
phase name in it comes from that candidate's own text and evidence, and none of them is dropped."""

import json

import httpx
import pytest

from app.xrd_workbench.core.notes import validate_writing, verify_note_text
from app.xrd_workbench.llm import select_notes

PHASES = ['PhaseA', 'PhaseB', 'PhaseC']
QUANT = {
    'id': 'q',
    'kind': 'reported_quantities',
    'mandatory': True,
    'text': '정량(WPPF) Report에 기재된 값은 PhaseA 12.34(5) wt%; PhaseB 87.66(5) wt%입니다.',
    'evidence': [
        {'filename': 'r.pdf', 'page': 2, 'field': 'Weight fraction', 'raw': '12.34(5)'},
        {'filename': 'r.pdf', 'page': 2, 'field': 'Weight fraction', 'raw': '87.66(5)'},
    ],
}
SUM = {
    'id': 'w',
    'kind': 'weight_sum',
    'mandatory': False,
    'text': '보고된 중량 분율 중심값의 합계는 101.23 wt%입니다.',
    'evidence': [],
}


def test_a_faithful_rewrite_is_accepted():
    text = 'WPPF 정량 결과 PhaseA는 12.34(5) wt%, PhaseB는 87.66(5) wt%로 기재되어 있습니다.'
    assert verify_note_text(QUANT, text, PHASES) is None


@pytest.mark.parametrize(
    'text',
    [
        'PhaseA 12.34(5) wt%, PhaseB 87.66(5) wt%, PhaseC 1.0 wt%입니다.',  # invented phase and value
        'PhaseA 12.34 wt%, PhaseB 87.66(5) wt%입니다.',  # uncertainty dropped from the token
        'PhaseA 12.34(5) wt%입니다.',  # PhaseB and its value dropped
        'PhaseA 12.34(5) wt%, PhaseB 87.66(5) wt%로 순도가 높습니다.',  # interpretation
        'PhaseA 12.35(5) wt%, PhaseB 87.66(5) wt%입니다.',  # changed number
        '',
    ],
)
def test_a_sentence_that_adds_changes_or_drops_facts_is_rejected(text):
    assert verify_note_text(QUANT, text, PHASES)


def reply(notes, sample_id='s'):
    return json.dumps({'sample_id': sample_id, 'notes': notes}, ensure_ascii=False)


def test_rejected_sentences_fall_back_to_the_template_and_mandatory_ones_return():
    good = '중량 분율 중심값을 더하면 101.23 wt%입니다.'
    result = validate_writing('s', [QUANT, SUM], reply([{'id': 'w', 'text': good}]), PHASES)
    assert result['ids'] == ['w', 'q']
    assert result['texts'] == {'w': good}
    bad = validate_writing(
        's', [QUANT, SUM], reply([{'id': 'q', 'text': 'PhaseA만 있습니다.'}]), PHASES
    )
    assert bad['texts'] == {} and bad['replaced'] == 1 and bad['ids'] == ['q']


@pytest.mark.parametrize(
    'raw',
    [
        reply([], 'other'),
        reply([{'id': 'invented', 'text': 'x'}]),
        reply([{'id': 'q', 'text': 'x'}, {'id': 'q', 'text': 'y'}]),
        reply([{'id': 'q', 'text': 'x', 'extra': 1}]),
        json.dumps({'sample_id': 's', 'notes': [], 'free': '새 상 PhaseC'}),
        '[]',
    ],
)
def test_a_malformed_or_foreign_reply_is_rejected(raw):
    with pytest.raises(ValueError):
        validate_writing('s', [QUANT, SUM], raw, PHASES)


def sample_with_quantities():
    return {
        'id': 's',
        'name': 's',
        'phases': [],
        'reports': {'wppf_report': {'filename': 'r.pdf'}},
        'sources': [],
        'quantities': [
            {'phase': 'PhaseA', 'raw': '12.34(5)', 'value': 12.34, 'unit': 'wt%', 'page': 2},
            {'phase': 'PhaseB', 'raw': '87.66(5)', 'value': 87.66, 'unit': 'wt%', 'page': 2},
        ],
    }


def ollama(content):
    def handler(request):
        return httpx.Response(200, json={'message': {'content': content}})

    return httpx.MockTransport(handler)


def test_the_model_text_is_used_when_it_passes_and_the_message_says_so():
    sample = sample_with_quantities()
    from app.xrd_workbench.core.notes import build_candidates

    cid = next(c['id'] for c in build_candidates(sample) if c['kind'] == 'reported_quantities')
    text = 'PhaseA는 12.34(5) wt%, PhaseB는 87.66(5) wt%로 Report에 기재되어 있습니다.'
    settings = {'provider': 'ollama', 'model': 'm-pass'}
    result = select_notes(sample, settings, ollama(reply([{'id': cid, 'text': text}])), PHASES)
    assert result['status'] == 'llm'
    assert result['texts'] == {cid: text}
    assert '근거' in result['message']


def test_an_unverifiable_model_sentence_keeps_the_template_and_says_how_many():
    sample = sample_with_quantities()
    from app.xrd_workbench.core.notes import build_candidates

    cid = next(c['id'] for c in build_candidates(sample) if c['kind'] == 'reported_quantities')
    settings = {'provider': 'ollama', 'model': 'm-fail'}
    result = select_notes(
        sample, settings, ollama(reply([{'id': cid, 'text': 'PhaseA 50 wt%'}])), PHASES
    )
    assert result['status'] == 'llm' and result['texts'] == {}
    assert '1개' in result['message']


def project_with_quantities():
    from .figure_fixtures import make_project

    sample = sample_with_quantities()
    sample.update(
        measurement=None, observed_peaks=[], assignments=[], reference_peaks=[], warnings=[]
    )
    return make_project([sample])


def test_merged_model_text_is_saved_and_survives_validation_while_it_still_verifies():
    from app.xrd_workbench.core.report import merge_notes, new_document, validate_document

    project = project_with_quantities()
    document = new_document(project)
    entry = document['samples'][0]
    note = next(n for n in entry['notes'] if n['kind'] == 'reported_quantities')
    text = 'PhaseA는 12.34(5) wt%, PhaseB는 87.66(5) wt%로 Report에 기재되어 있습니다.'
    merge_notes(
        entry,
        dict(
            candidates=[{k: v for k, v in n.items() if k in QUANT} for n in entry['notes']],
            selected_ids=[n['id'] for n in entry['notes']],
            texts={note['id']: text},
            status='llm',
            message='m',
            model_used='m',
        ),
    )
    merged = next(n for n in entry['notes'] if n['id'] == note['id'])
    assert merged['text'] == text and merged['origin'] == 'llm' and not merged['edited']
    clean = validate_document(project, document)
    kept = next(n for n in clean['samples'][0]['notes'] if n['id'] == note['id'])
    assert kept['text'] == text and kept['origin'] == 'llm' and not kept['edited']
    # A model text that no longer verifies goes back to the template; an author edit (origin
    # user, as the editor marks it) stays the author's.
    merged['text'] = 'PhaseA 50 wt%'
    clean = validate_document(project, document)
    kept = next(n for n in clean['samples'][0]['notes'] if n['id'] == note['id'])
    assert kept['origin'] == 'rules' and kept['text'] == note['text'] and not kept['edited']
    merged.update(origin='user', edited=True)
    clean = validate_document(project, document)
    kept = next(n for n in clean['samples'][0]['notes'] if n['id'] == note['id'])
    assert kept['origin'] == 'user' and kept['edited'] and kept['text'] == 'PhaseA 50 wt%'

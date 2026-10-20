from copy import deepcopy

import pytest
from .fixture_data import example_project

from app.xrd_workbench.core.notes import build_candidates, validate_settings
from app.xrd_workbench.llm import select_notes


@pytest.mark.real_data
def test_actual_notes_preserve_raw_quantities_and_do_not_infer_scientific_quality():
    samples = example_project()['samples']
    first = build_candidates(samples[0])
    second = build_candidates(samples[1])
    text = ' '.join(c['text'] for c in first + second)
    for sample, candidates in zip(samples[:2], (first, second)):
        own = ' '.join(c['text'] for c in candidates)
        assert all(q['raw'] in own for q in sample['quantities'])
        if sample['quantities'] and all(q['unit'] == 'wt%' for q in sample['quantities']):
            total = sum(float(q['raw'].split('(')[0]) for q in sample['quantities'])
            if abs(total - 100) >= 0.01:
                assert f'{total:.2f}' in own
    assert not any(word in text for word in ['순도', '미검출', '합격', 'Cu K', '결정립 크기는 0'])
    assert all(c['evidence'] for c in first + second)


@pytest.mark.real_data
def test_missing_model_returns_honest_rule_fallback():
    result = select_notes(example_project()['samples'][0], {'provider': 'none'})
    assert result['status'] == 'rules'
    assert result['selected_ids']
    assert result['model_used'] is None


@pytest.mark.parametrize(
    'url',
    [
        'https://example.com',
        'http://localhost.evil.test',
        'http://127.0.0.1:1234@evil.test',
        'file:///tmp/x',
    ],
)
def test_model_endpoint_is_local_only(url):
    with pytest.raises(ValueError):
        validate_settings({'provider': 'ollama', 'base_url': url, 'model': 'm'})


@pytest.mark.real_data
def test_registered_unparsed_wppf_is_not_claimed_missing():
    sample = deepcopy(example_project()['samples'][0])
    sample['quantities'] = []
    notes = build_candidates(sample)
    assert not any(c['kind'] == 'no_quantity' for c in notes)
    assert any(c['kind'] == 'quantity_extraction_review' for c in notes)
    assert '미제공' not in ' '.join(c['text'] for c in notes)


def test_manual_quantities_use_their_image_source_and_do_not_claim_pdf_results():
    from app.xrd_workbench.core.notes import build_candidates

    sample = {
        'id': 's',
        'name': 's',
        'phases': [],
        'reports': {},
        'sources': [],
        'quantities': [
            {
                'phase': 'PhaseA',
                'raw': '12.34(5)',
                'value': 13,
                'unit': 'wt%',
                'page': None,
                'source_filename': 's_정량.png',
                'origin': 'manual',
            }
        ],
    }
    note = next(n for n in build_candidates(sample) if n['kind'] == 'reported_quantities')
    assert '직접 확인·입력' in note['text'] and 'Report에 기재된' not in note['text']
    assert note['evidence'][0]['filename'] == 's_정량.png'


def test_analysis_correction_note_keeps_original_and_reviewed_tokens():
    from app.xrd_workbench.core.notes import build_candidates

    sample = {
        'id': 's',
        'name': 's',
        'phases': [],
        'reports': {},
        'sources': [],
        'quantities': [],
        'analysis_review': {
            'changes': [
                {
                    'filename': 's_Report.pdf',
                    'page': 2,
                    'table': 0,
                    'row': 4,
                    'col': 3,
                    'original': '12.34(5)',
                    'value': '12.84(5)',
                    'reason': '원문 확인 수정',
                }
            ]
        },
    }
    note = next(n for n in build_candidates(sample) if n['kind'] == 'analysis_changes')
    assert '12.34(5)' in note['text'] and '12.84(5)' in note['text']
    assert note['mandatory'] and note['evidence'][0]['filename'] == 's_Report.pdf'
    sample['analysis_review']['changes'] *= 11
    before = deepcopy(sample)
    note = next(n for n in build_candidates(sample) if n['kind'] == 'analysis_changes')
    assert '총 11건' in note['text'] and '서버의 분석 자료 검토 화면' in note['text']
    assert '분석검토.json' not in note['text']
    assert sample == before

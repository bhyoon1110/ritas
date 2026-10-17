from copy import deepcopy

import pytest
from .figure_fixtures import make_project, make_sample

from app.xrd_workbench.core.presets import apply_preset, settings_from_document, validate_preset
from app.xrd_workbench.core.report import new_document


def project():
    return make_project([make_sample(i, i, [], []) for i in ('a', 'b')])


def settings(**change):
    base = {
        'figure': {
            'include': True,
            'order_mode': 'similarity',
            'spacing': 12.5,
            'x_range': [10, 80],
            'normalize': False,
            'show_markers': False,
            'show_reference': True,
            'caption': '캡션',
        },
        'quantity_table': {'include': False},
        'phase_classes': {'criteria_text': '작성자 기준', 'trace_percent': 2},
    }
    base.update(change)
    return base


def test_a_valid_preset_is_cleaned_and_keeps_its_values():
    clean = validate_preset('내 설정', settings())
    assert clean['name'] == '내 설정'
    assert clean['settings'] == settings()


def test_the_name_is_trimmed_and_must_be_one_to_eighty_characters():
    assert validate_preset('  이름  ', settings())['name'] == '이름'
    for bad in ('', '   ', 'x' * 81, None, 5):
        with pytest.raises(ValueError):
            validate_preset(bad, settings())


@pytest.mark.parametrize(
    'bad',
    [
        {'figure': {'order_mode': 'random'}},
        {'figure': {'spacing': -1}},
        {'figure': {'x_range': [5, 5]}},
        {'figure': {'caption': 'x' * 301}},
        {'figure': {'groups': [{'title': 'g', 'samples': ['a']}]}},
        {'figure': 'bad'},
        {'quantity_table': {'include': 'yes'}},
        {'quantity_table': None},
        {'phase_classes': {'criteria_text': 'x' * 1501, 'trace_percent': 2}},
        {'phase_classes': {'criteria_text': '', 'trace_percent': 101}},
        {'phase_classes': {'criteria_text': '', 'trace_percent': 'x'}},
        {'phase_classes': {'criteria_text': '', 'trace_percent': True}},
        {'phase_classes': 'bad'},
    ],
)
def test_invalid_settings_are_rejected(bad):
    with pytest.raises(ValueError):
        validate_preset('p', settings(**bad))


def test_a_missing_block_or_an_empty_percent_uses_the_defaults():
    clean = validate_preset('p', {})
    assert clean['settings']['figure']['include'] is False
    assert clean['settings']['quantity_table'] == {'include': True}
    assert clean['settings']['phase_classes'] == {'criteria_text': '', 'trace_percent': None}
    assert 'groups' not in clean['settings']['figure']


def test_a_preset_never_stores_samples_labels_or_cover_information():
    clean = validate_preset('p', {**settings(), 'metadata': {'title': 'x'}, 'samples': []})
    assert set(clean['settings']) == {'figure', 'quantity_table', 'phase_classes'}


def test_settings_taken_from_a_document_leave_out_groups_and_labels():
    document = new_document(project())
    document['figure'].update(spacing=3, groups=[{'title': 'g', 'samples': ['a']}])
    document['phase_classes'].update(
        criteria_text='기준',
        rule={'trace_max_percent': 4},
        labels=[
            {
                'sample_id': 'a',
                'phase': {'name': 'P', 'card': '', 'db': ''},
                'class': 'major',
                'origin': 'manual',
            }
        ],
    )
    taken = settings_from_document(document)
    assert taken['figure']['spacing'] == 3 and 'groups' not in taken['figure']
    assert taken['phase_classes'] == {'criteria_text': '기준', 'trace_percent': 4}
    assert validate_preset('p', taken)['settings'] == taken


def test_a_document_without_a_rule_gives_no_percent():
    assert settings_from_document(new_document(project()))['phase_classes']['trace_percent'] is None


def test_applying_replaces_the_settings_and_keeps_groups_labels_cover_and_text():
    document = new_document(project())
    document['metadata']['title'] = '내 보고서'
    document['summary'] = '요약 문장'
    document['samples'][0]['manual_note'] = '직접 쓴 문장'
    document['figure'].update(groups=[{'title': 'g', 'samples': ['a']}])
    label = {
        'sample_id': 'a',
        'phase': {'name': 'P', 'card': '', 'db': ''},
        'class': 'major',
        'origin': 'manual',
    }
    document['phase_classes'].update(
        labels=[label], rule={'trace_max_percent': 7}, criteria_text='이전'
    )
    before = deepcopy(document)
    result = apply_preset(document, validate_preset('p', settings())['settings'])
    assert document == before
    assert result['figure']['spacing'] == 12.5 and result['figure']['order_mode'] == 'similarity'
    assert result['figure']['groups'] == [{'title': 'g', 'samples': ['a']}]
    assert result['quantity_table'] == {'include': False}
    assert result['phase_classes']['criteria_text'] == '작성자 기준'
    assert result['phase_classes']['labels'] == [label]
    assert result['phase_classes']['rule'] == {'trace_max_percent': 7}
    assert result['metadata'] == document['metadata'] and result['summary'] == '요약 문장'
    assert result['samples'] == document['samples']


def test_applying_a_preset_never_fills_a_class_by_itself():
    document = new_document(project())
    result = apply_preset(document, validate_preset('p', settings())['settings'])
    assert result['phase_classes']['labels'] == [] and result['phase_classes']['rule'] is None


def test_extract_and_apply_match_the_shared_cases():
    import json
    from pathlib import Path

    cases = json.loads(
        (Path(__file__).parent / 'cases' / 'preset_cases.json').read_text(encoding='utf-8')
    )
    for case in cases:
        if 'document' in case:
            assert settings_from_document(case['document']) == case['settings'], case['name']
        else:
            before = deepcopy(case['target'])
            assert apply_preset(case['target'], case['apply']) == case['expected'], case['name']
            assert case['target'] == before

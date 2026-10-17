from copy import deepcopy

import pytest
from .figure_fixtures import make_project, make_trace_sample

from app.xrd_workbench.core.report import apply_trace_rule, new_document, validate_document

PHASE = {'name': 'PhaseA', 'card': '00-001', 'db': 'DB1'}


def project(*samples):
    return make_project(list(samples) or [make_trace_sample('a'), make_trace_sample('b')])


def label(sample_id, cls='major', origin='manual', phase=PHASE, percent=2):
    value = {'sample_id': sample_id, 'phase': dict(phase), 'class': cls, 'origin': origin}
    if origin == 'rule':
        value['percent'] = percent
    return value


def test_new_document_starts_with_nothing_classified():
    assert new_document(project())['phase_classes'] == {
        'criteria_text': '',
        'rule': None,
        'labels': [],
        'retained': [],
    }


def test_a_saved_document_without_phase_classes_opens_unclassified():
    p = project()
    saved = new_document(p)
    del saved['phase_classes']
    assert validate_document(p, saved)['phase_classes']['labels'] == []


def test_valid_classes_round_trip():
    p = project()
    doc = new_document(p)
    doc['phase_classes'].update(
        criteria_text='작성자 기준',
        rule={'trace_max_percent': 2},
        labels=[label('a', 'major'), label('b', 'trace', 'rule')],
    )
    assert validate_document(p, doc)['phase_classes'] == doc['phase_classes']


@pytest.mark.parametrize(
    'change',
    [
        {'labels': [label('zzz')]},
        {'labels': [label('a', cls='미분류')]},
        {'labels': [label('a', cls='other')]},
        {'labels': [label('a', origin='llm')]},
        {'labels': [label('a'), label('a', 'trace')]},
        {'labels': [dict(label('a'), phase='PhaseA')]},
        {'labels': [dict(label('a'), phase={'name': 'PhaseA'})]},
        {'labels': 'bad'},
        {'criteria_text': 'x' * 1501},
        {'rule': {'trace_max_percent': -1}},
        {'rule': {'trace_max_percent': 101}},
        {'rule': {'trace_max_percent': 'x'}},
        {'rule': 'bad'},
        {'retained': 'bad'},
    ],
)
def test_invalid_phase_classes_are_rejected(change):
    p = project()
    doc = new_document(p)
    doc['phase_classes'].update(change)
    with pytest.raises(ValueError):
        validate_document(p, doc)


def test_a_class_for_a_phase_that_is_gone_is_retained_not_rejected():
    p = project()
    doc = new_document(p)
    gone = {'name': 'PhaseGone', 'card': '', 'db': ''}
    doc['phase_classes']['labels'] = [label('a', phase=gone), label('b')]
    result = validate_document(p, doc)['phase_classes']
    assert result['labels'] == [label('b')]
    assert result['retained'] == [label('a', phase=gone)]
    # Validating again keeps it in retained (no automatic restore, no duplicate).
    again = validate_document(p, {**doc, 'phase_classes': result})['phase_classes']
    assert again['retained'] == [label('a', phase=gone)]


def test_classes_of_a_sample_left_out_of_the_report_are_kept():
    p = project()
    doc = new_document(p)
    doc['samples'][1]['included'] = False
    doc['phase_classes']['labels'] = [label('a'), label('b')]
    assert validate_document(p, doc)['phase_classes']['labels'] == [label('a'), label('b')]


def test_a_phase_with_the_same_name_but_another_card_is_a_different_phase():
    p = project()
    doc = new_document(p)
    other_card = dict(PHASE, card='99-999')
    doc['phase_classes']['labels'] = [label('a', phase=other_card)]
    result = validate_document(p, doc)['phase_classes']
    assert result['labels'] == [] and result['retained'] == [label('a', phase=other_card)]


def test_validation_does_not_modify_the_input():
    p = project()
    doc = new_document(p)
    doc['phase_classes']['labels'] = [label('a')]
    before = deepcopy(doc)
    validate_document(p, doc)
    assert doc == before


def trace_project():
    weak = make_trace_sample('weak', heights=(20.0, 10.0, 1000.0))
    strong = make_trace_sample('strong', heights=(900.0, 800.0, 1000.0))
    return make_project([weak, strong])


def test_the_rule_fills_only_trace_phases_and_records_the_percent():
    p = trace_project()
    result = apply_trace_rule(p, new_document(p), 2)['phase_classes']
    assert result['labels'] == [label('weak', 'trace', 'rule')]
    assert result['rule'] == {'trace_max_percent': 2}


def test_the_rule_does_not_overwrite_a_direct_choice():
    p = trace_project()
    doc = new_document(p)
    doc['phase_classes']['labels'] = [label('weak', 'major', 'manual')]
    result = apply_trace_rule(p, doc, 2)['phase_classes']
    assert result['labels'] == [label('weak', 'major', 'manual')]


def test_reapplying_with_a_lower_percent_removes_labels_the_rule_set_earlier():
    p = trace_project()
    first = apply_trace_rule(p, new_document(p), 2)
    second = apply_trace_rule(p, first, 1)['phase_classes']
    assert second['labels'] == []
    assert second['rule'] == {'trace_max_percent': 1}


def test_rule_labels_of_other_classes_and_direct_labels_survive_a_reapply():
    p = trace_project()
    doc = new_document(p)
    doc['phase_classes']['labels'] = [label('strong', 'presumed', 'manual')]
    result = apply_trace_rule(p, doc, 2)['phase_classes']
    assert label('strong', 'presumed', 'manual') in result['labels']


def test_the_rule_skips_samples_left_out_of_the_report():
    p = trace_project()
    doc = new_document(p)
    doc['samples'][0]['included'] = False
    assert apply_trace_rule(p, doc, 2)['phase_classes']['labels'] == []


def test_the_rule_does_not_modify_its_input_and_rejects_a_bad_percent():
    p = trace_project()
    doc = new_document(p)
    before = deepcopy(doc)
    apply_trace_rule(p, doc, 2)
    assert doc == before
    with pytest.raises(ValueError):
        apply_trace_rule(p, doc, 150)


def test_applying_verdicts_matches_the_shared_cases():
    import json
    from pathlib import Path

    from app.xrd_workbench.core.report import apply_trace_verdicts

    cases = json.loads(
        (Path(__file__).parent / 'cases' / 'phase_class_cases.json').read_text(encoding='utf-8')
    )
    for case in cases:
        document = {
            'phase_classes': {**new_document(project())['phase_classes'], 'labels': case['labels']}
        }
        result = apply_trace_verdicts(document, case['evaluation'], case['percent'])
        assert result['phase_classes']['labels'] == case['expected']['labels'], case['name']
        assert result['phase_classes']['rule'] == case['expected']['rule'], case['name']
        assert document['phase_classes']['labels'] == case['labels'], 'input must not change'


def test_a_rule_label_needs_the_percent_it_was_applied_with():
    p = project()
    doc = new_document(p)
    bad = label('a', 'trace', 'rule')
    del bad['percent']
    doc['phase_classes']['labels'] = [bad]
    with pytest.raises(ValueError):
        validate_document(p, doc)
    doc['phase_classes']['labels'] = [dict(bad, percent=101)]
    with pytest.raises(ValueError):
        validate_document(p, doc)


def test_a_direct_label_carries_no_percent():
    p = project()
    doc = new_document(p)
    doc['phase_classes']['labels'] = [dict(label('a', 'major'), percent=2)]
    assert validate_document(p, doc)['phase_classes']['labels'] == [label('a', 'major')]


def test_the_rule_stores_the_percent_on_each_label_it_sets_and_keeps_older_ones():
    p = trace_project()
    first = apply_trace_rule(p, new_document(p), 5)
    first['samples'][0]['included'] = False
    second = apply_trace_rule(p, first, 1)['phase_classes']
    assert second['labels'] == [label('weak', 'trace', 'rule', percent=5)]
    assert second['rule'] == {'trace_max_percent': 1}

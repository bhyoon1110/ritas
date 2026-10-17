from copy import deepcopy

import pytest
from .figure_fixtures import make_project, make_sample

from app.xrd_workbench.core.report import new_document, validate_document


def project():
    return make_project([make_sample(i, i, [], []) for i in ('a', 'b', 'c')])


def test_new_document_includes_the_figure_with_defaults():
    figure = new_document(project())['figure']
    assert figure == dict(
        include=True,
        order_mode='report',
        spacing=None,
        x_range=None,
        normalize=False,
        show_markers=True,
        show_reference=True,
        caption='',
        groups=[],
    )


def test_a_saved_document_without_figure_opens_with_the_figure_off():
    p = project()
    saved = new_document(p)
    del saved['figure']
    assert validate_document(p, saved)['figure']['include'] is False


def test_valid_figure_round_trips():
    p = project()
    doc = new_document(p)
    doc['figure'].update(
        spacing=12.5,
        x_range=[10, 80],
        normalize=False,
        order_mode='similarity',
        caption='비교',
        groups=[{'title': '계열 1', 'samples': ['a', 'b']}],
    )
    assert validate_document(p, doc)['figure'] == doc['figure']


@pytest.mark.parametrize(
    'change',
    [
        {'order_mode': 'random'},
        {'spacing': -1},
        {'spacing': 'x'},
        {'x_range': [5, 5]},
        {'x_range': [1]},
        {'x_range': [10, 'a']},
        {'include': 'yes'},
        {'caption': 'x' * 301},
        {'groups': [{'title': '', 'samples': ['a']}]},
        {'groups': [{'title': 'g', 'samples': ['zzz']}]},
        {'groups': [{'title': 'g', 'samples': ['a']}, {'title': 'h', 'samples': ['a']}]},
        {'groups': [{'title': 'g', 'samples': ['a', 'a']}]},
        {'groups': 'bad'},
    ],
)
def test_invalid_figure_values_are_rejected(change):
    p = project()
    doc = new_document(p)
    doc['figure'].update(change)
    with pytest.raises(ValueError):
        validate_document(p, doc)


def test_group_members_left_out_of_the_report_are_dropped_and_empty_groups_removed():
    p = project()
    doc = new_document(p)
    doc['samples'][1]['included'] = False
    doc['figure']['groups'] = [
        {'title': 'g', 'samples': ['a', 'c']},
        {'title': 'only b', 'samples': ['b']},
    ]
    assert validate_document(p, doc)['figure']['groups'] == [{'title': 'g', 'samples': ['a', 'c']}]


def test_validation_does_not_modify_the_input():
    p = project()
    doc = new_document(p)
    before = deepcopy(doc)
    validate_document(p, doc)
    assert doc == before

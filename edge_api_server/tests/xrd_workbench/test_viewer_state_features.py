import copy

import pytest
from .figure_fixtures import make_project, make_sample

from app.xrd_workbench.render.viewer_state import prepare_state, prepare_views

A = ('PhaseA', 'c1', 'DB')
B = ('PhaseB', 'c2', 'DB')


def project():
    return make_project(
        [
            make_sample('a', 'a', [A], [(1, 21.0, ['PhaseA'])]),
            make_sample('b', 'b', [B], [(1, 22.0, ['PhaseB'])]),
        ]
    )


def state(**extra):
    return {'version': 1, 'projectId': 'fig-project', **extra}


def test_a_highlight_by_name_or_by_name_card_and_database_is_kept():
    p = project()
    by_name = prepare_state(p, state(highlight={'name': 'PhaseA', 'card': None, 'db': None}))
    assert by_name['highlight'] == {'name': 'PhaseA', 'card': None, 'db': None}
    full = prepare_state(p, state(highlight={'name': 'PhaseB', 'card': 'c2', 'db': 'DB'}))
    assert full['highlight'] == {'name': 'PhaseB', 'card': 'c2', 'db': 'DB'}


def test_no_highlight_stays_none_and_a_state_without_the_key_has_none():
    p = project()
    assert prepare_state(p, state(highlight=None))['highlight'] is None
    assert 'highlight' not in prepare_state(p, state())


@pytest.mark.parametrize(
    'bad',
    [
        'PhaseA',
        [],
        {'card': None},
        {'name': '', 'card': None, 'db': None},
        {'name': 5, 'card': None, 'db': None},
        {'name': 'PhaseA', 'card': 5, 'db': None},
        {'name': 'PhaseA', 'card': None, 'db': ['x']},
        {'name': 'Unknown', 'card': None, 'db': None},
        {'name': 'PhaseA', 'card': 'other', 'db': None},
        {'name': 'x' * 301, 'card': None, 'db': None},
        {'name': 'PhaseA', 'card': None, 'db': None, 'extra': 1},
    ],
)
def test_an_invalid_highlight_is_rejected(bad):
    with pytest.raises(ValueError):
        prepare_state(project(), state(highlight=bad))


def test_a_highlight_on_a_phase_left_out_of_the_delivery_is_dropped_not_fatal():
    p = project()
    target = make_project([p['samples'][0]])
    ok = prepare_state(p, state(highlight={'name': 'PhaseA', 'card': None, 'db': None}), target)
    assert ok['highlight']['name'] == 'PhaseA'
    dropped = prepare_state(
        p, state(highlight={'name': 'PhaseB', 'card': None, 'db': None}), target
    )
    assert dropped['highlight'] is None
    # A phase that does not exist in the project at all is still refused.
    with pytest.raises(ValueError):
        prepare_state(p, state(highlight={'name': 'Nope', 'card': None, 'db': None}), target)


def view(name='보기', **extra):
    return {'name': name, 'state': state(markers=True, **extra)}


def test_views_are_cleaned_and_each_state_is_validated():
    p = project()
    views = prepare_views(p, [view('  첫 보기  '), view('둘째', visible=['a'])])
    assert [v['name'] for v in views] == ['첫 보기', '둘째']
    assert views[0]['state']['markers'] is True and views[1]['state']['visible'] == ['a']
    assert prepare_views(p, None) == [] and prepare_views(p, []) == []


def test_views_drop_samples_left_out_of_the_report():
    p = project()
    target = make_project([p['samples'][0]])
    views = prepare_views(p, [view(visible=['a', 'b'], order=['b', 'a'])], target)
    assert views[0]['state']['visible'] == ['a'] and views[0]['state']['order'] == ['a']


@pytest.mark.parametrize(
    'bad',
    [
        'x',
        {'name': 'x'},
        [5],
        [{'name': '', 'state': state()}],
        [{'name': 'x' * 81, 'state': state(markers=True)}],
        [{'name': 'a', 'state': {}}],
        [{'name': 'a', 'state': {'version': 1, 'projectId': 'other'}}],
        [{'name': 'a', 'state': state(markers='yes')}],
        [{'state': state(markers=True)}],
        [{'name': 'a', 'state': state(markers=True), 'extra': 1}],
    ],
)
def test_invalid_views_are_rejected(bad):
    with pytest.raises(ValueError):
        prepare_views(project(), bad)


def test_duplicate_names_and_more_than_twenty_views_are_rejected():
    p = project()
    with pytest.raises(ValueError, match='이름'):
        prepare_views(p, [view('같음'), view('같음')])
    assert len(prepare_views(p, [view(f'v{i}') for i in range(20)])) == 20
    with pytest.raises(ValueError, match='20개'):
        prepare_views(p, [view(f'v{i}') for i in range(21)])


def test_validation_does_not_modify_the_input():
    p = project()
    views = [view('보기', visible=['a'])]
    before = copy.deepcopy((p, views))
    prepare_views(p, views)
    assert (p, views) == before


def test_a_view_highlight_on_a_phase_left_out_of_the_report_is_dropped_not_fatal():
    p = project()
    target = make_project([p['samples'][0]])
    lit = {'name': 'PhaseB', 'card': None, 'db': None}
    views = prepare_views(p, [view(highlight=lit)], target)
    assert views[0]['state']['highlight'] is None
    kept = prepare_views(p, [view(highlight={'name': 'PhaseA', 'card': None, 'db': None})], target)
    assert kept[0]['state']['highlight']['name'] == 'PhaseA'
    with pytest.raises(ValueError):
        prepare_views(p, [view(highlight={'name': 'Nope', 'card': None, 'db': None})], target)


def test_new_viewer_settings_are_accepted_and_validated():
    p = project()
    phase_id = p['samples'][0]['phases'][0]['id']
    ok = prepare_state(
        p,
        state(
            viewMode='single',
            markerSize=11,
            originStyle=True,
            unknownMarkers=True,
            phaseLabels=True,
            phaseSymbols={phase_id: 'star'},
            offsets={'raw': {'spacing': -250}},
        ),
    )
    assert ok['viewMode'] == 'single' and ok['markerSize'] == 11
    assert ok['originStyle'] is True
    assert ok['unknownMarkers'] is True and ok['phaseLabels'] is True
    assert ok['phaseSymbols'] == {phase_id: 'star'}
    assert ok['offsets']['raw']['spacing'] == -250
    for bad in (
        {'viewMode': 'grid'},
        {'markerSize': 0},
        {'markerSize': 'x'},
        {'originStyle': 'true'},
        {'originStyle': 1},
        {'unknownMarkers': 'yes'},
        {'phaseSymbols': {phase_id: 'skull'}},
        {'phaseSymbols': {'nope': 'star'}},
    ):
        with pytest.raises(ValueError):
            prepare_state(p, state(**bad))


def test_unknown_peak_marker_symbol_and_colour_are_validated_and_kept():
    clean = prepare_state(project(), state(unknownSymbol='diamond', unknownColor='#aa0000'))
    assert clean['unknownSymbol'] == 'diamond' and clean['unknownColor'] == '#aa0000'
    assert prepare_state(project(), state(unknownSymbol='x'))['unknownSymbol'] == 'x'
    for bad in ({'unknownSymbol': 'sparkle'}, {'unknownColor': 'red'}, {'unknownColor': 5}):
        with pytest.raises(ValueError):
            prepare_state(project(), state(**bad))

import math

from .figure_fixtures import make_document, make_project, make_sample

from app.xrd_workbench.core import figure as fig

A = ('PhaseA', '00-001', 'DB1')
B = ('PhaseB', '00-002', 'DB1')


def s(sid, phases, peaks=None):
    peaks = peaks if peaks is not None else [(i, 20 + i, [p[0]]) for i, p in enumerate(phases, 1)]
    return make_sample(sid, sid, phases, peaks)


def test_phase_key_requires_name_card_and_database_to_match():
    base = dict(name='PhaseA', card='1', db='X')
    assert fig.phase_key(base) == ('PhaseA', '1', 'X')
    assert fig.phase_key(base) != fig.phase_key(dict(base, card='2'))
    assert fig.phase_key(base) != fig.phase_key(dict(base, db='Y'))


def test_key_set_counts_only_phases_with_an_observed_assigned_peak():
    sample = s('s1', [A, B], peaks=[(1, 21.0, ['PhaseA'])])
    assert fig.phase_key_set(sample) == {fig.phase_key(sample['phases'][0])}


def test_peak_outside_the_measurement_range_or_without_observed_row_is_not_marked():
    sample = s('s1', [A], peaks=[(1, 21.0, ['PhaseA'])])
    match = [dict(phase='PhaseA', hkl='')]
    sample['assignments'].append(dict(no=2, x=999.0, x_raw='999', page=1, matches=match))
    sample['assignments'].append(dict(no=3, x=22.0, x_raw='22', page=1, matches=match))
    marks = fig.observed_markers(sample, sample['phases'][0])
    assert [m['no'] for m in marks] == [1]


def test_similarity_is_jaccard_and_zero_for_empty_sets():
    assert fig.similarity({1, 2}, {2, 3}) == 1 / 3
    assert fig.similarity(set(), {1}) == 0.0
    assert fig.similarity(set(), set()) == 0.0


def test_similarity_order_chains_nearest_ties_by_report_order_and_empty_last():
    keys = {'a': {1, 2}, 'e': set(), 'b': {3}, 'c': {1, 2, 3}, 'd': {1, 2}}
    expected = ['a', 'd', 'c', 'b', 'e']
    assert fig.similarity_order(['a', 'e', 'b', 'c', 'd'], keys) == expected
    assert fig.similarity_order(['a', 'e', 'b', 'c', 'd'], keys) == expected


def test_similarity_order_tie_keeps_report_order_and_all_empty_is_unchanged():
    assert fig.similarity_order(['a', 'b', 'c'], {'a': {1}, 'b': {2}, 'c': {3}}) == ['a', 'b', 'c']
    assert fig.similarity_order(['x', 'y'], {'x': set(), 'y': set()}) == ['x', 'y']


def test_identical_groups_lists_only_repeated_non_empty_compositions():
    keys = {'a': {1}, 'b': {2}, 'c': {1}, 'd': set(), 'e': set()}
    assert fig.identical_groups(['a', 'b', 'c', 'd', 'e'], keys) == [['a', 'c']]


def test_composition_note_names_added_and_removed_phases():
    ka, kb = ('PhaseA', '', ''), ('PhaseB', '', '')
    assert fig.composition_note(None, {ka}) == ''
    assert fig.composition_note({ka}, {ka}) == '앞 시료와 상 구성 동일'
    assert fig.composition_note({ka}, {kb}) == '+PhaseB, −PhaseA'


def plan_for(samples, **figure):
    project = make_project(samples)
    return fig.build_plan(project, make_document(project, **figure))


def with_formula(sample, *formulas):
    for phase, formula in zip(sample['phases'], formulas):
        phase['formula'] = formula
    return sample


def test_near_formulas_do_not_merge_distinct_phase_identities():
    a1 = with_formula(s('s1', [A]), 'Xa0.08 Yb0.01 O2 Qd0.91')
    a2 = with_formula(
        make_sample('s2', 's2', [('PhaseA2', '99-999', 'DB2')], [(1, 21.0, ['PhaseA2'])]),
        'Xa0.08 Yb0.01 O2 Qd0.9',
    )
    panel = plan_for([a1, a2])['panels'][0]
    assert len(panel['phases']) == 2
    row = panel['phases'][0]
    assert (row['name'], row['card'], row['formula']) == (
        'PhaseA',
        '00-001',
        'Xa0.08 Yb0.01 O2 Qd0.91',
    )
    assert {m['phase_row'] for c in panel['curves'] for m in c['markers']} == {0, 1}


def test_different_formulas_are_separate_rows_and_without_a_formula_the_name_decides():
    a1 = with_formula(s('s1', [A]), 'Ti O2')
    b1 = with_formula(s('s2', [B]), 'Ca C O3')
    assert len(plan_for([a1, b1])['panels'][0]['phases']) == 2
    plain1, plain2 = s('s3', [A]), s('s4', [A])
    assert len(plan_for([plain1, plain2])['panels'][0]['phases']) == 1


def test_two_phases_of_one_sample_are_never_merged_even_with_the_same_formula():
    both = with_formula(s('s1', [A, B]), 'Ti O2', 'Ti O2')
    other = with_formula(s('s2', [A, B]), 'Ti O2', 'Ti O2')
    panel = plan_for([both, other])['panels'][0]
    assert [p['name'] for p in panel['phases']] == ['PhaseA', 'PhaseB']
    assert len({p['symbol'] for p in panel['phases']}) == 2


def test_same_phase_is_one_row_with_one_symbol_across_samples():
    panel = plan_for([s('s1', [A, B]), s('s2', [A])])['panels'][0]
    assert [p['name'] for p in panel['phases']] == ['PhaseA', 'PhaseB']
    rows = {m['phase_row'] for c in panel['curves'] for m in c['markers']}
    assert rows == {0, 1}


def test_markers_sit_at_the_reported_x_and_above_the_nearest_measured_value():
    sample = s('s1', [A], peaks=[(1, 21.0, ['PhaseA'])])
    panel = plan_for([sample])['panels'][0]
    marker = panel['curves'][0]['markers'][0]
    assert marker['x'] == 21.0
    i = sample['measurement']['x'].index(21.0)
    assert marker['y'] > sample['measurement']['y'][i]


def test_samples_without_axis_or_unit_are_excluded_with_a_reason_and_listed():
    ok = s('s1', [A])
    bad = make_sample('s2', 's2', [A], [(1, 21.0, ['PhaseA'])], unit=None)
    panel = plan_for([ok, bad])['panels'][0]
    assert [c['sample_id'] for c in panel['curves']] == ['s1']
    assert panel['excluded'] == [
        {'sample_id': 's2', 'name': 's2', 'reason': '축 또는 강도 단위 미확인'}
    ]


def test_different_intensity_unit_is_excluded_from_raw_but_not_from_normalized():
    a = s('s1', [A])
    b = make_sample('s2', 's2', [A], [(1, 21.0, ['PhaseA'])], unit='counts')
    raw = plan_for([a, b])['panels'][0]
    assert [c['sample_id'] for c in raw['curves']] == ['s1']
    assert raw['excluded'][0]['reason'] == '강도 단위가 달라 원강도 비교에서 제외'
    normalized = plan_for([a, b], normalize=True)['panels'][0]
    assert [c['sample_id'] for c in normalized['curves']] == ['s1', 's2']


def test_all_samples_excluded_gives_an_empty_panel_with_reasons():
    bad = make_sample('s1', 's1', [A], [], unit=None)
    panel = plan_for([bad])['panels'][0]
    assert panel['curves'] == [] and panel['excluded']


def test_auto_spacing_follows_the_viewer_rule_and_user_spacing_wins():
    sample = s('s1', [A])
    span = max(sample['measurement']['y']) - min(sample['measurement']['y'])
    assert plan_for([sample])['panels'][0]['spacing'] == round(max(1, span) * 0.15)
    assert plan_for([sample], normalize=True)['panels'][0]['spacing'] == 15
    assert plan_for([sample], spacing=42)['panels'][0]['spacing'] == 42


def test_x_range_defaults_to_the_measured_range_in_whole_degrees_and_user_range_wins():
    sample = s('s1', [A])
    xs = sample['measurement']['x']
    assert plan_for([sample])['panels'][0]['x_range'] == [math.floor(xs[0]), math.ceil(xs[-1])]
    assert plan_for([sample], x_range=[20, 30])['panels'][0]['x_range'] == [20, 30]


def test_similarity_mode_orders_curves_inside_each_panel_only():
    a, b, c = s('a', [A]), s('b', [B]), s('c', [A])
    project = make_project([a, b, c])
    document = make_document(project, order_mode='similarity')
    panel = fig.build_plan(project, document)['panels'][0]
    assert [x['sample_id'] for x in panel['curves']] == ['a', 'c', 'b']
    assert [e['sample_id'] for e in document['samples']] == ['a', 'b', 'c']


def test_groups_make_one_panel_each_and_leftovers_go_to_other():
    a, b, c = s('a', [A]), s('b', [B]), s('c', [A])
    panels = plan_for([a, b, c], groups=[{'title': '계열 1', 'samples': ['c', 'a']}])['panels']
    assert [p['title'] for p in panels] == ['계열 1', '기타']
    assert [x['sample_id'] for x in panels[0]['curves']] == ['a', 'c']
    assert [x['sample_id'] for x in panels[1]['curves']] == ['b']


def test_samples_not_included_in_the_report_are_not_drawn():
    a, b = s('a', [A]), s('b', [B])
    project = make_project([a, b])
    document = make_document(project)
    document['samples'][1]['included'] = False
    panel = fig.build_plan(project, document)['panels'][0]
    assert [c['sample_id'] for c in panel['curves']] == ['a']


def test_sample_without_phases_is_drawn_without_markers_and_ranks_last_by_similarity():
    none = make_sample('n', 'n', [], [])
    a = s('a', [A])
    panel = plan_for([none, a], order_mode='similarity')['panels'][0]
    assert [c['sample_id'] for c in panel['curves']] == ['a', 'n']
    assert panel['curves'][1]['markers'] == []


def test_normalized_curve_peaks_at_100_and_original_values_are_untouched():
    sample = s('s1', [A])
    original = list(sample['measurement']['y'])
    panel = plan_for([sample], normalize=True)['panels'][0]
    assert max(panel['curves'][0]['base']) == 100
    assert sample['measurement']['y'] == original


def test_reference_rows_use_only_the_reports_reference_peaks():
    sample = make_sample(
        's1', 's1', [A], [(1, 21.0, ['PhaseA'])], references={'PhaseA': [(21.0, 100), (30.0, 40)]}
    )
    panel = plan_for([sample])['panels'][0]
    assert panel['phases'][0]['ref'] == [[21.0, 100], [30.0, 40]]


def test_stack_offsets_put_the_first_curve_on_top():
    assert fig.stack_offsets(3, 10) == [20, 10, 0]


def test_similarity_summary_reports_order_identical_groups_and_notes():
    a, b, c = s('a', [A]), s('b', [B]), s('c', [A])
    project = make_project([a, b, c])
    summary = fig.similarity_summary(project, make_document(project))
    assert summary['order'] == ['a', 'c', 'b']
    assert summary['identical'] == [['a', 'c']]


def many_phases(count):
    return [(f'Phase{i}', f'c{i}', 'DB') for i in range(count)]


def test_markers_have_their_own_colour_and_shape_per_phase_and_pairs_stay_unique_to_36():
    phases = many_phases(36)
    peaks = [(i + 1, 20 + i, [p[0]]) for i, p in enumerate(phases)]
    panel = plan_for([make_sample('s1', 's1', phases, peaks)])['panels'][0]
    rows = panel['phases']
    assert [r['color'] for r in rows[:4]] == ['#8261c9', '#df9b29', '#199c78', '#c44d76']
    assert [r['symbol'] for r in rows[:4]] == ['square', 'triangle', 'star', 'diamond']
    assert len({(r['color'], r['symbol']) for r in rows}) == 36


def test_the_same_phase_keeps_its_colour_and_symbol_in_every_group_panel():
    a, b, c = s('a', [A]), s('b', [B]), s('c', [B, A])
    panels = plan_for(
        [a, b, c],
        groups=[{'title': 'g1', 'samples': ['a']}, {'title': 'g2', 'samples': ['b', 'c']}],
    )['panels']
    style = {}
    for panel in panels:
        for row in panel['phases']:
            current = (row['color'], row['symbol'])
            assert style.setdefault(row['key'], current) == current
    assert len(style) == 2 and len(set(style.values())) == 2  # one symbol each, same black


def test_styles_do_not_depend_on_the_order_mode():
    samples = [s('a', [A]), s('b', [B]), s('c', [A])]
    report = plan_for(samples)['panels'][0]['phases']
    similar = plan_for(samples, order_mode='similarity')['panels'][0]['phases']
    by_key = lambda rows: {r['key']: (r['color'], r['symbol']) for r in rows}  # noqa: E731
    assert by_key(report) == by_key(similar)


def test_summary_notes_compare_with_the_previous_sample_in_report_order():
    a, b, c = s('a', [A]), s('b', [B]), s('c', [A])
    project = make_project([a, b, c])
    notes = fig.similarity_summary(project, make_document(project))['notes']
    assert notes['a'] == ''
    assert notes['b'] == '+PhaseB, −PhaseA'
    assert notes['c'] == '+PhaseA, −PhaseB'


def test_missing_reference_peaks_are_not_borrowed_from_a_different_phase():
    bare = with_formula(s('s1', [A]), 'Ti O2')
    bare['reference_peaks'] = []  # listed in the report but no d-I rows
    rich = with_formula(
        make_sample(
            's2',
            's2',
            [('PolyB', '00-009', 'DB1')],
            [(1, 22.0, ['PolyB'])],
            references={'PolyB': [(25.0, 100), (35.0, 40)]},
        ),
        'Ti O2',
    )
    rows = plan_for([bare, rich])['panels'][0]['phases']
    assert len(rows) == 2 and rows[0]['ref'] == []
    row = rows[1]
    assert row['ref'] == [[25.0, 100], [35.0, 40]]
    assert (row['name'], row['card']) == ('PolyB', '00-009')


def test_distinct_cards_remain_distinct_even_with_equal_element_sets():
    a1 = with_formula(s('s1', [A]), 'Fe O')
    b1 = with_formula(s('s2', [B]), 'Fe O')
    row = plan_for([a1, b1])['panels'][0]['phases'][0]
    assert row['alt'] == []
    assert len(plan_for([a1, b1])['panels'][0]['phases']) == 2
    same = with_formula(s('s3', [A]), 'Fe O')
    assert plan_for([a1, same])['panels'][0]['phases'][0]['alt'] == []

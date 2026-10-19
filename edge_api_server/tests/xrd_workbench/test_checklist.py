import copy

from .figure_fixtures import make_document, make_project, make_sample

from app.xrd_workbench.core.checklist import build_checklist

A = ('PhaseA', 'c1', 'DB')
WPPF = {'wppf_report': {'filename': 'w.pdf'}}
PHASE = {'phase_report': {'filename': 'p.pdf'}}


def q(phase, raw='1(1)'):
    return {'phase': phase, 'raw': raw, 'unit': 'wt%', 'origin': 'pdf', 'page': 1}


def sample(sid, *, reports=None, quantities=(), phases=(A,), unit='cps', warnings=()):
    s = make_sample(
        sid, f'시료 {sid}', list(phases), [(1, 21.0, [p[0] for p in phases])], unit=unit
    )
    s['reports'] = dict(reports if reports is not None else {**PHASE, **WPPF})
    s['quantities'] = list(quantities)
    s['warnings'] = list(warnings)
    return s


def check(samples, **figure):
    project = make_project(samples)
    document = make_document(project, **figure)
    for entry in document['samples']:
        entry['reviewed'] = True
        entry['manual_note'] = '직접 쓴 문장'
    return project, document, build_checklist(project, document)


def codes(result, sid):
    return [i['code'] for s in result['samples'] if s['sample_id'] == sid for i in s['items']]


def clean_sample(sid='a'):
    return sample(sid, quantities=[q('PhaseA')])


def test_a_complete_sample_has_nothing_to_check():
    _, _, result = check([clean_sample()], include=False)
    assert codes(result, 'a') == []
    assert result['summary'] == {
        'samples': 1,
        'reviewed': 1,
        'blockers': 0,
        'warnings': 0,
        'infos': 0,
    }


def test_not_reviewed_is_a_warning_until_the_author_selects_review_complete():
    project, document, _ = check([clean_sample()], include=False)
    document['samples'][0]['reviewed'] = False
    result = build_checklist(project, document)
    assert codes(result, 'a') == ['not_reviewed']
    assert result['samples'][0]['reviewed'] is False and result['summary']['reviewed'] == 0


def test_changed_analysis_is_a_blocker_and_replaces_the_not_reviewed_warning():
    project, document, _ = check([clean_sample()], include=False)
    entry = document['samples'][0]
    entry['reviewed'] = False
    entry['analysis_review_required'] = True
    result = build_checklist(project, document)
    assert codes(result, 'a') == ['analysis_review_required']
    assert result['summary']['blockers'] == 1
    assert result['samples'][0]['items'][0]['level'] == 'blocker'


def test_figure_exclusion_is_reported_with_the_reason_only_when_the_figure_is_on():
    bad = sample('b', quantities=[q('PhaseA')], unit=None)
    _, _, on = check([clean_sample(), bad], include=True)
    item = next(i for i in on['samples'][1]['items'] if i['code'] == 'figure_excluded')
    assert item['level'] == 'warning' and '축 또는 강도 단위 미확인' in item['text']
    assert 'figure_excluded' not in codes(on, 'a')
    _, _, off = check([clean_sample(), bad], include=False)
    assert 'figure_excluded' not in codes(off, 'b')


def test_missing_material_is_listed_with_the_right_level():
    s = sample('a', reports={}, quantities=[], phases=())
    _, _, result = check([s], include=False)
    assert codes(result, 'a') == ['no_phase_report', 'no_quantity_material']
    levels = {i['code']: i['level'] for i in result['samples'][0]['items']}
    assert levels == {'no_phase_report': 'warning', 'no_quantity_material': 'info'}


def test_a_quantity_report_without_values_is_a_warning():
    s = sample('a', reports=WPPF, quantities=[])
    _, _, result = check([s], include=False)
    assert 'quantity_unreadable' in codes(result, 'a')
    assert 'no_quantity_material' not in codes(result, 'a')


def test_project_warnings_are_repeated_word_for_word():
    s = sample('a', quantities=[q('PhaseA')], warnings=['Dat: 연결을 확인하세요.'])
    _, _, result = check([s], include=False)
    item = next(i for i in result['samples'][0]['items'] if i['code'] == 'source_warning')
    assert item['text'] == 'Dat: 연결을 확인하세요.' and item['level'] == 'warning'


def test_phases_the_trace_rule_cannot_decide_are_listed_once_a_rule_is_applied():
    # PhaseB has no reference peaks, so the rule can say nothing about it.
    two = sample('a', quantities=[q('PhaseA')], phases=(A, ('PhaseB', 'c2', 'DB')))
    project, document, plain = check([two], include=False)
    assert 'undecided_trace_phases' not in codes(plain, 'a')  # no rule applied
    document['phase_classes']['rule'] = {'trace_max_percent': 5}
    items = build_checklist(project, document)['samples'][0]['items']
    item = next((i for i in items if i['code'] == 'undecided_trace_phases'), None)
    assert item and item['level'] == 'info' and '미분류' in item['text'] and '개' in item['text']
    # Having classes or criteria alone no longer reports anything as unclassified.
    document['phase_classes']['rule'] = None
    document['phase_classes']['criteria_text'] = '기준'
    assert 'undecided_trace_phases' not in codes(build_checklist(project, document), 'a')
    assert not any(i['code'] == 'unclassified_phases' for i in items)


def test_a_sample_without_any_note_is_reported_but_a_manual_note_counts():
    project, document, _ = check([clean_sample()], include=False)
    entry = document['samples'][0]
    entry['manual_note'] = ''
    for note in entry['notes']:
        note['selected'] = False
    assert 'no_notes' in codes(build_checklist(project, document), 'a')
    entry['manual_note'] = '직접'
    assert 'no_notes' not in codes(build_checklist(project, document), 'a')


def test_items_are_ordered_blocker_then_warning_then_info():
    s = sample('a', reports={}, quantities=[], phases=())
    project, document, _ = check([s], include=False)
    entry = document['samples'][0]
    entry['analysis_review_required'] = True
    entry['reviewed'] = False
    entry['manual_note'] = ''
    for note in entry['notes']:
        note['selected'] = False
    levels = [i['level'] for i in build_checklist(project, document)['samples'][0]['items']]
    assert levels == sorted(levels, key=['blocker', 'warning', 'info'].index)
    assert levels[0] == 'blocker' and levels[-1] == 'info'


def test_report_level_items_cover_no_samples_too_many_summary_samples_and_an_empty_figure():
    samples = [clean_sample(str(i)) for i in range(6)]
    project, document, _ = check(samples, include=False)
    for entry in document['samples']:
        entry['summary_included'] = True
    report = build_checklist(project, document)['report']
    assert report == []  # summaries continue onto further pages
    for entry in document['samples']:
        entry['included'] = False
    assert [i['code'] for i in build_checklist(project, document)['report']] == [
        'no_included_samples'
    ]
    bad = [sample('x', unit=None, quantities=[q('PhaseA')])]
    project, document, result = check(bad, include=True)
    assert [i['code'] for i in result['report']] == ['figure_empty']
    assert result['report'][0]['level'] == 'warning'


def test_the_summary_counts_every_level_and_skips_samples_left_out():
    left_out = clean_sample('b')
    one = sample('a', reports={}, quantities=[], phases=())
    project, document, _ = check([one, left_out], include=False)
    document['samples'][1]['included'] = False
    document['samples'][0]['reviewed'] = False
    result = build_checklist(project, document)
    assert [s['sample_id'] for s in result['samples']] == ['a']
    counts = result['summary']
    assert counts['samples'] == 1 and counts['reviewed'] == 0
    assert counts['warnings'] == 2 and counts['infos'] == 1 and counts['blockers'] == 0


def test_a_document_without_the_newer_blocks_still_works_and_nothing_is_modified():
    project, document, _ = check([clean_sample()], include=False)
    for key in ('figure', 'quantity_table', 'phase_classes'):
        del document[key]
    before = copy.deepcopy((project, document))
    result = build_checklist(project, document)
    assert result['summary']['samples'] == 1
    assert (project, document) == before

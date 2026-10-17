import copy

from .figure_fixtures import make_document, make_project, make_sample

from app.xrd_workbench.core.quantity_table import build_quantity_table, quantity_material_registered


def q(phase, raw, unit='wt%', origin='pdf'):
    return {'phase': phase, 'raw': raw, 'unit': unit, 'origin': origin, 'page': 1}


def sample(sid, quantities=None, reports=None, sources=None, name=None):
    s = make_sample(sid, name or sid, [], [])
    s['quantities'] = quantities or []
    s['reports'] = reports or {}
    s['sources'] = sources or []
    return s


def table(samples, **figure):
    project = make_project(samples)
    return build_quantity_table(project, make_document(project, **figure))


def cells(t, sid):
    return next(r for r in t['rows'] if r['sample_id'] == sid)['cells']


def texts(t, sid):
    return [c['text'] for c in cells(t, sid)]


WPPF = {'wppf_report': {'filename': 'w.pdf'}}


def test_columns_are_the_quantity_phase_names_in_order_of_first_appearance():
    a = sample('a', [q('B', '1(1)'), q('A', '2(1)')], WPPF)
    b = sample('b', [q('C', '3(1)'), q('A', '4(1)')], WPPF)
    assert [c['name'] for c in table([a, b])['columns']] == ['B', 'A', 'C']


def test_a_value_is_the_original_token_and_unit_and_never_rounded_or_summed():
    t = table([sample('a', [q('A', '12.34(5)'), q('B', '0.10(2)')], WPPF)])
    assert texts(t, 'a') == ['12.34(5) wt%', '0.10(2) wt%']
    assert not any(c['text'] in ('0', '0%', '-', '') for c in cells(t, 'a'))


def test_a_phase_missing_from_a_registered_report_is_listed_as_not_stated():
    a = sample('a', [q('A', '1(1)'), q('B', '2(1)')], WPPF)
    b = sample('b', [q('A', '3(1)')], WPPF)
    t = table([a, b])
    assert texts(t, 'b') == ['3(1) wt%', '미기재']
    assert cells(t, 'b')[1]['kind'] == 'missing'


def test_registered_material_without_any_entry_needs_checking_in_every_cell():
    t = table([sample('a', [q('A', '1(1)')], WPPF), sample('b', [], WPPF)])
    assert texts(t, 'b') == ['확인 필요'] and cells(t, 'b')[0]['kind'] == 'check'
    image_only = sample('c', [], sources=[{'role': 'quantity_image', 'filename': 'q.png'}])
    assert quantity_material_registered(image_only)
    assert texts(table([sample('a', [q('A', '1(1)')], WPPF), image_only]), 'c') == ['확인 필요']


def test_a_sample_without_any_quantity_material_says_so_in_its_first_cell_only():
    a = sample('a', [q('A', '1(1)'), q('B', '2(1)')], WPPF)
    t = table([a, sample('b')])
    assert texts(t, 'b') == ['정량 자료 없음', '']
    assert cells(t, 'b')[0]['kind'] == 'none' and cells(t, 'b')[1]['kind'] == 'blank'


def test_an_unreadable_value_is_marked_for_checking_not_zero():
    t = table([sample('a', [q('A', None), q('B', '  ')], WPPF)])
    assert texts(t, 'a') == ['확인 필요', '확인 필요']


def test_two_entries_of_one_phase_are_listed_side_by_side_without_adding_them():
    t = table([sample('a', [q('A', '1(1)'), q('A', '2(1)')], WPPF)])
    assert texts(t, 'a') == ['1(1) wt%; 2(1) wt%']


def test_manual_values_carry_a_dagger_and_a_footnote_only_when_present():
    manual = table([sample('a', [q('A', '5(1)', origin='manual')], WPPF)])
    assert texts(manual, 'a') == ['5(1) wt% †']
    assert any('† 직접 입력' in f for f in manual['footnotes'])
    plain = table([sample('a', [q('A', '5(1)')], WPPF)])
    assert not any('†' in f for f in plain['footnotes'])
    assert any('원문 표기' in f for f in plain['footnotes'])


def test_rows_follow_the_report_order_and_skip_samples_left_out():
    a = sample('a', [q('A', '1(1)')], WPPF)
    b = sample('b', [q('A', '2(1)')], WPPF)
    c = sample('c', [q('A', '3(1)')], WPPF)
    project = make_project([a, b, c])
    document = make_document(project)
    document['samples'].reverse()
    document['samples'][0]['included'] = False  # c
    t = build_quantity_table(project, document)
    assert [r['sample_id'] for r in t['rows']] == ['b', 'a']


def test_the_display_name_is_used_for_the_row():
    project = make_project([sample('a', [q('A', '1(1)')], WPPF, name='등록명')])
    document = make_document(project)
    document['samples'][0]['display_name'] = '표시 이름'
    assert build_quantity_table(project, document)['rows'][0]['name'] == '표시 이름'


def labelled(cls_by_sample):
    samples = [sample(sid, [q('A', '1(1)')], WPPF) for sid in cls_by_sample]
    for s in samples:
        s['phases'] = [
            dict(name='A', card='c', db='d', id=s['id'] + 'p', page=1, name_raw='A', source='r.pdf')
        ]
    project = make_project(samples)
    document = make_document(project)
    document['phase_classes']['labels'] = [
        {
            'sample_id': sid,
            'phase': {'name': 'A', 'card': 'c', 'db': 'd'},
            'class': cls,
            'origin': 'manual',
        }
        for sid, cls in cls_by_sample.items()
        if cls
    ]
    return build_quantity_table(project, document)


def test_a_column_shows_the_class_only_when_every_sample_agrees_and_unlabelled_means_major():
    assert labelled({'a': 'major', 'b': 'major'})['columns'][0]['class_note'] == '(주요상)'
    assert labelled({'a': 'major', 'b': 'trace'})['columns'][0]['class_note'] == ''
    # No label is the default class (주요상): it agrees with an explicit major, not with a trace.
    assert labelled({'a': None, 'b': None})['columns'][0]['class_note'] == ''
    assert labelled({'a': None, 'b': 'major'})['columns'][0]['class_note'] == ''
    assert labelled({'a': None, 'b': 'trace'})['columns'][0]['class_note'] == ''
    assert labelled({'a': 'presumed', 'b': 'presumed'})['columns'][0]['class_note'] == '(추정상)'


def test_nothing_registered_and_nothing_readable_have_different_empty_messages():
    none = table([sample('a'), sample('b')])
    assert none['columns'] == [] and '등록된 시료가 없습니다' in none['empty_message']
    unreadable = table([sample('a', [], WPPF)])
    assert unreadable['columns'] == [] and '읽은 시료가 없습니다' in unreadable['empty_message']
    assert table([sample('a', [q('A', '1(1)')], WPPF)])['empty_message'] == ''


def test_the_inputs_are_not_modified():
    s = sample('a', [q('A', '1(1)', origin='manual')], WPPF)
    project = make_project([s])
    document = make_document(project)
    before = copy.deepcopy((project, document))
    build_quantity_table(project, document)
    assert (project, document) == before


def test_names_that_differ_only_by_surrounding_whitespace_are_one_column():
    pdf = sample('a', [q('P0', '1(1)')], WPPF)
    manual = sample('b', [q('P0 ', '2(1)', origin='manual')], WPPF)
    t = table([pdf, manual])
    assert [c['name'] for c in t['columns']] == ['P0']
    assert texts(t, 'a') == ['1(1) wt%'] and texts(t, 'b') == ['2(1) wt% †']


def test_the_class_note_still_applies_to_a_name_with_stray_whitespace():
    s = sample('a', [q('A ', '1(1)')], WPPF)
    s['phases'] = [dict(name='A', card='c', db='d', id='ap', page=1, name_raw='A', source='r.pdf')]
    project = make_project([s])
    document = make_document(project)
    document['phase_classes']['labels'] = [
        {
            'sample_id': 'a',
            'phase': {'name': 'A', 'card': 'c', 'db': 'd'},
            'class': 'major',
            'origin': 'manual',
        }
    ]
    assert build_quantity_table(project, document)['columns'][0]['class_note'] == '(주요상)'

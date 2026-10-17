import copy

import pytest
from .figure_fixtures import make_trace_sample

from app.xrd_workbench.core import phase_class as pc

KEY = ('PhaseA', '00-001', 'DB1')


def check(sample, percent=2):
    return pc.evaluate_trace(sample, percent)[KEY]


def test_both_representative_peaks_at_or_below_the_percent_is_trace_including_the_boundary():
    result = check(make_trace_sample(heights=(20.0, 10.0, 1000.0)))
    assert result['status'] == 'trace' and result['reason'] == ''
    assert [p['role'] for p in result['peaks']] == ['first', 'second']
    assert [p['ratio'] for p in result['peaks']] == [2.0, 1.0]


def test_just_above_the_percent_is_not_trace():
    assert check(make_trace_sample(heights=(20.0, 10.0, 1000.0)), 1.9)['status'] == 'not_trace'


def test_one_strong_representative_peak_is_enough_to_not_be_trace():
    assert check(make_trace_sample(heights=(20.0, 30.0, 1000.0)))['status'] == 'not_trace'


def test_a_decimal_boundary_is_not_lost_to_float_rounding():
    # 100 * 1.1 / 1000 is 0.11000000000000001 in binary floating point.
    sample = make_trace_sample(heights=(1.1, 1.1, 1000.0))
    assert check(sample, 0.11)['status'] == 'trace'


def test_a_phase_with_one_reference_peak_is_undecided():
    sample = make_trace_sample(heights=(5.0,), refs=[(25.0, 100.0, '1 0 0')])
    result = check(sample)
    assert result['status'] == 'undecided' and '대표 피크 부족' in result['reason']


def test_a_tie_for_second_place_is_undecided_and_says_why():
    refs = [(25.0, 100.0, '1 0 0'), (35.0, 40.0, '1 1 0'), (45.0, 40.0, '2 0 0')]
    result = check(make_trace_sample(heights=(1.0, 1.0, 1000.0), refs=refs))
    assert result['status'] == 'undecided' and '동점' in result['reason']


def test_a_tie_for_first_place_at_different_positions_is_undecided():
    refs = [(25.0, 100.0, '1 0 0'), (35.0, 100.0, '1 1 0'), (45.0, 10.0, '2 0 0')]
    result = check(make_trace_sample(heights=(1.0, 1.0, 1000.0), refs=refs))
    assert result['status'] == 'undecided' and '동점' in result['reason']


def test_two_hkl_rows_at_one_position_count_as_one_peak():
    refs = [(25.0, 100.0, '1 0 0'), (25.0, 100.0, '2 0 0'), (35.0, 40.0, '1 1 0')]
    result = check(make_trace_sample(heights=(1.0, 500.0, 1.0), refs=refs))
    assert result['status'] in ('trace', 'not_trace')
    assert [p['hkl'] for p in result['peaks']] == ['1 0 0', '1 1 0']


def test_a_representative_peak_without_an_assignment_is_undecided():
    sample = make_trace_sample()
    sample['assignments'] = [a for a in sample['assignments'] if a['no'] != 1]
    result = check(sample)
    assert result['status'] == 'undecided' and '배정 없음' in result['reason']


def test_the_same_hkl_assigned_twice_is_undecided():
    sample = make_trace_sample()
    sample['observed_peaks'].append(dict(no=9, x=99.0, x_raw='99.0', page=1, height_raw='5.0'))
    sample['assignments'].append(
        dict(no=9, x=99.0, x_raw='99.0', page=1, matches=[dict(phase='PhaseA', hkl='1 0 0')])
    )
    result = check(sample)
    assert result['status'] == 'undecided' and '연결이 여러 개' in result['reason']


def test_an_assignment_without_an_observed_row_is_undecided():
    sample = make_trace_sample()
    sample['observed_peaks'] = [p for p in sample['observed_peaks'] if p['no'] != 1]
    result = check(sample)
    assert result['status'] == 'undecided' and '관측 피크 연결 안 됨' in result['reason']


def test_an_unreadable_height_is_undecided():
    sample = make_trace_sample(heights=('n/a', 10.0, 1000.0))
    result = check(sample)
    assert result['status'] == 'undecided' and 'Height 읽지 못함' in result['reason']


def test_a_sample_whose_highest_peak_is_zero_is_undecided():
    result = check(make_trace_sample(heights=(0.0, 0.0, 0.0)))
    assert result['status'] == 'undecided'


def test_the_central_value_is_used_and_the_raw_token_is_kept():
    sample = make_trace_sample(heights=('150.5(3)', 10.0, 1000.0))
    first = check(sample, 100)['peaks'][0]
    assert first['height_raw'] == '150.5(3)'
    assert first['ratio'] == pytest.approx(15.05)


def test_evaluation_does_not_modify_the_sample():
    sample = make_trace_sample()
    before = copy.deepcopy(sample)
    check(sample)
    assert sample == before


@pytest.mark.parametrize('percent', [-1, 100.5, 'x', None, float('nan'), True])
def test_an_invalid_percent_is_rejected(percent):
    with pytest.raises(ValueError):
        pc.evaluate_trace(make_trace_sample(), percent)

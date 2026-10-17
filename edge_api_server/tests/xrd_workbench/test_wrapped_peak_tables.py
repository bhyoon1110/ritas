"""Instrument-style wrapped, combined peak tables; synthetic data only."""
from copy import deepcopy
from functools import lru_cache

import pytest
from reportlab.platypus import PageBreak

from app.xrd_workbench.core.parsing.pdf_report import get_cell, header_type, parse_report
from app.xrd_workbench.core.parsing.values import central, pdf_number
from app.xrd_workbench.core.project import build_project
from app.xrd_workbench.core.review import apply_review, new_review, rebuild, validate_review
from .synthetic import _document, _register_font, _table


@lru_cache(maxsize=1)
def combined_pdf():
    _register_font()
    phases = [['Phase name', 'Chemical formula', 'FOM'],
              ['Phase Alpha, syn', 'A O2', '0.12'], ['Phase B', 'B O', '0.23']]
    peaks = [['N\no\n.', '2θ, °', 'Heig\nht,\ncps', 'Phase\nName', 'Card\nNo'],
             ['1', '25.29\n0(3)', '165\n(4)', 'Phase Al\npha, syn: 1 0 1', '123'],
             ['2', '30.125\n(2)', '234(5)', 'Phase Alpha, syn: 1 1 0,Phase B: 2 0 0', '123,456'],
             ['3', '36.0', '12(1)', 'Unknown', '']]
    return _document([_table(phases), PageBreak(), _table(peaks)])


def project_fixture():
    return build_project([('A.txt', b'#Intensity_unit=cps\n20 1\n25 9\n30 3\n40 2\n'),
                          ('A_상매칭_Report.pdf', combined_pdf())])


@pytest.mark.parametrize('label', ['No.', 'N\no.', 'N\no\n.', 'No'])
def test_wrapped_peak_headers(label):
    headers = [label, '2θ, °', 'Heig\nht,\ncps', 'Phase\nName', 'Nor\nm. I.']
    assert header_type(headers) == 'observed'
    assert get_cell(['1', '23.05\n7(7)', '306\n(15)', 'Phase A: 0 1 2', '3.20'],
                    headers, 'Height, cps') == '306\n(15)'
    assert header_type([label, '2θ, °', 'Phase\nName']) == 'assignments'


@pytest.mark.parametrize('raw,value', [('23.05\n7(7)', 23.057), ('165\n(4)', 165),
                                     ('1.2e-\n3', .0012), ('12 34', None), ('nan', None),
                                     ('1\n2x', None), ('', None)])
def test_only_pdf_cells_accept_soft_wraps(raw, value):
    assert pdf_number(raw) == value
    if '\n' in raw:
        assert central(raw) is None  # TXT and manually entered values stay strict.


def test_combined_pdf_keeps_both_observations_and_assignments_and_original_tokens():
    result = parse_report(combined_pdf(), 'wrapped.pdf')
    assert len(result['observed_peaks']) == len(result['assignments']) == 3
    assert [r['x'] for r in result['observed_peaks']] == [25.29, 30.125, 36]
    assert result['observed_peaks'][0]['x_raw'] == '25.29\n0(3)'
    assert result['observed_peaks'][0]['height_raw'] == '165\n(4)'
    assert result['assignments'][0]['matches'] == [dict(phase='Phase Alpha, syn', hkl='1 0 1')]
    assert [m['phase'] for m in result['assignments'][1]['matches']] == ['Phase Alpha, syn', 'Phase B']
    assert result['assignments'][2]['matches'] == []
    assert not result['warnings']
    assert result['raw_tables'][1]['rows'][0][0] == 'N\no\n.'


def test_old_saved_project_recovers_peak_tables_without_mutation_or_losing_review_edits():
    project = project_fixture()
    s = project['samples'][0]
    report = s['reports']['phase_report']
    report['logical_tables'] = [t for t in report['logical_tables'] if t['kind'] != 'observed']
    report['observed_peaks'] = report['assignments'] = []
    s['observed_peaks'] = s['assignments'] = []
    before = deepcopy(project)
    review = new_review(project)
    source = next(r['id'] for r in s['sources'] if r['role'] == 'phase_report')
    review['tables'][source] = {'1': dict(status='reviewed', excluded_rows=[2], cells=[
        dict(row=1, col=1, value='25.5', reason='확인')])}
    result = apply_review(project, review)['samples'][0]
    assert [(r['no'], r['x']) for r in result['observed_peaks']] == [(1, 25.5), (3, 36)]
    assert [(r['no'], r['x']) for r in result['assignments']] == [(1, 25.5), (3, 36)]
    assert project == before
    review['tables'][source]['1']['status'] = 'excluded'
    result = apply_review(project, review)['samples'][0]
    assert result['observed_peaks'] == result['assignments'] == []
    assert any('관측 피크표' in w for w in result['warnings'])


def test_combined_review_renames_wrapped_assignments_and_rejects_invalid_numeric_edits():
    project = project_fixture()
    source = next(r['id'] for r in project['samples'][0]['sources'] if r['role'] == 'phase_report')
    review = new_review(project)
    review['tables'][source] = {'0': dict(status='reviewed', excluded_rows=[], cells=[
        dict(row=1, col=0, value='Renamed phase', reason='확인')])}
    result = apply_review(project, review)['samples'][0]
    assert result['assignments'][0]['matches'][0]['phase'] == 'Renamed phase'
    review['tables'][source]['1'] = dict(status='reviewed', excluded_rows=[], cells=[
        dict(row=1, col=2, value='not a number', reason='확인')])
    with pytest.raises(ValueError, match='유한한 숫자'):
        validate_review(project, review)


def test_recovery_is_idempotent_and_does_not_double_read_existing_sections():
    report = parse_report(combined_pdf(), 'wrapped.pdf')
    first = rebuild(report, {})
    second = rebuild(first, {})
    assert len(first['observed_peaks']) == len(second['observed_peaks']) == 3
    assert first['assignments'] == second['assignments']


def test_ambiguous_wrapped_phase_name_is_not_guessed_and_is_reported():
    report = parse_report(combined_pdf(), 'wrapped.pdf')
    phases, peaks = report['logical_tables']
    phases['rows'].append(['Phase Al pha, syn', 'A O2', '0.2'])
    phases['row_indices'].append(3)
    peaks['rows'][0][3] = 'Phase A\nlpha, syn: 1 0 1'
    from app.xrd_workbench.core.parsing.pdf_report import derive_report
    result = derive_report(report)
    assert result['assignments'][0]['matches'][0]['phase'] == 'Phase A lpha, syn'
    assert any('연결하지 못했습니다' in w for w in result['warnings'])

import re

import pytest
from .fixture_data import fixtures_dir

from app.xrd_workbench.core.parsing.pdf_report import header_type, parse_report
from app.xrd_workbench.core.parsing.txt import parse_txt
from app.xrd_workbench.core.parsing.values import central


def test_txt_preserves_values_headers_and_line_numbers():
    result = parse_txt(b'\xef\xbb\xbf#Intensity_unit=cps\n10 2\n10.02 4\n', 'A.txt')
    assert result['x'] == [10, 10.02]
    assert result['y'] == [2, 4]
    assert result['line_numbers'] == [2, 3]
    assert result['intensity_unit'] == 'cps'
    assert result['axis_kind'] is None


@pytest.mark.parametrize('content', [b'10 nan\n', b'10 2\n9 3\n', b'10 2 3\n', b'10 2\n10 3\n'])
def test_invalid_txt_is_rejected_without_repair(content):
    with pytest.raises(ValueError):
        parse_txt(content, 'bad.txt')


def phase_reports():
    return sorted(fixtures_dir().glob('*_상매칭_Report.pdf'))


def wppf_reports():
    return sorted(fixtures_dir().glob('*_정량(WPPF)_Report.pdf'))


@pytest.mark.real_data
def test_phase_report_keeps_reference_observed_and_assignment_rows_distinct():
    reports = phase_reports()
    assert reports, 'XRD_DATA_FIXTURES has no phase report'
    for path in reports:
        result = parse_report(path.read_bytes(), path.name)
        assert result['raw_tables'] and result['page_texts']
        assert result['axis_kind'] == 'two_theta'
        # Every observed peak has exactly one assignment row with the same number and position.
        observed = {r['no']: r for r in result['observed_peaks']}
        assigned = {r['no']: r for r in result['assignments']}
        assert observed and set(observed) == set(assigned)
        assert all(observed[n]['x'] == assigned[n]['x'] for n in observed)
        # Reference peaks are tied to a phase and never mixed with observed rows.
        names = {p['name'] for p in result['phases']}
        assert result['reference_peaks']
        assert all(r['phase'] in names for r in result['reference_peaks'])
        assert all('hkl_raw' in r and 'intensity_raw' in r for r in result['reference_peaks'])
        # Every printed d-I row becomes a reference peak: nothing is merged or dropped, even
        # when several rows (different hkl) share one position. Counted from the raw tables.
        printed = 0
        kind = None
        for table in result['raw_tables']:
            for row in table['rows']:
                if header_type(row):
                    kind = header_type(row)
                elif kind == 'reference' and central(row[0]) is not None:
                    printed += 1
        assert printed == len(result['reference_peaks'])
        # Every phase written on an assignment row survives (one 'Phase: hkl' entry per colon).
        for row in result['assignments']:
            assert row['phase_raw'].count(':') == len(row['matches'])
        # An assignment lists only phases that the report itself names.
        for row in result['assignments']:
            assert all(m['phase'] in names for m in row['matches'])


@pytest.mark.real_data
def test_wppf_report_keeps_original_tokens_units_and_pages():
    reports = wppf_reports()
    assert reports, 'XRD_DATA_FIXTURES has no WPPF report'
    token = re.compile(r'[+-]?\d+(?:\.\d+)?(?:\(\d+\))?')
    for path in reports:
        result = parse_report(path.read_bytes(), path.name)
        assert result['quantities']
        for row in result['quantities']:
            assert token.fullmatch(row['raw']), row['raw']
            assert row['unit'] == 'wt%' and row['page']
            assert row['phase'].strip()
            # The numeric value is the central value of the untouched token.
            assert row['value'] == float(row['raw'].split('(')[0])

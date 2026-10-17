import pytest

from app.xrd_workbench.core.project import build_project, classify_filename
from .synthetic import sample_files


@pytest.mark.parametrize('suffix', ['_정량_Report.pdf', '_정량(WPPF)_Report.pdf', '_정량_report.PDF'])
def test_quantity_pdf_filename_aliases(suffix):
    assert classify_filename('Sample' + suffix) == {'sample': 'Sample', 'role': 'wppf_report'}


def test_short_quantity_filename_is_parsed_without_manual_mapping():
    original = sample_files('SampleA')
    renamed = {name.replace('_정량(WPPF)_Report', '_정량_Report'): data for name, data in original.items()}
    before = build_project(list(original.items()))['samples'][0]
    after = build_project(list(renamed.items()))['samples'][0]
    assert after['quantities']
    assert [(q['phase'], q['raw'], q['unit']) for q in after['quantities']] == [
        (q['phase'], q['raw'], q['unit']) for q in before['quantities']]
    assert 'wppf_report' in after['reports']
    assert all(r['role'] != 'ignore' for r in after['sources'])


def test_explicit_exclusion_still_wins_and_duplicate_aliases_are_not_overwritten():
    files = sample_files('SampleA')
    files['SampleA_정량_Report.pdf'] = files['SampleA_정량(WPPF)_Report.pdf']
    with pytest.raises(ValueError, match='중복'):
        build_project(list(files.items()))
    project = build_project(list(files.items()), {'SampleA_정량_Report.pdf': {'role': 'ignore'}})
    assert len(project['samples']) == 1
    assert project['samples'][0]['quantities']

"""Review overlay contracts against the supplied source reports."""

from copy import deepcopy

import pytest
from .fixture_data import (
    example_project,
    first_phase_name,
    first_quantity_token,
    other_token,
    table_cell,
)

pytestmark = pytest.mark.real_data


def change(review, location, value, status='reviewed'):
    source, ti, row, col = location
    review['tables'].setdefault(source, {})[str(ti)] = {
        'status': status,
        'cells': [
            {'row': row, 'col': col, 'value': value, 'reason': '원문을 확인하여 추출값 수정'}
        ],
        'excluded_rows': [],
    }


def test_reviewed_quantity_changes_effective_value_without_mutating_source():
    from app.xrd_workbench.core.review import apply_review, new_review, validate_review

    project = example_project()
    before = deepcopy(project)
    review = new_review(project)
    token = first_quantity_token(project)
    changed = other_token(token)
    location = table_cell(project, 'wppf_report', token)
    change(review, location, changed, status='unreviewed')
    draft = apply_review(project, validate_review(project, review))
    assert draft['samples'][0]['quantities'][0]['raw'] == token
    review['tables'][location[0]][str(location[1])]['status'] = 'reviewed'
    effective = apply_review(project, validate_review(project, review))
    assert effective['samples'][0]['quantities'][0]['raw'] == changed
    assert effective['samples'][0]['quantities'][0]['value'] == float(changed.split('(')[0])
    for index in range(1, len(project['samples'])):
        expected = [q['raw'] for q in before['samples'][index]['quantities']]
        assert [q['raw'] for q in effective['samples'][index]['quantities']] == expected
    assert project == before
    assert (
        effective['samples'][0]['reports']['wppf_report']['raw_tables']
        == before['samples'][0]['reports']['wppf_report']['raw_tables']
    )


def test_reviewed_phase_name_keeps_only_existing_reference_and_assignment_links():
    from app.xrd_workbench.core.review import apply_review, new_review, validate_review

    project = example_project()
    original = project['samples'][0]
    name = first_phase_name(project)
    renamed = name + ' (reviewed)'
    review = new_review(project)
    change(review, table_cell(project, 'phase_report', name), renamed)
    effective = apply_review(project, validate_review(project, review))
    sample = effective['samples'][0]
    assert any(p['name'] == renamed for p in sample['phases'])
    expected_refs = len([p for p in original['reference_peaks'] if p['phase'] == name])
    assert expected_refs > 0
    assert len([p for p in sample['reference_peaks'] if p['phase'] == renamed]) == expected_refs
    had = any(m['phase'] == name for p in original['assignments'] for m in p['matches'])
    has = any(m['phase'] == renamed for p in sample['assignments'] for m in p['matches'])
    assert has == had
    for index in range(1, len(project['samples'])):
        names = [p['name'] for p in project['samples'][index]['phases']]
        assert [p['name'] for p in effective['samples'][index]['phases']] == names
    assert len(sample['assignments']) == len(original['assignments'])


def test_manual_quantity_requires_sample_owned_source_and_explicit_selection():
    from app.xrd_workbench.core.review import apply_review, new_review, validate_review

    project = example_project()
    review = new_review(project)
    sample = project['samples'][0]
    source = next(r for r in sample['sources'] if r['role'] == 'quantity_image')
    manual_raw = other_token(first_quantity_token(project))
    review['manual_quantities'][sample['id']] = [
        {
            'phase': first_phase_name(project),
            'raw': manual_raw,
            'unit': 'wt%',
            'source_id': source['id'],
            'page': None,
            'reason': '정량 이미지 확인 입력',
            'reviewed': True,
        }
    ]
    pdf_raws = [q['raw'] for q in sample['quantities']]
    unselected = apply_review(project, validate_review(project, review))['samples'][0]
    assert [q['raw'] for q in unselected['quantities']] == pdf_raws
    review['quantity_source'][sample['id']] = 'manual'
    chosen = apply_review(project, validate_review(project, review))['samples'][0]
    assert len(chosen['quantities']) == 1 and chosen['quantities'][0]['raw'] == manual_raw
    assert chosen['quantities'][0]['source_filename'] == source['filename']
    review['manual_quantities'][sample['id']][0]['source_id'] = next(
        r for r in project['samples'][1]['sources'] if r['role'] == 'quantity_image'
    )['id']
    with pytest.raises(ValueError, match='출처'):
        validate_review(project, review)


def test_invalid_numeric_header_and_outside_cell_edits_are_rejected():
    from app.xrd_workbench.core.review import new_review, validate_review

    project = example_project()
    review = new_review(project)
    token = first_quantity_token(project)
    location = table_cell(project, 'wppf_report', token)
    change(review, location, 'NaN')
    with pytest.raises(ValueError, match='숫자'):
        validate_review(project, review)
    change(review, location, other_token(token))
    review['tables'][location[0]][str(location[1])]['cells'][0]['col'] = 999
    with pytest.raises(ValueError, match='셀'):
        validate_review(project, review)
    header = table_cell(project, 'phase_report', 'Phase name')
    change(review, header, 'Something else')
    # Remove the previous intentionally invalid cell before checking headers.
    review['tables'].pop(location[0], None)
    with pytest.raises(ValueError, match='헤더'):
        validate_review(project, review)


def test_review_store_preserves_revision_and_does_not_overwrite_conflicts(tmp_path):
    from app.xrd_workbench.core.errors import RevisionConflict
    from app.xrd_workbench.core.review import new_review
    from app.xrd_workbench.storage import AnalysisStore

    project = example_project()
    store = AnalysisStore(tmp_path)
    review = new_review(project)
    saved = store.save('example', project, review, 0)
    assert saved['revision'] == 1 and store.load('example') == saved
    with pytest.raises(RevisionConflict):
        store.save('example', project, review, 0)


def test_excluded_reference_row_only_changes_its_sample_after_review():
    from app.xrd_workbench.core.parsing.pdf_report import header_type
    from app.xrd_workbench.core.review import apply_review, new_review

    project = example_project()
    review = new_review(project)
    s = project['samples'][0]
    source = next(r for r in s['sources'] if r['role'] == 'phase_report')
    ti, t = next(
        (i, t)
        for i, t in enumerate(s['reports']['phase_report']['raw_tables'])
        if any(header_type(row) == 'reference' for row in t['rows'])
    )
    row = next(i + 1 for i, r in enumerate(t['rows']) if header_type(r) == 'reference')
    review['tables'][source['id']] = {
        str(ti): {'status': 'unreviewed', 'cells': [], 'excluded_rows': [row]}
    }
    count = len(s['reference_peaks'])
    assert len(apply_review(project, review)['samples'][0]['reference_peaks']) == count
    review['tables'][source['id']][str(ti)]['status'] = 'reviewed'
    effective = apply_review(project, review)
    assert len(effective['samples'][0]['reference_peaks']) == count - 1
    for index in range(1, len(project['samples'])):
        other = len(project['samples'][index]['reference_peaks'])
        assert len(effective['samples'][index]['reference_peaks']) == other
    review['tables'][source['id']][str(ti)]['status'] = 'excluded'
    assert len(apply_review(project, review)['samples'][0]['reference_peaks']) < count - 1


@pytest.mark.parametrize('row', [1, 2, 3])
def test_excluded_phase_never_reassigns_its_reference_peaks(row):
    from app.xrd_workbench.core.parsing.pdf_report import header_type
    from app.xrd_workbench.core.review import apply_review, new_review

    project = example_project()
    review = new_review(project)
    s = project['samples'][0]
    if row > len(s['phases']):
        pytest.skip('the fixture sample has fewer phases than this row index')
    source = next(r for r in s['sources'] if r['role'] == 'phase_report')
    ti = next(
        i
        for i, t in enumerate(s['reports']['phase_report']['raw_tables'])
        if header_type(t['rows'][0]) == 'phases'
    )
    review['tables'][source['id']] = {
        str(ti): {'status': 'reviewed', 'cells': [], 'excluded_rows': [row]}
    }
    value = apply_review(project, review)['samples'][0]
    assert len(value['phases']) == len(s['phases']) - 1
    from collections import Counter

    assert Counter(r['phase'] for r in value['reference_peaks']) == Counter(
        r['phase'] for r in s['reference_peaks']
    )
    assert len(value['assignments']) == len(s['assignments'])


def test_excluded_wppf_group_does_not_move_weight_to_previous_phase():
    from app.xrd_workbench.core.review import apply_review, new_review

    project = example_project()
    review = new_review(project)
    quantities = project['samples'][0]['quantities']
    if len(quantities) < 2:
        pytest.skip('needs a sample with at least two reported phases')
    dropped = quantities[1]['phase']
    source, ti, row, col = table_cell(project, 'wppf_report', dropped)
    review['tables'][source] = {
        str(ti): {'status': 'reviewed', 'cells': [], 'excluded_rows': [row]}
    }
    value = apply_review(project, review)['samples'][0]
    expected = [(q['phase'], q['raw']) for q in quantities if q['phase'] != dropped]
    assert [(q['phase'], q['raw']) for q in value['quantities']] == expected
    assert any('그룹' in w for w in value['warnings'])


def test_editing_unrecognized_table_never_creates_a_new_phase():
    from app.xrd_workbench.core.review import apply_review, new_review

    project = deepcopy(example_project())
    s = project['samples'][0]
    r = s['reports']['phase_report']
    r['raw_tables'] = [
        {
            'page': 1,
            'bbox': [0, 0, 100, 100],
            'rows': [['Phase names', 'FOM'], ['Unexpected phase', '0.1']],
        }
    ]
    r['logical_tables'] = []
    r['phases'] = []
    source = next(v for v in s['sources'] if v['role'] == 'phase_report')
    review = new_review(project)
    change(review, (source['id'], 0, 0, 0), 'Phase name')
    effective = apply_review(project, review)['samples'][0]
    assert (
        effective['phases'] == [] and effective['reports']['phase_report']['logical_tables'] == []
    )
    assert effective['reports']['phase_report']['effective_tables'][0]['rows'][0][0] == 'Phase name'

from copy import deepcopy

import pytest
from .fixture_data import example_project, first_phase_name, weight_sum_sample_index

from app.xrd_workbench.core.errors import RevisionConflict
from app.xrd_workbench.core.report import merge_notes, new_document, validate_document
from app.xrd_workbench.storage import ReportStore

pytestmark = pytest.mark.real_data


def test_document_edit_changes_display_only_and_reorders_by_stable_id():
    project = deepcopy(example_project())
    original = deepcopy(project)
    doc = new_document(project)
    doc['samples'].reverse()
    doc['samples'][0]['display_name'] = '의뢰 시료 B'
    doc['metadata']['title'] = '수정된 보고서'
    result = validate_document(project, doc)
    assert result['samples'][0]['sample_id'] == project['samples'][1]['id']
    assert result['samples'][0]['display_name'] == '의뢰 시료 B'
    assert project == original


def test_regeneration_preserves_user_edited_text_and_user_selection():
    entry = new_document(example_project())['samples'][0]
    entry['notes'][0].update(text='담당자가 수정한 문장', edited=True, selected=False)
    result = {
        'candidates': [{'id': entry['notes'][0]['id'], 'text': '자동 후보', 'evidence': []}],
        'selected_ids': [entry['notes'][0]['id']],
        'status': 'llm',
        'model_used': 'm',
        'message': 'ok',
    }
    merge_notes(entry, result)
    assert entry['notes'][0]['text'] == '담당자가 수정한 문장'
    assert entry['notes'][0]['selected'] is False


def test_atomic_save_conflict_and_reopen(tmp_path):
    project = example_project()
    store = ReportStore(tmp_path)
    doc = new_document(project)
    first = store.save('example', project, doc, 0)
    assert first['revision'] == 1
    first['metadata']['title'] = '새 제목'
    second = store.save('example', project, first, 1)
    assert second['revision'] == 2
    with pytest.raises(RevisionConflict):
        store.save('example', project, doc, 0)
    assert ReportStore(tmp_path).load('example')['metadata']['title'] == '새 제목'


def test_foreign_sample_and_empty_document_are_rejected():
    project = example_project()
    doc = new_document(project)
    doc['samples'][0]['sample_id'] = 'wrong'
    with pytest.raises(ValueError):
        validate_document(project, doc)
    doc = new_document(project)
    for sample in doc['samples']:
        sample['included'] = False
    with pytest.raises(ValueError):
        validate_document(project, doc)


def test_model_order_is_used_and_explicit_user_order_and_selection_are_preserved():
    entry = new_document(example_project())['samples'][0]
    a, b = entry['notes'][:2]
    candidates = [
        {
            k: v
            for k, v in n.items()
            if k not in ('selected', 'edited', 'origin', 'selection_edited')
        }
        for n in [a, b]
    ]
    result = {
        'candidates': candidates,
        'selected_ids': [b['id'], a['id']],
        'status': 'llm',
        'message': 'ok',
        'model_used': 'm',
    }
    merge_notes(entry, result)
    assert [n['id'] for n in entry['notes']] == [b['id'], a['id']]
    entry['note_order_edited'] = True
    entry['notes'].reverse()
    entry['notes'][0].update(selected=False, selection_edited=True)
    merge_notes(entry, result)
    assert [n['id'] for n in entry['notes']] == [a['id'], b['id']]
    assert entry['notes'][0]['selected'] is False


def test_reappearing_candidate_preserves_distinct_author_versions_when_retained_twice():
    from app.xrd_workbench.core.report import sync_analysis
    from app.xrd_workbench.core.review import apply_review, new_review

    project = example_project()
    doc = new_document(project)
    index = weight_sum_sample_index(project)
    s = project['samples'][index]
    source = next(r for r in s['sources'] if r['role'] == 'quantity_image')
    review = new_review(project)
    review['manual_quantities'][s['id']] = [
        {
            'phase': first_phase_name(project, index),
            'raw': '100',
            'unit': 'wt%',
            'source_id': source['id'],
            'page': None,
            'reason': '확인 입력',
            'reviewed': True,
        }
    ]
    for i, text in enumerate(['첫 번째 사용자 해석', '두 번째 사용자 해석']):
        note = next(n for n in doc['samples'][index]['notes'] if n['kind'] == 'weight_sum')
        note.update(text=text, edited=True)
        review.update(revision=i * 2 + 1)
        review['quantity_source'][s['id']] = 'manual'
        effective = apply_review(project, review)
        doc, _ = sync_analysis(effective, doc)
        doc = validate_document(effective, doc)
        if i == 0:
            review.update(revision=2)
            review['quantity_source'][s['id']] = 'pdf'
            effective = apply_review(project, review)
            doc, _ = sync_analysis(effective, doc)
            doc = validate_document(effective, doc)
    retained = doc['samples'][1]['retained_notes']
    assert [n['text'] for n in retained] == ['첫 번째 사용자 해석', '두 번째 사용자 해석']
    assert len({n['id'] for n in retained}) == 2

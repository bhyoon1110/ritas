import io
from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from .fixture_data import (
    example_project,
    first_phase_name,
    first_quantity_token,
    other_token,
    resolve_source,
    weight_sum_sample_index,
)
from PIL import Image

from app.xrd_workbench.routes.reports import build_report_router
from app.xrd_workbench.storage import ReportStore

pytestmark = pytest.mark.real_data


@pytest.fixture
def context(tmp_path):
    from app.xrd_workbench.core.review import apply_review
    from app.xrd_workbench.routes.analysis import build_analysis_router
    from app.xrd_workbench.storage import AnalysisStore

    project = deepcopy(example_project())
    reviews = AnalysisStore(tmp_path / 'analysis')
    reports = ReportStore(tmp_path / 'reports')
    reports.lock = reviews.lock
    app = FastAPI()

    def source(sid):
        if sid != 'example':
            raise ValueError('missing')
        return project

    def effective(sid):
        return apply_review(source(sid), reviews.load(sid))

    app.include_router(build_analysis_router(source, resolve_source, reviews))
    app.include_router(build_report_router(effective, resolve_source, reports))
    return TestClient(app), project, reviews, reports


def quantity_change(client, project):
    review = client.get('/api/analysis/example').json()['review']
    sample = project['samples'][0]
    source = next(r for r in sample['sources'] if r['role'] == 'wppf_report')
    token = first_quantity_token(project)
    changed = other_token(token)
    quantity_change.changed = changed
    for ti, t in enumerate(sample['reports']['wppf_report']['raw_tables']):
        for ri, row in enumerate(t['rows']):
            if token in row:
                review['tables'][source['id']] = {
                    str(ti): {
                        'status': 'reviewed',
                        'cells': [
                            {
                                'row': ri,
                                'col': row.index(token),
                                'value': changed,
                                'reason': '원문 확인 수정',
                            }
                        ],
                        'excluded_rows': [],
                    }
                }
                response = client.put(
                    '/api/analysis/example',
                    json={'review': review, 'expected_revision': review['revision']},
                )
                assert response.status_code == 200, response.text
                return response.json()['review']
    raise AssertionError('quantity fixture missing')


def test_original_page_render_and_invalid_sources_are_rejected(context):
    client, project, _, _ = context
    source = next(r for r in project['samples'][0]['sources'] if r['role'] == 'phase_report')
    response = client.get(f"/api/analysis/example/pages/{source['id']}/3")
    assert response.status_code == 200 and response.headers['content-type'] == 'image/png'
    image = Image.open(io.BytesIO(response.content))
    assert image.width > 500 and image.height > 500
    assert float(response.headers['x-pdf-width']) > 0
    assert client.get('/api/analysis/example/pages/not-registered/1').status_code == 404
    assert client.get(f"/api/analysis/example/pages/{source['id']}/999").status_code == 422
    assert client.get(f"/api/analysis/example/pages/{source['id']}/0").status_code == 422


def test_analysis_change_blocks_old_report_and_preserves_author_edits(context):
    client, project, _, _ = context
    doc = client.get('/api/reports/example').json()['document']
    doc['samples'][0]['notes'][1].update(text='사용자가 수정한 이전 정량 문장', edited=True)
    doc['samples'][0]['manual_note'] = '직접 작성 내용'
    doc['metadata']['title'] = '보존할 제목'
    old = client.put('/api/reports/example', json={'document': doc, 'expected_revision': 0}).json()
    quantity_change(client, project)
    assert (
        client.get('/api/reports/example/pdf?revision=' + str(old['revision'])).status_code == 409
    )
    assert (
        client.put(
            '/api/reports/example', json={'document': old, 'expected_revision': old['revision']}
        ).status_code
        == 409
    )
    value = client.get('/api/reports/example').json()
    assert value['analysis_update_required']
    updated = value['document']
    assert updated['analysis_revision'] == 1
    assert (
        updated['metadata']['title'] == '보존할 제목'
        and updated['samples'][0]['manual_note'] == '직접 작성 내용'
    )
    assert any(
        n['text'] == '사용자가 수정한 이전 정량 문장' for n in updated['samples'][0]['notes']
    )
    assert (
        updated['samples'][0]['analysis_review_required'] and not updated['samples'][0]['reviewed']
    )
    saved = client.put(
        '/api/reports/example', json={'document': updated, 'expected_revision': old['revision']}
    )
    assert saved.status_code == 200
    assert (
        client.get(
            '/api/reports/example/preview?revision=' + str(saved.json()['revision'])
        ).status_code
        == 409
    )
    updated = saved.json()
    updated['samples'][0]['reviewed'] = True
    saved = client.put(
        '/api/reports/example', json={'document': updated, 'expected_revision': updated['revision']}
    ).json()
    response = client.get('/api/reports/example/preview?revision=' + str(saved['revision']))
    assert response.status_code == 200 and quantity_change.changed in response.text


def test_disappeared_candidate_keeps_its_user_text_for_review(context):
    client, project, _, _ = context
    doc = client.get('/api/reports/example').json()['document']
    index = weight_sum_sample_index(project)
    sample = doc['samples'][index]
    note = next(n for n in sample['notes'] if n['kind'] == 'weight_sum')
    note.update(text='기존 합계에 대해 직접 쓴 문장', edited=True)
    client.put('/api/reports/example', json={'document': doc, 'expected_revision': 0}).json()
    review = client.get('/api/analysis/example').json()['review']
    canonical = project['samples'][index]
    source = next(r for r in canonical['sources'] if r['role'] == 'quantity_image')
    review['manual_quantities'][canonical['id']] = [
        {
            'phase': first_phase_name(project, index),
            'raw': '100',
            'unit': 'wt%',
            'source_id': source['id'],
            'page': None,
            'reason': '수동 입력 확인',
            'reviewed': True,
        }
    ]
    review['quantity_source'][canonical['id']] = 'manual'
    assert (
        client.put(
            '/api/analysis/example', json={'review': review, 'expected_revision': 0}
        ).status_code
        == 200
    )
    updated = client.get('/api/reports/example').json()['document']
    assert updated['samples'][index]['retained_notes'][0]['text'] == '기존 합계에 대해 직접 쓴 문장'


def test_generation_does_not_save_when_analysis_changes_during_model_call(context, monkeypatch):
    client, project, reviews, reports = context
    doc = client.get('/api/reports/example').json()['document']
    doc = client.put('/api/reports/example', json={'document': doc, 'expected_revision': 0}).json()
    from app.xrd_workbench.core.notes import build_candidates
    from app.xrd_workbench.core.review import new_review

    def race(sample, settings, *rest):
        review = new_review(project)
        reviews.save('example', project, review, 0)
        candidates = build_candidates(sample)
        return {
            'candidates': candidates,
            'selected_ids': [c['id'] for c in candidates],
            'status': 'rules',
            'message': 'rules',
            'model_used': None,
        }

    monkeypatch.setattr('app.xrd_workbench.routes.reports.select_notes', race)
    response = client.post(
        '/api/reports/example/notes/' + project['samples'][0]['id'],
        json={'expected_revision': doc['revision']},
    )
    assert response.status_code == 409
    assert reports.load('example')['revision'] == doc['revision']

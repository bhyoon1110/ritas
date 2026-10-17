"""Slide 1 is a cover (title, 접수일, 분석 기간, 의뢰번호, (소속) 의뢰자 님); slide 2 is the Multi
Profile figure of the samples; the summary page, remarks, tables and the sample slides follow."""

import pytest
from .figure_fixtures import make_project, make_sample

from app.xrd_workbench.core.report import new_document, validate_document
from app.xrd_workbench.render.layout import BOTTOM, build_pages


def project():
    samples = [
        make_sample('a', '시료 A', [('PhaseA', 'c1', 'DB')], [(1, 20, ['PhaseA'])]),
        make_sample('b', '시료 B', [('PhaseB', 'c2', 'DB')], [(1, 30, ['PhaseB'])]),
    ]
    return make_project(samples)


def doc(**meta):
    p = project()
    d = new_document(p)
    d['quantity_table']['include'] = False
    d['metadata'].update(meta)
    return p, validate_document(p, d)


def texts(page):
    return [i['text'] for i in page['items'] if i['type'] == 'text' and not i.get('role')]


FULL = dict(
    title='XRD 분석 결과',
    received_date='2026-10-01',
    analysis_period='2026-10-02 ~ 2026-10-09',
    report_number='R-0042',
    client='가상연구소',
    requester='김가상',
)


def test_the_first_slide_is_a_cover_with_the_requested_lines():
    p, d = doc(**FULL)
    pages = build_pages(p, d, lambda fid: b'')
    cover = pages[0]
    assert cover['kind'] == 'cover'
    shown = texts(cover)
    assert shown[0] == 'XRD 분석 결과'
    assert '접수일: 2026-10-01' in shown
    assert '분석 기간: 2026-10-02 ~ 2026-10-09' in shown
    assert '의뢰번호: R-0042' in shown
    assert '(가상연구소) 김가상 님' in shown
    assert not any(i.get('role') == 'header' for i in cover['items'])
    assert all(i['y'] + i['h'] <= BOTTOM for i in cover['items'] if not i.get('role'))


def test_lines_without_a_value_are_left_out_not_filled():
    p, d = doc(title='XRD 분석 결과')
    shown = texts(build_pages(p, d, lambda fid: b'')[0])
    assert shown == ['XRD 분석 결과', '작성자: ', '완료일: ' + d['metadata']['date'],
                     '실험코드: XRD  |  장비코드: ', '실험자 ID: ']
    p, d = doc(title='T', requester='김가상')
    assert '김가상 님' in texts(build_pages(p, d, lambda fid: b'')[0])
    p, d = doc(title='T', client='가상연구소')
    assert '(가상연구소)' in ' '.join(texts(build_pages(p, d, lambda fid: b'')[0]))


def test_the_viewer_figure_is_the_second_slide_and_the_summary_comes_after_it():
    p, d = doc(**FULL)
    pages = build_pages(p, d, lambda fid: b'')
    kinds = [x['kind'] for x in pages]
    assert kinds[:3] == ['cover', 'figure', 'summary']
    assert kinds[-2:] == ['sample', 'sample']
    assert [x['number'] for x in pages] == list(range(1, len(pages) + 1))
    assert all(x['total'] == len(pages) for x in pages)
    d['figure']['include'] = False
    kinds = [x['kind'] for x in build_pages(p, d, lambda fid: b'')]
    assert kinds[:2] == ['cover', 'summary']


def test_the_summary_page_no_longer_repeats_the_cover_information():
    p, d = doc(**FULL)
    summary = next(x for x in build_pages(p, d, lambda fid: b'') if x['kind'] == 'summary')
    joined = ' '.join(texts(summary))
    assert 'R-0042' not in joined and '접수일' not in joined
    assert '시료 A' in joined and '시료 B' in joined


def test_documents_saved_before_the_new_fields_open_with_empty_values():
    p = project()
    d = new_document(p)
    for key in ('requester', 'received_date', 'analysis_period'):
        del d['metadata'][key]
    clean = validate_document(p, d)
    assert [clean['metadata'][k] for k in ('requester', 'received_date', 'analysis_period')] == [
        '',
        '',
        '',
    ]


@pytest.mark.parametrize(
    'change',
    [{'received_date': '10월 1일'}, {'analysis_period': 'x' * 61}, {'requester': 'x' * 81}],
)
def test_invalid_cover_fields_are_rejected(change):
    p = project()
    d = new_document(p)
    d['metadata'].update(change)
    with pytest.raises(ValueError):
        validate_document(p, d)


def test_a_full_summary_page_never_fails_on_the_gap_below_its_last_block():
    """Whatever the length of the first-page remarks, the report builds: it shrinks, then moves
    the remarks to their own pages, but never refuses because of the spacing after the blocks."""
    p = project()
    for length in range(0, 3000, 250):
        d = new_document(p)
        d['figure']['include'] = False
        d['quantity_table']['include'] = False
        for entry in d['samples']:
            entry['manual_note'] = 'x' * length
        pages = build_pages(p, validate_document(p, d), lambda fid: b'')
        summary = next(x for x in pages if x['kind'] == 'summary')
        assert all(i['y'] + i['h'] <= BOTTOM for i in summary['items'] if not i.get('role'))


def test_a_cover_with_every_field_at_its_limit_stays_on_the_page():
    p, d = doc(
        title='가' * 120,
        client='나' * 120,
        requester='다' * 80,
        analysis_period='라' * 60,
        report_number='마' * 80,
        received_date='2026-10-01',
        experiment_code='바' * 100,
        equipment_code='사' * 100,
        operator_id='아' * 100,
        author='자' * 80,
    )
    cover = build_pages(p, d, lambda fid: b'')[0]
    assert all(i['y'] + i['h'] <= BOTTOM for i in cover['items'] if not i.get('role'))
    assert len(texts(cover)) == 9

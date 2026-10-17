import io

import pytest
from .figure_fixtures import make_document, make_project, make_sample
from pptx import Presentation
from pypdf import PdfReader

from app.xrd_workbench.core.report import new_document, validate_document
from app.xrd_workbench.render import theme
from app.xrd_workbench.render.html import render_html
from app.xrd_workbench.render.layout import BOTTOM, LEFT, WIDTH, build_pages
from app.xrd_workbench.render.pdf import render_pdf
from app.xrd_workbench.render.pptx import render_pptx

WPPF = {'wppf_report': {'filename': 'w.pdf'}}


def q(phase, raw, origin='pdf'):
    return {'phase': phase, 'raw': raw, 'unit': 'wt%', 'origin': origin, 'page': 1}


def sample(i, quantities, reports=WPPF):
    s = make_sample(f's{i}', f'시료 {i}', [], [])
    s['quantities'] = quantities
    s['reports'] = dict(reports)
    return s


def pages_for(samples, **extra):
    project = make_project(samples)
    document = make_document(project, include=False)
    document.update(extra)
    return build_pages(project, document, lambda fid: b''), project, document


def table_pages(pages):
    return [p for p in pages if p['kind'] == 'table']


def body(page):
    return [i for i in page['items'] if not i.get('role')]


def texts(page):
    return [i['text'] for i in body(page) if i['type'] == 'text']


def test_new_documents_include_the_table_and_it_follows_the_figure_pages():
    s = [sample(0, [q('A', '1(1)')])]
    project = make_project(s)
    document = make_document(project, table=True)  # figure on (default)
    assert document['quantity_table'] == {'include': True}
    kinds = [p['kind'] for p in build_pages(project, document, lambda fid: b'')]
    assert kinds == ['cover', 'figure', 'summary', 'table', 'sample']


def test_a_document_saved_before_the_table_opens_with_it_off_and_keeps_its_pages():
    project = make_project([sample(0, [q('A', '1(1)')])])
    saved = new_document(project)
    del saved['quantity_table']
    assert validate_document(project, saved)['quantity_table'] == {'include': False}
    saved['figure']['include'] = False
    kinds = [p['kind'] for p in build_pages(project, saved, lambda fid: b'')]
    assert kinds == ['cover', 'summary', 'sample']


@pytest.mark.parametrize('value', ['yes', 1, None, {'include': 1}, {'include': 'yes'}, []])
def test_an_invalid_quantity_table_block_is_rejected(value):
    project = make_project([sample(0, [q('A', '1(1)')])])
    document = new_document(project)
    document['quantity_table'] = value
    with pytest.raises(ValueError):
        validate_document(project, document)


def test_cells_show_the_original_tokens_with_the_states_for_missing_values():
    a = sample(0, [q('PhaseA', '12.34(5)'), q('PhaseB', '0.50(2)')])
    b = sample(1, [q('PhaseA', '7.00(1)')])
    c = sample(2, [], reports={})
    pages, *_ = pages_for([a, b, c], quantity_table={'include': True})
    page = table_pages(pages)[0]
    flat = ' | '.join(texts(page))
    for expected in ('12.34(5) wt%', '0.50(2) wt%', '7.00(1) wt%', '미기재', '정량 자료 없음'):
        assert expected in flat, expected
    assert not any(t in ('0', '0%', '-') for t in texts(page))
    assert 'PhaseA' in flat and 'PhaseB' in flat and '시료 0' in flat


def test_the_fixed_footnote_is_printed_and_the_manual_footnote_only_when_needed():
    plain, *_ = pages_for([sample(0, [q('A', '1(1)')])], quantity_table={'include': True})
    flat = ' '.join(texts(table_pages(plain)[-1]))
    assert '원문 표기' in flat and '†' not in flat
    manual, *_ = pages_for(
        [sample(0, [q('A', '1(1)', origin='manual')])], quantity_table={'include': True}
    )
    flat = ' '.join(texts(table_pages(manual)[-1]))
    assert '† 직접 입력' in flat and '1(1) wt% †' in flat


def test_more_than_eight_phases_split_into_tables_that_repeat_every_sample_row():
    quantities = [q(f'Phase{i}', f'{i}(1)') for i in range(11)]
    pages, project, document = pages_for(
        [sample(0, quantities), sample(1, quantities)], quantity_table={'include': True}
    )
    tp = table_pages(pages)
    flat = ' | '.join(' | '.join(texts(p)) for p in tp)
    assert '표 1/2' in flat and '표 2/2' in flat
    assert flat.count('시료 0') >= 2 and flat.count('시료 1') >= 2
    assert all(f'Phase{i}' in flat for i in range(11))


def test_many_samples_continue_on_more_pages_with_the_header_repeated_and_stay_inside():
    samples = [sample(i, [q('A', f'{i}(1)'), q('B', f'{i}(2)')]) for i in range(60)]
    pages, *_ = pages_for(samples, quantity_table={'include': True})
    tp = table_pages(pages)
    assert len(tp) > 1
    assert tp[0]['title'] == '정량 요약 표' and tp[1]['title'].endswith('(계속)')
    for page in tp:
        assert any(t == '시료' for t in texts(page)), page['title']
        assert all(i['y'] + i['h'] <= BOTTOM + 0.01 for i in body(page))
        assert all(LEFT <= i['x'] and i['x'] + i['w'] <= LEFT + WIDTH + 0.01 for i in body(page))
    flat = ' '.join(t for p in tp for t in texts(p))
    assert all(f'시료 {i}' in flat for i in range(60))


def test_without_any_quantity_a_message_replaces_the_table():
    pages, *_ = pages_for([sample(0, [], reports={})], quantity_table={'include': True})
    assert '등록된 시료가 없습니다' in ' '.join(texts(table_pages(pages)[0]))
    pages, *_ = pages_for([sample(0, [])], quantity_table={'include': True})
    assert '읽은 시료가 없습니다' in ' '.join(texts(table_pages(pages)[0]))


def test_a_phase_name_with_markup_is_escaped_and_every_output_builds():
    pages, project, document = pages_for(
        [sample(0, [q('<b>x</b>', '1(1)')])], quantity_table={'include': True}
    )
    assert '<b>x</b>' not in render_html(pages, document, 'package')
    assert len(PdfReader(io.BytesIO(render_pdf(pages, document))).pages) == len(pages)
    assert len(Presentation(io.BytesIO(render_pptx(pages, document))).slides) == len(pages)


def test_the_table_uses_only_theme_colours():
    pages, *_ = pages_for(
        [sample(0, [q('A', '1(1)')]), sample(1, [])], quantity_table={'include': True}
    )
    allowed = {theme.TEXT, theme.NAVY, theme.MUTED, theme.WARN, theme.NOTE}
    assert {
        i['color'] for p in table_pages(pages) for i in body(p) if i['type'] == 'text'
    } <= allowed


def test_a_sample_without_quantity_material_says_so_in_every_table_of_a_split_table():
    quantities = [q(f'Phase{i}', f'{i}(1)') for i in range(11)]
    pages, *_ = pages_for(
        [sample(0, quantities), sample(1, [], reports={})], quantity_table={'include': True}
    )
    tp = table_pages(pages)
    flat = [t for p in tp for t in texts(p)]
    assert flat.count('정량 자료 없음') == 2
    for number in (1, 2):
        page = next(p for p in tp if any(f'표 {number}/2' in t for t in texts(p)))
        assert '정량 자료 없음' in texts(page)

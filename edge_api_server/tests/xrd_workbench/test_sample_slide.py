"""Each included sample is one slide: phase identification, phase fraction, major/trace phases and
presumed phases, shrunk to fit, with a link to the sample's Report and data. Remarks go to the
sample's block on the first page (or to its own slide when it is not on the first page)."""

import hashlib
import io

from .figure_fixtures import make_project, make_sample
from PIL import Image

from app.xrd_workbench.core.report import new_document
from app.xrd_workbench.render.layout import BOTTOM, LEFT, WIDTH, build_pages

HEADINGS = [
    '상 동정 (Phase Identification) 결과',
    '정량 (Phase Fraction) 결과',
    '주요상 및 미량상',
    '추정상',
]


def png(w=800, h=500):
    out = io.BytesIO()
    Image.new('RGB', (w, h), 'white').save(out, 'PNG')
    return out.getvalue()


IMAGE = png()


def source(sid, role, filename, data=b'x'):
    return dict(
        id=f'{sid}-{role}-0000',
        role=role,
        filename=filename,
        sha256=hashlib.sha256(data).hexdigest(),
    )


def sample(sid, n_phases=3, images=False, quantities=None):
    s = make_sample(sid, f'시료 {sid}', [(f'Phase{i}', f'c{i}', 'DB') for i in range(n_phases)])
    s['sources'] = [
        source(sid, 'phase_report', f'{sid}_상매칭_Report.pdf'),
        source(sid, 'measurement', f'{sid}.txt'),
    ]
    if images:
        s['sources'] += [
            source(sid, 'phase_image', f'{sid}_상매칭.png', IMAGE),
            source(sid, 'quantity_image', f'{sid}_정량.png', IMAGE),
        ]
    s['reports'] = {'phase_report': {'filename': f'{sid}_상매칭_Report.pdf'}}
    s['quantities'] = [
        dict(phase=f'Phase{i}', raw=f'{10 + i}.{i}({i + 1})', value=10 + i, unit='wt%', page=1)
        for i in range(quantities if quantities is not None else n_phases)
    ]
    return s


def build(samples, edit=None):
    project = make_project(samples)
    document = new_document(project)
    document['figure']['include'] = False
    document['quantity_table']['include'] = False
    if edit:
        edit(document)
    data = {
        r['id']: (IMAGE if r['role'].endswith('image') else b'x')
        for s in samples
        for r in s['sources']
    }
    return build_pages(project, document, data.__getitem__), document


def texts(page):
    return [i['text'] for i in page['items'] if i['type'] == 'text' and not i.get('role')]


def inside(page):
    for i in page['items']:
        if i.get('role'):
            continue
        assert i['y'] + i['h'] <= BOTTOM + 1e-6, (i.get('text'), i['y'], i['h'])
        assert i['x'] >= LEFT - 1e-6 and i['x'] + i['w'] <= LEFT + WIDTH + 1e-6


def test_every_sample_is_one_slide_with_the_four_sections_in_order():
    pages, _ = build([sample('a', images=True), sample('b')])
    slides = [p for p in pages if p['kind'] == 'sample']
    assert [p['title'] for p in slides] == ['시료 a', '시료 b']
    for page in slides:
        found = [t for t in texts(page) if t in HEADINGS]
        assert found == HEADINGS
        inside(page)
    assert sum(i['type'] == 'image' for i in slides[0]['items']) == 2


def test_the_sections_carry_the_phases_and_original_quantity_tokens():
    pages, _ = build([sample('a')])
    body = '\n'.join(texts(next(p for p in pages if p['kind'] == 'sample')))
    assert all(f'Phase{i}' in body for i in range(3))
    assert all(f'{10 + i}.{i}({i + 1}) wt%' in body for i in range(3))
    assert '주요상: ' not in body and '미분류' in body
    assert '작성자가 지정한 추정상이 없습니다.' in body


def test_a_long_sample_stays_on_one_slide_shrunk_and_points_to_the_report():
    pages, _ = build([sample('a', n_phases=120, images=True)])
    slides = [p for p in pages if p['kind'] == 'sample']
    assert len(slides) == 1
    inside(slides[0])
    body = '\n'.join(texts(slides[0]))
    assert '(원본 Report 참조)' in body
    sizes = {i['size'] for i in slides[0]['items'] if i['type'] == 'text' and not i.get('role')}
    assert max(sizes) < 11  # headings shrank


def test_the_slide_links_to_the_samples_report_and_data():
    pages, _ = build([sample('a')])
    links = [i for p in pages if p['kind'] == 'sample' for i in p['items'] if i['type'] == 'link']
    assert links[0]['label'] == '시료 a의 Report 및 데이터 확인하기'
    assert links[0]['href'].startswith('Report/') and links[0]['href'].endswith('_Report.pdf')
    assert {link['label'] for link in links[1:]} == {'상매칭 Report', 'Measured Data (TXT)'}
    row_y = {round(link['y'], 3) for link in links}
    assert len(row_y) == 1  # one row at the bottom of the slide
    assert all(i['x'] + i['w'] <= LEFT + WIDTH + 1e-6 for i in links)


def summary_page(pages):
    return next(p for p in pages if p['kind'] == 'summary')


def remark_pages(pages):
    return ['\n'.join(texts(p)) for p in pages if p['kind'] == 'remarks']


def test_remarks_go_to_the_first_page_block_or_the_remark_pages_never_the_slide():
    def edit(document):
        document['samples'][0]['manual_note'] = '작성자 메모 A'
        document['samples'][1]['summary_included'] = False
        document['samples'][1]['manual_note'] = '작성자 메모 B'

    pages, document = build([sample('a'), sample('b')], edit)
    cover = '\n'.join(texts(summary_page(pages)))
    slides = '\n'.join('\n'.join(texts(p)) for p in pages if p['kind'] == 'sample')
    first_note = next(n for n in document['samples'][0]['notes'] if n['selected'])
    assert '특이사항' in cover and '작성자 메모 A' in cover
    assert ''.join(first_note['text'].split())[:20] in ''.join(cover.split())
    assert '특이사항' not in slides and '작성자 메모' not in slides
    # Not on the first page: the remarks follow it on the remark pages, under the sample name.
    assert '작성자 메모 B' not in cover
    [remarks] = remark_pages(pages)
    assert '시료 b' in remarks and '작성자 메모 B' in remarks and '작성자 메모 A' not in remarks
    assert [p['kind'] for p in pages][:3] == ['cover', 'summary', 'remarks']


def test_long_author_text_continues_on_remark_pages_whole():
    def edit(document):
        document['samples'][0]['summary_included'] = False
        document['samples'][0]['manual_note'] = 'QA-START ' + '긴 문장입니다. ' * 1000 + 'QA-END'

    pages, _ = build([sample('a')], edit)
    remarks = remark_pages(pages)
    assert len(remarks) > 1
    flat = ''.join(''.join(remarks).split())
    assert flat.count('긴문장입니다.') == 1000 and 'QA-START' in flat and 'QA-END' in flat
    for page in pages:
        inside(page)
    assert [p['title'] for p in pages if p['kind'] == 'remarks'][1].endswith('(계속)')


def test_four_first_page_samples_with_remarks_fit_by_shrinking():
    samples = [sample(c, n_phases=6) for c in 'abcd']
    pages, _ = build(samples)
    inside(summary_page(pages))
    assert sum('특이사항' in t for t in texts(summary_page(pages))) == 4


def test_remarks_too_long_for_the_first_page_move_to_the_remark_pages_instead_of_failing():
    def edit(document):
        document['samples'][0]['manual_note'] = 'QA-START ' + '긴 문장입니다. ' * 700 + 'QA-END'

    pages, _ = build([sample('a')], edit)
    cover = '\n'.join(texts(summary_page(pages)))
    assert '특이사항: 시료별 특이사항 페이지에 수록' in cover
    flat = ''.join(''.join(remark_pages(pages)).split())
    assert 'QA-START' in flat and 'QA-END' in flat


def test_the_main_link_goes_to_the_phase_report_whatever_the_registration_order():
    s = sample('a')
    s['sources'].reverse()  # the TXT was registered first
    pages, _ = build([s])
    links = [i for p in pages if p['kind'] == 'sample' for i in p['items'] if i['type'] == 'link']
    assert links[0]['href'].endswith('_Report.pdf')
    assert [link['label'] for link in links[1:]] == ['상매칭 Report', 'Measured Data (TXT)']

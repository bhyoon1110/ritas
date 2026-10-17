import io

from .figure_fixtures import make_project, make_sample
from pptx import Presentation
from pypdf import PdfReader

from app.xrd_workbench.core.report import new_document
from app.xrd_workbench.render.html import render_html
from app.xrd_workbench.render.layout import BOTTOM, build_pages
from app.xrd_workbench.render.pdf import render_pdf
from app.xrd_workbench.render.pptx import render_pptx

NAMES = ['PhaseA', 'PhaseB', 'PhaseC', 'PhaseD']


def ref(name, card='c1', db='DB'):
    return {'name': name, 'card': card, 'db': db}


def label(sample_id, name, cls, origin='manual', card='c1', percent=2):
    value = {'sample_id': sample_id, 'phase': ref(name, card), 'class': cls, 'origin': origin}
    if origin == 'rule':
        value['percent'] = percent
    return value


def sample(names=NAMES, quantities=None, reports=None):
    s = make_sample('s1', '시료 1', [(n, 'c1', 'DB') for n in names], [])
    for phase in s['phases']:
        if quantities and phase['name'] in quantities:
            phase['quantity'] = {'raw': quantities[phase['name']], 'unit': 'wt%'}
    s['reports'] = reports or {}
    return s


def pages_for(s, **classes):
    project = make_project([s])
    document = new_document(project)
    document['figure']['include'] = False
    document['phase_classes'].update(classes)
    return build_pages(project, document, lambda fid: b''), project, document


def sample_page_text(pages):
    return '\n'.join(
        i['text'] for p in pages if p['kind'] == 'sample' for i in p['items'] if i['type'] == 'text'
    )


def test_without_classes_or_criteria_the_sample_pages_are_unchanged():
    s = sample()
    pages, project, document = pages_for(s)
    old = dict(document)
    del old['phase_classes']
    older = build_pages(project, old, lambda fid: b'')
    assert [p['items'] for p in pages if p['kind'] == 'sample'] == [
        p['items'] for p in older if p['kind'] == 'sample'
    ]
    assert '작성자 분류' not in sample_page_text(pages)


def test_only_unclassified_phases_and_no_criteria_adds_nothing():
    pages, *_ = pages_for(sample(), labels=[])
    assert '작성자 분류' not in sample_page_text(pages)


def test_phases_are_listed_by_class_with_the_original_quantity_token():
    s = sample(quantities={'PhaseA': '12.34(5)', 'PhaseB': '0.56(7)'})
    labels = [label('s1', 'PhaseB', 'trace'), label('s1', 'PhaseA', 'major')]
    text = sample_page_text(pages_for(s, labels=labels)[0])
    lines = text.split('\n')
    assert '주요상 및 미량상' in lines
    assert '주요상: PhaseA (12.34(5) wt%)' in lines
    assert '미량상: PhaseB (0.56(7) wt%)' in lines
    assert not any(line.startswith('추정상:') for line in lines)
    assert '작성자가 지정한 추정상이 없습니다.' in lines
    # PhaseC and PhaseD were not classified: the default is 주요상, listed with PhaseA.
    assert any(
        line.startswith('미분류') and 'PhaseC' in line and 'PhaseD' in line
        for line in lines
    )
    assert any(line.startswith('미분류') for line in lines)
    major = next(i for i, x in enumerate(lines) if x.startswith('주요상: PhaseA'))
    assert major < lines.index('미량상: PhaseB (0.56(7) wt%)')


def test_a_missing_quantity_is_not_available_or_needs_checking_but_never_zero():
    labels = [label('s1', 'PhaseA', 'major')]
    none = sample_page_text(pages_for(sample(['PhaseA']), labels=labels)[0])
    assert '주요상: PhaseA (미제공)' in none
    registered = sample(['PhaseA'], reports={'wppf_report': {'filename': 'r.pdf'}})
    checking = sample_page_text(pages_for(registered, labels=labels)[0])
    assert '주요상: PhaseA (확인 필요)' in checking
    assert '0 wt%' not in none + checking and '0%' not in none + checking


def test_criteria_text_and_the_applied_rule_are_printed():
    s = sample(['PhaseA'])
    labels = [label('s1', 'PhaseA', 'trace', 'rule')]
    classes = dict(labels=labels, criteria_text='작성자 판단 기준', rule={'trace_max_percent': 2})
    text = sample_page_text(pages_for(s, **classes)[0])
    assert '작성자 판단 기준' in text
    assert (
        '적용 기준: 미량상 = d-I List의 최강·차강 피크에 연결된 관측 Height가 시료 최대 Height의 2% 이하'
        in text
    )


def test_the_rule_line_is_not_printed_when_no_class_came_from_the_rule():
    s = sample(['PhaseA'])
    classes = dict(
        labels=[label('s1', 'PhaseA', 'major')],
        criteria_text='기준',
        rule={'trace_max_percent': 2},
    )
    assert '적용 기준' not in sample_page_text(pages_for(s, **classes)[0])


def test_criteria_alone_lists_every_phase_as_unclassified():
    text = sample_page_text(pages_for(sample(['PhaseA']), criteria_text='기준만')[0])
    assert '기준만' in text and '미분류 (검토 필요): PhaseA (미제공)' in text


def test_a_long_phase_list_stays_inside_the_one_slide_and_points_to_the_report():
    many = [f'Phase{i}' for i in range(120)]
    labels = [label('s1', n, 'trace') for n in many[:60]]
    pages, *_ = pages_for(sample(many), labels=labels)
    assert len([p for p in pages if p['kind'] == 'sample']) == 1
    assert all(i['y'] + i['h'] <= BOTTOM for p in pages for i in p['items'] if not i.get('role'))
    flat = ''.join(sample_page_text(pages).split())
    assert 'Phase0(미제공)' in flat and 'Phase60(미제공)' in flat
    assert '(원본Report참조)' in flat


def test_names_are_escaped_in_html_and_every_output_builds():
    s = sample(['<b>x</b>'])
    pages, project, document = pages_for(s, labels=[label('s1', '<b>x</b>', 'major')])
    assert '<b>x</b>' not in render_html(pages, document, 'package')
    assert len(PdfReader(io.BytesIO(render_pdf(pages, document))).pages) == len(pages)
    assert len(Presentation(io.BytesIO(render_pptx(pages, document))).slides) == len(pages)


def test_each_rule_label_states_the_percent_it_was_applied_with_even_if_the_last_run_differs():
    s = sample(['PhaseA', 'PhaseB'])
    labels = [
        label('s1', 'PhaseA', 'trace', 'rule', percent=5),
        label('s1', 'PhaseB', 'trace', 'rule', percent=1),
    ]
    text = sample_page_text(pages_for(s, labels=labels, rule={'trace_max_percent': 1})[0])
    assert '시료 최대 Height의 5% 이하' in text and '시료 최대 Height의 1% 이하' in text
    assert text.index('1% 이하') < text.index('5% 이하')

"""All phase identities remain in native cells below their matching graph."""
from copy import deepcopy

import pytest

from app.xrd_workbench.core.report import new_document
from app.xrd_workbench.render.posco_pptx import build_posco_deck, named, NS, box, phase_table_data
from app.xrd_workbench.render.posco_preview import deck_pages, text_items
from .figure_fixtures import make_project
from .test_sample_slide import sample, IMAGE


@pytest.mark.parametrize('count', [0, 3, 4, 8, 12, 18])
@pytest.mark.parametrize('quantitative', [False, True])
def test_complete_phase_table_fits_same_slide_and_preview(count, quantitative):
    s = sample('dense', n_phases=count, quantities=3 if quantitative else 0)
    for i, p in enumerate(s['phases']):
        p.update(name=f'결정상 {i+1} / Phase {i+1}', formula='Al2(SO4)3', card=f'00-062-{i:04}')
    project = make_project([s])
    doc = new_document(project)
    doc['figure']['include'] = False
    before = deepcopy((project, doc))
    filled = build_posco_deck(project, doc, lambda _: IMAGE, image_renderer=lambda *a: IMAGE)
    details = [slide for slide, _ in filled.pages if filled.page_info[slide]['kind'] == 'sample']
    assert len(details) == 1
    slide = details[0]
    table = named(slide, 'Phase table')[0]
    assert len(table.findall('.//a:tr', NS)) == max(1, count) + 1
    assert not named(slide, 'Phase continuation')
    _, expected = phase_table_data(s, lambda *args: None, revised=quantitative)
    rows = table.findall('.//a:tr', NS)
    for row, values in zip(rows[1:], expected):
        cells = row.findall('a:tc', NS)
        assert len(cells) == 3
        for cell, value in zip(cells, values):
            actual = ''.join(cell.xpath('.//a:t/text()', namespaces=NS))
            assert ''.join(actual.split()) == ''.join(value.split())
            assert '…' not in actual
    x,y,w,h = box(table)
    assert y+h <= (166 if quantitative else 160.4) + .001
    frame = named(slide, 'Match frame')[0]
    label = named(slide, 'Section label')[0]
    assert box(frame)[1]+box(frame)[3] <= box(label)[1]
    assert box(label)[1]+box(label)[3] <= y
    assert box(frame)[3] >= 44
    # Projection uses the exact same cells, margins, line spacing and font sizes.
    widths = [int(c.get('w'))/36000 for c in table.findall('.//a:gridCol', NS)]
    for row in rows:
        height = int(row.get('h'))/36000
        for cell,width in zip(row.findall('a:tc', NS),widths):
            props = cell.find('a:tcPr', NS)
            margins = {key:int(props.get(key))/36000 for key in ('marL','marR','marT','marB')}
            items = text_items(cell.find('a:txBody',NS),
                               (0,0,width-margins['marL']-margins['marR'],height-margins['marT']-margins['marB']))
            assert all(i['y']+i['h'] <= height-margins['marT']-margins['marB']+.01 for i in items)
    pages = deck_pages(filled)
    page = next(p for p in pages if p['kind'] == 'sample')
    assert any(i['type'] == 'rect' and i['x'] == x and i['y'] == y for i in page['items'])
    assert before == (project,doc)


def test_unequal_counts_do_not_repeat_graphs_or_move_rows_to_other_sample():
    project = make_project([sample('few', n_phases=3, quantities=0), sample('many', n_phases=12, quantities=0)])
    for p in project['samples'][1]['phases']:
        p['name'] += ' · 긴 결정상 이름을 생략하지 않고 한 셀에 표시합니다'
    doc = new_document(project)
    doc['figure']['include'] = False
    filled = build_posco_deck(project,doc,lambda _: IMAGE,image_renderer=lambda *a: IMAGE)
    details = [s for s,_ in filled.pages if filled.page_info[s]['kind']=='sample']
    assert len(details) == 1
    tables = named(details[0],'Phase table')
    assert [len(t.findall('.//a:tr',NS))-1 for t in tables] == [3,12]
    assert len([p for p in details[0].findall('.//p:pic',NS)
                if filled.edits.get(p,{}).get('field') == 'phase_caption']) == 2
    assert box(tables[0])[0]+box(tables[0])[2] < box(tables[1])[0]
    assert '생략하지' in ''.join(tables[1].itertext()) and '…' not in ''.join(tables[1].itertext())

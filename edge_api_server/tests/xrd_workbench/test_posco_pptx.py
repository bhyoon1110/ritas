"""The supplied three-layout deck is a presentation template, never example analysis data."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import io
import posixpath
from zipfile import ZipFile

from lxml import etree as E
from openpyxl import load_workbook

from app.xrd_workbench.core.report import new_document
from app.xrd_workbench.core.phase_class import phase_ref
from app.xrd_workbench.render.posco_pptx import TEMPLATE, NS, Deck, render_report_pptx
from .figure_fixtures import make_project
from .test_sample_slide import sample, IMAGE


def fixture():
    project=make_project([sample('A',quantities=0),sample('B',quantities=0),sample('Mix',quantities=3)])
    for quantity in project['samples'][2]['quantities']:
        quantity['value']=float(quantity['raw'].split('(')[0])
    document=new_document(project)
    document['figure']['include']=False
    document['quantity_table']['include']=False
    document['metadata'].update(title='POSCO 합성 검증',report_number='REQ-123',requester='시험 의뢰자',
                                equipment_code='XRD-01',operator_id='QA-OP')
    document['samples'][0]['manual_note']='측정값을 그대로 보존하는 검토 문장'
    for cls,phase in zip(('major','trace','presumed'),project['samples'][0]['phases']):
        document['phase_classes']['labels'].append(dict(sample_id='A',phase=phase_ref(phase),
                                                        origin='manual',**{'class':cls}))
    return project,document


def deck(data):
    z=ZipFile(io.BytesIO(data))
    names=sorted((n for n in z.namelist() if n.startswith('ppt/slides/slide') and n.endswith('.xml')),
                 key=lambda n:int(n.split('slide')[-1].split('.')[0]))
    slides=[E.fromstring(z.read(n)) for n in names]
    text='\n'.join(''.join(s.itertext()) for s in slides)
    return z,slides,text


def test_reference_background_dimensions_and_sanitized_example_data():
    project,document=fixture()
    before=deepcopy((project,document))
    z,slides,text=deck(render_report_pptx(project,document,lambda _:IMAGE))
    with ZipFile(TEMPLATE) as original:
        for name in original.namelist():
            if name.startswith(('ppt/slideMasters/','ppt/slideLayouts/','ppt/theme/','ppt/media/')):
                assert z.read(name)==original.read(name),name
    size=E.fromstring(z.read('ppt/presentation.xml')).find('p:sldSz',NS)
    assert (size.get('cx'),size.get('cy'))==('9906000','6858000')
    assert 'Zinc Manganese' not in text and '00-062-0414' not in text and '{Sample' not in text
    assert all(value in text for value in ('REQ-123','시험 의뢰자','XRD-01','QA-OP','검토 문장'))
    assert before==(project,document)
    assert len(slides[2].findall('.//a:tbl',NS))==2  # phase-only samples share the reference layout
    assert len(slides[3].findall('.//a:tbl',NS))==1  # WPPF sample has its own page
    assert '[주요상] Phase0' in text and '[미량상] Phase1' in text and '[추정상] Phase2' in text
    assert '[미분류] Phase0' in text  # no implicit scientific classification


def test_native_chart_workbook_and_cache_preserve_quantities_and_raw_precision():
    project,document=fixture()
    z,_,text=deck(render_report_pptx(project,document,lambda _:IMAGE))
    chart=E.fromstring(z.read('ppt/charts/chart1.xml'))
    values=chart.findall('.//c:numCache/c:pt/c:v',NS)
    assert [float(v.text) for v in values]==[10,11.1,12.2]
    book=load_workbook(io.BytesIO(z.read('ppt/embeddings/quantity1.xlsx')),data_only=True)
    assert list(book.active.values)[1:]==[('Phase0',10,'10.0(1)'),('Phase1',11.1,'11.1(2)'),('Phase2',12.2,'12.2(3)')]
    assert all(v in text for v in ('10.0(1) wt%','11.1(2) wt%','12.2(3) wt%'))
    assert '합계 33.3 wt%' in text
    # Every internal relationship resolves, including charts and workbooks.
    for name in z.namelist():
        if not name.endswith('.rels'):continue
        root=E.fromstring(z.read(name))
        parent=posixpath.dirname(name).replace('/_rels','') if name!='_rels/.rels' else ''
        for rel in root:
            if rel.get('TargetMode')=='External':continue
            target=posixpath.normpath(posixpath.join(parent,rel.get('Target')))
            assert target in z.namelist(),(name,target)


def test_long_rows_notes_more_samples_and_odd_pair_are_not_lost():
    project,document=fixture()
    project['samples']= [sample(str(i),n_phases=8,quantities=0) for i in range(5)]
    document=new_document(project)
    document['figure']['include']=False;document['quantity_table']['include']=False
    for i,e in enumerate(document['samples']):e['summary_included']=i<4
    document['samples'][0]['manual_note']='QA-START '+('긴 검토문장입니다. '*180)+' QA-END'
    z,slides,text=deck(render_report_pptx(project,document,lambda _:IMAGE,include_sources=False))
    assert 'QA-START' in text and 'QA-END' in text
    # Join body text across continuation pages without inserting headings/page numbers
    # into a sentence that happens to cross the page boundary.
    bodies=''.join(''.join(shape.itertext()) for slide in slides
                   for shape in slide.findall('.//p:sp',NS)
                   if shape.xpath('.//p:cNvPr/@name',namespaces=NS)==['Report text'])
    assert ''.join(bodies.split()).count('긴검토문장입니다.')==180
    assert all('시료 '+str(i) in text for i in range(5))
    assert 'Phase7' in text and '계속' in text
    for n in z.namelist():
        if n.endswith('.rels'):assert b'TargetMode="External"' not in z.read(n)
    for slide in slides:
        for shape in slide.findall('.//p:sp',NS):
            xf=shape.find('p:spPr/a:xfrm',NS)
            if xf is not None:assert int(xf[0].get('y'))+int(xf[1].get('cy'))<=6858000


def test_concurrent_templates_do_not_leak_titles_or_data():
    project,document=fixture()
    def output(title):
        value=deepcopy(document);value['metadata']['title']=title
        return deck(render_report_pptx(project,value,lambda _:IMAGE))[2]
    titles=['동시 검증 AAA','동시 검증 BBB']
    with ThreadPoolExecutor(2) as pool:values=list(pool.map(output,titles))
    for i,value in enumerate(values):assert titles[i] in value and titles[1-i] not in value


def test_source_artwork_is_exact_and_user_quantity_exclusions_remain_missing():
    project,document=fixture()
    from .test_sample_slide import source
    project['samples'][2]['sources'].append(source('Mix','quantity_image','quant.png',IMAGE))
    document['samples'][2]['include_quantity_image']=True
    z,_,_=deck(render_report_pptx(project,document,lambda _:IMAGE))
    assert IMAGE in [z.read(n) for n in z.namelist() if n.startswith('ppt/media/')]
    assert not any(n.startswith('ppt/charts/chart') for n in z.namelist())


def test_missing_inconsistent_or_mixed_unit_quantities_are_not_silently_graphed():
    for change in ({'value':None},{'value':-1},{'value':999},{'unit':'vol%'}):
        project,document=fixture()
        project['samples'][2]['quantities'][0].update(change)
        document=new_document(project)
        document['figure']['include']=False;document['quantity_table']['include']=False
        z,_,text=deck(render_report_pptx(project,document,lambda _:IMAGE))
        assert not any(n.startswith('ppt/charts/chart') for n in z.namelist())
        assert '10.0(1)' in text


def test_valid_long_sample_names_do_not_break_export_and_remain_complete():
    project,document=fixture()
    name='긴샘플이름'*30
    document['samples'][0]['display_name']=name
    _,_,text=deck(render_report_pptx(project,document,lambda _:IMAGE))
    assert name in ''.join(text.split())
    assert '긴 제목 전체' in text


def test_phase_names_in_embedded_workbook_are_literal_strings_not_formulas():
    output=Deck();slide=output.slide(blank=True)
    assert output.pie(slide,[{'phase':'=1+1','value':100,'raw':'100.0','unit':'wt%'}],(20,30,100,100))
    book=load_workbook(io.BytesIO(output.charts[0][1]),data_only=False)
    assert book.active['A2'].value=='=1+1'
    assert book.active['A2'].data_type=='s'

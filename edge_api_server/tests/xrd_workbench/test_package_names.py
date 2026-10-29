"""Human-readable names must still address exact source bytes and viewer identities."""
from copy import deepcopy
import io
import json
import re
import unicodedata
from urllib.parse import parse_qs, unquote, urlsplit
from zipfile import ZipFile

import pytest
from pypdf import PdfReader

from app.report.pptx_protection import create_protection
from app.xrd_workbench.core.project import build_project
from app.xrd_workbench.core.report import new_document
from app.xrd_workbench.render.package_names import package_paths, portable_filename
from app.xrd_workbench.render.package import build_package
from app.xrd_workbench.render.link_audit import check_package_links, ppt_click_targets
from .synthetic import sample_files


@pytest.mark.parametrize('original, expected', [
    ('2.txt', '2.txt'), ('시료 A #1 (50%).pdf','시료 A #1 (50%).pdf'),
    ('../CON.txt', '.._CON.txt'), ('CON.txt', '_CON.txt'), ('com1.html', '_com1.html'),
    ('a/b:c?.html','a_b_c_.html'), ('\x00bad\n.pdf', '_bad_.pdf'),
    ('시료. ', '시료'), ('...', '파일'),
])
def test_portable_names_preserve_normal_names_and_neutralize_windows_paths(original, expected):
    assert portable_filename(original) == expected
    name = portable_filename(original)
    assert not re.search(r'[\\/:*?"<>|\x00-\x1f]', name)


def test_unicode_and_long_names_keep_extensions_and_stable_numeric_collisions():
    names = ['시료', unicodedata.normalize('NFD','시료'), '시료 (2)', '긴시료'*150, '긴시료'*151]
    project = {'samples':[{'id':str(i), 'name':name, 'sources':[]} for i,name in enumerate(names)]}
    paths = package_paths(project)
    assert {paths['viewers'][str(i)] for i in range(3)} == {'Viewer/시료.html','Viewer/시료 (2).html','Viewer/시료 (3).html'}
    assert len({v.casefold() for v in paths['viewers'].values()}) == 5
    assert all(v.endswith('.html') and len(v.rsplit('/',1)[-1].encode()) < 200 for v in paths['viewers'].values())
    assert package_paths({'samples':list(reversed(project['samples']))}) == paths


@pytest.fixture(scope='module')
def collision_project():
    originals = sample_files('SampleA')
    raw, pdf = originals['SampleA.txt'], originals['SampleA_상매칭_Report.pdf']
    files, mappings = {}, {}
    for sample, txt_name, pdf_name in [('Same/Name','A.txt','Phase.pdf'),
            ('Same:Name','a.TXT','phase.PDF'), ('Same_Name (2)','a (2).txt','phase (2).pdf'),
            ('시료 A #1 (50%)','시료 A #1 (50%).txt','시료 A #1 (50%)_상매칭.pdf')]:
        for name, data, role in [(txt_name,raw,'measurement'),(pdf_name,pdf,'phase_report')]:
            files[name] = data
            mappings[name] = {'sample':sample,'role':role,'axis_kind':'two_theta','intensity_unit':'cps'}
    project = build_project(list(files.items()), mappings)
    data = {r['id']:files[r['filename']] for r in project['sources']}
    return project, new_document(project), data


@pytest.mark.parametrize('password', [False, True])
def test_delivery_names_bytes_ppt_pdf_and_viewer_all_agree(collision_project, password):
    project, document, data = collision_project
    before = deepcopy((project, document))
    doc = deepcopy(document)
    if password: doc['pptxProtection'] = create_protection('review')
    paths = package_paths(project)
    result = build_package(project, doc, data.__getitem__)
    with ZipFile(io.BytesIO(result)) as archive:
        names = archive.namelist()
        assert {'Measured Data/A.txt', 'Measured Data/a (3).TXT', 'Measured Data/a (2).txt'} <= set(names)
        assert {'Report/Phase.pdf', 'Report/phase (3).PDF', 'Report/phase (2).pdf'} <= set(names)
        assert {'Viewer/Same_Name.html','Viewer/Same_Name (2).html','Viewer/Same_Name (3).html'} <= set(names)
        assert 'Viewer/시료 A #1 (50%).html' in names
        assert len(names) == len({unicodedata.normalize('NFC', n).casefold() for n in names})
        assert not any(re.match(r'(?:Measured Data|Report|Viewer)/(?:sample-)?[a-f0-9]{8,}', n) for n in names)
        ppt_links = ppt_click_targets(archive.read('보고서.pptx'))
        pdf = PdfReader(io.BytesIO(archive.read('보고서.pdf')))
        pdf_links = [str(a.get_object().get('/A', {}).get('/URI', '')) for p in pdf.pages for a in p.get('/Annots', [])]
        assert not ppt_links  # PPT originals live inside the PPT itself.
        check_package_links(pdf_links, archive)
        from .test_offline_pptx import embedded_files
        embedded = embedded_files(archive.read('보고서.pptx'))
        assert '보고서.html' not in names
        for record in project['sources']:
            target = paths['sources'][record['id']]
            assert archive.read(target) == data[record['id']]
            assert embedded[target.rsplit('/',1)[-1]] == data[record['id']]
        for sample in project['samples']:
            target = paths['viewers'][sample['id']]
            entry = archive.read(target).decode()
            destination = json.loads(re.search(r'location\.replace\((".*")\);', entry)[1])
            assert parse_qs(urlsplit(destination).fragment)['sample'] == [sample['id']]
            assert any(unquote(urlsplit(link).path) == target for link in pdf_links)
            assert target.rsplit('/',1)[-1] in embedded
    assert before == (project, document)


def test_excluding_a_colliding_sample_keeps_exact_link_allocations(collision_project):
    project, document, data = collision_project
    doc = deepcopy(document)
    doc['samples'][0]['included'] = False
    paths = package_paths(project)
    with ZipFile(io.BytesIO(build_package(project, doc, data.__getitem__))) as archive:
        excluded = project['samples'][0]
        assert paths['viewers'][excluded['id']] not in archive.namelist()
        assert all(paths['sources'][r['id']] not in archive.namelist() for r in excluded['sources'])
        check_package_links(ppt_click_targets(archive.read('보고서.pptx')), archive)
        for sample in project['samples'][1:]:
            assert paths['viewers'][sample['id']] in archive.namelist()

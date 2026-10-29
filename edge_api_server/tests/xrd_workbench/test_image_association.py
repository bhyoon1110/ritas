import hashlib
import unicodedata
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.xrd_workbench import integration as api
from app.xrd_workbench.core.project import build_project, classify_filename, normalize_mappings
from app.xrd_workbench.core.report import new_document, validate_document
from app.xrd_workbench.core.checklist import build_checklist
from app.xrd_workbench.core.source_images import phase_image_choices, phase_image_record
from app.xrd_workbench.storage import Storage
from .synthetic import _png
from .test_integration import make_app, poll

RAW = b'#Intensity_unit=cps\n10 1\n20 4\n30 2\n'


@pytest.mark.parametrize('name', ['A 상매칭.png', 'A-상매칭.jpeg', 'A_상매칭 이미지.PNG', 'A_상 매칭_이미지.jpg'])
def test_explicit_image_name_variants(name):
    for text in (name, unicodedata.normalize('NFD', name), 'C:\\data\\'+name):
        assert classify_filename(text) == dict(sample='A', role='phase_image')


@pytest.mark.parametrize('name', ['사진.png', 'A_상매칭_Report.png', 'A_상매칭 (1).png', 'A_상매칭.tiff', 'A_정량.png'])
def test_ambiguous_or_other_roles_are_not_guessed(name):
    assert classify_filename(name)['role'] != 'phase_image'


def test_nfd_and_client_paths_keep_explicit_image_and_txt_mappings():
    raw_name = unicodedata.normalize('NFD', '시료.txt')
    image_name = unicodedata.normalize('NFD', '원본사진.PNG')
    mappings = {raw_name: dict(sample='A', role='measurement', axis_kind='two_theta'),
                'C:\\data\\'+image_name: dict(sample='A', role='phase_image')}
    before = deepcopy(mappings)
    project = build_project([(raw_name, RAW), (image_name, _png('source', 90))], mappings)
    sample, = project['samples']
    assert sample['name'] == 'A' and sample['measurement']['axis_kind'] == 'two_theta'
    assert [r['role'] for r in sample['sources']] == ['measurement', 'phase_image']
    assert sample['sources'][1]['filename'] == '원본사진.PNG'
    assert mappings == before


def test_colliding_normalized_mappings_and_duplicate_roles_require_user_choice():
    name = '시료.png'
    with pytest.raises(ValueError, match='중복'):
        normalize_mappings({name:{}, unicodedata.normalize('NFD',name):{}})
    data = _png('same bytes', 90)
    files = [('A.txt', RAW), ('A_상매칭.png', data), ('A 상매칭.png', data)]
    with pytest.raises(ValueError, match='중복'):
        build_project(files)
    project = build_project(files, {'A 상매칭.png':dict(role='ignore')})
    assert len(project['samples'][0]['sources']) == 2


def test_existing_project_can_explicitly_select_preserved_orphan_without_changing_raw(tmp_path):
    storage = Storage(tmp_path)
    image = _png('preserved original', 90)
    project = storage.files.create([('A.txt', RAW), ('원본사진.png', image), ('B.txt', RAW+b'40 1\n'),
                                    ('B_상매칭.png', _png('B',110))], {})
    snapshot = deepcopy(project)
    sample = project['samples'][0]
    doc = new_document(project)
    entry = doc['samples'][0]
    assert phase_image_record(project, sample, entry) is None
    assert any(i['code']=='no_phase_image' for i in build_checklist(project,doc)['samples'][0]['items'])
    choices = phase_image_choices(project,sample)
    assert [r['filename'] for r in choices] == ['원본사진.png']
    entry['phase_image_source_id'] = choices[0]['id']
    saved = storage.reports.save(project['storage_id'], project, doc, 0)
    assert phase_image_record(project,sample,saved['samples'][0])['filename'] == '원본사진.png'
    assert not any(i['code']=='no_phase_image' for i in build_checklist(project,saved)['samples'][0]['items'])
    old_client = deepcopy(saved)
    old_client['samples'][0].pop('phase_image_source_id')
    saved = storage.reports.save(project['storage_id'], project, old_client, 1)
    assert saved['samples'][0]['phase_image_source_id'] == choices[0]['id']
    assert storage.files.load_source(project['storage_id']) == snapshot == project
    assert storage.files.read_source(project['storage_id'], choices[0]['id']) == image
    # An explicit reset is different from an old client's absent field.
    saved['samples'][0]['phase_image_source_id'] = ''
    assert storage.reports.save(project['storage_id'],project,saved,2)['samples'][0]['phase_image_source_id'] == ''
    for wrong in (project['samples'][1]['sources'][1]['id'], 'unknown', project['sources'][0]['id']):
        doc['samples'][0]['phase_image_source_id'] = wrong
        with pytest.raises(ValueError, match='선택한 상매칭 이미지'):
            validate_document(project,doc)


def test_chunk_resume_retains_saved_unicode_mapping_and_preview_selection(tmp_path):
    name = unicodedata.normalize('NFD', '원본사진.png')
    files = {'A.txt':RAW, name:_png('chunk evidence',90)}
    with TestClient(make_app(tmp_path)) as client:
        upload_id = client.post(api.BASE+'/api/upload-sessions').json()['upload_id']
        url = api.BASE+'/api/upload-sessions/'+upload_id
        mappings = {name:dict(role='phase_image',sample='A'), 'A.txt':dict(role='measurement',sample='A',axis_kind='two_theta')}
        assert client.put(url+'/mappings',json=mappings).status_code == 200
        for filename,data in files.items():
            result = client.post(url+'/chunks',data=dict(relative_path=filename,offset=0,total_size=len(data),chunk_index=0,chunk_count=1),files={'file':(filename,data)})
            assert result.status_code == 200, result.text
        assert client.get(url).json()['mappings'] == mappings
        assert client.post(url+'/preflight').json()['files'][0]['axis'] == '2θ (deg)'
        response = client.post(url+'/complete',json={})
        assert response.status_code == 202, response.text
        done = poll(client,response.json())
        report_url = api.BASE+'/api/reports/'+done['storage_id']
        value = client.get(report_url).json()
        sample = value['project']['samples'][0]
        record = next(r for r in sample['sources'] if r['role']=='phase_image')
        assert record['sha256'] == hashlib.sha256(files[name]).hexdigest()
        preview = client.post(report_url+'/draft-preview',json={'document':value['document']})
        assert preview.status_code == 200, preview.text

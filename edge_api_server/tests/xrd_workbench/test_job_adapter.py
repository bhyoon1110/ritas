import io
import json
import zipfile

import pytest

from app.config import Settings
from app.errors import ApiException
from app.specialized_reports import generate_specialized_report
from .synthetic import sample_files


@pytest.mark.parametrize('zipped', [False, True])
@pytest.mark.parametrize('include_raw', [False, True])
def test_new_csharp_inputs_build_report_without_changing_sources(tmp_path, zipped, include_raw):
    source = tmp_path / 'job' / 'input'
    source.mkdir(parents=True)
    files = sample_files('SampleA')
    if zipped:
        with zipfile.ZipFile(source / 'bundle.zip', 'w') as archive:
            for name, data in files.items():
                archive.writestr('dataset/'+name, data)
    else:
        for name, data in files.items():
            (source / name).write_bytes(data)
    before = {p.name:p.read_bytes() for p in source.iterdir()}
    job = dict(job_id='job1', root_relative_path='job', request_number='R-123',
               experiment_code='XRD', equipment_code='XRD-1', operator_id='O-1',
               report_options_json=json.dumps({'reportFormats':['PPTX'], 'includeRawFiles':include_raw}))
    result = generate_specialized_report(Settings(storage_root=tmp_path), job)
    assert result.primary_path.name == '보고서.html'
    assert {p.name:p.read_bytes() for p in source.iterdir()} == before
    with zipfile.ZipFile(result.package_path) as archive:
        assert {name.split('/')[0] for name in archive.namelist()} == {
            'Measured Data', 'Report', '보고서.pdf', '보고서.pptx', '보고서.html', '뷰어.html',
        }
        assert any(name.endswith('.txt') and name.startswith('Measured Data/') for name in archive.namelist()) == include_raw
        html = archive.read('보고서.html').decode()
        assert 'R-123' in html and 'XRD-1' in html and 'O-1' in html
        assert 'contenteditable' not in html
        if not include_raw:
            assert 'class="item sourceLink"' not in html


def test_new_zip_rejects_traversal(tmp_path):
    root = tmp_path/'job'/'input'
    root.mkdir(parents=True)
    with zipfile.ZipFile(root/'bad.zip', 'w') as archive:
        archive.writestr('../S_상매칭_Report.pdf', b'%PDF-bad')
    job = dict(root_relative_path='job', experiment_code='XRD', equipment_code='XRD-1',
               report_options_json='{"reportFormats":["HTML"]}')
    with pytest.raises(ApiException, match='경로'):
        generate_specialized_report(Settings(storage_root=tmp_path), job)

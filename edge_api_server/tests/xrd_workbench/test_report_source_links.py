"""Follow actual clicked PPT/PDF destinations, not just unused relationship records."""
from dataclasses import replace
import hashlib
import io
import time
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile

from fastapi.testclient import TestClient
from lxml import etree as E
from pypdf import PdfReader
import pytest

from app.xrd_workbench import integration as api
from app.xrd_workbench.render.posco_pptx import NS
from .synthetic import sample_files
from .test_integration import make_app, poll, upload


def ppt_links(data):
    result = []
    with ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            if not name.startswith('ppt/slides/slide') or not name.endswith('.xml'):
                continue
            filename = name.rsplit('/', 1)[-1]
            rels = {r.get('Id'): r for r in E.fromstring(archive.read(f'ppt/slides/_rels/{filename}.rels'))}
            slide = E.fromstring(archive.read(name))
            for node in slide.findall('.//a:hlinkClick', NS):
                rel = rels[node.get('{'+NS['r']+'}id')]
                assert rel.get('Type').endswith('/hyperlink')
                assert rel.get('TargetMode') == 'External'
                assert rel.get('Target')
                result.append(rel.get('Target'))
    return result


def pdf_links(data):
    return [str(a.get_object()['/A']['/URI'])
            for page in PdfReader(io.BytesIO(data)).pages for a in page.get('/Annots', [])
            if a.get_object().get('/A', {}).get('/URI')]


@pytest.fixture
def report(tmp_path):
    app = make_app(tmp_path)
    app.state.settings = replace(app.state.settings, edge_public_base_url='http://testserver')
    client = TestClient(app)
    # Windows filenames with Korean, spaces and URL-sensitive characters must
    # round-trip exactly. Both normal and WPPF result layouts are exercised.
    files = {}
    for old, new in [('SampleA', '시료 A #1 (50%)'), ('SampleB', '시료 B + 2')]:
        for name, data in sample_files(old).items():
            if old == 'SampleB' and ('정량' in name):
                continue
            files[name.replace(old, new)] = data
    sid = upload(client, files)
    project = client.get(api.BASE+f'/api/projects/{sid}').json()
    doc = client.get(api.BASE+f'/api/reports/{sid}').json()['document']
    saved = client.put(api.BASE+f'/api/reports/{sid}', json={'document':doc,'expected_revision':0})
    assert saved.status_code == 200, saved.text
    yield client, sid, project, files
    client.close()


@pytest.mark.parametrize('kind', ['pdf'])
def test_standalone_report_links_keep_workbench_prefix_and_open_exact_originals(report, kind):
    client, sid, project, files = report
    response = client.post(api.BASE+f'/api/reports/{sid}/exports',json={'kind':kind,'revision':1})
    assert response.status_code == 202, response.text
    job = poll(client, response.json())
    result = client.get(api.BASE+f'/api/jobs/{job["job_id"]}/file')
    assert result.status_code == 200, result.text[:100] if result.status_code != 200 else ''
    links = (ppt_links if kind == 'pptx' else pdf_links)(result.content)
    assert links
    records = {r['id']:r for r in project['sources']}
    opened = set()
    for target in set(links):
        assert target.startswith('http://testserver'+api.BASE+'/'), target
        response = client.get(target)
        assert response.status_code == 200, (target,response.status_code)
        if '/api/files/' not in target:
            continue
        record = records[target.rsplit('/',1)[-1]]
        opened.add(record['id'])
        assert response.content == files[record['filename']]
        assert hashlib.sha256(response.content).hexdigest() == record['sha256']
        assert response.headers['content-type'].startswith(
            'text/plain' if record['role'] == 'measurement' else 'application/pdf')
    expected = {r['id'] for r in records.values() if r['role'] in ('measurement','phase_report','wppf_report')}
    assert opened == expected


def test_zip_ppt_click_targets_resolve_to_nonempty_exact_files_after_extraction(report, tmp_path):
    client, sid, project, files = report
    created = client.post(api.BASE+f'/api/reports/{sid}/exports',json={'kind':'zip','revision':1})
    assert created.status_code == 202, created.text
    job = poll(client, created.json())
    response = client.get(api.BASE+f'/api/jobs/{job["job_id"]}/file')
    assert response.status_code == 200, response.text[:100] if response.status_code != 200 else ''
    with ZipFile(io.BytesIO(response.content)) as archive:
        from .test_offline_pptx import embedded_files
        from app.xrd_workbench.render.link_audit import ppt_click_targets
        assert not ppt_click_targets(archive.read('보고서.pptx'))
        embedded = embedded_files(archive.read('보고서.pptx'))
        targets = set(pdf_links(archive.read('보고서.pdf')))
        # Only generated paths from our fixture; never extract an untrusted user archive here.
        archive.extractall(tmp_path / 'delivered')
        opened = set()
        for target in targets:
            url = urlsplit(target)
            assert not url.scheme and not url.netloc
            filename = unquote(url.path)
            assert filename in archive.namelist(), target
            content = (tmp_path / 'delivered' / filename).read_bytes()
            assert content
            if filename.startswith(('Report/','Measured Data/')):
                record = next(r for r in project['sources'] if filename.endswith('/'+r['filename']))
                opened.add(record['id'])
                assert content == files[record['filename']]
                assert embedded[record['filename']] == content
                if record['role'] != 'measurement':
                    reader = PdfReader(io.BytesIO(content))
                    assert reader.pages and any(p.extract_text().strip() for p in reader.pages)
        assert opened == {r['id'] for r in project['sources']
                          if r['role'] in ('measurement','phase_report','wppf_report')}


def test_standalone_downloaded_ppt_is_self_contained_without_server_urls(report):
    from .test_offline_pptx import embedded_files
    from app.xrd_workbench.render.link_audit import ppt_click_targets
    client, sid, project, files = report
    created = client.post(api.BASE+f'/api/reports/{sid}/exports',json={'kind':'pptx','revision':1})
    assert created.status_code == 202, created.text
    job = poll(client, created.json())
    result = client.get(api.BASE+f'/api/jobs/{job["job_id"]}/file')
    assert result.status_code == 200
    assert not ppt_click_targets(result.content)
    embedded = embedded_files(result.content)
    for record in project['sources']:
        if record['role'] in ('measurement','phase_report','wppf_report'):
            assert embedded[record['filename']] == files[record['filename']]


@pytest.mark.parametrize('kind', ['pptx', 'zip'])
@pytest.mark.parametrize('fault', ['missing', 'empty'])
def test_missing_link_source_is_reported_and_never_offered_for_download_or_transfer(report, monkeypatch, kind, fault):
    from app.xrd_workbench.storage import ProjectFiles
    client, sid, project, _ = report
    record = next(r for r in project['sources'] if r['role'] == 'phase_report')
    original = ProjectFiles.read_source
    def broken_source(store, storage_id, file_id):
        if file_id == record['id']:
            if fault == 'empty': return b''
            raise FileNotFoundError('removed source')
        return original(store, storage_id, file_id)
    monkeypatch.setattr(ProjectFiles, 'read_source', broken_source)
    registered = []
    monkeypatch.setattr(api, 'register_generated_report_package', lambda **kwargs: registered.append(kwargs))
    response = client.post(api.BASE+f'/api/reports/{sid}/exports', json={'kind':kind,'revision':1})
    assert response.status_code == 202, response.text
    job = response.json()
    deadline = time.monotonic() + 20
    while job['status'] in ('queued', 'running') and time.monotonic() < deadline:
        time.sleep(.02)
        job = client.get(api.BASE+'/api/jobs/'+job['job_id']).json()
    assert job['status'] == 'failed', job
    assert record['filename'] in job['error']
    assert client.get(api.BASE+'/api/jobs/'+job['job_id']+'/file').status_code == 409
    assert not registered

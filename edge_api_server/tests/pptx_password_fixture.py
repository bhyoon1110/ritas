"""Loopback-only browser fixture with real reports; never calls LIMS or an LLM."""
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import sys

root = Path(sys.argv[1])
os.environ.update(RIST_STORAGE_ROOT=str(root/'storage'), RIST_AUTH_ENABLED='false',
                  RIST_ERROR_ARCHIVE_ROOT=str(root/'errors'), RIST_USAGE_LOG_ROOT=str(root/'usage'))
from app.path_bootstrap import add_project_package_paths
add_project_package_paths()
from app import ahn_web, tem_report_editor, preview_report
from app.ftir_web import router as ftir
from app.raman_web import router as raman
from app.xrd_workbench.integration import router as xrd, BASE
from app.xrd_workbench.storage import Storage
from app.storage import atomic_write_json
from ahn.ppt_report import build_pptx
from ahn.tests.test_ppt_report import _project_with_sections
from tests.test_edited_report_analysis import edited_payload
from tests.test_ftir_web import TINY_PNG_DATA_URL
from tests.xrd_workbench.synthetic import sample_files
import uvicorn

app = ahn_web.create_tem_preview_app()
app.include_router(ftir); app.include_router(raman); app.include_router(xrd)
app.state.database = None
urls = {}
for kind, code in [('ftir','FT-IR'), ('raman','RAMAN')]:
    tmp, package = preview_report.build_preview_report_package(experiment_code=code,
        analysis_payload=edited_payload(), raw_series=[preview_report.RawSeries('sample.dpt',[1500,1700,1900],[1,3,1])],
        figure_image=base64.b64decode(TINY_PNG_DATA_URL.split(',')[1]))
    store = preview_report.preview_report_job_store(app)
    job = store.create(filename=kind+'-report-package.zip', owner_user_id='test-author')
    store.complete(job.job_id, tmp_root=tmp, package_path=package)
    urls[kind] = '/api/v1/'+kind+'/report/jobs/'+job.job_id+'/edit'

workspace = ahn_web.tem_history.new_workspace(app.state.settings)
inputs = workspace/'input'; inputs.mkdir(parents=True)
job = ahn_web._create_ahn_job(inputs, workspace, None, app.state.settings, owner_user_id='test-author')
job.output_dir.mkdir()
project = _project_with_sections(inputs, {'tem'})
build_pptx(project, job.pptx_path)
atomic_write_json(job.analysis_path, project)
manifest = {'summary':{'temSampleCount':1}, 'pptx':'tem-report.pptx'}
atomic_write_json(job.manifest_path, manifest)
tem_report_editor.prepare_review(job, manifest)
if job.review_error: raise RuntimeError(job.review_error)
ahn_web._build_package(job.output_dir, job.package_path)
ahn_web._set_job_state(job, status='completed', manifest=manifest)
urls['tem'] = '/api/v1/tem/report/jobs/'+job.job_id+'/edit'

home = app.state.settings.storage_root/'xrd-workbench-v1/users'/hashlib.sha256(b'development').hexdigest()
project = Storage(home).files.create(list(sample_files('SampleA').items()), {})
urls['xrd'] = BASE+'/report.html?project='+project['storage_id']

@app.get('/api/v1/auth/me')
def me(): return {'userId':'test-author','projects':['FTIR','RAMAN','TEM','XRD'],'roles':[]}

sock = socket.socket(); sock.bind(('127.0.0.1',0))
print(json.dumps({'port':sock.getsockname()[1], 'urls':urls}), flush=True)
uvicorn.Server(uvicorn.Config(app, log_level='warning')).run(sockets=[sock])

"""Loopback dashboard fixture with real scoped aggregation, isolated SQL and files."""
import json
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

root=Path(sys.argv[1])
os.environ['RIST_STORAGE_ROOT']=str(root / 'storage')
from app.path_bootstrap import add_project_package_paths
add_project_package_paths()
from fastapi import FastAPI,Request
from fastapi.responses import HTMLResponse,JSONResponse
from app.browser_compat import install_browser_compat
from app.config import Settings
from app.errors import ApiException,api_exception_handler
from app.preview_web import build_workspace_index
from app.preview_report import PreviewReportJobStore
from app.workspace_dashboard import router
from test_workspace_dashboard import DashboardDB,context,spectrum,tem_record,xrd_project
import uvicorn

app=FastAPI()
app.state.settings=Settings(storage_root=root / 'storage',auth_enabled=True)
app.state.database=DashboardDB()
app.state.preview_report_job_store=PreviewReportJobStore()
app.state.xrd_workbench_enabled=True
app.add_exception_handler(ApiException,api_exception_handler)
w=SimpleNamespace(app=app,root=root)
for i in range(12):
    job=spectrum(w,kind='raman' if i%3==1 else 'ftir',state='running' if i==2 else 'failed' if i==3 else 'completed')
    job.transfer_metadata={'requestNumber':'C272-'+str(i)}
    if i==0:app.state.database.report(job.job_id,'PENDING',request='C272-0')
    if i==1:app.state.database.report(job.job_id,'FAILED',request='C272-1')
spectrum(w,owner='bob')
tem_record(w);xrd_project(w)
app.state.database.connection.execute("INSERT INTO app_users VALUES('PENDING')")
app.state.database.connection.execute("INSERT INTO voc_requests VALUES('FTIR','피크 라벨 표시 개선','RESOLVED','alice','2026-10-26')")

@app.middleware('http')
async def actor(request,call_next):
    mode=request.headers.get('x-dashboard-test-user','member')
    request.state.auth_context=context(projects=() if mode=='none' else ('FTIR',) if mode=='limited' else ('FTIR','RAMAN','TEM','XRD'),admin=mode=='admin')
    return await call_next(request)

install_browser_compat(app)
app.include_router(router)

@app.get('/')
def home():return HTMLResponse(build_workspace_index())

@app.post('/api/v1/auth/logout')
def logout():return {'ok':True}

sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
print(json.dumps({'port':sock.getsockname()[1]}),flush=True)
uvicorn.Server(uvicorn.Config(app,log_level='warning')).run(sockets=[sock])

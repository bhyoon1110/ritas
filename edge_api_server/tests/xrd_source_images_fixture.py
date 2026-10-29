"""Loopback-only old project with a preserved but unlinked phase image."""
import json
from pathlib import Path
import socket
import sys

from fastapi.testclient import TestClient
import uvicorn
from tests.xrd_workbench.test_integration import make_app, upload
from tests.xrd_workbench.synthetic import sample_files

root = Path(sys.argv[1])
app = make_app(root / 'storage')
files = sample_files('SampleA')
files['고객 원본사진.png'] = files.pop('SampleA_상매칭.png')
with TestClient(app) as client:
    sid = upload(client, files)
    value = client.get('/api/v1/xrd/workbench/api/reports/'+sid).json()
record = next(r for r in value['project']['sources'] if r['filename']=='고객 원본사진.png')

@app.get('/api/v1/auth/me')
def me():
    return {'userId':'fixture-author','projects':['XRD'],'roles':[]}

sock = socket.socket()
sock.bind(('127.0.0.1',0))
print(json.dumps(dict(port=sock.getsockname()[1],sid=sid,image_id=record['id'],sample_id=value['project']['samples'][0]['id'])),flush=True)
uvicorn.Server(uvicorn.Config(app,log_level='warning')).run(sockets=[sock])

"""Isolated loopback XRD report for browser tests; never uses production data."""
import json
from pathlib import Path
import socket
import sys

from fastapi.testclient import TestClient
from fastapi.responses import FileResponse
from PIL import Image
import uvicorn

from tests.xrd_workbench.test_integration import make_app, upload
from tests.xrd_workbench.synthetic import sample_files

root = Path(sys.argv[1])
app = make_app(root / 'storage')

@app.get('/assets/request_filters.js')
def request_filters():
    return FileResponse(Path(__file__).resolve().parents[1] / 'app' / 'request_filters.js', media_type='text/javascript')

with TestClient(app) as client:
    sid = upload(client, sample_files('SampleA'))
Image.new('RGB', (160, 80), '#117799').save(root / 'image.png')

@app.get('/api/v1/auth/me')
def me():
    return {'userId': 'fixture-author', 'projects': ['XRD'], 'roles': []}

sock = socket.socket()
sock.bind(('127.0.0.1', 0))
print(json.dumps({'port': sock.getsockname()[1], 'sid': sid}), flush=True)
uvicorn.Server(uvicorn.Config(app, log_level='warning')).run(sockets=[sock])

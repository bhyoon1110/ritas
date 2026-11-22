"""CSS-only real UI pages. No database, uploads or outbound requests."""
import importlib.util
import json
from pathlib import Path
import re
import socket
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
import uvicorn
from app.path_bootstrap import add_project_package_paths
add_project_package_paths()
from app.browser_compat import ASSET_ROOT, compatible_html
from app.browser_support import with_browser_support_notice
from app.xrd_workbench.integration import BASE, WEB, web_asset
edge = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ui_collect', edge / 'browser_compat/collect.py')
collect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collect)
pages = collect.pages()
for name in ('operations', 'errors'):
    pages[name] = with_browser_support_notice(pages[name])
for name in ('index', 'analysis', 'report'):
    request = Request({'type': 'http', 'headers': [], 'method': 'GET', 'path': '/'})
    pages['xrd-' + name] = web_asset(name + '.html', request).body.decode().replace('<body ', '<body class="withShell" ', 1)
app = FastAPI()

@app.get('/assets/browser-compat/{name}')
def compat_asset(name: str):
    path = (ASSET_ROOT / name).resolve()
    assert path.parent == ASSET_ROOT.resolve() and path.suffix == '.css'
    return FileResponse(path)

@app.get(BASE + '/{name:path}')
def xrd_asset(name: str):
    if name in ('ppt-tools/report_ppt_tools.css', 'ppt-tools/report_ppt_tools.legacy.css'):
        return FileResponse(edge / 'app/report_ppt_tools.css')
    path = (WEB / name).resolve()
    assert path.is_relative_to(WEB.resolve()) and path.suffix == '.css'
    return FileResponse(path)

@app.get('/{name}')
@app.get('/')
def page(request: Request, name: str = 'index'):
    if name not in pages:
        raise HTTPException(404)
    html = pages[name]
    if request.query_params.get('legacy') == '1':
        html = compatible_html(html) if not name.startswith('xrd-') else re.sub(r'href="([^"]+)\.css"', r'href="\1.legacy.css"', html)
    # Layout only. Independent browser suites exercise real application JS.
    html = re.sub(r'<script\b[^>]*>.*?</script>', '', html, flags=re.I | re.S)
    return HTMLResponse(html)

sock = socket.socket()
sock.bind(('127.0.0.1', 0))
print(json.dumps({'port': sock.getsockname()[1], 'pages': list(pages)}), flush=True)
uvicorn.Server(uvicorn.Config(app, log_level='warning')).run(sockets=[sock])

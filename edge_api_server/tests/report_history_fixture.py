"""Read-only loopback fixture for history presentation; API data is mocked by browser tests."""
import json
from pathlib import Path
import socket
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse
from app.browser_compat import install_browser_compat
from app.browser_support import with_browser_support_notice
import uvicorn
app = FastAPI()
root = Path(__file__).resolve().parents[1] / "app"
install_browser_compat(app)
@app.get("/my/reports")
def page():
    return HTMLResponse(with_browser_support_notice((root / "report_history.html").read_text()), headers={"Cache-Control":"private, no-store"})
@app.get("/assets/report_history.js")
def history_script():
    return FileResponse(root / "report_history.js", media_type="application/javascript")
@app.get("/assets/report_delivery_progress.js")
def progress_script():
    return FileResponse(root / "report_delivery_progress.js", media_type="application/javascript")
sock=socket.socket();sock.bind(("127.0.0.1",0));sock.listen(128)
print(json.dumps({"port":sock.getsockname()[1]}),flush=True)
uvicorn.Server(uvicorn.Config(app,log_level="warning")).run(sockets=[sock])

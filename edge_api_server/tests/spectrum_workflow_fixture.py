"""Loopback-only, isolated E2E app. Real RAW, report render, and revision code; no LIMS/LLM."""
import json
import os
from pathlib import Path
import socket
import sys

root = Path(sys.argv[1])
os.environ["RIST_STORAGE_ROOT"] = str(root / "jobs")
os.environ["RIST_RAMAN_ASSIGNMENT_LIBRARY_DIR"] = str(root / "raman-libraries")
os.environ["RIST_FTIR_ASSIGNMENT_LIBRARY_DIR"] = str(root / "ftir-libraries")

from app.path_bootstrap import add_project_package_paths
add_project_package_paths()
from app.ftir_web import create_ftir_preview_app
from app.raman_web import router as raman_router
from fastapi import Request
import uvicorn

app = create_ftir_preview_app()
app.include_router(raman_router)
app.state.settings = None  # No database, authentication, LLM or external delivery in this fixture.


@app.get("/api/v1/auth/me")
def me():
    return {"userId": "workflow-test", "roles": [], "projects": ["FTIR", "RAMAN"]}


@app.get("/api/v1/requests")
def requests():
    return {"items": []}


@app.get("/__test__/request-editor/{kind}")
def request_editor(kind: str, request: Request):
    """UI-only fixture; browser tests mock job/lookup responses, never real LIMS."""
    from fastapi.responses import HTMLResponse
    from app.report_editor import editor_page
    from app.browser_compat import browser_profile, compatible_html
    assert kind in {"ftir", "raman", "tem"}
    prefix = f"/api/v1/{kind}/report/jobs" if kind != "tem" else "/api/v1/tem/jobs"
    html = editor_page(prefix, "lookup-test", project="FT-IR" if kind == "ftir" else kind.upper())
    if browser_profile(request.headers.get("user-agent", "")) == "chrome49":
        html = compatible_html(html)
    return HTMLResponse(html)


@app.post("/__test__/expire-preview/{job_id}")
def expire_preview(job_id: str):
    # Isolated loopback fixture only: model the in-memory job loss on restart.
    from app.preview_report import preview_report_job_store
    preview_report_job_store(app).remove(job_id)
    return {"expired": job_id}


sock = socket.socket()
sock.bind(("127.0.0.1", 0))
print(json.dumps({"port": sock.getsockname()[1]}), flush=True)
uvicorn.Server(uvicorn.Config(app, log_level="warning")).run(sockets=[sock])

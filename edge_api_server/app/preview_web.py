from __future__ import annotations

import base64
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .ahn_web import router as ahn_router
from .browser_support import with_browser_support_notice
from .config import Settings
from .error_archive import install_error_management
from .ftir_web import (
    DEFAULT_ASSIGNMENT_LIBRARY_DIR,
    router as ftir_router,
)
from .raman_web import router as raman_router
from .xrd_web import router as xrd_router
from .workspace_dashboard import router as dashboard_router
from ahn.report_branding import ASSET_DIR, LOGO_NAME


def _bool_env(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in {"1", "true", "yes", "on"}


def build_workspace_index() -> str:
    directory = Path(__file__).parent
    page = (directory / "workspace_dashboard.html").read_text(encoding="utf-8")
    # Reuse the report's original logo without an external request in isolated labs.
    logo = base64.b64encode((ASSET_DIR / LOGO_NAME).read_bytes()).decode("ascii")
    page = page.replace("__RIST_LOGO__", "data:image/png;base64," + logo)
    for key, suffix in (("CSS", "css"), ("JS", "js")):
        page = page.replace("__DASHBOARD_" + key + "__",
                            (directory / ("workspace_dashboard." + suffix)).read_text(encoding="utf-8"))
    paths = {
        "FTIR": '<path d="M3 23h26M5 4v19m1-6 4-1 3 3 4-13 3 14 4-8 3 4"/>',
        "RAMAN": '<path d="M3 25h26M5 4v21m1-3h5l3-9 3 9 4-17 3 17h4"/>',
        "XRD": '<path d="m16 3 11 7v13l-11 7-11-7V10Zm0 0v13m11-6-11 6-11-6m11 6v14"/>',
        "TEM": '<circle cx="16" cy="16" r="12"/><circle cx="12" cy="12" r="3"/><circle cx="21" cy="17" r="4"/><path d="m8 23 4-4m6 9 1-4"/>',
    }
    for key, shape in paths.items():
        page = page.replace("__ICON_" + key + "__",
                            '<svg viewBox="0 0 32 32" fill="none" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">' + shape + '</svg>')
    return with_browser_support_notice(page)

def create_preview_app() -> FastAPI:
    """Create a DB-free combined preview app for project web screens."""
    app = FastAPI(title="RIST 분석 포털")
    app.state.ftir_assignment_library_dir = Path(
        os.getenv(
            "RIST_FTIR_ASSIGNMENT_LIBRARY_DIR",
            str(DEFAULT_ASSIGNMENT_LIBRARY_DIR),
        )
    )
    app.state.ftir_assignment_library_delete_enabled = _bool_env(
        "RIST_FTIR_ASSIGNMENT_LIBRARY_DELETE_ENABLED"
    )
    settings = Settings.from_env()
    app.state.settings = settings
    install_error_management(app, settings)
    app.include_router(ftir_router)
    app.include_router(raman_router)
    app.include_router(xrd_router)
    app.include_router(ahn_router)
    app.include_router(dashboard_router)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> HTMLResponse:
        return HTMLResponse(build_workspace_index())

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "preview"}

    return app

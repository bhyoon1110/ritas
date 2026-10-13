from __future__ import annotations

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


def _bool_env(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in {"1", "true", "yes", "on"}


def build_workspace_index() -> str:
    page = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RIST Preview</title>
  <style>
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      font-family: Arial, "Noto Sans KR", sans-serif;
      color: #172a46;
      background: #f8fafc;
    }
    main {
      width: min(720px, calc(100vw - 32px));
      box-sizing: border-box;
      background: #fff;
      border: 1px solid #cbd5e1;
      border-radius: 10px;
      padding: 28px;
    }
    h1 { margin: 0 0 8px; font-size: 30px; }
    .workspace-header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; margin-bottom: 8px; }
    .workspace-header h1 { margin: 0; }
    .workspace-actions { display: flex; align-items: center; justify-content: flex-end; flex-wrap: wrap; gap: 8px; }
    .workspace-actions a, .workspace-actions button { border: 1px solid #9fb6d6; border-radius: 6px; padding: 8px 12px; color: #172a46; background: #fff; font: inherit; font-size: 14px; font-weight: 700; text-decoration: none; cursor: pointer; }
    .workspace-actions button:disabled { opacity: .55; cursor: wait; }
    .workspace-actions[hidden], #workspace-admin-link[hidden] { display: none; }
    p { margin: 0 0 22px; color: #64748b; }
    .workspace-projects { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
    a {
      display: block;
      border: 1px solid #9fb6d6;
      border-radius: 8px;
      padding: 18px 14px;
      text-align: center;
      color: #172a46;
      font-size: 18px;
      font-weight: 700;
      text-decoration: none;
      background: #fff;
    }
    a:hover { border-color: #2563eb; background: #f1f7ff; }
    @media (max-width: 640px) { .workspace-projects { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <main>
    <header class="workspace-header">
      <h1>RIST Preview</h1>
      <nav class="workspace-actions" id="workspace-account-actions" aria-label="회원 메뉴" hidden>
        <a href="/account">회원정보 관리</a>
        <button type="button" id="workspace-logout">로그아웃</button>
      </nav>
    </header>
    <p>FT-IR, Raman, XRD, TEM 화면을 같은 포트에서 확인합니다.</p>
    <nav class="workspace-projects" aria-label="실험 메뉴">
      <a href="/ftir">FT-IR</a>
      <a href="/raman">Raman</a>
      <a href="/xrd">XRD</a>
      <a href="/tem">TEM</a>
      <a id="workspace-admin-link" href="/operations" hidden>운영 관리</a>
      <a href="/voc">VOC 게시판</a>
    </nav>
  </main>
  <script>
  (function() {
    var actions = document.getElementById("workspace-account-actions");
    var adminLink = document.getElementById("workspace-admin-link");
    var logoutButton = document.getElementById("workspace-logout");
    fetch("/api/v1/auth/me", {
      credentials: "same-origin",
      headers: {"Accept": "application/json"}
    }).then(function(response) {
      if (!response.ok) throw new Error("auth lookup failed");
      return response.json();
    }).then(function(payload) {
      var roles = payload && Array.isArray(payload.roles) ? payload.roles : [];
      actions.hidden = false;
      adminLink.hidden = roles.indexOf("ADMIN") === -1;
    }).catch(function() {
      actions.hidden = true;
      adminLink.hidden = true;
    });
    logoutButton.addEventListener("click", function() {
      if (logoutButton.disabled) return;
      logoutButton.disabled = true;
      fetch("/api/v1/auth/logout", {
        method: "POST",
        credentials: "same-origin",
        headers: {"X-Requested-With": "RIST-Project"}
      }).then(function(response) {
        if (!response.ok) throw new Error("logout failed");
        window.location.href = "/login";
      }).catch(function() {
        logoutButton.disabled = false;
        window.alert("로그아웃하지 못했습니다. 다시 시도하세요.");
      });
    });
  })();
  </script>
</body>
</html>"""
    return with_browser_support_notice(page)


def create_preview_app() -> FastAPI:
    """Create a DB-free combined preview app for project web screens."""
    app = FastAPI(title="RIST Combined Preview")
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

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> HTMLResponse:
        return HTMLResponse(build_workspace_index())

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "preview"}

    return app

"""Serve precompiled browser-specific UI assets, without a runtime compiler/CDN.

This is presentation negotiation, never an authentication/security decision.
API, upload/download streams and exported reports are not intercepted.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import logging
from pathlib import Path
import re

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from starlette.datastructures import Headers, MutableHeaders

ASSET_ROOT = Path(__file__).parent / "static" / "chrome49"
ASSET_PREFIX = "/assets/browser-compat/"
UI_PATHS = frozenset({"/", "/login", "/signup", "/account", "/admin/users", "/ftir", "/raman", "/xrd", "/tem", "/voc", "/operations", "/errors", "/report-management"})
SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.I | re.S)
STYLE_RE = re.compile(r"(<style\b[^>]*>)(.*?)(</style>)", re.I | re.S)
logger = logging.getLogger(__name__)


def browser_profile(user_agent: str, client_hints: str = "") -> str:
    chromium = re.search(r"(?:Chrome|Chromium)/(\d+)", user_agent)
    edge = re.search(r"Edge/(\d+)", user_agent)
    if edge or (chromium and 49 <= int(chromium[1]) < 110):
        return "chrome49"
    # Default Supermium UAs/brands can be identical to Chrome. Only use an
    # explicit brand; both modern profiles intentionally serve native assets.
    if re.search(r"\bSupermium/\d+", user_agent, re.I) or re.search(r'"Supermium"\s*;\s*v="\d+', client_hints, re.I):
        return "supermium"
    return "modern"


@lru_cache(maxsize=1)
def manifest() -> dict:
    return json.loads((ASSET_ROOT / "manifest.json").read_text(encoding="utf-8"))


def _digest(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def compatible_html(html: str) -> str:
    assets = manifest()

    def script(match: re.Match) -> str:
        attrs, source = match[1], match[2].strip()
        if re.search(r"\bsrc\s*=", attrs, re.I):
            if assets.get("plotly") and re.search(r'/assets/plotly\.min\.js["\']', attrs):
                return '<script src="' + ASSET_PREFIX + assets["plotly"] + '"></script>'
            return match[0]
        if not source or "application/json" in attrs.lower():
            return match[0]
        if source.startswith("window.RIST_VOC_CONFIG="):
            # Validate the data-only exception rather than accepting arbitrary JS.
            json.loads(source[len("window.RIST_VOC_CONFIG="):].removesuffix(";"))
            return match[0]
        filename = assets["scripts"].get(_digest(source))
        if not filename:
            raise ValueError("Browser compatibility assets are stale; rebuild browser_compat.")
        return '<script src="' + ASSET_PREFIX + filename + '"></script>'

    html = SCRIPT_RE.sub(script, html)
    html = STYLE_RE.sub(lambda m: m[1] + assets["styles"].get(_digest(m[2]), m[2]) + m[3], html)
    bootstrap = ('<script src="' + ASSET_PREFIX + assets["runtime"] + '"></script>'
                 '<link rel="stylesheet" href="' + ASSET_PREFIX + assets["css"] + '">')
    return re.sub(r"(<head\b[^>]*>)", lambda m: m[0] + bootstrap, html, count=1, flags=re.I)


class BrowserCompatibilityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not (scope["path"] in UI_PATHS or scope["path"].startswith(("/reports/", "/error-feedback/"))):
            return await self.app(scope, receive, send)
        request_headers = Headers(scope=scope)
        profile = browser_profile(request_headers.get("user-agent", ""), request_headers.get("sec-ch-ua", ""))
        start = None
        body = bytearray()

        async def negotiated_send(message):
            nonlocal start
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.add_vary_header("User-Agent")
                headers.add_vary_header("Sec-CH-UA")
                headers["X-RIST-Browser-Profile"] = profile
                if profile == "chrome49" and message["status"] == 200 and "text/html" in headers.get("content-type", ""):
                    start = message
                    return
            elif message["type"] == "http.response.body" and start is not None:
                body.extend(message.get("body", b""))
                if message.get("more_body", False):
                    return
                try:
                    result = compatible_html(body.decode("utf-8")).encode("utf-8")
                except (OSError, ValueError, KeyError):
                    logger.exception("Chrome 49 compatibility assets are missing or out of date")
                    start["status"] = 503
                    result = '<!doctype html><html><head><meta charset="utf-8"></head><body><h1>호환 자산 업데이트 필요</h1><p>관리자에게 Chrome 49 호환 파일을 포함한 최신 배포를 요청해 주세요. 새로고침만 반복하지 마세요.</p></body></html>'.encode()
                headers = MutableHeaders(scope=start)
                headers["content-length"] = str(len(result))
                headers["cache-control"] = "private, no-store"
                if "etag" in headers:
                    del headers["etag"]
                await send(start)
                await send({"type":"http.response.body", "body":result})
                return
            await send(message)

        await self.app(scope, receive, negotiated_send)


def install_browser_compat(app: FastAPI) -> None:
    if getattr(app.state, "browser_compat_installed", False):
        return
    app.state.browser_compat_installed = True
    app.add_middleware(BrowserCompatibilityMiddleware)

    @app.get(ASSET_PREFIX + "{filename}", include_in_schema=False)
    def browser_asset(filename: str):
        if not re.fullmatch(r"[a-f0-9]{64}\.(?:js|css)", filename) or not (ASSET_ROOT / filename).is_file():
            return HTMLResponse("Not found", status_code=404)
        return FileResponse(ASSET_ROOT / filename, media_type="text/css" if filename.endswith(".css") else "application/javascript",
                            headers={"Cache-Control":"public, max-age=31536000, immutable", "X-Content-Type-Options":"nosniff"})

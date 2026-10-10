from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import importlib.util
from pathlib import Path
import re

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.auth import AuthContext
from app.browser_compat import ASSET_ROOT, ASSET_PREFIX, browser_profile, compatible_html, install_browser_compat, manifest
from app.error_archive import _error_feedback_page
from app.ftir_web import plotly_asset_path
from app.report_delivery import _generated_report_page
from app.preview_web import create_preview_app
from app.voc_web import build_voc_page

CHROME49 = "Mozilla/5.0 (Windows NT 5.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/49.0.2623.75 Safari/537.36"
MODERN = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/150.0.0.0 Safari/537.36"


@pytest.mark.parametrize("ua,expected", [
    (CHROME49,"chrome49"), ("Chrome/109.0 Edg/109.0", "chrome49"),
    ("Chrome/42.0 Edge/12.0", "chrome49"), (MODERN,"modern"),
    ("Chrome/150.0 Edg/150.0", "modern"), ("Chrome/48.0", "modern"),
    ("MSIE 8.0; Windows NT 5.1", "modern"), ("Version/18.0 Safari/605.1.15", "modern"),
])
def test_profile_negotiation(ua, expected):
    assert browser_profile(ua) == expected


@pytest.mark.parametrize("ua,hints,expected", [
    (MODERN + " Supermium/150.0", "", "supermium"),
    (MODERN, '"Chromium";v="150", "Supermium";v="150"', "supermium"),
    (MODERN, '"Google Chrome";v="150"', "modern"),
    (CHROME49, '"Supermium";v="150"', "chrome49"),
])
def test_supermium_uses_native_assets_without_guessing_masked_brand(ua, hints, expected):
    assert browser_profile(ua, hints) == expected


def test_supermium_native_html_and_cache_negotiation(preview):
    response = preview.get('/tem', headers={'User-Agent': MODERN, 'Sec-CH-UA': '"Supermium";v="150"'})
    assert response.status_code == 200
    assert response.headers['x-rist-browser-profile'] == 'supermium'
    assert 'Sec-CH-UA' in response.headers['vary']
    assert ASSET_PREFIX not in response.text


def test_every_live_ui_template_has_current_prebuilt_assets():
    path = Path(__file__).resolve().parents[1] / "browser_compat" / "collect.py"
    spec = importlib.util.spec_from_file_location("rist_compat_collector", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, html in module.pages().items():
        result = compatible_html(html)
        assert result.index(manifest()["runtime"]) < result.index("<body"), name
        assert result.count(manifest()["runtime"]) == 1, name
        assert 'async function' not in result, name
        assert 'https://cdn' not in result, name


def test_assets_are_content_addressed_and_complete():
    assets = manifest()
    names = [assets["runtime"], assets["css"], *assets["scripts"].values()]
    if assets.get("plotly"):
        names.append(assets["plotly"])
    for filename in names:
        assert hashlib.sha256((ASSET_ROOT / filename).read_bytes()).hexdigest() == filename.split(".")[0]


def test_third_party_and_css_sources_match_build_inputs():
    assets = manifest()
    source = Path(__file__).resolve().parents[1] / 'browser_compat' / 'fallback.css'
    assert hashlib.sha256(source.read_bytes()).hexdigest() == assets['css'].split('.')[0]
    assert hashlib.sha256(plotly_asset_path().read_bytes()).hexdigest() == assets['plotlySourceSha256']


def test_stale_application_script_is_not_silently_sent_to_old_browser():
    with pytest.raises(ValueError, match="stale"):
        compatible_html('<html><head></head><body><script>async function newUnbuiltCode(){}</script></body></html>')


def test_dynamic_voc_identity_is_not_baked_into_shared_asset():
    for name in ('alice', 'bob', '</script><img src=x onerror=alert(1)>'):
        context = AuthContext(user_id=name, login_id="member", email=None, display_name="회원", status="ACTIVE",
            session_id="test", session_expires_at=datetime.now()+timedelta(hours=1), sso_authenticated_at=None,
            projects=frozenset({'FTIR'}), roles=frozenset(), sso_identity=None)
        html = compatible_html(build_voc_page(context,'FTIR'))
        assert 'window.RIST_VOC_CONFIG=' in html
        assert 'img src=x' not in html or '\\u003cimg src=x' in html
        assert '"allowedProjects": ["FTIR"]' in html
        assert '"admin": false' in html


@pytest.fixture
def preview(monkeypatch, tmp_path):
    monkeypatch.setenv("RIST_STORAGE_ROOT", str(tmp_path / "runtime"))
    with TestClient(create_preview_app()) as client:
        yield client


@pytest.mark.parametrize("path", ["/", "/ftir", "/tem", "/raman", "/xrd", "/operations", "/errors", "/report-management"])
def test_negotiation_changes_only_legacy_html_and_serves_assets(preview, path):
    modern = preview.get(path, headers={"User-Agent": MODERN})
    legacy = preview.get(path, headers={"User-Agent": CHROME49})
    assert modern.status_code == legacy.status_code == 200
    assert modern.headers["x-rist-browser-profile"] == "modern"
    assert legacy.headers["x-rist-browser-profile"] == "chrome49"
    assert "User-Agent" in modern.headers["vary"]
    assert "User-Agent" in legacy.headers["vary"]
    assert ASSET_PREFIX not in modern.text
    assert ASSET_PREFIX in legacy.text
    assert legacy.headers["cache-control"] == "private, no-store"
    assert int(legacy.headers["content-length"]) == len(legacy.content)
    for asset in set(re.findall(r'/assets/browser-compat/[a-f0-9]+\.(?:js|css)', legacy.text)):
        response = preview.get(asset)
        assert response.status_code == 200
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "immutable" in response.headers["cache-control"]


def test_api_and_assets_are_not_rewritten(preview):
    assert preview.get('/health',headers={"User-Agent":CHROME49}).json() == {"status":"ok", "mode":"preview"}
    assert preview.get(ASSET_PREFIX+'manifest.json').status_code == 404
    assert preview.get(ASSET_PREFIX+'a'*64+'.js').status_code == 404
    asset = preview.get('/ftir/assets/plotly.min.js',headers={"User-Agent":CHROME49})
    assert asset.status_code == 200
    assert "x-rist-browser-profile" not in asset.headers


def test_stale_deployment_returns_readable_error(preview, monkeypatch):
    monkeypatch.setattr('app.browser_compat.manifest', lambda: {"scripts": {}, "styles": {}})
    response = preview.get('/ftir',headers={"User-Agent":CHROME49})
    assert response.status_code == 503
    assert '호환 자산 업데이트 필요' in response.text


@pytest.mark.parametrize("identifier", ['second-id', 'quote"<&한글'])
def test_dynamic_review_and_feedback_routes_use_shared_scripts(identifier):
    app = FastAPI()
    install_browser_compat(app)

    @app.get('/reports/{report_id}')
    def review(report_id: str):
        return _generated_report_page(identifier, 'TEM', {
            'canQueue': False, 'transferStatus': 'NOT_QUEUED', 'requestNumber': 'REQ',
            'experimentCode': 'TEM', 'equipmentCode': 'TEST', 'generatedAt': None,
        })

    @app.get('/error-feedback/{event_id}')
    def feedback(event_id: str):
        return _error_feedback_page(identifier, {'message': 'test', 'comments': []})

    with TestClient(app) as client:
        for path in ('/reports/test', '/error-feedback/test'):
            response = client.get(path, headers={'User-Agent': CHROME49})
            assert response.status_code == 200
            assert response.headers['x-rist-browser-profile'] == 'chrome49'
            assert ASSET_PREFIX + manifest()['runtime'] in response.text
            assert 'async' not in response.text
            assert 'quote"<&' not in response.text

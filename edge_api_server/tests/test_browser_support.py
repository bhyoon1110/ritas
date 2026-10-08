from __future__ import annotations

import json
import shutil
import subprocess

from fastapi.testclient import TestClient
import pytest

from app.auth import _admin_page, _login_page, _signup_page
from app.browser_support import (
    BROWSER_POLICY_TEXT,
    BROWSER_SUPPORT_SCRIPT,
    with_browser_support_notice,
)
from app.preview_web import build_workspace_index, create_preview_app


_RUN_DETECTOR = r"""
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const banner = {style: {display: 'none'}};
const reason = input.legacyText ? {innerText: ''} : {textContent: ''};
function FormData() {}
FormData.prototype.get = function () {};
const window = {
  fetch: function () { throw new Error('Detector must not make network requests'); },
  Promise: function () {}, FormData: FormData, FileReader: function () {},
  Blob: function () {}, URLSearchParams: function () {}, URL: {createObjectURL: function () {}}
};
for (const name of input.missing || []) {
  if (name === 'FormData.get') delete FormData.prototype.get;
  else if (name === 'URL.createObjectURL') delete window.URL.createObjectURL;
  else delete window[name];
}
const sandbox = {
  window: window,
  navigator: {userAgent: input.ua},
  document: {getElementById: function (id) {
    if (input.noNodes) return null;
    if (id === 'rist-browser-warning') return banner;
    if (id === 'rist-browser-warning-reason') return reason;
    throw new Error('Unexpected DOM access: ' + id);
  }}
};
vm.runInNewContext(input.script, sandbox, {timeout: 1000});
process.stdout.write(JSON.stringify({display: banner.style.display, reason: reason.textContent || reason.innerText || ''}));
"""


def _detect(ua: str, *, missing: list[str] | None = None, legacy_text: bool = False, no_nodes: bool = False) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("브라우저 안내 스크립트 단위 검증에 Node.js가 필요합니다.")
    result = subprocess.run(
        [node, "-e", _RUN_DETECTOR],
        input=json.dumps({"script": BROWSER_SUPPORT_SCRIPT, "ua": ua, "missing": missing or [], "legacyText": legacy_text, "noNodes": no_nodes}),
        text=True, capture_output=True, timeout=10, check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("ua", [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/150.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/150.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 15) Chrome/150.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 CriOS/150.0.0.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 Firefox/150.0",
    "",
])
def test_capable_browsers_are_not_blocked_or_given_a_false_warning(ua):
    # Feature/UA fixtures, not a claim that these actual browser versions were run.
    assert _detect(ua) == {"display": "none", "reason": ""}


@pytest.mark.parametrize("ua,reason", [
    ("Mozilla/4.0 (compatible; MSIE 8.0; Windows NT 5.1)", "Internet Explorer"),
    ("Mozilla/5.0 (Windows NT 6.1; Trident/7.0; rv:11.0)", "Internet Explorer"),
    ("Mozilla/5.0 (Windows NT 10.0) Chrome/42.0 Safari/537.36 Edge/12.0", "구형 Edge"),
    ("Mozilla/5.0 (Windows NT 5.1) Chrome/49.0 Safari/537.36", "Windows 버전"),
    ("Mozilla/5.0 (Windows NT 6.1) Chrome/109.0 Safari/537.36 Edg/109.0", "Windows 버전"),
    ("Mozilla/5.0 (Windows NT 6.3) Chrome/109.0 Safari/537.36", "Windows 버전"),
    ("Mozilla/5.0 (Windows NT 10.0) Chrome/109.0 Safari/537.36", "오래된 Chromium"),
])
def test_known_obsolete_environments_receive_explanation(ua, reason):
    result = _detect(ua)
    assert result["display"] == "block"
    assert reason in result["reason"]


@pytest.mark.parametrize("feature", ["fetch", "Promise", "FormData", "FormData.get", "FileReader", "Blob", "URLSearchParams", "URL", "URL.createObjectURL"])
def test_missing_required_feature_is_detected_even_with_modern_or_spoofed_ua(feature):
    result = _detect("Mozilla/5.0 Chrome/999.0.0.0", missing=[feature])
    assert result["display"] == "block"
    assert "필수 브라우저 기능이 없습니다" in result["reason"]


def test_detector_does_not_need_the_apis_it_warns_about_or_textcontent():
    result = _detect("MSIE 8.0; Windows NT 5.1", missing=["fetch", "Promise", "FormData", "FileReader", "Blob", "URLSearchParams", "URL"], legacy_text=True)
    assert result["display"] == "block"
    assert "Internet Explorer" in result["reason"]
    assert "fetch" in result["reason"]


def test_detector_is_harmless_if_notice_nodes_are_missing():
    assert _detect("MSIE 8.0", no_nodes=True) == {"display": "none", "reason": ""}


def test_notice_is_inserted_once_before_application_scripts_and_does_not_overlay_plot():
    original = '<html><body class="workspace"><main>Content</main><script>app();</script></body></html>'
    html = with_browser_support_notice(original)
    assert html.count('id="rist-browser-warning"') == 1
    assert html.index(BROWSER_SUPPORT_SCRIPT) < html.index("app();")
    assert with_browser_support_notice(html) == html
    assert "position:fixed" not in html
    assert "position:absolute" not in html
    assert "@media print" in html
    assert "<noscript>" in html
    assert '<main>Content</main><script>app();</script>' in html
    assert with_browser_support_notice('{"status":"ok"}') == '{"status":"ok"}'


def test_login_signup_keep_script_free_fallback_and_display_chrome_first_policy():
    for html in (_login_page("/"), _signup_page()):
        assert BROWSER_POLICY_TEXT in html
        assert '<script' not in html
        assert 'method="post"' in html
    assert BROWSER_POLICY_TEXT in build_workspace_index()
    assert 'id="rist-browser-warning"' in _admin_page()


@pytest.mark.parametrize("path", ["/", "/ftir", "/raman", "/xrd", "/tem", "/operations", "/errors", "/report-management"])
def test_all_workspace_pages_include_advisory_without_denying_legacy_requests(path, monkeypatch, tmp_path):
    monkeypatch.setenv("RIST_STORAGE_ROOT", str(tmp_path / "runtime"))
    with TestClient(create_preview_app()) as client:
        response = client.get(path, headers={"User-Agent": "MSIE 8.0; Windows NT 5.1"})
    assert response.status_code == 200
    assert response.text.count('id="rist-browser-warning"') == 1
    assert BROWSER_SUPPORT_SCRIPT in response.text


def test_api_and_plotly_asset_responses_are_not_modified(monkeypatch, tmp_path):
    monkeypatch.setenv("RIST_STORAGE_ROOT", str(tmp_path / "runtime"))
    with TestClient(create_preview_app()) as client:
        assert client.get("/health").json() == {"status": "ok", "mode": "preview"}
        asset = client.get("/ftir/assets/plotly.min.js")
    assert asset.status_code == 200
    assert 'id="rist-browser-warning"' not in asset.text

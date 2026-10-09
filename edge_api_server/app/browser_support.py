"""Chrome-first support guidance for interactive web pages, not report exports.

This is advisory, not authentication or a browser allowlist. Keep the detector
ES5-compatible so a broken modern application script cannot hide the warning.
"""

from __future__ import annotations

import re


BROWSER_POLICY_TEXT = (
    "권장 환경: 보안 지원 중인 운영체제의 최신 Chrome. "
    "Chrome 49.0.2623.75~109 및 구형 Edge에는 호환 모드를 적용합니다. "
    "XP와 구형 브라우저는 보안 지원이 종료되었으므로 격리된 실험망에서만 사용하세요. Internet Explorer는 지원하지 않습니다."
)

BROWSER_SUPPORT_SCRIPT = r"""(function () {
  var warning = document.getElementById('rist-browser-warning');
  var reasonNode = document.getElementById('rist-browser-warning-reason');
  if (!warning || !reasonNode) return;
  if (window.RIST_BROWSER_PROFILE === 'chrome49') {
    warning.className = 'rist-compat-notice';
    var heading = warning.getElementsByTagName('strong')[0];
    if (heading) heading.textContent = 'Chrome 49 / 구형 브라우저 호환 모드';
    reasonNode.textContent = '구형 브라우저 호환 모드입니다. 분석·보고서 처리는 Edge 서버에서 수행합니다. 보안 지원이 종료된 환경이므로 외부 인터넷 사용은 피하세요.';
    warning.style.display = 'block';
    return;
  }
  var ua = navigator.userAgent || '';
  var reason = '';
  var chromium = /(?:Chrome|Chromium|Edg)\/(\d+)/.exec(ua);
  if (/MSIE\s|Trident\/|Edge\//.test(ua)) {
    reason = 'Internet Explorer 및 구형 Edge는 분석·그래프·SSO 화면의 지원 대상이 아닙니다.';
  } else if (/Windows NT (?:5\.[0-9]+|6\.[0-3])(?:[;)\s]|$)/.test(ua)) {
    reason = '이 Windows 버전은 지원 대상이 아닙니다. 브라우저만 바꾸어도 모든 기능이 보장되지는 않습니다.';
  } else if (chromium && parseInt(chromium[1], 10) <= 109) {
    // A known-obsolete floor, NOT a promise that 110+ is current/supported.
    reason = '오래된 Chromium 계열 브라우저입니다. 지원되는 운영체제에서 최신 버전으로 업데이트해 주세요.';
  }
  var missing = [];
  if (typeof window.fetch !== 'function') missing.push('fetch');
  if (typeof window.Promise !== 'function') missing.push('Promise');
  if (typeof window.FormData !== 'function' || !window.FormData.prototype ||
      typeof window.FormData.prototype.get !== 'function') missing.push('FormData');
  if (typeof window.FileReader !== 'function') missing.push('FileReader');
  if (typeof window.Blob !== 'function') missing.push('Blob');
  if (typeof window.URLSearchParams !== 'function') missing.push('URLSearchParams');
  if (!window.URL || typeof window.URL.createObjectURL !== 'function') missing.push('파일 다운로드');
  if (missing.length) {
    reason += (reason ? ' ' : '') + '필수 브라우저 기능이 없습니다: ' + missing.join(', ') + '.';
  }
  if (!reason) return;
  if (typeof reasonNode.textContent !== 'undefined') reasonNode.textContent = reason;
  else reasonNode.innerText = reason;
  warning.style.display = 'block';
}());"""

_NOTICE = """<style>
#rist-browser-warning,.rist-browser-noscript{box-sizing:border-box;max-width:1080px;margin:12px auto;padding:12px 16px;border:1px solid #d69e2e;border-radius:6px;background:#fff8df;color:#624500;font:14px/1.5 Arial,sans-serif;text-align:left;overflow-wrap:break-word;word-wrap:break-word}
#rist-browser-warning p,.rist-browser-noscript p{margin:4px 0;color:#624500;font:inherit}
#rist-browser-warning.rist-compat-notice{max-width:none;margin:0;padding:8px 20px;border-radius:0;border-color:#b9cde3;background:#eef5fc;color:#254563;font-size:12px}
#rist-browser-warning.rist-compat-notice p{color:#254563}
#rist-browser-warning.rist-compat-notice p~p{display:none}
@media(max-width:640px){#rist-browser-warning,.rist-browser-noscript{margin:8px;padding:10px 12px}}
@media print{#rist-browser-warning,.rist-browser-noscript{display:none!important}}
</style>
<div id="rist-browser-warning" role="status" style="display:none">
<strong>브라우저 환경을 확인해 주세요.</strong>
<p id="rist-browser-warning-reason"></p>
<p>지원되는 운영체제의 최신 Chrome에서 다시 열어 주세요. 최신 Edge는 호환 확인 대상입니다.</p>
<p>Chrome 49 이상 구형 Chromium에는 호환 화면이 자동 적용됩니다. HTTPS 연결 및 인증서 문제는 별도 서버·장비 설정 확인이 필요합니다.</p>
</div>
<noscript><div class="rist-browser-noscript"><strong>분석·보고서·SSO 화면에는 JavaScript가 필요합니다.</strong>
<p>지원되는 운영체제의 최신 Chrome을 권장합니다. 로그인·회원가입의 기본 제출은 JavaScript 없이도 가능합니다.</p></div></noscript>
<script>""" + BROWSER_SUPPORT_SCRIPT + "</script>"


def with_browser_support_notice(html: str) -> str:
    """Insert only in a UI page builder; never wrap downloads or API responses."""
    if 'id="rist-browser-warning"' in html:
        return html
    return re.sub(r"<body\b[^>]*>", lambda match: match.group(0) + _NOTICE, html, count=1, flags=re.IGNORECASE)

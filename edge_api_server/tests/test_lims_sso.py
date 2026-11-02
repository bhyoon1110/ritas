from __future__ import annotations

from dataclasses import replace
import json
import ssl

import httpx
import pytest

from app.lims_sso import (
    LimsSsoClient,
    LimsSsoConfigurationError,
    LimsSsoProtocolError,
    LimsSsoUnavailableError,
)


URL = "https://spring.example.test/api/lims/auth/verify"
INTERNAL_KEY = "test-internal-key-32-characters-long"


def client_for(body=None, *, status=200, headers=None):
    return LimsSsoClient(
        URL, INTERNAL_KEY,
        transport=httpx.MockTransport(lambda request: httpx.Response(
            status, json=body or {"result": "SUCCESS"}, headers=headers,
        )),
    )


def test_posts_json_to_relay_without_sid_and_keeps_tls_validation(monkeypatch):
    observed = {}
    original_client = httpx.Client

    def capture_client(**kwargs):
        observed.update(kwargs)
        return original_client(**kwargs)

    def handler(request):
        observed["request"] = request
        return httpx.Response(200, json={"result": "SUCCESS"})

    monkeypatch.setattr(httpx, "Client", capture_client)
    adapter = LimsSsoClient(URL, INTERNAL_KEY, transport=httpx.MockTransport(handler))
    password = "p&ss=word+한글!"
    assert adapter.validate("employee01", password) is True
    request = observed["request"]
    assert str(request.url) == URL
    assert request.method == "POST"
    assert request.headers["X-RITASAX-Internal-Key"] == INTERNAL_KEY
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == {"userId": "employee01", "password": password}
    assert observed["trust_env"] is False
    assert observed["follow_redirects"] is False
    assert isinstance(observed["verify"], ssl.SSLContext)
    assert observed["verify"].check_hostname is True
    assert observed["verify"].verify_mode == ssl.CERT_REQUIRED
    assert INTERNAL_KEY not in repr(adapter)
    assert password not in repr(adapter)


@pytest.mark.parametrize("code", ["INVALID_CREDENTIAL", "ACCOUNT_LOCKED", "ACCOUNT_DISABLED"])
def test_documented_rejections_are_false(code):
    assert client_for({"result": "FAIL", "errorCode": code}).validate("employee", "password") is False


@pytest.mark.parametrize("body", [
    {"result": "T"}, {"result": True}, {"result": "success"}, {"status": "SUCCESS"},
    {"result": "FAIL"}, {"result": "FAIL", "errorCode": "UNRECOGNIZED"},
    {"result": "SUCCESS", "errorCode": "ACCOUNT_LOCKED"}, ["SUCCESS"],
])
def test_undocumented_responses_never_authenticate(body):
    with pytest.raises(LimsSsoProtocolError):
        client_for(body).validate("employee", "password")


@pytest.mark.parametrize("status", [201, 400, 401, 403, 404, 500, 502, 503])
def test_http_error_or_non_200_never_becomes_user_success(status):
    with pytest.raises(LimsSsoUnavailableError):
        client_for(status=status).validate("employee", "password")


def test_redirect_is_not_followed():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(307, headers={"Location": "https://other.example.test/capture"})

    with pytest.raises(LimsSsoProtocolError):
        LimsSsoClient(URL, INTERNAL_KEY, transport=httpx.MockTransport(handler)).validate("id", "secret")
    assert len(requests) == 1


@pytest.mark.parametrize("url", [
    "http://spring.example.test/api/lims/auth/verify", "http://localhost:8888/api/lims/auth/verify",
    "http://192.168.0.2:8888/api/lims/auth/verify", "https://user:pw@spring.example.test/verify",
    URL + "?token=secret", URL + "#secret", "file:///tmp/auth",
])
def test_unsafe_url_is_blocked_even_when_loopback_http_enabled(url):
    with pytest.raises(LimsSsoConfigurationError):
        replace(client_for(), validation_url=url, allow_loopback_http=True).validate("id", "password")


@pytest.mark.parametrize("url", ["http://127.0.0.1:8888/api/lims/auth/verify", "http://[::1]:8888/api/lims/auth/verify"])
def test_loopback_http_requires_explicit_opt_in(url):
    adapter = replace(client_for(), validation_url=url)
    with pytest.raises(LimsSsoConfigurationError):
        adapter.validate("id", "password")
    assert replace(adapter, allow_loopback_http=True).validate("id", "password") is True


@pytest.mark.parametrize("key", ["", "too-short", "x" * 31, "x" * 513, "x" * 32 + "\r\n", "x" * 32 + "한"])
def test_invalid_internal_key_is_rejected(key):
    with pytest.raises(LimsSsoConfigurationError):
        replace(client_for(), internal_key=key).validate("id", "password")


def test_missing_ca_file_is_not_ignored(tmp_path):
    with pytest.raises(LimsSsoConfigurationError):
        replace(client_for(), ca_bundle=tmp_path / "missing.pem").validate("id", "password")


@pytest.mark.parametrize("body", ["not-json", "{\"result\":\"SUCCESS\",\"padding\":\"" + "x" * 5000 + "\"}"])
def test_malformed_or_oversized_body_rejected(body):
    adapter = LimsSsoClient(URL, INTERNAL_KEY, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=body)))
    with pytest.raises(LimsSsoProtocolError):
        adapter.validate("id", "password")


def test_transport_error_does_not_expose_upstream_exception_details():
    secret = "never-expose-this-password"

    def handler(request):
        raise httpx.ConnectError(secret + INTERNAL_KEY, request=request)

    adapter = LimsSsoClient(URL, INTERNAL_KEY, transport=httpx.MockTransport(handler))
    with pytest.raises(LimsSsoUnavailableError) as captured:
        adapter.validate("employee", secret)
    assert secret not in str(captured.value)
    assert INTERNAL_KEY not in str(captured.value)
    assert captured.value.__suppress_context__

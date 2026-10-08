from __future__ import annotations

from datetime import datetime, timedelta
from html.parser import HTMLParser
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.auth import AUTH_FORM_COOKIE, SESSION_COOKIE, AuthContext, install_auth, safe_return_to
from app.config import Settings
from app.errors import ApiException, api_exception_handler


class _Form(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__()
        self.fields: dict[str, str] = {}
        self.inputs: dict[str, dict] = {}
        self.forms: list[dict] = []
        self.scripts = 0
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attributes = dict(attrs)
        if tag == "form":
            self.forms.append(attributes)
        if tag == "input" and "name" in attributes:
            self.inputs[attributes["name"]] = attributes
            self.fields[attributes["name"]] = attributes.get("value", "")
        if tag == "script":
            self.scripts += 1


@pytest.fixture
def browser(tmp_path):
    app = FastAPI()
    settings = Settings(storage_root=tmp_path, auth_enabled=True, auth_cookie_secure=True, edge_public_base_url="https://testserver")
    install_auth(app, settings, Mock())
    app.add_exception_handler(ApiException, api_exception_handler)
    service = app.state.auth_service
    context = AuthContext(
        user_id="member-1", login_id="member01", email=None, display_name="회원",
        status="ACTIVE", session_id="session-1",
        session_expires_at=datetime.now() + timedelta(hours=1),
        sso_authenticated_at=None, projects=frozenset({"FTIR"}), roles=frozenset(),
        sso_identity=None,
    )
    service.context_from_token = Mock(side_effect=lambda token: context if token == "test-session-token" else None)
    service.login = Mock(return_value=(context, "test-session-token"))
    service.signup = Mock(return_value={"userId": "new-member", "status": "PENDING"})
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as client:
        yield client, service


def _submit(client: TestClient, path: str, data: dict, **kwargs):
    fields = _Form(client.get(path).text).fields
    fields.update(data)
    kwargs.setdefault("headers", {"Referer": str(client.base_url).rstrip("/") + path})
    return client.post(path, data=fields, **kwargs)


@pytest.mark.parametrize("path", ["/login", "/signup"])
def test_auth_pages_are_native_post_forms_without_javascript(browser, path):
    client, _ = browser
    response = client.get(path, params={"returnTo": "/ftir?tab=report"})
    form = _Form(response.text)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert form.scripts == 0
    assert form.forms == [{"id": "form", "method": "post", "action": path, "accept-charset": "UTF-8"}]
    assert form.fields["returnTo"] == "/ftir?tab=report"
    assert form.fields["csrfToken"] == client.cookies[AUTH_FORM_COOKIE]
    assert len(form.fields["csrfToken"]) == 43
    assert 'value' not in form.inputs['password']
    assert '<main' not in response.text and '<section' not in response.text
    assert 'content="IE=edge"' in response.text
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert "expires=" in cookie.lower()


@pytest.mark.parametrize("headers", [
    {"Referer": "https://testserver/login", "User-Agent": "Mozilla/4.0 (compatible; MSIE 8.0; Windows NT 5.1)"},
    {"Origin": "https://testserver", "User-Agent": "Mozilla/5.0 (Windows NT 6.1) Chrome/109.0.0.0 Edg/109.0.1518.78"},
])
def test_login_form_creates_session_without_javascript(browser, headers):
    # These headers exercise HTTP behavior, not an emulated IE/Edge JS engine.
    client, service = browser
    secret = "한글 &+=% 비밀번호"
    response = _submit(client, "/login", {"loginId": " MEMBER01 ", "password": secret, "returnTo": "/ftir?tab=report"}, headers=headers)

    assert response.status_code == 303
    assert response.headers["location"] == "/ftir?tab=report"
    assert response.headers["cache-control"] == "no-store"
    payload = service.login.call_args.args[0]
    assert payload.login_id == "member01"
    assert payload.password == secret
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert "expires=" in cookie.lower()
    assert client.cookies[SESSION_COOKIE] == "test-session-token"
    assert client.get("/api/v1/auth/me").json()["loginId"] == "member01"
    assert secret not in str(response.headers) + response.text


@pytest.mark.parametrize("status", ["PENDING", "ACTIVE"])
def test_signup_form_redirects_to_get_and_retains_existing_approval_policy(browser, status):
    client, service = browser
    service.signup.return_value["status"] = status
    response = _submit(client, "/signup", {"loginId": " New.User ", "displayName": " 시험 회원 ", "password": "safe-password&+=123", "returnTo": "/tem"})

    assert response.status_code == 303
    target = urlsplit(response.headers["location"])
    assert target.path == "/login"
    assert parse_qs(target.query) == {"registered": [status], "returnTo": ["/tem"]}
    payload = service.signup.call_args.args[0]
    assert payload.login_id == "new.user"
    assert payload.display_name == "시험 회원"
    assert payload.email is None
    assert payload.password == "safe-password&+=123"
    assert SESSION_COOKIE not in client.cookies
    page = client.get(response.headers["location"])
    assert ("관리자 승인을 기다려" if status == "PENDING" else "가입되었습니다. 로그인하세요.") in page.text
    client.get(response.headers["location"])
    assert service.signup.call_count == 1


@pytest.mark.parametrize("path,field,value,message", [
    ("/login", "loginId", "ab", "로그인 ID를 3~255자"),
    ("/login", "password", "", "비밀번호를 1~200자"),
    ("/signup", "loginId", "invalid id", "가입 ID는"),
    ("/signup", "password", "short", "비밀번호를 10~200자"),
    ("/signup", "displayName", "   ", "이름을 1~100자"),
    ("/signup", "email", "not-an-email", "올바른 이메일"),
])
def test_server_validates_input_even_without_html5_validation(browser, path, field, value, message):
    client, service = browser
    data = {"loginId": "member01", "displayName": "사용자", "email": "", "password": "temporary-secret"}
    data[field] = value
    response = _submit(client, path, data)

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/html")
    assert message in response.text
    assert _Form(response.text).fields["password"] == ""
    assert "temporary-secret" not in response.text
    service.login.assert_not_called()
    service.signup.assert_not_called()


@pytest.mark.parametrize("path,code,message", [
    ("/login", 401, "로그인 ID 또는 비밀번호를 확인하세요."),
    ("/login", 403, "관리자 승인 대기 중이거나 사용이 중지된 계정입니다."),
    ("/signup", 409, "이미 사용 중인 로그인 ID입니다."),
])
def test_service_errors_are_visible_and_never_reflect_password(browser, path, code, message):
    client, service = browser
    getattr(service, path[1:]).side_effect = ApiException(code, "TEST_ERROR", message)
    response = _submit(client, path, {"loginId": "member01", "displayName": '<img src=x onerror="alert(1)">', "password": "must-not-be-reflected"})

    assert response.status_code == code
    assert message in response.text
    form = _Form(response.text)
    assert form.fields["loginId"] == "member01"
    assert form.fields["password"] == ""
    assert "must-not-be-reflected" not in response.text
    assert '<img src=x' not in response.text
    assert SESSION_COOKIE not in client.cookies


@pytest.mark.parametrize("path", ["/login", "/signup"])
@pytest.mark.parametrize("headers", [
    {}, {"Origin": "null"}, {"Origin": "https://evil.example"},
    {"Referer": "https://testserver.evil.example/login"},
    {"Origin": "https://evil.example", "Referer": "https://testserver/login"},
    {"Origin": "http://testserver"}, {"Origin": "https://testserver:444"},
])
def test_native_auth_forms_require_same_origin_even_with_valid_token(browser, path, headers):
    client, service = browser
    response = _submit(client, path, {"loginId": "member01", "displayName": "회원", "password": "temporary-secret"}, headers=headers)
    assert response.status_code == 403
    assert "요청 출처를 확인할 수 없습니다" in response.text
    service.login.assert_not_called()
    service.signup.assert_not_called()


@pytest.mark.parametrize("token", ["", "wrong-token", "x" * 43, "한" * 43])
def test_invalid_csrf_token_does_not_create_session(browser, token):
    client, service = browser
    response = _submit(client, "/login", {"loginId": "member01", "password": "temporary-secret", "csrfToken": token})
    assert response.status_code == 403
    assert "쿠키" in response.text
    service.login.assert_not_called()


def test_blocked_cookies_show_an_actionable_error(browser):
    client, service = browser
    fields = _Form(client.get("/login").text).fields
    fields.update(loginId="member01", password="temporary-secret")
    client.cookies.clear()
    response = client.post("/login", data=fields, headers={"Referer": "https://testserver/login"})
    assert response.status_code == 403
    assert "쿠키를 허용하고 다시 입력" in response.text
    assert "temporary-secret" not in response.text
    service.login.assert_not_called()


def test_opening_another_auth_tab_keeps_existing_form_valid(browser):
    client, service = browser
    first = _Form(client.get("/login").text).fields
    second = _Form(client.get("/signup").text).fields
    assert first["csrfToken"] == second["csrfToken"]
    first.update(loginId="member01", password="temporary-secret")
    assert client.post("/login", data=first, headers={"Referer": "https://testserver/login"}).status_code == 303
    service.login.assert_called_once()


@pytest.mark.parametrize("body,content_type,status", [
    ("loginId=one&loginId=two", "application/x-www-form-urlencoded", 400),
    ("password=" + "s" * 8192, "application/x-www-form-urlencoded", 413),
    ("password=%FF", "application/x-www-form-urlencoded", 400),
    ("&".join(f"a{i}=b" for i in range(13)), "application/x-www-form-urlencoded", 400),
    ('{"password":"secret"}', "application/json", 415),
])
def test_malformed_form_does_not_reach_authentication(browser, body, content_type, status):
    client, service = browser
    response = client.post("/login", content=body, headers={"Referer": "https://testserver/login", "Content-Type": content_type})
    assert response.status_code == status
    assert response.headers["content-type"].startswith("text/html")
    service.login.assert_not_called()


@pytest.mark.parametrize("path", ["/login", "/signup"])
def test_secure_cookie_deployments_explain_and_reject_http_forms(browser, path):
    client, service = browser
    page = client.get("http://testserver" + path)
    assert "HTTPS 주소로 접속하세요" in page.text
    response = client.post("http://testserver" + path, data={"password": "temporary-secret"})
    assert response.status_code == 426
    assert "HTTPS 주소로 접속하세요" in response.text
    assert "temporary-secret" not in response.text
    service.login.assert_not_called()
    service.signup.assert_not_called()


@pytest.mark.parametrize("target", ["//evil.example", "https://evil.example", "/\\evil.example", "/\tevil.example", "/\x00evil"])
def test_native_login_never_redirects_to_an_external_or_control_character_url(browser, target):
    client, _ = browser
    assert safe_return_to(target) == "/"
    response = _submit(client, "/login", {"loginId": "member01", "password": "temporary-secret", "returnTo": target})
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_json_auth_endpoints_remain_compatible(browser):
    client, service = browser
    response = client.post("/api/v1/auth/signup", json={"loginId": "member01", "displayName": "회원", "password": "temporary-secret"})
    assert response.status_code == 200
    assert response.json()["status"] == "PENDING"
    response = client.post("/api/v1/auth/login", json={"loginId": "member01", "password": "temporary-secret", "returnTo": "/ftir"})
    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert response.json()["returnTo"] == "/ftir"
    assert client.cookies[SESSION_COOKIE] == "test-session-token"
    service.signup.assert_called_once()
    service.login.assert_called_once()


def test_account_forms_never_submit_passwords_in_urls_when_javascript_fails(browser):
    client, service = browser
    _submit(client, "/login", {"loginId": "member01", "password": "temporary-secret"})
    form = _Form(client.get("/account").text)
    assert {item["id"] for item in form.forms} == {"profile-form", "password-form", "sso-form"}
    for item in form.forms:
        assert item["method"] == "post"
        assert item["action"] == "/account"

    response = client.post("/account", data={"username": "member01", "password": "never-echo-this"})
    assert response.status_code == 409
    assert "최신 브라우저와 JavaScript가 필요" in response.text
    assert "never-echo-this" not in response.text + str(response.url)
    assert response.headers["cache-control"] == "no-store"
    service.signup.assert_not_called()


def test_development_http_cookie_mode_remains_supported(tmp_path):
    app = FastAPI()
    install_auth(app, Settings(storage_root=tmp_path, auth_cookie_secure=False), Mock())
    service = app.state.auth_service
    service.login = Mock(return_value=(None, "dev-session-only"))
    with TestClient(app, follow_redirects=False) as client:
        response = _submit(client, "/login", {"loginId": "member01", "password": "temporary-secret"})
        assert response.status_code == 303
        assert client.cookies[SESSION_COOKIE] == "dev-session-only"
        assert "Secure" not in response.headers["set-cookie"]


def test_signed_in_user_redirects_and_cannot_access_unapproved_project(browser):
    client, _ = browser
    _submit(client, "/login", {"loginId": "member01", "password": "temporary-secret"})
    response = client.get("/login", params={"returnTo": "/tem"})
    assert response.status_code == 303
    assert response.headers["location"] == "/tem"
    denied = client.get("/tem")
    assert denied.status_code == 403
    assert "프로젝트 승인이 필요합니다" in denied.text

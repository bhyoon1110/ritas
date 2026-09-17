from __future__ import annotations

from datetime import datetime, timedelta
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.auth import (
    AuthService,
    AuthContext,
    LoginRequest,
    PasswordChangeRequest,
    PoscoSsoVerifyRequest,
    ProfileUpdateRequest,
    SignupRequest,
    _account_page,
    _admin_page,
    authenticated_transfer_payload,
    hash_password,
    install_auth,
    is_bootstrap_admin,
    project_for_path,
    safe_return_to,
    verify_password,
)
from app.config import Settings
from app.errors import ApiException, redact_validation_errors
from app.preview_report import PreviewReportSendRequest


def _context(
    *,
    projects: frozenset[str] = frozenset({"FTIR"}),
    roles: frozenset[str] = frozenset({"REPORT_SENDER"}),
    sso_identity: dict | None = None,
    sso_authenticated_at: datetime | None = None,
) -> AuthContext:
    return AuthContext(
        user_id="user-1",
        login_id="user01",
        email="user@example.com",
        display_name="사용자",
        status="ACTIVE",
        session_id="session-1",
        session_expires_at=datetime.utcnow() + timedelta(hours=1),
        sso_authenticated_at=sso_authenticated_at,
        projects=projects,
        roles=roles,
        sso_identity=sso_identity,
    )


def _request(tmp_path, context: AuthContext, *, auth_enabled: bool = True) -> Request:
    app = FastAPI()
    app.state.settings = Settings(
        storage_root=tmp_path,
        auth_enabled=auth_enabled,
        auth_recent_sso_minutes=30,
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/ftir/report/send",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
            "scheme": "http",
            "query_string": b"",
            "app": app,
        }
    )
    request.state.auth_context = context
    return request


def _payload() -> PreviewReportSendRequest:
    return PreviewReportSendRequest(
        requestNumber="REQ-1",
        experimentCode="FT-IR",
        equipmentCode="FTIR-01",
        operatorId="화면 입력값",
    )


def test_password_hash_round_trip() -> None:
    encoded = hash_password("correct horse battery staple")

    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong password", encoded)
    assert not verify_password("anything", "invalid")


def test_safe_return_to_rejects_external_urls() -> None:
    assert safe_return_to("/ftir?tab=report") == "/ftir?tab=report"
    assert safe_return_to("https://evil.example") == "/"
    assert safe_return_to("//evil.example") == "/"
    assert safe_return_to("/ftir\nLocation: https://evil.example") == "/"


def test_project_for_path() -> None:
    assert project_for_path("/ftir") == "FTIR"
    assert project_for_path("/api/v1/raman/reports") == "RAMAN"
    assert project_for_path("/xrd/example") == "XRD"
    assert project_for_path("/api/v1/tem/report") == "TEM"
    assert project_for_path("/operations") is None


def test_admin_page_exposes_member_permissions_and_sso_state() -> None:
    html = _admin_page()

    assert 'href="/operations"' in html
    assert 'href="/errors"' in html
    assert 'href="/report-management"' in html
    assert 'href="/admin/users"' in html
    assert 'main class="wide"' in html
    assert 'href="/account"' in html
    assert 'id="logout-button"' in html
    assert "/api/v1/auth/logout" in html
    assert "회원 관리" in html
    assert "승인 대기" in html
    assert "프로젝트 접근 및 보고서 생성" in html
    assert "운영 관리자" in html
    assert "보고서 전송 권한" in html
    assert "최근 SSO 인증" in html
    assert "/api/v1/auth/admin/users" in html
    assert 'id="select-all-members"' in html
    assert 'id="bulk-approve"' in html
    assert 'id="bulk-project"' in html
    assert 'id="bulk-grant"' in html
    assert 'id="bulk-revoke"' in html
    assert 'data-member-row' in html
    assert 'id="member-drawer"' in html
    assert 'id="drawer-save"' in html
    assert "table-layout:fixed" in html
    assert "min-width:1266px" in html
    assert '<col style="width:220px"><col style="width:190px">' in html
    assert "overflow-wrap:anywhere" in html
    assert ".member-select,#select-all-members,input.project,input.role" in html
    assert ".member-select,#select-all-members,.project,.role" not in html


def test_bootstrap_admin_policy() -> None:
    assert is_bootstrap_admin("first", first_user=True, configured_ids=())
    assert is_bootstrap_admin(
        "ADMIN",
        first_user=False,
        configured_ids=("admin",),
    )
    assert not is_bootstrap_admin(
        "first",
        first_user=True,
        configured_ids=("admin",),
    )


def test_login_id_models_normalize_and_allow_optional_email() -> None:
    signup = SignupRequest(
        loginId="  Test.User_01  ",
        password="correct horse battery staple",
        displayName="사용자",
    )
    login = LoginRequest(loginId="  TEST.USER_01 ", password="password")

    assert signup.login_id == "test.user_01"
    assert signup.email is None
    assert login.login_id == "test.user_01"


def test_account_page_exposes_profile_password_and_logout(tmp_path) -> None:
    html = _account_page(
        _context(),
        Settings(
            storage_root=tmp_path,
            edge_public_base_url="https://edge.example.com",
        ),
    )

    assert 'id="profile-form"' in html
    assert 'id="password-form"' in html
    assert 'id="logout"' in html
    assert "/api/v1/auth/me" in html
    assert "/api/v1/auth/password" in html
    assert "/api/v1/auth/logout" in html
    assert 'id="sso-form"' in html
    assert "/api/v1/auth/sso/verify" in html
    assert "저장하거나 감사 로그에 남기지 않습니다" in html


def test_account_page_blocks_posco_password_form_until_https(tmp_path) -> None:
    html = _account_page(
        _context(),
        Settings(
            storage_root=tmp_path,
            sso_mode="posco",
            edge_public_base_url="http://edge.example.com",
        ),
    )

    assert 'id="sso-form"' not in html
    assert "Edge HTTPS 적용 후 인증할 수 있습니다" in html


def test_account_page_keeps_oidc_redirect_mode(tmp_path) -> None:
    html = _account_page(
        _context(),
        Settings(storage_root=tmp_path, sso_mode="oidc"),
    )

    assert 'id="sso-form"' not in html
    assert 'href="/auth/sso/start?returnTo=/account"' in html


def test_posco_sso_start_routes_to_local_account_form(tmp_path) -> None:
    service = AuthService(
        Settings(storage_root=tmp_path, sso_mode="posco"),
        _FakeDatabase(),
        _FakePoscoClient(True),
    )

    assert service.start_sso(_context(), "/ftir?tab=report") == (
        "/account?ssoReturnTo=%2Fftir%3Ftab%3Dreport#sso-auth"
    )


def test_account_update_models_normalize_values() -> None:
    profile = ProfileUpdateRequest(
        displayName="  시험 사용자  ",
        email="  USER@EXAMPLE.COM  ",
    )
    password = PasswordChangeRequest(
        currentPassword="existing-password",
        newPassword="new-password-123",
    )

    assert profile.display_name == "시험 사용자"
    assert profile.email == "user@example.com"
    assert password.current_password == "existing-password"
    assert password.new_password == "new-password-123"


def test_posco_sso_request_strips_id_but_preserves_case_and_password() -> None:
    payload = PoscoSsoVerifyRequest(
        username="  EMPLOYEE01  ",
        password="case-sensitive-password",
        returnTo="/ftir",
    )

    assert payload.username == "EMPLOYEE01"
    assert payload.password == "case-sensitive-password"
    assert payload.return_to == "/ftir"


def test_validation_error_details_redact_credentials() -> None:
    redacted = redact_validation_errors(
        [
            {"loc": ("body", "password"), "input": "top-secret"},
            {
                "loc": ("body",),
                "input": {
                    "username": "employee",
                    "currentPassword": "old-secret",
                    "profile": {"token": "hidden"},
                },
            },
        ]
    )

    rendered = repr(redacted)
    assert "top-secret" not in rendered
    assert "old-secret" not in rendered
    assert "hidden" not in rendered
    assert "employee" in rendered
    assert rendered.count("[REDACTED]") == 3


def test_validation_error_details_are_json_serializable() -> None:
    redacted = redact_validation_errors(
        [
            {
                "loc": ("body", "prompt"),
                "ctx": {"error": ValueError("must not be blank")},
                "input": "   ",
            }
        ]
    )

    assert "must not be blank" in json.dumps(redacted)


class _Result:
    def __init__(self, row: dict | None = None) -> None:
        self.row = row

    def fetchone(self) -> dict | None:
        return self.row


class _FakeConnection:
    def __init__(self, owner: dict | None = None) -> None:
        self.owner = owner
        self.statements: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params: tuple = ()) -> _Result:
        normalized = " ".join(sql.split())
        self.statements.append((normalized, tuple(params)))
        if normalized.startswith("SELECT user_id FROM sso_identities"):
            return _Result(self.owner)
        return _Result()


class _Transaction:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    def __enter__(self) -> _FakeConnection:
        return self.connection

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


class _FakeDatabase:
    def __init__(self, owner: dict | None = None) -> None:
        self.connection = _FakeConnection(owner)

    def transaction(self) -> _Transaction:
        return _Transaction(self.connection)


class _FakePoscoClient:
    def __init__(self, result: bool) -> None:
        self.result = result
        self.calls: list[tuple[str, str]] = []

    def validate(self, username: str, password: str) -> bool:
        self.calls.append((username, password))
        return self.result


def test_posco_sso_success_links_employee_and_updates_current_session(tmp_path) -> None:
    database = _FakeDatabase()
    client = _FakePoscoClient(True)
    settings = Settings(
        storage_root=tmp_path,
        sso_mode="posco",
        sso_provider_name="POSCO SSO",
    )
    service = AuthService(settings, database, client)
    context = _context()
    payload = PoscoSsoVerifyRequest(
        username="EMPLOYEE01",
        password="temporary-secret",
        returnTo="/ftir",
    )

    result = service.verify_posco_sso(
        context,
        payload,
        _request(tmp_path, context),
    )

    assert result["authenticated"] is True
    assert result["employeeId"] == "EMPLOYEE01"
    assert result["returnTo"] == "/ftir"
    assert client.calls == [("EMPLOYEE01", "temporary-secret")]
    statements = database.connection.statements
    assert any("INSERT INTO sso_identities" in sql for sql, _ in statements)
    assert any("UPDATE auth_sessions SET sso_authenticated_at" in sql for sql, _ in statements)
    assert any("employee01" in params for _, params in statements)
    assert "temporary-secret" not in repr(statements)


def test_posco_sso_f_result_does_not_link_identity(tmp_path) -> None:
    database = _FakeDatabase()
    context = _context()
    service = AuthService(
        Settings(storage_root=tmp_path, sso_mode="posco"),
        database,
        _FakePoscoClient(False),
    )

    try:
        service.verify_posco_sso(
            context,
            PoscoSsoVerifyRequest(username="employee", password="wrong-password"),
            _request(tmp_path, context),
        )
    except ApiException as exc:
        assert exc.code == "SSO_VALIDATION_REJECTED"
    else:
        raise AssertionError("SSO F 응답이 인증 성공으로 처리되었습니다.")
    assert not any(
        "INSERT INTO sso_identities" in sql
        for sql, _ in database.connection.statements
    )
    assert "wrong-password" not in repr(database.connection.statements)


def test_auth_routes_include_server_side_posco_verification(tmp_path) -> None:
    app = FastAPI()

    install_auth(
        app,
        Settings(storage_root=tmp_path, sso_mode="posco"),
        _FakeDatabase(),
    )

    routes = {
        (route.path, frozenset(route.methods or ()))
        for route in app.routes
        if hasattr(route, "methods")
    }
    assert (
        "/api/v1/auth/sso/verify",
        frozenset({"POST"}),
    ) in routes


def test_posco_sso_api_rejects_http_before_authentication_or_body_use(tmp_path) -> None:
    app = FastAPI()
    database = _FakeDatabase()
    install_auth(
        app,
        Settings(storage_root=tmp_path, sso_mode="posco"),
        database,
    )
    secret = "must-not-be-processed-over-http"

    response = TestClient(app).post(
        "/api/v1/auth/sso/verify",
        headers={"X-Requested-With": "RIST-Account"},
        json={"username": "employee01", "password": secret},
    )

    assert response.status_code == 426
    assert response.json()["code"] == "SSO_HTTPS_REQUIRED"
    assert secret not in response.text
    assert database.connection.statements == []


def test_transfer_auth_disabled_keeps_payload(tmp_path) -> None:
    payload = _payload()
    request = _request(tmp_path, _context(), auth_enabled=False)

    assert authenticated_transfer_payload(request, payload, "FTIR") is payload


def test_transfer_uses_recent_sso_identity_as_operator(tmp_path) -> None:
    context = _context(
        sso_identity={
            "subject": "sso-subject",
            "employee_id": "EMP-001",
            "email": "employee@example.com",
        },
        sso_authenticated_at=datetime.utcnow() - timedelta(minutes=5),
    )
    result = authenticated_transfer_payload(_request(tmp_path, context), _payload(), "FTIR")

    assert result.operator_id == "EMP-001"
    assert result.request_number == "REQ-1"


def test_transfer_requires_sender_role(tmp_path) -> None:
    context = _context(
        roles=frozenset(),
        sso_identity={"subject": "sso-subject"},
        sso_authenticated_at=datetime.utcnow(),
    )

    try:
        authenticated_transfer_payload(_request(tmp_path, context), _payload(), "FTIR")
    except ApiException as exc:
        assert exc.code == "REPORT_SEND_PERMISSION_REQUIRED"
    else:
        raise AssertionError("보고서 전송 역할이 없는데 전송이 허용되었습니다.")


def test_transfer_requires_linked_sso(tmp_path) -> None:
    context = _context(sso_authenticated_at=datetime.utcnow())

    try:
        authenticated_transfer_payload(_request(tmp_path, context), _payload(), "FTIR")
    except ApiException as exc:
        assert exc.code == "SSO_LINK_REQUIRED"
    else:
        raise AssertionError("SSO 연결 없이 전송이 허용되었습니다.")


def test_transfer_requires_recent_sso(tmp_path) -> None:
    context = _context(
        sso_identity={"subject": "sso-subject"},
        sso_authenticated_at=datetime.utcnow() - timedelta(minutes=31),
    )

    try:
        authenticated_transfer_payload(
            _request(tmp_path, context),
            _payload(),
            "FTIR",
            return_to="/reports/report-1",
        )
    except ApiException as exc:
        assert exc.code == "SSO_REAUTH_REQUIRED"
        assert exc.details == {
            "reauthUrl": "/auth/sso/start?returnTo=%2Freports%2Freport-1"
        }
    else:
        raise AssertionError("최근 SSO 재인증 없이 전송이 허용되었습니다.")

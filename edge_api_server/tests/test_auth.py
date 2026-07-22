from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import FastAPI
from starlette.requests import Request

from app.auth import (
    AuthContext,
    LoginRequest,
    SignupRequest,
    authenticated_transfer_payload,
    hash_password,
    is_bootstrap_admin,
    project_for_path,
    safe_return_to,
    verify_password,
)
from app.config import Settings
from app.errors import ApiException
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
        authenticated_transfer_payload(_request(tmp_path, context), _payload(), "FTIR")
    except ApiException as exc:
        assert exc.code == "SSO_REAUTH_REQUIRED"
    else:
        raise AssertionError("최근 SSO 재인증 없이 전송이 허용되었습니다.")

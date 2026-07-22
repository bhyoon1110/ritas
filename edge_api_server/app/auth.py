from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
import hashlib
import hmac
import json
import re
import secrets
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field, field_validator

from .config import Settings
from .database import Database
from .errors import ApiException, error_response
from .preview_report import PreviewReportSendRequest


SESSION_COOKIE = "rist_session"
PROJECT_CODES = ("FTIR", "RAMAN", "XRD", "TEM")
ROLE_CODES = ("ADMIN", "REPORT_SENDER")
USER_STATUSES = ("PENDING", "ACTIVE", "SUSPENDED")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LOGIN_ID_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


@dataclass(frozen=True)
class AuthContext:
    user_id: str
    login_id: str
    email: str | None
    display_name: str
    status: str
    session_id: str
    session_expires_at: datetime
    sso_authenticated_at: datetime | None
    projects: frozenset[str]
    roles: frozenset[str]
    sso_identity: dict[str, Any] | None

    @property
    def is_admin(self) -> bool:
        return "ADMIN" in self.roles


class SignupRequest(BaseModel):
    login_id: str = Field(alias="loginId", min_length=3, max_length=64)
    email: str | None = Field(default=None, max_length=255)
    password: str = Field(min_length=10, max_length=200)
    display_name: str = Field(alias="displayName", min_length=1, max_length=100)

    @field_validator("login_id")
    @classmethod
    def validate_login_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not LOGIN_ID_RE.fullmatch(normalized):
            raise ValueError("로그인 ID는 영문, 숫자, 점, 밑줄, 하이픈만 사용할 수 있습니다.")
        return normalized

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip().lower()
        if not EMAIL_RE.fullmatch(normalized):
            raise ValueError("올바른 이메일 형식이 아닙니다.")
        return normalized

    @field_validator("display_name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip()


class LoginRequest(BaseModel):
    login_id: str = Field(alias="loginId", min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=200)
    return_to: str = Field(default="/", alias="returnTo", max_length=512)

    @field_validator("login_id")
    @classmethod
    def normalize_login_id(cls, value: str) -> str:
        return value.strip().lower()


class AdminUserUpdate(BaseModel):
    status: str
    projects: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in USER_STATUSES:
            raise ValueError("지원하지 않는 회원 상태입니다.")
        return normalized

    @field_validator("projects")
    @classmethod
    def validate_projects(cls, value: list[str]) -> list[str]:
        normalized = sorted({item.strip().upper() for item in value})
        if any(item not in PROJECT_CODES for item in normalized):
            raise ValueError("지원하지 않는 프로젝트 권한입니다.")
        return normalized

    @field_validator("roles")
    @classmethod
    def validate_roles(cls, value: list[str]) -> list[str]:
        normalized = sorted({item.strip().upper() for item in value})
        if any(item not in ROLE_CODES for item in normalized):
            raise ValueError("지원하지 않는 역할입니다.")
        return normalized


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32
    )
    return f"scrypt$16384$8$1${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt_bytes = base64.urlsafe_b64decode(salt + "=" * (-len(salt) % 4))
        expected_bytes = base64.urlsafe_b64decode(
            expected + "=" * (-len(expected) % 4)
        )
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt_bytes,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected_bytes),
        )
        return hmac.compare_digest(actual, expected_bytes)
    except (TypeError, ValueError):
        return False


def safe_return_to(value: str | None, default: str = "/") -> str:
    candidate = str(value or "").strip()
    if not candidate.startswith("/") or candidate.startswith("//"):
        return default
    if "\r" in candidate or "\n" in candidate:
        return default
    return candidate[:512]


def project_for_path(path: str) -> str | None:
    lowered = path.lower()
    for prefix, project in (
        ("/api/v1/ftir", "FTIR"),
        ("/ftir", "FTIR"),
        ("/api/v1/raman", "RAMAN"),
        ("/raman", "RAMAN"),
        ("/api/v1/xrd", "XRD"),
        ("/xrd", "XRD"),
        ("/api/v1/tem", "TEM"),
        ("/tem", "TEM"),
    ):
        if lowered == prefix or lowered.startswith(prefix + "/"):
            return project
    return None


def is_bootstrap_admin(
    login_id: str,
    *,
    first_user: bool,
    configured_ids: tuple[str, ...],
) -> bool:
    normalized_id = login_id.strip().lower()
    configured = {item.strip().lower() for item in configured_ids if item.strip()}
    return normalized_id in configured or (first_user and not configured)


def _request_ip(request: Request) -> str | None:
    forwarded = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
    return forwarded or (request.client.host if request.client else None)


class AuthService:
    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database

    def _audit(
        self,
        event_type: str,
        success: bool,
        *,
        user_id: str | None = None,
        request: Request | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO auth_audit_events (
                    user_id, event_type, success, details_json, remote_ip
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    event_type,
                    success,
                    json.dumps(details or {}, ensure_ascii=False),
                    _request_ip(request) if request else None,
                ),
            )

    def signup(self, payload: SignupRequest, request: Request) -> dict[str, Any]:
        user_id = str(uuid4())
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT user_id FROM app_users WHERE login_id = ?", (payload.login_id,)
            ).fetchone()
            if existing:
                raise ApiException(409, "LOGIN_ID_ALREADY_REGISTERED", "이미 사용 중인 로그인 ID입니다.")
            if payload.email:
                existing_email = connection.execute(
                    "SELECT user_id FROM app_users WHERE email = ?", (payload.email,)
                ).fetchone()
                if existing_email:
                    raise ApiException(409, "EMAIL_ALREADY_REGISTERED", "이미 등록된 이메일입니다.")
            count = connection.execute(
                "SELECT COUNT(*) AS count FROM app_users"
            ).fetchone()
            first_user = int((count or {}).get("count") or 0) == 0
            bootstrap_admin = is_bootstrap_admin(
                payload.login_id,
                first_user=first_user,
                configured_ids=self.settings.auth_bootstrap_admin_ids,
            )
            status = "ACTIVE" if bootstrap_admin else "PENDING"
            connection.execute(
                """
                INSERT INTO app_users (
                    user_id, login_id, email, password_hash, display_name, status
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    payload.login_id,
                    payload.email,
                    hash_password(payload.password),
                    payload.display_name,
                    status,
                ),
            )
            if bootstrap_admin:
                for role in ROLE_CODES:
                    connection.execute(
                        "INSERT INTO user_roles (user_id, role_code) VALUES (?, ?)",
                        (user_id, role),
                    )
                for project in PROJECT_CODES:
                    connection.execute(
                        """
                        INSERT INTO user_project_permissions (user_id, project_code)
                        VALUES (?, ?)
                        """,
                        (user_id, project),
                    )
        self._audit(
            "SIGNUP",
            True,
            user_id=user_id,
            request=request,
            details={
                "loginId": payload.login_id,
                "status": status,
                "bootstrapAdmin": bootstrap_admin,
            },
        )
        return {"userId": user_id, "status": status}

    def login(
        self, payload: LoginRequest, request: Request
    ) -> tuple[AuthContext, str]:
        with self.database.transaction() as connection:
            user = connection.execute(
                "SELECT * FROM app_users WHERE login_id = ?", (payload.login_id,)
            ).fetchone()

        if not user or not verify_password(payload.password, user["password_hash"]):
            self._audit(
                "LOGIN",
                False,
                request=request,
                details={"loginId": payload.login_id},
            )
            raise ApiException(401, "LOGIN_FAILED", "로그인 ID 또는 비밀번호를 확인하세요.")
        if user["status"] != "ACTIVE":
            self._audit(
                "LOGIN",
                False,
                user_id=user["user_id"],
                request=request,
                details={"status": user["status"]},
            )
            raise ApiException(
                403,
                "ACCOUNT_NOT_ACTIVE",
                "관리자 승인 대기 중이거나 사용이 중지된 계정입니다.",
                details={"status": user["status"]},
            )

        with self.database.transaction() as connection:
            raw_token = secrets.token_urlsafe(48)
            session_id = str(uuid4())
            expires_at = _utc_now() + timedelta(hours=self.settings.auth_session_hours)
            connection.execute(
                """
                INSERT INTO auth_sessions (
                    session_id, user_id, token_hash, expires_at,
                    user_agent, remote_ip
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    user["user_id"],
                    _token_hash(raw_token),
                    expires_at,
                    request.headers.get("User-Agent", "")[:512],
                    _request_ip(request),
                ),
            )
            connection.execute(
                "UPDATE app_users SET last_login_at = ? WHERE user_id = ?",
                (_utc_now(), user["user_id"]),
            )
        context = self.context_from_token(raw_token)
        if context is None:
            raise ApiException(500, "SESSION_CREATE_FAILED", "로그인 세션 생성에 실패했습니다.")
        self._audit("LOGIN", True, user_id=context.user_id, request=request)
        return context, raw_token

    def context_from_token(self, raw_token: str | None) -> AuthContext | None:
        if not raw_token:
            return None
        now = _utc_now()
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT u.user_id, u.login_id, u.email, u.display_name, u.status,
                       s.session_id, s.expires_at, s.sso_authenticated_at
                  FROM auth_sessions s
                  JOIN app_users u ON u.user_id = s.user_id
                 WHERE s.token_hash = ?
                   AND s.revoked_at IS NULL
                   AND s.expires_at > ?
                """,
                (_token_hash(raw_token), now),
            ).fetchone()
            if not row:
                return None
            projects = connection.execute(
                "SELECT project_code FROM user_project_permissions WHERE user_id = ?",
                (row["user_id"],),
            ).fetchall()
            roles = connection.execute(
                "SELECT role_code FROM user_roles WHERE user_id = ?",
                (row["user_id"],),
            ).fetchall()
            identity = connection.execute(
                """
                SELECT provider, subject, employee_id, email, display_name,
                       active, last_authenticated_at
                  FROM sso_identities
                 WHERE user_id = ? AND active = TRUE
                 ORDER BY updated_at DESC LIMIT 1
                """,
                (row["user_id"],),
            ).fetchone()
            connection.execute(
                "UPDATE auth_sessions SET last_seen_at = ? WHERE session_id = ?",
                (now, row["session_id"]),
            )
        return AuthContext(
            user_id=row["user_id"],
            login_id=row["login_id"],
            email=row["email"],
            display_name=row["display_name"],
            status=row["status"],
            session_id=row["session_id"],
            session_expires_at=row["expires_at"],
            sso_authenticated_at=row.get("sso_authenticated_at"),
            projects=frozenset(item["project_code"] for item in projects),
            roles=frozenset(item["role_code"] for item in roles),
            sso_identity=identity,
        )

    def revoke_session(self, context: AuthContext) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE auth_sessions SET revoked_at = ? WHERE session_id = ?",
                (_utc_now(), context.session_id),
            )

    def list_users(self) -> list[dict[str, Any]]:
        with self.database.transaction() as connection:
            users = connection.execute(
                """
                SELECT user_id, login_id, email, display_name, status, last_login_at,
                       created_at, updated_at
                  FROM app_users ORDER BY created_at DESC
                """
            ).fetchall()
            for user in users:
                user["projects"] = [
                    row["project_code"]
                    for row in connection.execute(
                        """
                        SELECT project_code FROM user_project_permissions
                         WHERE user_id = ? ORDER BY project_code
                        """,
                        (user["user_id"],),
                    ).fetchall()
                ]
                user["roles"] = [
                    row["role_code"]
                    for row in connection.execute(
                        """
                        SELECT role_code FROM user_roles
                         WHERE user_id = ? ORDER BY role_code
                        """,
                        (user["user_id"],),
                    ).fetchall()
                ]
                identity = connection.execute(
                    """
                    SELECT provider, employee_id, email, display_name,
                           active, last_authenticated_at
                      FROM sso_identities WHERE user_id = ? LIMIT 1
                    """,
                    (user["user_id"],),
                ).fetchone()
                user["sso"] = identity
        return users

    def update_user(
        self,
        target_user_id: str,
        payload: AdminUserUpdate,
        admin: AuthContext,
        request: Request,
    ) -> dict[str, Any]:
        if target_user_id == admin.user_id and (
            payload.status != "ACTIVE" or "ADMIN" not in payload.roles
        ):
            raise ApiException(
                409,
                "ADMIN_SELF_LOCKOUT",
                "현재 로그인한 관리자 자신의 활성 상태와 관리자 역할은 해제할 수 없습니다.",
            )
        with self.database.transaction() as connection:
            target = connection.execute(
                "SELECT user_id FROM app_users WHERE user_id = ?", (target_user_id,)
            ).fetchone()
            if not target:
                raise ApiException(404, "USER_NOT_FOUND", "회원을 찾을 수 없습니다.")
            connection.execute(
                "UPDATE app_users SET status = ? WHERE user_id = ?",
                (payload.status, target_user_id),
            )
            connection.execute(
                "DELETE FROM user_project_permissions WHERE user_id = ?",
                (target_user_id,),
            )
            for project in payload.projects:
                connection.execute(
                    """
                    INSERT INTO user_project_permissions (
                        user_id, project_code, granted_by
                    ) VALUES (?, ?, ?)
                    """,
                    (target_user_id, project, admin.user_id),
                )
            connection.execute(
                "DELETE FROM user_roles WHERE user_id = ?", (target_user_id,)
            )
            for role in payload.roles:
                connection.execute(
                    """
                    INSERT INTO user_roles (user_id, role_code, granted_by)
                    VALUES (?, ?, ?)
                    """,
                    (target_user_id, role, admin.user_id),
                )
            if payload.status != "ACTIVE":
                connection.execute(
                    """
                    UPDATE auth_sessions SET revoked_at = ?
                     WHERE user_id = ? AND revoked_at IS NULL
                    """,
                    (_utc_now(), target_user_id),
                )
        self._audit(
            "PERMISSION_CHANGE",
            True,
            user_id=target_user_id,
            request=request,
            details={
                "adminUserId": admin.user_id,
                "status": payload.status,
                "projects": payload.projects,
                "roles": payload.roles,
            },
        )
        return {"updated": True, "userId": target_user_id}

    def start_sso(self, context: AuthContext, return_to: str) -> str:
        if not (
            self.settings.sso_issuer_url
            and self.settings.sso_client_id
            and self.settings.sso_client_secret
        ):
            raise ApiException(
                503,
                "SSO_NOT_CONFIGURED",
                "사내 SSO 연결 설정이 아직 완료되지 않았습니다.",
            )
        discovery_url = (
            self.settings.sso_issuer_url + "/.well-known/openid-configuration"
        )
        try:
            discovery = httpx.get(discovery_url, timeout=10.0).raise_for_status().json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ApiException(503, "SSO_DISCOVERY_FAILED", "SSO 설정 조회에 실패했습니다.") from exc
        authorization_endpoint = str(discovery.get("authorization_endpoint") or "")
        if not authorization_endpoint:
            raise ApiException(503, "SSO_DISCOVERY_INVALID", "SSO 인증 주소가 없습니다.")
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = _b64(hashlib.sha256(verifier.encode("ascii")).digest())
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM auth_oidc_states WHERE expires_at <= ?", (_utc_now(),)
            )
            connection.execute(
                """
                INSERT INTO auth_oidc_states (
                    state_hash, user_id, session_id, code_verifier,
                    return_to, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    _token_hash(state),
                    context.user_id,
                    context.session_id,
                    verifier,
                    safe_return_to(return_to, "/account"),
                    _utc_now() + timedelta(minutes=10),
                ),
            )
        params = {
            "response_type": "code",
            "client_id": self.settings.sso_client_id,
            "redirect_uri": self.settings.edge_public_base_url.rstrip("/")
            + "/auth/sso/callback",
            "scope": self.settings.sso_scopes,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return authorization_endpoint + "?" + urlencode(params)

    def finish_sso(self, state: str, code: str, request: Request) -> str:
        state_hash = _token_hash(state)
        with self.database.transaction() as connection:
            oidc_state = connection.execute(
                """
                SELECT * FROM auth_oidc_states
                 WHERE state_hash = ? AND expires_at > ?
                """,
                (state_hash, _utc_now()),
            ).fetchone()
            if not oidc_state:
                raise ApiException(400, "SSO_STATE_INVALID", "SSO 인증 요청이 만료되었거나 유효하지 않습니다.")
            connection.execute(
                "DELETE FROM auth_oidc_states WHERE state_hash = ?", (state_hash,)
            )
        try:
            discovery = httpx.get(
                self.settings.sso_issuer_url + "/.well-known/openid-configuration",
                timeout=10.0,
            ).raise_for_status().json()
            token = httpx.post(
                discovery["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.settings.edge_public_base_url.rstrip("/")
                    + "/auth/sso/callback",
                    "client_id": self.settings.sso_client_id,
                    "client_secret": self.settings.sso_client_secret,
                    "code_verifier": oidc_state["code_verifier"],
                },
                timeout=15.0,
            ).raise_for_status().json()
            userinfo = httpx.get(
                discovery["userinfo_endpoint"],
                headers={"Authorization": f"Bearer {token['access_token']}"},
                timeout=10.0,
            ).raise_for_status().json()
        except (KeyError, httpx.HTTPError, ValueError) as exc:
            self._audit(
                "SSO_LINK",
                False,
                user_id=oidc_state["user_id"],
                request=request,
            )
            raise ApiException(502, "SSO_AUTH_FAILED", "사내 SSO 인증에 실패했습니다.") from exc
        subject = str(userinfo.get("sub") or "").strip()
        if not subject:
            raise ApiException(502, "SSO_USERINFO_INVALID", "SSO 사용자 식별자를 확인할 수 없습니다.")
        employee_id = str(
            userinfo.get("employee_id")
            or userinfo.get("employeeId")
            or userinfo.get("preferred_username")
            or ""
        ).strip()[:100]
        now = _utc_now()
        with self.database.transaction() as connection:
            owned = connection.execute(
                """
                SELECT user_id FROM sso_identities
                 WHERE provider = ? AND subject = ?
                """,
                (self.settings.sso_provider_name, subject),
            ).fetchone()
            if owned and owned["user_id"] != oidc_state["user_id"]:
                raise ApiException(409, "SSO_ALREADY_LINKED", "이 SSO 계정은 다른 회원과 연결되어 있습니다.")
            connection.execute(
                "DELETE FROM sso_identities WHERE user_id = ? AND provider = ?",
                (oidc_state["user_id"], self.settings.sso_provider_name),
            )
            connection.execute(
                """
                INSERT INTO sso_identities (
                    identity_id, user_id, provider, subject, employee_id,
                    email, display_name, active, last_authenticated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, TRUE, ?)
                """,
                (
                    str(uuid4()),
                    oidc_state["user_id"],
                    self.settings.sso_provider_name,
                    subject,
                    employee_id or None,
                    str(userinfo.get("email") or "")[:255] or None,
                    str(userinfo.get("name") or userinfo.get("preferred_username") or "")[:100]
                    or None,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE auth_sessions SET sso_authenticated_at = ?
                 WHERE session_id = ? AND user_id = ? AND revoked_at IS NULL
                """,
                (now, oidc_state["session_id"], oidc_state["user_id"]),
            )
        self._audit(
            "SSO_LINK",
            True,
            user_id=oidc_state["user_id"],
            request=request,
            details={"provider": self.settings.sso_provider_name},
        )
        return safe_return_to(oidc_state["return_to"], "/account")


def _context_payload(context: AuthContext, settings: Settings) -> dict[str, Any]:
    recent_cutoff = _utc_now() - timedelta(minutes=settings.auth_recent_sso_minutes)
    recent_sso = bool(
        context.sso_authenticated_at and context.sso_authenticated_at >= recent_cutoff
    )
    return {
        "userId": context.user_id,
        "loginId": context.login_id,
        "email": context.email,
        "displayName": context.display_name,
        "status": context.status,
        "projects": sorted(context.projects),
        "roles": sorted(context.roles),
        "ssoLinked": bool(context.sso_identity),
        "ssoRecentlyAuthenticated": recent_sso,
        "ssoProvider": settings.sso_provider_name,
    }


def _is_api(path: str) -> bool:
    return path.startswith("/api/")


def _admin_path(path: str) -> bool:
    return path in {"/operations", "/errors", "/report-management", "/admin/users"} or path.startswith(
        ("/api/v1/usage-events", "/api/v1/report-management")
    ) or (path.startswith("/api/v1/errors") and not path.endswith("/comments"))


def _public_path(request: Request) -> bool:
    path = request.url.path
    if path in {"/login", "/signup", "/health", "/health/llm", "/openapi.json", "/docs", "/redoc"}:
        return True
    if path.startswith(("/auth/sso/callback", "/api/v1/auth/signup", "/api/v1/auth/login")):
        return True
    if path.startswith("/error-feedback/"):
        return True
    if path.startswith("/api/v1/errors/") and path.endswith("/comments") and request.method == "POST":
        return True
    # 실험 PC/C# 인터페이스는 브라우저 회원 인증과 분리한다.
    if path == "/api/v1/jobs" or path == "/api/v1/requests" or path.startswith("/api/v1/jobs/"):
        return True
    return False


def require_context(request: Request) -> AuthContext:
    context = getattr(request.state, "auth_context", None)
    if not isinstance(context, AuthContext):
        raise ApiException(401, "AUTHENTICATION_REQUIRED", "로그인이 필요합니다.")
    return context


def authenticated_transfer_payload(
    request: Request,
    payload: PreviewReportSendRequest,
    project_code: str,
) -> PreviewReportSendRequest:
    settings = getattr(request.app.state, "settings", None)
    if not settings or not getattr(settings, "auth_enabled", False):
        return payload
    context = require_context(request)
    project = project_code.strip().upper().replace("-", "")
    if not context.is_admin and project not in context.projects:
        raise ApiException(403, "PROJECT_ACCESS_DENIED", "이 프로젝트의 보고서 전송 권한이 없습니다.")
    if "REPORT_SENDER" not in context.roles:
        raise ApiException(403, "REPORT_SEND_PERMISSION_REQUIRED", "관리자에게 보고서 전송 권한을 요청하세요.")
    if not context.sso_identity:
        raise ApiException(
            403,
            "SSO_LINK_REQUIRED",
            "보고서 전송 전에 사내 SSO 계정을 연결해야 합니다.",
            details={"reauthUrl": "/auth/sso/start?" + urlencode({"returnTo": f"/{project.lower()}"})},
        )
    cutoff = _utc_now() - timedelta(minutes=settings.auth_recent_sso_minutes)
    if not context.sso_authenticated_at or context.sso_authenticated_at < cutoff:
        raise ApiException(
            401,
            "SSO_REAUTH_REQUIRED",
            "보고서 전송을 위해 사내 SSO 인증을 다시 진행하세요.",
            details={"reauthUrl": "/auth/sso/start?" + urlencode({"returnTo": f"/{project.lower()}"})},
        )
    identity = context.sso_identity
    operator_id = str(
        identity.get("employee_id") or identity.get("subject") or context.login_id
    )[:100]
    return payload.model_copy(update={"operator_id": operator_id})


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>
*{{box-sizing:border-box}} body{{margin:0;background:#f5f7fa;color:#172b4d;font:15px/1.5 system-ui,-apple-system,sans-serif;letter-spacing:0}}
main{{max-width:1080px;margin:0 auto;padding:40px 20px}} .panel{{background:#fff;border:1px solid #d7dee8;border-radius:8px;padding:24px}}
h1{{font-size:28px;margin:0 0 8px}} h2{{font-size:18px;margin:22px 0 10px}} p{{color:#526277}}
label{{display:block;font-weight:650;margin:14px 0 5px}} input,select{{width:100%;min-height:42px;border:1px solid #aebbd0;border-radius:6px;padding:9px 11px;font:inherit}}
button,.button{{display:inline-flex;align-items:center;justify-content:center;min-height:42px;border:1px solid #1769aa;border-radius:6px;background:#1769aa;color:#fff;padding:8px 15px;font-weight:700;text-decoration:none;cursor:pointer}}
.secondary{{background:#fff;color:#1769aa}} .row{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}} .muted{{color:#6b778c}} .error{{color:#b42318;white-space:pre-wrap}}
.user{{border-top:1px solid #e2e7ef;padding:16px 0}} .checks{{display:flex;gap:14px;flex-wrap:wrap}} .checks label{{font-weight:500;margin:0}} .checks input{{width:auto;min-height:auto}}
@media(max-width:640px){{main{{padding:22px 14px}}.panel{{padding:18px}}h1{{font-size:23px}}}}
</style></head><body><main>{body}</main></body></html>"""


def _login_page(return_to: str) -> str:
    body = f"""<section class="panel"><h1>RIST Edge 로그인</h1><p>승인받은 프로젝트에서 분석하고 보고서를 생성할 수 있습니다.</p>
<form id="form"><label>로그인 ID</label><input name="loginId" autocomplete="username" minlength="3" maxlength="255" required>
<label>비밀번호</label><input name="password" type="password" autocomplete="current-password" required>
<p id="message" class="error"></p><div class="row"><button>로그인</button><a class="button secondary" href="/signup">회원가입</a></div></form></section>
<script>document.getElementById('form').onsubmit=async(e)=>{{e.preventDefault();const f=new FormData(e.target);const r=await fetch('/api/v1/auth/login',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{loginId:f.get('loginId'),password:f.get('password'),returnTo:{json.dumps(return_to)}}})}});const d=await r.json();if(!r.ok){{message.textContent=d.message||'로그인에 실패했습니다.';return}}location.href=d.returnTo||'/';}};</script>"""
    return _page("RIST Edge 로그인", body)


def _signup_page() -> str:
    body = """<section class="panel"><h1>회원가입</h1><p>가입 후 관리자가 FTIR, Raman, XRD, TEM 접근 권한을 승인합니다. SSO 연결 전에도 승인된 프로젝트의 보고서 생성은 가능합니다.</p>
<form id="form"><label>이름</label><input name="displayName" maxlength="100" required><label>로그인 ID</label><input name="loginId" minlength="3" maxlength="64" pattern="[A-Za-z0-9._-]+" autocomplete="username" required><p class="muted">영문, 숫자, 점, 밑줄, 하이픈을 사용할 수 있습니다.</p><label>이메일 (선택)</label><input name="email" type="email" autocomplete="email">
<label>비밀번호</label><input name="password" type="password" minlength="10" required><p class="muted">10자 이상으로 입력하세요.</p>
<p id="message"></p><div class="row"><button>가입 신청</button><a class="button secondary" href="/login">로그인</a></div></form></section>
<script>const form=document.getElementById('form'),message=document.getElementById('message');form.onsubmit=async(e)=>{e.preventDefault();const f=new FormData(form),body=Object.fromEntries(f);if(!body.email)delete body.email;const r=await fetch('/api/v1/auth/signup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();message.className=r.ok?'muted':'error';message.textContent=r.ok?(d.status==='ACTIVE'?'가입되었습니다. 로그인하세요.':'가입 신청이 완료되었습니다. 관리자 승인을 기다려 주세요.'):(d.message||'가입에 실패했습니다.');};</script>"""
    return _page("RIST Edge 회원가입", body)


def _account_page(context: AuthContext, settings: Settings) -> str:
    data = _context_payload(context, settings)
    project_links = "".join(
        f'<a class="button secondary" href="/{project.lower() if project != "FTIR" else "ftir"}">{escape(project)}</a>'
        for project in PROJECT_CODES
        if context.is_admin or project in context.projects
    ) or '<span class="muted">아직 승인된 프로젝트가 없습니다.</span>'
    sso_text = "연결됨" if data["ssoLinked"] else "연결 안 됨"
    email_text = f" · {escape(context.email)}" if context.email else ""
    body = f"""<section class="panel"><div class="row" style="justify-content:space-between"><div><h1>{escape(context.display_name)}</h1><p>로그인 ID: <strong>{escape(context.login_id)}</strong>{email_text}</p></div><button class="secondary" id="logout">로그아웃</button></div>
<h2>승인된 프로젝트</h2><div class="row">{project_links}</div><h2>보고서 전송 인증</h2><p>사내 SSO: <strong>{sso_text}</strong><br>최근 전송 인증: <strong>{'유효' if data['ssoRecentlyAuthenticated'] else '재인증 필요'}</strong></p>
<a class="button" href="/auth/sso/start?returnTo=/account">{escape(settings.sso_provider_name)} 연결 / 재인증</a>
{'<h2>관리</h2><div class="row"><a class="button secondary" href="/admin/users">회원·권한 관리</a><a class="button secondary" href="/operations">운영 관리</a></div>' if context.is_admin else ''}
</section><script>document.getElementById('logout').onclick=async()=>{{await fetch('/api/v1/auth/logout',{{method:'POST'}});location.href='/login';}};</script>"""
    return _page("내 계정", body)


def _admin_page() -> str:
    body = """<section class="panel"><div class="row" style="justify-content:space-between"><div><h1>회원·권한 관리</h1><p>로컬 회원 승인, 프로젝트 접근, 보고서 전송 역할을 관리합니다.</p></div><a class="button secondary" href="/account">내 계정</a></div><div id="users"></div><p id="message"></p></section>
<script>
const projects=['FTIR','RAMAN','XRD','TEM'],roles=['ADMIN','REPORT_SENDER'],users=document.getElementById('users'),message=document.getElementById('message');
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function load(){const r=await fetch('/api/v1/auth/admin/users');const d=await r.json();if(!r.ok){message.textContent=d.message;return}users.innerHTML=d.items.map(u=>`<article class="user" data-id="${esc(u.userId)}"><strong>${esc(u.displayName)}</strong> <span class="muted">ID: ${esc(u.loginId)}${u.email?' · '+esc(u.email):''}</span><div class="row"><select class="status" style="width:auto">${['PENDING','ACTIVE','SUSPENDED'].map(x=>`<option ${x===u.status?'selected':''}>${x}</option>`).join('')}</select><div class="checks">${projects.map(x=>`<label><input class="project" type="checkbox" value="${x}" ${u.projects.includes(x)?'checked':''}> ${x}</label>`).join('')}</div><div class="checks">${roles.map(x=>`<label><input class="role" type="checkbox" value="${x}" ${u.roles.includes(x)?'checked':''}> ${x}</label>`).join('')}</div><button onclick="save(this)">저장</button></div><small class="muted">SSO ${u.sso?'연결됨':'미연결'}</small></article>`).join('')}
async function save(btn){const el=btn.closest('.user');const body={status:el.querySelector('.status').value,projects:[...el.querySelectorAll('.project:checked')].map(x=>x.value),roles:[...el.querySelectorAll('.role:checked')].map(x=>x.value)};const r=await fetch('/api/v1/auth/admin/users/'+el.dataset.id,{method:'PATCH',headers:{'Content-Type':'application/json','X-Requested-With':'RIST-Admin'},body:JSON.stringify(body)});const d=await r.json();message.className=r.ok?'muted':'error';message.textContent=r.ok?'저장되었습니다.':d.message;}
load();
</script>"""
    return _page("회원·권한 관리", body)


def install_auth(app: FastAPI, settings: Settings, database: Database) -> None:
    service = AuthService(settings, database)
    app.state.auth_service = service
    router = APIRouter()

    @app.middleware("http")
    async def authentication_middleware(request: Request, call_next: Any) -> Response:
        if not settings.auth_enabled:
            return await call_next(request)
        context = service.context_from_token(request.cookies.get(SESSION_COOKIE))
        request.state.auth_context = context
        if _public_path(request):
            return await call_next(request)
        if context is None or context.status != "ACTIVE":
            if _is_api(request.url.path):
                return error_response(request, ApiException(401, "AUTHENTICATION_REQUIRED", "로그인이 필요합니다."))
            return RedirectResponse(
                "/login?" + urlencode({"returnTo": safe_return_to(request.url.path)}),
                status_code=303,
            )
        if _admin_path(request.url.path) and not context.is_admin:
            if _is_api(request.url.path):
                return error_response(request, ApiException(403, "ADMIN_REQUIRED", "관리자 권한이 필요합니다."))
            return HTMLResponse(_page("접근 거부", '<section class="panel"><h1>접근 권한이 없습니다.</h1><a class="button" href="/account">내 계정</a></section>'), status_code=403)
        project = project_for_path(request.url.path)
        if project and not context.is_admin and project not in context.projects:
            if _is_api(request.url.path):
                return error_response(request, ApiException(403, "PROJECT_ACCESS_DENIED", "관리자에게 프로젝트 접근 권한을 요청하세요."))
            return HTMLResponse(_page("승인 필요", '<section class="panel"><h1>프로젝트 승인이 필요합니다.</h1><p>관리자에게 접근 권한을 요청하세요.</p><a class="button" href="/account">내 계정</a></section>'), status_code=403)
        return await call_next(request)

    @router.get("/login", response_class=HTMLResponse, include_in_schema=False)
    def login_page(request: Request, returnTo: str = "/") -> Response:
        if getattr(request.state, "auth_context", None):
            return RedirectResponse(safe_return_to(returnTo), status_code=303)
        return HTMLResponse(_login_page(safe_return_to(returnTo)))

    @router.get("/signup", response_class=HTMLResponse, include_in_schema=False)
    def signup_page() -> HTMLResponse:
        return HTMLResponse(_signup_page())

    @router.get("/account", response_class=HTMLResponse, include_in_schema=False)
    def account_page(request: Request) -> HTMLResponse:
        return HTMLResponse(_account_page(require_context(request), settings))

    @router.get("/admin/users", response_class=HTMLResponse, include_in_schema=False)
    def admin_page() -> HTMLResponse:
        return HTMLResponse(_admin_page())

    @router.post("/api/v1/auth/signup", tags=["auth"])
    def signup(payload: SignupRequest, request: Request) -> dict[str, Any]:
        return service.signup(payload, request)

    @router.post("/api/v1/auth/login", tags=["auth"])
    def login(payload: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
        context, raw_token = service.login(payload, request)
        response.set_cookie(
            SESSION_COOKIE,
            raw_token,
            httponly=True,
            secure=settings.auth_cookie_secure,
            samesite="lax",
            max_age=settings.auth_session_hours * 3600,
            path="/",
        )
        return {
            "authenticated": True,
            "returnTo": safe_return_to(payload.return_to),
            "user": _context_payload(context, settings),
        }

    @router.post("/api/v1/auth/logout", tags=["auth"])
    def logout(request: Request, response: Response) -> dict[str, bool]:
        context = require_context(request)
        service.revoke_session(context)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"loggedOut": True}

    @router.get("/api/v1/auth/me", tags=["auth"])
    def me(request: Request) -> dict[str, Any]:
        return _context_payload(require_context(request), settings)

    @router.get("/api/v1/auth/admin/users", tags=["auth"])
    def admin_users(request: Request) -> dict[str, Any]:
        context = require_context(request)
        if not context.is_admin:
            raise ApiException(403, "ADMIN_REQUIRED", "관리자 권한이 필요합니다.")
        items = []
        for user in service.list_users():
            items.append(
                {
                    "userId": user["user_id"],
                    "loginId": user["login_id"],
                    "email": user["email"],
                    "displayName": user["display_name"],
                    "status": user["status"],
                    "projects": user["projects"],
                    "roles": user["roles"],
                    "sso": user["sso"],
                    "lastLoginAt": user["last_login_at"],
                    "createdAt": user["created_at"],
                }
            )
        return {"items": items}

    @router.patch("/api/v1/auth/admin/users/{user_id}", tags=["auth"])
    def update_admin_user(
        user_id: str, payload: AdminUserUpdate, request: Request
    ) -> dict[str, Any]:
        context = require_context(request)
        if not context.is_admin:
            raise ApiException(403, "ADMIN_REQUIRED", "관리자 권한이 필요합니다.")
        if request.headers.get("X-Requested-With") != "RIST-Admin":
            raise ApiException(403, "CSRF_CHECK_FAILED", "관리 화면 요청을 확인할 수 없습니다.")
        return service.update_user(user_id, payload, context, request)

    @router.get("/auth/sso/start", include_in_schema=False)
    def sso_start(request: Request, returnTo: str = "/account") -> RedirectResponse:
        context = require_context(request)
        return RedirectResponse(service.start_sso(context, returnTo), status_code=303)

    @router.get("/auth/sso/callback", include_in_schema=False)
    def sso_callback(request: Request, state: str = "", code: str = "", error: str = "") -> RedirectResponse:
        if error:
            raise ApiException(401, "SSO_AUTH_CANCELLED", "SSO 인증이 취소되었거나 실패했습니다.")
        if not state or not code:
            raise ApiException(400, "SSO_CALLBACK_INVALID", "SSO 인증 응답이 올바르지 않습니다.")
        return RedirectResponse(service.finish_sso(state, code, request), status_code=303)

    app.include_router(router)

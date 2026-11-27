from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import auth, sso_policy
from app.auth import (AuthService, SsoPolicyUpdate, PoscoSsoVerifyRequest, _context_payload,
                      LoginRequest, PasswordChangeRequest, AdminUserUpdate)
from app.config import Settings
from app.errors import ApiException, api_exception_handler
from app.preview_report import PreviewReportSendRequest


class Result:
    def __init__(self, cursor): self.cursor = cursor
    def fetchone(self):
        row = self.cursor.fetchone()
        return dict(row) if row is not None else None
    def fetchall(self): return [dict(row) for row in self.cursor.fetchall()]


class Store:
    """Execute the service SQL against real state; adapt only MariaDB syntax."""
    def __init__(self):
        self.db = sqlite3.connect(":memory:", detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
CREATE TABLE app_users(user_id TEXT PRIMARY KEY, login_id TEXT, email TEXT, display_name TEXT, status TEXT, password_hash TEXT, last_login_at TIMESTAMP, updated_at TIMESTAMP);
CREATE TABLE auth_sessions(session_id TEXT PRIMARY KEY,user_id TEXT,token_hash TEXT,expires_at TIMESTAMP,sso_authenticated_at TIMESTAMP,revoked_at TIMESTAMP,last_seen_at TIMESTAMP,user_agent TEXT,remote_ip TEXT);
CREATE TABLE user_project_permissions(user_id TEXT,project_code TEXT,granted_by TEXT);
CREATE TABLE user_roles(user_id TEXT,role_code TEXT,granted_by TEXT);
CREATE TABLE sso_identities(identity_id TEXT,user_id TEXT,provider TEXT,subject TEXT,employee_id TEXT,email TEXT,display_name TEXT,active INTEGER,last_authenticated_at TIMESTAMP,updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE auth_oidc_states(state_hash TEXT,user_id TEXT,session_id TEXT,code_verifier TEXT,return_to TEXT,expires_at TIMESTAMP);
CREATE TABLE auth_audit_events(user_id TEXT,event_type TEXT,success INTEGER,details_json TEXT,remote_ip TEXT);
CREATE TABLE auth_sso_policy(policy_id INTEGER PRIMARY KEY,valid_minutes INTEGER,updated_by TEXT,updated_at TIMESTAMP);
CREATE TABLE auth_sso_resets(user_id TEXT PRIMARY KEY,generation INTEGER,reset_at TIMESTAMP,reset_by TEXT);
CREATE TABLE auth_sso_grants(user_id TEXT PRIMARY KEY,provider TEXT,subject TEXT,generation INTEGER,authenticated_at TIMESTAMP);
""")
    def execute(self, query, params=()):
        query = query.replace("INSERT IGNORE", "INSERT OR IGNORE").replace(" FOR UPDATE", "")
        return Result(self.db.execute(query, params))
    @contextmanager
    def transaction(self):
        with self.db: yield self


@pytest.fixture
def setup(tmp_path, monkeypatch):
    now = datetime(2026, 11, 24, 9, 0, 0)
    monkeypatch.setattr(auth, "_utc_now", lambda: now)
    monkeypatch.setattr(sso_policy, "utc_now", lambda: now)
    store = Store()
    password_hash = auth.hash_password("local-password-before")
    for user in ("admin", "alice", "bob"):
        store.execute("INSERT INTO app_users(user_id,login_id,display_name,status,password_hash) VALUES (?, ?, ?, 'ACTIVE', ?)", (user,user,user,password_hash))
        store.execute("INSERT INTO user_roles(user_id,role_code) VALUES (?, 'REPORT_SENDER')", (user,))
        store.execute("INSERT INTO user_project_permissions(user_id,project_code) VALUES (?, 'XRD')", (user,))
        store.execute("INSERT INTO sso_identities VALUES (?, ?, 'RIST SSO', ?, ?, NULL, NULL, TRUE, ?, ?)",
                      (user,user,user,user,now-timedelta(minutes=5),now))
        store.execute("INSERT INTO auth_sso_grants VALUES (?, 'RIST SSO', ?, 0, ?)", (user,user,now-timedelta(minutes=5)))
        for n in (1,2):
            token = user + str(n)
            store.execute("INSERT INTO auth_sessions(session_id,user_id,token_hash,expires_at,sso_authenticated_at) VALUES (?, ?, ?, ?, ?)",
                          (token,user,auth._token_hash(token),now+timedelta(hours=8),now-timedelta(minutes=5)))
    store.execute("INSERT INTO user_roles(user_id,role_code) VALUES ('admin', 'ADMIN')")
    store.db.commit()
    settings = Settings(storage_root=tmp_path, auth_enabled=True, sso_mode="lims", auth_recent_sso_minutes=30,
                        edge_public_base_url="https://edge.test")
    service = AuthService(settings, store, lims_sso_client=SimpleNamespace(validate=lambda *args: True))
    request = Request({"type":"http","method":"POST","path":"/","headers":[],"client":("127.0.0.1",1234)})
    return service, store, settings, request, now


def context(service, who="alice", session=1): return service.context_from_token(who+str(session))


def test_policy_persists_and_controls_expiry_without_restarting(setup):
    service, store, settings, request, now = setup
    assert service.get_sso_policy()["validMinutes"] == 30
    service.update_sso_policy(SsoPolicyUpdate(validMinutes=120), context(service,"admin"), request)
    another_worker = AuthService(settings, store)
    assert another_worker.get_sso_policy()["validMinutes"] == 120
    result = _context_payload(context(another_worker), settings)
    assert result["ssoExpiresAt"] == "2026-11-24T10:55:00Z"
    assert result["ssoRecentlyAuthenticated"] is True
    service.update_sso_policy(SsoPolicyUpdate(validMinutes=1), context(service,"admin"), request)
    assert _context_payload(context(service), settings)["ssoRecentlyAuthenticated"] is False
    service.update_sso_policy(SsoPolicyUpdate(validMinutes=120), context(service,"admin"), request)
    assert _context_payload(context(service), settings)["ssoRecentlyAuthenticated"] is False


def test_larger_policy_cannot_revive_an_already_expired_account_proof(setup):
    service, store, settings, request, now = setup
    expired = now-timedelta(minutes=31)
    store.execute("UPDATE auth_sessions SET sso_authenticated_at = ? WHERE user_id='alice'", (expired,))
    store.execute("UPDATE sso_identities SET last_authenticated_at = ? WHERE user_id='alice'", (expired,))
    store.execute("UPDATE auth_sso_grants SET authenticated_at=? WHERE user_id='alice'", (expired,))
    service.update_sso_policy(SsoPolicyUpdate(validMinutes=60), context(service,"admin"), request)
    assert context(service).sso_authenticated_at is None
    assert _context_payload(context(service,session=2), settings)["ssoRecentlyAuthenticated"] is False
    assert _context_payload(context(service,"bob"), settings)["ssoRecentlyAuthenticated"] is True


def test_account_expiry_is_independent_but_active_local_session_is_required(setup):
    service, _, settings, _, now = setup
    current = replace(context(service), sso_valid_minutes=43200, session_expires_at=now+timedelta(minutes=10))
    assert _context_payload(current, settings)["ssoExpiresAt"] == "2026-12-24T08:55:00Z"
    assert _context_payload(current, settings)["ssoRecentlyAuthenticated"] is True
    assert _context_payload(replace(current, session_expires_at=now), settings)["ssoRecentlyAuthenticated"] is False
    expired = replace(current, sso_authenticated_at=now-timedelta(minutes=30), sso_valid_minutes=30)
    assert _context_payload(expired, settings)["ssoRecentlyAuthenticated"] is False


@pytest.mark.parametrize("minutes", [0,-1,43201,True,2.5,"60"])
def test_invalid_policy_rejected(minutes):
    with pytest.raises(ValueError): SsoPolicyUpdate(validMinutes=minutes)


def test_self_reset_revokes_every_sso_session_only_for_owner(setup):
    service, store, settings, request, now = setup
    store.execute("INSERT INTO auth_oidc_states VALUES ('pending','alice','alice1','old','/account',?)", (now+timedelta(minutes=10),))
    result = service.reset_sso("alice", context(service), request)
    assert result["allSessions"] is True
    for session in (1,2):
        user = context(service, session=session)
        assert user is not None  # Local login and permissions survive.
        assert user.sso_identity is None and user.sso_authenticated_at is None
        assert "REPORT_SENDER" in user.roles and "XRD" in user.projects
    assert context(service,"bob").sso_identity is not None
    assert store.execute("SELECT * FROM auth_oidc_states WHERE user_id='alice'").fetchone() is None
    audit=store.execute("SELECT * FROM auth_audit_events WHERE event_type='SSO_RESET'").fetchone()
    assert json.loads(audit["details_json"]) == {"actorUserId":"alice", "allSessions":True}


def test_only_admin_can_reset_someone_else_or_change_policy(setup):
    service, _, _, request, _ = setup
    with pytest.raises(ApiException) as denied: service.reset_sso("bob", context(service), request)
    assert denied.value.status_code == 403
    with pytest.raises(ApiException): service.update_sso_policy(SsoPolicyUpdate(validMinutes=60), context(service), request)
    service.reset_sso("bob", context(service,"admin"), request)
    assert context(service,"bob").sso_identity is None
    with pytest.raises(ApiException) as missing: service.reset_sso("missing", context(service,"admin"), request)
    assert missing.value.status_code == 404


def test_reset_wins_over_in_flight_password_validation_and_allows_fresh_retry(setup):
    service, _, settings, request, now = setup
    stale = context(service)
    def reset_then_accept(*args):
        service.reset_sso("alice", context(service,"admin"), request)
        return True
    service.lims_sso_client = SimpleNamespace(validate=reset_then_accept)
    payload=PoscoSsoVerifyRequest(username="alice",password="not-logged")
    with pytest.raises(ApiException) as error: service.verify_posco_sso(stale,payload,request)
    assert error.value.code == "SSO_RESET_DURING_AUTH"
    assert context(service).sso_identity is None
    service.lims_sso_client = SimpleNamespace(validate=lambda *args:True)
    service.verify_posco_sso(context(service),payload,request)
    assert _context_payload(context(service),settings)["ssoRecentlyAuthenticated"] is True
    assert context(service,session=2).sso_authenticated_at == now
    assert _context_payload(context(service,session=2),settings)["ssoRecentlyAuthenticated"] is True


def test_report_authorization_refreshes_after_reset(setup):
    service, _, settings, request, _ = setup
    app=FastAPI();app.state.settings=settings;app.state.auth_service=service
    report_request=Request({"type":"http","method":"POST","path":"/send","headers":[(b"cookie",b"rist_session=alice1")],"app":app})
    report_request.state.auth_context=context(service)
    service.reset_sso("alice",context(service,"admin"),request)
    payload=PreviewReportSendRequest(requestNumber="REQ",experimentCode="XRD",equipmentCode="XRD",operatorId="ignored")
    with pytest.raises(ApiException) as error: auth.authenticated_transfer_payload(report_request,payload,"XRD")
    assert error.value.code == "SSO_LINK_REQUIRED"


def test_api_permissions_csrf_and_persistence(setup):
    service, store, settings, _, _ = setup
    app=FastAPI();app.add_exception_handler(ApiException,api_exception_handler);auth.install_auth(app,settings,store)
    with TestClient(app,base_url="https://edge.test") as client:
        assert client.get('/api/v1/auth/admin/sso-policy').status_code==401
        client.cookies.set(auth.SESSION_COOKIE,'alice1')
        assert client.patch('/api/v1/auth/admin/sso-policy',json={"validMinutes":60},headers={"X-Requested-With":"RIST-Admin"}).status_code==403
        assert client.post('/api/v1/auth/admin/users/bob/sso/reset',headers={"X-Requested-With":"RIST-Admin"}).status_code==403
        assert client.post('/api/v1/auth/sso/reset').status_code==403
        assert client.post('/api/v1/auth/sso/reset',headers={"X-Requested-With":"RIST-Account"}).status_code==200
        assert client.get('/api/v1/auth/me').json()['ssoLinked'] is False
        client.cookies.set(auth.SESSION_COOKIE,'admin1')
        assert client.patch('/api/v1/auth/admin/sso-policy',json={"validMinutes":60}).status_code==403
        response=client.patch('/api/v1/auth/admin/sso-policy',json={"validMinutes":60},headers={"X-Requested-With":"RIST-Admin"})
        assert response.status_code==200 and response.json()['validMinutes']==60
        assert client.post('/api/v1/auth/admin/users/bob/sso/reset',headers={"X-Requested-With":"RIST-Admin"}).status_code==200


def test_account_and_admin_expose_controls_and_persistent_success(setup):
    service, _, settings, _, _ = setup
    account=auth._account_page(context(service),settings)
    assert 'id="sso-reset"' in account and 'id="sso-expires-at"' in account
    assert "setTimeout(()=>location.href=data.returnTo" not in account
    admin=auth._admin_page()
    for element in ['sso-policy-minutes','sso-policy-save','drawer-sso-reset','bulk-sso-reset']:
        assert f'id="{element}"' in admin


@pytest.mark.parametrize("verifier", ["g0.test-pkce-verifier", "legacy-pkce-verifier"])
def test_reset_wins_over_in_flight_oidc_callback(setup, monkeypatch, verifier):
    service, store, _, request, now = setup
    state="oidc-state"
    store.execute("INSERT INTO auth_oidc_states VALUES (?, 'alice','alice1',?, '/account',?)",
                  (auth._token_hash(state),verifier,now+timedelta(minutes=10)))
    class Reply:
        def __init__(self, data): self.data=data
        def raise_for_status(self): return self
        def json(self): return self.data
    def get(url, **kwargs):
        if url.endswith('/.well-known/openid-configuration'):
            return Reply({"token_endpoint":"https://sso.invalid/token","userinfo_endpoint":"https://sso.invalid/user"})
        service.reset_sso("alice",context(service,"admin"),request)
        return Reply({"sub":"alice","employee_id":"alice"})
    monkeypatch.setattr(auth.httpx,'get',get)
    monkeypatch.setattr(auth.httpx,'post',lambda *args,**kwargs:Reply({"access_token":"synthetic-test-token"}))
    with pytest.raises(ApiException) as error: service.finish_sso(state,'synthetic-code',request)
    assert error.value.code=='SSO_RESET_DURING_AUTH'
    assert context(service).sso_identity is None


def test_schema_migration_matches_additive_application_tables():
    from pathlib import Path
    from app.database import _SCHEMA
    migration=(Path(__file__).resolve().parents[1]/'deploy/mariadb_sso_policy_migration.sql').read_text()
    for name in ('auth_sso_policy','auth_sso_resets','auth_sso_grants'):
        statement=next(x.strip() for x in _SCHEMA if 'CREATE TABLE IF NOT EXISTS '+name+' (' in x)
        assert statement+';' in migration
    assert 'ALTER TABLE' not in migration and 'DROP TABLE' not in migration


# Account SSO reuse is tested against real SQL state and real local password login.
def _advance(monkeypatch, now):
    monkeypatch.setattr(auth, "_utc_now", lambda: now)
    monkeypatch.setattr(sso_policy, "utc_now", lambda: now)


def _login(service, request, password="local-password-before"):
    return service.login(LoginRequest(loginId="alice", password=password), request)


def _send(service, settings, token):
    app = FastAPI()
    app.state.settings = settings
    app.state.auth_service = service
    req = Request({"type":"http", "method":"POST", "path":"/send",
                   "headers":[(b"cookie", ("rist_session="+token).encode())], "app":app})
    req.state.auth_context = service.context_from_token(token)
    return auth.authenticated_transfer_payload(req, PreviewReportSendRequest(
        requestNumber="REQ", experimentCode="XRD", equipmentCode="XRD", operatorId="ignored"), "XRD")


@pytest.mark.parametrize("logout", [False, True])
def test_weekly_sso_survives_fresh_login_without_renewing_deadline(setup, monkeypatch, logout):
    service, store, settings, request, start = setup
    service.update_sso_policy(SsoPolicyUpdate(validMinutes=10080), context(service,"admin"), request)
    service.verify_posco_sso(context(service), PoscoSsoVerifyRequest(username="alice",password="test-sso"), request)
    if logout:
        service.revoke_session(context(service))
        assert service.context_from_token("alice1") is None
    # Both the old cookie and its login session expire; a real local login is required.
    _advance(monkeypatch, start+timedelta(days=1))
    assert service.context_from_token("alice1") is None
    fresh, token = _login(service, request)
    data = _context_payload(fresh, settings)
    assert data["ssoRecentlyAuthenticated"] is True
    assert data["ssoAuthenticatedAt"] == start.isoformat()+"Z"
    assert data["ssoExpiresAt"] == (start+timedelta(days=7)).isoformat()+"Z"
    assert store.execute("SELECT sso_authenticated_at FROM auth_sessions WHERE session_id=?", (fresh.session_id,)).fetchone()["sso_authenticated_at"] is None
    assert _send(service, settings, token).operator_id == "alice"
    # Logging in again at the exact deadline cannot extend the original proof.
    _advance(monkeypatch, start+timedelta(days=7))
    expired, token = _login(service, request)
    assert _context_payload(expired, settings)["ssoRecentlyAuthenticated"] is False
    with pytest.raises(ApiException) as error:
        _send(service, settings, token)
    assert error.value.code == "SSO_REAUTH_REQUIRED"


@pytest.mark.parametrize("missing", ["identity", "proof", "timestamp_mismatch", "provider", "subject", "generation", "future"])
def test_relogin_never_restores_missing_or_mismatched_account_proof(setup, missing):
    service, store, settings, request, now = setup
    if missing == "identity":
        store.execute("UPDATE sso_identities SET active=FALSE WHERE user_id='alice'")
    elif missing == "proof":
        store.execute("DELETE FROM auth_sso_grants WHERE user_id='alice'")
    elif missing == "timestamp_mismatch":
        store.execute("UPDATE sso_identities SET last_authenticated_at=? WHERE user_id='alice'", (now,))
    elif missing == "provider":
        service = AuthService(replace(settings, sso_provider_name="Other provider"), store)
        settings = service.settings
    elif missing == "subject":
        store.execute("UPDATE auth_sso_grants SET subject='other' WHERE user_id='alice'")
    elif missing == "generation":
        store.execute("UPDATE auth_sso_grants SET generation=99 WHERE user_id='alice'")
    else:
        store.execute("UPDATE auth_sso_grants SET authenticated_at=? WHERE user_id='alice'", (now+timedelta(minutes=1),))
        store.execute("UPDATE auth_sessions SET sso_authenticated_at=? WHERE user_id='alice'", (now+timedelta(minutes=1),))
        store.execute("UPDATE sso_identities SET last_authenticated_at=? WHERE user_id='alice'", (now+timedelta(minutes=1),))
    fresh, token = _login(service, request)
    assert _context_payload(fresh, settings)["ssoRecentlyAuthenticated"] is False
    with pytest.raises(ApiException) as error:
        _send(service, settings, token)
    assert error.value.code in {"SSO_LINK_REQUIRED", "SSO_REAUTH_REQUIRED"}


def test_policy_shortening_and_later_extension_cannot_restore_proof_on_login(setup):
    service, _, settings, request, _ = setup
    admin = context(service,"admin")
    service.update_sso_policy(SsoPolicyUpdate(validMinutes=1), admin, request)
    service.update_sso_policy(SsoPolicyUpdate(validMinutes=10080), admin, request)
    fresh, token = _login(service, request)
    assert fresh.sso_authenticated_at is None
    assert _context_payload(fresh, settings)["ssoRecentlyAuthenticated"] is False
    with pytest.raises(ApiException) as error:
        _send(service, settings, token)
    assert error.value.code == "SSO_REAUTH_REQUIRED"


@pytest.mark.parametrize("action", ["reset", "password", "suspend"])
def test_security_revocation_survives_new_login_and_reactivation(setup, action):
    service, _, settings, request, _ = setup
    old = context(service)
    admin = context(service,"admin")
    password = "local-password-before"
    if action == "reset":
        service.reset_sso("alice", old, request)
    elif action == "password":
        password = "local-password-after"
        service.change_password(old, PasswordChangeRequest(currentPassword="local-password-before",newPassword=password), request)
        assert service.context_from_token("alice1") is None
    else:
        service.update_user("alice", AdminUserUpdate(status="SUSPENDED",projects=["XRD"],roles=["REPORT_SENDER"]), admin, request)
        assert service.context_from_token("alice1") is None
        with pytest.raises(ApiException) as denied:
            _login(service, request)
        assert denied.value.code == "ACCOUNT_NOT_ACTIVE"
        service.update_user("alice", AdminUserUpdate(status="ACTIVE",projects=["XRD"],roles=["REPORT_SENDER"]), admin, request)
    fresh, token = _login(service, request, password)
    assert fresh.sso_identity is None and fresh.sso_authenticated_at is None
    with pytest.raises(ApiException) as error:
        _send(service, settings, token)
    assert error.value.code == "SSO_LINK_REQUIRED"


@pytest.mark.parametrize("permission,expected", [("role", "REPORT_SEND_PERMISSION_REQUIRED"), ("project", "PROJECT_ACCESS_DENIED")])
def test_persisted_sso_never_grants_report_permission(setup, permission, expected):
    service, store, settings, request, _ = setup
    fresh, token = _login(service, request)
    assert _context_payload(fresh, settings)["ssoRecentlyAuthenticated"] is True
    if permission == "role":
        store.execute("DELETE FROM user_roles WHERE user_id='alice'")
    else:
        store.execute("DELETE FROM user_project_permissions WHERE user_id='alice'")
    with pytest.raises(ApiException) as error:
        _send(service, settings, token)
    assert error.value.code == expected


def _invalidate_origin(service, store, original, now, action):
    if action == "logout":
        service.revoke_session(original)
    elif action == "expired":
        store.execute("UPDATE auth_sessions SET expires_at=? WHERE session_id=?", (now, original.session_id))
    else:
        # Revalidate ACTIVE independently even if state was changed outside the admin flow.
        store.execute("UPDATE app_users SET status='SUSPENDED' WHERE user_id=?", (original.user_id,))


@pytest.mark.parametrize("action", ["logout", "expired", "suspended"])
def test_posco_completion_rechecks_originating_login_after_network(setup, action):
    service, store, _, request, now = setup
    original = context(service)
    previous = original.sso_authenticated_at
    def accepted(*args):
        _invalidate_origin(service, store, original, now, action)
        return True
    service.lims_sso_client = SimpleNamespace(validate=accepted)
    with pytest.raises(ApiException) as error:
        service.verify_posco_sso(original, PoscoSsoVerifyRequest(username="alice",password="test-sso"), request)
    assert error.value.code == "AUTHENTICATION_REQUIRED"
    assert store.execute("SELECT last_authenticated_at FROM sso_identities WHERE user_id='alice'").fetchone()["last_authenticated_at"] == previous


@pytest.mark.parametrize("action", ["password", "suspend"])
def test_account_security_change_wins_over_inflight_password_sso(setup, action):
    service, store, _, request, _ = setup
    original = context(service)
    admin = context(service,"admin")
    def accepted(*args):
        if action == "password":
            service.change_password(original, PasswordChangeRequest(currentPassword="local-password-before",newPassword="local-password-after"), request)
        else:
            service.update_user("alice", AdminUserUpdate(status="SUSPENDED",projects=["XRD"],roles=["REPORT_SENDER"]), admin, request)
        return True
    service.lims_sso_client = SimpleNamespace(validate=accepted)
    with pytest.raises(ApiException) as error:
        service.verify_posco_sso(original, PoscoSsoVerifyRequest(username="alice",password="test-sso"), request)
    assert error.value.code == "SSO_RESET_DURING_AUTH"
    assert store.execute("SELECT active,last_authenticated_at FROM sso_identities WHERE user_id='alice'").fetchone() == {"active":0,"last_authenticated_at":None}


@pytest.mark.parametrize("action", ["logout", "expired", "suspended"])
def test_oidc_completion_rechecks_originating_login_after_network(setup, monkeypatch, action):
    service, store, _, request, now = setup
    original = context(service)
    state = "origin-state"
    store.execute("INSERT INTO auth_oidc_states VALUES (?, 'alice','alice1','g0.pkce','/account',?)", (auth._token_hash(state),now+timedelta(minutes=10)))
    class Reply:
        def __init__(self, data): self.data=data
        def raise_for_status(self): return self
        def json(self): return self.data
    def get(url, **kwargs):
        if url.endswith('/.well-known/openid-configuration'):
            return Reply({"token_endpoint":"https://sso.invalid/token","userinfo_endpoint":"https://sso.invalid/user"})
        _invalidate_origin(service, store, original, now, action)
        return Reply({"sub":"alice","employee_id":"alice"})
    monkeypatch.setattr(auth.httpx, 'get', get)
    monkeypatch.setattr(auth.httpx, 'post', lambda *args,**kwargs: Reply({"access_token":"synthetic-test-token"}))
    with pytest.raises(ApiException) as error:
        service.finish_sso(state, 'synthetic-code', request)
    assert error.value.code == "AUTHENTICATION_REQUIRED"
    assert store.execute("SELECT last_authenticated_at FROM sso_identities WHERE user_id='alice'").fetchone()["last_authenticated_at"] == original.sso_authenticated_at


@pytest.mark.parametrize("legacy_revoked", [False, True])
def test_upgrade_never_bootstraps_legacy_session_or_identity_proof(setup, legacy_revoked):
    service, store, settings, request, now = setup
    store.execute("DELETE FROM auth_sso_grants")
    if legacy_revoked:
        # Older password changes/suspensions left both SSO timestamps behind.
        store.execute("UPDATE auth_sessions SET revoked_at=? WHERE user_id='alice'", (now,))
    fresh, token = _login(service, request)
    assert fresh.sso_identity is not None
    assert fresh.sso_authenticated_at is None
    with pytest.raises(ApiException) as error:
        _send(service, settings, token)
    assert error.value.code == "SSO_REAUTH_REQUIRED"
    service.verify_posco_sso(fresh, PoscoSsoVerifyRequest(username="alice", password="test-sso"), request)
    assert _send(service, settings, token).operator_id == "alice"
    assert store.execute("SELECT COUNT(*) AS n FROM auth_sso_grants").fetchone()["n"] == 1


def test_grant_survives_deleting_historical_sessions(setup):
    service, store, settings, request, _ = setup
    store.execute("DELETE FROM auth_sessions WHERE user_id='alice'")
    fresh, token = _login(service, request)
    assert _context_payload(fresh, settings)["ssoRecentlyAuthenticated"] is True
    assert _send(service, settings, token).operator_id == "alice"


def test_fresh_oidc_completion_creates_grant_reused_on_new_login(setup, monkeypatch):
    service, store, settings, request, now = setup
    store.execute("DELETE FROM auth_sso_grants WHERE user_id='alice'")
    state = "fresh-oidc-state"
    store.execute("INSERT INTO auth_oidc_states VALUES (?, 'alice','alice1','g0.pkce','/account',?)",
                  (auth._token_hash(state), now+timedelta(minutes=10)))
    class Reply:
        def __init__(self, data): self.data = data
        def raise_for_status(self): return self
        def json(self): return self.data
    def get(url, **kwargs):
        if url.endswith('/.well-known/openid-configuration'):
            return Reply({"token_endpoint":"https://sso.invalid/token","userinfo_endpoint":"https://sso.invalid/user"})
        return Reply({"sub":"alice", "employee_id":"alice"})
    monkeypatch.setattr(auth.httpx, 'get', get)
    monkeypatch.setattr(auth.httpx, 'post', lambda *args, **kwargs: Reply({"access_token":"synthetic-test-token"}))
    assert service.finish_sso(state, 'synthetic-code', request) == '/account'
    service.revoke_session(context(service))
    fresh, token = _login(service, request)
    assert fresh.sso_authenticated_at == now
    assert _context_payload(fresh, settings)["ssoRecentlyAuthenticated"] is True
    assert _send(service, settings, token).operator_id == 'alice'

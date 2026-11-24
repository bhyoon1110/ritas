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
from app.auth import AuthService, SsoPolicyUpdate, PoscoSsoVerifyRequest, _context_payload
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
CREATE TABLE app_users(user_id TEXT PRIMARY KEY, login_id TEXT, email TEXT, display_name TEXT, status TEXT);
CREATE TABLE auth_sessions(session_id TEXT PRIMARY KEY,user_id TEXT,token_hash TEXT,expires_at TIMESTAMP,sso_authenticated_at TIMESTAMP,revoked_at TIMESTAMP,last_seen_at TIMESTAMP);
CREATE TABLE user_project_permissions(user_id TEXT,project_code TEXT);
CREATE TABLE user_roles(user_id TEXT,role_code TEXT);
CREATE TABLE sso_identities(identity_id TEXT,user_id TEXT,provider TEXT,subject TEXT,employee_id TEXT,email TEXT,display_name TEXT,active INTEGER,last_authenticated_at TIMESTAMP,updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE auth_oidc_states(state_hash TEXT,user_id TEXT,session_id TEXT,code_verifier TEXT,return_to TEXT,expires_at TIMESTAMP);
CREATE TABLE auth_audit_events(user_id TEXT,event_type TEXT,success INTEGER,details_json TEXT,remote_ip TEXT);
CREATE TABLE auth_sso_policy(policy_id INTEGER PRIMARY KEY,valid_minutes INTEGER,updated_by TEXT,updated_at TIMESTAMP);
CREATE TABLE auth_sso_resets(user_id TEXT PRIMARY KEY,generation INTEGER,reset_at TIMESTAMP,reset_by TEXT);
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
    for user in ("admin", "alice", "bob"):
        store.execute("INSERT INTO app_users VALUES (?, ?, NULL, ?, 'ACTIVE')", (user,user,user))
        store.execute("INSERT INTO user_roles VALUES (?, 'REPORT_SENDER')", (user,))
        store.execute("INSERT INTO user_project_permissions VALUES (?, 'XRD')", (user,))
        store.execute("INSERT INTO sso_identities VALUES (?, ?, 'RIST SSO', ?, ?, NULL, NULL, TRUE, ?, ?)",
                      (user,user,user,user,now-timedelta(minutes=5),now))
        for n in (1,2):
            token = user + str(n)
            store.execute("INSERT INTO auth_sessions VALUES (?, ?, ?, ?, ?, NULL, NULL)",
                          (token,user,auth._token_hash(token),now+timedelta(hours=8),now-timedelta(minutes=5)))
    store.execute("INSERT INTO user_roles VALUES ('admin', 'ADMIN')")
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


def test_larger_policy_cannot_revive_an_already_expired_session(setup):
    service, store, settings, request, now = setup
    store.execute("UPDATE auth_sessions SET sso_authenticated_at = ? WHERE session_id='alice1'", (now-timedelta(minutes=31),))
    service.update_sso_policy(SsoPolicyUpdate(validMinutes=60), context(service,"admin"), request)
    assert context(service).sso_authenticated_at is None
    assert _context_payload(context(service,session=2), settings)["ssoRecentlyAuthenticated"] is True


def test_expiry_is_capped_by_local_session_and_excludes_exact_boundary(setup):
    service, _, settings, _, now = setup
    current = replace(context(service), sso_valid_minutes=43200, session_expires_at=now+timedelta(minutes=10))
    assert _context_payload(current, settings)["ssoExpiresAt"] == "2026-11-24T09:10:00Z"
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
    service, _, settings, request, _ = setup
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
    assert context(service,session=2).sso_authenticated_at is None


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
    for name in ('auth_sso_policy','auth_sso_resets'):
        statement=next(x.strip() for x in _SCHEMA if 'CREATE TABLE IF NOT EXISTS '+name+' (' in x)
        assert statement+';' in migration
    assert 'ALTER TABLE' not in migration and 'DROP TABLE' not in migration

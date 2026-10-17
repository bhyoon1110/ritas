from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.auth import SESSION_COOKIE
from app.config import Settings
from app.main import create_app


PASSWORD = "account-navigation-test-password"
ACCOUNT_HEADERS = {"X-Requested-With": "RIST-Account"}


@pytest.fixture
def accounts(tmp_path, mariadb):
    settings = Settings(
        storage_root=tmp_path / "jobs",
        ftir_assignment_library_dir=tmp_path / "libraries",
        db_host=mariadb["host"], db_port=mariadb["port"],
        db_name=mariadb["name"], db_user=mariadb["user"],
        db_password=mariadb["password"], auth_enabled=True,
        auth_cookie_secure=True, auth_bootstrap_admin_ids=("nav-admin",),
    )
    app = create_app(settings)
    clients = [TestClient(app, base_url="https://testserver", follow_redirects=False)
               for _ in range(3)]
    admin, member, other_session = clients
    try:
        for client, login_id in [(admin, "nav-admin"), (member, "nav-member")]:
            signup = client.post("/api/v1/auth/signup", json={
                "loginId": login_id, "displayName": login_id, "password": PASSWORD,
            })
            assert signup.status_code == 200, signup.text
            if login_id == "nav-member":
                approved = admin.patch(
                    "/api/v1/auth/admin/users/" + signup.json()["userId"],
                    headers={"X-Requested-With": "RIST-Admin"},
                    json={"status": "ACTIVE", "projects": ["FTIR", "RAMAN", "XRD", "TEM"], "roles": []},
                )
                assert approved.status_code == 200, approved.text
            login = client.post("/api/v1/auth/login", json={"loginId": login_id, "password": PASSWORD})
            assert login.status_code == 200, login.text
        result = other_session.post("/api/v1/auth/login", json={"loginId": "nav-member", "password": PASSWORD})
        assert result.status_code == 200, result.text
        yield SimpleNamespace(admin=admin, member=member, other_session=other_session)
    finally:
        for client in clients:
            client.close()
        app.state.database.close()


@pytest.mark.parametrize("role", ["admin", "member"])
def test_all_workspaces_link_to_the_authenticated_account(accounts, role):
    client = getattr(accounts, role)
    for path, prefix in [("/", "workspace"), ("/ftir", "ftir"), ("/raman", "raman"),
                         ("/xrd?legacy=1", "xrd"), ("/tem", "ahn")]:
        response = client.get(path)
        assert response.status_code == 200, path
        assert 'href="/account"' in response.text
        assert "회원정보 관리" in response.text
        assert f'id="{prefix}-logout"' in response.text
    # The default XRD entry now redirects to the isolated workbench. Its account,
    # VOC and logout controls are mounted by shell.mjs (DOM-tested separately).
    entry = client.get("/xrd")
    assert entry.status_code == 303
    assert entry.headers["location"] == "/api/v1/xrd/workbench/"
    workbench = client.get(entry.headers["location"])
    assert workbench.status_code == 200
    assert workbench.headers["cache-control"] == "private, no-store"
    assert 'data-step="viewer"' in workbench.text
    assert "shared/shell.mjs" in workbench.text
    for asset in ("shared/shell.mjs", "shared/shell.legacy.js"):
        assert client.get(entry.headers["location"] + asset).status_code == 200
    account = client.get("/account")
    assert account.status_code == 200
    assert 'id="profile-form"' in account.text
    assert 'id="password-form"' in account.text
    assert "변경 후 모든 기기에서 로그아웃됩니다." in account.text
    assert client.get("/operations").status_code == (200 if role == "admin" else 403)


def test_logout_revokes_only_the_current_session_and_clears_cookie(accounts):
    member = accounts.member
    old_token = member.cookies.get(SESSION_COOKIE)
    assert old_token
    result = member.post("/api/v1/auth/logout", headers={"X-Requested-With": "RIST-Project"})
    assert result.status_code == 200
    assert result.json() == {"loggedOut": True}
    assert member.cookies.get(SESSION_COOKIE) is None
    assert member.get("/api/v1/auth/me").status_code == 401
    # Even restoring the previous cookie must not restore a revoked session.
    assert member.get("/api/v1/auth/me", headers={"Cookie": f"{SESSION_COOKIE}={old_token}"}).status_code == 401
    assert member.get("/account").status_code == 303
    assert accounts.other_session.get("/api/v1/auth/me").status_code == 200
    assert accounts.admin.get("/api/v1/auth/me").status_code == 200


def test_profile_update_and_password_change_keep_existing_security_policy(accounts):
    member = accounts.member
    profile = {"displayName": "회원정보 검증", "email": "navigation@example.invalid"}
    assert member.patch("/api/v1/auth/me", json=profile).status_code == 403
    updated = member.patch("/api/v1/auth/me", json=profile, headers=ACCOUNT_HEADERS)
    assert updated.status_code == 200
    me = member.get("/api/v1/auth/me").json()
    assert me["displayName"] == profile["displayName"]
    assert me["email"] == profile["email"]
    assert profile["displayName"] in member.get("/account").text
    new_password = "changed-navigation-test-password"
    payload = {"currentPassword": PASSWORD, "newPassword": new_password}
    assert member.post("/api/v1/auth/password", json=payload).status_code == 403
    wrong = member.post("/api/v1/auth/password", headers=ACCOUNT_HEADERS,
                        json={**payload, "currentPassword": "incorrect-password"})
    assert wrong.status_code == 400
    assert wrong.json()["code"] == "CURRENT_PASSWORD_INVALID"
    assert accounts.other_session.get("/api/v1/auth/me").status_code == 200
    changed = member.post("/api/v1/auth/password", json=payload, headers=ACCOUNT_HEADERS)
    assert changed.status_code == 200
    assert member.cookies.get(SESSION_COOKIE) is None
    assert member.get("/api/v1/auth/me").status_code == 401
    assert accounts.other_session.get("/api/v1/auth/me").status_code == 401
    assert accounts.admin.get("/api/v1/auth/me").status_code == 200
    assert member.post("/api/v1/auth/login", json={"loginId": "nav-member", "password": PASSWORD}).status_code == 401
    assert member.post("/api/v1/auth/login", json={"loginId": "nav-member", "password": new_password}).status_code == 200

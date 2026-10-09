from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.auth import SESSION_COOKIE
from app.config import Settings
from app.database import _SCHEMA
from app.errors import ApiException
from app.main import create_app
from app.voc import PROJECT_PAGES, VocAction, VocCreate, VocService
from app.voc_web import build_voc_page


WRITE_HEADERS = {"X-Requested-With": "RIST-VOC"}
ADMIN_HEADERS = {"X-Requested-With": "RIST-Admin"}


@pytest.fixture
def board(tmp_path, mariadb):
    settings = Settings(
        storage_root=tmp_path / "jobs", db_host=mariadb["host"],
        ftir_assignment_library_dir=tmp_path / "ftir-libraries",
        db_port=mariadb["port"], db_name=mariadb["name"],
        db_user=mariadb["user"], db_password=mariadb["password"],
        auth_enabled=True, auth_cookie_secure=True,
        auth_bootstrap_admin_ids=("voc-admin",),
    )
    app = create_app(settings)
    clients, ids, contexts = {}, {}, {}
    anonymous = TestClient(app, base_url="https://testserver", follow_redirects=False)
    try:
        for name, projects in [("admin", []), ("alice", ["FTIR", "RAMAN"]),
                               ("bob", ["FTIR"]), ("xrd", ["XRD"]), ("none", [])]:
            login_id = "voc-" + name
            password = "test-only-voc-password"
            signup = anonymous.post("/api/v1/auth/signup", json={
                "loginId": login_id, "displayName": "VOC 시험 " + name, "password": password,
            })
            assert signup.status_code == 200, signup.text
            ids[name] = signup.json()["userId"]
            if name != "admin":
                approved = clients["admin"].patch(
                    "/api/v1/auth/admin/users/" + ids[name], headers=ADMIN_HEADERS,
                    json={"status": "ACTIVE", "projects": projects, "roles": []},
                )
                assert approved.status_code == 200, approved.text
            client = TestClient(app, base_url="https://testserver", follow_redirects=False)
            clients[name] = client
            login = client.post("/api/v1/auth/login", json={"loginId": login_id, "password": password})
            assert login.status_code == 200, login.text
            contexts[name] = app.state.auth_service.context_from_token(client.cookies.get(SESSION_COOKIE))
        yield SimpleNamespace(app=app, clients=clients, ids=ids, contexts=contexts,
                              anonymous=anonymous, settings=settings)
    finally:
        anonymous.close()
        for client in clients.values():
            client.close()
        app.state.database.close()


def create(board, name="alice", project="FTIR", **values):
    result = board.clients[name].post("/api/v1/voc", headers={
        **WRITE_HEADERS, "Idempotency-Key": str(uuid4()),
    }, json={"project": project, "title": "피크 표시 개선", "content": "시험 데이터의 표시를 확인해 주세요.", **values})
    assert result.status_code == 201, result.text
    assert result.headers["cache-control"] == "no-store"
    return result.json()


def act(board, item, status="RESOLVED", note="시험 조치를 완료했습니다.", name="admin"):
    return board.clients[name].post("/api/v1/voc/" + item["vocId"] + "/status",
        headers=WRITE_HEADERS, json={"expectedVersion": item["version"], "status": status, "note": note})


def confirm(board, item, name="alice"):
    return board.clients[name].post("/api/v1/voc/" + item["vocId"] + "/confirm",
        headers=WRITE_HEADERS, json={"expectedVersion": item["version"]})


def test_full_resolution_workflow_and_persistent_history(board):
    item = create(board)
    assert item["status"] == "OPEN" and item["version"] == 1
    assert item["authorUserId"] == board.ids["alice"]
    assert item["pageUrl"] == "/ftir" and item["projectLabel"] == "FT-IR"
    assert item["resolvedAt"] is None and item["confirmedAt"] is None
    assert confirm(board, item).status_code == 409
    response = act(board, item, "IN_PROGRESS", "현상을 재현했습니다.")
    assert response.status_code == 200, response.text
    item = response.json()
    assert item["version"] == 2 and item["resolvedAt"] is None
    item = act(board, item).json()
    assert item["status"] == "RESOLVED" and item["version"] == 3
    assert item["resolvedAt"].endswith("+00:00")
    assert item["resolvedByName"] == "VOC 시험 admin"
    resolved = item
    response = confirm(board, resolved)
    assert response.status_code == 200, response.text
    item = response.json()
    assert item["status"] == "CONFIRMED" and item["version"] == 4
    assert item["confirmedAt"].endswith("+00:00")
    # A retried confirmation is harmless even with the previous version.
    assert confirm(board, resolved).json() == item
    assert act(board, item).status_code == 409
    persisted = VocService(board.app.state.database).get(board.contexts["bob"], item["vocId"])
    assert [event["action"] for event in persisted["events"]] == [
        "CREATED", "ADMIN_ACTION", "ADMIN_ACTION", "AUTHOR_CONFIRMED",
    ]
    assert [event["status"] for event in persisted["events"]] == [
        "OPEN", "IN_PROGRESS", "RESOLVED", "CONFIRMED",
    ]
    assert persisted["resolutionNote"] == "시험 조치를 완료했습니다."


def test_project_sharing_hides_other_experiments_and_restricts_mutations(board):
    ftir = create(board)
    raman = create(board, project="RAMAN")
    xrd = create(board, "xrd", "XRD")
    tem = create(board, "admin", "TEM")
    expected = {"alice": {ftir["vocId"], raman["vocId"]}, "bob": {ftir["vocId"]},
                "xrd": {xrd["vocId"]}, "none": set(),
                "admin": {item["vocId"] for item in (ftir, raman, xrd, tem)}}
    for name, visible in expected.items():
        result = board.clients[name].get("/api/v1/voc").json()
        assert {item["vocId"] for item in result["items"]} == visible
        assert result["total"] == sum(result["counts"].values()) == len(visible)
        for item in (ftir, raman, xrd, tem):
            response = board.clients[name].get("/api/v1/voc/" + item["vocId"])
            assert response.status_code == (200 if item["vocId"] in visible else 404)
    assert act(board, ftir, name="bob").status_code == 403
    assert act(board, ftir, name="alice").status_code == 403
    resolved = act(board, ftir).json()
    assert confirm(board, resolved, "bob").status_code == 403
    assert confirm(board, resolved, "admin").status_code == 403
    assert confirm(board, resolved, "xrd").status_code == 404
    assert board.clients["bob"].get("/api/v1/voc", params={"project": "RAMAN"}).json()["total"] == 0
    denied = board.clients["bob"].post("/api/v1/voc", headers={**WRITE_HEADERS, "Idempotency-Key": str(uuid4())},
        json={"project": "RAMAN", "title": "우회 등록", "content": "권한 없음"})
    assert denied.status_code == 403


def test_revoked_project_permissions_take_effect_without_relogin(board):
    item = act(board, create(board)).json()
    response = board.clients["admin"].patch("/api/v1/auth/admin/users/" + board.ids["alice"],
        headers=ADMIN_HEADERS, json={"status": "ACTIVE", "projects": [], "roles": []})
    assert response.status_code == 200
    assert board.clients["alice"].get("/api/v1/voc").json()["total"] == 0
    assert board.clients["alice"].get("/api/v1/voc/" + item["vocId"]).status_code == 404
    assert confirm(board, item).status_code == 404
    assert board.clients["bob"].get("/api/v1/voc/" + item["vocId"]).status_code == 200


def test_authentication_csrf_and_invalid_requests(board):
    item = create(board)
    assert board.anonymous.get("/voc").status_code == 303
    redirect = board.anonymous.get("/voc?project=RAMAN")
    assert redirect.status_code == 303
    assert parse_qs(urlsplit(redirect.headers["location"]).query)["returnTo"] == ["/voc?project=RAMAN"]
    assert board.anonymous.get("/api/v1/voc").status_code == 401
    assert board.clients["none"].get("/voc").status_code == 200
    assert board.clients["bob"].get("/voc?project=RAMAN").status_code == 403
    client = board.clients["alice"]
    body = {"project": "FTIR", "title": "시험", "content": "내용"}
    assert client.post("/api/v1/voc", headers={"Idempotency-Key": str(uuid4())}, json=body).status_code == 403
    assert client.post("/api/v1/voc", headers=WRITE_HEADERS, json=body).status_code == 400
    assert client.post("/api/v1/voc", headers={**WRITE_HEADERS, "Idempotency-Key": "invalid"}, json=body).status_code == 400
    assert client.post("/api/v1/voc/" + item["vocId"] + "/confirm", json={"expectedVersion": 1}).status_code == 403
    assert board.clients["admin"].post("/api/v1/voc/" + item["vocId"] + "/status",
        json={"expectedVersion": 1, "status": "RESOLVED", "note": "완료"}).status_code == 403
    for path in ["/api/v1/voc/not-a-uuid", "/api/v1/voc?page=0", "/api/v1/voc?pageSize=101",
                 "/api/v1/voc?project=FAKE", "/api/v1/voc?status=DELETED", "/api/v1/voc?q=" + "x" * 201]:
        assert client.get(path).status_code == 400
    for changes in [{"title": " "}, {"content": " "}, {"authorUserId": board.ids["admin"]},
                    {"status": "CONFIRMED"}, {"project": "INVALID"}]:
        response = client.post("/api/v1/voc", headers={**WRITE_HEADERS, "Idempotency-Key": str(uuid4())}, json={**body, **changes})
        assert response.status_code == 400
    assert act(board, item, note="  ").status_code == 400
    suspended = board.clients["admin"].patch("/api/v1/auth/admin/users/" + board.ids["alice"],
        headers=ADMIN_HEADERS, json={"status": "SUSPENDED", "projects": ["FTIR"], "roles": []})
    assert suspended.status_code == 200
    assert client.get("/api/v1/voc").status_code == 401


def test_voc_fails_closed_when_auth_middleware_is_disabled(board):
    app = create_app(replace(board.settings, auth_enabled=False))
    with TestClient(app) as client:
        assert client.get("/api/v1/voc").status_code == 401
        assert client.get("/voc").status_code == 401


def test_duplicate_creation_is_idempotent_and_rejects_different_payload(board):
    client = board.clients["alice"]
    headers = {**WRITE_HEADERS, "Idempotency-Key": str(uuid4())}
    payload = {"project": "FTIR", "title": " 재전송 시험 ", "content": " 내용 "}
    first = client.post("/api/v1/voc", headers=headers, json=payload)
    second = client.post("/api/v1/voc", headers=headers, json=payload)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert first.json()["title"] == "재전송 시험"
    detail = client.get("/api/v1/voc/" + first.json()["vocId"]).json()
    assert len(detail["events"]) == 1
    conflict = client.post("/api/v1/voc", headers=headers, json={**payload, "content": "다른 내용"})
    assert conflict.status_code == 409
    other = board.clients["bob"].post("/api/v1/voc", headers=headers, json=payload)
    assert other.status_code == 201 and other.json()["vocId"] != first.json()["vocId"]


def test_concurrent_retry_creates_one_record_and_one_event(board):
    service = VocService(board.app.state.database)
    key, barrier = str(uuid4()), Barrier(4)
    payload = VocCreate(project="FTIR", title="동시 재시도", content="동일 요청")
    def submit(_):
        barrier.wait(timeout=10)
        return service.create(board.contexts["alice"], payload, key)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(submit, range(4)))
    assert len({item["vocId"] for item in results}) == 1
    assert len(service.get(board.contexts["alice"], results[0]["vocId"])["events"]) == 1


def test_concurrent_admin_changes_use_optimistic_locking(board):
    item = create(board)
    service, barrier = VocService(board.app.state.database), Barrier(2)
    def update(index):
        barrier.wait(timeout=10)
        try:
            return service.act(board.contexts["admin"], item["vocId"],
                VocAction(expectedVersion=1, status="RESOLVED", note=f"조치 {index}"))
        except ApiException as exc:
            return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, range(2)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert 409 in results
    latest = service.get(board.contexts["alice"], item["vocId"])
    assert latest["version"] == 2 and len(latest["events"]) == 2


def test_confirmation_requires_latest_resolution_and_reopening_clears_metadata(board):
    resolved = act(board, create(board)).json()
    revised = act(board, resolved, note="추가 조치를 반영했습니다.").json()
    assert confirm(board, resolved).status_code == 409
    reopened = act(board, revised, "IN_PROGRESS", "재확인 중").json()
    assert reopened["status"] == "IN_PROGRESS"
    assert reopened["resolvedAt"] is None and reopened["resolvedByName"] is None
    assert confirm(board, reopened).status_code == 409
    assert confirm(board, act(board, reopened).json()).status_code == 200


def test_search_pagination_counts_and_literal_sql_characters(board):
    create(board, title="공통 먼저")
    second = create(board, "bob", title="공통 표시")
    act(board, second)
    create(board, project="RAMAN", title="공통 Raman")
    literal = create(board, title="100% _ ' OR 1=1 --", content="검색 구분")
    create(board, "xrd", "XRD", title="공통 숨김")
    client = board.clients["alice"]
    response = client.get("/api/v1/voc", params={"project": "FTIR", "q": "공통", "pageSize": 1})
    assert response.headers["cache-control"] == "no-store"
    page1 = response.json()
    page2 = client.get("/api/v1/voc", params={"project": "FTIR", "q": "공통", "pageSize": 1, "page": 2}).json()
    assert page1["total"] == page2["total"] == 2
    assert len(page1["items"]) == len(page2["items"]) == 1
    assert page1["items"][0]["vocId"] != page2["items"][0]["vocId"]
    resolved = client.get("/api/v1/voc", params={"project": "FTIR", "q": "공통", "status": "RESOLVED"}).json()
    assert resolved["total"] == 1
    assert resolved["counts"] == {"OPEN": 1, "IN_PROGRESS": 0, "RESOLVED": 1, "CONFIRMED": 0}
    assert client.get("/api/v1/voc", params={"q": "' OR 1=1 --"}).json()["items"][0]["vocId"] == literal["vocId"]
    assert client.get("/api/v1/voc", params={"q": "voc-bob"}).json()["total"] == 1


def test_max_unicode_text_and_markup_round_trip(board):
    text = "<script>alert('voc')</script>\n" + "가🧪" * 4980
    item = create(board, title="장" * 160, content=text)
    response = board.clients["bob"].get("/api/v1/voc/" + item["vocId"])
    assert response.status_code == 200 and response.json()["content"] == text
    assert response.headers["cache-control"] == "no-store"


class Links(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.items = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.items.append(dict(attrs))


def test_experiment_entry_points_and_responsive_board(board):
    admin = board.clients["admin"]
    for project, page in PROJECT_PAGES.items():
        response = admin.get(page)
        assert response.status_code == 200, response.text[:1000]
        assert any(link.get("href") == "/voc?project=" + project and link.get("target") == "_blank"
                   and link.get("rel") == "noopener" for link in Links(response.text).items)
        board_page = admin.get("/voc?project=" + project)
        assert board_page.status_code == 200
        assert board_page.headers["cache-control"] == "no-store"
        assert '"project": "' + project + '"' in board_page.text
    for page in ["/", "/operations", "/admin/users", "/report-management"]:
        assert any(link.get("href") == "/voc" for link in Links(admin.get(page).text).items)
    html = build_voc_page(board.contexts["bob"], "FTIR")
    assert '"allowedProjects": ["FTIR"]' in html
    assert "같은 실험 권한이 있는 회원과 관리자에게 공개" in html
    assert 'id="confirm-check" required' in html
    assert "textContent" in html and ".innerHTML" not in html
    assert "@media(max-width:760px)" in html and "overflow-wrap:anywhere" in html
    assert "rist-browser-warning" in html
    escaped = build_voc_page(replace(board.contexts["bob"], user_id="</script><script>injected()"))
    assert "</script><script>injected()" not in escaped


def test_manual_migration_matches_runtime_and_can_be_reapplied(board):
    path = Path(__file__).resolve().parents[1] / "deploy" / "mariadb_voc_migration.sql"
    sql = "\n".join(line for line in path.read_text().splitlines() if not line.startswith("--"))
    statements = [part.strip() for part in sql.split(";") if part.strip()]
    expected = [statement.strip() for statement in _SCHEMA if "CREATE TABLE IF NOT EXISTS voc_" in statement]
    assert [" ".join(s.split()) for s in statements] == [" ".join(s.split()) for s in expected]
    item = create(board)
    for _ in range(2):
        with board.app.state.database.transaction() as connection:
            for statement in statements:
                connection.execute(statement)
    assert board.clients["alice"].get("/api/v1/voc/" + item["vocId"]).status_code == 200


@pytest.mark.parametrize("changes", [
    {"title": ""}, {"title": " "}, {"title": "x" * 161}, {"content": ""},
    {"content": "x" * 10001}, {"project": "NOPE"}, {"authorUserId": "forged"},
])
def test_create_model_rejects_invalid_or_forged_values(changes):
    with pytest.raises(ValidationError):
        VocCreate.model_validate({"project": "FTIR", "title": "시험", "content": "내용", **changes})


@pytest.mark.parametrize("changes", [
    {"status": "CONFIRMED"}, {"note": " "}, {"note": "x" * 10001},
    {"expectedVersion": 0}, {"resolvedByName": "forged"},
])
def test_action_model_rejects_invalid_or_forged_values(changes):
    with pytest.raises(ValidationError):
        VocAction.model_validate({"status": "RESOLVED", "expectedVersion": 1, "note": "완료", **changes})

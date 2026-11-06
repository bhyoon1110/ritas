from __future__ import annotations

from contextlib import contextmanager
import sqlite3
import ssl
from types import SimpleNamespace

import httpx
import pytest

from app.database import Database
from app.errors import ApiException
from app.lims_requests import LimsRequestsClient, NUMBER_FIELDS, TEXT_FIELDS, validate_filters, validate_response
from app.models import RequestListResponse
from app.service import EdgeService


URL = "https://spring.example.test/api/lims/requests"
KEY = "local-only-test-internal-key-32-bytes"


def response_body(page=1, size=50, count=1):
    item = {key: None for key in NUMBER_FIELDS | TEXT_FIELDS}
    item.update(reqResultNo=100, reqNumber="2026R02236", testMtdCode="A23142", testMtdName="XRD 정성분석")
    return {"totalCount": count, "page": page, "size": size, "items": [item] if count else []}


def test_client_keeps_internal_token_private_and_forwards_all_filters(monkeypatch):
    observed = {}
    original = httpx.Client
    def client(**kwargs):
        observed.update(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(httpx, "Client", client)
    def handler(request):
        observed["request"] = request
        return httpx.Response(200, json=response_body(page=2, size=2, count=3))
    filters = {"reqNumber": "2026R02236", "testMtdCode": "A23142", "fromDate": "2026-10-01",
               "toDate": "2026-10-31", "testChargerNo": 7, "testChargerId": "한글+id",
               "assetNumber": "AX/한글+007", "testState": "1,3,5,7"}
    adapter = LimsRequestsClient(URL, KEY, transport=httpx.MockTransport(handler))
    result = adapter.fetch(2, 2, filters)
    request = observed["request"]
    assert result["totalCount"] == 3
    assert request.method == "GET" and not request.content
    assert dict(request.url.params) == {"page": "2", "size": "2", **{k: str(v) for k, v in filters.items()}}
    assert request.headers["X-RITASAX-Internal-Key"] == KEY
    assert "X-Authorization" not in request.headers
    assert KEY not in str(request.url) and KEY not in repr(adapter)
    assert observed["trust_env"] is False and observed["follow_redirects"] is False
    assert isinstance(observed["verify"], ssl.SSLContext)
    assert observed["verify"].check_hostname and observed["verify"].verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize("url", [
    "http://localhost:8888/api/lims/requests", "http://192.168.1.2/requests", "file:///tmp/query",
    "https://user:password@spring.example.test/requests", URL + "?token=secret", URL + "#secret",
])
def test_unsafe_relay_urls_never_make_requests(url):
    def forbidden(_):
        raise AssertionError("unsafe relay contacted")
    with pytest.raises(ApiException) as error:
        LimsRequestsClient(url, KEY, allow_loopback_http=True, transport=httpx.MockTransport(forbidden)).fetch(1, 50, {})
    assert error.value.code == "LIMS_REQUESTS_NOT_CONFIGURED"


def test_explicit_loopback_http_is_supported_without_redirects():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://other.test/capture"}, text="secret")
    with pytest.raises(ApiException) as error:
        LimsRequestsClient("http://127.0.0.1:8888/api/lims/requests", KEY,
                           allow_loopback_http=True, transport=httpx.MockTransport(handler)).fetch(1, 50, {})
    assert len(calls) == 1 and error.value.status_code == 502
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("status,expected", [(400, 400), (401, 502), (403, 502), (404, 502), (500, 502), (503, 503)])
def test_upstream_errors_do_not_expose_body(status, expected):
    client = LimsRequestsClient(URL, KEY, transport=httpx.MockTransport(lambda _: httpx.Response(status, text=KEY)))
    with pytest.raises(ApiException) as error:
        client.fetch(1, 50, {})
    assert error.value.status_code == expected
    assert KEY not in str(error.value)


@pytest.mark.parametrize("payload", [
    {"totalCount": True, "page": 1, "size": 50, "items": []},
    {"totalCount": 1, "page": 2, "size": 50, "items": []},
    {"totalCount": 1, "page": 1, "size": 50, "items": []},
    {"totalCount": 0, "page": 1, "size": 50, "items": [{}]},
    {"totalCount": 1, "page": 1, "size": 50, "items": [{}]},
    [],
])
def test_malformed_or_inconsistent_response_is_rejected(payload):
    with pytest.raises(ApiException):
        validate_response(payload, 1, 50)


def test_null_fields_are_valid_but_wrong_types_are_not():
    payload = response_body()
    assert validate_response(payload, 1, 50) == payload
    payload["items"][0]["reqUserNo"] = "not-a-number"
    with pytest.raises(ApiException):
        validate_response(payload, 1, 50)


def test_response_size_and_network_errors_are_bounded():
    huge = LimsRequestsClient(URL, KEY, transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * (4 * 1024 * 1024 + 1))))
    with pytest.raises(ApiException) as error:
        huge.fetch(1, 50, {})
    assert error.value.code == "LIMS_REQUESTS_INVALID_RESPONSE"
    def timeout(request):
        raise httpx.ReadTimeout(KEY, request=request)
    with pytest.raises(ApiException) as error:
        LimsRequestsClient(URL, KEY, transport=httpx.MockTransport(timeout)).fetch(1, 50, {})
    assert KEY not in str(error.value) and error.value.retryable


@pytest.mark.parametrize("filters", [
    {"fromDate": "2026-2-01"}, {"fromDate": "2026-02-30"},
    {"fromDate": "2026-11-01", "toDate": "2026-10-31"},
    {"testChargerNo": 0}, {"testChargerNo": True}, {"testChargerNo": "7"},
    {"testChargerId": "id\nvalue"}, {"testMtdCode": "x" * 201},
    {"testState": ""}, {"testState": "7,"}, {"testState": "3,,5"}, {"testState": "4"},
    {"testState": "1;5"}, {"testState": "07"}, {"testState": "7 OR 1=1"}, {"assetNumber": " "},
])
def test_invalid_filters_fail_before_network(filters):
    with pytest.raises(ApiException) as error:
        validate_filters(filters)
    assert error.value.status_code == 400


class NoCacheAccess:
    def __getattr__(self, name):
        raise AssertionError("live request lookup must not read or replace full cache: " + name)


def settings(tmp_path):
    return SimpleNamespace(storage_root=tmp_path, lims_requests_enabled=True, lims_requests_url=URL,
                           sso_lims_internal_key=KEY, sso_lims_ca_bundle=None, sso_lims_allow_loopback_http=False,
                           sso_connect_timeout_seconds=2, sso_read_timeout_seconds=4)


def test_service_live_lookup_never_replaces_cache_and_maps_null_fields(monkeypatch, tmp_path):
    seen = []
    def fetch(self, page, size, filters):
        seen.append((page, size, filters))
        return response_body(page, size)
    monkeypatch.setattr(LimsRequestsClient, "fetch", fetch)
    service = EdgeService(settings(tmp_path), NoCacheAccess())
    response = service.request_summaries(1, 1000, source="lims", request_filters={"testChargerId": "hong"})
    assert seen == [(1, 1000, {"testChargerId": "hong"})]
    assert response["source"] == "lims" and response["totalCount"] == 1
    assert response["items"][0]["syncedAt"] is None
    assert response["items"][0]["requestState"] is None
    assert response["items"][0]["testMethodCode"] == "A23142"
    assert RequestListResponse.model_validate(response).total_count == 1


@pytest.mark.parametrize("kwargs,code", [
    ({"source": "lims", "experiment_type": "XRD"}, "UNSUPPORTED_LIMS_FILTER"),
    ({"source": "lims", "include_completed": False, "request_filters": {"testState": "9"}}, "INVALID_REQUEST_FILTER"),
    ({"source": "cache", "request_filters": {"assetNumber": "XRD-100"}}, "LIMS_LOOKUP_REQUIRED"),
    ({"source": "cache", "request_filters": {"testChargerId": "hong"}}, "LIMS_LOOKUP_REQUIRED"),
    ({"source": "cache", "request_filters": {"testChargerNo": 7}}, "LIMS_LOOKUP_REQUIRED"),
])
def test_unsupported_filters_are_not_silently_ignored(tmp_path, kwargs, code):
    with pytest.raises(ApiException) as error:
        EdgeService(settings(tmp_path), NoCacheAccess()).request_summaries(1, 50, **kwargs)
    assert error.value.code == code


def test_live_lookup_requires_explicit_enable(tmp_path):
    config = settings(tmp_path)
    config.lims_requests_enabled = False
    with pytest.raises(ApiException) as error:
        EdgeService(config, NoCacheAccess()).request_summaries(1, 50, source="lims")
    assert error.value.status_code == 503


def test_local_filters_use_and_inclusive_dates_and_matching_total_count():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    columns = ["req_result_no", "req_number", "req_date", "req_state", "req_state_name", "req_type_no", "req_type_code", "req_type_name",
               "project_code", "cust_req_name", "customer_no", "customer_name", "req_user_no", "req_user_name", "smp_result_no", "smp_result_name",
               "smp_result_state", "test_mtd_result_no", "test_mtd_no", "test_mtd_code", "test_mtd_name", "test_state", "test_charger_name", "output_order", "synced_at"]
    connection.execute("CREATE TABLE lims_req_ax_search (" + ",".join(columns) + ")")
    rows = [
        ("A", "2026-10-01", "M1", "XRD", "의뢰완료", 3),
        ("B", "2026-10-31", "M1", "XRD", "진행", 7),
        ("C", "2026-11-01", "M1", "XRD", "진행", 1),
        ("D", "2026-10-10", "M2", "XRD", "진행", 5),
        ("E", "2026-10-15", "M1", "XRD", "진행", 9),
        ("F", "2026-10-15", "M1", "XRD", "진행", 0),
        ("G", "2026-10-15", "M1", "XRD", "진행", None),
    ]
    connection.executemany("INSERT INTO lims_req_ax_search(req_number,req_date,test_mtd_code,test_mtd_name,req_state_name,test_state) VALUES(?,?,?,?,?,?)", rows)
    database = Database.__new__(Database)
    @contextmanager
    def connect():
        yield connection
    database._connect = connect
    filters = {"testMtdCode": "M1", "fromDate": "2026-10-01", "toDate": "2026-10-31"}
    assert database.count_request_summaries(("xrd",), False, filters) == 2
    assert database.fetch_request_summaries(1, 0, ("xrd",), False, filters)[0]["req_number"] == "B"
    assert database.fetch_request_summaries(1, 1, ("xrd",), False, filters)[0]["req_number"] == "A"
    assert database.count_request_summaries(("xrd",), True, filters) == 5
    terminal = {**filters, "testState": "9,0"}
    assert database.count_request_summaries(("xrd",), False, terminal) == 2
    assert {r["req_number"] for r in database.fetch_request_summaries(10, 0, ("xrd",), False, terminal)} == {"E", "F"}
    assert database.count_request_summaries(("xrd",), False, {**filters, "reqNumber": "B"}) == 1
    assert database.count_request_summaries((), True, {"reqNumber": "A' OR 1=1 --"}) == 0
    connection.close()


def test_shared_controls_are_available_in_all_standalone_preview_apps(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.ahn_web import create_tem_preview_app
    from app.ftir_web import create_ftir_preview_app
    from app.raman_web import create_raman_preview_app
    from app.xrd_web import create_xrd_preview_app
    monkeypatch.setenv("RIST_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("RIST_ERROR_ARCHIVE_ROOT", str(tmp_path / "errors"))
    monkeypatch.setenv("RIST_USAGE_LOG_ROOT", str(tmp_path / "usage"))
    for factory in (create_tem_preview_app, create_ftir_preview_app, create_raman_preview_app, create_xrd_preview_app):
        response = TestClient(factory()).get("/assets/request_filters.js")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/javascript")
        assert response.headers["cache-control"] == "no-cache"
        assert "RistRequestFilters" in response.text


def test_http_route_forwards_v5_filters_and_rejects_unsupported_combinations(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.main import create_app
    monkeypatch.setattr(Database, "from_settings", lambda _: NoCacheAccess())
    observed = []
    def fetch(self, page, size, filters):
        observed.append((page, size, filters))
        return response_body(page, size)
    monkeypatch.setattr(LimsRequestsClient, "fetch", fetch)
    config = Settings(storage_root=tmp_path, lims_requests_enabled=True, lims_requests_url=URL,
                      sso_lims_internal_key=KEY, error_capture_files=False)
    client = TestClient(create_app(config))
    response = client.get("/api/v1/requests", params={"source": "lims", "pageSize": 1000, "reqNumber": "R1",
                          "testMtdCode": "A23142", "fromDate": "2026-10-01", "toDate": "2026-10-31",
                          "testChargerNo": 7, "testChargerId": "hong", "assetNumber": "ASSET/42",
                          "testState": "7, 3,7"}, headers={"X-Request-Id": "filter-test"})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["totalCount"] == 1
    assert observed == [(1, 1000, {"reqNumber": "R1", "testMtdCode": "A23142", "fromDate": "2026-10-01",
                                 "toDate": "2026-10-31", "testChargerNo": 7, "testChargerId": "hong",
                                 "assetNumber": "ASSET/42", "testState": "7,3"})]
    incomplete = client.get("/api/v1/requests?source=lims&includeCompleted=false", headers={"X-Request-Id": "filter-test-2"})
    assert incomplete.status_code == 200 and observed[-1][2] == {"testState": "1,3,5,7"}
    for query, status in [
        ({"source": "lims", "includeCompleted": "false", "testState": "0,5"}, 400),
        ({"source": "cache", "assetNumber": "AX-42"}, 400),
        ({"source": "cache", "testChargerId": "hong"}, 400),
        ({"source": "lims", "fromDate": "2026-02-30"}, 400),
        ({"source": "lims", "pageSize": 1001}, 400),
    ]:
        rejected = client.get("/api/v1/requests", params=query, headers={"X-Request-Id": "invalid-filter-test"})
        assert rejected.status_code == status, rejected.text
    assert len(observed) == 2

"""Read-only LIMS request lookup through the trusted Spring relay.

The relay owns the external integration token. Filtered results are never written
back to the full request snapshot in the Edge database.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from ipaddress import ip_address
import json
import math
from pathlib import Path
import re
import ssl
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .request_scope import RequestScope

import httpx

from .errors import ApiException


FILTER_KEYS = ("matchMode", "reqNumber", "testMtdCode", "fromDate", "toDate", "testChargerNo", "testChargerId", "assetNumber", "testState")
EQUIPMENT_CODE_FIELDS = ("equipmentCode", "deviceCode", "instrumentCode")
INCOMPLETE_TEST_STATES = ("1", "3", "5", "7")
TEST_STATES = frozenset(("0", *INCOMPLETE_TEST_STATES, "9"))
NUMBER_FIELDS = frozenset({
    "reqResultNo", "reqState", "reqTypeNo", "customerNo", "reqUserNo",
    "smpResultNo", "smpResultState", "testMtdResultNo", "testMtdNo", "testState", "outputOrder",
})
TEXT_FIELDS = frozenset({
    "reqNumber", "reqDate", "reqStateName", "reqTypeCode", "reqTypeName",
    "projectCode", "custReqName", "customerName", "reqUserName", "smpResultName",
    "testMtdCode", "testMtdName", "testChargerName",
})


def validate_filters(values: dict[str, Any]) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    for key in FILTER_KEYS:
        value = values.get(key)
        if value is None or (value == "" and key not in {"assetNumber", "testState"}):
            continue
        if key == "testChargerNo":
            if type(value) is not int or not 1 <= value <= 9223372036854775807:
                raise ApiException(400, "INVALID_REQUEST_FILTER", "시험담당자 사용자번호가 올바르지 않습니다.")
        else:
            if not isinstance(value, str) or not value.strip() or len(value) > 200 or any(ord(c) < 32 for c in value):
                raise ApiException(400, "INVALID_REQUEST_FILTER", "의뢰 조회 조건을 확인해 주세요.")
            value = value.strip()
            if key == "matchMode":
                if value not in {"EXACT", "CONTAINS"}:
                    raise ApiException(400, "INVALID_REQUEST_FILTER", "검색 방식은 정확 일치 또는 부분 일치를 선택해 주세요.")
                # Only CONTAINS is documented upstream. Omission retains exact lookup.
                if value == "EXACT":
                    continue
            if key == "testState":
                states = [code.strip(" ") for code in value.split(",")]
                if not TEST_STATES.issuperset(states):
                    raise ApiException(400, "INVALID_REQUEST_FILTER", "시험 상태는 0, 1, 3, 5, 7, 9 중에서 쉼표로 구분해 주세요.")
                value = ",".join(dict.fromkeys(states))
            if key in {"fromDate", "toDate"}:
                try:
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                        raise ValueError()
                    date.fromisoformat(value)
                except ValueError:
                    raise ApiException(400, "INVALID_REQUEST_FILTER", "조회 기간은 YYYY-MM-DD 형식의 유효한 날짜여야 합니다.") from None
        filters[key] = value
    if filters.get("matchMode") == "CONTAINS":
        for key in ("reqNumber", "testMtdCode", "assetNumber"):
            if key in filters and len(filters[key]) < 3:
                raise ApiException(400, "INVALID_REQUEST_FILTER", "부분 검색 시 의뢰번호·시험방법코드·장비 자산번호는 각각 3자 이상 입력해 주세요.")
    if filters.get("fromDate") and filters.get("toDate") and filters["fromDate"] > filters["toDate"]:
        raise ApiException(400, "INVALID_REQUEST_FILTER", "조회 시작일은 종료일보다 늦을 수 없습니다.")
    return filters


def _protocol_error() -> ApiException:
    return ApiException(502, "LIMS_REQUESTS_INVALID_RESPONSE", "LIMS 의뢰조회 응답 형식이 올바르지 않습니다.")


def validate_response(body: object, page: int, size: int) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise _protocol_error()
    count, items = body.get("totalCount"), body.get("items")
    if (type(count) is not int or not 0 <= count <= 9223372036854775807
            or type(body.get("page")) is not int or body["page"] != page
            or type(body.get("size")) is not int or body["size"] != size
            or not isinstance(items, list) or len(items) > size):
        raise _protocol_error()
    if (items and count < (page - 1) * size + len(items)) or (not items and count > (page - 1) * size):
        raise _protocol_error()
    normalized_items = []
    for item in items:
        if not isinstance(item, dict) or not (NUMBER_FIELDS | TEXT_FIELDS) <= item.keys():
            raise _protocol_error()
        for key in NUMBER_FIELDS:
            if item[key] is not None and type(item[key]) is not int:
                raise _protocol_error()
        for key in TEXT_FIELDS:
            if item[key] is not None and not isinstance(item[key], str):
                raise _protocol_error()
        # Only explicit relay equipment identifiers can populate the transfer form.
        # Empty optional values mean unavailable; assetNumber remains a query filter.
        optional_codes = (*EQUIPMENT_CODE_FIELDS, "axTestMtdCode")
        normalized = {key: value for key, value in item.items() if key not in optional_codes}
        for key in optional_codes:
            value = item.get(key)
            if value is None:
                continue
            if not isinstance(value, str) or len(value) > 100 or re.search(r"[\x00-\x1f\x7f]", value):
                raise _protocol_error()
            if value.strip():
                normalized[key] = value.strip()
        normalized_items.append(normalized)
    return {**body, "items": normalized_items}


@dataclass(frozen=True)
class LimsRequestsClient:
    url: str
    internal_key: str = field(repr=False)
    ca_bundle: Path | None = None
    allow_loopback_http: bool = False
    connect_timeout_seconds: float = 2.0
    read_timeout_seconds: float = 4.0
    transport: httpx.BaseTransport | None = field(default=None, repr=False)

    @classmethod
    def from_settings(cls, settings: Any) -> LimsRequestsClient:
        return cls(
            url=settings.lims_requests_url,
            internal_key=settings.sso_lims_internal_key,
            ca_bundle=settings.sso_lims_ca_bundle,
            allow_loopback_http=settings.sso_lims_allow_loopback_http,
            connect_timeout_seconds=settings.sso_connect_timeout_seconds,
            read_timeout_seconds=settings.sso_read_timeout_seconds,
        )

    def fetch(self, page: int, size: int, filters: dict[str, Any]) -> dict[str, Any]:
        if type(page) is not int or page < 1 or type(size) is not int or not 1 <= size <= 1000:
            raise ApiException(400, "INVALID_REQUEST_FILTER", "페이지는 1 이상, 페이지 크기는 1~1000이어야 합니다.")
        body = self._read_json({"page": page, "size": size, **validate_filters(filters)})
        return validate_response(body, page, size)

    def fetch_recipient(self, request_number: str) -> dict[str, Any]:
        # Spring derives the internal request ID and resolves its mailbox in LIMS.
        # Never take an ID, mailbox, or destination URL from the browser.
        body = self._read_json({"reqNumber": request_number}, recipient=True)
        if (not isinstance(body, dict) or body.get("reqNumber") != request_number
                or type(body.get("reqResultNo")) is not int or not 1 <= body["reqResultNo"] <= 9223372036854775807
                or "reqUserName" not in body):
            raise _protocol_error()
        name, email = body["reqUserName"], body.get("email")
        if name is not None and (not isinstance(name, str) or len(name) > 200 or re.search(r"[\x00-\x1f\x7f]", name)):
            raise _protocol_error()
        from .report_mail import addresses
        try:
            if not isinstance(email, str) or len(email) > 254 or addresses(email) != [email]:
                raise ValueError()
        except ValueError:
            raise _protocol_error() from None
        return {"reqNumber": request_number, "reqResultNo": body["reqResultNo"], "reqUserName": name, "email": email}

    def _read_json(self, params: dict[str, Any], *, recipient: bool = False) -> Any:
        try:
            url = httpx.URL(self.url.strip())
            loopback_http = False
            if self.allow_loopback_http and url.scheme == "http":
                try:
                    loopback_http = ip_address(url.host).is_loopback
                except ValueError:
                    pass
            if (not url.host or url.userinfo or url.query or url.fragment
                    or (url.scheme != "https" and not loopback_http)
                    or not isinstance(self.internal_key, str) or not 32 <= len(self.internal_key) <= 512
                    or any(ord(c) < 33 or ord(c) > 126 for c in self.internal_key)
                    or not all(math.isfinite(t) and 0 < t <= 60 for t in (self.connect_timeout_seconds, self.read_timeout_seconds))):
                raise ValueError()
            if recipient:
                url = url.copy_with(path=url.path.rstrip("/") + "/recipient")
            tls = ssl.create_default_context(cafile=str(self.ca_bundle.expanduser()) if self.ca_bundle else None)
        except (TypeError, ValueError, OSError, ssl.SSLError, httpx.InvalidURL):
            raise ApiException(503, "LIMS_REQUESTS_NOT_CONFIGURED", "LIMS 실시간 의뢰조회 연결 설정을 확인해 주세요.") from None
        try:
            with httpx.Client(verify=tls, trust_env=False, follow_redirects=False, transport=self.transport,
                              timeout=httpx.Timeout(self.read_timeout_seconds, connect=self.connect_timeout_seconds)) as client:
                with client.stream("GET", url, params=params,
                                   headers={"Accept": "application/json", "X-RITASAX-Internal-Key": self.internal_key}) as response:
                    if response.status_code != 200:
                        if recipient and response.status_code in {404, 409}:
                            raise ApiException(response.status_code, "MAIL_RECIPIENT_NOT_RESOLVED",
                                               "LIMS에서 해당 의뢰의 수신자 이메일을 하나로 확인할 수 없습니다.")
                        if response.status_code == 400:
                            raise ApiException(400, "INVALID_REQUEST_FILTER", "LIMS에서 조회 조건을 수락하지 않았습니다. 조건을 확인해 주세요.")
                        if response.status_code == 503:
                            raise ApiException(503, "LIMS_REQUESTS_NOT_CONFIGURED", "LIMS 실시간 의뢰조회가 아직 준비되지 않았습니다.")
                        raise ApiException(502, "LIMS_REQUESTS_UNAVAILABLE", "LIMS 의뢰조회 중계가 정상 응답하지 않았습니다.", retryable=True)
                    limit = 8 * 1024 if recipient else 4 * 1024 * 1024
                    content = bytearray()
                    for chunk in response.iter_bytes(chunk_size=16384):
                        content.extend(chunk)
                        if len(content) > limit:
                            raise _protocol_error()
        except (httpx.HTTPError, OSError, ssl.SSLError):
            raise ApiException(502, "LIMS_REQUESTS_UNAVAILABLE", "LIMS 의뢰조회 중계에 연결할 수 없습니다.", retryable=True) from None
        try:
            return json.loads(content)
        except (ValueError, UnicodeError):
            raise _protocol_error() from None


# Scope filtering happens before Edge pagination. Never filter a single upstream
# page and pretend its unfiltered totalCount belongs to the current experiment.
SCOPE_BATCH_SIZE = 1000
SCOPE_MAX_ITEMS = 10000
SCOPE_MAX_CALLS = 32
SCOPE_DEADLINE_SECONDS = 20.0


def scoped_requests(client: LimsRequestsClient, scope: "RequestScope", page: int, size: int, filters: dict[str, Any]) -> dict[str, Any]:
    import time
    queries, trusted_assets = scope.query_filters(filters)

    def accepts(item):
        if scope.matches(item.get("testMtdCode"), item.get("testMtdName")):
            return True
        return (trusted_assets and not scope.methods
                and scope.mapped_project(str(item.get("testMtdCode") or "")) in (None, scope.project))

    def check_condition(items, query):
        # Treat substring operators as literal text. The upstream owns SQL escaping.
        # An ignored filter must not produce a plausible but incorrect scoped total.
        if query.get("matchMode") == "CONTAINS":
            for key in ("reqNumber", "testMtdCode"):
                needle = query.get(key)
                if needle and any(needle.casefold() not in str(item.get(key) or "").casefold() for item in items):
                    raise _protocol_error()
            return
        code = query.get("testMtdCode")
        if code and any(str(item.get("testMtdCode") or "").strip().lower() != code.lower() for item in items):
            raise _protocol_error()

    if len(queries) == 1:
        code = queries[0].get("testMtdCode")
        guaranteed = bool(queries[0].get("matchMode") != "CONTAINS" and code
                          and (scope.methods or scope.mapped_project(code) == scope.project))
        if guaranteed:
            result = client.fetch(page, size, queries[0])
            check_condition(result["items"], queries[0])
            return {**result, "scopeNote": scope.note(assets=trusted_assets)}

    started = time.monotonic()
    calls = scanned = 0
    matches: dict[tuple, dict[str, Any]] = {}

    def too_broad():
        return ApiException(422, "REQUEST_SCOPE_TOO_BROAD",
                            "실험별 의뢰 조회 범위가 큽니다. 의뢰번호·기간 등으로 범위를 좁히거나 관리자에게 시험코드·장비 매핑을 요청하세요. 일부 결과만 표시하지 않았습니다.")

    def changed():
        return ApiException(409, "LIMS_REQUESTS_CHANGED", "조회 중 LIMS 의뢰 목록이 변경되었습니다. 다시 조회해 주세요.", retryable=True)

    def item_key(item):
        ids = tuple(item.get(k) for k in ("reqResultNo", "smpResultNo", "testMtdResultNo"))
        return ("id", *ids) if all(x is not None for x in ids) else ("row", json.dumps(item, ensure_ascii=False, sort_keys=True))

    for query in queries:
        upstream_page, total, seen = 1, None, set()
        while True:
            if calls >= SCOPE_MAX_CALLS or time.monotonic() - started >= SCOPE_DEADLINE_SECONDS:
                raise too_broad()
            result = client.fetch(upstream_page, SCOPE_BATCH_SIZE, query)
            calls += 1
            count = result["totalCount"]
            if total is not None and total != count:
                raise changed()
            if total is None and scanned + count > SCOPE_MAX_ITEMS:
                raise too_broad()
            total = count
            items = result["items"]
            offset = (upstream_page - 1) * SCOPE_BATCH_SIZE
            if len(items) != min(SCOPE_BATCH_SIZE, max(0, total - offset)):
                raise changed()
            check_condition(items, query)
            scanned += len(items)
            for item in items:
                key = item_key(item)
                if key in seen:
                    raise changed()
                seen.add(key)
                if not accepts(item):
                    continue
                if key in matches and matches[key] != item:
                    raise changed()
                matches[key] = item
            if offset + len(items) >= total:
                break
            upstream_page += 1
    rows = list(matches.values())
    rows.sort(key=lambda r: (r.get("outputOrder") if r.get("outputOrder") is not None else 2147483647,
                             r.get("smpResultNo") or 0, r.get("testMtdResultNo") or 0,
                             json.dumps(r, sort_keys=True)))
    rows.sort(key=lambda r: (r.get("reqDate") or "", r.get("reqNumber") or ""), reverse=True)
    start = (page - 1) * size
    return {"items": rows[start:start + size], "totalCount": len(rows), "page": page, "size": size,
            "scopeNote": scope.note(assets=trusted_assets)}

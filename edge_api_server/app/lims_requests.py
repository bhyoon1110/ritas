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
from typing import Any

import httpx

from .errors import ApiException


FILTER_KEYS = ("reqNumber", "testMtdCode", "fromDate", "toDate", "testChargerNo", "testChargerId", "assetNumber", "testState")
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
    for item in items:
        if not isinstance(item, dict) or not (NUMBER_FIELDS | TEXT_FIELDS) <= item.keys():
            raise _protocol_error()
        for key in NUMBER_FIELDS:
            if item[key] is not None and type(item[key]) is not int:
                raise _protocol_error()
        for key in TEXT_FIELDS:
            if item[key] is not None and not isinstance(item[key], str):
                raise _protocol_error()
    return body


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

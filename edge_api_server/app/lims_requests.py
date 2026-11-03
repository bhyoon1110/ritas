"""Read-only LIMS v4 request lookup through the trusted Spring relay.

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


FILTER_KEYS = ("reqNumber", "testMtdCode", "fromDate", "toDate", "testChargerNo", "testChargerId")
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
        if value is None or value == "":
            continue
        if key == "testChargerNo":
            if type(value) is not int or not 1 <= value <= 9223372036854775807:
                raise ApiException(400, "INVALID_REQUEST_FILTER", "시험담당자 사용자번호가 올바르지 않습니다.")
        else:
            if not isinstance(value, str) or not value.strip() or len(value) > 200 or any(ord(c) < 32 for c in value):
                raise ApiException(400, "INVALID_REQUEST_FILTER", "의뢰 조회 조건을 확인해 주세요.")
            value = value.strip()
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

    def fetch(self, page: int, size: int, filters: dict[str, Any]) -> dict[str, Any]:
        if type(page) is not int or page < 1 or type(size) is not int or not 1 <= size <= 1000:
            raise ApiException(400, "INVALID_REQUEST_FILTER", "페이지는 1 이상, 페이지 크기는 1~1000이어야 합니다.")
        filters = validate_filters(filters)
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
            tls = ssl.create_default_context(cafile=str(self.ca_bundle.expanduser()) if self.ca_bundle else None)
        except (TypeError, ValueError, OSError, ssl.SSLError):
            raise ApiException(503, "LIMS_REQUESTS_NOT_CONFIGURED", "LIMS 실시간 의뢰조회 연결 설정을 확인해 주세요.") from None
        try:
            with httpx.Client(verify=tls, trust_env=False, follow_redirects=False, transport=self.transport,
                              timeout=httpx.Timeout(self.read_timeout_seconds, connect=self.connect_timeout_seconds)) as client:
                with client.stream("GET", url, params={"page": page, "size": size, **filters},
                                   headers={"Accept": "application/json", "X-RITASAX-Internal-Key": self.internal_key}) as response:
                    if response.status_code != 200:
                        if response.status_code == 400:
                            raise ApiException(400, "INVALID_REQUEST_FILTER", "LIMS에서 조회 조건을 수락하지 않았습니다. 조건을 확인해 주세요.")
                        if response.status_code == 503:
                            raise ApiException(503, "LIMS_REQUESTS_NOT_CONFIGURED", "LIMS 실시간 의뢰조회가 아직 준비되지 않았습니다.")
                        raise ApiException(502, "LIMS_REQUESTS_UNAVAILABLE", "LIMS 의뢰조회 중계가 정상 응답하지 않았습니다.", retryable=True)
                    content = bytearray()
                    for chunk in response.iter_bytes(chunk_size=16384):
                        content.extend(chunk)
                        if len(content) > 4 * 1024 * 1024:
                            raise _protocol_error()
        except (httpx.HTTPError, OSError, ssl.SSLError):
            raise ApiException(502, "LIMS_REQUESTS_UNAVAILABLE", "LIMS 의뢰조회 중계에 연결할 수 없습니다.", retryable=True) from None
        try:
            body = json.loads(content)
        except (ValueError, UnicodeError):
            raise _protocol_error() from None
        return validate_response(body, page, size)

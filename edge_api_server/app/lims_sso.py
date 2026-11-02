"""ID/password verification through the trusted Spring Boot LIMS relay."""
from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import ip_address
import json
from pathlib import Path
import ssl

import httpx

from .posco_sso import (
    PoscoSsoConfigurationError,
    PoscoSsoProtocolError,
    PoscoSsoUnavailableError,
)


class LimsSsoConfigurationError(PoscoSsoConfigurationError):
    """The relay configuration is incomplete or unsafe."""


class LimsSsoUnavailableError(PoscoSsoUnavailableError):
    """The relay did not return an authentication decision."""


class LimsSsoProtocolError(PoscoSsoProtocolError):
    """The relay response does not follow the agreed JSON contract."""


@dataclass(frozen=True)
class LimsSsoClient:
    validation_url: str
    internal_key: str = field(repr=False)
    ca_bundle: Path | None = None
    allow_loopback_http: bool = False
    connect_timeout_seconds: float = 2.0
    read_timeout_seconds: float = 4.0
    transport: httpx.BaseTransport | None = field(default=None, repr=False)

    def _validated_url(self) -> httpx.URL:
        try:
            url = httpx.URL(self.validation_url.strip())
        except (TypeError, ValueError):
            raise LimsSsoConfigurationError("LIMS SSO 중계 URL 형식이 올바르지 않습니다.") from None
        loopback_http = False
        if self.allow_loopback_http and url.scheme == "http":
            try:
                # Host names are intentionally excluded, including localhost.
                loopback_http = ip_address(url.host).is_loopback
            except ValueError:
                pass
        if (not url.host or url.userinfo or url.query or url.fragment
                or (url.scheme != "https" and not loopback_http)):
            raise LimsSsoConfigurationError(
                "LIMS SSO 중계에는 HTTPS가 필요합니다. 명시적으로 허용한 로컬 IP만 HTTP를 사용할 수 있습니다."
            )
        return url

    def _ssl_context(self) -> ssl.SSLContext:
        try:
            return ssl.create_default_context(
                cafile=str(self.ca_bundle.expanduser()) if self.ca_bundle else None
            )
        except (OSError, ssl.SSLError):
            raise LimsSsoConfigurationError("LIMS SSO 중계 CA 인증서를 읽을 수 없습니다.") from None

    def validate(self, username: str, password: str) -> bool:
        url = self._validated_url()
        if (not isinstance(self.internal_key, str) or not 32 <= len(self.internal_key) <= 512
                or any(ord(char) < 33 or ord(char) > 126 for char in self.internal_key)):
            raise LimsSsoConfigurationError("LIMS SSO 중계 내부 인증 키 설정을 확인해 주세요.")
        if self.connect_timeout_seconds <= 0 or self.read_timeout_seconds <= 0:
            raise LimsSsoConfigurationError("LIMS SSO 제한시간은 0보다 커야 합니다.")

        try:
            with httpx.Client(
                verify=self._ssl_context(),
                timeout=httpx.Timeout(self.read_timeout_seconds, connect=self.connect_timeout_seconds),
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                with client.stream(
                    "POST", url,
                    json={"userId": username, "password": password},
                    headers={"Accept": "application/json", "X-RITASAX-Internal-Key": self.internal_key},
                ) as response:
                    if response.is_redirect:
                        raise LimsSsoProtocolError("LIMS SSO 중계가 리다이렉트로 응답했습니다.")
                    if response.status_code != 200:
                        # Never surface an upstream body, URL, or header containing secrets.
                        raise LimsSsoUnavailableError("LIMS SSO 중계가 정상 상태로 응답하지 않았습니다.")
                    content = bytearray()
                    for chunk in response.iter_bytes(chunk_size=1024):
                        content.extend(chunk)
                        if len(content) > 4096:
                            raise LimsSsoProtocolError("LIMS SSO 중계 응답이 예상보다 큽니다.")
        except (httpx.HTTPError, OSError, ssl.SSLError):
            raise LimsSsoUnavailableError("LIMS SSO 중계에 연결할 수 없습니다.") from None

        try:
            body = json.loads(content)
        except (ValueError, UnicodeError):
            raise LimsSsoProtocolError("LIMS SSO 중계 JSON 응답을 해석할 수 없습니다.") from None
        if isinstance(body, dict):
            if body.get("result") == "SUCCESS" and not body.get("errorCode"):
                return True
            if body.get("result") == "FAIL" and body.get("errorCode") in {
                "INVALID_CREDENTIAL", "ACCOUNT_LOCKED", "ACCOUNT_DISABLED",
            }:
                return False
        raise LimsSsoProtocolError("LIMS SSO 중계가 문서화되지 않은 값을 반환했습니다.")

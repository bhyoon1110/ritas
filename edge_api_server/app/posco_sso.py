from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import ssl

import httpx


class PoscoSsoError(Exception):
    """POSCO SSO validation failed before a T/F decision was available."""


class PoscoSsoConfigurationError(PoscoSsoError):
    """The server-side POSCO SSO configuration is incomplete or unsafe."""


class PoscoSsoUnavailableError(PoscoSsoError):
    """The configured POSCO SSO endpoint could not be reached successfully."""


class PoscoSsoProtocolError(PoscoSsoError):
    """The endpoint returned something other than the documented T/F response."""


@dataclass(frozen=True)
class PoscoSsoClient:
    validation_url: str
    sid: str
    ca_bundle: Path | None = None
    connect_timeout_seconds: float = 2.0
    read_timeout_seconds: float = 4.0
    transport: httpx.BaseTransport | None = None

    def _validated_url(self) -> httpx.URL:
        try:
            url = httpx.URL(self.validation_url.strip())
        except (TypeError, ValueError) as exc:
            raise PoscoSsoConfigurationError(
                "POSCO SSO 검증 URL 형식이 올바르지 않습니다."
            ) from exc
        if (
            url.scheme != "https"
            or not url.host
            or url.userinfo
            or url.query
            or url.fragment
        ):
            raise PoscoSsoConfigurationError(
                "POSCO SSO 검증 URL은 사용자 정보, query, fragment가 없는 HTTPS URL이어야 합니다."
            )
        return url

    def _ssl_context(self) -> ssl.SSLContext:
        if self.ca_bundle is None:
            return ssl.create_default_context()
        bundle = self.ca_bundle.expanduser()
        if not bundle.is_file():
            raise PoscoSsoConfigurationError(
                "POSCO SSO CA 인증서 파일을 찾을 수 없습니다."
            )
        try:
            return ssl.create_default_context(cafile=str(bundle))
        except (OSError, ssl.SSLError) as exc:
            raise PoscoSsoConfigurationError(
                "POSCO SSO CA 인증서 파일을 읽을 수 없습니다."
            ) from exc

    def validate(self, username: str, password: str) -> bool:
        """Return True/False only for the endpoint's documented T/F response.

        Credentials are form-encoded by httpx, never added to a URL, and are not
        retained on this client instance.
        """

        url = self._validated_url()
        sid = self.sid.strip()
        if not sid:
            raise PoscoSsoConfigurationError("POSCO SSO SID가 설정되지 않았습니다.")
        if self.connect_timeout_seconds <= 0 or self.read_timeout_seconds <= 0:
            raise PoscoSsoConfigurationError(
                "POSCO SSO 연결 및 응답 제한시간은 0보다 커야 합니다."
            )
        timeout = httpx.Timeout(
            self.read_timeout_seconds,
            connect=self.connect_timeout_seconds,
        )
        try:
            with httpx.Client(
                verify=self._ssl_context(),
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                response = client.post(
                    url,
                    data={
                        "username": username,
                        "password": password,
                        "sid": sid,
                    },
                    headers={"Accept": "text/plain"},
                )
        except PoscoSsoConfigurationError:
            raise
        except (httpx.HTTPError, OSError, ssl.SSLError) as exc:
            raise PoscoSsoUnavailableError(
                "POSCO SSO 서버에 연결하지 못했습니다."
            ) from exc

        if response.is_redirect:
            raise PoscoSsoProtocolError(
                "POSCO SSO 서버가 예상하지 않은 리다이렉트로 응답했습니다."
            )
        if not 200 <= response.status_code < 300:
            raise PoscoSsoUnavailableError(
                "POSCO SSO 서버가 정상 상태로 응답하지 않았습니다."
            )
        if len(response.content) > 1024:
            raise PoscoSsoProtocolError(
                "POSCO SSO 서버 응답이 예상보다 큽니다."
            )
        result = response.text.strip().upper()
        if result == "T":
            return True
        if result == "F":
            return False
        raise PoscoSsoProtocolError(
            "POSCO SSO 서버가 문서화되지 않은 값을 반환했습니다."
        )

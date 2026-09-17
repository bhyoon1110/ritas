from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from app.posco_sso import (
    PoscoSsoClient,
    PoscoSsoConfigurationError,
    PoscoSsoProtocolError,
    PoscoSsoUnavailableError,
)


VALIDATION_URL = "https://uswpsso.posco.net/idms/U61/jsp/userValidSSOM.jsp"


def test_posco_sso_posts_encoded_credentials_and_accepts_t() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["content_type"] = request.headers.get("content-type")
        captured["form"] = parse_qs(request.content.decode("utf-8"))
        return httpx.Response(200, text="  T\n")

    client = PoscoSsoClient(
        validation_url=VALIDATION_URL,
        sid="test-system-sid",
        transport=httpx.MockTransport(handler),
    )

    assert client.validate("employee01", "p&ss=word+1!") is True
    assert captured == {
        "method": "POST",
        "url": VALIDATION_URL,
        "content_type": "application/x-www-form-urlencoded",
        "form": {
            "username": ["employee01"],
            "password": ["p&ss=word+1!"],
            "sid": ["test-system-sid"],
        },
    }


def test_posco_sso_accepts_documented_f_result() -> None:
    client = PoscoSsoClient(
        validation_url=VALIDATION_URL,
        sid="issued-sid",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="F")
        ),
    )

    assert client.validate("employee", "password") is False


@pytest.mark.parametrize("body", ["", "TRUE", "OK", "TF"])
def test_posco_sso_rejects_undocumented_responses(body: str) -> None:
    client = PoscoSsoClient(
        validation_url=VALIDATION_URL,
        sid="issued-sid",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=body)
        ),
    )

    with pytest.raises(PoscoSsoProtocolError):
        client.validate("employee", "password")


def test_posco_sso_does_not_follow_redirects() -> None:
    client = PoscoSsoClient(
        validation_url=VALIDATION_URL,
        sid="issued-sid",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                302,
                headers={"Location": "https://example.invalid/capture"},
            )
        ),
    )

    with pytest.raises(PoscoSsoProtocolError):
        client.validate("employee", "password")


def test_posco_sso_rejects_insecure_or_incomplete_configuration(tmp_path: Path) -> None:
    with pytest.raises(PoscoSsoConfigurationError):
        PoscoSsoClient("http://swpsso.posco.net/validate", "sid").validate(
            "employee", "password"
        )
    with pytest.raises(PoscoSsoConfigurationError):
        PoscoSsoClient(VALIDATION_URL, "").validate("employee", "password")
    with pytest.raises(PoscoSsoConfigurationError):
        PoscoSsoClient(VALIDATION_URL + "?redirect=1", "sid").validate(
            "employee", "password"
        )
    with pytest.raises(PoscoSsoConfigurationError):
        PoscoSsoClient(
            VALIDATION_URL,
            "sid",
            ca_bundle=tmp_path / "missing-ca.pem",
        ).validate("employee", "password")


def test_posco_sso_maps_network_failures_without_exposing_credentials() -> None:
    secret = "never-print-this-password"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    client = PoscoSsoClient(
        validation_url=VALIDATION_URL,
        sid="issued-sid",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(PoscoSsoUnavailableError) as captured:
        client.validate("employee", secret)
    assert secret not in str(captured.value)

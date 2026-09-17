from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .models import ApiError
from .time_utils import isoformat_kst


_SENSITIVE_FIELD_NAMES = {
    "authorization",
    "clientsecret",
    "confirmpassword",
    "currentpassword",
    "newpassword",
    "password",
    "secret",
    "token",
}


def _normalized_field_name(value: object) -> str:
    return "".join(character for character in str(value).lower() if character.isalnum())


def _redact_sensitive_input(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, BaseException):
        return str(value)
    if isinstance(value, Mapping):
        return {
            key: (
                "[REDACTED]"
                if _normalized_field_name(key) in _SENSITIVE_FIELD_NAMES
                else _redact_sensitive_input(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_input(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_sensitive_input(item) for item in value)
    return str(value)


def redact_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove credentials from Pydantic validation details and error archives."""

    redacted: list[dict[str, Any]] = []
    for error in errors:
        raw_item = dict(error)
        location = raw_item.get("loc") or ()
        item = {
            key: _redact_sensitive_input(value)
            for key, value in raw_item.items()
        }
        sensitive_location = any(
            _normalized_field_name(part) in _SENSITIVE_FIELD_NAMES
            for part in location
        )
        if "input" in item:
            item["input"] = (
                "[REDACTED]"
                if sensitive_location
                else _redact_sensitive_input(item["input"])
            )
        redacted.append(item)
    return redacted


class ApiException(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        job_id: str | None = None,
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.job_id = job_id
        self.details = details


def error_response(request: Request, exc: ApiException) -> JSONResponse:
    payload = ApiError(
        timestamp=isoformat_kst(),
        status=exc.status_code,
        code=exc.code,
        message=exc.message,
        requestId=request.headers.get("X-Request-Id"),
        jobId=exc.job_id,
        retryable=exc.retryable,
        details=exc.details,
    )
    content = payload.model_dump(by_alias=True, exclude_none=True)
    event_id = getattr(request.state, "error_event_id", None)
    if event_id:
        content["errorEventId"] = str(event_id)
        content["errorFeedbackUrl"] = f"/error-feedback/{event_id}"
    return JSONResponse(
        status_code=exc.status_code,
        content=content,
    )


async def api_exception_handler(request: Request, exc: ApiException) -> JSONResponse:
    return error_response(request, exc)


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return error_response(
        request,
        ApiException(
            400,
            "REQUEST_VALIDATION_FAILED",
            "요청 형식이 올바르지 않습니다.",
            details=redact_validation_errors(exc.errors()),
        ),
    )

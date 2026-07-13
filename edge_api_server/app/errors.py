from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .models import ApiError
from .time_utils import isoformat_kst


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
            details=exc.errors(),
        ),
    )

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, Header, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from rist_common import get_logger

from .config import Settings
from .database import Database
from .errors import (
    ApiException,
    api_exception_handler,
    validation_exception_handler,
)
from .llm_client import LlmError, LocalLlmClient
from .models import (
    CompleteUploadRequest,
    CompleteUploadResponse,
    CreateJobRequest,
    CreateJobResponse,
    GenerateReportRequest,
    GenerateReportResponse,
    JobStatusResponse,
    UploadFileResponse,
)
from .service import EdgeService

logger = get_logger(__name__)

# idempotency_records.idempotency_key 컬럼 길이와 일치(초과 시 DB 오류 대신 400 반환).
MAX_IDEMPOTENCY_KEY_LENGTH = 128


def required_request_id(
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
) -> str:
    if not x_request_id or not x_request_id.strip():
        raise ApiException(
            400, "MISSING_REQUEST_ID", "X-Request-Id 헤더가 필요합니다."
        )
    return x_request_id.strip()


def required_idempotency_key(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> str:
    if not idempotency_key or not idempotency_key.strip():
        raise ApiException(
            400,
            "MISSING_IDEMPOTENCY_KEY",
            "Idempotency-Key 헤더가 필요합니다.",
        )
    key = idempotency_key.strip()
    if len(key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise ApiException(
            400,
            "INVALID_IDEMPOTENCY_KEY",
            f"Idempotency-Key는 {MAX_IDEMPOTENCY_KEY_LENGTH}자 이하여야 합니다.",
        )
    return key


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    database = Database.from_settings(resolved_settings)
    service = EdgeService(resolved_settings, database)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        database.close()

    app = FastAPI(
        title="RIST Experiment PC - Edge API",
        version="1.0.0",
        description="실험 PC 파일 bundle 수신 및 보고서 생성 요청 API",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.database = database
    app.state.service = service
    logger.info(
        "Edge API 애플리케이션 구성 완료 (env=%s, base_url=%s)",
        resolved_settings.environment,
        resolved_settings.edge_public_base_url,
    )

    app.add_exception_handler(ApiException, api_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str | int]:
        return {
            "status": "ok",
            "environment": resolved_settings.environment,
            "baseUrl": resolved_settings.edge_public_base_url,
            "port": resolved_settings.api_port,
            "llmModel": resolved_settings.llm_model,
        }

    @app.get("/health/llm", tags=["system"])
    def llm_health() -> dict:
        client = LocalLlmClient(
            resolved_settings.llm_base_url,
            resolved_settings.llm_model,
            resolved_settings.llm_timeout_seconds,
            resolved_settings.llm_temperature,
            resolved_settings.llm_max_tokens,
            True,
        )
        try:
            model = client.get_model_info(force=True)
        except LlmError as exc:
            logger.warning("LLM 헬스체크 실패: %s - %s", exc.code, exc.message)
            raise ApiException(
                503 if exc.retryable else 500,
                exc.code,
                exc.message,
                retryable=exc.retryable,
            ) from exc
        finally:
            client.close()
        return {
            "status": "ok",
            "baseUrl": resolved_settings.llm_base_url,
            "model": model.get("id"),
            "maxModelLength": model.get("max_model_len"),
            "temperature": resolved_settings.llm_temperature,
            "maxTokens": resolved_settings.llm_max_tokens,
            "visionEnabled": resolved_settings.llm_include_images,
        }

    @app.post(
        "/api/v1/jobs",
        response_model=CreateJobResponse,
        response_model_by_alias=True,
        status_code=201,
        tags=["jobs"],
    )
    def create_job(
        payload: CreateJobRequest,
        request: Request,
        response: Response,
        _: str = Depends(required_request_id),
        idempotency_key: str = Depends(required_idempotency_key),
    ) -> dict:
        status_code, result = service.create_job(
            payload,
            request.client.host if request.client else None,
            idempotency_key,
        )
        response.status_code = status_code
        return result

    @app.post(
        "/api/v1/jobs/{job_id}/files",
        response_model=UploadFileResponse,
        response_model_by_alias=True,
        status_code=201,
        tags=["files"],
    )
    def upload_file(
        job_id: str,
        response: Response,
        file: UploadFile = File(...),
        relative_path: str = Form(..., alias="relativePath"),
        size_bytes: int = Form(..., alias="sizeBytes"),
        sha256: str = Form(...),
        last_modified_at: str | None = Form(default=None, alias="lastModifiedAt"),
        _: str = Depends(required_request_id),
        idempotency_key: str = Depends(required_idempotency_key),
    ) -> dict:
        status_code, result = service.upload_file(
            job_id,
            file,
            relative_path,
            size_bytes,
            sha256,
            last_modified_at,
            idempotency_key,
        )
        response.status_code = status_code
        return result

    @app.post(
        "/api/v1/jobs/{job_id}/uploads/complete",
        response_model=CompleteUploadResponse,
        response_model_by_alias=True,
        tags=["files"],
    )
    def complete_upload(
        job_id: str,
        payload: CompleteUploadRequest,
        response: Response,
        _: str = Depends(required_request_id),
        idempotency_key: str = Depends(required_idempotency_key),
    ) -> dict:
        status_code, result = service.complete_upload(
            job_id, payload, idempotency_key
        )
        response.status_code = status_code
        return result

    @app.post(
        "/api/v1/jobs/{job_id}/report",
        response_model=GenerateReportResponse,
        response_model_by_alias=True,
        status_code=202,
        tags=["reports"],
    )
    def request_report(
        job_id: str,
        payload: GenerateReportRequest,
        response: Response,
        _: str = Depends(required_request_id),
        idempotency_key: str = Depends(required_idempotency_key),
    ) -> dict:
        status_code, result = service.request_report(
            job_id, payload, idempotency_key
        )
        response.status_code = status_code
        return result

    @app.get(
        "/api/v1/jobs/{job_id}",
        response_model=JobStatusResponse,
        response_model_by_alias=True,
        tags=["jobs"],
    )
    def get_job(
        job_id: str,
        _: str = Depends(required_request_id),
    ) -> dict:
        return service.status_response(job_id)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception(
            "처리되지 않은 서버 오류 (%s %s)",
            request.method,
            request.url.path,
        )
        api_exc = ApiException(
            500,
            "INTERNAL_SERVER_ERROR",
            "서버 내부 오류가 발생했습니다.",
            retryable=True,
        )
        return await api_exception_handler(request, api_exc)

    return app

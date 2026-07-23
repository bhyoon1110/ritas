from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class JobPk(ApiModel):
    request_number: str = Field(alias="requestNumber", min_length=1, max_length=100)
    experiment_code: str = Field(alias="experimentCode", min_length=1, max_length=50)
    equipment_code: str = Field(alias="equipmentCode", min_length=1, max_length=100)
    operator_id: str = Field(alias="operatorId", min_length=1, max_length=100)

    @field_validator("*")
    @classmethod
    def strip_values(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class SourcePc(ApiModel):
    host_name: str = Field(alias="hostName", min_length=1, max_length=255)
    declared_ip_address: str | None = Field(
        default=None, alias="declaredIpAddress", max_length=64
    )
    client_version: str | None = Field(
        default=None, alias="clientVersion", max_length=100
    )


class BundleDeclaration(ApiModel):
    file_count: int = Field(alias="fileCount", ge=1)
    total_size_bytes: int = Field(alias="totalSizeBytes", ge=0)


class CreateJobRequest(ApiModel):
    pk: JobPk
    source_pc: SourcePc = Field(alias="sourcePc")
    # 이전 실험 PC와의 호환을 위해 허용한다. 최종 bundle 선언은 uploads/complete가 기준이다.
    bundle: BundleDeclaration | None = None


class CreateJobResponse(ApiModel):
    job_id: str = Field(alias="jobId")
    status: str
    created_at: str = Field(alias="createdAt")
    upload_expires_at: str = Field(alias="uploadExpiresAt")
    reused: bool = False


class UploadFileResponse(ApiModel):
    file_id: str = Field(alias="fileId")
    relative_path: str = Field(alias="relativePath")
    size_bytes: int = Field(alias="sizeBytes")
    sha256: str
    status: str
    uploaded_at: str = Field(alias="uploadedAt")


class FileListResponse(ApiModel):
    job_id: str = Field(alias="jobId")
    files: list[UploadFileResponse]


class RequestSummary(ApiModel):
    request_result_no: int = Field(alias="requestResultNo")
    request_number: str | None = Field(alias="requestNumber")
    request_date: str | None = Field(alias="requestDate")
    request_state: int = Field(alias="requestState")
    request_state_name: str | None = Field(alias="requestStateName")
    request_type_no: int = Field(alias="requestTypeNo")
    request_type_code: str | None = Field(alias="requestTypeCode")
    request_type_name: str = Field(alias="requestTypeName")
    project_code: str | None = Field(alias="projectCode")
    customer_request_name: str | None = Field(alias="customerRequestName")
    customer_no: int | None = Field(alias="customerNo")
    customer_name: str | None = Field(alias="customerName")
    request_user_no: int | None = Field(alias="requestUserNo")
    request_user_name: str | None = Field(alias="requestUserName")
    sample_result_no: int = Field(alias="sampleResultNo")
    sample_name: str = Field(alias="sampleName")
    sample_state: int = Field(alias="sampleState")
    test_method_result_no: int = Field(alias="testMethodResultNo")
    test_method_no: int = Field(alias="testMethodNo")
    test_method_code: str | None = Field(alias="testMethodCode")
    test_method_name: str = Field(alias="testMethodName")
    test_state: int = Field(alias="testState")
    test_charger_name: str | None = Field(alias="testChargerName")
    output_order: int | None = Field(alias="outputOrder")
    synced_at: str = Field(alias="syncedAt")
    experiment_code: str | None = Field(alias="experimentCode")
    experiment_name: str = Field(alias="experimentName")


class RequestListResponse(ApiModel):
    page: int
    page_size: int = Field(alias="pageSize")
    experiment_type: str | None = Field(default=None, alias="experimentType")
    items: list[RequestSummary]


class BundleFile(ApiModel):
    relative_path: str = Field(alias="relativePath", min_length=1, max_length=1024)
    size_bytes: int = Field(alias="sizeBytes", ge=0)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class CompleteUploadRequest(ApiModel):
    file_count: int = Field(alias="fileCount", ge=1)
    total_size_bytes: int = Field(alias="totalSizeBytes", ge=0)
    files: list[BundleFile] = Field(min_length=1)


class CompleteUploadResponse(ApiModel):
    job_id: str = Field(alias="jobId")
    status: str
    verified_file_count: int = Field(alias="verifiedFileCount")
    verified_at: str = Field(alias="verifiedAt")


class ReportOptions(ApiModel):
    report_format: Literal["PPTX", "PDF", "HTML"] | None = Field(
        default=None, alias="reportFormat"
    )
    report_formats: list[Literal["PPTX", "PDF", "HTML"]] | None = Field(
        default=None, alias="reportFormats", min_length=1, max_length=3
    )
    include_raw_files: bool = Field(default=False, alias="includeRawFiles")

    @model_validator(mode="after")
    def normalize_report_formats(self) -> "ReportOptions":
        formats = self.report_formats or [self.report_format or "PPTX"]
        if len(set(formats)) != len(formats):
            raise ValueError("reportFormats에는 중복된 형식을 넣을 수 없습니다.")
        self.report_formats = formats
        self.report_format = formats[0]
        return self


class GenerateReportRequest(ApiModel):
    requested_at: str | None = Field(default=None, alias="requestedAt")
    options: ReportOptions = Field(default_factory=ReportOptions)


class GenerateReportResponse(ApiModel):
    job_id: str = Field(alias="jobId")
    status: str
    accepted_at: str = Field(alias="acceptedAt")


class RegenerateReportSignalRequest(ApiModel):
    requested_at: str | None = Field(default=None, alias="requestedAt")
    requested_by: str | None = Field(
        default=None, alias="requestedBy", max_length=100
    )
    reason: str | None = Field(default=None, max_length=1000)
    prompt: str = Field(min_length=1, max_length=4000)

    @field_validator("prompt")
    @classmethod
    def strip_regeneration_prompt(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class RegenerateReportSignalResponse(ApiModel):
    signal_id: str = Field(alias="signalId")
    report_id: str = Field(alias="reportId")
    source_job_id: str | None = Field(alias="sourceJobId")
    status: Literal["RECEIVED"]
    received_at: str = Field(alias="receivedAt")
    execution_queued: bool = Field(alias="executionQueued")


class ErrorDetail(ApiModel):
    code: str
    message: str
    retryable: bool


class JobStatusResponse(ApiModel):
    job_id: str = Field(alias="jobId")
    pk: JobPk
    status: str
    progress: int
    created_at: str = Field(alias="createdAt")
    processing_started_at: str | None = Field(
        default=None, alias="processingStartedAt"
    )
    completed_at: str | None = Field(default=None, alias="completedAt")
    error: ErrorDetail | None = None


class ApiError(ApiModel):
    timestamp: str
    status: int
    code: str
    message: str
    request_id: str | None = Field(default=None, alias="requestId")
    job_id: str | None = Field(default=None, alias="jobId")
    retryable: bool
    details: Any | None = None

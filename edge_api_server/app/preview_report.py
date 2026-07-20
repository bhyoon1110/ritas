from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import html
import json
import shutil
import tempfile
from threading import Lock, Thread
from time import perf_counter
from uuid import uuid4
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import Settings
from .error_archive import ErrorArchive, record_background_error
from .report.builders import get_builder
from .llm_client import LlmError, LocalLlmClient
from .report import annotator
from .report.model import ReportFigure
from .report.package import build_report_package
from .report.renderers import convert_pptx_to_pdf, render_report_formats
from .database import Database
from .report_queue import (
    ReportQueueError,
    enqueue_report_package,
    persist_preview_report_package,
)
from .storage import atomic_write_json
from .usage_archive import UsageArchive, record_background_usage


@dataclass(frozen=True)
class RawSeries:
    label: str
    axis: list[float]
    values: list[float]


@dataclass
class PreviewReportJob:
    job_id: str
    filename: str
    status: str
    stage: str
    progress_pct: int
    message: str
    created_at: datetime
    updated_at: datetime
    tmp_root: Path | None = None
    package_path: Path | None = None
    error: str | None = None
    error_event_id: str | None = None

    def to_dict(self, *, download_url: str | None = None) -> dict[str, Any]:
        payload = {
            "jobId": self.job_id,
            "status": self.status,
            "stage": self.stage,
            "progressPct": self.progress_pct,
            "message": self.message,
            "filename": self.filename,
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
        }
        if self.error:
            payload["error"] = self.error
        if self.error_event_id:
            payload["errorEventId"] = self.error_event_id
            payload["errorFeedbackUrl"] = f"/error-feedback/{self.error_event_id}"
        if download_url and self.status == "completed":
            payload["downloadUrl"] = download_url
        return payload


class PreviewReportSendRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

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


class PreviewReportJobStore:
    def __init__(self, *, ttl_seconds: int = 3600) -> None:
        self._jobs: dict[str, PreviewReportJob] = {}
        self._lock = Lock()
        self._ttl = timedelta(seconds=ttl_seconds)

    def create(self, *, filename: str) -> PreviewReportJob:
        self.cleanup_expired()
        now = _utc_now()
        job = PreviewReportJob(
            job_id=uuid4().hex,
            filename=filename,
            status="queued",
            stage="queued",
            progress_pct=0,
            message="보고서 생성 요청을 접수했습니다.",
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> PreviewReportJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(
        self,
        job_id: str,
        *,
        status: str | None = None,
        stage: str | None = None,
        progress_pct: int | None = None,
        message: str | None = None,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if status is not None:
                job.status = status
            if stage is not None:
                job.stage = stage
            if progress_pct is not None:
                job.progress_pct = max(0, min(100, int(progress_pct)))
            if message is not None:
                job.message = message
            job.updated_at = _utc_now()

    def complete(self, job_id: str, *, tmp_root: Path, package_path: Path) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                cleanup_preview_report(tmp_root)
                return
            job.status = "completed"
            job.stage = "completed"
            job.progress_pct = 100
            job.message = "보고서가 완성되었습니다."
            job.tmp_root = tmp_root
            job.package_path = package_path
            job.updated_at = _utc_now()

    def fail(self, job_id: str, error: str, *, error_event_id: str | None = None) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "failed"
            job.stage = "failed"
            job.progress_pct = 100
            job.message = "보고서 생성에 실패했습니다."
            job.error = error
            job.error_event_id = error_event_id
            job.updated_at = _utc_now()

    def remove(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.pop(job_id, None)
        if job is not None and job.tmp_root is not None:
            cleanup_preview_report(job.tmp_root)

    def cleanup_expired(self) -> None:
        cutoff = _utc_now() - self._ttl
        expired: list[str] = []
        with self._lock:
            for job_id, job in self._jobs.items():
                if job.updated_at < cutoff:
                    expired.append(job_id)
        for job_id in expired:
            self.remove(job_id)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _kst_now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=9))).replace(microsecond=0).isoformat()


def _is_preview_placeholder(value: str | None) -> bool:
    return str(value or "").strip() in {"", "-", "WEB-PREVIEW", "web-preview"}


def _normalize_metadata_key(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _metadata_value(metadata: Any, aliases: set[str]) -> str:
    if not isinstance(metadata, dict):
        return ""
    normalized_aliases = {_normalize_metadata_key(alias) for alias in aliases}
    for key, value in metadata.items():
        if _normalize_metadata_key(str(key)) in normalized_aliases:
            text = str(value or "").strip()
            if text and text != "(미기재)":
                return text
    return ""


def _preview_equipment_code(
    experiment_code: str,
    analysis_payload: dict[str, Any],
    equipment_code: str,
) -> str:
    if not _is_preview_placeholder(equipment_code):
        return equipment_code
    aliases = {
        "장비모델",
        "장비 모델",
        "equipment model",
        "instrument model",
        "instrument",
        "spectrometer",
        "model",
    }
    for key in (
        "experimentConditions",
        "experiment_conditions",
        "conditions",
        "metadata",
        "environment",
        "experimentEnvironment",
        "experiment_environment",
    ):
        value = _metadata_value(analysis_payload.get(key), aliases)
        if value:
            return value
    for sample in analysis_payload.get("samples") or []:
        if isinstance(sample, dict):
            value = _metadata_value(sample.get("metadata"), aliases)
            if value:
                return value
    if experiment_code.upper().replace("_", "-") in {"FTIR", "FT-IR", "IR"}:
        return "FT-IR 장비"
    if experiment_code.upper() in {"RAMAN", "RIN", "RIN-RAMAN"}:
        return "Raman 장비"
    return ""


def preview_report_job_store(app: Any) -> PreviewReportJobStore:
    store = getattr(app.state, "preview_report_job_store", None)
    if not isinstance(store, PreviewReportJobStore):
        store = PreviewReportJobStore()
        app.state.preview_report_job_store = store
    return store


def send_preview_report_package(
    *,
    settings: Any | None,
    database: Database | None,
    job: PreviewReportJob,
    payload: PreviewReportSendRequest,
) -> dict[str, Any]:
    if job.status != "completed" or job.package_path is None:
        raise ValueError("보고서가 아직 완성되지 않았습니다.")
    if not job.package_path.is_file():
        raise FileNotFoundError("보고서 파일이 만료되었습니다.")
    if database is None:
        raise ReportQueueError(
            "REPORT_QUEUE_DATABASE_UNAVAILABLE",
            "보고서 전송 큐를 등록할 데이터베이스가 연결되어 있지 않습니다.",
            retryable=True,
        )
    resolved_settings = settings or Settings.from_env()
    shared_package = persist_preview_report_package(
        settings=resolved_settings,
        report_id=job.job_id,
        experiment_code=payload.experiment_code,
        source_path=job.package_path,
    )
    result = enqueue_report_package(
        settings=resolved_settings,
        database=database,
        report_id=job.job_id,
        package_path=shared_package,
        source_job_id=None,
        request_number=payload.request_number,
        experiment_code=payload.experiment_code,
        equipment_code=payload.equipment_code,
        operator_id=payload.operator_id,
    )
    return {
        **result,
        "sent": False,
        "jobId": job.job_id,
        "requestNumber": payload.request_number,
        "experimentCode": payload.experiment_code,
        "equipmentCode": payload.equipment_code,
        "operatorId": payload.operator_id,
    }


def parse_analysis_payload(analysis_json: str, figure_json: str | None = None) -> dict[str, Any]:
    payload = json.loads(analysis_json)
    if not isinstance(payload, dict):
        raise ValueError("analysis_json은 JSON 객체여야 합니다.")
    if figure_json:
        figure = json.loads(figure_json)
        if isinstance(figure, dict):
            payload["figure"] = figure
    return payload


def decode_figure_image(data_url: str) -> bytes:
    if not data_url.startswith("data:image/"):
        raise ValueError("figure_image는 data:image URL이어야 합니다.")
    try:
        _prefix, encoded = data_url.split(",", 1)
        return base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("figure_image를 디코딩할 수 없습니다.") from exc


def build_preview_report_package(
    *,
    experiment_code: str,
    analysis_payload: dict[str, Any],
    raw_series: list[RawSeries],
    figure_image: bytes,
    request_number: str = "",
    equipment_code: str = "",
    operator_id: str = "",
    settings: Any | None = None,
    progress: Callable[[str, int, str], None] | None = None,
) -> tuple[Path, Path]:
    tmp_root = Path(tempfile.mkdtemp(prefix="rist-preview-report-"))
    try:
        raw_series = _filter_raw_series_for_payload(
            raw_series,
            analysis_payload,
            filter_axis_range=_is_ftir_experiment(experiment_code),
        )
        job_root = tmp_root / "job"
        report_dir = job_root / "report"
        processed_dir = job_root / "processed"
        report_dir.mkdir(parents=True)
        processed_dir.mkdir(parents=True)

        _emit_progress(progress, "input", 20, "보고서 입력 데이터를 정리하는 중입니다.")
        analysis_path = processed_dir / "analysis-result.json"
        atomic_write_json(analysis_path, analysis_payload)
        image_path = processed_dir / "current_graph.png"
        image_path.write_bytes(figure_image)
        (report_dir / "current_graph.png").write_bytes(figure_image)

        job = {
            "job_id": "web-preview-report",
            "request_number": "" if _is_preview_placeholder(request_number) else request_number,
            "experiment_code": experiment_code,
            "equipment_code": _preview_equipment_code(
                experiment_code,
                analysis_payload,
                equipment_code,
            ),
            "operator_id": "" if _is_preview_placeholder(operator_id) else operator_id,
            "root_relative_path": "web-preview-report",
            "_generated_at": _kst_now_iso(),
        }
        _emit_progress(progress, "document", 40, "보고서 본문을 구성하는 중입니다.")
        builder = get_builder(experiment_code)
        document = builder.build(
            job,
            [{"relativePath": analysis_path.name, "data": analysis_payload}],
        )
        spec = builder.llm_slots(
            job,
            [{"relativePath": analysis_path.name, "data": analysis_payload}],
        )
        if spec is not None:
            document.ensure_auxiliary_texts(spec.fallback)
        if spec is not None and settings is not None:
            _emit_progress(progress, "llm", 58, "LLM 문안을 생성하는 중입니다.")
            llm_client = LocalLlmClient(
                settings.llm_base_url,
                settings.llm_model,
                settings.llm_timeout_seconds,
                settings.llm_temperature,
                settings.llm_max_tokens,
                settings.llm_validate_model,
            )
            try:
                slots = annotator.annotate(
                    settings,
                    llm_client,
                    spec,
                    processed_dir=processed_dir,
                    logs_dir=job_root / "logs",
                )
                document.apply_llm_slots(slots)
                document.llm_used = True
            except LlmError as exc:
                document.llm_error = f"{exc.code}: {exc.message}"
            finally:
                llm_client.close()
        else:
            _emit_progress(progress, "llm", 58, "규칙 기반 문안을 적용하는 중입니다.")
        document.figures = [
            ReportFigure(
                figure_id="current-graph",
                title="현재 그래프 화면",
                path=str(image_path),
            )
        ]

        _emit_progress(progress, "raw", 72, "raw 데이터를 엑셀로 정리하는 중입니다.")
        atomic_write_json(report_dir / "report.json", document.to_dict())
        (report_dir / "report.md").write_text(document.to_markdown(), encoding="utf-8")
        _write_email_body(document, report_dir)
        write_raw_data_xlsx(
            report_dir / "raw_data.xlsx",
            raw_series,
            axis_header=(
                "wavenumber(cm-1)/샘플명"
                if _is_ftir_experiment(experiment_code)
                else "Axis"
            ),
        )
        _emit_progress(progress, "render", 86, "PPT/PDF/HTML 보고서를 렌더링하는 중입니다.")
        render_report_formats(
            document,
            report_dir,
            ["PPTX", "PDF", "HTML"],
            pdf_font_path=_preview_pdf_font_path(settings),
        )
        _emit_progress(progress, "convert", 90, "PPT 보고서를 PDF로 변환하는 중입니다.")
        convert_pptx_to_pdf(
            report_dir / "report.pptx",
            report_dir / "report-from-pptx.pdf",
        )
        _emit_progress(progress, "package", 95, "최종 ZIP을 패키징하는 중입니다.")
        package = build_report_package(
            report_dir,
            job_root / "input",
            include_raw_files=False,
        )
        return tmp_root, package
    except Exception:
        cleanup_preview_report(tmp_root)
        raise


def _preview_pdf_font_path(settings: Any | None) -> Path | None:
    configured = getattr(settings, "pdf_font_path", None)
    if configured is not None:
        return configured
    return Settings.from_env().pdf_font_path


def run_preview_report_job(
    store: PreviewReportJobStore,
    job_id: str,
    *,
    experiment_code: str,
    analysis_payload: dict[str, Any],
    raw_series_factory: Callable[[], list[RawSeries]],
    figure_image: bytes,
    request_number: str = "",
    equipment_code: str = "",
    operator_id: str = "",
    settings: Any | None = None,
    error_archive: ErrorArchive | None = None,
    usage_archive: UsageArchive | None = None,
    usage_client_context: dict[str, str | None] | None = None,
    error_project: str = "EDGE",
    failure_file_blobs: list[tuple[str, bytes]] | None = None,
) -> None:
    started = perf_counter()
    try:
        store.update(
            job_id,
            status="running",
            stage="raw",
            progress_pct=10,
            message="raw 데이터를 읽는 중입니다.",
        )
        raw_series = raw_series_factory()
        raw_series = _filter_raw_series_for_payload(
            raw_series,
            analysis_payload,
            filter_axis_range=_is_ftir_experiment(experiment_code),
        )

        def progress(stage: str, progress_pct: int, message: str) -> None:
            store.update(
                job_id,
                status="running",
                stage=stage,
                progress_pct=progress_pct,
                message=message,
            )

        tmp_root, package = build_preview_report_package(
            experiment_code=experiment_code,
            analysis_payload=analysis_payload,
            raw_series=raw_series,
            figure_image=figure_image,
            request_number=request_number,
            equipment_code=equipment_code,
            operator_id=operator_id,
            settings=settings,
            progress=progress,
        )
        store.complete(job_id, tmp_root=tmp_root, package_path=package)
        record_background_usage(
            usage_archive,
            project=error_project,
            action="보고서 생성 완료",
            result="success",
            duration_ms=round((perf_counter() - started) * 1000),
            job_id=job_id,
            endpoint=f"/background/{experiment_code.lower()}/report/jobs/{job_id}",
            request_number=request_number,
            experiment_code=experiment_code,
            equipment_code=equipment_code,
            operator_id=operator_id,
            file_name=package.name,
            file_size_bytes=package.stat().st_size if package.is_file() else None,
            client_context=usage_client_context,
        )
    except Exception as exc:
        event_id = record_background_error(
            error_archive,
            project=error_project,
            code=f"{error_project.replace('-', '_')}_REPORT_BUILD_FAILED",
            message=str(exc),
            exception=exc,
            job_id=job_id,
            file_blobs=failure_file_blobs or (),
        )
        store.fail(job_id, str(exc), error_event_id=event_id)
        record_background_usage(
            usage_archive,
            project=error_project,
            action="보고서 생성 실패",
            result="failure",
            duration_ms=round((perf_counter() - started) * 1000),
            job_id=job_id,
            endpoint=f"/background/{experiment_code.lower()}/report/jobs/{job_id}",
            request_number=request_number,
            experiment_code=experiment_code,
            equipment_code=equipment_code,
            operator_id=operator_id,
            client_context=usage_client_context,
        )


def _sample_label_keys(value: Any) -> set[str]:
    text = str(value or "").strip()
    if not text:
        return set()
    keys = {text.casefold()}
    stem = Path(text).stem.strip()
    if stem:
        keys.add(stem.casefold())
    return keys


def _payload_raw_series_keys(analysis_payload: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    samples = analysis_payload.get("samples")
    if not isinstance(samples, list):
        return keys
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        for field in ("label", "fileName", "name", "sample"):
            keys.update(_sample_label_keys(sample.get(field)))
    return keys


def _is_ftir_experiment(experiment_code: str) -> bool:
    normalized = experiment_code.upper().replace("_", "-")
    return normalized in {"FTIR", "FT-IR", "IR"}


def _figure_x_range(analysis_payload: dict[str, Any]) -> tuple[float, float] | None:
    figure = analysis_payload.get("figure")
    if not isinstance(figure, dict):
        return None
    layout = figure.get("layout")
    if not isinstance(layout, dict):
        return None
    xaxis = layout.get("xaxis")
    raw_range: Any = None
    if isinstance(xaxis, dict):
        raw_range = xaxis.get("range")
    if raw_range is None:
        raw_range = layout.get("xaxis.range")
    if (
        not isinstance(raw_range, list | tuple)
        or len(raw_range) != 2
    ):
        return None
    try:
        left = float(raw_range[0])
        right = float(raw_range[1])
    except (TypeError, ValueError):
        return None
    if not (left == left and right == right):
        return None
    return min(left, right), max(left, right)


def _filter_raw_series_for_axis_range(
    raw_series: list[RawSeries],
    axis_range: tuple[float, float] | None,
) -> list[RawSeries]:
    if axis_range is None:
        return raw_series
    lo, hi = axis_range
    filtered: list[RawSeries] = []
    for item in raw_series:
        axis: list[float] = []
        values: list[float] = []
        for x, y in zip(item.axis, item.values):
            try:
                x_value = float(x)
            except (TypeError, ValueError):
                continue
            if lo <= x_value <= hi:
                axis.append(x_value)
                values.append(y)
        filtered.append(RawSeries(item.label, axis, values))
    return filtered


def _filter_raw_series_for_payload(
    raw_series: list[RawSeries],
    analysis_payload: dict[str, Any],
    *,
    filter_axis_range: bool = False,
) -> list[RawSeries]:
    samples = analysis_payload.get("samples")
    if isinstance(samples, list) and not samples:
        return []
    keys = _payload_raw_series_keys(analysis_payload)
    if keys:
        filtered = [
            item
            for item in raw_series
            if _sample_label_keys(item.label) & keys
        ]
        raw_series = filtered if filtered else raw_series
    if filter_axis_range:
        raw_series = _filter_raw_series_for_axis_range(
            raw_series,
            _figure_x_range(analysis_payload),
        )
    return raw_series


def start_preview_report_job(
    store: PreviewReportJobStore,
    job_id: str,
    **kwargs: Any,
) -> None:
    thread = Thread(
        target=run_preview_report_job,
        args=(store, job_id),
        kwargs=kwargs,
        daemon=True,
    )
    thread.start()


def cleanup_preview_report(tmp_root: Path) -> None:
    shutil.rmtree(tmp_root, ignore_errors=True)


def _emit_progress(
    progress: Callable[[str, int, str], None] | None,
    stage: str,
    progress_pct: int,
    message: str,
) -> None:
    if progress is not None:
        progress(stage, progress_pct, message)


def _write_email_body(document: Any, report_dir: Path) -> None:
    subject = document.auxiliary_texts.get("email_subject", "").strip()
    body = document.auxiliary_texts.get("email_body", "").strip()
    if not subject and not body:
        return
    lines: list[str] = []
    if subject:
        lines.extend([f"# {subject}", ""])
    if body:
        lines.append(body)
    (report_dir / "email_body.md").write_text(
        "\n".join(lines).rstrip() + "\n",
        encoding="utf-8",
    )


def write_raw_data_xlsx(
    path: Path,
    series: list[RawSeries],
    *,
    axis_header: str = "Axis",
) -> None:
    axis_values = sorted(
        {
            round(float(value), 8)
            for item in series
            for value in item.axis
        }
    )
    lookups = []
    for item in series:
        lookups.append(
            {
                round(float(x), 8): y
                for x, y in zip(item.axis, item.values)
            }
        )
    rows: list[list[Any]] = [[axis_header, *[item.label for item in series]]]
    for axis in axis_values:
        rows.append([axis, *[lookup.get(axis, "") for lookup in lookups]])
    _write_xlsx(path, rows)


def _write_xlsx(path: Path, rows: list[list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _xlsx_content_types())
        archive.writestr("_rels/.rels", _xlsx_root_rels())
        archive.writestr("xl/workbook.xml", _xlsx_workbook())
        archive.writestr("xl/_rels/workbook.xml.rels", _xlsx_workbook_rels())
        archive.writestr("xl/worksheets/sheet1.xml", _xlsx_sheet(rows))


def _xlsx_content_types() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""


def _xlsx_root_rels() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""


def _xlsx_workbook() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Raw Data" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""


def _xlsx_workbook_rels() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""


def _xlsx_sheet(rows: list[list[Any]]) -> str:
    body = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for col_index, value in enumerate(row, start=1):
            if value == "":
                continue
            cells.append(_xlsx_cell(row_index, col_index, value))
        body.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(body)}</sheetData>'
        "</worksheet>"
    )


def _xlsx_cell(row_index: int, col_index: int, value: Any) -> str:
    ref = f"{_xlsx_col(col_index)}{row_index}"
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"><v>{float(value):.12g}</v></c>'
    text = html.escape(str(value), quote=False)
    return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'


def _xlsx_col(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters

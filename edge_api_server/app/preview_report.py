from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import html
import json
import shutil
import tempfile
from threading import Lock, Thread
from uuid import uuid4
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .report.builders import get_builder
from .llm_client import LlmError, LocalLlmClient
from .report import annotator
from .report.model import ReportFigure
from .report.package import build_report_package
from .report.renderers import render_report_formats
from .storage import atomic_write_json


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
        if download_url and self.status == "completed":
            payload["downloadUrl"] = download_url
        return payload


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

    def fail(self, job_id: str, error: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "failed"
            job.stage = "failed"
            job.progress_pct = 100
            job.message = "보고서 생성에 실패했습니다."
            job.error = error
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


def preview_report_job_store(app: Any) -> PreviewReportJobStore:
    store = getattr(app.state, "preview_report_job_store", None)
    if not isinstance(store, PreviewReportJobStore):
        store = PreviewReportJobStore()
        app.state.preview_report_job_store = store
    return store


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
    request_number: str = "WEB-PREVIEW",
    equipment_code: str = "WEB-PREVIEW",
    operator_id: str = "web-preview",
    settings: Any | None = None,
    progress: Callable[[str, int, str], None] | None = None,
) -> tuple[Path, Path]:
    tmp_root = Path(tempfile.mkdtemp(prefix="rist-preview-report-"))
    try:
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
            "request_number": request_number,
            "experiment_code": experiment_code,
            "equipment_code": equipment_code,
            "operator_id": operator_id,
            "root_relative_path": "web-preview-report",
            "_generated_at": "",
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
        write_raw_data_xlsx(report_dir / "raw_data.xlsx", raw_series)
        _emit_progress(progress, "render", 86, "PPT/PDF/HTML 보고서를 렌더링하는 중입니다.")
        render_report_formats(document, report_dir, ["PPTX", "PDF", "HTML"])
        _emit_progress(progress, "package", 95, "전달 ZIP을 패키징하는 중입니다.")
        package = build_report_package(
            report_dir,
            job_root / "input",
            include_raw_files=False,
        )
        return tmp_root, package
    except Exception:
        cleanup_preview_report(tmp_root)
        raise


def run_preview_report_job(
    store: PreviewReportJobStore,
    job_id: str,
    *,
    experiment_code: str,
    analysis_payload: dict[str, Any],
    raw_series_factory: Callable[[], list[RawSeries]],
    figure_image: bytes,
    settings: Any | None = None,
) -> None:
    try:
        store.update(
            job_id,
            status="running",
            stage="raw",
            progress_pct=10,
            message="raw 데이터를 읽는 중입니다.",
        )
        raw_series = raw_series_factory()

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
            settings=settings,
            progress=progress,
        )
        store.complete(job_id, tmp_root=tmp_root, package_path=package)
    except Exception as exc:
        store.fail(job_id, str(exc))


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


def write_raw_data_xlsx(path: Path, series: list[RawSeries]) -> None:
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
    rows: list[list[Any]] = [["Axis", *[item.label for item in series]]]
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

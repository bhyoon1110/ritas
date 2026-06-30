"""Raman upload workspace HTML and preview API."""

from __future__ import annotations

from pathlib import Path
import os

import plotly
import plotly.graph_objects as go
from fastapi import APIRouter, BackgroundTasks, FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

try:
    from rin.raman import preprocess as raman_preprocess_module
    from rin.raman.preprocess import SUPPORTED_SUFFIXES, load_raman_raw_samples
    from rin.raman.web_analysis import RamanAnalysisError, analyze_raman_files
except ModuleNotFoundError:  # pragma: no cover - installed via edge requirements
    from raman import preprocess as raman_preprocess_module
    from raman.preprocess import SUPPORTED_SUFFIXES, load_raman_raw_samples
    from raman.web_analysis import RamanAnalysisError, analyze_raman_files
from ftir.assignment_libraries import (
    AssignmentLibraryError,
    AssignmentLibraryStore,
    MAX_LIBRARY_BYTES,
)
from rist_common import get_logger
from rist_common.plotting import fig_to_responsive_html, peak_sensitivity_js

from . import assignment_suggestions
from .assignment_suggestions import AssignmentSuggestionRequest
from .config import Settings
from .errors import ApiException, api_exception_handler, validation_exception_handler
from .preview_report import (
    PreviewReportJob,
    RawSeries,
    build_preview_report_package,
    cleanup_preview_report,
    decode_figure_image,
    parse_analysis_payload,
    preview_report_job_store,
    start_preview_report_job,
)


PLOT_DIV_ID = "raman-plot"
MAX_RAMAN_PREVIEW_FILES = 10
MAX_RAMAN_PREVIEW_FILE_BYTES = 20 * 1024 * 1024
MAX_RAMAN_PREVIEW_TOTAL_BYTES = 50 * 1024 * 1024
DEFAULT_RAMAN_ASSIGNMENT_LIBRARY_ID = "general-raman"
DEFAULT_RAMAN_FUNC_GROUPS_PATH = (
    Path(raman_preprocess_module.__file__).resolve().parent
    / "resources"
    / "func_groups.csv"
)
DEFAULT_RAMAN_ASSIGNMENT_LIBRARY_DIR = (
    Path(__file__).resolve().parents[1] / "data" / "raman_assignment_libraries"
)
logger = get_logger(__name__)
router = APIRouter()


class RamanPeakAssignmentWrite(BaseModel):
    centerWavenumber: float
    tolerance: float
    name: str
    color: str = "#64748b"
    note: str = ""


class RamanAssignmentLibraryWrite(BaseModel):
    name: str
    description: str = ""
    assignments: list[RamanPeakAssignmentWrite]


class RamanAssignmentLibraryCreate(RamanAssignmentLibraryWrite):
    id: str


class RamanAssignmentLibrarySuggest(BaseModel):
    material: str
    libraryId: str | None = None
    libraryName: str | None = None


def llm_settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or Settings.from_env()


def assignment_library_store() -> AssignmentLibraryStore:
    configured = os.getenv(
        "RIST_RAMAN_ASSIGNMENT_LIBRARY_DIR",
        str(DEFAULT_RAMAN_ASSIGNMENT_LIBRARY_DIR),
    )
    return AssignmentLibraryStore(
        Path(configured),
        DEFAULT_RAMAN_FUNC_GROUPS_PATH,
        default_library_id=DEFAULT_RAMAN_ASSIGNMENT_LIBRARY_ID,
    )


def assignment_library_delete_enabled() -> bool:
    return os.getenv(
        "RIST_RAMAN_ASSIGNMENT_LIBRARY_DELETE_ENABLED",
        "false",
    ).lower() in {"1", "true", "yes", "on"}


def raise_assignment_library_api(exc: AssignmentLibraryError) -> None:
    if exc.code == "ASSIGNMENT_LIBRARY_NOT_FOUND":
        status_code = 404
    elif exc.code == "ASSIGNMENT_LIBRARY_EXISTS":
        status_code = 409
    elif exc.code == "ASSIGNMENT_LIBRARY_TOO_LARGE":
        status_code = 413
    elif exc.code.startswith("LLM_") or exc.code.startswith(
        "ASSIGNMENT_SUGGESTION_INVALID"
    ):
        status_code = 502
    else:
        status_code = 400
    raise ApiException(status_code, exc.code, exc.message) from exc


def _uploaded_raman_files(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    if not files:
        raise ApiException(400, "RAMAN_FILES_REQUIRED", "Raman raw 파일이 필요합니다.")
    if len(files) > MAX_RAMAN_PREVIEW_FILES:
        raise ApiException(
            400,
            "TOO_MANY_RAMAN_FILES",
            f"한 번에 최대 {MAX_RAMAN_PREVIEW_FILES}개 파일을 분석할 수 있습니다.",
        )

    uploaded: list[tuple[str, bytes]] = []
    total_bytes = 0
    supported = ", ".join(sorted(SUPPORTED_SUFFIXES))
    for upload in files:
        raw_filename = (upload.filename or "").replace("\\", "/")
        filename = Path(raw_filename).name
        suffix = Path(filename).suffix.casefold()
        if not filename or suffix not in SUPPORTED_SUFFIXES:
            raise ApiException(
                400,
                "INVALID_RAMAN_EXTENSION",
                f"지원하지 않는 Raman raw 파일입니다: {filename or '(이름 없음)'} ({supported})",
            )
        content = upload.file.read(MAX_RAMAN_PREVIEW_FILE_BYTES + 1)
        if not content:
            raise ApiException(400, "EMPTY_RAMAN_FILE", f"빈 파일입니다: {filename}")
        if len(content) > MAX_RAMAN_PREVIEW_FILE_BYTES:
            raise ApiException(
                413,
                "RAMAN_FILE_TOO_LARGE",
                f"Raman raw 파일은 20MB 이하여야 합니다: {filename}",
            )
        total_bytes += len(content)
        if total_bytes > MAX_RAMAN_PREVIEW_TOTAL_BYTES:
            raise ApiException(
                413,
                "RAMAN_UPLOAD_TOO_LARGE",
                "한 번에 업로드하는 Raman raw 파일의 총 크기는 50MB 이하여야 합니다.",
            )
        uploaded.append((filename, content))
    return uploaded


def _build_raman_raw_series(uploaded: list[tuple[str, bytes]]) -> list[RawSeries]:
    raw_series: list[RawSeries] = []
    used_labels: set[str] = set()
    for filename, content in uploaded:
        samples = load_raman_raw_samples(filename, content)
        for sample in samples:
            label = sample.label or Path(filename).stem
            base = label
            suffix = 2
            while label.casefold() in used_labels:
                label = f"{base} ({suffix})"
                suffix += 1
            used_labels.add(label.casefold())
            raw_series.append(
                RawSeries(
                    label=label,
                    axis=[float(value) for value in sample.frame["shift"].to_list()],
                    values=[
                        float(value)
                        for value in sample.frame["intensity"].to_list()
                    ],
                )
            )
    return raw_series


def _report_job_response(job: PreviewReportJob, *, prefix: str) -> dict:
    download_url = f"{prefix}/{job.job_id}/download"
    return job.to_dict(download_url=download_url)


def plotly_asset_path() -> Path:
    return Path(plotly.__file__).resolve().parent / "package_data" / "plotly.min.js"


def _blank_figure() -> go.Figure:
    figure = go.Figure()
    figure.update_layout(
        title=dict(
            text="Raman Peak Analysis",
            font=dict(size=18),
            x=0.01,
            y=0.98,
            yanchor="top",
        ),
        xaxis=dict(
            title="Raman Shift (cm⁻¹)",
            range=[0, 4000],
            showgrid=True,
            gridcolor="#e8e8e8",
            tickmode="linear",
            dtick=500,
            minor=dict(showgrid=True, gridcolor="#f4f4f4"),
        ),
        yaxis=dict(
            title="Normalized Intensity",
            range=[-0.05, 1.65],
            showgrid=True,
            gridcolor="#e8e8e8",
        ),
        plot_bgcolor="white",
        paper_bgcolor="#fafafa",
        height=720,
        hovermode="closest",
        margin=dict(l=70, r=70, t=105, b=70),
        meta={"ristPeakLabels": []},
    )
    return figure


_PAGE_STYLE = """
<link rel="icon" href="data:,">
<style>
html, body {
  margin: 0;
  min-height: 100%;
  background: #f8fafc;
  color: #1f2933;
  font-family: Arial, "Noto Sans KR", sans-serif;
}
body { overflow-x: hidden; }
.raman-app-bar {
  display: flex;
  align-items: center;
  min-height: 54px;
  padding: 0 22px;
  border-bottom: 1px solid #d9e2ec;
  background: #fff;
  box-sizing: border-box;
}
.raman-brand {
  display: flex;
  align-items: baseline;
  gap: 9px;
  min-width: 0;
}
.raman-brand strong {
  color: #102a43;
  font-size: 18px;
}
.raman-brand span {
  color: #52606d;
  font-size: 12px;
}
.raman-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}
.raman-status {
  max-width: 360px;
  overflow: hidden;
  color: #52606d;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.raman-file-button,
.raman-clear-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 30px;
  border: 1px solid #9fb3c8;
  border-radius: 4px;
  background: #f5f7fa;
  color: #243b53;
  cursor: pointer;
  font-size: 11px;
  padding: 0 10px;
  box-sizing: border-box;
}
.raman-file-button:hover,
.raman-clear-button:hover {
  border-color: #486581;
  background: #e8eef5;
}
.raman-clear-button[hidden],
.raman-file-input { display: none; }
.raman-library-band {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  min-height: 68px;
  padding: 7px 22px;
  border-bottom: 1px solid #d9e2ec;
  background: #f8fafc;
  box-sizing: border-box;
}
.raman-library-title {
  flex: 0 0 auto;
  margin-top: 9px;
  color: #334e68;
  font-size: 11px;
  font-weight: 700;
}
.raman-library-filter {
  flex: 0 0 150px;
  width: 150px;
  height: 30px;
  border: 1px solid #bcccdc;
  border-radius: 4px;
  background: #fff;
  color: #243b53;
  font: 11px Arial, "Noto Sans KR", sans-serif;
  padding: 0 9px;
  box-sizing: border-box;
}
.raman-library-list {
  display: flex;
  align-content: flex-start;
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
  flex: 1 1 auto;
  max-height: 62px;
  overflow-x: hidden;
  overflow-y: auto;
}
.raman-library-item {
  display: inline-flex;
  align-items: center;
  flex: 0 0 auto;
  max-width: 300px;
  height: 28px;
  border: 1px solid #bcccdc;
  border-radius: 4px;
  background: #fff;
  color: #334e68;
  font-size: 11px;
  box-sizing: border-box;
}
.raman-library-item.is-selected {
  border-color: #3e7ca6;
  background: #edf6fb;
  color: #174b6d;
}
.raman-library-item.is-invalid {
  border-color: #f5b7b1;
  color: #9b2c2c;
}
.raman-library-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
}
.raman-library-name {
  min-width: 0;
  max-width: 190px;
  height: 100%;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  font: inherit;
  overflow: hidden;
  padding: 0 6px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.raman-library-count,
.raman-library-state,
.raman-library-empty {
  color: #7b8794;
  font-size: 10px;
  padding: 0 6px;
  white-space: nowrap;
}
.raman-library-new,
.raman-library-upload {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  height: 30px;
  border: 1px solid #9fb3c8;
  border-radius: 4px;
  background: #fff;
  color: #243b53;
  cursor: pointer;
  font-size: 11px;
  padding: 0 10px;
  box-sizing: border-box;
}
.raman-library-modal {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: none;
  align-items: center;
  justify-content: center;
  background: rgba(15,23,42,0.24);
}
.raman-library-modal.is-visible { display: flex; }
.raman-library-dialog {
  display: flex;
  flex-direction: column;
  width: min(920px, calc(100vw - 28px));
  max-height: min(720px, calc(100vh - 28px));
  border: 1px solid #bcccdc;
  border-radius: 6px;
  background: #fff;
  box-shadow: 0 16px 42px rgba(15,23,42,0.2);
}
.raman-library-dialog-header,
.raman-library-dialog-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 11px 14px;
  border-bottom: 1px solid #d9e2ec;
}
.raman-library-dialog-actions {
  justify-content: flex-end;
  border-top: 1px solid #d9e2ec;
  border-bottom: 0;
}
.raman-library-dialog-heading {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.raman-library-dialog-heading strong {
  color: #102a43;
  font-size: 14px;
}
.raman-library-dialog-heading span {
  color: #627d98;
  font-size: 11px;
}
.raman-library-dialog-close {
  margin-left: auto;
  border: 0;
  background: transparent;
  color: #52606d;
  cursor: pointer;
  font: 20px/1 Arial, sans-serif;
}
.raman-library-dialog-body {
  overflow: auto;
  padding: 12px 14px;
}
.raman-library-form-meta {
  display: grid;
  grid-template-columns: 170px 1fr;
  gap: 9px;
  margin-bottom: 12px;
}
.raman-library-suggest {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  padding: 5px 6px;
  border: 1px solid #d9e2ec;
  border-radius: 4px;
  background: #f8fafc;
  box-sizing: border-box;
}
.raman-library-suggest input {
  flex: 1 1 220px;
  min-width: 0;
  height: 28px;
  border: 1px solid #bcccdc;
  border-radius: 3px;
  background: #ffffff;
  color: #243b53;
  font: 11px Arial, "Noto Sans KR", sans-serif;
  padding: 0 7px;
  box-sizing: border-box;
}
.raman-library-suggest button {
  flex: 0 0 auto;
  height: 28px;
  border: 1px solid #3e7ca6;
  border-radius: 4px;
  background: #edf6fb;
  color: #174b6d;
  cursor: pointer;
  font-size: 11px;
  padding: 0 10px;
}
.raman-library-suggest button:disabled {
  cursor: progress;
  opacity: 0.65;
}
.raman-library-suggest span {
  flex: 0 1 auto;
  color: #7b8794;
  font-size: 10px;
  white-space: nowrap;
}
.raman-library-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  color: #52606d;
  font-size: 11px;
}
.raman-library-field.is-wide { grid-column: 1 / -1; }
.raman-library-field input,
.raman-library-field textarea,
.raman-library-table input {
  border: 1px solid #bcccdc;
  border-radius: 4px;
  color: #243b53;
  font: 12px Arial, "Noto Sans KR", sans-serif;
  padding: 6px 7px;
  box-sizing: border-box;
}
.raman-library-field textarea {
  min-height: 58px;
  resize: vertical;
}
.raman-library-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11px;
}
.raman-library-table th,
.raman-library-table td {
  border-bottom: 1px solid #eef2f7;
  padding: 5px;
  text-align: left;
}
.raman-library-table .numeric { width: 96px; }
.raman-library-table .color { width: 48px; }
.raman-library-table .remove { width: 34px; }
.raman-library-table input { width: 100%; }
.raman-library-table input[type="color"] {
  height: 30px;
  padding: 2px;
}
.raman-library-row-remove {
  width: 28px;
  height: 28px;
  border: 0;
  background: transparent;
  color: #7b8794;
  cursor: pointer;
  font: 16px/1 Arial, sans-serif;
}
.raman-library-dialog-button {
  height: 30px;
  border: 1px solid #9fb3c8;
  border-radius: 4px;
  background: #f5f7fa;
  color: #243b53;
  cursor: pointer;
  font-size: 11px;
  padding: 0 10px;
}
.raman-library-dialog-button.primary {
  border-color: #2f6f9f;
  background: #2f6f9f;
  color: #fff;
}
.raman-library-dialog-button.danger {
  border-color: #f5b7b1;
  background: #fff5f5;
  color: #9b1c1c;
}
#raman-plot .rist-raman-stack-control {
  order: 14;
  display: flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 7px;
  border: 1px solid #9fb3c8;
  border-radius: 4px;
  background: rgba(255,255,255,0.94);
  box-sizing: border-box;
}
#raman-plot .rist-raman-stack-button {
  height: 22px;
  border: 1px solid #c7d0dd;
  border-radius: 3px;
  background: #f5f7fa;
  color: #243b53;
  cursor: pointer;
  font: 11px Arial, sans-serif;
  padding: 0 7px;
}
#raman-plot .rist-raman-stack-button.is-active {
  border-color: #2563eb;
  background: #dbeafe;
  color: #1d4ed8;
}
#raman-plot .rist-raman-stack-gap {
  width: 64px;
  margin: 0;
  accent-color: #52606d;
}
#raman-plot .rist-raman-stack-value {
  min-width: 24px;
  color: #334e68;
  font: bold 10px Arial, sans-serif;
  text-align: right;
}
#raman-plot.rist-raman-y-drag-active .nsewdrag {
  cursor: ns-resize;
}
#raman-plot .rist-plot-control-row > * {
  flex: 0 0 auto;
}
#raman-plot .rist-raman-tools-toggle {
  display: none;
}
#raman-plot .rist-raman-tools-head {
  display: none;
}
#raman-plot {
  --rist-raman-tool-panel-alpha: 0.97;
}
#raman-plot .rist-raman-ratio-control {
  order: 18;
  display: flex;
  align-items: center;
  gap: 5px;
  height: 28px;
  padding: 0 7px;
  border: 1px solid #9fb3c8;
  border-radius: 4px;
  background: rgba(255,255,255,0.94);
  box-sizing: border-box;
}
#raman-plot .rist-raman-ratio-button {
  height: 22px;
  border: 1px solid #c7d0dd;
  border-radius: 3px;
  background: #f5f7fa;
  color: #243b53;
  cursor: pointer;
  font: 11px Arial, sans-serif;
  padding: 0 7px;
  white-space: nowrap;
}
#raman-plot .rist-raman-ratio-button.is-active {
  border-color: #2563eb;
  background: #dbeafe;
  color: #1d4ed8;
}
#raman-plot .rist-raman-ratio-status {
  min-width: 44px;
  max-width: 96px;
  overflow: hidden;
  color: #334e68;
  font: bold 10px Arial, sans-serif;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.raman-drop-zone {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 48px;
  padding: 7px 22px;
  border-bottom: 1px solid #d9e2ec;
  background: #fff;
  box-sizing: border-box;
  transition: background-color 120ms ease, border-color 120ms ease;
}
.raman-drop-zone.is-dragging {
  border-color: #2f855a;
  background: #f0fff4;
}
.raman-drop-prompt {
  flex: 0 0 auto;
  color: #627d98;
  font-size: 11px;
  white-space: nowrap;
}
.raman-file-list {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  overflow-x: auto;
}
.raman-file-chip {
  display: inline-flex;
  align-items: center;
  flex: 0 1 auto;
  max-width: 210px;
  height: 24px;
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  background: rgba(255,255,255,0.92);
  color: #334155;
  font-size: 11px;
  padding-left: 7px;
  box-sizing: border-box;
  overflow: hidden;
  white-space: nowrap;
}
.raman-file-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.raman-file-remove {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  width: 25px;
  height: 22px;
  border: 0;
  background: transparent;
  color: #7b8794;
  cursor: pointer;
  font: 16px/1 Arial, sans-serif;
  padding: 0;
}
.raman-file-remove:hover {
  color: #b42318;
}
.raman-report-meta-band {
  border-bottom: 1px solid #d9e2ec;
  background: #f8fafc;
}
.raman-report-meta-panel {
  padding: 0 22px;
}
.raman-report-meta-panel > summary {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 34px;
  color: #334e68;
  cursor: pointer;
  font-size: 11px;
  font-weight: 700;
  list-style: none;
}
.raman-report-meta-panel > summary::-webkit-details-marker {
  display: none;
}
.raman-report-meta-panel > summary::before {
  content: "+";
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border: 1px solid #9fb3c8;
  border-radius: 3px;
  color: #52606d;
  font-size: 12px;
  line-height: 1;
}
.raman-report-meta-panel[open] > summary::before {
  content: "-";
}
.raman-report-meta-panel > summary span {
  color: #7b8794;
  font-size: 10px;
  font-weight: 400;
}
.raman-report-meta-toolbar {
  display: flex;
  justify-content: flex-end;
  padding: 0 0 8px 24px;
}
.raman-report-option-button {
  height: 28px;
  border: 1px solid #9fb3c8;
  border-radius: 4px;
  background: #ffffff;
  color: #243b53;
  cursor: pointer;
  font-size: 11px;
  padding: 0 10px;
}
.raman-report-option-button:hover {
  border-color: #486581;
  background: #eef2f6;
}
.raman-report-meta-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(130px, 1fr));
  gap: 8px 10px;
  padding: 0 0 12px 24px;
}
.raman-report-meta-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  color: #52606d;
  font-size: 10px;
}
.raman-report-meta-field.is-wide {
  grid-column: span 2;
}
.raman-report-meta-field input,
.raman-report-meta-field select,
.raman-report-meta-field textarea {
  width: 100%;
  border: 1px solid #bcccdc;
  border-radius: 4px;
  background: #ffffff;
  color: #243b53;
  font: 11px Arial, "Noto Sans KR", sans-serif;
  padding: 6px 7px;
  box-sizing: border-box;
}
.raman-report-meta-field textarea {
  min-height: 34px;
  resize: vertical;
}
.raman-report-picker-row {
  display: flex;
  align-items: stretch;
  gap: 4px;
  width: 100%;
}
.raman-report-meta-field .raman-report-picker-row input {
  flex: 1 1 auto;
  min-width: 0;
}
.raman-report-picker-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 30px;
  width: 30px;
  border: 1px solid #9fb3c8;
  border-radius: 4px;
  background: #f5f7fa;
  color: #243b53;
  cursor: pointer;
  font: 12px/1 Arial, sans-serif;
  padding: 0;
}
.raman-report-picker-button:hover,
.raman-report-picker-button[aria-expanded="true"] {
  border-color: #486581;
  background: #e8eef5;
}
.raman-report-picker-menu {
  position: fixed;
  z-index: 120;
  display: none;
  max-height: min(260px, calc(100dvh - 24px));
  overflow: auto;
  border: 1px solid #9fb3c8;
  border-radius: 5px;
  background: #ffffff;
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.18);
  box-sizing: border-box;
  padding: 4px;
}
.raman-report-picker-menu.is-visible {
  display: block;
}
.raman-report-picker-item {
  display: block;
  width: 100%;
  min-height: 30px;
  border: 0;
  border-radius: 3px;
  background: transparent;
  color: #243b53;
  cursor: pointer;
  font: 11px Arial, "Noto Sans KR", sans-serif;
  padding: 6px 8px;
  text-align: left;
}
.raman-report-picker-item:hover,
.raman-report-picker-item:focus {
  background: #edf6fb;
  outline: none;
}
.raman-report-picker-empty {
  padding: 8px;
  color: #7b8794;
  font-size: 11px;
}
.raman-report-option-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-bottom: 12px;
}
.raman-report-option-group {
  border: 1px solid #d9e2ec;
  border-radius: 5px;
  background: #ffffff;
  overflow: hidden;
}
.raman-report-option-group-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 34px;
  padding: 0 10px;
  border-bottom: 1px solid #e4e7eb;
  background: #f8fafc;
  color: #334e68;
  font-size: 11px;
  font-weight: 700;
}
.raman-report-option-count {
  color: #7b8794;
  font-size: 10px;
  font-weight: 400;
}
.raman-report-option-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(180px, 1fr));
  gap: 7px;
  padding: 10px;
}
.raman-report-option-row {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
}
.raman-report-option-row input {
  flex: 1 1 auto;
  min-width: 0;
  height: 28px;
  border: 1px solid #bcccdc;
  border-radius: 3px;
  background: #ffffff;
  color: #243b53;
  font: 11px Arial, "Noto Sans KR", sans-serif;
  padding: 0 7px;
  box-sizing: border-box;
}
.raman-report-option-remove {
  flex: 0 0 auto;
  width: 26px;
  height: 26px;
  border: 0;
  background: transparent;
  color: #7b8794;
  cursor: pointer;
  font: 17px/1 Arial, sans-serif;
}
.raman-report-option-remove:hover {
  color: #b42318;
}
.raman-report-option-add {
  margin: 0 10px 10px;
}
#raman-report-options-modal .raman-library-dialog {
  height: min(660px, calc(100vh - 32px));
  height: min(660px, calc(100dvh - 32px));
  max-height: calc(100vh - 32px);
  max-height: calc(100dvh - 32px);
}
#raman-report-options-modal .raman-library-dialog-body {
  flex: 1 1 auto;
  min-height: 0;
  padding-bottom: 12px;
}
#raman-report-options-modal .raman-library-dialog-actions {
  position: relative;
  z-index: 2;
  flex-wrap: wrap;
}
.raman-message {
  display: none;
  position: relative;
  min-height: 32px;
  padding: 8px 54px 8px 22px;
  border-bottom: 1px solid #fecaca;
  background: #fef2f2;
  color: #b42318;
  font-size: 12px;
  box-sizing: border-box;
}
.raman-message.is-visible { display: block; }
.raman-message.is-success {
  border-bottom-color: #bfdbfe;
  background: #eff6ff;
  color: #1e3a8a;
}
.raman-message a {
  color: #1d4ed8;
  font-weight: 700;
  text-decoration: underline;
}
.raman-message-close {
  position: absolute;
  top: 4px;
  right: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: inherit;
  cursor: pointer;
  font: 18px/1 Arial, sans-serif;
}
.raman-message-close:hover {
  background: rgba(30, 58, 138, 0.1);
}
.raman-loading {
  position: fixed;
  inset: 170px 0 0;
  z-index: 40;
  display: none;
  align-items: center;
  justify-content: center;
  background: rgba(248,250,252,0.58);
  color: #334e68;
  font-weight: 700;
}
.raman-loading.is-visible { display: flex; }
.raman-report-progress {
  display: none;
  padding: 8px 22px 10px;
  border-bottom: 1px solid #d9e2ec;
  background: #f8fafc;
  color: #243b53;
  font-size: 12px;
}
.raman-report-progress.is-visible { display: block; }
.raman-report-progress-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 6px;
}
.raman-report-progress-track {
  overflow: hidden;
  height: 6px;
  border-radius: 999px;
  background: #d9e2ec;
}
.raman-report-progress-bar {
  width: 0%;
  height: 100%;
  border-radius: inherit;
  background: #2f80ed;
  transition: width 220ms ease;
}
#raman-plot {
  min-height: 540px;
  height: calc(100vh - 170px) !important;
}
@media (max-width: 760px) {
  .raman-app-bar {
    align-items: flex-start;
    flex-direction: column;
    gap: 7px;
    padding: 9px 12px;
  }
  .raman-actions {
    width: 100%;
    margin-left: 0;
    flex-wrap: wrap;
    justify-content: flex-end;
  }
  .raman-status {
    flex: 1 1 100%;
    max-width: 100%;
    text-align: right;
  }
  .raman-drop-zone {
    align-items: flex-start;
    flex-direction: column;
    gap: 6px;
    min-height: 76px;
    padding: 7px 12px;
  }
  .raman-report-meta-panel {
    padding: 0 12px;
  }
  .raman-report-meta-grid {
    grid-template-columns: 1fr;
    padding-left: 0;
  }
  .raman-report-meta-toolbar {
    padding-left: 0;
  }
  .raman-report-meta-field.is-wide {
    grid-column: auto;
  }
  .raman-report-option-list {
    grid-template-columns: 1fr;
  }
  #raman-report-options-modal .raman-library-dialog {
    height: calc(100vh - 16px);
    height: calc(100dvh - 16px);
    max-height: calc(100vh - 16px);
    max-height: calc(100dvh - 16px);
  }
  .raman-library-band {
    flex-wrap: wrap;
    gap: 7px;
    padding: 7px 12px;
  }
  .raman-library-title {
    display: none;
  }
  .raman-library-filter {
    flex: 1 1 120px;
    width: auto;
  }
  .raman-library-list {
    order: 4;
    flex-basis: 100%;
    max-height: 86px;
  }
  .raman-library-suggest {
    align-items: center;
    flex-wrap: wrap;
  }
  .raman-library-suggest span {
    white-space: normal;
  }
  .raman-file-list {
    width: 100%;
  }
  .raman-file-chip {
    max-width: 100%;
  }
  .raman-loading {
    inset: 248px 0 0;
  }
  #raman-plot {
    min-height: 900px;
    height: calc(100vh - 248px + 360px) !important;
  }
}
@media (max-width: 1440px) {
  #raman-plot .rist-raman-tools-toggle {
    position: absolute;
    top: 34px;
    right: 8px;
    z-index: 56;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    height: 30px;
    border: 1px solid #9fb3c8;
    border-radius: 4px;
    background: rgba(255,255,255,0.96);
    color: #243b53;
    cursor: pointer;
    font: bold 11px Arial, sans-serif;
    padding: 0 10px;
    box-shadow: 0 1px 5px rgba(15,23,42,0.12);
  }
  #raman-plot.rist-raman-tools-open .rist-raman-tools-toggle {
    border-color: #2563eb;
    background: #dbeafe;
    color: #1d4ed8;
  }
  #raman-plot .rist-plot-control-row {
    left: auto !important;
    right: 8px !important;
    top: 70px !important;
    z-index: 55;
    width: min(860px, calc(100% - 24px)) !important;
    max-width: calc(100% - 24px);
    max-height: min(360px, calc(100% - 86px));
    display: none !important;
    flex-wrap: wrap;
    align-items: flex-start;
    justify-content: flex-start;
    gap: 6px;
    overflow: auto;
    padding: 8px;
    border: 1px solid #c7d0dd;
    border-radius: 6px;
    background: rgba(255,255,255,0.98);
    opacity: var(--rist-raman-tool-panel-alpha);
    box-shadow: 0 4px 18px rgba(15,23,42,0.16);
    box-sizing: border-box;
    scrollbar-width: thin;
  }
  #raman-plot .rist-raman-tools-head {
    order: -100;
    display: flex;
    align-items: center;
    gap: 8px;
    flex: 1 0 100%;
    min-width: 0;
    height: 28px;
    margin: -2px 0 2px;
    padding: 0 2px 6px;
    border-bottom: 1px solid #d7dee8;
    color: #243b53;
    cursor: move;
    font: bold 12px Arial, sans-serif;
    touch-action: none;
    user-select: none;
  }
  #raman-plot .rist-raman-tools-head span:first-child {
    flex: 1 1 auto;
    min-width: 0;
  }
  #raman-plot .rist-raman-tools-opacity {
    flex: 0 0 76px;
    width: 76px;
    accent-color: #52606d;
    cursor: pointer;
  }
  #raman-plot .rist-raman-tools-close {
    flex: 0 0 auto;
    width: 24px;
    height: 24px;
    border: 0;
    background: transparent;
    color: #52606d;
    cursor: pointer;
    font: 18px/1 Arial, sans-serif;
    padding: 0;
  }
  #raman-plot.rist-raman-tools-open .rist-plot-control-row {
    display: flex !important;
  }
  #raman-plot .rist-plot-control-row > * {
    flex: 0 0 auto;
  }
  #raman-plot .rist-legend-edit-button,
  #raman-plot .rist-peak-edit-button {
    min-width: 0;
    height: 28px;
    white-space: nowrap;
    font-size: 11px;
    padding: 0 8px;
  }
  #raman-plot .rist-raman-stack-control,
  #raman-plot .rist-raman-ratio-control,
  #raman-plot .rist-peak-sensitivity-control {
    height: 28px;
    gap: 5px;
    padding: 0 6px;
  }
  #raman-plot .rist-raman-ratio-button {
    min-width: 34px;
    height: 22px;
    padding: 0 6px;
  }
  #raman-plot .rist-raman-ratio-status {
    min-width: 36px;
    max-width: 76px;
  }
  #raman-plot .rist-raman-stack-button {
    min-width: 42px;
    height: 22px;
    white-space: nowrap;
    padding: 0 6px;
  }
  #raman-plot .rist-raman-stack-gap {
    width: 54px;
  }
  #raman-plot .rist-raman-stack-value {
    min-width: 20px;
  }
  #raman-plot .rist-peak-sensitivity-slider {
    width: 54px;
  }
  #raman-plot .rist-peak-sensitivity-number {
    width: 38px;
  }
  #raman-plot .rist-peak-sensitivity-value {
    min-width: 24px;
  }
  #raman-plot .rist-peak-group-name {
    width: 96px;
    flex: 0 0 96px;
  }
  #raman-plot .rist-peak-group-color,
  #raman-plot .rist-shape-tool-button {
    flex: 0 0 auto;
    width: 28px;
    height: 28px;
  }
}
@media (max-width: 420px) {
  #raman-plot .rist-raman-tools-toggle {
    top: 42px;
    right: 8px;
    height: 28px;
    padding: 0 8px;
  }
  #raman-plot .rist-plot-control-row {
    right: 8px !important;
    top: 76px !important;
    width: calc(100% - 16px) !important;
    max-width: calc(100% - 16px);
    gap: 5px;
  }
  #raman-plot .rist-legend-edit-button,
  #raman-plot .rist-peak-edit-button,
  #raman-plot .rist-raman-ratio-button {
    font-size: 10px;
    padding: 0 6px;
  }
  #raman-plot .rist-raman-stack-gap,
  #raman-plot .rist-peak-sensitivity-slider {
    width: 46px;
  }
}
</style>
"""


_PAGE_SHELL = """
<header class="raman-app-bar">
  <div class="raman-brand">
    <strong>RIN Raman</strong>
    <span>Raw upload · preprocessing · peak preview</span>
  </div>
  <div class="raman-actions">
    <span class="raman-status" id="raman-status">Raman raw 파일을 업로드하세요</span>
    <label class="raman-file-button" for="raman-file-input">파일 선택</label>
    <button class="raman-clear-button" id="raman-report" type="button">보고서 생성</button>
    <button class="raman-clear-button" id="raman-clear" type="button">초기화</button>
    <input class="raman-file-input" id="raman-file-input" type="file"
           accept=".txt,.csv,.tsv,.xlsx,.xlsm" multiple>
  </div>
</header>
<section class="raman-library-band" aria-label="Raman 피크 assignment 라이브러리">
  <span class="raman-library-title">피크 라이브러리</span>
  <input type="search" class="raman-library-filter" id="raman-library-filter"
         placeholder="라이브러리 검색" autocomplete="off">
  <div class="raman-library-list" id="raman-library-list">
    <span class="raman-library-empty">라이브러리 불러오는 중...</span>
  </div>
  <button type="button" class="raman-library-new"
          id="raman-library-new">새 라이브러리</button>
  <label class="raman-library-upload">
    파일 가져오기
    <input id="raman-library-input" class="raman-file-input" type="file"
           accept=".json,.csv">
  </label>
</section>
<section class="raman-drop-zone" id="raman-drop-zone">
  <span class="raman-drop-prompt" id="raman-drop-prompt">
    Raman raw 파일을 선택하거나 여기에 놓으세요
  </span>
  <div class="raman-file-list" id="raman-file-list"></div>
</section>
<section class="raman-report-meta-band" id="raman-report-meta">
  <details class="raman-report-meta-panel">
    <summary>보고서 정보 <span>raw 헤더 자동 추출 + 직접 입력</span></summary>
    <div class="raman-report-meta-toolbar">
      <button type="button" class="raman-report-option-button"
              id="raman-report-options-open">선택지 관리</button>
    </div>
    <div class="raman-report-meta-grid">
      <label class="raman-report-meta-field">
        <span>측정일</span>
        <input type="date" data-report-field="measurementDate"
               data-report-label="측정일">
      </label>
      <label class="raman-report-meta-field">
        <span>의뢰자</span>
        <input type="text" placeholder="예: 홍길동"
               data-report-field="requester"
               data-report-label="의뢰자">
      </label>
      <label class="raman-report-meta-field">
        <span>Laser</span>
        <input type="text" list="raman-report-laser-options"
               placeholder="선택 또는 입력"
               data-report-field="laserPreset"
               data-report-label="Laser">
      </label>
      <label class="raman-report-meta-field">
        <span>Exposure / Accumulation</span>
        <input type="text" list="raman-report-exposure-options"
               placeholder="예: 10 s x 3"
               data-report-field="exposure"
               data-report-label="Exposure / Accumulation">
      </label>
      <label class="raman-report-meta-field is-wide">
        <span>시료 정보</span>
        <input type="text" list="raman-report-sample-options"
               placeholder="예: air-sensitive LiOH sample"
               data-report-field="sampleDescription"
               data-report-label="시료 정보">
      </label>
      <label class="raman-report-meta-field is-wide">
        <span>분석 목적</span>
        <input type="text" list="raman-report-purpose-options"
               placeholder="예: LiOH/탄산염 피크 확인, D/G ratio 비교"
               data-report-field="requestPurpose"
               data-report-label="분석 목적">
      </label>
      <label class="raman-report-meta-field is-wide">
        <span>실험환경 직접 입력</span>
        <textarea placeholder="예: air exposure minimized, room temperature"
                  data-report-field="conditionDetail"
                  data-report-label="실험환경 상세"></textarea>
      </label>
    </div>
    <datalist id="raman-report-laser-options">
      <option value="532 nm">
      <option value="633 nm">
      <option value="785 nm">
      <option value="1064 nm">
      <option value="514 nm">
      <option value="488 nm">
      <option value="780 nm">
    </datalist>
    <datalist id="raman-report-exposure-options">
      <option value="1 s x 10">
      <option value="5 s x 3">
      <option value="10 s x 3">
      <option value="30 s x 1">
      <option value="60 s x 1">
    </datalist>
    <datalist id="raman-report-sample-options">
      <option value="air-sensitive sample">
      <option value="powder sample">
      <option value="carbon material">
      <option value="lithium compound">
      <option value="layered oxide cathode">
    </datalist>
    <datalist id="raman-report-purpose-options">
      <option value="LiOH/탄산염 피크 확인">
      <option value="D/G ratio 비교">
      <option value="탄소 D/G/2D band 확인">
      <option value="LMR layered oxide mode 확인">
      <option value="시료 간 Raman 피크 비교">
    </datalist>
  </details>
</section>
<div class="raman-message" id="raman-message"></div>
<div class="raman-report-progress" id="raman-report-progress" aria-live="polite">
  <div class="raman-report-progress-row">
    <span id="raman-report-progress-label">보고서 생성 대기</span>
    <span id="raman-report-progress-value">0%</span>
  </div>
  <div class="raman-report-progress-track">
    <div class="raman-report-progress-bar" id="raman-report-progress-bar"></div>
  </div>
</div>
<div class="raman-loading" id="raman-loading">Raman 전처리 중...</div>
<div class="raman-library-modal" id="raman-library-modal" role="dialog"
     aria-modal="true" aria-labelledby="raman-library-dialog-title">
  <section class="raman-library-dialog">
    <header class="raman-library-dialog-header">
      <div class="raman-library-dialog-heading">
        <strong id="raman-library-dialog-title">피크 라이브러리</strong>
        <span id="raman-library-dialog-meta"></span>
      </div>
      <button type="button" class="raman-library-dialog-close"
              id="raman-library-dialog-close" aria-label="닫기">×</button>
    </header>
    <div class="raman-library-dialog-body" id="raman-library-dialog-body"></div>
    <footer class="raman-library-dialog-actions">
      <button type="button" class="raman-library-dialog-button"
              id="raman-library-row-add">피크 행 추가</button>
      <button type="button" class="raman-library-dialog-button"
              id="raman-library-dialog-cancel">취소</button>
      <button type="button" class="raman-library-dialog-button primary"
              id="raman-library-dialog-save">저장</button>
    </footer>
  </section>
</div>
<div class="raman-library-modal" id="raman-report-options-modal" role="dialog"
     aria-modal="true" aria-labelledby="raman-report-options-title">
  <section class="raman-library-dialog">
    <header class="raman-library-dialog-header">
      <div class="raman-library-dialog-heading">
        <strong id="raman-report-options-title">보고서 선택지 관리</strong>
        <span>Laser, Exposure 등 입력 후보를 추가하거나 삭제합니다.</span>
      </div>
      <button type="button" class="raman-library-dialog-close"
              id="raman-report-options-close" aria-label="닫기">×</button>
    </header>
    <div class="raman-library-dialog-body">
      <div class="raman-report-option-body" id="raman-report-options-body"></div>
    </div>
    <footer class="raman-library-dialog-actions">
      <button type="button" class="raman-library-dialog-button"
              id="raman-report-options-reset">기본 선택지 복원</button>
      <button type="button" class="raman-library-dialog-button"
              id="raman-report-options-cancel">취소</button>
      <button type="button" class="raman-library-dialog-button primary"
              id="raman-report-options-save">저장</button>
    </footer>
  </section>
</div>
"""


_RAMAN_TOOL_PANEL_SCRIPT = """
<script>
(function() {
  var gd = document.getElementById("raman-plot");
  if (!gd || gd._ristRamanToolPanelInstalled) return;
  gd._ristRamanToolPanelInstalled = true;
  if (getComputedStyle(gd).position === "static") gd.style.position = "relative";
  var button = document.createElement("button");
  button.type = "button";
  button.className = "rist-raman-tools-toggle";
  button.textContent = "도구";
  button.title = "그래프 도구 열기";
  button.setAttribute("aria-expanded", "false");
  gd.appendChild(button);
  var toolbar = gd.querySelector(".rist-plot-control-row");
  if (!toolbar) {
    toolbar = document.createElement("div");
    toolbar.className = "rist-plot-control-row";
    gd.appendChild(toolbar);
  }
  if (!toolbar.querySelector(".rist-raman-tools-head")) {
    var head = document.createElement("div");
    head.className = "rist-raman-tools-head";
    head.innerHTML =
      "<span>그래프 도구</span>"
      + "<input class='rist-raman-tools-opacity' type='range' min='55' max='100' value='97' title='도구창 투명도' aria-label='도구창 투명도'>"
      + "<button type='button' class='rist-raman-tools-close' aria-label='도구창 닫기'>×</button>";
    toolbar.insertBefore(head, toolbar.firstChild);
  }
  var head = toolbar.querySelector(".rist-raman-tools-head");
  var opacity = toolbar.querySelector(".rist-raman-tools-opacity");
  var closeButton = toolbar.querySelector(".rist-raman-tools-close");
  var dragState = null;

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function keepPanelInBounds(left, top) {
    var plotRect = gd.getBoundingClientRect();
    var width = toolbar.offsetWidth || 320;
    var height = toolbar.offsetHeight || 180;
    var title = gd.querySelector(".gtitle");
    var titleBottom = title ? title.getBoundingClientRect().bottom - plotRect.top + 8 : 0;
    var minTop = Math.max(window.innerWidth <= 420 ? 76 : 70, titleBottom);
    return {
      left: clamp(left, 8, Math.max(8, plotRect.width - width - 8)),
      top: clamp(top, minTop, Math.max(minTop, plotRect.height - height - 8))
    };
  }

  function setPanelPosition(left, top) {
    var next = keepPanelInBounds(left, top);
    toolbar.style.setProperty("left", next.left + "px", "important");
    toolbar.style.setProperty("right", "auto", "important");
    toolbar.style.setProperty("top", next.top + "px", "important");
  }

  function setOpen(open) {
    gd.classList.toggle("rist-raman-tools-open", open);
    button.setAttribute("aria-expanded", open ? "true" : "false");
    button.textContent = open ? "닫기" : "도구";
    if (open) gd.dispatchEvent(new CustomEvent("rist-open-edit-tool"));
    gd.dispatchEvent(new CustomEvent("rist-raman-tools-toggle", {
      detail: {open: open}
    }));
  }

  button.addEventListener("click", function(ev) {
    ev.preventDefault();
    ev.stopPropagation();
    setOpen(!gd.classList.contains("rist-raman-tools-open"));
  });
  if (closeButton) {
    closeButton.addEventListener("click", function(ev) {
      ev.preventDefault();
      ev.stopPropagation();
      setOpen(false);
    });
  }
  if (opacity) {
    function setToolPanelAlpha(value) {
      opacity.value = String(clamp(Math.round(value), 55, 100));
      gd.style.setProperty(
        "--rist-raman-tool-panel-alpha",
        String(clamp(Number(opacity.value) || 97, 55, 100) / 100)
      );
    }

    function setToolPanelAlphaFromPointer(ev) {
      var rect = opacity.getBoundingClientRect();
      var ratio = rect.width > 0 ? (ev.clientX - rect.left) / rect.width : 1;
      setToolPanelAlpha(55 + clamp(ratio, 0, 1) * 45);
    }

    opacity.addEventListener("input", function() {
      setToolPanelAlpha(Number(opacity.value) || 97);
    });
    opacity.addEventListener("pointerdown", function(ev) {
      ev.stopPropagation();
      opacity.setPointerCapture(ev.pointerId);
      setToolPanelAlphaFromPointer(ev);
      ev.preventDefault();
    });
    opacity.addEventListener("pointermove", function(ev) {
      if (!opacity.hasPointerCapture(ev.pointerId)) return;
      setToolPanelAlphaFromPointer(ev);
      ev.preventDefault();
    });
    opacity.addEventListener("pointerup", function(ev) {
      if (opacity.hasPointerCapture(ev.pointerId)) {
        opacity.releasePointerCapture(ev.pointerId);
      }
      ev.preventDefault();
    });
    opacity.addEventListener("pointercancel", function(ev) {
      if (opacity.hasPointerCapture(ev.pointerId)) {
        opacity.releasePointerCapture(ev.pointerId);
      }
    });
  }
  if (head) {
    head.addEventListener("pointerdown", function(ev) {
      if (ev.target.closest(".rist-raman-tools-opacity,.rist-raman-tools-close")) return;
      var rect = toolbar.getBoundingClientRect();
      var plotRect = gd.getBoundingClientRect();
      dragState = {
        pointerId: ev.pointerId,
        dx: ev.clientX - rect.left,
        dy: ev.clientY - rect.top,
        plotLeft: plotRect.left,
        plotTop: plotRect.top
      };
      head.setPointerCapture(ev.pointerId);
      ev.preventDefault();
    });
    head.addEventListener("pointermove", function(ev) {
      if (!dragState) return;
      setPanelPosition(
        ev.clientX - dragState.plotLeft - dragState.dx,
        ev.clientY - dragState.plotTop - dragState.dy
      );
      ev.preventDefault();
    });
    head.addEventListener("pointerup", function(ev) {
      if (dragState && head.hasPointerCapture(dragState.pointerId)) {
        head.releasePointerCapture(dragState.pointerId);
      }
      dragState = null;
      ev.preventDefault();
    });
    head.addEventListener("pointercancel", function() {
      dragState = null;
    });
  }
  document.addEventListener("pointerdown", function(ev) {
    if (!gd.classList.contains("rist-raman-tools-open")) return;
    if (ev.target.closest("#raman-plot .rist-plot-control-row")) return;
    if (ev.target.closest("#raman-plot .rist-raman-tools-toggle")) return;
    if (gd._ristRamanRatioMode && gd.contains(ev.target)) return;
    setOpen(false);
  });
  gd.addEventListener("rist-plot-data-replaced", function() {
    setOpen(false);
  });
})();
</script>
"""


_RAMAN_STACK_SCRIPT = """
<script>
(function() {
  var gd = document.getElementById("raman-plot");
  if (!gd || !window.Plotly || gd._ristRamanStackInstalled) return;
  gd._ristRamanStackInstalled = true;
  var state = {
    initialized: false,
    enabled: false,
    gap: 1.2,
    offsets: {},
    order: [],
    baseTraces: {},
    baseAnnotations: {},
    baseShapes: {},
    dragMode: false,
    dragging: null,
    raf: null
  };

  function traceMeta(index) {
    var trace = (gd.data || [])[index] || {};
    return trace.meta && typeof trace.meta === "object" ? trace.meta : {};
  }

  function groupOfTrace(index) {
    var meta = traceMeta(index);
    return String(
      meta.rist_sample_group
      || (meta.rist_peak && meta.rist_peak.sample_group)
      || ""
    );
  }

  function stackMeta() {
    if (!gd.layout.meta || typeof gd.layout.meta !== "object") gd.layout.meta = {};
    if (!gd.layout.meta.ristRamanStack) gd.layout.meta.ristRamanStack = {};
    return gd.layout.meta.ristRamanStack;
  }

  function numberArray(values) {
    return Array.prototype.slice.call(values || []).map(function(value) {
      var next = Number(value);
      return Number.isFinite(next) ? next : 0;
    });
  }

  function discoverOrder() {
    var seen = {};
    var order = [];
    (gd.data || []).forEach(function(trace, index) {
      var group = groupOfTrace(index);
      var meta = traceMeta(index);
      if (!group || !meta.rist_sample_parent || seen[group]) return;
      seen[group] = true;
      order.push(group);
    });
    return order;
  }

  function labelItems() {
    return (
      gd.layout.meta && Array.isArray(gd.layout.meta.ristPeakLabels)
    ) ? gd.layout.meta.ristPeakLabels : [];
  }

  function annotationGroup(index) {
    var match = labelItems().find(function(item) {
      return item.annotationIndex === index;
    });
    return match ? String(match.legendgroup || "") : "";
  }

  function shapeGroup(index) {
    var match = labelItems().find(function(item) {
      return item.shapeIndex === index;
    });
    return match ? String(match.legendgroup || "") : "";
  }

  function labelForAnnotation(index) {
    return labelItems().find(function(item) {
      return item.annotationIndex === index;
    }) || null;
  }

  function labelForShape(index) {
    return labelItems().find(function(item) {
      return item.shapeIndex === index;
    }) || null;
  }

  function initState() {
    var meta = stackMeta();
    state.enabled = !!meta.enabled;
    state.gap = Number.isFinite(Number(meta.gap)) ? Number(meta.gap) : 1.2;
    state.order = Array.isArray(meta.sampleOrder) && meta.sampleOrder.length
      ? meta.sampleOrder.map(String)
      : discoverOrder();
    state.offsets = {};
    state.order.forEach(function(group, index) {
      var configured = meta.sampleOffsets && meta.sampleOffsets[group];
      state.offsets[group] = Number.isFinite(Number(configured))
        ? Number(configured)
        : (state.enabled ? index * state.gap : 0);
    });

    state.baseTraces = {};
    (gd.data || []).forEach(function(trace, index) {
      var group = groupOfTrace(index);
      if (!group || !Object.prototype.hasOwnProperty.call(state.offsets, group)) return;
      var offset = Number(traceMeta(index).rist_raman_stack_offset);
      if (!Number.isFinite(offset)) offset = state.offsets[group] || 0;
      state.baseTraces[index] = {
        group: group,
        y: numberArray(trace.y).map(function(value) { return value - offset; }),
        parent: !!traceMeta(index).rist_sample_parent
      };
    });

    state.baseAnnotations = {};
    (gd.layout.annotations || []).forEach(function(annotation, index) {
      var group = annotationGroup(index);
      if (!group || !Object.prototype.hasOwnProperty.call(state.offsets, group)) return;
      var label = labelForAnnotation(index);
      var baseY = Number(label && label.annotationBaseY);
      if (!Number.isFinite(baseY)) {
        baseY = Number(annotation.y) - (state.offsets[group] || 0);
      }
      state.baseAnnotations[index] = {group: group, y: baseY};
    });

    state.baseShapes = {};
    (gd.layout.shapes || []).forEach(function(shape, index) {
      var group = shapeGroup(index);
      if (!group || !Object.prototype.hasOwnProperty.call(state.offsets, group)) return;
      var label = labelForShape(index);
      var baseY0 = Number(label && label.shapeBaseY0);
      var baseY1 = Number(label && label.shapeBaseY1);
      if (!Number.isFinite(baseY0)) {
        baseY0 = Number(shape.y0) - (state.offsets[group] || 0);
      }
      if (!Number.isFinite(baseY1)) {
        baseY1 = Number(shape.y1) - (state.offsets[group] || 0);
      }
      state.baseShapes[index] = {group: group, y0: baseY0, y1: baseY1};
    });

    state.initialized = true;
    syncControls();
  }

  function fitRange() {
    var groups = state.order.length ? state.order : Object.keys(state.offsets);
    if (!groups.length) return [-0.05, 1.65];
    var low = Infinity;
    var high = -Infinity;
    groups.forEach(function(group) {
      var offset = Number(state.offsets[group] || 0);
      low = Math.min(low, offset - 0.08);
      high = Math.max(high, offset + 1.65);
    });
    return [low, high];
  }

  function updateMeta() {
    var meta = stackMeta();
    meta.enabled = state.enabled;
    meta.gap = state.gap;
    meta.sampleOffsets = Object.assign({}, state.offsets);
    meta.sampleOrder = state.order.slice();
  }

  function applyOffsets() {
    if (!state.initialized) initState();
    var data = gd.data || [];
    Object.keys(state.baseTraces).forEach(function(indexText) {
      var index = Number(indexText);
      var base = state.baseTraces[index];
      var offset = Number(state.offsets[base.group] || 0);
      data[index].y = base.y.map(function(value) { return value + offset; });
      if (data[index].meta && typeof data[index].meta === "object") {
        data[index].meta.rist_raman_stack_offset = offset;
      }
    });

    var layout = gd.layout || {};
    layout.annotations = (layout.annotations || []).map(function(annotation, index) {
      var base = state.baseAnnotations[index];
      if (!base) return annotation;
      var next = Object.assign({}, annotation);
      next.y = base.y + Number(state.offsets[base.group] || 0);
      return next;
    });
    layout.shapes = (layout.shapes || []).map(function(shape, index) {
      var base = state.baseShapes[index];
      if (!base) return shape;
      var offset = Number(state.offsets[base.group] || 0);
      var next = Object.assign({}, shape);
      next.y0 = base.y0 + offset;
      next.y1 = base.y1 + offset;
      return next;
    });
    if (!layout.yaxis) layout.yaxis = {};
    layout.yaxis.range = fitRange();
    updateMeta();
    syncControls();
    return window.Plotly.react(gd, data, layout, gd._context).then(function() {
      gd.dispatchEvent(new CustomEvent("rist-raman-stack-change"));
    });
  }

  function requestApply() {
    if (state.raf) return;
    state.raf = window.requestAnimationFrame(function() {
      state.raf = null;
      applyOffsets();
    });
  }

  function resetStackOffsets() {
    state.order.forEach(function(group, index) {
      state.offsets[group] = state.enabled ? index * state.gap : 0;
    });
  }

  function plotPoint(ev) {
    var drag = gd.querySelector(".nsewdrag");
    var layout = gd._fullLayout || {};
    if (!drag || !layout.xaxis || !layout.yaxis) return null;
    var rect = drag.getBoundingClientRect();
    if (
      ev.clientX < rect.left || ev.clientX > rect.right
      || ev.clientY < rect.top || ev.clientY > rect.bottom
    ) return null;
    return {
      x: layout.xaxis.p2d(ev.clientX - rect.left),
      y: layout.yaxis.p2d(ev.clientY - rect.top),
      rect: rect,
      xaxis: layout.xaxis,
      yaxis: layout.yaxis
    };
  }

  function nearestIndex(xs, target) {
    var best = -1;
    var bestDelta = Infinity;
    xs.forEach(function(value, index) {
      var delta = Math.abs(Number(value) - target);
      if (delta < bestDelta) {
        bestDelta = delta;
        best = index;
      }
    });
    return best;
  }

  function nearestSample(ev) {
    var point = plotPoint(ev);
    if (!point) return null;
    var best = null;
    (gd.data || []).forEach(function(trace, index) {
      var base = state.baseTraces[index];
      if (!base || !base.parent) return;
      if (trace.visible === false || trace.visible === "legendonly") return;
      var xs = numberArray(trace.x);
      var nearest = nearestIndex(xs, point.x);
      if (nearest < 0) return;
      var y = base.y[nearest] + Number(state.offsets[base.group] || 0);
      var px = point.xaxis.d2p(xs[nearest]);
      var py = point.yaxis.d2p(y);
      var dx = Math.abs(px - (ev.clientX - point.rect.left));
      var dy = Math.abs(py - (ev.clientY - point.rect.top));
      var distance = Math.sqrt(dx * dx + dy * dy);
      if (distance > 36) return;
      if (!best || distance < best.distance) {
        best = {group: base.group, distance: distance, point: point};
      }
    });
    return best;
  }

  function syncControls() {
    if (!stackButton || !dragButton || !gapSlider || !gapValue) return;
    var hasSamples = state.order.length > 0;
    stackButton.classList.toggle("is-active", !!state.enabled);
    dragButton.classList.toggle("is-active", !!state.dragMode);
    stackButton.disabled = !hasSamples;
    dragButton.disabled = !hasSamples;
    gapSlider.disabled = !hasSamples;
    gapSlider.value = String(Math.round(state.gap * 100));
    gapValue.textContent = state.gap.toFixed(1);
    gd.classList.toggle("rist-raman-y-drag-active", !!state.dragMode);
  }

  if (getComputedStyle(gd).position === "static") gd.style.position = "relative";
  var toolbar = gd.querySelector(".rist-plot-control-row");
  if (!toolbar) {
    toolbar = document.createElement("div");
    toolbar.className = "rist-plot-control-row";
    gd.appendChild(toolbar);
  }
  var control = document.createElement("div");
  control.className = "rist-raman-stack-control";
  control.innerHTML =
    "<button type='button' class='rist-raman-stack-button' title='샘플을 위아래로 벌려서 보기'>스택</button>"
    + "<input class='rist-raman-stack-gap' type='range' min='60' max='220' step='5' value='120' title='스택 간격' aria-label='스택 간격'>"
    + "<span class='rist-raman-stack-value'>1.2</span>"
    + "<button type='button' class='rist-raman-stack-button' title='샘플 곡선을 위아래로 드래그'>Y 이동</button>";
  toolbar.appendChild(control);
  var stackButton = control.querySelectorAll(".rist-raman-stack-button")[0];
  var gapSlider = control.querySelector(".rist-raman-stack-gap");
  var gapValue = control.querySelector(".rist-raman-stack-value");
  var dragButton = control.querySelectorAll(".rist-raman-stack-button")[1];

  stackButton.addEventListener("click", function() {
    state.enabled = !state.enabled;
    resetStackOffsets();
    applyOffsets();
  });
  dragButton.addEventListener("click", function() {
    state.dragMode = !state.dragMode;
    syncControls();
  });
  gapSlider.addEventListener("input", function() {
    state.gap = Math.max(0.6, Math.min(2.2, Number(gapSlider.value) / 100));
    if (state.enabled) resetStackOffsets();
    applyOffsets();
  });

  gd.addEventListener("pointerdown", function(ev) {
    if (!state.dragMode) return;
    if (!state.initialized) initState();
    var nearest = nearestSample(ev);
    if (!nearest) return;
    ev.preventDefault();
    ev.stopPropagation();
    if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
    state.dragging = {
      group: nearest.group,
      startY: nearest.point.y,
      startOffset: Number(state.offsets[nearest.group] || 0)
    };
  }, true);

  document.addEventListener("pointermove", function(ev) {
    if (!state.dragging) return;
    var point = plotPoint(ev);
    if (!point) return;
    state.offsets[state.dragging.group] =
      state.dragging.startOffset + (point.y - state.dragging.startY);
    state.enabled = state.order.some(function(group) {
      return Math.abs(Number(state.offsets[group] || 0)) > 0.001;
    });
    requestApply();
  });
  document.addEventListener("pointerup", function() {
    state.dragging = null;
  });
  document.addEventListener("pointercancel", function() {
    state.dragging = null;
  });

  gd.addEventListener("rist-plot-data-replaced", function() {
    state.initialized = false;
    setTimeout(initState, 0);
  });
  initState();
})();
</script>
"""


_RAMAN_RATIO_SCRIPT = """
<script>
(function() {
  var gd = document.getElementById("raman-plot");
  if (!gd || !window.Plotly || gd._ristRamanRatioInstalled) return;
  gd._ristRamanRatioInstalled = true;
  var ratioMode = false;
  var pendingNumerator = null;
  var ratios = [];
  var nextRatioId = 1;

  function traceMeta(curve) {
    var trace = (gd.data || [])[curve] || {};
    return trace.meta && typeof trace.meta === "object" ? trace.meta : {};
  }

  function peakMeta(curve) {
    var meta = traceMeta(curve);
    return meta.rist_peak && typeof meta.rist_peak === "object"
      ? meta.rist_peak
      : null;
  }

  function peakValue(curve) {
    var peak = peakMeta(curve);
    if (peak && Number.isFinite(Number(peak.base_y))) return Number(peak.base_y);
    var trace = (gd.data || [])[curve] || {};
    var offset = Number(traceMeta(curve).rist_raman_stack_offset || 0);
    var y = Array.isArray(trace.y) ? Number(trace.y[0]) : Number(trace.y);
    return Number.isFinite(y) ? y - offset : NaN;
  }

  function plottedPeakY(curve) {
    var trace = (gd.data || [])[curve] || {};
    var y = Array.isArray(trace.y) ? Number(trace.y[0]) : Number(trace.y);
    return Number.isFinite(y) ? y : peakValue(curve);
  }

  function peakX(curve) {
    var peak = peakMeta(curve);
    if (peak && Number.isFinite(Number(peak.x))) return Number(peak.x);
    var trace = (gd.data || [])[curve] || {};
    var x = Array.isArray(trace.x) ? Number(trace.x[0]) : Number(trace.x);
    return Number.isFinite(x) ? x : NaN;
  }

  function sampleGroup(curve) {
    var peak = peakMeta(curve);
    return String(
      (peak && peak.sample_group)
      || traceMeta(curve).rist_sample_group
      || ""
    );
  }

  function labelFor(curve) {
    var x = peakX(curve);
    return Number.isFinite(x) ? x.toFixed(0) : "peak";
  }

  function ratioPrefix() {
    return "rist_raman_ratio:";
  }

  function withoutRatioItems(items) {
    return (items || []).filter(function(item) {
      return String(item && item.name || "").indexOf(ratioPrefix()) !== 0;
    });
  }

  function ratioText(item) {
    var numerator = peakValue(item.numeratorCurve);
    var denominator = peakValue(item.denominatorCurve);
    if (!Number.isFinite(numerator) || !Number.isFinite(denominator)
        || Math.abs(denominator) < 1e-12) {
      return "I(num)/I(den) = n/a";
    }
    return "I(" + labelFor(item.numeratorCurve) + ")/I("
      + labelFor(item.denominatorCurve) + ") = "
      + (numerator / denominator).toFixed(3);
  }

  function renderRatios() {
    var annotations = withoutRatioItems(gd.layout.annotations);
    var shapes = withoutRatioItems(gd.layout.shapes);
    ratios = ratios.filter(function(item) {
      return peakMeta(item.numeratorCurve) && peakMeta(item.denominatorCurve);
    });
    ratios.forEach(function(item) {
      var x0 = peakX(item.numeratorCurve);
      var x1 = peakX(item.denominatorCurve);
      var y0 = plottedPeakY(item.numeratorCurve);
      var y1 = plottedPeakY(item.denominatorCurve);
      if (![x0, x1, y0, y1].every(Number.isFinite)) return;
      var y = Math.max(y0, y1) + 0.14;
      var name = ratioPrefix() + item.id;
      shapes.push({
        type: "line",
        x0: x0,
        x1: x1,
        y0: y,
        y1: y,
        line: {color: "#111827", width: 1.3, dash: "dash"},
        name: name
      });
      annotations.push({
        x: (x0 + x1) / 2,
        y: y + 0.045,
        text: ratioText(item),
        showarrow: false,
        font: {size: 11, color: "#111827"},
        bgcolor: "rgba(255,255,255,0.92)",
        bordercolor: "#111827",
        borderwidth: 1,
        borderpad: 3,
        name: name
      });
    });
    return window.Plotly.relayout(gd, {annotations: annotations, shapes: shapes});
  }

  function setStatus(text) {
    if (status) status.textContent = text || "";
  }

  function syncControls() {
    gd._ristRamanRatioMode = ratioMode;
    ratioButton.classList.toggle("is-active", ratioMode);
    ratioButton.textContent = ratioMode ? "분자" : "비율";
    if (!ratioMode) {
      setStatus(ratios.length ? ratios.length + "개" : "");
    } else if (pendingNumerator) {
      setStatus("분모 선택");
    } else {
      setStatus("분자 선택");
    }
    gd.dispatchEvent(new CustomEvent("rist-peak-actions-disabled", {
      detail: {disabled: ratioMode}
    }));
  }

  function pickPeak(curve) {
    var peak = peakMeta(curve);
    if (!peak) return false;
    var value = peakValue(curve);
    if (!Number.isFinite(value)) {
      setStatus("값 없음");
      return true;
    }
    if (!pendingNumerator) {
      pendingNumerator = {curve: curve, group: sampleGroup(curve)};
      syncControls();
      return true;
    }
    if (sampleGroup(curve) !== pendingNumerator.group) {
      setStatus("같은 샘플");
      return true;
    }
    var denominator = peakValue(curve);
    if (Math.abs(denominator) < 1e-12) {
      setStatus("분모 0");
      return true;
    }
    ratios.push({
      id: nextRatioId++,
      numeratorCurve: pendingNumerator.curve,
      denominatorCurve: curve
    });
    pendingNumerator = null;
    renderRatios().then(syncControls);
    return true;
  }

  function peakCurveFromEvent(ev) {
    if (!ev || !gd._ristNearestPeakCurveFromEvent) return null;
    return gd._ristNearestPeakCurveFromEvent(ev);
  }

  function handleRatioPeakPointer(ev) {
    if (!ratioMode) return;
    if (ev.target.closest(".legend,.modebar,.rist-plot-control-row,.rist-legend-edit-panel")) return;
    if (ev.type === "click" && gd._ristHandledRamanRatioClick) {
      ev.preventDefault();
      ev.stopPropagation();
      return;
    }
    var curve = peakCurveFromEvent(ev);
    if (curve == null) return;
    ev.preventDefault();
    ev.stopPropagation();
    gd._ristHandledRamanRatioClick = true;
    gd._ristHandledRamanRatioAt = Date.now();
    pickPeak(curve);
    setTimeout(function() {
      gd._ristHandledRamanRatioClick = false;
    }, 250);
  }

  if (getComputedStyle(gd).position === "static") gd.style.position = "relative";
  var toolbar = gd.querySelector(".rist-plot-control-row");
  if (!toolbar) {
    toolbar = document.createElement("div");
    toolbar.className = "rist-plot-control-row";
    gd.appendChild(toolbar);
  }
  var control = document.createElement("div");
  control.className = "rist-raman-ratio-control";
  control.innerHTML =
    "<button type='button' class='rist-raman-ratio-button' title='분자 피크와 분모 피크를 차례로 선택'>비율</button>"
    + "<button type='button' class='rist-raman-ratio-button' title='표시한 비율 삭제'>삭제</button>"
    + "<span class='rist-raman-ratio-status'></span>";
  toolbar.appendChild(control);
  var ratioButton = control.querySelectorAll(".rist-raman-ratio-button")[0];
  var clearButton = control.querySelectorAll(".rist-raman-ratio-button")[1];
  var status = control.querySelector(".rist-raman-ratio-status");

  ratioButton.addEventListener("click", function() {
    ratioMode = !ratioMode;
    pendingNumerator = null;
    syncControls();
  });
  clearButton.addEventListener("click", function() {
    ratios = [];
    pendingNumerator = null;
    renderRatios().then(syncControls);
  });
  gd.addEventListener("mousedown", handleRatioPeakPointer, true);
  gd.addEventListener("click", handleRatioPeakPointer, true);
  gd.on("plotly_click", function(ev) {
    if (!ratioMode || !ev || !ev.points || !ev.points.length) return;
    if (
      gd._ristHandledRamanRatioClick
      || (gd._ristHandledRamanRatioAt && Date.now() - gd._ristHandledRamanRatioAt < 250)
    ) return;
    var curve = ev.points[0].curveNumber;
    pickPeak(curve);
  });
  gd.addEventListener("rist-raman-stack-change", function() {
    if (ratios.length) renderRatios();
  });
  gd.addEventListener("rist-plot-data-replaced", function() {
    ratios = [];
    ratioMode = false;
    pendingNumerator = null;
    setTimeout(syncControls, 0);
  });
  syncControls();
})();
</script>
"""


_UPLOAD_SCRIPT = """
<script>
(function() {
  var gd = document.getElementById("raman-plot");
  var input = document.getElementById("raman-file-input");
  var dropZone = document.getElementById("raman-drop-zone");
  var prompt = document.getElementById("raman-drop-prompt");
  var fileList = document.getElementById("raman-file-list");
  var status = document.getElementById("raman-status");
  var message = document.getElementById("raman-message");
  var loading = document.getElementById("raman-loading");
  var reportProgress = document.getElementById("raman-report-progress");
  var reportProgressLabel = document.getElementById("raman-report-progress-label");
  var reportProgressValue = document.getElementById("raman-report-progress-value");
  var reportProgressBar = document.getElementById("raman-report-progress-bar");
  var clearButton = document.getElementById("raman-clear");
  var reportButton = document.getElementById("raman-report");
  var libraryInput = document.getElementById("raman-library-input");
  var libraryList = document.getElementById("raman-library-list");
  var libraryFilter = document.getElementById("raman-library-filter");
  var libraryNew = document.getElementById("raman-library-new");
  var libraryModal = document.getElementById("raman-library-modal");
  var libraryDialogTitle = document.getElementById("raman-library-dialog-title");
  var libraryDialogMeta = document.getElementById("raman-library-dialog-meta");
  var libraryDialogBody = document.getElementById("raman-library-dialog-body");
  var libraryDialogClose = document.getElementById("raman-library-dialog-close");
  var libraryRowAdd = document.getElementById("raman-library-row-add");
  var libraryDialogCancel = document.getElementById("raman-library-dialog-cancel");
  var libraryDialogSave = document.getElementById("raman-library-dialog-save");
  var reportMetaControls = Array.prototype.slice.call(
    document.querySelectorAll("#raman-report-meta [data-report-field]")
  );
  var reportOptionsOpen = document.getElementById("raman-report-options-open");
  var reportOptionsModal = document.getElementById("raman-report-options-modal");
  var reportOptionsBody = document.getElementById("raman-report-options-body");
  var reportOptionsClose = document.getElementById("raman-report-options-close");
  var reportOptionsCancel = document.getElementById("raman-report-options-cancel");
  var reportOptionsSave = document.getElementById("raman-report-options-save");
  var reportOptionsReset = document.getElementById("raman-report-options-reset");
  var MESSAGE_AUTO_HIDE_MS = 5000;
  var messageTimer = null;
  if (!gd || !input || !dropZone || !prompt || !fileList || !status || !message
      || !loading || !clearButton || !libraryInput || !libraryList
      || !libraryFilter || !libraryNew || !libraryModal || !libraryDialogClose
      || !libraryRowAdd || !libraryDialogCancel || !libraryDialogSave
      || !reportOptionsOpen || !reportOptionsModal || !reportOptionsBody
      || !reportOptionsClose || !reportOptionsCancel || !reportOptionsSave
      || !reportOptionsReset
      || !reportButton || !reportProgress || !reportProgressLabel
      || !reportProgressValue || !reportProgressBar) return;

  var files = [];
  var latestAnalysisPayload = null;
  var libraries = [];
  var selectedLibraryIds = [];
  var libraryDeleteEnabled = false;
  var libraryDeleteButton = null;
  var activeLibraryId = null;
  var activeLibraryIsNew = false;
  var emptyData = JSON.parse(JSON.stringify(gd.data || []));
  var emptyLayout = JSON.parse(JSON.stringify(gd.layout || {}));
  var MAX_FILES = 10;
  var MAX_FILE_BYTES = 20 * 1024 * 1024;
  var MAX_TOTAL_BYTES = 50 * 1024 * 1024;
  var SESSION_DB_NAME = "rist-raman-workspace-v1";
  var SESSION_STORE = "workspace";
  var SESSION_KEY = "current";
  var REPORT_OPTION_STORAGE_KEY = "rist-raman-report-condition-options-v1";
  var REPORT_OPTION_FIELDS = [
    {
      field: "laserPreset",
      label: "Laser",
      datalistId: "raman-report-laser-options",
      defaults: ["532 nm", "633 nm", "785 nm", "1064 nm", "514 nm", "488 nm", "780 nm"]
    },
    {
      field: "exposure",
      label: "Exposure / Accumulation",
      datalistId: "raman-report-exposure-options",
      defaults: ["1 s x 10", "5 s x 3", "10 s x 3", "30 s x 1", "60 s x 1"]
    },
    {
      field: "sampleDescription",
      label: "시료 정보",
      datalistId: "raman-report-sample-options",
      defaults: [
        "air-sensitive sample",
        "powder sample",
        "carbon material",
        "lithium compound",
        "layered oxide cathode"
      ]
    },
    {
      field: "requestPurpose",
      label: "분석 목적",
      datalistId: "raman-report-purpose-options",
      defaults: [
        "LiOH/탄산염 피크 확인",
        "D/G ratio 비교",
        "탄소 D/G/2D band 확인",
        "LMR layered oxide mode 확인",
        "시료 간 Raman 피크 비교"
      ]
    }
  ];
  var reportOptionValues = loadReportOptionValues();
  var reportOptionDraft = null;
  var reportPickerMenu = null;
  var activeReportPickerControl = null;
  var workspaceDbPromise = null;
  var restoreInProgress = false;
  var saveTimer = 0;

  function cloneReportOptions(source) {
    var result = {};
    REPORT_OPTION_FIELDS.forEach(function(config) {
      result[config.field] = (source[config.field] || []).slice();
    });
    return result;
  }

  function defaultReportOptions() {
    var result = {};
    REPORT_OPTION_FIELDS.forEach(function(config) {
      result[config.field] = config.defaults.slice();
    });
    return result;
  }

  function normalizeReportOptionValues(values) {
    var seen = {};
    return (values || []).map(function(value) {
      return String(value || "").trim();
    }).filter(function(value) {
      var key = value.toLowerCase();
      if (!value || seen[key]) return false;
      seen[key] = true;
      return true;
    });
  }

  function loadReportOptionValues() {
    var defaults = defaultReportOptions();
    try {
      var raw = window.localStorage.getItem(REPORT_OPTION_STORAGE_KEY);
      if (!raw) return defaults;
      var parsed = JSON.parse(raw);
      REPORT_OPTION_FIELDS.forEach(function(config) {
        if (Array.isArray(parsed[config.field])) {
          defaults[config.field] = normalizeReportOptionValues(parsed[config.field]);
        }
      });
    } catch (err) {
      return defaults;
    }
    return defaults;
  }

  function saveReportOptionValues() {
    try {
      window.localStorage.setItem(
        REPORT_OPTION_STORAGE_KEY,
        JSON.stringify(reportOptionValues)
      );
    } catch (err) {}
  }

  function renderReportDatalists() {
    REPORT_OPTION_FIELDS.forEach(function(config) {
      var list = document.getElementById(config.datalistId);
      if (!list) return;
      list.innerHTML = "";
      (reportOptionValues[config.field] || []).forEach(function(value) {
        var option = document.createElement("option");
        option.value = value;
        list.appendChild(option);
      });
    });
  }

  function reportOptionConfigForControl(control) {
    return REPORT_OPTION_FIELDS.find(function(config) {
      return config.field === control.dataset.reportField;
    });
  }

  function reportOptionListForControl(control) {
    var config = reportOptionConfigForControl(control);
    if (!config) return [];
    return normalizeReportOptionValues(reportOptionValues[config.field] || []);
  }

  function ensureReportPickerMenu() {
    if (reportPickerMenu) return reportPickerMenu;
    reportPickerMenu = document.createElement("div");
    reportPickerMenu.className = "raman-report-picker-menu";
    reportPickerMenu.setAttribute("role", "listbox");
    document.body.appendChild(reportPickerMenu);
    reportPickerMenu.addEventListener("click", function(ev) {
      ev.stopPropagation();
    });
    return reportPickerMenu;
  }

  function closeReportOptionPicker() {
    if (!reportPickerMenu) return;
    reportPickerMenu.classList.remove("is-visible");
    reportMetaControls.forEach(function(control) {
      var button = control._ristReportPickerButton;
      if (button) button.setAttribute("aria-expanded", "false");
    });
    activeReportPickerControl = null;
  }

  function positionReportPickerMenu(control) {
    var menu = ensureReportPickerMenu();
    var rect = (control.closest(".raman-report-picker-row") || control).getBoundingClientRect();
    var gap = 4;
    var width = Math.min(Math.max(rect.width, 180), window.innerWidth - 16);
    var left = Math.min(Math.max(8, rect.left), window.innerWidth - width - 8);
    var top = rect.bottom + gap;
    var menuHeight = Math.min(menu.scrollHeight || 260, window.innerHeight - 24);
    if (top + menuHeight > window.innerHeight - 8) {
      top = Math.max(8, rect.top - menuHeight - gap);
    }
    menu.style.left = left + "px";
    menu.style.top = top + "px";
    menu.style.width = width + "px";
  }

  function openReportOptionPicker(control) {
    var menu = ensureReportPickerMenu();
    var options = reportOptionListForControl(control);
    activeReportPickerControl = control;
    menu.innerHTML = "";
    if (!options.length) {
      var empty = document.createElement("div");
      empty.className = "raman-report-picker-empty";
      empty.textContent = "선택지가 없습니다.";
      menu.appendChild(empty);
    } else {
      options.forEach(function(value) {
        var item = document.createElement("button");
        item.type = "button";
        item.className = "raman-report-picker-item";
        item.setAttribute("role", "option");
        item.textContent = value;
        item.addEventListener("click", function() {
          control.value = value;
          control.dispatchEvent(new Event("input", {bubbles: true}));
          control.dispatchEvent(new Event("change", {bubbles: true}));
          closeReportOptionPicker();
          control.focus();
        });
        menu.appendChild(item);
      });
    }
    reportMetaControls.forEach(function(item) {
      var button = item._ristReportPickerButton;
      if (button) button.setAttribute("aria-expanded", item === control ? "true" : "false");
    });
    menu.classList.add("is-visible");
    positionReportPickerMenu(control);
  }

  function installReportOptionPickers() {
    reportMetaControls.forEach(function(control) {
      if (!control.getAttribute("list") || control._ristReportPickerButton) return;
      var row = document.createElement("div");
      row.className = "raman-report-picker-row";
      control.parentNode.insertBefore(row, control);
      row.appendChild(control);
      var button = document.createElement("button");
      button.type = "button";
      button.className = "raman-report-picker-button";
      button.textContent = "▼";
      button.title = "선택지 열기";
      button.setAttribute("aria-label", (control.dataset.reportLabel || "항목") + " 선택지 열기");
      button.setAttribute("aria-expanded", "false");
      button.addEventListener("click", function(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        if (activeReportPickerControl === control && reportPickerMenu
            && reportPickerMenu.classList.contains("is-visible")) {
          closeReportOptionPicker();
          return;
        }
        openReportOptionPicker(control);
      });
      row.appendChild(button);
      control._ristReportPickerButton = button;
    });
  }

  function renderReportOptionsEditor(focusTarget) {
    reportOptionsBody.innerHTML = "";
    REPORT_OPTION_FIELDS.forEach(function(config) {
      var values = reportOptionDraft[config.field] || [];
      var group = document.createElement("section");
      group.className = "raman-report-option-group";

      var header = document.createElement("div");
      header.className = "raman-report-option-group-header";
      var title = document.createElement("span");
      title.textContent = config.label;
      var count = document.createElement("span");
      count.className = "raman-report-option-count";
      count.textContent = values.length + "개";
      header.appendChild(title);
      header.appendChild(count);
      group.appendChild(header);

      var list = document.createElement("div");
      list.className = "raman-report-option-list";
      values.forEach(function(value, index) {
        var row = document.createElement("div");
        row.className = "raman-report-option-row";
        var inputEl = document.createElement("input");
        inputEl.type = "text";
        inputEl.value = value;
        inputEl.placeholder = "선택지 입력";
        inputEl.dataset.optionField = config.field;
        inputEl.dataset.optionIndex = String(index);
        inputEl.addEventListener("input", function() {
          reportOptionDraft[config.field][index] = inputEl.value;
        });
        var removeButton = document.createElement("button");
        removeButton.type = "button";
        removeButton.className = "raman-report-option-remove";
        removeButton.setAttribute("aria-label", config.label + " 선택지 삭제");
        removeButton.textContent = "×";
        removeButton.addEventListener("click", function() {
          reportOptionDraft[config.field].splice(index, 1);
          renderReportOptionsEditor();
        });
        row.appendChild(inputEl);
        row.appendChild(removeButton);
        list.appendChild(row);
      });
      group.appendChild(list);

      var addButton = document.createElement("button");
      addButton.type = "button";
      addButton.className = "raman-library-dialog-button raman-report-option-add";
      addButton.textContent = config.label + " 추가";
      addButton.addEventListener("click", function() {
        reportOptionDraft[config.field].push("");
        renderReportOptionsEditor({
          field: config.field,
          index: reportOptionDraft[config.field].length - 1
        });
      });
      group.appendChild(addButton);
      reportOptionsBody.appendChild(group);
    });

    if (focusTarget) {
      var selector = '[data-option-field="' + focusTarget.field + '"][data-option-index="'
        + focusTarget.index + '"]';
      var target = reportOptionsBody.querySelector(selector);
      if (target) target.focus();
    }
  }

  function openReportOptionsEditor() {
    reportOptionDraft = cloneReportOptions(reportOptionValues);
    renderReportOptionsEditor();
    reportOptionsModal.classList.add("is-visible");
  }

  function closeReportOptionsEditor() {
    reportOptionDraft = null;
    reportOptionsModal.classList.remove("is-visible");
  }

  function saveReportOptionsEditor() {
    var normalized = {};
    REPORT_OPTION_FIELDS.forEach(function(config) {
      normalized[config.field] = normalizeReportOptionValues(reportOptionDraft[config.field]);
    });
    reportOptionValues = normalized;
    saveReportOptionValues();
    renderReportDatalists();
    closeReportOptionPicker();
    closeReportOptionsEditor();
  }

  function resetReportOptionsEditor() {
    reportOptionDraft = defaultReportOptions();
    renderReportOptionsEditor();
  }

  function openWorkspaceDb() {
    if (workspaceDbPromise) return workspaceDbPromise;
    workspaceDbPromise = new Promise(function(resolve, reject) {
      var request = indexedDB.open(SESSION_DB_NAME, 1);
      request.onupgradeneeded = function() {
        request.result.createObjectStore(SESSION_STORE);
      };
      request.onsuccess = function() { resolve(request.result); };
      request.onerror = function() { reject(request.error); };
    });
    return workspaceDbPromise;
  }

  function workspaceStore(mode) {
    return openWorkspaceDb().then(function(db) {
      return db.transaction(SESSION_STORE, mode).objectStore(SESSION_STORE);
    });
  }

  function fileRecord(file) {
    return {
      name: file.name,
      type: file.type || "application/octet-stream",
      lastModified: file.lastModified || Date.now(),
      blob: file
    };
  }

  function recordFile(record) {
    return new File(
      [record.blob],
      record.name,
      {type: record.type || "application/octet-stream", lastModified: record.lastModified}
    );
  }

  function freshEmptyData() {
    return JSON.parse(JSON.stringify(emptyData));
  }

  function freshEmptyLayout() {
    return JSON.parse(JSON.stringify(emptyLayout));
  }

  function currentWorkspaceState() {
    return {
      version: 1,
      files: files.map(fileRecord),
      selectedLibraryIds: selectedLibraryIds.slice(),
      reportMetadata: reportMetadataFormState(),
      sensitivity: gd._ristPeakSensitivityValue || 25,
      statusText: status.textContent || "",
      analysisPayload: latestAnalysisPayload,
      plotData: JSON.parse(JSON.stringify(gd.data || [])),
      plotLayout: JSON.parse(JSON.stringify(gd.layout || {}))
    };
  }

  function saveWorkspaceNow() {
    if (restoreInProgress) return Promise.resolve();
    return workspaceStore("readwrite").then(function(store) {
      return new Promise(function(resolve, reject) {
        var request = store.put(currentWorkspaceState(), SESSION_KEY);
        request.onsuccess = function() { resolve(); };
        request.onerror = function() { reject(request.error); };
      });
    }).catch(function() {});
  }

  function scheduleWorkspaceSave() {
    if (restoreInProgress) return;
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(function() {
      saveTimer = 0;
      saveWorkspaceNow();
    }, 350);
  }

  function clearWorkspaceState() {
    if (saveTimer) {
      clearTimeout(saveTimer);
      saveTimer = 0;
    }
    return workspaceStore("readwrite").then(function(store) {
      return new Promise(function(resolve, reject) {
        var request = store.delete(SESSION_KEY);
        request.onsuccess = function() { resolve(); };
        request.onerror = function() { reject(request.error); };
      });
    }).catch(function() {});
  }

  function restoreWorkspace() {
    return workspaceStore("readonly").then(function(store) {
      return new Promise(function(resolve, reject) {
        var request = store.get(SESSION_KEY);
        request.onsuccess = function() { resolve(request.result || null); };
        request.onerror = function() { reject(request.error); };
      });
    }).then(function(state) {
      if (!state || state.version !== 1) return null;
      restoreInProgress = true;
      files = (state.files || []).map(recordFile);
      selectedLibraryIds = (state.selectedLibraryIds || []).slice();
      applyReportMetadataFormState(state.reportMetadata || {});
      latestAnalysisPayload = state.analysisPayload || null;
      if (Number.isFinite(Number(state.sensitivity))) {
        gd._ristPeakSensitivityValue = Number(state.sensitivity);
      }
      renderFiles();
      status.textContent = state.statusText || status.textContent;
      if (state.plotData && state.plotLayout) {
        return window.Plotly.react(
          gd,
          state.plotData,
          state.plotLayout,
          gd._context
        ).then(function() {
          dispatchDataReplaced(gd._ristPeakSensitivityValue || 25);
          window.Plotly.Plots.resize(gd);
          return state;
        }).finally(function() {
          restoreInProgress = false;
        });
      }
      restoreInProgress = false;
      return state;
    }).catch(function() {
      restoreInProgress = false;
      return null;
    });
  }

  function installWorkspaceAutosave() {
    gd.on("plotly_relayout", scheduleWorkspaceSave);
    gd.on("plotly_restyle", scheduleWorkspaceSave);
    [
      "rist-legend-name-change",
      "rist-legend-color-change",
      "rist-legend-visibility-change",
      "rist-peak-delete",
      "rist-peak-group-change",
      "rist-peak-group-clear",
      "rist-peak-group-update",
      "rist-history-restored",
      "rist-plot-data-replaced",
      "rist-raman-stack-change"
    ].forEach(function(name) {
      gd.addEventListener(name, scheduleWorkspaceSave);
    });
  }

  function dispatchDataReplaced(sensitivity) {
    gd.dispatchEvent(new CustomEvent("rist-plot-data-replaced", {
      detail: { sensitivity: sensitivity }
    }));
  }

  function applyResponsiveLayout() {
    var compact = window.innerWidth <= 760;
    return window.Plotly.relayout(gd, compact ? {
      "height": 900,
      "margin.t": 82,
      "margin.r": 30,
      "margin.b": 150,
      "legend.orientation": "h",
      "legend.x": 0.5,
      "legend.xanchor": "center",
      "legend.y": -0.30,
      "legend.yanchor": "top"
    } : {
      "height": 720,
      "margin.t": 90,
      "margin.r": (gd.data || []).length ? 260 : 70,
      "margin.b": 70,
      "legend.orientation": "v",
      "legend.x": 1.02,
      "legend.xanchor": "left",
      "legend.y": 1.0,
      "legend.yanchor": "top"
    });
  }

  function fileKey(file) {
    return [file.name, file.size, file.lastModified].join(":");
  }

  function reportMetadataFormState() {
    var state = {};
    reportMetaControls.forEach(function(control) {
      state[control.dataset.reportField] = control.value || "";
    });
    return state;
  }

  function applyReportMetadataFormState(state) {
    reportMetaControls.forEach(function(control) {
      var field = control.dataset.reportField;
      if (Object.prototype.hasOwnProperty.call(state, field)) {
        control.value = state[field] || "";
      }
    });
  }

  function clearReportMetadataForm() {
    reportMetaControls.forEach(function(control) {
      control.value = "";
    });
  }

  function normalizedMetadataKey(value) {
    return String(value || "")
      .toLowerCase()
      .replace(/[^a-z0-9가-힣]+/g, "");
  }

  function sampleMetadataItems(payload) {
    var items = [];
    (payload.samples || []).forEach(function(sample) {
      var metadata = sample && sample.metadata;
      if (!metadata || typeof metadata !== "object") return;
      Object.keys(metadata).forEach(function(key) {
        var value = metadata[key];
        if (value == null || String(value).trim() === "") return;
        items.push({
          sample: sample.label || sample.fileName || "",
          key: key,
          normalizedKey: normalizedMetadataKey(key),
          value: String(value).trim()
        });
      });
    });
    return items;
  }

  function firstMetadataValue(items, aliases) {
    var normalizedAliases = aliases.map(normalizedMetadataKey);
    for (var i = 0; i < items.length; i++) {
      for (var j = 0; j < normalizedAliases.length; j++) {
        if (items[i].normalizedKey === normalizedAliases[j]) {
          return items[i].value;
        }
      }
    }
    for (var k = 0; k < items.length; k++) {
      for (var m = 0; m < normalizedAliases.length; m++) {
        if (items[k].normalizedKey.indexOf(normalizedAliases[m]) >= 0) {
          return items[k].value;
        }
      }
    }
    return "";
  }

  function normalizedDateValue(value) {
    var text = String(value || "").trim();
    var match = text.match(/(20\\d{2})[-/.년\\s]*(\\d{1,2})[-/.월\\s]*(\\d{1,2})/);
    if (!match) return "";
    return [
      match[1],
      String(Number(match[2])).padStart(2, "0"),
      String(Number(match[3])).padStart(2, "0")
    ].join("-");
  }

  function normalizedLaserValue(value) {
    var text = String(value || "");
    var match = text.match(/(532|633|785|1064)(?:\\.\\d+)?\\s*nm/i);
    return match ? match[1] + " nm" : text;
  }

  function setReportControlIfEmpty(field, value) {
    var control = reportMetaControls.find(function(item) {
      return item.dataset.reportField === field;
    });
    if (!control || control.value || !value) return;
    if (control.tagName === "SELECT") {
      var normalizedValue = normalizedMetadataKey(value);
      var matched = Array.prototype.slice.call(control.options).find(function(option) {
        var optionValue = normalizedMetadataKey(option.value);
        return option.value && (
          normalizedValue === optionValue
          || normalizedValue.indexOf(optionValue) >= 0
          || optionValue.indexOf(normalizedValue) >= 0
        );
      });
      if (matched) control.value = matched.value;
      return;
    }
    control.value = value;
  }

  function metadataDetailText(items) {
    var seen = {};
    var lines = [];
    items.forEach(function(item) {
      var key = item.sample + "|" + item.key + "|" + item.value;
      if (seen[key]) return;
      seen[key] = true;
      lines.push(
        (item.sample ? item.sample + " - " : "")
        + item.key + ": " + item.value
      );
    });
    return lines.join("\\n");
  }

  function populateReportMetadataFromPayload(payload) {
    var items = sampleMetadataItems(payload || {});
    if (!items.length) return;
    setReportControlIfEmpty(
      "measurementDate",
      normalizedDateValue(firstMetadataValue(items, [
        "measurement date",
        "acquisition date",
        "date",
        "측정일",
        "측정 날짜"
      ]))
    );
    setReportControlIfEmpty("requester", firstMetadataValue(items, [
      "requester",
      "requested by",
      "의뢰자",
      "요청자"
    ]));
    setReportControlIfEmpty(
      "laserPreset",
      normalizedLaserValue(firstMetadataValue(items, [
        "excitation wavelength",
        "laser",
        "laser wavelength",
        "wavelength",
        "여기 파장",
        "레이저"
      ]))
    );
    setReportControlIfEmpty("exposure", firstMetadataValue(items, [
      "exposure time",
      "exposure",
      "accumulation",
      "acquisition time",
      "integration time",
      "노출시간",
      "적산"
    ]));
    setReportControlIfEmpty("sampleDescription", firstMetadataValue(items, [
      "sample",
      "sample name",
      "sample id",
      "시료",
      "시료명"
    ]));
    setReportControlIfEmpty("requestPurpose", firstMetadataValue(items, [
      "purpose",
      "analysis purpose",
      "request purpose",
      "분석 목적",
      "의뢰 목적"
    ]));
    setReportControlIfEmpty("conditionDetail", metadataDetailText(items));
    scheduleWorkspaceSave();
  }

  function reportMetadataConditions() {
    var conditions = {};
    reportMetaControls.forEach(function(control) {
      var value = (control.value || "").trim();
      if (!value) return;
      var label = control.dataset.reportLabel || control.dataset.reportField;
      conditions[label] = value;
    });
    return conditions;
  }

  function reportAnalysisPayload() {
    var payload = JSON.parse(JSON.stringify(latestAnalysisPayload || {}));
    var conditions = reportMetadataConditions();
    if (Object.keys(conditions).length) {
      payload.experimentConditions = Object.assign(
        {},
        payload.experimentConditions || {},
        conditions
      );
      if (conditions["시료 정보"]) payload.sample = conditions["시료 정보"];
      if (conditions["분석 목적"]) payload.requestPurpose = conditions["분석 목적"];
    }
    return payload;
  }

  function clearMessageTimer() {
    if (messageTimer) {
      window.clearTimeout(messageTimer);
      messageTimer = null;
    }
  }

  function setMessage(text) {
    clearMessageTimer();
    message.textContent = text || "";
    message.classList.remove("is-success");
    message.classList.toggle("is-visible", !!text);
    if (text) {
      messageTimer = window.setTimeout(function() {
        if (!message.classList.contains("is-success")) {
          setMessage("");
        }
      }, MESSAGE_AUTO_HIDE_MS);
    }
  }

  function setBusy(busy) {
    loading.classList.toggle("is-visible", busy);
    input.disabled = busy;
    reportButton.disabled = busy;
    clearButton.disabled = busy;
    reportMetaControls.forEach(function(control) {
      control.disabled = busy;
    });
    libraryInput.disabled = busy;
    libraryFilter.disabled = busy;
    libraryNew.disabled = busy;
    libraryList.querySelectorAll("input, button").forEach(function(control) {
      control.disabled = busy;
    });
  }

  function setReportProgress(job) {
    if (!job) {
      reportProgress.classList.remove("is-visible");
      reportProgressBar.style.width = "0%";
      reportProgressLabel.textContent = "보고서 생성 대기";
      reportProgressValue.textContent = "0%";
      return;
    }
    var pct = Math.max(0, Math.min(100, Number(job.progressPct || 0)));
    if (job.status === "completed" || pct >= 100) {
      reportProgress.classList.remove("is-visible");
      reportProgressBar.style.width = "0%";
      reportProgressLabel.textContent = job.message || "보고서가 완성되었습니다.";
      reportProgressValue.textContent = "100%";
      status.textContent = "보고서 생성 완료";
      return;
    }
    reportProgress.classList.add("is-visible");
    reportProgressBar.style.width = pct + "%";
    reportProgressValue.textContent = pct + "%";
    reportProgressLabel.textContent = job.message || "보고서 생성 중입니다.";
    status.textContent = job.status === "completed"
      ? "보고서 생성 완료"
      : "보고서 생성 중 · " + (job.stage || "대기");
  }

  function wait(ms) {
    return new Promise(function(resolve) { setTimeout(resolve, ms); });
  }

  async function fetchJson(url, options) {
    var response = await fetch(url, options || {});
    var payload = await response.json().catch(function() { return {}; });
    if (!response.ok) {
      throw new Error(payload.message || payload.error || "요청에 실패했습니다.");
    }
    return payload;
  }

  async function pollReportJob(jobId) {
    for (;;) {
      await wait(900);
      var job = await fetchJson("/api/v1/raman/report/jobs/" + encodeURIComponent(jobId));
      setReportProgress(job);
      if (job.status === "completed") return job;
      if (job.status === "failed") {
        throw new Error(job.error || job.message || "보고서 생성에 실패했습니다.");
      }
    }
  }

  function setReportDownloadLink(job) {
    var downloadUrl = job.downloadUrl
      || ("/api/v1/raman/report/jobs/" + encodeURIComponent(job.jobId) + "/download");
    var filename = job.filename || "raman-report-package.zip";
    clearMessageTimer();
    message.textContent = "";
    message.classList.add("is-visible", "is-success");
    var label = document.createElement("span");
    label.textContent = "보고서가 완성되었습니다. ";
    var link = document.createElement("a");
    link.href = downloadUrl;
    link.download = filename;
    link.textContent = "보고서 다운로드";
    var close = document.createElement("button");
    close.type = "button";
    close.className = "raman-message-close";
    close.setAttribute("aria-label", "알림 닫기");
    close.textContent = "×";
    close.addEventListener("click", function() {
      setMessage("");
    });
    message.appendChild(label);
    message.appendChild(link);
    message.appendChild(close);
  }

  function updateIdleStatus() {
    if (files.length) return;
    status.textContent = selectedLibraryIds.length
      ? "피크 라이브러리 " + selectedLibraryIds.length + "개 적용"
      : "Raman raw 파일을 업로드하세요";
  }

  async function apiPayload(response, fallback) {
    var payload = null;
    try {
      payload = await response.json();
    } catch (err) {
      payload = null;
    }
    if (!response.ok) {
      throw new Error(
        (payload && (payload.message || payload.detail))
        || fallback
        || "Raman 요청에 실패했습니다."
      );
    }
    return payload;
  }

  function appendCell(row, className) {
    var cell = document.createElement("td");
    if (className) cell.className = className;
    row.appendChild(cell);
    return cell;
  }

  function editorInput(type, value, field) {
    var inputElement = document.createElement("input");
    inputElement.type = type;
    inputElement.value = value == null ? "" : String(value);
    inputElement.dataset.field = field;
    return inputElement;
  }

  function formField(labelText, control, wide) {
    var label = document.createElement("label");
    label.className = "raman-library-field" + (wide ? " is-wide" : "");
    var caption = document.createElement("span");
    caption.textContent = labelText;
    label.appendChild(caption);
    label.appendChild(control);
    return label;
  }

  function addAssignmentRow(assignment) {
    var body = libraryDialogBody.querySelector("tbody");
    if (!body) return;
    var values = assignment || {
      centerWavenumber: 1350,
      tolerance: 30,
      name: "",
      color: "#64748b",
      note: ""
    };
    var row = document.createElement("tr");
    var center = editorInput("number", values.centerWavenumber, "centerWavenumber");
    center.step = "0.1";
    center.min = "0.1";
    appendCell(row, "numeric").appendChild(center);
    var tolerance = editorInput("number", values.tolerance, "tolerance");
    tolerance.step = "0.1";
    tolerance.min = "0.1";
    appendCell(row, "numeric").appendChild(tolerance);
    appendCell(row, "").appendChild(editorInput("text", values.name || "", "name"));
    appendCell(row, "color").appendChild(
      editorInput("color", values.color || "#64748b", "color")
    );
    appendCell(row, "").appendChild(editorInput("text", values.note || "", "note"));
    var removeCell = appendCell(row, "remove");
    var remove = document.createElement("button");
    remove.type = "button";
    remove.className = "raman-library-row-remove";
    remove.textContent = "×";
    remove.title = "항목 제거";
    remove.setAttribute("aria-label", "항목 제거");
    remove.addEventListener("click", function() { row.remove(); });
    removeCell.appendChild(remove);
    body.appendChild(row);
  }

  function replaceAssignmentRows(assignments) {
    var body = libraryDialogBody.querySelector("tbody");
    if (!body) return;
    body.innerHTML = "";
    (assignments || []).forEach(addAssignmentRow);
    if (!(assignments || []).length) addAssignmentRow();
  }

  function applySuggestedLibrary(library) {
    var idInput = libraryDialogBody.querySelector('[data-field="libraryId"]');
    var nameInput = libraryDialogBody.querySelector('[data-field="libraryName"]');
    var description = libraryDialogBody.querySelector(
      '[data-field="libraryDescription"]'
    );
    if (idInput && !idInput.disabled && library.id) idInput.value = library.id;
    if (nameInput && library.name) nameInput.value = library.name;
    if (description && library.description) {
      description.value = library.description;
    }
    replaceAssignmentRows(library.assignments || []);
  }

  function renderLibrarySuggestControl() {
    var box = document.createElement("div");
    box.className = "raman-library-suggest";
    var input = document.createElement("input");
    input.type = "text";
    input.placeholder = "예: LiOH, graphite, carbonate";
    var button = document.createElement("button");
    button.type = "button";
    button.textContent = "LLM 추천 채우기";
    var hint = document.createElement("span");
    hint.textContent = "저장 전 검토 필요";
    button.addEventListener("click", function() {
      suggestLibraryDraft(input, button);
    });
    input.addEventListener("keydown", function(event) {
      if (event.key === "Enter") {
        event.preventDefault();
        suggestLibraryDraft(input, button);
      }
    });
    box.appendChild(input);
    box.appendChild(button);
    box.appendChild(hint);
    return box;
  }

  function suggestLibraryDraft(input, button) {
    var idInput = libraryDialogBody.querySelector('[data-field="libraryId"]');
    var nameInput = libraryDialogBody.querySelector('[data-field="libraryName"]');
    var material = (input.value || "").trim();
    if (!material && nameInput) material = nameInput.value.trim();
    if (!material && idInput) material = idInput.value.trim();
    if (!material) {
      setMessage("추천할 물질명 또는 계열명을 입력하세요.");
      input.focus();
      return;
    }
    var originalText = button.textContent;
    button.disabled = true;
    libraryDialogSave.disabled = true;
    libraryRowAdd.disabled = true;
    button.textContent = "추천 중...";
    fetch("/api/v1/raman/assignment-libraries/suggest", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        material: material,
        libraryId: idInput ? idInput.value.trim() : "",
        libraryName: nameInput ? nameInput.value.trim() : ""
      })
    }).then(function(response) {
      return apiPayload(response, "LLM 추천 초안을 만들지 못했습니다.");
    }).then(function(payload) {
      applySuggestedLibrary(payload.library || {});
      setMessage(payload.warning || "LLM 추천 초안을 채웠습니다.");
    }).catch(function(err) {
      setMessage(err.message);
    }).finally(function() {
      button.disabled = false;
      libraryDialogSave.disabled = false;
      libraryRowAdd.disabled = false;
      button.textContent = originalText;
    });
  }

  function closeLibraryEditor() {
    activeLibraryId = null;
    activeLibraryIsNew = false;
    libraryModal.classList.remove("is-visible");
    libraryDialogBody.innerHTML = "";
    if (libraryDeleteButton) {
      libraryDeleteButton.remove();
      libraryDeleteButton = null;
    }
  }

  function syncLibraryDeleteButton(library, isNew) {
    if (libraryDeleteButton) {
      libraryDeleteButton.remove();
      libraryDeleteButton = null;
    }
    if (!libraryDeleteEnabled || isNew || !library || !library.id) return;
    libraryDeleteButton = document.createElement("button");
    libraryDeleteButton.type = "button";
    libraryDeleteButton.className = "raman-library-dialog-button danger";
    libraryDeleteButton.textContent = "삭제";
    libraryDeleteButton.addEventListener("click", function() {
      deleteActiveLibrary(library.id, library.name || library.id);
    });
    libraryRowAdd.parentNode.insertBefore(libraryDeleteButton, libraryRowAdd);
  }

  function renderLibraryEditor(library, isNew) {
    activeLibraryId = isNew ? null : library.id;
    activeLibraryIsNew = isNew;
    libraryDialogTitle.textContent = isNew
      ? "새 Raman 피크 라이브러리"
      : "Raman 피크 라이브러리 편집";
    libraryDialogMeta.textContent = isNew
      ? "JSON 라이브러리 생성"
      : library.fileName + " · " + library.assignmentCount + "개";
    libraryDialogBody.innerHTML = "";

    var meta = document.createElement("div");
    meta.className = "raman-library-form-meta";
    var idInput = editorInput("text", isNew ? "" : library.id, "libraryId");
    idInput.placeholder = "예: graphite-carbon";
    idInput.disabled = !isNew;
    var nameInput = editorInput("text", library.name || "", "libraryName");
    var description = document.createElement("textarea");
    description.dataset.field = "libraryDescription";
    description.value = library.description || "";
    meta.appendChild(formField("라이브러리 ID", idInput, false));
    meta.appendChild(formField("라이브러리 이름", nameInput, false));
    meta.appendChild(formField("설명", description, true));
    meta.appendChild(renderLibrarySuggestControl());
    libraryDialogBody.appendChild(meta);

    var table = document.createElement("table");
    table.className = "raman-library-table";
    var head = document.createElement("thead");
    var headRow = document.createElement("tr");
    [
      ["중심 Raman shift", "numeric"],
      ["허용 오차", "numeric"],
      ["피크 이름", ""],
      ["색상", "color"],
      ["비고", ""],
      ["", "remove"]
    ].forEach(function(item) {
      var th = document.createElement("th");
      th.textContent = item[0];
      th.className = item[1];
      headRow.appendChild(th);
    });
    head.appendChild(headRow);
    table.appendChild(head);
    table.appendChild(document.createElement("tbody"));
    libraryDialogBody.appendChild(table);
    (library.assignments || []).forEach(addAssignmentRow);
    if (!(library.assignments || []).length) addAssignmentRow();
    syncLibraryDeleteButton(library, isNew);
    libraryModal.classList.add("is-visible");
  }

  function showLibraryEditor(library) {
    if (!library.valid) {
      setMessage(library.error || "유효하지 않은 라이브러리입니다.");
      return;
    }
    activeLibraryId = library.id;
    libraryDialogTitle.textContent = "Raman 피크 라이브러리 편집";
    libraryDialogMeta.textContent = library.fileName;
    libraryDialogBody.innerHTML = "라이브러리 구성 불러오는 중...";
    libraryModal.classList.add("is-visible");
    fetch("/api/v1/raman/assignment-libraries/" + encodeURIComponent(library.id))
      .then(function(response) {
        return apiPayload(response, "라이브러리 구성을 불러오지 못했습니다.");
      })
      .then(function(payload) {
        if (activeLibraryId === library.id) renderLibraryEditor(payload.library, false);
      })
      .catch(function(err) {
        closeLibraryEditor();
        setMessage(err.message);
      });
  }

  function collectLibraryEditor() {
    function rootValue(field) {
      var element = libraryDialogBody.querySelector('[data-field="' + field + '"]');
      return element ? element.value : "";
    }
    var libraryId = rootValue("libraryId").trim().toLowerCase();
    var libraryName = rootValue("libraryName").trim();
    if (!/^[a-z0-9][a-z0-9-]{0,79}$/.test(libraryId)) {
      throw new Error("라이브러리 ID는 영문 소문자, 숫자, 하이픈으로 입력하세요.");
    }
    if (!libraryName) throw new Error("라이브러리 이름을 입력하세요.");
    var assignments = [];
    libraryDialogBody.querySelectorAll("tbody tr").forEach(function(row) {
      function value(field) {
        var element = row.querySelector('[data-field="' + field + '"]');
        return element ? element.value : "";
      }
      assignments.push({
        centerWavenumber: Number(value("centerWavenumber")),
        tolerance: Number(value("tolerance")),
        name: value("name").trim(),
        color: value("color") || "#64748b",
        note: value("note").trim()
      });
    });
    assignments.forEach(function(item, index) {
      if (!(item.centerWavenumber > 0) || !(item.tolerance > 0) || !item.name) {
        throw new Error((index + 1) + "번 항목의 shift, 허용 오차, 이름을 확인하세요.");
      }
    });
    return {
      id: libraryId,
      name: libraryName,
      description: rootValue("libraryDescription").trim(),
      assignments: assignments
    };
  }

  function saveLibraryEditor() {
    var values;
    try {
      values = collectLibraryEditor();
    } catch (err) {
      setMessage(err.message);
      return;
    }
    var isNew = activeLibraryIsNew;
    var targetId = isNew ? values.id : activeLibraryId;
    var body = {
      name: values.name,
      description: values.description,
      assignments: values.assignments
    };
    if (isNew) body.id = values.id;
    libraryDialogSave.disabled = true;
    libraryRowAdd.disabled = true;
    fetch(
      isNew
        ? "/api/v1/raman/assignment-libraries/create"
        : "/api/v1/raman/assignment-libraries/" + encodeURIComponent(targetId),
      {
        method: isNew ? "POST" : "PUT",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body)
      }
    ).then(function(response) {
      return apiPayload(response, "라이브러리 저장에 실패했습니다.");
    }).then(function(payload) {
      var preferred = selectedLibraryIds.slice();
      if (isNew && preferred.indexOf(payload.library.id) < 0) {
        preferred.push(payload.library.id);
      }
      closeLibraryEditor();
      return loadLibraries(preferred);
    }).then(function() {
      return files.length ? analyze() : null;
    }).catch(function(err) {
      setMessage(err.message);
    }).finally(function() {
      libraryDialogSave.disabled = false;
      libraryRowAdd.disabled = false;
    });
  }

  function deleteActiveLibrary(libraryId, libraryName) {
    if (!libraryDeleteEnabled || !libraryId) return;
    if (!window.confirm("'" + libraryName + "' 라이브러리 파일을 삭제할까요?")) return;
    fetch(
      "/api/v1/raman/assignment-libraries/" + encodeURIComponent(libraryId),
      {method: "DELETE"}
    ).then(function(response) {
      return apiPayload(response, "라이브러리 삭제에 실패했습니다.");
    }).then(function() {
      selectedLibraryIds = selectedLibraryIds.filter(function(id) {
        return id !== libraryId;
      });
      closeLibraryEditor();
      return loadLibraries(selectedLibraryIds);
    }).then(function() {
      return files.length ? analyze() : null;
    }).catch(function(err) {
      setMessage(err.message);
    });
  }

  function renderLibraries() {
    libraryList.innerHTML = "";
    var selected = {};
    selectedLibraryIds.forEach(function(id) { selected[id] = true; });
    var query = libraryFilter.value.trim().toLowerCase();
    var visibleLibraries = libraries.filter(function(library) {
      if (!query) return true;
      return [
        library.id,
        library.name,
        library.description,
        library.fileName
      ].join(" ").toLowerCase().indexOf(query) >= 0;
    }).slice().sort(function(left, right) {
      var leftSelected = selected[left.id] ? 1 : 0;
      var rightSelected = selected[right.id] ? 1 : 0;
      if (leftSelected !== rightSelected) return rightSelected - leftSelected;
      if (left.valid !== right.valid) return left.valid ? -1 : 1;
      if (left.defaultSelected !== right.defaultSelected) {
        return left.defaultSelected ? -1 : 1;
      }
      return left.name.localeCompare(right.name, "ko");
    });
    if (!visibleLibraries.length) {
      var empty = document.createElement("span");
      empty.className = "raman-library-empty";
      empty.textContent = libraries.length ? "검색 결과가 없습니다" : "등록된 라이브러리가 없습니다";
      libraryList.appendChild(empty);
      return;
    }
    visibleLibraries.forEach(function(library) {
      var isSelected = selectedLibraryIds.indexOf(library.id) >= 0;
      var item = document.createElement("span");
      item.className = "raman-library-item"
        + (isSelected ? " is-selected" : "")
        + (library.valid ? "" : " is-invalid");
      var toggle = document.createElement("span");
      toggle.className = "raman-library-toggle";
      var checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = isSelected;
      checkbox.disabled = !library.valid;
      checkbox.setAttribute("aria-label", library.name + " 선택");
      checkbox.addEventListener("change", function() {
        if (checkbox.checked) {
          if (selectedLibraryIds.indexOf(library.id) < 0) selectedLibraryIds.push(library.id);
        } else {
          selectedLibraryIds = selectedLibraryIds.filter(function(id) {
            return id !== library.id;
          });
        }
        renderLibraries();
        updateIdleStatus();
        if (files.length) analyze();
        else scheduleWorkspaceSave();
      });
      toggle.appendChild(checkbox);
      var name = document.createElement("button");
      name.type = "button";
      name.className = "raman-library-name";
      name.textContent = library.name;
      name.title = library.name + " 편집";
      name.addEventListener("click", function() { showLibraryEditor(library); });
      var count = document.createElement("span");
      count.className = "raman-library-count";
      count.textContent = library.valid ? String(library.assignmentCount) : "오류";
      var state = document.createElement("span");
      state.className = "raman-library-state";
      state.textContent = isSelected ? "적용" : "미적용";
      item.appendChild(toggle);
      item.appendChild(name);
      item.appendChild(count);
      item.appendChild(state);
      libraryList.appendChild(item);
    });
  }

  function loadLibraries(preferredIds) {
    return fetch("/api/v1/raman/assignment-libraries")
      .then(function(response) {
        return apiPayload(response, "피크 라이브러리를 불러오지 못했습니다.");
      })
      .then(function(payload) {
        libraries = payload.libraries || [];
        libraryDeleteEnabled = !!payload.deleteEnabled;
        var validIds = {};
        libraries.forEach(function(item) {
          if (item.valid) validIds[item.id] = true;
        });
        var requested = preferredIds || selectedLibraryIds;
        selectedLibraryIds = requested.filter(function(id) {
          return validIds[id];
        });
        if (!selectedLibraryIds.length && !preferredIds) {
          selectedLibraryIds = libraries
            .filter(function(item) { return item.valid && item.defaultSelected; })
            .map(function(item) { return item.id; });
        }
        renderLibraries();
        updateIdleStatus();
      })
      .catch(function(err) {
        libraries = [];
        selectedLibraryIds = [];
        renderLibraries();
        setMessage(err.message);
      });
  }

  function uploadLibrary(file) {
    if (!file) return;
    if (!/\\.(json|csv)$/i.test(file.name)) {
      setMessage("JSON 또는 CSV 라이브러리 파일만 업로드할 수 있습니다.");
      return;
    }
    if (file.size > 2 * 1024 * 1024) {
      setMessage("라이브러리 파일은 2MB 이하여야 합니다.");
      return;
    }
    var form = new FormData();
    form.append("file", file, file.name);
    loading.textContent = "라이브러리 업로드 중...";
    setBusy(true);
    fetch("/api/v1/raman/assignment-libraries", {
      method: "POST",
      body: form
    }).then(function(response) {
      return apiPayload(response, "라이브러리 업로드에 실패했습니다.");
    }).then(function(payload) {
      var preferred = selectedLibraryIds.slice();
      if (preferred.indexOf(payload.library.id) < 0) preferred.push(payload.library.id);
      return loadLibraries(preferred);
    }).then(function() {
      return files.length ? analyze() : null;
    }).catch(function(err) {
      setMessage(err.message);
    }).finally(function() {
      setBusy(false);
      loading.textContent = "Raman 전처리 중...";
    });
  }

  function renderFiles() {
    fileList.innerHTML = "";
    prompt.style.display = files.length ? "none" : "inline";
    files.forEach(function(file, index) {
      var chip = document.createElement("span");
      chip.className = "raman-file-chip";
      var name = document.createElement("span");
      name.className = "raman-file-name";
      name.textContent = file.name;
      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "raman-file-remove";
      remove.textContent = "×";
      remove.title = file.name + " 제거";
      remove.setAttribute("aria-label", file.name + " 제거");
      remove.addEventListener("click", function() {
        files.splice(index, 1);
        renderFiles();
        if (files.length) analyze();
        else resetPlot();
      });
      chip.appendChild(name);
      chip.appendChild(remove);
      fileList.appendChild(chip);
    });
    clearButton.hidden = false;
  }

  function resetPlot() {
    files = [];
    renderFiles();
    setMessage("");
    status.textContent = "Raman raw 파일을 업로드하세요";
    if (window.Plotly) {
      window.Plotly.react(gd, freshEmptyData(), freshEmptyLayout(), gd._context).then(function() {
        dispatchDataReplaced(25);
        return applyResponsiveLayout();
      }).then(function() {
        window.Plotly.Plots.resize(gd);
        scheduleWorkspaceSave();
      });
    }
  }

  async function analyze() {
    if (!files.length) {
      resetPlot();
      return;
    }
    loading.textContent = "Raman 전처리 중...";
    setBusy(true);
    setMessage("");
    try {
      var form = new FormData();
      files.forEach(function(file) { form.append("files", file); });
      var sensitivity = gd._ristPeakSensitivityValue;
      if (!Number.isFinite(Number(sensitivity))) sensitivity = 25;
      form.append("sensitivity", String(Math.max(0, Math.min(100, Number(sensitivity)))));
      form.append("assignment_library_selection_explicit", "true");
      selectedLibraryIds.forEach(function(id) {
        form.append("assignment_library_ids", id);
      });
      var response = await fetch("/api/v1/raman/analyze", {
        method: "POST",
        body: form
      });
      var payload = await apiPayload(response, "Raman 분석에 실패했습니다.");
      latestAnalysisPayload = JSON.parse(JSON.stringify(payload));
      populateReportMetadataFromPayload(payload);
      await window.Plotly.react(
        gd,
        payload.figure.data || [],
        payload.figure.layout || {},
        gd._context
      );
      gd._ristPeakSensitivityValue = payload.settings.sensitivity || sensitivity;
      dispatchDataReplaced(gd._ristPeakSensitivityValue);
      await applyResponsiveLayout();
      window.Plotly.Plots.resize(gd);
      var libraryCount = selectedLibraryIds.length;
      status.textContent = payload.samples.map(function(item) {
        return item.label + " 피크 " + item.peakCount + "개";
      }).join(" · ") + " · 라이브러리 " + libraryCount + "개";
      renderFiles();
      scheduleWorkspaceSave();
    } catch (err) {
      setMessage(err.message || String(err));
    } finally {
      setBusy(false);
    }
  }

  function currentFigurePayload() {
    return {
      data: JSON.parse(JSON.stringify(gd.data || [])),
      layout: JSON.parse(JSON.stringify(gd.layout || {}))
    };
  }

  async function createReport() {
    if (!files.length) {
      setMessage("보고서를 생성하려면 Raman raw 파일을 먼저 업로드하세요.");
      return;
    }
    if (!latestAnalysisPayload) {
      await analyze();
      if (!latestAnalysisPayload) return;
    }
    loading.textContent = "보고서 생성 중...";
    setBusy(true);
    setMessage("");
    setReportProgress({
      status: "running",
      stage: "capture",
      progressPct: 5,
      message: "현재 그래프 화면을 캡처하는 중입니다."
    });
    status.textContent = "보고서 생성 중";
    try {
      var figureImage = await window.Plotly.toImage(gd, {
        format: "png",
        width: Math.max(900, Math.round(gd.clientWidth || 1200)),
        height: Math.max(640, Math.round(gd.clientHeight || 800)),
        scale: 2
      });
      var form = new FormData();
      files.forEach(function(file) { form.append("files", file, file.name); });
      form.append("analysis_json", JSON.stringify(reportAnalysisPayload()));
      form.append("figure_json", JSON.stringify(currentFigurePayload()));
      form.append("figure_image", figureImage);
      var job = await fetchJson("/api/v1/raman/report/jobs", {
        method: "POST",
        body: form
      });
      setReportProgress(job);
      job = await pollReportJob(job.jobId);
      setReportProgress(job);
      setReportDownloadLink(job);
      status.textContent = "보고서 생성 완료";
    } catch (err) {
      setMessage(err.message || "보고서 생성에 실패했습니다.");
      status.textContent = "보고서 생성 실패";
    } finally {
      setBusy(false);
      loading.textContent = "Raman 전처리 중...";
    }
  }

  function addFiles(list) {
    var next = files.slice();
    var seen = {};
    next.forEach(function(file) { seen[fileKey(file)] = true; });
    var total = next.reduce(function(sum, file) { return sum + file.size; }, 0);
    Array.prototype.slice.call(list || []).forEach(function(file) {
      var suffix = (file.name.split(".").pop() || "").toLowerCase();
      if (["txt", "csv", "tsv", "xlsx", "xlsm"].indexOf(suffix) < 0) {
        setMessage("지원하지 않는 파일입니다: " + file.name);
        return;
      }
      if (file.size > MAX_FILE_BYTES) {
        setMessage("파일은 20MB 이하여야 합니다: " + file.name);
        return;
      }
      if (seen[fileKey(file)]) return;
      if (next.length >= MAX_FILES) {
        setMessage("한 번에 최대 " + MAX_FILES + "개 파일까지 분석할 수 있습니다.");
        return;
      }
      if (total + file.size > MAX_TOTAL_BYTES) {
        setMessage("파일 총 크기는 50MB 이하여야 합니다.");
        return;
      }
      next.push(file);
      seen[fileKey(file)] = true;
      total += file.size;
    });
    files = next;
    renderFiles();
    if (files.length) analyze();
    else scheduleWorkspaceSave();
  }

  input.addEventListener("change", function() {
    addFiles(input.files);
    input.value = "";
  });
  libraryInput.addEventListener("change", function() {
    uploadLibrary(libraryInput.files && libraryInput.files[0]);
    libraryInput.value = "";
  });
  libraryFilter.addEventListener("input", renderLibraries);
  reportMetaControls.forEach(function(control) {
    control.addEventListener("input", scheduleWorkspaceSave);
    control.addEventListener("change", scheduleWorkspaceSave);
  });
  libraryNew.addEventListener("click", function() {
    renderLibraryEditor(
      {
        id: "",
        name: "",
        description: "",
        fileName: "new.json",
        assignmentCount: 0,
        assignments: []
      },
      true
    );
  });
  libraryRowAdd.addEventListener("click", function() {
    addAssignmentRow();
  });
  libraryDialogCancel.addEventListener("click", closeLibraryEditor);
  libraryDialogClose.addEventListener("click", closeLibraryEditor);
  libraryDialogSave.addEventListener("click", saveLibraryEditor);
  libraryModal.addEventListener("click", function(ev) {
    if (ev.target === libraryModal) closeLibraryEditor();
  });
  reportOptionsOpen.addEventListener("click", openReportOptionsEditor);
  reportOptionsSave.addEventListener("click", saveReportOptionsEditor);
  reportOptionsReset.addEventListener("click", resetReportOptionsEditor);
  reportOptionsCancel.addEventListener("click", closeReportOptionsEditor);
  reportOptionsClose.addEventListener("click", closeReportOptionsEditor);
  reportOptionsModal.addEventListener("click", function(ev) {
    if (ev.target === reportOptionsModal) closeReportOptionsEditor();
  });
  document.addEventListener("click", function() {
    closeReportOptionPicker();
  });
  document.addEventListener("keydown", function(ev) {
    if (ev.key !== "Escape") return;
    if (activeReportPickerControl) {
      closeReportOptionPicker();
      return;
    }
    if (reportOptionsModal.classList.contains("is-visible")) {
      closeReportOptionsEditor();
      return;
    }
    if (libraryModal.classList.contains("is-visible")) {
      closeLibraryEditor();
    }
  });
  clearButton.addEventListener("click", function() {
    latestAnalysisPayload = null;
    setReportProgress(null);
    setMessage("");
    clearReportMetadataForm();
    clearWorkspaceState();
    resetPlot();
  });
  reportButton.addEventListener("click", createReport);
  gd.addEventListener("rist-raman-tools-toggle", function() {
    applyResponsiveLayout();
  });
  window.addEventListener("resize", function() {
    if (activeReportPickerControl) closeReportOptionPicker();
    applyResponsiveLayout();
  });
  window.addEventListener("scroll", closeReportOptionPicker, true);
  window.addEventListener("dragenter", function(ev) {
    if (ev.dataTransfer && ev.dataTransfer.types.indexOf("Files") >= 0) {
      dropZone.classList.add("is-dragging");
    }
  });
  window.addEventListener("dragover", function(ev) {
    ev.preventDefault();
  });
  window.addEventListener("dragleave", function(ev) {
    if (!ev.relatedTarget) dropZone.classList.remove("is-dragging");
  });
  window.addEventListener("drop", function(ev) {
    ev.preventDefault();
    dropZone.classList.remove("is-dragging");
    addFiles(ev.dataTransfer ? ev.dataTransfer.files : []);
  });
  renderReportDatalists();
  installReportOptionPickers();
  installWorkspaceAutosave();
  restoreWorkspace().then(function(restored) {
    return loadLibraries(restored && restored.selectedLibraryIds).then(function() {
      if (restored) return applyResponsiveLayout();
      renderFiles();
      return applyResponsiveLayout();
    });
  });
})();
</script>
"""


def build_raman_page() -> str:
    page = fig_to_responsive_html(
        _blank_figure(),
        div_id=PLOT_DIV_ID,
        include_plotlyjs="/raman/assets/plotly.min.js",
        responsive_legend=False,
        crosshair=True,
        legend_text_edit=True,
        peak_editor=True,
        shape_editor=True,
        title_edit=True,
        trace_highlight=True,
        image_format_selector=True,
        image_filename="raman_peak_analysis",
        post_body_html=(
            peak_sensitivity_js(PLOT_DIV_ID, initial="25")
            + _RAMAN_TOOL_PANEL_SCRIPT
            + _RAMAN_STACK_SCRIPT
            + _RAMAN_RATIO_SCRIPT
            + _UPLOAD_SCRIPT
        ),
        config={"scrollZoom": True},
    )
    page = page.replace("</head>", _PAGE_STYLE + "</head>", 1)
    return page.replace("<body>", "<body>" + _PAGE_SHELL, 1)


@router.get("/raman", response_class=HTMLResponse, include_in_schema=False)
def raman_workspace() -> HTMLResponse:
    return HTMLResponse(build_raman_page())


@router.get("/raman/assets/plotly.min.js", include_in_schema=False)
def raman_plotly_asset() -> FileResponse:
    path = plotly_asset_path()
    if not path.is_file():
        raise ApiException(500, "PLOTLY_ASSET_NOT_FOUND", "Plotly 웹 자산을 찾을 수 없습니다.")
    return FileResponse(
        path,
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/api/v1/raman/assignment-libraries", tags=["raman"])
def list_assignment_libraries() -> dict:
    store = assignment_library_store()
    return {
        "libraries": store.summaries(),
        "directory": str(store.root),
        "supportedFormats": ["json", "csv"],
        "deleteEnabled": assignment_library_delete_enabled(),
    }


@router.post("/api/v1/raman/assignment-libraries/suggest", tags=["raman"])
def suggest_assignment_library(
    request: Request,
    payload: RamanAssignmentLibrarySuggest,
) -> dict:
    try:
        return assignment_suggestions.suggest_assignment_library(
            llm_settings(request),
            AssignmentSuggestionRequest(
                experiment_code="RAMAN",
                material=payload.material,
                library_id=payload.libraryId,
                library_name=payload.libraryName,
            ),
        )
    except AssignmentLibraryError as exc:
        raise_assignment_library_api(exc)


@router.get("/api/v1/raman/assignment-libraries/{library_id}", tags=["raman"])
def get_assignment_library(library_id: str) -> dict:
    try:
        library = assignment_library_store().get(library_id)
    except AssignmentLibraryError as exc:
        raise_assignment_library_api(exc)
    return {"library": library.detail()}


@router.post(
    "/api/v1/raman/assignment-libraries",
    tags=["raman"],
    status_code=201,
)
def upload_assignment_library(file: UploadFile = File(...)) -> dict:
    raw_filename = (file.filename or "").replace("\\", "/")
    filename = Path(raw_filename).name
    content = file.file.read(MAX_LIBRARY_BYTES + 1)
    try:
        library = assignment_library_store().save(filename, content)
    except AssignmentLibraryError as exc:
        raise_assignment_library_api(exc)
    logger.info(
        "Raman assignment 라이브러리 업로드 (id=%s, assignments=%d)",
        library.library_id,
        len(library.assignments),
    )
    return {"library": library.summary(
        default_library_id=DEFAULT_RAMAN_ASSIGNMENT_LIBRARY_ID,
    )}


@router.post(
    "/api/v1/raman/assignment-libraries/create",
    tags=["raman"],
    status_code=201,
)
def create_assignment_library(payload: RamanAssignmentLibraryCreate) -> dict:
    try:
        library = assignment_library_store().write(
            payload.id,
            payload.model_dump(exclude={"id"}, by_alias=True),
            create_only=True,
        )
    except AssignmentLibraryError as exc:
        raise_assignment_library_api(exc)
    return {"library": library.summary(
        default_library_id=DEFAULT_RAMAN_ASSIGNMENT_LIBRARY_ID,
    )}


@router.put("/api/v1/raman/assignment-libraries/{library_id}", tags=["raman"])
def update_assignment_library(
    library_id: str,
    payload: RamanAssignmentLibraryWrite,
) -> dict:
    try:
        library = assignment_library_store().write(
            library_id,
            payload.model_dump(by_alias=True),
            create_only=False,
        )
    except AssignmentLibraryError as exc:
        raise_assignment_library_api(exc)
    return {"library": library.summary(
        default_library_id=DEFAULT_RAMAN_ASSIGNMENT_LIBRARY_ID,
    )}


@router.delete("/api/v1/raman/assignment-libraries/{library_id}", tags=["raman"])
def delete_assignment_library(library_id: str) -> dict:
    if not assignment_library_delete_enabled():
        raise ApiException(
            403,
            "ASSIGNMENT_LIBRARY_DELETE_DISABLED",
            "라이브러리 삭제 기능이 비활성화되어 있습니다.",
        )
    try:
        assignment_library_store().delete(library_id)
    except AssignmentLibraryError as exc:
        raise_assignment_library_api(exc)
    return {"deleted": True, "id": library_id}


@router.post("/api/v1/raman/analyze", tags=["raman"])
def analyze_raman(
    files: list[UploadFile] = File(...),
    sensitivity: int = Form(default=25, ge=0, le=100),
    assignment_library_ids: list[str] | None = Form(default=None),
    assignment_library_selection_explicit: bool = Form(default=False),
) -> dict:
    uploaded = _uploaded_raman_files(files)

    store = assignment_library_store()
    if assignment_library_selection_explicit:
        selected_ids = assignment_library_ids or []
    elif assignment_library_ids is not None:
        selected_ids = assignment_library_ids
    else:
        selected_ids = store.default_ids()
    try:
        libraries = store.load(selected_ids)
        result = analyze_raman_files(
            uploaded,
            sensitivity=sensitivity,
            assignment_libraries=libraries,
        )
    except AssignmentLibraryError as exc:
        raise_assignment_library_api(exc)
    except RamanAnalysisError as exc:
        raise ApiException(400, exc.code, exc.message) from exc
    logger.info(
        "Raman 미리보기 분석 완료 (files=%d, sensitivity=%d)",
        len(uploaded),
        sensitivity,
    )
    return result


@router.post("/api/v1/raman/report", tags=["raman"])
def create_raman_preview_report(
    request: Request,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    analysis_json: str = Form(...),
    figure_json: str = Form(default=""),
    figure_image: str = Form(...),
) -> FileResponse:
    uploaded = _uploaded_raman_files(files)
    try:
        analysis_payload = parse_analysis_payload(analysis_json, figure_json)
        image_bytes = decode_figure_image(figure_image)
        raw_series = _build_raman_raw_series(uploaded)
        tmp_root, package = build_preview_report_package(
            experiment_code="RAMAN",
            analysis_payload=analysis_payload,
            raw_series=raw_series,
            figure_image=image_bytes,
            settings=getattr(request.app.state, "settings", None),
        )
    except ValueError as exc:
        raise ApiException(400, "RAMAN_REPORT_INVALID_PAYLOAD", str(exc)) from exc
    except Exception as exc:
        raise ApiException(422, "RAMAN_REPORT_FAILED", str(exc)) from exc

    background_tasks.add_task(cleanup_preview_report, tmp_root)
    return FileResponse(
        package,
        media_type="application/zip",
        filename="raman-report-package.zip",
    )


@router.post("/api/v1/raman/report/jobs", status_code=202, tags=["raman"])
def create_raman_preview_report_job(
    request: Request,
    files: list[UploadFile] = File(...),
    analysis_json: str = Form(...),
    figure_json: str = Form(default=""),
    figure_image: str = Form(...),
) -> dict:
    uploaded = _uploaded_raman_files(files)
    try:
        analysis_payload = parse_analysis_payload(analysis_json, figure_json)
        image_bytes = decode_figure_image(figure_image)
    except ValueError as exc:
        raise ApiException(400, "RAMAN_REPORT_INVALID_PAYLOAD", str(exc)) from exc

    store = preview_report_job_store(request.app)
    job = store.create(filename="raman-report-package.zip")

    def raw_series_factory() -> list[RawSeries]:
        return _build_raman_raw_series(uploaded)

    start_preview_report_job(
        store,
        job.job_id,
        experiment_code="RAMAN",
        analysis_payload=analysis_payload,
        raw_series_factory=raw_series_factory,
        figure_image=image_bytes,
        settings=getattr(request.app.state, "settings", None),
    )
    return _report_job_response(job, prefix="/api/v1/raman/report/jobs")


@router.get("/api/v1/raman/report/jobs/{job_id}", tags=["raman"])
def get_raman_preview_report_job(request: Request, job_id: str) -> dict:
    store = preview_report_job_store(request.app)
    job = store.get(job_id)
    if job is None:
        raise ApiException(404, "RAMAN_REPORT_JOB_NOT_FOUND", "보고서 작업을 찾을 수 없습니다.")
    return _report_job_response(job, prefix="/api/v1/raman/report/jobs")


@router.get("/api/v1/raman/report/jobs/{job_id}/download", tags=["raman"])
def download_raman_preview_report_job(
    request: Request,
    background_tasks: BackgroundTasks,
    job_id: str,
) -> FileResponse:
    store = preview_report_job_store(request.app)
    job = store.get(job_id)
    if job is None:
        raise ApiException(404, "RAMAN_REPORT_JOB_NOT_FOUND", "보고서 작업을 찾을 수 없습니다.")
    if job.status != "completed" or job.package_path is None:
        raise ApiException(409, "RAMAN_REPORT_JOB_NOT_READY", "보고서가 아직 완성되지 않았습니다.")
    if not job.package_path.is_file():
        store.remove(job_id)
        raise ApiException(410, "RAMAN_REPORT_PACKAGE_EXPIRED", "보고서 파일이 만료되었습니다.")
    background_tasks.add_task(store.remove, job_id)
    return FileResponse(
        job.package_path,
        media_type="application/zip",
        filename=job.filename,
    )


def create_raman_preview_app() -> FastAPI:
    app = FastAPI(title="RIST Raman Preview")
    app.add_exception_handler(ApiException, api_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.include_router(router)
    return app

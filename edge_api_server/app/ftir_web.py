"""FT-IR upload workspace HTML and local Plotly asset helpers."""

from __future__ import annotations

from io import BytesIO
from functools import lru_cache
import os
from pathlib import Path

import plotly
import plotly.graph_objects as go
from fastapi import APIRouter, BackgroundTasks, FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from ftir.assignment_libraries import (
    AssignmentLibraryError,
    AssignmentLibraryStore,
    MAX_LIBRARY_BYTES,
)
from ftir.findings import DEFAULT_FUNC_GROUPS_PATH
from ftir.preprocess import load_dpt
from ftir.plotting import ftir_abs_trans_toggle_js
from ftir.web_analysis import DptAnalysisError, WN_MAX, WN_MIN, analyze_dpt_files
from rist_common import get_logger
from rist_common.plotting import fig_to_responsive_html, peak_sensitivity_js

from .errors import ApiException, api_exception_handler, validation_exception_handler
from . import assignment_suggestions
from .assignment_suggestions import AssignmentSuggestionRequest
from .config import Settings
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


PLOT_DIV_ID = "peak-plot"
MAX_FTIR_PREVIEW_FILES = 10
MAX_FTIR_PREVIEW_FILE_BYTES = 20 * 1024 * 1024
MAX_FTIR_PREVIEW_TOTAL_BYTES = 50 * 1024 * 1024
DEFAULT_ASSIGNMENT_LIBRARY_DIR = (
    Path(__file__).resolve().parents[1] / "data" / "ftir_assignment_libraries"
)
logger = get_logger(__name__)
router = APIRouter()


class PeakAssignmentWrite(BaseModel):
    centerWavenumber: float
    tolerance: float
    name: str
    color: str = "#64748b"
    note: str = ""


class AssignmentLibraryWrite(BaseModel):
    name: str
    description: str = ""
    assignments: list[PeakAssignmentWrite]


class AssignmentLibraryCreate(AssignmentLibraryWrite):
    id: str


class AssignmentLibrarySuggest(BaseModel):
    material: str
    libraryId: str | None = None
    libraryName: str | None = None


def llm_settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or Settings.from_env()


def assignment_library_store(request: Request) -> AssignmentLibraryStore:
    configured = getattr(
        request.app.state,
        "ftir_assignment_library_dir",
        os.getenv(
            "RIST_FTIR_ASSIGNMENT_LIBRARY_DIR",
            str(DEFAULT_ASSIGNMENT_LIBRARY_DIR),
        ),
    )
    return AssignmentLibraryStore(Path(configured), DEFAULT_FUNC_GROUPS_PATH)


def assignment_library_delete_enabled(request: Request) -> bool:
    configured = getattr(
        request.app.state,
        "ftir_assignment_library_delete_enabled",
        os.getenv(
            "RIST_FTIR_ASSIGNMENT_LIBRARY_DELETE_ENABLED",
            "false",
        ).lower()
        in {"1", "true", "yes", "on"},
    )
    return bool(configured)


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


def _uploaded_dpt_files(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    if not files:
        raise ApiException(400, "DPT_FILES_REQUIRED", "DPT 파일이 필요합니다.")
    if len(files) > MAX_FTIR_PREVIEW_FILES:
        raise ApiException(
            400,
            "TOO_MANY_DPT_FILES",
            f"한 번에 최대 {MAX_FTIR_PREVIEW_FILES}개 파일을 분석할 수 있습니다.",
        )

    uploaded: list[tuple[str, bytes]] = []
    total_bytes = 0
    for upload in files:
        raw_filename = (upload.filename or "").replace("\\", "/")
        filename = Path(raw_filename).name
        if not filename or Path(filename).suffix.lower() != ".dpt":
            raise ApiException(
                400,
                "INVALID_DPT_EXTENSION",
                f"DPT 파일만 업로드할 수 있습니다: {filename or '(이름 없음)'}",
            )
        content = upload.file.read(MAX_FTIR_PREVIEW_FILE_BYTES + 1)
        if not content:
            raise ApiException(
                400,
                "EMPTY_DPT_FILE",
                f"빈 파일은 분석할 수 없습니다: {filename}",
            )
        if len(content) > MAX_FTIR_PREVIEW_FILE_BYTES:
            raise ApiException(
                413,
                "DPT_FILE_TOO_LARGE",
                f"DPT 파일은 20MB 이하여야 합니다: {filename}",
            )
        total_bytes += len(content)
        if total_bytes > MAX_FTIR_PREVIEW_TOTAL_BYTES:
            raise ApiException(
                413,
                "DPT_UPLOAD_TOO_LARGE",
                "한 번에 업로드하는 DPT 파일의 총 크기는 50MB 이하여야 합니다.",
            )
        uploaded.append((filename, content))
    return uploaded


def _build_ftir_raw_series(uploaded: list[tuple[str, bytes]]) -> list[RawSeries]:
    raw_series = []
    for filename, content in uploaded:
        frame = load_dpt(BytesIO(content), WN_MIN, WN_MAX)
        raw_series.append(
            RawSeries(
                label=Path(filename).stem,
                axis=[float(value) for value in frame["wn"].to_list()],
                values=[float(value) for value in frame["y"].to_list()],
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
            text="FT-IR Peak Analysis",
            font=dict(size=18),
            x=0.01,
            y=0.98,
            yanchor="top",
        ),
        xaxis=dict(
            title="Wavenumber (cm⁻¹)",
            range=[4000, 400],
            showgrid=True,
            gridcolor="#e8e8e8",
            tickmode="linear",
            dtick=500,
            minor=dict(showgrid=True, gridcolor="#f4f4f4"),
        ),
        yaxis=dict(
            title="Normalized Absorbance",
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
body {
  overflow-x: hidden;
}
.ftir-app-bar {
  display: flex;
  align-items: center;
  min-height: 54px;
  padding: 0 22px;
  border-bottom: 1px solid #d9e2ec;
  background: #ffffff;
  box-sizing: border-box;
}
.ftir-brand {
  display: flex;
  align-items: baseline;
  gap: 9px;
  min-width: 0;
}
.ftir-brand strong {
  color: #102a43;
  font-size: 18px;
  letter-spacing: 0;
}
.ftir-brand span {
  color: #52606d;
  font-size: 12px;
}
.ftir-app-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}
.ftir-status {
  max-width: 360px;
  overflow: hidden;
  color: #52606d;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ftir-file-button,
.ftir-clear-button {
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
.ftir-file-button:hover,
.ftir-clear-button:hover {
  border-color: #486581;
  background: #e8eef5;
}
.ftir-clear-button[hidden] {
  display: none;
}
.ftir-file-input {
  display: none;
}
.ftir-library-band {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  min-height: 68px;
  padding: 7px 22px;
  border-bottom: 1px solid #d9e2ec;
  background: #f8fafc;
  box-sizing: border-box;
}
.ftir-library-title {
  flex: 0 0 auto;
  margin-top: 9px;
  color: #334e68;
  font-size: 11px;
  font-weight: 700;
}
.ftir-library-filter {
  flex: 0 0 150px;
  width: 150px;
  height: 30px;
  margin-top: 0;
  border: 1px solid #bcccdc;
  border-radius: 4px;
  background: #ffffff;
  color: #243b53;
  font: 11px Arial, "Noto Sans KR", sans-serif;
  padding: 0 9px;
  box-sizing: border-box;
}
.ftir-library-list {
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
  padding: 0 2px 1px 0;
}
.ftir-library-item {
  display: inline-flex;
  align-items: center;
  flex: 0 0 auto;
  max-width: 300px;
  height: 28px;
  border: 1px solid #bcccdc;
  border-radius: 4px;
  background: #ffffff;
  color: #334e68;
  font-size: 11px;
  box-sizing: border-box;
}
.ftir-library-item.is-selected {
  border-color: #3e7ca6;
  background: #edf6fb;
  color: #174b6d;
}
.ftir-library-item.is-invalid {
  border-color: #f5b7b1;
  background: #fff5f5;
  color: #9b2c2c;
}
.ftir-library-toggle {
  display: inline-flex;
  align-items: center;
  height: 100%;
  padding-left: 8px;
}
.ftir-library-toggle input {
  margin: 0;
}
.ftir-library-name {
  min-width: 0;
  max-width: 210px;
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
.ftir-library-name:hover {
  text-decoration: underline;
}
.ftir-library-count {
  color: #7b8794;
  font-size: 10px;
  padding-right: 5px;
}
.ftir-library-state {
  border-left: 1px solid #d9e2ec;
  color: #7b8794;
  font-size: 9px;
  padding: 0 6px;
  white-space: nowrap;
}
.ftir-library-item.is-selected .ftir-library-state {
  color: #17633a;
  font-weight: 700;
}
.ftir-library-empty {
  color: #7b8794;
  font-size: 11px;
  white-space: nowrap;
}
.ftir-library-upload {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  height: 30px;
  margin-top: 0;
  border: 1px solid #9fb3c8;
  border-radius: 4px;
  background: #ffffff;
  color: #243b53;
  cursor: pointer;
  font-size: 11px;
  padding: 0 10px;
  box-sizing: border-box;
}
.ftir-library-new {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  height: 30px;
  margin-top: 0;
  border: 1px solid #3e7ca6;
  border-radius: 4px;
  background: #edf6fb;
  color: #174b6d;
  cursor: pointer;
  font-size: 11px;
  padding: 0 10px;
  box-sizing: border-box;
}
.ftir-library-new:hover {
  background: #dceef8;
}
.ftir-library-upload:hover {
  border-color: #486581;
  background: #eef2f6;
}
.ftir-library-modal {
  position: fixed;
  inset: 0;
  z-index: 90;
  display: none;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: rgba(15, 23, 42, 0.34);
  box-sizing: border-box;
}
.ftir-library-modal.is-visible {
  display: flex;
}
.ftir-library-dialog {
  display: flex;
  flex-direction: column;
  width: min(880px, 100%);
  max-height: min(78vh, 720px);
  border: 1px solid #9fb3c8;
  border-radius: 6px;
  background: #ffffff;
  box-shadow: 0 18px 45px rgba(15, 23, 42, 0.22);
  overflow: hidden;
}
.ftir-library-dialog-header {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 0 0 auto;
  min-height: 48px;
  padding: 0 14px;
  border-bottom: 1px solid #d9e2ec;
  background: #f8fafc;
  box-sizing: border-box;
}
.ftir-library-dialog-heading {
  min-width: 0;
}
.ftir-library-dialog-heading strong {
  display: block;
  overflow: hidden;
  color: #102a43;
  font-size: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ftir-library-dialog-heading span {
  display: block;
  overflow: hidden;
  color: #627d98;
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ftir-library-dialog-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  margin-left: auto;
  border: 0;
  background: transparent;
  color: #52606d;
  cursor: pointer;
  font: 20px/1 Arial, sans-serif;
}
.ftir-library-dialog-body {
  flex: 1 1 auto;
  overflow: auto;
  padding: 12px 14px 0;
}
.ftir-library-form-meta {
  display: grid;
  grid-template-columns: minmax(150px, 0.7fr) minmax(220px, 1fr);
  gap: 10px 12px;
  margin-bottom: 12px;
}
.ftir-library-suggest {
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
.ftir-library-suggest input {
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
.ftir-library-suggest button {
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
.ftir-library-suggest button:disabled {
  cursor: progress;
  opacity: 0.65;
}
.ftir-library-suggest span {
  flex: 0 1 auto;
  color: #7b8794;
  font-size: 10px;
  white-space: nowrap;
}
.ftir-library-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  color: #52606d;
  font-size: 10px;
}
.ftir-library-field.is-wide {
  grid-column: 1 / -1;
}
.ftir-library-field input,
.ftir-library-field textarea,
.ftir-library-table input {
  width: 100%;
  border: 1px solid #bcccdc;
  border-radius: 3px;
  background: #ffffff;
  color: #243b53;
  font: 11px Arial, "Noto Sans KR", sans-serif;
  padding: 6px 7px;
  box-sizing: border-box;
}
.ftir-library-field input:disabled {
  background: #eef2f6;
  color: #627d98;
}
.ftir-library-field textarea {
  min-height: 52px;
  resize: vertical;
}
.ftir-library-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  color: #243b53;
  font-size: 11px;
}
.ftir-library-table th {
  position: sticky;
  top: 0;
  z-index: 1;
  padding: 8px;
  border-bottom: 1px solid #bcccdc;
  background: #eef2f6;
  color: #334e68;
  text-align: left;
}
.ftir-library-table td {
  padding: 7px 8px;
  border-bottom: 1px solid #e4e7eb;
  vertical-align: top;
  overflow-wrap: anywhere;
}
.ftir-library-table input[type="number"] {
  text-align: right;
}
.ftir-library-table input[type="color"] {
  width: 34px;
  min-width: 34px;
  height: 28px;
  padding: 2px;
}
.ftir-library-table .numeric {
  width: 90px;
  text-align: right;
}
.ftir-library-table .color {
  width: 48px;
}
.ftir-library-table .remove {
  width: 42px;
  text-align: center;
}
.ftir-library-row-remove {
  width: 26px;
  height: 26px;
  border: 0;
  background: transparent;
  color: #7b8794;
  cursor: pointer;
  font: 17px/1 Arial, sans-serif;
}
.ftir-library-row-remove:hover {
  color: #b42318;
}
.ftir-library-swatch {
  display: inline-block;
  width: 16px;
  height: 16px;
  margin-right: 5px;
  border: 1px solid rgba(0,0,0,0.18);
  border-radius: 3px;
  vertical-align: middle;
}
.ftir-library-dialog-loading {
  padding: 28px 16px;
  color: #627d98;
  font-size: 12px;
  text-align: center;
}
.ftir-library-dialog-footer {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
  min-height: 50px;
  padding: 8px 14px;
  border-top: 1px solid #d9e2ec;
  background: #f8fafc;
  box-sizing: border-box;
}
.ftir-library-dialog-footer-actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}
.ftir-library-dialog-button {
  height: 30px;
  border: 1px solid #9fb3c8;
  border-radius: 4px;
  background: #ffffff;
  color: #243b53;
  cursor: pointer;
  font-size: 11px;
  padding: 0 11px;
}
.ftir-library-dialog-button.primary {
  border-color: #2f6f9f;
  background: #2f6f9f;
  color: #ffffff;
}
.ftir-library-dialog-button.danger {
  border-color: #ba2525;
  background: #fff5f5;
  color: #9b1c1c;
}
.ftir-library-dialog-button.danger:hover {
  background: #ffe3e3;
}
.ftir-library-dialog-button:disabled {
  cursor: default;
  opacity: 0.55;
}
.ftir-drop-band {
  display: flex;
  align-items: center;
  min-height: 48px;
  padding: 7px 22px;
  border-bottom: 1px solid #d9e2ec;
  background: #ffffff;
  box-sizing: border-box;
  transition: background-color 120ms ease, border-color 120ms ease;
}
.ftir-drop-band.is-dragging {
  border-color: #2f855a;
  background: #f0fff4;
}
.ftir-drop-prompt {
  color: #627d98;
  font-size: 11px;
  white-space: nowrap;
}
.ftir-file-list {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  overflow-x: auto;
}
.ftir-file-item {
  display: inline-flex;
  align-items: center;
  flex: 0 0 auto;
  height: 28px;
  border: 1px solid #bcccdc;
  border-radius: 4px;
  background: #f5f7fa;
  color: #334e68;
  font-size: 11px;
  padding-left: 8px;
}
.ftir-file-remove {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 27px;
  height: 26px;
  border: 0;
  background: transparent;
  color: #7b8794;
  cursor: pointer;
  font: 16px/1 Arial, sans-serif;
  padding: 0;
}
.ftir-file-remove:hover {
  color: #b42318;
}
.ftir-report-meta-band {
  border-bottom: 1px solid #d9e2ec;
  background: #f8fafc;
}
.ftir-report-meta-panel {
  padding: 0 22px;
}
.ftir-report-meta-panel > summary {
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
.ftir-report-meta-panel > summary::-webkit-details-marker {
  display: none;
}
.ftir-report-meta-panel > summary::before {
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
.ftir-report-meta-panel[open] > summary::before {
  content: "-";
}
.ftir-report-meta-panel > summary span {
  color: #7b8794;
  font-size: 10px;
  font-weight: 400;
}
.ftir-report-meta-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(130px, 1fr));
  gap: 8px 10px;
  padding: 0 0 12px 24px;
}
.ftir-report-meta-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  color: #52606d;
  font-size: 10px;
}
.ftir-report-meta-field.is-wide {
  grid-column: span 2;
}
.ftir-report-meta-field input,
.ftir-report-meta-field select,
.ftir-report-meta-field textarea {
  width: 100%;
  border: 1px solid #bcccdc;
  border-radius: 4px;
  background: #ffffff;
  color: #243b53;
  font: 11px Arial, "Noto Sans KR", sans-serif;
  padding: 6px 7px;
  box-sizing: border-box;
}
.ftir-report-meta-field textarea {
  min-height: 34px;
  resize: vertical;
}
.ftir-message {
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
.ftir-message.is-visible {
  display: block;
}
.ftir-message.is-success {
  border-bottom-color: #bfdbfe;
  background: #eff6ff;
  color: #1e3a8a;
}
.ftir-message a {
  color: #1d4ed8;
  font-weight: 700;
  text-decoration: underline;
}
.ftir-message-close {
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
.ftir-message-close:hover {
  background: rgba(30, 58, 138, 0.1);
}
.ftir-loading {
  position: fixed;
  inset: 170px 0 0;
  z-index: 40;
  display: none;
  align-items: center;
  justify-content: center;
  background: rgba(248,250,252,0.7);
  color: #243b53;
  font-size: 12px;
}
.ftir-loading.is-visible {
  display: flex;
}
.ftir-report-progress {
  display: none;
  padding: 8px 22px 10px;
  border-bottom: 1px solid #d9e2ec;
  background: #f8fafc;
  color: #243b53;
  font-size: 12px;
}
.ftir-report-progress.is-visible {
  display: block;
}
.ftir-report-progress-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 6px;
}
.ftir-report-progress-track {
  overflow: hidden;
  height: 6px;
  border-radius: 999px;
  background: #d9e2ec;
}
.ftir-report-progress-bar {
  width: 0%;
  height: 100%;
  border-radius: inherit;
  background: #2f80ed;
  transition: width 220ms ease;
}
#peak-plot {
  --rist-ftir-tool-panel-alpha: 0.97;
  min-height: 540px;
  height: calc(100vh - 170px) !important;
}
#peak-plot .rist-ftir-tools-toggle,
#peak-plot .rist-ftir-tools-head {
  display: none;
}
@media (max-width: 760px) {
  .ftir-app-bar {
    align-items: flex-start;
    flex-wrap: wrap;
    gap: 7px;
    padding: 9px 12px;
  }
  .ftir-app-actions {
    width: 100%;
    margin-left: 0;
    justify-content: flex-end;
    flex-wrap: wrap;
  }
  .ftir-status {
    flex: 1 1 100%;
    max-width: 100%;
    text-align: right;
  }
  .ftir-drop-band {
    padding: 7px 12px;
  }
  .ftir-report-meta-panel {
    padding: 0 12px;
  }
  .ftir-report-meta-grid {
    grid-template-columns: 1fr;
    padding-left: 0;
  }
  .ftir-report-meta-field.is-wide {
    grid-column: auto;
  }
  .ftir-library-band {
    gap: 7px;
    padding: 7px 12px;
  }
  .ftir-library-filter {
    flex: 1 1 120px;
    width: auto;
  }
  .ftir-library-list {
    order: 4;
    flex-basis: 100%;
    max-height: 86px;
  }
  .ftir-library-item {
    max-width: 100%;
  }
  .ftir-library-name {
    max-width: 190px;
  }
  .ftir-library-title {
    display: none;
  }
  .ftir-library-upload {
    padding: 0 8px;
  }
  .ftir-library-new {
    padding: 0 8px;
  }
  .ftir-library-state {
    display: none;
  }
  .ftir-library-modal {
    padding: 8px;
    align-items: flex-start;
  }
  .ftir-library-dialog {
    max-height: calc(100vh - 16px);
  }
  .ftir-library-table .color {
    display: none;
  }
  .ftir-library-form-meta {
    grid-template-columns: 1fr;
  }
  .ftir-library-suggest {
    align-items: center;
    flex-wrap: wrap;
  }
  .ftir-library-suggest span {
    white-space: normal;
  }
  .ftir-library-field.is-wide {
    grid-column: auto;
  }
  #peak-plot {
    min-height: 900px;
    height: calc(100vh - 180px + 360px) !important;
  }
  #peak-plot .rist-plot-control-row {
    left: 8px !important;
    right: 8px !important;
    width: auto !important;
    flex-wrap: wrap;
    justify-content: flex-end;
  }
  .ftir-loading {
    inset: 180px 0 0;
  }
}
@media (max-width: 1440px) {
  #peak-plot .rist-ftir-tools-toggle {
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
  #peak-plot.rist-ftir-tools-open .rist-ftir-tools-toggle {
    border-color: #2563eb;
    background: #dbeafe;
    color: #1d4ed8;
  }
  #peak-plot .rist-plot-control-row {
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
    opacity: var(--rist-ftir-tool-panel-alpha);
    box-shadow: 0 4px 18px rgba(15,23,42,0.16);
    box-sizing: border-box;
    scrollbar-width: thin;
  }
  #peak-plot .rist-ftir-tools-head {
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
  #peak-plot .rist-ftir-tools-head span:first-child {
    flex: 1 1 auto;
    min-width: 0;
  }
  #peak-plot .rist-ftir-tools-opacity {
    flex: 0 0 76px;
    width: 76px;
    accent-color: #52606d;
    cursor: pointer;
  }
  #peak-plot .rist-ftir-tools-close {
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
  #peak-plot.rist-ftir-tools-open .rist-plot-control-row {
    display: flex !important;
  }
  #peak-plot .rist-plot-control-row > * {
    flex: 0 0 auto;
  }
  #peak-plot .rist-legend-edit-button,
  #peak-plot .rist-peak-edit-button {
    min-width: 0;
    height: 28px;
    white-space: nowrap;
    font-size: 11px;
    padding: 0 8px;
  }
  #peak-plot .rist-peak-sensitivity-control {
    height: 28px;
    gap: 5px;
    padding: 0 6px;
  }
  #peak-plot .rist-peak-sensitivity-slider {
    width: 54px;
  }
  #peak-plot .rist-peak-sensitivity-number {
    width: 38px;
  }
  #peak-plot .rist-peak-sensitivity-value {
    min-width: 24px;
  }
  #peak-plot .rist-peak-group-name {
    width: 96px;
    flex: 0 0 96px;
  }
  #peak-plot .rist-peak-group-color,
  #peak-plot .rist-shape-tool-button {
    flex: 0 0 auto;
    width: 28px;
    height: 28px;
  }
}
@media (max-width: 420px) {
  #peak-plot .rist-ftir-tools-toggle {
    top: 42px;
    right: 8px;
    height: 28px;
    padding: 0 8px;
  }
  #peak-plot .rist-plot-control-row {
    right: 8px !important;
    top: 76px !important;
    width: calc(100% - 16px) !important;
    max-width: calc(100% - 16px);
    gap: 5px;
  }
  #peak-plot .rist-legend-edit-button,
  #peak-plot .rist-peak-edit-button {
    font-size: 10px;
    padding: 0 6px;
  }
  #peak-plot .rist-peak-sensitivity-slider {
    width: 48px;
  }
}
</style>
"""


_PAGE_SHELL = """
<header class="ftir-app-bar">
  <div class="ftir-brand">
    <strong>FT-IR</strong>
    <span>스펙트럼 분석</span>
  </div>
  <div class="ftir-app-actions">
    <span class="ftir-status" id="ftir-status">대기</span>
    <button type="button" class="ftir-clear-button" id="ftir-report">보고서 생성</button>
    <button type="button" class="ftir-clear-button" id="ftir-clear">초기화</button>
    <label class="ftir-file-button">
      DPT 파일 선택
      <input id="ftir-file-input" class="ftir-file-input" type="file"
             accept=".dpt" multiple>
    </label>
  </div>
</header>
<section class="ftir-library-band" aria-label="피크 assignment 라이브러리">
  <span class="ftir-library-title">피크 라이브러리</span>
  <input type="search" class="ftir-library-filter" id="ftir-library-filter"
         placeholder="라이브러리 검색" autocomplete="off">
  <div class="ftir-library-list" id="ftir-library-list">
    <span class="ftir-library-empty">라이브러리 불러오는 중...</span>
  </div>
  <button type="button" class="ftir-library-new"
          id="ftir-library-new">새 라이브러리</button>
  <label class="ftir-library-upload">
    파일 가져오기
    <input id="ftir-library-input" class="ftir-file-input" type="file"
           accept=".json,.csv">
  </label>
</section>
<section class="ftir-drop-band" id="ftir-drop-zone">
  <span class="ftir-drop-prompt" id="ftir-drop-prompt">
    DPT 파일을 선택하거나 여기에 놓으세요
  </span>
  <div class="ftir-file-list" id="ftir-file-list"></div>
</section>
<section class="ftir-report-meta-band" id="ftir-report-meta">
  <details class="ftir-report-meta-panel">
    <summary>보고서 정보 <span>raw 자동 추출 + 직접 입력</span></summary>
    <div class="ftir-report-meta-grid">
      <label class="ftir-report-meta-field">
        <span>장비모델</span>
        <input type="text" placeholder="예: Nicolet iS50"
               data-report-field="equipmentModel"
               data-report-label="장비모델">
      </label>
      <label class="ftir-report-meta-field">
        <span>Type</span>
        <input type="text" list="ftir-report-type-options"
               placeholder="선택 또는 입력"
               data-report-field="analysisType"
               data-report-label="Type">
      </label>
      <label class="ftir-report-meta-field">
        <span>Detector</span>
        <input type="text" list="ftir-report-detector-options"
               placeholder="선택 또는 입력"
               data-report-field="detector"
               data-report-label="Detector">
      </label>
      <label class="ftir-report-meta-field">
        <span>Crystal</span>
        <input type="text" list="ftir-report-crystal-options"
               placeholder="선택 또는 입력"
               data-report-field="crystal"
               data-report-label="Crystal">
      </label>
      <label class="ftir-report-meta-field">
        <span>Resolution</span>
        <input type="text" list="ftir-report-resolution-options"
               placeholder="예: 4 cm-1"
               data-report-field="resolution"
               data-report-label="Resolution">
      </label>
      <label class="ftir-report-meta-field">
        <span>Scan time</span>
        <input type="text" list="ftir-report-scan-options"
               placeholder="예: 64 scans"
               data-report-field="scanTime"
               data-report-label="Scan time">
      </label>
      <label class="ftir-report-meta-field">
        <span>Range</span>
        <input type="text" list="ftir-report-range-options"
               placeholder="예: 4000 ~ 400 cm-1"
               data-report-field="range"
               data-report-label="Range">
      </label>
    </div>
    <datalist id="ftir-report-type-options">
      <option value="ATR method">
      <option value="Transmission">
      <option value="Reflection">
    </datalist>
    <datalist id="ftir-report-detector-options">
      <option value="DTGS">
      <option value="MCT">
    </datalist>
    <datalist id="ftir-report-crystal-options">
      <option value="diamond">
      <option value="ZnSe">
      <option value="Ge">
    </datalist>
    <datalist id="ftir-report-resolution-options">
      <option value="4 cm-1">
      <option value="2 cm-1">
      <option value="8 cm-1">
    </datalist>
    <datalist id="ftir-report-scan-options">
      <option value="64 scans">
      <option value="32 scans">
      <option value="128 scans">
    </datalist>
    <datalist id="ftir-report-range-options">
      <option value="4000 ~ 400 cm-1">
      <option value="4000 ~ 650 cm-1">
    </datalist>
  </details>
</section>
<div class="ftir-message" id="ftir-message" role="alert"></div>
<div class="ftir-report-progress" id="ftir-report-progress" aria-live="polite">
  <div class="ftir-report-progress-row">
    <span id="ftir-report-progress-label">보고서 생성 대기</span>
    <span id="ftir-report-progress-value">0%</span>
  </div>
  <div class="ftir-report-progress-track">
    <div class="ftir-report-progress-bar" id="ftir-report-progress-bar"></div>
  </div>
</div>
<div class="ftir-loading" id="ftir-loading" aria-live="polite">전처리 및 피크 분석 중...</div>
<div class="ftir-library-modal" id="ftir-library-modal" role="dialog"
     aria-modal="true" aria-labelledby="ftir-library-dialog-title">
  <section class="ftir-library-dialog">
    <header class="ftir-library-dialog-header">
      <div class="ftir-library-dialog-heading">
        <strong id="ftir-library-dialog-title">피크 라이브러리</strong>
        <span id="ftir-library-dialog-meta"></span>
      </div>
      <button type="button" class="ftir-library-dialog-close"
              id="ftir-library-dialog-close" aria-label="닫기">×</button>
    </header>
    <div class="ftir-library-dialog-body" id="ftir-library-dialog-body"></div>
    <footer class="ftir-library-dialog-footer">
      <button type="button" class="ftir-library-dialog-button"
              id="ftir-library-row-add">항목 추가</button>
      <div class="ftir-library-dialog-footer-actions">
        <button type="button" class="ftir-library-dialog-button"
                id="ftir-library-dialog-cancel">취소</button>
        <button type="button" class="ftir-library-dialog-button primary"
                id="ftir-library-dialog-save">저장</button>
      </div>
    </footer>
  </section>
</div>
"""


_FTIR_TOOL_PANEL_SCRIPT = """
<script>
(function() {
  var gd = document.getElementById("peak-plot");
  if (!gd || gd._ristFtirToolPanelInstalled) return;
  gd._ristFtirToolPanelInstalled = true;
  if (getComputedStyle(gd).position === "static") gd.style.position = "relative";
  var button = document.createElement("button");
  button.type = "button";
  button.className = "rist-ftir-tools-toggle";
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
  if (!toolbar.querySelector(".rist-ftir-tools-head")) {
    var head = document.createElement("div");
    head.className = "rist-ftir-tools-head";
    head.innerHTML =
      "<span>그래프 도구</span>"
      + "<input class='rist-ftir-tools-opacity' type='range' min='55' max='100' value='97' title='도구창 투명도' aria-label='도구창 투명도'>"
      + "<button type='button' class='rist-ftir-tools-close' aria-label='도구창 닫기'>×</button>";
    toolbar.insertBefore(head, toolbar.firstChild);
  }
  var head = toolbar.querySelector(".rist-ftir-tools-head");
  var opacity = toolbar.querySelector(".rist-ftir-tools-opacity");
  var closeButton = toolbar.querySelector(".rist-ftir-tools-close");
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
    gd.classList.toggle("rist-ftir-tools-open", open);
    button.setAttribute("aria-expanded", open ? "true" : "false");
    button.textContent = open ? "닫기" : "도구";
    if (open) gd.dispatchEvent(new CustomEvent("rist-open-edit-tool"));
    gd.dispatchEvent(new CustomEvent("rist-ftir-tools-toggle", {
      detail: {open: open}
    }));
  }

  button.addEventListener("click", function(ev) {
    ev.preventDefault();
    ev.stopPropagation();
    setOpen(!gd.classList.contains("rist-ftir-tools-open"));
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
        "--rist-ftir-tool-panel-alpha",
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
      if (ev.target.closest(".rist-ftir-tools-opacity,.rist-ftir-tools-close")) return;
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
    if (!gd.classList.contains("rist-ftir-tools-open")) return;
    if (ev.target.closest("#peak-plot .rist-plot-control-row")) return;
    if (ev.target.closest("#peak-plot .rist-ftir-tools-toggle")) return;
    setOpen(false);
  });
  gd.addEventListener("rist-plot-data-replaced", function() {
    setOpen(false);
  });
})();
</script>
"""


_UPLOAD_SCRIPT = """
<script>
(function() {
  var gd = document.getElementById("peak-plot");
  var input = document.getElementById("ftir-file-input");
  var dropZone = document.getElementById("ftir-drop-zone");
  var prompt = document.getElementById("ftir-drop-prompt");
  var fileList = document.getElementById("ftir-file-list");
  var status = document.getElementById("ftir-status");
  var message = document.getElementById("ftir-message");
  var loading = document.getElementById("ftir-loading");
  var reportProgress = document.getElementById("ftir-report-progress");
  var reportProgressLabel = document.getElementById("ftir-report-progress-label");
  var reportProgressValue = document.getElementById("ftir-report-progress-value");
  var reportProgressBar = document.getElementById("ftir-report-progress-bar");
  var clearButton = document.getElementById("ftir-clear");
  var reportButton = document.getElementById("ftir-report");
  var libraryInput = document.getElementById("ftir-library-input");
  var libraryList = document.getElementById("ftir-library-list");
  var libraryFilter = document.getElementById("ftir-library-filter");
  var libraryNew = document.getElementById("ftir-library-new");
  var libraryModal = document.getElementById("ftir-library-modal");
  var libraryDialogTitle = document.getElementById("ftir-library-dialog-title");
  var libraryDialogMeta = document.getElementById("ftir-library-dialog-meta");
  var libraryDialogBody = document.getElementById("ftir-library-dialog-body");
  var libraryDialogClose = document.getElementById("ftir-library-dialog-close");
  var libraryRowAdd = document.getElementById("ftir-library-row-add");
  var libraryDialogCancel = document.getElementById("ftir-library-dialog-cancel");
  var libraryDialogSave = document.getElementById("ftir-library-dialog-save");
  var reportMetaControls = Array.prototype.slice.call(
    document.querySelectorAll("#ftir-report-meta [data-report-field]")
  );
  var MESSAGE_AUTO_HIDE_MS = 5000;
  var messageTimer = null;
  if (!gd || !input || !dropZone || !libraryInput || !libraryList
      || !libraryFilter
      || !libraryNew || !libraryModal || !libraryDialogClose
      || !libraryRowAdd || !libraryDialogCancel || !libraryDialogSave
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
  var controller = null;
  var emptyData = JSON.parse(JSON.stringify(gd.data || []));
  var emptyLayout = JSON.parse(JSON.stringify(gd.layout || {}));
  var MAX_FILES = 10;
  var MAX_FILE_BYTES = 20 * 1024 * 1024;
  var MAX_TOTAL_BYTES = 50 * 1024 * 1024;
  var SESSION_DB_NAME = "rist-ftir-workspace-v1";
  var SESSION_STORE = "workspace";
  var SESSION_KEY = "current";
  var workspaceDbPromise = null;
  var restoreInProgress = false;
  var saveTimer = 0;

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
      "rist-plot-data-replaced"
    ].forEach(function(name) {
      gd.addEventListener(name, scheduleWorkspaceSave);
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
      control.value = control.defaultValue || "";
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

  function setReportControlIfEmpty(field, value) {
    var control = reportMetaControls.find(function(item) {
      return item.dataset.reportField === field;
    });
    if (!control || !value) return;
    if (control.value && control.value !== control.defaultValue) return;
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

  function populateReportMetadataFromPayload(payload) {
    var items = sampleMetadataItems(payload || {});
    if (!items.length) return;
    setReportControlIfEmpty("equipmentModel", firstMetadataValue(items, [
      "equipment model",
      "instrument model",
      "instrument",
      "spectrometer",
      "model",
      "장비모델",
      "장비 모델"
    ]));
    setReportControlIfEmpty("analysisType", firstMetadataValue(items, [
      "type",
      "measurement type",
      "method",
      "technique",
      "measurement mode",
      "sampling mode",
      "sampling method",
      "accessory",
      "측정조건",
      "분석방법"
    ]));
    setReportControlIfEmpty("detector", firstMetadataValue(items, [
      "detector",
      "검출기"
    ]));
    setReportControlIfEmpty("crystal", firstMetadataValue(items, [
      "crystal",
      "atr crystal",
      "crystal type",
      "크리스탈"
    ]));
    setReportControlIfEmpty("resolution", firstMetadataValue(items, [
      "resolution",
      "spectral resolution",
      "resolving power",
      "해상도"
    ]));
    setReportControlIfEmpty("scanTime", firstMetadataValue(items, [
      "scan time",
      "scan times",
      "scans",
      "number of scans",
      "scan number",
      "sample scans",
      "accumulation",
      "스캔",
      "스캔수"
    ]));
    setReportControlIfEmpty("range", firstMetadataValue(items, [
      "range",
      "spectral range",
      "wavenumber range",
      "data range",
      "측정범위",
      "범위"
    ]));
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
      var job = await fetchJson("/api/v1/ftir/report/jobs/" + encodeURIComponent(jobId));
      setReportProgress(job);
      if (job.status === "completed") return job;
      if (job.status === "failed") {
        throw new Error(job.error || job.message || "보고서 생성에 실패했습니다.");
      }
    }
  }

  function setReportDownloadLink(job) {
    var downloadUrl = job.downloadUrl
      || ("/api/v1/ftir/report/jobs/" + encodeURIComponent(job.jobId) + "/download");
    var filename = job.filename || "ftir-report-package.zip";
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
    close.className = "ftir-message-close";
    close.setAttribute("aria-label", "알림 닫기");
    close.textContent = "×";
    close.addEventListener("click", function() {
      setMessage("");
    });
    message.appendChild(label);
    message.appendChild(link);
    message.appendChild(close);
  }

  function selectedLibraryNames() {
    var selected = {};
    selectedLibraryIds.forEach(function(id) { selected[id] = true; });
    return libraries
      .filter(function(item) { return selected[item.id]; })
      .map(function(item) { return item.name; });
  }

  function updateIdleStatus() {
    if (files.length) return;
    status.textContent = selectedLibraryIds.length
      ? "피크 라이브러리 " + selectedLibraryIds.length + "개 적용"
      : "피크 라이브러리 미적용";
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

  function appendCell(row, className) {
    var cell = document.createElement("td");
    if (className) cell.className = className;
    row.appendChild(cell);
    return cell;
  }

  function formField(labelText, input, wide) {
    var label = document.createElement("label");
    label.className = "ftir-library-field" + (wide ? " is-wide" : "");
    var caption = document.createElement("span");
    caption.textContent = labelText;
    label.appendChild(caption);
    label.appendChild(input);
    return label;
  }

  function editorInput(type, value, field) {
    var inputElement = document.createElement("input");
    inputElement.type = type;
    inputElement.value = value == null ? "" : String(value);
    inputElement.dataset.field = field;
    return inputElement;
  }

  function addAssignmentRow(assignment) {
    var body = libraryDialogBody.querySelector("tbody");
    if (!body) return;
    var values = assignment || {
      centerWavenumber: 1000,
      tolerance: 20,
      name: "",
      color: "#64748b",
      note: ""
    };
    var row = document.createElement("tr");
    var center = editorInput(
      "number", values.centerWavenumber, "centerWavenumber"
    );
    center.step = "0.1";
    center.min = "0.1";
    appendCell(row, "numeric").appendChild(center);
    var tolerance = editorInput("number", values.tolerance, "tolerance");
    tolerance.step = "0.1";
    tolerance.min = "0.1";
    appendCell(row, "numeric").appendChild(tolerance);
    appendCell(row, "").appendChild(
      editorInput("text", values.name || "", "name")
    );
    appendCell(row, "color").appendChild(
      editorInput("color", values.color || "#64748b", "color")
    );
    appendCell(row, "").appendChild(
      editorInput("text", values.note || "", "note")
    );
    var removeCell = appendCell(row, "remove");
    var remove = document.createElement("button");
    remove.type = "button";
    remove.className = "ftir-library-row-remove";
    remove.textContent = "×";
    remove.title = "항목 제거";
    remove.setAttribute("aria-label", "항목 제거");
    remove.addEventListener("click", function() {
      row.remove();
    });
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
    box.className = "ftir-library-suggest";
    var input = document.createElement("input");
    input.type = "text";
    input.placeholder = "예: ethanol, alcohol 계열, melamine";
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
    fetch("/api/v1/ftir/assignment-libraries/suggest", {
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

  function syncLibraryDeleteButton(library, isNew) {
    if (libraryDeleteButton) {
      libraryDeleteButton.remove();
      libraryDeleteButton = null;
    }
    if (!libraryDeleteEnabled || isNew || !library || !library.id) return;
    libraryDeleteButton = document.createElement("button");
    libraryDeleteButton.type = "button";
    libraryDeleteButton.className = "ftir-library-dialog-button danger";
    libraryDeleteButton.textContent = "삭제";
    libraryDeleteButton.title = "서버 라이브러리 파일 삭제";
    libraryDeleteButton.addEventListener("click", function() {
      deleteActiveLibrary(library.id, library.name || library.id);
    });
    libraryRowAdd.parentNode.insertBefore(libraryDeleteButton, libraryRowAdd);
  }

  function renderLibraryEditor(library, isNew) {
    activeLibraryId = isNew ? null : library.id;
    activeLibraryIsNew = isNew;
    libraryDialogTitle.textContent = isNew
      ? "새 피크 라이브러리"
      : "피크 라이브러리 편집";
    libraryDialogMeta.textContent = isNew
      ? "JSON 라이브러리 생성"
      : library.fileName + " · " + library.assignmentCount + "개";
    libraryDialogBody.innerHTML = "";

    var meta = document.createElement("div");
    meta.className = "ftir-library-form-meta";
    var idInput = editorInput("text", isNew ? "" : library.id, "libraryId");
    idInput.id = "ftir-library-editor-id";
    idInput.placeholder = "예: melamine";
    idInput.disabled = !isNew;
    var nameInput = editorInput("text", library.name || "", "libraryName");
    nameInput.id = "ftir-library-editor-name";
    var description = document.createElement("textarea");
    description.dataset.field = "libraryDescription";
    description.value = library.description || "";
    meta.appendChild(formField("라이브러리 ID", idInput, false));
    meta.appendChild(formField("라이브러리 이름", nameInput, false));
    meta.appendChild(formField("설명", description, true));
    meta.appendChild(renderLibrarySuggestControl());
    libraryDialogBody.appendChild(meta);

    var table = document.createElement("table");
    table.className = "ftir-library-table";
    var head = document.createElement("thead");
    var headRow = document.createElement("tr");
    [
      ["중심 파수", "numeric"],
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
    activeLibraryIsNew = false;
    libraryDialogTitle.textContent = "피크 라이브러리 편집";
    libraryDialogMeta.textContent = library.fileName;
    libraryDialogBody.innerHTML = "";
    var loadingDetail = document.createElement("div");
    loadingDetail.className = "ftir-library-dialog-loading";
    loadingDetail.textContent = "라이브러리 구성 불러오는 중...";
    libraryDialogBody.appendChild(loadingDetail);
    libraryModal.classList.add("is-visible");
    fetch(
      "/api/v1/ftir/assignment-libraries/" + encodeURIComponent(library.id)
    ).then(function(response) {
      return apiPayload(response, "라이브러리 구성을 불러오지 못했습니다.");
    }).then(function(payload) {
      if (activeLibraryId === library.id) {
        renderLibraryEditor(payload.library, false);
      }
    }).catch(function(err) {
      closeLibraryEditor();
      setMessage(err.message);
    });
  }

  function collectLibraryEditor() {
    var idInput = libraryDialogBody.querySelector(
      '[data-field="libraryId"]'
    );
    var nameInput = libraryDialogBody.querySelector(
      '[data-field="libraryName"]'
    );
    var description = libraryDialogBody.querySelector(
      '[data-field="libraryDescription"]'
    );
    var libraryId = (idInput && idInput.value || "").trim().toLowerCase();
    var libraryName = (nameInput && nameInput.value || "").trim();
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
    if (!assignments.length) {
      throw new Error("피크 assignment 항목을 하나 이상 추가하세요.");
    }
    assignments.forEach(function(item, index) {
      if (!(item.centerWavenumber > 0) || !(item.tolerance > 0)
          || !item.name) {
        throw new Error((index + 1) + "번 항목의 파수, 허용 오차, 이름을 확인하세요.");
      }
    });
    return {
      id: libraryId,
      name: libraryName,
      description: description ? description.value.trim() : "",
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
    setMessage("");
    fetch(
      isNew
        ? "/api/v1/ftir/assignment-libraries/create"
        : "/api/v1/ftir/assignment-libraries/" + encodeURIComponent(targetId),
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
    if (!window.confirm("'" + libraryName + "' 라이브러리 파일을 삭제할까요?")) {
      return;
    }
    if (libraryDeleteButton) libraryDeleteButton.disabled = true;
    libraryDialogSave.disabled = true;
    libraryRowAdd.disabled = true;
    setMessage("");
    fetch(
      "/api/v1/ftir/assignment-libraries/" + encodeURIComponent(libraryId),
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
    }).finally(function() {
      if (libraryDeleteButton) libraryDeleteButton.disabled = false;
      libraryDialogSave.disabled = false;
      libraryRowAdd.disabled = false;
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
      empty.className = "ftir-library-empty";
      empty.textContent = libraries.length
        ? "검색 결과가 없습니다"
        : "등록된 라이브러리가 없습니다";
      libraryList.appendChild(empty);
      return;
    }
    visibleLibraries.forEach(function(library) {
      var isSelected = selectedLibraryIds.indexOf(library.id) >= 0;
      var item = document.createElement("span");
      item.className = "ftir-library-item"
        + (isSelected ? " is-selected" : "")
        + (library.valid ? "" : " is-invalid");
      if (library.description || library.error) {
        item.title = library.error || library.description;
      }

      var toggle = document.createElement("span");
      toggle.className = "ftir-library-toggle";
      var checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = isSelected;
      checkbox.disabled = !library.valid;
      checkbox.setAttribute("aria-label", library.name + " 선택");
      checkbox.addEventListener("change", function() {
        if (checkbox.checked) {
          if (selectedLibraryIds.indexOf(library.id) < 0) {
            selectedLibraryIds.push(library.id);
          }
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
      name.className = "ftir-library-name";
      name.textContent = library.name;
      name.title = library.name + " 편집";
      name.addEventListener("click", function() {
        showLibraryEditor(library);
      });
      var count = document.createElement("span");
      count.className = "ftir-library-count";
      count.textContent = library.valid
        ? String(library.assignmentCount)
        : "오류";
      var state = document.createElement("span");
      state.className = "ftir-library-state";
      state.textContent = isSelected ? "적용" : "미적용";

      item.appendChild(toggle);
      item.appendChild(name);
      item.appendChild(count);
      item.appendChild(state);
      libraryList.appendChild(item);
    });
  }

  async function apiPayload(response, fallback) {
    var payload = await response.json().catch(function() { return {}; });
    if (!response.ok) {
      throw new Error(payload.message || fallback);
    }
    return payload;
  }

  function loadLibraries(preferredIds) {
    return fetch("/api/v1/ftir/assignment-libraries")
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
    setMessage("");
    fetch("/api/v1/ftir/assignment-libraries", {
      method: "POST",
      body: form
    }).then(function(response) {
      return apiPayload(response, "라이브러리 업로드에 실패했습니다.");
    }).then(function(payload) {
      var preferred = selectedLibraryIds.slice();
      if (preferred.indexOf(payload.library.id) < 0) {
        preferred.push(payload.library.id);
      }
      return loadLibraries(preferred);
    }).then(function() {
      return files.length ? analyze() : null;
    }).catch(function(err) {
      setMessage(err.message);
    }).finally(function() {
      setBusy(false);
      loading.textContent = "전처리 및 피크 분석 중...";
    });
  }

  function renderFiles() {
    fileList.innerHTML = "";
    prompt.style.display = files.length ? "none" : "inline";
    clearButton.hidden = false;
    files.forEach(function(file, index) {
      var item = document.createElement("span");
      item.className = "ftir-file-item";
      item.textContent = file.name;
      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "ftir-file-remove";
      remove.textContent = "×";
      remove.title = file.name + " 제거";
      remove.setAttribute("aria-label", file.name + " 제거");
      remove.addEventListener("click", function() {
        files.splice(index, 1);
        renderFiles();
        if (files.length) analyze();
        else resetGraph();
      });
      item.appendChild(remove);
      fileList.appendChild(item);
    });
  }

  function validate(incoming) {
    var accepted = [];
    for (var i = 0; i < incoming.length; i++) {
      var file = incoming[i];
      if (!/\\.dpt$/i.test(file.name)) {
        throw new Error("DPT 파일만 업로드할 수 있습니다: " + file.name);
      }
      if (file.size === 0) throw new Error("빈 파일은 분석할 수 없습니다: " + file.name);
      if (file.size > MAX_FILE_BYTES) {
        throw new Error("파일 크기는 20MB 이하여야 합니다: " + file.name);
      }
      accepted.push(file);
    }
    return accepted;
  }

  function addFiles(incoming) {
    setMessage("");
    var previousFiles = files.slice();
    var accepted;
    try {
      accepted = validate(Array.prototype.slice.call(incoming || []));
    } catch (err) {
      setMessage(err.message);
      return;
    }
    var keys = {};
    files.forEach(function(file) { keys[fileKey(file)] = true; });
    accepted.forEach(function(file) {
      if (!keys[fileKey(file)]) {
        files.push(file);
        keys[fileKey(file)] = true;
      }
    });
    if (files.length > MAX_FILES) {
      files = files.slice(0, MAX_FILES);
      setMessage("한 번에 최대 10개 DPT 파일을 분석할 수 있습니다.");
    }
    var totalBytes = files.reduce(function(total, file) {
      return total + file.size;
    }, 0);
    if (totalBytes > MAX_TOTAL_BYTES) {
      files = previousFiles;
      setMessage("한 번에 업로드하는 DPT 파일의 총 크기는 50MB 이하여야 합니다.");
      renderFiles();
      return;
    }
    renderFiles();
    if (files.length) analyze();
    else scheduleWorkspaceSave();
  }

  function dispatchDataReplaced(sensitivity) {
    gd.dispatchEvent(new CustomEvent("rist-plot-data-replaced", {
      detail: { sensitivity: sensitivity }
    }));
  }

  function applyResponsiveLayout() {
    var mobile = window.innerWidth <= 760;
    return window.Plotly.relayout(gd, mobile ? {
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
      "margin.t": 82,
      "margin.r": (gd.data || []).length ? 260 : 70,
      "margin.b": 70,
      "legend.orientation": "v",
      "legend.x": 1.02,
      "legend.xanchor": "left",
      "legend.y": 1.0,
      "legend.yanchor": "top"
    });
  }

  function resetGraph() {
    if (controller) controller.abort();
    controller = null;
    setBusy(false);
    setMessage("");
    updateIdleStatus();
    window.Plotly.react(gd, freshEmptyData(), freshEmptyLayout(), gd._context).then(function() {
      dispatchDataReplaced(25);
      return applyResponsiveLayout();
    }).then(function() {
      window.Plotly.Plots.resize(gd);
      scheduleWorkspaceSave();
    });
  }

  function analyze() {
    if (!files.length) return Promise.resolve();
    if (controller) controller.abort();
    controller = new AbortController();
    var activeController = controller;
    var form = new FormData();
    files.forEach(function(file) { form.append("files", file, file.name); });
    form.append("sensitivity", String(gd._ristPeakSensitivityValue || 25));
    form.append("assignment_library_selection_explicit", "true");
    selectedLibraryIds.forEach(function(id) {
      form.append("assignment_library_ids", id);
    });
    loading.textContent = "전처리 및 피크 분석 중...";
    setBusy(true);
    setMessage("");
    status.textContent = files.length + "개 파일 분석 중";

    return fetch("/api/v1/ftir/analyze", {
      method: "POST",
      body: form,
      signal: activeController.signal
    }).then(async function(response) {
      var payload = await response.json().catch(function() { return {}; });
      if (!response.ok) {
        throw new Error(payload.message || "DPT 분석에 실패했습니다.");
      }
      return payload;
    }).then(function(payload) {
      if (controller !== activeController) return;
      latestAnalysisPayload = JSON.parse(JSON.stringify(payload));
      populateReportMetadataFromPayload(payload);
      return window.Plotly.react(
        gd,
        payload.figure.data,
        payload.figure.layout,
        gd._context
      ).then(function() {
        var peakCount = payload.samples.reduce(function(total, sample) {
          return total + Number(sample.peakCount || 0);
        }, 0);
        var libraryCount = selectedLibraryNames().length;
        status.textContent = payload.samples.length + "개 시료 · 피크 "
          + peakCount + "개 · 라이브러리 " + libraryCount + "개";
        dispatchDataReplaced(payload.settings.sensitivity);
        return applyResponsiveLayout();
      }).then(function() {
        window.Plotly.Plots.resize(gd);
        scheduleWorkspaceSave();
      });
    }).catch(function(err) {
      if (err.name === "AbortError") return;
      setMessage(err.message || "DPT 분석에 실패했습니다.");
      status.textContent = "분석 실패";
    }).finally(function() {
      if (controller === activeController) {
        controller = null;
        setBusy(false);
      }
    });
  }

  function currentFigurePayload() {
    return {
      data: JSON.parse(JSON.stringify(gd.data || [])),
      layout: JSON.parse(JSON.stringify(gd.layout || {}))
    };
  }

  async function createReport() {
    if (!files.length) {
      setMessage("보고서를 생성하려면 DPT 파일을 먼저 업로드하세요.");
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
      var job = await fetchJson("/api/v1/ftir/report/jobs", {
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
      loading.textContent = "전처리 및 피크 분석 중...";
    }
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
        fileName: "",
        assignmentCount: 0,
        assignments: []
      },
      true
    );
  });
  libraryRowAdd.addEventListener("click", function() {
    addAssignmentRow();
  });
  libraryDialogSave.addEventListener("click", saveLibraryEditor);
  libraryDialogCancel.addEventListener("click", closeLibraryEditor);
  libraryDialogClose.addEventListener("click", closeLibraryEditor);
  libraryModal.addEventListener("click", function(ev) {
    if (ev.target === libraryModal) closeLibraryEditor();
  });
  document.addEventListener("keydown", function(ev) {
    if (ev.key === "Escape" && libraryModal.classList.contains("is-visible")) {
      closeLibraryEditor();
    }
  });
  reportButton.addEventListener("click", createReport);
  clearButton.addEventListener("click", function() {
    files = [];
    latestAnalysisPayload = null;
    setReportProgress(null);
    setMessage("");
    clearReportMetadataForm();
    renderFiles();
    clearWorkspaceState();
    resetGraph();
  });
  ["dragenter", "dragover"].forEach(function(name) {
    dropZone.addEventListener(name, function(ev) {
      ev.preventDefault();
      dropZone.classList.add("is-dragging");
    });
  });
  ["dragleave", "drop"].forEach(function(name) {
    dropZone.addEventListener(name, function(ev) {
      ev.preventDefault();
      dropZone.classList.remove("is-dragging");
    });
  });
  dropZone.addEventListener("drop", function(ev) {
    addFiles(ev.dataTransfer && ev.dataTransfer.files);
  });
  document.addEventListener("dragover", function(ev) { ev.preventDefault(); });
  document.addEventListener("drop", function(ev) {
    if (!dropZone.contains(ev.target)) ev.preventDefault();
  });
  var resizeFrame = 0;
  window.addEventListener("resize", function() {
    if (resizeFrame) cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(function() {
      resizeFrame = 0;
      applyResponsiveLayout();
    });
  });
  gd.addEventListener("rist-ftir-tools-toggle", function() {
    applyResponsiveLayout();
  });
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


@lru_cache(maxsize=1)
def build_ftir_page() -> str:
    extra_scripts = (
        peak_sensitivity_js(PLOT_DIV_ID, initial="low")
        + _FTIR_TOOL_PANEL_SCRIPT
        + ftir_abs_trans_toggle_js(
            PLOT_DIV_ID,
            yaxis_titles={
                "yaxis": {
                    "absorbance": "Normalized Absorbance",
                    "transmittance": "Transmittance (%)",
                }
            },
        )
        + _UPLOAD_SCRIPT
    )
    page = fig_to_responsive_html(
        _blank_figure(),
        div_id=PLOT_DIV_ID,
        include_plotlyjs="/ftir/assets/plotly.min.js",
        responsive_legend=False,
        crosshair=True,
        title_edit=True,
        legend_text_edit=True,
        peak_editor=True,
        shape_editor=True,
        image_filename="ftir_peak_analysis",
        image_format_selector=True,
        post_body_html=extra_scripts,
        config={"scrollZoom": True},
    )
    page = page.replace("</head>", _PAGE_STYLE + "</head>", 1)
    return page.replace("<body>", "<body>" + _PAGE_SHELL, 1)


@router.get("/ftir", response_class=HTMLResponse, include_in_schema=False)
def ftir_workspace() -> HTMLResponse:
    return HTMLResponse(build_ftir_page())


@router.get("/ftir/assets/plotly.min.js", include_in_schema=False)
def ftir_plotly_asset() -> FileResponse:
    path = plotly_asset_path()
    if not path.is_file():
        raise ApiException(
            500,
            "PLOTLY_ASSET_NOT_FOUND",
            "Plotly 웹 자산을 찾을 수 없습니다.",
        )
    return FileResponse(
        path,
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/api/v1/ftir/assignment-libraries", tags=["ftir"])
def list_assignment_libraries(request: Request) -> dict:
    store = assignment_library_store(request)
    return {
        "libraries": store.summaries(),
        "directory": str(store.root),
        "supportedFormats": ["json", "csv"],
        "deleteEnabled": assignment_library_delete_enabled(request),
    }


@router.post(
    "/api/v1/ftir/assignment-libraries",
    tags=["ftir"],
    status_code=201,
)
def upload_assignment_library(
    request: Request,
    file: UploadFile = File(...),
) -> dict:
    raw_filename = (file.filename or "").replace("\\", "/")
    filename = Path(raw_filename).name
    content = file.file.read(MAX_LIBRARY_BYTES + 1)
    try:
        library = assignment_library_store(request).save(filename, content)
    except AssignmentLibraryError as exc:
        raise_assignment_library_api(exc)
    logger.info(
        "FT-IR assignment 라이브러리 업로드 (id=%s, assignments=%d)",
        library.library_id,
        len(library.assignments),
    )
    return {"library": library.summary()}


@router.post(
    "/api/v1/ftir/assignment-libraries/create",
    tags=["ftir"],
    status_code=201,
)
def create_assignment_library(
    request: Request,
    payload: AssignmentLibraryCreate,
) -> dict:
    values = payload.model_dump(exclude={"id"})
    try:
        library = assignment_library_store(request).write(
            payload.id,
            values,
            create_only=True,
        )
    except AssignmentLibraryError as exc:
        raise_assignment_library_api(exc)
    logger.info(
        "FT-IR assignment 라이브러리 생성 (id=%s, assignments=%d)",
        library.library_id,
        len(library.assignments),
    )
    return {"library": library.detail()}


@router.post(
    "/api/v1/ftir/assignment-libraries/suggest",
    tags=["ftir"],
)
def suggest_assignment_library(
    request: Request,
    payload: AssignmentLibrarySuggest,
) -> dict:
    try:
        return assignment_suggestions.suggest_assignment_library(
            llm_settings(request),
            AssignmentSuggestionRequest(
                experiment_code="FT-IR",
                material=payload.material,
                library_id=payload.libraryId,
                library_name=payload.libraryName,
            ),
        )
    except AssignmentLibraryError as exc:
        raise_assignment_library_api(exc)


@router.get(
    "/api/v1/ftir/assignment-libraries/{library_id}",
    tags=["ftir"],
)
def get_assignment_library(request: Request, library_id: str) -> dict:
    try:
        library = assignment_library_store(request).get(library_id)
    except AssignmentLibraryError as exc:
        raise_assignment_library_api(exc)
    return {"library": library.detail()}


@router.put(
    "/api/v1/ftir/assignment-libraries/{library_id}",
    tags=["ftir"],
)
def update_assignment_library(
    request: Request,
    library_id: str,
    payload: AssignmentLibraryWrite,
) -> dict:
    try:
        library = assignment_library_store(request).write(
            library_id,
            payload.model_dump(),
            create_only=False,
        )
    except AssignmentLibraryError as exc:
        raise_assignment_library_api(exc)
    logger.info(
        "FT-IR assignment 라이브러리 수정 (id=%s, assignments=%d)",
        library.library_id,
        len(library.assignments),
    )
    return {"library": library.detail()}


@router.delete(
    "/api/v1/ftir/assignment-libraries/{library_id}",
    tags=["ftir"],
)
def delete_assignment_library(request: Request, library_id: str) -> dict:
    if not assignment_library_delete_enabled(request):
        raise ApiException(
            403,
            "ASSIGNMENT_LIBRARY_DELETE_DISABLED",
            "피크 assignment 라이브러리 삭제 기능이 비활성화되어 있습니다.",
        )
    try:
        assignment_library_store(request).delete(library_id)
    except AssignmentLibraryError as exc:
        raise_assignment_library_api(exc)
    logger.info("FT-IR assignment 라이브러리 삭제 (id=%s)", library_id)
    return {"deleted": True, "id": library_id}


@router.post("/api/v1/ftir/analyze", tags=["ftir"])
def analyze_ftir(
    request: Request,
    files: list[UploadFile] = File(...),
    sensitivity: int = Form(default=25, ge=0, le=100),
    assignment_library_ids: list[str] | None = Form(default=None),
    assignment_library_selection_explicit: bool = Form(default=False),
) -> dict:
    uploaded = _uploaded_dpt_files(files)

    store = assignment_library_store(request)
    if assignment_library_selection_explicit:
        selected_ids = assignment_library_ids or []
    elif assignment_library_ids is not None:
        selected_ids = assignment_library_ids
    else:
        selected_ids = store.default_ids()
    try:
        libraries = store.load(selected_ids)
        result = analyze_dpt_files(
            uploaded,
            sensitivity=sensitivity,
            assignment_libraries=libraries,
        )
    except AssignmentLibraryError as exc:
        raise_assignment_library_api(exc)
    except DptAnalysisError as exc:
        logger.info(
            "FT-IR 미리보기 분석 거부 (code=%s, file=%s)",
            exc.code,
            exc.filename,
        )
        raise ApiException(422, exc.code, exc.message) from exc

    logger.info(
        "FT-IR 미리보기 분석 완료 (files=%d, sensitivity=%d, libraries=%d)",
        len(uploaded),
        sensitivity,
        len(libraries),
    )
    return result


@router.post("/api/v1/ftir/report", tags=["ftir"])
def create_ftir_preview_report(
    request: Request,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    analysis_json: str = Form(...),
    figure_json: str = Form(default=""),
    figure_image: str = Form(...),
) -> FileResponse:
    uploaded = _uploaded_dpt_files(files)
    try:
        analysis_payload = parse_analysis_payload(analysis_json, figure_json)
        image_bytes = decode_figure_image(figure_image)
        raw_series = _build_ftir_raw_series(uploaded)
        tmp_root, package = build_preview_report_package(
            experiment_code="FT-IR",
            analysis_payload=analysis_payload,
            raw_series=raw_series,
            figure_image=image_bytes,
            settings=getattr(request.app.state, "settings", None),
        )
    except ValueError as exc:
        raise ApiException(400, "FTIR_REPORT_INVALID_PAYLOAD", str(exc)) from exc
    except Exception as exc:
        raise ApiException(422, "FTIR_REPORT_FAILED", str(exc)) from exc

    background_tasks.add_task(cleanup_preview_report, tmp_root)
    return FileResponse(
        package,
        media_type="application/zip",
        filename="ftir-report-package.zip",
    )


@router.post("/api/v1/ftir/report/jobs", status_code=202, tags=["ftir"])
def create_ftir_preview_report_job(
    request: Request,
    files: list[UploadFile] = File(...),
    analysis_json: str = Form(...),
    figure_json: str = Form(default=""),
    figure_image: str = Form(...),
) -> dict:
    uploaded = _uploaded_dpt_files(files)
    try:
        analysis_payload = parse_analysis_payload(analysis_json, figure_json)
        image_bytes = decode_figure_image(figure_image)
    except ValueError as exc:
        raise ApiException(400, "FTIR_REPORT_INVALID_PAYLOAD", str(exc)) from exc

    store = preview_report_job_store(request.app)
    job = store.create(filename="ftir-report-package.zip")

    def raw_series_factory() -> list[RawSeries]:
        return _build_ftir_raw_series(uploaded)

    start_preview_report_job(
        store,
        job.job_id,
        experiment_code="FT-IR",
        analysis_payload=analysis_payload,
        raw_series_factory=raw_series_factory,
        figure_image=image_bytes,
        settings=getattr(request.app.state, "settings", None),
    )
    return _report_job_response(job, prefix="/api/v1/ftir/report/jobs")


@router.get("/api/v1/ftir/report/jobs/{job_id}", tags=["ftir"])
def get_ftir_preview_report_job(request: Request, job_id: str) -> dict:
    store = preview_report_job_store(request.app)
    job = store.get(job_id)
    if job is None:
        raise ApiException(404, "FTIR_REPORT_JOB_NOT_FOUND", "보고서 작업을 찾을 수 없습니다.")
    return _report_job_response(job, prefix="/api/v1/ftir/report/jobs")


@router.get("/api/v1/ftir/report/jobs/{job_id}/download", tags=["ftir"])
def download_ftir_preview_report_job(
    request: Request,
    background_tasks: BackgroundTasks,
    job_id: str,
) -> FileResponse:
    store = preview_report_job_store(request.app)
    job = store.get(job_id)
    if job is None:
        raise ApiException(404, "FTIR_REPORT_JOB_NOT_FOUND", "보고서 작업을 찾을 수 없습니다.")
    if job.status != "completed" or job.package_path is None:
        raise ApiException(409, "FTIR_REPORT_JOB_NOT_READY", "보고서가 아직 완성되지 않았습니다.")
    if not job.package_path.is_file():
        store.remove(job_id)
        raise ApiException(410, "FTIR_REPORT_PACKAGE_EXPIRED", "보고서 파일이 만료되었습니다.")
    background_tasks.add_task(store.remove, job_id)
    return FileResponse(
        job.package_path,
        media_type="application/zip",
        filename=job.filename,
    )


def create_ftir_preview_app(
    assignment_library_dir: Path | None = None,
    assignment_library_delete_enabled: bool | None = None,
) -> FastAPI:
    """Create a DB-free app for local FT-IR workspace development."""
    app = FastAPI(title="RIST FT-IR Preview")
    app.state.ftir_assignment_library_dir = (
        assignment_library_dir
        or Path(
            os.getenv(
                "RIST_FTIR_ASSIGNMENT_LIBRARY_DIR",
                str(DEFAULT_ASSIGNMENT_LIBRARY_DIR),
            )
        )
    )
    app.state.ftir_assignment_library_delete_enabled = (
        assignment_library_delete_enabled
        if assignment_library_delete_enabled is not None
        else os.getenv(
            "RIST_FTIR_ASSIGNMENT_LIBRARY_DELETE_ENABLED",
            "false",
        ).lower()
        in {"1", "true", "yes", "on"}
    )
    app.add_exception_handler(ApiException, api_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.include_router(router)
    return app

"""Raman upload workspace HTML and preview API."""

from __future__ import annotations

from pathlib import Path
import os

import plotly
import plotly.graph_objects as go
from fastapi import APIRouter, FastAPI, File, Form, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

try:
    from rin.raman import preprocess as raman_preprocess_module
    from rin.raman.preprocess import SUPPORTED_SUFFIXES
    from rin.raman.web_analysis import RamanAnalysisError, analyze_raman_files
except ModuleNotFoundError:  # pragma: no cover - installed via edge requirements
    from raman import preprocess as raman_preprocess_module
    from raman.preprocess import SUPPORTED_SUFFIXES
    from raman.web_analysis import RamanAnalysisError, analyze_raman_files
from ftir.assignment_libraries import (
    AssignmentLibraryError,
    AssignmentLibraryStore,
    MAX_LIBRARY_BYTES,
)
from rist_common import get_logger
from rist_common.plotting import fig_to_responsive_html, peak_sensitivity_js

from .errors import ApiException, api_exception_handler, validation_exception_handler


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
    else:
        status_code = 400
    raise ApiException(status_code, exc.code, exc.message) from exc


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
.raman-message {
  display: none;
  min-height: 32px;
  padding: 8px 22px;
  border-bottom: 1px solid #fecaca;
  background: #fef2f2;
  color: #b42318;
  font-size: 12px;
  box-sizing: border-box;
}
.raman-message.is-visible { display: block; }
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
  }
  .raman-status {
    flex: 1 1 100%;
    max-width: 100%;
  }
  .raman-drop-zone {
    align-items: flex-start;
    flex-direction: column;
    gap: 6px;
    min-height: 76px;
    padding: 7px 12px;
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
    min-height: 720px;
    height: calc(100vh - 248px + 180px) !important;
  }
}
@media (max-width: 1440px) {
  #raman-plot .rist-raman-tools-toggle {
    position: absolute;
    top: 34px;
    right: 8px;
    z-index: 25;
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
    <button class="raman-clear-button" id="raman-clear" type="button" hidden>초기화</button>
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
<div class="raman-message" id="raman-message"></div>
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
    return {
      left: clamp(left, 8, Math.max(8, plotRect.width - width - 8)),
      top: clamp(top, 44, Math.max(44, plotRect.height - height - 8))
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
    ratioButton.classList.toggle("is-active", ratioMode);
    ratioButton.textContent = ratioMode ? "분자" : "비율";
    if (!ratioMode) {
      setStatus(ratios.length ? ratios.length + "개" : "");
    } else if (pendingNumerator) {
      setStatus("분모 선택");
    } else {
      setStatus("분자 선택");
    }
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
  gd.on("plotly_click", function(ev) {
    if (!ratioMode || !ev || !ev.points || !ev.points.length) return;
    var curve = ev.points[0].curveNumber;
    pickPeak(curve);
  });
  gd.addEventListener("rist-raman-stack-change", function() {
    if (ratios.length) renderRatios();
  });
  gd.addEventListener("rist-plot-data-replaced", function() {
    ratios = [];
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
  var clearButton = document.getElementById("raman-clear");
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
  if (!gd || !input || !dropZone || !prompt || !fileList || !status || !message
      || !loading || !clearButton || !libraryInput || !libraryList
      || !libraryFilter || !libraryNew || !libraryModal || !libraryDialogClose
      || !libraryRowAdd || !libraryDialogCancel || !libraryDialogSave) return;

  var files = [];
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

  function dispatchDataReplaced(sensitivity) {
    gd.dispatchEvent(new CustomEvent("rist-plot-data-replaced", {
      detail: { sensitivity: sensitivity }
    }));
  }

  function applyResponsiveLayout() {
    var compact = window.innerWidth <= 760;
    return window.Plotly.relayout(gd, compact ? {
      "margin.t": 170,
      "margin.r": 30,
      "margin.b": 135,
      "legend.orientation": "h",
      "legend.x": 0.5,
      "legend.xanchor": "center",
      "legend.y": -0.2,
      "legend.yanchor": "top"
    } : {
      "margin.t": 120,
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

  function setMessage(text) {
    message.textContent = text || "";
    message.classList.toggle("is-visible", !!text);
  }

  function setBusy(busy) {
    loading.classList.toggle("is-visible", busy);
    input.disabled = busy;
    clearButton.disabled = busy;
    libraryInput.disabled = busy;
    libraryFilter.disabled = busy;
    libraryNew.disabled = busy;
    libraryList.querySelectorAll("input, button").forEach(function(control) {
      control.disabled = busy;
    });
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
    clearButton.hidden = !files.length;
  }

  function resetPlot() {
    files = [];
    renderFiles();
    setMessage("");
    status.textContent = "Raman raw 파일을 업로드하세요";
    if (window.Plotly) {
      window.Plotly.react(gd, emptyData, emptyLayout, gd._context).then(function() {
        dispatchDataReplaced(25);
        return applyResponsiveLayout();
      }).then(function() {
        window.Plotly.Plots.resize(gd);
      });
    }
  }

  async function analyze() {
    if (!files.length) {
      resetPlot();
      return;
    }
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
    } catch (err) {
      setMessage(err.message || String(err));
    } finally {
      setBusy(false);
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
    analyze();
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
  clearButton.addEventListener("click", resetPlot);
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
  loadLibraries();
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


def create_raman_preview_app() -> FastAPI:
    app = FastAPI(title="RIST Raman Preview")
    app.add_exception_handler(ApiException, api_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.include_router(router)
    return app

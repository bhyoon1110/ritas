"""AHN TEM/STEM/EDS/coating report upload workspace."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4
import zipfile

from fastapi import APIRouter, FastAPI, File, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from rist_common import get_logger

from .errors import ApiException, api_exception_handler, validation_exception_handler
from .path_bootstrap import add_project_package_paths

add_project_package_paths()

from ahn.analysis import DOCX_EXTENSIONS, IMAGE_EXTENSIONS, SPREADSHEET_EXTENSIONS
from ahn.processor import build_outputs

logger = get_logger(__name__)
router = APIRouter()

AHN_SECTION_DIRS = {"tem", "stem", "report", "scale"}
AHN_SUPPORTED_EXTENSIONS = (
    IMAGE_EXTENSIONS | DOCX_EXTENSIONS | SPREADSHEET_EXTENSIONS | {".zip"}
)
MAX_AHN_UPLOAD_FILE_BYTES = 250 * 1024 * 1024
MAX_AHN_UPLOAD_TOTAL_BYTES = 1200 * 1024 * 1024
AHN_REPORT_JOB_TTL_SECONDS = 2 * 60 * 60


@dataclass
class AhnReportJob:
    job_id: str
    work_dir: Path
    output_dir: Path
    pptx_path: Path
    package_path: Path
    analysis_path: Path
    manifest_path: Path
    manifest: dict[str, Any]
    created_at: float


_ahn_report_jobs: dict[str, AhnReportJob] = {}


def _safe_relative_path(filename: str | None, fallback: str) -> Path:
    raw = str(filename or "").strip().replace("\\", "/")
    parts = [part for part in raw.split("/") if part and part not in {".", ".."}]
    if not parts:
        parts = [fallback]
    safe_parts: list[str] = []
    for index, part in enumerate(parts):
        clean = re.sub(r"[^\w.\-() \[\]\u3131-\u318e\uac00-\ud7a3]+", "_", part).strip()
        if not clean:
            clean = fallback if index == len(parts) - 1 else "folder"
        safe_parts.append(clean[:180])
    return Path(*safe_parts)


def _unique_path(path: Path) -> Path:
    candidate = path
    index = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        index += 1
    return candidate


def _section_dir_score(directory: Path) -> int:
    if not directory.exists() or not directory.is_dir():
        return 0
    names = {
        child.name.casefold()
        for child in directory.iterdir()
        if child.is_dir() and not child.name.startswith(".")
    }
    return sum(1 for name in AHN_SECTION_DIRS if name in names)


def _find_ahn_input_root(upload_root: Path) -> Path:
    """Accept both `stem/...` and `TopFolder/stem/...` browser uploads."""
    if _section_dir_score(upload_root) > 0:
        return upload_root

    candidates: list[tuple[int, int, Path]] = []
    for directory in upload_root.rglob("*"):
        if not directory.is_dir():
            continue
        score = _section_dir_score(directory)
        if score:
            try:
                depth = len(directory.relative_to(upload_root).parts)
            except ValueError:
                depth = 99
            candidates.append((score, -depth, directory))
    if not candidates:
        return upload_root
    candidates.sort(key=lambda item: (-item[0], -item[1], item[2].as_posix()))
    return candidates[0][2]


def _has_reportable_data(summary: dict[str, Any]) -> bool:
    keys = (
        "temImageCount",
        "stemImageCount",
        "stemBfImageCount",
        "edsReportCount",
        "coatingImageCount",
    )
    return any(int(summary.get(key) or 0) > 0 for key in keys)


def _extract_zip_bytes(data: bytes, target_root: Path) -> int:
    saved_count = 0
    extracted_bytes = 0
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ApiException(400, "INVALID_AHN_ZIP", "읽을 수 없는 ZIP 파일입니다.") from exc

    with archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            relative = _safe_relative_path(member.filename, "zip-file")
            if "__MACOSX" in relative.parts or relative.name.startswith("."):
                continue
            suffix = relative.suffix.lower()
            if suffix not in (AHN_SUPPORTED_EXTENSIONS - {".zip"}):
                continue
            size = int(member.file_size or 0)
            if size > MAX_AHN_UPLOAD_FILE_BYTES:
                raise ApiException(
                    413,
                    "AHN_FILE_TOO_LARGE",
                    f"{relative.name} 파일이 너무 큽니다. 파일당 최대 250MB입니다.",
                )
            extracted_bytes += size
            if extracted_bytes > MAX_AHN_UPLOAD_TOTAL_BYTES:
                raise ApiException(
                    413,
                    "AHN_UPLOAD_TOO_LARGE",
                    "ZIP 압축 해제 후 AHN raw bundle의 총 크기는 1.2GB 이하여야 합니다.",
                )
            destination = _unique_path(target_root / relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)
            saved_count += 1
    return saved_count


async def _save_ahn_uploads(files: list[UploadFile] | None, upload_root: Path) -> list[str]:
    if not files:
        raise ApiException(400, "AHN_FILES_REQUIRED", "AHN raw 폴더 또는 파일이 필요합니다.")

    upload_root.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    unsupported: list[str] = []
    total_bytes = 0
    for index, upload in enumerate(files, start=1):
        relative = _safe_relative_path(upload.filename, f"ahn-file-{index}")
        suffix = relative.suffix.lower()
        if not suffix and relative.name.startswith("."):
            continue
        if suffix not in AHN_SUPPORTED_EXTENSIONS:
            unsupported.append(relative.as_posix())
            continue

        data = await upload.read()
        if not data:
            continue
        if len(data) > MAX_AHN_UPLOAD_FILE_BYTES:
            raise ApiException(
                413,
                "AHN_FILE_TOO_LARGE",
                f"{relative.name} 파일이 너무 큽니다. 파일당 최대 250MB입니다.",
            )
        total_bytes += len(data)
        if total_bytes > MAX_AHN_UPLOAD_TOTAL_BYTES:
            raise ApiException(
                413,
                "AHN_UPLOAD_TOO_LARGE",
                "한 번에 업로드하는 AHN raw bundle의 총 크기는 1.2GB 이하여야 합니다.",
            )

        if suffix == ".zip":
            extracted = _extract_zip_bytes(data, upload_root)
            saved.append(f"{relative.as_posix()} ({extracted} files)")
            continue

        destination = _unique_path(upload_root / relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        saved.append(destination.relative_to(upload_root).as_posix())

    if unsupported:
        preview = ", ".join(unsupported[:5])
        more = f" 외 {len(unsupported) - 5}개" if len(unsupported) > 5 else ""
        allowed = ", ".join(sorted(AHN_SUPPORTED_EXTENSIONS))
        raise ApiException(
            400,
            "INVALID_AHN_FILE_TYPE",
            f"지원하지 않는 파일이 포함되어 있습니다: {preview}{more}. 허용 형식: {allowed}",
        )
    if not saved:
        raise ApiException(400, "AHN_FILES_REQUIRED", "분석 가능한 AHN 파일이 없습니다.")
    return saved


def _cleanup_old_jobs() -> None:
    now = time.time()
    expired = [
        job_id
        for job_id, job in _ahn_report_jobs.items()
        if now - job.created_at > AHN_REPORT_JOB_TTL_SECONDS
    ]
    for job_id in expired:
        job = _ahn_report_jobs.pop(job_id, None)
        if job:
            shutil.rmtree(job.work_dir, ignore_errors=True)


def _build_package(output_dir: Path, package_path: Path) -> Path:
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.rglob("*")):
            if not path.is_file() or path == package_path:
                continue
            archive.write(path, path.relative_to(output_dir).as_posix())
    return package_path


def _build_ahn_job(input_root: Path, work_dir: Path) -> AhnReportJob:
    output_dir = work_dir / "output"
    pptx_path = output_dir / "ahn-report.pptx"
    manifest = build_outputs(
        input_dir=input_root,
        output_dir=output_dir,
        pptx_path=pptx_path,
        copy_raw_spreadsheets=True,
    )
    summary = manifest.get("summary") if isinstance(manifest.get("summary"), dict) else {}
    if not _has_reportable_data(summary):
        raise ApiException(
            400,
            "AHN_NO_REPORT_DATA",
            "입력 폴더에서 TEM, STEM, EDS, 코팅층 분석 대상 데이터를 찾지 못했습니다.",
        )
    package_path = _build_package(output_dir, output_dir / "ahn-report-package.zip")
    job_id = uuid4().hex
    job = AhnReportJob(
        job_id=job_id,
        work_dir=work_dir,
        output_dir=output_dir,
        pptx_path=pptx_path,
        package_path=package_path,
        analysis_path=output_dir / "analysis-result.json",
        manifest_path=output_dir / "manifest.json",
        manifest=manifest,
        created_at=time.time(),
    )
    _ahn_report_jobs[job_id] = job
    return job


def _job_payload(job: AhnReportJob) -> dict[str, Any]:
    prefix = f"/api/v1/ahn/report/jobs/{job.job_id}/download"
    return {
        "jobId": job.job_id,
        "summary": job.manifest.get("summary") or {},
        "manifest": job.manifest,
        "downloads": {
            "pptx": f"{prefix}/pptx",
            "package": f"{prefix}/package",
            "analysisJson": f"{prefix}/analysis-json",
        },
    }


def build_ahn_page() -> str:
    return """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RIST AHN TEM/STEM</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #172a46;
      --muted: #64748b;
      --line: #cbd5e1;
      --blue: #2563eb;
      --green: #16a34a;
      --bg: #f8fafc;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Arial, "Noto Sans KR", sans-serif;
      color: var(--ink);
      background: var(--bg);
    }
    .ahn-shell { min-height: 100vh; display: flex; flex-direction: column; }
    .ahn-topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 24px 28px;
      border-bottom: 1px solid var(--line);
      background: #fff;
    }
    .ahn-brand { display: flex; align-items: baseline; gap: 14px; min-width: 0; }
    .ahn-brand h1 { margin: 0; font-size: 30px; letter-spacing: 0; }
    .ahn-brand span { color: var(--muted); font-size: 17px; white-space: nowrap; }
    .ahn-actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
    button, .ahn-download {
      border: 1px solid #9fb6d6;
      background: #fff;
      color: var(--ink);
      border-radius: 7px;
      min-height: 42px;
      padding: 9px 14px;
      font-size: 15px;
      font-weight: 700;
      text-decoration: none;
      cursor: pointer;
    }
    button.primary { border-color: var(--blue); background: var(--blue); color: #fff; }
    button:disabled, .ahn-download[aria-disabled="true"] {
      opacity: .48;
      cursor: not-allowed;
      pointer-events: none;
    }
    .ahn-main { padding: 18px 28px 28px; display: grid; gap: 16px; }
    .ahn-panel {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }
    .ahn-hidden-input { display: none; }
    .ahn-drop {
      border: 1px dashed #9fb6d6;
      border-radius: 8px;
      min-height: 150px;
      color: #476483;
      background: #f8fbff;
      text-align: center;
      padding: 18px;
      display: grid;
      place-items: center;
    }
    .ahn-drop.dragover { border-color: var(--blue); background: #eef6ff; }
    .ahn-drop-title {
      margin: 0 0 5px;
      color: var(--ink);
      font-size: 18px;
      font-weight: 800;
    }
    .ahn-drop-text { margin: 0; color: #476483; line-height: 1.45; }
    .ahn-bundle-actions {
      margin-top: 13px;
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: center;
      align-items: center;
    }
    .ahn-bundle-actions button { min-height: 38px; padding: 7px 12px; font-size: 14px; }
    .ahn-bundle-meta {
      margin-top: 10px;
      color: var(--muted);
      font-size: 13px;
      text-align: center;
    }
    .ahn-files {
      margin-top: 12px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .ahn-chip {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 6px 10px;
      background: #fff;
      color: #334155;
      font-size: 13px;
      max-width: 300px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .ahn-status-stack { display: grid; gap: 8px; }
    .ahn-status {
      min-height: 38px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 8px 10px 8px 12px;
      border-radius: 7px;
      background: #ecfdf5;
      border: 1px solid #bbf7d0;
      color: #166534;
      font-size: 14px;
      transition: opacity 180ms ease, transform 180ms ease;
    }
    .ahn-status.error { background: #fef2f2; border-color: #fecaca; color: #991b1b; }
    .ahn-status.is-hiding { opacity: 0; transform: translateY(-4px); }
    .ahn-status-text { min-width: 0; overflow-wrap: anywhere; }
    .ahn-status-close {
      min-height: 0;
      border: 0;
      background: transparent;
      color: currentColor;
      cursor: pointer;
      font-size: 17px;
      font-weight: 800;
      line-height: 1;
      padding: 2px 4px;
      opacity: .72;
    }
    .ahn-status-close:hover { opacity: 1; background: rgba(15, 23, 42, .06); }
    .ahn-progress {
      display: none;
      padding: 11px 14px 12px;
      border: 1px solid #bfdbfe;
      border-radius: 8px;
      background: #eff6ff;
      color: #1e3a8a;
      font-size: 13px;
    }
    .ahn-progress.is-visible {
      position: fixed;
      left: 50%;
      top: 50%;
      z-index: 70;
      display: block;
      width: min(560px, calc(100vw - 32px));
      transform: translate(-50%, -50%);
      box-shadow: 0 18px 48px rgba(15, 23, 42, 0.18);
    }
    .ahn-progress.is-error {
      border-color: #fecaca;
      background: #fef2f2;
      color: #991b1b;
    }
    .ahn-progress-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 7px;
      font-weight: 700;
    }
    .ahn-progress-track {
      overflow: hidden;
      height: 7px;
      border-radius: 999px;
      background: #dbeafe;
    }
    .ahn-progress-bar {
      width: 0%;
      height: 100%;
      border-radius: inherit;
      background: var(--blue);
      transition: width 240ms ease;
    }
    .ahn-progress.is-error .ahn-progress-bar { background: #dc2626; }
    .ahn-result {
      display: grid;
      gap: 14px;
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }
    .ahn-result[hidden] { display: none; }
    .ahn-summary {
      display: grid;
      grid-template-columns: repeat(4, minmax(120px, 1fr));
      gap: 10px;
    }
    .ahn-summary-item {
      border: 1px solid #d8e2ef;
      border-radius: 8px;
      padding: 12px;
      background: #fbfdff;
    }
    .ahn-summary-item span { display: block; color: var(--muted); font-size: 13px; }
    .ahn-summary-item strong { display: block; margin-top: 5px; font-size: 22px; color: var(--ink); }
    .ahn-downloads { display: flex; gap: 8px; flex-wrap: wrap; }
    .ahn-empty {
      min-height: 220px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--muted);
      font-weight: 700;
      text-align: center;
      border: 1px dashed var(--line);
      border-radius: 8px;
      background: #fff;
    }
    .ahn-busy {
      position: fixed;
      inset: 0;
      display: none;
      align-items: center;
      justify-content: center;
      background: rgba(255,255,255,.74);
      z-index: 50;
      color: var(--ink);
      font-weight: 700;
    }
    .ahn-busy.show { display: flex; }
    @media (max-width: 900px) {
      .ahn-topbar { align-items: flex-start; padding: 18px 16px; }
      .ahn-brand { display: block; }
      .ahn-brand h1 { font-size: 25px; }
      .ahn-brand span { display: block; margin-top: 4px; white-space: normal; }
      .ahn-main { padding: 14px 12px 24px; }
      .ahn-actions { width: 100%; justify-content: flex-end; }
      button, .ahn-download { min-height: 40px; padding: 8px 11px; font-size: 14px; }
      .ahn-summary { grid-template-columns: repeat(2, minmax(120px, 1fr)); }
    }
  </style>
</head>
<body>
  <div class="ahn-shell">
    <header class="ahn-topbar">
      <div class="ahn-brand">
        <h1>AHN TEM/STEM</h1>
        <span>folder upload · OCR · PowerPoint report</span>
      </div>
      <div class="ahn-actions">
        <button type="button" id="ahn-example">예제 불러오기</button>
        <button type="submit" form="ahn-form" class="primary" id="ahn-run">보고서 생성</button>
        <button type="button" id="ahn-clear">초기화</button>
      </div>
    </header>
    <main class="ahn-main">
      <section class="ahn-panel">
        <form id="ahn-form">
          <input class="ahn-hidden-input" type="file" id="ahn-bundle-files" name="files" multiple accept=".tif,.tiff,.png,.jpg,.jpeg,.bmp,.webp,.docx,.xlsx,.xls,.csv,.tsv,.zip">
          <input class="ahn-hidden-input" type="file" id="ahn-bundle-folder" name="files" multiple webkitdirectory directory>
          <div class="ahn-drop" id="ahn-drop">
            <div>
              <p class="ahn-drop-title">AHN raw bundle 추가</p>
              <p class="ahn-drop-text">tem, stem, report, scale 폴더를 포함한 raw 폴더를 여기에 드래그하거나 폴더째 선택하세요.</p>
              <div class="ahn-bundle-actions">
                <button type="button" id="ahn-add-files">파일 추가</button>
                <button type="button" id="ahn-add-folder">폴더 추가</button>
              </div>
              <div class="ahn-bundle-meta" id="ahn-bundle-meta">선택된 파일 없음</div>
            </div>
          </div>
          <div class="ahn-files" id="ahn-file-list"></div>
        </form>
      </section>
      <div class="ahn-status-stack" id="ahn-status" aria-live="polite"></div>
      <div class="ahn-progress" id="ahn-progress" aria-live="polite">
        <div class="ahn-progress-row">
          <span id="ahn-progress-label">보고서 생성 대기</span>
          <span id="ahn-progress-value">0%</span>
        </div>
        <div class="ahn-progress-track">
          <div class="ahn-progress-bar" id="ahn-progress-bar"></div>
        </div>
      </div>
      <section class="ahn-result" id="ahn-result" hidden>
        <div class="ahn-summary" id="ahn-summary"></div>
        <div class="ahn-downloads">
          <a href="#" class="ahn-download" id="ahn-download-pptx" aria-disabled="true">PPTX 다운로드</a>
          <a href="#" class="ahn-download" id="ahn-download-package" aria-disabled="true">보고서 ZIP 다운로드</a>
          <a href="#" class="ahn-download" id="ahn-download-json" aria-disabled="true">분석 JSON 다운로드</a>
        </div>
      </section>
      <section class="ahn-empty" id="ahn-empty">TEM/STEM/EDS/코팅층 raw 폴더를 올리면 PPT 보고서를 생성합니다.</section>
    </main>
  </div>
  <div class="ahn-busy" id="ahn-busy">AHN 보고서를 생성하는 중입니다.</div>
  <script>
  (function() {
    var form = document.getElementById("ahn-form");
    var bundleInput = document.getElementById("ahn-bundle-files");
    var folderInput = document.getElementById("ahn-bundle-folder");
    var addFilesButton = document.getElementById("ahn-add-files");
    var addFolderButton = document.getElementById("ahn-add-folder");
    var runButton = document.getElementById("ahn-run");
    var clearButton = document.getElementById("ahn-clear");
    var exampleButton = document.getElementById("ahn-example");
    var status = document.getElementById("ahn-status");
    var busy = document.getElementById("ahn-busy");
    var drop = document.getElementById("ahn-drop");
    var fileList = document.getElementById("ahn-file-list");
    var bundleMeta = document.getElementById("ahn-bundle-meta");
    var progress = document.getElementById("ahn-progress");
    var progressLabel = document.getElementById("ahn-progress-label");
    var progressValue = document.getElementById("ahn-progress-value");
    var progressBar = document.getElementById("ahn-progress-bar");
    var result = document.getElementById("ahn-result");
    var empty = document.getElementById("ahn-empty");
    var summary = document.getElementById("ahn-summary");
    var downloadPptx = document.getElementById("ahn-download-pptx");
    var downloadPackage = document.getElementById("ahn-download-package");
    var downloadJson = document.getElementById("ahn-download-json");
    var bundleItems = [];
    var progressTimer = null;

    function setStatus(message, error) {
      if (!message) return;
      var item = document.createElement("div");
      item.className = "ahn-status" + (error ? " error" : "");
      var text = document.createElement("span");
      text.className = "ahn-status-text";
      text.textContent = message;
      var close = document.createElement("button");
      close.type = "button";
      close.className = "ahn-status-close";
      close.setAttribute("aria-label", "알림 닫기");
      close.textContent = "×";
      item.appendChild(text);
      item.appendChild(close);
      status.appendChild(item);
      var timer = null;
      function remove() {
        if (timer) clearTimeout(timer);
        item.classList.add("is-hiding");
        setTimeout(function() {
          if (item.parentNode) item.parentNode.removeChild(item);
        }, 190);
      }
      close.addEventListener("click", remove);
      timer = setTimeout(remove, error ? 7200 : 4300);
    }
    function setBusy(value) {
      busy.classList.toggle("show", Boolean(value));
      runButton.disabled = Boolean(value);
      exampleButton.disabled = Boolean(value);
    }
    function progressMessage(percent) {
      if (percent < 25) return "raw 파일을 서버로 전송하는 중입니다.";
      if (percent < 48) return "TEM/STEM/EDS/코팅층 폴더를 분류하는 중입니다.";
      if (percent < 72) return "코팅층 OCR과 분석 JSON을 만드는 중입니다.";
      return "PowerPoint 보고서를 렌더링하는 중입니다.";
    }
    function setProgress(percent, message, visible, error) {
      var pct = Math.max(0, Math.min(100, Math.round(Number(percent) || 0)));
      progress.classList.toggle("is-visible", Boolean(visible));
      progress.classList.toggle("is-error", Boolean(error));
      progressLabel.textContent = message || "보고서 생성 중입니다.";
      progressValue.textContent = pct + "%";
      progressBar.style.width = pct + "%";
    }
    function stopProgressTimer() {
      if (progressTimer) {
        clearInterval(progressTimer);
        progressTimer = null;
      }
    }
    function startProgress(message) {
      stopProgressTimer();
      var pct = 6;
      setProgress(pct, message || progressMessage(pct), true, false);
      progressTimer = setInterval(function() {
        pct = Math.min(92, pct + (pct < 40 ? 4 : pct < 72 ? 3 : 1));
        setProgress(pct, progressMessage(pct), true, false);
        if (pct >= 92) stopProgressTimer();
      }, 650);
    }
    function finishProgress(message) {
      stopProgressTimer();
      setProgress(100, message || "보고서가 완성되었습니다.", true, false);
      setTimeout(function() {
        setProgress(0, "보고서 생성 대기", false, false);
      }, 900);
    }
    function failProgress(message) {
      stopProgressTimer();
      setProgress(100, message || "보고서 생성에 실패했습니다.", true, true);
      setTimeout(function() {
        setProgress(0, "보고서 생성 대기", false, false);
      }, 1800);
    }
    function filesOf(input) {
      return Array.prototype.slice.call(input.files || []);
    }
    function bundleItem(file, path) {
      return {file: file, path: path || file.webkitRelativePath || file.name};
    }
    function classifyFile(file) {
      var name = file.name.toLowerCase();
      if (/\\.(tif|tiff|png|jpe?g|bmp|webp)$/.test(name)) return "image";
      if (/\\.docx$/.test(name)) return "docx";
      if (/\\.(xlsx|xls|csv|tsv)$/.test(name)) return "table";
      if (/\\.zip$/.test(name)) return "zip";
      return "skip";
    }
    function classifySection(path) {
      var lowered = String(path || "").toLowerCase().split("/");
      if (lowered.indexOf("tem") >= 0) return "TEM";
      if (lowered.indexOf("stem") >= 0) return "STEM";
      if (lowered.indexOf("report") >= 0) return "EDS";
      if (lowered.indexOf("scale") >= 0) return "코팅층";
      return "기타";
    }
    function addBundleItems(items) {
      var seen = new Set(bundleItems.map(function(item) {
        return item.path + "|" + item.file.size + "|" + item.file.lastModified;
      }));
      items.forEach(function(item) {
        var key = item.path + "|" + item.file.size + "|" + item.file.lastModified;
        if (!seen.has(key)) {
          seen.add(key);
          bundleItems.push(item);
        }
      });
      renderFileList();
    }
    function renderFileList() {
      var counts = {TEM: 0, STEM: 0, EDS: 0, "코팅층": 0, "기타": 0};
      fileList.replaceChildren();
      bundleItems.forEach(function(item) {
        var section = classifySection(item.path);
        counts[section] = (counts[section] || 0) + 1;
        var chip = document.createElement("span");
        chip.className = "ahn-chip";
        chip.textContent = section + " · " + item.path;
        fileList.appendChild(chip);
      });
      bundleMeta.textContent = bundleItems.length
        ? "TEM " + counts.TEM + " · STEM " + counts.STEM + " · EDS " + counts.EDS + " · 코팅층 " + counts["코팅층"] + " · 기타 " + counts["기타"]
        : "선택된 파일 없음";
    }
    function fileInputItems(input) {
      return filesOf(input).map(function(file) {
        return bundleItem(file, file.webkitRelativePath || file.name);
      });
    }
    function readAllDirectoryEntries(reader) {
      return new Promise(function(resolve, reject) {
        var entries = [];
        function readBatch() {
          reader.readEntries(function(batch) {
            if (!batch.length) {
              resolve(entries);
              return;
            }
            entries = entries.concat(Array.prototype.slice.call(batch));
            readBatch();
          }, reject);
        }
        readBatch();
      });
    }
    function entryToBundleItems(entry, prefix) {
      prefix = prefix || "";
      if (entry.isFile) {
        return new Promise(function(resolve, reject) {
          entry.file(function(file) {
            resolve([bundleItem(file, prefix + file.name)]);
          }, reject);
        });
      }
      if (entry.isDirectory) {
        return readAllDirectoryEntries(entry.createReader()).then(function(entries) {
          return Promise.all(entries.map(function(child) {
            return entryToBundleItems(child, prefix + entry.name + "/");
          })).then(function(groups) {
            return groups.reduce(function(acc, group) { return acc.concat(group); }, []);
          });
        });
      }
      return Promise.resolve([]);
    }
    async function droppedBundleItems(dataTransfer) {
      var items = Array.prototype.slice.call(dataTransfer.items || []);
      var entries = items
        .filter(function(item) { return item.kind === "file" && item.webkitGetAsEntry; })
        .map(function(item) { return item.webkitGetAsEntry(); })
        .filter(Boolean);
      if (entries.length) {
        var groups = await Promise.all(entries.map(function(entry) {
          return entryToBundleItems(entry, "");
        }));
        return groups.reduce(function(acc, group) { return acc.concat(group); }, []);
      }
      return Array.prototype.slice.call(dataTransfer.files || []).map(function(file) {
        return bundleItem(file, file.webkitRelativePath || file.name);
      });
    }
    function buildBundleFormData() {
      var data = new FormData();
      bundleItems.forEach(function(item) {
        data.append("files", item.file, item.path || item.file.name);
      });
      return data;
    }
    function setDownload(link, url) {
      link.href = url || "#";
      link.setAttribute("aria-disabled", url ? "false" : "true");
    }
    function renderSummary(payload) {
      var data = payload.summary || {};
      var items = [
        ["TEM 이미지", data.temImageCount || 0],
        ["STEM 이미지", (data.stemImageCount || 0) + (data.stemBfImageCount || 0)],
        ["EDS 보고서", data.edsReportCount || 0],
        ["코팅층 이미지", data.coatingImageCount || 0]
      ];
      summary.replaceChildren();
      items.forEach(function(item) {
        var box = document.createElement("div");
        box.className = "ahn-summary-item";
        var label = document.createElement("span");
        label.textContent = item[0];
        var value = document.createElement("strong");
        value.textContent = item[1];
        box.appendChild(label);
        box.appendChild(value);
        summary.appendChild(box);
      });
      setDownload(downloadPptx, payload.downloads && payload.downloads.pptx);
      setDownload(downloadPackage, payload.downloads && payload.downloads.package);
      setDownload(downloadJson, payload.downloads && payload.downloads.analysisJson);
      result.hidden = false;
      empty.hidden = true;
    }
    async function requestReport(url, formData) {
      var options = formData ? {method: "POST", body: formData} : {method: "GET"};
      var response = await fetch(url, options);
      var text = await response.text();
      if (!response.ok) throw new Error(text || "보고서 생성 요청에 실패했습니다.");
      return JSON.parse(text);
    }
    bundleInput.addEventListener("change", function() {
      addBundleItems(fileInputItems(bundleInput));
      bundleInput.value = "";
    });
    folderInput.addEventListener("change", function() {
      addBundleItems(fileInputItems(folderInput));
      folderInput.value = "";
    });
    addFilesButton.addEventListener("click", function() { bundleInput.click(); });
    addFolderButton.addEventListener("click", function() { folderInput.click(); });
    drop.addEventListener("dragover", function(event) {
      event.preventDefault();
      drop.classList.add("dragover");
    });
    drop.addEventListener("dragleave", function() {
      drop.classList.remove("dragover");
    });
    drop.addEventListener("drop", async function(event) {
      event.preventDefault();
      drop.classList.remove("dragover");
      setBusy(true);
      try {
        addBundleItems(await droppedBundleItems(event.dataTransfer));
        setStatus("AHN raw bundle 파일이 추가되었습니다.", false);
      } catch (error) {
        setStatus(error.message || String(error), true);
      } finally {
        setBusy(false);
      }
    });
    clearButton.addEventListener("click", function() {
      form.reset();
      bundleItems = [];
      renderFileList();
      result.hidden = true;
      empty.hidden = false;
      setDownload(downloadPptx, null);
      setDownload(downloadPackage, null);
      setDownload(downloadJson, null);
      stopProgressTimer();
      setProgress(0, "보고서 생성 대기", false, false);
      setStatus("AHN raw 폴더를 선택하면 보고서를 생성할 수 있습니다.", false);
    });
    exampleButton.addEventListener("click", async function() {
      setBusy(true);
      startProgress("AHN 예제 보고서를 생성하는 중입니다.");
      try {
        var payload = await requestReport("/api/v1/ahn/example", null);
        renderSummary(payload);
        setStatus("AHN 예제 보고서가 생성되었습니다.", false);
        finishProgress("AHN 예제 보고서가 생성되었습니다.");
      } catch (error) {
        setStatus(error.message || String(error), true);
        failProgress(error.message || "예제 보고서 생성에 실패했습니다.");
      } finally {
        setBusy(false);
      }
    });
    form.addEventListener("submit", async function(event) {
      event.preventDefault();
      if (!bundleItems.length) {
        setStatus("AHN raw 폴더 또는 ZIP 파일을 먼저 추가하세요.", true);
        return;
      }
      setBusy(true);
      startProgress("AHN 보고서 생성 요청을 준비하는 중입니다.");
      try {
        var payload = await requestReport("/api/v1/ahn/analyze", buildBundleFormData());
        renderSummary(payload);
        setStatus("AHN 보고서가 생성되었습니다.", false);
        finishProgress("AHN 보고서가 생성되었습니다.");
      } catch (error) {
        setStatus(error.message || String(error), true);
        failProgress(error.message || "보고서 생성에 실패했습니다.");
      } finally {
        setBusy(false);
      }
    });
    renderFileList();
    setStatus("AHN raw 폴더를 선택하면 보고서를 생성할 수 있습니다.", false);
  })();
  </script>
</body>
</html>"""


@router.get("/ahn", response_class=HTMLResponse, include_in_schema=False)
def ahn_page() -> HTMLResponse:
    return HTMLResponse(build_ahn_page())


@router.post("/api/v1/ahn/analyze", response_class=JSONResponse, tags=["ahn"])
async def analyze_ahn(
    files: list[UploadFile] | None = File(default=None, alias="files"),
) -> JSONResponse:
    _cleanup_old_jobs()
    work_dir = Path(tempfile.mkdtemp(prefix="rist-ahn-web-"))
    try:
        upload_root = work_dir / "input"
        await _save_ahn_uploads(files, upload_root)
        input_root = _find_ahn_input_root(upload_root)
        job = _build_ahn_job(input_root, work_dir)
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    logger.info("AHN 웹 보고서 생성 완료 (job_id=%s)", job.job_id)
    return JSONResponse(_job_payload(job))


@router.get("/api/v1/ahn/example", response_class=JSONResponse, tags=["ahn"])
def ahn_example() -> JSONResponse:
    _cleanup_old_jobs()
    repo_root = Path(__file__).resolve().parents[2]
    input_root = repo_root / "ahn" / "data" / "TESTData"
    if not input_root.exists():
        raise ApiException(
            404,
            "AHN_EXAMPLE_NOT_FOUND",
            "AHN 예제 데이터를 찾을 수 없습니다.",
        )
    work_dir = Path(tempfile.mkdtemp(prefix="rist-ahn-example-"))
    try:
        job = _build_ahn_job(input_root, work_dir)
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    logger.info("AHN 예제 웹 보고서 생성 완료 (job_id=%s)", job.job_id)
    return JSONResponse(_job_payload(job))


@router.get("/api/v1/ahn/report/jobs/{job_id}/download/{kind}", tags=["ahn"])
def download_ahn_report(job_id: str, kind: str) -> FileResponse:
    _cleanup_old_jobs()
    job = _ahn_report_jobs.get(job_id)
    if job is None:
        raise ApiException(
            404,
            "AHN_REPORT_NOT_FOUND",
            "AHN 보고서 다운로드 정보를 찾을 수 없습니다. 보고서를 다시 생성하세요.",
        )
    if kind == "pptx":
        return FileResponse(
            job.pptx_path,
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            filename="ahn-report.pptx",
        )
    if kind == "package":
        return FileResponse(
            job.package_path,
            media_type="application/zip",
            filename="ahn-report-package.zip",
        )
    if kind == "analysis-json":
        return FileResponse(
            job.analysis_path,
            media_type="application/json",
            filename="analysis-result.json",
        )
    raise ApiException(404, "AHN_REPORT_FILE_NOT_FOUND", "지원하지 않는 AHN 보고서 파일입니다.")


def create_ahn_preview_app() -> FastAPI:
    app = FastAPI(title="RIST AHN Preview")
    app.add_exception_handler(ApiException, api_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.include_router(router)
    return app

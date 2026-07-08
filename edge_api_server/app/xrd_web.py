from __future__ import annotations

import html
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse
from rist_common import get_logger

from .path_bootstrap import add_project_package_paths

add_project_package_paths()

from lim.xrd_plot import (
    HEADER,
    IMAGE_EXTENSIONS,
    TABLE_EXTENSIONS,
    build_xrd_html,
)

from .errors import ApiException, api_exception_handler, validation_exception_handler
from .config import Settings
from .llm_client import LlmError, LocalLlmClient
from .report import annotator
from .report.builders import LlmSlotSpec

router = APIRouter()
logger = get_logger(__name__)

RAW_EXTENSIONS = {".txt", ".dat", ".xy", ".asc"}
PDF_EXTENSIONS = {".pdf"}
SUPPORTED_BUNDLE_EXTENSIONS = (
    RAW_EXTENSIONS | PDF_EXTENSIONS | TABLE_EXTENSIONS | IMAGE_EXTENSIONS
)
MAX_XRD_UPLOAD_BYTES = 80 * 1024 * 1024

_XRD_LLM_SYSTEM_PROMPT = (
    "당신은 XRD 분석 보고서 작성 보조자입니다.\n"
    "제공된 구조화 JSON만 근거로 한국어 문안을 작성하세요. "
    "제공되지 않은 물질명, 결정상, 원인, 수치를 추측하지 마세요.\n"
    "ICDD 후보상은 확정 동정이 아니라 후보 소견으로 표현하세요.\n"
    "PDF 폴더 구조에서 온 주요상/유사상/미량상 분류를 우선하고, raw 피크, ICDD 피크 대응, "
    "Peak list Excel, 첨부 이미지 정보를 함께 고려하세요.\n"
    "수식은 LaTeX/Markdown 수식 문법을 쓰지 말고 일반 텍스트로 쓰세요.\n"
    "출력은 반드시 JSON 객체 하나로만 작성하고, 키는 "
    "major_phases/similar_uncertain_phases/minor_phases/advisory 입니다.\n"
    "- major_phases: '본 [샘플명] XRD 분석 결과, ... 주요 상으로 존재하는 것으로 판단됩니다.' 형식의 2~3문장\n"
    "- similar_uncertain_phases: 유사상 후보와 구분 한계를 2~3문장\n"
    "- minor_phases: 미량상 후보와 근거를 1~2문장\n"
    "- advisory: 원소 정보(XRF/ICP/EDS) 공유 시 추가 검토 가능, 없으면 XRD 단독 해석 한계를 고정 안내문으로 2문장"
)


def _safe_name(filename: str | None, fallback: str) -> str:
    name = Path(filename or "").name.strip() or fallback
    name = re.sub(r"[^\w.\-() \[\]\u3131-\u318e\uac00-\ud7a3]+", "_", name)
    return name[:160] or fallback


def _safe_relative_path(filename: str | None, fallback: str) -> Path:
    raw = str(filename or "").strip().replace("\\", "/")
    parts = [part for part in raw.split("/") if part and part not in {".", ".."}]
    if not parts:
        parts = [fallback]
    safe_parts = []
    for index, part in enumerate(parts):
        clean = re.sub(r"[^\w.\-() \[\]\u3131-\u318e\uac00-\ud7a3]+", "_", part).strip()
        if not clean:
            clean = fallback if index == len(parts) - 1 else "folder"
        safe_parts.append(clean[:160])
    return Path(*safe_parts)


def _unique_path(directory: Path, filename: str) -> Path:
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    candidate = directory / filename
    index = 1
    while candidate.exists():
        candidate = directory / f"{stem}_{index}{suffix}"
        index += 1
    return candidate


def _has_pdf_files(directory: Path | str) -> bool:
    root = Path(directory)
    return any(
        path.is_file() and path.suffix.lower() in PDF_EXTENSIONS
        for path in root.rglob("*")
    )


def _request_settings(request: Request) -> Settings | None:
    settings = getattr(request.app.state, "settings", None)
    return settings if isinstance(settings, Settings) else None


def _html_lines(value: str) -> str:
    lines = [html.escape(line.strip()) for line in str(value or "").splitlines() if line.strip()]
    return "<br>".join(lines)


def _xrd_llm_comment_html(slots: dict[str, str]) -> str:
    labels = [
        ("major_phases", "1. 주요 상 (Major Phases)"),
        ("similar_uncertain_phases", "2. 유사 상 / 불확실 상 (Similar / Uncertain phases)"),
        ("minor_phases", "3. 미량 상 (Minor Phases)"),
        ("advisory", "안내 사항"),
    ]
    blocks = []
    for key, title in labels:
        text = slots.get(key, "").strip()
        if not text:
            continue
        blocks.append(f"<p><strong>{html.escape(title)}</strong><br>{_html_lines(text)}</p>")
    return "\n".join(blocks)


def _xrd_llm_fallback(context: dict[str, Any]) -> dict[str, str]:
    counts = context.get("phase_category_counts") if isinstance(context.get("phase_category_counts"), dict) else {}
    sample_name = str(context.get("sample_name") or "XRD 시료")
    raw_patterns = context.get("raw_patterns") if isinstance(context.get("raw_patterns"), list) else []
    raw_peak_count = sum(
        len(item.get("detected_raw_peaks") or [])
        for item in raw_patterns
        if isinstance(item, dict)
    )
    major = int(counts.get("major") or 0)
    uncertain = int(counts.get("uncertain") or 0)
    minor = int(counts.get("minor") or 0)
    return {
        "major_phases": (
            f"본 {sample_name} XRD 분석 결과, 주요 상 후보 {major}건이 정리되었습니다. "
            f"raw 피크 {raw_peak_count}개와 ICDD 카드 피크의 위치 대응을 기준으로 주요 상 존재 가능성을 검토했습니다."
        ),
        "similar_uncertain_phases": (
            f"유사 상/불확실 상 후보는 {uncertain}건입니다. "
            "주요 피크 위치가 일부 겹치는 후보는 현재 XRD 데이터만으로 명확히 구분하기 어렵습니다."
        ),
        "minor_phases": (
            f"미량 상 후보는 {minor}건입니다. 피크 대응이 제한적인 후보는 미약한 피크 또는 배경 후보로 검토해야 합니다."
        ),
        "advisory": (
            "유사 상 구분 및 불순물/미량 상 확인을 위해 시료에 포함될 수 있는 주요 원소 정보(XRF/ICP/EDS)를 공유해주시면 "
            "상 후보를 원소 제약 조건으로 추가 검토할 수 있습니다.\n"
            "원소 정보가 없을 경우 XRD 결과만으로는 결정학적으로 유사한 상 구분 및 미량상 확인에 한계가 있어 "
            "원소 성분 분석을 권장드립니다."
        ),
    }


def _generate_xrd_llm_comment(
    settings: Settings,
    context: dict[str, Any],
    *,
    processed_dir: Path,
    logs_dir: Path,
) -> dict[str, str] | None:
    spec = LlmSlotSpec(
        system_prompt=_XRD_LLM_SYSTEM_PROMPT,
        facts=context,
        requested_slots=[
            "major_phases",
            "similar_uncertain_phases",
            "minor_phases",
            "advisory",
        ],
        fallback=_xrd_llm_fallback(context),
    )
    try:
        with LocalLlmClient(
            settings.llm_base_url,
            settings.llm_model,
            settings.llm_timeout_seconds,
            settings.llm_temperature,
            settings.llm_max_tokens,
            settings.llm_validate_model,
        ) as llm_client:
            slots = annotator.annotate(
                settings,
                llm_client,
                spec,
                processed_dir=processed_dir,
                logs_dir=logs_dir,
            )
    except LlmError as exc:
        logger.warning(
            "XRD LLM 자동 해석 실패 (code=%s) - 규칙 기반 초안 사용",
            exc.code,
        )
        return None
    comment_html = _xrd_llm_comment_html({**spec.fallback, **slots})
    if not comment_html:
        return None
    return {
        "html": comment_html,
        "note": "LLM이 raw 피크, ICDD 후보상, 첨부 표/이미지 JSON을 근거로 작성한 자동 해석 초안입니다.",
    }


def _xrd_comment_provider(
    settings: Settings | None,
    *,
    processed_dir: Path,
    logs_dir: Path,
):
    if settings is None:
        return None

    def provider(context: dict[str, Any]) -> dict[str, str] | None:
        return _generate_xrd_llm_comment(
            settings,
            context,
            processed_dir=processed_dir,
            logs_dir=logs_dir,
        )

    return provider


async def _save_uploads(
    files: list[UploadFile] | None,
    directory: Path,
    *,
    allowed_extensions: set[str],
    field_name: str,
    required: bool = False,
) -> list[str]:
    if not files:
        if required:
            raise ApiException(
                400,
                "MISSING_XRD_INPUT",
                f"{field_name} 파일이 필요합니다.",
            )
        return []

    directory.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for index, upload in enumerate(files, start=1):
        filename = _safe_name(upload.filename, f"{field_name}-{index}")
        suffix = Path(filename).suffix.lower()
        if suffix not in allowed_extensions:
            allowed = ", ".join(sorted(allowed_extensions))
            raise ApiException(
                400,
                "INVALID_XRD_FILE_TYPE",
                f"{filename} 형식은 지원하지 않습니다. 허용 형식: {allowed}",
            )
        data = await upload.read()
        if len(data) > MAX_XRD_UPLOAD_BYTES:
            raise ApiException(
                413,
                "XRD_FILE_TOO_LARGE",
                f"{filename} 파일이 너무 큽니다. 파일당 최대 80MB입니다.",
            )
        path = _unique_path(directory, filename)
        path.write_bytes(data)
        saved.append(str(path))
    return saved


async def _save_xrd_bundle_uploads(
    files: list[UploadFile] | None,
    root: Path,
) -> tuple[list[str], str, list[str], list[str]]:
    raw_paths: list[str] = []
    table_paths: list[str] = []
    image_paths: list[str] = []
    pdf_dir = root / "pdf"
    directories = {
        "raw": root / "raw",
        "pdf": pdf_dir,
        "table": root / "tables",
        "image": root / "images",
    }
    for directory in directories.values():
        directory.mkdir(parents=True, exist_ok=True)

    unsupported: list[str] = []
    for index, upload in enumerate(files or [], start=1):
        relative_path = _safe_relative_path(upload.filename, f"bundle-{index}")
        filename = relative_path.name
        suffix = relative_path.suffix.lower()
        if not suffix and filename.startswith("."):
            continue
        if suffix not in SUPPORTED_BUNDLE_EXTENSIONS:
            unsupported.append(str(relative_path))
            continue

        data = await upload.read()
        if len(data) > MAX_XRD_UPLOAD_BYTES:
            raise ApiException(
                413,
                "XRD_FILE_TOO_LARGE",
                f"{filename} 파일이 너무 큽니다. 파일당 최대 80MB입니다.",
            )

        if suffix in RAW_EXTENSIONS:
            path = _unique_path(directories["raw"], filename)
            raw_paths.append(str(path))
        elif suffix in PDF_EXTENSIONS:
            pdf_parent = directories["pdf"] / relative_path.parent
            pdf_parent.mkdir(parents=True, exist_ok=True)
            path = _unique_path(pdf_parent, filename)
        elif suffix in TABLE_EXTENSIONS:
            path = _unique_path(directories["table"], filename)
            table_paths.append(str(path))
        else:
            path = _unique_path(directories["image"], filename)
            image_paths.append(str(path))
        path.write_bytes(data)

    if unsupported:
        preview = ", ".join(unsupported[:5])
        more = f" 외 {len(unsupported) - 5}개" if len(unsupported) > 5 else ""
        allowed = ", ".join(sorted(SUPPORTED_BUNDLE_EXTENSIONS))
        raise ApiException(
            400,
            "INVALID_XRD_FILE_TYPE",
            f"지원하지 않는 파일이 포함되어 있습니다: {preview}{more}. 허용 형식: {allowed}",
        )

    return raw_paths, str(pdf_dir), table_paths, image_paths


def build_xrd_page() -> str:
    return """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RIST XRD Preview</title>
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
    .xrd-shell { min-height: 100vh; display: flex; flex-direction: column; }
    .xrd-topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 24px 28px;
      border-bottom: 1px solid var(--line);
      background: #fff;
    }
    .xrd-brand { display: flex; align-items: baseline; gap: 14px; min-width: 0; }
    .xrd-brand h1 { margin: 0; font-size: 30px; letter-spacing: 0; }
    .xrd-brand span { color: var(--muted); font-size: 17px; white-space: nowrap; }
    .xrd-actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
    button, .xrd-download {
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
    button:disabled, .xrd-download[aria-disabled="true"] {
      opacity: .48;
      cursor: not-allowed;
      pointer-events: none;
    }
    .xrd-main { padding: 18px 28px 28px; display: grid; gap: 16px; }
    .xrd-panel {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }
    .xrd-hidden-input { display: none; }
    .xrd-check {
      display: flex;
      gap: 8px;
      align-items: center;
      min-height: 42px;
      font-weight: 700;
    }
    .xrd-drop {
      border: 1px dashed #9fb6d6;
      border-radius: 8px;
      min-height: 122px;
      color: #476483;
      background: #f8fbff;
      text-align: center;
      padding: 18px;
      display: grid;
      place-items: center;
    }
    .xrd-drop.dragover { border-color: var(--blue); background: #eef6ff; }
    .xrd-drop-title {
      margin: 0 0 5px;
      color: var(--ink);
      font-size: 18px;
      font-weight: 800;
    }
    .xrd-drop-text { margin: 0; color: #476483; line-height: 1.45; }
    .xrd-bundle-actions {
      margin-top: 13px;
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: center;
      align-items: center;
    }
    .xrd-bundle-actions button { min-height: 38px; padding: 7px 12px; font-size: 14px; }
    .xrd-bundle-meta {
      margin-top: 10px;
      color: var(--muted);
      font-size: 13px;
      text-align: center;
    }
    .xrd-files {
      margin-top: 12px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .xrd-chip {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 6px 10px;
      background: #fff;
      color: #334155;
      font-size: 13px;
      max-width: 280px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .xrd-status-stack {
      display: grid;
      gap: 8px;
    }
    .xrd-status {
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
    .xrd-status.error { background: #fef2f2; border-color: #fecaca; color: #991b1b; }
    .xrd-status.is-hiding { opacity: 0; transform: translateY(-4px); }
    .xrd-status-text { min-width: 0; overflow-wrap: anywhere; }
    .xrd-status-close {
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
    .xrd-status-close:hover { opacity: 1; background: rgba(15, 23, 42, .06); }
    .xrd-report-progress {
      display: none;
      padding: 11px 14px 12px;
      border: 1px solid #bfdbfe;
      border-radius: 8px;
      background: #eff6ff;
      color: #1e3a8a;
      font-size: 13px;
    }
    .xrd-report-progress.is-visible {
      position: fixed;
      left: 50%;
      top: 50%;
      z-index: 70;
      display: block;
      width: min(560px, calc(100vw - 32px));
      transform: translate(-50%, -50%);
      box-shadow: 0 18px 48px rgba(15, 23, 42, 0.18);
    }
    .xrd-report-progress.is-error {
      border-color: #fecaca;
      background: #fef2f2;
      color: #991b1b;
    }
    .xrd-report-progress-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 7px;
      font-weight: 700;
    }
    .xrd-report-progress-track {
      overflow: hidden;
      height: 7px;
      border-radius: 999px;
      background: #dbeafe;
    }
    .xrd-report-progress-bar {
      width: 0%;
      height: 100%;
      border-radius: inherit;
      background: var(--blue);
      transition: width 240ms ease;
    }
    .xrd-report-progress.is-error .xrd-report-progress-bar { background: #dc2626; }
    .xrd-preview {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      min-height: 720px;
    }
    .xrd-preview iframe {
      width: 100%;
      height: 920px;
      border: 0;
      display: block;
      background: #fff;
    }
    .xrd-empty {
      height: 720px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--muted);
      font-weight: 700;
    }
    .xrd-busy {
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
    .xrd-busy.show { display: flex; }
    @media (max-width: 900px) {
      .xrd-topbar { align-items: flex-start; padding: 18px 16px; }
      .xrd-brand { display: block; }
      .xrd-brand h1 { font-size: 25px; }
      .xrd-brand span { display: block; margin-top: 4px; white-space: normal; }
      .xrd-main { padding: 14px 12px 24px; }
      .xrd-actions { width: 100%; justify-content: flex-end; }
      button, .xrd-download { min-height: 40px; padding: 8px 11px; font-size: 14px; }
      .xrd-preview iframe { height: 780px; }
    }
  </style>
</head>
<body>
  <div class="xrd-shell">
    <header class="xrd-topbar">
      <div class="xrd-brand">
        <h1>LIM XRD</h1>
        <span>raw upload · ICDD peak overlay · report preview</span>
      </div>
      <div class="xrd-actions">
        <button type="button" id="xrd-example">예제 불러오기</button>
        <button type="submit" form="xrd-form" class="primary" id="xrd-run">보고서 생성</button>
        <button type="button" id="xrd-clear">초기화</button>
        <a href="#" class="xrd-download" id="xrd-download" aria-disabled="true">보고서 다운로드</a>
      </div>
    </header>
    <main class="xrd-main">
      <section class="xrd-panel">
        <form id="xrd-form">
          <input class="xrd-hidden-input" type="file" id="xrd-bundle-files" name="files" multiple accept=".txt,.dat,.xy,.asc,.pdf,.xlsx,.csv,.tsv,.png,.jpg,.jpeg,.webp,.gif">
          <input class="xrd-hidden-input" type="file" id="xrd-bundle-folder" name="files" multiple webkitdirectory directory>
          <div class="xrd-drop" id="xrd-drop">
            <div>
              <p class="xrd-drop-title">XRD 번들 추가</p>
              <p class="xrd-drop-text">raw TXT, ICDD PDF 폴더, Excel/CSV, 이미지를 여기에 한꺼번에 드래그하세요.</p>
              <div class="xrd-bundle-actions">
                <button type="button" id="xrd-add-files">파일 추가</button>
                <button type="button" id="xrd-add-folder">폴더 추가</button>
              </div>
              <div class="xrd-bundle-meta" id="xrd-bundle-meta">선택된 파일 없음</div>
            </div>
          </div>
          <label class="xrd-check"><input type="checkbox" id="xrd-origin" name="origin" value="true" checked> Origin 스타일</label>
          <div class="xrd-files" id="xrd-file-list"></div>
        </form>
      </section>
      <div class="xrd-status-stack" id="xrd-status" aria-live="polite"></div>
      <div class="xrd-report-progress" id="xrd-report-progress" aria-live="polite">
        <div class="xrd-report-progress-row">
          <span id="xrd-report-progress-label">보고서 생성 대기</span>
          <span id="xrd-report-progress-value">0%</span>
        </div>
        <div class="xrd-report-progress-track">
          <div class="xrd-report-progress-bar" id="xrd-report-progress-bar"></div>
        </div>
      </div>
      <section class="xrd-preview" id="xrd-preview">
        <div class="xrd-empty" id="xrd-empty">미리보기 대기 중</div>
      </section>
    </main>
  </div>
  <div class="xrd-busy" id="xrd-busy">보고서를 생성하는 중입니다.</div>
  <script>
  (function() {
    var form = document.getElementById("xrd-form");
    var bundleInput = document.getElementById("xrd-bundle-files");
    var folderInput = document.getElementById("xrd-bundle-folder");
    var addFilesButton = document.getElementById("xrd-add-files");
    var addFolderButton = document.getElementById("xrd-add-folder");
    var runButton = document.getElementById("xrd-run");
    var clearButton = document.getElementById("xrd-clear");
    var exampleButton = document.getElementById("xrd-example");
    var downloadLink = document.getElementById("xrd-download");
    var preview = document.getElementById("xrd-preview");
    var empty = document.getElementById("xrd-empty");
    var status = document.getElementById("xrd-status");
    var busy = document.getElementById("xrd-busy");
    var drop = document.getElementById("xrd-drop");
    var fileList = document.getElementById("xrd-file-list");
    var bundleMeta = document.getElementById("xrd-bundle-meta");
    var reportProgress = document.getElementById("xrd-report-progress");
    var reportProgressLabel = document.getElementById("xrd-report-progress-label");
    var reportProgressValue = document.getElementById("xrd-report-progress-value");
    var reportProgressBar = document.getElementById("xrd-report-progress-bar");
    var downloadUrl = null;
    var bundleItems = [];
    var reportFrame = null;
    var reportProgressTimer = null;

    function setStatus(message, error) {
      if (!message) return;
      var item = document.createElement("div");
      item.className = "xrd-status" + (error ? " error" : "");
      var text = document.createElement("span");
      text.className = "xrd-status-text";
      text.textContent = message;
      var close = document.createElement("button");
      close.type = "button";
      close.className = "xrd-status-close";
      close.setAttribute("aria-label", "알림 닫기");
      close.textContent = "×";
      item.appendChild(text);
      item.appendChild(close);
      status.appendChild(item);
      var timer = null;
      function remove() {
        if (timer) {
          clearTimeout(timer);
          timer = null;
        }
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
    function setReportProgress(percent, message, visible, error) {
      var pct = Math.max(0, Math.min(100, Math.round(Number(percent) || 0)));
      reportProgress.classList.toggle("is-visible", Boolean(visible));
      reportProgress.classList.toggle("is-error", Boolean(error));
      reportProgressLabel.textContent = message || "보고서 생성 중입니다.";
      reportProgressValue.textContent = pct + "%";
      reportProgressBar.style.width = pct + "%";
    }
    function stopReportProgressTimer() {
      if (reportProgressTimer) {
        clearInterval(reportProgressTimer);
        reportProgressTimer = null;
      }
    }
    function progressMessage(percent) {
      if (percent < 28) return "업로드 파일을 서버로 전송하는 중입니다.";
      if (percent < 52) return "raw와 ICDD Card 데이터를 분석하는 중입니다.";
      if (percent < 76) return "그래프와 후보상 정보를 구성하는 중입니다.";
      return "HTML 보고서를 렌더링하는 중입니다.";
    }
    function startReportProgress(message) {
      stopReportProgressTimer();
      var pct = 6;
      setReportProgress(pct, message || progressMessage(pct), true, false);
      reportProgressTimer = setInterval(function() {
        pct = Math.min(92, pct + (pct < 40 ? 4 : pct < 72 ? 3 : 1));
        setReportProgress(pct, progressMessage(pct), true, false);
        if (pct >= 92) stopReportProgressTimer();
      }, 650);
    }
    function finishReportProgress(message) {
      stopReportProgressTimer();
      setReportProgress(100, message || "보고서가 완성되었습니다.", true, false);
      setTimeout(function() {
        setReportProgress(0, "보고서 생성 대기", false, false);
      }, 900);
    }
    function failReportProgress(message) {
      stopReportProgressTimer();
      setReportProgress(100, message || "보고서 생성에 실패했습니다.", true, true);
      setTimeout(function() {
        setReportProgress(0, "보고서 생성 대기", false, false);
      }, 1800);
    }
    function revokeDownload() {
      if (downloadUrl) URL.revokeObjectURL(downloadUrl);
      downloadUrl = null;
      downloadLink.href = "#";
      downloadLink.setAttribute("aria-disabled", "true");
    }
    function readOnlyReportPatch() {
      return [
        "<script>",
        "window.readOnlyReport=true;",
        "(function(){",
        "function ready(fn){if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',fn,{once:true});}else{fn();}}",
        "function inGraph(target){return !!(target&&target.closest&&target.closest('#xrd-plot'));}",
        "function editableTarget(target){return !!(target&&target.closest&&target.closest('input,textarea,select,[contenteditable=true]'));}",
        "ready(function(){",
        "document.documentElement.setAttribute('data-read-only-report','true');",
        "document.querySelectorAll('[contenteditable]').forEach(function(node){if(!inGraph(node))node.setAttribute('contenteditable','false');});",
        "document.querySelectorAll('input,textarea,select').forEach(function(node){if(inGraph(node))return;if(node.tagName==='SELECT'){node.disabled=true;}else{node.readOnly=true;}});",
        "['beforeinput','paste','drop','keydown'].forEach(function(name){document.addEventListener(name,function(ev){if(inGraph(ev.target))return;if(!editableTarget(ev.target))return;ev.preventDefault();ev.stopPropagation();},true);});",
        "});",
        "})();",
        "</scr" + "ipt>"
      ].join("");
    }
    function makeReadOnlyDownloadHtml(htmlText) {
      if (!htmlText || htmlText.indexOf("window.readOnlyReport=true") >= 0) return htmlText;
      var patch = readOnlyReportPatch();
      if (htmlText.indexOf("</body>") >= 0) {
        return htmlText.replace("</body>", patch + "</body>");
      }
      return htmlText + patch;
    }
    function setDownload(htmlText) {
      revokeDownload();
      var downloadHtml = makeReadOnlyDownloadHtml(htmlText);
      downloadUrl = URL.createObjectURL(new Blob([downloadHtml], {type: "text/html;charset=utf-8"}));
      downloadLink.href = downloadUrl;
      downloadLink.download = "xrd-report.html";
      downloadLink.setAttribute("aria-disabled", "false");
    }
    function showHtml(htmlText) {
      empty.style.display = "none";
      var iframe = document.createElement("iframe");
      iframe.setAttribute("title", "XRD report preview");
      iframe.srcdoc = htmlText;
      preview.replaceChildren(iframe);
      reportFrame = iframe;
      setDownload(htmlText);
    }
    function filesOf(input) {
      return Array.prototype.slice.call(input.files || []);
    }
    function bundleItem(file, path) {
      return {file: file, path: path || file.webkitRelativePath || file.name};
    }
    function classifyFile(file) {
      var name = file.name.toLowerCase();
      if (/\\.(txt|dat|xy|asc)$/.test(name)) return "raw";
      if (/\\.pdf$/.test(name)) return "pdf";
      if (/\\.(xlsx|csv|tsv)$/.test(name)) return "table";
      if (/\\.(png|jpe?g|webp|gif)$/.test(name)) return "image";
      return "skip";
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
      var counts = {raw: 0, pdf: 0, table: 0, image: 0, skip: 0};
      var files = bundleItems.map(function(item) {
        var type = classifyFile(item.file);
        counts[type] = (counts[type] || 0) + 1;
        return [type, item.path];
      });
      fileList.replaceChildren();
      files.forEach(function(item) {
        var chip = document.createElement("span");
        chip.className = "xrd-chip";
        chip.textContent = item[0] + " · " + item[1];
        fileList.appendChild(chip);
      });
      bundleMeta.textContent = files.length
        ? "raw " + counts.raw + " · pdf " + counts.pdf + " · table " + counts.table + " · image " + counts.image
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
    async function routeDroppedFiles(dataTransfer) {
      var items = await droppedBundleItems(dataTransfer);
      addBundleItems(items);
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
    function buildBundleFormData() {
      var data = new FormData();
      bundleItems.forEach(function(item) {
        data.append("files", item.file, item.path || item.file.name);
      });
      if (document.getElementById("xrd-origin").checked) {
        data.append("origin", "true");
      }
      return data;
    }
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
        await routeDroppedFiles(event.dataTransfer);
        setStatus("XRD bundle 파일이 추가되었습니다.", false);
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
      reportFrame = null;
      revokeDownload();
      preview.replaceChildren(empty);
      empty.style.display = "flex";
      setStatus("XRD 파일을 선택하면 보고서를 생성할 수 있습니다.", false);
      stopReportProgressTimer();
      setReportProgress(0, "보고서 생성 대기", false, false);
    });
    exampleButton.addEventListener("click", async function() {
      setBusy(true);
      startReportProgress("예제 보고서를 불러오는 중입니다.");
      try {
        var response = await fetch("/api/v1/xrd/example");
        var text = await response.text();
        if (!response.ok) throw new Error(text || "예제 보고서를 불러오지 못했습니다.");
        setReportProgress(94, "예제 보고서 화면을 준비하는 중입니다.", true, false);
        showHtml(text);
        setStatus("예제 보고서를 불러왔습니다.", false);
        finishReportProgress("예제 보고서가 준비되었습니다.");
      } catch (error) {
        setStatus(error.message || String(error), true);
        failReportProgress(error.message || "예제 보고서를 불러오지 못했습니다.");
      } finally {
        setBusy(false);
      }
    });
    form.addEventListener("submit", async function(event) {
      event.preventDefault();
      if (!bundleItems.some(function(item) { return classifyFile(item.file) === "raw"; })) {
        setStatus("Bundle 안에 raw TXT 파일이 필요합니다.", true);
        return;
      }
      if (!bundleItems.some(function(item) { return classifyFile(item.file) === "pdf"; })) {
        setStatus("Bundle 안에 ICDD PDF 파일이 필요합니다.", true);
        return;
      }
      setBusy(true);
      startReportProgress("보고서 생성 요청을 준비하는 중입니다.");
      try {
        var data = buildBundleFormData();
        var response = await fetch("/api/v1/xrd/analyze", {method: "POST", body: data});
        var text = await response.text();
        if (!response.ok) throw new Error(text || "보고서 생성 요청에 실패했습니다.");
        setReportProgress(94, "보고서 화면을 준비하는 중입니다.", true, false);
        showHtml(text);
        setStatus("XRD 보고서가 생성되었습니다.", false);
        finishReportProgress("XRD 보고서가 생성되었습니다.");
      } catch (error) {
        setStatus(error.message || String(error), true);
        failReportProgress(error.message || "보고서 생성에 실패했습니다.");
      } finally {
        setBusy(false);
      }
    });
    renderFileList();
    setStatus("XRD 파일을 선택하면 보고서를 생성할 수 있습니다.", false);
  })();
  </script>
</body>
</html>"""


@router.get("/xrd", response_class=HTMLResponse, include_in_schema=False)
def xrd_page() -> HTMLResponse:
    return HTMLResponse(build_xrd_page())


@router.post("/api/v1/xrd/analyze", response_class=HTMLResponse, tags=["xrd"])
async def analyze_xrd(
    request: Request,
    files: list[UploadFile] | None = File(default=None, alias="files"),
    raw_files: list[UploadFile] | None = File(default=None, alias="rawFiles"),
    pdf_files: list[UploadFile] | None = File(default=None, alias="pdfFiles"),
    table_files: list[UploadFile] | None = File(default=None, alias="tableFiles"),
    image_files: list[UploadFile] | None = File(default=None, alias="imageFiles"),
    origin: bool = Form(True),
) -> HTMLResponse:
    with tempfile.TemporaryDirectory(prefix="rist-xrd-web-") as tmp:
        root = Path(tmp)
        raw_paths, pdf_dir, table_paths, image_paths = await _save_xrd_bundle_uploads(
            files,
            root,
        )
        raw_paths.extend(
            await _save_uploads(
                raw_files,
                root / "raw",
                allowed_extensions=RAW_EXTENSIONS,
                field_name="raw",
            )
        )
        await _save_uploads(
            pdf_files,
            Path(pdf_dir),
            allowed_extensions=PDF_EXTENSIONS,
            field_name="pdf",
        )
        table_paths.extend(
            await _save_uploads(
                table_files,
                root / "tables",
                allowed_extensions=TABLE_EXTENSIONS,
                field_name="table",
            )
        )
        image_paths.extend(
            await _save_uploads(
                image_files,
                root / "images",
                allowed_extensions=IMAGE_EXTENSIONS,
                field_name="image",
            )
        )
        if not raw_paths:
            raise ApiException(
                400,
                "MISSING_XRD_INPUT",
                "Bundle 안에 raw TXT 파일이 필요합니다.",
            )
        if not _has_pdf_files(pdf_dir):
            raise ApiException(
                400,
                "MISSING_XRD_PDF",
                "Bundle 안에 ICDD PDF 파일이 필요합니다.",
            )
        result = build_xrd_html(
            [(path, pdf_dir) for path in raw_paths],
            table_files=table_paths,
            image_files=image_paths,
            origin=origin,
            comment_provider=_xrd_comment_provider(
                _request_settings(request),
                processed_dir=root / "images",
                logs_dir=root / "logs",
            ),
        )
    return HTMLResponse(result["html"])


def _write_synthetic_xrd_raw(path: Path) -> None:
    rows = []
    for index in range(701):
        two_theta = 10.0 + index * 0.1
        intensity = 80.0
        for center, height, width in (
            (25.3, 1050.0, 0.18),
            (37.8, 420.0, 0.22),
            (48.1, 650.0, 0.20),
            (54.0, 260.0, 0.24),
            (62.7, 300.0, 0.28),
        ):
            intensity += height * math.exp(-((two_theta - center) ** 2) / (2 * width**2))
        rows.append(f"{two_theta:.3f} {intensity:.3f}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _register_xrd_example_pdf_font() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    candidates = [
        os.environ.get("RIST_PDF_FONT_PATH", ""),
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for index, candidate in enumerate(candidates):
        if not candidate:
            continue
        font_path = Path(candidate)
        if not font_path.is_file():
            continue
        font_name = f"XrdExampleFont{index}"
        try:
            pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
        except Exception:
            continue
        return font_name
    return "Helvetica"


def _write_synthetic_icdd_pdf(path: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    font_name = _register_xrd_example_pdf_font()
    styles = getSampleStyleSheet()
    for style in styles.byName.values():
        style.fontName = font_name

    rows = [
        HEADER,
        ["1", "25.300", "3.516", "100.00", "1 0 1"],
        ["2", "37.800", "2.379", "42.00", "0 0 4"],
        ["3", "48.100", "1.890", "65.00", "2 0 0"],
        ["4", "54.000", "1.697", "26.00", "1 0 5"],
        ["5", "62.700", "1.480", "30.00", "2 1 1"],
    ]
    table = Table(rows, colWidths=[45, 74, 74, 74, 74], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), font_name),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5e7eb")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )

    document = SimpleDocTemplate(str(path), pagesize=letter)
    story = [
        Paragraph("PDF Card No. : 00-000-0001 QM: S", styles["Normal"]),
        Paragraph("Chemical formula: Ti O2", styles["Normal"]),
        Paragraph("Name: Synthetic Anatase Example I/Ic 1.00", styles["Normal"]),
        Paragraph("Crystal system: Tetragonal Space group: 141 : I41/amd", styles["Normal"]),
        Paragraph("2θ range: 10.00000 - 80.00000", styles["Normal"]),
        Spacer(1, 12),
        table,
    ]
    document.build(story)


def _write_synthetic_icdd_pdf_dir(pdf_dir: Path) -> None:
    pdf_dir.mkdir(parents=True, exist_ok=True)
    _write_synthetic_icdd_pdf(pdf_dir / "Synthetic Anatase 00-000-0001(S).pdf")


def _xrd_example_candidates(repo_root: Path) -> list[tuple[Path, Path, list[Path], list[Path]]]:
    examples_with_peak_list = (
        repo_root
        / "lim"
        / "data"
        / "AX 예제 데이터 (XRD, Peak list 엑셀 포함)"
    )

    candidates: list[tuple[Path, Path, list[Path], list[Path]]] = []
    if examples_with_peak_list.is_dir():
        for directory in sorted(examples_with_peak_list.iterdir()):
            if not directory.is_dir():
                continue
            raw_files = sorted(directory.glob("*.txt"))
            pdf_dirs = [
                child for child in sorted(directory.iterdir())
                if child.is_dir() and "ICDD" in child.name
            ]
            if not raw_files or not pdf_dirs:
                continue
            table_files = sorted(directory.glob("*.csv")) + sorted(directory.glob("*.xlsx"))
            image_files = sorted(directory.glob("*.png")) + sorted(directory.glob("*.jpg"))
            candidates.append((raw_files[0], pdf_dirs[0], table_files, image_files))

    candidates.extend(
        [
            (
                repo_root / "lim" / "data" / "data_dir" / "Mix2.txt",
                repo_root / "lim" / "data" / "data_dir" / "Mix2",
                [],
                [],
            ),
            (
                repo_root / "lim" / "data" / "data_dir" / "Mix3.txt",
                repo_root / "lim" / "data" / "data_dir" / "Mix3",
                [],
                [],
            ),
            (
                repo_root
                / "lim"
                / "data"
                / "예제 데이터(AX - XRD)"
                / "예제 데이터 1"
                / "Mix2.txt",
                repo_root
                / "lim"
                / "data"
                / "예제 데이터(AX - XRD)"
                / "예제 데이터 1"
                / "Mix2",
                [],
                [],
            ),
        ]
    )
    return candidates


def _build_xrd_example_html(repo_root: Path, *, settings: Settings | None = None) -> str:
    for raw_path, pdf_dir, table_files, image_files in _xrd_example_candidates(repo_root):
        if raw_path.is_file() and pdf_dir.is_dir():
            with tempfile.TemporaryDirectory(prefix="rist-xrd-example-") as tmp:
                root = Path(tmp)
                return build_xrd_html(
                    [(str(raw_path), str(pdf_dir))],
                    table_files=[str(path) for path in table_files],
                    image_files=[str(path) for path in image_files],
                    origin=True,
                    comment_provider=_xrd_comment_provider(
                        settings,
                        processed_dir=root / "images",
                        logs_dir=root / "logs",
                    ),
                )["html"]
        if raw_path.is_file():
            with tempfile.TemporaryDirectory(prefix="rist-xrd-example-") as tmp:
                root = Path(tmp)
                synthetic_pdf_dir = root / "pdf"
                _write_synthetic_icdd_pdf_dir(synthetic_pdf_dir)
                return build_xrd_html(
                    [(str(raw_path), str(synthetic_pdf_dir))],
                    table_files=[str(path) for path in table_files],
                    image_files=[str(path) for path in image_files],
                    origin=True,
                    comment_provider=_xrd_comment_provider(
                        settings,
                        processed_dir=root / "images",
                        logs_dir=root / "logs",
                    ),
                )["html"]

    with tempfile.TemporaryDirectory(prefix="rist-xrd-example-") as tmp:
        root = Path(tmp)
        raw_path = root / "synthetic-xrd.txt"
        pdf_dir = root / "pdf"
        _write_synthetic_xrd_raw(raw_path)
        _write_synthetic_icdd_pdf_dir(pdf_dir)
        return build_xrd_html(
            [(str(raw_path), str(pdf_dir))],
            origin=True,
            comment_provider=_xrd_comment_provider(
                settings,
                processed_dir=root / "images",
                logs_dir=root / "logs",
            ),
        )["html"]


@router.get("/api/v1/xrd/example", response_class=HTMLResponse, tags=["xrd"])
def xrd_example(request: Request) -> HTMLResponse:
    repo_root = Path(__file__).resolve().parents[2]
    return HTMLResponse(
        _build_xrd_example_html(repo_root, settings=_request_settings(request))
    )


def create_xrd_preview_app() -> FastAPI:
    app = FastAPI(title="RIST XRD Preview")
    app.add_exception_handler(ApiException, api_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.include_router(router)
    return app

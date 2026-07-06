from __future__ import annotations

import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, FastAPI, File, Form, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse

from .path_bootstrap import add_project_package_paths

add_project_package_paths()

from lim.xrd_plot import (
    IMAGE_EXTENSIONS,
    TABLE_EXTENSIONS,
    build_xrd_html,
)

from .errors import ApiException, api_exception_handler, validation_exception_handler

router = APIRouter()

RAW_EXTENSIONS = {".txt", ".dat", ".xy", ".asc"}
PDF_EXTENSIONS = {".pdf"}
SUPPORTED_BUNDLE_EXTENSIONS = (
    RAW_EXTENSIONS | PDF_EXTENSIONS | TABLE_EXTENSIONS | IMAGE_EXTENSIONS
)
MAX_XRD_UPLOAD_BYTES = 80 * 1024 * 1024


def _safe_name(filename: str | None, fallback: str) -> str:
    name = Path(filename or "").name.strip() or fallback
    name = re.sub(r"[^\w.\-() \[\]\u3131-\u318e\uac00-\ud7a3]+", "_", name)
    return name[:160] or fallback


def _unique_path(directory: Path, filename: str) -> Path:
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    candidate = directory / filename
    index = 1
    while candidate.exists():
        candidate = directory / f"{stem}_{index}{suffix}"
        index += 1
    return candidate


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
        filename = _safe_name(upload.filename, f"bundle-{index}")
        suffix = Path(filename).suffix.lower()
        if not suffix and filename.startswith("."):
            continue
        if suffix not in SUPPORTED_BUNDLE_EXTENSIONS:
            unsupported.append(filename)
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
            path = _unique_path(directories["pdf"], filename)
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
    .xrd-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(220px, 1fr));
      gap: 12px;
      align-items: end;
    }
    .xrd-field label {
      display: block;
      font-size: 13px;
      font-weight: 700;
      margin-bottom: 7px;
      color: #334155;
    }
    .xrd-field input[type="file"] {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 9px;
      background: #f8fafc;
      min-height: 42px;
    }
    .xrd-check {
      display: flex;
      gap: 8px;
      align-items: center;
      min-height: 42px;
      font-weight: 700;
    }
    .xrd-drop {
      margin-top: 12px;
      border: 1px dashed #9fb6d6;
      border-radius: 8px;
      min-height: 72px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: #476483;
      background: #f8fbff;
      text-align: center;
      padding: 12px;
    }
    .xrd-drop.dragover { border-color: var(--blue); background: #eef6ff; }
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
    .xrd-status {
      min-height: 38px;
      display: flex;
      align-items: center;
      padding: 8px 12px;
      border-radius: 7px;
      background: #ecfdf5;
      border: 1px solid #bbf7d0;
      color: #166534;
      font-size: 14px;
    }
    .xrd-status.error { background: #fef2f2; border-color: #fecaca; color: #991b1b; }
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
      .xrd-grid { grid-template-columns: 1fr; }
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
        <a href="#" class="xrd-download" id="xrd-download" aria-disabled="true">HTML 다운로드</a>
      </div>
    </header>
    <main class="xrd-main">
      <section class="xrd-panel">
        <form id="xrd-form">
          <div class="xrd-grid">
            <div class="xrd-field">
              <label for="xrd-bundle-files">XRD bundle 파일</label>
              <input type="file" id="xrd-bundle-files" name="files" multiple accept=".txt,.dat,.xy,.asc,.pdf,.xlsx,.csv,.tsv,.png,.jpg,.jpeg,.webp,.gif">
            </div>
            <div class="xrd-field">
              <label for="xrd-bundle-folder">XRD bundle 폴더</label>
              <input type="file" id="xrd-bundle-folder" name="files" multiple webkitdirectory directory>
            </div>
          </div>
          <label class="xrd-check"><input type="checkbox" id="xrd-origin" name="origin" value="true"> Origin 스타일</label>
          <div class="xrd-drop" id="xrd-drop">raw TXT, ICDD PDF, Excel/CSV, 이미지를 한꺼번에 놓으세요</div>
          <div class="xrd-files" id="xrd-file-list"></div>
        </form>
      </section>
      <div class="xrd-status" id="xrd-status">XRD 파일을 선택하면 보고서를 생성할 수 있습니다.</div>
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
    var downloadUrl = null;

    function setStatus(message, error) {
      status.textContent = message;
      status.classList.toggle("error", Boolean(error));
    }
    function setBusy(value) {
      busy.classList.toggle("show", Boolean(value));
      runButton.disabled = Boolean(value);
      exampleButton.disabled = Boolean(value);
    }
    function revokeDownload() {
      if (downloadUrl) URL.revokeObjectURL(downloadUrl);
      downloadUrl = null;
      downloadLink.href = "#";
      downloadLink.setAttribute("aria-disabled", "true");
    }
    function setDownload(htmlText) {
      revokeDownload();
      downloadUrl = URL.createObjectURL(new Blob([htmlText], {type: "text/html;charset=utf-8"}));
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
      setDownload(htmlText);
    }
    function filesOf(input) {
      return Array.prototype.slice.call(input.files || []);
    }
    function allBundleFiles() {
      return filesOf(bundleInput).concat(filesOf(folderInput));
    }
    function classifyFile(file) {
      var name = file.name.toLowerCase();
      if (/\\.(txt|dat|xy|asc)$/.test(name)) return "raw";
      if (/\\.pdf$/.test(name)) return "pdf";
      if (/\\.(xlsx|csv|tsv)$/.test(name)) return "table";
      if (/\\.(png|jpe?g|webp|gif)$/.test(name)) return "image";
      return "skip";
    }
    function renderFileList() {
      var files = allBundleFiles().map(function(file) {
        return [classifyFile(file), file.webkitRelativePath || file.name];
      });
      fileList.replaceChildren();
      files.forEach(function(item) {
        var chip = document.createElement("span");
        chip.className = "xrd-chip";
        chip.textContent = item[0] + " · " + item[1];
        fileList.appendChild(chip);
      });
    }
    function appendFiles(input, files) {
      var dt = new DataTransfer();
      filesOf(input).forEach(function(file) { dt.items.add(file); });
      files.forEach(function(file) { dt.items.add(file); });
      input.files = dt.files;
    }
    function routeDroppedFiles(files) {
      appendFiles(bundleInput, files);
      renderFileList();
    }
    [bundleInput, folderInput].forEach(function(input) {
      input.addEventListener("change", renderFileList);
    });
    function buildBundleFormData() {
      var data = new FormData();
      allBundleFiles().forEach(function(file) {
        data.append("files", file, file.webkitRelativePath || file.name);
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
    drop.addEventListener("drop", function(event) {
      event.preventDefault();
      drop.classList.remove("dragover");
      routeDroppedFiles(Array.prototype.slice.call(event.dataTransfer.files || []));
    });
    clearButton.addEventListener("click", function() {
      form.reset();
      renderFileList();
      revokeDownload();
      preview.replaceChildren(empty);
      empty.style.display = "flex";
      setStatus("XRD 파일을 선택하면 보고서를 생성할 수 있습니다.", false);
    });
    exampleButton.addEventListener("click", async function() {
      setBusy(true);
      try {
        var response = await fetch("/api/v1/xrd/example");
        var text = await response.text();
        if (!response.ok) throw new Error(text || "예제 보고서를 불러오지 못했습니다.");
        showHtml(text);
        setStatus("예제 보고서를 불러왔습니다.", false);
      } catch (error) {
        setStatus(error.message || String(error), true);
      } finally {
        setBusy(false);
      }
    });
    form.addEventListener("submit", async function(event) {
      event.preventDefault();
      if (!allBundleFiles().some(function(file) { return classifyFile(file) === "raw"; })) {
        setStatus("Bundle 안에 raw TXT 파일이 필요합니다.", true);
        return;
      }
      setBusy(true);
      try {
        var data = buildBundleFormData();
        var response = await fetch("/api/v1/xrd/analyze", {method: "POST", body: data});
        var text = await response.text();
        if (!response.ok) throw new Error(text || "보고서 생성 요청에 실패했습니다.");
        showHtml(text);
        setStatus("XRD 보고서가 생성되었습니다.", false);
      } catch (error) {
        setStatus(error.message || String(error), true);
      } finally {
        setBusy(false);
      }
    });
    renderFileList();
  })();
  </script>
</body>
</html>"""


@router.get("/xrd", response_class=HTMLResponse, include_in_schema=False)
def xrd_page() -> HTMLResponse:
    return HTMLResponse(build_xrd_page())


@router.post("/api/v1/xrd/analyze", response_class=HTMLResponse, tags=["xrd"])
async def analyze_xrd(
    files: list[UploadFile] | None = File(default=None, alias="files"),
    raw_files: list[UploadFile] | None = File(default=None, alias="rawFiles"),
    pdf_files: list[UploadFile] | None = File(default=None, alias="pdfFiles"),
    table_files: list[UploadFile] | None = File(default=None, alias="tableFiles"),
    image_files: list[UploadFile] | None = File(default=None, alias="imageFiles"),
    origin: bool = Form(False),
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
        result = build_xrd_html(
            [(path, pdf_dir) for path in raw_paths],
            table_files=table_paths,
            image_files=image_paths,
            origin=origin,
        )
    return HTMLResponse(result["html"])


@router.get("/api/v1/xrd/example", response_class=HTMLResponse, tags=["xrd"])
def xrd_example() -> HTMLResponse:
    repo_root = Path(__file__).resolve().parents[2]
    raw_path = repo_root / "lim" / "data" / "data_dir" / "Mix2.txt"
    pdf_dir = repo_root / "lim" / "data" / "data_dir" / "Mix2"
    if not raw_path.is_file() or not pdf_dir.is_dir():
        raise ApiException(
            404,
            "XRD_EXAMPLE_NOT_FOUND",
            "XRD 예제 데이터를 찾을 수 없습니다.",
        )
    result = build_xrd_html([(str(raw_path), str(pdf_dir))])
    return HTMLResponse(result["html"])


def create_xrd_preview_app() -> FastAPI:
    app = FastAPI(title="RIST XRD Preview")
    app.add_exception_handler(ApiException, api_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.include_router(router)
    return app

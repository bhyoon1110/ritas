"""FT-IR upload workspace HTML and local Plotly asset helpers."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import plotly
import plotly.graph_objects as go
from fastapi import APIRouter, FastAPI, File, Form, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse

from ftir.plotting import ftir_abs_trans_toggle_js
from ftir.web_analysis import DptAnalysisError, analyze_dpt_files
from rist_common import get_logger
from rist_common.plotting import fig_to_responsive_html, peak_sensitivity_js

from .errors import ApiException, api_exception_handler, validation_exception_handler


PLOT_DIV_ID = "peak-plot"
MAX_FTIR_PREVIEW_FILES = 10
MAX_FTIR_PREVIEW_FILE_BYTES = 20 * 1024 * 1024
MAX_FTIR_PREVIEW_TOTAL_BYTES = 50 * 1024 * 1024
logger = get_logger(__name__)
router = APIRouter()


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
.ftir-message {
  display: none;
  min-height: 32px;
  padding: 8px 22px;
  border-bottom: 1px solid #fecaca;
  background: #fef2f2;
  color: #b42318;
  font-size: 12px;
  box-sizing: border-box;
}
.ftir-message.is-visible {
  display: block;
}
.ftir-loading {
  position: fixed;
  inset: 102px 0 0;
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
#peak-plot {
  min-height: 540px;
  height: calc(100vh - 102px) !important;
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
  }
  .ftir-status {
    flex: 1 1 auto;
  }
  .ftir-drop-band {
    padding: 7px 12px;
  }
  #peak-plot {
    min-height: 520px;
    height: calc(100vh - 132px) !important;
  }
  #peak-plot .rist-plot-control-row {
    left: 8px !important;
    right: 8px !important;
    width: auto !important;
    flex-wrap: wrap;
    justify-content: flex-end;
  }
  .ftir-loading {
    inset: 132px 0 0;
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
    <button type="button" class="ftir-clear-button" id="ftir-clear" hidden>초기화</button>
    <label class="ftir-file-button">
      DPT 파일 선택
      <input id="ftir-file-input" class="ftir-file-input" type="file"
             accept=".dpt" multiple>
    </label>
  </div>
</header>
<section class="ftir-drop-band" id="ftir-drop-zone">
  <span class="ftir-drop-prompt" id="ftir-drop-prompt">
    DPT 파일을 선택하거나 여기에 놓으세요
  </span>
  <div class="ftir-file-list" id="ftir-file-list"></div>
</section>
<div class="ftir-message" id="ftir-message" role="alert"></div>
<div class="ftir-loading" id="ftir-loading" aria-live="polite">전처리 및 피크 분석 중...</div>
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
  var clearButton = document.getElementById("ftir-clear");
  if (!gd || !input || !dropZone) return;

  var files = [];
  var controller = null;
  var emptyData = JSON.parse(JSON.stringify(gd.data || []));
  var emptyLayout = JSON.parse(JSON.stringify(gd.layout || {}));
  var MAX_FILES = 10;
  var MAX_FILE_BYTES = 20 * 1024 * 1024;
  var MAX_TOTAL_BYTES = 50 * 1024 * 1024;

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
  }

  function renderFiles() {
    fileList.innerHTML = "";
    prompt.style.display = files.length ? "none" : "inline";
    clearButton.hidden = files.length === 0;
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
  }

  function dispatchDataReplaced(sensitivity) {
    gd.dispatchEvent(new CustomEvent("rist-plot-data-replaced", {
      detail: { sensitivity: sensitivity }
    }));
  }

  function applyResponsiveLayout() {
    var mobile = window.innerWidth <= 760;
    return window.Plotly.relayout(gd, mobile ? {
      "margin.t": 170,
      "margin.r": 30,
      "margin.b": 135,
      "legend.orientation": "h",
      "legend.x": 0.5,
      "legend.xanchor": "center",
      "legend.y": -0.2,
      "legend.yanchor": "top"
    } : {
      "margin.t": 105,
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
    status.textContent = "대기";
    window.Plotly.react(gd, emptyData, emptyLayout, gd._context).then(function() {
      dispatchDataReplaced(25);
      return applyResponsiveLayout();
    }).then(function() {
      window.Plotly.Plots.resize(gd);
    });
  }

  function analyze() {
    if (!files.length) return;
    if (controller) controller.abort();
    controller = new AbortController();
    var activeController = controller;
    var form = new FormData();
    files.forEach(function(file) { form.append("files", file, file.name); });
    form.append("sensitivity", String(gd._ristPeakSensitivityValue || 25));
    setBusy(true);
    setMessage("");
    status.textContent = files.length + "개 파일 분석 중";

    fetch("/api/v1/ftir/analyze", {
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
      return window.Plotly.react(
        gd,
        payload.figure.data,
        payload.figure.layout,
        gd._context
      ).then(function() {
        var peakCount = payload.samples.reduce(function(total, sample) {
          return total + Number(sample.peakCount || 0);
        }, 0);
        status.textContent = payload.samples.length + "개 시료 · 피크 " + peakCount + "개";
        dispatchDataReplaced(payload.settings.sensitivity);
        return applyResponsiveLayout();
      }).then(function() {
        window.Plotly.Plots.resize(gd);
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

  input.addEventListener("change", function() {
    addFiles(input.files);
    input.value = "";
  });
  clearButton.addEventListener("click", function() {
    files = [];
    renderFiles();
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
  renderFiles();
  applyResponsiveLayout();
})();
</script>
"""


@lru_cache(maxsize=1)
def build_ftir_page() -> str:
    extra_scripts = (
        peak_sensitivity_js(PLOT_DIV_ID, initial="low")
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


@router.post("/api/v1/ftir/analyze", tags=["ftir"])
def analyze_ftir(
    files: list[UploadFile] = File(...),
    sensitivity: int = Form(default=25, ge=0, le=100),
) -> dict:
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

    try:
        result = analyze_dpt_files(uploaded, sensitivity=sensitivity)
    except DptAnalysisError as exc:
        logger.info(
            "FT-IR 미리보기 분석 거부 (code=%s, file=%s)",
            exc.code,
            exc.filename,
        )
        raise ApiException(422, exc.code, exc.message) from exc

    logger.info(
        "FT-IR 미리보기 분석 완료 (files=%d, sensitivity=%d)",
        len(uploaded),
        sensitivity,
    )
    return result


def create_ftir_preview_app() -> FastAPI:
    """Create a DB-free app for local FT-IR workspace development."""
    app = FastAPI(title="RIST FT-IR Preview")
    app.add_exception_handler(ApiException, api_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.include_router(router)
    return app

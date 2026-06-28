"""Raman upload workspace HTML and preview API."""

from __future__ import annotations

from pathlib import Path

import plotly
import plotly.graph_objects as go
from fastapi import APIRouter, FastAPI, File, Form, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse

try:
    from rin.raman.preprocess import SUPPORTED_SUFFIXES
    from rin.raman.web_analysis import RamanAnalysisError, analyze_raman_files
except ModuleNotFoundError:  # pragma: no cover - installed via edge requirements
    from raman.preprocess import SUPPORTED_SUFFIXES
    from raman.web_analysis import RamanAnalysisError, analyze_raman_files
from rist_common import get_logger
from rist_common.plotting import fig_to_responsive_html, peak_sensitivity_js

from .errors import ApiException, api_exception_handler, validation_exception_handler


PLOT_DIV_ID = "raman-plot"
MAX_RAMAN_PREVIEW_FILES = 10
MAX_RAMAN_PREVIEW_FILE_BYTES = 20 * 1024 * 1024
MAX_RAMAN_PREVIEW_TOTAL_BYTES = 50 * 1024 * 1024
logger = get_logger(__name__)
router = APIRouter()


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
        margin=dict(l=70, r=260, t=105, b=70),
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
.raman-drop-zone {
  position: fixed;
  inset: 54px 0 0;
  z-index: 35;
  display: none;
  align-items: center;
  justify-content: center;
  border: 2px dashed #3e7ca6;
  background: rgba(240,247,252,0.82);
  color: #174b6d;
  font-size: 15px;
  font-weight: 700;
  pointer-events: none;
}
.raman-drop-zone.is-dragging { display: flex; }
.raman-file-list {
  position: absolute;
  top: 67px;
  left: 20px;
  z-index: 18;
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  max-width: min(560px, calc(100% - 40px));
  pointer-events: none;
}
.raman-file-chip {
  display: inline-flex;
  align-items: center;
  max-width: 220px;
  height: 24px;
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  background: rgba(255,255,255,0.92);
  color: #334155;
  font-size: 11px;
  padding: 0 7px;
  box-sizing: border-box;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.raman-message {
  position: absolute;
  top: 94px;
  left: 20px;
  z-index: 19;
  display: none;
  max-width: min(620px, calc(100% - 40px));
  border: 1px solid #f5b7b1;
  border-radius: 4px;
  background: #fff5f5;
  color: #9b2c2c;
  font-size: 12px;
  padding: 8px 10px;
  box-sizing: border-box;
}
.raman-message.is-visible { display: block; }
.raman-loading {
  position: fixed;
  inset: 54px 0 0;
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
  height: calc(100vh - 54px) !important;
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
  .raman-drop-zone,
  .raman-loading {
    inset: 96px 0 0;
  }
  #raman-plot {
    height: calc(100vh - 96px) !important;
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
<div class="raman-drop-zone" id="raman-drop-zone">여기에 Raman raw 파일을 놓으세요</div>
<div class="raman-file-list" id="raman-file-list"></div>
<div class="raman-message" id="raman-message"></div>
<div class="raman-loading" id="raman-loading">Raman 전처리 중...</div>
"""


_UPLOAD_SCRIPT = """
<script>
(function() {
  var gd = document.getElementById("raman-plot");
  var input = document.getElementById("raman-file-input");
  var dropZone = document.getElementById("raman-drop-zone");
  var fileList = document.getElementById("raman-file-list");
  var status = document.getElementById("raman-status");
  var message = document.getElementById("raman-message");
  var loading = document.getElementById("raman-loading");
  var clearButton = document.getElementById("raman-clear");
  if (!gd || !input || !dropZone || !fileList || !status || !message
      || !loading || !clearButton) return;

  var files = [];
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
    var mobile = window.innerWidth <= 760;
    return window.Plotly.relayout(gd, mobile ? {
      "margin.t": 132,
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
  }

  function renderFiles() {
    fileList.innerHTML = "";
    files.forEach(function(file) {
      var chip = document.createElement("span");
      chip.className = "raman-file-chip";
      chip.textContent = file.name;
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

  async function apiPayload(response) {
    var payload = null;
    try {
      payload = await response.json();
    } catch (err) {
      payload = null;
    }
    if (!response.ok) {
      throw new Error(
        (payload && (payload.message || payload.detail))
        || "Raman 분석에 실패했습니다."
      );
    }
    return payload;
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
      var response = await fetch("/api/v1/raman/analyze", {
        method: "POST",
        body: form
      });
      var payload = await apiPayload(response);
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
      status.textContent = payload.samples.map(function(item) {
        return item.label + " 피크 " + item.peakCount + "개";
      }).join(" · ");
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
        post_body_html=peak_sensitivity_js(PLOT_DIV_ID, initial="25") + _UPLOAD_SCRIPT,
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


@router.post("/api/v1/raman/analyze", tags=["raman"])
def analyze_raman(
    files: list[UploadFile] = File(...),
    sensitivity: int = Form(default=25, ge=0, le=100),
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

    try:
        result = analyze_raman_files(uploaded, sensitivity=sensitivity)
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

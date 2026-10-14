"""Offline, view-only spectrum reports with previews rendered from the actual PPT."""
from __future__ import annotations

import base64
from copy import deepcopy
from functools import lru_cache
from html import escape
from io import BytesIO
import json
import math
from pathlib import Path
from threading import Lock
import zipfile

import pdfplumber
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs
from plotly.utils import PlotlyJSONEncoder
from fastapi.responses import HTMLResponse

from ..errors import ApiException

MAX_VIEWER_BYTES = 80 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 50 * 1024 * 1024
VIEWER_MARKER = '<meta name="rist-report-viewer" content="1">'
_PDF_RENDER_LOCK = Lock()  # PDFium is not thread-safe; report jobs run in threads.


def read_viewer_snapshot(upload) -> dict | None:
    if upload is None:
        return None
    content = upload.file.read(MAX_SNAPSHOT_BYTES + 1)
    if len(content) > MAX_SNAPSHOT_BYTES:
        raise ValueError("열람용 그래프 데이터는 50MB 이하여야 합니다.")
    figure = json.loads(content)
    if not isinstance(figure, dict) or not isinstance(figure.get("data"), list):
        raise ValueError("열람용 그래프 데이터가 올바르지 않습니다.")
    if not isinstance(figure.get("layout", {}), dict) or any(not isinstance(trace, dict) for trace in figure["data"]):
        raise ValueError("열람용 그래프 구조가 올바르지 않습니다.")
    return figure


def render_slide_images(pdf_path: Path) -> list[str]:
    """Decode one PDF page at a time; the browser also decodes only the active slide."""
    pages = []
    total_bytes = 0
    with _PDF_RENDER_LOCK, pdfplumber.open(pdf_path) as document:
        if not document.pages or len(document.pages) > 200:
            raise ValueError("PPT 미리보기는 1~200페이지를 지원합니다.")
        for page in document.pages:
            rendered = page.to_image(width=1280, antialias=True)
            image = rendered.original.convert("RGB")
            try:
                with BytesIO() as output:
                    image.save(output, format="JPEG", quality=86)
                    encoded = base64.b64encode(output.getvalue()).decode("ascii")
                total_bytes += len(encoded)
                if total_bytes > 40 * 1024 * 1024:
                    raise ValueError("PPT 미리보기 이미지 크기가 40MB를 초과했습니다.")
                pages.append("data:image/jpeg;base64," + encoded)
            finally:
                image.close()
                rendered.original.close()
                page.close()
    return pages


def readonly_figure(source: dict) -> dict:
    """Keep published coordinates/labels, never ship analysis editor programs."""
    layout_keys = {"title", "xaxis", "yaxis", "annotations", "shapes", "font", "legend",
                   "paper_bgcolor", "plot_bgcolor", "margin", "width", "height", "meta"}
    trace_keys = {"x", "y", "name", "mode", "line", "marker", "opacity", "hovertemplate",
                  "hoverinfo", "customdata", "showlegend", "legendgroup", "legendrank", "text",
                  "textposition", "textfont", "connectgaps", "fill", "fillcolor"}
    source_layout = source.get("layout") or {}
    layout = {k: deepcopy(v) for k, v in source_layout.items() if k in layout_keys}
    meta = source_layout.get("meta") or {}
    data, indices = [], {}
    for index, trace in enumerate(source.get("data") or []):
        if trace.get("visible") in (False, "legendonly"):
            continue
        if trace.get("type", "scatter") not in {"scatter", "scattergl"}:
            continue
        if not isinstance(trace.get("x"), list) or not isinstance(trace.get("y"), list):
            continue  # Old compact submissions have no coordinates. Do not invent results.
        if len(trace["x"]) != len(trace["y"]):
            raise ValueError("열람용 그래프 X/Y 데이터 길이가 일치하지 않습니다.")
        item = {k: deepcopy(v) for k, v in trace.items() if k in trace_keys}
        source_meta = trace.get("meta") or {}
        group = str(source_meta.get("rist_sample_group") or trace.get("legendgroup") or f"trace:{index}")
        offset = source_meta.get("rist_view_offset", source_meta.get("rist_raman_stack_offset"))
        if offset is None:
            ytitle = str((source_layout.get("yaxis") or {}).get("title", ""))
            unit = "transmittance" if "transmittance" in ytitle.lower() else "absorbance"
            offset = source_meta.get("rist_ftir_stack_offset_" + unit, 0)
        parent = bool(source_meta.get("rist_sample_parent")) or not source_meta.get("rist_peak")
        offset = float(offset or 0)
        if not math.isfinite(offset):
            raise ValueError("열람용 그래프 스택 값이 올바르지 않습니다.")
        item.update(type="scatter", legendgroup=group, showlegend=parent,
                    meta={"group": group, "parent": parent, "offset": offset})
        indices[index] = len(data)
        data.append(item)
    # Remap only published label associations; deleted/hidden peaks stay absent.
    labels = []
    excluded_annotations, excluded_shapes = set(), set()
    for label in meta.get("ristPeakLabels") or []:
        if label.get("traceIndex") in indices:
            item = deepcopy(label)
            item["traceIndex"] = indices[label["traceIndex"]]
            labels.append(item)
        else:
            excluded_annotations.add(label.get("annotationIndex"))
            excluded_shapes.add(label.get("shapeIndex"))
    for key, excluded, label_key in (
        ("annotations", excluded_annotations, "annotationIndex"),
        ("shapes", excluded_shapes, "shapeIndex"),
    ):
        mapping = {}
        kept = []
        for index, item in enumerate(layout.get(key) or []):
            if index not in excluded:
                mapping[index] = len(kept)
                kept.append(item)
        layout[key] = kept
        for label in labels:
            label[label_key] = mapping.get(label.get(label_key), -1)
    ratio_groups = {}
    for ratio in meta.get("ristRamanRatios") or []:
        try:
            group = json.loads(ratio["numeratorKey"])[0]
            ratio_groups["rist_raman_ratio:" + str(ratio["id"])] = group
        except (KeyError, ValueError, TypeError, IndexError):
            continue
    stack = meta.get("ristFtirStack") or meta.get("ristRamanStack") or {}
    if not isinstance(stack, dict):
        stack = {}
    saved_axis = stack.get("yAxisDisplay") or {}
    axis_display = {
        key: deepcopy(saved_axis[key])
        for key in ("ticks", "ticklen", "showgrid", "zeroline",
                    "minorTicks", "minorTicklen", "minorShowgrid")
        if isinstance(saved_axis, dict) and isinstance(saved_axis.get(key), dict)
    }
    layout["meta"] = {"labels": labels, "ratioGroups": ratio_groups,
                      "size": meta.get("ristPlotSize") or {},
                      "stacked": bool(stack.get("enabled", any(t["meta"]["offset"] for t in data))),
                      "yAxisDisplay": axis_display}
    layout.update(template=None, dragmode="zoom")
    for name in ("xaxis", "yaxis"):
        layout.setdefault(name, {})["fixedrange"] = False
    layout.setdefault("legend", {}).update(groupclick="togglegroup", itemclick="toggle",
                                          itemdoubleclick=False)
    for item in layout.get("annotations") or []:
        item["captureevents"] = False
    for item in layout.get("shapes") or []:
        item["editable"] = False
    return go.Figure(data=data, layout=layout, skip_invalid=True).to_plotly_json()


@lru_cache(maxsize=1)
def viewer_script() -> str:
    return Path(__file__).with_name("spectrum_viewer.js").read_text(encoding="utf-8")


def _analysis_html(analysis: dict | None) -> str:
    if not analysis:
        return ""
    status = analysis.get("status")
    label = {"reviewed": "분석자 수정 문안 포함 · 최종 내용 확인 필요", "generated": "로컬 AI 해석 · 분석자 검토 필요",
             "partial": "로컬 AI 해석 일부 생성 · 나머지는 규칙 기반 요약"}.get(status,
             "로컬 AI 해석 미생성 · 규칙 기반 요약")
    sections = []
    for section in analysis.get("sections") or []:
        source = {"llm": "로컬 AI", "user": "사용자 수정"}.get(section.get("source"), "규칙 기반")
        paragraphs = "".join("<p>" + escape(str(text)).replace("\n", "<br>") + "</p>"
                             for text in section.get("paragraphs") or [])
        sections.append("<article><h2>" + escape(str(section.get("heading") or "분석 결과"))
                        + " <small>" + source + "</small></h2>" + paragraphs + "</article>")
    return ("<section id='analysis-panel'><p class='analysis-status' role='status'>" + label + "</p>"
            + "<p class='notice'>보고서 생성 시점의 피크와 주석을 기준으로 작성했습니다. 사용자 메모와 피크명은 해석 참고 정보이며 확정 동정이 아닙니다. 그래프 수정 후에는 분석 화면에서 보고서를 다시 생성하세요.</p>"
            + "".join(sections) + "</section>")


def viewer_html(*, title: str, figure: dict, slides: list[str], fallback_image: str = "", analysis: dict | None = None) -> str:
    payload = json.dumps({"figure": readonly_figure(figure), "slides": slides,
                          "fallback": fallback_image}, cls=PlotlyJSONEncoder,
                         ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    return ("<!doctype html><html lang='ko'><head><meta charset='utf-8'>" + VIEWER_MARKER
            + "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            + "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'\">"
            + "<title>" + escape(title) + "</title><style>" + _STYLE + "</style></head><body>"
            + "<header><h1>" + escape(title) + "</h1><p>확정된 분석 보고서 · 열람 전용</p>"
            + "<nav aria-label='보고서 보기'>"
            + ("<button id='analysis-tab' aria-pressed='true'>분석 결과</button>" if analysis else "")
            + "<button id='slides-tab' aria-pressed='" + ("false" if analysis else "true") + "'>PPT 페이지</button>"
            + "<button id='graph-tab' aria-pressed='false'>인터랙티브 그래프</button></nav></header>"
            + "<main>" + _analysis_html(analysis)
            + "<section id='slides-panel'" + (" hidden" if analysis else "") + "><div class='controls'><button id='previous'>이전</button>"
            + "<label>페이지 <select id='page-select' aria-label='PPT 페이지 선택'></select></label>"
            + "<span id='page-count' aria-live='polite'></span><button id='next'>다음</button></div>"
            + "<img id='slide-image' alt='PPT 페이지'></section>"
            + "<section id='graph-panel' hidden><div class='controls'><label>그래프 보기 <select id='view-mode'>"
            + "<option value='original'>발행본 보기</option><option value='overlay'>중첩 보기</option>"
            + "<option value='stack'>스택 보기</option></select></label><button id='reset-view'>발행본으로 복원</button></div>"
            + "<p class='notice'>확대·축소, 이동, 시료 표시와 스택은 보기만 바꿉니다. 피크·라벨·해석·보고서 내용은 편집하거나 저장할 수 없습니다.</p>"
            + "<div id='report-graph'></div><img id='graph-fallback' alt='발행 당시 그래프' hidden>"
            + "<p id='graph-message' role='status'></p></section></main>"
            + "<script type='application/json' id='report-data'>" + payload + "</script>"
            + "<script>" + get_plotlyjs() + "</script><script>" + viewer_script() + "</script></body></html>")


def build_spectrum_viewer(report_dir: Path, *, title: str, figure: dict, analysis: dict | None = None) -> Path:
    slides = render_slide_images(report_dir / "report-from-pptx.pdf")
    fallback = "data:image/png;base64," + base64.b64encode((report_dir / "current_graph.png").read_bytes()).decode("ascii")
    content = viewer_html(title=title, figure=figure, slides=slides, fallback_image=fallback, analysis=analysis)
    if len(content.encode("utf-8")) > MAX_VIEWER_BYTES:
        raise ValueError("열람용 HTML 크기가 80MB를 초과했습니다.")
    summary = report_dir / "report.html"
    if summary.exists():
        summary.rename(report_dir / "report-summary.html")
    summary.write_text(content, encoding="utf-8")
    return summary


def spectrum_preview_response(package: Path) -> HTMLResponse:
    if not package.is_file():
        raise ApiException(410, "REPORT_PACKAGE_EXPIRED", "보고서 파일이 만료되었습니다.")
    try:
        with zipfile.ZipFile(package) as archive:
            info = archive.getinfo("report.html")
            if info.file_size > MAX_VIEWER_BYTES:
                raise ApiException(413, "REPORT_PREVIEW_TOO_LARGE", "미리보기 크기가 제한을 초과했습니다.")
            content = archive.read(info).decode("utf-8")
    except (KeyError, zipfile.BadZipFile, UnicodeDecodeError) as exc:
        raise ApiException(404, "REPORT_PREVIEW_NOT_FOUND", "미리보기를 찾을 수 없습니다. 보고서를 다시 생성하세요.") from exc
    if VIEWER_MARKER not in content:
        raise ApiException(404, "REPORT_PREVIEW_NOT_FOUND", "이전 보고서입니다. 미리보기를 사용하려면 다시 생성하세요.")
    return HTMLResponse(content, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                         "Content-Security-Policy": "sandbox allow-scripts allow-downloads"})


_STYLE = """
*{box-sizing:border-box}body{margin:0;background:#f1f5f9;color:#1e293b;font:15px Arial,sans-serif}
header{padding:20px 24px;background:#fff;border-bottom:1px solid #cbd5e1}h1{margin:0;font-size:22px}
header p{color:#64748b;font-size:13px}nav,.controls{display:flex;flex-wrap:wrap;align-items:center;gap:10px}
#analysis-panel{max-width:1000px;margin:auto}#analysis-panel article{background:#fff;padding:18px 22px;border:1px solid #cbd5e1;border-radius:6px;margin:14px 0;overflow-wrap:break-word}
#analysis-panel h2{font-size:18px;margin:0 0 12px}#analysis-panel p{line-height:1.85;white-space:normal}#analysis-panel small{font-size:12px;font-weight:normal;color:#64748b}.analysis-status{color:#1e40af;font-weight:bold}
button,select{font:inherit;min-height:38px;padding:7px 12px;border:1px solid #94a3b8;border-radius:5px;background:#fff;color:#1e293b}
button{cursor:pointer}button[aria-pressed=true]{background:#1d4ed8;color:#fff;border-color:#1d4ed8}button:disabled{opacity:.45;cursor:default}
main{padding:16px;max-width:1500px;margin:auto}.controls{margin-bottom:12px}#slide-image,#graph-fallback{display:block;width:100%;height:auto;margin:auto;border:1px solid #cbd5e1;background:#fff}
[hidden]{display:none!important}#report-graph{width:100%;height:720px;background:#fff}.notice,#graph-message{font-size:13px;color:#475569;line-height:1.5}
#report-graph .modebar{top:-30px!important;background:#fff!important}
@media(max-width:600px){header{padding:14px}h1{font-size:18px}main{padding:8px}.controls{gap:6px}button,select{font-size:13px;padding:6px 9px}}
@media print{nav,.controls,.notice,#graph-message{display:none}body{background:#fff}}
"""

"""XRD raw 데이터(.txt)를 Plotly로 그리고, ICDD Card PDF의 피크 표를
2θ 위치에 Norm. I.(0~100%) 높이의 수직 막대로 오버레이한다.
또한 (AX) XRD Report 양식의 구조에 맞춰 그래프, 자동 해석 초안,
피크 정보, 결정상(Phase) 정보를 한 HTML 보고서로 출력한다.

================================ 실행 방법 ================================

[1] 사전 준비 (최초 1회) — 필요한 파이썬 패키지 설치
        pip install pdfplumber plotly

[2] 입력 데이터 준비
    - raw 데이터: XRD 측정 결과 .txt 파일.
      형식은 한 줄에 "2theta intensity" 두 컬럼(공백 구분),
      '#'로 시작하는 줄은 주석으로 무시한다.
          예) 25.30  1234.5
    - PDF 폴더: ICDD Card PDF들이 모여 있는 폴더.
      각 PDF 안에는 No./2θ/d-value/Norm. I./h k l 컬럼의 표가 들어 있어야 한다.
    - Excel/CSV 파일(선택): 피크 정보 영역에 표시할 표.
      .xlsx/.csv/.tsv 를 지원한다.
    - 이미지 파일(선택): 그래프/상매칭 보조 이미지 영역에 표시할 이미지.
      .png/.jpg/.jpeg/.webp/.gif 를 지원한다.

[3] 기본 실행
        python xrd_plot.py <raw.txt> <pdf_dir>

    - <raw.txt> : raw 데이터 .txt 파일 경로 (필수, 첫 번째 인자)
    - <pdf_dir> : ICDD Card PDF들이 들어있는 폴더 경로 (필수, 두 번째 인자)
    - 경로에 공백/한글이 있으면 반드시 큰따옴표로 감싼다.

    예시)
        python xrd_plot.py \\
            "data/예제 데이터(AX - XRD)/예제 데이터 1/Mix2.txt" \\
            "data/예제 데이터(AX - XRD)/예제 데이터 1/ICDD Card (라이브러리 pdf)"

[4] 선택 옵션
    -o, --output <경로>
        출력 HTML 파일 경로를 직접 지정한다.
        생략하면 raw 파일과 같은 폴더에 "<raw 파일명>.html"로 저장된다.
        예) -o result.html

    --origin
        Origin(OriginLab) 논문 스타일(사방 테두리 박스, 안쪽 눈금,
        그리드 제거, 굵은 검정 축)로 그린다. 생략하면 기본 디자인.

    --excel <경로>, --image <경로>
        보고서에 포함할 표/이미지 파일을 명시한다. --data-dir 또는 raw 주변
        폴더에 있는 지원 파일은 자동으로 포함된다.

    옵션을 함께 쓴 예시)
        python xrd_plot.py "Mix2.txt" "ICDD Card" --origin -o paper_fig.html

[5] 결과 확인
    - 생성된 .html 파일을 웹 브라우저로 열면 된다.
    - 기본 출력은 보고서형 HTML이다. 기존 그래프+표 화면만 필요하면
      --plot-only 옵션을 사용한다.
    - 그래프는 반응형(모바일/태블릿 대응)이며, 화면 폭에 따라 범례 위치가
      자동으로 바뀐다(기준: 아래 LEGEND_BREAKPOINT_PX).
    - 범례는 손가락/마우스로 드래그해 위치를 옮길 수 있다.
    - 그래프 아래에는 PDF별 피크 표가 색상 구분과 함께 표시된다.

==========================================================================
"""

from __future__ import annotations

import argparse
import base64
import csv
import glob
import json
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Any, Callable
import xml.etree.ElementTree as ET

import pdfplumber
import plotly.graph_objects as go

# 루트 common 패키지를 설치하지 않은 소스 실행도 지원한다.
_COMMON_DIR = Path(__file__).resolve().parents[1] / "common"
if str(_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMON_DIR))
from rist_common.plotting import (  # noqa: E402
    LEGEND_BREAKPOINT_PX,
    fig_to_responsive_html,
)

HEADER = ["No.", "2θ, °", "d-value", "Norm. I.", "h k l"]

XRD_DOWNLOAD_IMAGE_FORMAT = "jpeg"
XRD_IMAGE_FORMAT_SELECTOR = False

# raw 라인 색상(여러 raw 파일을 구분). 첫 번째 측정 데이터는 보고서 기본 빨간색.
RAW_LINE_COLORS = [
    "#d62728", "#1f3b73", "#7a1f1f", "#1f5c2e",
    "#5a2d82", "#8a5a00", "#005f6b", "#6b2d5a",
]
RAW_LINE_WIDTH = 2.2
XRD_MODE_BAR_BUTTONS_TO_REMOVE = ["autoScale2d"]

# PDF 피크 막대 색상 팔레트.
PEAK_PALETTE = [
    "#e41a1c", "#377eb8", "#4daf4a", "#984ea3",
    "#ff7f00", "#a65628", "#f781bf", "#999999",
]

PHASE_GROUPS = {
    "major": "주요 상 (Major Phases)",
    "uncertain": "유사/불확실 상 (Uncertain / Similar Phases)",
    "minor": "미량 상 후보 (Minor Phase Candidates)",
}

TABLE_EXTENSIONS = {".csv", ".tsv", ".xlsx"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_REPORT_TABLE_ROWS = 80
MAX_REPORT_TABLE_COLS = 12


# ----------------------------------------------------------------------------
# raw 데이터 로드 (.txt: "2theta intensity" 두 컬럼, '#' 주석 무시)
# ----------------------------------------------------------------------------
def load_raw(path: str):
    two_theta, intensity = [], []
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                x = float(parts[0])
                y = float(parts[1])
            except ValueError:
                continue
            two_theta.append(x)
            intensity.append(y)
    return two_theta, intensity


# ----------------------------------------------------------------------------
# PDF 표에서 피크 추출 -> [{"no", "two_theta", "d", "norm", "hkl"}, ...]
# 표 한 행에 좌(0:5)/우(5:10) 두 블록이 들어있다.
# ----------------------------------------------------------------------------
def _is_pdf_peak_header(cells: list[str]) -> bool:
    if len(cells) < 5:
        return False
    header = [(cell or "").strip() for cell in cells[:5]]
    if header == HEADER:
        return True
    normalized = [header[0], header[1].replace("q", "θ"), *header[2:5]]
    return normalized == HEADER


def parse_pdf_peaks(path: str):
    peaks = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                if not table:
                    continue
                first = [(c or "").strip() for c in table[0]]
                if not _is_pdf_peak_header(first):
                    continue
                for row in table[1:]:
                    cells = [(c or "").strip() for c in row]
                    for block in (cells[0:5], cells[5:10]):
                        if len(block) < 5 or not block[0]:
                            continue
                        no, two_theta, d_value, norm_i, hkl = block
                        try:
                            tt = float(two_theta)
                            ni = float(norm_i)
                        except ValueError:
                            continue
                        peaks.append({
                            "no": no,
                            "two_theta": tt,
                            "d": d_value,
                            "norm": ni,
                            "hkl": hkl,
                        })
    peaks.sort(key=lambda p: p["two_theta"])
    return peaks


def parse_pdf_card_metadata(path: str) -> dict[str, str]:
    """ICDD Card PDF의 첫 페이지 텍스트에서 보고서용 메타데이터를 추출한다."""
    info = {
        "card_no": "",
        "quality_mark": "",
        "formula": "",
        "phase_name": "",
        "crystal_system": "",
        "space_group": "",
        "two_theta_range": "",
    }
    try:
        with pdfplumber.open(path) as pdf:
            text = "\n".join(
                page.extract_text() or "" for page in pdf.pages[:2]
            )
    except Exception:
        return info

    card_match = re.search(r"PDF Card No\.\s*:\s*([^\s]+)\s+QM:\s*([A-Z])", text)
    if card_match:
        info["card_no"] = card_match.group(1).strip()
        info["quality_mark"] = card_match.group(2).strip()

    patterns = {
        "formula": r"Chemical formula:\s*([^\n]+)",
        "phase_name": r"Name:\s*([^\n]+)",
        "two_theta_range": r"2θ range:\s*([^\n]+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        if match:
            value = match.group(1).strip()
            if key == "phase_name":
                value = re.split(r"\s+I/Ic\b", value, maxsplit=1)[0].strip()
            info[key] = value

    crystal_match = re.search(
        r"Crystal system:\s*([^:\n]+?)\s+Space group:\s*([^\n]+)",
        text,
    )
    if crystal_match:
        info["crystal_system"] = crystal_match.group(1).strip()
        info["space_group"] = crystal_match.group(2).strip()
    return info


def _compact_formula(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def phase_label_from_metadata(metadata: dict[str, str], fallback: str) -> str:
    phase_name = metadata.get("phase_name") or ""
    formula = _compact_formula(metadata.get("formula") or "")
    card_no = metadata.get("card_no") or ""
    quality = metadata.get("quality_mark") or ""
    left = phase_name or formula or fallback
    right_parts = [part for part in (formula if phase_name else "", card_no) if part]
    if quality:
        right_parts.append(f"QM:{quality}")
    return f"{left} / {' '.join(right_parts)}" if right_parts else left


def _nearest_raw_intensity(
    rx: list[float],
    ry: list[float],
    target: float,
) -> tuple[float, float] | None:
    if not rx or not ry:
        return None
    best_idx = min(range(len(rx)), key=lambda idx: abs(rx[idx] - target))
    return abs(rx[best_idx] - target), ry[best_idx]


def score_phase_candidate(
    peaks: list[dict[str, Any]],
    rx: list[float],
    ry: list[float],
    raw_max: float,
    *,
    tolerance: float = 0.25,
) -> dict[str, Any]:
    """PDF 카드 피크가 raw 패턴 근처에 얼마나 나타나는지 간단히 점수화한다."""
    important = [peak for peak in peaks if float(peak.get("norm") or 0) >= 10.0]
    if not important:
        important = sorted(
            peaks,
            key=lambda peak: float(peak.get("norm") or 0),
            reverse=True,
        )[:5]
    total_weight = sum(float(peak.get("norm") or 0) for peak in important) or 1.0
    intensity_floor = max(raw_max * 0.03, 1e-9)
    matched = []
    matched_weight = 0.0
    for peak in important:
        nearest = _nearest_raw_intensity(rx, ry, float(peak["two_theta"]))
        if not nearest:
            continue
        distance, intensity = nearest
        if distance <= tolerance and intensity >= intensity_floor:
            matched.append(peak)
            matched_weight += float(peak.get("norm") or 0)
    score = matched_weight / total_weight * 100.0
    return {
        "score": round(score, 1),
        "matched_count": len(matched),
        "important_count": len(important),
        "matched_peaks": matched,
    }


def classify_phase_candidate(match: dict[str, Any]) -> str:
    score = float(match.get("score") or 0)
    matched_count = int(match.get("matched_count") or 0)
    if score >= 45 and matched_count >= 2:
        return "major"
    if score >= 18 or matched_count >= 1:
        return "uncertain"
    return "minor"


def assign_relative_phase_categories(items: list[dict[str, Any]]) -> None:
    """한 raw 안에서 후보가 모두 major로 몰리지 않도록 상대 순위를 보정한다."""
    ranked = sorted(
        items,
        key=lambda item: float(item["match"].get("score") or 0),
        reverse=True,
    )
    major_count = 0
    for item in ranked:
        score = float(item["match"].get("score") or 0)
        if score < 18:
            item["category"] = "minor"
        elif major_count < 2 and score >= 45:
            item["category"] = "major"
            major_count += 1
        elif score >= 18:
            item["category"] = "uncertain"
        else:
            item["category"] = "minor"


_PHASE_CATEGORY_ORDER = {"major": 0, "uncertain": 1, "minor": 2}


def _phase_similarity_key(item: dict[str, Any]) -> str:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    formula = _compact_formula(str(metadata.get("formula") or ""))
    phase_name = str(metadata.get("phase_name") or item.get("label") or "").lower()
    phase_name = re.sub(r"[^0-9a-zA-Z\u3131-\u318e\uac00-\ud7a3]+", " ", phase_name)
    return f"{formula.lower()} {phase_name}".strip()


def sort_phase_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            _PHASE_CATEGORY_ORDER.get(str(item.get("category") or ""), 99),
            _phase_similarity_key(item),
            -float((item.get("match") or {}).get("score") or 0),
        ),
    )


def _phase_category_separator_label(category: str) -> str:
    title = PHASE_GROUPS.get(category, category)
    return f"──────── {title}"


def _xrd_plot_config() -> dict[str, Any]:
    return {
        "scrollZoom": True,
        "modeBarButtonsToRemove": XRD_MODE_BAR_BUTTONS_TO_REMOVE,
    }


def build_xrd_legend_checkbox_js(div_id: str) -> str:
    return f"""
<script>
(function() {{
  var gd = document.getElementById("{div_id}");
  if (!gd) return;
  var SVG_NS = "http://www.w3.org/2000/svg";
  function stripBox(text) {{
    return String(text || "").replace(/^[☑☐□✓]\\s*/, "");
  }}
  function svg(tag) {{
    return document.createElementNS(SVG_NS, tag);
  }}
  function ensureCheckbox(row) {{
    var mark = row.querySelector(".rist-xrd-legend-checkbox");
    if (!mark) {{
      mark = svg("g");
      mark.setAttribute("class", "rist-xrd-legend-checkbox");
      var rect = svg("rect");
      rect.setAttribute("width", "12");
      rect.setAttribute("height", "12");
      rect.setAttribute("rx", "2");
      rect.setAttribute("ry", "2");
      rect.setAttribute("stroke-width", "1.5");
      var path = svg("path");
      path.setAttribute("d", "M3 6.2l2.1 2.1L9.4 3.6");
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", "#ffffff");
      path.setAttribute("stroke-width", "1.8");
      path.setAttribute("stroke-linecap", "round");
      path.setAttribute("stroke-linejoin", "round");
      mark.appendChild(rect);
      mark.appendChild(path);
      row.insertBefore(mark, row.firstChild);
    }}
    return mark;
  }}
  function removeCheckbox(row) {{
    var mark = row.querySelector(".rist-xrd-legend-checkbox");
    if (mark && mark.parentNode) mark.parentNode.removeChild(mark);
  }}
  function placeCheckbox(mark, textNode) {{
    var tx = Number(textNode.getAttribute("x") || 40);
    var ty = Number(textNode.getAttribute("y") || 0);
    mark.setAttribute("transform", "translate(" + (tx - 18) + "," + (ty - 10) + ")");
  }}
  function paintCheckbox(mark, visible) {{
    var rect = mark.querySelector("rect");
    var path = mark.querySelector("path");
    rect.setAttribute("fill", visible ? "#2563eb" : "#ffffff");
    rect.setAttribute("stroke", visible ? "#2563eb" : "#94a3b8");
    path.setAttribute("d", "M3 6.2l2.1 2.1L9.4 3.6");
    path.setAttribute("stroke", "#ffffff");
    path.style.display = visible ? "block" : "none";
  }}
  function rowVisible(row) {{
    var opacity = row.style.opacity || row.getAttribute("opacity")
      || window.getComputedStyle(row).opacity;
    var value = Number(opacity);
    return !Number.isFinite(value) || value >= 0.75;
  }}
  function refreshLegendCheckboxes() {{
    var rows = Array.prototype.slice.call(gd.querySelectorAll("g.legend g.traces"));
    rows.forEach(function(row) {{
      var node = row.querySelector("text.legendtext");
      if (!node) return;
      var base = stripBox(node.textContent || "");
      if (base.trim().indexOf("────────") === 0) {{
        removeCheckbox(row);
        node.textContent = base;
        node.style.fill = "#94a3b8";
        node.style.fontSize = "11px";
        node.style.opacity = "1";
        node.style.textDecoration = "none";
        return;
      }}
      var mark = ensureCheckbox(row);
      placeCheckbox(mark, node);
      paintCheckbox(mark, rowVisible(row));
      node.textContent = base;
    }});
  }}
  function schedule() {{ setTimeout(refreshLegendCheckboxes, 0); }}
  if (gd.on) {{
    gd.on("plotly_afterplot", schedule);
    gd.on("plotly_restyle", schedule);
    gd.on("plotly_relayout", schedule);
  }}
  schedule();
}})();
</script>
"""


# ----------------------------------------------------------------------------
# PDF별 피크 표를 HTML로 생성 (그래프 색상과 일치하는 헤더, 반응형)
# ----------------------------------------------------------------------------
def _esc(text) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _cell_ref_to_index(ref: str) -> tuple[int, int]:
    match = re.match(r"([A-Z]+)([0-9]+)", ref.upper())
    if not match:
        return 0, 0
    letters, row = match.groups()
    col_idx = 0
    for char in letters:
        col_idx = col_idx * 26 + (ord(char) - ord("A") + 1)
    return int(row) - 1, col_idx - 1


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    values = []
    for si in root.findall("a:si", ns):
        chunks = [node.text or "" for node in si.findall(".//a:t", ns)]
        values.append("".join(chunks))
    return values


def read_xlsx_preview(path: str) -> list[list[str]]:
    """외부 의존성 없이 XLSX 첫 번째 워크시트를 HTML 미리보기용으로 읽는다."""
    with zipfile.ZipFile(path) as archive:
        shared_strings = _xlsx_shared_strings(archive)
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        ns = {
            "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
            "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        }
        first_sheet = workbook.find("a:sheets/a:sheet", ns)
        sheet_path = "xl/worksheets/sheet1.xml"
        if first_sheet is not None:
            rid = first_sheet.attrib.get(f"{{{ns['r']}}}id")
            if rid:
                rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
                rel_ns = {
                    "rel": "http://schemas.openxmlformats.org/package/2006/relationships"
                }
                for rel in rels.findall("rel:Relationship", rel_ns):
                    if rel.attrib.get("Id") == rid:
                        target = rel.attrib.get("Target", "worksheets/sheet1.xml")
                        sheet_path = "xl/" + target.lstrip("/")
                        break

        sheet = ET.fromstring(archive.read(sheet_path))
        rows: dict[int, dict[int, str]] = {}
        for c in sheet.findall(".//a:sheetData/a:row/a:c", ns):
            cell_ref = c.attrib.get("r", "")
            row_idx, col_idx = _cell_ref_to_index(cell_ref)
            if row_idx >= MAX_REPORT_TABLE_ROWS or col_idx >= MAX_REPORT_TABLE_COLS:
                continue
            cell_type = c.attrib.get("t")
            value_node = c.find("a:v", ns)
            inline_node = c.find("a:is/a:t", ns)
            value = ""
            if cell_type == "s" and value_node is not None:
                try:
                    value = shared_strings[int(value_node.text or "0")]
                except (IndexError, ValueError):
                    value = value_node.text or ""
            elif inline_node is not None:
                value = inline_node.text or ""
            elif value_node is not None:
                value = value_node.text or ""
            rows.setdefault(row_idx, {})[col_idx] = value
        if not rows:
            return []
        max_row = min(max(rows) + 1, MAX_REPORT_TABLE_ROWS)
        max_col = min(
            max((max(cols) if cols else 0) for cols in rows.values()) + 1,
            MAX_REPORT_TABLE_COLS,
        )
        return [
            [rows.get(r, {}).get(c, "") for c in range(max_col)]
            for r in range(max_row)
        ]


def read_delimited_preview(path: str, delimiter: str | None = None) -> list[list[str]]:
    with open(path, "r", encoding="utf-8-sig", errors="ignore", newline="") as fh:
        if delimiter is None:
            sample = fh.read(4096)
            fh.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
            delimiter = dialect.delimiter
        reader = csv.reader(fh, delimiter=delimiter)
        rows = []
        for row in reader:
            rows.append(row[:MAX_REPORT_TABLE_COLS])
            if len(rows) >= MAX_REPORT_TABLE_ROWS:
                break
    return rows


def read_table_preview(path: str) -> dict[str, Any]:
    ext = Path(path).suffix.lower()
    try:
        if ext == ".xlsx":
            rows = read_xlsx_preview(path)
        elif ext == ".tsv":
            rows = read_delimited_preview(path, "\t")
        elif ext == ".csv":
            rows = read_delimited_preview(path)
        else:
            return {"path": path, "rows": [], "error": f"지원하지 않는 표 형식: {ext}"}
        return {"path": path, "rows": rows, "error": ""}
    except Exception as exc:
        return {"path": path, "rows": [], "error": str(exc)}


def image_data_uri(path: str) -> str:
    ext = Path(path).suffix.lower()
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext, "application/octet-stream")
    data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def discover_support_files(
    pairs: list[tuple[str, str]],
    *,
    data_dir: str | None,
    excel_paths: list[str] | None,
    image_paths: list[str] | None,
) -> tuple[list[str], list[str]]:
    """명시 입력과 data/raw 주변 폴더에서 보고서 보조 파일을 찾는다."""
    table_files: list[str] = []
    image_files: list[str] = []
    seen_tables: set[Path] = set()
    seen_images: set[Path] = set()

    def add_once(target: list[str], seen: set[Path], path: Path) -> None:
        key = path.expanduser().resolve()
        if key in seen:
            return
        seen.add(key)
        target.append(str(path))

    for path in excel_paths or []:
        add_once(table_files, seen_tables, Path(path))
    for path in image_paths or []:
        add_once(image_files, seen_images, Path(path))

    search_dirs = []
    if data_dir:
        search_dirs.append(Path(data_dir))
    for raw_txt, _pdf_dir in pairs:
        search_dirs.append(Path(raw_txt).resolve().parent)

    for directory in search_dirs:
        if not directory.is_dir():
            continue
        for child in sorted(directory.iterdir()):
            if not child.is_file():
                continue
            suffix = child.suffix.lower()
            if suffix in TABLE_EXTENSIONS:
                add_once(table_files, seen_tables, child)
            if suffix in IMAGE_EXTENSIONS:
                add_once(image_files, seen_images, child)

    return table_files, image_files


def build_tables_html(groups) -> str:
    """PDF별 피크 표를 raw 파일 단위로 묶어 HTML로 생성한다.

    groups: [(raw_stem, raw_color, [(label, color, peaks, trace_idx), ...]), ...]
    각 카드에 data-trace, raw 제목에 data-group 을 달아 범례 표시 상태와 연동한다.
    """
    if not groups:
        return ""

    css = """
<style>
  .xrd-tables { font-family: Arial, sans-serif; max-width: 1100px;
                margin: 24px auto; padding: 0 12px; }
  .xrd-tables h2 { font-size: 18px; margin: 24px 0 8px; }
  .xrd-tables h3.xrd-raw { font-size: 16px; margin: 22px 0 10px;
                           padding-left: 8px; }
  .xrd-card { margin-bottom: 28px; }
  .xrd-card-title { display: flex; align-items: center; gap: 8px;
                    font-size: 15px; font-weight: 700; margin: 0 0 6px; }
  .xrd-swatch { width: 14px; height: 14px; border-radius: 3px;
                display: inline-block; flex: 0 0 auto; }
  .xrd-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
  table.xrd { border-collapse: collapse; width: 100%; font-size: 13px; }
  table.xrd th, table.xrd td { border: 1px solid #ccc; padding: 4px 8px;
                               text-align: right; white-space: nowrap; }
  table.xrd td.hkl, table.xrd th.hkl { text-align: center; }
  table.xrd tbody tr:nth-child(even) { background: #f7f7f7; }
</style>
"""

    parts = [css, '<div class="xrd-tables">', "<h2>ICDD Card Peak Tables</h2>"]
    for raw_stem, raw_color, items in groups:
        parts.append(
            f'<h3 class="xrd-raw" data-group="{_esc(raw_stem)}" '
            f'style="border-left:6px solid {raw_color}">'
            f"{_esc(raw_stem)}</h3>"
        )
        for item in items:
            label = item["label"]
            color = item["color"]
            peaks = item["peaks"]
            trace_idx = item["trace_idx"]
            rows = []
            for p in peaks:
                rows.append(
                    "<tr>"
                    f"<td>{_esc(p['no'])}</td>"
                    f"<td>{p['two_theta']:.3f}</td>"
                    f"<td>{_esc(p['d'])}</td>"
                    f"<td>{p['norm']:.2f}</td>"
                    f"<td class='hkl'>{_esc(p['hkl'])}</td>"
                    "</tr>"
                )
            parts.append(
                f'<div class="xrd-card" data-trace="{trace_idx}">'
                f'<div class="xrd-card-title">'
                f'<span class="xrd-swatch" style="background:{color}"></span>'
                f"{_esc(label)}</div>"
                '<div class="xrd-scroll">'
                '<table class="xrd"><thead><tr>'
                "<th>No.</th><th>2θ (°)</th><th>d-value</th>"
                "<th>Norm. I.</th><th class='hkl'>h k l</th>"
                "</tr></thead><tbody>"
                + "".join(rows) +
                "</tbody></table></div></div>"
            )
    parts.append("</div>")
    return "\n".join(parts)


def _phase_groups(groups) -> dict[str, list[dict[str, Any]]]:
    grouped = {key: [] for key in PHASE_GROUPS}
    for raw_stem, _raw_color, items in groups:
        for item in items:
            enriched = dict(item)
            enriched["raw_stem"] = raw_stem
            grouped.setdefault(item["category"], []).append(enriched)
    return grouped


def build_auto_interpretation_html(sample_name: str, groups, warnings: list[str]) -> str:
    """XRD LLM 슬롯이 붙기 전에도 쓸 수 있는 규칙 기반 코멘트 초안."""
    grouped = _phase_groups(groups)
    major = grouped.get("major", [])
    uncertain = grouped.get("uncertain", [])
    minor = grouped.get("minor", [])

    def names(items: list[dict[str, Any]], limit: int = 3) -> str:
        labels = [
            item["metadata"].get("phase_name")
            or _compact_formula(item["metadata"].get("formula") or "")
            or item["label"]
            for item in items[:limit]
        ]
        return ", ".join(_esc(label) for label in labels) if labels else "해당 후보 없음"

    sample = _esc(sample_name)
    paragraphs = [
        (
            "<strong>A. 주요 상 (Major Phases)</strong><br>"
            f"본 {sample} 시료의 XRD 패턴은 {names(major)} 후보와 주요 피크 위치가 "
            "상대적으로 잘 대응합니다. 해당 후보는 주요 상으로 우선 검토할 수 있습니다."
            if major else
            "<strong>A. 주요 상 (Major Phases)</strong><br>"
            f"본 {sample} 시료에서 자동 기준을 만족하는 주요 상 후보는 아직 없습니다."
        ),
        (
            "<strong>B. 유사 상 / 불확실 상 (Uncertain / Similar Phases)</strong><br>"
            f"{names(uncertain)} 후보는 일부 주요 피크가 raw 패턴과 근접하지만, "
            "현재 데이터만으로 확정 구분하기에는 불확실성이 있습니다."
            if uncertain else
            "<strong>B. 유사 상 / 불확실 상 (Uncertain / Similar Phases)</strong><br>"
            "유사 상으로 분류된 후보는 없습니다."
        ),
        (
            "<strong>C. 미량 상 (Minor Phases)</strong><br>"
            f"{names(minor)} 후보는 피크 대응이 제한적이어서 미량 상 또는 배경 후보로 검토됩니다."
            if minor else
            "<strong>C. 미량 상 (Minor Phases)</strong><br>"
            "미량 상 후보는 없습니다."
        ),
        (
            "<strong>안내</strong><br>"
            "유사 상 구분 및 불순물/미량 상 확인을 위해 XRF, ICP, EDS 등 원소 성분 정보를 "
            "함께 검토하면 후보상을 더 좁힐 수 있습니다."
        ),
    ]
    if warnings:
        paragraphs.append(
            "<strong>데이터 확인</strong><br>"
            + "<br>".join(_esc(warning) for warning in warnings)
        )
    return "\n".join(f"<p>{paragraph}</p>" for paragraph in paragraphs)


build_llm_comment_html = build_auto_interpretation_html


def _round_float(value: Any, digits: int = 3) -> float | None:
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def detect_raw_pattern_peaks(
    rx: list[float],
    ry: list[float],
    *,
    max_items: int = 12,
) -> list[dict[str, Any]]:
    if len(rx) < 3 or len(ry) < 3:
        return []
    y_min = min(ry)
    y_max = max(ry)
    span = max(y_max - y_min, 1e-9)
    threshold = y_min + span * 0.05
    candidates = []
    for idx in range(1, min(len(rx), len(ry)) - 1):
        y_value = ry[idx]
        if y_value <= threshold:
            continue
        if y_value >= ry[idx - 1] and y_value >= ry[idx + 1]:
            candidates.append(
                {
                    "two_theta": rx[idx],
                    "intensity": y_value,
                    "relative_intensity": (y_value - y_min) / span * 100.0,
                }
            )
    candidates.sort(key=lambda item: item["intensity"], reverse=True)
    selected: list[dict[str, Any]] = []
    for item in candidates:
        theta = float(item["two_theta"])
        if any(abs(theta - float(prev["two_theta"])) < 0.18 for prev in selected):
            continue
        selected.append(
            {
                "two_theta": _round_float(item["two_theta"]),
                "intensity": _round_float(item["intensity"]),
                "relative_intensity": _round_float(item["relative_intensity"], 1),
            }
        )
        if len(selected) >= max_items:
            break
    selected.sort(key=lambda item: float(item["two_theta"] or 0))
    return selected


def _raw_pattern_context(
    *,
    raw_stem: str,
    raw_txt: str,
    rx: list[float],
    ry: list[float],
    raw_max: float,
) -> dict[str, Any]:
    return {
        "sample": raw_stem,
        "source_file": os.path.basename(raw_txt),
        "point_count": len(rx),
        "two_theta_range": [
            _round_float(min(rx)) if rx else None,
            _round_float(max(rx)) if rx else None,
        ],
        "max_intensity": _round_float(raw_max),
        "detected_raw_peaks": detect_raw_pattern_peaks(rx, ry),
    }


def _peak_context(peaks: list[dict[str, Any]], *, max_items: int = 6) -> list[dict[str, Any]]:
    ranked = sorted(
        peaks,
        key=lambda peak: float(peak.get("norm") or 0),
        reverse=True,
    )
    return [
        {
            "no": peak.get("no"),
            "two_theta": _round_float(peak.get("two_theta")),
            "d_value": peak.get("d"),
            "norm_i": _round_float(peak.get("norm"), 1),
            "hkl": peak.get("hkl"),
        }
        for peak in ranked[:max_items]
    ]


def _phase_candidate_context(groups) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key in PHASE_GROUPS}
    for raw_stem, _raw_color, items in groups:
        for item in items:
            metadata = item["metadata"]
            match = item["match"]
            candidate = {
                "sample": raw_stem,
                "label": item["label"],
                "category": item["category"],
                "phase_name": metadata.get("phase_name") or "",
                "formula": _compact_formula(metadata.get("formula") or ""),
                "card_no": metadata.get("card_no") or "",
                "quality_mark": metadata.get("quality_mark") or "",
                "crystal_system": metadata.get("crystal_system") or "",
                "space_group": metadata.get("space_group") or "",
                "two_theta_range": metadata.get("two_theta_range") or "",
                "match_score": _round_float(match.get("score"), 1),
                "matched_count": match.get("matched_count"),
                "important_count": match.get("important_count"),
                "top_icdd_peaks": _peak_context(item["peaks"], max_items=5),
                "matched_icdd_peaks": _peak_context(
                    match.get("matched_peaks") or [],
                    max_items=5,
                ),
                "source_pdf": os.path.basename(str(item.get("source_pdf") or "")),
            }
            grouped.setdefault(item["category"], []).append(candidate)
    for items in grouped.values():
        items.sort(
            key=lambda item: float(item.get("match_score") or 0),
            reverse=True,
        )
    return grouped


def _table_context(path: str) -> dict[str, Any]:
    preview = read_table_preview(path)
    rows = preview.get("rows") or []
    return {
        "file": Path(path).name,
        "row_count_previewed": len(rows),
        "column_count_previewed": max((len(row) for row in rows), default=0),
        "headers": rows[0][:8] if rows else [],
        "preview_rows": rows[1:6] if len(rows) > 1 else [],
        "error": preview.get("error") or "",
    }


def build_xrd_llm_context(
    *,
    sample_name: str,
    raw_patterns: list[dict[str, Any]],
    groups,
    warnings: list[str],
    table_files: list[str] | None,
    image_files: list[str] | None,
    origin: bool,
) -> dict[str, Any]:
    candidates = _phase_candidate_context(groups)
    return {
        "experiment": "XRD",
        "sample_name": sample_name,
        "display": {
            "origin_style": origin,
            "x_axis": "2θ (°)",
            "y_axis": "Intensity (cps)",
        },
        "raw_patterns": raw_patterns,
        "icdd_candidates": candidates,
        "phase_category_counts": {
            key: len(value)
            for key, value in candidates.items()
        },
        "supporting_files": {
            "tables": [_table_context(path) for path in (table_files or [])[:4]],
            "images": [
                {"file": Path(path).name, "type": Path(path).suffix.lower().lstrip(".")}
                for path in (image_files or [])[:8]
            ],
        },
        "warnings": warnings,
    }


def _table_preview_html(table: dict[str, Any]) -> str:
    filename = Path(table["path"]).name
    if table.get("error"):
        return (
            '<article class="xrd-file-table">'
            f"<h3>{_esc(filename)}</h3>"
            f'<p class="xrd-warning">{_esc(table["error"])}</p>'
            "</article>"
        )
    rows = table.get("rows") or []
    if not rows:
        return (
            '<article class="xrd-file-table">'
            f"<h3>{_esc(filename)}</h3>"
            '<p class="xrd-empty">표시할 데이터가 없습니다.</p>'
            "</article>"
        )
    head = rows[0]
    body = rows[1:] if len(rows) > 1 else []
    header_html = "".join(f"<th>{_esc(cell)}</th>" for cell in head)
    body_html = "".join(
        "<tr>" + "".join(f"<td>{_esc(cell)}</td>" for cell in row) + "</tr>"
        for row in body
    )
    if not body_html:
        body_html = (
            "<tr>"
            + "".join(f"<td>{_esc(cell)}</td>" for cell in head)
            + "</tr>"
        )
        header_html = "".join(
            f"<th>Column {index + 1}</th>" for index in range(len(head))
        )
    note = ""
    if len(rows) >= MAX_REPORT_TABLE_ROWS:
        note = (
            f'<p class="xrd-table-note">표시는 상위 {MAX_REPORT_TABLE_ROWS}행, '
            f"{MAX_REPORT_TABLE_COLS}열로 제한했습니다.</p>"
        )
    return f"""
    <article class="xrd-file-table">
      <h3>{_esc(filename)}</h3>
      <div class="xrd-table-scroll xrd-file-table-scroll">
        <table class="xrd-report-table">
          <thead><tr>{header_html}</tr></thead>
          <tbody>{body_html}</tbody>
        </table>
      </div>
      {note}
    </article>
    """


def build_excel_display_html(table_files: list[str]) -> str:
    if not table_files:
        return ""
    previews = [_table_preview_html(read_table_preview(path)) for path in table_files]
    return f"""
  <div class="xrd-provided-block">
    <h3>제공된 Excel 파일 Display</h3>
    {''.join(previews)}
  </div>
"""


def build_image_display_html(image_files: list[str]) -> str:
    if not image_files:
        return ""
    figures = []
    for path in image_files:
        try:
            src = image_data_uri(path)
            figures.append(
                f"""
                <figure class="xrd-image-card">
                  <img src="{src}" alt="{_esc(Path(path).name)}">
                  <figcaption>{_esc(Path(path).name)}</figcaption>
                </figure>
                """
            )
        except Exception as exc:
            figures.append(
                f"""
                <figure class="xrd-image-card">
                  <div class="xrd-warning">{_esc(Path(path).name)} 이미지를 읽지 못했습니다: {_esc(exc)}</div>
                </figure>
                """
            )
    return f"""
<section class="xrd-report-section" id="xrd-image-info">
  <div class="xrd-section-head">
    <h2>그래프/상매칭 보조 이미지</h2>
    <p>입력 bundle에 포함된 이미지 파일을 보고서에 함께 표시합니다.</p>
  </div>
  <div class="xrd-image-grid">{''.join(figures)}</div>
</section>
"""


def build_peak_info_html(groups, table_files: list[str] | None = None) -> str:
    rows = []
    for raw_stem, _raw_color, items in groups:
        for item in items:
            for peak in item["peaks"]:
                rows.append(
                    "<tr data-trace=\"{trace}\">"
                    "<td>{sample}</td><td>{phase}</td><td>{no}</td>"
                    "<td>{theta:.3f}</td><td>{d}</td><td>{norm:.2f}</td>"
                    "<td>{hkl}</td></tr>".format(
                        trace=item["trace_idx"],
                        sample=_esc(raw_stem),
                        phase=_esc(item["label"]),
                        no=_esc(peak["no"]),
                        theta=float(peak["two_theta"]),
                        d=_esc(peak["d"]),
                        norm=float(peak["norm"]),
                        hkl=_esc(peak["hkl"]),
                    )
                )
    body = (
        "".join(rows)
        if rows
        else '<tr><td colspan="7" class="xrd-empty">추출된 피크 정보가 없습니다.</td></tr>'
    )
    return f"""
<section class="xrd-report-section" id="xrd-peak-info">
  <div class="xrd-section-head">
    <h2>피크 정보</h2>
    <p>PDF 카드에서 추출한 피크 정보를 보고서용 표로 정리했습니다. e.s.d 열과 Phase Name 이후 열은 표시하지 않습니다.</p>
  </div>
  {build_excel_display_html(table_files or [])}
  <div class="xrd-table-scroll">
    <table class="xrd-report-table xrd-peak-table">
      <thead>
        <tr>
          <th>시료</th><th>결정상 후보</th><th>No.</th>
          <th>2θ (°)</th><th>d-value</th><th>Norm. I.</th><th>h k l</th>
        </tr>
      </thead>
      <tbody>{body}</tbody>
    </table>
  </div>
</section>
"""


def _top_peak_rows(peaks: list[dict[str, Any]]) -> str:
    ranked = sorted(peaks, key=lambda peak: float(peak.get("norm") or 0), reverse=True)
    rows = []
    for index, peak in enumerate(ranked[:3], start=1):
        rows.append(
            "<tr class=\"xrd-rank-{rank}\"><td>{rank}</td><td>{theta:.3f}</td>"
            "<td>{norm:.2f}</td><td>{hkl}</td></tr>".format(
                rank=index,
                theta=float(peak["two_theta"]),
                norm=float(peak["norm"]),
                hkl=_esc(peak["hkl"]),
            )
        )
    return "".join(rows) or '<tr><td colspan="4">-</td></tr>'


def build_phase_info_html(groups) -> str:
    grouped = _phase_groups(groups)
    sections = []
    for category, title in PHASE_GROUPS.items():
        items = grouped.get(category, [])
        cards = []
        for item in items:
            metadata = item["metadata"]
            formula = _compact_formula(metadata.get("formula") or "")
            phase_name = metadata.get("phase_name") or item["label"]
            card_no = metadata.get("card_no") or "-"
            quality = metadata.get("quality_mark") or "-"
            match = item["match"]
            card_rows = [
                ("시료", item["raw_stem"]),
                ("Phase name", phase_name),
                ("Formula", formula or "-"),
                ("PDF Card", card_no),
                ("QM", quality),
                ("Crystal system", metadata.get("crystal_system") or "-"),
                ("Space group", metadata.get("space_group") or "-"),
                ("2θ range", metadata.get("two_theta_range") or "-"),
                (
                    "raw 피크 대응",
                    f"{match['score']:.1f}% "
                    f"({match['matched_count']}/{match['important_count']})",
                ),
            ]
            meta_html = "".join(
                f"<tr><th>{_esc(label)}</th><td>{_esc(value)}</td></tr>"
                for label, value in card_rows
            )
            cards.append(
                f"""
                <article class="xrd-phase-card xrd-card" data-trace="{item['trace_idx']}">
                  <h4><span class="xrd-swatch" style="background:{item['color']}"></span>{_esc(item['label'])}</h4>
                  <div class="xrd-phase-grid">
                    <table class="xrd-mini-table"><tbody>{meta_html}</tbody></table>
                    <table class="xrd-mini-table xrd-top-peak-table">
                      <thead><tr><th>Rank</th><th>2θ (°)</th><th>Norm. I.</th><th>h k l</th></tr></thead>
                      <tbody>{_top_peak_rows(item['peaks'])}</tbody>
                    </table>
                  </div>
                </article>
                """
            )
        content = (
            "".join(cards)
            if cards
            else '<p class="xrd-empty">해당 그룹의 결정상 후보가 없습니다.</p>'
        )
        sections.append(
            f"""
            <details class="xrd-phase-group" open>
              <summary>{_esc(title)} <span>{len(items)}건</span></summary>
              {content}
            </details>
            """
        )
    return f"""
<section class="xrd-report-section" id="xrd-phase-info">
  <div class="xrd-section-head">
    <h2>결정상(Phase) 정보</h2>
    <p>PDF/DB 카드의 결정상 정보를 주요상, 유사/불확실상, 미량상 후보로 묶어 표시합니다. Norm. I. 상위 3개 피크는 강조했습니다.</p>
  </div>
  {''.join(sections)}
</section>
"""


def _html_body_inner(html: str) -> str:
    start = html.find("<body>")
    end = html.rfind("</body>")
    if start < 0 or end < 0:
        return html
    return html[start + len("<body>"):end]


def xrd_report_css() -> str:
    return """
<style>
  html { background: #f3f4f6; }
  body { margin: 0; font-family: Arial, "Noto Sans KR", sans-serif; color: #111827; }
  .xrd-report-page { max-width: 980px; margin: 0 auto; background: #fff; min-height: 100vh; padding: 28px 34px 48px; box-sizing: border-box; }
  .xrd-report-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin: 0 0 22px; }
  .xrd-report-title { flex: 1 1 auto; text-align: center; font-size: 26px; margin: 0; font-weight: 700; }
  .xrd-report-action-spacer { width: 108px; flex: 0 0 auto; }
  .xrd-report-pdf-button { flex: 0 0 auto; border: 1px solid #9fb6d6; background: #fff; color: #172a46; border-radius: 7px; min-height: 38px; padding: 8px 13px; font-size: 14px; font-weight: 700; cursor: pointer; }
  .xrd-report-pdf-button:hover { background: #eff6ff; border-color: #2563eb; }
  .xrd-report-section { margin: 20px 0 0; }
  .xrd-report-section h2 { font-size: 17px; margin: 0 0 8px; }
  .xrd-section-head { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; border-bottom: 1px solid #d1d5db; padding-bottom: 6px; margin-bottom: 10px; }
  .xrd-section-head p { margin: 0; color: #6b7280; font-size: 12px; line-height: 1.45; text-align: right; }
  .xrd-graph-frame, .xrd-comment-box, .xrd-table-scroll, .xrd-phase-group { border: 2px solid #111827; border-radius: 18px; background: #fff; }
  .xrd-graph-frame { padding: 10px 12px 4px; }
  #xrd-plot { height: 510px !important; min-height: 420px; }
  .xrd-comment-box { padding: 16px 18px; border-radius: 10px; }
  .xrd-comment-box p { margin: 0 0 12px; font-size: 14px; line-height: 1.65; }
  .xrd-comment-box p:last-child { margin-bottom: 0; }
  .xrd-table-scroll { border-radius: 10px; overflow: auto; max-height: 520px; }
  .xrd-report-table, .xrd-mini-table { width: 100%; border-collapse: collapse; font-size: 12px; }
  .xrd-report-table th, .xrd-report-table td, .xrd-mini-table th, .xrd-mini-table td { border: 1px solid #d1d5db; padding: 6px 8px; vertical-align: middle; }
  .xrd-report-table th { background: #f3f4f6; position: sticky; top: 0; z-index: 1; }
  .xrd-report-table td:nth-child(4), .xrd-report-table td:nth-child(5), .xrd-report-table td:nth-child(6), .xrd-report-table td:nth-child(7) { text-align: right; }
  .xrd-provided-block { margin: 0 0 14px; }
  .xrd-provided-block > h3 { font-size: 14px; margin: 0 0 8px; }
  .xrd-file-table { margin: 10px 0 14px; }
  .xrd-file-table h3 { font-size: 13px; margin: 0 0 6px; color: #374151; }
  .xrd-file-table-scroll { max-height: 360px; border-width: 1px; border-radius: 8px; }
  .xrd-table-note { color: #6b7280; font-size: 12px; margin: 6px 0 0; }
  .xrd-image-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }
  .xrd-image-card { margin: 0; border: 2px solid #111827; border-radius: 14px; padding: 10px; background: #fff; }
  .xrd-image-card img { display: block; width: 100%; height: auto; max-height: 520px; object-fit: contain; }
  .xrd-image-card figcaption { margin-top: 6px; font-size: 12px; color: #6b7280; text-align: center; }
  .xrd-phase-group { border-radius: 10px; margin: 12px 0; padding: 0 12px 12px; }
  .xrd-phase-group summary { cursor: pointer; font-size: 16px; font-weight: 700; padding: 12px 0; }
  .xrd-phase-group summary span { color: #6b7280; font-size: 12px; margin-left: 6px; }
  .xrd-phase-card { border-top: 1px solid #e5e7eb; padding: 12px 0; }
  .xrd-phase-card h4 { display: flex; align-items: center; gap: 8px; font-size: 14px; margin: 0 0 10px; }
  .xrd-phase-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .xrd-mini-table th { width: 120px; background: #f9fafb; text-align: left; }
  .xrd-top-peak-table th, .xrd-top-peak-table td { text-align: right; }
  .xrd-rank-1 { background: #fff3cd; font-weight: 700; }
  .xrd-rank-2, .xrd-rank-3 { background: #fff8e6; }
  .xrd-empty { color: #6b7280; text-align: center; padding: 18px; }
  .xrd-warning { border-left: 4px solid #f59e0b; background: #fffbeb; padding: 10px 12px; margin: 8px 0; font-size: 13px; }
  @media (max-width: 760px) {
    .xrd-report-page { padding: 18px 12px 32px; }
    .xrd-report-header { align-items: flex-start; gap: 8px; }
    .xrd-report-title { text-align: left; font-size: 23px; }
    .xrd-report-action-spacer { display: none; }
    .xrd-report-pdf-button { min-height: 36px; padding: 7px 10px; font-size: 13px; }
    .xrd-section-head { display: block; }
    .xrd-section-head p { text-align: left; margin-top: 4px; }
    #xrd-plot { height: 460px !important; }
    .xrd-phase-grid { grid-template-columns: 1fr; }
    .xrd-image-grid { grid-template-columns: 1fr; }
  }
  @media print {
    html { background: #fff; }
    .xrd-report-page { max-width: none; padding: 0; }
    .xrd-report-pdf-button, .xrd-report-action-spacer { display: none !important; }
    .xrd-report-title { text-align: center; }
  }
</style>
"""


def build_report_html(
    fig,
    *,
    sample_name: str,
    groups,
    group_map: dict,
    warnings: list[str],
    table_files: list[str] | None = None,
    image_files: list[str] | None = None,
    origin: bool,
    first_stem: str,
    raw_line_indices: list[int],
    highlight_groups: dict[int, list[int]],
    comment_html: str | None = None,
    comment_note: str | None = None,
) -> str:
    plot_html = fig_to_responsive_html(
        fig,
        div_id="xrd-plot",
        origin=origin,
        legend_breakpoint_px=LEGEND_BREAKPOINT_PX,
        crosshair=True,
        title_edit=True,
        legend_text_edit=True,
        trace_highlight=True,
        highlight_pickable=raw_line_indices,
        highlight_groups=highlight_groups,
        image_filename=first_stem,
        image_format=XRD_DOWNLOAD_IMAGE_FORMAT,
        image_format_selector=XRD_IMAGE_FORMAT_SELECTOR,
        post_body_html=build_xrd_legend_checkbox_js("xrd-plot"),
        config=_xrd_plot_config(),
    )
    plot_body = _html_body_inner(plot_html)
    warning_html = "".join(f'<div class="xrd-warning">{_esc(w)}</div>' for w in warnings)
    comments = comment_html or build_auto_interpretation_html(sample_name, groups, warnings)
    comment_note_text = (
        comment_note
        or "raw 피크, ICDD 후보상, 첨부 표/이미지 정보를 기준으로 작성한 자동 해석 초안입니다."
    )
    image_info = build_image_display_html(image_files or [])
    peak_info = build_peak_info_html(groups, table_files or [])
    phase_info = build_phase_info_html(groups)
    group_toggle_js = build_group_toggle_js("xrd-plot", group_map)
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=5">
  <title>{_esc(sample_name)} Report</title>
  {xrd_report_css()}
</head>
<body>
  <main class="xrd-report-page">
    <header class="xrd-report-header">
      <div class="xrd-report-action-spacer" aria-hidden="true"></div>
      <h1 class="xrd-report-title">{_esc(sample_name)} Report</h1>
      <button type="button" class="xrd-report-pdf-button" id="xrd-report-pdf-export">PDF Export</button>
    </header>
    <section class="xrd-report-section" id="xrd-graph-section">
      <div class="xrd-section-head">
        <h2>그래프 영역</h2>
        <p>측정 데이터와 ICDD Card 피크를 함께 표시합니다.</p>
      </div>
      <div class="xrd-graph-frame">{plot_body}</div>
    </section>
    {image_info}
    <section class="xrd-report-section" id="xrd-llm-comment">
      <div class="xrd-section-head">
        <h2>특이사항 / 자동 해석 초안</h2>
        <p>{_esc(comment_note_text)}</p>
      </div>
      {warning_html}
      <div class="xrd-comment-box">{comments}</div>
    </section>
    {peak_info}
    {phase_info}
  </main>
  {group_toggle_js}
  <script>
  (function() {{
    var button = document.getElementById("xrd-report-pdf-export");
    if (!button) return;
    button.addEventListener("click", function() {{
      window.print();
    }});
  }})();
  </script>
</body>
</html>
"""


def build_group_toggle_js(div_id: str, group_map: dict) -> str:
    """Plotly 기본 범례 토글 결과에 맞춰 하단 ICDD 표만 동기화한다.

    group_map: {raw_stem: [그 그룹에 속한 trace 인덱스, ...]}
    """
    gm = json.dumps(group_map, ensure_ascii=True)
    return f"""
<script>
(function() {{
  var gd = document.getElementById("{div_id}");
  if (!gd) return;
  var GROUPS = {gm};
  function traces() {{
    return gd.data || gd._fullData || [];
  }}
  function isOn(i) {{
    var trace = traces()[i] || {{}};
    var v = trace.visible;
    return v === true || v === undefined;
  }}
  function syncTables() {{
    // 표시 기준: 범례에서 켜진(visible) 카드만. 단, raw 강조(highlight) 중이면
    // 그 raw 그룹의 표만 보여준다(다른 raw 표는 숨김).
    var hs = gd._hiState || {{ mode: "none" }};
    var only = (hs.mode === "highlight" && hs.members) ? hs.members : null;
    function shown(i) {{
      if (!isOn(i)) return false;
      if (only && only.indexOf(i) < 0) return false;
      return true;
    }}
    var cards = document.querySelectorAll(".xrd-card[data-trace]");
    cards.forEach(function(c) {{
      var i = parseInt(c.getAttribute("data-trace"), 10);
      c.style.display = shown(i) ? "" : "none";
    }});
    // 그 raw 그룹의 모든 카드가 꺼지면 raw 제목도 숨긴다.
    var titles = document.querySelectorAll("h3.xrd-raw[data-group]");
    titles.forEach(function(h) {{
      var key = h.getAttribute("data-group");
      var idxs = GROUPS[key] || [];
      var anyOn = idxs.some(function(i) {{
        var c = document.querySelector('.xrd-card[data-trace="' + i + '"]');
        return c && shown(i);
      }});
      h.style.display = anyOn ? "" : "none";
    }});
  }}
  function init() {{
    syncTables();
    if (gd.on && !gd.__xrdGroupEventsBound) {{
      gd.__xrdGroupEventsBound = true;
      gd.on("plotly_afterplot", syncTables);
      gd.on("plotly_restyle", syncTables);
    }}
    gd.addEventListener("trace-highlight", syncTables);
  }}
  init();
}})();
</script>
"""


def auto_pdf_dir(raw_txt):
    """raw 파일과 같은 이름의 폴더를 같은 디렉터리에서 찾아 PDF 폴더로 사용한다.

    예: ".../예제 데이터 1/Mix2.txt" → ".../예제 데이터 1/Mix2" 폴더가 있으면 그 폴더.
    정확히 일치하는 폴더가 없으면 대소문자 무시로 한 번 더 찾는다. 못 찾으면 None.
    """
    base_dir = os.path.dirname(os.path.abspath(raw_txt))
    stem = os.path.splitext(os.path.basename(raw_txt))[0]

    exact = os.path.join(base_dir, stem)
    if os.path.isdir(exact):
        return exact

    if os.path.isdir(base_dir):
        low = stem.lower()
        for name in os.listdir(base_dir):
            cand = os.path.join(base_dir, name)
            if os.path.isdir(cand) and name.lower() == low:
                return cand
    return None


def scan_data_dir(data_dir):
    """data_dir 안의 모든 .txt 를 raw 로, 같은 이름 폴더를 PDF 폴더로 짝지어 반환한다.

    같은 이름 폴더가 없는 .txt 는 건너뛴다. 하나도 못 찾으면 SystemExit.
    """
    if not os.path.isdir(data_dir):
        raise SystemExit(f"폴더를 찾을 수 없습니다: {data_dir}")

    pairs = []
    for raw_txt in sorted(glob.glob(os.path.join(data_dir, "*.txt"))):
        pdf_dir = auto_pdf_dir(raw_txt)
        if pdf_dir:
            pairs.append((raw_txt, pdf_dir))
        else:
            print(f"건너뜀(짝 폴더 없음): {os.path.basename(raw_txt)}")
    if not pairs:
        raise SystemExit(
            f"'{data_dir}' 안에서 raw .txt 와 같은 이름의 PDF 폴더 쌍을 찾지 못했습니다."
        )
    return pairs


def collect_pairs(args):
    """positional(raw, pdf) / --pair / --data-dir 를 모아 (raw_txt, pdf_dir) 쌍 생성.

    - --data-dir DIR: DIR 안의 *.txt 를 raw, 같은 이름 폴더를 PDF 로 자동 인식.
    - positional raw_txt 가 폴더이면 data_dir 로 간주한다.
    - pdf 폴더를 지정하지 않으면 raw 파일명과 같은 이름의 폴더를 자동으로 짝짓는다.
    """
    pairs = []
    if args.data_dir:
        pairs.extend(scan_data_dir(args.data_dir))
    if args.raw_txt and os.path.isdir(args.raw_txt):
        # positional 인자가 폴더이면 data_dir 로 처리
        pairs.extend(scan_data_dir(args.raw_txt))
    elif args.raw_txt:
        pdf_dir = args.pdf_dir or auto_pdf_dir(args.raw_txt)
        if not pdf_dir:
            raise SystemExit(
                f"PDF 폴더를 지정하지 않았고, '{args.raw_txt}' 와 같은 이름의 "
                "폴더도 찾지 못했습니다. pdf_dir 를 직접 지정하세요."
            )
        pairs.append((args.raw_txt, pdf_dir))
    if args.pair:
        for r, d in args.pair:
            pdf_dir = d or auto_pdf_dir(r)
            if not pdf_dir:
                raise SystemExit(
                    f"'{r}' 와 같은 이름의 PDF 폴더를 찾지 못했습니다. "
                    "--pair 에 폴더 경로를 직접 지정하세요."
                )
            pairs.append((r, pdf_dir))
    if not pairs:
        raise SystemExit(
            "입력이 없습니다. data_dir 폴더를 지정하거나, 'raw.txt [pdf_dir]' / "
            "--pair raw.txt pdf_dir 를 사용하세요."
        )
    return pairs


def pdf_peak_warning(pdf_dir, pdf_count, parsed_count):
    """PDF 피크 오버레이가 비어 있는 이유를 사용자에게 설명한다."""
    if pdf_count == 0:
        return f"경고: '{pdf_dir}'에서 PDF 파일을 찾지 못했습니다."
    if parsed_count == 0:
        return (
            f"경고: '{pdf_dir}'의 PDF {pdf_count}개에서 피크 표를 추출하지 "
            "못했습니다. HTML에는 raw 패턴만 표시됩니다."
        )
    return None


def build_xrd_html(
    pairs: list[tuple[str, str]],
    *,
    table_files: list[str] | None = None,
    image_files: list[str] | None = None,
    origin: bool = False,
    plot_only: bool = False,
    comment_provider: Callable[[dict[str, Any]], dict[str, str] | None] | None = None,
) -> dict[str, Any]:
    """raw/PDF 입력 쌍에서 XRD Plotly HTML을 생성한다.

    CLI와 웹 미리보기 화면이 같은 보고서 생성 로직을 공유하기 위한 함수다.
    """
    if not pairs:
        raise ValueError("XRD raw/PDF 입력 쌍이 필요합니다.")

    first_stem = os.path.splitext(os.path.basename(pairs[0][0]))[0]
    raw_stems = [os.path.splitext(os.path.basename(r))[0] for r, _ in pairs]

    fig = go.Figure()
    peak_ci = 0
    trace_idx = 0
    group_map = {}
    groups_for_tables = []
    summary = []
    raw_patterns = []
    all_x = []
    warnings = []

    for gi, (raw_txt, pdf_dir) in enumerate(pairs):
        if not os.path.isfile(raw_txt):
            raise FileNotFoundError(f"raw 파일을 찾을 수 없습니다: {raw_txt}")
        if not os.path.isdir(pdf_dir):
            raise FileNotFoundError(f"PDF 폴더를 찾을 수 없습니다: {pdf_dir}")

        raw_stem = os.path.splitext(os.path.basename(raw_txt))[0]
        gid = f"g{gi}"
        raw_color = RAW_LINE_COLORS[gi % len(RAW_LINE_COLORS)]

        rx, ry = load_raw(raw_txt)
        raw_max = max(ry) if ry else 1.0
        raw_patterns.append(
            _raw_pattern_context(
                raw_stem=raw_stem,
                raw_txt=raw_txt,
                rx=rx,
                ry=ry,
                raw_max=raw_max,
            )
        )
        all_x += rx
        idxs = []

        fig.add_trace(
            go.Scatter(
                x=rx,
                y=ry,
                mode="lines",
                name=raw_stem,
                line=dict(color=raw_color, width=RAW_LINE_WIDTH),
            )
        )
        idxs.append(trace_idx)
        trace_idx += 1

        pdf_files = sorted(glob.glob(os.path.join(pdf_dir, "*.pdf")))
        items = []
        for pdf_path in pdf_files:
            try:
                peaks = parse_pdf_peaks(pdf_path)
            except Exception as exc:
                warnings.append(
                    f"경고: '{os.path.basename(pdf_path)}' PDF를 읽지 못했습니다: {exc}"
                )
                continue
            if not peaks:
                continue
            color = PEAK_PALETTE[peak_ci % len(PEAK_PALETTE)]
            peak_ci += 1
            fallback_label = os.path.splitext(os.path.basename(pdf_path))[0]
            metadata = parse_pdf_card_metadata(pdf_path)
            label = phase_label_from_metadata(metadata, fallback_label)
            match = score_phase_candidate(peaks, rx, ry, raw_max)
            category = classify_phase_candidate(match)
            items.append({
                "label": label,
                "color": color,
                "peaks": peaks,
                "metadata": metadata,
                "match": match,
                "category": category,
                "source_pdf": pdf_path,
            })

        assign_relative_phase_categories(items)
        items = sort_phase_candidates(items)
        last_category = items[0]["category"] if items else None
        for item_index, item in enumerate(items):
            category = item["category"]
            if item_index > 0 and category != last_category:
                fig.add_trace(
                    go.Scatter(
                        x=[None],
                        y=[None],
                        mode="lines",
                        name=_phase_category_separator_label(category),
                        line=dict(color="#cbd5e1", width=1),
                        hoverinfo="skip",
                        showlegend=True,
                        meta={"xrd_separator": True, "xrd_category": category},
                    )
                )
                idxs.append(trace_idx)
                trace_idx += 1
                last_category = category

            item["trace_idx"] = trace_idx
            xs, ys, customdata = [], [], []
            for p in item["peaks"]:
                tt, ni, hkl = p["two_theta"], p["norm"], p["hkl"]
                h = ni / 100.0 * raw_max
                xs += [tt, tt, None]
                ys += [0.0, h, None]
                customdata += [(ni, hkl), (ni, hkl), (None, None)]

            fig.add_trace(
                go.Scatter(
                    x=xs,
                    y=ys,
                    mode="lines",
                    name=item["label"],
                    line=dict(color=item["color"], width=1.5),
                    customdata=customdata,
                    hovertemplate=(
                        "2θ = %{x:.3f}°<br>"
                        "Norm. I. = %{customdata[0]:.1f}%<br>"
                        "h k l = %{customdata[1]}<extra>" + item["label"] + "</extra>"
                    ),
                )
            )
            idxs.append(trace_idx)
            trace_idx += 1

        warning = pdf_peak_warning(pdf_dir, len(pdf_files), len(items))
        if warning:
            warnings.append(warning)

        group_map[raw_stem] = idxs
        groups_for_tables.append((raw_stem, raw_color, items))
        summary.append((raw_stem, len(rx), raw_max, items))

    xrange = [min(all_x), max(all_x)] if all_x else None
    title_text = (
        f"XRD Pattern ({first_stem}) with ICDD Card Peaks"
        if len(pairs) == 1 else "XRD Patterns with ICDD Card Peaks"
    )

    if origin:
        fig.update_layout(
            title=dict(
                text=title_text,
                font=dict(family="Arial", size=22, color="black"),
                x=0.5,
                xanchor="center",
            ),
            hovermode="closest",
            autosize=True,
            margin=dict(l=70, r=30, t=60, b=120),
            legend=dict(groupclick="toggleitem"),
        )
        fig.update_xaxes(title_text="2θ (°)", range=xrange)
        fig.update_yaxes(title_text="Intensity (cps)", rangemode="tozero")
    else:
        fig.update_layout(
            title=title_text,
            xaxis_title="2θ (°)",
            yaxis_title="Intensity (cps)",
            template="plotly_white",
            hovermode="closest",
            autosize=True,
            margin=dict(l=60, r=30, t=60, b=120),
            legend=dict(groupclick="toggleitem"),
        )
        fig.update_xaxes(range=xrange)
        fig.update_yaxes(rangemode="tozero")

    raw_line_indices = [idxs[0] for idxs in group_map.values() if idxs]
    highlight_groups = {idxs[0]: idxs for idxs in group_map.values() if idxs}
    sample_name = first_stem if len(pairs) == 1 else ", ".join(raw_stems)
    llm_context = build_xrd_llm_context(
        sample_name=sample_name,
        raw_patterns=raw_patterns,
        groups=groups_for_tables,
        warnings=warnings,
        table_files=table_files or [],
        image_files=image_files or [],
        origin=origin,
    )
    comment_result: dict[str, str] | None = None
    if comment_provider is not None and not plot_only:
        comment_result = comment_provider(llm_context)
    if plot_only:
        tables_html = build_tables_html(groups_for_tables)
        group_toggle_js = build_group_toggle_js("xrd-plot", group_map)
        html_text = fig_to_responsive_html(
            fig,
            div_id="xrd-plot",
            origin=origin,
            legend_breakpoint_px=LEGEND_BREAKPOINT_PX,
            crosshair=True,
            title_edit=True,
            legend_text_edit=True,
            trace_highlight=True,
            highlight_pickable=raw_line_indices,
            highlight_groups=highlight_groups,
            image_filename=first_stem,
            image_format=XRD_DOWNLOAD_IMAGE_FORMAT,
            image_format_selector=XRD_IMAGE_FORMAT_SELECTOR,
            post_body_html=build_xrd_legend_checkbox_js("xrd-plot")
            + group_toggle_js
            + tables_html,
            config=_xrd_plot_config(),
        )
    else:
        html_text = build_report_html(
            fig,
            sample_name=sample_name,
            groups=groups_for_tables,
            group_map=group_map,
            warnings=warnings,
            table_files=table_files or [],
            image_files=image_files or [],
            origin=origin,
            first_stem=first_stem,
            raw_line_indices=raw_line_indices,
            highlight_groups=highlight_groups,
            comment_html=(comment_result or {}).get("html"),
            comment_note=(comment_result or {}).get("note"),
        )

    return {
        "html": html_text,
        "summary": summary,
        "warnings": warnings,
        "first_stem": first_stem,
        "raw_stems": raw_stems,
        "llm_context": llm_context,
        "llm_comment_used": bool(comment_result),
    }


def main():
    args = parse_args()
    pairs = collect_pairs(args)
    table_files, image_files = discover_support_files(
        pairs,
        data_dir=args.data_dir or (args.raw_txt if args.raw_txt and os.path.isdir(args.raw_txt) else None),
        excel_paths=args.excel,
        image_paths=args.image,
    )

    first_stem = os.path.splitext(os.path.basename(pairs[0][0]))[0]
    # 출력 파일명: raw 파일명들을 '_'로 연결하고 끝에 '_result' 를 붙인다.
    # 기본 저장 위치는 -o 미지정 시 현재 실행 위치(cwd).
    raw_stems = [os.path.splitext(os.path.basename(r))[0] for r, _ in pairs]
    if args.output:
        out_html = args.output
    else:
        out_html = os.path.join(os.getcwd(), "_".join(raw_stems) + "_result.html")

    result = build_xrd_html(
        pairs,
        table_files=table_files,
        image_files=image_files,
        origin=args.origin,
        plot_only=args.plot_only,
    )
    with open(out_html, "w", encoding="utf-8") as fh:
        fh.write(result["html"])
    summary = result["summary"]
    for warning in result["warnings"]:
        print(warning)

    print(f"Saved: {out_html}")
    if table_files:
        print("Included table files:")
        for path in table_files:
            print(f"    {path}")
    if image_files:
        print("Included image files:")
        for path in image_files:
            print(f"    {path}")
    for raw_stem, n_points, raw_max, items in summary:
        print(f"[{raw_stem}] raw points: {n_points}, raw_max: {raw_max:.1f}")
        for item in items:
            print(
                f"    {item['label']}: {len(item['peaks'])} peaks, "
                f"match={item['match']['score']:.1f}%, category={item['category']}"
            )


def parse_args():
    parser = argparse.ArgumentParser(
        description="XRD raw 데이터와 ICDD Card PDF 피크를 Plotly로 시각화한다."
    )
    parser.add_argument(
        "raw_txt", nargs="?", default=None,
        help="raw .txt 파일 경로. 폴더를 주면 data_dir 로 간주(내부 .txt 자동 인식).",
    )
    parser.add_argument(
        "pdf_dir", nargs="?", default=None,
        help="ICDD Card PDF 폴더 경로 (생략 시 raw 파일명과 같은 이름의 폴더를 자동 사용)",
    )
    parser.add_argument(
        "--data-dir", default=None,
        help="폴더 안의 모든 .txt 를 raw 로, 같은 이름 폴더를 PDF 로 자동 인식한다.",
    )
    parser.add_argument(
        "--pair", action="append", nargs=2, metavar=("RAW", "PDF"),
        help="raw.txt 와 pdf 폴더 한 쌍. 여러 raw 파일을 겹쳐 그리려면 반복 사용.",
    )
    parser.add_argument(
        "--excel", action="append", default=[],
        help="보고서 피크 정보 영역에 표시할 Excel/CSV 파일(.xlsx/.csv/.tsv). 반복 지정 가능.",
    )
    parser.add_argument(
        "--image", action="append", default=[],
        help="보고서 그래프/상매칭 보조 이미지 영역에 표시할 이미지(.png/.jpg/.jpeg/.webp/.gif). 반복 지정 가능.",
    )
    parser.add_argument(
        "-o", "--output", default=None,
        help="출력 HTML 경로 (기본: 실행 위치에 raw파일명들을 '_'로 연결한 'A_B_result.html')",
    )
    parser.add_argument(
        "--origin", action="store_true",
        help="Origin(OriginLab) 논문 스타일로 그린다 (기본: 원래 디자인)",
    )
    parser.add_argument(
        "--plot-only", action="store_true",
        help="보고서 양식 없이 기존처럼 Plotly 그래프와 피크 표만 저장한다.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()

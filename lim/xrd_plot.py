"""XRD raw 데이터(.txt)를 Plotly로 그리고, ICDD Card PDF의 피크 표를
2θ 위치에 Norm. I.(0~100%) 높이의 수직 막대로 오버레이한다.
또한 (AX) XRD Report 양식의 구조에 맞춰 그래프, 분석결과,
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
import unicodedata
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
    "major": "주요상 (Major Phases)",
    "uncertain": "유사/불확실상 (Uncertain / Similar Phases)",
    "minor": "미량상 후보 (Minor Phase Candidates)",
}

PHASE_CATEGORY_SHORT_LABELS = {
    "major": "주요상",
    "uncertain": "유사상",
    "minor": "미량상",
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
    left = phase_name or fallback
    if formula:
        left = f"{left} ({formula})"
    right = card_no
    if right and quality:
        right = f"{right}({quality})"
    return f"{left} / {right}" if right else left


def normalize_card_no(value: str) -> str:
    """Card No를 파일명/CSV/PDF 표기 차이와 무관하게 비교할 수 있게 정규화한다."""
    text = str(value or "").strip()
    text = re.sub(r"\([A-Z]\)\s*$", "", text)
    text = text.replace("PDF Card No.", "")
    text = re.sub(r"[^0-9A-Za-z]+", "", text)
    return text.upper()


def split_card_numbers(value: str) -> list[str]:
    cards = []
    for part in re.split(r"[,;/\n]+", str(value or "")):
        card = normalize_card_no(part)
        if card and card not in cards:
            cards.append(card)
    return cards


_HANGUL_RE = re.compile(r"[\u3131-\u318e\uac00-\ud7a3]")


def _hangul_count(value: str) -> int:
    return len(_HANGUL_RE.findall(str(value or "")))


def repair_korean_mojibake(value: str) -> str:
    """Repair common ZIP/browser filename mojibake while leaving normal names untouched."""
    text = str(value or "")
    best = unicodedata.normalize("NFC", text)
    best_score = _hangul_count(best)
    for encoded_as in ("cp437", "latin1", "cp1252"):
        for decoded_as in ("utf-8", "cp949", "euc-kr"):
            try:
                candidate = text.encode(encoded_as).decode(decoded_as)
            except UnicodeError:
                continue
            candidate = unicodedata.normalize("NFC", candidate)
            score = _hangul_count(candidate)
            if score > best_score:
                best = candidate
                best_score = score
    return best


def _path_text(value: str) -> str:
    return re.sub(r"\s+", "", repair_korean_mojibake(value).lower())


def _display_path_part(value: str) -> str:
    return repair_korean_mojibake(value).strip()


def _phase_folder_category(part: str) -> tuple[str | None, str | None]:
    text = _path_text(part)
    display_part = _display_path_part(part)
    if not text:
        return None, None

    has_major = "주요" in text or "major" in text
    has_similar = (
        "유사" in text
        or "불확실" in text
        or "similar" in text
        or "uncertain" in text
    )
    # "주요상_유사상 Case" 같은 상위 설명 폴더는 실제 분류 폴더가 아니므로
    # 그 아래의 더 구체적인 "주요상", "유사상 1" 폴더가 우선되게 건너뛴다.
    if has_major and has_similar:
        return None, None
    if "미량" in text or "minor" in text or "trace" in text:
        return "minor", display_part
    if has_similar:
        return "uncertain", display_part
    if has_major:
        return "major", display_part
    return None, None


def phase_category_from_pdf_path(pdf_path: str, pdf_dir: str) -> tuple[str | None, str, str]:
    """PDF 상대 폴더명에서 주요상/유사상/미량상 분류와 그룹명을 읽는다.

    주요상은 PDF 루트에 두는 규칙이므로 하위 폴더가 없으면 major로 본다.
    유사상/미량상은 가장 깊은 폴더명에 적힌 구분을 우선한다.
    """
    try:
        rel = Path(pdf_path).resolve().relative_to(Path(pdf_dir).resolve())
    except ValueError:
        rel = Path(pdf_path).name
        parts: tuple[str, ...] = ()
    else:
        parts = rel.parts[:-1]

    if not parts:
        return "major", PHASE_CATEGORY_SHORT_LABELS["major"], "folder"

    for part in reversed(parts):
        category, display_part = _phase_folder_category(part)
        if category and display_part:
            return category, display_part, "folder"

    if any("icdd" in _path_text(part) for part in parts):
        return "major", PHASE_CATEGORY_SHORT_LABELS["major"], "folder"

    return None, "자동 분류", "score"


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
        if item.get("category_locked"):
            continue
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


def _phase_folder_group_key(item: dict[str, Any]) -> str:
    folder_group = str(item.get("folder_group") or "")
    if not folder_group or folder_group == "자동 분류":
        folder_group = PHASE_CATEGORY_SHORT_LABELS.get(str(item.get("category") or ""), "")
    return folder_group


def sort_phase_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            _PHASE_CATEGORY_ORDER.get(str(item.get("category") or ""), 99),
            _phase_folder_group_key(item),
            _phase_similarity_key(item),
            -float((item.get("match") or {}).get("score") or 0),
        ),
    )


def _phase_category_separator_label(category: str) -> str:
    title = PHASE_GROUPS.get(category, category)
    return f"──────── {title}"


def _phase_section_separator_label(item: dict[str, Any]) -> str:
    category = str(item.get("category") or "")
    title = PHASE_GROUPS.get(category, category)
    folder_group = _phase_folder_group_key(item)
    if folder_group and folder_group not in {PHASE_CATEGORY_SHORT_LABELS.get(category), title}:
        if category == "uncertain":
            return f"──────── {folder_group} (Similar Phases)"
        if category == "major":
            return f"──────── {folder_group} (Major Phases)"
        if category == "minor":
            return f"──────── {folder_group} (Minor Phase Candidates)"
        return f"──────── {folder_group}"
    return f"──────── {title}"


def _xrd_plot_config() -> dict[str, Any]:
    return {
        "editable": False,
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
  function traceMeta(trace) {{
    return (trace && trace.meta && typeof trace.meta === "object") ? trace.meta : {{}};
  }}
  function visibleLegendTraceIndexes() {{
    var fd = gd._fullData || gd.data || [];
    var idxs = [];
    for (var i = 0; i < fd.length; i++) {{
      var tr = fd[i];
      if (!tr || tr.showlegend === false) continue;
      idxs.push(typeof tr.index === "number" ? tr.index : i);
    }}
    return idxs;
  }}
  function legendTraceItems() {{
    return Array.prototype.slice.call(
      gd.querySelectorAll("g.legend g.traces")
    ).filter(function(node) {{
      return node.querySelector("text.legendtext");
    }});
  }}
  function curveFromLegendDatum(value, depth) {{
    if (value == null || depth > 4) return null;
    if (typeof value === "number" || typeof value === "string") {{
      var direct = Number(value);
      return Number.isInteger(direct) ? direct : null;
    }}
    if (Array.isArray(value)) {{
      for (var a = 0; a < value.length; a++) {{
        var arrayCurve = curveFromLegendDatum(value[a], depth + 1);
        if (arrayCurve != null) return arrayCurve;
      }}
      return null;
    }}
    if (typeof value !== "object") return null;
    var explicitKeys = ["curveNumber", "curveIndex", "traceIndex"];
    for (var k = 0; k < explicitKeys.length; k++) {{
      var explicit = Number(value[explicitKeys[k]]);
      if (Number.isInteger(explicit)) return explicit;
    }}
    if (value.trace) {{
      var traceCurve = curveFromLegendDatum(value.trace, depth + 1);
      if (traceCurve != null) return traceCurve;
    }}
    if (value._fullInput) {{
      var inputCurve = curveFromLegendDatum(value._fullInput, depth + 1);
      if (inputCurve != null) return inputCurve;
    }}
    if ((value.meta || value.name != null || value.x || value.y)
        && Number.isInteger(Number(value.index))) {{
      return Number(value.index);
    }}
    return null;
  }}
  function curveFromLegendItem(item) {{
    if (!item) return null;
    var datumCurve = curveFromLegendDatum(item.__data__, 0);
    if (datumCurve != null) return datumCurve;
    var items = legendTraceItems();
    var pos = items.indexOf(item);
    if (pos < 0) return null;
    var idxs = visibleLegendTraceIndexes();
    var curve = Number(idxs[pos]);
    return Number.isInteger(curve) ? curve : null;
  }}
  function legendKind(row) {{
    var curve = curveFromLegendItem(row);
    var trace = curve != null ? (gd.data || [])[curve] : null;
    var meta = traceMeta(trace);
    if (meta.xrd_raw) return "raw";
    if (meta.xrd_separator) return "separator";
    if (meta.xrd_phase_candidate) return "phase";
    return "";
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
  function ensureBranch(row) {{
    var branch = row.querySelector(".rist-xrd-legend-branch");
    if (!branch) {{
      branch = svg("g");
      branch.setAttribute("class", "rist-xrd-legend-branch");
      var path = svg("path");
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", "#cbd5e1");
      path.setAttribute("stroke-width", "1.4");
      path.setAttribute("stroke-linecap", "round");
      branch.appendChild(path);
      row.insertBefore(branch, row.firstChild);
    }}
    return branch;
  }}
  function removeBranch(row) {{
    var branch = row.querySelector(".rist-xrd-legend-branch");
    if (branch && branch.parentNode) branch.parentNode.removeChild(branch);
  }}
  function baseTextX(row, textNode) {{
    var stored = row.getAttribute("data-rist-xrd-legend-text-x");
    if (stored != null) return Number(stored) || 40;
    var current = Number(textNode.getAttribute("x") || 40);
    row.setAttribute("data-rist-xrd-legend-text-x", String(current));
    return current;
  }}
  function restoreTextX(row, textNode) {{
    var tx = baseTextX(row, textNode);
    textNode.setAttribute("x", String(tx));
  }}
  function placeBranch(branch, row, textNode, indent) {{
    var tx = baseTextX(row, textNode);
    var ty = Number(textNode.getAttribute("y") || 0);
    branch.setAttribute("transform", "translate(" + (tx + indent - 14) + "," + (ty - 8) + ")");
    var path = branch.querySelector("path");
    path.setAttribute("d", "M0 0 V12 M0 8 H10");
  }}
  function placeCheckbox(mark, row, textNode, indent) {{
    var tx = baseTextX(row, textNode);
    var ty = Number(textNode.getAttribute("y") || 0);
    var offset = Number(indent || 0);
    textNode.setAttribute("x", String(tx + offset + 18));
    mark.setAttribute("transform", "translate(" + (tx + offset + 2) + "," + (ty - 10) + ")");
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
      var kind = legendKind(row);
      var isChild = kind === "phase" || kind === "separator";
      var indent = isChild ? 22 : 0;
      if (base.trim().indexOf("────────") === 0) {{
        removeCheckbox(row);
        if (isChild) {{
          var separatorBranch = ensureBranch(row);
          placeBranch(separatorBranch, row, node, indent);
          node.setAttribute("x", String(baseTextX(row, node) + indent + 8));
        }} else {{
          removeBranch(row);
          restoreTextX(row, node);
        }}
        node.textContent = base;
        node.style.fill = "#94a3b8";
        node.style.fontSize = "11px";
        node.style.fontWeight = "600";
        node.style.opacity = "1";
        node.style.textDecoration = "none";
        return;
      }}
      if (isChild) {{
        var branch = ensureBranch(row);
        placeBranch(branch, row, node, indent);
        node.style.fontSize = "11px";
        node.style.fontWeight = "400";
        node.style.fill = "#334155";
      }} else {{
        removeBranch(row);
        node.style.fontSize = kind === "raw" ? "12px" : "";
        node.style.fontWeight = kind === "raw" ? "700" : "";
        node.style.fill = kind === "raw" ? "#172a46" : "";
      }}
      var mark = ensureCheckbox(row);
      placeCheckbox(mark, row, node, indent);
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


def build_xrd_axis_text_guard_js(div_id: str) -> str:
    """XRD에서는 Plotly 축/tick 텍스트가 클릭 편집 대상으로 잡히지 않게 막는다."""
    return f"""
<style>
#{div_id} .xaxislayer-above,
#{div_id} .xaxislayer-below,
#{div_id} .yaxislayer-above,
#{div_id} .yaxislayer-below,
#{div_id} .g-xtitle,
#{div_id} .g-ytitle {{
  pointer-events: none !important;
  user-select: none !important;
}}
</style>
<script>
(function() {{
  var gd = document.getElementById("{div_id}");
  if (!gd) return;
  gd.classList.add("xrd-axis-text-guard");
  function isAxisTextTarget(target) {{
    return Boolean(target && target.closest && target.closest(
      ".xaxislayer-above,.xaxislayer-below,"
      + ".yaxislayer-above,.yaxislayer-below,"
      + ".g-xtitle,.g-ytitle,.xtick,.ytick"
    ));
  }}
  function blockAxisTextEdit(event) {{
    if (!isAxisTextTarget(event.target)) return;
    event.preventDefault();
    event.stopPropagation();
  }}
  ["click", "dblclick", "mousedown", "pointerdown", "touchstart"].forEach(function(name) {{
    gd.addEventListener(name, blockAxisTextEdit, true);
  }});
}})();
</script>
"""


def build_xrd_phase_group_editor_js(div_id: str) -> str:
    """XRD phase 후보 단위 그룹/범례 편집 패널을 추가한다."""
    snippet = r"""
<style>
#__DIV_ID__ .xrd-phase-group-button {
  order: 24;
  border: 1px solid #c7d0dd;
  border-radius: 4px;
  background: rgba(255,255,255,0.94);
  color: #1f2933;
  cursor: pointer;
  font: 12px Arial, sans-serif;
  padding: 5px 9px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.08);
}
#__DIV_ID__ .xrd-phase-group-panel {
  position: absolute;
  top: 94px;
  right: 30px;
  z-index: 22;
  display: none;
  width: min(460px, calc(100% - 16px));
  max-width: calc(100% - 16px);
  max-height: min(520px, calc(100% - 54px));
  overflow: auto;
  overflow-x: hidden;
  background: rgba(255,255,255,0.98);
  border: 1px solid #c7d0dd;
  border-radius: 6px;
  box-shadow: 0 4px 18px rgba(0,0,0,0.16);
  box-sizing: border-box;
  color: #1f2933;
  font: 12px Arial, sans-serif;
}
#__DIV_ID__ .xrd-phase-group-head {
  position: sticky;
  top: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px;
  border-bottom: 1px solid #d7dee8;
  background: rgba(255,255,255,0.99);
  font-weight: 700;
}
#__DIV_ID__ .xrd-phase-group-close {
  border: 0;
  background: transparent;
  color: #52606d;
  cursor: pointer;
  font: 18px Arial, sans-serif;
  line-height: 1;
}
#__DIV_ID__ .xrd-phase-group-controls {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 34px auto auto;
  gap: 6px;
  align-items: center;
  padding: 10px;
  border-bottom: 1px solid #e4e9f0;
  background: #f8fafc;
}
#__DIV_ID__ .xrd-phase-group-name,
#__DIV_ID__ .xrd-phase-label-input,
#__DIV_ID__ .xrd-phase-group-title-input {
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  background: #fff;
  color: #1f2933;
  font: 12px Arial, sans-serif;
  padding: 6px 7px;
}
#__DIV_ID__ .xrd-phase-group-color {
  width: 30px;
  height: 30px;
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  background: #fff;
  padding: 2px;
}
#__DIV_ID__ .xrd-phase-group-apply,
#__DIV_ID__ .xrd-phase-selection-clear,
#__DIV_ID__ .xrd-phase-group-clear {
  border: 1px solid #a8bbd3;
  border-radius: 4px;
  background: #fff;
  color: #1f2933;
  cursor: pointer;
  font: 12px Arial, sans-serif;
  padding: 6px 8px;
  white-space: nowrap;
}
#__DIV_ID__ .xrd-phase-group-apply {
  border-color: #2563eb;
  color: #1d4ed8;
  font-weight: 700;
}
#__DIV_ID__ .xrd-phase-group-apply:disabled {
  border-color: #cbd5e1;
  color: #94a3b8;
  cursor: not-allowed;
}
#__DIV_ID__ .xrd-phase-group-body {
  padding: 8px 10px 10px;
}
#__DIV_ID__ .xrd-phase-section {
  margin: 0 0 10px;
  border: 1px solid #e4e9f0;
  border-radius: 6px;
  overflow: hidden;
}
#__DIV_ID__ .xrd-phase-section-head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  gap: 6px;
  align-items: center;
  padding: 7px 8px;
  background: #eef4fb;
  border-bottom: 1px solid #e4e9f0;
  color: #1f3b73;
  font-weight: 700;
}
#__DIV_ID__ .xrd-phase-section-head.is-manual {
  background: #ecfdf5;
  color: #166534;
}
#__DIV_ID__ .xrd-phase-section-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
#__DIV_ID__ .xrd-phase-row {
  display: grid;
  grid-template-columns: auto 16px minmax(0, 1fr);
  gap: 7px;
  align-items: center;
  padding: 7px 8px;
  border-bottom: 1px solid #eef2f7;
  background: #fff;
}
#__DIV_ID__ .xrd-phase-row:last-child {
  border-bottom: 0;
}
#__DIV_ID__ .xrd-phase-row:hover {
  background: #f8fafc;
}
#__DIV_ID__ .xrd-phase-row input[type="checkbox"] {
  width: 15px;
  height: 15px;
  margin: 0;
  accent-color: #2563eb;
}
#__DIV_ID__ .xrd-phase-color-chip {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  border: 1px solid rgba(15, 23, 42, 0.25);
}
#__DIV_ID__ .xrd-phase-group-empty {
  padding: 12px;
  color: #64748b;
  text-align: center;
}
</style>
<script>
(function() {
  var gd = document.getElementById(__DIV_JSON__);
  if (!gd || !window.Plotly) return;

  function ensureToolbar() {
    if (getComputedStyle(gd).position === "static") gd.style.position = "relative";
    var toolbar = gd.querySelector(".rist-plot-control-row");
    if (!toolbar) {
      toolbar = document.createElement("div");
      toolbar.className = "rist-plot-control-row";
      gd.appendChild(toolbar);
    }
    return toolbar;
  }

  function traceMeta(trace) {
    return (trace && trace.meta && typeof trace.meta === "object") ? trace.meta : {};
  }

  function phaseCurves() {
    return (gd.data || []).map(function(trace, curve) {
      return { trace: trace, curve: curve, meta: traceMeta(trace) };
    }).filter(function(item) {
      return item.meta.xrd_phase_candidate === true;
    });
  }

  function originalColor(item) {
    var meta = item.meta;
    if (!meta.xrd_original_color) {
      meta.xrd_original_color = (item.trace.line && item.trace.line.color) || "#64748b";
    }
    return meta.xrd_original_color;
  }

  function currentColor(item) {
    return (item.trace.line && item.trace.line.color)
      || item.meta.xrd_manual_phase_color
      || item.meta.xrd_original_color
      || "#64748b";
  }

  function phaseSortKey(item) {
    var meta = item.meta;
    var manual = meta.xrd_manual_phase_group || "";
    var category = meta.xrd_phase_category || "";
    var groupKey = meta.xrd_phase_group_key || "";
    return [
      manual ? "0:" + manual : "1:",
      category,
      groupKey,
      String(item.trace.name || "")
    ].join("|").toLowerCase();
  }

  function buttonText(count) {
    return "그룹 적용" + (count ? " (" + count + ")" : "");
  }

  function dispatchPhaseGroupChange(curves) {
    gd.dispatchEvent(new CustomEvent("xrd-phase-group-change", {
      detail: { curves: curves }
    }));
  }

  function restyleLabel(curve, value) {
    window.Plotly.restyle(gd, { name: value || "Phase" }, [curve]);
  }

  function applyGroup(curves, name, color) {
    if (!curves.length || !name) return Promise.resolve();
    var groupKey = "xrd-phase-group-" + name.toLowerCase().replace(/[^0-9a-z가-힣]+/g, "-");
    curves.forEach(function(curve) {
      var item = phaseCurves().filter(function(entry) { return entry.curve === curve; })[0];
      if (!item) return;
      originalColor(item);
      item.meta.xrd_manual_phase_group = name;
      item.meta.xrd_manual_phase_color = color;
    });
    return window.Plotly.restyle(gd, {
      "line.color": color,
      "legendgroup": groupKey,
      "legendgrouptitle.text": name
    }, curves).then(function() {
      dispatchPhaseGroupChange(curves);
    });
  }

  function clearGroup(groupName) {
    var curves = phaseCurves().filter(function(item) {
      return item.meta.xrd_manual_phase_group === groupName;
    });
    var indices = curves.map(function(item) { return item.curve; });
    if (!indices.length) return Promise.resolve();
    var colors = curves.map(function(item) {
      var color = originalColor(item);
      delete item.meta.xrd_manual_phase_group;
      delete item.meta.xrd_manual_phase_color;
      return color;
    });
    return window.Plotly.restyle(gd, {
      "line.color": colors,
      "legendgroup": "",
      "legendgrouptitle.text": ""
    }, indices).then(function() {
      dispatchPhaseGroupChange(indices);
    });
  }

  function recolorGroup(groupName, color) {
    var curves = phaseCurves().filter(function(item) {
      return item.meta.xrd_manual_phase_group === groupName;
    });
    var indices = curves.map(function(item) { return item.curve; });
    if (!indices.length) return Promise.resolve();
    curves.forEach(function(item) {
      originalColor(item);
      item.meta.xrd_manual_phase_color = color;
    });
    return window.Plotly.restyle(gd, { "line.color": color }, indices).then(function() {
      dispatchPhaseGroupChange(indices);
    });
  }

  function renameGroup(groupName, nextName) {
    var clean = String(nextName || "").trim();
    if (!clean || clean === groupName) return Promise.resolve();
    var curves = phaseCurves().filter(function(item) {
      return item.meta.xrd_manual_phase_group === groupName;
    });
    var indices = curves.map(function(item) { return item.curve; });
    if (!indices.length) return Promise.resolve();
    curves.forEach(function(item) {
      item.meta.xrd_manual_phase_group = clean;
    });
    var groupKey = "xrd-phase-group-" + clean.toLowerCase().replace(/[^0-9a-z가-힣]+/g, "-");
    return window.Plotly.restyle(gd, {
      "legendgroup": groupKey,
      "legendgrouptitle.text": clean
    }, indices).then(function() {
      dispatchPhaseGroupChange(indices);
    });
  }

  function render(body, applyButton) {
    var items = phaseCurves().sort(function(a, b) {
      return phaseSortKey(a).localeCompare(phaseSortKey(b), "ko");
    });
    body.innerHTML = "";
    if (!items.length) {
      var empty = document.createElement("div");
      empty.className = "xrd-phase-group-empty";
      empty.textContent = "편집할 phase 후보가 없습니다.";
      body.appendChild(empty);
      applyButton.textContent = buttonText(0);
      applyButton.disabled = true;
      return;
    }

    var manualGroups = {};
    var ungrouped = [];
    items.forEach(function(item) {
      var name = item.meta.xrd_manual_phase_group;
      if (name) {
        if (!manualGroups[name]) manualGroups[name] = [];
        manualGroups[name].push(item);
      } else {
        ungrouped.push(item);
      }
    });

    Object.keys(manualGroups).sort(function(a, b) { return a.localeCompare(b, "ko"); }).forEach(function(groupName) {
      appendSection(body, groupName, manualGroups[groupName], true);
    });
    if (ungrouped.length) appendSection(body, "미그룹 phase 후보", ungrouped, false);

    updateApplyButton(applyButton);
  }

  function appendSection(body, title, items, manual) {
    var section = document.createElement("section");
    section.className = "xrd-phase-section";
    var head = document.createElement("div");
    head.className = "xrd-phase-section-head" + (manual ? " is-manual" : "");

    if (manual) {
      var titleInput = document.createElement("input");
      titleInput.className = "xrd-phase-group-title-input";
      titleInput.value = title;
      titleInput.title = "그룹명";
      titleInput.addEventListener("change", function() {
        renameGroup(title, titleInput.value).then(function() {
          renderCurrentPanel();
        });
      });
      var color = document.createElement("input");
      color.className = "xrd-phase-group-color";
      color.type = "color";
      color.value = items[0] ? currentColor(items[0]) : "#2563eb";
      color.title = "그룹 색상";
      color.addEventListener("input", function() {
        recolorGroup(title, color.value);
      });
      var clear = document.createElement("button");
      clear.className = "xrd-phase-group-clear";
      clear.type = "button";
      clear.title = "그룹 해제";
      clear.textContent = "해제";
      clear.addEventListener("click", function() {
        clearGroup(title).then(function() {
          renderCurrentPanel();
        });
      });
      head.appendChild(titleInput);
      head.appendChild(color);
      head.appendChild(clear);
    } else {
      var label = document.createElement("div");
      label.className = "xrd-phase-section-title";
      label.textContent = title;
      var count = document.createElement("span");
      count.textContent = items.length + "개";
      head.appendChild(label);
      head.appendChild(count);
      head.appendChild(document.createElement("span"));
    }

    section.appendChild(head);
    items.forEach(function(item) {
      section.appendChild(phaseRow(item));
    });
    body.appendChild(section);
  }

  function phaseRow(item) {
    var row = document.createElement("div");
    row.className = "xrd-phase-row";
    var check = document.createElement("input");
    check.type = "checkbox";
    check.className = "xrd-phase-select";
    check.dataset.curve = String(item.curve);
    check.addEventListener("change", function() {
      updateApplyButton(currentApplyButton);
    });
    var chip = document.createElement("span");
    chip.className = "xrd-phase-color-chip";
    chip.style.backgroundColor = currentColor(item);
    var input = document.createElement("input");
    input.className = "xrd-phase-label-input";
    input.value = item.trace.name || "";
    input.title = "범례 이름";
    input.addEventListener("change", function() {
      restyleLabel(item.curve, input.value);
    });
    row.appendChild(check);
    row.appendChild(chip);
    row.appendChild(input);
    return row;
  }

  function selectedCurves() {
    return Array.prototype.slice.call(gd.querySelectorAll(".xrd-phase-select:checked"))
      .map(function(node) { return parseInt(node.dataset.curve, 10); })
      .filter(function(value) { return Number.isFinite(value); });
  }

  var currentBody = null;
  var currentApplyButton = null;
  function renderCurrentPanel() {
    if (currentBody && currentApplyButton) render(currentBody, currentApplyButton);
  }

  function updateApplyButton(button) {
    if (!button) return;
    var count = selectedCurves().length;
    button.textContent = buttonText(count);
    button.disabled = count === 0;
  }

  function install() {
    if (gd.__xrdPhaseGroupEditorInstalled) return;
    gd.__xrdPhaseGroupEditorInstalled = true;
    var toolbar = ensureToolbar();
    var button = document.createElement("button");
    button.type = "button";
    button.className = "xrd-phase-group-button";
    button.textContent = "상 그룹 편집";
    toolbar.appendChild(button);

    var panel = document.createElement("div");
    panel.className = "xrd-phase-group-panel";
    panel.innerHTML = ""
      + "<div class='xrd-phase-group-head'>"
      + "<span>상 그룹 편집</span>"
      + "<button type='button' class='xrd-phase-group-close' aria-label='close'>×</button>"
      + "</div>"
      + "<div class='xrd-phase-group-controls'>"
      + "<input class='xrd-phase-group-name' type='text' placeholder='그룹명'>"
      + "<input class='xrd-phase-group-color' type='color' value='#2563eb' title='그룹 색상'>"
      + "<button class='xrd-phase-group-apply' type='button' disabled>그룹 적용</button>"
      + "<button class='xrd-phase-selection-clear' type='button'>선택 해제</button>"
      + "</div>"
      + "<div class='xrd-phase-group-body'></div>";
    gd.appendChild(panel);

    var body = panel.querySelector(".xrd-phase-group-body");
    var nameInput = panel.querySelector(".xrd-phase-group-name");
    var colorInput = panel.querySelector(".xrd-phase-group-color");
    var applyButton = panel.querySelector(".xrd-phase-group-apply");
    currentBody = body;
    currentApplyButton = applyButton;

    button.addEventListener("click", function() {
      var other = gd.querySelector(".rist-legend-edit-panel");
      if (other) other.style.display = "none";
      var open = panel.style.display === "block";
      panel.style.display = open ? "none" : "block";
      if (!open) render(body, applyButton);
    });
    panel.querySelector(".xrd-phase-group-close").addEventListener("click", function() {
      panel.style.display = "none";
    });
    panel.querySelector(".xrd-phase-selection-clear").addEventListener("click", function() {
      panel.querySelectorAll(".xrd-phase-select:checked").forEach(function(node) {
        node.checked = false;
      });
      updateApplyButton(applyButton);
    });
    applyButton.addEventListener("click", function() {
      var name = String(nameInput.value || "").trim();
      if (!name) {
        nameInput.focus();
        return;
      }
      var curves = selectedCurves();
      applyGroup(curves, name, colorInput.value).then(function() {
        nameInput.value = "";
        render(body, applyButton);
      });
    });
    if (gd.on) {
      gd.on("plotly_afterplot", function() {
        if (panel.style.display === "block") render(body, applyButton);
      });
      gd.on("plotly_restyle", function() {
        if (panel.style.display === "block") render(body, applyButton);
      });
    }
  }

  install();
})();
</script>
"""
    return (
        snippet.replace("__DIV_ID__", div_id)
        .replace("__DIV_JSON__", json.dumps(div_id, ensure_ascii=False))
    )


def build_xrd_tool_drawer_js(div_id: str) -> str:
    """XRD 그래프 우상단 컨트롤을 FTIR/Raman처럼 도구 팝업으로 접는다."""
    snippet = r"""
<style>
#__DIV_ID__ .rist-plot-control-row.xrd-tool-drawer-installed {
  top: 58px;
  right: 30px;
  z-index: 32;
  display: block;
  width: auto;
  min-width: 0;
}
#__DIV_ID__ .xrd-tool-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 54px;
  height: 30px;
  border: 1px solid #a8bbd3;
  border-radius: 5px;
  background: rgba(255,255,255,0.96);
  color: #1f2933;
  cursor: pointer;
  font: bold 12px Arial, sans-serif;
  padding: 0 12px;
  box-shadow: 0 2px 8px rgba(15,23,42,0.14);
}
#__DIV_ID__ .xrd-tool-toggle:hover {
  border-color: #7891a8;
  background: #f5f7fa;
}
#__DIV_ID__ .xrd-tool-toggle.is-open {
  border-color: #2563eb;
  color: #1d4ed8;
}
#__DIV_ID__ .xrd-tool-panel {
  position: absolute;
  top: 38px;
  right: 0;
  z-index: 33;
  display: none;
  width: min(390px, calc(100vw - 42px));
  max-width: calc(100vw - 42px);
  border: 1px solid #c7d0dd;
  border-radius: 7px;
  background: rgba(255,255,255,0.96);
  box-shadow: 0 8px 24px rgba(15,23,42,0.18);
  box-sizing: border-box;
  color: #1f2933;
  font: 12px Arial, sans-serif;
  overflow: hidden;
}
#__DIV_ID__ .xrd-tool-panel.is-open {
  display: block;
}
#__DIV_ID__ .xrd-tool-panel-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 9px;
  border-bottom: 1px solid #d7dee8;
  background: rgba(248,250,252,0.98);
  font-weight: 700;
  user-select: none;
}
#__DIV_ID__ .xrd-tool-opacity-slider {
  flex: 1 1 auto;
  min-width: 72px;
  height: 16px;
  margin: 0 2px 0 auto;
  accent-color: #52606d;
  cursor: pointer;
}
#__DIV_ID__ .xrd-tool-close {
  flex: 0 0 auto;
  border: 0;
  background: transparent;
  color: #52606d;
  cursor: pointer;
  font: 18px Arial, sans-serif;
  line-height: 1;
  padding: 0 3px;
}
#__DIV_ID__ .xrd-tool-panel-body {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  max-height: min(260px, calc(100vh - 180px));
  overflow: auto;
  padding: 10px;
  box-sizing: border-box;
}
#__DIV_ID__ .xrd-tool-panel-body > * {
  order: 0 !important;
}
#__DIV_ID__ .xrd-tool-panel-body .rist-history-controls,
#__DIV_ID__ .xrd-tool-panel-body .rist-legend-bulk-controls {
  display: flex;
  gap: 8px;
  align-items: center;
}
#__DIV_ID__ .xrd-tool-panel-body .rist-history-controls {
  order: 10 !important;
}
#__DIV_ID__ .xrd-tool-panel-body .rist-legend-bulk-controls {
  order: 20 !important;
}
#__DIV_ID__ .xrd-tool-panel-body .rist-legend-edit-button {
  order: 30 !important;
}
#__DIV_ID__ .xrd-tool-panel-body .xrd-phase-group-button {
  order: 40 !important;
}
#__DIV_ID__ .xrd-tool-panel-body .rist-legend-edit-button,
#__DIV_ID__ .xrd-tool-panel-body .xrd-phase-group-button {
  margin: 0;
}
@media (max-width: 640px) {
  #__DIV_ID__ .rist-plot-control-row.xrd-tool-drawer-installed {
    top: 50px;
    right: 12px;
  }
  #__DIV_ID__ .xrd-tool-panel {
    width: min(340px, calc(100vw - 24px));
    max-width: calc(100vw - 24px);
  }
}
</style>
<script>
(function() {
  var gd = document.getElementById(__DIV_JSON__);
  if (!gd) return;

  function install() {
    var toolbar = gd.querySelector(".rist-plot-control-row");
    if (!toolbar) {
      window.setTimeout(install, 50);
      return;
    }
    if (toolbar.__xrdToolDrawerInstalled) return;
    toolbar.__xrdToolDrawerInstalled = true;
    Array.prototype.slice.call(
      toolbar.querySelectorAll(".xrd-tool-toggle,.xrd-tool-panel")
    ).forEach(function(node) { node.remove(); });
    toolbar.classList.remove("xrd-tool-drawer-installed");
    toolbar.classList.add("xrd-tool-drawer-installed");

    var existing = Array.prototype.slice.call(toolbar.children);
    var toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "xrd-tool-toggle";
    toggle.textContent = "도구";
    toggle.setAttribute("aria-expanded", "false");
    toggle.title = "그래프 도구";

    var panel = document.createElement("div");
    panel.className = "xrd-tool-panel";
    panel.innerHTML = ""
      + "<div class='xrd-tool-panel-head'>"
      + "<span>그래프 도구</span>"
      + "<input class='xrd-tool-opacity-slider' type='range' min='45' max='100' value='96' title='도구창 투명도'>"
      + "<button type='button' class='xrd-tool-close' aria-label='close'>×</button>"
      + "</div>"
      + "<div class='xrd-tool-panel-body'></div>";
    var body = panel.querySelector(".xrd-tool-panel-body");
    var opacity = panel.querySelector(".xrd-tool-opacity-slider");
    var close = panel.querySelector(".xrd-tool-close");

    function isDrawerNode(node) {
      return !!(
        node
        && node.nodeType === 1
        && (
          node.classList.contains("xrd-tool-toggle")
          || node.classList.contains("xrd-tool-panel")
        )
      );
    }

    function moveIntoPanel(node) {
      if (!node || node.nodeType !== 1 || isDrawerNode(node) || node.parentNode === body) return;
      body.appendChild(node);
    }

    function controlRank(node) {
      if (!node || !node.matches) return 999;
      if (node.matches(".rist-history-controls")) return 10;
      if (node.matches(".rist-legend-bulk-controls")) return 20;
      if (node.matches(".rist-legend-edit-button")) return 30;
      if (node.matches(".xrd-phase-group-button")) return 40;
      return 900;
    }

    function sortPanelItems() {
      Array.prototype.slice.call(body.children)
        .sort(function(a, b) {
          return controlRank(a) - controlRank(b);
        })
        .forEach(function(node) {
          body.appendChild(node);
        });
    }

    toolbar.appendChild(toggle);
    toolbar.appendChild(panel);
    existing.forEach(moveIntoPanel);
    sortPanelItems();

    var observer = new MutationObserver(function(records) {
      records.forEach(function(record) {
        Array.prototype.slice.call(record.addedNodes).forEach(moveIntoPanel);
      });
      sortPanelItems();
    });
    observer.observe(toolbar, { childList: true });

    function setOpen(open) {
      panel.classList.toggle("is-open", open);
      toggle.classList.toggle("is-open", open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    }

    toggle.addEventListener("click", function(ev) {
      setOpen(!panel.classList.contains("is-open"));
      ev.stopPropagation();
    });
    close.addEventListener("click", function(ev) {
      setOpen(false);
      ev.stopPropagation();
    });
    opacity.addEventListener("input", function() {
      panel.style.opacity = String(Number(opacity.value) / 100);
    });
    panel.addEventListener("click", function(ev) {
      var opensEditor = ev.target.closest(".rist-legend-edit-button,.xrd-phase-group-button");
      if (opensEditor) window.setTimeout(function() { setOpen(false); }, 0);
      ev.stopPropagation();
    });
    document.addEventListener("click", function(ev) {
      if (!panel.classList.contains("is-open")) return;
      if (toolbar.contains(ev.target)) return;
      setOpen(false);
    });
    document.addEventListener("keydown", function(ev) {
      if (ev.key === "Escape") setOpen(false);
    });
  }

  install();
})();
</script>
"""
    return (
        snippet.replace("__DIV_ID__", div_id)
        .replace("__DIV_JSON__", json.dumps(div_id, ensure_ascii=False))
    )


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


def _normalized_header(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Zθ]+", "", str(value or "").lower())


def _find_column(headers: list[str], *needles: str) -> int | None:
    normalized_needles = [_normalized_header(needle) for needle in needles]
    for index, header in enumerate(headers):
        normalized = _normalized_header(header)
        if any(needle and needle in normalized for needle in normalized_needles):
            return index
    return None


def _is_esd_header(header: str) -> bool:
    normalized = _normalized_header(header)
    return normalized in {"esd", "esd"} or "esd" in normalized


def _float_or_none(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_peak_list_table(path: str) -> dict[str, Any]:
    """Peak list CSV/XLSX를 보고서 표/그래프 피크 번호 표시용으로 구조화한다."""
    preview = read_table_preview(path)
    rows = preview.get("rows") or []
    if preview.get("error") or not rows:
        return {
            "path": path,
            "headers": [],
            "display_headers": [],
            "display_rows": [],
            "peaks": [],
            "error": preview.get("error") or "",
        }

    headers = [str(cell or "").strip() for cell in rows[0]]
    col_no = _find_column(headers, "No.")
    col_theta = _find_column(headers, "2θ", "2theta")
    col_phase = _find_column(headers, "Phase Name")
    col_formula = _find_column(headers, "Chemical Formula")
    col_card = _find_column(headers, "Card No")
    col_norm = _find_column(headers, "Norm. I.")

    non_esd_indices = [
        index for index, header in enumerate(headers)
        if header and not _is_esd_header(header)
    ]
    required_indices = [
        index for index in (col_no, col_theta, col_phase, col_formula, col_card, col_norm)
        if index is not None and index in non_esd_indices
    ]
    display_indices: list[int] = []
    optional_indices = [index for index in non_esd_indices if index not in required_indices]
    optional_limit = max(0, MAX_REPORT_TABLE_COLS - len(required_indices))
    preferred_indices = optional_indices[:optional_limit] + required_indices
    for index in sorted(set(preferred_indices)):
        if index in non_esd_indices:
            display_indices.append(index)
    for index in required_indices:
        if index not in display_indices:
            display_indices.append(index)
    display_indices = display_indices[:MAX_REPORT_TABLE_COLS]
    display_headers = [headers[index] for index in display_indices]

    display_rows = []
    peaks = []
    for raw_index, row in enumerate(rows[1:], start=1):
        padded = list(row) + [""] * max(0, len(headers) - len(row))
        display_row = [padded[index] if index < len(padded) else "" for index in display_indices]
        theta = _float_or_none(padded[col_theta] if col_theta is not None and col_theta < len(padded) else "")
        card_value = padded[col_card] if col_card is not None and col_card < len(padded) else ""
        phase_value = padded[col_phase] if col_phase is not None and col_phase < len(padded) else ""
        formula_value = padded[col_formula] if col_formula is not None and col_formula < len(padded) else ""
        card_numbers = split_card_numbers(card_value)
        is_overlap = len(card_numbers) > 1 or "," in str(phase_value or "")
        peak = {
            "row_index": raw_index,
            "no": padded[col_no] if col_no is not None and col_no < len(padded) else str(raw_index),
            "two_theta": theta,
            "phase_name": phase_value,
            "formula": formula_value,
            "card_no": card_value,
            "card_numbers": card_numbers,
            "norm_i": _float_or_none(padded[col_norm] if col_norm is not None and col_norm < len(padded) else ""),
            "is_overlap": is_overlap,
            "display": display_row,
        }
        display_rows.append((display_row, peak))
        if theta is not None:
            peaks.append(peak)

    return {
        "path": path,
        "headers": headers,
        "display_headers": display_headers,
        "display_rows": display_rows,
        "peaks": peaks,
        "error": "",
    }


def parse_peak_list_tables(table_files: list[str] | None) -> list[dict[str, Any]]:
    return [parse_peak_list_table(path) for path in (table_files or [])]


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
            "<strong>A. 주요상 (Major Phases)</strong><br>"
            f"본 {sample} 시료의 XRD 패턴은 {names(major)} 후보와 주요 피크 위치가 "
            "상대적으로 잘 대응합니다. 해당 후보는 주요상으로 우선 검토할 수 있습니다."
            if major else
            "<strong>A. 주요상 (Major Phases)</strong><br>"
            f"본 {sample} 시료에서 자동 기준을 만족하는 주요상 후보는 아직 없습니다."
        ),
        (
            "<strong>B. 유사상 / 불확실상 (Uncertain / Similar Phases)</strong><br>"
            f"{names(uncertain)} 후보는 일부 주요 피크가 raw 패턴과 근접하지만, "
            "현재 데이터만으로 확정 구분하기에는 불확실성이 있습니다."
            if uncertain else
            "<strong>B. 유사상 / 불확실상 (Uncertain / Similar Phases)</strong><br>"
            "유사상으로 분류된 후보는 없습니다."
        ),
        (
            "<strong>C. 미량상 (Minor Phases)</strong><br>"
            f"{names(minor)} 후보는 피크 대응이 제한적이어서 미량상 또는 배경 후보로 검토됩니다."
            if minor else
            "<strong>C. 미량상 (Minor Phases)</strong><br>"
            "미량상 후보는 없습니다."
        ),
        (
            "<strong>안내</strong><br>"
            "유사상 구분 및 불순물/미량상 확인을 위해 XRF, ICP, EDS 등 원소 성분 정보를 "
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
                "category_source": item.get("category_source") or "score",
                "folder_group": item.get("folder_group") or "",
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
    peak_tables: list[dict[str, Any]] | None = None,
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
            "peak_lists": [
                {
                    "file": Path(table["path"]).name,
                    "peak_count": len(table.get("peaks") or []),
                    "overlap_peak_count": sum(
                        1 for peak in (table.get("peaks") or []) if peak.get("is_overlap")
                    ),
                    "headers": table.get("display_headers") or [],
                }
                for table in (peak_tables or [])[:4]
            ],
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


def _card_no_set_from_groups(groups) -> set[str]:
    cards: set[str] = set()
    for _raw_stem, _raw_color, items in groups:
        for item in items:
            card = normalize_card_no((item.get("metadata") or {}).get("card_no") or "")
            if card:
                cards.add(card)
    return cards


def _peak_row_card_match(peak: dict[str, Any], known_cards: set[str]) -> bool:
    cards = peak.get("card_numbers") or []
    return bool(cards) and any(card in known_cards for card in cards)


def _peak_list_display_html(peak_tables: list[dict[str, Any]], known_cards: set[str]) -> str:
    if not peak_tables:
        return ""
    blocks = []
    for table in peak_tables:
        filename = Path(table["path"]).name
        if table.get("error"):
            blocks.append(
                '<article class="xrd-file-table">'
                f"<h3>{_esc(filename)}</h3>"
                f'<p class="xrd-warning">{_esc(table["error"])}</p>'
                "</article>"
            )
            continue
        headers = table.get("display_headers") or []
        rows = table.get("display_rows") or []
        if not headers or not rows:
            blocks.append(
                '<article class="xrd-file-table">'
                f"<h3>{_esc(filename)}</h3>"
                '<p class="xrd-empty">표시할 Peak list 데이터가 없습니다.</p>'
                "</article>"
            )
            continue
        header_html = "<th>Card No<br>연동</th>" + "".join(
            f"<th>{_esc(header)}</th>" for header in headers
        )
        body_rows = []
        for display_row, peak in rows:
            matched = _peak_row_card_match(peak, known_cards)
            row_class = " class=\"xrd-overlap-row\"" if peak.get("is_overlap") else ""
            checked = " checked" if matched else ""
            title = "ICDD 후보 Card No와 연동됨" if matched else "ICDD 후보 Card No와 매칭되지 않아 기본 해제"
            cells = "".join(f"<td>{_esc(cell)}</td>" for cell in display_row)
            body_rows.append(
                f"<tr{row_class} data-card-nos=\"{_esc(','.join(peak.get('card_numbers') or []))}\">"
                f"<td class=\"xrd-card-check\"><input type=\"checkbox\" disabled{checked} title=\"{_esc(title)}\"></td>"
                f"{cells}</tr>"
            )
        blocks.append(
            f"""
            <article class="xrd-file-table">
              <h3>{_esc(filename)}</h3>
              <div class="xrd-table-scroll xrd-file-table-scroll">
                <table class="xrd-report-table xrd-peak-list-display">
                  <thead><tr>{header_html}</tr></thead>
                  <tbody>{''.join(body_rows)}</tbody>
                </table>
              </div>
              <p class="xrd-table-note">e.s.d. 열은 제외했습니다. 노란 행은 여러 Card No/Phase가 겹친 피크입니다.</p>
            </article>
            """
        )
    return f"""
  <div class="xrd-provided-block">
    <h3>Peak list Excel Display</h3>
    {''.join(blocks)}
  </div>
"""


def build_excel_display_html(table_files: list[str]) -> str:
    if not table_files:
        return ""
    previews = [_table_preview_html(read_table_preview(path)) for path in table_files]
    return f"""
  <div class="xrd-provided-block">
    <h3>제공된 Excel/CSV 파일 Display</h3>
    {''.join(previews)}
  </div>
"""


def build_image_display_html(image_files: list[str]) -> str:
    if not image_files:
        return ""
    figures = []
    for path in image_files:
        display_name = _display_path_part(Path(path).name)
        try:
            src = image_data_uri(path)
            figures.append(
                f"""
                <figure class="xrd-image-card">
                  <img src="{src}" alt="{_esc(display_name)}">
                  <figcaption>{_esc(display_name)}</figcaption>
                </figure>
                """
            )
        except Exception as exc:
            figures.append(
                f"""
                <figure class="xrd-image-card">
                  <div class="xrd-warning">{_esc(display_name)} 이미지를 읽지 못했습니다: {_esc(exc)}</div>
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


def build_peak_info_html(
    groups,
    table_files: list[str] | None = None,
    peak_tables: list[dict[str, Any]] | None = None,
) -> str:
    known_cards = _card_no_set_from_groups(groups)
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
    <p>Excel Peak list의 2θ/Card No/Phase Name을 기준으로 피크 번호와 후보상 연동을 표시합니다.</p>
  </div>
  {_peak_list_display_html(peak_tables or [], known_cards) or build_excel_display_html(table_files or [])}
  <div class="xrd-table-scroll">
    <table class="xrd-report-table xrd-peak-table">
      <caption>ICDD Card PDF에서 추출한 후보 피크</caption>
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


def _phase_overlap_peak_indices(
    items: list[dict[str, Any]],
    *,
    tolerance: float = 0.25,
) -> dict[int, set[int]]:
    """같은 유사상 묶음 안에서 2θ가 겹치는 PDF DB 피크 행을 찾는다."""
    points: list[tuple[int, int, float]] = []
    overlaps: dict[int, set[int]] = {}
    for item in items:
        trace_idx = int(item.get("trace_idx") or -1)
        overlaps.setdefault(trace_idx, set())
        for peak_index, peak in enumerate(item.get("peaks") or []):
            theta = _float_or_none(peak.get("two_theta"))
            if theta is not None:
                points.append((trace_idx, peak_index, theta))

    for left_index, (left_trace, left_peak, left_theta) in enumerate(points):
        for right_trace, right_peak, right_theta in points[left_index + 1:]:
            if left_trace == right_trace:
                continue
            if abs(left_theta - right_theta) <= tolerance:
                overlaps.setdefault(left_trace, set()).add(left_peak)
                overlaps.setdefault(right_trace, set()).add(right_peak)
    return overlaps


def _phase_db_peak_rows(peaks: list[dict[str, Any]], overlap_indices: set[int] | None = None) -> str:
    rows = []
    overlap_indices = overlap_indices or set()
    for index, peak in enumerate(peaks):
        row_class = ' class="xrd-phase-overlap-row"' if index in overlap_indices else ""
        rows.append(
            "<tr{row_class}><td>{no}</td><td>{theta:.3f}</td><td>{d}</td>"
            "<td>{norm:.2f}</td><td>{hkl}</td></tr>".format(
                row_class=row_class,
                no=_esc(peak.get("no") or str(index + 1)),
                theta=float(peak["two_theta"]),
                d=_esc(peak.get("d") or "-"),
                norm=float(peak["norm"]),
                hkl=_esc(peak["hkl"]),
            )
        )
    return "".join(rows) or '<tr><td colspan="5">-</td></tr>'


def _phase_excel_overlap_peak_indices(
    item: dict[str, Any],
    peak_tables: list[dict[str, Any]] | None,
    *,
    tolerance: float = 0.25,
) -> set[int]:
    """Peak list Excel에서 겹침으로 표시된 행과 가까운 PDF DB 피크 행을 찾는다."""
    if not peak_tables:
        return set()
    card_no = normalize_card_no((item.get("metadata") or {}).get("card_no") or "")
    overlap_thetas: list[float] = []
    for table in peak_tables:
        for peak in table.get("peaks") or []:
            if not peak.get("is_overlap"):
                continue
            theta = _float_or_none(peak.get("two_theta"))
            if theta is None:
                continue
            card_numbers = peak.get("card_numbers") or []
            if card_no and card_numbers and card_no not in card_numbers:
                continue
            overlap_thetas.append(theta)
    indices: set[int] = set()
    for index, peak in enumerate(item.get("peaks") or []):
        theta = _float_or_none(peak.get("two_theta"))
        if theta is None:
            continue
        if any(abs(theta - excel_theta) <= tolerance for excel_theta in overlap_thetas):
            indices.add(index)
    return indices


def _phase_db_peak_table_html(item: dict[str, Any], overlap_indices: set[int]) -> str:
    return f"""
      <div class="xrd-phase-db-table-wrap">
        <table class="xrd-report-table xrd-db-peak-table">
          <thead>
            <tr><th>No.</th><th>2θ (°)</th><th>d-value</th><th>Norm. I.</th><th>h k l</th></tr>
          </thead>
          <tbody>{_phase_db_peak_rows(item['peaks'], overlap_indices)}</tbody>
        </table>
      </div>
    """


def build_phase_info_html(groups, peak_tables: list[dict[str, Any]] | None = None) -> str:
    grouped = _phase_groups(groups)
    sections = []
    for category, title in PHASE_GROUPS.items():
        items = grouped.get(category, [])
        subgroup_items: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            folder_group = _phase_folder_group_key(item)
            subgroup_items.setdefault(folder_group, []).append(item)
        subgroup_blocks = []
        for subgroup_index, (folder_group, group_items) in enumerate(subgroup_items.items(), start=1):
            show_subgroup = (
                len(subgroup_items) > 1
                or folder_group
                not in {PHASE_CATEGORY_SHORT_LABELS.get(category), title, "자동 분류", ""}
            )
            if category == "uncertain":
                group_label = folder_group if show_subgroup else f"유사상 {subgroup_index}"
                heading = (
                    '<h3 class="xrd-phase-subgroup-title xrd-similar-group-title">'
                    f'<span>{_esc(group_label)}</span>'
                    f'<small>유사/불확실상 {len(group_items)}건</small>'
                    '</h3>'
                )
            else:
                heading = (
                    f'<h3 class="xrd-phase-subgroup-title">{_esc(folder_group)}</h3>'
                    if show_subgroup else ""
                )
            overlap_indices = (
                _phase_overlap_peak_indices(group_items)
                if category == "uncertain" and len(group_items) > 1
                else {}
            )
            cards = []
            for item in group_items:
                metadata = item["metadata"]
                formula = _compact_formula(metadata.get("formula") or "")
                phase_name = metadata.get("phase_name") or item["label"]
                card_no = metadata.get("card_no") or "-"
                quality = metadata.get("quality_mark") or "-"
                match = item["match"]
                meta_bits = [
                    ("Phase name", phase_name),
                    ("Formula", formula or "-"),
                    ("PDF Card", card_no),
                    ("QM", quality),
                    (
                        "raw 피크 대응",
                        f"{match['score']:.1f}% "
                        f"({match['matched_count']}/{match['important_count']})",
                    ),
                ]
                meta_html = "".join(
                    '<span class="xrd-phase-meta-chip">'
                    f'<b>{_esc(label)}</b>{_esc(value)}'
                    '</span>'
                    for label, value in meta_bits
                )
                trace_idx = int(item.get("trace_idx") or -1)
                row_highlights = set(overlap_indices.get(trace_idx) or set())
                row_highlights.update(_phase_excel_overlap_peak_indices(item, peak_tables))
                cards.append(
                    f"""
                    <article class="xrd-phase-card xrd-card xrd-phase-db-card" data-trace="{item['trace_idx']}">
                      <header class="xrd-phase-db-card-head">
                        <h4><span class="xrd-swatch" style="background:{item['color']}"></span>{_esc(item['label'])}</h4>
                        <div class="xrd-phase-meta-chips">{meta_html}</div>
                      </header>
                      {_phase_db_peak_table_html(item, row_highlights)}
                      <p class="xrd-table-note">노란 행은 유사상 그룹 또는 Peak list Excel에서 겹치는 DB 피크입니다.</p>
                    </article>
                    """
                )
            block_class = (
                "xrd-similar-phase-cluster"
                if category == "uncertain"
                else "xrd-phase-card-grid"
            )
            subgroup_blocks.append(
                heading
                + f'<div class="{block_class}">'
                + "".join(cards)
                + "</div>"
            )
        content = (
            "".join(subgroup_blocks)
            if subgroup_blocks
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
    <p>PDF/DB 카드의 결정상 정보를 주요상, 유사/불확실상, 미량상 후보로 묶어 표시합니다. 유사상 그룹과 Peak list Excel에서 겹치는 피크는 노란색으로 강조합니다.</p>
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


def _remove_plotly_loader_scripts(html: str) -> str:
    return re.sub(
        r'<script\b[^>]*\bsrc=["\'](?:https://cdn\.plot\.ly/plotly-[^"\']+\.min\.js|/xrd/assets/plotly\.min\.js)["\'][^>]*>\s*</script>',
        "",
        html,
        flags=re.IGNORECASE,
    )


def _trace_meta(trace: Any) -> dict[str, Any]:
    meta = getattr(trace, "meta", None)
    return meta if isinstance(meta, dict) else {}


def _trace_line_color(trace: Any) -> str:
    line = getattr(trace, "line", None)
    color = getattr(line, "color", None) if line is not None else None
    marker = getattr(trace, "marker", None)
    marker_color = getattr(marker, "color", None) if marker is not None else None
    value = str(color or marker_color or "#64748b")
    return re.sub(r'[;"<>]', "", value) or "#64748b"


def _trace_visible_for_legend(trace: Any) -> bool:
    return (
        getattr(trace, "showlegend", True) is not False
        and getattr(trace, "visible", True) not in {"legendonly", False}
    )


def _trace_legend_kind(trace: Any) -> str:
    meta = _trace_meta(trace)
    if meta.get("xrd_raw"):
        return "raw"
    if meta.get("xrd_separator"):
        return "separator"
    if meta.get("xrd_phase_candidate"):
        return "phase"
    return ""


def _strip_legend_separator(name: str) -> str:
    return re.sub(r"^[-─\s]+", "", str(name or "")).strip()


def build_xrd_print_legend_html(fig) -> str:
    rows = []
    for trace in fig.data:
        if not _trace_visible_for_legend(trace):
            continue
        kind = _trace_legend_kind(trace)
        if not kind:
            continue
        label = _strip_legend_separator(getattr(trace, "name", "") or "")
        if not label:
            continue
        if kind == "separator":
            rows.append(
                "<div class=\"xrd-print-legend-item is-separator\">"
                f"<span class=\"xrd-print-legend-label\">{_esc(label)}</span>"
                "</div>"
            )
            continue
        class_name = "xrd-print-legend-item is-raw" if kind == "raw" else "xrd-print-legend-item"
        rows.append(
            f"<div class=\"{class_name}\">"
            f"<span class=\"xrd-print-legend-swatch\" style=\"color:{_trace_line_color(trace)}\"></span>"
            f"<span class=\"xrd-print-legend-label\">{_esc(label)}</span>"
            "</div>"
        )
    body = "".join(rows) or '<div class="xrd-print-legend-item">표시할 범례가 없습니다.</div>'
    return (
        '<div class="xrd-print-legend" aria-label="XRD print legend">'
        '<div class="xrd-print-legend-title">범례</div>'
        f'<div class="xrd-print-legend-grid">{body}</div>'
        "</div>"
    )


def xrd_report_css() -> str:
    return """
<style>
  html { background: #f3f4f6; }
  body { margin: 0; font-family: Arial, "Noto Sans KR", sans-serif; color: #111827; }
  .xrd-report-page { max-width: 980px; margin: 0 auto; background: #fff; min-height: 100vh; padding: 28px 34px 48px; box-sizing: border-box; }
  .xrd-report-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin: 0 0 22px; }
  .xrd-report-title { flex: 1 1 auto; text-align: center; font-size: 26px; margin: 0; font-weight: 700; }
  .xrd-report-action-spacer { width: 230px; flex: 0 0 auto; }
  .xrd-report-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; flex: 0 0 auto; }
  .xrd-report-pdf-option { display: inline-flex; align-items: center; gap: 6px; min-height: 38px; padding: 8px 10px; border: 1px solid #cbd5e1; border-radius: 7px; color: #172a46; background: #fff; font-size: 13px; font-weight: 700; box-sizing: border-box; white-space: nowrap; }
  .xrd-report-pdf-option input { width: 15px; height: 15px; margin: 0; accent-color: #2563eb; }
  .xrd-report-pdf-button { flex: 0 0 auto; display: inline-flex; align-items: center; justify-content: center; gap: 7px; border: 1px solid #9fb6d6; background: #fff; color: #172a46; border-radius: 7px; min-height: 38px; padding: 8px 13px; font-size: 14px; font-weight: 700; cursor: pointer; }
  .xrd-report-pdf-button:hover { background: #eff6ff; border-color: #2563eb; }
  .xrd-report-pdf-button:disabled, .xrd-report-pdf-button.is-loading { cursor: progress; opacity: .76; }
  .xrd-report-pdf-button:disabled:hover { background: #fff; border-color: #9fb6d6; }
  .xrd-report-pdf-spinner { display: none; width: 13px; height: 13px; border: 2px solid #bfdbfe; border-top-color: #2563eb; border-radius: 999px; animation: xrdPdfSpin .75s linear infinite; }
  .xrd-report-pdf-button.is-loading .xrd-report-pdf-spinner { display: inline-block; }
  @keyframes xrdPdfSpin { to { transform: rotate(360deg); } }
  .xrd-report-section { margin: 20px 0 0; }
  .xrd-report-section h2 { font-size: 17px; margin: 0 0 8px; }
  .xrd-section-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; border-bottom: 1px solid #d1d5db; padding-bottom: 6px; margin-bottom: 10px; }
  .xrd-section-head h2 { flex: 0 0 min(260px, 36%); line-height: 1.25; word-break: keep-all; overflow-wrap: normal; }
  .xrd-section-head p { flex: 1 1 auto; min-width: 0; margin: 2px 0 0; color: #6b7280; font-size: 12px; line-height: 1.45; text-align: right; word-break: keep-all; overflow-wrap: anywhere; }
  .xrd-graph-frame, .xrd-comment-box, .xrd-table-scroll, .xrd-phase-group { border: 2px solid #111827; border-radius: 18px; background: #fff; }
  .xrd-graph-frame { padding: 10px 12px 12px; }
  #xrd-plot { height: 500px !important; min-height: 420px; }
  .xrd-comment-box { padding: 16px 18px; border-radius: 10px; }
  .xrd-comment-box[contenteditable="true"] { outline: none; }
  .xrd-comment-box[contenteditable="true"]:focus { border-color: #2563eb; box-shadow: 0 0 0 3px rgba(37, 99, 235, .16); }
  .xrd-comment-box p { margin: 0 0 12px; font-size: 14px; line-height: 1.65; }
  .xrd-comment-box p:last-child { margin-bottom: 0; }
  .xrd-table-scroll { border-radius: 10px; overflow: auto; max-height: 520px; }
  .xrd-report-table, .xrd-mini-table { width: 100%; border-collapse: collapse; font-size: 12px; }
  .xrd-report-table caption { caption-side: top; text-align: left; font-weight: 700; padding: 8px 0; color: #374151; }
  .xrd-report-table th, .xrd-report-table td, .xrd-mini-table th, .xrd-mini-table td { border: 1px solid #d1d5db; padding: 6px 8px; vertical-align: middle; }
  .xrd-report-table th { background: #f3f4f6; position: sticky; top: 0; z-index: 1; }
  .xrd-report-table td:nth-child(4), .xrd-report-table td:nth-child(5), .xrd-report-table td:nth-child(6), .xrd-report-table td:nth-child(7) { text-align: right; }
  .xrd-peak-list-display th, .xrd-peak-list-display td { text-align: center; }
  .xrd-peak-list-display td { white-space: nowrap; }
  .xrd-peak-list-display td:nth-child(3),
  .xrd-peak-list-display td:nth-child(5),
  .xrd-peak-list-display td:nth-child(7) { text-align: right; }
  .xrd-card-check input { width: 15px; height: 15px; accent-color: #2563eb; }
  .xrd-overlap-row { background: #fff7cc !important; }
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
  .xrd-print-legend { display: none; }
  .xrd-print-plot-image { display: none; }
  html[data-read-only-report="true"] .xrd-report-page {
    overflow-x: hidden;
  }
  html[data-read-only-report="true"] .xrd-graph-frame {
    max-width: 100%;
    box-sizing: border-box;
  }
  @media screen and (max-width: 900px), screen and (pointer: coarse) {
    html[data-read-only-report="true"] .xrd-report-page {
      max-width: 100%;
      padding-left: 10px;
      padding-right: 10px;
    }
    html[data-read-only-report="true"] .xrd-graph-frame {
      overflow: hidden;
      padding: 8px 8px 10px;
    }
    html[data-read-only-report="true"] #xrd-plot {
      width: 100% !important;
      max-width: 100% !important;
      height: 430px !important;
      min-height: 360px !important;
      overflow: hidden !important;
    }
    html[data-read-only-report="true"] #xrd-plot .plot-container,
    html[data-read-only-report="true"] #xrd-plot .svg-container,
    html[data-read-only-report="true"] #xrd-plot .main-svg {
      max-width: 100% !important;
      overflow: hidden !important;
    }
    html[data-read-only-report="true"] #xrd-plot .modebar {
      max-width: calc(100% - 12px);
      transform: scale(.92);
      transform-origin: top right;
    }
    html[data-read-only-report="true"] #xrd-plot .legend,
    html[data-read-only-report="true"] #xrd-plot .rist-legend-drag-handle {
      max-width: calc(100% - 18px) !important;
      box-sizing: border-box;
    }
  }
  .xrd-phase-group { border-radius: 10px; margin: 12px 0; padding: 0 12px 12px; }
  .xrd-phase-group summary { cursor: pointer; font-size: 16px; font-weight: 700; padding: 12px 0; }
  .xrd-phase-group summary span { color: #6b7280; font-size: 12px; margin-left: 6px; }
  .xrd-phase-subgroup-title { margin: 12px 0 4px; padding: 7px 10px; border-left: 4px solid #3b82f6; background: #f1f5f9; border-radius: 6px; font-size: 13px; }
  .xrd-similar-group-title { display: flex; align-items: center; justify-content: space-between; gap: 10px; border-left-color: #f97316; background: #fff7ed; }
  .xrd-similar-group-title small { flex: 0 0 auto; color: #9a3412; font-size: 12px; font-weight: 700; }
  .xrd-similar-phase-cluster { display: grid; grid-template-columns: 1fr; gap: 14px; }
  .xrd-phase-card-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(390px, 1fr)); gap: 12px; }
  .xrd-phase-card { border: 1px solid #d1d5db; border-radius: 10px; padding: 10px; background: #fff; }
  .xrd-phase-card h4 { display: flex; align-items: center; gap: 8px; font-size: 14px; margin: 0 0 10px; }
  .xrd-phase-db-card { padding: 0; overflow: hidden; }
  .xrd-phase-db-card-head { padding: 10px 12px 8px; border-bottom: 1px solid #d1d5db; }
  .xrd-phase-db-card h4 { margin: 0 0 8px; }
  .xrd-phase-meta-chips { display: flex; flex-wrap: wrap; gap: 6px 8px; }
  .xrd-phase-meta-chip { border: 1px solid #e5e7eb; border-radius: 6px; padding: 3px 6px; background: #f9fafb; color: #111827; font-size: 11px; line-height: 1.35; }
  .xrd-phase-meta-chip b { margin-right: 4px; color: #475569; }
  .xrd-mini-table th { width: 120px; background: #f9fafb; text-align: left; }
  .xrd-phase-db-scroll { border-width: 1px; border-radius: 8px; margin-top: 8px; max-height: 420px; }
  .xrd-phase-db-table-wrap { max-height: 520px; overflow: auto; }
  .xrd-db-peak-table caption { caption-side: top; text-align: left; font-weight: 700; padding: 7px 0; color: #374151; }
  .xrd-db-peak-table { min-width: 520px; font-size: 11px; }
  .xrd-db-peak-table th { background: #f8fafc; position: sticky; top: 0; z-index: 1; }
  .xrd-db-peak-table th, .xrd-db-peak-table td { text-align: right; white-space: nowrap; padding: 4px 6px; }
  .xrd-db-peak-table th:first-child, .xrd-db-peak-table td:first-child,
  .xrd-db-peak-table th:last-child, .xrd-db-peak-table td:last-child { text-align: center; }
  .xrd-phase-overlap-row { background: #fff7cc !important; }
  .xrd-rank-1 { background: #fff3cd; font-weight: 700; }
  .xrd-rank-2, .xrd-rank-3 { background: #fff8e6; }
  .xrd-empty { color: #6b7280; text-align: center; padding: 18px; }
  .xrd-warning { border-left: 4px solid #f59e0b; background: #fffbeb; padding: 10px 12px; margin: 8px 0; font-size: 13px; }
  @media (max-width: 760px) {
    .xrd-report-page { padding: 18px 12px 32px; }
    .xrd-report-header { align-items: flex-start; gap: 8px; }
    .xrd-report-title { text-align: left; font-size: 23px; }
    .xrd-report-action-spacer { display: none; }
    .xrd-report-actions { flex-wrap: wrap; }
    .xrd-report-pdf-button { min-height: 36px; padding: 7px 10px; font-size: 13px; }
    .xrd-report-pdf-option { min-height: 36px; padding: 7px 10px; font-size: 12px; }
    .xrd-section-head { display: block; }
    .xrd-section-head h2 { flex-basis: auto; }
    .xrd-section-head p { text-align: left; margin-top: 4px; }
    #xrd-plot { height: 400px !important; min-height: 340px; }
    .xrd-phase-grid { grid-template-columns: 1fr; }
    .xrd-image-grid { grid-template-columns: 1fr; }
  }
  @media (min-width: 761px) and (max-width: 1280px) {
    #xrd-plot { height: 430px !important; min-height: 380px; }
  }
  @media print {
    @page { size: A4 portrait; margin: 9mm 10mm; }
    @page xrd-graph-landscape { size: A4 landscape; margin: 9mm 10mm; }
    html { background: #fff; }
    .xrd-report-page { max-width: none; padding: 0; }
    .xrd-report-header { display: none !important; }
    .xrd-report-pdf-button, .xrd-report-action-spacer { display: none !important; }
    .xrd-report-section { margin-top: 10px; }
    .xrd-section-head { margin-bottom: 8px; padding-bottom: 5px; }
    #xrd-graph-section {
      page: auto;
      break-after: page;
      page-break-after: always;
      margin-bottom: 0;
    }
    #xrd-graph-section .xrd-section-head {
      break-after: avoid;
      page-break-after: avoid;
    }
    #xrd-graph-section .xrd-section-head + .xrd-graph-frame {
      break-before: avoid;
      page-break-before: avoid;
    }
    body.xrd-report-graph-landscape #xrd-graph-section {
      page: xrd-graph-landscape;
    }
    body.xrd-report-graph-landscape #xrd-image-info {
      page: xrd-graph-landscape;
      break-before: page;
      page-break-before: always;
      break-after: page;
      page-break-after: always;
    }
    body.xrd-report-graph-landscape #xrd-llm-comment,
    body.xrd-report-graph-landscape #xrd-peak-info,
    body.xrd-report-graph-landscape #xrd-phase-info {
      page: auto;
    }
    body.xrd-report-graph-landscape #xrd-llm-comment {
      break-before: page;
      page-break-before: always;
    }
    .xrd-graph-frame {
      overflow: visible !important;
      width: 92% !important;
      max-width: 178mm !important;
      margin: 0 auto !important;
      padding: 8px 12px 12px !important;
      border: 0 !important;
      border-radius: 12px;
      box-shadow: inset 0 0 0 2px #111827;
      box-sizing: border-box;
      position: relative;
      break-inside: avoid;
      page-break-inside: avoid;
    }
    .xrd-print-plot-image {
      display: none;
      width: 100%;
      max-width: 100%;
      height: auto;
      object-fit: contain;
      margin: 0 auto 6px;
    }
    .xrd-graph-frame::after {
      display: none !important;
      content: none !important;
    }
    #xrd-plot {
      width: 100% !important;
      max-width: calc(100% - 24px) !important;
      height: 350px !important;
      min-height: 350px !important;
      margin: 0 auto 6px;
      overflow: visible !important;
      box-sizing: border-box;
    }
    body.xrd-report-graph-landscape .xrd-graph-frame {
      width: 98% !important;
      max-width: 268mm !important;
      padding: 8px 12px 12px !important;
      text-align: center;
    }
    body.xrd-report-graph-landscape #xrd-plot {
      width: 100% !important;
      max-width: calc(100% - 24px) !important;
      height: 420px !important;
      min-height: 420px !important;
      transform: none !important;
    }
    .xrd-graph-frame.has-print-plot #xrd-plot {
      display: none !important;
      visibility: hidden !important;
    }
    .xrd-graph-frame.has-print-plot .xrd-print-plot-image {
      display: block !important;
      visibility: visible !important;
      max-height: 118mm;
    }
    body.xrd-report-graph-landscape .xrd-graph-frame.has-print-plot .xrd-print-plot-image {
      max-height: 126mm;
    }
    body.xrd-report-graph-landscape .xrd-print-legend-grid {
      column-count: 3;
      column-gap: 12px;
    }
    #xrd-plot .plot-container,
    #xrd-plot .svg-container,
    #xrd-plot .main-svg {
      width: 100% !important;
      max-width: 100% !important;
      overflow: visible !important;
    }
    #xrd-peak-info {
      break-before: page;
      page-break-before: always;
    }
    #xrd-plot .modebar,
    #xrd-plot .rist-plot-control-row,
    #xrd-plot .xrd-tool-toggle,
    #xrd-plot .xrd-tool-panel,
    #xrd-plot .rist-legend-edit-panel,
    #xrd-plot .xrd-phase-group-panel,
    #xrd-plot .rist-legend-drag-handle,
    #xrd-plot .rist-xrd-legend-checkbox,
    #xrd-plot .rist-xrd-legend-branch,
    #xrd-plot .legend {
      display: none !important;
      visibility: hidden !important;
    }
    .xrd-print-legend {
      display: block;
      box-sizing: border-box;
      margin: 8px 12px 0 6px;
      padding: 6px 8px;
      border: 1px solid #cbd5e1;
      border-radius: 7px;
      background: #fff;
      color: #111827;
      font-size: 9px;
      line-height: 1.28;
      break-inside: auto;
      page-break-inside: auto;
    }
    .xrd-print-legend-title {
      margin: 0 0 5px;
      font-weight: 700;
      color: #172a46;
    }
    .xrd-print-legend-grid {
      column-count: 2;
      column-gap: 14px;
    }
    .xrd-print-legend-item {
      display: flex;
      align-items: flex-start;
      gap: 5px;
      min-width: 0;
      margin: 0 0 3px;
      word-break: keep-all;
      overflow-wrap: anywhere;
      break-inside: avoid;
      page-break-inside: avoid;
    }
    .xrd-print-legend-item.is-raw {
      font-weight: 700;
    }
    .xrd-print-legend-item.is-separator {
      color: #64748b;
      font-weight: 700;
      margin: 4px 0 3px;
    }
    .xrd-print-legend-swatch {
      flex: 0 0 18px;
      width: 18px;
      height: 0;
      margin-top: 6px;
      border-top: 2px solid currentColor;
    }
    .xrd-print-legend-label {
      min-width: 0;
    }
    .xrd-table-scroll,
    .xrd-file-table-scroll,
    .xrd-phase-db-table-wrap {
      max-height: none !important;
      height: auto !important;
      overflow: visible !important;
    }
    .xrd-report-table th {
      position: static !important;
      top: auto !important;
      z-index: auto !important;
    }
    .xrd-report-table thead {
      display: table-header-group;
    }
    .xrd-report-table tfoot {
      display: table-footer-group;
    }
    .xrd-report-table tr,
    .xrd-mini-table tr {
      break-inside: avoid;
      page-break-inside: avoid;
    }
    .xrd-phase-group {
      break-inside: avoid;
      page-break-inside: avoid;
    }
    .xrd-phase-group summary,
    .xrd-phase-subgroup-title,
    .xrd-phase-db-card-head {
      break-after: avoid;
      page-break-after: avoid;
    }
    .xrd-phase-group summary + .xrd-empty,
    .xrd-phase-group summary + .xrd-phase-subgroup-title,
    .xrd-phase-subgroup-title + .xrd-similar-phase-cluster,
    .xrd-phase-subgroup-title + .xrd-phase-card-grid {
      break-before: avoid;
      page-break-before: avoid;
    }
    .xrd-phase-card {
      break-inside: avoid;
      page-break-inside: avoid;
    }
    .xrd-file-table {
      break-inside: auto;
      page-break-inside: auto;
    }
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
    peak_tables: list[dict[str, Any]] | None = None,
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
        wide_legend_inside=True,
        crosshair=True,
        title_edit=True,
        legend_text_edit=True,
        trace_highlight=True,
        highlight_pickable=raw_line_indices,
        highlight_groups=highlight_groups,
        image_filename=first_stem,
        image_format=XRD_DOWNLOAD_IMAGE_FORMAT,
        image_format_selector=XRD_IMAGE_FORMAT_SELECTOR,
        include_plotlyjs="/xrd/assets/plotly.min.js",
        post_body_html=(
            build_xrd_axis_text_guard_js("xrd-plot")
            + build_xrd_legend_checkbox_js("xrd-plot")
            + build_xrd_phase_group_editor_js("xrd-plot")
            + build_xrd_tool_drawer_js("xrd-plot")
        ),
        config=_xrd_plot_config(),
    )
    plot_body = re.sub(
        r'style="height:[^"]*?;\s*width:100%;"',
        'style="height:500px; width:100%;"',
        _remove_plotly_loader_scripts(_html_body_inner(plot_html)),
        count=1,
    )
    print_legend = build_xrd_print_legend_html(fig)
    warning_html = "".join(f'<div class="xrd-warning">{_esc(w)}</div>' for w in warnings)
    comments = comment_html or build_auto_interpretation_html(sample_name, groups, warnings)
    comment_note_text = (
        comment_note
        or "raw 피크, ICDD 후보상, 첨부 표/이미지 정보를 기준으로 정리한 분석결과입니다."
    )
    image_info = build_image_display_html(image_files or [])
    peak_info = build_peak_info_html(groups, table_files or [], peak_tables or [])
    phase_info = build_phase_info_html(groups, peak_tables or [])
    group_toggle_js = build_group_toggle_js("xrd-plot", group_map)
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=5">
  <title>{_esc(sample_name)} Report</title>
  <script charset="utf-8" src="/xrd/assets/plotly.min.js"></script>
  {xrd_report_css()}
</head>
<body>
  <main class="xrd-report-page">
    <header class="xrd-report-header">
      <div class="xrd-report-action-spacer" aria-hidden="true"></div>
      <h1 class="xrd-report-title">{_esc(sample_name)} Report</h1>
      <div class="xrd-report-actions">
        <label class="xrd-report-pdf-option">
          <input type="checkbox" id="xrd-report-landscape-graph" checked>
          그래프 가로형
        </label>
        <button type="button" class="xrd-report-pdf-button" id="xrd-report-pdf-export"><span class="xrd-report-pdf-button-label">PDF Export</span><span class="xrd-report-pdf-spinner" aria-hidden="true"></span></button>
      </div>
    </header>
    <section class="xrd-report-section" id="xrd-graph-section">
      <div class="xrd-section-head">
        <h2>상 동정 (Phase Identification) 결과</h2>
        <p>측정 데이터와 ICDD Card 피크를 함께 표시합니다.</p>
      </div>
      <div class="xrd-graph-frame">{plot_body}<img class="xrd-print-plot-image" alt="XRD graph print image">{print_legend}</div>
    </section>
    {image_info}
    <section class="xrd-report-section" id="xrd-llm-comment">
      <div class="xrd-section-head">
        <h2>분석결과</h2>
        <p>{_esc(comment_note_text)}</p>
      </div>
      {warning_html}
      <div class="xrd-comment-box" id="xrd-analysis-result" contenteditable="true" spellcheck="false" aria-label="분석결과 편집">{comments}</div>
    </section>
    {peak_info}
    {phase_info}
  </main>
  {group_toggle_js}
  <script>
  (function() {{
    var button = document.getElementById("xrd-report-pdf-export");
    var landscapeOption = document.getElementById("xrd-report-landscape-graph");
    var gd = document.getElementById("xrd-plot");
    var pdfButtonLabel = button ? button.querySelector(".xrd-report-pdf-button-label") : null;
    var pdfButtonDefaultText = pdfButtonLabel ? pdfButtonLabel.textContent : "PDF Export";
    var originalLegendLayout = null;
    var printLegend = null;
    var printPlotImage = null;
    var printPageStyle = null;
    var PRINT_PLOT_HEIGHT = 350;
    var PRINT_PLOT_WIDTH = 620;
    var PRINT_LANDSCAPE_PLOT_HEIGHT = 420;
    var PRINT_LANDSCAPE_PLOT_WIDTH = 1040;
    function compactLayout(layout) {{
      var cleaned = {{}};
      Object.keys(layout || {{}}).forEach(function(key) {{
        if (layout[key] !== undefined) cleaned[key] = layout[key];
      }});
      return cleaned;
    }}
    function traceMeta(trace) {{
      return (trace && trace.meta && typeof trace.meta === "object") ? trace.meta : {{}};
    }}
    function stripSeparator(name) {{
      return String(name || "").replace(/^[-─\\s]+/, "").trim();
    }}
    function escapeHtml(value) {{
      return String(value || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }}
    function safeCssColor(value) {{
      return String(value || "#64748b").replace(/[;"<>]/g, "") || "#64748b";
    }}
    function traceVisible(trace) {{
      return trace && trace.showlegend !== false && trace.visible !== "legendonly" && trace.visible !== false;
    }}
    function traceKind(trace) {{
      var meta = traceMeta(trace);
      if (meta.xrd_raw) return "raw";
      if (meta.xrd_separator) return "separator";
      if (meta.xrd_phase_candidate) return "phase";
      return "";
    }}
    function traceColor(trace) {{
      var line = trace && trace.line ? trace.line : {{}};
      if (line.color) return line.color;
      var marker = trace && trace.marker ? trace.marker : {{}};
      return marker.color || "#64748b";
    }}
    function ensurePrintLegend() {{
      if (!gd) return null;
      if (!printLegend) {{
        var graphFrame = gd.closest ? gd.closest(".xrd-graph-frame") : null;
        printLegend = graphFrame
          ? graphFrame.querySelector(".xrd-print-legend")
          : null;
        if (!printLegend) {{
          printLegend = document.createElement("div");
          printLegend.className = "xrd-print-legend";
          gd.insertAdjacentElement("afterend", printLegend);
        }}
      }}
      return printLegend;
    }}
    function graphFrame() {{
      return gd && gd.closest ? gd.closest(".xrd-graph-frame") : null;
    }}
    function ensurePrintPlotImage() {{
      var frame = graphFrame();
      if (!frame) return null;
      if (!printPlotImage) {{
        printPlotImage = frame.querySelector(".xrd-print-plot-image");
        if (!printPlotImage) {{
          printPlotImage = document.createElement("img");
          printPlotImage.className = "xrd-print-plot-image";
          printPlotImage.alt = "XRD graph print image";
          gd.insertAdjacentElement("afterend", printPlotImage);
        }}
      }}
      return printPlotImage;
    }}
    function refreshPrintLegend() {{
      var host = ensurePrintLegend();
      if (!host || !gd) return;
      var traces = gd.data || [];
      var rows = [];
      traces.forEach(function(trace) {{
        if (!traceVisible(trace)) return;
        var kind = traceKind(trace);
        if (!kind) return;
        var label = stripSeparator(trace.name || "");
        if (!label) return;
        if (kind === "separator") {{
          rows.push("<div class='xrd-print-legend-item is-separator'>"
            + "<span class='xrd-print-legend-label'>" + escapeHtml(label) + "</span></div>");
          return;
        }}
        var color = safeCssColor(traceColor(trace));
        var className = kind === "raw"
          ? "xrd-print-legend-item is-raw"
          : "xrd-print-legend-item";
        rows.push("<div class='" + className + "'>"
          + "<span class='xrd-print-legend-swatch' style='color:" + color + "'></span>"
          + "<span class='xrd-print-legend-label'>" + escapeHtml(label) + "</span>"
          + "</div>");
      }});
      host.innerHTML = "<div class='xrd-print-legend-title'>범례</div>"
        + "<div class='xrd-print-legend-grid'>" + rows.join("") + "</div>";
    }}
    function normalizeLegendHandleLabel() {{
      if (!gd) return;
      var handle = gd.querySelector(".rist-legend-drag-handle");
      if (!handle) return;
      handle.textContent = "범례";
      handle.title = "이 바를 드래그해서 범례 위치 이동";
    }}
    function currentLegendLayout() {{
      if (!gd || !gd.layout) return null;
      var legend = gd.layout.legend || {{}};
      return compactLayout({{
        "legend.x": legend.x,
        "legend.y": legend.y,
        "legend.xanchor": legend.xanchor,
        "legend.yanchor": legend.yanchor,
        "legend.font.size": legend.font && legend.font.size,
        "legend.bgcolor": legend.bgcolor,
        "legend.bordercolor": legend.bordercolor,
        "legend.borderwidth": legend.borderwidth,
        "legend.itemsizing": legend.itemsizing
      }});
    }}
    function graphPageLandscapeEnabled() {{
      return !landscapeOption || landscapeOption.checked;
    }}
    function effectivePrintLandscapeEnabled() {{
      return graphPageLandscapeEnabled();
    }}
    function isMobileBrowserPrintClient() {{
      if (!shouldUseBrowserPrint()) return false;
      var narrow = false;
      var coarse = false;
      try {{
        narrow = window.matchMedia && window.matchMedia("(max-width: 760px)").matches;
        coarse = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
      }} catch (_error) {{
        narrow = false;
        coarse = false;
      }}
      return narrow || coarse || /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent || "");
    }}
    function isMobileReadOnlyScreen() {{
      if (window.readOnlyReport !== true) return false;
      var narrow = false;
      var coarse = false;
      try {{
        narrow = window.matchMedia && window.matchMedia("(max-width: 900px)").matches;
        coarse = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
      }} catch (_error) {{
        narrow = false;
        coarse = false;
      }}
      return narrow || coarse || /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent || "");
    }}
    function applyGraphPageMode() {{
      var enabled = graphPageLandscapeEnabled();
      document.body.classList.toggle("xrd-report-graph-landscape", enabled);
      return enabled;
    }}
    function removePrintPageStyle() {{
      if (printPageStyle && printPageStyle.parentNode) {{
        printPageStyle.parentNode.removeChild(printPageStyle);
      }}
      printPageStyle = null;
    }}
    function applyPrintPageStyle() {{
      removePrintPageStyle();
      if (!graphPageLandscapeEnabled()) return;
      var mobileBrowserPrint = isMobileBrowserPrintClient();
      printPageStyle = document.createElement("style");
      printPageStyle.setAttribute("data-xrd-print-page-style", "true");
      if (mobileBrowserPrint) {{
        printPageStyle.textContent = "@media print {{ @page {{ size: A4 landscape; margin: 9mm 10mm; }} body.xrd-report-graph-landscape #xrd-graph-section, body.xrd-report-graph-landscape #xrd-image-info, body.xrd-report-graph-landscape #xrd-llm-comment, body.xrd-report-graph-landscape #xrd-peak-info, body.xrd-report-graph-landscape #xrd-phase-info {{ page: auto; }} body.xrd-report-graph-landscape .xrd-graph-frame {{ width: 96% !important; max-width: 268mm !important; }} body.xrd-report-graph-landscape .xrd-graph-frame.has-print-plot .xrd-print-plot-image {{ max-height: 126mm; }} body.xrd-report-graph-landscape .xrd-print-legend-grid {{ column-count: 3; }} body.xrd-report-graph-landscape #xrd-image-info, body.xrd-report-graph-landscape #xrd-llm-comment {{ break-before: page; page-break-before: always; }} }}";
      }} else {{
        printPageStyle.textContent = "@media print {{ @page {{ size: A4 portrait; margin: 9mm 10mm; }} @page:first {{ size: A4 landscape; margin: 9mm 10mm; }} @page xrd-graph-landscape {{ size: A4 landscape; margin: 9mm 10mm; }} body.xrd-report-graph-landscape #xrd-graph-section, body.xrd-report-graph-landscape #xrd-image-info {{ page: xrd-graph-landscape; }} body.xrd-report-graph-landscape #xrd-llm-comment, body.xrd-report-graph-landscape #xrd-peak-info, body.xrd-report-graph-landscape #xrd-phase-info {{ page: auto; }} body.xrd-report-graph-landscape #xrd-image-info, body.xrd-report-graph-landscape #xrd-llm-comment {{ break-before: page; page-break-before: always; }} }}";
      }}
      document.head.appendChild(printPageStyle);
    }}
    function currentXAxisRange() {{
      if (!gd) return null;
      var axis = (gd.layout && gd.layout.xaxis) || (gd._fullLayout && gd._fullLayout.xaxis) || {{}};
      var range = axis.range;
      if (!Array.isArray(range) || range.length < 2) return null;
      var lo = Number(range[0]);
      var hi = Number(range[1]);
      if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null;
      return [Math.min(lo, hi), Math.max(lo, hi)];
    }}
    function computePrintYRange() {{
      if (!gd) return null;
      var xRange = currentXAxisRange();
      var minY = Infinity;
      var maxY = -Infinity;
      (gd.data || []).forEach(function(trace) {{
        if (!traceVisible(trace) || !Array.isArray(trace.y)) return;
        var xs = Array.isArray(trace.x) ? trace.x : [];
        trace.y.forEach(function(rawY, idx) {{
          var y = Number(rawY);
          if (!Number.isFinite(y)) return;
          if (xRange && xs.length === trace.y.length) {{
            var x = Number(xs[idx]);
            if (!Number.isFinite(x) || x < xRange[0] || x > xRange[1]) return;
          }}
          minY = Math.min(minY, y);
          maxY = Math.max(maxY, y);
        }});
      }});
      if (!Number.isFinite(minY) || !Number.isFinite(maxY) || maxY <= minY) return null;
      var span = maxY - minY;
      var padTop = Math.max(span * 0.08, Math.abs(maxY) * 0.03, 1);
      var padBottom = Math.max(span * 0.02, 1);
      return [Math.min(0, minY - padBottom), maxY + padTop];
    }}
    function applyReportPlotLayout() {{
      if (!window.Plotly || !gd) return null;
      var landscape = applyGraphPageMode();
      var plotHeight = landscape ? PRINT_LANDSCAPE_PLOT_HEIGHT : PRINT_PLOT_HEIGHT;
      var plotWidthLimit = landscape ? PRINT_LANDSCAPE_PLOT_WIDTH : PRINT_PLOT_WIDTH;
      gd.style.height = plotHeight + "px";
      gd.style.minHeight = plotHeight + "px";
      gd.style.width = plotWidthLimit + "px";
      gd.style.maxWidth = "calc(100% - 24px)";
      gd.style.marginLeft = "auto";
      gd.style.marginRight = "auto";
      var graphFrame = gd.closest ? gd.closest(".xrd-graph-frame") : null;
      var graphFrameWidth = graphFrame ? graphFrame.getBoundingClientRect().width : 0;
      var gdWidth = gd.getBoundingClientRect ? gd.getBoundingClientRect().width : gd.clientWidth;
      var plotWidth = graphFrameWidth > 0 ? graphFrameWidth - (landscape ? 40 : 56) : gdWidth;
      var layout = {{
        "height": plotHeight,
        "autosize": false,
        "title.text": "",
        "margin.t": 20,
        "margin.b": 74
      }};
      if (plotWidth > 240) {{
        layout["width"] = Math.min(Math.floor(plotWidth), plotWidthLimit);
      }}
      var yRange = computePrintYRange();
      if (yRange) {{
        layout["yaxis.autorange"] = false;
        layout["yaxis.range"] = yRange;
      }}
      return window.Plotly.relayout(gd, layout);
    }}
    function applyReadOnlyMobilePlotLayout() {{
      if (!window.Plotly || !gd || !isMobileReadOnlyScreen()) return null;
      var frame = graphFrame();
      var frameWidth = frame && frame.getBoundingClientRect ? frame.getBoundingClientRect().width : 0;
      var availableWidth = Math.max(300, Math.floor((frameWidth || window.innerWidth || 360) - 18));
      var plotHeight = Math.max(430, Math.min(540, Math.floor((window.innerHeight || 720) * 0.58)));
      gd.style.width = "100%";
      gd.style.maxWidth = "100%";
      gd.style.height = plotHeight + "px";
      gd.style.minHeight = "360px";
      refreshPrintLegend();
      return window.Plotly.relayout(gd, {{
        "autosize": false,
        "width": availableWidth,
        "height": plotHeight,
        "title.text": "",
        "margin.t": 20,
        "margin.b": 104,
        "legend.x": 0,
        "legend.y": -0.22,
        "legend.xanchor": "left",
        "legend.yanchor": "top",
        "legend.orientation": "h",
        "legend.font.size": 10
      }}).then(function() {{
        if (window.Plotly && window.Plotly.Plots) window.Plotly.Plots.resize(gd);
      }});
    }}
    function scheduleReadOnlyMobilePlotLayout() {{
      window.setTimeout(function() {{
        var relayout = applyReadOnlyMobilePlotLayout();
        if (relayout && relayout.catch) {{
          relayout.catch(function(error) {{
            console.warn("XRD mobile read-only plot layout failed.", error);
          }});
        }}
      }}, 80);
    }}
    function clonePlotlyValue(value) {{
      try {{
        return JSON.parse(JSON.stringify(value || {{}}));
      }} catch (error) {{
        return value;
      }}
    }}
    function printableTraces() {{
      return (gd.data || []).filter(function(trace) {{
        return traceVisible(trace) && traceKind(trace) !== "separator";
      }}).map(function(trace) {{
        var copy = clonePlotlyValue(trace);
        copy.showlegend = false;
        return copy;
      }});
    }}
    function buildPrintPlotLayout(width, height) {{
      var layout = clonePlotlyValue(gd.layout || {{}});
      layout.width = width;
      layout.height = height;
      layout.autosize = false;
      layout.showlegend = false;
      layout.title = {{"text": ""}};
      layout.margin = Object.assign({{}}, layout.margin || {{}}, {{
        l: 78,
        r: 32,
        t: 24,
        b: 72
      }});
      layout.paper_bgcolor = "#ffffff";
      layout.plot_bgcolor = "#ffffff";
      layout.xaxis = Object.assign({{}}, layout.xaxis || {{}}, {{automargin: true}});
      layout.yaxis = Object.assign({{}}, layout.yaxis || {{}}, {{automargin: true}});
      var yRange = computePrintYRange();
      if (yRange) {{
        layout.yaxis.autorange = false;
        layout.yaxis.range = yRange;
      }}
      return layout;
    }}
    function createPrintPlotImage() {{
      if (!window.Plotly || !gd || !window.Plotly.toImage) return Promise.resolve();
      var image = ensurePrintPlotImage();
      var frame = graphFrame();
      if (!image || !frame) return Promise.resolve();
      var landscape = effectivePrintLandscapeEnabled();
      var width = landscape ? 1480 : 980;
      var height = landscape ? 650 : 560;
      var holder = document.createElement("div");
      holder.style.position = "fixed";
      holder.style.left = "-10000px";
      holder.style.top = "0";
      holder.style.width = width + "px";
      holder.style.height = height + "px";
      holder.style.background = "#fff";
      document.body.appendChild(holder);
      return window.Plotly.newPlot(holder, printableTraces(), buildPrintPlotLayout(width, height), {{
        staticPlot: true,
        displayModeBar: false,
        responsive: false
      }}).then(function() {{
        return window.Plotly.toImage(holder, {{
          format: "png",
          width: width,
          height: height,
          scale: 2
        }});
      }}).then(function(src) {{
        image.src = src;
        frame.classList.add("has-print-plot");
      }}).finally(function() {{
        if (window.Plotly && window.Plotly.purge) window.Plotly.purge(holder);
        if (holder.parentNode) holder.parentNode.removeChild(holder);
      }});
    }}
    function preparePrintLegend() {{
      if (!window.Plotly || !gd) return;
      applyGraphPageMode();
      applyPrintPageStyle();
      normalizeLegendHandleLabel();
      refreshPrintLegend();
      if (!originalLegendLayout) originalLegendLayout = currentLegendLayout();
      return createPrintPlotImage();
    }}
    function restorePrintLegend() {{
      if (!window.Plotly || !gd || !originalLegendLayout) return;
      window.Plotly.relayout(gd, originalLegendLayout);
    }}
    function restoreScreenPlotLayout() {{
      if (!window.Plotly || !gd) return;
      var frame = graphFrame();
      if (frame) frame.classList.remove("has-print-plot");
      if (printPlotImage) printPlotImage.removeAttribute("src");
      gd.style.height = "500px";
      gd.style.minHeight = "420px";
      gd.style.width = "100%";
      gd.style.maxWidth = "";
      window.Plotly.relayout(gd, {{
        "autosize": true,
        "height": 500,
        "width": null,
        "margin.t": 28,
        "margin.b": 72
      }}).then(function() {{
        if (window.Plotly && window.Plotly.Plots) window.Plotly.Plots.resize(gd);
      }});
    }}
    function exportHtmlSnapshot() {{
      var landscape = graphPageLandscapeEnabled();
      if (landscapeOption) {{
        if (landscape) {{
          landscapeOption.setAttribute("checked", "checked");
        }} else {{
          landscapeOption.removeAttribute("checked");
        }}
      }}
      document.body.classList.toggle("xrd-report-graph-landscape", landscape);
      var clone = document.documentElement.cloneNode(true);
      var cloneBody = clone.querySelector("body");
      if (cloneBody) cloneBody.classList.toggle("xrd-report-graph-landscape", landscape);
      var cloneLandscapeOption = clone.querySelector("#xrd-report-landscape-graph");
      if (cloneLandscapeOption) {{
        if (landscape) {{
          cloneLandscapeOption.setAttribute("checked", "checked");
        }} else {{
          cloneLandscapeOption.removeAttribute("checked");
        }}
      }}
      Array.prototype.forEach.call(clone.querySelectorAll("script"), function(node) {{
        node.remove();
      }});
      Array.prototype.forEach.call(
        clone.querySelectorAll(
          "#xrd-plot .modebar,#xrd-plot .rist-plot-control-row,#xrd-plot .xrd-tool-toggle,"
          + "#xrd-plot .xrd-tool-panel,#xrd-plot .rist-legend-edit-panel,#xrd-plot .xrd-phase-group-panel,"
          + "#xrd-plot .rist-legend-drag-handle,#xrd-plot .rist-xrd-legend-checkbox,#xrd-plot .rist-xrd-legend-branch"
        ),
        function(node) {{ node.remove(); }}
      );
      return "<!doctype html>\\n" + clone.outerHTML;
    }}
    function downloadPdfBlob(blob) {{
      var url = URL.createObjectURL(blob);
      var link = document.createElement("a");
      var title = String(document.title || "xrd-report").replace(/[\\\\/:*?"<>|]+/g, "_").trim() || "xrd-report";
      link.href = url;
      link.download = title + ".pdf";
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(function() {{ URL.revokeObjectURL(url); }}, 30000);
    }}
    function restoreAfterServerPdf() {{
      removePrintPageStyle();
      restorePrintLegend();
      restoreScreenPlotLayout();
    }}
    function setPdfExportBusy(busy) {{
      if (!button) return;
      button.disabled = !!busy;
      button.classList.toggle("is-loading", !!busy);
      button.setAttribute("aria-busy", busy ? "true" : "false");
      button.title = busy ? "PDF 보고서를 생성하는 중입니다." : "";
      if (pdfButtonLabel) {{
        pdfButtonLabel.textContent = busy ? "PDF 생성 중..." : pdfButtonDefaultText;
      }}
    }}
    function extractPdfErrorMessage(text) {{
      var fallback = "서버 PDF 생성에 실패했습니다. Chrome/Chromium 렌더러 설정을 확인한 뒤 다시 시도하세요.";
      if (!text) return fallback;
      try {{
        var payload = JSON.parse(text);
        var message = payload.message || fallback;
        if (payload.code) message += " (" + payload.code + ")";
        if (payload.details && Array.isArray(payload.details.rendererErrors) && payload.details.rendererErrors.length) {{
          message += "\\n" + payload.details.rendererErrors.slice(0, 2).join("\\n");
        }}
        return message;
      }} catch (error) {{
        return text.length > 500 ? text.slice(0, 500) : text;
      }}
    }}
    function serverRenderPdf() {{
      if (window.location.protocol === "file:") {{
        return Promise.reject(new Error("file URL에서는 서버 PDF 생성을 사용할 수 없습니다."));
      }}
      var prepared = preparePrintLegend();
      return Promise.resolve(prepared).then(function() {{
        return fetch("/api/v1/xrd/render-pdf", {{
          method: "POST",
          headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify({{
            html: exportHtmlSnapshot(),
            landscape: graphPageLandscapeEnabled()
          }})
        }});
      }}).then(function(response) {{
          if (!response.ok) {{
            return response.text().then(function(text) {{
              throw new Error(extractPdfErrorMessage(text));
            }});
          }}
          return response.blob();
        }}).then(function(blob) {{
          downloadPdfBlob(blob);
        }}).catch(function(error) {{
          if (error && /Failed to fetch|NetworkError|Load failed/i.test(String(error.message || error))) {{
            throw new Error(
              "서버 PDF 생성 요청이 연결 중 끊겼습니다. 업로드 파일이 많거나 보고서가 커서 브라우저가 요청을 중단했을 수 있습니다."
            );
          }}
          throw error;
        }});
    }}
    function shouldUseBrowserPrint() {{
      return window.readOnlyReport === true || window.location.protocol === "file:";
    }}
    function openBrowserPrintFallback() {{
      var relayout = preparePrintLegend();
      var printFallback = function() {{
        window.setTimeout(function() {{ window.print(); }}, 80);
      }};
      if (relayout && relayout.then) {{
        return relayout.finally(printFallback);
      }} else {{
        printFallback();
      }}
    }}
    if (gd && gd.on) {{
      gd.on("plotly_afterplot", function() {{
        normalizeLegendHandleLabel();
        refreshPrintLegend();
      }});
      gd.on("plotly_restyle", function() {{
        normalizeLegendHandleLabel();
        refreshPrintLegend();
      }});
      gd.on("plotly_relayout", function() {{
        normalizeLegendHandleLabel();
        refreshPrintLegend();
      }});
    }}
    normalizeLegendHandleLabel();
    refreshPrintLegend();
    applyGraphPageMode();
    applyPrintPageStyle();
    scheduleReadOnlyMobilePlotLayout();
    if (landscapeOption) {{
      landscapeOption.addEventListener("change", function() {{
        applyGraphPageMode();
        applyPrintPageStyle();
        scheduleReadOnlyMobilePlotLayout();
      }});
    }}
    window.setTimeout(function() {{
      normalizeLegendHandleLabel();
      refreshPrintLegend();
      var mobileRelayout = applyReadOnlyMobilePlotLayout();
      if (mobileRelayout && mobileRelayout.catch) {{
        mobileRelayout.catch(function(error) {{
          console.warn("XRD mobile read-only plot layout failed.", error);
        }});
      }}
      if (window.Plotly && window.Plotly.Plots && gd) window.Plotly.Plots.resize(gd);
    }}, 600);
    window.setTimeout(function() {{
      if (!window.Plotly || !gd) return;
      createPrintPlotImage().catch(function(error) {{
        console.warn("XRD print graph snapshot failed.", error);
      }});
    }}, 1000);
    window.addEventListener("beforeprint", preparePrintLegend);
    window.addEventListener("afterprint", function() {{
      removePrintPageStyle();
      restorePrintLegend();
      restoreScreenPlotLayout();
      scheduleReadOnlyMobilePlotLayout();
    }});
    window.addEventListener("resize", scheduleReadOnlyMobilePlotLayout);
    if (!button) return;
    button.addEventListener("click", function() {{
      if (button.disabled) return;
      setPdfExportBusy(true);
      if (shouldUseBrowserPrint()) {{
        Promise.resolve(openBrowserPrintFallback())
          .finally(function() {{
            setPdfExportBusy(false);
          }});
        return;
      }}
      var runExport = function() {{
        serverRenderPdf()
          .then(restoreAfterServerPdf)
          .catch(function(error) {{
            console.warn("XRD server PDF export failed.", error);
            restoreAfterServerPdf();
            var message = error && error.message ? error.message : "서버 PDF 생성에 실패했습니다.";
            var shouldFallback = window.location.protocol === "file:"
              || /연결 중 끊겼습니다|file URL|너무 큽니다/i.test(message);
            if (shouldFallback) {{
              window.alert(message + "\\n\\n브라우저 인쇄 창을 열겠습니다. 대상에서 PDF 저장을 선택해 주세요.");
              return openBrowserPrintFallback();
            }}
            window.alert(message);
          }})
          .finally(function() {{
            setPdfExportBusy(false);
          }});
      }};
      runExport();
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
    peak_tables = parse_peak_list_tables(table_files or [])

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
                meta={
                    "xrd_raw": True,
                    "xrd_raw_stem": raw_stem,
                    "xrd_raw_group": raw_stem,
                    "xrd_legend_kind": "raw",
                },
            )
        )
        idxs.append(trace_idx)
        trace_idx += 1

        pdf_files = sorted(str(path) for path in Path(pdf_dir).rglob("*.pdf"))
        items = []
        for pdf_path in pdf_files:
            try:
                peaks = parse_pdf_peaks(pdf_path)
            except Exception:
                warnings.append(
                    f"경고: '{os.path.basename(pdf_path)}' PDF를 읽지 못했습니다. "
                    "파일은 PDF 뷰어에서 열릴 수 있지만 자동 피크 표 추출에는 실패했습니다. "
                    "ICDD Card의 표 구조가 이미지이거나 비표준 PDF일 수 있습니다."
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
            path_category, folder_group, category_source = phase_category_from_pdf_path(
                pdf_path,
                pdf_dir,
            )
            category = path_category or classify_phase_candidate(match)
            items.append({
                "label": label,
                "color": color,
                "peaks": peaks,
                "metadata": metadata,
                "match": match,
                "category": category,
                "category_source": category_source,
                "category_locked": path_category is not None,
                "folder_group": folder_group,
                "source_pdf": pdf_path,
            })

        assign_relative_phase_categories(items)
        items = sort_phase_candidates(items)
        last_section: tuple[str, str] | None = None
        for item_index, item in enumerate(items):
            category = item["category"]
            section = (category, _phase_folder_group_key(item))
            if section != last_section:
                fig.add_trace(
                    go.Scatter(
                        x=[None],
                        y=[None],
                        mode="lines",
                        name=_phase_section_separator_label(item),
                        line=dict(color="#cbd5e1", width=1),
                        hoverinfo="skip",
                        showlegend=True,
                        meta={
                            "xrd_separator": True,
                            "xrd_category": category,
                            "xrd_folder_group": _phase_folder_group_key(item),
                            "xrd_raw_group": raw_stem,
                            "xrd_legend_kind": "separator",
                        },
                    )
                )
                idxs.append(trace_idx)
                trace_idx += 1
                last_section = section

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
                    meta={
                        "xrd_phase_candidate": True,
                        "xrd_phase_label": item["label"],
                        "xrd_phase_category": category,
                        "xrd_phase_category_source": item.get("category_source") or "",
                        "xrd_phase_folder_group": item.get("folder_group") or "",
                        "xrd_phase_group_key": _phase_similarity_key(item),
                        "xrd_raw_group": raw_stem,
                        "xrd_legend_kind": "phase",
                        "xrd_phase_name": item["metadata"].get("phase_name") or "",
                        "xrd_phase_formula": item["metadata"].get("formula") or "",
                        "xrd_phase_card_no": item["metadata"].get("card_no") or "",
                        "xrd_original_color": item["color"],
                    },
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
    if origin:
        fig.update_layout(
            title=dict(
                text="",
                font=dict(family="Arial", size=22, color="black"),
                x=0.5,
                xanchor="center",
            ),
            hovermode="closest",
            autosize=True,
            margin=dict(l=70, r=30, t=28, b=120),
            legend=dict(groupclick="toggleitem", traceorder="grouped"),
        )
        fig.update_xaxes(title_text="2θ (°)", range=xrange)
        fig.update_yaxes(title_text="Intensity (cps)", rangemode="tozero")
    else:
        fig.update_layout(
            title="",
            xaxis_title="2θ (°)",
            yaxis_title="Intensity (cps)",
            template="plotly_white",
            hovermode="closest",
            autosize=True,
            margin=dict(l=60, r=30, t=28, b=120),
            legend=dict(groupclick="toggleitem", traceorder="grouped"),
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
        peak_tables=peak_tables,
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
            wide_legend_inside=True,
            crosshair=True,
            title_edit=True,
            legend_text_edit=True,
            trace_highlight=True,
            highlight_pickable=raw_line_indices,
            highlight_groups=highlight_groups,
            image_filename=first_stem,
            image_format=XRD_DOWNLOAD_IMAGE_FORMAT,
            image_format_selector=XRD_IMAGE_FORMAT_SELECTOR,
            post_body_html=(
                build_xrd_axis_text_guard_js("xrd-plot")
                + build_xrd_legend_checkbox_js("xrd-plot")
                + build_xrd_phase_group_editor_js("xrd-plot")
                + build_xrd_tool_drawer_js("xrd-plot")
            )
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
            peak_tables=peak_tables,
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

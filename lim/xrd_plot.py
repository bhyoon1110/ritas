"""XRD raw 데이터(.txt)를 Plotly로 그리고, ICDD Card PDF의 피크 표를
2θ 위치에 Norm. I.(0~100%) 높이의 수직 막대로 오버레이한다.
또한 그래프 아래에 각 PDF의 피크 표를 함께 출력한다.

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

    옵션을 함께 쓴 예시)
        python xrd_plot.py "Mix2.txt" "ICDD Card" --origin -o paper_fig.html

[5] 결과 확인
    - 생성된 .html 파일을 웹 브라우저로 열면 된다.
    - 그래프는 반응형(모바일/태블릿 대응)이며, 화면 폭에 따라 범례 위치가
      자동으로 바뀐다(기준: 아래 LEGEND_BREAKPOINT_PX).
    - 범례는 손가락/마우스로 드래그해 위치를 옮길 수 있다.
    - 그래프 아래에는 PDF별 피크 표가 색상 구분과 함께 표시된다.

==========================================================================
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

import pdfplumber
import plotly.graph_objects as go

# 루트 common 패키지를 설치하지 않은 소스 실행도 지원한다.
_COMMON_DIR = Path(__file__).resolve().parents[1] / "common"
if str(_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMON_DIR))
from rist_common.plotting import (  # noqa: E402
    LEGEND_BREAKPOINT_PX,
    write_responsive_html,
)

HEADER = ["No.", "2θ, °", "d-value", "Norm. I.", "h k l"]

# raw 라인 색상(여러 raw 파일을 구분). 첫 번째는 검정(단일 파일 시 기존과 동일).
RAW_LINE_COLORS = [
    "#000000", "#1f3b73", "#7a1f1f", "#1f5c2e",
    "#5a2d82", "#8a5a00", "#005f6b", "#6b2d5a",
]

# PDF 피크 막대 색상 팔레트.
PEAK_PALETTE = [
    "#e41a1c", "#377eb8", "#4daf4a", "#984ea3",
    "#ff7f00", "#a65628", "#f781bf", "#999999",
]


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
def parse_pdf_peaks(path: str):
    peaks = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                if not table:
                    continue
                first = [(c or "").strip() for c in table[0]]
                if first[:5] != HEADER:
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
        for label, color, peaks, trace_idx in items:
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


def build_group_toggle_js(div_id: str, group_map: dict) -> str:
    """범례 그룹 제목(최상위 raw 범주)을 클릭하면 그 그룹 전체를 한꺼번에
    켜고/끄는 JS 스니펫. (개별 항목 토글은 Plotly 기본 동작이 담당)

    group_map: {raw_stem: [그 그룹에 속한 trace 인덱스, ...]}
    """
    gm = json.dumps(group_map, ensure_ascii=True)
    return f"""
<script>
(function() {{
  var gd = document.getElementById("{div_id}");
  if (!gd) return;
  var GROUPS = {gm};
  function indicesFor(text) {{
    text = (text || "").trim();
    if (GROUPS[text]) return GROUPS[text];
    var keys = Object.keys(GROUPS);
    for (var i = 0; i < keys.length; i++) {{
      if (keys[i].trim() === text) return GROUPS[keys[i]];
    }}
    return null;
  }}
  function toggleGroup(idxs) {{
    if (!window.Plotly || !idxs || !idxs.length) return;
    var allOn = idxs.every(function(i) {{
      var v = gd.data[i].visible; return v === true || v === undefined;
    }});
    window.Plotly.restyle(gd, {{ "visible": allOn ? "legendonly" : true }}, idxs);
  }}
  function isOn(i) {{
    var v = gd.data[i] && gd.data[i].visible;
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
      var idxs = indicesFor(key) || [];
      var anyOn = idxs.some(function(i) {{
        var c = document.querySelector('.xrd-card[data-trace="' + i + '"]');
        return c && shown(i);
      }});
      h.style.display = anyOn ? "" : "none";
    }});
  }}
  function bind() {{
    // 범례 그룹 제목(최상위 raw 범주) 항목에만 클릭 핸들러를 붙인다.
    // Plotly 는 그룹 제목 항목의 __data__[0].groupTitle 를 객체로 둔다.
    var items = gd.querySelectorAll("g.legend g.traces");
    items.forEach(function(it) {{
      var d = it.__data__;
      var meta = Array.isArray(d) ? d[0] : d;
      if (!meta || !meta.groupTitle || typeof meta.groupTitle !== "object") return;
      if (it.__xrdBound) return;
      it.__xrdBound = true;
      it.style.cursor = "pointer";
      var tx = it.querySelector("text.legendtext");
      it.addEventListener("click", function(ev) {{
        var idxs = indicesFor(tx ? tx.textContent : "");
        if (idxs) {{ ev.stopPropagation(); ev.preventDefault(); toggleGroup(idxs); }}
      }}, true);
    }});
  }}
  function init() {{
    if (!window.Plotly) {{ setTimeout(init, 80); return; }}
    bind();
    syncTables();
    gd.on("plotly_afterplot", bind);
    gd.on("plotly_restyle", syncTables);
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


def main():
    args = parse_args()
    pairs = collect_pairs(args)

    first_stem = os.path.splitext(os.path.basename(pairs[0][0]))[0]
    # 출력 파일명: raw 파일명들을 '_'로 연결하고 끝에 '_result' 를 붙인다.
    # 기본 저장 위치는 -o 미지정 시 현재 실행 위치(cwd).
    raw_stems = [os.path.splitext(os.path.basename(r))[0] for r, _ in pairs]
    if args.output:
        out_html = args.output
    else:
        out_html = os.path.join(os.getcwd(), "_".join(raw_stems) + "_result.html")

    fig = go.Figure()
    peak_ci = 0                 # PDF 피크 색상 인덱스(전체 누적)
    trace_idx = 0              # 현재까지 추가한 trace 수
    group_map = {}             # {raw_stem: [trace 인덱스, ...]}  (그룹 토글용)
    groups_for_tables = []     # [(raw_stem, raw_color, [(label, color, peaks)])]
    summary = []               # 출력용 [(raw_stem, n_points, raw_max, items)]
    all_x = []

    for gi, (raw_txt, pdf_dir) in enumerate(pairs):
        if not os.path.isfile(raw_txt):
            raise SystemExit(f"raw 파일을 찾을 수 없습니다: {raw_txt}")
        if not os.path.isdir(pdf_dir):
            raise SystemExit(f"PDF 폴더를 찾을 수 없습니다: {pdf_dir}")

        raw_stem = os.path.splitext(os.path.basename(raw_txt))[0]  # ".txt" 제거
        gid = f"g{gi}"
        raw_color = RAW_LINE_COLORS[gi % len(RAW_LINE_COLORS)]

        rx, ry = load_raw(raw_txt)
        raw_max = max(ry) if ry else 1.0
        all_x += rx
        idxs = []

        # raw 라인: 그룹의 첫 trace이며 그룹 제목(최상위 raw 범주)을 정의한다.
        fig.add_trace(
            go.Scatter(
                x=rx, y=ry, mode="lines", name=raw_stem,
                line=dict(color=raw_color, width=1),
                legendgroup=gid,
                legendgrouptitle_text=raw_stem,
            )
        )
        idxs.append(trace_idx)
        trace_idx += 1

        # 이 raw 파일에 속한 PDF 피크들(같은 legendgroup → 함께 토글 가능)
        pdf_files = sorted(glob.glob(os.path.join(pdf_dir, "*.pdf")))
        items = []
        for pdf_path in pdf_files:
            peaks = parse_pdf_peaks(pdf_path)
            if not peaks:
                continue
            color = PEAK_PALETTE[peak_ci % len(PEAK_PALETTE)]
            peak_ci += 1
            label = os.path.splitext(os.path.basename(pdf_path))[0]
            items.append((label, color, peaks, trace_idx))  # trace_idx = 이 피크의 trace 번호

            xs, ys, customdata = [], [], []
            for p in peaks:
                tt, ni, hkl = p["two_theta"], p["norm"], p["hkl"]
                h = ni / 100.0 * raw_max
                xs += [tt, tt, None]
                ys += [0.0, h, None]
                customdata += [(ni, hkl), (ni, hkl), (None, None)]

            fig.add_trace(
                go.Scatter(
                    x=xs, y=ys, mode="lines", name=label,
                    line=dict(color=color, width=1.5),
                    legendgroup=gid,
                    customdata=customdata,
                    hovertemplate=(
                        "2θ = %{x:.3f}°<br>"
                        "Norm. I. = %{customdata[0]:.1f}%<br>"
                        "h k l = %{customdata[1]}<extra>" + label + "</extra>"
                    ),
                )
            )
            idxs.append(trace_idx)
            trace_idx += 1

        warning = pdf_peak_warning(pdf_dir, len(pdf_files), len(items))
        if warning:
            print(warning)

        group_map[raw_stem] = idxs
        groups_for_tables.append((raw_stem, raw_color, items))
        summary.append((raw_stem, len(rx), raw_max, items))

    xrange = [min(all_x), max(all_x)] if all_x else None
    title_text = (
        f"XRD Pattern ({first_stem}) with ICDD Card Peaks"
        if len(pairs) == 1 else "XRD Patterns with ICDD Card Peaks"
    )

    # ----- 레이아웃 (스타일은 공통 모듈이 origin 적용을 담당) -----
    #   legend.groupclick="toggleitem": 범례 개별 항목은 하나씩 토글.
    #   그룹 전체 토글은 그룹 제목 클릭(아래 build_group_toggle_js)이 담당.
    if args.origin:
        fig.update_layout(
            title=dict(
                text=title_text,
                font=dict(family="Arial", size=22, color="black"),
                x=0.5, xanchor="center",
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

    # ----- 공통 모듈로 반응형 HTML 출력 (모든 공통 기능 적용) -----
    #   origin / 반응형 범례 / crosshair / title_edit / trace_highlight /
    #   image_format_selector + post_body_html(그룹 토글 JS + PDF 피크 표)
    #   더블클릭 강조는 raw 라인(각 그룹 첫 trace)만 대상으로 하고,
    #   강조 시 그 raw + 소속 피크(2θ 수직바)가 함께 살아나도록 그룹을 넘긴다.
    raw_line_indices = [idxs[0] for idxs in group_map.values() if idxs]
    highlight_groups = {idxs[0]: idxs for idxs in group_map.values() if idxs}
    tables_html = build_tables_html(groups_for_tables)
    group_toggle_js = build_group_toggle_js("xrd-plot", group_map)
    write_responsive_html(
        fig,
        out_html,
        div_id="xrd-plot",
        origin=args.origin,
        legend_breakpoint_px=LEGEND_BREAKPOINT_PX,
        crosshair=True,
        title_edit=True,
        legend_text_edit=True,
        trace_highlight=True,
        highlight_pickable=raw_line_indices,
        highlight_groups=highlight_groups,
        image_filename=first_stem,
        image_format_selector=True,
        post_body_html=group_toggle_js + tables_html,
        config={"scrollZoom": True},
    )

    print(f"Saved: {out_html}")
    for raw_stem, n_points, raw_max, items in summary:
        print(f"[{raw_stem}] raw points: {n_points}, raw_max: {raw_max:.1f}")
        for label, _color, peaks, _ti in items:
            print(f"    {label}: {len(peaks)} peaks")


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
        "-o", "--output", default=None,
        help="출력 HTML 경로 (기본: 실행 위치에 raw파일명들을 '_'로 연결한 'A_B_result.html')",
    )
    parser.add_argument(
        "--origin", action="store_true",
        help="Origin(OriginLab) 논문 스타일로 그린다 (기본: 원래 디자인)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()

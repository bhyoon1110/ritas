# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: RIST 프로젝트의 모든 Plotly 기반 그래프(HTML)에 공통 스타일·출력
#            설정을 적용하는 공용 모듈. XRD/FT-IR의 Origin 논문 스타일,
#            모바일 반응형 뷰포트, 범례 이동/반응형 배치를 표준화한다.
# 실행 방법: 모듈 — 직접 실행하지 않고 import해서 사용
#            (apply_origin_style / write_responsive_html / fig_to_responsive_html)
# ─────────────────────────────────────────────────────────────────────────────
"""공통 Plotly 그래프 스타일/출력 모듈.

이 모듈은 RIST 프로젝트의 모든 Plotly 기반 그래프(HTML)에 동일한 설정을
적용하기 위한 헬퍼를 모았다. XRD/FT-IR에서 검증된 다음 설정을
공통으로 제공한다.

  1) Origin(OriginLab) 논문 스타일
       - 사방 테두리 박스(mirror), 안쪽 눈금, 그리드 제거, 굵은 검정 축
       - apply_origin_style(fig) 로 임의의 Figure(서브플롯 포함)에 적용
  2) 모바일 친화적 반응형 출력
       - viewport 메타태그 삽입, config.responsive=True
  3) 범례 전용 핸들 드래그 이동
  4) 화면 너비에 따른 범례 위치 자동 전환
       - 넓은 화면(>=LEGEND_BREAKPOINT_PX): 그래프 안 우측 상단(세로)
       - 좁은 화면(폰): 그래프 아래 가로 배치

사용 예)
    from rist_common.plotting import write_responsive_html
    write_responsive_html(fig, "out.html", div_id="peak-plot", origin=args.origin)
"""

from __future__ import annotations

import json
from collections.abc import Mapping

# 범례 위치 전환 기준 화면 너비(px). 이 값 이상이면 그래프 안쪽(세로),
# 미만이면 그래프 아래(가로)로 범례를 배치한다.
LEGEND_BREAKPOINT_PX = 768

# 공통 색상 팔레트 (필요 시 각 스크립트에서 재사용)
PALETTE = [
    "#e41a1c", "#377eb8", "#4daf4a", "#984ea3",
    "#ff7f00", "#a65628", "#f781bf", "#999999",
]

# Origin(OriginLab) 논문 스타일 축 설정.
# title_text/range 등은 넘기지 않으므로 기존 figure의 제목·범위는 보존된다.
_ORIGIN_AXIS = dict(
    showline=True,            # 축선 표시
    linecolor="black",
    linewidth=2,
    mirror=True,             # 사방 테두리(박스)
    ticks="inside",          # 눈금 안쪽
    ticklen=7,
    tickwidth=2,
    tickcolor="black",
    showgrid=False,          # 그리드 제거
    zeroline=False,
    tickfont=dict(family="Arial", size=14, color="black"),
    title_font=dict(family="Arial", size=18, color="black"),
    minor=dict(ticks="inside", ticklen=4, tickwidth=1.5, tickcolor="black"),
)

_VIEWPORT_META = (
    '<meta name="viewport" '
    'content="width=device-width, initial-scale=1, maximum-scale=5">'
)


def apply_origin_style(fig):
    """Figure(서브플롯 포함) 전체에 Origin 논문 스타일을 적용한다.

    update_xaxes/update_yaxes 는 기본적으로 모든 축에 병합 적용되므로
    기존에 지정한 range/title/showticklabels 등은 유지된다.
    """
    fig.update_xaxes(**_ORIGIN_AXIS)
    fig.update_yaxes(**_ORIGIN_AXIS)
    fig.update_layout(
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(family="Arial", color="black"),
    )
    return fig


def apply_crosshair_spikes(fig):
    """마우스 커서 위치에 십자 점선(spike line)을 표시하도록 축을 설정한다."""
    spike = dict(
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikecolor="#888",
        spikethickness=1,
        spikedash="dot",
    )
    fig.update_xaxes(**spike)
    fig.update_yaxes(**spike)
    return fig


def apply_legend_text(
    fig,
    legend_text: Mapping[int | str, str] | None = None,
    legend_group_text: Mapping[str, str] | None = None,
):
    """Figure의 범례 텍스트를 trace index/name 또는 legendgroup 기준으로 변경한다.

    legend_text:
      - {0: "Sample"} 처럼 trace index 기준 변경
      - {"Raw": "원본"} 처럼 기존 trace.name 기준 변경
    legend_group_text:
      - {"group-a": "Group A"} 처럼 trace.legendgroup 또는 현재 group title 기준 변경
    """
    if legend_text:
        index_map = {k: v for k, v in legend_text.items() if isinstance(k, int)}
        name_map = {str(k): v for k, v in legend_text.items() if not isinstance(k, int)}
        for idx, trace in enumerate(fig.data):
            if idx in index_map:
                trace.name = index_map[idx]
                continue
            name = getattr(trace, "name", None)
            if name is not None and str(name) in name_map:
                trace.name = name_map[str(name)]

    if legend_group_text:
        group_map = {str(k): v for k, v in legend_group_text.items()}
        for trace in fig.data:
            legendgroup = getattr(trace, "legendgroup", None)
            group_title = getattr(trace, "legendgrouptitle", None)
            current_title = getattr(group_title, "text", None) if group_title else None
            next_title = None
            if legendgroup is not None and str(legendgroup) in group_map:
                next_title = group_map[str(legendgroup)]
            elif current_title is not None and str(current_title) in group_map:
                next_title = group_map[str(current_title)]
            if next_title is not None:
                trace.legendgrouptitle = {"text": next_title}

    return fig


def _crosshair_js(div_id: str) -> str:
    """마우스 위치의 x/y 데이터 좌표를 실시간 표시하는 JS 스니펫.

    - 마우스가 곡선 근처(SNAP_PX 이내)로 가면 가장 가까운 데이터 점에 스냅하여
      점 위에 마커를 띄우고 그 점의 정확한 x/y 값을 표시한다.
    - 곡선에서 멀면 커서 위치의 자유 좌표를 표시한다.
    """
    return f"""
<script>
(function() {{
  var gd = document.getElementById("{div_id}");
  if (!gd) return;
  var SNAP_PX = 24;   // 이 픽셀 거리 이내면 데이터 점에 스냅
  var box = document.createElement("div");
  box.style.cssText = "position:absolute;z-index:9;"
    + "background:rgba(255,255,255,0.88);border:1px solid #bbb;border-radius:4px;"
    + "padding:3px 8px;font:12px/1.3 Arial,monospace;color:#333;"
    + "pointer-events:none;display:none;white-space:nowrap;";
  var dot = document.createElement("div");
  dot.style.cssText = "position:absolute;z-index:9;width:9px;height:9px;"
    + "border-radius:50%;border:2px solid #333;background:#fff;"
    + "pointer-events:none;display:none;box-sizing:border-box;"
    + "transform:translate(-50%,-50%);";
  function fmt(v) {{
    if (!isFinite(v)) return "\u2013";
    var a = Math.abs(v);
    if (a >= 100) return v.toFixed(0);
    if (a >= 1) return v.toFixed(2);
    return v.toFixed(3);
  }}
  // 정렬된 배열에서 target에 가장 가까운 인덱스(이분 탐색)
  function nearestIdx(xs, target) {{
    var lo = 0, hi = xs.length - 1;
    if (hi < 0) return -1;
    var asc = xs[hi] >= xs[0];
    while (lo < hi) {{
      var mid = (lo + hi) >> 1;
      var cond = asc ? (xs[mid] < target) : (xs[mid] > target);
      if (cond) lo = mid + 1; else hi = mid;
    }}
    return lo;
  }}
  if (getComputedStyle(gd).position === "static") gd.style.position = "relative";
  gd.appendChild(box);
  gd.appendChild(dot);

  // 화면 좌표(clientX, clientY)에서 tol 픽셀 이내 가장 가까운 트레이스 인덱스 반환(없으면 -1)
  gd._nearestCurveAt = function(clientX, clientY, tol) {{
    var fl = gd._fullLayout;
    if (!fl || !fl.xaxis || !fl.yaxis) return -1;
    var drag = gd.querySelector(".nsewdrag");
    if (!drag) return -1;
    var xa = fl.xaxis, ya = fl.yaxis;
    var r = drag.getBoundingClientRect();
    var px = clientX - r.left, py = clientY - r.top;
    if (px < 0 || py < 0 || px > r.width || py > r.height) return -1;
    var curX = xa.p2d(px);
    var data = gd._fullData || gd.data || [];
    var bestT = -1, bestD = tol * tol;
    for (var t = 0; t < data.length; t++) {{
      var tr = data[t];
      if (!tr || tr.visible === false || tr.visible === "legendonly") continue;
      var xs = tr.x, ys = tr.y;
      if (!xs || !ys || !xs.length) continue;
      var j = nearestIdx(xs, curX);
      for (var k = j - 1; k <= j + 1; k++) {{
        if (k < 0 || k >= xs.length) continue;
        var dx = xa.d2p(xs[k]) - px, dy = ya.d2p(ys[k]) - py;
        var d2 = dx * dx + dy * dy;
        if (d2 < bestD) {{ bestD = d2; bestT = t; }}
      }}
    }}
    return bestT;
  }};

  function hide() {{
    box.style.display = "none";
    dot.style.display = "none";
    gd._snapCurve = -1;
    gd._snapPoint = null;
  }}
  gd.addEventListener("mousemove", function(e) {{
    gd._lastPtr = {{ x: e.clientX, y: e.clientY }};   // 마지막 포인터(더블클릭 하이라이트용)
    var fl = gd._fullLayout;
    if (!fl || !fl.xaxis || !fl.yaxis) {{ hide(); return; }}
    var drag = gd.querySelector(".nsewdrag");
    if (!drag) {{ hide(); return; }}
    var xa = fl.xaxis, ya = fl.yaxis;
    var r = drag.getBoundingClientRect();
    var gdr = gd.getBoundingClientRect();
    var px = e.clientX - r.left, py = e.clientY - r.top;
    if (px < 0 || py < 0 || px > r.width || py > r.height) {{ hide(); return; }}
    var curX = xa.p2d(px);

    // ── 가장 가까운 데이터 점 탐색 ──
    var data = gd._fullData || gd.data || [];
    var best = null, bestD = SNAP_PX * SNAP_PX;
    for (var t = 0; t < data.length; t++) {{
      var tr = data[t];
      if (!tr || tr.visible === false || tr.visible === "legendonly") continue;
      var xs = tr.x, ys = tr.y;
      if (!xs || !ys || !xs.length) continue;
      var j = nearestIdx(xs, curX);
      // 인접 인덱스도 함께 비교 (선형 구간 대비)
      for (var k = j - 1; k <= j + 1; k++) {{
        if (k < 0 || k >= xs.length) continue;
        var cx = xa.d2p(xs[k]), cy = ya.d2p(ys[k]);
        var dx = cx - px, dy = cy - py;
        var d2 = dx * dx + dy * dy;
        if (d2 < bestD) {{ bestD = d2; best = {{ x: xs[k], y: ys[k], cx: cx, cy: cy, tt: t }}; }}
      }}
    }}

    box.style.display = "block";
    var anchorX, anchorY;   // gd 기준 박스 배치 기준점
    if (best) {{
      // 스냅된 트레이스 인덱스 공개(더블클릭 하이라이트 연동용)
      gd._snapCurve = best.tt;
      gd._snapPoint = {{ x: best.x, y: best.y, curve: best.tt }};
      // 데이터 점에 스냅: 마커 표시 + 정확한 값
      var dotL = r.left - gdr.left + best.cx;
      var dotT = r.top - gdr.top + best.cy;
      dot.style.left = dotL + "px";
      dot.style.top = dotT + "px";
      dot.style.display = "block";
      box.innerHTML = "x: " + fmt(best.x) + " &nbsp; y: " + fmt(best.y);
      anchorX = dotL; anchorY = dotT;
    }} else {{
      gd._snapCurve = -1;
      gd._snapPoint = null;
      dot.style.display = "none";
      box.innerHTML = "x: " + fmt(curX) + " &nbsp; y: " + fmt(ya.p2d(py));
      anchorX = e.clientX - gdr.left; anchorY = e.clientY - gdr.top;
    }}
    // 기준점 근처에 박스 배치 (가장자리 넘으면 반대편)
    var bx = anchorX + 14, by = anchorY + 14;
    if (bx + box.offsetWidth > gd.clientWidth) bx = anchorX - box.offsetWidth - 14;
    if (by + box.offsetHeight > gd.clientHeight) by = anchorY - box.offsetHeight - 14;
    box.style.left = bx + "px";
    box.style.top = by + "px";
  }});
  gd.addEventListener("mouseleave", hide);
}})();
</script>
"""


def _title_edit_js(div_id: str) -> str:
    """제목을 더블클릭하면 HTML 태그 없이 본문 글자만 인라인 편집하는 JS 스니펫.

    - 단일 클릭이 아니라 더블클릭에서만 편집창이 열린다.
    - 제목 텍스트의 <span ...> 접미(부제)는 그대로 보존하고 본문만 수정한다.
    - Enter=저장, Escape=취소, 입력창 바깥 클릭(blur)=저장.
    - Plotly 네이티브 제목 편집 시 나타나는 빈 부제 자리표시자
      ("Click to enter Plot subtitle")는 CSS로 숨긴다.
    """
    return f"""
<style>.gtitle-subtitle.js-placeholder {{ display: none !important; }}</style>
<script>
(function() {{
  var gd = document.getElementById("{div_id}");
  if (!gd) return;
  var editing = false;
  function titleParts() {{
    var full = (gd.layout && gd.layout.title && gd.layout.title.text) || "";
    var i = full.indexOf("<span");
    return i >= 0
      ? [full.slice(0, i).trim(), full.slice(i)]   // [본문, 접미(span)]
      : [full.trim(), ""];
  }}
  gd.addEventListener("dblclick", function(e) {{
    if (editing) return;
    var t = gd.querySelector(".gtitle");
    if (!t) return;
    // 제목 텍스트는 pointer-events가 없을 수 있어 좌표로 직접 판정한다.
    var tr = t.getBoundingClientRect();
    if (e.clientX < tr.left - 6 || e.clientX > tr.right + 6 ||
        e.clientY < tr.top - 6 || e.clientY > tr.bottom + 6) return;
    e.preventDefault();
    e.stopPropagation();
    editing = true;
    var p = titleParts();
    var gr = gd.getBoundingClientRect();
    var inp = document.createElement("input");
    inp.type = "text";
    inp.value = p[0];
    inp.style.cssText = "position:absolute;z-index:30;font:bold 20px Arial;"
      + "padding:2px 6px;border:1px solid #4a90d9;border-radius:4px;"
      + "background:#fff;color:#222;box-sizing:border-box;";
    inp.style.left = (tr.left - gr.left) + "px";
    inp.style.top = (tr.top - gr.top - 2) + "px";
    inp.style.minWidth = Math.max(tr.width + 40, 220) + "px";
    if (getComputedStyle(gd).position === "static") gd.style.position = "relative";
    gd.appendChild(inp);
    inp.focus();
    inp.select();
    function finish(save) {{
      if (!editing) return;
      editing = false;
      if (save && inp.value.trim() !== "") {{
        var suffix = p[1] ? " " + p[1] : "";
        if (gd._ristHistory) gd._ristHistory.capture();
        window.Plotly.relayout(gd, {{ "title.text": inp.value.trim() + suffix }});
      }}
      if (inp.parentNode) inp.parentNode.removeChild(inp);
    }}
    inp.addEventListener("keydown", function(ev) {{
      if (ev.key === "Enter") finish(true);
      else if (ev.key === "Escape") finish(false);
    }});
    // 더블클릭 직후의 우발적 blur로 즉시 닫히지 않도록 잠시 뒤 등록
    setTimeout(function() {{
      inp.addEventListener("blur", function() {{ finish(true); }});
    }}, 250);
  }}, true);
}})();
</script>
"""


def _legend_text_edit_js(div_id: str) -> str:
    """HTML 화면에서 범례 항목을 수정하는 JS 스니펫.

    - 범례 텍스트 더블클릭: 해당 항목을 바로 인라인 편집
    - 그래프 우상단 "범례 수정" 버튼: 범례 항목 목록을 패널에서 일괄 편집
    """
    return f"""
<style>
#{div_id} .rist-plot-control-row {{
  position: absolute;
  top: 58px;
  right: 30px;
  z-index: 20;
  display: flex;
  gap: 8px;
  align-items: center;
}}
#{div_id} .rist-legend-edit-button {{
  order: 20;
  border: 1px solid #c7d0dd;
  border-radius: 4px;
  background: rgba(255,255,255,0.92);
  color: #1f2933;
  cursor: pointer;
  font: 12px Arial, sans-serif;
  padding: 5px 9px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.08);
}}
#{div_id} .rist-legend-edit-panel {{
  position: absolute;
  top: 94px;
  right: 30px;
  z-index: 21;
  display: none;
  width: min(360px, calc(100% - 16px));
  max-width: calc(100% - 16px);
  max-height: min(420px, calc(100% - 54px));
  overflow: auto;
  overflow-x: hidden;
  background: rgba(255,255,255,0.97);
  border: 1px solid #c7d0dd;
  border-radius: 6px;
  box-shadow: 0 4px 18px rgba(0,0,0,0.16);
  padding: 10px;
  box-sizing: border-box;
}}
#{div_id} .rist-legend-edit-head {{
  position: sticky;
  top: -10px;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: -10px -10px 8px;
  padding: 10px;
  border-bottom: 1px solid #d7dee8;
  background: rgba(255,255,255,0.98);
  font: bold 13px Arial, sans-serif;
  color: #1f2933;
  cursor: move;
  user-select: none;
  touch-action: none;
}}
#{div_id} .rist-legend-edit-panel.is-panel-dragging {{
  box-shadow: 0 6px 24px rgba(0,0,0,0.22);
}}
#{div_id} .rist-legend-opacity-control {{
  display: flex;
  align-items: center;
  gap: 5px;
  margin-left: auto;
  color: #52606d;
  cursor: default;
  font: normal 10px Arial, sans-serif;
  white-space: nowrap;
}}
#{div_id} .rist-legend-opacity-slider {{
  width: 68px;
  height: 16px;
  margin: 0;
  accent-color: #52606d;
  cursor: pointer;
}}
#{div_id} .rist-legend-edit-close {{
  border: 0;
  background: transparent;
  color: #52606d;
  cursor: pointer;
  font: 18px Arial, sans-serif;
  line-height: 1;
  padding: 0 4px;
}}
#{div_id} .rist-legend-edit-row {{
  display: flex;
  gap: 6px;
  align-items: flex-start;
  margin: 6px 0;
  min-width: 0;
  max-width: 100%;
}}
#{div_id} .rist-legend-edit-row.is-sample {{
  margin: 12px 0 5px;
  padding: 6px;
  border: 1px solid #b8c8d8;
  border-radius: 5px;
  background: #eef4f8;
}}
#{div_id} .rist-legend-edit-row.is-sample:first-child {{
  margin-top: 4px;
}}
#{div_id} .rist-legend-edit-row.is-sample .rist-legend-edit-input {{
  border-color: #9fb3c8;
  background: #fff;
  font-weight: bold;
}}
#{div_id} .rist-legend-edit-row.is-peak {{
  position: relative;
  margin-left: 20px;
  padding-left: 8px;
  border-left: 2px solid #d7e0e8;
}}
#{div_id} .rist-legend-edit-row.is-peak::before {{
  content: "";
  position: absolute;
  left: -2px;
  top: 50%;
  width: 8px;
  border-top: 2px solid #d7e0e8;
}}
#{div_id} .rist-legend-edit-row.is-peak.is-group-member {{
  margin-left: 30px;
}}
#{div_id} .rist-legend-edit-row.is-pending-delete {{
  opacity: 0.58;
}}
#{div_id} .rist-legend-edit-row.is-pending-delete .rist-legend-edit-input {{
  text-decoration: line-through;
}}
#{div_id} .rist-legend-edit-row.is-pending-group-remove {{
  background: #fff8e8;
}}
#{div_id} .rist-legend-edit-row.is-pending-group-remove .rist-legend-edit-input {{
  text-decoration: line-through;
  text-decoration-color: #b7791f;
}}
#{div_id} .rist-legend-row-kind {{
  flex: 0 0 34px;
  color: #52606d;
  font: bold 10px Arial, sans-serif;
  text-align: center;
  margin-top: 6px;
}}
#{div_id} .rist-legend-edit-row.is-sample .rist-legend-row-kind {{
  color: #174a68;
}}
#{div_id} .rist-legend-edit-row.is-peak .rist-legend-row-kind {{
  color: #66788a;
  cursor: grab;
  user-select: none;
}}
#{div_id} .rist-legend-edit-row.is-dragging {{
  opacity: 0.55;
}}
#{div_id} .rist-legend-edit-row.is-pending-group-add {{
  background: #edf7f1;
}}
#{div_id} .rist-legend-group-row {{
  display: flex;
  gap: 6px;
  align-items: flex-start;
  margin: 10px 0 4px;
  padding: 6px 7px;
  border: 1px solid #d7dee8;
  border-radius: 5px;
  background: #f8fafc;
  min-width: 0;
  max-width: 100%;
}}
#{div_id} .rist-legend-group-row.is-drop-target {{
  border-color: #2f855a;
  background: #e8f5ed;
  box-shadow: 0 0 0 2px rgba(47,133,90,0.18);
}}
#{div_id} .rist-legend-group-row .rist-legend-row-kind {{
  color: #44546a;
}}
#{div_id} .rist-legend-group-row.is-pending-clear {{
  opacity: 0.62;
  text-decoration: line-through;
}}
#{div_id} .rist-legend-group-title {{
  flex: 1 1 auto;
  min-width: 0;
  border: 1px solid #c7d0dd;
  border-radius: 4px;
  background: #fff;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #1f2933;
  font: bold 12px Arial, sans-serif;
  padding: 5px 7px;
  box-sizing: border-box;
}}
#{div_id} .rist-legend-group-row .rist-legend-row-kind {{
  margin-top: 7px;
}}
#{div_id} .rist-legend-group-add,
#{div_id} .rist-legend-group-clear,
#{div_id} .rist-legend-group-remove {{
  flex: 0 0 auto;
  width: 26px;
  height: 26px;
  border: 1px solid #c7d0dd;
  border-radius: 4px;
  background: #fff;
  color: #52606d;
  cursor: pointer;
  font: 13px Arial, sans-serif;
  line-height: 1;
  padding: 0;
}}
#{div_id} .rist-legend-group-add {{
  border-color: #8fb3a3;
  color: #276749;
  font-size: 16px;
}}
#{div_id} .rist-legend-group-add.has-pending-add {{
  background: #276749;
  color: #fff;
  font-size: 11px;
}}
#{div_id} .rist-legend-group-remove {{
  border-color: #d6b778;
  color: #9c6515;
  font-size: 16px;
}}
#{div_id} .rist-legend-edit-row.is-pending-group-remove .rist-legend-group-remove {{
  background: #b7791f;
  color: #fff;
}}
#{div_id} .rist-legend-peak-delete {{
  flex: 0 0 auto;
  width: 26px;
  height: 26px;
  border: 1px solid #d5a3a3;
  border-radius: 4px;
  background: #fff;
  color: #9b2c2c;
  cursor: pointer;
  font: 15px Arial, sans-serif;
  line-height: 1;
  padding: 0;
}}
#{div_id} .rist-legend-edit-row.is-pending-delete .rist-legend-peak-delete {{
  background: #9b2c2c;
  color: #fff;
}}
#{div_id} .rist-legend-group-color {{
  flex: 0 0 auto;
  width: 30px;
  height: 28px;
  border: 1px solid #c7d0dd;
  border-radius: 4px;
  background: #fff;
  cursor: pointer;
  padding: 2px;
  box-sizing: border-box;
}}
#{div_id} .rist-legend-color-input {{
  flex: 0 0 auto;
  width: 30px;
  height: 28px;
  border: 1px solid #c7d0dd;
  border-radius: 4px;
  background: #fff;
  cursor: pointer;
  padding: 2px;
  box-sizing: border-box;
}}
#{div_id} .rist-legend-edit-actions {{
  position: sticky;
  bottom: -10px;
  z-index: 2;
  display: flex;
  justify-content: flex-end;
  margin: 10px -10px -10px;
  padding: 10px;
  border-top: 1px solid #d7dee8;
  background: rgba(255,255,255,0.98);
}}
#{div_id} .rist-legend-bulk-controls {{
  order: 10;
  display: flex;
  gap: 8px;
  align-items: center;
}}
#{div_id} .rist-legend-bulk-button {{
  border: 1px solid #9fb3c8;
  border-radius: 4px;
  background: #f5f7fa;
  color: #1f2933;
  cursor: pointer;
  font: 12px Arial, sans-serif;
  padding: 5px 8px;
}}
#{div_id} .rist-legend-edit-input {{
  flex: 1 1 auto;
  min-width: 0;
  max-width: 100%;
  border: 1px solid #c7d0dd;
  border-radius: 4px;
  color: #1f2933;
  font: 12px Arial, sans-serif;
  line-height: 1.28;
  padding: 5px 7px;
  box-sizing: border-box;
  resize: vertical;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
  min-height: 28px;
  max-height: 96px;
}}
#{div_id} .rist-legend-edit-save-all {{
  flex: 0 0 auto;
  border: 1px solid #9fb3c8;
  border-radius: 4px;
  background: #f5f7fa;
  color: #1f2933;
  cursor: pointer;
  font: 12px Arial, sans-serif;
  padding: 5px 8px;
}}
</style>
<script>
(function() {{
  var gd = document.getElementById("{div_id}");
  if (!gd) return;
  var editing = false;

  function visibleLegendTraceIndexes() {{
    var fd = gd._fullData || gd.data || [];
    var idxs = [];
    for (var i = 0; i < fd.length; i++) {{
      var tr = fd[i];
      if (!tr) continue;
      if (tr.showlegend === false) continue;
      idxs.push(typeof tr.index === "number" ? tr.index : i);
    }}
    return idxs;
  }}

  function legendItemFor(target) {{
    var text = target && target.closest ? target.closest("text.legendtext") : null;
    if (!text) return null;
    var item = text.closest("g.traces");
    if (!item) return null;
    var items = Array.prototype.slice.call(
      gd.querySelectorAll("g.legend g.traces")
    ).filter(function(node) {{
      return node.querySelector("text.legendtext");
    }});
    var pos = items.indexOf(item);
    if (pos < 0) return null;
    var idxs = visibleLegendTraceIndexes();
    var curve = idxs[pos];
    if (curve == null) return null;
    return {{ text: text, curve: curve }};
  }}

  function traceName(curve) {{
    if (gd.data && gd.data[curve] && gd.data[curve].name != null) {{
      return String(gd.data[curve].name);
    }}
    return "";
  }}

  function legendDisplayToEdit(value) {{
    return String(value || "").replace(/<br\\s*\\/?>/gi, "\\n");
  }}

  function legendEditToDisplay(value) {{
    return String(value || "").trim().replace(/\\r?\\n/g, "<br>");
  }}

  function normalizeColor(value) {{
    if (typeof value === "string" && /^#[0-9a-fA-F]{{6}}$/.test(value)) {{
      return value;
    }}
    return "#374151";
  }}

  function traceColor(curve) {{
    var tr = (gd.data || [])[curve] || {{}};
    if (tr.marker && tr.marker.color) return normalizeColor(tr.marker.color);
    if (tr.line && tr.line.color) return normalizeColor(tr.line.color);
    return "#374151";
  }}

  function traceMeta(curve) {{
    var tr = (gd.data || [])[curve] || {{}};
    return tr.meta && typeof tr.meta === "object" ? tr.meta : {{}};
  }}

  function isSampleCurve(curve) {{
    return !!traceMeta(curve).rist_sample_parent;
  }}

  function isPeakCurve(curve) {{
    return !!traceMeta(curve).rist_peak;
  }}

  function sampleNameForCurve(curve) {{
    var meta = traceMeta(curve);
    var sampleGroup = meta.rist_sample_group
      || (meta.rist_peak && meta.rist_peak.sample_group)
      || "";
    if (!sampleGroup) return "";
    var data = gd.data || [];
    for (var i = 0; i < data.length; i++) {{
      var candidate = traceMeta(i);
      if (candidate.rist_sample_parent
          && String(candidate.rist_sample_group || "") === String(sampleGroup)) {{
        return traceName(i);
      }}
    }}
    return "";
  }}

  function legendEditGroup(curve) {{
    var meta = traceMeta(curve);
    if (meta.rist_legend_edit_group) return String(meta.rist_legend_edit_group);
    if (meta.rist_peak && meta.rist_peak.label_key) return String(meta.rist_peak.label_key);
    var tr = (gd.data || [])[curve] || {{}};
    if (tr.legendgroup != null && tr.legendgroup !== "") return String(tr.legendgroup);
    return "curve:" + curve;
  }}

  function peakCurvesForLegendItem(curve) {{
    var editGroup = legendEditGroup(curve);
    var data = gd.data || [];
    var curves = [];
    for (var i = 0; i < data.length; i++) {{
      if (isPeakCurve(i) && legendEditGroup(i) === editGroup) curves.push(i);
    }}
    return curves.length ? curves : [curve];
  }}

  function selectedPeakCurvesForGroup(groupKey) {{
    var data = gd.data || [];
    var curves = [];
    for (var i = 0; i < data.length; i++) {{
      var meta = traceMeta(i);
      if (!isPeakCurve(i) || !meta.rist_peak.selected) continue;
      if (manualPeakGroupKey(i) === groupKey) continue;
      curves.push(i);
    }}
    return curves;
  }}

  function manualPeakGroupKey(curve) {{
    var meta = traceMeta(curve);
    if (meta.rist_peak && meta.rist_peak.manual_group_key) {{
      return String(meta.rist_peak.manual_group_key);
    }}
    if (meta.rist_color_group && String(meta.rist_color_group).indexOf("manual-peak-group:") === 0) {{
      return String(meta.rist_color_group);
    }}
    return "";
  }}

  function manualPeakGroupName(curve) {{
    var meta = traceMeta(curve);
    if (meta.rist_peak && meta.rist_peak.group_name) {{
      return String(meta.rist_peak.group_name);
    }}
    var key = manualPeakGroupKey(curve);
    return key.indexOf("manual-peak-group:") === 0
      ? key.slice("manual-peak-group:".length)
      : key;
  }}

  function dispatchPeakGroupClear(groupKey) {{
    try {{
      gd.dispatchEvent(new CustomEvent("rist-peak-group-clear", {{
        detail: {{ groupKey: groupKey }}
      }}));
    }} catch (e) {{}}
  }}

  function dispatchPeakGroupUpdate(groupKey, name, color, addCurves, removeCurves) {{
    try {{
      gd.dispatchEvent(new CustomEvent("rist-peak-group-update", {{
        detail: {{
          groupKey: groupKey,
          name: name,
          color: color,
          addCurves: addCurves || [],
          removeCurves: removeCurves || []
        }}
      }}));
    }} catch (e) {{}}
  }}

  function dispatchPeakDelete(curves) {{
    if (!curves.length) return;
    try {{
      gd.dispatchEvent(new CustomEvent("rist-peak-delete", {{
        detail: {{ curves: curves }}
      }}));
    }} catch (e) {{}}
  }}

  function dispatchVisibilityChange(curves, visible) {{
    try {{
      gd.dispatchEvent(new CustomEvent("rist-legend-visibility-change", {{
        detail: {{ curves: curves, visible: visible }}
      }}));
    }} catch (e) {{}}
  }}

  function updateName(curve, value) {{
    value = String(value || "").trim();
    if (value === "" || !window.Plotly) return;
    if (gd._ristHistory) gd._ristHistory.capture();
    var data = gd.data || [];
    var base = data[curve] || {{}};
    var legendgroup = base.legendgroup;
    var editGroup = legendEditGroup(curve);
    var oldName = traceName(curve);
    var curves = [];
    for (var i = 0; i < data.length; i++) {{
      if (legendEditGroup(i) === editGroup) {{
        curves.push(i);
      }}
    }}
    if (!curves.length) curves = [curve];
    window.Plotly.restyle(gd, {{ "name": value }}, curves).then(function() {{
      try {{
        gd.dispatchEvent(new CustomEvent("rist-legend-name-change", {{
          detail: {{
            curve: curve,
            curves: curves,
            oldName: oldName,
            name: value,
            legendgroup: legendgroup,
            editGroup: editGroup
          }}
        }}));
      }} catch (e) {{}}
    }});
  }}

  function dispatchColorChange(curves, color) {{
    try {{
      gd.dispatchEvent(new CustomEvent("rist-legend-color-change", {{
        detail: {{ curves: curves, color: color }}
      }}));
    }} catch (e) {{}}
  }}

  function updateColor(curve, value) {{
    var color = normalizeColor(value);
    if (!window.Plotly) return;
    if (gd._ristHistory) gd._ristHistory.capture();
    var data = gd.data || [];
    var base = data[curve] || {{}};
    var legendgroup = base.legendgroup;
    var editGroup = legendEditGroup(curve);
    var curves = [];
    for (var i = 0; i < data.length; i++) {{
      if (legendEditGroup(i) === editGroup) {{
        curves.push(i);
      }}
    }}
    if (!curves.length) curves = [curve];
    window.Plotly.restyle(gd, {{
      "line.color": color,
      "marker.color": color
    }}, curves).then(function() {{
      dispatchColorChange(curves, color);
    }});
  }}

  function installPanel() {{
    if (gd._ristLegendEditInstalled) return;
    gd._ristLegendEditInstalled = true;
    if (getComputedStyle(gd).position === "static") gd.style.position = "relative";
    var toolbar = gd.querySelector(".rist-plot-control-row");
    if (!toolbar) {{
      toolbar = document.createElement("div");
      toolbar.className = "rist-plot-control-row";
      gd.appendChild(toolbar);
    }}

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "rist-legend-edit-button";
    btn.textContent = "\ubc94\ub840 \uc218\uc815";

    var panel = document.createElement("div");
    panel.className = "rist-legend-edit-panel";
    panel.innerHTML = "<div class='rist-legend-edit-head'>"
      + "<span>\ubc94\ub840 \uc218\uc815</span>"
      + "<label class='rist-legend-opacity-control' title='창 투명도'>"
      + "<input class='rist-legend-opacity-slider' type='range' min='55' max='100' value='97' "
      + "step='1' aria-label='범례 수정창 투명도'>"
      + "</label>"
      + "<button type='button' class='rist-legend-edit-close' aria-label='close'>×</button>"
      + "</div><div class='rist-legend-edit-body'></div>"
      + "<div class='rist-legend-edit-actions'>"
      + "<button class='rist-legend-edit-save-all' type='button'>\uc804\uccb4 \uc800\uc7a5</button>"
      + "</div>";
    toolbar.appendChild(btn);
    gd.appendChild(panel);

    function closePanel() {{
      panel.style.display = "none";
    }}

    var panelHead = panel.querySelector(".rist-legend-edit-head");
    var opacitySlider = panel.querySelector(".rist-legend-opacity-slider");
    var panelDrag = null;

    opacitySlider.addEventListener("input", function(ev) {{
      var value = Math.max(55, Math.min(100, Number(ev.target.value) || 97));
      panel.style.opacity = String(value / 100);
    }});

    function constrainPanelPosition(left, top) {{
      var gdRect = gd.getBoundingClientRect();
      var panelRect = panel.getBoundingClientRect();
      return {{
        left: Math.max(0, Math.min(left, gdRect.width - panelRect.width)),
        top: Math.max(0, Math.min(top, gdRect.height - panelRect.height))
      }};
    }}

    panelHead.addEventListener("pointerdown", function(ev) {{
      if (ev.button !== 0
          || ev.target.closest("button,input,.rist-legend-opacity-control")) return;
      var gdRect = gd.getBoundingClientRect();
      var panelRect = panel.getBoundingClientRect();
      panelDrag = {{
        offsetX: ev.clientX - panelRect.left,
        offsetY: ev.clientY - panelRect.top
      }};
      panel.style.left = (panelRect.left - gdRect.left) + "px";
      panel.style.top = (panelRect.top - gdRect.top) + "px";
      panel.style.right = "auto";
      panel.classList.add("is-panel-dragging");
      ev.preventDefault();
    }});

    document.addEventListener("pointermove", function(ev) {{
      if (!panelDrag) return;
      var gdRect = gd.getBoundingClientRect();
      var position = constrainPanelPosition(
        ev.clientX - gdRect.left - panelDrag.offsetX,
        ev.clientY - gdRect.top - panelDrag.offsetY
      );
      panel.style.left = position.left + "px";
      panel.style.top = position.top + "px";
      ev.preventDefault();
    }});

    function finishPanelDrag() {{
      if (!panelDrag) return;
      panelDrag = null;
      panel.classList.remove("is-panel-dragging");
    }}

    document.addEventListener("pointerup", finishPanelDrag);
    document.addEventListener("pointercancel", finishPanelDrag);
    window.addEventListener("resize", function() {{
      if (!panel.style.left || panel.style.display !== "block") return;
      var position = constrainPanelPosition(
        parseFloat(panel.style.left) || 0,
        parseFloat(panel.style.top) || 0
      );
      panel.style.left = position.left + "px";
      panel.style.top = position.top + "px";
    }});

    function renderRows() {{
      var body = panel.querySelector(".rist-legend-edit-body");
      var idxs = visibleLegendTraceIndexes();
      body.innerHTML = "";
      var draggedPeak = null;

      var items = [];
      var groups = {{}};
      idxs.forEach(function(curve) {{
        var key = manualPeakGroupKey(curve);
        if (!key) {{
          items.push({{ kind: "curve", curve: curve }});
          return;
        }}
        if (!groups[key]) {{
          groups[key] = {{ kind: "group", key: key, curves: [] }};
          items.push(groups[key]);
        }}
        groups[key].curves.push(curve);
      }});

      function pendingAddCurves(groupRow) {{
        try {{
          var curves = JSON.parse(groupRow.getAttribute("data-add-curves") || "[]");
          return Array.isArray(curves) ? curves : [];
        }} catch (e) {{
          return [];
        }}
      }}

      function updatePendingAddBadge(groupRow) {{
        var addButton = groupRow.querySelector(".rist-legend-group-add");
        if (!addButton) return;
        var curves = pendingAddCurves(groupRow);
        addButton.textContent = curves.length ? "+" + curves.length : "+";
        addButton.title = curves.length
          ? curves.length + "개 피크 추가 예정"
          : "선택한 피크 추가";
        addButton.classList.toggle("has-pending-add", curves.length > 0);
      }}

      function queueGroupAdd(groupRow, groupKey, curves, sourceRow) {{
        curves = (curves || []).filter(function(curve, index, values) {{
          return isPeakCurve(curve) && values.indexOf(curve) === index;
        }});
        if (!curves.length) return false;

        body.querySelectorAll(".rist-legend-group-row").forEach(function(otherRow) {{
          var current = pendingAddCurves(otherRow).filter(function(curve) {{
            return curves.indexOf(curve) < 0;
          }});
          otherRow.setAttribute("data-add-curves", JSON.stringify(current));
          updatePendingAddBadge(otherRow);
        }});

        var additions = curves.filter(function(curve) {{
          return manualPeakGroupKey(curve) !== groupKey;
        }});
        if (!additions.length) {{
          if (sourceRow) sourceRow.classList.remove("is-pending-group-add");
          return false;
        }}
        var pending = pendingAddCurves(groupRow).concat(additions)
          .filter(function(curve, index, values) {{
            return values.indexOf(curve) === index;
          }});
        groupRow.setAttribute("data-add-curves", JSON.stringify(pending));
        updatePendingAddBadge(groupRow);
        if (sourceRow) {{
          sourceRow.classList.add("is-pending-group-add");
          sourceRow.title = "이 피크를 " + manualPeakGroupName(
            parseInt(groupRow.getAttribute("data-first-curve"), 10)
          ) + " 그룹에 추가 예정";
        }}
        return true;
      }}

      function clearDropTargets() {{
        body.querySelectorAll(".rist-legend-group-row.is-drop-target")
          .forEach(function(row) {{ row.classList.remove("is-drop-target"); }});
      }}

      function appendCurveRow(curve, groupKey) {{
        var sampleCurve = isSampleCurve(curve);
        var peakCurve = isPeakCurve(curve);
        var row = document.createElement("div");
        row.className = "rist-legend-edit-row"
          + (sampleCurve ? " is-sample" : "")
          + (peakCurve ? " is-peak" : "")
          + (groupKey ? " is-group-member" : "");
        row.setAttribute("data-curve", String(curve));
        if (groupKey) row.setAttribute("data-group-key", groupKey);
        if (peakCurve) {{
          var sampleName = sampleNameForCurve(curve);
          if (sampleName) row.title = sampleName + " 샘플의 피크";
        }}
        row.innerHTML = "<span class='rist-legend-row-kind'>"
          + (sampleCurve ? "샘플" : (peakCurve ? "피크" : "항목"))
          + "</span>"
          + (groupKey ? "" : "<input class='rist-legend-color-input' type='color'>")
          + "<textarea class='rist-legend-edit-input' rows='1'></textarea>"
          + (peakCurve && groupKey
            ? "<button type='button' class='rist-legend-group-remove' title='그룹에서 제외'>−</button>"
            : "")
          + (peakCurve
            ? "<button type='button' class='rist-legend-peak-delete' title='피크 삭제'>×</button>"
            : "");
        var colorInput = row.querySelector(".rist-legend-color-input");
        var nameInput = row.querySelector(".rist-legend-edit-input");
        var kindBadge = row.querySelector(".rist-legend-row-kind");
        var removeButton = row.querySelector(".rist-legend-group-remove");
        var deleteButton = row.querySelector(".rist-legend-peak-delete");
        if (colorInput) colorInput.value = traceColor(curve);
        nameInput.value = legendDisplayToEdit(traceName(curve));
        if (peakCurve && kindBadge) {{
          kindBadge.draggable = true;
          kindBadge.title = "피크 그룹으로 드래그";
          kindBadge.addEventListener("dragstart", function(ev) {{
            draggedPeak = {{
              curves: peakCurvesForLegendItem(curve),
              row: row
            }};
            row.classList.add("is-dragging");
            if (ev.dataTransfer) {{
              ev.dataTransfer.effectAllowed = "move";
              ev.dataTransfer.setData("text/plain", String(curve));
            }}
          }});
          kindBadge.addEventListener("dragend", function() {{
            row.classList.remove("is-dragging");
            clearDropTargets();
            draggedPeak = null;
          }});
        }}
        if (deleteButton) {{
          deleteButton.addEventListener("click", function(ev) {{
            ev.preventDefault();
            ev.stopPropagation();
            var pending = row.getAttribute("data-delete") !== "true";
            row.setAttribute("data-delete", pending ? "true" : "false");
            row.classList.toggle("is-pending-delete", pending);
            if (pending) {{
              row.setAttribute("data-remove-group", "false");
              row.classList.remove("is-pending-group-remove");
            }}
          }});
        }}
        if (removeButton) {{
          removeButton.addEventListener("click", function(ev) {{
            ev.preventDefault();
            ev.stopPropagation();
            var pending = row.getAttribute("data-remove-group") !== "true";
            row.setAttribute("data-remove-group", pending ? "true" : "false");
            row.classList.toggle("is-pending-group-remove", pending);
            if (pending) {{
              row.setAttribute("data-delete", "false");
              row.classList.remove("is-pending-delete");
            }}
          }});
        }}
        nameInput.addEventListener("keydown", function(ev) {{
          if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {{
            ev.preventDefault();
            saveAllRows();
          }}
        }});
        body.appendChild(row);
      }}

      items.forEach(function(item) {{
        if (item.kind === "curve") {{
          appendCurveRow(item.curve, "");
          return;
        }}
        var groupKey = item.key;
        var firstCurve = item.curves[0];
        var groupRow = document.createElement("div");
        groupRow.className = "rist-legend-group-row";
        groupRow.setAttribute("data-group-key", groupKey);
        groupRow.setAttribute("data-first-curve", String(firstCurve));
        groupRow.innerHTML = "<span class='rist-legend-row-kind'>그룹</span>"
          + "<input class='rist-legend-group-title' type='text'>"
          + "<button type='button' class='rist-legend-group-add' title='선택한 피크 추가'>+</button>"
          + "<input class='rist-legend-group-color' type='color' title='그룹 색상 선택'>"
          + "<button type='button' class='rist-legend-group-clear' title='그룹 해제'>×</button>";
        var title = groupRow.querySelector(".rist-legend-group-title");
        var add = groupRow.querySelector(".rist-legend-group-add");
        var groupColor = groupRow.querySelector(".rist-legend-group-color");
        var clear = groupRow.querySelector(".rist-legend-group-clear");
        title.value = legendDisplayToEdit(manualPeakGroupName(firstCurve));
        groupColor.value = traceColor(firstCurve);
        add.addEventListener("click", function(ev) {{
          ev.preventDefault();
          ev.stopPropagation();
          var selected = selectedPeakCurvesForGroup(groupKey);
          if (!queueGroupAdd(groupRow, groupKey, selected, null)) {{
            add.title = "추가할 피크를 먼저 선택하세요";
          }}
        }});
        groupRow.addEventListener("dragover", function(ev) {{
          if (!draggedPeak) return;
          ev.preventDefault();
          if (ev.dataTransfer) ev.dataTransfer.dropEffect = "move";
          clearDropTargets();
          groupRow.classList.add("is-drop-target");
        }});
        groupRow.addEventListener("dragleave", function(ev) {{
          if (!groupRow.contains(ev.relatedTarget)) {{
            groupRow.classList.remove("is-drop-target");
          }}
        }});
        groupRow.addEventListener("drop", function(ev) {{
          if (!draggedPeak) return;
          ev.preventDefault();
          ev.stopPropagation();
          queueGroupAdd(groupRow, groupKey, draggedPeak.curves, draggedPeak.row);
          draggedPeak.row.classList.remove("is-dragging");
          clearDropTargets();
          draggedPeak = null;
        }});
        clear.addEventListener("click", function(ev) {{
          ev.preventDefault();
          ev.stopPropagation();
          var pending = groupRow.getAttribute("data-clear") !== "true";
          groupRow.setAttribute("data-clear", pending ? "true" : "false");
          groupRow.classList.toggle("is-pending-clear", pending);
        }});
        body.appendChild(groupRow);
        item.curves.forEach(function(curve) {{
          appendCurveRow(curve, groupKey);
        }});
      }});
    }}

    function saveAllRows() {{
      var deleteCurves = [];
      var groupRemovals = {{}};
      panel.querySelectorAll(".rist-legend-edit-row").forEach(function(row) {{
        var curve = parseInt(row.getAttribute("data-curve"), 10);
        var nameInput = row.querySelector(".rist-legend-edit-input");
        var colorInput = row.querySelector(".rist-legend-color-input");
        if (!Number.isFinite(curve) || !nameInput) return;
        if (row.getAttribute("data-delete") === "true") {{
          deleteCurves = deleteCurves.concat(peakCurvesForLegendItem(curve));
          return;
        }}
        if (row.getAttribute("data-remove-group") === "true") {{
          var removeGroupKey = row.getAttribute("data-group-key") || "";
          if (removeGroupKey) {{
            if (!groupRemovals[removeGroupKey]) groupRemovals[removeGroupKey] = [];
            groupRemovals[removeGroupKey] = groupRemovals[removeGroupKey]
              .concat(peakCurvesForLegendItem(curve));
          }}
          return;
        }}
        var nextName = legendEditToDisplay(nameInput.value);
        if (nextName && nextName !== traceName(curve)) updateName(curve, nextName);
        if (colorInput && normalizeColor(colorInput.value) !== traceColor(curve)) {{
          updateColor(curve, colorInput.value);
        }}
      }});
      deleteCurves = deleteCurves.filter(function(curve, index, values) {{
        return values.indexOf(curve) === index;
      }});
      panel.querySelectorAll(".rist-legend-group-row").forEach(function(row) {{
        var groupKey = row.getAttribute("data-group-key") || "";
        var titleInput = row.querySelector(".rist-legend-group-title");
        var colorInput = row.querySelector(".rist-legend-group-color");
        if (!groupKey) return;
        if (row.getAttribute("data-clear") === "true") {{
          dispatchPeakGroupClear(groupKey);
          return;
        }}
        var addCurves = [];
        try {{
          addCurves = JSON.parse(row.getAttribute("data-add-curves") || "[]");
        }} catch (e) {{}}
        var removeCurves = groupRemovals[groupKey] || [];
        addCurves = addCurves.filter(function(curve) {{
          return deleteCurves.indexOf(curve) < 0;
        }});
        removeCurves = removeCurves.filter(function(curve, index, values) {{
          return deleteCurves.indexOf(curve) < 0 && values.indexOf(curve) === index;
        }});
        var firstCurve = parseInt(row.getAttribute("data-first-curve"), 10);
        var nextTitle = titleInput ? legendEditToDisplay(titleInput.value) : "";
        var nextColor = colorInput ? colorInput.value : "";
        if (Number.isFinite(firstCurve)
            && nextTitle === manualPeakGroupName(firstCurve)
            && normalizeColor(nextColor) === traceColor(firstCurve)
            && !addCurves.length
            && !removeCurves.length) {{
          return;
        }}
        dispatchPeakGroupUpdate(
          groupKey,
          nextTitle,
          nextColor,
          addCurves,
          removeCurves
        );
      }});
      dispatchPeakDelete(deleteCurves);
      closePanel();
    }}

    btn.addEventListener("click", function(ev) {{
      ev.preventDefault();
      ev.stopPropagation();
      var willOpen = panel.style.display !== "block";
      if (willOpen) {{
        var shapePanel = gd.querySelector(".rist-shape-editor-panel");
        if (shapePanel) {{
          shapePanel.style.display = "none";
          gd.dispatchEvent(new CustomEvent("rist-shape-editor-close"));
        }}
        renderRows();
      }}
      panel.style.display = willOpen ? "block" : "none";
    }});
    panel.querySelector(".rist-legend-edit-close").addEventListener("click", function(ev) {{
      ev.preventDefault();
      closePanel();
    }});
    panel.querySelector(".rist-legend-edit-save-all").addEventListener("click", saveAllRows);
    document.addEventListener("click", function(ev) {{
      if (panel.style.display !== "block") return;
      if (panel.contains(ev.target) || btn.contains(ev.target)) return;
      closePanel();
    }});
    document.addEventListener("keydown", function(ev) {{
      if (ev.key === "Escape") closePanel();
    }});
    gd.addEventListener("rist-peak-group-change", function() {{
      if (panel.style.display === "block") renderRows();
    }});
    gd.addEventListener("rist-history-restored", function() {{
      if (panel.style.display === "block") renderRows();
    }});
  }}

  function setAllLegendVisibility(visible) {{
    if (!window.Plotly) return;
      if (gd._ristHistory) gd._ristHistory.capture();
      var n = (gd.data || []).length;
      var curves = [];
      for (var i = 0; i < n; i++) curves.push(i);
      window.Plotly.restyle(gd, {{ visible: visible }}, curves).then(function() {{
        dispatchVisibilityChange(curves, visible);
      }});
  }}

  function installLegendBulkControls() {{
    if (gd._ristLegendBulkInstalled) return;
    gd._ristLegendBulkInstalled = true;
    if (getComputedStyle(gd).position === "static") gd.style.position = "relative";
    var toolbar = gd.querySelector(".rist-plot-control-row");
    if (!toolbar) {{
      toolbar = document.createElement("div");
      toolbar.className = "rist-plot-control-row";
      gd.appendChild(toolbar);
    }}

    var bulk = document.createElement("div");
    bulk.className = "rist-legend-bulk-controls";
    bulk.innerHTML =
      "<button type='button' class='rist-legend-bulk-button' data-visible='true'>\ubaa8\ub450 \ud45c\uc2dc</button>"
      + "<button type='button' class='rist-legend-bulk-button' data-visible='legendonly'>\ubaa8\ub450 \uc228\uae40</button>";
    toolbar.appendChild(bulk);

    bulk.addEventListener("click", function(ev) {{
      var b = ev.target.closest(".rist-legend-bulk-button");
      if (!b) return;
      ev.preventDefault();
      ev.stopPropagation();
      setAllLegendVisibility(
        b.getAttribute("data-visible") === "true" ? true : "legendonly"
      );
    }});
  }}

  installPanel();
  installLegendBulkControls();

  gd.addEventListener("dblclick", function(e) {{
    if (editing) return;
    var picked = legendItemFor(e.target);
    if (!picked) return;
    e.preventDefault();
    e.stopPropagation();
    editing = true;

    var tr = picked.text.getBoundingClientRect();
    var gr = gd.getBoundingClientRect();
    var current = traceName(picked.curve) || picked.text.textContent || "";

    var inp = document.createElement("input");
    inp.type = "text";
    inp.value = current;
    inp.style.cssText = "position:absolute;z-index:40;font:12px Arial;"
      + "padding:2px 6px;border:1px solid #4a90d9;border-radius:4px;"
      + "background:#fff;color:#222;box-sizing:border-box;";
    inp.style.left = (tr.left - gr.left - 4) + "px";
    inp.style.top = (tr.top - gr.top - 3) + "px";
    inp.style.minWidth = Math.max(tr.width + 40, 120) + "px";
    if (getComputedStyle(gd).position === "static") gd.style.position = "relative";
    gd.appendChild(inp);
    inp.focus();
    inp.select();

    function finish(save) {{
      if (!editing) return;
      editing = false;
      if (save) updateName(picked.curve, inp.value);
      if (inp.parentNode) inp.parentNode.removeChild(inp);
    }}
    inp.addEventListener("keydown", function(ev) {{
      if (ev.key === "Enter") finish(true);
      else if (ev.key === "Escape") finish(false);
    }});
    setTimeout(function() {{
      inp.addEventListener("blur", function() {{ finish(true); }});
    }}, 250);
  }}, true);
}})();
</script>
"""


def peak_sensitivity_js(div_id: str, initial: str = "medium") -> str:
    """HTML에서 검출 피크의 0~100 민감도를 즉시 전환한다."""
    initial_value = {"low": 25, "medium": 50, "high": 100}.get(initial, 50)
    return f"""
<style>
#{div_id} .rist-peak-sensitivity-control {{
  order: 19;
  display: flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 7px;
  border: 1px solid #9fb3c8;
  border-radius: 4px;
  background: rgba(255,255,255,0.94);
  box-sizing: border-box;
}}
#{div_id} .rist-peak-sensitivity-slider {{
  width: 72px;
  margin: 0;
  accent-color: #52606d;
  cursor: pointer;
}}
#{div_id} .rist-peak-sensitivity-number {{
  width: 44px;
  height: 22px;
  border: 1px solid #c7d0dd;
  border-radius: 3px;
  background: #fff;
  color: #243b53;
  font: 11px Arial, sans-serif;
  text-align: right;
  padding: 2px 4px;
  box-sizing: border-box;
}}
#{div_id} .rist-peak-sensitivity-value {{
  min-width: 28px;
  color: #334e68;
  font: bold 10px Arial, sans-serif;
  text-align: right;
  white-space: nowrap;
}}
</style>
<script>
(function() {{
  var gd = document.getElementById("{div_id}");
  if (!gd || !window.Plotly || gd._ristPeakSensitivityInstalled) return;
  gd._ristPeakSensitivityInstalled = true;
  gd._ristPeakSensitivityValue = {initial_value};
  var pendingSensitivity = null;
  var applyingSensitivity = false;

  function traceMeta(curve) {{
    var tr = (gd.data || [])[curve] || {{}};
    return tr.meta && typeof tr.meta === "object" ? tr.meta : {{}};
  }}

  function minimumSensitivity(curve) {{
    var peak = traceMeta(curve).rist_peak;
    if (!peak || !Number.isFinite(Number(peak.sensitivity_min))) return null;
    return Number(peak.sensitivity_min);
  }}

  function sampleGroup(curve) {{
    var meta = traceMeta(curve);
    return String(
      meta.rist_sample_group
      || (meta.rist_peak && meta.rist_peak.sample_group)
      || ""
    );
  }}

  function sampleVisible(group) {{
    if (!group) return true;
    var data = gd.data || [];
    for (var i = 0; i < data.length; i++) {{
      var meta = traceMeta(i);
      if (!meta.rist_sample_parent
          || String(meta.rist_sample_group || "") !== group) continue;
      return data[i].visible !== false && data[i].visible !== "legendonly";
    }}
    return true;
  }}

  function currentVisibleCount() {{
    var data = gd.data || [];
    var count = 0;
    for (var i = 0; i < data.length; i++) {{
      var minimum = minimumSensitivity(i);
      if (minimum != null && minimum <= gd._ristPeakSensitivityValue) count += 1;
    }}
    return count;
  }}

  function updateStatus(count) {{
    value.textContent = count + "개";
  }}

  function applySensitivity(sensitivity) {{
    var data = gd.data || [];
    var changedCurves = [];
    var changedVisible = [];
    var changedShowlegend = [];
    var seenLegendItems = {{}};
    var visibleByCurve = {{}};
    var eligibleCount = 0;
    for (var i = 0; i < data.length; i++) {{
      var minimum = minimumSensitivity(i);
      if (minimum == null) continue;
      var eligible = minimum <= sensitivity;
      var on = eligible && sampleVisible(sampleGroup(i));
      var visibility = eligible ? (on ? true : "legendonly") : false;
      var meta = traceMeta(i);
      var editGroup = String(
        meta.rist_legend_edit_group
        || (meta.rist_peak && meta.rist_peak.label_key)
        || "curve:" + i
      );
      var nextShowlegend = eligible && !seenLegendItems[editGroup];
      if (eligible) {{
        seenLegendItems[editGroup] = true;
        eligibleCount += 1;
      }}
      visibleByCurve[i] = on;
      var currentVisibility = data[i].visible;
      if (currentVisibility !== false && currentVisibility !== "legendonly") {{
        currentVisibility = true;
      }}
      var currentShowlegend = data[i].showlegend !== false;
      if (currentVisibility !== visibility || currentShowlegend !== nextShowlegend) {{
        changedCurves.push(i);
        changedVisible.push(visibility);
        changedShowlegend.push(nextShowlegend);
      }}
    }}
    if (!Object.keys(visibleByCurve).length) return Promise.resolve();
    if (!changedCurves.length) {{
      updateStatus(eligibleCount);
      return Promise.resolve();
    }}

    var labels = (
      gd.layout.meta
      && Array.isArray(gd.layout.meta.ristPeakLabels)
    ) ? gd.layout.meta.ristPeakLabels : [];
    var annotations = (gd.layout.annotations || []).map(function(item) {{
      return Object.assign({{}}, item);
    }});
    var shapes = (gd.layout.shapes || []).map(function(item) {{
      return Object.assign({{}}, item);
    }});
    labels.forEach(function(label) {{
      if (!Object.prototype.hasOwnProperty.call(visibleByCurve, label.traceIndex)) return;
      var on = visibleByCurve[label.traceIndex];
      if (annotations[label.annotationIndex]) annotations[label.annotationIndex].visible = on;
      if (shapes[label.shapeIndex]) shapes[label.shapeIndex].visible = on;
      if (gd._ristFtirUnitOriginalAnnotations
          && gd._ristFtirUnitOriginalAnnotations[label.annotationIndex]) {{
        gd._ristFtirUnitOriginalAnnotations[label.annotationIndex].visible = on;
      }}
      if (gd._ristFtirUnitOriginalShapes
          && gd._ristFtirUnitOriginalShapes[label.shapeIndex]) {{
        gd._ristFtirUnitOriginalShapes[label.shapeIndex].visible = on;
      }}
    }});

    return window.Plotly.update(gd, {{
      visible: changedVisible,
      showlegend: changedShowlegend
    }}, {{
      annotations: annotations,
      shapes: shapes
    }}, changedCurves).then(function() {{
      updateStatus(eligibleCount);
      gd.dispatchEvent(new CustomEvent("rist-peak-sensitivity-change", {{
        detail: {{ sensitivity: sensitivity }}
      }}));
    }});
  }}

  function runPendingSensitivity() {{
    if (applyingSensitivity || pendingSensitivity == null) return;
    var sensitivity = pendingSensitivity;
    pendingSensitivity = null;
    applyingSensitivity = true;
    applySensitivity(sensitivity).catch(function(err) {{
      console.error("RIST peak sensitivity update failed", err);
    }}).then(function() {{
      applyingSensitivity = false;
      runPendingSensitivity();
    }});
  }}

  function requestSensitivity(rawValue) {{
    if (rawValue === "") return;
    var sensitivity = Math.max(0, Math.min(100, Math.round(Number(rawValue) || 0)));
    slider.value = String(sensitivity);
    numberInput.value = String(sensitivity);
    gd._ristPeakSensitivityValue = sensitivity;
    pendingSensitivity = sensitivity;
    runPendingSensitivity();
  }}

  if (getComputedStyle(gd).position === "static") gd.style.position = "relative";
  var toolbar = gd.querySelector(".rist-plot-control-row");
  if (!toolbar) {{
    toolbar = document.createElement("div");
    toolbar.className = "rist-plot-control-row";
    gd.appendChild(toolbar);
  }}
  var control = document.createElement("div");
  control.className = "rist-peak-sensitivity-control";
  control.title = "피크 검출 민감도";
  control.innerHTML =
    "<input class='rist-peak-sensitivity-slider' type='range' min='0' max='100' "
      + "step='1' value='{initial_value}' aria-label='피크 검출 민감도'>"
    + "<input class='rist-peak-sensitivity-number' type='number' min='0' max='100' "
      + "step='1' value='{initial_value}' aria-label='피크 검출 민감도 수치'>"
    + "<span class='rist-peak-sensitivity-value'></span>";
  toolbar.appendChild(control);
  var slider = control.querySelector(".rist-peak-sensitivity-slider");
  var numberInput = control.querySelector(".rist-peak-sensitivity-number");
  var value = control.querySelector(".rist-peak-sensitivity-value");
  var sliderPointerActive = false;
  var sliderKeyboardActive = false;
  var numberInputActive = false;

  function updateSensitivityInteraction() {{
    var active = sliderPointerActive || sliderKeyboardActive || numberInputActive;
    var wasActive = !!gd._ristPeakSensitivityInteracting;
    gd._ristPeakSensitivityInteracting = active;
    if (wasActive && !active) {{
      gd.dispatchEvent(new CustomEvent("rist-peak-sensitivity-interaction-end"));
    }}
  }}

  slider.addEventListener("pointerdown", function() {{
    sliderPointerActive = true;
    updateSensitivityInteraction();
  }});
  document.addEventListener("pointerup", function() {{
    if (!sliderPointerActive) return;
    sliderPointerActive = false;
    updateSensitivityInteraction();
  }});
  document.addEventListener("pointercancel", function() {{
    if (!sliderPointerActive) return;
    sliderPointerActive = false;
    updateSensitivityInteraction();
  }});
  slider.addEventListener("keydown", function() {{
    sliderKeyboardActive = true;
    updateSensitivityInteraction();
  }});
  slider.addEventListener("keyup", function() {{
    sliderKeyboardActive = false;
    updateSensitivityInteraction();
  }});
  numberInput.addEventListener("focus", function() {{
    numberInputActive = true;
    updateSensitivityInteraction();
  }});
  numberInput.addEventListener("blur", function() {{
    numberInputActive = false;
    updateSensitivityInteraction();
  }});
  slider.addEventListener("input", function() {{
    requestSensitivity(slider.value);
  }});
  numberInput.addEventListener("input", function() {{
    requestSensitivity(numberInput.value);
  }});
  gd.addEventListener("rist-plot-data-replaced", function(ev) {{
    var detail = ev.detail || {{}};
    var next = Number.isFinite(Number(detail.sensitivity))
      ? Number(detail.sensitivity)
      : gd._ristPeakSensitivityValue;
    requestSensitivity(next);
  }});
  updateStatus(currentVisibleCount());
}})();
</script>
"""


def peak_editor_js(div_id: str) -> str:
    """피크 trace/라벨/보조선을 HTML에서 추가·삭제·동기화하는 JS 스니펫.

    Figure는 ``layout.meta.ristPeakLabels``에 다음 정보를 담을 수 있다.

    - traceIndex: 피크 marker trace index
    - annotationIndex: 피크 라벨 annotation index
    - shapeIndex: 피크 보조선 shape index
    - legendgroup: 범례 편집 시 같은 그룹 라벨을 갱신할 key
    - labelKey: 샘플 그룹과 별도로 피크 라벨을 갱신할 key
    - wnText: 라벨 첫 줄에 표시할 x축 값 문자열

    피크 trace에는 ``trace.meta.rist_peak`` 값을 넣어두면 삭제 대상 피크로
    인식한다. 여러 시료를 한 그래프에 그릴 때는 raw trace와 피크 trace에
    같은 ``meta.rist_sample_group`` 값을 넣고 raw trace에는
    ``meta.rist_sample_parent=True``를 지정하면 raw 범례 숨김이 자식 피크
    범례/라벨/보조선에 함께 전파된다. sune/rin 같은 프로젝트별 분석 모듈은
    피크 검출 방식만 다르게 유지하고, HTML 편집 UX는 이 공통 스니펫을
    재사용한다.
    """
    return f"""
<style>
#{div_id} .rist-peak-edit-button {{
  order: 15;
  border: 1px solid #9fb3c8;
  border-radius: 4px;
  background: #f5f7fa;
  color: #1f2933;
  cursor: pointer;
  font: 12px Arial, sans-serif;
  padding: 5px 8px;
}}
#{div_id} .rist-peak-edit-button.is-active {{
  background: #dbeafe;
  border-color: #3b82f6;
  color: #1d4ed8;
}}
#{div_id} .rist-peak-group-name {{
  order: 16;
  width: 116px;
  min-width: 0;
  border: 1px solid #c7d0dd;
  border-radius: 4px;
  background: rgba(255,255,255,0.95);
  color: #1f2933;
  font: 12px Arial, sans-serif;
  padding: 5px 7px;
  box-sizing: border-box;
}}
#{div_id} .rist-peak-group-color {{
  order: 17;
  width: 30px;
  height: 28px;
  border: 1px solid #c7d0dd;
  border-radius: 4px;
  background: #fff;
  cursor: pointer;
  padding: 2px;
  box-sizing: border-box;
}}
#{div_id} .rist-peak-group-apply {{
  order: 18;
}}
</style>
<script>
(function() {{
  var gd = document.getElementById("{div_id}");
  if (!gd) return;
  var mode = "none";
  var selectedPeaks = [];

  function esc(s) {{
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }}

  function toolbar() {{
    if (getComputedStyle(gd).position === "static") gd.style.position = "relative";
    var row = gd.querySelector(".rist-plot-control-row");
    if (!row) {{
      row = document.createElement("div");
      row.className = "rist-plot-control-row";
      gd.appendChild(row);
    }}
    return row;
  }}

  function meta() {{
    if (!gd.layout.meta || typeof gd.layout.meta !== "object") gd.layout.meta = {{}};
    if (!Array.isArray(gd.layout.meta.ristPeakLabels)) gd.layout.meta.ristPeakLabels = [];
    return gd.layout.meta.ristPeakLabels;
  }}

  function peakName(x) {{
    var n = Number(x);
    if (!isFinite(n)) return "Peak";
    return n.toFixed(Math.abs(n) >= 100 ? 0 : 2) + " peak";
  }}

  function labelText(xText, name) {{
    return "<b>" + esc(xText) + "</b><br><span style='font-size:10px'>"
      + esc(name) + "</span>";
  }}

  function traceVisible(curve) {{
    var tr = (gd.data || [])[curve] || {{}};
    return tr.visible !== false && tr.visible !== "legendonly";
  }}

  function traceMeta(curve) {{
    var tr = (gd.data || [])[curve] || {{}};
    return tr.meta && typeof tr.meta === "object" ? tr.meta : {{}};
  }}

  function sampleGroup(curve) {{
    var meta = traceMeta(curve);
    if (meta.rist_sample_group) return String(meta.rist_sample_group);
    if (meta.rist_peak && meta.rist_peak.sample_group) {{
      return String(meta.rist_peak.sample_group);
    }}
    return "";
  }}

  function isSampleParent(curve) {{
    var meta = traceMeta(curve);
    return !!meta.rist_sample_parent;
  }}

  function labelKeyForTrace(curve) {{
    var meta = traceMeta(curve);
    if (meta.rist_legend_edit_group) return String(meta.rist_legend_edit_group);
    if (meta.rist_peak && meta.rist_peak.label_key) return String(meta.rist_peak.label_key);
    var tr = (gd.data || [])[curve] || {{}};
    if (tr.legendgroup != null && tr.legendgroup !== "") return String(tr.legendgroup);
    return "curve:" + curve;
  }}

  function isPeakCurve(curve) {{
    var meta = traceMeta(curve);
    return !!(meta && meta.rist_peak);
  }}

  function traceColor(curve) {{
    var tr = (gd.data || [])[curve] || {{}};
    var marker = tr.marker || {{}};
    var line = tr.line || {{}};
    var color = marker.color || line.color || "#ef4444";
    if (Array.isArray(color)) color = color[0] || "#ef4444";
    return String(color || "#ef4444");
  }}

  function labelForCurve(curve) {{
    return meta().find(function(label) {{ return label.traceIndex === curve; }}) || null;
  }}

  function selectedPeakCurves() {{
    var data = gd.data || [];
    var fromMeta = [];
    for (var i = 0; i < data.length; i++) {{
      var meta = traceMeta(i);
      if (meta.rist_peak && meta.rist_peak.selected) fromMeta.push(i);
    }}
    return selectedPeaks.concat(fromMeta).filter(function(curve, pos, arr) {{
      return arr.indexOf(curve) === pos && isPeakCurve(curve);
    }});
  }}

  function updateSelectButton() {{
    if (!selectBtn) return;
    var count = selectedPeakCurves().length;
    selectBtn.textContent = count ? "피크 선택 (" + count + ")" : "피크 선택";
  }}

  function flashGroupApply(message) {{
    if (!groupApplyBtn) return;
    var original = groupApplyBtn.getAttribute("data-original-text") || groupApplyBtn.textContent;
    groupApplyBtn.setAttribute("data-original-text", original);
    groupApplyBtn.textContent = message;
    groupApplyBtn.classList.add("is-active");
    clearTimeout(groupApplyBtn._ristFlashTimer);
    groupApplyBtn._ristFlashTimer = setTimeout(function() {{
      groupApplyBtn.textContent = original;
      groupApplyBtn.classList.remove("is-active");
    }}, 1200);
  }}

  function setPeakSelected(curve, selected) {{
    if (!window.Plotly || !isPeakCurve(curve)) return Promise.resolve();
    var selectedColor = "#111827";
    var tr = (gd.data || [])[curve] || {{}};
    var nextMeta = Object.assign({{}}, tr.meta || {{}});
    var peakMeta = Object.assign({{}}, nextMeta.rist_peak || {{}});
    var lineWidth = selected ? 3 : 1.5;
    var size = selected ? 12 : 9;
    peakMeta.selected = selected;
    nextMeta.rist_peak = peakMeta;
    if (gd.data && gd.data[curve]) gd.data[curve].meta = nextMeta;
    if (selected) {{
      selectedPeaks.push(curve);
    }} else {{
      selectedPeaks = selectedPeaks.filter(function(item) {{ return item !== curve; }});
    }}
    updateSelectButton();
    return window.Plotly.restyle(gd, {{
      "marker.size": size,
      "marker.line.color": selected ? selectedColor : "white",
      "marker.line.width": lineWidth,
      meta: [nextMeta]
    }}, [curve]);
  }}

  function togglePeakSelection(curve) {{
    if (!isPeakCurve(curve)) return;
    var on = selectedPeakCurves().indexOf(curve) < 0;
    setPeakSelected(curve, on);
  }}

  function clearPeakSelection() {{
    var curves = selectedPeakCurves();
    selectedPeaks = [];
    curves.forEach(function(curve) {{
      var tr = (gd.data || [])[curve] || {{}};
      var nextMeta = Object.assign({{}}, tr.meta || {{}});
      var peakMeta = Object.assign({{}}, nextMeta.rist_peak || {{}});
      delete peakMeta.selected;
      nextMeta.rist_peak = peakMeta;
      if (gd.data && gd.data[curve]) gd.data[curve].meta = nextMeta;
    }});
    updateSelectButton();
    if (!window.Plotly || !curves.length) return Promise.resolve();
    return window.Plotly.restyle(gd, {{
      "marker.size": 9,
      "marker.line.color": "white",
      "marker.line.width": 1.5,
      meta: curves.map(function(curve) {{ return (gd.data || [])[curve].meta; }})
    }}, curves);
  }}

  function nearestIdx(xs, target) {{
    var lo = 0, hi = xs.length - 1;
    if (hi < 0) return -1;
    var asc = Number(xs[hi]) >= Number(xs[0]);
    while (lo < hi) {{
      var mid = (lo + hi) >> 1;
      var v = Number(xs[mid]);
      var cond = asc ? (v < target) : (v > target);
      if (cond) lo = mid + 1; else hi = mid;
    }}
    return lo;
  }}

  function updateAnnotationList(annotations, labels, legendgroup, name) {{
    labels.forEach(function(label) {{
      var key = label.labelKey || label.legendgroup;
      if (key !== legendgroup) return;
      var ann = annotations[label.annotationIndex];
      if (!ann) return;
      ann.text = labelText(label.wnText, name);
    }});
  }}

  function updatePeakColorList(annotations, shapes, labels, curves, color) {{
    labels.forEach(function(label) {{
      if (curves.indexOf(label.traceIndex) < 0) return;
      var ann = annotations[label.annotationIndex];
      var shape = shapes[label.shapeIndex];
      if (ann) {{
        ann.arrowcolor = color;
        ann.bordercolor = color;
        ann.font = Object.assign({{}}, ann.font || {{}}, {{ color: color }});
      }}
      if (shape) {{
        shape.line = Object.assign({{}}, shape.line || {{}}, {{ color: color }});
      }}
    }});
  }}

  function applyPeakGroup() {{
    if (!window.Plotly) return;
    var curves = selectedPeakCurves();
    if (!curves.length) {{
      flashGroupApply("피크 선택 필요");
      return;
    }}
    if (gd._ristHistory) gd._ristHistory.capture();
    var groupName = (groupNameInput.value || "").trim() || "Peak Group";
    var groupColor = groupColorInput.value || "#ef4444";
    var groupKey = "manual-peak-group:" + groupName;
    var data = gd.data || [];
    var labels = meta();
    var metas = [];
    curves.forEach(function(curve) {{
      var tr = data[curve] || {{}};
      var nextMeta = Object.assign({{}}, tr.meta || {{}});
      var peakMeta = Object.assign({{}}, nextMeta.rist_peak || {{}});
      if (peakMeta.original_color == null) {{
        peakMeta.original_color = traceColor(curve);
      }}
      if (peakMeta.original_legendgroup == null) {{
        peakMeta.original_legendgroup = tr.legendgroup || "";
      }}
      if (peakMeta.original_legend_title == null) {{
        var title = tr.legendgrouptitle;
        peakMeta.original_legend_title = title && title.text != null ? String(title.text) : "";
      }}
      peakMeta.group_name = groupName;
      peakMeta.group_color = groupColor;
      peakMeta.manual_group_key = groupKey;
      nextMeta.rist_peak = peakMeta;
      nextMeta.rist_color_group = groupKey;
      data[curve].meta = nextMeta;
      metas.push(nextMeta);
    }});
    var annotations = (gd.layout.annotations || []).map(function(a) {{
      return Object.assign({{}}, a);
    }});
    var shapes = (gd.layout.shapes || []).map(function(s) {{
      return Object.assign({{}}, s);
    }});
    updatePeakColorList(annotations, shapes, labels, curves, groupColor);
    if (gd._ristFtirUnitOriginalAnnotations || gd._ristFtirUnitOriginalShapes) {{
      updatePeakColorList(
        gd._ristFtirUnitOriginalAnnotations || [],
        gd._ristFtirUnitOriginalShapes || [],
        labels,
        curves,
        groupColor
      );
    }}
    window.Plotly.restyle(gd, {{
      legendgroup: curves.map(function() {{ return groupKey; }}),
      "legendgrouptitle.text": curves.map(function() {{ return groupName; }}),
      "marker.color": curves.map(function() {{ return groupColor; }}),
      "line.color": curves.map(function() {{ return groupColor; }}),
      meta: metas
    }}, curves).then(function() {{
      return window.Plotly.relayout(gd, {{
        "meta.ristPeakLabels": labels,
        "legend.traceorder": "grouped",
        annotations: annotations,
        shapes: shapes
      }});
    }}).then(function() {{
      clearPeakSelection();
      try {{
        gd.dispatchEvent(new CustomEvent("rist-legend-color-change", {{
          detail: {{ curves: curves, color: groupColor }}
        }}));
        gd.dispatchEvent(new CustomEvent("rist-peak-group-change"));
      }} catch (e) {{}}
      setMode("none");
    }}).catch(function(err) {{
      console.error("RIST peak group apply failed", err);
      flashGroupApply("적용 실패");
    }});
  }}

  function clearPeakGroupForCurves(curves) {{
    if (!window.Plotly || !curves.length) return;
    if (gd._ristHistory) gd._ristHistory.capture();
    var data = gd.data || [];
    var labels = meta();
    var legendgroups = [];
    var legendTitles = [];
    var colors = [];
    var metas = [];
    curves.forEach(function(curve) {{
      var tr = data[curve] || {{}};
      var nextMeta = Object.assign({{}}, tr.meta || {{}});
      var peakMeta = Object.assign({{}}, nextMeta.rist_peak || {{}});
      var originalColor = peakMeta.original_color || traceColor(curve);
      legendgroups.push(peakMeta.original_legendgroup || sampleGroup(curve) || "");
      legendTitles.push(peakMeta.original_legend_title || "");
      colors.push(originalColor);
      delete peakMeta.manual_group_key;
      delete peakMeta.group_name;
      delete peakMeta.group_color;
      nextMeta.rist_peak = peakMeta;
      delete nextMeta.rist_color_group;
      data[curve].meta = nextMeta;
      metas.push(nextMeta);
    }});
    var annotations = (gd.layout.annotations || []).map(function(a) {{
      return Object.assign({{}}, a);
    }});
    var shapes = (gd.layout.shapes || []).map(function(s) {{
      return Object.assign({{}}, s);
    }});
    curves.forEach(function(curve, idx) {{
      updatePeakColorList(annotations, shapes, labels, [curve], colors[idx]);
      if (gd._ristFtirUnitOriginalAnnotations || gd._ristFtirUnitOriginalShapes) {{
        updatePeakColorList(
          gd._ristFtirUnitOriginalAnnotations || [],
          gd._ristFtirUnitOriginalShapes || [],
          labels,
          [curve],
          colors[idx]
        );
      }}
    }});
    window.Plotly.restyle(gd, {{
      legendgroup: legendgroups,
      "legendgrouptitle.text": legendTitles,
      "marker.color": colors,
      "line.color": colors,
      meta: metas
    }}, curves).then(function() {{
      return window.Plotly.relayout(gd, {{
        "meta.ristPeakLabels": labels,
        "legend.traceorder": "grouped",
        annotations: annotations,
        shapes: shapes
      }});
    }}).then(function() {{
      try {{
        gd.dispatchEvent(new CustomEvent("rist-peak-group-change"));
      }} catch (e) {{}}
    }});
  }}

  function clearPeakGroupByKey(groupKey) {{
    var data = gd.data || [];
    var curves = [];
    for (var i = 0; i < data.length; i++) {{
      var meta = traceMeta(i);
      var peakMeta = meta.rist_peak || {{}};
      var key = peakMeta.manual_group_key || meta.rist_color_group || "";
      if (key === groupKey) curves.push(i);
    }}
    clearPeakGroupForCurves(curves);
  }}

  function updatePeakGroupByKey(
    groupKey,
    groupName,
    groupColor,
    addCurves,
    removeCurves
  ) {{
    if (!window.Plotly || !groupKey) return;
    groupName = String(groupName || "").trim() || "Peak Group";
    groupColor = groupColor || "#ef4444";
    addCurves = Array.isArray(addCurves) ? addCurves : [];
    removeCurves = Array.isArray(removeCurves) ? removeCurves : [];
    var nextGroupKey = "manual-peak-group:" + groupName;
    var data = gd.data || [];
    var labels = meta();
    var existingCurves = [];
    var finalCurves = [];
    var affectedCurves = [];
    var metas = [];
    for (var i = 0; i < data.length; i++) {{
      var metaObj = traceMeta(i);
      var peakMeta = metaObj.rist_peak || {{}};
      var key = peakMeta.manual_group_key || metaObj.rist_color_group || "";
      if (key === groupKey) existingCurves.push(i);
    }}
    existingCurves.forEach(function(curve) {{
      if (removeCurves.indexOf(curve) < 0) finalCurves.push(curve);
    }});
    addCurves.forEach(function(curve) {{
      if (isPeakCurve(curve) && finalCurves.indexOf(curve) < 0) finalCurves.push(curve);
    }});
    affectedCurves = existingCurves.concat(addCurves).filter(function(curve, index, values) {{
      return isPeakCurve(curve) && values.indexOf(curve) === index;
    }});
    if (!affectedCurves.length) return;
    if (gd._ristHistory) gd._ristHistory.capture();

    var legendgroups = [];
    var legendTitles = [];
    var colors = [];
    affectedCurves.forEach(function(curve) {{
      var tr = data[curve] || {{}};
      var nextMeta = Object.assign({{}}, tr.meta || {{}});
      var peakMeta = Object.assign({{}}, nextMeta.rist_peak || {{}});
      var staysInGroup = finalCurves.indexOf(curve) >= 0;
      if (staysInGroup) {{
        if (peakMeta.original_color == null) {{
          peakMeta.original_color = traceColor(curve);
        }}
        if (peakMeta.original_legendgroup == null) {{
          peakMeta.original_legendgroup = tr.legendgroup || "";
        }}
        if (peakMeta.original_legend_title == null) {{
          var currentTitle = tr.legendgrouptitle;
          peakMeta.original_legend_title = currentTitle && currentTitle.text != null
            ? String(currentTitle.text)
            : "";
        }}
        peakMeta.group_name = groupName;
        peakMeta.group_color = groupColor;
        peakMeta.manual_group_key = nextGroupKey;
        nextMeta.rist_color_group = nextGroupKey;
        legendgroups.push(nextGroupKey);
        legendTitles.push(groupName);
        colors.push(groupColor);
      }} else {{
        var originalColor = peakMeta.original_color || traceColor(curve);
        legendgroups.push(peakMeta.original_legendgroup || sampleGroup(curve) || "");
        legendTitles.push(peakMeta.original_legend_title || "");
        colors.push(originalColor);
        delete peakMeta.manual_group_key;
        delete peakMeta.group_name;
        delete peakMeta.group_color;
        delete nextMeta.rist_color_group;
      }}
      nextMeta.rist_peak = peakMeta;
      data[curve].meta = nextMeta;
      metas.push(nextMeta);
    }});

    var annotations = (gd.layout.annotations || []).map(function(a) {{
      return Object.assign({{}}, a);
    }});
    var shapes = (gd.layout.shapes || []).map(function(s) {{
      return Object.assign({{}}, s);
    }});
    affectedCurves.forEach(function(curve, index) {{
      updatePeakColorList(annotations, shapes, labels, [curve], colors[index]);
      if (gd._ristFtirUnitOriginalAnnotations || gd._ristFtirUnitOriginalShapes) {{
        updatePeakColorList(
          gd._ristFtirUnitOriginalAnnotations || [],
          gd._ristFtirUnitOriginalShapes || [],
          labels,
          [curve],
          colors[index]
        );
      }}
    }});
    window.Plotly.restyle(gd, {{
      legendgroup: legendgroups,
      "legendgrouptitle.text": legendTitles,
      "marker.color": colors,
      "line.color": colors,
      meta: metas
    }}, affectedCurves).then(function() {{
      return window.Plotly.relayout(gd, {{
        "meta.ristPeakLabels": labels,
        "legend.traceorder": "grouped",
        annotations: annotations,
        shapes: shapes
      }});
    }}).then(function() {{
      if (addCurves.length || removeCurves.length) {{
        clearPeakSelection();
        setMode("none");
      }}
      try {{
        gd.dispatchEvent(new CustomEvent("rist-legend-color-change", {{
          detail: {{ curves: finalCurves, color: groupColor }}
        }}));
        gd.dispatchEvent(new CustomEvent("rist-peak-group-change"));
      }} catch (e) {{}}
    }}).catch(function(err) {{
      console.error("RIST peak group update failed", err);
    }});
  }}

  function nearestPeakCurveFromEvent(ev) {{
    if (ev.target && ev.target.closest
        && ev.target.closest(".legend,.modebar,.rist-plot-control-row,.rist-legend-edit-panel")) {{
      return null;
    }}
    var fl = gd._fullLayout;
    if (!fl || !fl.xaxis || !fl.yaxis) return null;
    var drag = gd.querySelector(".nsewdrag");
    if (!drag) return null;
    var r = drag.getBoundingClientRect();
    var px = ev.clientX - r.left;
    var py = ev.clientY - r.top;
    if (px < 0 || py < 0 || px > r.width || py > r.height) return null;
    var pickRadius = 32;
    var xa = fl.xaxis;
    var ya = fl.yaxis;
    function axisPixel(axis, value) {{
      if (axis.d2p) return axis.d2p(value);
      if (axis.l2p) return axis.l2p(value);
      return null;
    }}
    var data = gd.data || [];
    var best = null;
    var bestDistanceSquared = pickRadius * pickRadius;
    for (var i = 0; i < data.length; i++) {{
      var tr = data[i];
      if (!tr || !isPeakCurve(i) || !traceVisible(i)) continue;
      var xs = tr.x || [];
      var ys = tr.y || [];
      if (!xs.length || !ys.length) continue;
      var x = Number(xs[0]);
      var y = Number(ys[0]);
      if (!isFinite(x) || !isFinite(y)) continue;
      var xPixel = axisPixel(xa, x);
      var yPixel = axisPixel(ya, y);
      if (xPixel == null || yPixel == null) continue;
      var dx = xPixel - px;
      var dy = yPixel - py;
      var distanceSquared = dx * dx + dy * dy;
      if (distanceSquared <= bestDistanceSquared) {{
        bestDistanceSquared = distanceSquared;
        best = i;
      }}
    }}
    return best;
  }}

  function syncVisibility() {{
    if (!window.Plotly) return;
    var labels = meta();
    if (!labels.length) return;
    var annotations = (gd.layout.annotations || []).map(function(a) {{
      return Object.assign({{}}, a);
    }});
    var shapes = (gd.layout.shapes || []).map(function(s) {{
      return Object.assign({{}}, s);
    }});
    labels.forEach(function(label) {{
      var on = traceVisible(label.traceIndex);
      var ann = annotations[label.annotationIndex];
      var shape = shapes[label.shapeIndex];
      if (ann) ann.visible = on;
      if (shape) shape.visible = on;
      if (gd._ristFtirUnitOriginalAnnotations
          && gd._ristFtirUnitOriginalAnnotations[label.annotationIndex]) {{
        gd._ristFtirUnitOriginalAnnotations[label.annotationIndex].visible = on;
      }}
      if (gd._ristFtirUnitOriginalShapes
          && gd._ristFtirUnitOriginalShapes[label.shapeIndex]) {{
        gd._ristFtirUnitOriginalShapes[label.shapeIndex].visible = on;
      }}
    }});
    window.Plotly.relayout(gd, {{ annotations: annotations, shapes: shapes }});
  }}

  function childCurvesForSample(group, parentCurve) {{
    var data = gd.data || [];
    var curves = [];
    if (!group) return curves;
    for (var i = 0; i < data.length; i++) {{
      if (i === parentCurve) continue;
      if (sampleGroup(i) === group) curves.push(i);
    }}
    return curves;
  }}

  function peakMatchesCurrentSensitivity(curve) {{
    var peak = traceMeta(curve).rist_peak;
    if (!peak || !Number.isFinite(Number(peak.sensitivity_min))) return true;
    var sensitivity = Number(gd._ristPeakSensitivityValue);
    if (!Number.isFinite(sensitivity)) return true;
    return Number(peak.sensitivity_min) <= sensitivity;
  }}

  function syncSampleChildren(restyleEvent) {{
    if (!window.Plotly || gd._ristSyncingSampleVisibility) return;
    if (!restyleEvent || !restyleEvent.length) return;
    var update = restyleEvent[0] || {{}};
    if (!Object.prototype.hasOwnProperty.call(update, "visible")) return;
    var curves = restyleEvent[1] || [];
    if (!Array.isArray(curves)) curves = [curves];
    var visibleValues = update.visible;
    var pending = [];
    curves.forEach(function(curve, pos) {{
      if (!isSampleParent(curve)) return;
      var group = sampleGroup(curve);
      if (!group) return;
      var childCurves = childCurvesForSample(group, curve);
      if (!childCurves.length) return;
      var visible = Array.isArray(visibleValues) ? visibleValues[pos] : visibleValues;
      var childVisibility = childCurves.map(function(childCurve) {{
        return peakMatchesCurrentSensitivity(childCurve) ? visible : false;
      }});
      pending.push({{ curves: childCurves, visible: childVisibility }});
    }});
    if (!pending.length) return;
    gd._ristSyncingSampleVisibility = true;
    Promise.all(pending.map(function(item) {{
      return window.Plotly.restyle(gd, {{ visible: item.visible }}, item.curves)
        .then(function() {{
          try {{
            gd.dispatchEvent(new CustomEvent("rist-legend-visibility-change", {{
              detail: {{ curves: item.curves, visible: item.visible }}
            }}));
          }} catch (e) {{}}
        }});
    }})).then(function() {{
      gd._ristSyncingSampleVisibility = false;
      syncVisibility();
    }}).catch(function() {{
      gd._ristSyncingSampleVisibility = false;
    }});
  }}

  function dataPointFromEvent(ev) {{
    var fl = gd._fullLayout;
    if (!fl || !fl.xaxis || !fl.yaxis) return null;
    var drag = gd.querySelector(".nsewdrag");
    if (!drag) return null;
    var xa = fl.xaxis;
    var r = drag.getBoundingClientRect();
    var px = ev.clientX - r.left;
    var py = ev.clientY - r.top;
    if (px < 0 || py < 0 || px > r.width || py > r.height) return null;
    var data = gd._fullData || gd.data || [];
    var best = null;
    var bestD = Infinity;
    var curX = xa.p2d(px);
    var curY = fl.yaxis.p2d(py);
    var targetTrace = -1;
    var targetDY = Infinity;
    for (var t = 0; t < data.length; t++) {{
      var tr = data[t];
      if (!tr || tr.visible === false || tr.visible === "legendonly") continue;
      if (tr.meta && tr.meta.rist_peak) continue;
      var xs = tr.x, ys = tr.y;
      if (!xs || !ys || xs.length < 3) continue;
      var near = nearestIdx(xs, curX);
      if (near < 0) continue;
      var yAtX = Number(ys[near]);
      if (!isFinite(yAtX)) continue;
      var dy = Math.abs(yAtX - curY);
      if (dy < targetDY) {{
        targetDY = dy;
        targetTrace = t;
      }}
    }}
    for (var t2 = 0; t2 < data.length; t2++) {{
      if (targetTrace >= 0 && t2 !== targetTrace) continue;
      var tr2 = data[t2];
      if (!tr2 || tr2.visible === false || tr2.visible === "legendonly") continue;
      if (tr2.meta && tr2.meta.rist_peak) continue;
      var xs = tr2.x, ys = tr2.y;
      if (!xs || !ys || xs.length < 3) continue;
      for (var k = 1; k < xs.length - 1; k++) {{
        var x = Number(xs[k]), y = Number(ys[k]);
        var prev = Number(ys[k - 1]), next = Number(ys[k + 1]);
        if (!isFinite(x) || !isFinite(y) || !isFinite(prev) || !isFinite(next)) continue;
        if (y < prev || y < next) continue;
        var d = Math.abs(x - curX);
        if (d < bestD) {{
          bestD = d;
          best = {{ x: xs[k], y: ys[k], curve: t2, localMaximum: true, yNearestTrace: true }};
        }}
      }}
    }}
    if (!best && gd._snapPoint
        && isFinite(Number(gd._snapPoint.x))
        && isFinite(Number(gd._snapPoint.y))) {{
      return {{ x: gd._snapPoint.x, y: gd._snapPoint.y, snapped: true }};
    }}
    return best;
  }}

  function addPeakAt(pt) {{
    if (!window.Plotly || !pt) return;
    if (gd._ristHistory) gd._ristHistory.capture();
    var xText = isFinite(Number(pt.x)) ? Number(pt.x).toFixed(0) : String(pt.x);
    var name = peakName(pt.x);
    var group = "user-peak-" + Date.now() + "-" + Math.floor(Math.random() * 10000);
    var parentSampleGroup = pt.curve == null ? "" : sampleGroup(pt.curve);
    var legendGroup = parentSampleGroup || group;
    var traceIndex = (gd.data || []).length;
    var annotationIndex = (gd.layout.annotations || []).length;
    var shapeIndex = (gd.layout.shapes || []).length;
    var color = "#ef4444";
    var tr = {{
      type: "scatter",
      mode: "markers",
      x: [pt.x],
      y: [pt.y],
      name: name,
      legendgroup: legendGroup,
      showlegend: true,
      marker: {{
        color: color,
        size: 9,
        symbol: "circle",
        line: {{ color: "white", width: 1.5 }}
      }},
      meta: {{
        rist_legend_edit_group: group,
        rist_peak: {{
          user: true,
          sample_group: parentSampleGroup,
          label_key: group
        }}
      }},
      hovertemplate: "<b>%{{x:.2f}}</b><br>%{{y:.4f}}<extra></extra>"
    }};
    var ann = {{
      x: pt.x,
      y: pt.y,
      text: labelText(xText, name),
      showarrow: true,
      captureevents: true,
      arrowhead: 0,
      arrowcolor: color,
      arrowwidth: 1,
      ax: 0,
      ay: -28,
      font: {{ size: 9, color: color }},
      bgcolor: "rgba(255,255,255,0.88)",
      bordercolor: color,
      borderwidth: 1,
      borderpad: 2,
      name: "rist_user_peak_label_" + annotationIndex
    }};
    var shape = {{
      type: "line",
      x0: pt.x,
      x1: pt.x,
      y0: 0,
      y1: pt.y,
      line: {{ color: color, width: 0.8, dash: "dot" }}
    }};
    window.Plotly.addTraces(gd, tr).then(function() {{
      var labels = meta();
      labels.push({{
        annotationIndex: annotationIndex,
        shapeIndex: shapeIndex,
        traceIndex: traceIndex,
        legendgroup: legendGroup,
        labelKey: group,
        wnText: xText
      }});
      var annotations = (gd.layout.annotations || []).slice();
      var shapes = (gd.layout.shapes || []).slice();
      annotations.push(ann);
      shapes.push(shape);
      if (gd._ristFtirUnitOriginalAnnotations) {{
        gd._ristFtirUnitOriginalAnnotations = annotations.map(function(a) {{
          return Object.assign({{}}, a);
        }});
      }}
      if (gd._ristFtirUnitOriginalShapes) {{
        gd._ristFtirUnitOriginalShapes = shapes.map(function(s) {{
          return Object.assign({{}}, s);
        }});
      }}
      return window.Plotly.relayout(gd, {{
        "meta.ristPeakLabels": labels,
        annotations: annotations,
        shapes: shapes
      }});
    }});
  }}

  function deletePeakTrace(curve, skipHistory) {{
    if (!window.Plotly || curve == null || curve < 0) return Promise.resolve();
    var labels = meta();
    var label = labelForCurve(curve);
    var tr = (gd.data || [])[curve] || {{}};
    var isPeak = label || (tr.meta && tr.meta.rist_peak);
    if (!isPeak) return Promise.resolve();
    if (!skipHistory && gd._ristHistory) gd._ristHistory.capture();
    var annotations = (gd.layout.annotations || []).slice();
    var shapes = (gd.layout.shapes || []).slice();
    if (label) {{
      annotations.splice(label.annotationIndex, 1);
      shapes.splice(label.shapeIndex, 1);
      if (gd._ristFtirUnitOriginalAnnotations) {{
        gd._ristFtirUnitOriginalAnnotations.splice(label.annotationIndex, 1);
      }}
      if (gd._ristFtirUnitOriginalShapes) {{
        gd._ristFtirUnitOriginalShapes.splice(label.shapeIndex, 1);
      }}
      labels = labels.filter(function(item) {{ return item.traceIndex !== curve; }});
    }}
    selectedPeaks = selectedPeaks.filter(function(item) {{ return item !== curve; }});
    labels.forEach(function(item) {{
      if (label && item.annotationIndex > label.annotationIndex) item.annotationIndex -= 1;
      if (label && item.shapeIndex > label.shapeIndex) item.shapeIndex -= 1;
      if (item.traceIndex > curve) item.traceIndex -= 1;
    }});
    return window.Plotly.deleteTraces(gd, [curve]).then(function() {{
      selectedPeaks = selectedPeaks.map(function(item) {{
        return item > curve ? item - 1 : item;
      }});
      updateSelectButton();
      return window.Plotly.relayout(gd, {{
        "meta.ristPeakLabels": labels,
        annotations: annotations,
        shapes: shapes
      }});
    }});
  }}

  function setMode(next) {{
    var prev = mode;
    mode = mode === next ? "none" : next;
    if (prev === "select" && mode !== "select") {{
      clearPeakSelection();
    }}
    addBtn.classList.toggle("is-active", mode === "add");
    delBtn.classList.toggle("is-active", mode === "delete");
    selectBtn.classList.toggle("is-active", mode === "select");
  }}

  var addBtn = document.createElement("button");
  addBtn.type = "button";
  addBtn.className = "rist-peak-edit-button";
  addBtn.textContent = "피크 추가";
  addBtn.addEventListener("click", function(ev) {{
    ev.preventDefault();
    ev.stopPropagation();
    setMode("add");
  }});

  var delBtn = document.createElement("button");
  delBtn.type = "button";
  delBtn.className = "rist-peak-edit-button";
  delBtn.textContent = "피크 삭제";
  delBtn.addEventListener("click", function(ev) {{
    ev.preventDefault();
    ev.stopPropagation();
    setMode("delete");
  }});

  var selectBtn = document.createElement("button");
  selectBtn.type = "button";
  selectBtn.className = "rist-peak-edit-button";
  selectBtn.textContent = "피크 선택";
  selectBtn.addEventListener("click", function(ev) {{
    ev.preventDefault();
    ev.stopPropagation();
    setMode("select");
  }});

  var groupNameInput = document.createElement("input");
  groupNameInput.type = "text";
  groupNameInput.className = "rist-peak-group-name";
  groupNameInput.placeholder = "그룹명";

  var groupColorInput = document.createElement("input");
  groupColorInput.type = "color";
  groupColorInput.className = "rist-peak-group-color";
  groupColorInput.value = "#ef4444";

  var groupApplyBtn = document.createElement("button");
  groupApplyBtn.type = "button";
  groupApplyBtn.className = "rist-peak-edit-button rist-peak-group-apply";
  groupApplyBtn.textContent = "그룹 적용";
  groupApplyBtn.addEventListener("click", function(ev) {{
    ev.preventDefault();
    ev.stopPropagation();
    applyPeakGroup();
  }});

  var row = toolbar();
  row.appendChild(addBtn);
  row.appendChild(delBtn);
  row.appendChild(selectBtn);
  row.appendChild(groupNameInput);
  row.appendChild(groupColorInput);
  row.appendChild(groupApplyBtn);

  gd.addEventListener("click", function(ev) {{
    if (mode !== "add") return;
    if (ev.target.closest(".rist-plot-control-row")) return;
    var pt = dataPointFromEvent(ev);
    if (!pt) return;
    ev.preventDefault();
    ev.stopPropagation();
    addPeakAt(pt);
  }}, true);

  function handlePeakSelectPointer(ev) {{
    if (mode !== "select") return;
    if (ev.target.closest(".legend,.modebar,.rist-plot-control-row,.rist-legend-edit-panel")) return;
    if (ev.type === "click" && gd._ristHandledPeakSelectClick) {{
      ev.preventDefault();
      ev.stopPropagation();
      return;
    }}
    var curve = nearestPeakCurveFromEvent(ev);
    if (curve == null) return;
    ev.preventDefault();
    ev.stopPropagation();
    gd._ristHandledPeakSelectClick = true;
    gd._ristHandledPeakSelectAt = Date.now();
    togglePeakSelection(curve);
    setTimeout(function() {{
      gd._ristHandledPeakSelectClick = false;
    }}, 250);
  }}

  gd.addEventListener("mousedown", handlePeakSelectPointer, true);
  gd.addEventListener("click", handlePeakSelectPointer, true);

  gd.on("plotly_click", function(ev) {{
    if (mode !== "delete" && mode !== "select") return;
    if (mode === "select" && (
        gd._ristHandledPeakSelectClick
        || (gd._ristHandledPeakSelectAt && Date.now() - gd._ristHandledPeakSelectAt < 250)
    )) return;
    var point = ev && ev.points && ev.points[0];
    if (!point) return;
    if (mode === "delete") deletePeakTrace(point.curveNumber);
    else {{
      var curve = isPeakCurve(point.curveNumber) ? point.curveNumber : null;
      if (curve == null && ev.event) curve = nearestPeakCurveFromEvent(ev.event);
      if (curve != null) togglePeakSelection(curve);
    }}
  }});

  gd.addEventListener("rist-peak-delete", function(ev) {{
    var detail = ev.detail || {{}};
    var curves = Array.isArray(detail.curves) ? detail.curves.slice() : [];
    curves = curves
      .filter(function(curve, index, values) {{
        return Number.isFinite(curve) && values.indexOf(curve) === index;
      }})
      .sort(function(a, b) {{ return b - a; }});
    if (curves.length && gd._ristHistory) gd._ristHistory.capture();
    curves.reduce(function(promise, curve) {{
      return promise.then(function() {{ return deletePeakTrace(curve, true); }});
    }}, Promise.resolve());
  }});

  gd.addEventListener("rist-legend-name-change", function(ev) {{
    if (!window.Plotly) return;
    var detail = ev.detail || {{}};
    var labels = meta();
    if (!labels.length) return;
    var annotations = (gd.layout.annotations || []).map(function(a) {{
      return Object.assign({{}}, a);
    }});
    updateAnnotationList(
      annotations,
      labels,
      detail.editGroup || detail.legendgroup,
      detail.name
    );
    if (gd._ristFtirUnitOriginalAnnotations) {{
      updateAnnotationList(
        gd._ristFtirUnitOriginalAnnotations,
        labels,
        detail.editGroup || detail.legendgroup,
        detail.name
      );
    }}
    window.Plotly.relayout(gd, {{ annotations: annotations }});
  }});
  gd.addEventListener("rist-legend-color-change", function(ev) {{
    if (!window.Plotly) return;
    var detail = ev.detail || {{}};
    var labels = meta();
    if (!labels.length || !detail.curves || !detail.color) return;
    var annotations = (gd.layout.annotations || []).map(function(a) {{
      return Object.assign({{}}, a);
    }});
    var shapes = (gd.layout.shapes || []).map(function(s) {{
      return Object.assign({{}}, s);
    }});
    updatePeakColorList(annotations, shapes, labels, detail.curves, detail.color);
    if (gd._ristFtirUnitOriginalAnnotations || gd._ristFtirUnitOriginalShapes) {{
      updatePeakColorList(
        gd._ristFtirUnitOriginalAnnotations || [],
        gd._ristFtirUnitOriginalShapes || [],
        labels,
        detail.curves,
        detail.color
      );
    }}
    window.Plotly.relayout(gd, {{ annotations: annotations, shapes: shapes }});
  }});
  gd.addEventListener("rist-peak-group-clear", function(ev) {{
    var detail = ev.detail || {{}};
    if (!detail.groupKey) return;
    clearPeakGroupByKey(String(detail.groupKey));
  }});
  gd.addEventListener("rist-peak-group-update", function(ev) {{
    var detail = ev.detail || {{}};
    if (!detail.groupKey) return;
    updatePeakGroupByKey(
      String(detail.groupKey),
      detail.name,
      detail.color,
      detail.addCurves,
      detail.removeCurves
    );
  }});
  gd.addEventListener("rist-history-restored", function() {{
    selectedPeaks = [];
    updateSelectButton();
    syncVisibility();
  }});
  gd.addEventListener("rist-plot-data-replaced", function() {{
    selectedPeaks = [];
    setMode("none");
    updateSelectButton();
    syncVisibility();
  }});
  gd.on("plotly_restyle", function(ev) {{
    setTimeout(function() {{
      syncSampleChildren(ev);
      syncVisibility();
    }}, 0);
  }});
  gd.addEventListener("rist-legend-visibility-change", function() {{
    setTimeout(syncVisibility, 0);
  }});
}})();
</script>
"""


def _image_download_js(div_id: str, formats, filename: str, scale: float) -> str:
    """다운로드(카메라) 버튼을 누르면 저장 형식 선택 팝업을 띄우는 JS 스니펫.

    - Plotly 모드바의 'Download plot' 버튼 클릭을 가로채 형식 선택 팝업을 표시한다.
    - 형식(svg/png/jpeg/webp)을 고르면 Plotly.downloadImage 로 저장하고 팝업은 사라진다.
    - 팝업 바깥을 클릭하거나 Esc 를 누르면 저장 없이 닫힌다.
    """
    opts = "".join(
        f"<button data-fmt='{f}' style='display:block;width:100%;text-align:left;"
        f"border:0;background:none;cursor:pointer;font:12px Arial;color:#333;"
        f"padding:5px 12px;'>{f.upper()}</button>"
        for f in formats
    )
    return f"""
<script>
(function() {{
  var gd = document.getElementById("{div_id}");
  if (!gd) return;

  var pop = document.createElement("div");
  pop.style.cssText = "position:fixed;z-index:1000;display:none;"
    + "background:#fff;border:1px solid #ccc;border-radius:6px;"
    + "box-shadow:0 2px 10px rgba(0,0,0,0.18);padding:4px 0;min-width:90px;";
  pop.innerHTML = "<div style='font:11px Arial;color:#888;padding:3px 12px 5px;'>"
    + "\uc800\uc7a5 \ud615\uc2dd</div>{opts}";
  document.body.appendChild(pop);

  function hide() {{ pop.style.display = "none"; }}
  function showAt(x, y) {{
    pop.style.display = "block";
    var w = pop.offsetWidth, h = pop.offsetHeight;
    var vw = window.innerWidth, vh = window.innerHeight;
    pop.style.left = Math.max(6, Math.min(x, vw - w - 6)) + "px";
    pop.style.top  = Math.max(6, Math.min(y, vh - h - 6)) + "px";
  }}

  pop.addEventListener("click", function(ev) {{
    var b = ev.target.closest("button[data-fmt]");
    if (!b) return;
    ev.stopPropagation();
    hide();
    if (!window.Plotly) return;
    var fl = gd._fullLayout || {{}};
    window.Plotly.downloadImage(gd, {{
      format: b.getAttribute("data-fmt"),
      filename: "{filename}",
      scale: {scale},
      width: fl.width || gd.clientWidth,
      height: fl.height || gd.clientHeight
    }});
  }});

  // 모드바의 'Download plot' 버튼 클릭을 가로채 팝업 표시
  document.addEventListener("click", function(ev) {{
    var btn = ev.target.closest("a.modebar-btn");
    if (btn && /Download/i.test(btn.getAttribute("data-title") || "")
        && gd.contains(btn)) {{
      ev.preventDefault();
      ev.stopPropagation();
      var r = btn.getBoundingClientRect();
      showAt(r.left, r.bottom + 4);
      return;
    }}
    if (!pop.contains(ev.target)) hide();
  }}, true);

  document.addEventListener("keydown", function(ev) {{
    if (ev.key === "Escape") hide();
  }});
}})();
</script>
"""


def _trace_highlight_js(div_id: str, pickable=None, groups=None) -> str:
    """크로스헤어 스냅 지점을 보조키+더블클릭해 그래프를 강조·격리하는 JS 스니펫.

    - 일반 더블클릭은 Plotly 기본 축 리셋 동작에 맡긴다.
    - 스냅 좌표가 표시된 상태(그래프 위의 점이 선택된 상태)에서
      Shift/Alt + 더블클릭하면
      그 트레이스를 강조(굵게)하고 나머지는 반투명으로 만든다.
    - 같은 트레이스를 다시 Shift/Alt + 더블클릭하면 그 트레이스만 남기고 나머지를 숨긴다(격리).
      이때 범례에서도 그 트레이스만 선택(나머지는 legendonly)된다.
    - 격리 상태에서 빈 영역을 더블클릭하면 격리를 유지한 채 축만 리셋(reset axes)한다.
    - 격리된 트레이스를 다시 더블클릭하면 모든 트레이스를 원래대로 복원한다.
    - 강조(반투명) 상태에서 빈 영역을 더블클릭하면 강조를 해제(전체 복원)한다.
    - 어떤 트레이스를 강조할지는 plotly 클릭이 아니라 크로스헤어 스냅
      인덱스(gd._snapCurve)로 결정하므로 겹친 그래프에서도 정확히 선택된다.

    pickable: 더블클릭으로 선택할 수 있는 트레이스 인덱스 목록(예: raw 라인만).
              None/빈 목록이면 모든 트레이스가 선택 대상.
    groups:   {대표 트레이스 인덱스: [함께 강조/격리할 트레이스 인덱스, ...]}.
              대표(raw 라인)를 강조하면 그 그룹(raw + 소속 피크) 전체가 함께 살아난다.
    상태가 바뀔 때마다 gd._hiState = {mode, curve, members} 를 갱신하고
    gd 에 "trace-highlight" 커스텀 이벤트를 발생시킨다(표 등 외부 연동용).
    """
    pickable_js = json.dumps(list(pickable) if pickable else [])
    groups_js = json.dumps(
        {str(k): list(v) for k, v in (groups or {}).items()}
    )
    return f"""
<script>
(function() {{
  var gd = document.getElementById("{div_id}");
  if (!gd) return;
  var DIM = 0.15, BOLD = 2.2;
  var PICKABLE = {pickable_js};   // 선택 가능 트레이스(비면 전체)
  var GROUPS = {groups_js};       // 대표→그룹 멤버
  var hiCurve = -1;   // 현재 강조/격리 중인 트레이스
  var mode = "none"; // "none" | "highlight"(반투명 강조) | "isolate"(격리)

  function idxArr() {{
    var n = (gd.data || []).length, a = [];
    for (var i = 0; i < n; i++) a.push(i);
    return a;
  }}
  function membersOf(cn) {{
    var g = GROUPS[String(cn)];
    return (g && g.length) ? g : [cn];
  }}
  function inList(arr, i) {{ return arr.indexOf(i) >= 0; }}
  function emitState(members) {{
    gd._hiState = {{ mode: mode, curve: hiCurve, members: members || null }};
    try {{ gd.dispatchEvent(new CustomEvent("trace-highlight",
      {{ detail: gd._hiState }})); }} catch (e) {{}}
  }}
  function ensureOrig() {{
    if (gd._origLW) return;
    var fd = gd._fullData || gd.data || [];
    gd._origLW = fd.map(function(t) {{
      return (t.line && t.line.width != null) ? t.line.width : 2;
    }});
  }}
  function highlight(cn) {{
    ensureOrig();
    var mem = membersOf(cn);
    var n = (gd.data || []).length, lw = [], op = [];
    for (var i = 0; i < n; i++) {{
      var on = inList(mem, i);
      lw.push(i === cn ? gd._origLW[i] * BOLD : gd._origLW[i]);
      op.push(on ? 1 : DIM);
    }}
    window.Plotly.restyle(gd, {{opacity: op, "line.width": lw}}, idxArr());
    hiCurve = cn;
    mode = "highlight";
    emitState(mem);
  }}
  function isolate(cn) {{
    ensureOrig();
    var mem = membersOf(cn);
    var n = (gd.data || []).length, vis = [], lw = [], op = [];
    for (var i = 0; i < n; i++) {{
      vis.push(inList(mem, i) ? true : "legendonly");
      lw.push(gd._origLW[i]);
      op.push(1);
    }}
    window.Plotly.restyle(gd, {{visible: vis, opacity: op, "line.width": lw}}, idxArr());
    hiCurve = cn;
    mode = "isolate";
    emitState(mem);
  }}
  function resetAll() {{
    ensureOrig();
    var n = (gd.data || []).length, vis = [], lw = [], op = [];
    for (var i = 0; i < n; i++) {{ vis.push(true); lw.push(gd._origLW[i]); op.push(1); }}
    window.Plotly.restyle(gd, {{visible: vis, opacity: op, "line.width": lw}}, idxArr());
    hiCurve = -1;
    mode = "none";
    emitState(null);
  }}

  gd.addEventListener("mousemove", function(e) {{
    gd._lastPtr = {{ x: e.clientX, y: e.clientY, modifier: e.shiftKey || e.altKey }};
  }}, true);

  function pickCurve() {{
    var p = gd._lastPtr;
    var cn = -1;
    if (p && typeof gd._nearestCurveAt === "function") {{
      cn = gd._nearestCurveAt(p.x, p.y, 30);
    }}
    if (cn < 0 && typeof gd._snapCurve === "number") cn = gd._snapCurve;
    // 선택 가능 목록이 지정되면 그 트레이스(raw 라인)만 허용한다.
    if (PICKABLE.length && cn >= 0 && PICKABLE.indexOf(cn) < 0) cn = -1;
    return cn;
  }}

  // 일반 더블클릭은 Plotly 기본 축 리셋을 유지한다.
  // Shift/Alt + 더블클릭만 트레이스 강조/격리로 사용한다.
  //
  // 실제 마우스 더블클릭은 Plotly가 가로채 네이티브 dblclick이 안 뜨므로
  // Plotly 자체 이벤트(plotly_doubleclick)를 사용한다.
  gd.on("plotly_doubleclick", function() {{
    if (!window.Plotly) return false;
    var modifier = gd._lastPtr && gd._lastPtr.modifier;
    if (!modifier) return true;
    var cn = pickCurve();
    if (cn < 0) {{                 // 그래프 선 근처가 아닌 빈 영역
      if (mode === "highlight") {{ resetAll(); return false; }}  // 강조 해제
      // 격리(isolate) 또는 일반 상태: 격리를 유지한 채 축만 리셋(reset axes) 허용
      return true;
    }}
    if (cn === hiCurve) {{
      if (mode === "highlight") isolate(cn);  // 강조 → 격리
      else if (mode === "isolate") resetAll(); // 격리 → 전체 복원
      else highlight(cn);
    }} else {{
      highlight(cn);                          // 다른 트레이스 강조
    }}
    return false;                             // 기본 자동 맞춤(줌 리셋) 취소
  }});
}})();
</script>
"""


def shape_editor_js(div_id: str) -> str:
    """그래프 위에 사각형과 텍스트 박스를 추가·편집한다."""
    return f"""
<style>
#{div_id} .rist-shape-tool-button {{
  order: 22;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid #9fb3c8;
  border-radius: 4px;
  background: #f5f7fa;
  color: #243b53;
  cursor: pointer;
  padding: 0;
}}
#{div_id} .rist-shape-tool-button svg {{
  width: 16px;
  height: 16px;
  stroke-width: 2;
}}
#{div_id} .rist-shape-tool-button.is-active {{
  border-color: #3b82f6;
  background: #dbeafe;
  color: #1d4ed8;
}}
#{div_id} .rist-shape-editor-panel {{
  position: absolute;
  top: 94px;
  right: 30px;
  z-index: 23;
  display: none;
  width: min(310px, calc(100% - 16px));
  border: 1px solid #c7d0dd;
  border-radius: 6px;
  background: rgba(255,255,255,0.98);
  box-shadow: 0 4px 18px rgba(0,0,0,0.16);
  padding: 10px;
  box-sizing: border-box;
}}
#{div_id} .rist-shape-kind-tabs {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 5px;
  margin-bottom: 8px;
}}
#{div_id} .rist-shape-kind-button {{
  height: 28px;
  border: 1px solid #c7d0dd;
  border-radius: 4px;
  background: #f5f7fa;
  color: #334e68;
  cursor: pointer;
  font: 11px Arial, sans-serif;
}}
#{div_id} .rist-shape-kind-button.is-active {{
  border-color: #3b82f6;
  background: #dbeafe;
  color: #1d4ed8;
}}
#{div_id} .rist-shape-editor-head {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 9px;
  color: #1f2933;
  font: bold 13px Arial, sans-serif;
}}
#{div_id} .rist-shape-editor-close {{
  border: 0;
  background: transparent;
  color: #52606d;
  cursor: pointer;
  font: 18px/1 Arial, sans-serif;
  padding: 0 3px;
}}
#{div_id} .rist-shape-editor-text {{
  width: 100%;
  height: 30px;
  border: 1px solid #c7d0dd;
  border-radius: 4px;
  color: #1f2933;
  font: 12px Arial, sans-serif;
  padding: 5px 7px;
  box-sizing: border-box;
}}
#{div_id} .rist-shape-text-options {{
  display: none;
}}
#{div_id} .rist-shape-font-row {{
  display: grid;
  grid-template-columns: auto 1fr auto 64px;
  gap: 6px;
  align-items: center;
  margin-top: 8px;
  color: #52606d;
  font: 10px Arial, sans-serif;
}}
#{div_id} .rist-shape-font-size {{
  width: 100%;
  height: 28px;
  border: 1px solid #c7d0dd;
  border-radius: 4px;
  color: #1f2933;
  font: 11px Arial, sans-serif;
  padding: 3px 5px;
  box-sizing: border-box;
}}
#{div_id} .rist-shape-editor-colors {{
  display: grid;
  grid-template-columns: auto 1fr auto 1fr;
  gap: 6px;
  align-items: center;
  margin-top: 8px;
}}
#{div_id} .rist-shape-border-row {{
  display: flex;
  align-items: center;
  gap: 7px;
  margin-top: 8px;
  color: #52606d;
  font: 10px Arial, sans-serif;
}}
#{div_id} .rist-shape-border-style {{
  flex: 1 1 auto;
  height: 28px;
  border: 1px solid #c7d0dd;
  border-radius: 4px;
  background: #fff;
  color: #1f2933;
  font: 11px Arial, sans-serif;
  padding: 3px 5px;
}}
#{div_id} .rist-shape-editor-colors span {{
  color: #52606d;
  font: 10px Arial, sans-serif;
}}
#{div_id} .rist-shape-color {{
  width: 100%;
  height: 28px;
  border: 1px solid #c7d0dd;
  border-radius: 4px;
  background: #fff;
  cursor: pointer;
  padding: 2px;
  box-sizing: border-box;
}}
#{div_id} .rist-shape-opacity-row {{
  display: flex;
  align-items: center;
  gap: 7px;
  margin-top: 8px;
  color: #52606d;
  font: 10px Arial, sans-serif;
}}
#{div_id} .rist-shape-opacity {{
  flex: 1 1 auto;
  min-width: 0;
  accent-color: #52606d;
}}
#{div_id} .rist-shape-fill-none-row {{
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-top: 7px;
  color: #52606d;
  cursor: pointer;
  font: 10px Arial, sans-serif;
}}
#{div_id} .rist-shape-fill-none {{
  accent-color: #52606d;
}}
#{div_id} .rist-shape-fill-color:disabled,
#{div_id} .rist-shape-opacity:disabled {{
  cursor: default;
  opacity: 0.45;
}}
#{div_id} .rist-shape-editor-actions {{
  display: flex;
  gap: 7px;
  justify-content: flex-end;
  margin-top: 10px;
}}
#{div_id} .rist-shape-action {{
  height: 28px;
  border: 1px solid #9fb3c8;
  border-radius: 4px;
  background: #f5f7fa;
  color: #1f2933;
  cursor: pointer;
  font: 11px Arial, sans-serif;
  padding: 4px 8px;
}}
#{div_id} .rist-shape-delete {{
  border-color: #d5a3a3;
  color: #9b2c2c;
}}
#{div_id} .rist-shape-delete:disabled {{
  opacity: 0.4;
  cursor: default;
}}
#{div_id} .rist-shape-draw-preview {{
  position: absolute;
  z-index: 18;
  display: none;
  border: 2px dashed #2563eb;
  background: rgba(37,99,235,0.12);
  pointer-events: none;
  box-sizing: border-box;
}}
#{div_id} .rist-shape-selection {{
  position: absolute;
  z-index: 20;
  display: none;
  border: 1px dashed #2563eb;
  cursor: move;
  box-sizing: border-box;
}}
#{div_id} .rist-shape-resize-handle {{
  position: absolute;
  width: 9px;
  height: 9px;
  border: 1px solid #1d4ed8;
  background: #fff;
  box-sizing: border-box;
}}
#{div_id} .rist-shape-resize-handle[data-dir*="n"] {{ top: -5px; }}
#{div_id} .rist-shape-resize-handle[data-dir*="s"] {{ bottom: -5px; }}
#{div_id} .rist-shape-resize-handle[data-dir*="w"] {{ left: -5px; }}
#{div_id} .rist-shape-resize-handle[data-dir*="e"] {{ right: -5px; }}
#{div_id} .rist-shape-resize-handle[data-dir="n"],
#{div_id} .rist-shape-resize-handle[data-dir="s"] {{
  left: calc(50% - 4px);
  cursor: ns-resize;
}}
#{div_id} .rist-shape-resize-handle[data-dir="e"],
#{div_id} .rist-shape-resize-handle[data-dir="w"] {{
  top: calc(50% - 4px);
  cursor: ew-resize;
}}
#{div_id} .rist-shape-resize-handle[data-dir="nw"],
#{div_id} .rist-shape-resize-handle[data-dir="se"] {{ cursor: nwse-resize; }}
#{div_id} .rist-shape-resize-handle[data-dir="ne"],
#{div_id} .rist-shape-resize-handle[data-dir="sw"] {{ cursor: nesw-resize; }}
</style>
<script>
(function() {{
  var gd = document.getElementById("{div_id}");
  if (!gd || !window.Plotly || gd._ristShapeEditorInstalled) return;
  gd._ristShapeEditorInstalled = true;
  if (getComputedStyle(gd).position === "static") gd.style.position = "relative";
  var selectedId = "";
  var selectedKind = "";
  var drawKind = "rect";
  var drawMode = false;
  var drawStart = null;
  var transformState = null;
  var editSnapshot = null;
  var previewFrame = 0;

  function esc(value) {{
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }}

  function clone(value) {{
    return Object.assign({{}}, value || {{}});
  }}

  function deepClone(value) {{
    return value == null ? value : JSON.parse(JSON.stringify(value));
  }}

  function hexToRgba(hex, opacity) {{
    var value = String(hex || "#ffffff").replace("#", "");
    var r = parseInt(value.slice(0, 2), 16);
    var g = parseInt(value.slice(2, 4), 16);
    var b = parseInt(value.slice(4, 6), 16);
    return "rgba(" + r + "," + g + "," + b + "," + opacity + ")";
  }}

  function rgbaToHex(value, fallback) {{
    var text = String(value || "").trim();
    if (/^#[0-9a-f]{{6}}$/i.test(text)) return text.toLowerCase();
    if (/^#[0-9a-f]{{3}}$/i.test(text)) {{
      return "#" + text.slice(1).split("").map(function(part) {{
        return part + part;
      }}).join("").toLowerCase();
    }}
    var match = text.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/i);
    if (!match) return fallback;
    return "#" + [match[1], match[2], match[3]].map(function(part) {{
      return Math.max(0, Math.min(255, Number(part))).toString(16).padStart(2, "0");
    }}).join("");
  }}

  function rgbaOpacity(value, fallback) {{
    var match = String(value || "").match(/rgba\\([^,]+,[^,]+,[^,]+,\\s*([\\d.]+)\\)/i);
    return match ? Math.round(Number(match[1]) * 100) : fallback;
  }}

  function shapeName(id, kind) {{
    return (kind === "text" ? "rist_text_box:" : "rist_rect:") + id;
  }}

  function textName(id) {{ return "rist_text_box_text:" + id; }}

  function shapeKind(shape) {{
    var name = String(shape && shape.name || "");
    if (name.indexOf("rist_rect:") === 0) return "rect";
    if (name.indexOf("rist_text_box:") === 0) return "text";
    return "";
  }}

  function shapeId(shape) {{
    var kind = shapeKind(shape);
    if (!kind) return "";
    return String(shape.name).slice((kind === "text" ? "rist_text_box:" : "rist_rect:").length);
  }}

  function shapeIndex(id, kind, shapes) {{
    return (shapes || []).findIndex(function(shape) {{
      return shape.name === shapeName(id, kind);
    }});
  }}

  function annotationIndex(id, annotations) {{
    return (annotations || []).findIndex(function(annotation) {{
      return annotation.name === textName(id);
    }});
  }}

  function recordHistory() {{
    if (gd._ristHistory) gd._ristHistory.capture();
  }}

  function recordEditHistory() {{
    if (!gd._ristHistory) return;
    if (editSnapshot && editSnapshot.historyCaptured) return;
    if (editSnapshot
        && editSnapshot.historyState
        && gd._ristHistory.captureState) {{
      gd._ristHistory.captureState(editSnapshot.historyState);
      editSnapshot.historyCaptured = true;
      return;
    }}
    gd._ristHistory.capture();
    if (editSnapshot) editSnapshot.historyCaptured = true;
  }}

  var toolbar = gd.querySelector(".rist-plot-control-row");
  if (!toolbar) {{
    toolbar = document.createElement("div");
    toolbar.className = "rist-plot-control-row";
    gd.appendChild(toolbar);
  }}
  var toolButton = document.createElement("button");
  toolButton.type = "button";
  toolButton.className = "rist-shape-tool-button";
  toolButton.title = "도형 추가";
  toolButton.setAttribute("aria-label", "도형 추가");
  toolButton.innerHTML =
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' "
    + "stroke='currentColor' stroke-linecap='round' stroke-linejoin='round' "
    + "class='lucide lucide-square' aria-hidden='true'>"
    + "<rect width='18' height='18' x='3' y='3' rx='1'></rect></svg>";
  toolbar.appendChild(toolButton);

  var panel = document.createElement("div");
  panel.className = "rist-shape-editor-panel";
  panel.innerHTML =
    "<div class='rist-shape-editor-head'><span>도형 편집</span>"
    + "<button type='button' class='rist-shape-editor-close' aria-label='닫기'>×</button></div>"
    + "<div class='rist-shape-kind-tabs'>"
    + "<button type='button' class='rist-shape-kind-button is-active' data-kind='rect'>사각형</button>"
    + "<button type='button' class='rist-shape-kind-button' data-kind='text'>텍스트 박스</button>"
    + "</div>"
    + "<div class='rist-shape-text-options'>"
    + "<input class='rist-shape-editor-text' type='text' value='텍스트' aria-label='도형 글자'>"
    + "<div class='rist-shape-font-row'>"
    + "<span>글자색</span><input class='rist-shape-font-color rist-shape-color' type='color' value='#1f2933'>"
    + "<span>크기</span><input class='rist-shape-font-size' type='number' min='8' max='72' step='1' value='12' aria-label='글자 크기'>"
    + "</div></div>"
    + "<div class='rist-shape-editor-colors'>"
    + "<span>선</span><input class='rist-shape-border-color rist-shape-color' type='color' value='#2563eb'>"
    + "<span>배경</span><input class='rist-shape-fill-color rist-shape-color' type='color' value='#dbeafe'>"
    + "</div>"
    + "<label class='rist-shape-border-row'><span>선 종류</span>"
    + "<select class='rist-shape-border-style' aria-label='사각형 선 종류'>"
    + "<option value='none'>선 없음</option><option value='solid' selected>실선</option>"
    + "<option value='dash'>파선</option>"
    + "<option value='dot'>점선</option><option value='dashdot'>일점쇄선</option>"
    + "</select></label>"
    + "<label class='rist-shape-opacity-row'><span>배경 투명도</span>"
    + "<input class='rist-shape-opacity' type='range' min='0' max='100' value='30'></label>"
    + "<label class='rist-shape-fill-none-row'>"
    + "<input class='rist-shape-fill-none' type='checkbox'>배경 없음</label>"
    + "<div class='rist-shape-editor-actions'>"
    + "<button type='button' class='rist-shape-action rist-shape-draw'>그리기</button>"
    + "<button type='button' class='rist-shape-action rist-shape-delete' disabled>삭제</button>"
    + "</div>";
  gd.appendChild(panel);

  var preview = document.createElement("div");
  preview.className = "rist-shape-draw-preview";
  gd.appendChild(preview);
  var selection = document.createElement("div");
  selection.className = "rist-shape-selection";
  selection.innerHTML = ["nw", "n", "ne", "e", "se", "s", "sw", "w"].map(function(dir) {{
    return "<span class='rist-shape-resize-handle' data-dir='" + dir + "'></span>";
  }}).join("");
  gd.appendChild(selection);
  var textOptions = panel.querySelector(".rist-shape-text-options");
  var textInput = panel.querySelector(".rist-shape-editor-text");
  var fontColorInput = panel.querySelector(".rist-shape-font-color");
  var fontSizeInput = panel.querySelector(".rist-shape-font-size");
  var borderInput = panel.querySelector(".rist-shape-border-color");
  var fillInput = panel.querySelector(".rist-shape-fill-color");
  var borderStyleInput = panel.querySelector(".rist-shape-border-style");
  var opacityInput = panel.querySelector(".rist-shape-opacity");
  var fillNoneInput = panel.querySelector(".rist-shape-fill-none");
  var drawButton = panel.querySelector(".rist-shape-draw");
  var deleteButton = panel.querySelector(".rist-shape-delete");
  var kindButtons = Array.prototype.slice.call(
    panel.querySelectorAll(".rist-shape-kind-button")
  );

  function drawLabel() {{
    return drawKind === "text" ? "텍스트 박스 그리기" : "사각형 그리기";
  }}

  function fontSizeValue() {{
    var value = Number(fontSizeInput.value);
    if (!Number.isFinite(value)) value = 12;
    value = Math.max(8, Math.min(72, Math.round(value)));
    fontSizeInput.value = String(value);
    return value;
  }}

  function borderLine() {{
    var hidden = borderStyleInput.value === "none";
    return {{
      color: borderInput.value,
      width: hidden ? 0 : 2,
      dash: hidden ? "solid" : borderStyleInput.value
    }};
  }}

  function updateBorderControl() {{
    borderInput.disabled = borderStyleInput.value === "none";
  }}

  function fillColor() {{
    var opacity = fillNoneInput.checked ? 0 : Number(opacityInput.value) / 100;
    return hexToRgba(fillInput.value, opacity);
  }}

  function updateFillControl() {{
    fillInput.disabled = fillNoneInput.checked;
    opacityInput.disabled = fillNoneInput.checked;
  }}

  function setDrawMode(enabled) {{
    drawMode = !!enabled;
    toolButton.classList.toggle("is-active", drawMode);
    drawButton.classList.toggle("is-active", drawMode);
    drawButton.textContent = drawMode ? "그래프에서 드래그" : drawLabel();
  }}

  function setDrawKind(kind) {{
    drawKind = kind === "text" ? "text" : "rect";
    kindButtons.forEach(function(button) {{
      button.classList.toggle("is-active", button.dataset.kind === drawKind);
    }});
    textOptions.style.display = drawKind === "text" ? "block" : "none";
    setDrawMode(drawMode);
  }}

  function openPanel() {{
    var legendPanel = gd.querySelector(".rist-legend-edit-panel");
    if (legendPanel) legendPanel.style.display = "none";
    panel.style.display = "block";
  }}

  function closePanel() {{
    panel.style.display = "none";
    setDrawMode(false);
  }}

  function updateSelectionButtons() {{
    deleteButton.disabled = !selectedId;
  }}

  function clearSelection() {{
    if (previewFrame) {{
      cancelAnimationFrame(previewFrame);
      previewFrame = 0;
    }}
    selectedId = "";
    selectedKind = "";
    transformState = null;
    editSnapshot = null;
    selection.style.display = "none";
    updateSelectionButtons();
  }}

  function selectedShape() {{
    if (!selectedId || !selectedKind) return null;
    var shapes = gd.layout.shapes || [];
    var index = shapeIndex(selectedId, selectedKind, shapes);
    return index >= 0 ? shapes[index] : null;
  }}

  function shapeClientBounds(shape) {{
    var drag = gd.querySelector(".nsewdrag");
    var layout = gd._fullLayout;
    if (!shape || !drag || !layout || !layout.xaxis || !layout.yaxis) return null;
    var gdRect = gd.getBoundingClientRect();
    var dragRect = drag.getBoundingClientRect();
    var x0 = dragRect.left - gdRect.left + layout.xaxis.d2p(shape.x0);
    var x1 = dragRect.left - gdRect.left + layout.xaxis.d2p(shape.x1);
    var y0 = dragRect.top - gdRect.top + layout.yaxis.d2p(shape.y0);
    var y1 = dragRect.top - gdRect.top + layout.yaxis.d2p(shape.y1);
    return {{
      left: Math.min(x0, x1),
      right: Math.max(x0, x1),
      top: Math.min(y0, y1),
      bottom: Math.max(y0, y1)
    }};
  }}

  function setSelectionBounds(bounds) {{
    if (!bounds) {{
      selection.style.display = "none";
      return;
    }}
    selection.style.display = "block";
    selection.style.left = bounds.left + "px";
    selection.style.top = bounds.top + "px";
    selection.style.width = Math.max(1, bounds.right - bounds.left) + "px";
    selection.style.height = Math.max(1, bounds.bottom - bounds.top) + "px";
  }}

  function updateSelectionOverlay() {{
    setSelectionBounds(shapeClientBounds(selectedShape()));
  }}

  function captureEditSnapshot() {{
    editSnapshot = {{
      id: selectedId,
      kind: selectedKind,
      historyState: gd._ristHistory && gd._ristHistory.snapshot
        ? gd._ristHistory.snapshot()
        : null,
      historyCaptured: false
    }};
  }}

  function finishSelection(close) {{
    var pending = Promise.resolve();
    if (previewFrame) {{
      cancelAnimationFrame(previewFrame);
      previewFrame = 0;
      pending = previewSelection();
    }}
    return pending.then(function() {{
      clearSelection();
      if (close) closePanel();
    }});
  }}

  function beginShapeSelection(id, kind) {{
    var shapes = gd.layout.shapes || [];
    var annotations = gd.layout.annotations || [];
    var si = shapeIndex(id, kind, shapes);
    var ai = annotationIndex(id, annotations);
    if (si < 0 || (kind === "text" && ai < 0)) return;
    selectedId = id;
    selectedKind = kind;
    var shape = shapes[si];
    var annotation = ai >= 0 ? annotations[ai] : null;
    setDrawKind(kind);
    if (annotation) {{
      textInput.value = String(annotation.text || "").replace(/<[^>]*>/g, "");
      fontColorInput.value = rgbaToHex(
        annotation.font && annotation.font.color,
        "#1f2933"
      );
      fontSizeInput.value = String(
        Math.max(8, Math.min(72, Number(annotation.font && annotation.font.size) || 12))
      );
    }}
    borderInput.value = rgbaToHex(shape.line && shape.line.color, "#2563eb");
    fillInput.value = rgbaToHex(shape.fillcolor, "#dbeafe");
    borderStyleInput.value = shape.line && Number(shape.line.width) === 0
      ? "none"
      : String(shape.line && shape.line.dash || "solid");
    updateBorderControl();
    var fillOpacity = rgbaOpacity(shape.fillcolor, 30);
    fillNoneInput.checked = fillOpacity === 0;
    opacityInput.value = String(fillOpacity === 0 ? 30 : fillOpacity);
    updateFillControl();
    updateSelectionButtons();
    captureEditSnapshot();
    openPanel();
    requestAnimationFrame(updateSelectionOverlay);
    return Promise.resolve();
  }}

  function selectShape(id, kind) {{
    if (selectedId === id && selectedKind === kind) {{
      openPanel();
      return Promise.resolve();
    }}
    if (selectedId) {{
      return finishSelection(false).then(function() {{
        return beginShapeSelection(id, kind);
      }});
    }}
    return beginShapeSelection(id, kind);
  }}

  function syncOriginalArrays(id, kind, shape, annotation, remove) {{
    if (gd._ristFtirUnitOriginalShapes) {{
      var si = shapeIndex(id, kind, gd._ristFtirUnitOriginalShapes);
      if (remove && si >= 0) gd._ristFtirUnitOriginalShapes.splice(si, 1);
      else if (si >= 0) gd._ristFtirUnitOriginalShapes[si] = clone(shape);
      else if (!remove) gd._ristFtirUnitOriginalShapes.push(clone(shape));
    }}
    if (kind === "text" && gd._ristFtirUnitOriginalAnnotations) {{
      var ai = annotationIndex(id, gd._ristFtirUnitOriginalAnnotations);
      if (remove && ai >= 0) gd._ristFtirUnitOriginalAnnotations.splice(ai, 1);
      else if (ai >= 0) gd._ristFtirUnitOriginalAnnotations[ai] = clone(annotation);
      else if (!remove) gd._ristFtirUnitOriginalAnnotations.push(clone(annotation));
    }}
  }}

  function editedObjectsFromControls() {{
    if (!selectedId || !selectedKind) return null;
    var shapes = gd.layout.shapes || [];
    var annotations = gd.layout.annotations || [];
    var si = shapeIndex(selectedId, selectedKind, shapes);
    var ai = annotationIndex(selectedId, annotations);
    if (si < 0 || (selectedKind === "text" && ai < 0)) return null;
    var shape = deepClone(shapes[si]);
    shape.line = Object.assign({{}}, shape.line || {{}}, borderLine());
    shape.fillcolor = fillColor();
    var annotation = ai >= 0 ? deepClone(annotations[ai]) : null;
    if (annotation) {{
      annotation.text = esc(textInput.value || "텍스트");
      annotation.font = Object.assign({{}}, annotation.font || {{}}, {{
        color: fontColorInput.value,
        size: fontSizeValue()
      }});
    }}
    return {{
      shape: shape,
      annotation: annotation,
      shapeIndex: si,
      annotationIndex: ai
    }};
  }}

  function previewSelection() {{
    previewFrame = 0;
    var edited = editedObjectsFromControls();
    if (!edited) return Promise.resolve();
    recordEditHistory();
    var shapes = (gd.layout.shapes || []).map(clone);
    var annotations = (gd.layout.annotations || []).map(clone);
    shapes[edited.shapeIndex] = edited.shape;
    if (edited.annotationIndex >= 0) {{
      annotations[edited.annotationIndex] = edited.annotation;
    }}
    syncOriginalArrays(
      selectedId,
      selectedKind,
      edited.shape,
      edited.annotation,
      false
    );
    return window.Plotly.relayout(gd, {{
      shapes: shapes,
      annotations: annotations
    }}).then(updateSelectionOverlay);
  }}

  function schedulePreview() {{
    if (!selectedId || previewFrame) return;
    previewFrame = requestAnimationFrame(previewSelection);
  }}

  function addObject(x0, y0, x1, y1) {{
    recordHistory();
    var kind = drawKind;
    var id = Date.now() + "-" + Math.floor(Math.random() * 10000);
    var shape = {{
      type: "rect",
      xref: "x",
      yref: "y",
      x0: x0,
      y0: y0,
      x1: x1,
      y1: y1,
      name: shapeName(id, kind),
      line: borderLine(),
      fillcolor: fillColor(),
      layer: "above"
    }};
    var annotation = null;
    if (kind === "text") {{
      var bounds = shapeClientBounds(shape);
      annotation = {{
        xref: "x",
        yref: "y",
        x: (x0 + x1) / 2,
        y: (y0 + y1) / 2,
        text: esc(textInput.value || "텍스트"),
        name: textName(id),
        showarrow: false,
        captureevents: true,
        align: "center",
        width: bounds ? Math.max(20, bounds.right - bounds.left - 12) : undefined,
        height: bounds ? Math.max(16, bounds.bottom - bounds.top - 8) : undefined,
        bgcolor: "rgba(255,255,255,0)",
        font: {{
          color: fontColorInput.value,
          size: fontSizeValue()
        }}
      }};
    }}
    var shapes = (gd.layout.shapes || []).map(clone);
    var annotations = (gd.layout.annotations || []).map(clone);
    shapes.push(shape);
    if (annotation) annotations.push(annotation);
    syncOriginalArrays(id, kind, shape, annotation, false);
    window.Plotly.relayout(gd, {{ shapes: shapes, annotations: annotations }}).then(function() {{
      selectShape(id, kind);
    }});
    setDrawMode(false);
  }}

  function deleteSelection() {{
    if (!selectedId) return;
    var id = selectedId;
    var kind = selectedKind;
    var shapes = (gd.layout.shapes || []).map(clone);
    var annotations = (gd.layout.annotations || []).map(clone);
    var si = shapeIndex(id, kind, shapes);
    var ai = annotationIndex(id, annotations);
    if (si < 0 || (kind === "text" && ai < 0)) return;
    recordEditHistory();
    var shape = shapes[si];
    var annotation = ai >= 0 ? annotations[ai] : null;
    shapes.splice(si, 1);
    if (ai >= 0) annotations.splice(ai, 1);
    syncOriginalArrays(id, kind, shape, annotation, true);
    clearSelection();
    window.Plotly.relayout(gd, {{ shapes: shapes, annotations: annotations }});
  }}

  function plotPoint(ev) {{
    var drag = gd.querySelector(".nsewdrag");
    var layout = gd._fullLayout;
    if (!drag || !layout || !layout.xaxis || !layout.yaxis) return null;
    var rect = drag.getBoundingClientRect();
    var px = ev.clientX - rect.left;
    var py = ev.clientY - rect.top;
    if (px < 0 || py < 0 || px > rect.width || py > rect.height) return null;
    return {{
      x: layout.xaxis.p2d(px),
      y: layout.yaxis.p2d(py),
      rect: rect
    }};
  }}

  function plotClientBounds() {{
    var drag = gd.querySelector(".nsewdrag");
    if (!drag) return null;
    var gdRect = gd.getBoundingClientRect();
    var dragRect = drag.getBoundingClientRect();
    return {{
      left: dragRect.left - gdRect.left,
      right: dragRect.right - gdRect.left,
      top: dragRect.top - gdRect.top,
      bottom: dragRect.bottom - gdRect.top
    }};
  }}

  function objectAtPoint(point) {{
    var shapes = gd.layout.shapes || [];
    for (var index = shapes.length - 1; index >= 0; index -= 1) {{
      var shape = shapes[index];
      var kind = shapeKind(shape);
      if (!kind || shape.visible === false) continue;
      var minX = Math.min(Number(shape.x0), Number(shape.x1));
      var maxX = Math.max(Number(shape.x0), Number(shape.x1));
      var minY = Math.min(Number(shape.y0), Number(shape.y1));
      var maxY = Math.max(Number(shape.y0), Number(shape.y1));
      if (point.x >= minX && point.x <= maxX && point.y >= minY && point.y <= maxY) {{
        return {{ id: shapeId(shape), kind: kind }};
      }}
    }}
    return null;
  }}

  function resizedBounds(state, ev) {{
    var bounds = Object.assign({{}}, state.startBounds);
    var limits = plotClientBounds();
    if (!limits) return bounds;
    var dx = ev.clientX - state.startX;
    var dy = ev.clientY - state.startY;
    var minSize = 16;
    if (state.mode === "move") {{
      var width = bounds.right - bounds.left;
      var height = bounds.bottom - bounds.top;
      bounds.left += dx;
      bounds.right += dx;
      bounds.top += dy;
      bounds.bottom += dy;
      if (bounds.left < limits.left) {{
        bounds.left = limits.left;
        bounds.right = limits.left + width;
      }}
      if (bounds.right > limits.right) {{
        bounds.right = limits.right;
        bounds.left = limits.right - width;
      }}
      if (bounds.top < limits.top) {{
        bounds.top = limits.top;
        bounds.bottom = limits.top + height;
      }}
      if (bounds.bottom > limits.bottom) {{
        bounds.bottom = limits.bottom;
        bounds.top = limits.bottom - height;
      }}
      return bounds;
    }}
    if (state.mode.indexOf("w") >= 0) {{
      bounds.left = Math.max(limits.left, Math.min(bounds.right - minSize, bounds.left + dx));
    }}
    if (state.mode.indexOf("e") >= 0) {{
      bounds.right = Math.min(limits.right, Math.max(bounds.left + minSize, bounds.right + dx));
    }}
    if (state.mode.indexOf("n") >= 0) {{
      bounds.top = Math.max(limits.top, Math.min(bounds.bottom - minSize, bounds.top + dy));
    }}
    if (state.mode.indexOf("s") >= 0) {{
      bounds.bottom = Math.min(
        limits.bottom,
        Math.max(bounds.top + minSize, bounds.bottom + dy)
      );
    }}
    return bounds;
  }}

  function applyTransformedBounds(bounds) {{
    if (!selectedId || !selectedKind || !bounds) return;
    var drag = gd.querySelector(".nsewdrag");
    var layout = gd._fullLayout;
    if (!drag || !layout || !layout.xaxis || !layout.yaxis) return;
    var gdRect = gd.getBoundingClientRect();
    var dragRect = drag.getBoundingClientRect();
    var offsetX = dragRect.left - gdRect.left;
    var offsetY = dragRect.top - gdRect.top;
    var shapes = (gd.layout.shapes || []).map(clone);
    var annotations = (gd.layout.annotations || []).map(clone);
    var si = shapeIndex(selectedId, selectedKind, shapes);
    var ai = annotationIndex(selectedId, annotations);
    if (si < 0 || (selectedKind === "text" && ai < 0)) return;
    var shape = shapes[si];
    shape.x0 = layout.xaxis.p2d(bounds.left - offsetX);
    shape.x1 = layout.xaxis.p2d(bounds.right - offsetX);
    shape.y0 = layout.yaxis.p2d(bounds.top - offsetY);
    shape.y1 = layout.yaxis.p2d(bounds.bottom - offsetY);
    var annotation = ai >= 0 ? annotations[ai] : null;
    if (annotation) {{
      annotation.x = layout.xaxis.p2d(
        ((bounds.left + bounds.right) / 2) - offsetX
      );
      annotation.y = layout.yaxis.p2d(
        ((bounds.top + bounds.bottom) / 2) - offsetY
      );
      annotation.width = Math.max(20, bounds.right - bounds.left - 12);
      annotation.height = Math.max(16, bounds.bottom - bounds.top - 8);
    }}
    syncOriginalArrays(
      selectedId,
      selectedKind,
      shape,
      annotation,
      false
    );
    window.Plotly.relayout(gd, {{
      shapes: shapes,
      annotations: annotations
    }}).then(updateSelectionOverlay);
  }}

  gd.addEventListener("pointerdown", function(ev) {{
    if (ev.target.closest(
      ".rist-shape-editor-panel, .rist-plot-control-row, .modebar, .rist-shape-selection"
    )) return;
    var point = plotPoint(ev);
    if (!point) return;
    if (drawMode) {{
      ev._ristShapeEditorHandled = true;
      drawStart = {{
        clientX: ev.clientX,
        clientY: ev.clientY,
        x: point.x,
        y: point.y
      }};
      preview.style.display = "block";
      ev.preventDefault();
      ev.stopPropagation();
      return;
    }}
    var object = objectAtPoint(point);
    if (object) {{
      ev._ristShapeEditorHandled = true;
      selectShape(object.id, object.kind);
      ev.preventDefault();
      ev.stopPropagation();
    }} else {{
      ev._ristShapeEditorHandled = true;
      finishSelection(true);
    }}
  }}, true);

  selection.addEventListener("pointerdown", function(ev) {{
    if (!selectedId || !selectedKind) return;
    var bounds = shapeClientBounds(selectedShape());
    if (!bounds) return;
    var handle = ev.target.closest(".rist-shape-resize-handle");
    transformState = {{
      mode: handle ? handle.dataset.dir : "move",
      startX: ev.clientX,
      startY: ev.clientY,
      startBounds: bounds,
      bounds: bounds,
      captured: false
    }};
    ev._ristShapeEditorHandled = true;
    ev.preventDefault();
    ev.stopPropagation();
  }});

  document.addEventListener("pointermove", function(ev) {{
    if (transformState) {{
      if (!transformState.captured
          && (Math.abs(ev.clientX - transformState.startX) > 1
              || Math.abs(ev.clientY - transformState.startY) > 1)) {{
        transformState.captured = true;
        recordEditHistory();
      }}
      transformState.bounds = resizedBounds(transformState, ev);
      setSelectionBounds(transformState.bounds);
      ev.preventDefault();
      return;
    }}
    if (drawStart) {{
      var gdRect = gd.getBoundingClientRect();
      var left = Math.min(drawStart.clientX, ev.clientX) - gdRect.left;
      var top = Math.min(drawStart.clientY, ev.clientY) - gdRect.top;
      preview.style.left = left + "px";
      preview.style.top = top + "px";
      preview.style.width = Math.abs(ev.clientX - drawStart.clientX) + "px";
      preview.style.height = Math.abs(ev.clientY - drawStart.clientY) + "px";
      ev.preventDefault();
    }}
  }});

  document.addEventListener("pointerup", function(ev) {{
    if (transformState) {{
      var transform = transformState;
      transformState = null;
      if (transform.captured) applyTransformedBounds(transform.bounds);
      else updateSelectionOverlay();
      ev.preventDefault();
      return;
    }}
    if (drawStart) {{
      var start = drawStart;
      drawStart = null;
      preview.style.display = "none";
      var point = plotPoint(ev);
      if (!point
          || Math.abs(ev.clientX - start.clientX) < 6
          || Math.abs(ev.clientY - start.clientY) < 6) return;
      addObject(start.x, start.y, point.x, point.y);
      ev.preventDefault();
    }}
  }});

  toolButton.addEventListener("click", function(ev) {{
    ev.preventDefault();
    ev.stopPropagation();
    openPanel();
    if (selectedId) {{
      finishSelection(false).then(function() {{
        setDrawMode(!drawMode);
      }});
    }} else {{
      setDrawMode(!drawMode);
    }}
  }});
  toolButton.addEventListener("pointerdown", function(ev) {{
    ev._ristShapeEditorHandled = true;
    ev.stopPropagation();
  }});
  panel.querySelector(".rist-shape-editor-close").addEventListener("click", function() {{
    finishSelection(true);
  }});
  kindButtons.forEach(function(button) {{
    button.addEventListener("click", function() {{
      var kind = button.dataset.kind;
      finishSelection(false).then(function() {{
        setDrawKind(kind);
      }});
    }});
  }});
  drawButton.addEventListener("click", function() {{
    if (selectedId) {{
      finishSelection(false).then(function() {{
        setDrawMode(!drawMode);
      }});
    }} else {{
      setDrawMode(!drawMode);
    }}
  }});
  [textInput, fontColorInput, fontSizeInput, borderInput, fillInput, opacityInput]
    .forEach(function(input) {{
      input.addEventListener("input", schedulePreview);
    }});
  borderStyleInput.addEventListener("change", function() {{
    updateBorderControl();
    schedulePreview();
  }});
  fillNoneInput.addEventListener("change", function() {{
    updateFillControl();
    schedulePreview();
  }});
  deleteButton.addEventListener("click", deleteSelection);
  gd.on("plotly_clickannotation", function(ev) {{
    var annotation = ev && ev.annotation;
    var name = annotation && String(annotation.name || "");
    if (name.indexOf("rist_text_box_text:") !== 0) return;
    selectShape(name.slice("rist_text_box_text:".length), "text");
  }});
  gd.addEventListener("rist-history-restored", function() {{
    clearSelection();
    closePanel();
  }});
  gd.addEventListener("rist-plot-data-replaced", function() {{
    clearSelection();
    closePanel();
  }});
  gd.addEventListener("rist-shape-editor-close", function() {{
    finishSelection(true);
  }});
  document.addEventListener("pointerdown", function(ev) {{
    if (panel.style.display !== "block" || ev._ristShapeEditorHandled) return;
    if (panel.contains(ev.target)
        || toolButton.contains(ev.target)
        || selection.contains(ev.target)) return;
    finishSelection(true);
  }});
  gd.on("plotly_afterplot", updateSelectionOverlay);
  gd.on("plotly_relayout", function() {{
    requestAnimationFrame(updateSelectionOverlay);
  }});
  window.addEventListener("resize", updateSelectionOverlay);
  setDrawKind("rect");
  updateBorderControl();
  updateFillControl();
  updateSelectionButtons();
}})();
</script>
"""


def _plot_edit_history_js(div_id: str) -> str:
    """그래프 편집 상태의 실행취소/다시 실행 스택을 제공한다."""
    return f"""
<style>
#{div_id} .rist-history-controls {{
  order: 5;
  display: flex;
  gap: 8px;
  align-items: center;
}}
#{div_id} .rist-history-button {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid #9fb3c8;
  border-radius: 4px;
  background: #f5f7fa;
  color: #1f2933;
  cursor: pointer;
  padding: 0;
  transition: background-color 120ms ease, border-color 120ms ease, color 120ms ease;
}}
#{div_id} .rist-history-button:hover:not(:disabled) {{
  border-color: #7891a8;
  background: #e8eef5;
  color: #102a43;
}}
#{div_id} .rist-history-button:active:not(:disabled) {{
  background: #d9e2ec;
}}
#{div_id} .rist-history-button svg {{
  width: 17px;
  height: 17px;
  stroke-width: 2.1;
}}
#{div_id} .rist-history-button:disabled {{
  opacity: 0.38;
  cursor: default;
}}
</style>
<script>
(function() {{
  var gd = document.getElementById("{div_id}");
  if (!gd || !window.Plotly || gd._ristHistory) return;
  var undoStack = [];
  var redoStack = [];
  var restoring = false;
  var lastCaptureAt = 0;
  var MAX_HISTORY = 50;

  function clone(value) {{
    return value == null ? value : JSON.parse(JSON.stringify(value));
  }}

  function snapshot() {{
    return {{
      data: clone(gd.data || []),
      layout: clone(gd.layout || {{}}),
      originalAnnotations: clone(gd._ristFtirUnitOriginalAnnotations || null),
      originalShapes: clone(gd._ristFtirUnitOriginalShapes || null)
    }};
  }}

  var toolbar = gd.querySelector(".rist-plot-control-row");
  if (!toolbar) {{
    if (getComputedStyle(gd).position === "static") gd.style.position = "relative";
    toolbar = document.createElement("div");
    toolbar.className = "rist-plot-control-row";
    gd.appendChild(toolbar);
  }}
  var controls = document.createElement("div");
  controls.className = "rist-history-controls";
  controls.innerHTML =
    "<button type='button' class='rist-history-button rist-history-undo' "
      + "title='실행취소 (Ctrl/Cmd+Z)' aria-label='실행취소'>"
      + "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' "
      + "stroke='currentColor' stroke-linecap='round' stroke-linejoin='round' "
      + "class='lucide lucide-undo-2' aria-hidden='true'>"
      + "<path d='M9 14 4 9l5-5'></path>"
      + "<path d='M4 9h10.5a5.5 5.5 0 0 1 0 11H11'></path>"
      + "</svg></button>"
    + "<button type='button' class='rist-history-button rist-history-redo' "
      + "title='다시 실행 (Ctrl/Cmd+Shift+Z)' aria-label='다시 실행'>"
      + "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' "
      + "stroke='currentColor' stroke-linecap='round' stroke-linejoin='round' "
      + "class='lucide lucide-redo-2' aria-hidden='true'>"
      + "<path d='m15 14 5-5-5-5'></path>"
      + "<path d='M20 9H9.5A5.5 5.5 0 0 0 4 14.5 5.5 5.5 0 0 0 9.5 20H13'></path>"
      + "</svg></button>";
  toolbar.appendChild(controls);
  var undoButton = controls.querySelector(".rist-history-undo");
  var redoButton = controls.querySelector(".rist-history-redo");

  function updateButtons() {{
    undoButton.disabled = undoStack.length === 0 || restoring;
    redoButton.disabled = redoStack.length === 0 || restoring;
  }}

  function capture() {{
    if (restoring) return;
    var now = Date.now();
    if (now - lastCaptureAt < 50) return;
    lastCaptureAt = now;
    captureState(snapshot());
  }}

  function captureState(state) {{
    if (restoring || !state) return;
    try {{
      undoStack.push(clone(state));
      if (undoStack.length > MAX_HISTORY) undoStack.shift();
      redoStack = [];
      updateButtons();
    }} catch (err) {{
      console.error("RIST plot history capture failed", err);
    }}
  }}

  function restore(state) {{
    if (!state || restoring) return Promise.resolve();
    restoring = true;
    updateButtons();
    return window.Plotly.react(
      gd,
      clone(state.data),
      clone(state.layout),
      gd._context
    ).then(function() {{
      gd._ristFtirUnitOriginalAnnotations = clone(state.originalAnnotations);
      gd._ristFtirUnitOriginalShapes = clone(state.originalShapes);
      gd.dispatchEvent(new CustomEvent("rist-history-restored"));
    }}).catch(function(err) {{
      console.error("RIST plot history restore failed", err);
    }}).then(function() {{
      restoring = false;
      updateButtons();
    }});
  }}

  function undo() {{
    if (!undoStack.length || restoring) return;
    var state = undoStack.pop();
    redoStack.push(snapshot());
    restore(state);
  }}

  function redo() {{
    if (!redoStack.length || restoring) return;
    var state = redoStack.pop();
    undoStack.push(snapshot());
    restore(state);
  }}

  function reset() {{
    undoStack = [];
    redoStack = [];
    lastCaptureAt = 0;
    updateButtons();
  }}

  gd._ristHistory = {{
    capture: capture,
    captureState: captureState,
    snapshot: snapshot,
    undo: undo,
    redo: redo,
    reset: reset
  }};
  undoButton.addEventListener("click", undo);
  redoButton.addEventListener("click", redo);
  gd.on("plotly_legendclick", function() {{ capture(); }});
  gd.addEventListener("rist-plot-data-replaced", reset);
  document.addEventListener("keydown", function(ev) {{
    var target = ev.target;
    if (target && target.closest && target.closest("input,textarea,select")) return;
    var modifier = ev.ctrlKey || ev.metaKey;
    if (!modifier) return;
    var key = String(ev.key || "").toLowerCase();
    if (key === "z" && ev.shiftKey) {{
      ev.preventDefault();
      redo();
    }} else if (key === "z") {{
      ev.preventDefault();
      undo();
    }} else if (key === "y") {{
      ev.preventDefault();
      redo();
    }}
  }});
  updateButtons();
}})();
</script>
"""


def _responsive_legend_js(div_id: str, wide_legend_inside: bool = True,
                          breakpoint_px: int = LEGEND_BREAKPOINT_PX) -> str:
    """화면 너비에 따라 범례 위치를 자동 전환하는 JS 스니펫."""
    if wide_legend_inside:
        wide_layout = (
            '{"legend.orientation": "v",'
            ' "legend.x": 0.98, "legend.xanchor": "right",'
            ' "legend.y": 0.98, "legend.yanchor": "top",'
            ' "legend.bgcolor": "rgba(255,255,255,0.7)",'
            ' "margin.r": 30, "margin.b": 70}'
        )
    else:
        wide_layout = (
            '{"legend.orientation": "v",'
            ' "legend.x": 1.02, "legend.xanchor": "left",'
            ' "legend.y": 1.0, "legend.yanchor": "top",'
            ' "legend.bgcolor": "rgba(255,255,255,0.7)",'
            ' "margin.r": 180, "margin.b": 70}'
        )
    return f"""
<script>
(function() {{
  var gd = document.getElementById("{div_id}");
  var WIDE = {breakpoint_px};
  var originalHeightStyle = gd ? gd.style.height : "";
  var originalMinHeightStyle = gd ? gd.style.minHeight : "";
  var baseHeightPx = null;
  var legendReservePx = 50;

  function setMobileLegendReserve(enabled) {{
    if (!gd) return;
    if (enabled) {{
      if (baseHeightPx == null) {{
        baseHeightPx = Math.max(1, Math.round(gd.getBoundingClientRect().height));
      }}
      gd.style.height = (baseHeightPx + legendReservePx) + "px";
      gd.style.minHeight = (baseHeightPx + legendReservePx) + "px";
    }} else {{
      gd.style.height = originalHeightStyle;
      gd.style.minHeight = originalMinHeightStyle;
      baseHeightPx = null;
    }}
  }}

  function applyLegend() {{
    if (!gd || !window.Plotly) return;
    var wide = window.innerWidth >= WIDE;
    setMobileLegendReserve(!wide);
    var layout = wide ? {wide_layout} : {{
      "legend.orientation": "h",
      "legend.x": 0.5, "legend.xanchor": "center",
      "legend.y": -0.2, "legend.yanchor": "top",
      "margin.r": 30, "margin.b": 120
    }};
    window.Plotly.relayout(gd, layout);
  }}
  if (gd) {{
    applyLegend();
    window.addEventListener("resize", applyLegend);
  }}
}})();
</script>
"""


def _legend_drag_handle_js(div_id: str) -> str:
    """Plotly 범례를 본문이 아닌 전용 상단바에서만 이동시키는 JS 스니펫."""
    return f"""
<style>
#{div_id} .rist-legend-drag-handle {{
  position: absolute;
  z-index: 35;
  display: none;
  align-items: center;
  justify-content: center;
  height: 18px;
  min-width: 72px;
  border: 1px solid rgba(148,163,184,0.9);
  border-radius: 4px 4px 0 0;
  background: rgba(248,250,252,0.95);
  color: #334155;
  cursor: move;
  font: bold 10px Arial, sans-serif;
  letter-spacing: 0;
  box-shadow: 0 1px 4px rgba(15,23,42,0.12);
  user-select: none;
  touch-action: none;
  box-sizing: border-box;
}}
#{div_id} .rist-legend-drag-handle.is-dragging {{
  background: #dbeafe;
  border-color: #60a5fa;
  color: #1d4ed8;
}}
</style>
<script>
(function() {{
  var gd = document.getElementById("{div_id}");
  if (!gd || gd._ristLegendDragHandleInstalled) return;
  gd._ristLegendDragHandleInstalled = true;
  if (getComputedStyle(gd).position === "static") gd.style.position = "relative";

  var handle = document.createElement("div");
  handle.className = "rist-legend-drag-handle";
  handle.textContent = "범례 이동";
  handle.title = "이 바를 드래그해서 범례 위치 이동";
  gd.appendChild(handle);
  var dragState = null;
  var updateFrame = 0;

  function clamp(value, min, max) {{
    return Math.max(min, Math.min(max, value));
  }}

  function legendLayout() {{
    var full = gd._fullLayout || {{}};
    return full.legend || (gd.layout && gd.layout.legend) || {{}};
  }}

  function plotSize() {{
    var full = gd._fullLayout || {{}};
    var width = Number(full.width || gd.clientWidth || 1);
    var height = Number(full.height || gd.clientHeight || 1);
    var margin = full.margin || {{}};
    return {{
      width: Math.max(1, width - Number(margin.l || 0) - Number(margin.r || 0)),
      height: Math.max(1, height - Number(margin.t || 0) - Number(margin.b || 0))
    }};
  }}

  function legendBoxRect() {{
    var legend = gd.querySelector(".legend");
    if (!legend) return null;
    var box = legend.querySelector(".bg");
    var target = box || legend;
    var rect = target.getBoundingClientRect();
    if (rect.width < 8 || rect.height < 8) return null;
    return rect;
  }}

  function scheduleHandlePosition() {{
    if (updateFrame) cancelAnimationFrame(updateFrame);
    updateFrame = requestAnimationFrame(positionHandle);
  }}

  function positionHandle() {{
    updateFrame = 0;
    var rect = legendBoxRect();
    if (!rect) {{
      handle.style.display = "none";
      return;
    }}
    var gdRect = gd.getBoundingClientRect();
    var left = rect.left - gdRect.left;
    var top = rect.top - gdRect.top - 18;
    if (top < 2) top = rect.top - gdRect.top;
    handle.style.left = Math.round(left) + "px";
    handle.style.top = Math.round(top) + "px";
    handle.style.width = Math.max(72, Math.round(rect.width)) + "px";
    handle.style.display = "flex";
  }}

  handle.addEventListener("pointerdown", function(ev) {{
    if (ev.button !== 0) return;
    var layout = legendLayout();
    var size = plotSize();
    var gdRect = gd.getBoundingClientRect();
    var handleRect = handle.getBoundingClientRect();
    dragState = {{
      pointerId: ev.pointerId,
      startX: ev.clientX,
      startY: ev.clientY,
      handleLeft: handleRect.left - gdRect.left,
      handleTop: handleRect.top - gdRect.top,
      legendX: Number.isFinite(Number(layout.x)) ? Number(layout.x) : 1,
      legendY: Number.isFinite(Number(layout.y)) ? Number(layout.y) : 1,
      plotWidth: size.width,
      plotHeight: size.height
    }};
    handle.setPointerCapture(ev.pointerId);
    handle.classList.add("is-dragging");
    ev.preventDefault();
    ev.stopPropagation();
  }});

  handle.addEventListener("pointermove", function(ev) {{
    if (!dragState) return;
    var dx = ev.clientX - dragState.startX;
    var dy = ev.clientY - dragState.startY;
    handle.style.left = Math.round(dragState.handleLeft + dx) + "px";
    handle.style.top = Math.round(dragState.handleTop + dy) + "px";
    var nextX = clamp(dragState.legendX + dx / dragState.plotWidth, -0.2, 1.4);
    var nextY = clamp(dragState.legendY - dy / dragState.plotHeight, -0.4, 1.2);
    window.Plotly.relayout(gd, {{
      "legend.x": nextX,
      "legend.y": nextY
    }}).then(scheduleHandlePosition);
    ev.preventDefault();
    ev.stopPropagation();
  }});

  function finishDrag(ev) {{
    if (!dragState) return;
    if (handle.hasPointerCapture(dragState.pointerId)) {{
      handle.releasePointerCapture(dragState.pointerId);
    }}
    dragState = null;
    handle.classList.remove("is-dragging");
    scheduleHandlePosition();
    if (ev) {{
      ev.preventDefault();
      ev.stopPropagation();
    }}
  }}

  handle.addEventListener("pointerup", finishDrag);
  handle.addEventListener("pointercancel", finishDrag);
  gd.on("plotly_afterplot", scheduleHandlePosition);
  gd.on("plotly_relayout", scheduleHandlePosition);
  gd.on("plotly_restyle", scheduleHandlePosition);
  gd.addEventListener("rist-legend-visibility-change", scheduleHandlePosition);
  gd.addEventListener("rist-plot-data-replaced", scheduleHandlePosition);
  window.addEventListener("resize", scheduleHandlePosition);
  scheduleHandlePosition();
}})();
</script>
"""


def fig_to_responsive_html(
    fig,
    *,
    div_id: str = "plot",
    origin: bool = False,
    config: dict | None = None,
    responsive_legend: bool = True,
    wide_legend_inside: bool = True,
    legend_breakpoint_px: int = LEGEND_BREAKPOINT_PX,
    crosshair: bool = False,
    title_edit: bool = False,
    trace_highlight: bool = False,
    highlight_pickable=None,
    highlight_groups=None,
    legend_text: Mapping[int | str, str] | None = None,
    legend_group_text: Mapping[str, str] | None = None,
    legend_text_edit: bool = False,
    peak_editor: bool = False,
    shape_editor: bool = False,
    image_format: str = "svg",
    image_filename: str = "plot",
    image_scale: float = 2,
    image_format_selector: bool = False,
    image_formats: tuple = ("svg", "png", "jpeg", "webp"),
    post_body_html: str = "",
    include_plotlyjs: str | bool = "cdn",
) -> str:
    """Figure를 모바일 친화적 반응형 HTML 문자열로 변환한다.

    - origin=True 면 Origin 논문 스타일을 먼저 적용한다.
    - config 는 responsive/displaylogo/edits 기본값에 병합된다.
    - responsive_legend=True 면 범례 위치 자동 전환 JS를 삽입한다.
    - legend_breakpoint_px 미만 화면에서는 범례를 하단 가로로 내린다.
      (범례 항목이 많아 우측 세로 배치 시 그래프가 너무 좌아지면 값을 키운다.)
    - crosshair=True 면 마우스 커서에 십자선 + x/y 좌표 표시기를 추가한다.
    - title_edit=True 면 제목을 더블클릭해 태그 없이 글자만 인라인 편집할 수 있다.
    - trace_highlight=True 면 트레이스를 클릭해 강조(나머지 반투명),
      같은 트레이스 더블클릭으로 격리(그 트레이스만 표시)할 수 있다.
    - legend_text 는 저장 전 범례 항목명을 trace index/name 기준으로 변경한다.
    - legend_group_text 는 저장 전 범례 그룹 제목을 legendgroup/title 기준으로 변경한다.
    - legend_text_edit=True 면 생성된 HTML에서 범례 항목을 더블클릭해 수정할 수 있다.
    - peak_editor=True 면 피크 marker/라벨/보조선을 HTML에서 추가·삭제할 수 있다.
    - shape_editor=True 면 사각형과 텍스트 박스를 그래프에 추가·편집할 수 있다.
    - image_format 은 모드바 카메라(Download plot) 버튼의 기본 저장 형식(svg/png/jpeg/webp).
    - image_filename / image_scale 은 저장 파일명·배율.
    - image_format_selector=True 면 그래프 우상단에 형식 선택 드롭다운 + 저장 버튼을 띄운다.
    - image_formats 는 드롭다운에 표시할 형식 목록.
    - post_body_html 은 </body> 직전에 추가로 삽입할 HTML(예: 표).
    - include_plotlyjs 는 Plotly JS 포함 방식 또는 로컬 자산 URL이다.
    """
    if origin:
        apply_origin_style(fig)

    if legend_text or legend_group_text:
        apply_legend_text(fig, legend_text, legend_group_text)

    if crosshair:
        apply_crosshair_spikes(fig)

    default_img_opts = {
        "format": image_format,
        "filename": image_filename,
        "scale": image_scale,
    }
    merged_config = {
        "responsive": True,
        "displaylogo": False,
        "editable": True,
        # 마우스/손가락으로 범례와 피크 라벨(annotation) 위치 조정 가능
        "edits": {
            "annotationPosition": True,
            "annotationTail": True,
            "annotationText": False,
            "axisTitleText": False,
            "colorbarPosition": False,
            "colorbarTitleText": False,
            "legendPosition": False,
            "legendText": False,
            "shapePosition": False,
            "titleText": False,
        },
        "toImageButtonOptions": default_img_opts,
    }
    if config:
        # toImageButtonOptions 는 기본값 위에 얙은 병합(호출부의 width/height 등 보존)
        if "toImageButtonOptions" in config:
            opts = dict(default_img_opts)
            opts.update(config["toImageButtonOptions"])
            config = {**config, "toImageButtonOptions": opts}
        if "edits" in config:
            edits = dict(merged_config["edits"])
            edits.update(config["edits"])
            config = {**config, "edits": edits}
        merged_config.update(config)

    html = fig.to_html(
        include_plotlyjs=include_plotlyjs,
        full_html=True,
        config=merged_config,
        default_width="100%",
        default_height="80vh",
        div_id=div_id,
    )

    html = html.replace("<head>", "<head>\n  " + _VIEWPORT_META, 1)

    if responsive_legend:
        js = _responsive_legend_js(div_id, wide_legend_inside, legend_breakpoint_px)
        html = html.replace("</body>", js + "</body>", 1)

    html = html.replace("</body>", _legend_drag_handle_js(div_id) + "</body>", 1)

    if crosshair:
        html = html.replace("</body>", _crosshair_js(div_id) + "</body>", 1)

    if title_edit:
        html = html.replace("</body>", _title_edit_js(div_id) + "</body>", 1)

    if legend_text_edit:
        html = html.replace("</body>", _legend_text_edit_js(div_id) + "</body>", 1)

    if peak_editor:
        html = html.replace("</body>", peak_editor_js(div_id) + "</body>", 1)

    if shape_editor:
        html = html.replace("</body>", shape_editor_js(div_id) + "</body>", 1)

    if title_edit or legend_text_edit or peak_editor or shape_editor:
        html = html.replace("</body>", _plot_edit_history_js(div_id) + "</body>", 1)

    if trace_highlight:
        html = html.replace(
            "</body>",
            _trace_highlight_js(div_id, highlight_pickable, highlight_groups)
            + "</body>", 1)

    if image_format_selector:
        html = html.replace(
            "</body>",
            _image_download_js(div_id, image_formats, image_filename, image_scale)
            + "</body>", 1)

    if post_body_html:
        html = html.replace("</body>", post_body_html + "</body>", 1)

    return html


def write_responsive_html(
    fig,
    out_path: str,
    *,
    div_id: str = "plot",
    origin: bool = False,
    config: dict | None = None,
    responsive_legend: bool = True,
    wide_legend_inside: bool = True,
    legend_breakpoint_px: int = LEGEND_BREAKPOINT_PX,
    crosshair: bool = False,
    title_edit: bool = False,
    trace_highlight: bool = False,
    highlight_pickable=None,
    highlight_groups=None,
    legend_text: Mapping[int | str, str] | None = None,
    legend_group_text: Mapping[str, str] | None = None,
    legend_text_edit: bool = False,
    peak_editor: bool = False,
    shape_editor: bool = False,
    image_format: str = "svg",
    image_filename: str = "plot",
    image_scale: float = 2,
    image_format_selector: bool = False,
    image_formats: tuple = ("svg", "png", "jpeg", "webp"),
    post_body_html: str = "",
    include_plotlyjs: str | bool = "cdn",
) -> str:
    """Figure를 모바일 친화적 반응형 HTML 파일로 저장하고 경로를 반환한다."""
    html = fig_to_responsive_html(
        fig,
        div_id=div_id,
        origin=origin,
        config=config,
        responsive_legend=responsive_legend,
        wide_legend_inside=wide_legend_inside,
        legend_breakpoint_px=legend_breakpoint_px,
        crosshair=crosshair,
        title_edit=title_edit,
        trace_highlight=trace_highlight,
        highlight_pickable=highlight_pickable,
        highlight_groups=highlight_groups,
        legend_text=legend_text,
        legend_group_text=legend_group_text,
        legend_text_edit=legend_text_edit,
        peak_editor=peak_editor,
        shape_editor=shape_editor,
        image_format=image_format,
        image_filename=image_filename,
        image_scale=image_scale,
        image_format_selector=image_format_selector,
        image_formats=image_formats,
        post_body_html=post_body_html,
        include_plotlyjs=include_plotlyjs,
    )
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return out_path

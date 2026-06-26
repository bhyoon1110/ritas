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
  3) 범례 드래그 이동 (config.edits.legendPosition=True)
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

  function hide() {{ box.style.display = "none"; dot.style.display = "none"; gd._snapCurve = -1; }}
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
  top: 34px;
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
  top: 66px;
  right: 30px;
  z-index: 21;
  display: none;
  width: min(360px, calc(100% - 16px));
  max-height: min(420px, calc(100% - 54px));
  overflow: auto;
  background: rgba(255,255,255,0.97);
  border: 1px solid #c7d0dd;
  border-radius: 6px;
  box-shadow: 0 4px 18px rgba(0,0,0,0.16);
  padding: 10px;
  box-sizing: border-box;
}}
#{div_id} .rist-legend-edit-head {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
  font: bold 13px Arial, sans-serif;
  color: #1f2933;
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
  align-items: center;
  margin: 6px 0;
}}
#{div_id} .rist-legend-edit-actions {{
  display: flex;
  justify-content: flex-end;
  margin-top: 10px;
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
  border: 1px solid #c7d0dd;
  border-radius: 4px;
  color: #1f2933;
  font: 12px Arial, sans-serif;
  padding: 5px 7px;
  box-sizing: border-box;
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
    var data = gd.data || [];
    var base = data[curve] || {{}};
    var legendgroup = base.legendgroup;
    var oldName = traceName(curve);
    var curves = [];
    for (var i = 0; i < data.length; i++) {{
      var tr = data[i] || {{}};
      if (legendgroup != null && legendgroup !== "") {{
        if (tr.legendgroup === legendgroup) curves.push(i);
      }} else if (i === curve) {{
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
            legendgroup: legendgroup
          }}
        }}));
      }} catch (e) {{}}
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
      + "<button type='button' class='rist-legend-edit-close' aria-label='close'>×</button>"
      + "</div><div class='rist-legend-edit-body'></div>"
      + "<div class='rist-legend-edit-actions'>"
      + "<button class='rist-legend-edit-save-all' type='button'>\uc804\uccb4 \uc800\uc7a5</button>"
      + "</div>";
    toolbar.appendChild(btn);
    gd.appendChild(panel);

    function renderRows() {{
      var body = panel.querySelector(".rist-legend-edit-body");
      var idxs = visibleLegendTraceIndexes();
      body.innerHTML = "";
      idxs.forEach(function(curve) {{
        var row = document.createElement("div");
        row.className = "rist-legend-edit-row";
        row.setAttribute("data-curve", String(curve));
        row.innerHTML = "<input class='rist-legend-edit-input' type='text'>";
        var input = row.querySelector("input");
        input.value = traceName(curve);
        input.addEventListener("keydown", function(ev) {{
          if (ev.key === "Enter") {{
            ev.preventDefault();
            saveAllRows();
          }}
        }});
        body.appendChild(row);
      }});
    }}

    function saveAllRows() {{
      panel.querySelectorAll(".rist-legend-edit-row").forEach(function(row) {{
        var curve = parseInt(row.getAttribute("data-curve"), 10);
        var input = row.querySelector(".rist-legend-edit-input");
        if (!Number.isFinite(curve) || !input) return;
        updateName(curve, input.value);
      }});
    }}

    btn.addEventListener("click", function(ev) {{
      ev.preventDefault();
      ev.stopPropagation();
      var willOpen = panel.style.display !== "block";
      if (willOpen) renderRows();
      panel.style.display = willOpen ? "block" : "none";
    }});
    panel.querySelector(".rist-legend-edit-close").addEventListener("click", function() {{
      panel.style.display = "none";
    }});
    panel.querySelector(".rist-legend-edit-save-all").addEventListener("click", saveAllRows);
    document.addEventListener("keydown", function(ev) {{
      if (ev.key === "Escape") panel.style.display = "none";
    }});
  }}

  function setAllLegendVisibility(visible) {{
    if (!window.Plotly) return;
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
  function applyLegend() {{
    if (!gd || !window.Plotly) return;
    var wide = window.innerWidth >= WIDE;
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
    image_format: str = "svg",
    image_filename: str = "plot",
    image_scale: float = 2,
    image_format_selector: bool = False,
    image_formats: tuple = ("svg", "png", "jpeg", "webp"),
    post_body_html: str = "",
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
    - image_format 은 모드바 카메라(Download plot) 버튼의 기본 저장 형식(svg/png/jpeg/webp).
    - image_filename / image_scale 은 저장 파일명·배율.
    - image_format_selector=True 면 그래프 우상단에 형식 선택 드롭다운 + 저장 버튼을 띄운다.
    - image_formats 는 드롭다운에 표시할 형식 목록.
    - post_body_html 은 </body> 직전에 추가로 삽입할 HTML(예: 표).
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
        # 마우스/손가락으로 범례를 드래그해 위치 조정 가능
        "edits": {"legendPosition": True},
        "toImageButtonOptions": default_img_opts,
    }
    if config:
        # toImageButtonOptions 는 기본값 위에 얙은 병합(호출부의 width/height 등 보존)
        if "toImageButtonOptions" in config:
            opts = dict(default_img_opts)
            opts.update(config["toImageButtonOptions"])
            config = {**config, "toImageButtonOptions": opts}
        merged_config.update(config)

    html = fig.to_html(
        include_plotlyjs="cdn",
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

    if crosshair:
        html = html.replace("</body>", _crosshair_js(div_id) + "</body>", 1)

    if title_edit:
        html = html.replace("</body>", _title_edit_js(div_id) + "</body>", 1)

    if legend_text_edit:
        html = html.replace("</body>", _legend_text_edit_js(div_id) + "</body>", 1)

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
    image_format: str = "svg",
    image_filename: str = "plot",
    image_scale: float = 2,
    image_format_selector: bool = False,
    image_formats: tuple = ("svg", "png", "jpeg", "webp"),
    post_body_html: str = "",
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
        image_format=image_format,
        image_filename=image_filename,
        image_scale=image_scale,
        image_format_selector=image_format_selector,
        image_formats=image_formats,
        post_body_html=post_body_html,
    )
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return out_path

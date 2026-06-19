# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: RIST FTIR 라이브러리를 주제·소재별로 묶어 Plotly 인터랙티브 HTML
#            뷰어(viewer.html)로 출력한다. 공통 plot_style 모듈로 반응형·범례
#            드래그·origin 스타일을 적용한다.
# 실행 방법: python plot_ftir_library.py            (기본 디자인)
#            python plot_ftir_library.py --origin   (Origin 논문 스타일)
#            (입력 manifest와 출력 경로는 상단 BASE 상수에 내장)
# ─────────────────────────────────────────────────────────────────────────────
"""
RIST FTIR 라이브러리 인터랙티브 HTML 뷰어
- Plotly 기반: 줌/패닝, 범례 클릭으로 표시/숨기기
- 소재별 그룹핑, 동일 소재 다중 측정은 같은 색
- 출력: data/RIST_FTIR_Library/viewer.html
"""
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
import os
import sys
import html as html_lib
from pathlib import Path

COMMON_DIR = Path(__file__).resolve().parents[1] / "common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from rist_common.plotting import write_responsive_html  # noqa: E402

# --origin 플래그가 있으면 Origin(OriginLab) 논문 스타일로 출력
ORIGIN = "--origin" in sys.argv

BASE = "data/RIST_FTIR_Library"
MANIFEST = os.path.join(BASE, "manifest.csv")

# ── 카테고리 메타데이터 ───────────────────────────────────────────────
CATEGORIES = {
    "01_battery": {
        "label": "🔋 Battery",
        "color_start": "#1f77b4",
        "subs": {"01_electrolyte_solvents": "Electrolyte Solvents",
                 "02_binders_polymers":    "Binders & Polymers"},
    },
    "02_steel_coating": {
        "label": "⚙️ Steel Coating",
        "color_start": "#d62728",
        "subs": {"01_corrosion_inhibitors": "Corrosion Inhibitors",
                 "02_lubricants":           "Lubricants",
                 "03_coatings_resins":      "Coatings & Resins"},
    },
    "03_engineering_plastic": {
        "label": "🧱 Engineering Plastic",
        "color_start": "#2ca02c",
        "subs": {"01_commodity":    "Commodity",
                 "02_engineering":  "Engineering",
                 "03_bioplastics":  "Bioplastics"},
    },
    "04_elastomers_seals": {
        "label": "⭕ Elastomers & Seals",
        "color_start": "#ff7f0e",
        "subs": {},
    },
    "05_ceramic_inorganic": {
        "label": "🏺 Ceramic / Inorganic",
        "color_start": "#9467bd",
        "subs": {},
    },
    "06_natural_fibers": {
        "label": "🌿 Natural Fibers",
        "color_start": "#17becf",
        "subs": {},
    },
}

# ── 색상 팔레트 (카테고리별 HSL gradient) ──────────────────────────────
import colorsys

def gen_palette(n, hue, s=0.65, l_start=0.35, l_end=0.75):
    """hue(0-1) 기준으로 n가지 밝기 변화 색 생성"""
    colors = []
    for i in range(max(n, 1)):
        l = l_start + (l_end - l_start) * i / max(n - 1, 1)
        r, g, b = colorsys.hls_to_rgb(hue, l, s)
        colors.append(f"rgb({int(r*255)},{int(g*255)},{int(b*255)})")
    return colors

CATEGORY_HUES = {
    "01_battery":             0.58,   # 파란 계열
    "02_steel_coating":       0.02,   # 빨간 계열
    "03_engineering_plastic": 0.35,   # 초록 계열
    "04_elastomers_seals":    0.08,   # 주황 계열
    "05_ceramic_inorganic":   0.75,   # 보라 계열
    "06_natural_fibers":      0.48,   # 청록(시안) 계열
}

def load_spectrum(path):
    df = pd.read_csv(path, comment="#")
    df.columns = [c.strip() for c in df.columns]
    wn_col = [c for c in df.columns if "wave" in c.lower() or "wn" in c.lower()][0]
    ab_col = [c for c in df.columns if "abs" in c.lower() or "int" in c.lower()][0]
    df = df[[wn_col, ab_col]].apply(pd.to_numeric, errors="coerce").dropna()
    df = df[(df[wn_col] >= 650) & (df[wn_col] <= 4000)]
    df = df.sort_values(wn_col)
    return df[wn_col].values, df[ab_col].values

def normalize(y):
    mn, mx = y.min(), y.max()
    if mx - mn < 1e-10:
        return y
    return (y - mn) / (mx - mn)

def average_spectra(spectra, step=2.0):
    """동일 소재의 여러 (wn, ab_norm) 측정을 공통 파수 그리드에 보간 후 평균낸다.

    - 각 측정의 겹치는 파수 구간(max(min)~min(max))에서만 평균한다.
    - 겹치는 구간이 없으면 점이 가장 많은 측정 하나를 그대로 반환한다.
    - 반환: (공통 파수, 평균 흡광도), 평균에 사용된 측정 개수
    """
    if len(spectra) == 1:
        wn, ab = spectra[0]
        return wn, ab, 1

    lo = max(wn.min() for wn, _ in spectra)
    hi = min(wn.max() for wn, _ in spectra)
    if hi - lo < step:
        # 겹치는 구간이 없으면 점이 가장 많은 측정 사용
        wn, ab = max(spectra, key=lambda s: len(s[0]))
        return wn, ab, 1

    grid = np.arange(np.ceil(lo), np.floor(hi) + 1e-9, step)
    stack = np.vstack([np.interp(grid, wn, ab) for wn, ab in spectra])
    return grid, stack.mean(axis=0), len(spectra)

# ── 데이터 로드 ──────────────────────────────────────────────────────
def main():
    print("스펙트럼 로드 중...")
    df_mani = pd.read_csv(MANIFEST)
    df_mani["category"] = df_mani["file"].apply(lambda x: x.split("/")[0])
    df_mani["subcategory"] = df_mani["file"].apply(
        lambda x: x.split("/")[1] if len(x.split("/")) > 2 else "")

    # 소재별로 파일 그룹핑
    mat_groups = {}
    for _, row in df_mani.iterrows():
        key = (row["category"], row["material"])
        mat_groups.setdefault(key, []).append(row)

    # ── Plotly Figure 생성 ────────────────────────────────────────────────
    fig = go.Figure()

    # 카테고리별 색상 팔레트 미리 생성
    cat_mats = {}
    for (cat, mat), _ in mat_groups.items():
        cat_mats.setdefault(cat, set()).add(mat)
    cat_palettes = {cat: gen_palette(len(mats), CATEGORY_HUES[cat])
                    for cat, mats in cat_mats.items()}
    cat_mat_color = {}
    for cat, mats in cat_mats.items():
        palette = cat_palettes[cat]
        for i, mat in enumerate(sorted(mats)):
            cat_mat_color[(cat, mat)] = palette[i % len(palette)]

    trace_count = 0
    # 좁은 화면 커스텀 범례용: 표시그룹 -> [(소재명, 색, [trace 인덱스...]), ...]
    legend_data = {}
    # 좁은 화면 카테고리 필터용: 카테고리코드 -> [trace 인덱스...]
    category_indices = {}
    for (cat, mat), rows in sorted(mat_groups.items()):
        color = cat_mat_color[(cat, mat)]
        cat_label = CATEGORIES[cat]["label"]
        # 서브카테고리가 섞여 있으면 가장 빈번한 것을 범례 그룹으로 사용
        _subs = [r["subcategory"] for r in rows]
        sub = max(set(_subs), key=_subs.count) if _subs else ""
        sub_label = CATEGORIES[cat]["subs"].get(sub, "")
        legend_group = f"{cat_label}" + (f" / {sub_label}" if sub_label else "")

        mat_indices = []
        spectra = []          # 평균 병합용 (wn, ab_norm) 목록
        src_set = []          # 출처 표시용
        for row in rows:
            fpath = os.path.join(BASE, row["file"])
            try:
                wn, ab = load_spectrum(fpath)
            except Exception as e:
                print(f"  ⚠ 로드 실패: {row['file']} ({e})")
                continue
            spectra.append((wn, normalize(ab)))
            if row["source"] not in src_set:
                src_set.append(row["source"])

        if spectra:
            # 동일 소재 다중 측정은 공통 그리드에 보간 후 평균하여 1개 트레이스로 병합
            wn_avg, ab_avg, n_used = average_spectra(spectra)

            merged_note = (f"<br>Merged: {n_used} measurements (avg)"
                           if n_used > 1 else "")
            hover = (f"<b>{mat}</b><br>"
                     f"Category: {cat_label}<br>"
                     f"Source: {', '.join(str(s) for s in src_set)}<br>"
                     f"Spectra: {len(spectra)}{merged_note}<br>"
                     f"Range: {wn_avg.min():.0f}–{wn_avg.max():.0f} cm⁻¹"
                     f"<extra></extra>")

            fig.add_trace(go.Scatter(
                x=wn_avg,
                y=ab_avg,
                mode="lines",
                name=mat,
                legendgroup=legend_group,
                legendgrouptitle_text=legend_group if trace_count == 0 or
                                       not any(t.legendgroup == legend_group
                                               for t in fig.data[:-1]) else None,
                line=dict(color=color, width=1.2),
                opacity=0.85,
                showlegend=True,
                hovertemplate=hover,
                meta={"category": cat, "material": mat},
            ))
            mat_indices.append(trace_count)
            trace_count += 1

        if mat_indices:
            legend_data.setdefault(legend_group, []).append((mat, color, mat_indices))
            category_indices.setdefault(cat, []).extend(mat_indices)

    print(f"  → {trace_count}개 트레이스 생성")

    # ── 레이아웃 ─────────────────────────────────────────────────────────
    fig.update_layout(
        title=dict(
            text="RIST FTIR Spectral Library  <span style='font-size:14px;color:#888'>"
                 f"| {len(df_mani)}개 스펙트럼 · {df_mani['material'].nunique()}종 소재</span>",
            font=dict(size=20),
            x=0.01,
            y=0.97,
            yref="container",
            yanchor="top",
        ),
        xaxis=dict(
            title="Wavenumber (cm⁻¹)",
            range=[4000, 650],          # 역방향 (FTIR 관례)
            showgrid=True,
            gridcolor="#e8e8e8",
            gridwidth=0.5,
            tickmode="linear",
            dtick=500,
            minor=dict(ticks="inside", ticklen=3, showgrid=True, gridcolor="#f2f2f2"),
        ),
        yaxis=dict(
            title="Normalized Absorbance (offset)",
            showgrid=True,
            gridcolor="#e8e8e8",
            gridwidth=0.5,
            zeroline=False,
        ),
        legend=dict(
            title="<b>소재 (범례 클릭 = 표시/숨기기)</b>",
            groupclick="toggleitem",    # 개별 항목 토글
            tracegroupgap=8,
            font=dict(size=11),
            bgcolor="rgba(255,255,255,0.9)",
            bordercolor="#ccc",
            borderwidth=1,
            itemclick="toggle",
            itemdoubleclick="toggleothers",  # 더블클릭 = 해당 소재만 표시
        ),
        hovermode="x unified",
        plot_bgcolor="white",
        paper_bgcolor="#fafafa",
        height=700,
        margin=dict(l=60, r=20, t=150, b=60),
        font=dict(family="Arial, sans-serif"),
    )

    # ── 버튼: 카테고리별 일괄 토글 ────────────────────────────────────────
    buttons = [dict(
        label="전체 표시",
        method="restyle",
        args=[{"visible": True}],
    )]
    for cat, info in CATEGORIES.items():
        visible = []
        for t in fig.data:
            visible.append(True if t.meta and t.meta.get("category") == cat else "legendonly")
        buttons.append(dict(
            label=info["label"],
            method="restyle",
            args=[{"visible": visible}],
        ))
    buttons.append(dict(
        label="전체 숨기기",
        method="restyle",
        args=[{"visible": "legendonly"}],
    ))

    fig.update_layout(
        updatemenus=[dict(
            type="buttons",
            direction="left",
            buttons=buttons,
            pad=dict(r=10, t=10),
            showactive=True,
            x=0.01,
            xanchor="left",
            y=0.94,
            yanchor="bottom",
            bgcolor="white",
            bordercolor="#ccc",
            font=dict(size=12),
        )],
    )

    # ── 좁은 화면용 커스텀 범례(그래프 아래 스크롤 영역) HTML 생성 ─────────────
    LEGEND_BREAKPOINT_PX = 1100

    _legend_rows = ['<div class="cl-title">소재 (클릭 = 표시/숨기기)</div>']
    for group_label, items in legend_data.items():
        _legend_rows.append(
            f'<div class="cl-group-title">{html_lib.escape(group_label)}</div>')
        for mat, color, indices in items:
            idx_attr = ",".join(str(x) for x in indices)
            _legend_rows.append(
                f'<div class="cl-item" data-traces="{idx_attr}">'
                f'<span class="cl-sw" style="background:{color}"></span>'
                f'<span class="cl-label">{html_lib.escape(str(mat))}</span></div>')
    _legend_inner = "\n".join(_legend_rows)

    # 좁은 화면용 카테고리 필터 버튼 (Plotly updatemenus 대체)
    _cat_btns = ['<button class="cl-cat-btn" data-cat="__all__">전체 표시</button>']
    for cat, info in CATEGORIES.items():
        if cat in category_indices:
            _cat_btns.append(
                f'<button class="cl-cat-btn" data-cat="{cat}">'
                f'{html_lib.escape(info["label"])}</button>')
    _cat_btns.append('<button class="cl-cat-btn" data-cat="__none__">전체 숨기기</button>')
    _cat_btns_html = "\n".join(_cat_btns)
    _category_indices_json = json.dumps(category_indices)

    custom_legend_html = f"""
    <style>
      #custom-legend {{
        display: none;
        max-width: 960px;
        margin: 6px auto 28px;
        padding: 10px 14px;
        border: 1px solid #ccc;
        border-radius: 8px;
        background: #fff;
        max-height: 42vh;
        overflow-y: auto;
        font-family: Arial, sans-serif;
        box-sizing: border-box;
      }}
      #custom-legend .cl-title {{ font-weight: bold; font-size: 14px; margin-bottom: 6px; }}
      #custom-legend .cl-group-title {{
        font-weight: bold; font-size: 13px; color: #333;
        margin: 10px 0 4px; padding-top: 6px; border-top: 1px solid #eee;
      }}
      #custom-legend .cl-item {{
        display: flex; align-items: center; gap: 8px;
        padding: 3px 4px; font-size: 13px; cursor: pointer; border-radius: 4px;
      }}
      #custom-legend .cl-item:hover {{ background: #f2f6ff; }}
      #custom-legend .cl-sw {{
        display: inline-block; width: 22px; height: 3px;
        border-radius: 2px; flex: none;
      }}
      #custom-legend .cl-off {{ opacity: 0.4; text-decoration: line-through; }}
      #custom-legend .cl-label {{ line-height: 1.3; }}
      #cl-catbar {{
        display: none;
        max-width: 960px;
        margin: 4px auto 0;
        padding: 0 14px;
        flex-wrap: wrap;
        gap: 6px;
        box-sizing: border-box;
      }}
      #cl-catbar .cl-cat-btn {{
        font-family: Arial, sans-serif;
        font-size: 12px;
        padding: 6px 10px;
        border: 1px solid #ccc;
        border-radius: 6px;
        background: #fff;
        cursor: pointer;
      }}
      #cl-catbar .cl-cat-btn:hover {{ background: #f2f6ff; }}
      #cl-catbar .cl-cat-btn:active {{ background: #e3ecff; }}
    </style>
    <div id="cl-catbar">
    {_cat_btns_html}
    </div>
    <div id="custom-legend">
    {_legend_inner}
    </div>
    <script>
    (function() {{
      var WIDE = {LEGEND_BREAKPOINT_PX};
      var CAT_INDICES = {_category_indices_json};
      var gd = document.getElementById("lib-plot");
      var legend = document.getElementById("custom-legend");
      var catbar = document.getElementById("cl-catbar");
      if (!gd || !legend) return;

      // 그래프 가로:세로 비율(세로/가로). 좁은 화면에서 그래프 폭에 맞춰 높이를
      // 계산해 일정한 비율을 유지한다. (예: 0.8 → 가로가 100이면 세로 80)
      var ASPECT = 0.8;

      function narrowHeight() {{
        var w = gd.getBoundingClientRect().width || window.innerWidth;
        var h = Math.round(w * ASPECT);
        // 화면을 벗어나지 않게 상·하한을 둔다.
        var maxH = Math.round(window.innerHeight * 0.85);
        return Math.max(300, Math.min(h, maxH));
      }}

      function syncLegendOff() {{
        // 커스텀 범례 항목의 취소선 상태를 현재 트레이스 visible과 동기화
        var items = legend.querySelectorAll(".cl-item");
        items.forEach(function(item) {{
          var traces = item.getAttribute("data-traces").split(",").map(Number);
          var v = gd.data[traces[0]].visible;
          var on = (v === true || v === undefined);
          item.classList.toggle("cl-off", !on);
        }});
      }}

      function apply() {{
        if (!window.Plotly) return;
        var wide = window.innerWidth >= WIDE;
        var layout;
        if (wide) {{
          legend.style.display = "none";
          catbar.style.display = "none";
          layout = {{
            "showlegend": true,
            "updatemenus[0].visible": true,
            "legend.orientation": "v",
            "legend.x": 1.02, "legend.xanchor": "left",
            "legend.y": 1.0, "legend.yanchor": "top",
            "margin.r": 180, "margin.l": 60, "margin.b": 60, "margin.t": 70
          }};
        }} else {{
          // 좁은 화면: 내장 범례 + Plotly 필터 버튼(updatemenus)을 끈다.
          // (가로로 긴 버튼 바가 오른쪽 마진을 잡아먹어 그래프 폭을 찌그러뜨리기 때문)
          // 범례/카테고리 필터는 아래 HTML 영역에서 조작한다.
          layout = {{
            "showlegend": false,
            "updatemenus[0].visible": false,
            "margin.r": 14, "margin.l": 55, "margin.b": 50, "margin.t": 70
          }};
        }}
        // autosize를 켜고 width/height를 레이아웃에서 제거 → 그래프가 컨테이너 폭(100%)을
        // 항상 채우도록 한다. 높이는 컨테이너(div) 스타일로 제어한다.
        layout["autosize"] = true;
        layout["width"] = null;
        layout["height"] = null;
        window.Plotly.relayout(gd, layout).then(function() {{
          gd.style.width = "100%";
          gd.style.height = (wide ? 700 : narrowHeight()) + "px";
          window.Plotly.Plots.resize(gd);
          if (!wide) {{
            legend.style.display = "block";
            catbar.style.display = "flex";
          }}
        }});
      }}

      function init() {{
        if (window.Plotly) {{
          apply();
          // 공통 모듈의 강조/격리(plotly restyle) 등 모든 visible 변경 후
          // 커스텀 범례의 취소선 상태를 자동 동기화한다.
          gd.on("plotly_restyle", syncLegendOff);
          syncLegendOff();
        }}
        else {{ setTimeout(init, 60); }}
      }}
      init();
      window.addEventListener("resize", apply);

      // 커스텀 범례 항목 클릭 → 해당 소재의 모든 트레이스 토글
      legend.addEventListener("click", function(e) {{
        var item = e.target.closest(".cl-item");
        if (!item || !window.Plotly) return;
        var traces = item.getAttribute("data-traces").split(",").map(Number);
        var cur = gd.data[traces[0]].visible;
        var on = (cur === true || cur === undefined);
        window.Plotly.restyle(gd, {{ "visible": on ? "legendonly" : true }}, traces);
        item.classList.toggle("cl-off", on);
      }});

      // 카테고리 필터 버튼
      catbar.addEventListener("click", function(e) {{
        var btn = e.target.closest(".cl-cat-btn");
        if (!btn || !window.Plotly) return;
        var cat = btn.getAttribute("data-cat");
        if (cat === "__all__") {{
          window.Plotly.restyle(gd, {{ "visible": true }}).then(syncLegendOff);
        }} else if (cat === "__none__") {{
          window.Plotly.restyle(gd, {{ "visible": "legendonly" }}).then(syncLegendOff);
        }} else {{
          // 해당 카테고리만 표시, 나머지는 숨김
          var vis = gd.data.map(function(t) {{
            return (t.meta && t.meta.category === cat) ? true : "legendonly";
          }});
          window.Plotly.restyle(gd, {{ "visible": vis }}).then(syncLegendOff);
        }}
      }});
    }})();
    </script>
    """

    # ── HTML 저장 ────────────────────────────────────────────────────────
    out_path = os.path.join(BASE, "viewer.html")
    write_responsive_html(
        fig,
        out_path,
        div_id="lib-plot",
        origin=ORIGIN,
        responsive_legend=False,         # 범례 위치/표시는 아래 커스텀 JS가 전담
        crosshair=True,                  # 마우스 위치 x/y 좌표 + 십자선 표시
        title_edit=True,                 # 제목 더블클릭 인라인 편집
        trace_highlight=True,            # 그래프 클릭=강조, 더블클릭=격리
        image_format="svg",              # Download plot 기본 저장 형식
        image_filename="rist_ftir_library",
        image_format_selector=True,      # 그래프 우상단에 형식 선택 드롭다운 표시
        post_body_html=custom_legend_html,
        config={
            "scrollZoom": True,
            "displayModeBar": True,
            "editable": False,
            "edits": {"titleText": False},   # 네이티브 편집 비활성(태그 노출·단일클릭 방지). 아래 커스텀 JS가 더블클릭 편집 담당
            "modeBarButtonsToRemove": ["select2d", "lasso2d"],
            "toImageButtonOptions": {
                "height": 800,
                "width": 1400,
            },
        },
    )

    size_kb = os.path.getsize(out_path) / 1024
    print(f"\n✅ 저장 완료: {out_path}")
    print(f"   파일 크기: {size_kb:.0f} KB")
    print(f"   브라우저에서 열기: open '{out_path}'")


if __name__ == "__main__":
    main()

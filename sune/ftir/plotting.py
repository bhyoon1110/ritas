# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: FTIR 분석용 Plotly 그림 생성기(전처리/피크/막대/비교).
#            공통 plot_style 모듈의 스타일·출력 헬퍼와 연계된다.
# 실행 방법: 모듈 — 직접 실행하지 않고 import해서 사용 (ftir_analyze.py / ftir.cli 를 통해 동작)
# ─────────────────────────────────────────────────────────────────────────────
"""Plotly figure builders for FTIR analysis (preprocess / peaks / bar / comparison)."""

import os
import json

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy.signal import find_peaks
from rist_common.plotting import peak_editor_js

from .findings import assign_group
from .peaks import build_interactive_peak_candidates
from .preprocess import load_csv, preprocess

SAMPLE_PALETTE = [
    "#2563eb", "#dc2626", "#16a34a", "#ea580c", "#7c3aed",
    "#0891b2", "#be123c", "#4d7c0f", "#9333ea", "#0f766e",
]


def _transmittance_percent(absorbance):
    values = np.asarray(absorbance, dtype=float)
    return 100.0 * np.power(10.0, -values)


def _merge_trace_meta(trace, values):
    meta = trace.meta if isinstance(trace.meta, dict) else {}
    trace.meta = {**meta, **values}


def _enable_abs_trans_toggle(trace, absorbance_y, *, absorbance_offset=0.0,
                             transmittance_offset=0.0):
    abs_values = np.asarray(absorbance_y, dtype=float)
    base_abs = abs_values - absorbance_offset
    trans_values = _transmittance_percent(base_abs) + transmittance_offset
    _merge_trace_meta(trace, {
        "ftir_signal_toggle": {
            "absorbance_y": abs_values.tolist(),
            "transmittance_y": trans_values.tolist(),
        }
    })
    return trace


def _peak_label_text(wn, label):
    return (
        f"<b>{wn:.0f}</b><br>"
        f"<span style='font-size:10px'>{label}</span>"
    )


def _sample_key(index):
    return f"sample:{index}"


def ftir_peak_label_sync_js(div_id: str) -> str:
    """Backward-compatible wrapper for the shared peak editor."""
    return peak_editor_js(div_id)


def ftir_abs_trans_toggle_js(div_id: str, *, yaxis_titles: dict[str, dict[str, str]]) -> str:
    """FT-IR HTML 그래프에서 흡광도/투과도 표시를 전환하는 JS 스니펫."""
    titles_json = json.dumps(yaxis_titles, ensure_ascii=False)
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
#{div_id} .rist-ftir-unit-toggle {{
  order: 30;
  border: 1px solid #c7d0dd;
  border-radius: 4px;
  background: rgba(255,255,255,0.92);
  color: #1f2933;
  cursor: pointer;
  font: 12px Arial, sans-serif;
  padding: 5px 9px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.08);
}}
</style>
<script>
(function() {{
  var gd = document.getElementById("{div_id}");
  if (!gd) return;
  var TITLES = {titles_json};
  var mode = "absorbance";

  function tracesWithToggle() {{
    var data = gd.data || [];
    var out = [];
    for (var i = 0; i < data.length; i++) {{
      var meta = data[i] && data[i].meta && data[i].meta.ftir_signal_toggle;
      if (meta && meta.absorbance_y && meta.transmittance_y) {{
        out.push([i, meta]);
      }}
    }}
    return out;
  }}

  function applyMode(nextMode) {{
    if (!window.Plotly) return;
    if (!gd._ristFtirUnitOriginalShapes) {{
      gd._ristFtirUnitOriginalShapes = (gd.layout.shapes || []).slice();
      gd._ristFtirUnitOriginalAnnotations = (gd.layout.annotations || []).map(function(a) {{
        return Object.assign({{}}, a);
      }});
    }}
    var pairs = tracesWithToggle();
    var indexes = [];
    var ys = [];
    pairs.forEach(function(pair) {{
      indexes.push(pair[0]);
      ys.push(nextMode === "transmittance"
        ? pair[1].transmittance_y
        : pair[1].absorbance_y);
    }});
    var restyle = ys.length ? window.Plotly.restyle(gd, {{ y: ys }}, indexes) : Promise.resolve();
    restyle.then(function() {{
      var layout = {{}};
      Object.keys(TITLES).forEach(function(axis) {{
        var t = TITLES[axis] || {{}};
        layout[axis + ".title.text"] = nextMode === "transmittance"
          ? t.transmittance
          : t.absorbance;
      }});
      if (nextMode === "transmittance") {{
        layout.shapes = [];
        layout.annotations = [];
      }} else {{
        layout.shapes = gd._ristFtirUnitOriginalShapes || [];
        layout.annotations = gd._ristFtirUnitOriginalAnnotations || [];
      }}
      return window.Plotly.relayout(gd, layout);
    }});
    mode = nextMode;
    btn.textContent = nextMode === "transmittance" ? "흡광도 보기" : "투과도 보기";
  }}

  var btn = document.createElement("button");
  btn.type = "button";
  btn.className = "rist-ftir-unit-toggle";
  btn.textContent = "투과도 보기";
  btn.addEventListener("click", function(ev) {{
    ev.preventDefault();
    ev.stopPropagation();
    applyMode(mode === "absorbance" ? "transmittance" : "absorbance");
  }});
  if (getComputedStyle(gd).position === "static") gd.style.position = "relative";
  var toolbar = gd.querySelector(".rist-plot-control-row");
  if (!toolbar) {{
    toolbar = document.createElement("div");
    toolbar.className = "rist-plot-control-row";
    gd.appendChild(toolbar);
  }}
  toolbar.appendChild(btn);

  function alignToolbarWithLegend() {{
    var legend = gd.querySelector(".legend");
    if (!legend) {{
      toolbar.style.right = "30px";
      return;
    }}
    var gdRect = gd.getBoundingClientRect();
    var legendRect = legend.getBoundingClientRect();
    var right = Math.max(8, Math.round(gdRect.right - legendRect.right));
    toolbar.style.right = right + "px";
  }}

  requestAnimationFrame(alignToolbarWithLegend);
  gd.on("plotly_afterplot", alignToolbarWithLegend);
  window.addEventListener("resize", alignToolbarWithLegend);
}})();
</script>
"""


def build_preprocess_fig(raw, sample_vec, grid, sample_label, wn_min, wn_max):
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        subplot_titles=["원시 스펙트럼 (Raw)", "전처리 후 (정규화)"],
        vertical_spacing=0.12,
    )
    fig.add_trace(
        _enable_abs_trans_toggle(
            go.Scatter(x=raw["wn"], y=raw["y"], mode="lines",
                       line=dict(color="#555", width=1), name="Raw"),
            raw["y"],
        ),
        row=1, col=1,
    )
    fig.add_trace(
        _enable_abs_trans_toggle(
            go.Scatter(x=grid, y=sample_vec, mode="lines",
                       line=dict(color="#2563eb", width=1.5), name="Preprocessed"),
            sample_vec,
        ),
        row=2, col=1,
    )
    fig.update_layout(
        title=f"전처리 확인 — {sample_label}",
        height=500, plot_bgcolor="white", paper_bgcolor="#fafafa",
        showlegend=False,
    )
    fig.update_xaxes(range=[wn_max, wn_min], title_text="Wavenumber (cm⁻¹)", row=2, col=1)
    fig.update_yaxes(title_text="Absorbance", showgrid=True, gridcolor="#e8e8e8", row=1, col=1)
    fig.update_yaxes(title_text="Normalized Absorbance", showgrid=True,
                     gridcolor="#e8e8e8", row=2, col=1)
    return fig


def build_peak_fig(
    sample_vec,
    grid,
    peak_idx,
    peak_wn,
    peak_val,
    peak_fwhm,
    func_groups,
    sample_label,
    wn_min,
    wn_max,
    initial_sensitivity="medium",
):
    fig = go.Figure()
    sample_key = _sample_key(0)
    raw_trace = _enable_abs_trans_toggle(
        go.Scatter(
            x=grid, y=sample_vec, mode="lines", name=sample_label,
            legendgroup=sample_key,
            legendgrouptitle_text=sample_label,
            line=dict(color="#374151", width=1.8),
            hovertemplate="%{x:.1f} cm⁻¹ | %{y:.4f}<extra></extra>",
        ),
        sample_vec,
    )
    _merge_trace_meta(raw_trace, {
        "rist_sample_group": sample_key,
        "rist_sample_parent": True,
        "rist_legend_edit_group": sample_key,
    })
    fig.add_trace(raw_trace)

    candidates = build_interactive_peak_candidates(
        sample_vec,
        grid,
        peak_idx,
        peak_wn,
        peak_val,
        peak_fwhm,
        initial_sensitivity=initial_sensitivity,
    )
    top_peak_indexes = {
        candidate["index"]
        for candidate in sorted(
            candidates,
            key=lambda item: -item["value"],
        )[:25]
    }
    annotations = []
    peak_labels = []
    seen_legendgroups = set()

    for candidate in candidates:
        wn = candidate["wn"]
        val = candidate["value"]
        fwhm = candidate["fwhm"]
        initially_visible = candidate["initial"]
        group_name, color, note = assign_group(wn, func_groups)
        unknown = group_name == "unknown"
        legendgroup = f"unknown:{wn:.1f}" if unknown else group_name
        label_key = f"{sample_key}:peak:{legendgroup}"
        display_name = f"{wn:.0f} cm⁻¹" if unknown else group_name
        trace_index = len(fig.data)
        peak_trace = _enable_abs_trans_toggle(
            go.Scatter(
                x=[wn], y=[val], mode="markers",
                marker=dict(color=color, size=9, symbol="circle",
                            line=dict(color="white", width=1.5)),
                name=display_name,
                legendgroup=legendgroup,
                visible=initially_visible,
                showlegend=initially_visible and legendgroup not in seen_legendgroups,
                hovertemplate=(
                    f"<b>{wn:.1f} cm⁻¹</b><br>{display_name}<br>"
                    f"Value: %{{y:.4f}}<br>FWHM: {fwhm:.1f} cm⁻¹<br>"
                    f"<i>{note}</i><extra></extra>"
                ),
            ),
            [val],
        )
        _merge_trace_meta(peak_trace, {
            "rist_sample_group": sample_key,
            "rist_legend_edit_group": label_key,
            "rist_peak": {
                "source": "detected",
                "x": float(wn),
                "label": display_name,
                "sample_group": sample_key,
                "label_key": label_key,
                "sensitivity_levels": candidate["levels"],
                "sensitivity_min": candidate["sensitivity_min"],
            }
        })
        fig.add_trace(peak_trace)
        if initially_visible:
            seen_legendgroups.add(legendgroup)

        if candidate["index"] in top_peak_indexes:
            y_label = val + 0.07 + (0.07 if len(annotations) % 2 == 0 else 0.0)
            annotation_index = len(annotations)
            shape_index = len(fig.layout.shapes)
            annotations.append(dict(
                x=wn, y=y_label, text=_peak_label_text(wn, display_name),
                showarrow=True, captureevents=True,
                arrowhead=0, arrowcolor=color, arrowwidth=1,
                ax=0, ay=-28, font=dict(size=9, color=color),
                bgcolor="rgba(255,255,255,0.88)",
                bordercolor=color, borderwidth=1, borderpad=2,
                name=f"ftir_peak_label_{annotation_index}",
                visible=initially_visible,
            ))
            peak_labels.append({
                "annotationIndex": annotation_index,
                "shapeIndex": shape_index,
                "traceIndex": trace_index,
                "legendgroup": legendgroup,
                "labelKey": label_key,
                "wnText": f"{wn:.0f}",
            })
            fig.add_shape(type="line", x0=wn, x1=wn, y0=0, y1=val,
                          line=dict(color=color, width=0.8, dash="dot"),
                          visible=initially_visible)

    fig.update_layout(
        title=dict(
            text=f"FTIR Peak Analysis — {sample_label}",
            font=dict(size=18),
            x=0.01,
            y=0.98,
            yanchor="top",
        ),
        xaxis=dict(
            title="Wavenumber (cm⁻¹)", range=[wn_max, wn_min],
            showgrid=True, gridcolor="#e8e8e8",
            tickmode="linear", dtick=500,
            minor=dict(showgrid=True, gridcolor="#f4f4f4"),
        ),
        yaxis=dict(
            title="Normalized Absorbance", showgrid=True, gridcolor="#e8e8e8",
            range=[-0.05, max(peak_val) * 1.6 if len(peak_val) else 1.3],
        ),
        annotations=annotations,
        legend=dict(
            orientation="h", x=0.5, xanchor="center", y=-0.18, yanchor="top",
            itemclick="toggle", itemdoubleclick="toggleothers",
            bgcolor="rgba(255,255,255,0.9)",
            bordercolor="#ccc", borderwidth=1, font=dict(size=10),
            itemsizing="constant", tracegroupgap=2,
        ),
        plot_bgcolor="white", paper_bgcolor="#fafafa",
        height=620, hovermode="closest",
        margin=dict(l=70, r=30, t=100, b=125),
        meta={"ristPeakLabels": peak_labels},
    )
    return fig


def build_multi_peak_fig(
    samples,
    func_groups,
    wn_min,
    wn_max,
    initial_sensitivity="medium",
):
    """여러 FT-IR 시료의 raw/preprocessed trace와 피크를 한 그래프에 그린다."""
    fig = go.Figure()
    annotations = []
    peak_labels = []
    max_y = 1.0

    for sample_no, sample in enumerate(samples):
        sample_key = _sample_key(sample_no)
        label = sample["label"]
        grid = sample["grid"]
        sample_vec = sample["sample_vec"]
        color = SAMPLE_PALETTE[sample_no % len(SAMPLE_PALETTE)]
        max_y = max(max_y, float(np.nanmax(sample_vec)) if len(sample_vec) else 1.0)

        raw_trace = _enable_abs_trans_toggle(
            go.Scatter(
                x=grid, y=sample_vec, mode="lines", name=label,
                legendgroup=sample_key,
                legendgrouptitle_text=label,
                line=dict(color=color, width=1.8),
                hovertemplate=f"<b>{label}</b><br>%{{x:.1f}} cm⁻¹ | %{{y:.4f}}<extra></extra>",
            ),
            sample_vec,
        )
        _merge_trace_meta(raw_trace, {
            "rist_sample_group": sample_key,
            "rist_sample_parent": True,
            "rist_legend_edit_group": sample_key,
        })
        fig.add_trace(raw_trace)

        peak_wn = sample["peak_wn"]
        peak_val = sample["peak_val"]
        peak_fwhm = sample["peak_fwhm"]
        candidates = build_interactive_peak_candidates(
            sample_vec,
            grid,
            sample["peak_idx"],
            peak_wn,
            peak_val,
            peak_fwhm,
            initial_sensitivity=initial_sensitivity,
        )
        top_peak_indexes = {
            candidate["index"]
            for candidate in sorted(
                candidates,
                key=lambda item: -item["value"],
            )[:25]
        }
        seen_label_keys = set()

        for peak_no, candidate in enumerate(candidates):
            wn = candidate["wn"]
            val = candidate["value"]
            fwhm = candidate["fwhm"]
            initially_visible = candidate["initial"]
            group_name, peak_color, note = assign_group(wn, func_groups)
            unknown = group_name == "unknown"
            local_group = f"unknown:{wn:.1f}" if unknown else group_name
            label_key = f"{sample_key}:peak:{local_group}"
            display_name = f"{wn:.0f} cm⁻¹" if unknown else group_name
            trace_index = len(fig.data)
            peak_trace = _enable_abs_trans_toggle(
                go.Scatter(
                    x=[wn], y=[val], mode="markers",
                    marker=dict(color=peak_color, size=8, symbol="circle",
                                line=dict(color="white", width=1.5)),
                    name=display_name,
                    legendgroup=sample_key,
                    visible=initially_visible,
                    showlegend=initially_visible and label_key not in seen_label_keys,
                    hovertemplate=(
                        f"<b>{label}</b><br>{wn:.1f} cm⁻¹<br>{display_name}<br>"
                        f"Value: %{{y:.4f}}<br>FWHM: {fwhm:.1f} cm⁻¹<br>"
                        f"<i>{note}</i><extra></extra>"
                    ),
                ),
                [val],
            )
            _merge_trace_meta(peak_trace, {
                "rist_sample_group": sample_key,
                "rist_legend_edit_group": label_key,
                "rist_peak": {
                    "source": "detected",
                    "x": float(wn),
                    "label": display_name,
                    "sample_group": sample_key,
                    "label_key": label_key,
                    "sensitivity_levels": candidate["levels"],
                    "sensitivity_min": candidate["sensitivity_min"],
                },
            })
            fig.add_trace(peak_trace)
            if initially_visible:
                seen_label_keys.add(label_key)

            if candidate["index"] in top_peak_indexes:
                y_label = val + 0.06 + (0.05 if (len(annotations) + peak_no) % 2 == 0 else 0.0)
                annotation_index = len(annotations)
                shape_index = len(fig.layout.shapes)
                annotations.append(dict(
                    x=wn, y=y_label, text=_peak_label_text(wn, display_name),
                    showarrow=True, captureevents=True,
                    arrowhead=0, arrowcolor=peak_color, arrowwidth=1,
                    ax=0, ay=-28, font=dict(size=9, color=peak_color),
                    bgcolor="rgba(255,255,255,0.88)",
                    bordercolor=peak_color, borderwidth=1, borderpad=2,
                    name=f"ftir_peak_label_{sample_no}_{annotation_index}",
                    visible=initially_visible,
                ))
                peak_labels.append({
                    "annotationIndex": annotation_index,
                    "shapeIndex": shape_index,
                    "traceIndex": trace_index,
                    "legendgroup": sample_key,
                    "labelKey": label_key,
                    "wnText": f"{wn:.0f}",
                })
                fig.add_shape(type="line", x0=wn, x1=wn, y0=0, y1=val,
                              line=dict(color=peak_color, width=0.8, dash="dot"),
                              visible=initially_visible)

    title = "FTIR Peak Analysis — " + ", ".join(sample["label"] for sample in samples[:3])
    if len(samples) > 3:
        title += f" 외 {len(samples) - 3}개"

    fig.update_layout(
        title=dict(
            text=title,
            font=dict(size=18),
            x=0.01,
            y=0.98,
            yanchor="top",
        ),
        xaxis=dict(
            title="Wavenumber (cm⁻¹)", range=[wn_max, wn_min],
            showgrid=True, gridcolor="#e8e8e8",
            tickmode="linear", dtick=500,
            minor=dict(showgrid=True, gridcolor="#f4f4f4"),
        ),
        yaxis=dict(
            title="Normalized Absorbance", showgrid=True, gridcolor="#e8e8e8",
            range=[-0.05, max_y * 1.65],
        ),
        annotations=annotations,
        legend=dict(
            orientation="v", x=1.02, xanchor="left", y=1.0, yanchor="top",
            itemclick="toggle", itemdoubleclick="toggleothers",
            groupclick="toggleitem",
            bgcolor="rgba(255,255,255,0.9)",
            bordercolor="#ccc", borderwidth=1, font=dict(size=10),
            itemsizing="constant", tracegroupgap=10,
        ),
        plot_bgcolor="white", paper_bgcolor="#fafafa",
        height=720, hovermode="closest",
        margin=dict(l=70, r=260, t=105, b=70),
        meta={"ristPeakLabels": peak_labels},
    )
    return fig


def build_bar_fig(best_per_material, top_n, sample_label):
    TIER_COLORS = {
        "동정 (Identified)":          "#16a34a",
        "후보 복수 (Ambiguous)":      "#f59e0b",
        "미동정 (No reliable match)": "#9ca3af",
    }
    bar_colors = [TIER_COLORS.get(t, "#888") for t in best_per_material["tier"]]
    fig = go.Figure(go.Bar(
        x=best_per_material["composite_pct"],
        y=best_per_material["material"],
        orientation="h",
        marker_color=bar_colors,
        text=best_per_material["composite_pct"].apply(lambda v: f"{v:.1f}%"),
        textposition="outside",
        customdata=np.stack([
            best_per_material["cosine_pct"],
            best_per_material["deriv_pct"],
            best_per_material["peak_pct"],
            best_per_material["category_label"],
        ], axis=-1),
        hovertemplate=("<b>%{y}</b><br>종합: %{x:.1f}%<br>"
                       "코사인: %{customdata[0]:.1f}% | 미분: %{customdata[1]:.1f}% | "
                       "피크: %{customdata[2]:.1f}%<br>%{customdata[3]}<extra></extra>"),
    ))
    fig.update_layout(
        title=f"Top {top_n} 종합 점수 매칭 — {sample_label}",
        xaxis=dict(title="Composite Score (%)", range=[0, 110]),
        yaxis=dict(autorange="reversed"),
        height=max(350, top_n * 40),
        plot_bgcolor="white", paper_bgcolor="#fafafa",
        margin=dict(l=200, r=60, t=60, b=50),
    )
    return fig


def build_comparison_fig(sample_vec, grid, best_per_material, plot_top_n, sample_label,
                         library_dir, wn_min, wn_max, peak_height, peak_prominence,
                         peak_distance, smooth, smooth_win, smooth_poly):
    top_matches = best_per_material.head(plot_top_n)
    OFFSET_STEP = 1.3
    PALETTE = ["#2563eb", "#dc2626", "#16a34a", "#ea580c", "#7c3aed"]

    fig = go.Figure()
    cmp_annotations = []

    def add_peak_markers(vec, offset, color, label_prefix=""):
        p_idx, _ = find_peaks(vec, height=peak_height, prominence=peak_prominence,
                               distance=peak_distance)
        if len(p_idx) == 0:
            return
        p_wn = grid[p_idx]
        p_val = vec[p_idx] + offset
        fig.add_trace(_enable_abs_trans_toggle(
            go.Scatter(
                x=p_wn, y=p_val, mode="markers",
                marker=dict(color=color, size=7, symbol="circle-open",
                            line=dict(color=color, width=2)),
                name=f"{label_prefix}peaks",
                legendgroup=f"{label_prefix}peaks",
                showlegend=False,
                hovertemplate="%{x:.1f} cm⁻¹<br>%{y:.3f}<extra></extra>",
            ),
            p_val,
            absorbance_offset=offset,
            transmittance_offset=offset * 80.0,
        ))
        top_n = min(8, len(p_idx))
        top_sort = sorted(zip(p_wn, p_val, vec[p_idx]), key=lambda x: -x[2])[:top_n]
        for i, (wn_p, yy, _) in enumerate(top_sort):
            y_ann = yy + 0.06 + (0.05 if i % 2 == 0 else 0.0)
            cmp_annotations.append(dict(
                x=wn_p, y=y_ann, text=f"<b>{wn_p:.0f}</b>",
                showarrow=True, arrowhead=0, arrowcolor=color, arrowwidth=1,
                ax=0, ay=-22, font=dict(size=8, color=color),
                bgcolor="rgba(255,255,255,0.75)", borderpad=1,
            ))

    total_offset = plot_top_n * OFFSET_STEP
    fig.add_trace(_enable_abs_trans_toggle(
        go.Scatter(
            x=grid, y=sample_vec + total_offset, mode="lines",
            name=f"★ {sample_label}",
            line=dict(color="black", width=2),
            hovertemplate=f"<b>{sample_label}</b><br>%{{x:.1f}} cm⁻¹<extra></extra>",
        ),
        sample_vec + total_offset,
        absorbance_offset=total_offset,
        transmittance_offset=total_offset * 80.0,
    ))
    add_peak_markers(sample_vec, total_offset, "black", "sample")

    for rank, (_, row) in enumerate(top_matches.iterrows()):
        fpath = os.path.join(library_dir, row["file"])
        try:
            df_lib = load_csv(fpath, wn_min, wn_max)
            vec = preprocess(df_lib["wn"].values, df_lib["y"].values, grid,
                             smooth, smooth_win, smooth_poly)
        except Exception:
            continue

        offset = (plot_top_n - 1 - rank) * OFFSET_STEP
        color = PALETTE[rank % len(PALETTE)]
        label = f"#{rank+1} {row['material']}  ({row['composite_pct']:.1f}%)"

        fig.add_trace(_enable_abs_trans_toggle(
            go.Scatter(
                x=grid, y=vec + offset, mode="lines", name=label,
                line=dict(color=color, width=1.4),
                hovertemplate=(f"<b>{row['material']}</b><br>%{{x:.1f}} cm⁻¹<br>"
                               f"종합: {row['composite_pct']:.1f}%<extra></extra>"),
            ),
            vec + offset,
            absorbance_offset=offset,
            transmittance_offset=offset * 80.0,
        ))
        add_peak_markers(vec, offset, color, label)

    fig.update_layout(
        title=f"Spectral Comparison — {sample_label} vs Top {plot_top_n} Matches",
        xaxis=dict(
            title="Wavenumber (cm⁻¹)", range=[wn_max, wn_min],
            showgrid=True, gridcolor="#e8e8e8",
            tickmode="linear", dtick=500,
            minor=dict(showgrid=True, gridcolor="#f4f4f4"),
        ),
        yaxis=dict(
            title="Normalized Absorbance (offset)",
            showgrid=False, zeroline=False, showticklabels=False,
        ),
        annotations=cmp_annotations,
        legend=dict(
            title="<b>범례 클릭으로 표시/숨기기</b>",
            itemclick="toggle", itemdoubleclick="toggleothers",
            bgcolor="rgba(255,255,255,0.9)", bordercolor="#ccc", borderwidth=1,
        ),
        plot_bgcolor="white", paper_bgcolor="#fafafa",
        height=700, hovermode="closest",
        margin=dict(l=60, r=20, t=70, b=60),
    )
    return fig

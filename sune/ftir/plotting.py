# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: FTIR 분석용 Plotly 그림 생성기(전처리/피크/막대/비교).
#            공통 plot_style 모듈의 스타일·출력 헬퍼와 연계된다.
# 실행 방법: 모듈 — 직접 실행하지 않고 import해서 사용 (ftir_analyze.py / ftir.cli 를 통해 동작)
# ─────────────────────────────────────────────────────────────────────────────
"""Plotly figure builders for FTIR analysis (preprocess / peaks / bar / comparison)."""

import os

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy.signal import find_peaks

from .findings import assign_group
from .preprocess import load_csv, preprocess


def build_preprocess_fig(raw, sample_vec, grid, sample_label, wn_min, wn_max):
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        subplot_titles=["원시 스펙트럼 (Raw)", "전처리 후 (정규화)"],
        vertical_spacing=0.12,
    )
    fig.add_trace(
        go.Scatter(x=raw["wn"], y=raw["y"], mode="lines",
                   line=dict(color="#555", width=1), name="Raw"),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=grid, y=sample_vec, mode="lines",
                   line=dict(color="#2563eb", width=1.5), name="Preprocessed"),
        row=2, col=1,
    )
    fig.update_layout(
        title=f"전처리 확인 — {sample_label}",
        height=500, plot_bgcolor="white", paper_bgcolor="#fafafa",
        showlegend=False,
    )
    fig.update_xaxes(range=[wn_max, wn_min], title_text="Wavenumber (cm⁻¹)", row=2, col=1)
    fig.update_yaxes(showgrid=True, gridcolor="#e8e8e8")
    return fig


def build_peak_fig(sample_vec, grid, peak_idx, peak_wn, peak_val, peak_fwhm,
                   func_groups, sample_label, wn_min, wn_max):
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=grid, y=sample_vec, mode="lines", name=sample_label,
        line=dict(color="#374151", width=1.8),
        hovertemplate="%{x:.1f} cm⁻¹ | Abs: %{y:.4f}<extra></extra>",
    ))

    for wn, val, fwhm in zip(peak_wn, peak_val, peak_fwhm):
        group_name, color, note = assign_group(wn, func_groups)
        fig.add_trace(go.Scatter(
            x=[wn], y=[val], mode="markers",
            marker=dict(color=color, size=9, symbol="circle",
                        line=dict(color="white", width=1.5)),
            name=group_name,
            legendgroup=group_name,
            showlegend=not any(t.name == group_name for t in fig.data[:-1]),
            hovertemplate=(
                f"<b>{wn:.1f} cm⁻¹</b><br>{group_name}<br>"
                f"Intensity: {val:.4f}<br>FWHM: {fwhm:.1f} cm⁻¹<br>"
                f"<i>{note}</i><extra></extra>"
            ),
        ))

    top_peaks = sorted(zip(peak_wn, peak_val, peak_fwhm), key=lambda x: -x[1])[:25]
    annotations = []
    for i, (wn, val, fwhm) in enumerate(top_peaks):
        _, color, _ = assign_group(wn, func_groups)
        y_label = val + 0.07 + (0.07 if i % 2 == 0 else 0.0)
        annotations.append(dict(
            x=wn, y=y_label, text=f"<b>{wn:.0f}</b>",
            showarrow=True, arrowhead=0, arrowcolor=color, arrowwidth=1,
            ax=0, ay=-28, font=dict(size=9, color=color),
            bgcolor="rgba(255,255,255,0.8)", borderpad=1,
        ))
        fig.add_shape(type="line", x0=wn, x1=wn, y0=0, y1=val,
                      line=dict(color=color, width=0.8, dash="dot"))

    fig.update_layout(
        title=dict(text=f"FTIR Peak Analysis — {sample_label}", font=dict(size=18), x=0.01),
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
            title="<b>작용기 (범례 클릭)</b>", itemclick="toggle",
            itemdoubleclick="toggleothers", bgcolor="rgba(255,255,255,0.9)",
            bordercolor="#ccc", borderwidth=1, font=dict(size=11),
        ),
        plot_bgcolor="white", paper_bgcolor="#fafafa",
        height=620, hovermode="closest",
        margin=dict(l=70, r=30, t=70, b=60),
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
        fig.add_trace(go.Scatter(
            x=p_wn, y=p_val, mode="markers",
            marker=dict(color=color, size=7, symbol="circle-open",
                        line=dict(color=color, width=2)),
            name=f"{label_prefix}peaks",
            legendgroup=f"{label_prefix}peaks",
            showlegend=False,
            hovertemplate="%{x:.1f} cm⁻¹<br>%{y:.3f}<extra></extra>",
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
    fig.add_trace(go.Scatter(
        x=grid, y=sample_vec + total_offset, mode="lines",
        name=f"★ {sample_label}",
        line=dict(color="black", width=2),
        hovertemplate=f"<b>{sample_label}</b><br>%{{x:.1f}} cm⁻¹<extra></extra>",
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

        fig.add_trace(go.Scatter(
            x=grid, y=vec + offset, mode="lines", name=label,
            line=dict(color=color, width=1.4),
            hovertemplate=(f"<b>{row['material']}</b><br>%{{x:.1f}} cm⁻¹<br>"
                           f"종합: {row['composite_pct']:.1f}%<extra></extra>"),
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

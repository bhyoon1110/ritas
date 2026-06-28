"""Plotly figure builders for Raman preview graphs."""

from __future__ import annotations

import numpy as np
import plotly.graph_objects as go

from .peaks import build_interactive_peak_candidates


SAMPLE_PALETTE = [
    "#2563eb",
    "#dc2626",
    "#16a34a",
    "#9333ea",
    "#ea580c",
    "#0891b2",
    "#c026d3",
    "#65a30d",
]


def _sample_key(index: int) -> str:
    return f"sample:{index}"


def _peak_label_text(shift: float) -> str:
    return f"<b>{shift:.0f}</b><br><span style='font-size:10px'>Raman peak</span>"


def build_multi_raman_fig(
    samples: list[dict],
    *,
    shift_min: float,
    shift_max: float,
    initial_sensitivity: int | float = 25,
) -> go.Figure:
    fig = go.Figure()
    annotations = []
    peak_labels = []
    max_y = 1.0

    for sample_no, sample in enumerate(samples):
        sample_key = _sample_key(sample_no)
        label = sample["label"]
        grid = sample["grid"]
        values = sample["processed"]
        color = SAMPLE_PALETTE[sample_no % len(SAMPLE_PALETTE)]
        max_y = max(max_y, float(np.nanmax(values)) if len(values) else 1.0)

        sample_trace = go.Scatter(
            x=grid,
            y=values,
            mode="lines",
            name=label,
            legendgroup=sample_key,
            legendgrouptitle_text=label,
            line=dict(color=color, width=1.8),
            hovertemplate=(
                f"<b>{label}</b><br>%{{x:.1f}} cm⁻¹ | %{{y:.4f}}"
                "<extra></extra>"
            ),
        )
        sample_trace.meta = {
            "rist_sample_group": sample_key,
            "rist_sample_parent": True,
            "rist_legend_edit_group": sample_key,
        }
        fig.add_trace(sample_trace)

        candidates = build_interactive_peak_candidates(
            values,
            grid,
            sample["peak_idx"],
            initial_sensitivity=initial_sensitivity,
        )
        top_peak_indexes = {
            candidate["index"]
            for candidate in sorted(candidates, key=lambda item: -item["value"])[:25]
        }
        seen_label_keys = set()

        for peak_no, candidate in enumerate(candidates):
            shift = candidate["shift"]
            value = candidate["value"]
            fwhm = candidate["fwhm"]
            initially_visible = candidate["initial"]
            display_name = f"{shift:.0f} cm⁻¹"
            label_key = f"{sample_key}:peak:{shift:.1f}"
            trace_index = len(fig.data)
            peak_trace = go.Scatter(
                x=[shift],
                y=[value],
                mode="markers",
                marker=dict(
                    color=color,
                    size=8,
                    symbol="circle",
                    line=dict(color="white", width=1.5),
                ),
                name=display_name,
                legendgroup=sample_key,
                visible=initially_visible,
                showlegend=initially_visible and label_key not in seen_label_keys,
                hovertemplate=(
                    f"<b>{label}</b><br>{shift:.1f} cm⁻¹<br>"
                    f"Intensity: %{{y:.4f}}<br>FWHM: {fwhm:.1f} cm⁻¹"
                    "<extra></extra>"
                ),
            )
            peak_trace.meta = {
                "rist_sample_group": sample_key,
                "rist_legend_edit_group": label_key,
                "rist_peak": {
                    "source": "detected",
                    "x": float(shift),
                    "label": display_name,
                    "sample_group": sample_key,
                    "label_key": label_key,
                    "sensitivity_levels": candidate["levels"],
                    "sensitivity_min": candidate["sensitivity_min"],
                    "assignments": [],
                },
            }
            fig.add_trace(peak_trace)
            if initially_visible:
                seen_label_keys.add(label_key)

            if candidate["index"] in top_peak_indexes:
                y_label = value + 0.06 + (
                    0.05 if (len(annotations) + peak_no) % 2 == 0 else 0.0
                )
                annotation_index = len(annotations)
                shape_index = len(fig.layout.shapes)
                annotations.append(
                    dict(
                        x=shift,
                        y=y_label,
                        text=_peak_label_text(shift),
                        showarrow=True,
                        captureevents=True,
                        arrowhead=0,
                        arrowcolor=color,
                        arrowwidth=1,
                        ax=0,
                        ay=-28,
                        font=dict(size=9, color=color),
                        bgcolor="rgba(255,255,255,0.88)",
                        bordercolor=color,
                        borderwidth=1,
                        borderpad=2,
                        name=f"raman_peak_label_{sample_no}_{annotation_index}",
                        visible=initially_visible,
                    )
                )
                peak_labels.append(
                    {
                        "annotationIndex": annotation_index,
                        "shapeIndex": shape_index,
                        "traceIndex": trace_index,
                        "legendgroup": sample_key,
                        "labelKey": label_key,
                        "wnText": f"{shift:.0f}",
                    }
                )
                fig.add_shape(
                    type="line",
                    x0=shift,
                    x1=shift,
                    y0=0,
                    y1=value,
                    line=dict(color=color, width=0.8, dash="dot"),
                    visible=initially_visible,
                )

    title = "Raman Peak Analysis — " + ", ".join(
        sample["label"] for sample in samples[:3]
    )
    if len(samples) > 3:
        title += f" 외 {len(samples) - 3}개"

    fig.update_layout(
        title=dict(text=title, font=dict(size=18), x=0.01, y=0.98, yanchor="top"),
        xaxis=dict(
            title="Raman Shift (cm⁻¹)",
            range=[shift_min, shift_max],
            showgrid=True,
            gridcolor="#e8e8e8",
            tickmode="linear",
            dtick=500,
            minor=dict(showgrid=True, gridcolor="#f4f4f4"),
        ),
        yaxis=dict(
            title="Normalized Intensity",
            showgrid=True,
            gridcolor="#e8e8e8",
            range=[-0.05, max_y * 1.65],
        ),
        annotations=annotations,
        legend=dict(
            orientation="v",
            x=1.02,
            xanchor="left",
            y=1.0,
            yanchor="top",
            itemclick="toggle",
            itemdoubleclick="toggleothers",
            groupclick="toggleitem",
            bgcolor="rgba(255,255,255,0.9)",
            bordercolor="#ccc",
            borderwidth=1,
            font=dict(size=10),
            itemsizing="constant",
            tracegroupgap=10,
        ),
        plot_bgcolor="white",
        paper_bgcolor="#fafafa",
        height=720,
        hovermode="closest",
        margin=dict(l=70, r=260, t=105, b=70),
        meta={"ristPeakLabels": peak_labels},
    )
    return fig


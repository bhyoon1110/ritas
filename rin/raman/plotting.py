"""Plotly figure builders for Raman preview graphs."""

from __future__ import annotations

import numpy as np
from rist_common.plotting.raw_spectrum import peaks_on_raw_points
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


def _peak_label_text(shift: float, label: str) -> str:
    return f"<b>{shift:.0f}</b><br><span style='font-size:10px'>{label}</span>"


def _assignment_candidates(shift: float, func_groups: list[tuple]) -> list[dict]:
    selected = {}
    for raw in func_groups:
        center, tolerance, name, color, note = raw[:5]
        library_id = raw[5] if len(raw) > 5 else "default"
        library_name = raw[6] if len(raw) > 6 else ""
        delta = abs(float(shift) - float(center))
        if delta > float(tolerance):
            continue
        candidate = {
            "name": str(name),
            "color": str(color),
            "note": str(note or ""),
            "library_id": str(library_id),
            "library_name": str(library_name),
            "center_wn": float(center),
            "tolerance": float(tolerance),
            "delta": float(delta),
        }
        current = selected.get(library_id)
        if current is None or (candidate["tolerance"], candidate["delta"]) < (
            current["tolerance"],
            current["delta"],
        ):
            selected[library_id] = candidate
    return list(selected.values())


def _peak_assignment(shift: float, func_groups: list[tuple]) -> dict:
    assignments = _assignment_candidates(shift, func_groups)
    if not assignments:
        name = f"{shift:.0f} cm⁻¹"
        return {
            "display_name": name,
            "color": "#9ca3af",
            "note": "",
            "assignments": [],
        }
    names = list(dict.fromkeys(item["name"] for item in assignments))
    notes = []
    for item in assignments:
        source = item["library_name"] or item["library_id"]
        detail = f"{source}: {item['name']}"
        if item["note"]:
            detail += f" - {item['note']}"
        notes.append(detail)
    return {
        "display_name": "<br>".join(names),
        "color": assignments[0]["color"],
        "note": "<br>".join(notes),
        "assignments": assignments,
    }


def build_multi_raman_fig(
    samples: list[dict],
    *,
    shift_min: float,
    shift_max: float,
    func_groups: list[tuple] | None = None,
    initial_sensitivity: int | float = 25,
) -> go.Figure:
    fig = go.Figure()
    annotations = []
    peak_labels = []
    min_y, max_y = float("inf"), float("-inf")
    func_groups = func_groups or []
    stack_gap = 1.2
    stack_enabled = False
    raw_values = [
        np.asarray(sample.get("display_vec", sample["processed"]), dtype=float)
        for sample in samples
    ]
    nonempty = [values for values in raw_values if len(values)]
    stack_unit = max(
        max(float(np.max(v)) for v in nonempty) - min(float(np.min(v)) for v in nonempty),
        1e-9,
    ) if nonempty else 1.0
    sample_offsets = {
        _sample_key(index): (index * stack_gap * stack_unit if stack_enabled else 0.0)
        for index in range(len(samples))
    }

    for sample_no, sample in enumerate(samples):
        sample_key = _sample_key(sample_no)
        label = sample["label"]
        grid = sample["grid"]
        analysis_values = sample["processed"]
        values = np.asarray(sample.get("display_vec", analysis_values), dtype=float)
        display_grid = sample.get("display_grid", grid)
        stack_offset = sample_offsets[sample_key]
        plotted_values = values + stack_offset
        color = SAMPLE_PALETTE[sample_no % len(SAMPLE_PALETTE)]
        sample_min = float(np.min(values)) if len(values) else 0.0
        sample_span = max(float(np.ptp(values)), 1e-9) if len(values) else 1.0
        min_y = min(min_y, sample_min + stack_offset)
        max_y = max(max_y, sample_min + sample_span + stack_offset)

        sample_trace = go.Scatter(
            x=display_grid,
            y=plotted_values,
            mode="lines",
            name=label,
            legendgroup=sample_key,
            showlegend=True,
            line=dict(color=color, width=1.8, shape="linear", simplify=False),
            hovertemplate=(
                f"<b>{label}</b><br>%{{x:.1f}} cm⁻¹ | %{{customdata:.4f}}"
                "<extra></extra>"
            ),
            customdata=values,
        )
        sample_trace.meta = {
            "rist_sample_group": sample_key,
            "rist_sample_parent": True,
            "rist_legend_edit_group": sample_key,
            "rist_raman_stack_offset": float(stack_offset),
            "rist_raman_sample_index": sample_no,
        }
        fig.add_trace(sample_trace)

        candidates = build_interactive_peak_candidates(
            analysis_values,
            grid,
            sample["peak_idx"],
            initial_sensitivity=initial_sensitivity,
        )
        candidates = peaks_on_raw_points(candidates, display_grid, values, x_key="shift")
        top_peak_indexes = {
            candidate["index"]
            for candidate in sorted(candidates, key=lambda item: -item["value"])[:25]
        }
        seen_label_keys = set()

        for peak_no, candidate in enumerate(candidates):
            shift = candidate["shift"]
            value = candidate["raw_value"]
            fwhm = candidate["fwhm"]
            initially_visible = candidate["initial"]
            assignment = _peak_assignment(shift, func_groups)
            display_name = assignment["display_name"]
            peak_color = assignment["color"]
            note = assignment["note"]
            label_key = f"{sample_key}:peak:{display_name}:{shift:.1f}"
            trace_index = len(fig.data)
            peak_trace = go.Scatter(
                x=[shift],
                y=[value + stack_offset],
                mode="markers",
                marker=dict(
                    color=peak_color,
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
                    f"{display_name}<br>Intensity: {value:.4f}"
                    f"<br>FWHM (분석 추정): {fwhm:.1f} cm⁻¹<br><i>{note}</i>"
                    "<extra></extra>"
                ),
            )
            peak_trace.meta = {
                "rist_sample_group": sample_key,
                "rist_legend_edit_group": label_key,
                "rist_peak": {
                    "source": "detected",
                    "x": float(shift),
                    "base_y": float(value),
                    "label": display_name,
                    "sample_group": sample_key,
                    "label_key": label_key,
                    "sensitivity_levels": candidate["levels"],
                    "sensitivity_min": candidate["sensitivity_min"],
                    "assignments": assignment["assignments"],
                },
                "rist_raman_stack_offset": float(stack_offset),
                "rist_raman_sample_index": sample_no,
            }
            fig.add_trace(peak_trace)
            if initially_visible:
                seen_label_keys.add(label_key)

            if candidate["index"] in top_peak_indexes:
                base_y_label = value + sample_span * 0.06 + (
                    sample_span * 0.05 if (len(annotations) + peak_no) % 2 == 0 else 0.0
                )
                y_label = base_y_label + stack_offset
                annotation_index = len(annotations)
                shape_index = len(fig.layout.shapes)
                annotations.append(
                    dict(
                        x=shift,
                        y=y_label,
                        text=_peak_label_text(shift, display_name),
                        showarrow=True,
                        captureevents=True,
                        arrowhead=0,
                        arrowcolor=peak_color,
                        arrowwidth=1,
                        ax=0,
                        ay=-28,
                        font=dict(size=9, color=peak_color),
                        bgcolor="rgba(255,255,255,0.88)",
                        bordercolor=peak_color,
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
                        "annotationBaseY": float(base_y_label),
                        "shapeBaseY0": min(0.0, sample_min),
                        "shapeBaseY1": float(value),
                    }
                )
                fig.add_shape(
                    type="line",
                    x0=shift,
                    x1=shift,
                    y0=min(0.0, sample_min) + stack_offset,
                    y1=value + stack_offset,
                    line=dict(color=peak_color, width=0.8, dash="dot"),
                    visible=initially_visible,
                )

    title = "Raman Peak Analysis — " + ", ".join(
        sample["label"] for sample in samples[:3]
    )
    if len(samples) > 3:
        title += f" 외 {len(samples) - 3}개"

    if not np.isfinite(min_y) or not np.isfinite(max_y):
        min_y, max_y = 0.0, 1.0
    y_span = max(max_y - min_y, abs(max_y) * 0.01, 1e-9)

    fig.update_layout(
        title=dict(text=title, font=dict(size=18), x=0.01, y=0.98, yanchor="top"),
        xaxis=dict(
            title="Raman Shift (cm⁻¹)",
            range=[shift_min, shift_max],
            showgrid=True,
            gridcolor="#e8e8e8",
            tickmode="auto",
            fixedrange=False,
            minor=dict(showgrid=True, gridcolor="#f4f4f4"),
        ),
        yaxis=dict(
            title="Intensity (a.u.)",
            showgrid=True,
            gridcolor="#e8e8e8",
            range=[
                min_y - y_span * 0.08,
                max_y + y_span * 0.25,
            ],
            tickmode="auto",
            fixedrange=False,
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
        meta={
            "ristPeakLabels": peak_labels,
            "ristRamanStack": {
                "enabled": stack_enabled,
                "gap": stack_gap,
                "unit": stack_unit,
                "sampleOffsets": sample_offsets,
                "sampleOrder": [_sample_key(index) for index in range(len(samples))],
            },
        },
    )
    return fig

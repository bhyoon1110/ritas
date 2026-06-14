"""Shared Plotly styling and responsive HTML output."""

from .plot_style import (
    LEGEND_BREAKPOINT_PX,
    PALETTE,
    apply_crosshair_spikes,
    apply_origin_style,
    fig_to_responsive_html,
    write_responsive_html,
)

__all__ = [
    "LEGEND_BREAKPOINT_PX",
    "PALETTE",
    "apply_crosshair_spikes",
    "apply_origin_style",
    "fig_to_responsive_html",
    "write_responsive_html",
]

"""Draw a comparison-figure panel (from ..core.figure) as a PNG with matplotlib (Agg).

The panel is drawn exactly as planned: no new peaks, positions or values. The bytes are
reproducible (no timestamps) so the same plan gives the same image."""

import io
import threading

from matplotlib import font_manager
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

from ..core.figure import stack_offsets
from ..render.fonts import matplotlib_font_files

DPI = 300
MM = 25.4
LEFT_MM, RIGHT_MM, TOP_MM, BOTTOM_MM = 11.0, 3.0, 2.0, 12.0
_MARKERS = {
    'triangle': '^',
    'square': 's',
    'circle': 'o',
    'diamond': 'D',
    'plus': 'P',
    'star': '*',
    'triangle_down': 'v',
    'hexagon': 'h',
}
_lock = threading.Lock()


_family_names: dict[str, str] = {}  # font path -> family name, registered with matplotlib once


def _families() -> list[str]:
    names = []
    for path in matplotlib_font_files():
        key = str(path)
        if key not in _family_names:
            font_manager.fontManager.addfont(key)
            _family_names[key] = font_manager.FontProperties(fname=key).get_name()
        names.append(_family_names[key])
    return names


def row_caption(row: dict) -> str:
    """Row name in the viewer's form: chemical formula, phase name, # card number."""
    caption = ' · '.join(
        part
        for part in (row.get('formula'), row['name'], '#' + row['card'] if row['card'] else '')
        if part
    )
    # A row that stands for differently named phases names them all.
    return ' / '.join([caption, *row.get('alt', [])])


def _marker_style(row: dict) -> dict:
    # Origin's rule: filled markers (black unless the colour was changed).
    return dict(
        marker=_MARKERS[row['symbol']],
        markersize=3.4,
        markerfacecolor=row['color'],
        markeredgecolor=row['color'],
        markeredgewidth=0.4,
        linestyle='none',
    )


def render_panel_png(panel: dict, width_mm: float, height_mm: float, row_mm: float = 5.0) -> bytes:
    """PNG (300 dpi) of one panel at the given physical size in mm; reference rows are `row_mm` tall."""
    if not panel['curves']:
        raise ValueError('그릴 수 있는 시료가 없습니다.')
    with _lock:
        families = _families()
        figure = Figure(figsize=(width_mm / MM, height_mm / MM), dpi=DPI)
        FigureCanvasAgg(figure)
        curves, rows = panel['curves'], panel['phases']
        show_ref = bool(panel['show_reference'] and rows)
        offsets = stack_offsets(len(curves), panel['spacing'])
        left, right = LEFT_MM / width_mm, 1 - RIGHT_MM / width_mm
        bottom, top_edge = BOTTOM_MM / height_mm, 1 - TOP_MM / height_mm
        ref_height = min(0.5, len(rows) * row_mm / height_mm) if show_ref else 0.0
        top = figure.add_axes(
            [left, bottom + ref_height, right - left, top_edge - bottom - ref_height]
        )
        lo, hi = panel['x_range']
        ceiling, floor = 0.0, None
        for curve, offset in zip(curves, offsets):
            y = [v + offset for v in curve['base']]
            top.plot(curve['x'], y, color=curve['color'], linewidth=0.7, label=curve['name'])
            label = curve['name'] + (f"  ({curve['note']})" if curve['note'] else '')
            if not panel.get('sample_legend'):
                top.text(
                    lo,
                    offset + min(curve['base']),
                    label,
                    color=curve['color'],
                    fontsize=6,
                    va='bottom',
                    ha='left',
                    fontfamily=families,
                )
            ceiling = max(ceiling, max(y))
            floor = min(y) if floor is None else min(floor, min(y))
            if panel['show_markers']:
                for index, row in enumerate(rows):
                    pts = [
                        (m['x'], m['y'] + offset)
                        for m in curve['markers']
                        if m['phase_row'] == index
                    ]
                    if pts:
                        top.plot([p[0] for p in pts], [p[1] for p in pts], **_marker_style(row))
                        ceiling = max(ceiling, *(p[1] for p in pts))
        if panel.get('sample_legend'):
            top.legend(loc='upper right',frameon=False,
                       prop=font_manager.FontProperties(family=families,size=6))
        if panel['show_markers'] and rows and not show_ref:
            # Without reference rows the symbols still need a key naming each phase.
            handles = [
                Line2D(
                    [],
                    [],
                    label=row_caption(r),
                    **_marker_style(r),
                )
                for r in rows
            ]
            top.legend(
                handles=handles,
                loc='upper right',
                frameon=False,
                prop=font_manager.FontProperties(family=families, size=5.5),
                ncols=1 if len(handles) < 6 else 2,
                handletextpad=0.3,
                labelspacing=0.3,
            )
        top.set_xlim(lo, hi)
        margin = max(panel['spacing'] * 0.15, 0.02 * (ceiling - floor))
        top.set_ylim(floor - margin, ceiling + 0.04 * (ceiling - floor))
        # The label spans the whole figure height, so a short figure (one curve) does not clip it.
        figure.text(
            3.2 / width_mm,
            0.5,
            panel['y_label'],
            rotation=90,
            fontsize=7,
            fontweight='bold',
            ha='center',
            va='center',
            fontfamily=families,
        )
        top.set_yticks([])
        # Origin's frame: all four sides, ticks pointing inwards.
        axis = top
        if show_ref:
            axis = figure.add_axes([left, bottom, right - left, ref_height], sharex=top)
            top.tick_params(labelbottom=False)
            for r, row in enumerate(reversed(rows)):
                for x, intensity in row['ref']:
                    axis.plot(
                        [x, x], [r, r + 0.75 * intensity / 100], color=row['color'], linewidth=0.8
                    )
                label = row_caption(row)
                axis.text(
                    hi,
                    r + 0.95,
                    label,
                    color=row['color'],
                    fontsize=5.5,
                    ha='right',
                    va='top',
                    fontfamily=families,
                )
                axis.plot([lo], [r + 0.4], clip_on=False, **_marker_style(row))
            axis.set_ylim(0, len(rows))
            axis.set_yticks([])
        axis.set_xlim(lo, hi)
        axis.set_xlabel(panel['x_label'], fontsize=7, fontfamily=families, fontweight='bold')
        for tick in axis.get_xticklabels():
            tick.set_fontsize(6)
            tick.set_fontfamily(families)
        for ax in figure.axes:
            for spine in ax.spines.values():
                spine.set_linewidth(0.5)
            ax.tick_params(width=0.5, length=2, direction='in', top=True, right=True)
        buffer = io.BytesIO()
        figure.savefig(buffer, format='png', dpi=DPI, metadata={'Software': None})
        return buffer.getvalue()

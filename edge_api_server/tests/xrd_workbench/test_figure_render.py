import io

import pytest
from .figure_fixtures import make_document, make_project, make_sample
from PIL import Image

from app.xrd_workbench.core import figure as fig
from app.xrd_workbench.render.figure import render_panel_png

A = ('PhaseA', '00-001', 'DB1')


def panel(**figure):
    sample = make_sample(
        's1',
        'SampleX ₂θ',
        [A],
        [(1, 21.0, ['PhaseA'])],
        references={'PhaseA': [(21.0, 100), (30.0, 30)]},
    )
    other = make_sample(
        's2', '시료 2', [A], [(1, 25.0, ['PhaseA'])], references={'PhaseA': [(25.0, 100)]}
    )
    project = make_project([sample, other])
    return fig.build_plan(project, make_document(project, **figure))['panels'][0]


def test_png_has_the_requested_physical_size_at_300_dpi():
    data = render_panel_png(panel(), 176, 90)
    image = Image.open(io.BytesIO(data))
    assert image.format == 'PNG'
    assert abs(image.width - 176 / 25.4 * 300) <= 1
    assert abs(image.height - 90 / 25.4 * 300) <= 1


def test_same_plan_gives_identical_bytes():
    assert render_panel_png(panel(), 176, 90) == render_panel_png(panel(), 176, 90)


def test_changing_a_curve_or_a_marker_changes_the_image():
    base = render_panel_png(panel(), 176, 90)
    assert render_panel_png(panel(normalize=True), 176, 90) != base
    assert render_panel_png(panel(show_markers=False), 176, 90) != base
    assert render_panel_png(panel(show_reference=False), 176, 90) != base


def test_png_carries_no_timestamp_or_software_metadata():
    image = Image.open(io.BytesIO(render_panel_png(panel(), 176, 90)))
    assert 'Software' not in image.info and 'Creation Time' not in image.info


def test_panel_without_curves_is_refused_with_a_clear_message():
    project = make_project([make_sample('s1', 's1', [A], [], unit=None)])
    empty = fig.build_plan(project, make_document(project))['panels'][0]
    with pytest.raises(ValueError, match='그릴 수 있는 시료'):
        render_panel_png(empty, 176, 90)


def test_markers_without_reference_rows_still_get_a_phase_legend():
    base = panel(show_reference=False)
    renamed = dict(base, phases=[dict(r, name='Other name') for r in base['phases']])
    # With a legend the phase name is drawn, so renaming it changes the image.
    assert render_panel_png(base, 176, 60) != render_panel_png(renamed, 176, 60)


def test_rendering_does_not_grow_matplotlibs_font_list():
    from matplotlib import font_manager

    render_panel_png(panel(), 176, 60)
    size = len(font_manager.fontManager.ttflist)
    for _ in range(3):
        render_panel_png(panel(), 176, 60)
    assert len(font_manager.fontManager.ttflist) == size


def test_markers_are_filled_and_rows_are_named_formula_name_card():
    from app.xrd_workbench.render.figure import _marker_style, row_caption

    row = dict(name='PhaseA', formula='Ti O2', card='00-001', color='#000000', symbol='square')
    style = _marker_style(row)
    assert style['markerfacecolor'] == '#000000' and style['markeredgecolor'] == '#000000'
    assert row_caption(row) == 'Ti O2 · PhaseA · #00-001'
    assert row_caption(dict(row, formula='', card='')) == 'PhaseA'
    for symbol in ('triangle_down', 'hexagon', 'star', 'plus'):
        assert _marker_style(dict(row, symbol=symbol))['marker']


def test_a_merged_row_caption_names_every_phase_it_stands_for():
    from app.xrd_workbench.render.figure import row_caption

    row = dict(name='PhaseA', formula='Fe O', card='1', alt=['PhaseB · #2'])
    assert row_caption(row) == 'Fe O · PhaseA · #1 / PhaseB · #2'

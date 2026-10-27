"""Stable sample colours, shared by contract with viewer/sample_colors.mjs.

Keep the familiar first six colours; extend instead of wrapping at six. Colours
depend on the original sample index, never the visible subset or display order.
"""
import math

SAMPLE_COLORS = (
    '#ff3535', '#3535ff', '#15966b', '#9d51c9', '#d98515', '#00a1ad',
    '#333333', '#8c564b', '#cf267d', '#687c15', '#163f76', '#8e2424',
    '#ff11ff', '#11aa11', '#1188ff', '#aa88bb', '#662255', '#1199dd',
    '#224411', '#113399', '#778877', '#dd7755', '#dd22bb', '#ff1166',
)


def sample_color(index):
    """Return a deterministic #RRGGBB colour for a zero-based project sample index."""
    if index < len(SAMPLE_COLORS):
        return SAMPLE_COLORS[index]
    # Golden-angle hue steps avoid a second short repeating palette. Alternate
    # lightness as well as hue, staying dark enough for thin lines on white.
    step = index - len(SAMPLE_COLORS)
    hue = (210 + step * 137.50776405003785) % 360
    saturation = (0.65, 0.80, 0.70)[step % 3]
    lightness = (0.38, 0.46, 0.30)[step % 3]
    amplitude = saturation * min(lightness, 1 - lightness)

    def channel(offset):
        k = (offset + hue / 30) % 12
        value = lightness - amplitude * max(-1, min(k - 3, 9 - k, 1))
        return math.floor(255 * value + 0.5)

    rgb = [channel(offset) for offset in (0, 8, 4)]

    def luminance():
        srgb = [v / 255 for v in rgb]
        linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in srgb]
        return sum(v * w for v, w in zip(linear, (0.2126, 0.7152, 0.0722)))

    # Yellow/green need darkening even at the same HSL lightness. Keep at least
    # 3:1 contrast against white for generated colours (existing six stay intact).
    while luminance() > 0.30:
        rgb = [math.floor(v * 0.92) for v in rgb]
    return '#' + ''.join(f'{v:02x}' for v in rgb)

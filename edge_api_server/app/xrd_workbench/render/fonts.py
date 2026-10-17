"""Report fonts: register the configured TrueType files with reportlab and wrap text with their
metrics. Registration names follow the file, so changing the configuration never mixes metrics."""

import hashlib
import re
import threading

from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFError, TTFont

from ..config import BUNDLED_FONTS, load_settings

_lock = threading.Lock()

# Bundled fonts drawn for characters the configured font has no glyph for, in order:
# Latin-Extended/Greek/Cyrillic/sub- and superscripts, then arrows/math operators/shapes.
_FALLBACKS = (
    ('NotoSansFallback-Regular.ttf', 'NotoSansFallback-Bold.ttf'),
    ('NotoSansMath-Regular.ttf', 'NotoSansMath-Regular.ttf'),
)


_registered: dict[str, str] = {}  # path string -> reportlab name, after a successful registration


def _register(path, setting: str) -> str:
    cached = _registered.get(str(path))
    if cached is not None:
        return cached
    if not path.is_file():
        raise ValueError(
            f'보고서 글꼴 파일을 찾지 못했습니다: {path}. {setting}에 TTF 경로를 지정하세요.'
        )
    name = 'XRD-' + hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:12]
    if name not in pdfmetrics.getRegisteredFontNames():
        try:
            pdfmetrics.registerFont(TTFont(name, str(path)))
        except (TTFError, OSError) as exc:
            raise ValueError(
                f'보고서 글꼴 파일을 읽지 못했습니다: {path}. '
                f'{setting}에 TrueType(.ttf) 글꼴을 지정하세요 (CFF 방식 .otf는 지원하지 않습니다).'
            ) from exc
    _registered[str(path)] = name
    return name


def font_names() -> tuple[str, str]:
    """Registered (regular, bold) reportlab font names for the current settings."""
    settings = load_settings()
    with _lock:
        regular = _register(settings.font_regular, 'XRD_FONT_REGULAR')
        bold = _register(settings.font_bold, 'XRD_FONT_BOLD')
    return regular, bold


def _glyph_sets(name: str) -> set[int]:
    """Code points the registered reportlab font can draw."""
    return pdfmetrics.getFont(name).face.charToGlyph.keys()


def segments(text: str, bold: bool = False) -> list[tuple[str, str]]:
    """Split text into (font name, text) runs so every character uses a font that has it.

    The configured font is used wherever it has the glyph; other characters come from the
    bundled fallback fonts. A character no font has stays on the configured font (its notdef).
    """
    regular, bold_name = font_names()
    primary = bold_name if bold else regular
    with _lock:
        chain = [primary] + [
            _register(BUNDLED_FONTS / (b if bold else r), 'bundled fallback font')
            for r, b in _FALLBACKS
        ]
    covers = [_glyph_sets(name) for name in chain]
    runs: list[tuple[str, str]] = []
    for char in str(text):
        index = next((i for i, c in enumerate(covers) if ord(char) in c or char.isspace()), 0)
        if runs and runs[-1][0] == chain[index]:
            runs[-1] = (chain[index], runs[-1][1] + char)
        else:
            runs.append((chain[index], char))
    return runs


def text_width(text: str, size: float, bold: bool = False) -> float:
    """Width in points of text drawn with segments() at the given size."""
    return sum(pdfmetrics.stringWidth(part, name, size) for name, part in segments(text, bold))


def draw_string(canvas, x: float, y: float, text: str, size: float, bold: bool = False) -> None:
    """Draw text on a reportlab canvas, switching fonts per run (same rule as text_width)."""
    for name, part in segments(text, bold):
        canvas.setFont(name, size)
        canvas.drawString(x, y, part)
        x += pdfmetrics.stringWidth(part, name, size)


def family_of(font_name: str) -> str:
    """Family name (e.g. 'Noto Sans') of a registered reportlab font, for PPTX typefaces."""
    family = pdfmetrics.getFont(font_name).face.familyName
    return family.decode('utf-8') if isinstance(family, bytes) else str(family)


def pptx_family() -> str:
    """Typeface name to declare in PPTX: the override, else the regular font's own family name."""
    settings = load_settings()
    if settings.pptx_font_family:
        return settings.pptx_font_family
    regular, _ = font_names()  # validates the file and registers it
    return family_of(regular)


def wrap_lines(text, width_mm, size=10, bold=False):
    """Split text into lines that fit width_mm with the configured font's real metrics."""
    limit = width_mm * mm
    lines = []
    for paragraph in str(text).split('\n'):
        current = ''
        for token in re.findall(r'\S+|\s+', paragraph):
            if text_width(current + token, size, bold) <= limit:
                current += token
                continue
            if current.strip():
                lines.append(current.rstrip())
                current = ''
            if token.isspace():
                continue
            for char in token:
                if current and text_width(current + char, size, bold) > limit:
                    lines.append(current)
                    current = ''
                current += char
        lines.append(current.rstrip())
    return lines or ['']


def matplotlib_font_files() -> list:
    """TTF files for matplotlib in fallback order: the configured regular font, then the bundled
    fallbacks (Latin-Extended/Greek/sub- and superscripts, then math symbols)."""
    return [load_settings().font_regular] + [BUNDLED_FONTS / regular for regular, _ in _FALLBACKS]

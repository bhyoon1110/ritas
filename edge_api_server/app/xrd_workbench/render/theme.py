"""Look of the report: page size, colours, margins and the title band / footer geometry.

This is a TEMPORARY design (16:9 landscape slides). The in-house template will replace these
values, so the layout and the renderers read every colour and page measurement from here instead
of writing their own. All lengths are millimetres from the top-left corner of the page."""

# Page: PowerPoint widescreen (12192000 x 6858000 EMU).
PAGE_W, PAGE_H = 12192000 / 36000, 190.5
LEFT = 20
WIDTH = round(PAGE_W - 2 * LEFT, 3)
TOP, BOTTOM = 26, 178  # body area: first content line (minimum) and the lowest allowed edge

# Colours.
NAVY = '#1b3555'  # title band, headings
WHITE = '#ffffff'
TEXT = '#394758'  # body text
SOFT = '#647286'
MUTED = '#7c8796'
CAPTION = '#758397'
NOTE = '#8793a1'
WARN = '#b0561f'
FOOTER = '#99a2ae'

# Title band (full width, navy, white title text). It grows when the title needs more lines.
HEADER_MIN_H = 16
HEADER_PAD = 4.5
HEADER_FONT = 14
HEADER_LEADING = 7
HEADER_GAP = 6  # between the band and the first body line

# Footer: report number on the left, "page / total" on the right, anchored to the page bottom.
FOOTER_FONT = 8
FOOTER_LEADING = 4.3
FOOTER_TEXT_W = 200
FOOTER_COUNT_W = 40
FOOTER_BOTTOM = 3.5

# Summary page: two columns of sample summaries.
COVER_GAP = 8
# Cover slide: where the title starts and the gap below it (mm).
COVER_TITLE_Y = 62
COVER_GAP_Y = 16

# Largest height (mm) of a registered source image on a sample slide (before shrinking to fit).
SLIDE_IMAGE_MAX_H = 62

# Pixels per millimetre of the HTML page at 96 dpi (the preview scales it to the window).
PX_PER_MM = 96 / 25.4

# Quantity summary table: first column (sample name), phase columns, and text size.
QT_FIRST_COL_W = 50
QT_COL_MIN_W = 30
QT_COL_MAX_W = 60
QT_FONT = 8.5
QT_HEADER_GAP = 3
QT_ROW_GAP = 2

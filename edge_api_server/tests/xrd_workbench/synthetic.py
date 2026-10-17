"""A small synthetic fixture folder, generated at test time (never stored in the repository).

It follows the real file-naming convention and Report table structure so that every parser,
review and report test can run without confidential data:

    <sample>.txt                       measurement (two numeric columns, '#Intensity_unit=cps')
    <sample>_상매칭_Report.pdf          phase table, observed peaks, assignments, one d-I table per phase
    <sample>_정량(WPPF)_Report.pdf      weight fractions per phase group
    <sample>_상매칭.png / _정량.png      source images

All names and numbers are invented (PhaseA, SampleA, 12.34-style tokens). SampleB's reports print
a dataset label different from its registered name, and its weights do not total 100.
"""

import io
import math
from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.xrd_workbench.config import BUNDLED_FONTS

FONT = 'SyntheticReportFont'

# phase -> (formula, FOM, database, space group, card, reference peaks [(2θ, d, hkl, I)])
PHASES = {
    'PhaseA': (
        'A2B',
        '0.101',
        'COD',
        'P-1',
        '9000001',
        [
            ('24.812', '3.5851', '1 0 0', '100'),
            ('36.457', '2.4623', '1 1 0', '55'),
            ('47.903', '1.8975', '2 0 0', '20'),
        ],
    ),
    'PhaseB': (
        'B3',
        '0.202',
        'COD',
        'Fm-3m',
        '9000002',
        [
            ('29.268', '3.0488', '1 1 1', '100'),
            ('43.115', '2.0963', '2 0 0', '40'),
            ('62.774', '1.4789', '2 2 0', '18'),
        ],
    ),
    'PhaseC': (
        'C',
        '0.303',
        'COD',
        'P6/mmc',
        '9000003',
        [('33.642', '2.6617', '0 0 2', '100'), ('51.389', '1.7769', '1 0 2', '35')],
    ),
    'PhaseD': (
        'D2',
        '0.404',
        'COD',
        'C2/m',
        '9000004',
        [('21.906', '4.0538', '0 0 1', '100'), ('58.241', '1.5832', '3 1 0', '25')],
    ),
    'PhaseE': (
        'E',
        '0.505',
        'COD',
        'Pnma',
        '9000005',
        [('40.337', '2.2342', '2 1 1', '100')],
    ),
}

# sample -> ordered phase names, quantities {phase: token}, printed dataset label
SAMPLES = {
    'SampleA': {
        'phases': ['PhaseA', 'PhaseB', 'PhaseC'],
        'quantities': {'PhaseA': '60.0(1)', 'PhaseB': '25.0(1)', 'PhaseC': '15.0(1)'},
        'label': 'SampleA',
        # observed 2θ token -> phases assigned to it
        'observed': [
            ('24.81(2)', '1250', ['PhaseA']),
            ('29.27(3)', '980', ['PhaseB']),
            ('33.64(2)', '610', ['PhaseC']),
            ('36.46(2)', '430', ['PhaseA', 'PhaseB']),
        ],
    },
    'SampleB': {
        'phases': ['PhaseA', 'PhaseB', 'PhaseD', 'PhaseE'],
        'quantities': {
            'PhaseA': '55.50(20)',
            'PhaseB': '30.25(15)',
            'PhaseD': '14.20(30)',
            'PhaseE': '0.02(1)',
        },
        'label': 'SampleB-1',
        'observed': [
            ('21.91(2)', '890', ['PhaseD']),
            ('24.82(3)', '1100', ['PhaseA']),
            ('29.26(2)', '760', ['PhaseB']),
            ('40.34(3)', '120', ['PhaseE']),
        ],
    },
}


def _register_font() -> None:
    if FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(FONT, str(BUNDLED_FONTS / 'NotoSansKR-Regular.ttf')))


def _table(data, widths=None):
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ('GRID', (0, 0), (-1, -1), 0.6, colors.black),
                ('FONTNAME', (0, 0), (-1, -1), FONT),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ]
        )
    )
    return table


def _document(story) -> bytes:
    out = io.BytesIO()
    SimpleDocTemplate(out, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=40).build(story)
    return out.getvalue()


def phase_report_pdf(sample: str) -> bytes:
    """Phase table, observed peaks, assignments and one d-I table (with its phase heading) per phase."""
    _register_font()
    spec = SAMPLES[sample]
    style = ParagraphStyle('s', fontName=FONT, fontSize=11, leading=14)
    story = [Paragraph(f'{spec["label"]}-Evaluation report', style), Spacer(1, 12)]
    rows = [
        [
            'Phase name',
            'Chemical formula',
            'FOM',
            'Phase reg. detail',
            'Space Group',
            'DB Card Number',
        ]
    ]
    for name in spec['phases']:
        formula, fom, database, group, card, _ = PHASES[name]
        rows.append([name, formula, fom, database, group, card])
    story += [_table(rows), PageBreak()]
    observed = [['No.', '2θ, °', 'Height, cps']]
    assignments = [['No.', '2θ, °', 'Phase Name', 'Card No']]
    for number, (x_raw, height, names) in enumerate(spec['observed'], 1):
        observed.append([str(number), x_raw, height])
        listed = ', '.join(f'{n}: {PHASES[n][5][0][2]}' for n in names)
        assignments.append([str(number), x_raw, listed, PHASES[names[0]][4]])
    story += [Paragraph('Peak List', style), Spacer(1, 6), _table(observed), PageBreak()]
    story += [Paragraph('Peak assignment', style), Spacer(1, 6), _table(assignments), PageBreak()]
    for name in spec['phases']:
        reference = [['No.', '2θ, °', 'd, Å', 'h k l', 'Norm. I.']]
        for number, (x_raw, d_raw, hkl, intensity) in enumerate(PHASES[name][5], 1):
            reference.append([str(number), x_raw, d_raw, hkl, intensity])
        story += [Paragraph(name, style), Spacer(1, 6), _table(reference), PageBreak()]
    return _document(story[:-1])


def wppf_report_pdf(sample: str) -> bytes:
    """Weight fractions grouped per phase ('Phase / dataset' table)."""
    _register_font()
    spec = SAMPLES[sample]
    style = ParagraphStyle('s', fontName=FONT, fontSize=11, leading=14)
    rows = [['Item', 'Phase / dataset', 'Parameter', 'Value']]
    for index, name in enumerate(spec['phases'], 1):
        rows.append([str(index), name, '', ''])
        if name in spec['quantities']:
            rows.append(['', '', 'Weight fraction, wt%', spec['quantities'][name]])
    story = [Paragraph(f'{spec["label"]}-WPPF report', style), Spacer(1, 12), _table(rows)]
    return _document(story)


def measurement_txt(sample: str) -> bytes:
    """A 2θ scan with a Gaussian at every observed position (strictly increasing x)."""
    centres = [float(x.split('(')[0]) for x, _, _ in SAMPLES[sample]['observed']]
    lines = ['#Intensity_unit=cps']
    for i in range(401):
        x = 10 + i * 0.2
        y = 40 + sum(900 * math.exp(-((x - c) ** 2) / 0.05) for c in centres)
        lines.append(f'{x:.1f} {y:.1f}')
    return ('\n'.join(lines) + '\n').encode()


def _png(label: str, shade: int) -> bytes:
    image = Image.new('RGB', (640, 420), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 600, 380), outline=(shade, 60, 120), width=3)
    draw.line((60, 360, 200, 120, 340, 300, 580, 80), fill=(shade, 80, 40), width=4)
    draw.text((60, 50), label, fill=(0, 0, 0))
    out = io.BytesIO()
    image.save(out, format='PNG')
    return out.getvalue()


def sample_files(sample: str) -> dict[str, bytes]:
    """The five registered files of one sample, keyed by filename."""
    shade = 200 if sample == 'SampleA' else 120
    return {
        f'{sample}.txt': measurement_txt(sample),
        f'{sample}_상매칭_Report.pdf': phase_report_pdf(sample),
        f'{sample}_정량(WPPF)_Report.pdf': wppf_report_pdf(sample),
        f'{sample}_상매칭.png': _png(f'{sample} phase matching', shade),
        f'{sample}_정량.png': _png(f'{sample} quantification', shade),
    }


def write_folder(target: Path) -> Path:
    """Write the synthetic fixture folder (idempotent) and return it."""
    target.mkdir(parents=True, exist_ok=True)
    for sample in SAMPLES:
        for name, data in sample_files(sample).items():
            (target / name).write_bytes(data)
    return target

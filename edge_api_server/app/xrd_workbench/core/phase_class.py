"""Trace-phase rule for the author's phase classification.

Evaluates, from the PDF's own tables, whether a phase's two representative reference peaks are
weak relative to the strongest observed peak of the sample. It never decides a class by itself:
the author applies the result (see xrd.core.report). No peak is matched by position; the link
between a reference peak and an observed peak is the report's own assignment of that hkl."""

from decimal import Decimal

from ..core.figure import phase_key
from ..core.parsing.values import pdf_number


def _hkl(text) -> str:
    return ' '.join(str(text or '').split())


def phase_ref(phase: dict) -> dict:
    """Identity of a phase as stored in the report document."""
    name, card, db = phase_key(phase)
    return {'name': name, 'card': card, 'db': db}


# The trace rule a new report starts with (the author can change the percent or clear it).
DEFAULT_TRACE_PERCENT = 5


def check_percent(percent) -> float:
    """The author's threshold: a number from 0 to 100 (booleans and NaN are rejected)."""
    if type(percent) not in (int, float) or percent != percent or not 0 <= percent <= 100:
        raise ValueError('미량상 기준 비율은 0 이상 100 이하의 숫자입니다.')
    return float(percent)


def _representatives(sample: dict, phase: dict):
    """(first, second, reason): reference rows of the two strongest distinct positions. Rows at
    one position are one peak whose strength is the largest Norm. I. among them."""
    by_x: dict = {}
    for row in sample.get('reference_peaks', []):
        if row['phase'] == phase['name'] and row.get('intensity') is not None:
            by_x.setdefault(row['x'], []).append(row)
    peaks = sorted(
        ((max(r['intensity'] for r in rows), x, rows) for x, rows in by_x.items()),
        key=lambda peak: -peak[0],
    )
    if len(peaks) < 2:
        return None, None, '대표 피크 부족 (d-I List에 피크가 둘 미만)'
    if peaks[0][0] == peaks[1][0]:
        return None, None, '동점 (가장 강한 피크가 둘 이상)'
    if len(peaks) > 2 and peaks[1][0] == peaks[2][0]:
        return None, None, '동점 (두 번째로 강한 피크가 둘 이상)'
    strongest = lambda peak: max(peak[2], key=lambda r: r['intensity'])  # noqa: E731
    return strongest(peaks[0]), strongest(peaks[1]), ''


def _observed(sample: dict, phase: dict, row: dict):
    """(observed peak, reason) linked through the report's own assignment of this hkl."""
    hkl = _hkl(row.get('hkl_raw'))
    linked = [
        a
        for a in sample.get('assignments', [])
        for m in a['matches']
        if m['phase'] == phase['name'] and _hkl(m['hkl']) == hkl
    ]
    if not linked:
        return None, '관측 피크 연결 안 됨 (배정 없음)'
    if len(linked) > 1:
        return None, '연결이 여러 개 (같은 hkl 배정이 둘 이상)'
    found = [
        p
        for p in sample.get('observed_peaks', [])
        if (p['no'], p['x']) == (linked[0]['no'], linked[0]['x'])
    ]
    if len(found) != 1:
        return None, '관측 피크 연결 안 됨 (Peak List에 없음)'
    return found[0], ''


def trace_check(sample: dict, phase: dict, percent) -> dict:
    """Verdict for one phase: `trace` (both representative peaks are at most `percent` of the
    sample's strongest observed peak), `not_trace`, or `undecided` with the reason."""
    limit = check_percent(percent)
    heights = [pdf_number(p.get('height_raw')) for p in sample.get('observed_peaks', [])]
    top = max((h for h in heights if h is not None), default=0)
    first, second, reason = _representatives(sample, phase)
    if reason:
        return {'status': 'undecided', 'reason': reason, 'peaks': []}
    peaks = []
    within = True
    for role, row in (('first', first), ('second', second)):
        observed, why = _observed(sample, phase, row)
        height = pdf_number(observed.get('height_raw')) if observed else None
        if observed is not None and height is None:
            why = 'Height 읽지 못함'
        if why:
            return {'status': 'undecided', 'reason': f'{role}: {why}', 'peaks': peaks}
        if top <= 0:
            return {'status': 'undecided', 'reason': '시료의 최대 Height가 0 이하', 'peaks': peaks}
        # Compared in decimal so a boundary value such as 1.1 against 0.11% is not lost to
        # binary rounding; the float ratio is only for display.
        within = within and Decimal(str(height)) * 100 <= Decimal(str(limit)) * Decimal(str(top))
        peaks.append(
            dict(
                role=role,
                x=row['x'],
                hkl=_hkl(row.get('hkl_raw')),
                norm_raw=row.get('intensity_raw'),
                height_raw=observed['height_raw'],
                ratio=100 * height / top,
            )
        )
    return {'status': 'trace' if within else 'not_trace', 'reason': '', 'peaks': peaks}


def evaluate_trace(sample: dict, percent) -> dict:
    """trace_check for every phase of the sample, keyed by (name, card, db)."""
    check_percent(percent)
    return {phase_key(p): trace_check(sample, p, percent) for p in sample['phases']}


CLASS_LABELS = {'major': '주요상', 'trace': '미량상', 'presumed': '추정상'}

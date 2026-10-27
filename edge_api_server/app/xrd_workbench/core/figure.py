"""Comparison-figure planning: phase merging, similarity order, exclusions and marker positions.

Pure computation on the project and report document. The renderer only draws what the plan
says; nothing here creates a measurement, a peak or a phase assignment."""

import math
import json
import re
from bisect import bisect_left

from .sample_colors import sample_color
# Filled markers, the same as the viewer's: a colour per phase and shapes in the order square,
# triangle, star, diamond, circle, plus, ... (names the renderer maps).
PHASE_COLORS = ['#8261c9', '#df9b29', '#199c78', '#c44d76', '#4178bf', '#777777']
PHASE_SYMBOLS = [
    'square',
    'triangle',
    'star',
    'diamond',
    'circle',
    'plus',
    'triangle_down',
    'hexagon',
]
MERGE_NOTE = (
    '상 이름·Card 번호·데이터베이스가 모두 같은 상만 한 줄·같은 마커로 묶었습니다. '
    '한 시료 안의 상은 묶지 않습니다.'
)
SPACING_RATIO = 0.15
NORMALIZED_SPACING = 15
MARKER_GAP_RATIO = 0.018


def phase_key(phase: dict) -> tuple[str, str, str]:
    """Identity of a phase across samples: name, database card number and database must all match."""
    return (phase['name'], phase.get('card') or '', phase.get('db') or '')


def group_key(phase: dict) -> str:
    """The viewer's grouping of a phase across samples: the set of elements in its chemical
    formula (Ti0.91 and Ti0.9 match), or the lower-cased name when there is no formula."""
    return json.dumps(phase_key(phase), ensure_ascii=False, separators=(',', ':'))


def sample_group_keys(sample: dict) -> list[str]:
    """Group key of each phase of a sample. Two phases of one sample are never one group: the
    second one with the same key becomes `key#1` (and pairs with other samples' second one)."""
    seen: dict = {}
    keys = []
    for phase in sample['phases']:
        base = group_key(phase)
        n = seen.get(base, 0)
        seen[base] = n + 1
        keys.append(f'{base}#{sample["id"]}:{n}' if n else base)
    return keys


def observed_markers(sample: dict, phase: dict) -> list[dict]:
    """Observed peaks the report itself assigns to `phase` (same rule as the viewer's markers):
    the assignment needs an observed row with the same number and position, inside the
    measured x range."""
    xs = sample['measurement']['x']
    observed = {(p['no'], p['x']) for p in sample.get('observed_peaks', [])}
    result = []
    for assignment in sample.get('assignments', []):
        index = next(
            (i for i, m in enumerate(assignment['matches']) if m['phase'] == phase['name']), None
        )
        if index is None or (assignment['no'], assignment['x']) not in observed:
            continue
        if assignment['x'] < xs[0] or assignment['x'] > xs[-1]:
            continue
        result.append(dict(no=assignment['no'], x=assignment['x'], match_index=index))
    return result


def phase_key_set(sample: dict) -> set:
    """Phases of the sample that have at least one marked observed peak."""
    return {phase_key(p) for p in sample['phases'] if observed_markers(sample, p)}


def similarity(a: set, b: set) -> float:
    """Shared phases divided by all phases of the two samples; 0 when either has none."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def similarity_order(ids: list[str], keysets: dict) -> list[str]:
    """Start at the first sample (report order) that has phases, then repeatedly append the most
    similar remaining sample; ties go to the earlier report position. Samples without phases
    come last in report order."""
    pending = [i for i in ids if keysets[i]]
    empty = [i for i in ids if not keysets[i]]
    if not pending:
        return list(ids)
    order = [pending.pop(0)]
    while pending:
        last = keysets[order[-1]]
        best = max(range(len(pending)), key=lambda i: (similarity(last, keysets[pending[i]]), -i))
        order.append(pending.pop(best))
    return order + empty


def identical_groups(ids: list[str], keysets: dict) -> list[list[str]]:
    """Groups of two or more samples whose phase sets are exactly equal (non-empty only)."""
    groups: dict = {}
    for sample_id in ids:
        if keysets[sample_id]:
            groups.setdefault(frozenset(keysets[sample_id]), []).append(sample_id)
    return [g for g in groups.values() if len(g) > 1]


def composition_note(previous, current: set) -> str:
    """How a sample's phase set differs from the previous curve's ('' for the first curve)."""
    if previous is None:
        return ''
    if previous == current:
        return '앞 시료와 상 구성 동일'
    added = sorted(k[0] for k in current - previous)
    removed = sorted(k[0] for k in previous - current)
    return ', '.join([f'+{n}' for n in added] + [f'−{n}' for n in removed])


def ready_samples(samples: list[dict], normalized: bool):
    """Split samples into drawable ones and (sample, reason) exclusions (the viewer's rules)."""
    ready, excluded = [], []
    axis = unit = None
    for sample in samples:
        m = sample['measurement']
        if not m.get('axis_kind') or not m.get('axis_unit') or not m.get('intensity_unit'):
            excluded.append((sample, '축 또는 강도 단위 미확인'))
            continue
        if normalized and max(m['y']) <= 0:
            excluded.append((sample, '최대 강도 ≤ 0, 정규화 불가'))
            continue
        coordinate = (m['axis_kind'], m['axis_unit'])
        if axis and coordinate != axis:
            excluded.append((sample, '가로축 종류·단위가 달라 비교에서 제외'))
            continue
        if unit and not normalized and unit != m['intensity_unit']:
            excluded.append((sample, '강도 단위가 달라 원강도 비교에서 제외'))
            continue
        axis, unit = coordinate, m['intensity_unit']
        ready.append(sample)
    return ready, excluded


def stack_offsets(count: int, spacing: float) -> list[float]:
    """Vertical offset per curve; the first curve is the top one."""
    return [(count - 1 - i) * spacing for i in range(count)]


def panel_slice(panel: dict, start: int, stop: int) -> dict:
    """The same panel limited to curves[start:stop] (axis, spacing and phase rows unchanged)."""
    return dict(panel, curves=panel['curves'][start:stop])


def _nearest(xs: list[float], x: float) -> int:
    i = bisect_left(xs, x)
    if i == 0:
        return 0
    if i == len(xs):
        return len(xs) - 1
    return i - 1 if abs(xs[i - 1] - x) < abs(xs[i] - x) else i


def phase_style(index: int) -> tuple[str, str]:
    """Colour and symbol of the index-th distinct phase of the report. The pair is unique for the
    first 36 phases (colour cycles every 6, the symbol shifts once per cycle)."""
    return (
        PHASE_COLORS[index % len(PHASE_COLORS)],
        PHASE_SYMBOLS[(index % 6 + index // 6) % len(PHASE_SYMBOLS)],
    )


def _report_styles(project_samples: dict, order: list[str]) -> dict:
    """Style per phase key, numbered by first appearance over every included sample in report
    order, so a phase looks the same in every group panel and in every order mode."""
    styles: dict = {}
    for sample_id in order:
        for key in sample_group_keys(project_samples[sample_id]):
            styles.setdefault(key, phase_style(len(styles)))
    return styles


def _phase_rows(ready: list[dict], styles: dict):
    """One row per phase group. The name, card and reference peaks come from the first member that
    has d-I rows (else the first member); every other differently named member is listed in `alt`
    so the caption never names one phase for another's markers."""
    members: dict = {}
    for sample in ready:
        for phase, key in zip(sample['phases'], sample_group_keys(sample), strict=True):
            members.setdefault(key, []).append((sample, phase))

    def reference(sample, phase):
        return [
            [p['x'], p['intensity']]
            for p in sample.get('reference_peaks', [])
            if p['phase'] == phase['name'] and p.get('intensity') is not None
        ]

    rows: list[dict] = []
    row_of: dict = {}
    for key, group in members.items():
        sample, phase = next(((s, p) for s, p in group if reference(s, p)), group[0])
        label = lambda p: f"{p['name']} · #{p.get('card') or ''}".rstrip(' ·#')  # noqa: E731
        alt = []
        for _, other in group:
            if (other['name'], other.get('card') or '') != (phase['name'], phase.get('card') or ''):
                if label(other) not in alt:
                    alt.append(label(other))
        row_of[key] = len(rows)
        rows.append(
            dict(
                key=key,
                name=phase['name'],
                formula=str(phase.get('formula') or '').strip(),
                card=phase.get('card') or '',
                db=phase.get('db') or '',
                color=styles[key][0],
                symbol=styles[key][1],
                ref=reference(sample, phase),
                alt=alt,
            )
        )
    return rows, row_of


def _panel(title, ids, project_samples, names, figure, styles):
    colors = {sid: sample_color(i) for i, sid in enumerate(project_samples)}
    normalized = figure['normalize']
    ready, skipped = ready_samples([project_samples[i] for i in ids], normalized)
    keysets = {s['id']: phase_key_set(s) for s in ready}
    ordered = [s['id'] for s in ready]
    if figure['order_mode'] == 'similarity':
        ordered = similarity_order(ordered, keysets)
    ready = [project_samples[i] for i in ordered]
    first = ready[0]['measurement'] if ready else None
    spans = [max(s['measurement']['y']) - min(s['measurement']['y']) for s in ready]
    if figure['spacing'] is not None:
        spacing = figure['spacing']
    elif normalized:
        spacing = NORMALIZED_SPACING
    else:
        spacing = round(max([1, *spans]) * SPACING_RATIO)
    if figure['x_range'] is not None:
        x_range = list(figure['x_range'])
    elif ready:
        # Whole degrees, rounded outwards (e.g. 10–90), like the viewer's default range.
        x_range = [
            math.floor(min(s['measurement']['x'][0] for s in ready)),
            math.ceil(max(s['measurement']['x'][-1] for s in ready)),
        ]
    else:
        x_range = [0, 1]
    rows, row_of = _phase_rows(ready, styles)
    curves, previous = [], None
    for index, sample in enumerate(ready):
        m = sample['measurement']
        top = max(m['y'])
        base = [100 * v / top for v in m['y']] if normalized else list(m['y'])
        gap = max(1, max(base) - min(base)) * MARKER_GAP_RATIO
        markers = []
        for phase, key in zip(sample['phases'], sample_group_keys(sample), strict=True):
            for mark in observed_markers(sample, phase):
                i = _nearest(m['x'], mark['x'])
                markers.append(
                    dict(
                        x=mark['x'],
                        y=base[i] + gap * (mark['match_index'] + 1),
                        phase_row=row_of[key],
                    )
                )
        current = keysets[sample['id']]
        curves.append(
            dict(
                sample_id=sample['id'],
                name=names[sample['id']],
                x=list(m['x']),
                base=base,
                color=colors[sample['id']],
                note=composition_note(previous, current),
                markers=markers,
            )
        )
        previous = current
    if first and first['axis_kind'] == 'two_theta' and first['axis_unit'] == 'deg':
        x_label = '2θ (deg)'
    elif first:
        x_label = f"{first['axis_kind']} ({first['axis_unit']})"
    else:
        x_label = ''
    unit = first['intensity_unit'] if first else ''
    return dict(
        title=title,
        normalized=normalized,
        spacing=spacing,
        x_range=x_range,
        x_label=x_label,
        y_label='상대 강도 (최대 = 100)' if normalized else f'Intensity ({unit})',
        show_markers=figure['show_markers'],
        show_reference=figure['show_reference'],
        curves=curves,
        phases=rows,
        excluded=[
            {'sample_id': s['id'], 'name': names[s['id']], 'reason': why} for s, why in skipped
        ],
        merge_note=MERGE_NOTE,
    )


def build_plan(project: dict, document: dict) -> dict:
    """Panels to draw: one per user group (leftovers in '기타'), or one for every included
    sample when there are no groups. Curves follow the report order, or similarity order."""
    figure = document['figure']
    by_id = {s['id']: s for s in project['samples']}
    entries = [e for e in document['samples'] if e['included']]
    names = {e['sample_id']: e['display_name'] for e in entries}
    order = [e['sample_id'] for e in entries]
    grouped = []
    for group in figure['groups']:
        members = set(group['samples'])
        ids = [i for i in order if i in members]
        if ids:
            grouped.append((group['title'], ids))
    if grouped:
        used = {i for _, ids in grouped for i in ids}
        rest = [i for i in order if i not in used]
        if rest:
            grouped.append(('기타', rest))
    else:
        grouped = [('', order)]
    styles = _report_styles(by_id, order)
    return {'panels': [_panel(t, ids, by_id, names, figure, styles) for t, ids in grouped]}


def similarity_summary(project: dict, document: dict) -> dict:
    """Included samples in similarity order, groups with identical phase sets, and per-sample
    composition notes against the previous sample in REPORT order (the order the editor lists)."""
    by_id = {s['id']: s for s in project['samples']}
    ids = [e['sample_id'] for e in document['samples'] if e['included']]
    keysets = {i: phase_key_set(by_id[i]) for i in ids}
    order = similarity_order(ids, keysets)
    notes, previous = {}, None
    for sample_id in ids:
        notes[sample_id] = composition_note(previous, keysets[sample_id])
        previous = keysets[sample_id]
    return dict(order=order, identical=identical_groups(ids, keysets), notes=notes)

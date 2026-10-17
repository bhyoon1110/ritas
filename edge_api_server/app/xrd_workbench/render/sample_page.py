"""The one slide of each sample and the remark lines of the first page.

A sample slide has four sections: phase identification and phase fraction (each with its source
image when included) side by side, then the author's major/trace phases and presumed phases.
Everything is shrunk until it fits one slide; long data lists are cut with a pointer to the
original Report, author text never is. Remarks are not on the slide: they are in the sample's
block on the first page or on the remark pages that follow it."""

from ..core.phase_class import phase_ref
from ..render import theme
from ..render.fonts import text_width
from ..render.items import text_item
from ..render.theme import BOTTOM, LEFT, WIDTH

SECTION_TITLES = (
    '상 동정 (Phase Identification) 결과',
    '정량 (Phase Fraction) 결과',
    '주요상 및 미량상',
    '추정상',
)
# Tried in order until the slide fits: first only smaller, then smaller with shorter lists.
SCALES = (1, 0.9, 0.8, 0.7, 0.6)
LIST_LIMITS = (30, 20, 12, 8, 5, 3)
LINK_SIZE = 9
LINK_ORDER = ('상매칭 Report', '정량(WPPF) Report', 'Measured Data (TXT)')
LINK_H = 6
LINK_GAP = 6
PT_PER_MM = 2.83464567


def clip(lines, limit, noun):
    if limit is None or len(lines) <= limit:
        return lines
    return lines[:limit] + [f'외 {len(lines) - limit}개 {noun} (원본 Report 참조)']


def phase_lines(sample, limit=None):
    phases = sample['phases']
    if not phases:
        return [
            '상 Report 등록 · 추출/제외 상태 확인 필요'
            if sample.get('reports', {}).get('phase_report')
            else '상 목록 미제공'
        ]
    lines = []
    for p in phases:
        parts = [p['name']]
        if p.get('formula'):
            parts.append(p['formula'])
        if p.get('card'):
            parts.append(f"{p.get('db') or 'Card'} {p['card']}")
        lines.append(' · '.join(parts))
    return clip(lines, limit, '상')


def quantity_label(sample):
    review = sample.get('analysis_review', {})
    if review.get('quantity_source') == 'manual':
        return '정량 (직접 입력)'
    return '정량 (추출·검토)' if review.get('changes') else '정량(WPPF) Report'


def quantity_status(sample):
    """Why there is no quantity to show: not provided versus registered but not read."""
    if sample.get('reports', {}).get('wppf_report'):
        return '정량 Report 등록 · 값 추출 확인 필요'
    if any(r['role'] == 'quantity_image' for r in sample['sources']):
        return '정량 이미지 등록 · 수치 확인 필요'
    return '정량 자료 미제공'


def quantity_lines(sample, limit=None):
    quantities = sample.get('quantities', [])
    if not quantities:
        return [quantity_status(sample)]
    values = [f"{q['phase']}: {q['raw']} {q['unit']}" for q in quantities]
    return ['출처: ' + quantity_label(sample)] + clip(values, limit, '값')


def phase_quantity_text(sample, phase):
    """Original quantity token of a phase, or why there is none: 미제공 (no quantity material
    registered) versus 확인 필요 (material registered but no value for this phase). Never 0."""
    quantity = phase.get('quantity')
    if quantity:
        return f"{quantity['raw']} {quantity['unit']}"
    registered = (
        sample.get('quantities')
        or sample.get('reports', {}).get('wppf_report')
        or any(r['role'] == 'quantity_image' for r in sample.get('sources', []))
    )
    return '확인 필요' if registered else '미제공'


def class_lines(sample, entry_labels, classes, limit=None):
    """(major/trace lines, presumed lines) of the author's classification. Criteria text is the
    author's and is never shortened; the member lists are."""
    by_ref = {tuple(x['phase'].values()): x['class'] for x in entry_labels}
    criteria = classes['criteria_text'].strip()

    def members(cls):
        return [
            f'{p["name"]} ({phase_quantity_text(sample, p)})'
            for p in sample['phases']
            if by_ref.get(tuple(phase_ref(p).values())) == cls
        ]

    def joined(title, items):
        if limit is not None and len(items) > limit:
            items = items[:limit] + [f'외 {len(items) - limit}개 상 (원본 Report 참조)']
        return f'{title}: ' + '; '.join(items)

    main = [criteria] if criteria else []
    # Each rule label states the percent it was applied with (a later run may have used another).
    for percent in sorted({x['percent'] for x in entry_labels if x['origin'] == 'rule'}):
        main.append(
            '적용 기준: 미량상 = d-I List의 최강·차강 피크에 연결된 관측 Height가 '
            f'시료 최대 Height의 {percent:g}% 이하'
        )
    classified = []
    # A phase nobody classified is a major phase (the default); only the author picks 추정상.
    for classes_of, title in ((('major',), '주요상'), (('trace',), '미량상'), ((None,), '미분류 (검토 필요)')):
        # the sample's own phase order within a title
        found = [
            f'{p["name"]} ({phase_quantity_text(sample, p)})'
            for p in sample['phases']
            if by_ref.get(tuple(phase_ref(p).values())) in classes_of
        ]
        if found:
            classified.append(joined(title, found))
    main += classified or ['분류할 상이 없습니다.']
    presumed = members('presumed')
    return main, [joined('추정상', presumed) if presumed else '작성자가 지정한 추정상이 없습니다.']


def remarks(entry):
    """[(text, evidence line)] of the selected remarks in order, then the author's free note."""
    notes = [n for n in entry['notes'] if n['selected'] and n['text'].strip()]
    notes += [
        dict(n, text='이전 분석에 대한 사용자 문장: ' + n['text'])
        for n in entry.get('retained_notes', [])
        if n['selected'] and n['text'].strip()
    ]
    out = []
    for i, note in enumerate(notes, 1):
        evidence = []
        for ref in note.get('evidence', []):
            label = ref.get('filename', '') + (f" p.{ref['page']}" if ref.get('page') else '')
            if label and label not in evidence:
                evidence.append(label)
        out.append((f'{i}. ' + note['text'], '근거: ' + '; '.join(evidence) if evidence else ''))
    if entry['manual_note'].strip():
        out.append((entry['manual_note'], ''))
    return out


def remark_items(rows, x, y, w, scale, heading_size):
    """Items of a '특이사항' block from `y`; returns (items, bottom)."""
    items = [text_item('특이사항', x, y, w, heading_size, True, theme.NAVY)]
    y += items[0]['h'] + 1.5 * scale
    for text, evidence in rows:
        item = text_item(text, x, y, w, 9 * scale)
        items.append(item)
        y += item['h'] + 0.8 * scale
        if evidence:
            ref = text_item(evidence, x, y, w, 7.5 * scale, color=theme.NOTE)
            items.append(ref)
            y += ref['h'] + 1.5 * scale
    return items, y


def link_row(name, links, y):
    """The slide's last row: '<name>의 Report 및 데이터 확인하기' to the main Report, then each
    original file."""
    if not links:
        return []
    links = sorted(links, key=lambda link: LINK_ORDER.index(link['label']))

    def width(label):
        return text_width(label + ' →', LINK_SIZE) / PT_PER_MM + 2

    # The file links always fit; a long sample name is shortened inside the first label.
    room = WIDTH - sum(width(link['label']) + LINK_GAP for link in links)
    suffix = '의 Report 및 데이터 확인하기'
    label = name + suffix
    while width(label) > room and len(name) > 1:
        name = name[:-1]
        label = name.rstrip() + '…' + suffix
    items, x = [], LEFT
    for link in [dict(links[0], label=label)] + links:
        w = width(link['label'])
        items.append(dict(type='link', **link, x=x, y=y, w=w, h=LINK_H, size=LINK_SIZE))
        x += w + LINK_GAP
    return items


def layout_slide(top, sample, labels, classes, images, scale, limit):
    """Items of one sample slide at `scale`, or None when they do not fit above the link row."""
    s = scale
    gap = 8
    col_w = (WIDTH - gap) / 2
    bottom = BOTTOM - LINK_H - 2
    items = []

    def column(x, y, title, image, lines):
        heading = text_item(title, x, y, col_w, 11 * s, True, theme.NAVY)
        items.append(heading)
        y += heading['h'] + 1.5 * s
        if image:
            width, height = image['size']
            image_w = min(col_w, theme.SLIDE_IMAGE_MAX_H * s * width / height)
            image_h = image_w * height / width
            items.append(
                dict(
                    type='image',
                    data=image['data'],
                    mime=image['mime'],
                    x=x + (col_w - image_w) / 2,
                    y=y,
                    w=image_w,
                    h=image_h,
                    source_id=image['record']['id'],
                    filename=image['record']['filename'],
                    original_size=[width, height],
                )
            )
            y += image_h + 1
            caption = text_item(image['caption'], x, y, col_w, 8 * s, color=theme.CAPTION)
            items.append(caption)
            y += caption['h'] + 2 * s
        for line in lines:
            item = text_item(line, x, y, col_w, 9 * s)
            items.append(item)
            y += item['h'] + 0.8 * s
        return y

    right = LEFT + col_w + gap
    y = max(
        column(LEFT, top, SECTION_TITLES[0], images.get('phase_image'), phase_lines(sample, limit)),
        column(
            right,
            top,
            SECTION_TITLES[1],
            images.get('quantity_image'),
            quantity_lines(sample, limit),
        ),
    )
    main, presumed = class_lines(sample, labels, classes, limit)
    y += 5 * s
    y = max(
        column(LEFT, y, SECTION_TITLES[2], None, main),
        column(right, y, SECTION_TITLES[3], None, presumed),
    )
    return items if y <= bottom else None


def place_sample(page, entry, sample, labels, classes, images, links):
    """Fill `page` with the sample's slide, shrinking (and then shortening data lists) until it
    fits."""
    attempts = [(s, None) for s in SCALES] + [(SCALES[-1], n) for n in LIST_LIMITS]
    for scale, limit in attempts:
        items = layout_slide(page['content_top'], sample, labels, classes, images, scale, limit)
        if items is not None:
            page['items'] += items + link_row(entry['display_name'], links, BOTTOM - LINK_H)
            return
    raise ValueError(
        f"{entry['display_name']}: 시료 페이지가 한 장을 넘습니다. " '분류 기준 문구를 줄이세요.'
    )

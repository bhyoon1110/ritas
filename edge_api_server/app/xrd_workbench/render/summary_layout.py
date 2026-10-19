"""Deterministic XRD summary pagination and reviewed phase-class notices."""
from ..core.phase_class import phase_ref
from .fonts import wrap_lines

NOTICE = ('XRD 분석 결과만으로 미량상 및 추정상의 명확한 상 동정에 한계가 있으므로, '
          '원소 성분 분석 결과(XRF / SEM-EDS / ICP 등)를 고려한 종합적인 판단이 필요합니다.')
LABELS = {'major': '주요상', 'trace': '미량상', 'presumed': '추정상', None: '미분류'}


def classifier(document):
    def key(phase):
        return tuple(phase.get(k, '') for k in ('name','card','db'))
    labels = {(v['sample_id'], key(v['phase'])): v['class']
              for v in document['phase_classes']['labels']}
    def lookup(sample, phase):
        return labels.get((sample['id'], key(phase_ref(phase))))
    return lookup


def notice(entries, samples, classification):
    """Only actual reviewed trace/presumed classifications trigger a notice."""
    groups = {('trace',): [], ('presumed',): [], ('trace', 'presumed'): []}
    for entry in entries:
        sample = samples[entry['sample_id']]
        present = {classification(sample, p) for p in sample['phases']}
        key = tuple(k for k in ('trace', 'presumed') if k in present)
        if key:
            groups[key].append('[' + entry['display_name'] + ']')
    sentences = [', '.join(names) + ' 샘플에서 ' + ' 및 '.join(LABELS[k] for k in key)
                 + '이 존재합니다.' for key, names in groups.items() if names]
    return '\n'.join(sentences), NOTICE if sentences else ''


def summary_pages(entries, samples, classification, *, width=55.8, height=139):
    """Fit two-column rows, splitting long sample content without dropping any text."""
    cards = []
    for entry in entries:
        sample = samples[entry['sample_id']]
        lines = []
        for cls, label in LABELS.items():
            phases = [p for p in sample['phases'] if classification(sample, p) == cls]
            if not phases:
                continue
            lines.append((label, True))
            for index, phase in enumerate(phases, 1):
                formula = phase.get('formula') or ''
                value = f'{formula} ({phase["name"]})' if formula else phase['name']
                lines.extend((line, False) for line in wrap_lines(f'{index}. {value}', width, 8.5))
        if not lines:
            lines = [('등록된 결정상 정보 없음', False)]
        title = wrap_lines('샘플명: ' + entry['display_name'], width, 10, True)
        title_h = len(title) * 4.5
        capacity = max(1, int((height - title_h - 12) // 4))
        start = 0
        while start < len(lines):
            end = min(len(lines), start + capacity)
            if end < len(lines) and lines[end-1][1] and end-start > 1:
                end -= 1  # category heading travels with its first phase
            part = lines[start:end]
            cards.append(dict(entry=entry, title=title, continued=bool(start), lines=part,
                              height=title_h + len(part)*4 + 10))
            start = end
    pages, page, y = [], [], 0
    for start in range(0, len(cards), 2):
        row = cards[start:start+2]
        row_h = max(c['height'] for c in row)
        if page and y + row_h > height:
            pages.append(page); page=[]; y=0
        for column, card in enumerate(row):
            page.append(dict(card, column=column, top=y))
        y += row_h + 3
    if page or not pages:
        pages.append(page)
    return pages

"""Sample x phase quantity table for the report: original tokens only.

Columns are the phase names the quantity tables (WPPF Report or the author's own entries) use,
rows are the samples in the report. A cell is the original token and unit; when there is no value
it says why (not stated, needs checking, no quantity material) and never shows 0, a sum or a
re-scaled number."""

from ..core.parsing.values import normal
from ..core.phase_class import CLASS_LABELS

FIXED_FOOTNOTE = (
    '값은 각 시료의 정량 자료에 적힌 원문 표기입니다. 괄호 안 숫자는 원문 표기 그대로이며 '
    '의미를 해석하거나 합계를 맞추지 않았습니다.'
)
MANUAL_FOOTNOTE = '† 직접 입력한 값 (출처는 시료 쪽 참조)'


def quantity_material_registered(sample: dict) -> bool:
    """True when a quantity report, a quantity image or entered quantities exist for the sample."""
    return bool(
        sample.get('quantities')
        or sample.get('reports', {}).get('wppf_report')
        or any(r['role'] == 'quantity_image' for r in sample.get('sources', []))
    )


def _cell(text: str, kind: str, manual: bool = False) -> dict:
    return {'text': text, 'kind': kind, 'manual': manual}


def _value_cell(items: list[dict]) -> dict:
    """One cell from the entries of one phase: tokens kept as written, several joined, an unreadable
    one marked for checking."""
    pieces, manual = [], False
    for item in items:
        raw = item.get('raw')
        if raw is None or not str(raw).strip():
            pieces.append('확인 필요')
            continue
        token = f"{str(raw).strip()} {item.get('unit') or ''}".strip()
        if item.get('origin') == 'manual':
            token += ' †'
            manual = True
        pieces.append(token)
    if all(p == '확인 필요' for p in pieces):
        return _cell('확인 필요', 'check')
    return _cell('; '.join(pieces), 'value', manual)


def _class_notes(project: dict, document: dict, names: list[str]) -> dict:
    """Class of each phase name when every included sample's phase of that name agrees, else ''.
    A phase without a label has the default class (주요상)."""
    included = {e['sample_id'] for e in document['samples'] if e['included']}
    labelled = {
        (x['sample_id'], tuple(x['phase'].values())): x['class']
        for x in document.get('phase_classes', {}).get('labels', [])
    }
    found: dict = {name: set() for name in names}
    for sample in project['samples']:
        if sample['id'] not in included:
            continue
        for phase in sample['phases']:
            name = normal(phase['name'])
            if name in found:
                ref = (phase['name'], phase.get('card') or '', phase.get('db') or '')
                found[name].add(labelled.get((sample['id'], ref), ''))
    return {n: f'({CLASS_LABELS[next(iter(c))]})' if len(c) == 1 and '' not in c else '' for n, c in found.items()}


def build_quantity_table(project: dict, document: dict) -> dict:
    """The table as plain data: `columns` [{name, class_note}], `rows` [{sample_id, name, cells
    [{text, kind, manual}]}], `footnotes` and, when there is no column, `empty_message`."""
    by_id = {s['id']: s for s in project['samples']}
    entries = [e for e in document['samples'] if e['included']]
    names: list[str] = []
    for entry in entries:
        for item in by_id[entry['sample_id']].get('quantities', []):
            # Same identity rule as the project model: surrounding whitespace is not a different phase.
            if normal(item['phase']) not in names:
                names.append(normal(item['phase']))
    notes = _class_notes(project, document, names)
    rows = []
    for entry in entries:
        sample = by_id[entry['sample_id']]
        items = sample.get('quantities', [])
        cells = []
        for index, name in enumerate(names):
            if not quantity_material_registered(sample):
                cells.append(_cell('정량 자료 없음', 'none') if index == 0 else _cell('', 'blank'))
            elif not items:
                cells.append(_cell('확인 필요', 'check'))
            else:
                mine = [i for i in items if normal(i['phase']) == name]
                cells.append(_value_cell(mine) if mine else _cell('미기재', 'missing'))
        rows.append(
            {'sample_id': entry['sample_id'], 'name': entry['display_name'], 'cells': cells}
        )
    footnotes = [FIXED_FOOTNOTE]
    if any(c['manual'] for r in rows for c in r['cells']):
        footnotes.insert(0, MANUAL_FOOTNOTE)
    empty = ''
    if not names:
        registered = any(quantity_material_registered(by_id[e['sample_id']]) for e in entries)
        empty = (
            '정량 값을 읽은 시료가 없습니다. 각 시료의 원본 Report를 확인하세요.'
            if registered
            else '정량 자료가 등록된 시료가 없습니다.'
        )
    return {
        'columns': [{'name': n, 'class_note': notes[n]} for n in names],
        'rows': rows,
        'footnotes': footnotes,
        'empty_message': empty,
    }

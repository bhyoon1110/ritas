"""Review status and pre-export checklist: what still needs the author's attention.

Only states that already exist are collected (review flags, analysis changes, registered
material, the figure's own exclusion reasons, classification progress, source warnings). Nothing
here interprets measurements or changes the document; the author decides what to do."""

from ..core.figure import build_plan
from ..core.phase_class import evaluate_trace
from ..core.quantity_table import quantity_material_registered

LEVELS = ('blocker', 'warning', 'info')
# Order of the items of one sample within a level.
CODES = (
    'analysis_review_required',
    'not_reviewed',
    'figure_excluded',
    'no_phase_report',
    'quantity_unreadable',
    'source_warning',
    'no_quantity_material',
    'undecided_trace_phases',
    'no_notes',
)


def _item(level: str, code: str, text: str) -> dict:
    return {'level': level, 'code': code, 'text': text}


def _sorted(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda i: (LEVELS.index(i['level']), CODES.index(i['code'])))


def _figure_exclusions(project: dict, document: dict):
    """({sample_id: reason}, every panel empty) from the comparison figure's own plan."""
    figure = document.get('figure')
    if not figure or not figure.get('include'):
        return {}, False
    panels = build_plan(project, document)['panels']
    reasons = {e['sample_id']: e['reason'] for p in panels for e in p['excluded']}
    return reasons, bool(panels) and all(not p['curves'] for p in panels)


def _has_notes(entry: dict) -> bool:
    chosen = [n for n in entry['notes'] if n['selected'] and n['text'].strip()]
    retained = [n for n in entry.get('retained_notes', []) if n['selected'] and n['text'].strip()]
    return bool(chosen or retained or entry['manual_note'].strip())


def build_checklist(project: dict, document: dict) -> dict:
    """The checklist of the report: `summary` counts, `report` items and, for every sample in
    the report, its `items` (blocker, warning, info; each {level, code, text})."""
    by_id = {s['id']: s for s in project['samples']}
    entries = [e for e in document['samples'] if e['included']]
    classes = document.get('phase_classes') or {}
    rule = classes.get('rule')
    reasons, figure_empty = _figure_exclusions(project, document)
    samples = []
    for entry in entries:
        sample = by_id[entry['sample_id']]
        items = []
        if entry.get('analysis_review_required'):
            items.append(
                _item(
                    'blocker',
                    'analysis_review_required',
                    '분석값이 바뀌었습니다. 자료·문장 검토 완료를 다시 선택하고 저장해야 출력할 수 있습니다.',
                )
            )
        elif not entry['reviewed']:
            items.append(
                _item('warning', 'not_reviewed', '자료·문장 검토 완료를 선택하지 않았습니다.')
            )
        if sample['id'] in reasons:
            items.append(
                _item(
                    'warning',
                    'figure_excluded',
                    f'비교 그래프에서 제외됩니다: {reasons[sample["id"]]}',
                )
            )
        if not sample.get('reports', {}).get('phase_report'):
            items.append(
                _item(
                    'warning',
                    'no_phase_report',
                    '상매칭 Report가 없어 상 목록과 상 기호가 없습니다.',
                )
            )
        if not quantity_material_registered(sample):
            items.append(_item('info', 'no_quantity_material', '정량 자료가 없습니다.'))
        elif not sample.get('quantities'):
            items.append(
                _item(
                    'warning',
                    'quantity_unreadable',
                    '정량 값을 읽지 못했습니다. 원본을 확인하세요.',
                )
            )
        for text in dict.fromkeys(sample.get('warnings', [])):
            items.append(_item('warning', 'source_warning', text))
        if rule:
            # The verdict is independent of the report's default or manually chosen class.
            verdicts = evaluate_trace(sample, rule['trace_max_percent'])
            undecided = [v for v in verdicts.values() if v['status'] == 'undecided']
            if undecided:
                items.append(
                    _item(
                        'info',
                        'undecided_trace_phases',
                        f'미량상 규칙으로 판정하지 못한 상 {len(undecided)}개 (분류 검토 필요)',
                    )
                )
        if not _has_notes(entry):
            items.append(_item('info', 'no_notes', '저장된 검토 문장이 없습니다. 특이사항은 상 분류에서 자동 생성됩니다.'))
        samples.append(
            {
                'sample_id': sample['id'],
                'name': entry['display_name'],
                'reviewed': bool(entry['reviewed']),
                'items': _sorted(items),
            }
        )
    report = []
    if not entries:
        report.append(
            _item('blocker', 'no_included_samples', '보고서에 포함할 시료를 하나 이상 선택하세요.')
        )
    if figure_empty:
        report.append(
            _item(
                'warning',
                'figure_empty',
                '비교 그래프에 그릴 수 있는 시료가 없습니다. 제외 사유를 확인하세요.',
            )
        )
    everything = report + [i for s in samples for i in s['items']]
    return {
        'summary': {
            'samples': len(samples),
            'reviewed': sum(1 for s in samples if s['reviewed']),
            'blockers': sum(1 for i in everything if i['level'] == 'blocker'),
            'warnings': sum(1 for i in everything if i['level'] == 'warning'),
            'infos': sum(1 for i in everything if i['level'] == 'info'),
        },
        'report': report,
        'samples': samples,
    }

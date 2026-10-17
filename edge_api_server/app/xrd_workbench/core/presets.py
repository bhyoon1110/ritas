"""Settings presets: named bundles of report settings that are reused across reports.

A preset holds only settings that do not depend on a report's samples: the comparison figure
options (without groups), the quantity table switch, and the phase classification criteria text
with a default threshold for the trace rule. It never holds sample IDs, classes, cover
information, prose or layout styling. Applying a preset changes those settings in a copy of the
document; it never classifies a phase."""

from copy import deepcopy

from ..core.phase_class import check_percent
from ..core.report import (
    default_figure,
    default_phase_classes,
    default_quantity_table,
    string,
    validate_figure,
    validate_quantity_table,
)

NAME_MAX = 80
FIGURE_KEYS = (
    'include',
    'order_mode',
    'spacing',
    'x_range',
    'normalize',
    'show_markers',
    'show_reference',
    'caption',
)


def _clean_name(name) -> str:
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > NAME_MAX:
        raise ValueError(f'프리셋 이름은 1~{NAME_MAX}자로 입력하세요.')
    return name.strip()


def validate_preset(name, settings) -> dict:
    """The cleaned `{name, settings}` of a preset; anything else in `settings` is dropped."""
    settings = {} if settings is None else settings
    if not isinstance(settings, dict):
        raise ValueError('프리셋 설정 형식을 확인하세요.')
    figure = settings.get('figure', {})
    if isinstance(figure, dict) and figure.get('groups'):
        raise ValueError('프리셋에는 그룹(시료 선택)을 담을 수 없습니다.')
    if not isinstance(figure, dict):
        raise ValueError('그래프 설정 형식을 확인하세요.')
    clean_figure = validate_figure({k: v for k, v in figure.items() if k != 'groups'}, [])
    clean_table = (
        validate_quantity_table(settings['quantity_table'])
        if 'quantity_table' in settings
        else default_quantity_table(True)
    )
    classes = settings.get('phase_classes', {})
    if not isinstance(classes, dict):
        raise ValueError('상 분류 설정 형식을 확인하세요.')
    percent = classes.get('trace_percent')
    return {
        'name': _clean_name(name),
        'settings': {
            'figure': {k: clean_figure[k] for k in FIGURE_KEYS},
            'quantity_table': clean_table,
            'phase_classes': {
                'criteria_text': string(classes.get('criteria_text', ''), 1500, '분류 기준 문구'),
                'trace_percent': None if percent is None else check_percent(percent),
            },
        },
    }


def settings_from_document(document: dict) -> dict:
    """The preset settings of a report: figure options without groups, the table switch, the
    criteria text and the percent of the rule last applied (None when none was)."""
    figure = document.get('figure') or default_figure(False)
    classes = document.get('phase_classes') or default_phase_classes()
    rule = classes.get('rule')
    return {
        'figure': {k: deepcopy(figure[k]) for k in FIGURE_KEYS},
        'quantity_table': deepcopy(document.get('quantity_table') or default_quantity_table(True)),
        'phase_classes': {
            'criteria_text': classes['criteria_text'],
            'trace_percent': rule['trace_max_percent'] if rule else None,
        },
    }


def apply_preset(document: dict, settings: dict) -> dict:
    """A copy of the document with the preset's settings. Groups, classes, the applied rule,
    cover information and every sample's text stay as they are."""
    result = deepcopy(document)
    figure = result.setdefault('figure', default_figure(False))
    for key in FIGURE_KEYS:
        figure[key] = deepcopy(settings['figure'][key])
    result['quantity_table'] = deepcopy(settings['quantity_table'])
    classes = result.setdefault('phase_classes', default_phase_classes())
    classes['criteria_text'] = settings['phase_classes']['criteria_text']
    return result

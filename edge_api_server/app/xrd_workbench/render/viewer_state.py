"""Validated display choices only; measurements and phase facts are never accepted."""

import math
import re

from ..core.sample_colors import sample_color


def _highlight(value, project):
    """A phase highlight: None, or {name, card, db} with card/db a string or None, naming a phase
    that exists in `project`. card/db None means "any" (a link made from a name only)."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {'name', 'card', 'db'}:
        raise ValueError('뷰어 상 강조 설정을 확인하세요.')
    name = value['name']
    if not isinstance(name, str) or not name or len(name) > 300:
        raise ValueError('뷰어 상 강조의 상 이름을 확인하세요.')
    for key in ('card', 'db'):
        if value[key] is not None and (not isinstance(value[key], str) or len(value[key]) > 300):
            raise ValueError('뷰어 상 강조의 Card·데이터베이스를 확인하세요.')
    if not _highlight_matches(value, project):
        raise ValueError('강조할 상이 시료에 없습니다.')
    return {'name': name, 'card': value['card'], 'db': value['db']}


def _highlight_matches(value, project):
    return any(
        p['name'] == value['name']
        and (value['card'] is None or (p.get('card') or '') == value['card'])
        and (value['db'] is None or (p.get('db') or '') == value['db'])
        for s in project['samples']
        for p in s['phases']
    )


def prepare_views(project, views, target=None):
    """Named views to embed in a delivery viewer: a list of {name, state}. Each state goes through
    prepare_state (display choices only, samples left out of `target` dropped); a highlight on a
    phase that is not in `target` is dropped instead of refusing the whole delivery."""
    if views is None:
        return []
    if not isinstance(views, list):
        raise ValueError('전달할 보기 목록 형식을 확인하세요.')
    if len(views) > 20:
        raise ValueError('전달할 보기는 최대 20개입니다.')
    target = target or project
    seen, result = set(), []
    for item in views:
        if not isinstance(item, dict) or set(item) != {'name', 'state'}:
            raise ValueError('전달할 보기의 형식(이름, 상태)을 확인하세요.')
        name = item['name'].strip() if isinstance(item['name'], str) else ''
        if not name or len(name) > 80:
            raise ValueError('보기 이름은 1~80자로 입력하세요.')
        if name in seen:
            raise ValueError('보기 이름이 중복되었습니다.')
        seen.add(name)
        state = item['state']
        if not isinstance(state, dict) or not state:
            raise ValueError('보기 상태가 비어 있습니다.')
        state = dict(state)
        if state.get('highlight') is not None:
            clean = _highlight(state['highlight'], project)
            state['highlight'] = clean if _highlight_matches(clean, target) else None
        result.append({'name': name, 'state': prepare_state(project, state, target)})
    return result


def prepare_state(project, state=None, target=None):
    if state is None or state == {}:
        state = {'version': 1, 'projectId': project['id']}
    if (
        not isinstance(state, dict)
        or state.get('projectId') != project['id']
        or state.get('version', 1) != 1
    ):
        raise ValueError('뷰어 설정의 프로젝트·버전이 다릅니다.')
    target = target or project
    ids = [s['id'] for s in project['samples']]
    target_ids = [s['id'] for s in target['samples']]
    phases = [p['id'] for s in project['samples'] for p in s['phases']]
    target_phases = [p['id'] for s in target['samples'] for p in s['phases']]
    # Resolve defaults before filtering/reordering a delivery project. A report
    # containing only sample 8 must retain sample 8's colour in its offline viewer.
    result = {'version': 1, 'projectId': target['id'],
              'colors': {sid: sample_color(i) for i, sid in enumerate(ids) if sid in target_ids}}
    for key, allowed in {
        'intensity': ('raw', 'normalized'),
        'arrangement': ('stacked', 'overlay'),
        'phaseTarget': ('active', 'all'),
        'dragmode': ('zoom', 'pan'),
        'phaseVisibility': ('sample',),
    }.items():
        if key in state:
            if state[key] not in allowed:
                raise ValueError('뷰어 표시 옵션을 확인하세요.')
            result[key] = state[key]
    if 'offsetRule' in state:
        if state['offsetRule'] != 2:
            raise ValueError('뷰어 Offset 규칙을 확인하세요.')
        result['offsetRule'] = 2
    if 'viewMode' in state:
        if state['viewMode'] not in ('multi', 'single'):
            raise ValueError('뷰어 보기 방식을 확인하세요.')
        result['viewMode'] = state['viewMode']
    if 'markerSize' in state:
        size = state['markerSize']
        if type(size) not in (int, float) or not 3 <= size <= 20:
            raise ValueError('마커 크기는 3~20 사이 숫자입니다.')
        result['markerSize'] = size
    if 'unknownSymbol' in state:
        allowed = {'x', 'triangle-up', 'triangle-down', 'square', 'circle', 'diamond'} | {
            'cross',
            'star',
            'hexagon',
        }
        if state['unknownSymbol'] not in allowed:
            raise ValueError('알 수 없는 피크 마커 모양을 확인하세요.')
        result['unknownSymbol'] = state['unknownSymbol']
    if 'unknownColor' in state:
        color = state['unknownColor']
        if not isinstance(color, str) or not re.fullmatch('#[0-9a-fA-F]{6}', color):
            raise ValueError('알 수 없는 피크 마커 색은 #RRGGBB 값이어야 합니다.')
        result['unknownColor'] = color
    for key in ('unknownMarkers', 'phaseLabels', 'originStyle'):
        if key in state:
            if type(state[key]) is not bool:
                raise ValueError('뷰어 표시 옵션은 boolean이어야 합니다.')
            result[key] = state[key]
    for key in ('markers', 'peakLines', 'peakbar'):
        if key in state:
            if type(state[key]) is not bool:
                raise ValueError('Phase 표시 옵션은 boolean이어야 합니다.')
            result[key] = state[key]
    for key, owned, included in [
        ('visible', ids, target_ids),
        ('order', ids, target_ids),
        ('hiddenPhases', phases, target_phases),
        ('hiddenUnknownSamples', ids, target_ids),
    ]:
        if key not in state:
            continue
        values = state[key]
        if not isinstance(values, list) or any(
            not isinstance(v, str) or v not in owned for v in values
        ):
            raise ValueError('뷰어의 시료·상 선택을 확인하세요.')
        result[key] = list(dict.fromkeys(v for v in values if v in included))
    if 'highlight' in state:
        # A phase of a sample left out of the delivery cannot be highlighted there: drop the
        # highlight (a phase unknown to the whole project is still an error).
        clean = _highlight(state['highlight'], project)
        result['highlight'] = clean if clean is None or _highlight_matches(clean, target) else None
    if 'order' in result:
        result['order'] += [v for v in target_ids if v not in result['order']]
    visible = result.get('visible', target_ids)
    active = state.get('active')
    if active is not None and active not in ids:
        raise ValueError('뷰어 활성 시료가 다른 프로젝트입니다.')
    result['active'] = active if active in visible else next(iter(visible), None)
    if 'phaseSymbols' in state:
        symbols = state['phaseSymbols']
        allowed = {
            'triangle-up',
            'triangle-down',
            'square',
            'circle',
            'diamond',
            'cross',
            'star',
            'hexagon',
        }
        if not isinstance(symbols, dict) or any(
            k not in phases or v not in allowed for k, v in symbols.items()
        ):
            raise ValueError('상 마커 모양을 확인하세요.')
        result['phaseSymbols'] = {k: v for k, v in symbols.items() if k in target_phases}
    for key, owned, included in [
        ('colors', ids, target_ids),
        ('phaseColors', phases, target_phases),
    ]:
        if key not in state:
            continue
        colors = state[key]
        if not isinstance(colors, dict) or any(
            k not in owned or not isinstance(v, str) or not re.fullmatch('#[0-9a-fA-F]{6}', v)
            for k, v in colors.items()
        ):
            raise ValueError('뷰어 색상은 등록된 시료·상의 #RRGGBB 값이어야 합니다.')
        result.setdefault(key, {}).update({k: v for k, v in colors.items() if k in included})

    def number(v):
        try:
            return type(v) in (int, float) and math.isfinite(v)
        except OverflowError:
            return False

    if 'offsets' in state:
        offsets = state['offsets']
        if not isinstance(offsets, dict) or any(k not in ('raw', 'normalized') for k in offsets):
            raise ValueError('Offset 형식을 확인하세요.')
        result['offsets'] = {}
        for mode, value in offsets.items():
            if not isinstance(value, dict):
                raise ValueError('Offset 형식을 확인하세요.')
            clean = {}
            if 'spacing' in value:
                if not number(value['spacing']):
                    raise ValueError('Offset 간격은 유한한 숫자이어야 합니다.')
                clean['spacing'] = value['spacing']
            if 'individual' in value:
                individual = value['individual']
                if not isinstance(individual, dict) or any(
                    k not in ids or not number(v) for k, v in individual.items()
                ):
                    raise ValueError('개별 Offset과 시료를 확인하세요.')
                clean['individual'] = {k: v for k, v in individual.items() if k in target_ids}
            result['offsets'][mode] = clean
    for key in ('xRange', 'yRange'):
        if key not in state:
            continue
        value = state[key]
        if value is not None and (
            not isinstance(value, list)
            or len(value) != 2
            or not all(number(v) for v in value)
            or value[0] >= value[1]
            or not number(value[1] - value[0])
        ):
            raise ValueError('뷰어 축 범위는 유한한 시작·끝 숫자이어야 합니다.')
        result[key] = value[:] if value is not None else None
    # Check retained modes and hidden samples too: recipients can toggle them.
    samples = {s['id']: s for s in target['samples']}
    ordered = result.get('order', target_ids)
    span = max(
        [1] + [max(s['measurement']['y']) - min(s['measurement']['y']) for s in samples.values()]
    )
    if not number(span):
        raise ValueError('측정 강도 범위가 화면 표시 한계를 넘습니다.')
    for mode, default in [('raw', round(span * 0.15)), ('normalized', 15)]:
        offsets = result.get('offsets', {}).get(mode, {})
        spacing = offsets.get('spacing', default)
        individual = offsets.get('individual', {})
        for index, sid in enumerate(ordered):
            sample = samples[sid]
            values = sample['measurement']['y']
            lo = min(values)
            hi = max(values)
            if mode == 'normalized':
                if hi <= 0:
                    continue
                lo, hi = 100 * lo / hi, 100 * hi / hi
            # Same rule as the viewer (offsetFor): positive puts the first curve on top.
            count = len(ordered)
            step = (count - 1 - index) * spacing if spacing >= 0 else index * -spacing
            offset = step + individual.get(sid, 0)
            matches = max([0] + [len(a['matches']) for a in sample.get('assignments', [])])
            marker_gap = max(1, hi - lo) * 0.018 * matches
            if not all(
                number(v) for v in (offset, lo + offset, hi + offset, hi + offset + marker_gap)
            ):
                raise ValueError(
                    'Offset을 합친 표시 좌표가 유한한 숫자 범위를 넘습니다. 간격·개별 Offset을 줄이세요.'
                )
    return result

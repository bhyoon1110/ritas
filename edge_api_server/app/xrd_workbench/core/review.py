"""Analysis review: validated source-cell corrections, sourced manual quantities, and the
effective project they produce. Original rows and source bytes are never changed.
"""

import hashlib
import json
from copy import deepcopy

from ..core.parsing.pdf_report import derive_report, header_key, header_type, supported_peak_sections
from ..core.parsing.values import central, normal


def analysis_fingerprint(value):
    """Short stable hash of a JSON-able value, used to detect per-sample analysis changes."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:24]


BASE_FINGERPRINT = analysis_fingerprint({'tables': {}, 'manual': [], 'quantity_source': 'pdf'})


def new_review(project):
    """Empty review document for a project (revision 0, nothing changed)."""
    return {
        'schema_version': 1,
        'project_id': project['id'],
        'revision': 0,
        'tables': {},
        'manual_quantities': {},
        'quantity_source': {},
    }


def integer(value, label):
    """Return `value` if it is a non-negative int, else raise ValueError."""
    if type(value) is not int or value < 0:
        raise ValueError(label + '이 올바르지 않습니다.')
    return value


def string(value, limit, label, required=False):
    """Return `value` if it is a str within `limit` (non-blank when required), else raise ValueError."""
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(label + '을 확인하세요.')
    return value


def report_sources(project):
    """Map each registered phase/WPPF report source id to (sample, parsed report)."""
    return {
        r['id']: (sample, sample['reports'][r['role']])
        for sample in project['samples']
        for r in sample['sources']
        if r['role'] in ('phase_report', 'wppf_report') and r['role'] in sample['reports']
    }


def numeric_cell(table, row, col):
    """Whether a raw-table cell is a numeric field whose reviewed value must be a finite number."""
    rows = table['rows']
    headers = []
    kind = None
    for candidate in rows[: row + 1]:
        if header_type(candidate):
            headers = candidate
            kind = header_type(candidate)
    label = normal(headers[col]) if col < len(headers) else ''
    if kind == 'wppf' and col == 3:
        return central(rows[row][col]) is not None or (
            len(rows[row]) > 2 and 'Weight' in normal(rows[row][2])
        )
    return header_key(label) in ('2θ,°', 'd,å', 'norm.i.', 'height,cps', 'fom')


def validate_review(project, review):
    """Validate a review against its project and return a clean copy.

    Cell edits are allowed only on non-header cells of registered tables, numeric cells take
    finite numbers, and manual quantities must cite a source of the same sample."""
    if (
        not isinstance(review, dict)
        or review.get('schema_version') != 1
        or review.get('project_id') != project['id']
    ):
        raise ValueError('분석 검토와 원본 프로젝트가 다릅니다.')
    clean = new_review(project)
    clean['revision'] = integer(review.get('revision', 0), '검토 버전')
    sources = report_sources(project)
    tables = review.get('tables', {})
    if not isinstance(tables, dict):
        raise ValueError('표 검토 형식이 잘못되었습니다.')
    cell_count = 0
    for sid, entries in tables.items():
        if sid not in sources or not isinstance(entries, dict):
            raise ValueError('등록된 PDF 출처가 아닙니다.')
        clean['tables'][sid] = {}
        report = sources[sid][1]
        for ti, value in entries.items():
            if (
                not isinstance(ti, str)
                or not ti.isdigit()
                or str(int(ti)) != ti
                or int(ti) >= len(report['raw_tables'])
            ):
                raise ValueError('표 번호가 올바르지 않습니다.')
            if not isinstance(value, dict):
                raise ValueError('표 검토 형식이 잘못되었습니다.')
            status = value.get('status', 'unreviewed')
            if status not in ('unreviewed', 'reviewed', 'excluded'):
                raise ValueError('표 검토 상태가 잘못되었습니다.')
            table = report['raw_tables'][int(ti)]
            rows = table['rows']
            cells = value.get('cells', [])
            excluded = value.get('excluded_rows', [])
            if not isinstance(cells, list) or not isinstance(excluded, list):
                raise ValueError('셀·제외 행 형식이 잘못되었습니다.')
            entry = {'status': status, 'cells': [], 'excluded_rows': []}
            seen = set()
            for cell in cells:
                if not isinstance(cell, dict):
                    raise ValueError('셀 수정 형식이 잘못되었습니다.')
                row = integer(cell.get('row'), '셀 행')
                col = integer(cell.get('col'), '셀 열')
                if row >= len(rows) or col >= len(rows[row]) or (row, col) in seen:
                    raise ValueError('셀 범위 또는 중복 수정을 확인하세요.')
                if header_type(rows[row]):
                    raise ValueError('인식된 표 헤더는 수정할 수 없습니다.')
                after = cell.get('value')
                if after is not None:
                    string(after, 4000, '셀 값')
                reason = string(cell.get('reason', ''), 500, '수정 사유', True)
                if (
                    numeric_cell(table, row, col)
                    and normal(after) not in ('', 'None')
                    and central(after) is None
                ):
                    raise ValueError('유한한 숫자 또는 원문 빈 값/None을 입력하세요.')
                seen.add((row, col))
                cell_count += 1
                if cell_count > 10000:
                    raise ValueError('셀 수정은 최대 10000개입니다.')
                if after != rows[row][col]:
                    entry['cells'].append(
                        {
                            'row': row,
                            'col': col,
                            'value': after,
                            'original': rows[row][col],
                            'reason': reason,
                        }
                    )
            for row in excluded:
                row = integer(row, '제외 행')
                if row >= len(rows) or row in entry['excluded_rows'] or header_type(rows[row]):
                    raise ValueError('제외 행 범위·중복·헤더를 확인하세요.')
                entry['excluded_rows'].append(row)
            clean['tables'][sid][ti] = entry
    samples = {s['id']: s for s in project['samples']}
    selections = review.get('quantity_source', {})
    manual = review.get('manual_quantities', {})
    if not isinstance(selections, dict) or not isinstance(manual, dict):
        raise ValueError('정량 입력 형식을 확인하세요.')
    for sid, selection in selections.items():
        if sid not in samples or selection not in ('pdf', 'manual'):
            raise ValueError('정량 시료·출처 선택을 확인하세요.')
        clean['quantity_source'][sid] = selection
    for sid, values in manual.items():
        if sid not in samples or not isinstance(values, list) or len(values) > 100:
            raise ValueError('정량 시료·행 수를 확인하세요.')
        owned = {r['id']: r for r in samples[sid]['sources']}
        clean['manual_quantities'][sid] = []
        for value in values:
            if not isinstance(value, dict):
                raise ValueError('정량 입력 행을 확인하세요.')
            source = owned.get(value.get('source_id'))
            if not source or source['role'] not in ('quantity_image', 'wppf_report'):
                raise ValueError('같은 시료의 정량 이미지/WPPF PDF 출처를 지정하세요.')
            phase = string(value.get('phase'), 200, '정량 상 이름', True)
            raw = string(value.get('raw'), 80, '정량 원문 숫자', True)
            unit = string(value.get('unit'), 30, '정량 단위', True)
            number = central(raw)
            if number is None or number < 0 or (unit == 'wt%' and number > 100):
                raise ValueError('정량 숫자와 단위를 확인하세요. NaN/무한대는 사용할 수 없습니다.')
            page = value.get('page')
            if source['role'] == 'wppf_report':
                if type(page) is not int or not 1 <= page <= len(
                    samples[sid]['reports']['wppf_report']['page_texts']
                ):
                    raise ValueError('정량 PDF 출처 페이지를 확인하세요.')
            elif page is not None:
                raise ValueError('이미지 출처에는 PDF 페이지를 지정하지 않습니다.')
            if type(value.get('reviewed')) is not bool:
                raise ValueError('정량 입력 확인 상태가 필요합니다.')
            clean['manual_quantities'][sid].append(
                {
                    'phase': phase,
                    'raw': raw,
                    'unit': unit,
                    'source_id': source['id'],
                    'page': page,
                    'reason': string(value.get('reason', ''), 500, '정량 입력 사유', True),
                    'reviewed': value['reviewed'],
                    'source_filename': source['filename'],
                }
            )
    for sid, selection in clean['quantity_source'].items():
        if selection == 'manual' and (
            not clean['manual_quantities'].get(sid)
            or not all(v['reviewed'] for v in clean['manual_quantities'][sid])
        ):
            raise ValueError('직접 입력 정량을 사용하려면 출처와 모든 입력 행을 확인하세요.')
    return clean


def rebuild(report, entries, aliases=None):
    """Apply reviewed cells/exclusions to original sections, recovering supported peak
    headers missed by older parsers. Recognition never depends on edited cells."""
    work = deepcopy(report)
    work['logical_tables'] = []
    work['effective_tables'] = []
    table_indices = {(t['page'], tuple(t['bbox'])): i for i, t in enumerate(report['raw_tables'])}
    for ti, original in enumerate(report['raw_tables']):
        table = deepcopy(original)
        entry = entries.get(str(ti), {})
        table['status'] = entry.get('status', 'unreviewed')
        table['table_index'] = ti
        if table['status'] == 'reviewed':
            for cell in entry.get('cells', []):
                table['rows'][cell['row']][cell['col']] = cell['value']
        work['effective_tables'].append(table)
    # Only original peak headers may be recovered; edited unknown tables stay unknown.
    group = None
    reference_group = None
    for original in supported_peak_sections(report):
        ti = table_indices[(original['page'], tuple(original['bbox']))]
        table = work['effective_tables'][ti]
        entry = entries.get(str(ti), {})
        excluded = set(entry.get('excluded_rows', [])) if table['status'] == 'reviewed' else set()
        section = deepcopy(original)
        section.update(rows=[], row_indices=[], table_index=ti)
        if original['kind'] == 'reference':
            prefix = normal(original.get('prefix', ''))
            candidates = [
                (prefix.rfind(p['name']), p['name'])
                for p in report.get('phases', [])
                if p['name'] in prefix
            ]
            if candidates:
                reference_group = max(candidates)[1]
            section['origin_reference_phase'] = reference_group
        contexts = []
        for ri, old_row in zip(original['row_indices'], original['rows']):
            row = table['rows'][ri]
            hidden = table['status'] == 'excluded' or ri in excluded
            if original['kind'] == 'wppf':
                # Preserve original group boundaries even when their row is excluded.
                old_name = normal(old_row[1] if len(old_row) > 1 else '')
                old_parameter = normal(old_row[2] if len(old_row) > 2 else '')
                old_value = normal(old_row[3] if len(old_row) > 3 else '')
                if old_name and not old_parameter and not old_value:
                    group = None if hidden else normal((row[1] or '').replace('-\n', '-'))
            if hidden:
                continue
            section['rows'].append(row)
            section['row_indices'].append(ri)
            contexts.append(group)
        if table['status'] == 'excluded':
            continue
        if original['kind'] == 'wppf':
            section['group_context'] = contexts
        work['logical_tables'].append(section)
    return derive_report(work, aliases)


def apply_review(project, review=None):
    """The effective project with a review applied (corrected tables, renamed phases,
    chosen quantity source, change log and fingerprint per sample). Input is not modified."""
    review = validate_review(project, review or new_review(project))
    effective = deepcopy(project)
    effective['analysis_revision'] = review['revision']
    for original, sample in zip(project['samples'], effective['samples']):
        reports = {}
        aliases = {}
        source_by_role = {r['role']: r for r in sample['sources']}
        phase_report = original.get('reports', {}).get('phase_report')
        if phase_report:
            record = source_by_role['phase_report']
            entries = review['tables'].get(record['id'], {})
            baseline = rebuild(phase_report, {})
            aliases = {p['name']: p['name'] for p in baseline['phases']}
            current = rebuild(phase_report, entries, aliases)
            old_by_position = {
                json.dumps(p.get('source_position'), sort_keys=True): p for p in baseline['phases']
            }
            for phase in current['phases']:
                old = old_by_position.get(json.dumps(phase.get('source_position'), sort_keys=True))
                if old and old['name'] != phase['name']:
                    aliases[old['name']] = phase['name']
            if any(k != v for k, v in aliases.items()) and len(
                {p['name'] for p in current['phases']}
            ) != len(current['phases']):
                raise ValueError(
                    '수정한 상 이름이 충돌합니다. 같은 이름으로 자동 연결하지 않습니다.'
                )
            reports['phase_report'] = rebuild(phase_report, entries, aliases)
        if original.get('reports', {}).get('wppf_report'):
            record = source_by_role['wppf_report']
            reports['wppf_report'] = rebuild(
                original['reports']['wppf_report'], review['tables'].get(record['id'], {}), aliases
            )
        sample['reports'] = reports
        pdf_quantities = reports.get('wppf_report', {}).get('quantities', [])
        for q in pdf_quantities:
            q['source_filename'] = source_by_role['wppf_report']['filename']
            q['origin'] = 'pdf'
        sample['pdf_quantities'] = deepcopy(pdf_quantities)
        manual = deepcopy(review['manual_quantities'].get(sample['id'], []))
        if review['quantity_source'].get(sample['id'], 'pdf') == 'manual':
            for q in manual:
                q.update(
                    value=central(q['raw']),
                    origin='manual',
                    phase=aliases.get(q['phase'], q['phase']),
                )
            sample['quantities'] = manual
        else:
            sample['quantities'] = pdf_quantities
        base_phases = phase_report.get('phases', []) if phase_report else []
        phases = []
        for i, phase in enumerate(reports.get('phase_report', {}).get('phases', [])):
            name = next(
                (raw for raw, new in aliases.items() if new == phase['name']), phase['name']
            )
            base_index = next(
                (
                    j
                    for j, p in enumerate(base_phases)
                    if p['name'] == name and p['page'] == phase['page']
                ),
                i,
            )
            quantities = [q for q in sample['quantities'] if normal(q['phase']) == phase['name']]
            phases.append(
                {
                    **phase,
                    'id': sample['id'] + f'-p{base_index}',
                    'quantity': quantities[0] if len(quantities) == 1 else None,
                    'source': source_by_role['phase_report']['filename'],
                }
            )
        sample['phases'] = phases
        for key in ('reference_peaks', 'observed_peaks', 'assignments'):
            sample[key] = reports.get('phase_report', {}).get(key, [])
        changes = []
        states = []
        for record in sample['sources']:
            for ti, entry in review['tables'].get(record['id'], {}).items():
                table = original['reports'][record['role']]['raw_tables'][int(ti)]
                states.append(
                    {
                        'source_id': record['id'],
                        'table': int(ti),
                        'page': table['page'],
                        'status': entry['status'],
                    }
                )
                if entry['status'] == 'reviewed':
                    changes.extend(
                        {
                            'filename': record['filename'],
                            'page': table['page'],
                            'table': int(ti),
                            **c,
                        }
                        for c in entry['cells']
                    )
                    if entry['excluded_rows']:
                        changes.append(
                            {
                                'filename': record['filename'],
                                'page': table['page'],
                                'table': int(ti),
                                'excluded_rows': entry['excluded_rows'],
                            }
                        )
                elif entry['status'] == 'excluded':
                    changes.append(
                        {
                            'filename': record['filename'],
                            'page': table['page'],
                            'table': int(ti),
                            'excluded_table': True,
                        }
                    )
        relevant = {
            'tables': {
                r['id']: {
                    k: v
                    for k, v in review['tables'][r['id']].items()
                    if v['status'] != 'unreviewed'
                }
                for r in sample['sources']
                if r['id'] in review['tables']
                and any(v['status'] != 'unreviewed' for v in review['tables'][r['id']].values())
            },
            'manual': manual
            if review['quantity_source'].get(sample['id'], 'pdf') == 'manual'
            else [],
            'quantity_source': review['quantity_source'].get(sample['id'], 'pdf'),
        }
        sample['analysis_fingerprint'] = analysis_fingerprint(relevant)
        sample['analysis_review'] = {
            'revision': review['revision'],
            'changes': changes,
            'tables': states,
            'quantity_source': relevant['quantity_source'],
            'manual_quantities': manual,
        }
        old_warnings = {w for r in original['reports'].values() for w in r['warnings']}
        sample['warnings'] = [w for w in original['warnings'] if w not in old_warnings] + [
            w for r in reports.values() for w in r['warnings']
        ]
        if changes:
            sample['warnings'].append(
                f'분석 검토값 적용: 셀/행/표 변경 {len(changes)}건. 원본 이미지는 수정하지 않았습니다.'
            )
        if relevant['quantity_source'] == 'manual':
            sample['warnings'].append(
                '직접 확인·입력한 정량값을 사용합니다. PDF 추출값은 별도로 보존했습니다.'
            )
    return effective


def validate_for_save(project: dict, review: dict) -> dict:
    """Validate a review for saving and prove it can be applied to the project."""
    clean = validate_review(project, review)
    apply_review(project, clean)
    return clean

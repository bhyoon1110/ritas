"""Text-based analysis report PDF adapter (supported report layout only).

The PDF adapter recognises the headers in the supplied reports. It does
not perform phase identification, assign reference peaks to observations,
interpret uncertainty notation, or repair measurements.
"""

import hashlib
import io
import re

import pdfplumber

from ...core.parsing.values import central, normal, pdf_number


def header_key(value):
    """Match column labels even when a PDF wraps in the middle of a word."""
    key = re.sub(r'\s+', '', normal(value).lower())
    return 'no.' if key == 'no' else key


def header_type(row):
    """Classify a table row as a supported header kind ('phases', 'observed',
    'assignments', 'reference', 'wppf') or None."""
    cells = [header_key(cell) for cell in row]
    if 'phasename' in cells and 'fom' in cells:
        return 'phases'
    if cells and cells[0] in ('no.', 'no'):
        if 'height,cps' in cells:
            return 'observed'
        if 'phasename' in cells:
            return 'assignments'
        if 'hkl' in cells and 'norm.i.' in cells:
            return 'reference'
    if 'phase/dataset' in cells:
        return 'wppf'
    return None


def logical_sections(rows):
    """Split embedded header changes; column count alone is not a schema."""
    sections, current = [], None
    for row_index, row in enumerate(rows):
        kind = header_type(row)
        if kind:
            current = dict(kind=kind, headers=row, rows=[], row_indices=[])
            sections.append(current)
        elif current:
            current['rows'].append(row)
            current['row_indices'].append(row_index)
    return sections


def get_cell(row, headers, name):
    """Cell of a row under the named header column, or None when the column is absent."""
    names = [header_key(cell) for cell in headers]
    try:
        index = names.index(header_key(name))
        return row[index] if index < len(row) else None
    except ValueError:
        return None


def supported_peak_sections(report):
    """Recover known peak tables missed by old parsers, using ORIGINAL cells only.

    Keep frozen sections and provenance. Never turn a user-edited unknown table
    into a new schema, nor reclassify phase, reference or WPPF tables.
    """
    sections = list(report['logical_tables'])
    for ti, table in enumerate(report['raw_tables']):
        existing = [s for s in sections if s['page'] == table['page'] and s['bbox'] == table['bbox']]
        covered = {i for s in existing for i in s['row_indices']}
        for section in logical_sections(table['rows']):
            if section['kind'] not in ('observed', 'assignments') or not section['rows']:
                continue
            if covered.intersection(section['row_indices']):
                continue
            sections.append(dict(**section, page=table['page'], bbox=table['bbox'],
                                 prefix='', table_index=ti))
    return sections


def parse_report(data: bytes, filename: str) -> dict:
    """Extract page texts, raw tables (page, bbox) and logical sections from a
    text-based report PDF, then derive structured results. Raw tables are kept verbatim."""
    result = dict(
        filename=filename,
        phases=[],
        reference_peaks=[],
        observed_peaks=[],
        assignments=[],
        quantities=[],
        raw_tables=[],
        logical_tables=[],
        page_texts=[],
        warnings=[],
        axis_kind=None,
        axis_unit=None,
        dataset_labels=[],
        source_sha256=hashlib.sha256(data).hexdigest(),
    )
    # Keep word positions for phase headings immediately above d-I tables.
    with pdfplumber.open(io.BytesIO(data)) as document:
        for pageno, page in enumerate(document.pages, 1):
            text = page.extract_text() or ''
            result['page_texts'].append(dict(page=pageno, text=text))
            if '2θ' in text:
                result['axis_kind'], result['axis_unit'] = 'two_theta', 'deg'
            for label in re.findall(r'([^\n]+?)-(?:Evaluation|WPPF) report', text):
                if label not in result['dataset_labels']:
                    result['dataset_labels'].append(label)
            for table in page.find_tables():
                rows = table.extract()
                raw = dict(page=pageno, bbox=list(table.bbox), rows=rows)
                result['raw_tables'].append(raw)
                # Only textual headings above the table, not inferred order of phases.
                prefix = page.crop((0, 0, page.width, max(0.1, table.bbox[1]))).extract_text() or ''
                for section in logical_sections(rows):
                    result['logical_tables'].append(
                        dict(
                            **section,
                            page=pageno,
                            bbox=list(table.bbox),
                            prefix=prefix,
                            table_index=len(result['raw_tables']) - 1,
                        )
                    )
    return derive_report(result)


def derive_report(report, aliases=None):
    """Derive phases, reference/observed peaks, assignments and WPPF quantities from
    the logical tables. `aliases` maps original phase names to reviewed ones; nothing is
    inferred beyond what the tables state."""
    from copy import deepcopy

    result = deepcopy(report)
    aliases = aliases or {}
    for key in (
        'phases',
        'reference_peaks',
        'observed_peaks',
        'assignments',
        'quantities',
        'warnings',
    ):
        result[key] = []
    # First extract phases so later d-I heading matching is name-based.
    for section in result['logical_tables']:
        if section['kind'] != 'phases':
            continue
        for ri, row in enumerate(section['rows']):
            headers = section['headers']
            name = normal(get_cell(row, headers, 'Phase name'))
            if not name:
                continue
            phase = dict(
                name=name,
                name_raw=get_cell(row, headers, 'Phase name'),
                formula=normal(get_cell(row, headers, 'Chemical formula')),
                fom_raw=get_cell(row, headers, 'FOM'),
                db=normal(get_cell(row, headers, 'Phase reg. detail')),
                space_group=normal(get_cell(row, headers, 'Space Group')),
                card=normal(get_cell(row, headers, 'DB Card Number')),
                page=section['page'],
                source_position={
                    'table': section.get('table_index'),
                    'row': section['row_indices'][ri],
                },
            )
            result['phases'].append(phase)
    reference_phase = None
    wppf_phase = None
    for section in result['logical_tables']:
        kind, headers, pageno = section['kind'], section['headers'], section['page']
        if kind == 'reference':
            prefix = normal(section['prefix'])
            names = {p['name']: p['name'] for p in result['phases']}
            names.update(aliases)
            candidates = [(prefix.rfind(raw), name) for raw, name in names.items() if raw in prefix]
            if 'origin_reference_phase' in section:
                original = section['origin_reference_phase']
                reference_phase = aliases.get(original, original)
            elif candidates:
                reference_phase = max(candidates)[1]
            if not reference_phase:
                result['warnings'].append(
                    f"{result['filename']} p.{pageno}: d-I 상 이름을 연결하지 못했습니다."
                )
            for row in section['rows']:
                no = pdf_number(get_cell(row, headers, 'No.'))
                xraw = get_cell(row, headers, '2θ, °')
                x = pdf_number(xraw)
                if no is None or not no.is_integer() or x is None:
                    continue
                result['reference_peaks'].append(
                    dict(
                        phase=reference_phase,
                        no=int(no),
                        x=x,
                        x_raw=xraw,
                        d_raw=get_cell(row, headers, 'd, Å'),
                        hkl_raw=get_cell(row, headers, 'h k l'),
                        intensity_raw=get_cell(row, headers, 'Norm. I.'),
                        intensity=pdf_number(get_cell(row, headers, 'Norm. I.')),
                        page=pageno,
                    )
                )
        elif kind in ('observed', 'assignments'):
            # Some instruments print observation and assignment columns together.
            has_height = header_key('Height, cps') in [header_key(h) for h in headers]
            has_assignment = header_key('Phase Name') in [header_key(h) for h in headers]
            for row in section['rows']:
                no, xraw = pdf_number(get_cell(row, headers, 'No.')), get_cell(row, headers, '2θ, °')
                x = pdf_number(xraw)
                if no is None or not no.is_integer() or x is None:
                    if any(normal(cell) for cell in row):
                        result['warnings'].append(f"{result['filename']} p.{pageno}: 피크 번호 또는 2θ를 읽지 못한 행이 있습니다. 원문 표를 확인하세요.")
                    continue
                record = dict(
                    no=int(no),
                    x=x,
                    x_raw=xraw,
                    page=pageno,
                    fields={normal(k): v for k, v in zip(headers, row) if normal(k)},
                )
                if has_height:
                    result['observed_peaks'].append(dict(record, height_raw=get_cell(row, headers, 'Height, cps')))
                if has_assignment:
                    raw = get_cell(row, headers, 'Phase Name') or ''
                    record.update(
                        phase_raw=raw, card_raw=get_cell(row, headers, 'Card No'), matches=[]
                    )
                    # Parse only explicitly written "Phase: h k l" entries.
                    for raw_name, hkl in re.findall(
                        r'(?:^|,)\s*([^:]+):\s*([^,]+)', raw.replace('-\n', '-')
                    ):
                        name = normal(raw_name)
                        known = {p['name'] for p in result['phases']} | set(aliases)
                        # A wrapped word (Corund\num) is linked only to one exact
                        # whitespace-insensitive name already printed in the phase table.
                        # No spelling correction, nearest-peak or chemical inference.
                        if name not in known and ('\n' in raw_name or '\r' in raw_name):
                            compact = re.sub(r'\s+', '', name)
                            candidates = [p for p in known if re.sub(r'\s+', '', p) == compact]
                            if len(candidates) == 1:
                                name = candidates[0]
                        record['matches'].append(
                            dict(phase=aliases.get(name, name), hkl=normal(hkl))
                        )
                    result['assignments'].append(record)
        elif kind == 'wppf':
            for ri, row in enumerate(section['rows']):
                # This adapter's WPPF columns have a phase/metric group,
                # parameter name, and value; retain every original row above.
                group_raw = row[1] if len(row) > 1 else None
                group = normal((group_raw or '').replace('-\n', '-'))
                parameter = normal(row[2] if len(row) > 2 else None)
                value = row[3] if len(row) > 3 else None
                if group and not parameter and not normal(value):
                    wppf_phase = aliases.get(group, group)
                if 'group_context' in section:
                    context = section['group_context'][ri]
                    wppf_phase = aliases.get(context, context) if context else None
                if 'Weight' in parameter and 'wt%' in parameter:
                    if not wppf_phase:
                        result['warnings'].append(
                            f"{result['filename']} p.{pageno}: 정량 그룹을 확인할 수 없어 값 {value}의 상 배정을 제외했습니다."
                        )
                        continue
                    result['quantities'].append(
                        dict(
                            phase=wppf_phase,
                            raw=value,
                            value=central(value),
                            unit='wt%',
                            page=pageno,
                        )
                    )
    unmatched = sorted({m['phase'] for a in result['assignments'] for m in a['matches']}
                       - {p['name'] for p in result['phases']})
    if unmatched:
        result['warnings'].append('상 배정 이름을 결정상 목록과 연결하지 못했습니다: ' + ', '.join(unmatched) + '. 원문과 분석 검토를 확인하세요.')
    result['warnings'] = list(dict.fromkeys(result['warnings']))
    if result['phases'] and not result['observed_peaks']:
        result['warnings'].append('관측 피크표를 읽지 못했거나 검토에서 제외했습니다. 결정상 마커는 상매칭 Report의 관측 피크와 상 배정 정보가 필요합니다.')
    elif result['observed_peaks'] and not any(a['matches'] for a in result['assignments']):
        result['warnings'].append('관측 피크의 결정상 배정 정보가 없습니다. 상매칭 Report의 Phase Name 열과 분석 검토 내용을 확인하세요.')
    if not any((result['phases'], result['quantities'], result['observed_peaks'])):
        result['warnings'].append(
            '지원되는 분석 표를 찾지 못했습니다. 이미지 PDF/OCR 및 다른 보고서 형식은 검토가 필요합니다.'
        )
    return result

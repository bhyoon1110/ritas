"""Measurement TXT reader: two numeric columns, '#' headers, strictly increasing 2θ."""

import hashlib
import re

from ...core.parsing.values import central

# Refuse oversized interactive projects, never silently resample measurements.
MAX_MEASUREMENT_POINTS = 100_000


def parse_txt(data: bytes, filename: str) -> dict:
    """Read a measurement TXT into x/y with original line numbers and '#' headers.

    Rows that are not two finite numbers or not strictly increasing in x are rejected,
    never repaired. The axis kind stays unknown here (it comes from a report or the user)."""
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = data.decode('cp949')
    result = dict(
        filename=filename,
        x=[],
        y=[],
        line_numbers=[],
        headers=[],
        intensity_unit=None,
        axis_kind=None,
        axis_unit=None,
    )
    for n, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('#'):
            result['headers'].append(dict(line=n, raw=line))
            match = re.match(r'#\s*Intensity_unit\s*=\s*(.+)', stripped, re.I)
            if match:
                result['intensity_unit'] = match[1].strip()
            continue
        cells = re.split(r'[\s,]+', stripped)
        if len(cells) != 2:
            raise ValueError(f'{filename}: {n}행은 두 숫자 열이어야 합니다.')
        values = [central(cell) for cell in cells]
        # TXT is a measurement, not a fit parameter with uncertainty suffix.
        if None in values or any('(' in cell for cell in cells):
            raise ValueError(f'{filename}: {n}행에 유효하지 않은 측정값이 있습니다.')
        x, y = values
        if result['x'] and x <= result['x'][-1]:
            raise ValueError(f'{filename}: {n}행 좌표가 증가하지 않습니다. 원본을 확인하세요.')
        result['x'].append(x)
        result['y'].append(y)
        result['line_numbers'].append(n)
        if len(result['x']) > MAX_MEASUREMENT_POINTS:
            raise ValueError(f'{filename}: 한 시료는 최대 {MAX_MEASUREMENT_POINTS:,}개 측정점을 지원합니다. 원본을 보존한 별도 작업으로 나누세요.')
    if not result['x']:
        raise ValueError(f'{filename}: 측정점이 없습니다.')
    result['source_sha256'] = hashlib.sha256(data).hexdigest()
    return result

"""Sample/file association and the viewer's source-linked project model."""

import hashlib
import unicodedata
from collections import Counter
from pathlib import Path, PureWindowsPath

from ..core.parsing.pdf_report import parse_report
from ..core.parsing.txt import parse_txt
from ..core.parsing.values import normal

ROLE_EXTENSIONS = {
    'measurement': {'.txt'},
    'phase_report': {'.pdf'},
    'wppf_report': {'.pdf'},
    'phase_image': {'.png', '.jpg', '.jpeg'},
    'quantity_image': {'.png', '.jpg', '.jpeg'},
}


def safe_filename(name):
    """Base name of an uploaded filename (any client-side path dropped), NFC-normalised."""
    return unicodedata.normalize('NFC', PureWindowsPath(name).name.strip())


def classify_filename(filename: str) -> dict:
    """Sample name and role from the filename convention; unknown files get role 'ignore'."""
    path = PureWindowsPath(safe_filename(filename))
    stem, ext = path.stem, path.suffix.lower()
    suffixes = [
        ('_정량(WPPF)_Report', 'wppf_report'),
        ('_상매칭_Report', 'phase_report'),
        ('_상매칭', 'phase_image'),
        ('_정량', 'quantity_image'),
    ]
    for suffix, role in suffixes:
        if stem.lower().endswith(suffix.lower()) and ext in ROLE_EXTENSIONS[role]:
            return dict(sample=stem[: -len(suffix)].strip(), role=role)
    if ext == '.txt':
        return dict(sample=stem.strip(), role='measurement')
    return dict(sample=stem, role='ignore')


def _content(value):
    return value.read_bytes() if isinstance(value, Path) else value


def _sha256(value):
    if isinstance(value, Path):
        with value.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()
    return hashlib.sha256(value).hexdigest()


def build_project(files: list[tuple[str, bytes | Path]], mappings: dict | None = None) -> dict:
    """Link files to samples and parse them into the project model.

    `mappings` overrides the filename convention. Duplicate roles and role/extension
    mismatches are rejected; samples without a TXT are reported in warnings, not plotted."""
    mappings = mappings or {}
    groups, source_records = {}, []
    names = [safe_filename(name) for name, _ in files]
    if len(names) != len(set(names)):
        raise ValueError('같은 파일명이 중복되었습니다. 연결을 구분하도록 파일명을 확인하세요.')
    checksums = [_sha256(data) for _, data in files]
    counts = Counter(checksums)
    for (filename, data), sha in zip(files, checksums):
        filename = safe_filename(filename)
        mapping = mappings.get(filename, classify_filename(filename))
        role = mapping.get('role', 'ignore')
        # Equal bytes under distinct filenames still need distinct provenance
        # links. Keep existing IDs when the content occurs only once.
        source_id = (
            sha[:24]
            if counts[sha] == 1
            else hashlib.sha256((filename + '\0' + sha).encode()).hexdigest()[:24]
        )
        size = data.stat().st_size if isinstance(data, Path) else len(data)
        source = dict(id=source_id, filename=filename, sha256=sha, size=size, role=role)
        source_records.append(source)
        if role == 'ignore':
            continue
        sample = unicodedata.normalize('NFC', str(mapping.get('sample', ''))).strip()
        if not sample or len(sample) > 200:
            raise ValueError(f'{filename}: 시료명을 입력하세요 (200자 이하).')
        if (
            role not in ROLE_EXTENSIONS
            or PureWindowsPath(filename).suffix.lower() not in ROLE_EXTENSIONS[role]
        ):
            raise ValueError(f'{filename}: 역할과 확장자가 맞지 않습니다.')
        if role in groups.setdefault(sample, {}):
            raise ValueError(
                f'{sample}: {role} 역할이 중복되었습니다. 사용할 대표 파일을 선택하세요.'
            )
        groups[sample][role] = (source, data, mapping)
    samples = []
    total_points = 0
    for name, roles in groups.items():
        if 'measurement' not in roles:
            # Incomplete groups remain visible to the registration UI but are
            # not presented as a valid measured curve.
            continue
        source, data, mapping = roles['measurement']
        measurement = parse_txt(_content(data), source['filename'])
        total_points += len(measurement['x'])
        if total_points > 250_000:
            raise ValueError('저사양 PC 보호를 위해 한 프로젝트는 총 250,000개 측정점까지 지원합니다. 시료를 여러 작업으로 나누세요. 원본 값을 자동 축소하지 않습니다.')
        reports = {}
        for role in ('phase_report', 'wppf_report'):
            if role in roles:
                rsource, rdata, _ = roles[role]
                reports[role] = parse_report(_content(rdata), rsource['filename'])
        phase_report = reports.get('phase_report', {})
        if phase_report.get('axis_kind'):
            measurement.update(
                axis_kind=phase_report['axis_kind'],
                axis_unit=phase_report['axis_unit'],
                axis_source='PDF',
            )
        elif mapping.get('axis_kind') == 'two_theta':
            measurement.update(axis_kind='two_theta', axis_unit='deg', axis_source='사용자 지정')
        if not measurement['intensity_unit'] and mapping.get('intensity_unit'):
            measurement['intensity_unit'] = str(mapping['intensity_unit']).strip()
        sample_id = hashlib.sha256((name + source['sha256']).encode()).hexdigest()[:24]
        warnings = [w for r in reports.values() for w in r['warnings']]
        if not measurement['axis_kind']:
            warnings.append('가로축 종류가 미확인입니다. 연결 설정에서 2θ(deg)를 명시하세요.')
        if not measurement['intensity_unit']:
            warnings.append('강도 단위가 미확인입니다. 연결 설정에서 단위를 명시하세요.')
        for r in reports.values():
            for label in r['dataset_labels']:
                if label != name:
                    warnings.append(
                        f"{r['filename']}: PDF 내부 시료명 {label} / 등록 시료명 {name}. 연결을 확인하세요."
                    )
        quantities = reports.get('wppf_report', {}).get('quantities', [])
        phases = []
        for i, phase in enumerate(phase_report.get('phases', [])):
            pid = sample_id + f'-p{i}'
            candidates = [q for q in quantities if normal(q['phase']) == phase['name']]
            phases.append(
                dict(
                    **phase,
                    id=pid,
                    quantity=candidates[0] if len(candidates) == 1 else None,
                    source=phase_report['filename'],
                )
            )
        samples.append(
            dict(
                id=sample_id,
                name=name,
                measurement=measurement,
                phases=phases,
                reference_peaks=phase_report.get('reference_peaks', []),
                observed_peaks=phase_report.get('observed_peaks', []),
                assignments=phase_report.get('assignments', []),
                reports=reports,
                quantities=quantities,
                warnings=warnings,
                sources=[record[0] for record in roles.values()],
            )
        )
    if not samples:
        raise ValueError('표시할 TXT 측정 데이터가 없습니다. 파일 역할과 시료 연결을 확인하세요.')
    fingerprint = hashlib.sha256(
        ''.join(s['id'] + ''.join(r['sha256'] for r in s['sources']) for s in samples).encode()
    ).hexdigest()
    return dict(
        schema_version=1,
        id=fingerprint[:24],
        samples=samples,
        sources=source_records,
        warnings=[
            f'{name}: TXT 미등록, 그래프에 포함하지 않았습니다.'
            for name, r in groups.items()
            if 'measurement' not in r
        ],
    )

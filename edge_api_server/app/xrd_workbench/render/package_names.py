"""Readable delivery filenames, allocated once by identity across the whole project.

Storage IDs still select the exact bytes/sample; they never appear in filenames.
Use the full project so changing report order/inclusion cannot rename a link.
"""
import re
import unicodedata
from pathlib import PurePosixPath


def portable_filename(name, fallback='파일'):
    name = unicodedata.normalize('NFC', str(name))
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', '_', name).strip().rstrip('. ')
    if not name or name in {'.', '..'}:
        name = fallback
    if re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])', name.split('.')[0], re.I):
        name = '_' + name
    # Leave room for folders, duplicate counters and common extraction paths.
    suffix = PurePosixPath(name).suffix
    if len(suffix.encode('utf-8')) > 20:
        suffix = ''
    stem = name[:-len(suffix)] if suffix else name
    stem = stem.encode('utf-8')[:180-len(suffix.encode('utf-8'))].decode('utf-8', errors='ignore')
    return (stem or fallback) + suffix


def _unique_names(entries):
    """Preserve natural names (including existing '(2)') before allocating counters."""
    names = {identity: portable_filename(name) for identity, name in entries}
    reserved = {name.casefold() for name in names.values()}
    used, result = set(), {}
    for identity, name in sorted(names.items(), key=lambda pair: (pair[1].casefold(), pair[1], pair[0])):
        candidate = name
        if candidate.casefold() in used:
            suffix = PurePosixPath(name).suffix
            stem = name[:-len(suffix)] if suffix else name
            counter = 2
            while True:
                candidate = f'{stem} ({counter}){suffix}'
                if candidate.casefold() not in used | reserved:
                    break
                counter += 1
        result[identity] = candidate
        used.add(candidate.casefold())
    return result


def package_paths(project):
    records = [r for sample in project['samples'] for r in sample.get('sources', [])
               if r['role'] in {'measurement', 'phase_report', 'wppf_report'}]
    by_id = {}
    for record in records:
        previous = by_id.get(record['id'])
        if previous and any(previous.get(key) != record.get(key) for key in ('filename', 'role', 'sha256')):
            raise ValueError('보고서 원본 파일 식별자가 중복되었습니다.')
        by_id[record['id']] = record
    sources = {}
    for folder, roles in [('Measured Data', {'measurement'}), ('Report', {'phase_report', 'wppf_report'})]:
        names = _unique_names((r['id'], r['filename']) for r in records if r['role'] in roles)
        sources.update({identity: folder + '/' + name for identity, name in names.items()})
    viewers = _unique_names((s['id'], (s.get('name') or '시료') + '.html') for s in project['samples'])
    return {'sources': sources, 'viewers': {identity: 'Viewer/' + name for identity, name in viewers.items()}}

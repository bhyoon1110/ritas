"""Synthetic instrument files only; never load user experiment data implicitly."""
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
from . import synthetic
from app.xrd_workbench.storage import Storage

_temporary = TemporaryDirectory(prefix='rist-xrd-fixtures-')


@lru_cache(maxsize=1)
def fixtures_dir():
    path = Path(_temporary.name) / 'input'
    return synthetic.write_folder(path)


@lru_cache(maxsize=1)
def _storage():
    return Storage(Path(_temporary.name) / 'storage', fixtures_dir())


def example_project():
    return _storage().files.example()


def resolve_source(storage_id, file_id):
    return _storage().files.read_source(storage_id, file_id)


def first_quantity_token(project, sample_index=0):
    return project['samples'][sample_index]['quantities'][0]['raw']


def other_token(raw):
    import re
    match = re.fullmatch(r'([+-]?\d+)(\.\d+)?(\(\d+\))?', raw)
    assert match
    decimals = len(match[2] or '.') - 1
    value = float(match[1] + (match[2] or '')) + (0.5 if decimals else 1)
    return f'{value:.{decimals}f}' + (match[3] or '')


def first_phase_name(project, sample_index=0):
    return project['samples'][sample_index]['phases'][0]['name']


def table_cell(project, role, text, sample_index=0):
    from app.xrd_workbench.core.parsing.values import normal
    sample = project['samples'][sample_index]
    record = next(r for r in sample['sources'] if r['role'] == role)
    for ti, table in enumerate(sample['reports'][role]['raw_tables']):
        for ri, row in enumerate(table['rows']):
            for ci, value in enumerate(row):
                if value is not None and normal(value) == normal(text):
                    return record['id'], ti, ri, ci
    raise AssertionError('cell not found: ' + text)


def weight_sum_sample_index(project):
    from app.xrd_workbench.core.notes import build_candidates
    return next(i for i, sample in enumerate(project['samples'])
                if any(c['kind'] == 'weight_sum' for c in build_candidates(sample)))


def quantity_text(project, sample_index=0):
    quantities = project['samples'][sample_index]['quantities']
    return '; '.join(f"{q['phase']} {q['raw']} {q['unit']}" for q in quantities[:4])

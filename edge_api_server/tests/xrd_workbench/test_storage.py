"""File storage contract: immutable verified sources, isolated instances, atomic revisioned saves."""

import json

import pytest

from app.xrd_workbench.core.errors import RevisionConflict
from app.xrd_workbench.core.report import new_document
from app.xrd_workbench.core.review import new_review
from app.xrd_workbench.storage import ProjectFiles, Storage

TXT = b'#Intensity_unit=cps\n10 1\n11 2\n'


def test_created_project_is_listed_loaded_and_its_sources_verified(tmp_path):
    files = ProjectFiles(tmp_path / 'uploads')
    project = files.create([('S1.txt', TXT)], {})
    sid = project['storage_id']
    assert files.load_source(sid) == project
    assert [p['storage_id'] for p in files.list()] == [sid]
    record = project['sources'][0]
    assert files.read_source(sid, record['id']) == TXT
    assert files.source_file(sid, record['id']) == (
        tmp_path / 'uploads' / sid / record['id'],
        'S1.txt',
    )
    (tmp_path / 'uploads' / sid / record['id']).write_bytes(TXT + b'changed')
    with pytest.raises(ValueError, match='변경'):
        files.read_source(sid, record['id'])


@pytest.mark.parametrize('storage_id', ['../etc', 'ABC', '0' * 31, 'g' * 32])
def test_invalid_storage_ids_are_rejected(tmp_path, storage_id):
    files = ProjectFiles(tmp_path)
    with pytest.raises(ValueError, match='저장 ID'):
        files.load_source(storage_id)
    assert files.source_file(storage_id, 'x') is None


def test_unconfigured_example_is_neither_listed_nor_downloadable(tmp_path):
    files = ProjectFiles(tmp_path)
    assert files.list() == []
    assert files.source_file('example', 'x') is None
    with pytest.raises(ValueError, match='XRD_EXAMPLE_DIR'):
        files.example()


def test_two_storages_do_not_share_examples_or_locks(tmp_path):
    one, two = tmp_path / 'one', tmp_path / 'two'
    for folder, name in ((one, 'A.txt'), (two, 'B.txt')):
        folder.mkdir()
        (folder / name).write_bytes(TXT)
    first = Storage(tmp_path / 'd1', one)
    second = Storage(tmp_path / 'd2', two)
    assert [s['name'] for s in first.files.example()['samples']] == ['A']
    assert [s['name'] for s in second.files.example()['samples']] == ['B']
    assert first.lock is not second.lock
    assert first.reviews.lock is first.lock and first.reports.lock is first.lock


def test_saves_are_revisioned_and_validation_errors_win_over_conflicts(tmp_path):
    storage = Storage(tmp_path)
    project = storage.files.create([('S1.txt', TXT)], {})
    sid = project['storage_id']
    effective = storage.load_project(sid)
    document = new_document(effective)
    saved = storage.reports.save(sid, effective, document, 0)
    assert saved['revision'] == 1
    with pytest.raises(RevisionConflict):
        storage.reports.save(sid, effective, document, 0)
    broken = dict(document, metadata=dict(document['metadata'], title=''))
    with pytest.raises(ValueError) as error:
        storage.reports.save(sid, effective, broken, 0)
    assert not isinstance(error.value, RevisionConflict)
    review = storage.reviews.save(sid, project, new_review(project), 0)
    assert review['revision'] == 1
    stored = json.loads((tmp_path / 'analysis' / sid / 'review.json').read_text(encoding='utf-8'))
    assert stored == review


def test_saving_over_another_projects_document_is_a_conflict_not_a_crash(tmp_path):
    storage = Storage(tmp_path)
    first = storage.files.create([('S1.txt', TXT)], {})
    second = storage.files.create([('S2.txt', TXT.replace(b'10 1', b'10 5'))], {})
    sid = first['storage_id']
    storage.reports.save(sid, storage.load_project(sid), new_document(storage.load_project(sid)), 0)
    other = storage.load_project(second['storage_id'])
    with pytest.raises(RevisionConflict):
        storage.reports.save(sid, other, new_document(other), 1)


def test_file_storage_satisfies_the_documented_backend_protocol(tmp_path):
    from app.xrd_workbench.storage import StorageBackend

    storage = Storage(tmp_path)
    assert isinstance(storage, StorageBackend)
    assert storage.reviews.lock is storage.lock and storage.reports.lock is storage.lock
    # Routes hold the shared lock while save() acquires it again: it must be re-entrant.
    with storage.lock:
        with storage.lock:
            pass

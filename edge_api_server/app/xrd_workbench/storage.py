"""File-system storage: uploaded sources, the read-only example project, analysis reviews and
report documents. This is the module to replace for a database or internal storage service.

The routes use exactly the surface described by `StorageBackend` below:
- `lock`: one re-entrant lock (threading.RLock or equivalent) shared by `reviews` and `reports`;
  routes hold it across multi-step checks and then call save(), which acquires it again;
- `files`: example(), load_source(id), read_source(id, file_id) -> bytes,
  source_file(id, file_id) -> (filesystem Path, original filename) | None (served by FileResponse),
  create(inputs, mappings) -> project, list(archived=False) -> project summaries;
  manage(id, expected_revision, label=..., archived=...) -> summary (metadata only);
- `reviews` / `reports`: lock, load(id) -> dict | None, save(id, project, value, expected_revision);
- `presets`: lock, list() -> [preset] sorted by name, load(id) -> dict | None,
  save(clean, overwrite=False) -> preset (RevisionConflict when the name exists and not overwriting),
  delete(id) -> bool; `clean` is the validated {name, settings} from .core.presets;
- `load_project(id)`: the source project with the saved analysis review applied.

Contract every implementation must keep:
- source bytes are immutable and verified against their recorded SHA256 on every read;
- saves are atomic and raise RevisionConflict unless expected_revision equals the stored one;
- a saved document is never replaced by one that belongs to another project;
- domain validation is delegated to xrd.core (validate_for_save, carry_review_flags).
"""

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
from copy import deepcopy
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from .core.errors import RevisionConflict
from .core.project import build_project
from .core.report import carry_review_flags
from .core.report import validate_for_save as validate_document_for_save
from .core.review import apply_review
from .core.review import validate_for_save as validate_review_for_save

EXAMPLE_ID = 'example'
EXAMPLE_EXTENSIONS = ('.txt', '.pdf', '.png', '.jpg', '.jpeg')
_UPLOAD_ID = re.compile('[0-9a-f]{32}')
_PRESET_ID = re.compile('[0-9a-f]{32}')
report_actor = ContextVar('xrd_report_actor', default=None)


def _is_upload_id(storage_id: str) -> bool:
    return bool(_UPLOAD_ID.fullmatch(storage_id))


def _checked_id(storage_id: str) -> str:
    if storage_id != EXAMPLE_ID and not _is_upload_id(storage_id):
        raise ValueError('잘못된 프로젝트 저장 ID입니다.')
    return storage_id


def _write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', dir=path.parent, suffix='.json', delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def load_mapping(directory: Path, mapping_path: Path | None) -> dict:
    """Return {filename: {sample, role}} from the explicit path or <directory>/fixtures.json."""
    path = mapping_path or directory / 'fixtures.json'
    if not path.is_file():
        if mapping_path is not None:
            raise ValueError(f'예제 파일 연결 설정을 찾지 못했습니다: {path}')
        return {}
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
    except UnicodeDecodeError as exc:
        raise ValueError(f'예제 파일 연결 설정은 UTF-8로 저장하세요: {path}') from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f'예제 파일 연결 설정의 JSON 형식이 잘못되었습니다: {path}') from exc
    if not isinstance(value, dict) or not all(isinstance(v, dict) for v in value.values()):
        raise ValueError(f'예제 파일 연결 설정은 파일명별 객체여야 합니다: {path}')
    return value


def build_example(
    directory: Path | None, mapping_path: Path | None = None
) -> tuple[dict, dict[str, Path]]:
    """Build the example project and map each source id to its original file path."""
    if directory is None or not directory.is_dir():
        raise ValueError(
            '예제 데이터 폴더가 설정되지 않았거나 없습니다. XRD_EXAMPLE_DIR을 지정하거나 자료를 업로드하세요.'
        )
    files = sorted(
        p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in EXAMPLE_EXTENSIONS
    )
    project = build_project(
        [(p.name, p.read_bytes()) for p in files], load_mapping(directory, mapping_path)
    )
    project['storage_id'] = EXAMPLE_ID
    return project, {record['id']: directory / record['filename'] for record in project['sources']}


class SourceFiles(Protocol):
    def example(self) -> dict: ...
    def load_source(self, storage_id: str) -> dict: ...
    def read_source(self, storage_id: str, file_id: str) -> bytes: ...
    def source_file(self, storage_id: str, file_id: str) -> tuple[Path, str] | None: ...
    def create(self, inputs: list[tuple[str, bytes]], mappings: dict) -> dict: ...
    def list(self, archived: bool = False) -> list[dict]: ...
    def manage(self, storage_id: str, expected_revision: int, *, label=None, archived=None) -> dict: ...


class RevisionedDocuments(Protocol):
    lock: object

    def load(self, storage_id: str) -> dict | None: ...
    def save(self, storage_id: str, project: dict, value: dict, expected_revision: int) -> dict: ...


class PresetDocuments(Protocol):
    lock: object

    def list(self) -> list[dict]: ...
    def load(self, preset_id: str) -> dict | None: ...
    def save(self, clean: dict, overwrite: bool = False) -> dict: ...
    def delete(self, preset_id: str) -> bool: ...


@runtime_checkable
class StorageBackend(Protocol):
    """What create_app and the routes need from a storage backend (see module docstring)."""

    lock: object
    files: SourceFiles
    reviews: RevisionedDocuments
    reports: RevisionedDocuments
    presets: PresetDocuments

    def load_project(self, storage_id: str) -> dict: ...


class ProjectFiles:
    """Immutable uploads and optional revisioned management.json (name / soft deletion)."""

    def __init__(
        self,
        uploads: Path,
        example_dir: Path | None = None,
        example_mapping: Path | None = None,
        lock=None,
    ):
        self.uploads = Path(uploads)
        self.example_dir = example_dir
        self.example_mapping = example_mapping
        self._example: tuple[dict, dict[str, Path]] | None = None
        self.lock = lock or threading.RLock()

    def example(self) -> dict:
        """The read-only example project (built once per instance)."""
        if self._example is None:
            self._example = build_example(self.example_dir, self.example_mapping)
        return self._example[0]

    def load_source(self, storage_id: str, *, include_archived=False) -> dict:
        """The registered project exactly as parsed at upload time (no review applied)."""
        if _checked_id(storage_id) == EXAMPLE_ID:
            return self.example()
        path = self.uploads / storage_id / 'project.json'
        if not path.is_file():
            raise ValueError('저장 프로젝트를 찾지 못했습니다.')
        if not include_archived and self._management(storage_id).get('archived_at'):
            raise ValueError('휴지통에 있는 프로젝트입니다. 프로젝트 관리에서 복원한 뒤 여세요.')
        return json.loads(path.read_text(encoding='utf-8'))

    def _management(self, storage_id):
        path = self.uploads / _checked_id(storage_id) / 'management.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'revision': 0}

    def _summary(self, project, path, metadata):
        return dict(storage_id=project['storage_id'],
                    label=metadata.get('label') or ', '.join(s['name'] for s in project['samples']),
                    managed_revision=metadata['revision'], archived_at=metadata.get('archived_at'),
                    created_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                    sample_count=len(project['samples']), file_count=len(project['sources']),
                    total_bytes=sum(s.get('size', 0) for s in project['sources']))

    def _catalog_entry(self, path):
        """Do not reparse thousands of raw coordinate arrays on every list/search."""
        info = path.stat()
        signature = [info.st_mtime_ns, info.st_size]
        cache = path.with_name('summary.json')
        if cache.is_file() and not cache.is_symlink():
            try:
                value = json.loads(cache.read_text(encoding='utf-8'))
                if value.get('source_signature') == signature and value['item']['storage_id'] == path.parent.name:
                    return value['item']
            except (ValueError, KeyError, TypeError):
                pass
        project = json.loads(path.read_text(encoding='utf-8'))
        if project['storage_id'] != path.parent.name:
            raise ValueError('프로젝트 식별 정보가 다릅니다.')
        item = self._summary(project, path, {'revision': 0})
        try:
            _write_json_atomic(cache, dict(source_signature=signature, item=item))
        except OSError:
            pass  # A cache failure must not hide otherwise readable user projects.
        return item

    def manage(self, storage_id, expected_revision, *, label=None, archived=None):
        """Rename or soft-delete/restore only metadata; never move/unlink source/report bytes."""
        if _checked_id(storage_id) == EXAMPLE_ID:
            raise ValueError('예제 프로젝트는 변경할 수 없습니다.')
        if label is not None:
            if not isinstance(label, str) or not label.strip() or len(label.strip()) > 100 or any(ord(c) < 32 or ord(c) == 127 for c in label):
                raise ValueError('프로젝트 이름은 줄바꿈 없이 1~100자로 입력하세요.')
            label = label.strip()
        if archived is not None and type(archived) is not bool:
            raise ValueError('휴지통 상태가 올바르지 않습니다.')
        with self.lock:
            project = self.load_source(storage_id, include_archived=True)
            value = self._management(storage_id)
            if type(expected_revision) is not int or expected_revision != value['revision']:
                raise RevisionConflict('다른 창에서 프로젝트 정보가 변경되었습니다. 목록을 새로고침한 뒤 다시 시도하세요.')
            if label is not None:
                value['label'] = label
            if archived is not None:
                value['archived_at'] = datetime.now(timezone.utc).isoformat() if archived else None
            value['revision'] += 1
            value['updated_at'] = datetime.now(timezone.utc).isoformat()
            _write_json_atomic(self.uploads / storage_id / 'management.json', value)
            return self._summary(project, self.uploads / storage_id / 'project.json', value)

    def _path(self, storage_id: str, file_id: str) -> Path:
        if storage_id == EXAMPLE_ID:
            self.example()
            return self._example[1][file_id]
        return self.uploads / storage_id / file_id

    def read_source(self, storage_id: str, file_id: str) -> bytes:
        """Original bytes of a registered source, refused if they changed since registration."""
        # Background exports may already hold a document snapshot when it is archived.
        # Soft deletion must not interrupt those jobs or mutate a delivery artifact.
        project = self.load_source(storage_id, include_archived=True)
        record = next((r for r in project['sources'] if r['id'] == file_id), None)
        if not record:
            raise ValueError('등록된 원본 파일이 아닙니다.')
        content = self._path(storage_id, file_id).read_bytes()
        if hashlib.sha256(content).hexdigest() != record['sha256']:
            raise ValueError('등록 후 원본 파일 내용이 변경되었습니다. 자료를 다시 확인하세요.')
        return content

    def source_file(self, storage_id: str, file_id: str) -> tuple[Path, str] | None:
        """Path and original filename for download, or None when it does not exist."""
        if storage_id == EXAMPLE_ID:
            try:
                self.example()
            except ValueError:
                return None
            path = self._example[1].get(file_id)
            return (path, path.name) if path and path.is_file() else None
        if not _is_upload_id(storage_id):
            return None
        if self._management(storage_id).get('archived_at'):
            return None
        manifest = self.uploads / storage_id / 'project.json'
        if not manifest.is_file():
            return None
        records = json.loads(manifest.read_text(encoding='utf-8'))['sources']
        record = next((r for r in records if r['id'] == file_id), None)
        if not record:
            return None
        path = self.uploads / storage_id / file_id
        if not path.is_file():
            return None
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != record['sha256']:
            raise ValueError('등록 후 원본 파일 내용이 변경되었습니다. 자료를 다시 확인하세요.')
        return path, record['filename']

    def create(self, inputs: list[tuple[str, bytes]], mappings: dict) -> dict:
        """Parse and register uploaded files under a new opaque storage id."""
        project = build_project(inputs, mappings)
        storage_id = uuid.uuid4().hex
        directory = self.uploads / storage_id
        directory.mkdir(parents=True, exist_ok=True)
        for record, (_, content) in zip(project['sources'], inputs):
            # Stored by opaque content id, never by a caller-provided path.
            if isinstance(content, Path):
                shutil.copyfile(content, directory / record['id'])
            else:
                (directory / record['id']).write_bytes(content)
        project['storage_id'] = storage_id
        _write_json_atomic(directory / 'project.json', project)
        return project

    def list(self, archived: bool = False) -> list[dict]:
        with self.lock:
            return self._list(archived)

    def _list(self, archived: bool = False) -> 'list[dict]':
        """Example (when configured) and uploaded projects, newest first."""
        result = [dict(storage_id=EXAMPLE_ID, label='예제 프로젝트')] if self.example_dir and not archived else []
        if self.uploads.is_dir():
            paths = self.uploads.glob('*/project.json')
            for path in paths:
                try:
                    if not _is_upload_id(path.parent.name) or path.is_symlink() or path.parent.is_symlink():
                        continue
                    metadata = self._management(path.parent.name)
                    if bool(metadata.get('archived_at')) == archived:
                        item = dict(self._catalog_entry(path))
                        item.update(label=metadata.get('label') or item['label'],
                                    managed_revision=metadata['revision'], archived_at=metadata.get('archived_at'))
                        result.append(item)
                except (OSError, ValueError, KeyError):
                    continue
        return sorted(result, key=lambda item: (item.get('created_at', ''), item['storage_id']), reverse=True)


class _RevisionedStore:
    filename = ''

    def __init__(self, root: Path, lock=None):
        self.root = Path(root)
        self.lock = lock or threading.RLock()

    def path(self, storage_id: str) -> Path:
        return self.root / _checked_id(storage_id) / self.filename

    def load(self, storage_id: str) -> dict | None:
        path = self.path(storage_id)
        return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None

    def _current(self, storage_id, project, expected_revision, stale_message, foreign_message):
        current = self.load(storage_id)
        actual = current['revision'] if current else 0
        if type(expected_revision) is not int or expected_revision != actual:
            raise RevisionConflict(stale_message)
        if current and current['project_id'] != project['id']:
            raise RevisionConflict(foreign_message)
        return current, actual


class AnalysisStore(_RevisionedStore):
    """Analysis review documents (<data>/analysis/<id>/review.json)."""

    filename = 'review.json'

    def save(self, storage_id, project, review, expected_revision):
        clean = validate_review_for_save(project, review)
        with self.lock:
            _, actual = self._current(
                storage_id,
                project,
                expected_revision,
                '다른 분석 검토 버전이 있습니다. 저장본을 다시 열어 확인하세요.',
                '원본 프로젝트가 변경되었습니다.',
            )
            clean['revision'] = actual + 1
            _write_json_atomic(self.path(storage_id), clean)
            return clean


class ReportStore(_RevisionedStore):
    """Report documents (<data>/reports/<id>/document.json)."""

    filename = 'document.json'

    def save(self, storage_id, project, document, expected_revision):
        clean = validate_document_for_save(project, document)
        with self.lock:
            current, actual = self._current(
                storage_id,
                project,
                expected_revision,
                '다른 저장 버전이 있습니다. 저장본을 다시 열어 확인하세요.',
                '원자료가 변경되었습니다. 기존 문서를 덮어쓰지 않습니다.',
            )
            carry_review_flags(current, clean)
            # Older clients do not know the explicit source selector.
            if current:
                submitted = {e['sample_id']: e for e in document['samples']}
                previous = {e['sample_id']: e for e in current['samples']}
                for entry in clean['samples']:
                    sid = entry['sample_id']
                    if 'phase_image_source_id' not in submitted[sid]:
                        entry['phase_image_source_id'] = previous.get(sid, {}).get('phase_image_source_id', '')
            # Older clients must not silently clear an existing modify password.
            if 'pptxProtection' not in document and current:
                clean['pptxProtection'] = deepcopy(current.get('pptxProtection') or {})
            for key in ('pageEdits', 'canvasEdits'):
                if key not in document and current and key in current:
                    clean[key] = deepcopy(current[key])
            from .render.editor import validate_assets
            validate_assets(clean, self.path(storage_id).parent / 'editor-assets')
            clean['revision'] = actual + 1
            audit = {'saved_at': datetime.now(timezone.utc).isoformat(),
                     'saved_by': report_actor.get(), 'previous_revision': actual}
            history_dir = self.path(storage_id).parent / 'history'
            if current:
                history = history_dir / f'revision-{actual}.json'
                if not history.exists():
                    _write_json_atomic(history, current)
            # Keep server identity separate from client-editable/draft documents.
            # Only a revision present in document.json is a committed save.
            _write_json_atomic(history_dir / f'revision-{actual+1}.audit.json', audit)
            _write_json_atomic(self.path(storage_id), clean)
            return clean


class PresetStore:
    """Settings presets (<data>/presets/<id>.json): named bundles of report settings. Not
    revisioned; a name is unique and saving an existing name needs `overwrite`."""

    def __init__(self, root: Path, lock=None):
        self.root = Path(root)
        self.lock = lock or threading.RLock()

    def _path(self, preset_id: str) -> Path:
        if not isinstance(preset_id, str) or not _PRESET_ID.fullmatch(preset_id):
            raise ValueError('잘못된 프리셋 ID입니다.')
        return self.root / f'{preset_id}.json'

    def load(self, preset_id: str) -> dict | None:
        path = self._path(preset_id)
        return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None

    def list(self) -> list[dict]:
        found = []
        if self.root.is_dir():
            for path in self.root.glob('*.json'):
                if not _PRESET_ID.fullmatch(path.stem):
                    continue
                try:
                    value = json.loads(path.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    continue
                if isinstance(value, dict) and {'id', 'name', 'settings'} <= set(value):
                    found.append(value)
        return sorted(found, key=lambda p: p['name'])

    def save(self, clean: dict, overwrite: bool = False) -> dict:
        with self.lock:
            existing = next((p for p in self.list() if p['name'] == clean['name']), None)
            if existing and not overwrite:
                raise RevisionConflict('같은 이름의 프리셋이 있습니다. 덮어쓰려면 확인하세요.')
            record = {
                'schema_version': 1,
                'id': existing['id'] if existing else uuid.uuid4().hex,
                'name': clean['name'],
                'created': existing['created']
                if existing
                else datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                'settings': clean['settings'],
            }
            _write_json_atomic(self._path(record['id']), record)
            return record

    def delete(self, preset_id: str) -> bool:
        with self.lock:
            path = self._path(preset_id)
            if not path.is_file():
                return False
            path.unlink()
            return True


class Storage:
    """Everything the routes persist, sharing one lock so multi-step checks are consistent."""

    def __init__(
        self,
        data_dir: Path,
        example_dir: Path | None = None,
        example_mapping: Path | None = None,
    ):
        from .locking import ProcessLock
        self.lock = ProcessLock(Path(data_dir) / '.documents.lock')
        self.files = ProjectFiles(Path(data_dir) / 'uploads', example_dir, example_mapping, self.lock)
        self.reviews = AnalysisStore(Path(data_dir) / 'analysis', self.lock)
        self.reports = ReportStore(Path(data_dir) / 'reports', self.lock)
        self.presets = PresetStore(Path(data_dir) / 'presets', self.lock)

    @classmethod
    def from_settings(cls, settings) -> 'Storage':
        return cls(settings.data_dir, settings.example_dir, settings.example_mapping)

    def load_project(self, storage_id: str) -> dict:
        """The effective project: immutable sources with the saved analysis review applied."""
        with self.lock:
            return apply_review(self.files.load_source(storage_id), self.reviews.load(storage_id))

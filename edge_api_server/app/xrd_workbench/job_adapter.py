"""New instrument reports inside the existing C# job/worker contract.

Old ICDD/Excel jobs are selected by the caller's unchanged legacy adapter.
No input is renamed, rewritten or deleted. ZIP extraction is private and bounded.
"""
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import zipfile

from ..errors import ApiException
from .core.project import classify_filename, safe_filename, ROLE_EXTENSIONS
from .core.report import new_document_with_defaults, validate_document
from .locking import ProcessLock
from .storage import Storage
from .render.package import build_package

LIMIT_FILE = 80 * 1024 * 1024
LIMIT_TOTAL = 1200 * 1024 * 1024
EXTENSIONS = set().union(*ROLE_EXTENSIONS.values())


def uses_workbench(input_dir: Path) -> bool:
    def matched(name):
        return classify_filename(name)['role'] in ('phase_report', 'wppf_report')
    for path in input_dir.rglob('*'):
        if not path.is_file():
            continue
        if matched(path.name):
            return True
        if path.suffix.lower() == '.zip':
            try:
                with zipfile.ZipFile(path) as archive:
                    if any(matched(info.filename) for info in archive.infolist() if not info.is_dir()):
                        return True
            except zipfile.BadZipFile:
                pass  # The established input validator reports malformed archives.
    return False


def _inputs(input_dir, staging):
    inputs, names, total = [], set(), 0
    def reserve(name, size):
        nonlocal total
        normalized = name.replace('\\', '/')
        parts = PurePosixPath(normalized)
        if parts.is_absolute() or '..' in parts.parts or ':' in normalized:
            raise ValueError('ZIP 내부 경로를 확인하세요.')
        base = safe_filename(name)
        if base.startswith('.') or '__MACOSX' in parts.parts:
            return None
        if Path(base).suffix.lower() not in EXTENSIONS:
            raise ValueError(f'신규 XRD에 지원하지 않는 파일입니다: {base}')
        total += size
        if not 0 < size <= LIMIT_FILE or total > LIMIT_TOTAL or len(names) >= 100:
            raise ValueError('XRD 입력 한도: 파일당 80MB, 전체 1200MB, 최대 100개입니다.')
        if base in names:
            raise ValueError(f'같은 파일명이 중복되었습니다: {base}')
        names.add(base)
        return base
    for path in sorted(input_dir.rglob('*')):
        if not path.is_file() or path.name.endswith('.part'):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(input_dir.resolve()):
            raise ValueError('입력 폴더 밖의 링크는 처리하지 않습니다.')
        if path.suffix.lower() == '.zip':
            with zipfile.ZipFile(path) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    name = reserve(info.filename, info.file_size)
                    if name is None:
                        continue
                    target = staging / str(len(inputs))
                    written = 0
                    with archive.open(info) as source, target.open('xb') as out:
                        while block := source.read(1024 * 1024):
                            written += len(block)
                            if written > LIMIT_FILE or written > info.file_size:
                                raise ValueError('ZIP 압축 해제 크기가 한도를 초과했습니다.')
                            out.write(block)
                    inputs.append((name, target))
        else:
            name = reserve(path.relative_to(input_dir).as_posix(), path.stat().st_size)
            if name is not None:
                inputs.append((name, path))
    return inputs


def generate_job_package(settings, job, *, include_raw_files):
    root = settings.storage_root / str(job['root_relative_path'])
    destination = root / 'report'
    destination.mkdir(parents=True, exist_ok=True)
    try:
        # Also serialize against web exports: native rendering libraries have global state.
        with ProcessLock(settings.storage_root / 'xrd-workbench-v1' / '.render.lock'):
            with tempfile.TemporaryDirectory(prefix='xrd-workbench-', dir=root) as temp:
                temp = Path(temp)
                storage = Storage(temp / 'state')
                project = storage.files.create(_inputs(root / 'input', temp), {})
                document = new_document_with_defaults(project)
                document['metadata'].update(
                    report_number=str(job.get('request_number') or ''),
                    experiment_code=str(job.get('experiment_code') or ''),
                    equipment_code=str(job.get('equipment_code') or ''),
                    operator_id=str(job.get('operator_id') or ''),
                    author=str(job.get('operator_id') or ''))
                document = validate_document(project, document)
                output = temp / 'report-package.zip'
                build_package(project, document,
                    lambda fid: storage.files.read_source(project['storage_id'], fid),
                    output_path=output, include_sources=include_raw_files)
                # Generated names are ours, not archive member paths supplied by a client.
                with zipfile.ZipFile(output) as archive:
                    for name in ('보고서.pdf', '보고서.pptx', '뷰어.html'):
                        with archive.open(name) as source, (destination / name).open('wb') as target:
                            shutil.copyfileobj(source, target)
                output.replace(destination / 'report-package.zip')
        return destination / 'report-package.zip', destination / '보고서.pdf'
    except (ValueError, zipfile.BadZipFile) as exc:
        raise ApiException(422, 'XRD_WORKBENCH_INPUT_INVALID', str(exc)) from exc

"""AHN TEM/STEM/EDS/coating report upload workspace."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from io import BytesIO
import os
import re
import shutil
import tempfile
from threading import Lock
import time
from pathlib import Path
from typing import Any
from uuid import uuid4
import zipfile
import zlib

from fastapi import APIRouter, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from PIL import Image, ImageDraw, UnidentifiedImageError
from rist_common import get_logger

from .errors import ApiException
from .config import Settings
from .error_archive import (
    ErrorArchive,
    error_archive as app_error_archive,
    install_error_management,
    record_background_error,
)
from .path_bootstrap import add_project_package_paths
from .usage_archive import (
    UsageArchive,
    record_background_usage,
    set_usage_context,
    usage_archive as app_usage_archive,
)

add_project_package_paths()

from ahn.analysis import DOCX_EXTENSIONS, IMAGE_EXTENSIONS, SPREADSHEET_EXTENSIONS
from ahn.processor import build_outputs

logger = get_logger(__name__)
router = APIRouter()

AHN_SECTION_DIRS = {"tem", "stem", "report", "reports", "scale"}
AHN_SUPPORTED_EXTENSIONS = (
    IMAGE_EXTENSIONS | DOCX_EXTENSIONS | SPREADSHEET_EXTENSIONS | {".zip"}
)
MAX_AHN_UPLOAD_FILE_BYTES = 250 * 1024 * 1024
MAX_AHN_UPLOAD_TOTAL_BYTES = 1200 * 1024 * 1024
AHN_REPORT_JOB_TTL_SECONDS = 2 * 60 * 60
AHN_UPLOAD_CHUNK_BYTES = 1024 * 1024
AHN_UPLOAD_SESSION_TTL_SECONDS = 2 * 60 * 60
OLE_COMPOUND_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
OOXML_REQUIRED_MEMBERS = {
    ".docx": {"[Content_Types].xml", "word/document.xml"},
    ".xlsx": {"[Content_Types].xml", "xl/workbook.xml"},
    ".xlsm": {"[Content_Types].xml", "xl/workbook.xml"},
    ".xlsb": {"[Content_Types].xml", "xl/workbook.bin"},
}


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(1, value)


@dataclass
class AhnReportJob:
    job_id: str
    work_dir: Path
    input_root: Path
    output_dir: Path
    pptx_path: Path
    package_path: Path
    analysis_path: Path
    manifest_path: Path
    manifest: dict[str, Any] | None
    error_archive: ErrorArchive | None
    usage_archive: UsageArchive | None
    created_at: float
    updated_at: float
    status: str = "queued"
    progress_pct: int = 5
    message: str = "TEM 보고서 작업이 대기 중입니다."
    error: dict[str, Any] | None = None
    error_event_id: str | None = None


@dataclass
class AhnUploadFileState:
    relative_path: str
    stored_path: str
    temp_path: str
    total_size: int
    uploaded_bytes: int = 0
    completed: bool = False


@dataclass
class AhnUploadSession:
    upload_id: str
    work_dir: Path
    input_root: Path
    created_at: float
    updated_at: float
    files: dict[str, AhnUploadFileState] = field(default_factory=dict)


_ahn_report_jobs: dict[str, AhnReportJob] = {}
_ahn_report_jobs_lock = Lock()
_ahn_upload_sessions: dict[str, AhnUploadSession] = {}
_ahn_completed_upload_jobs: dict[str, str] = {}
_ahn_upload_sessions_lock = Lock()
_ahn_report_executor = ThreadPoolExecutor(
    max_workers=_positive_int_env("RIST_TEM_REPORT_WORKERS", 1),
    thread_name_prefix="rist-ahn-report",
)


def _safe_relative_path(filename: str | None, fallback: str) -> Path:
    raw = str(filename or "").strip().replace("\\", "/")
    parts = [part for part in raw.split("/") if part and part not in {".", ".."}]
    if not parts:
        parts = [fallback]
    safe_parts: list[str] = []
    for index, part in enumerate(parts):
        clean = re.sub(r"[^\w.\-() \[\]\u3131-\u318e\uac00-\ud7a3]+", "_", part).strip()
        if not clean:
            clean = fallback if index == len(parts) - 1 else "folder"
        safe_parts.append(clean[:180])
    return Path(*safe_parts)


def _unique_path(path: Path) -> Path:
    candidate = path
    index = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        index += 1
    return candidate


def _section_dir_score(directory: Path) -> int:
    if not directory.exists() or not directory.is_dir():
        return 0
    names = {
        child.name.casefold()
        for child in directory.iterdir()
        if child.is_dir() and not child.name.startswith(".")
    }
    return sum(1 for name in AHN_SECTION_DIRS if name in names)


def _find_ahn_input_root(upload_root: Path) -> Path:
    """Accept both `stem/...` and `TopFolder/stem/...` browser uploads."""
    if _section_dir_score(upload_root) > 0:
        return upload_root

    candidates: list[tuple[int, int, Path]] = []
    for directory in upload_root.rglob("*"):
        if not directory.is_dir():
            continue
        score = _section_dir_score(directory)
        if score:
            try:
                depth = len(directory.relative_to(upload_root).parts)
            except ValueError:
                depth = 99
            candidates.append((score, -depth, directory))
    if not candidates:
        return upload_root
    candidates.sort(key=lambda item: (-item[0], -item[1], item[2].as_posix()))
    return candidates[0][2]


def _has_reportable_data(summary: dict[str, Any]) -> bool:
    keys = (
        "temImageCount",
        "stemImageCount",
        "stemBfImageCount",
        "edsReportCount",
        "coatingImageCount",
    )
    return any(int(summary.get(key) or 0) > 0 for key in keys)


def _integrity_issue(path: str, reason: str, *, protected: bool = False) -> dict[str, Any]:
    return {"path": path, "reason": reason, "protected": protected}


def _ole_protection_marker(data: bytes) -> bool:
    markers = ("EncryptedPackage", "EncryptionInfo", "DRMContent", "DataSpaces")
    return any(
        marker.encode("utf-16le") in data or marker.encode("ascii") in data
        for marker in markers
    )


def _validate_image(source: Path | BytesIO | Any, display_path: str) -> list[dict[str, Any]]:
    try:
        with Image.open(source) as image:
            image.verify()
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        return [_integrity_issue(display_path, f"이미지 파일을 열 수 없습니다: {exc}")]
    return []


def _validate_legacy_xls(source: Path | BytesIO, display_path: str) -> list[dict[str, Any]]:
    if isinstance(source, Path):
        with source.open("rb") as stream:
            prefix = stream.read(8 * 1024 * 1024)
    else:
        source.seek(0)
        prefix = source.read(8 * 1024 * 1024)
        source.seek(0)
    if not prefix.startswith(OLE_COMPOUND_MAGIC):
        return [_integrity_issue(display_path, "확장자와 실제 파일 형식이 다른 XLS 파일입니다.")]
    if _ole_protection_marker(prefix):
        return [
            _integrity_issue(
                display_path,
                "암호 또는 DRM으로 보호된 Excel 파일입니다. 보호를 해제한 뒤 다시 저장하세요.",
                protected=True,
            )
        ]
    return []


def _validate_ooxml(
    source: Path | BytesIO,
    suffix: str,
    display_path: str,
) -> list[dict[str, Any]]:
    if isinstance(source, Path):
        with source.open("rb") as stream:
            prefix = stream.read(8 * 1024 * 1024)
    else:
        source.seek(0)
        prefix = source.read(8 * 1024 * 1024)
        source.seek(0)
    if prefix.startswith(OLE_COMPOUND_MAGIC):
        protected = _ole_protection_marker(prefix)
        reason = (
            "암호 또는 DRM으로 보호된 Office 파일입니다. 보호를 해제한 뒤 새 파일로 저장하세요."
            if protected
            else "암호화된 Office 파일이거나 확장자와 실제 파일 형식이 다릅니다. 새 파일로 저장하세요."
        )
        return [_integrity_issue(display_path, reason, protected=True)]

    try:
        with zipfile.ZipFile(source) as archive:
            encrypted = [item.filename for item in archive.infolist() if item.flag_bits & 0x1]
            if encrypted:
                return [
                    _integrity_issue(
                        display_path,
                        "암호화된 Office 파일입니다. 암호를 제거한 뒤 다시 저장하세요.",
                        protected=True,
                    )
                ]
            bad_member = archive.testzip()
            if bad_member:
                return [_integrity_issue(display_path, f"Office 파일 내부가 손상되었습니다: {bad_member}")]
            names = set(archive.namelist())
    except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
        return [_integrity_issue(display_path, f"손상되었거나 올바르지 않은 Office 파일입니다: {exc}")]

    missing = sorted(OOXML_REQUIRED_MEMBERS.get(suffix, set()) - names)
    if missing:
        return [
            _integrity_issue(
                display_path,
                "Office 필수 구성 파일이 없습니다: " + ", ".join(missing),
            )
        ]
    return []


def _validate_zip_archive(path: Path, display_path: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    total_bytes = 0
    try:
        with zipfile.ZipFile(path) as archive:
            encrypted = [item.filename for item in archive.infolist() if item.flag_bits & 0x1]
            if encrypted:
                return [
                    _integrity_issue(
                        display_path,
                        "암호화된 ZIP 파일입니다. 암호를 제거한 뒤 다시 압축하세요.",
                        protected=True,
                    )
                ]
            bad_member = archive.testzip()
            if bad_member:
                return [_integrity_issue(display_path, f"ZIP 내부 파일이 손상되었습니다: {bad_member}")]
            for member in archive.infolist():
                if member.is_dir() or "__MACOSX" in Path(member.filename).parts:
                    continue
                total_bytes += int(member.file_size or 0)
                if int(member.file_size or 0) > MAX_AHN_UPLOAD_FILE_BYTES:
                    return [
                        _integrity_issue(
                            f"{display_path}::{member.filename}",
                            "압축 해제 후 파일 크기가 250MB를 초과합니다.",
                        )
                    ]
                if total_bytes > MAX_AHN_UPLOAD_TOTAL_BYTES:
                    return [_integrity_issue(display_path, "압축 해제 후 전체 크기가 1.2GB를 초과합니다.")]
                member_path = f"{display_path}::{member.filename}"
                suffix = Path(member.filename).suffix.lower()
                if suffix in OOXML_REQUIRED_MEMBERS:
                    data = BytesIO(archive.read(member))
                    issues.extend(_validate_ooxml(data, suffix, member_path))
                elif suffix == ".xls":
                    data = BytesIO(archive.read(member))
                    issues.extend(_validate_legacy_xls(data, member_path))
                elif suffix in IMAGE_EXTENSIONS:
                    with archive.open(member) as stream:
                        issues.extend(_validate_image(stream, member_path))
    except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
        return [_integrity_issue(display_path, f"손상되었거나 올바르지 않은 ZIP 파일입니다: {exc}")]
    return issues


def _validate_ahn_upload_files(upload_root: Path) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    checked = 0
    for path in sorted(upload_root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file() or path.name.startswith(".") or path.name.startswith("~$"):
            continue
        suffix = path.suffix.lower()
        if suffix not in AHN_SUPPORTED_EXTENSIONS:
            continue
        checked += 1
        display_path = path.relative_to(upload_root).as_posix()
        if path.stat().st_size <= 0:
            issues.append(_integrity_issue(display_path, "빈 파일입니다."))
        elif suffix == ".zip":
            issues.extend(_validate_zip_archive(path, display_path))
        elif suffix in OOXML_REQUIRED_MEMBERS:
            issues.extend(_validate_ooxml(path, suffix, display_path))
        elif suffix == ".xls":
            issues.extend(_validate_legacy_xls(path, display_path))
        elif suffix in IMAGE_EXTENSIONS:
            issues.extend(_validate_image(path, display_path))

    if issues:
        protected = any(bool(issue.get("protected")) for issue in issues)
        preview = "; ".join(
            f"{issue['path']}: {issue['reason']}" for issue in issues[:5]
        )
        more = f" 외 {len(issues) - 5}건" if len(issues) > 5 else ""
        raise ApiException(
            400,
            "TEM_PROTECTED_FILE" if protected else "TEM_UPLOAD_INTEGRITY_FAILED",
            f"TEM 업로드 파일 검증에 실패했습니다. {preview}{more}",
            details={"checkedFileCount": checked, "invalidFiles": issues},
        )
    return {"checkedFileCount": checked, "invalidFileCount": 0}


def _extract_zip_file(path: Path, target_root: Path) -> int:
    saved_count = 0
    extracted_bytes = 0
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ApiException(400, "INVALID_TEM_ZIP", "읽을 수 없는 ZIP 파일입니다.") from exc

    with archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            relative = _safe_relative_path(member.filename, "zip-file")
            if "__MACOSX" in relative.parts or relative.name.startswith("."):
                continue
            suffix = relative.suffix.lower()
            if suffix not in (AHN_SUPPORTED_EXTENSIONS - {".zip"}):
                continue
            size = int(member.file_size or 0)
            if size > MAX_AHN_UPLOAD_FILE_BYTES:
                raise ApiException(
                    413,
                    "TEM_FILE_TOO_LARGE",
                    f"{relative.name} 파일이 너무 큽니다. 파일당 최대 250MB입니다.",
                )
            extracted_bytes += size
            if extracted_bytes > MAX_AHN_UPLOAD_TOTAL_BYTES:
                raise ApiException(
                    413,
                    "TEM_UPLOAD_TOO_LARGE",
                    "ZIP 압축 해제 후 TEM raw bundle의 총 크기는 1.2GB 이하여야 합니다.",
                )
            destination = _unique_path(target_root / relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)
            saved_count += 1
    return saved_count


def _extract_pending_zips(upload_root: Path) -> int:
    extracted = 0
    zip_paths = sorted(
        [
            path
            for path in upload_root.rglob("*")
            if path.is_file() and not path.name.startswith(".") and path.suffix.lower() == ".zip"
        ],
        key=lambda path: path.as_posix(),
    )
    for path in zip_paths:
        extracted += _extract_zip_file(path, path.parent)
    return extracted


async def _save_ahn_uploads(files: list[UploadFile] | None, upload_root: Path) -> list[str]:
    if not files:
        raise ApiException(400, "TEM_FILES_REQUIRED", "TEM raw 폴더 또는 파일이 필요합니다.")

    upload_root.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    unsupported: list[str] = []
    total_bytes = 0
    for index, upload in enumerate(files, start=1):
        relative = _safe_relative_path(upload.filename, f"ahn-file-{index}")
        suffix = relative.suffix.lower()
        if not suffix and relative.name.startswith("."):
            continue
        if suffix not in AHN_SUPPORTED_EXTENSIONS:
            unsupported.append(relative.as_posix())
            continue

        destination = _unique_path(upload_root / relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        try:
            with destination.open("wb") as output:
                while True:
                    chunk = await upload.read(AHN_UPLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    written += len(chunk)
                    total_bytes += len(chunk)
                    if written > MAX_AHN_UPLOAD_FILE_BYTES:
                        raise ApiException(
                            413,
                            "TEM_FILE_TOO_LARGE",
                            f"{relative.name} 파일이 너무 큽니다. 파일당 최대 250MB입니다.",
                        )
                    if total_bytes > MAX_AHN_UPLOAD_TOTAL_BYTES:
                        raise ApiException(
                            413,
                            "TEM_UPLOAD_TOO_LARGE",
                            "한 번에 업로드하는 TEM raw bundle의 총 크기는 1.2GB 이하여야 합니다.",
                        )
                    output.write(chunk)
        finally:
            await upload.close()

        if not written:
            destination.unlink(missing_ok=True)
            continue
        saved.append(destination.relative_to(upload_root).as_posix())

    if unsupported:
        preview = ", ".join(unsupported[:5])
        more = f" 외 {len(unsupported) - 5}개" if len(unsupported) > 5 else ""
        allowed = ", ".join(sorted(AHN_SUPPORTED_EXTENSIONS))
        raise ApiException(
            400,
            "INVALID_TEM_FILE_TYPE",
            f"지원하지 않는 파일이 포함되어 있습니다: {preview}{more}. 허용 형식: {allowed}",
        )
    if not saved:
        raise ApiException(400, "TEM_FILES_REQUIRED", "분석 가능한 TEM 파일이 없습니다.")
    return saved


async def _write_upload_chunk(
    session: AhnUploadSession,
    *,
    relative_path: str,
    offset: int,
    total_size: int,
    chunk_index: int,
    chunk_count: int,
    chunk_crc32: str | None,
    upload: UploadFile,
) -> AhnUploadFileState:
    relative = _safe_relative_path(relative_path, f"ahn-upload-{chunk_index}")
    suffix = relative.suffix.lower()
    if not suffix and relative.name.startswith("."):
        raise ApiException(400, "INVALID_TEM_FILE_TYPE", f"숨김 파일은 업로드하지 않습니다: {relative}")
    if suffix not in AHN_SUPPORTED_EXTENSIONS:
        allowed = ", ".join(sorted(AHN_SUPPORTED_EXTENSIONS))
        raise ApiException(
            400,
            "INVALID_TEM_FILE_TYPE",
            f"지원하지 않는 파일입니다: {relative.as_posix()}. 허용 형식: {allowed}",
        )
    if total_size <= 0:
        raise ApiException(400, "TEM_EMPTY_FILE", f"빈 파일은 업로드하지 않습니다: {relative.name}")
    if total_size > MAX_AHN_UPLOAD_FILE_BYTES:
        raise ApiException(
            413,
            "TEM_FILE_TOO_LARGE",
            f"{relative.name} 파일이 너무 큽니다. 파일당 최대 250MB입니다.",
        )
    if offset < 0 or offset > total_size:
        raise ApiException(400, "TEM_INVALID_UPLOAD_OFFSET", "TEM 업로드 offset 값이 올바르지 않습니다.")
    if chunk_index < 0 or chunk_count <= 0 or chunk_index >= chunk_count:
        raise ApiException(400, "TEM_INVALID_UPLOAD_CHUNK", "TEM 업로드 chunk 값이 올바르지 않습니다.")
    expected_crc32 = str(chunk_crc32 or "").strip().lower()
    if expected_crc32 and not re.fullmatch(r"[0-9a-f]{8}", expected_crc32):
        raise ApiException(400, "TEM_INVALID_CHUNK_CHECKSUM", "TEM 업로드 조각 체크섬이 올바르지 않습니다.")

    file_key = relative.as_posix()
    with _ahn_upload_sessions_lock:
        file_state = session.files.get(file_key)
        if file_state is None:
            expected_total = _upload_session_expected_total(session) + total_size
            if expected_total > MAX_AHN_UPLOAD_TOTAL_BYTES:
                raise ApiException(
                    413,
                    "TEM_UPLOAD_TOO_LARGE",
                    "한 번에 업로드하는 TEM raw bundle의 총 크기는 1.2GB 이하여야 합니다.",
                )
            destination = _unique_path(session.input_root / relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temp_path = destination.with_name(destination.name + ".part")
            file_state = AhnUploadFileState(
                relative_path=file_key,
                stored_path=destination.relative_to(session.input_root).as_posix(),
                temp_path=temp_path.relative_to(session.input_root).as_posix(),
                total_size=total_size,
            )
            session.files[file_key] = file_state
        elif file_state.total_size != total_size:
            raise ApiException(400, "TEM_UPLOAD_SIZE_CHANGED", "업로드 중 파일 크기가 변경되었습니다.")
        session.updated_at = time.time()

    destination = session.input_root / file_state.stored_path
    temp_path = session.input_root / file_state.temp_path
    if file_state.completed:
        if destination.exists() and destination.stat().st_size == total_size:
            await upload.close()
            return file_state
        file_state.completed = False

    current_size = temp_path.stat().st_size if temp_path.exists() else 0
    if current_size < offset:
        raise ApiException(
            409,
            "TEM_UPLOAD_OFFSET_MISMATCH",
            "이전 업로드 조각이 아직 서버에 없습니다. 잠시 후 다시 시도하세요.",
            details={"expectedOffset": current_size, "receivedOffset": offset},
        )

    written = 0
    received_crc32 = 0
    received_chunks: list[bytes] = []
    try:
        while True:
            chunk = await upload.read(AHN_UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            written += len(chunk)
            if offset + written > total_size:
                raise ApiException(
                    400,
                    "TEM_UPLOAD_CHUNK_TOO_LARGE",
                    "업로드 조각 크기가 파일 크기를 초과했습니다.",
                )
            received_crc32 = zlib.crc32(chunk, received_crc32)
            received_chunks.append(chunk)
    finally:
        await upload.close()

    actual_crc32 = f"{received_crc32 & 0xFFFFFFFF:08x}"
    if expected_crc32 and actual_crc32 != expected_crc32:
        raise ApiException(
            400,
            "TEM_UPLOAD_CHUNK_CHECKSUM_MISMATCH",
            f"업로드 조각 무결성 검사에 실패했습니다: {relative.name}. 같은 파일을 다시 업로드하세요.",
            retryable=True,
            details={"expectedCrc32": expected_crc32, "actualCrc32": actual_crc32},
        )

    temp_path.parent.mkdir(parents=True, exist_ok=True)
    with temp_path.open("r+b" if temp_path.exists() else "wb") as output:
        output.seek(offset)
        for chunk in received_chunks:
            output.write(chunk)
        output.truncate(offset + written)

    file_state.uploaded_bytes = min(total_size, offset + written)
    if file_state.uploaded_bytes == total_size:
        if destination.exists():
            destination.unlink()
        temp_path.replace(destination)
        file_state.completed = True
    with _ahn_upload_sessions_lock:
        session.updated_at = time.time()
    return file_state


def _cleanup_old_jobs() -> None:
    _cleanup_old_upload_sessions()
    now = time.time()
    with _ahn_report_jobs_lock:
        expired = [
            job_id
            for job_id, job in _ahn_report_jobs.items()
            if now - job.created_at > AHN_REPORT_JOB_TTL_SECONDS
            and job.status not in {"queued", "running"}
        ]
        jobs = [
            _ahn_report_jobs.pop(job_id, None)
            for job_id in expired
        ]
    for job in jobs:
        if job:
            shutil.rmtree(job.work_dir, ignore_errors=True)
    if expired:
        expired_set = set(expired)
        with _ahn_upload_sessions_lock:
            for upload_id, job_id in list(_ahn_completed_upload_jobs.items()):
                if job_id in expired_set:
                    _ahn_completed_upload_jobs.pop(upload_id, None)


def _build_package(output_dir: Path, package_path: Path) -> Path:
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.rglob("*")):
            if not path.is_file() or path == package_path:
                continue
            archive.write(path, path.relative_to(output_dir).as_posix())
    return package_path


def _cleanup_old_upload_sessions() -> None:
    now = time.time()
    with _ahn_upload_sessions_lock:
        expired = [
            upload_id
            for upload_id, session in _ahn_upload_sessions.items()
            if now - session.updated_at > AHN_UPLOAD_SESSION_TTL_SECONDS
        ]
        sessions = [
            _ahn_upload_sessions.pop(upload_id, None)
            for upload_id in expired
        ]
    for session in sessions:
        if session:
            shutil.rmtree(session.work_dir, ignore_errors=True)


def _create_upload_session() -> AhnUploadSession:
    work_dir = Path(tempfile.mkdtemp(prefix="rist-ahn-upload-"))
    input_root = work_dir / "input"
    input_root.mkdir(parents=True, exist_ok=True)
    upload_id = uuid4().hex
    now = time.time()
    session = AhnUploadSession(
        upload_id=upload_id,
        work_dir=work_dir,
        input_root=input_root,
        created_at=now,
        updated_at=now,
    )
    with _ahn_upload_sessions_lock:
        _ahn_upload_sessions[upload_id] = session
    return session


def _get_upload_session(upload_id: str) -> AhnUploadSession:
    with _ahn_upload_sessions_lock:
        session = _ahn_upload_sessions.get(upload_id)
        if session is not None:
            session.updated_at = time.time()
    if session is None:
        raise ApiException(
            404,
            "TEM_UPLOAD_SESSION_NOT_FOUND",
            "TEM 업로드 세션을 찾을 수 없습니다. 파일을 다시 선택해 업로드하세요.",
        )
    return session


def _upload_session_expected_total(session: AhnUploadSession) -> int:
    return sum(int(file_state.total_size or 0) for file_state in session.files.values())


def _upload_session_payload(session: AhnUploadSession) -> dict[str, Any]:
    completed_files = [state for state in session.files.values() if state.completed]
    total_size = _upload_session_expected_total(session)
    uploaded_bytes = sum(
        int(state.total_size if state.completed else state.uploaded_bytes)
        for state in session.files.values()
    )
    return {
        "uploadId": session.upload_id,
        "fileCount": len(session.files),
        "completedFileCount": len(completed_files),
        "uploadedBytes": uploaded_bytes,
        "totalBytes": total_size,
    }


def _job_for_completed_upload(upload_id: str) -> AhnReportJob | None:
    with _ahn_upload_sessions_lock:
        job_id = _ahn_completed_upload_jobs.get(upload_id)
    if not job_id:
        return None
    with _ahn_report_jobs_lock:
        return _ahn_report_jobs.get(job_id)


def _set_job_state(
    job: AhnReportJob,
    *,
    status: str | None = None,
    progress_pct: int | None = None,
    message: str | None = None,
    error: dict[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
) -> None:
    with _ahn_report_jobs_lock:
        if status is not None:
            job.status = status
        if progress_pct is not None:
            job.progress_pct = max(0, min(100, int(progress_pct)))
        if message is not None:
            job.message = message
        if error is not None:
            job.error = error
        if manifest is not None:
            job.manifest = manifest
        job.updated_at = time.time()


def _api_error_payload(exc: ApiException) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": exc.status_code,
        "code": exc.code,
        "message": exc.message,
        "retryable": exc.retryable,
    }
    if exc.details is not None:
        payload["details"] = exc.details
    return payload


def _create_ahn_job(
    input_root: Path,
    work_dir: Path,
    error_archive: ErrorArchive | None,
    usage_archive: UsageArchive | None = None,
) -> AhnReportJob:
    output_dir = work_dir / "output"
    pptx_path = output_dir / "tem-report.pptx"
    job_id = uuid4().hex
    now = time.time()
    job = AhnReportJob(
        job_id=job_id,
        work_dir=work_dir,
        input_root=input_root,
        output_dir=output_dir,
        pptx_path=pptx_path,
        package_path=output_dir / "tem-report-package.zip",
        analysis_path=output_dir / "analysis-result.json",
        manifest_path=output_dir / "manifest.json",
        manifest=None,
        error_archive=error_archive,
        usage_archive=usage_archive,
        created_at=now,
        updated_at=now,
    )
    with _ahn_report_jobs_lock:
        _ahn_report_jobs[job_id] = job
    return job


def _run_ahn_job(job: AhnReportJob) -> None:
    started = time.perf_counter()
    _set_job_state(
        job,
        status="running",
        progress_pct=25,
        message="TEM/STEM/EDS/코팅층 데이터를 분석하는 중입니다.",
    )
    try:
        _set_job_state(job, progress_pct=28, message="검증된 raw bundle 구조를 준비하는 중입니다.")
        extracted_count = _extract_pending_zips(job.input_root)
        if extracted_count:
            _set_job_state(
                job,
                progress_pct=32,
                message=f"ZIP 파일 압축 해제 완료: {extracted_count}개 파일을 확인했습니다.",
            )
        job.input_root = _find_ahn_input_root(job.input_root)

        def update_progress(_stage: str, progress_pct: int, message: str) -> None:
            _set_job_state(job, progress_pct=progress_pct, message=message)

        manifest = build_outputs(
            input_dir=job.input_root,
            output_dir=job.output_dir,
            pptx_path=job.pptx_path,
            copy_raw_spreadsheets=True,
            progress_callback=update_progress,
        )
        _set_job_state(job, progress_pct=88, message="보고서 ZIP 패키지를 만드는 중입니다.")
    except ApiException as exc:
        _set_job_state(
            job,
            status="failed",
            progress_pct=100,
            message=exc.message,
            error=_api_error_payload(exc),
        )
        event_id = record_background_error(
            job.error_archive,
            project="TEM",
            code=exc.code,
            message=exc.message,
            exception=exc,
            job_id=job.job_id,
            details=exc.details,
            source_paths=[job.input_root],
        )
        with _ahn_report_jobs_lock:
            job.error_event_id = event_id
        record_background_usage(
            job.usage_archive,
            project="TEM",
            action="보고서 생성 실패",
            result="failure",
            duration_ms=round((time.perf_counter() - started) * 1000),
            job_id=job.job_id,
            endpoint=f"/background/tem/report/jobs/{job.job_id}",
            experiment_code="TEM",
        )
        return
    except Exception as exc:
        logger.exception("TEM 보고서 생성 실패 (input_root=%s)", job.input_root)
        api_exc = ApiException(
            500,
            "TEM_REPORT_BUILD_FAILED",
            f"TEM 보고서 생성 중 오류가 발생했습니다: {exc}",
            retryable=False,
            details={"exceptionType": type(exc).__name__},
        )
        _set_job_state(
            job,
            status="failed",
            progress_pct=100,
            message=api_exc.message,
            error=_api_error_payload(api_exc),
        )
        event_id = record_background_error(
            job.error_archive,
            project="TEM",
            code=api_exc.code,
            message=api_exc.message,
            exception=exc,
            job_id=job.job_id,
            details=api_exc.details,
            source_paths=[job.input_root],
        )
        with _ahn_report_jobs_lock:
            job.error_event_id = event_id
        record_background_usage(
            job.usage_archive,
            project="TEM",
            action="보고서 생성 실패",
            result="failure",
            duration_ms=round((time.perf_counter() - started) * 1000),
            job_id=job.job_id,
            endpoint=f"/background/tem/report/jobs/{job.job_id}",
            experiment_code="TEM",
        )
        return
    summary = manifest.get("summary") if isinstance(manifest.get("summary"), dict) else {}
    if not _has_reportable_data(summary):
        api_exc = ApiException(
            400,
            "TEM_NO_REPORT_DATA",
            "입력 폴더에서 TEM, STEM, EDS, 코팅층 분석 대상 데이터를 찾지 못했습니다.",
        )
        _set_job_state(
            job,
            status="failed",
            progress_pct=100,
            message=api_exc.message,
            error=_api_error_payload(api_exc),
        )
        event_id = record_background_error(
            job.error_archive,
            project="TEM",
            code=api_exc.code,
            message=api_exc.message,
            job_id=job.job_id,
            source_paths=[job.input_root],
        )
        with _ahn_report_jobs_lock:
            job.error_event_id = event_id
        record_background_usage(
            job.usage_archive,
            project="TEM",
            action="보고서 생성 실패",
            result="failure",
            duration_ms=round((time.perf_counter() - started) * 1000),
            job_id=job.job_id,
            endpoint=f"/background/tem/report/jobs/{job.job_id}",
            experiment_code="TEM",
        )
        return
    try:
        _build_package(job.output_dir, job.package_path)
    except Exception as exc:
        logger.exception("TEM 보고서 패키지 생성 실패 (job_id=%s)", job.job_id)
        api_exc = ApiException(
            500,
            "TEM_REPORT_PACKAGE_FAILED",
            f"TEM 보고서 ZIP 패키지 생성 중 오류가 발생했습니다: {exc}",
            retryable=False,
            details={"exceptionType": type(exc).__name__},
        )
        _set_job_state(
            job,
            status="failed",
            progress_pct=100,
            message=api_exc.message,
            error=_api_error_payload(api_exc),
        )
        event_id = record_background_error(
            job.error_archive,
            project="TEM",
            code=api_exc.code,
            message=api_exc.message,
            exception=exc,
            job_id=job.job_id,
            details=api_exc.details,
            source_paths=[job.output_dir],
        )
        with _ahn_report_jobs_lock:
            job.error_event_id = event_id
        record_background_usage(
            job.usage_archive,
            project="TEM",
            action="보고서 생성 실패",
            result="failure",
            duration_ms=round((time.perf_counter() - started) * 1000),
            job_id=job.job_id,
            endpoint=f"/background/tem/report/jobs/{job.job_id}",
            experiment_code="TEM",
        )
        return
    _set_job_state(
        job,
        status="completed",
        progress_pct=100,
        message="TEM 보고서가 완성되었습니다.",
        manifest=manifest,
    )
    record_background_usage(
        job.usage_archive,
        project="TEM",
        action="보고서 생성 완료",
        result="success",
        duration_ms=round((time.perf_counter() - started) * 1000),
        job_id=job.job_id,
        endpoint=f"/background/tem/report/jobs/{job.job_id}",
        experiment_code="TEM",
        file_name=job.package_path.name,
        file_size_bytes=(
            job.package_path.stat().st_size if job.package_path.is_file() else None
        ),
    )


def _submit_ahn_job(
    input_root: Path,
    work_dir: Path,
    error_archive: ErrorArchive | None = None,
    usage_archive: UsageArchive | None = None,
) -> AhnReportJob:
    job = _create_ahn_job(input_root, work_dir, error_archive, usage_archive)
    _ahn_report_executor.submit(_run_ahn_job, job)
    return job


def _write_synthetic_tem_example(input_root: Path) -> Path:
    """Create a tiny built-in example when repository sample data is absent."""
    images = [
        (input_root / "tem" / "Example-A" / "Example-A_100kX.tif", "TEM 100kX", (205, 211, 222)),
        (input_root / "stem" / "Example-A_120kX.tif", "STEM 120kX", (196, 210, 224)),
        (input_root / "stem" / "BF_Example-A_120kX.tif", "BF-STEM 120kX", (226, 221, 210)),
    ]
    for path, label, color in images:
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (960, 720), color)
        draw = ImageDraw.Draw(image)
        draw.rectangle((80, 80, 880, 620), outline=(48, 65, 88), width=5)
        draw.line((120, 565, 360, 565), fill=(20, 36, 58), width=12)
        draw.text((120, 590), "200 nm", fill=(20, 36, 58))
        draw.text((95, 100), label, fill=(20, 36, 58))
        image.save(path, format="TIFF")
    return input_root


def _job_payload(job: AhnReportJob) -> dict[str, Any]:
    prefix = f"/api/v1/tem/report/jobs/{job.job_id}/download"
    manifest = job.manifest or {}
    downloads = None
    if job.status == "completed":
        downloads = {
            "pptx": f"{prefix}/pptx",
            "package": f"{prefix}/package",
            "analysisJson": f"{prefix}/analysis-json",
        }
    return {
        "jobId": job.job_id,
        "status": job.status,
        "progressPct": job.progress_pct,
        "message": job.message,
        "summary": manifest.get("summary") or {},
        "manifest": manifest,
        "downloads": downloads,
        "error": job.error,
        "errorEventId": job.error_event_id,
    }


def build_ahn_page() -> str:
    return """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RIST TEM/STEM</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #172a46;
      --muted: #64748b;
      --line: #cbd5e1;
      --blue: #2563eb;
      --green: #16a34a;
      --bg: #f8fafc;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Arial, "Noto Sans KR", sans-serif;
      color: var(--ink);
      background: var(--bg);
    }
    .ahn-shell { min-height: 100vh; display: flex; flex-direction: column; }
    .ahn-topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 24px 28px;
      border-bottom: 1px solid var(--line);
      background: #fff;
    }
    .ahn-brand { display: flex; align-items: baseline; gap: 14px; min-width: 0; }
    .ahn-brand h1 { margin: 0; font-size: 30px; letter-spacing: 0; }
    .ahn-brand span { color: var(--muted); font-size: 17px; white-space: nowrap; }
    .ahn-actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
    button, .ahn-download {
      border: 1px solid #9fb6d6;
      background: #fff;
      color: var(--ink);
      border-radius: 7px;
      min-height: 42px;
      padding: 9px 14px;
      font-size: 15px;
      font-weight: 700;
      text-decoration: none;
      cursor: pointer;
    }
    button.primary { border-color: var(--blue); background: var(--blue); color: #fff; }
    button:disabled, .ahn-download[aria-disabled="true"] {
      opacity: .48;
      cursor: not-allowed;
      pointer-events: none;
    }
    .ahn-main { padding: 18px 28px 28px; display: grid; gap: 16px; }
    .ahn-panel {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }
    .ahn-hidden-input { display: none; }
    .ahn-drop {
      border: 1px dashed #9fb6d6;
      border-radius: 8px;
      min-height: 150px;
      color: #476483;
      background: #f8fbff;
      text-align: center;
      padding: 18px;
      display: grid;
      place-items: center;
    }
    .ahn-drop.dragover { border-color: var(--blue); background: #eef6ff; }
    .ahn-drop-title {
      margin: 0 0 5px;
      color: var(--ink);
      font-size: 18px;
      font-weight: 800;
    }
    .ahn-drop-text { margin: 0; color: #476483; line-height: 1.45; }
    .ahn-bundle-actions {
      margin-top: 13px;
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: center;
      align-items: center;
    }
    .ahn-bundle-actions button { min-height: 38px; padding: 7px 12px; font-size: 14px; }
    .ahn-bundle-meta {
      margin-top: 10px;
      color: var(--muted);
      font-size: 13px;
      text-align: center;
    }
    .ahn-files {
      margin-top: 12px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .ahn-chip {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 6px 10px;
      background: #fff;
      color: #334155;
      font-size: 13px;
      max-width: 300px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .ahn-status-stack { display: grid; gap: 8px; }
    .ahn-status {
      min-height: 38px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 8px 10px 8px 12px;
      border-radius: 7px;
      background: #ecfdf5;
      border: 1px solid #bbf7d0;
      color: #166534;
      font-size: 14px;
      transition: opacity 180ms ease, transform 180ms ease;
    }
    .ahn-status.error { background: #fef2f2; border-color: #fecaca; color: #991b1b; }
    .ahn-status.is-hiding { opacity: 0; transform: translateY(-4px); }
    .ahn-status-text { min-width: 0; overflow-wrap: anywhere; }
    .ahn-status-close {
      min-height: 0;
      border: 0;
      background: transparent;
      color: currentColor;
      cursor: pointer;
      font-size: 17px;
      font-weight: 800;
      line-height: 1;
      padding: 2px 4px;
      opacity: .72;
    }
    .ahn-status-close:hover { opacity: 1; background: rgba(15, 23, 42, .06); }
    .ahn-progress {
      display: none;
      padding: 11px 14px 12px;
      border: 1px solid #bfdbfe;
      border-radius: 8px;
      background: #eff6ff;
      color: #1e3a8a;
      font-size: 13px;
    }
    .ahn-progress.is-visible {
      position: fixed;
      left: 50%;
      top: 50%;
      z-index: 70;
      display: block;
      width: min(560px, calc(100vw - 32px));
      transform: translate(-50%, -50%);
      box-shadow: 0 18px 48px rgba(15, 23, 42, 0.18);
    }
    .ahn-progress.is-error {
      border-color: #fecaca;
      background: #fef2f2;
      color: #991b1b;
    }
    .ahn-progress-stage { display: none; }
    .ahn-progress-stage.is-visible { display: block; }
    .ahn-progress-stage + .ahn-progress-stage {
      margin-top: 11px;
      padding-top: 10px;
      border-top: 1px solid rgba(30, 64, 175, .16);
    }
    .ahn-progress-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 7px;
      font-weight: 700;
    }
    .ahn-progress-track {
      overflow: hidden;
      height: 7px;
      border-radius: 999px;
      background: #dbeafe;
    }
    .ahn-progress-bar {
      width: 0%;
      height: 100%;
      border-radius: inherit;
      background: var(--blue);
      transition: width 240ms ease;
    }
    .ahn-upload-progress .ahn-progress-bar { background: #16a34a; }
    .ahn-progress.is-error .ahn-progress-bar { background: #dc2626; }
    .ahn-result {
      display: grid;
      gap: 14px;
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }
    .ahn-result[hidden] { display: none; }
    .ahn-summary {
      display: grid;
      grid-template-columns: repeat(4, minmax(120px, 1fr));
      gap: 10px;
    }
    .ahn-summary-item {
      border: 1px solid #d8e2ef;
      border-radius: 8px;
      padding: 12px;
      background: #fbfdff;
    }
    .ahn-summary-item span { display: block; color: var(--muted); font-size: 13px; }
    .ahn-summary-item strong { display: block; margin-top: 5px; font-size: 22px; color: var(--ink); }
    .ahn-downloads { display: flex; gap: 8px; flex-wrap: wrap; }
    .ahn-empty {
      min-height: 220px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--muted);
      font-weight: 700;
      text-align: center;
      border: 1px dashed var(--line);
      border-radius: 8px;
      background: #fff;
    }
    .ahn-busy {
      position: fixed;
      inset: 0;
      display: none;
      align-items: center;
      justify-content: center;
      background: rgba(255,255,255,.74);
      z-index: 50;
      color: var(--ink);
      font-weight: 700;
    }
    .ahn-busy.show { display: flex; }
    @media (max-width: 900px) {
      .ahn-topbar { align-items: flex-start; padding: 18px 16px; }
      .ahn-brand { display: block; }
      .ahn-brand h1 { font-size: 25px; }
      .ahn-brand span { display: block; margin-top: 4px; white-space: normal; }
      .ahn-main { padding: 14px 12px 24px; }
      .ahn-actions { width: 100%; justify-content: flex-end; }
      button, .ahn-download { min-height: 40px; padding: 8px 11px; font-size: 14px; }
      .ahn-summary { grid-template-columns: repeat(2, minmax(120px, 1fr)); }
    }
  </style>
</head>
<body>
  <div class="ahn-shell">
    <header class="ahn-topbar">
      <div class="ahn-brand">
        <h1>TEM/STEM</h1>
        <span>folder upload · OCR · PowerPoint report</span>
      </div>
      <div class="ahn-actions">
        <button type="button" id="ahn-example">예제 불러오기</button>
        <button type="submit" form="ahn-form" class="primary" id="ahn-run" disabled>보고서 생성</button>
        <button type="button" id="ahn-clear">초기화</button>
      </div>
    </header>
    <main class="ahn-main">
      <section class="ahn-panel">
        <form id="ahn-form">
          <input class="ahn-hidden-input" type="file" id="ahn-bundle-files" name="files" multiple accept=".tif,.tiff,.png,.jpg,.jpeg,.bmp,.webp,.docx,.xlsx,.xls,.xlsm,.xlsb,.csv,.tsv,.zip">
          <input class="ahn-hidden-input" type="file" id="ahn-bundle-folder" name="files" multiple webkitdirectory directory>
          <div class="ahn-drop" id="ahn-drop">
            <div>
              <p class="ahn-drop-title">TEM raw bundle 추가</p>
              <p class="ahn-drop-text">tem, stem, report, scale 폴더를 포함한 raw 폴더 또는 ZIP을 여기에 드래그하거나 폴더째 선택하세요.</p>
              <div class="ahn-bundle-actions">
                <button type="button" id="ahn-add-files">파일 추가</button>
                <button type="button" id="ahn-add-folder">폴더 추가</button>
              </div>
              <div class="ahn-bundle-meta" id="ahn-bundle-meta">선택된 파일 없음</div>
            </div>
          </div>
          <div class="ahn-files" id="ahn-file-list"></div>
        </form>
      </section>
      <div class="ahn-status-stack" id="ahn-status" aria-live="polite"></div>
      <div class="ahn-progress" id="ahn-progress" aria-live="polite">
        <div class="ahn-progress-stage ahn-upload-progress" id="ahn-upload-progress">
          <div class="ahn-progress-row">
            <span id="ahn-upload-progress-label">raw 파일 업로드 대기</span>
            <span id="ahn-upload-progress-value">0%</span>
          </div>
          <div class="ahn-progress-track">
            <div class="ahn-progress-bar" id="ahn-upload-progress-bar"></div>
          </div>
        </div>
        <div class="ahn-progress-stage" id="ahn-report-progress">
          <div class="ahn-progress-row">
            <span id="ahn-progress-label">보고서 생성 대기</span>
            <span id="ahn-progress-value">0%</span>
          </div>
          <div class="ahn-progress-track">
            <div class="ahn-progress-bar" id="ahn-progress-bar"></div>
          </div>
        </div>
      </div>
      <section class="ahn-result" id="ahn-result" hidden>
        <div class="ahn-summary" id="ahn-summary"></div>
        <div class="ahn-downloads">
          <a href="#" class="ahn-download" id="ahn-download-pptx" aria-disabled="true">PPTX 다운로드</a>
          <a href="#" class="ahn-download" id="ahn-download-package" aria-disabled="true">보고서 ZIP 다운로드</a>
          <a href="#" class="ahn-download" id="ahn-download-json" aria-disabled="true">분석 JSON 다운로드</a>
        </div>
      </section>
      <section class="ahn-empty" id="ahn-empty">TEM/STEM/EDS/코팅층 raw 폴더를 올리면 PPT 보고서를 생성합니다.</section>
    </main>
  </div>
  <div class="ahn-busy" id="ahn-busy">TEM 보고서를 생성하는 중입니다.</div>
  <script>
  (function() {
    var form = document.getElementById("ahn-form");
    var bundleInput = document.getElementById("ahn-bundle-files");
    var folderInput = document.getElementById("ahn-bundle-folder");
    var addFilesButton = document.getElementById("ahn-add-files");
    var addFolderButton = document.getElementById("ahn-add-folder");
    var runButton = document.getElementById("ahn-run");
    var clearButton = document.getElementById("ahn-clear");
    var exampleButton = document.getElementById("ahn-example");
    var status = document.getElementById("ahn-status");
    var busy = document.getElementById("ahn-busy");
    var drop = document.getElementById("ahn-drop");
    var fileList = document.getElementById("ahn-file-list");
    var bundleMeta = document.getElementById("ahn-bundle-meta");
    var progress = document.getElementById("ahn-progress");
    var uploadProgress = document.getElementById("ahn-upload-progress");
    var uploadProgressLabel = document.getElementById("ahn-upload-progress-label");
    var uploadProgressValue = document.getElementById("ahn-upload-progress-value");
    var uploadProgressBar = document.getElementById("ahn-upload-progress-bar");
    var reportProgress = document.getElementById("ahn-report-progress");
    var progressLabel = document.getElementById("ahn-progress-label");
    var progressValue = document.getElementById("ahn-progress-value");
    var progressBar = document.getElementById("ahn-progress-bar");
    var result = document.getElementById("ahn-result");
    var empty = document.getElementById("ahn-empty");
    var summary = document.getElementById("ahn-summary");
    var downloadPptx = document.getElementById("ahn-download-pptx");
    var downloadPackage = document.getElementById("ahn-download-package");
    var downloadJson = document.getElementById("ahn-download-json");
    var bundleItems = [];
    var progressTimer = null;
    var TEM_UPLOAD_CHUNK_BYTES = 4 * 1024 * 1024;
    var TEM_UPLOAD_CHUNK_RETRIES = 4;
    var TEM_MAX_FILE_BYTES = 250 * 1024 * 1024;
    var TEM_MAX_TOTAL_BYTES = 1200 * 1024 * 1024;
    var uploadProgressVisible = false;
    var reportProgressVisible = false;
    var uploadProgressError = false;
    var reportProgressError = false;
    var operationBusy = false;
    var collectingFiles = false;

    function syncActionState() {
      runButton.disabled = operationBusy || collectingFiles || !bundleItems.length;
      exampleButton.disabled = operationBusy || collectingFiles;
      addFilesButton.disabled = operationBusy || collectingFiles;
      addFolderButton.disabled = operationBusy || collectingFiles;
      clearButton.disabled = operationBusy || collectingFiles;
      drop.setAttribute("aria-busy", collectingFiles ? "true" : "false");
    }

    function setCollectingFiles(value, message) {
      collectingFiles = Boolean(value);
      if (collectingFiles) {
        bundleMeta.textContent = message || "첨부 파일 목록을 읽는 중입니다.";
      } else {
        renderFileList();
      }
      syncActionState();
    }

    function setStatus(message, error) {
      if (!message) return;
      var item = document.createElement("div");
      item.className = "ahn-status" + (error ? " error" : "");
      var text = document.createElement("span");
      text.className = "ahn-status-text";
      text.textContent = message;
      var close = document.createElement("button");
      close.type = "button";
      close.className = "ahn-status-close";
      close.setAttribute("aria-label", "알림 닫기");
      close.textContent = "×";
      item.appendChild(text);
      item.appendChild(close);
      status.appendChild(item);
      var timer = null;
      function remove() {
        if (timer) clearTimeout(timer);
        item.classList.add("is-hiding");
        setTimeout(function() {
          if (item.parentNode) item.parentNode.removeChild(item);
        }, 190);
      }
      close.addEventListener("click", remove);
      timer = setTimeout(remove, error ? 7200 : 4300);
    }
    function setBusy(value) {
      operationBusy = Boolean(value);
      busy.classList.toggle("show", Boolean(value));
      syncActionState();
    }
    function progressMessage(percent) {
      if (percent < 25) return "raw 파일을 서버로 전송하는 중입니다.";
      if (percent < 48) return "TEM/STEM/EDS/코팅층 폴더를 분류하는 중입니다.";
      if (percent < 72) return "코팅층 OCR과 분석 JSON을 만드는 중입니다.";
      return "PowerPoint 보고서를 생성하는 중입니다. 완료되면 다운로드 버튼이 활성화됩니다.";
    }
    function updateProgressVisibility() {
      progress.classList.toggle("is-visible", uploadProgressVisible || reportProgressVisible);
      progress.classList.toggle("is-error", uploadProgressError || reportProgressError);
      uploadProgress.classList.toggle("is-visible", uploadProgressVisible);
      reportProgress.classList.toggle("is-visible", reportProgressVisible);
    }
    function setUploadProgress(percent, message, visible, error) {
      var pct = Math.max(0, Math.min(100, Math.round(Number(percent) || 0)));
      uploadProgressVisible = Boolean(visible);
      uploadProgressError = Boolean(error);
      uploadProgressLabel.textContent = message || "raw 파일 업로드 중입니다.";
      uploadProgressValue.textContent = pct + "%";
      uploadProgressBar.style.width = pct + "%";
      updateProgressVisibility();
    }
    function setProgress(percent, message, visible, error) {
      var pct = Math.max(0, Math.min(100, Math.round(Number(percent) || 0)));
      reportProgressVisible = Boolean(visible);
      reportProgressError = Boolean(error);
      progressLabel.textContent = message || "보고서 생성 중입니다.";
      progressValue.textContent = pct + "%";
      progressBar.style.width = pct + "%";
      updateProgressVisibility();
    }
    function stopProgressTimer() {
      if (progressTimer) {
        clearInterval(progressTimer);
        progressTimer = null;
      }
    }
    function startProgress(message) {
      stopProgressTimer();
      setUploadProgress(0, "raw 파일 업로드 대기", false, false);
      var pct = 6;
      setProgress(pct, message || progressMessage(pct), true, false);
      progressTimer = setInterval(function() {
        pct = Math.min(24, pct + 2);
        setProgress(pct, "raw 파일을 서버로 전송하고 작업을 접수하는 중입니다.", true, false);
        if (pct >= 24) stopProgressTimer();
      }, 650);
    }
    function finishProgress(message) {
      stopProgressTimer();
      setProgress(100, message || "보고서가 완성되었습니다.", true, false);
      setTimeout(function() {
        setUploadProgress(0, "raw 파일 업로드 대기", false, false);
        setProgress(0, "보고서 생성 대기", false, false);
      }, 900);
    }
    function failProgress(message) {
      stopProgressTimer();
      setProgress(100, message || "보고서 생성에 실패했습니다.", true, true);
      setTimeout(function() {
        setUploadProgress(0, "raw 파일 업로드 대기", false, false);
        setProgress(0, "보고서 생성 대기", false, false);
      }, 1800);
    }
    function filesOf(input) {
      return Array.prototype.slice.call(input.files || []);
    }
    function bundleItem(file, path) {
      return {file: file, path: path || file.webkitRelativePath || file.name};
    }
    function classifyFile(file) {
      var name = file.name.toLowerCase();
      if (/\\.(tif|tiff|png|jpe?g|bmp|webp)$/.test(name)) return "image";
      if (/\\.docx$/.test(name)) return "docx";
      if (/\\.(xlsx|xls|xlsm|xlsb|csv|tsv)$/.test(name)) return "table";
      if (/\\.zip$/.test(name)) return "zip";
      return "skip";
    }
    function classifySection(path) {
      var lowered = String(path || "").toLowerCase().split("/");
      if (lowered.indexOf("tem") >= 0) return "TEM";
      if (lowered.indexOf("stem") >= 0) return "STEM";
      if (lowered.indexOf("report") >= 0 || lowered.indexOf("reports") >= 0) return "EDS";
      if (lowered.indexOf("scale") >= 0) return "코팅층";
      return "기타";
    }
    function addBundleItems(items) {
      var seen = new Set(bundleItems.map(function(item) {
        return item.path + "|" + item.file.size + "|" + item.file.lastModified;
      }));
      items.forEach(function(item) {
        var key = item.path + "|" + item.file.size + "|" + item.file.lastModified;
        if (!seen.has(key)) {
          seen.add(key);
          bundleItems.push(item);
        }
      });
      renderFileList();
    }
    function renderFileList() {
      var counts = {TEM: 0, STEM: 0, EDS: 0, "코팅층": 0, "기타": 0};
      fileList.replaceChildren();
      bundleItems.forEach(function(item) {
        var section = classifySection(item.path);
        counts[section] = (counts[section] || 0) + 1;
        var chip = document.createElement("span");
        chip.className = "ahn-chip";
        chip.textContent = section + " · " + item.path;
        fileList.appendChild(chip);
      });
      bundleMeta.textContent = bundleItems.length
        ? "TEM " + counts.TEM + " · STEM " + counts.STEM + " · EDS " + counts.EDS + " · 코팅층 " + counts["코팅층"] + " · 기타 " + counts["기타"]
        : "선택된 파일 없음";
      syncActionState();
    }
    function fileInputItems(input) {
      return filesOf(input).map(function(file) {
        return bundleItem(file, file.webkitRelativePath || file.name);
      });
    }
    function readAllDirectoryEntries(reader) {
      return new Promise(function(resolve, reject) {
        var entries = [];
        function readBatch() {
          reader.readEntries(function(batch) {
            if (!batch.length) {
              resolve(entries);
              return;
            }
            entries = entries.concat(Array.prototype.slice.call(batch));
            readBatch();
          }, reject);
        }
        readBatch();
      });
    }
    function entryToBundleItems(entry, prefix) {
      prefix = prefix || "";
      if (entry.isFile) {
        return new Promise(function(resolve, reject) {
          entry.file(function(file) {
            resolve([bundleItem(file, prefix + file.name)]);
          }, reject);
        });
      }
      if (entry.isDirectory) {
        return readAllDirectoryEntries(entry.createReader()).then(function(entries) {
          return Promise.all(entries.map(function(child) {
            return entryToBundleItems(child, prefix + entry.name + "/");
          })).then(function(groups) {
            return groups.reduce(function(acc, group) { return acc.concat(group); }, []);
          });
        });
      }
      return Promise.resolve([]);
    }
    async function droppedBundleItems(dataTransfer) {
      var items = Array.prototype.slice.call(dataTransfer.items || []);
      var entries = items
        .filter(function(item) { return item.kind === "file" && item.webkitGetAsEntry; })
        .map(function(item) { return item.webkitGetAsEntry(); })
        .filter(Boolean);
      if (entries.length) {
        var groups = await Promise.all(entries.map(function(entry) {
          return entryToBundleItems(entry, "");
        }));
        return groups.reduce(function(acc, group) { return acc.concat(group); }, []);
      }
      return Array.prototype.slice.call(dataTransfer.files || []).map(function(file) {
        return bundleItem(file, file.webkitRelativePath || file.name);
      });
    }
    function formatBytes(bytes) {
      var value = Number(bytes) || 0;
      var units = ["B", "KB", "MB", "GB"];
      var index = 0;
      while (value >= 1024 && index < units.length - 1) {
        value /= 1024;
        index += 1;
      }
      return (index === 0 ? String(Math.round(value)) : value.toFixed(value >= 10 ? 1 : 2)) + units[index];
    }
    function setDownload(link, url) {
      link.href = url || "#";
      link.setAttribute("aria-disabled", url ? "false" : "true");
    }
    function renderSummary(payload) {
      var data = payload.summary || {};
      var items = [
        ["TEM 이미지", data.temImageCount || 0],
        ["STEM 이미지", (data.stemImageCount || 0) + (data.stemBfImageCount || 0)],
        ["EDS 보고서", data.edsReportCount || 0],
        ["코팅층 이미지", data.coatingImageCount || 0]
      ];
      summary.replaceChildren();
      items.forEach(function(item) {
        var box = document.createElement("div");
        box.className = "ahn-summary-item";
        var label = document.createElement("span");
        label.textContent = item[0];
        var value = document.createElement("strong");
        value.textContent = item[1];
        box.appendChild(label);
        box.appendChild(value);
        summary.appendChild(box);
      });
      setDownload(downloadPptx, payload.downloads && payload.downloads.pptx);
      setDownload(downloadPackage, payload.downloads && payload.downloads.package);
      setDownload(downloadJson, payload.downloads && payload.downloads.analysisJson);
      result.hidden = false;
      empty.hidden = true;
    }
    function parseErrorMessage(text, fallback) {
      if (!text) return fallback;
      try {
        var payload = JSON.parse(text);
        return payload.message || payload.detail || text;
      } catch (_error) {
        return text;
      }
    }
    function networkErrorMessage(_error, url) {
      var target = String(url || "");
      if (target.indexOf("/analyze") >= 0) {
        return "서버 연결에 실패했습니다. 업로드 중 네트워크가 끊겼거나 Edge API 서비스가 재시작되었을 수 있습니다. 잠시 후 다시 시도하세요.";
      }
      if (target.indexOf("/upload-sessions") >= 0) {
        return "raw 파일 업로드 연결이 끊겼습니다. 같은 조각을 다시 전송합니다.";
      }
      return "서버 응답을 받지 못했습니다. 네트워크 또는 Edge API 서비스 상태를 확인하세요.";
    }
    async function requestJsonPost(url) {
      var response;
      try {
        response = await fetch(url, {method: "POST"});
      } catch (error) {
        var wrapped = new Error(networkErrorMessage(error, url));
        wrapped.cause = error;
        wrapped.isNetworkError = true;
        throw wrapped;
      }
      var text = await response.text();
      if (!response.ok) {
        var error = new Error(parseErrorMessage(text, "요청 처리에 실패했습니다."));
        error.isTransientError = response.status === 408 || response.status === 429 || response.status >= 500;
        throw error;
      }
      return JSON.parse(text);
    }
    async function requestJsonPostWithRetry(url, attempts, retryMessage) {
      var lastError = null;
      for (var attempt = 1; attempt <= attempts; attempt += 1) {
        try {
          return await requestJsonPost(url);
        } catch (error) {
          lastError = error;
          if (!(error.isNetworkError || error.isTransientError) || attempt >= attempts) break;
          setUploadProgress(
            100,
            retryMessage || "서버 응답이 끊겨 요청을 다시 확인하는 중입니다.",
            true,
            false
          );
          await sleep(700 * attempt);
        }
      }
      throw lastError || new Error("요청 처리에 실패했습니다.");
    }
    function uploadableBundleItems() {
      var supported = [];
      var skipped = [];
      bundleItems.forEach(function(item) {
        if (classifyFile(item.file) === "skip") {
          skipped.push(item.path || item.file.name);
        } else {
          supported.push(item);
        }
      });
      return {supported: supported, skipped: skipped};
    }
    function requestUploadChunk(options) {
      return new Promise(function(resolve, reject) {
        var xhr = new XMLHttpRequest();
        xhr.open(
          "POST",
          "/api/v1/tem/upload-sessions/" + encodeURIComponent(options.uploadId) + "/chunks",
          true
        );
        xhr.timeout = 120000;
        xhr.upload.onprogress = function(event) {
          var loaded = event.lengthComputable ? event.loaded : 0;
          var pct = options.totalUploadBytes > 0
            ? ((options.uploadedBefore + loaded) / options.totalUploadBytes) * 100
            : 0;
          setUploadProgress(
            pct,
            "raw 파일 업로드 중 (" + options.fileIndex + "/" + options.fileCount + "): " + options.path,
            true,
            false
          );
        };
        xhr.onload = function() {
          var text = xhr.responseText || "";
          if (xhr.status >= 200 && xhr.status < 300) {
            try {
              resolve(JSON.parse(text));
            } catch (_error) {
              reject(new Error("서버 응답을 해석하지 못했습니다."));
          }
          return;
        }
          var error = new Error(parseErrorMessage(text, "업로드 조각 전송에 실패했습니다."));
          error.isTransientError = xhr.status === 408 || xhr.status === 429 || xhr.status >= 500;
          reject(error);
        };
        xhr.onerror = function(error) {
          var wrapped = new Error(networkErrorMessage(error, "/api/v1/tem/upload-sessions"));
          wrapped.cause = error;
          wrapped.isNetworkError = true;
          reject(wrapped);
        };
        xhr.ontimeout = function(error) {
          var wrapped = new Error("업로드 조각 전송 시간이 초과되었습니다. 같은 조각을 다시 전송합니다.");
          wrapped.cause = error;
          wrapped.isNetworkError = true;
          reject(wrapped);
        };
        xhr.onabort = function(error) {
          var wrapped = new Error("업로드가 중단되었습니다. 네트워크 상태를 확인한 뒤 다시 시도하세요.");
          wrapped.cause = error;
          wrapped.isNetworkError = true;
          reject(wrapped);
        };
        var formData = new FormData();
        formData.append("relative_path", options.path);
        formData.append("offset", String(options.offset));
        formData.append("total_size", String(options.totalSize));
        formData.append("chunk_index", String(options.chunkIndex));
        formData.append("chunk_count", String(options.chunkCount));
        formData.append("chunk_crc32", options.chunkCrc32 || "");
        formData.append("file", options.blob, options.fileName);
        xhr.send(formData);
      });
    }
    var crc32Table = null;
    function getCrc32Table() {
      if (crc32Table) return crc32Table;
      crc32Table = new Uint32Array(256);
      for (var index = 0; index < 256; index += 1) {
        var value = index;
        for (var bit = 0; bit < 8; bit += 1) {
          value = (value & 1) ? (0xEDB88320 ^ (value >>> 1)) : (value >>> 1);
        }
        crc32Table[index] = value >>> 0;
      }
      return crc32Table;
    }
    async function chunkCrc32(blob) {
      var bytes = new Uint8Array(await blob.arrayBuffer());
      var table = getCrc32Table();
      var crc = 0xFFFFFFFF;
      for (var index = 0; index < bytes.length; index += 1) {
        crc = table[(crc ^ bytes[index]) & 0xFF] ^ (crc >>> 8);
      }
      return ((crc ^ 0xFFFFFFFF) >>> 0).toString(16).padStart(8, "0");
    }
    async function uploadChunkWithRetry(options) {
      var lastError = null;
      for (var attempt = 1; attempt <= TEM_UPLOAD_CHUNK_RETRIES; attempt += 1) {
        try {
          return await requestUploadChunk(options);
        } catch (error) {
          lastError = error;
          if (!(error.isNetworkError || error.isTransientError) || attempt >= TEM_UPLOAD_CHUNK_RETRIES) break;
          var pct = options.totalUploadBytes > 0
            ? (options.uploadedBefore / options.totalUploadBytes) * 100
            : 0;
          setUploadProgress(
            pct,
            "업로드가 잠시 끊겨 같은 조각을 다시 전송합니다. (" + attempt + "/" + TEM_UPLOAD_CHUNK_RETRIES + ")",
            true,
            false
          );
          await sleep(650 * attempt);
        }
      }
      throw lastError || new Error("업로드 조각 전송에 실패했습니다.");
    }
    async function uploadBundleWithSession() {
      var selection = uploadableBundleItems();
      if (!selection.supported.length) {
        throw new Error("업로드할 수 있는 TEM raw 파일이 없습니다.");
      }
      if (selection.skipped.length) {
        setStatus("지원하지 않는 파일 " + selection.skipped.length + "개는 업로드에서 제외했습니다.", false);
      }
      var totalBytes = selection.supported.reduce(function(sum, item) {
        return sum + Number(item.file.size || 0);
      }, 0);
      if (totalBytes <= 0) {
        throw new Error("빈 파일만 선택되어 있습니다.");
      }
      if (totalBytes > TEM_MAX_TOTAL_BYTES) {
        throw new Error("TEM raw bundle의 총 크기는 1.2GB 이하여야 합니다. 현재: " + formatBytes(totalBytes));
      }
      selection.supported.forEach(function(item) {
        if (item.file.size > TEM_MAX_FILE_BYTES) {
          throw new Error(item.path + " 파일이 너무 큽니다. 파일당 최대 250MB입니다.");
        }
      });

      setUploadProgress(0, "업로드 세션을 생성하는 중입니다.", true, false);
      var session = await requestJsonPostWithRetry(
        "/api/v1/tem/upload-sessions",
        3,
        "업로드 세션 생성을 다시 시도하는 중입니다."
      );
      var uploadedBytes = 0;
      for (var fileIndex = 0; fileIndex < selection.supported.length; fileIndex += 1) {
        var item = selection.supported[fileIndex];
        var file = item.file;
        var path = item.path || file.name;
        if (!file.size) continue;
        var chunkCount = Math.max(1, Math.ceil(file.size / TEM_UPLOAD_CHUNK_BYTES));
        for (var chunkIndex = 0; chunkIndex < chunkCount; chunkIndex += 1) {
          var offset = chunkIndex * TEM_UPLOAD_CHUNK_BYTES;
          var end = Math.min(file.size, offset + TEM_UPLOAD_CHUNK_BYTES);
          var blob = file.slice(offset, end);
          var checksum = await chunkCrc32(blob);
          await uploadChunkWithRetry({
            uploadId: session.uploadId,
            path: path,
            fileName: file.name,
            blob: blob,
            offset: offset,
            totalSize: file.size,
            chunkIndex: chunkIndex,
            chunkCount: chunkCount,
            chunkCrc32: checksum,
            uploadedBefore: uploadedBytes,
            totalUploadBytes: totalBytes,
            fileIndex: fileIndex + 1,
            fileCount: selection.supported.length
          });
          uploadedBytes += blob.size;
        }
      }
      setUploadProgress(100, "raw 파일 업로드 완료. 무결성 및 암호화 여부를 검사하는 중입니다.", true, false);
      return requestJsonPostWithRetry(
        "/api/v1/tem/upload-sessions/" + encodeURIComponent(session.uploadId) + "/complete",
        4,
        "보고서 작업 접수 응답을 다시 확인하는 중입니다."
      );
    }
    async function requestReport(url) {
      var options = {method: "GET"};
      var response;
      try {
        response = await fetch(url, options);
      } catch (error) {
        var wrapped = new Error(networkErrorMessage(error, url));
        wrapped.cause = error;
        wrapped.isNetworkError = true;
        throw wrapped;
      }
      var text = await response.text();
      if (!response.ok) {
        var error = new Error(parseErrorMessage(text, "보고서 생성 요청에 실패했습니다."));
        error.isTransientError = response.status === 408 || response.status === 429 || response.status >= 500;
        throw error;
      }
      return JSON.parse(text);
    }
    function sleep(ms) {
      return new Promise(function(resolve) { setTimeout(resolve, ms); });
    }
    async function waitForReportJob(payload) {
      if (!payload || !payload.jobId) return payload;
      stopProgressTimer();
      var current = payload;
      var shownPct = 0;
      var transientFailures = 0;
      while (current && current.status !== "completed") {
        if (current.status === "failed") {
          var error = current.error || {};
          throw new Error(error.message || current.message || "TEM 보고서 생성에 실패했습니다.");
        }
        shownPct = Math.max(shownPct, Number(current.progressPct || 8));
        shownPct = Math.min(96, shownPct);
        setProgress(
          shownPct,
          current.message || progressMessage(shownPct),
          true,
          false
        );
        await sleep(1500);
        try {
          current = await requestReport("/api/v1/tem/report/jobs/" + encodeURIComponent(payload.jobId), null);
          transientFailures = 0;
        } catch (error) {
          if (!(error.isNetworkError || error.isTransientError) || transientFailures >= 6) throw error;
          transientFailures += 1;
          setProgress(
            shownPct,
            "서버 응답을 다시 확인하는 중입니다. 네트워크가 잠시 불안정할 수 있습니다.",
            true,
            false
          );
          await sleep(1200);
        }
      }
      return current;
    }
    function openBundlePicker(input, message) {
      setCollectingFiles(true, message);
      var released = false;
      function releaseIfCancelled() {
        if (released) return;
        setTimeout(function() {
          if (!released && !(input.files || []).length) {
            released = true;
            setCollectingFiles(false);
          }
        }, 450);
      }
      window.addEventListener("focus", releaseIfCancelled, {once: true});
      input.oncancel = function() {
        released = true;
        setCollectingFiles(false);
      };
      input.click();
    }
    bundleInput.addEventListener("change", function() {
      try {
        addBundleItems(fileInputItems(bundleInput));
      } finally {
        bundleInput.oncancel = null;
        bundleInput.value = "";
        setCollectingFiles(false);
      }
    });
    folderInput.addEventListener("change", function() {
      try {
        addBundleItems(fileInputItems(folderInput));
      } finally {
        folderInput.oncancel = null;
        folderInput.value = "";
        setCollectingFiles(false);
      }
    });
    addFilesButton.addEventListener("click", function() {
      openBundlePicker(bundleInput, "첨부할 파일 목록을 읽는 중입니다.");
    });
    addFolderButton.addEventListener("click", function() {
      openBundlePicker(folderInput, "폴더 안의 raw 파일 목록을 읽는 중입니다.");
    });
    drop.addEventListener("dragover", function(event) {
      event.preventDefault();
      drop.classList.add("dragover");
    });
    drop.addEventListener("dragleave", function() {
      drop.classList.remove("dragover");
    });
    drop.addEventListener("drop", async function(event) {
      event.preventDefault();
      drop.classList.remove("dragover");
      setCollectingFiles(true, "드롭한 폴더의 raw 파일 목록을 읽는 중입니다.");
      try {
        addBundleItems(await droppedBundleItems(event.dataTransfer));
        setStatus("TEM raw bundle 파일이 추가되었습니다.", false);
      } catch (error) {
        setStatus(error.message || String(error), true);
      } finally {
        setCollectingFiles(false);
      }
    });
    clearButton.addEventListener("click", function() {
      form.reset();
      bundleItems = [];
      renderFileList();
      result.hidden = true;
      empty.hidden = false;
      setDownload(downloadPptx, null);
      setDownload(downloadPackage, null);
      setDownload(downloadJson, null);
      stopProgressTimer();
      setUploadProgress(0, "raw 파일 업로드 대기", false, false);
      setProgress(0, "보고서 생성 대기", false, false);
      setStatus("TEM raw 폴더를 선택하면 보고서를 생성할 수 있습니다.", false);
    });
    exampleButton.addEventListener("click", async function() {
      setBusy(true);
      startProgress("TEM 예제 보고서를 생성하는 중입니다.");
      try {
        var payload = await waitForReportJob(await requestReport("/api/v1/tem/example", null));
        renderSummary(payload);
        setStatus("TEM 예제 보고서가 생성되었습니다.", false);
        finishProgress("TEM 예제 보고서가 생성되었습니다.");
      } catch (error) {
        setStatus(error.message || String(error), true);
        failProgress(error.message || "예제 보고서 생성에 실패했습니다.");
      } finally {
        setBusy(false);
      }
    });
    form.addEventListener("submit", async function(event) {
      event.preventDefault();
      if (collectingFiles) {
        setStatus("첨부 파일 목록을 읽는 중입니다. 목록 표시가 완료된 뒤 다시 실행하세요.", true);
        return;
      }
      if (!bundleItems.length) {
        setStatus("TEM raw 폴더 또는 ZIP 파일을 먼저 추가하세요.", true);
        return;
      }
      setBusy(true);
      stopProgressTimer();
      setUploadProgress(0, "raw 파일 업로드를 준비하는 중입니다.", true, false);
      setProgress(0, "업로드 완료 후 보고서 작업을 시작합니다.", true, false);
      try {
        var payload = await waitForReportJob(await uploadBundleWithSession());
        renderSummary(payload);
        setStatus("TEM 보고서가 생성되었습니다.", false);
        finishProgress("TEM 보고서가 생성되었습니다.");
      } catch (error) {
        setStatus(error.message || String(error), true);
        failProgress(error.message || "보고서 생성에 실패했습니다.");
      } finally {
        setBusy(false);
      }
    });
    renderFileList();
    setStatus("TEM raw 폴더를 선택하면 보고서를 생성할 수 있습니다.", false);
  })();
  </script>
</body>
</html>"""


@router.get("/tem", response_class=HTMLResponse, include_in_schema=False)
def tem_page() -> HTMLResponse:
    return HTMLResponse(build_ahn_page())


@router.post("/api/v1/tem/upload-sessions", response_class=JSONResponse, tags=["tem"])
def create_tem_upload_session() -> JSONResponse:
    _cleanup_old_jobs()
    session = _create_upload_session()
    logger.info("TEM 청크 업로드 세션 생성 (upload_id=%s)", session.upload_id)
    return JSONResponse(_upload_session_payload(session))


@router.post("/api/v1/tem/upload-sessions/{upload_id}/chunks", response_class=JSONResponse, tags=["tem"])
async def upload_tem_chunk(
    request: Request,
    upload_id: str,
    relative_path: str = Form(...),
    offset: int = Form(...),
    total_size: int = Form(...),
    chunk_index: int = Form(...),
    chunk_count: int = Form(...),
    chunk_crc32: str | None = Form(default=None),
    file: UploadFile = File(...),
) -> JSONResponse:
    session = _get_upload_session(upload_id)
    request.state.error_project = "TEM"
    request.state.error_source_paths = [session.input_root]
    file_state = await _write_upload_chunk(
        session,
        relative_path=relative_path,
        offset=offset,
        total_size=total_size,
        chunk_index=chunk_index,
        chunk_count=chunk_count,
        chunk_crc32=chunk_crc32,
        upload=file,
    )
    payload = _upload_session_payload(session)
    payload.update(
        {
            "relativePath": file_state.relative_path,
            "uploadedFileBytes": file_state.uploaded_bytes,
            "fileSize": file_state.total_size,
            "fileCompleted": file_state.completed,
        }
    )
    return JSONResponse(payload)


@router.post("/api/v1/tem/upload-sessions/{upload_id}/complete", response_class=JSONResponse, tags=["tem"])
def complete_tem_upload_session(request: Request, upload_id: str) -> JSONResponse:
    _cleanup_old_jobs()
    existing_job = _job_for_completed_upload(upload_id)
    if existing_job is not None:
        set_usage_context(
            request,
            project="TEM",
            job_id=existing_job.job_id,
            experiment_code="TEM",
        )
        return JSONResponse(_job_payload(existing_job))
    session = _get_upload_session(upload_id)
    request.state.error_project = "TEM"
    request.state.error_source_paths = [session.input_root]
    completed_files = [state for state in session.files.values() if state.completed]
    incomplete_files = [state.relative_path for state in session.files.values() if not state.completed]
    if not completed_files:
        raise ApiException(400, "TEM_FILES_REQUIRED", "분석 가능한 TEM 파일이 없습니다.")
    if incomplete_files:
        preview = ", ".join(incomplete_files[:5])
        more = f" 외 {len(incomplete_files) - 5}개" if len(incomplete_files) > 5 else ""
        raise ApiException(
            409,
            "TEM_UPLOAD_INCOMPLETE",
            f"아직 업로드가 완료되지 않은 파일이 있습니다: {preview}{more}",
        )
    storage_mismatches = []
    for state in completed_files:
        stored_path = session.input_root / state.stored_path
        actual_size = stored_path.stat().st_size if stored_path.is_file() else -1
        if actual_size != state.total_size:
            storage_mismatches.append(
                {
                    "path": state.relative_path,
                    "expectedBytes": state.total_size,
                    "actualBytes": actual_size,
                }
            )
    if storage_mismatches:
        preview = ", ".join(item["path"] for item in storage_mismatches[:5])
        more = f" 외 {len(storage_mismatches) - 5}개" if len(storage_mismatches) > 5 else ""
        raise ApiException(
            409,
            "TEM_UPLOAD_STORAGE_MISMATCH",
            f"서버에 완전히 저장되지 않은 파일이 있습니다: {preview}{more}. 해당 파일을 다시 업로드하세요.",
            retryable=True,
            details={"files": storage_mismatches},
        )
    _validate_ahn_upload_files(session.input_root)
    job = _submit_ahn_job(
        session.input_root,
        session.work_dir,
        app_error_archive(request.app),
        app_usage_archive(request.app),
    )
    set_usage_context(
        request,
        project="TEM",
        job_id=job.job_id,
        experiment_code="TEM",
    )
    with _ahn_upload_sessions_lock:
        _ahn_upload_sessions.pop(upload_id, None)
        _ahn_completed_upload_jobs[upload_id] = job.job_id
    logger.info(
        "TEM 청크 업로드 완료 및 보고서 작업 시작 (upload_id=%s, job_id=%s, files=%s)",
        upload_id,
        job.job_id,
        len(completed_files),
    )
    return JSONResponse(_job_payload(job))


@router.post("/api/v1/tem/analyze", response_class=JSONResponse, tags=["tem"])
async def analyze_tem(
    request: Request,
    files: list[UploadFile] | None = File(default=None, alias="files"),
) -> JSONResponse:
    set_usage_context(request, project="TEM", experiment_code="TEM")
    _cleanup_old_jobs()
    work_dir = Path(tempfile.mkdtemp(prefix="rist-ahn-web-"))
    upload_root = work_dir / "input"
    request.state.error_project = "TEM"
    request.state.error_source_paths = [upload_root]
    try:
        await _save_ahn_uploads(files, upload_root)
        _validate_ahn_upload_files(upload_root)
        input_root = _find_ahn_input_root(upload_root)
        job = _submit_ahn_job(
            input_root,
            work_dir,
            app_error_archive(request.app),
            app_usage_archive(request.app),
        )
    except Exception:
        request.state.error_cleanup_paths = [work_dir]
        raise
    logger.info("TEM 웹 보고서 작업 시작 (job_id=%s)", job.job_id)
    set_usage_context(request, job_id=job.job_id)
    return JSONResponse(_job_payload(job))


@router.get("/api/v1/tem/example", response_class=JSONResponse, tags=["tem"])
def tem_example(request: Request) -> JSONResponse:
    set_usage_context(request, project="TEM", experiment_code="TEM")
    _cleanup_old_jobs()
    repo_root = Path(__file__).resolve().parents[2]
    input_root = repo_root / "ahn" / "data" / "TESTData"
    work_dir = Path(tempfile.mkdtemp(prefix="rist-ahn-example-"))
    try:
        if not input_root.exists():
            input_root = _write_synthetic_tem_example(work_dir / "input")
        job = _submit_ahn_job(
            input_root,
            work_dir,
            app_error_archive(request.app),
            app_usage_archive(request.app),
        )
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    logger.info("TEM 예제 웹 보고서 작업 시작 (job_id=%s)", job.job_id)
    set_usage_context(request, job_id=job.job_id)
    return JSONResponse(_job_payload(job))


@router.get("/api/v1/tem/report/jobs/{job_id}", response_class=JSONResponse, tags=["tem"])
def get_tem_report_job(job_id: str) -> JSONResponse:
    _cleanup_old_jobs()
    job = _ahn_report_jobs.get(job_id)
    if job is None:
        raise ApiException(
            404,
            "TEM_REPORT_NOT_FOUND",
            "TEM 보고서 작업 정보를 찾을 수 없습니다. 보고서를 다시 생성하세요.",
        )
    return JSONResponse(_job_payload(job))


@router.get("/api/v1/tem/report/jobs/{job_id}/download/{kind}", tags=["tem"])
def download_tem_report(job_id: str, kind: str) -> FileResponse:
    _cleanup_old_jobs()
    job = _ahn_report_jobs.get(job_id)
    if job is None:
        raise ApiException(
            404,
            "TEM_REPORT_NOT_FOUND",
            "TEM 보고서 다운로드 정보를 찾을 수 없습니다. 보고서를 다시 생성하세요.",
        )
    if job.status != "completed":
        raise ApiException(
            409,
            "TEM_REPORT_NOT_READY",
            "TEM 보고서가 아직 생성 중입니다. 완료 후 다시 다운로드하세요.",
            details={"status": job.status, "progressPct": job.progress_pct},
        )
    if kind == "pptx":
        return FileResponse(
            job.pptx_path,
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            filename="tem-report.pptx",
        )
    if kind == "package":
        return FileResponse(
            job.package_path,
            media_type="application/zip",
            filename="tem-report-package.zip",
        )
    if kind == "analysis-json":
        return FileResponse(
            job.analysis_path,
            media_type="application/json",
            filename="analysis-result.json",
        )
    raise ApiException(404, "TEM_REPORT_FILE_NOT_FOUND", "지원하지 않는 TEM 보고서 파일입니다.")


def create_tem_preview_app() -> FastAPI:
    app = FastAPI(title="RIST TEM Preview")
    settings = Settings.from_env()
    app.state.settings = settings
    install_error_management(app, settings)
    app.include_router(router)
    return app


def create_ahn_preview_app() -> FastAPI:
    """Backward-compatible factory name for local development scripts."""
    return create_tem_preview_app()

"""AHN TEM/STEM/EDS/coating report upload workspace."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from io import BytesIO
from html import escape
import os
import re
import shutil
import tempfile
from threading import BoundedSemaphore, Lock
import time
from pathlib import Path
from typing import Any
from uuid import uuid4
import zipfile
import zlib

from fastapi import APIRouter, Body, FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from PIL import Image, ImageDraw, UnidentifiedImageError
from rist_common import get_logger

from .errors import ApiException
from .tem_resume import UploadManifest, check_upload_owner, resume_payload, validate_manifest
from .auth import authenticated_transfer_payload
from .browser_support import with_browser_support_notice
from .config import Settings
from .database import Database
from .file_inspection import FileInspection, inspect_file_path
from .error_archive import (
    ErrorArchive,
    error_archive as app_error_archive,
    install_error_management,
    record_background_error,
)
from .path_bootstrap import add_project_package_paths
from .preview_report import PreviewReportSendRequest, send_preview_report_package
from .report_queue import ReportQueueError, register_generated_report_package
from .usage_archive import (
    UsageArchive,
    record_background_usage,
    request_usage_client_context,
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


MAX_AHN_UPLOAD_FILES = _positive_int_env("RIST_TEM_MAX_FILES", 5000)
MAX_AHN_IMAGE_PIXELS = _positive_int_env("RIST_TEM_MAX_IMAGE_PIXELS", 40_000_000)
MAX_AHN_OFFICE_EXPANDED_BYTES = _positive_int_env("RIST_TEM_MAX_OFFICE_EXPANDED_BYTES", 256 * 1024 * 1024)
AHN_DISK_RESERVE_BYTES = _positive_int_env("RIST_TEM_DISK_RESERVE_BYTES", 256 * 1024 * 1024)
MAX_AHN_CHUNK_BYTES = 8 * 1024 * 1024
AHN_REPORT_WORKERS = _positive_int_env("RIST_TEM_REPORT_WORKERS", 1)
AHN_REPORT_QUEUE_SIZE = _positive_int_env("RIST_TEM_REPORT_QUEUE_SIZE", 4)


def _require_disk_space(path: Path, required_bytes: int) -> None:
    if shutil.disk_usage(path).free < required_bytes + AHN_DISK_RESERVE_BYTES:
        raise ApiException(507, "TEM_INSUFFICIENT_DISK_SPACE",
                           "Edge 서버의 임시 저장 공간이 부족합니다. 관리자에게 저장 공간 확보를 요청한 뒤 다시 시도하세요.",
                           retryable=True)


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
    settings: Settings | None
    database: Database | None
    error_archive: ErrorArchive | None
    usage_archive: UsageArchive | None
    usage_client_context: dict[str, str | None]
    created_at: float
    updated_at: float
    status: str = "queued"
    progress_pct: int = 5
    message: str = "TEM 보고서 작업이 대기 중입니다."
    error: dict[str, Any] | None = None
    error_event_id: str | None = None
    validate_upload: bool = False
    owner_user_id: str | None = None
    parent_job_id: str | None = None
    transfer_metadata: dict[str, str] | None = None
    review_error: str | None = None
    active_revisions: int = 0


@dataclass
class AhnUploadFileState:
    relative_path: str
    stored_path: str
    temp_path: str
    total_size: int
    uploaded_bytes: int = 0
    completed: bool = False
    chunks: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AhnUploadSession:
    upload_id: str
    work_dir: Path
    input_root: Path
    created_at: float
    updated_at: float
    files: dict[str, AhnUploadFileState] = field(default_factory=dict)
    lock: Any = field(default_factory=Lock, repr=False)
    finalized: bool = False
    owner_user_id: str | None = None
    expected_files: dict[str, dict[str, Any]] | None = None


_ahn_report_jobs: dict[str, AhnReportJob] = {}
_ahn_report_jobs_lock = Lock()
_ahn_upload_sessions: dict[str, AhnUploadSession] = {}
_ahn_completed_upload_jobs: dict[str, str] = {}
_ahn_upload_sessions_lock = Lock()
_ahn_report_executor = ThreadPoolExecutor(
    max_workers=AHN_REPORT_WORKERS,
    thread_name_prefix="rist-ahn-report",
)
# Also gate synchronous legacy API validation, not just the report executor.
_ahn_processing_slots = BoundedSemaphore(AHN_REPORT_WORKERS)
_ahn_pending_slots = BoundedSemaphore(AHN_REPORT_WORKERS + AHN_REPORT_QUEUE_SIZE)


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


def _expected_ahn_kinds(suffix: str) -> set[str]:
    if suffix in IMAGE_EXTENSIONS:
        return {"image"}
    if suffix == ".docx":
        return {"docx"}
    if suffix in {".xlsx", ".xlsm"}:
        return {"xlsx", "xlsm"}
    if suffix == ".xlsb":
        return {"xlsb"}
    if suffix == ".xls":
        return {"xls", "ole"}
    if suffix == ".csv":
        return {"csv", "text"}
    if suffix == ".tsv":
        return {"tsv", "text"}
    if suffix == ".zip":
        return {"zip"}
    return set()


def _ahn_canonical_suffix(inspection: FileInspection) -> str | None:
    if inspection.kind == "image":
        return inspection.canonical_suffix
    if inspection.kind in {"docx", "xlsx", "xlsm", "xlsb", "xls", "csv", "tsv", "zip"}:
        return inspection.canonical_suffix
    return None


def _inspection_protection_issue(display_path: str, inspection: FileInspection) -> dict[str, Any] | None:
    if not inspection.protected:
        return None
    if inspection.kind == "pdf":
        reason = "암호 또는 DRM으로 보호된 PDF 파일입니다. 보호를 해제한 뒤 다시 저장하세요."
    elif inspection.kind == "zip":
        reason = "암호화된 ZIP 파일입니다. 암호를 제거한 뒤 다시 압축하세요."
    else:
        reason = "암호 또는 DRM으로 보호된 Office 파일입니다. 보호를 해제한 뒤 새 파일로 저장하세요."
    return _integrity_issue(display_path, reason, protected=True)


def _normalize_ahn_content_path(path: Path, upload_root: Path) -> tuple[Path, FileInspection, dict[str, Any] | None]:
    display_path = path.relative_to(upload_root).as_posix()
    inspection = inspect_file_path(path)
    protection_issue = _inspection_protection_issue(display_path, inspection)
    if protection_issue:
        return path, inspection, protection_issue
    if inspection.kind == "empty":
        return path, inspection, _integrity_issue(display_path, "빈 파일입니다.")

    suffix = path.suffix.lower()
    expected = _expected_ahn_kinds(suffix)
    if expected and inspection.kind not in expected:
        actual = inspection.kind if inspection.recognized else "알 수 없는 형식"
        expected_label = "Office 파일" if suffix in OOXML_REQUIRED_MEMBERS or suffix == ".xls" else "지원 파일"
        return path, inspection, _integrity_issue(
            display_path,
            f"확장자({suffix})와 실제 {expected_label} 형식({actual})이 일치하지 않습니다.",
        )

    canonical_suffix = _ahn_canonical_suffix(inspection)
    if not expected and canonical_suffix:
        target = _unique_path(path.with_suffix(canonical_suffix))
        path.rename(target)
        return target, inspection, None
    if not expected and not canonical_suffix:
        return path, inspection, None
    return path, inspection, None


def _ole_protection_marker(data: bytes) -> bool:
    markers = ("EncryptedPackage", "EncryptionInfo", "DRMContent", "DataSpaces")
    return any(
        marker.encode("utf-16le") in data or marker.encode("ascii") in data
        for marker in markers
    )


def _validate_image(source: Path | BytesIO | Any, display_path: str) -> list[dict[str, Any]]:
    try:
        with Image.open(source) as image:
            if image.width * image.height > MAX_AHN_IMAGE_PIXELS:
                return [_integrity_issue(display_path, f"이미지 해상도가 서버 처리 한도({MAX_AHN_IMAGE_PIXELS:,} 픽셀)를 초과합니다. 분할하거나 크기를 줄여 업로드하세요.")]
            image.verify()
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        return [_integrity_issue(display_path, f"이미지 파일을 열 수 없습니다: {exc}")]
    return []


def _archive_limits(archive: zipfile.ZipFile, *, office: bool = False) -> tuple[int, int]:
    """Check metadata BEFORE inflating even one member (including CRC checks)."""
    members = archive.infolist()
    if len(members) > MAX_AHN_UPLOAD_FILES:
        raise ValueError(f"압축 내부 항목 수가 {MAX_AHN_UPLOAD_FILES:,}개를 초과합니다. 묶음을 나누어 업로드하세요.")
    total = 0
    count = 0
    for member in members:
        if member.is_dir():
            continue
        count += 1
        if member.file_size > MAX_AHN_UPLOAD_FILE_BYTES:
            raise ValueError(f"압축 해제 후 파일 크기가 250MB를 초과합니다: {member.filename}")
        total += member.file_size
    limit = MAX_AHN_OFFICE_EXPANDED_BYTES if office else MAX_AHN_UPLOAD_TOTAL_BYTES
    if total > limit:
        raise ValueError(f"압축 해제 후 전체 크기가 처리 한도({limit // (1024 * 1024)}MB)를 초과합니다.")
    return count, total


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
            _archive_limits(archive, office=True)
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
            # Word embeds images that are later decoded by the report builder.
            for member in archive.infolist():
                if member.filename.startswith(("word/media/", "media/")) and Path(member.filename).suffix.lower() in IMAGE_EXTENSIONS:
                    with archive.open(member) as stream:
                        issues = _validate_image(stream, f"{display_path}::{member.filename}")
                    if issues:
                        return issues
    except (zipfile.BadZipFile, RuntimeError, OSError, ValueError) as exc:
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
    try:
        with zipfile.ZipFile(path) as archive:
            _archive_limits(archive)
            encrypted = [item.filename for item in archive.infolist() if item.flag_bits & 0x1]
            if encrypted:
                return [
                    _integrity_issue(
                        display_path,
                        "암호화된 ZIP 파일입니다. 암호를 제거한 뒤 다시 압축하세요.",
                        protected=True,
                    )
                ]
            for member in archive.infolist():
                if member.is_dir() or "__MACOSX" in Path(member.filename).parts:
                    continue
                member_path = f"{display_path}::{member.filename}"
                if Path(member.filename).name.startswith(".") or Path(member.filename).name.startswith("~$"):
                    continue
                suffix = Path(member.filename).suffix.lower()
                # Never archive.read(member): a single allowed TIFF can be 250MB.
                # Streaming to disk verifies CRC on EOF and bounds Python buffers.
                with tempfile.TemporaryDirectory(prefix="rist-tem-verify-", dir=path.parent) as temp:
                    probe = Path(temp) / "member"
                    _require_disk_space(path.parent, member.file_size)
                    with archive.open(member) as source, probe.open("wb") as output:
                        shutil.copyfileobj(source, output, AHN_UPLOAD_CHUNK_BYTES)
                    inspection = inspect_file_path(probe)
                    protection_issue = _inspection_protection_issue(member_path, inspection)
                    if protection_issue:
                        issues.append(protection_issue)
                        continue
                    expected = _expected_ahn_kinds(suffix)
                    if expected and inspection.kind not in expected:
                        actual = inspection.kind if inspection.recognized else "알 수 없는 형식"
                        issues.append(_integrity_issue(member_path, f"확장자({suffix})와 실제 파일 형식({actual})이 일치하지 않습니다."))
                        continue
                    if inspection.kind in {"docx", "xlsx", "xlsm", "xlsb"}:
                        issues.extend(_validate_ooxml(probe, inspection.canonical_suffix or suffix, member_path))
                    elif inspection.kind in {"xls", "ole"}:
                        issues.extend(_validate_legacy_xls(probe, member_path))
                    elif inspection.kind == "image":
                        issues.extend(_validate_image(probe, member_path))
    except (zipfile.BadZipFile, RuntimeError, OSError, ValueError) as exc:
        return [_integrity_issue(display_path, f"손상되었거나 올바르지 않은 ZIP 파일입니다: {exc}")]
    return issues


def _validate_ahn_upload_files(upload_root: Path) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    checked = 0
    normalized: list[dict[str, str]] = []
    paths = []
    for path in upload_root.rglob("*"):
        if path.is_file():
            paths.append(path)
            if len(paths) > MAX_AHN_UPLOAD_FILES:
                raise ApiException(413, "TEM_TOO_MANY_FILES", f"한 묶음은 최대 {MAX_AHN_UPLOAD_FILES:,}개 파일입니다. 묶음을 나누어 업로드하세요.")
    paths.sort(key=lambda item: item.as_posix())
    # Budget across ALL ZIPs plus loose files, not separately per archive.
    expanded_bytes = 0
    expanded_count = 0
    for path in paths:
        expanded_bytes += path.stat().st_size
        expanded_count += 1
        if zipfile.is_zipfile(path):
            try:
                with zipfile.ZipFile(path) as archive:
                    count, size = _archive_limits(archive)
                expanded_bytes += size
                expanded_count += count
            except (zipfile.BadZipFile, ValueError) as exc:
                raise ApiException(400, "TEM_UPLOAD_INTEGRITY_FAILED", f"{path.name}: {exc}") from exc
    if expanded_bytes > MAX_AHN_UPLOAD_TOTAL_BYTES or expanded_count > MAX_AHN_UPLOAD_FILES:
        raise ApiException(413, "TEM_EXPANDED_BUNDLE_TOO_LARGE", "원본과 ZIP 해제 파일을 합한 용량 또는 파일 수가 처리 한도를 초과합니다. 묶음을 나누어 업로드하세요.")
    _require_disk_space(upload_root, expanded_bytes * 2)
    for path in paths:
        if not path.is_file() or path.name.startswith(".") or path.name.startswith("~$"):
            continue
        checked += 1
        original_path = path.relative_to(upload_root).as_posix()
        path, inspection, issue = _normalize_ahn_content_path(path, upload_root)
        if issue:
            issues.append(issue)
            continue
        display_path = path.relative_to(upload_root).as_posix()
        if display_path != original_path:
            normalized.append({"from": original_path, "to": display_path, "type": inspection.kind})
        suffix = path.suffix.lower()
        if inspection.kind == "zip":
            issues.extend(_validate_zip_archive(path, display_path))
        elif inspection.kind in {"docx", "xlsx", "xlsm", "xlsb"}:
            issues.extend(_validate_ooxml(path, suffix, display_path))
        elif inspection.kind in {"xls", "ole"}:
            issues.extend(_validate_legacy_xls(path, display_path))
        elif inspection.kind == "image":
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
    return {
        "checkedFileCount": checked,
        "invalidFileCount": 0,
        "normalizedFiles": normalized,
    }


def _extract_zip_file(path: Path, target_root: Path) -> int:
    saved_count = 0
    extracted_bytes = 0
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ApiException(400, "INVALID_TEM_ZIP", "읽을 수 없는 ZIP 파일입니다.") from exc

    with archive:
        try:
            _count, expanded = _archive_limits(archive)
        except ValueError as exc:
            raise ApiException(413, "TEM_EXPANDED_BUNDLE_TOO_LARGE", str(exc)) from exc
        _require_disk_space(target_root, expanded)
        for member in archive.infolist():
            if member.is_dir():
                continue
            relative = _safe_relative_path(member.filename, "zip-file")
            if "__MACOSX" in relative.parts or relative.name.startswith("."):
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
            normalized, _inspection, issue = _normalize_ahn_content_path(destination, target_root)
            if issue:
                normalized.unlink(missing_ok=True)
                continue
            if not _ahn_canonical_suffix(_inspection):
                normalized.unlink(missing_ok=True)
                continue
            if normalized.suffix.lower() == ".zip":
                normalized.unlink(missing_ok=True)
                continue
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
    if len(files) > MAX_AHN_UPLOAD_FILES:
        raise ApiException(413, "TEM_TOO_MANY_FILES", f"한 묶음은 최대 {MAX_AHN_UPLOAD_FILES:,}개 파일입니다.")

    upload_root.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    total_bytes = 0
    for index, upload in enumerate(files, start=1):
        relative = _safe_relative_path(upload.filename, f"ahn-file-{index}")
        suffix = relative.suffix.lower()
        if not suffix and relative.name.startswith("."):
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

    if not saved:
        raise ApiException(400, "TEM_FILES_REQUIRED", "분석 가능한 TEM 파일이 없습니다.")
    return saved


async def _write_upload_chunk(session: AhnUploadSession, **kwargs) -> AhnUploadFileState:
    # A retry must never truncate a concurrent write/finalization of this session.
    if not session.lock.acquire(blocking=False):
        await kwargs["upload"].close()
        raise ApiException(409, "TEM_UPLOAD_BUSY", "이 업로드의 다른 조각을 처리 중입니다. 잠시 후 다시 시도하세요.", retryable=True)
    try:
        if session.finalized:
            raise ApiException(409, "TEM_UPLOAD_FINALIZED", "이미 보고서 처리에 넘긴 업로드입니다. 파일을 변경하려면 새 업로드를 시작하세요.")
        return await _write_upload_chunk_locked(session, **kwargs)
    except OSError as exc:
        if exc.errno == 28:
            raise ApiException(507, "TEM_INSUFFICIENT_DISK_SPACE", "Edge 서버의 임시 저장 공간이 부족합니다.", retryable=True) from exc
        raise
    finally:
        try:
            await kwargs["upload"].close()
        finally:
            session.lock.release()


async def _write_upload_chunk_locked(
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
    if session.expected_files is not None:
        expected = session.expected_files.get(file_key)
        if expected is None or expected["size"] != total_size or expected["path"] != relative_path:
            raise ApiException(409, "TEM_UPLOAD_SELECTION_CHANGED", "처음 선택한 파일과 다릅니다. 같은 파일을 선택하거나 초기화 후 새로 시작하세요.")
    with _ahn_upload_sessions_lock:
        file_state = session.files.get(file_key)
        if file_state is None:
            if len(session.files) >= MAX_AHN_UPLOAD_FILES:
                raise ApiException(413, "TEM_TOO_MANY_FILES", f"한 묶음은 최대 {MAX_AHN_UPLOAD_FILES:,}개 파일입니다.")
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
    current_path = destination if file_state.completed else temp_path
    current_size = current_path.stat().st_size if current_path.exists() else 0
    if current_size != file_state.uploaded_bytes:
        raise ApiException(409, "TEM_UPLOAD_STORAGE_MISMATCH", "서버 파일 크기가 저장된 진행 상태와 다릅니다. 초기화 후 다시 업로드하세요.")
    if current_size < offset:
        raise ApiException(
            409,
            "TEM_UPLOAD_OFFSET_MISMATCH",
            "이전 업로드 조각이 아직 서버에 없습니다. 잠시 후 다시 시도하세요.",
            details={"expectedOffset": current_size, "receivedOffset": offset},
        )

    written = 0
    received_crc32 = 0
    _require_disk_space(session.work_dir, min(MAX_AHN_CHUNK_BYTES, total_size - offset) * 2)
    # Stage on disk until the CRC is verified; a corrupt retry leaves existing
    # .part bytes intact. Never accumulate an arbitrarily sized request in RAM.
    with tempfile.TemporaryFile(dir=session.work_dir) as received:
        while True:
            chunk = await upload.read(AHN_UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_AHN_CHUNK_BYTES or offset + written > total_size:
                raise ApiException(
                    400,
                    "TEM_UPLOAD_CHUNK_TOO_LARGE",
                    "업로드 조각이 8MB 또는 전체 파일 크기를 초과했습니다.",
                )
            received_crc32 = zlib.crc32(chunk, received_crc32)
            received.write(chunk)
        if not written:
            raise ApiException(400, "TEM_EMPTY_CHUNK", "빈 업로드 조각은 저장하지 않습니다.")
        actual_crc32 = f"{received_crc32 & 0xFFFFFFFF:08x}"
        if expected_crc32 and actual_crc32 != expected_crc32:
            raise ApiException(
                400,
                "TEM_UPLOAD_CHUNK_CHECKSUM_MISMATCH",
                f"업로드 조각 무결성 검사에 실패했습니다: {relative.name}. 같은 파일을 다시 업로드하세요.",
                retryable=True,
                details={"expectedCrc32": expected_crc32, "actualCrc32": actual_crc32},
            )

        if offset < current_size:
            # An acknowledged chunk may be retried after its response was lost.
            # Never truncate later chunks or replace a completed file on retry.
            if {"offset": offset, "size": written, "crc32": actual_crc32} in file_state.chunks:
                return file_state
            raise ApiException(409, "TEM_UPLOAD_CONTENT_CHANGED", "이미 저장된 조각과 파일 내용이 다릅니다. 같은 원본 파일로 이어받으세요.")
        if sum(len(state.chunks) for state in session.files.values()) >= 25000:
            raise ApiException(413, "TEM_TOO_MANY_UPLOAD_CHUNKS", "업로드 조각 수 한도를 초과했습니다. 초기화 후 기본 조각 크기로 다시 업로드하세요.")

        temp_path.parent.mkdir(parents=True, exist_ok=True)
        received.seek(0)
        with temp_path.open("r+b" if temp_path.exists() else "wb") as output:
            output.seek(offset)
            shutil.copyfileobj(received, output, AHN_UPLOAD_CHUNK_BYTES)
            output.truncate(offset + written)

    file_state.uploaded_bytes = min(total_size, offset + written)
    file_state.chunks.append({"offset": offset, "size": written, "crc32": actual_crc32})
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
            if now - job.updated_at > AHN_REPORT_JOB_TTL_SECONDS
            and job.status not in {"queued", "running"}
            and not job.active_revisions
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
            if now - session.updated_at > AHN_UPLOAD_SESSION_TTL_SECONDS and not session.lock.locked()
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
        if session is not None and time.time() - session.updated_at > AHN_UPLOAD_SESSION_TTL_SECONDS and not session.lock.locked():
            session = None
    if session is None:
        raise ApiException(
            404,
            "TEM_UPLOAD_SESSION_NOT_FOUND",
            "TEM 업로드가 만료되었거나 서버가 재시작되었습니다. 초기화 후 파일을 다시 업로드하세요.",
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
        "maxFileCount": MAX_AHN_UPLOAD_FILES,
        "maxChunkBytes": MAX_AHN_CHUNK_BYTES,
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
    settings: Settings | None = None,
    database: Database | None = None,
    usage_archive: UsageArchive | None = None,
    usage_client_context: dict[str, str | None] | None = None,
    *, owner_user_id: str | None = None,
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
        settings=settings,
        database=database,
        error_archive=error_archive,
        usage_archive=usage_archive,
        usage_client_context=usage_client_context or {},
        created_at=now,
        updated_at=now,
        owner_user_id=owner_user_id,
    )
    with _ahn_report_jobs_lock:
        _ahn_report_jobs[job_id] = job
    return job


def _run_ahn_job(job: AhnReportJob) -> None:
    with _ahn_processing_slots:
        _process_ahn_job(job)


def _process_ahn_job(job: AhnReportJob) -> None:
    started = time.perf_counter()
    _set_job_state(
        job,
        status="running",
        progress_pct=15 if job.validate_upload else 25,
        message="TEM/STEM/EDS/코팅층 데이터를 분석하는 중입니다.",
    )
    try:
        if job.validate_upload:
            _set_job_state(job, progress_pct=15, message="서버에서 raw 파일의 무결성·암호화·처리 용량을 검사하는 중입니다.")
            _validate_ahn_upload_files(job.input_root)
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
            client_context=job.usage_client_context,
        )
        return
    except Exception as exc:
        logger.exception("TEM 보고서 생성 실패 (input_root=%s)", job.input_root)
        api_exc = ApiException(
            500,
            "TEM_MEMORY_LIMIT" if isinstance(exc, MemoryError) else "TEM_REPORT_BUILD_FAILED",
            ("Edge 서버의 메모리가 부족합니다. 입력 묶음을 나누거나 큰 이미지의 크기를 줄여 다시 시도하세요."
             if isinstance(exc, MemoryError) else f"TEM 보고서 생성 중 오류가 발생했습니다: {exc}"),
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
            client_context=job.usage_client_context,
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
            client_context=job.usage_client_context,
        )
        return
    try:
        from .tem_report_editor import prepare_review
        _set_job_state(job, progress_pct=90, message="TEM PPT 미리보기와 메일 문안을 준비하는 중입니다.")
        prepare_review(job, manifest)
        _build_package(job.output_dir, job.package_path)
        job.package_path = register_generated_report_package(
            settings=job.settings,
            database=job.database,
            report_id=job.job_id,
            package_path=job.package_path,
            experiment_code="TEM",
            is_test=True,
        )
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
            client_context=job.usage_client_context,
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
        client_context=job.usage_client_context,
    )


def _submit_ahn_job(
    input_root: Path,
    work_dir: Path,
    error_archive: ErrorArchive | None = None,
    settings: Settings | None = None,
    database: Database | None = None,
    usage_archive: UsageArchive | None = None,
    usage_client_context: dict[str, str | None] | None = None,
    *,
    validate_upload: bool = False,
    owner_user_id: str | None = None,
) -> AhnReportJob:
    slots = _ahn_pending_slots
    if not slots.acquire(blocking=False):
        raise ApiException(503, "TEM_REPORT_QUEUE_FULL", "Edge 서버의 TEM 처리 대기열이 가득 찼습니다. 기존 작업 완료 후 다시 시도하세요.", retryable=True)
    job = None
    try:
        job = _create_ahn_job(input_root, work_dir, error_archive, settings, database,
                              usage_archive, usage_client_context, owner_user_id=owner_user_id)
        job.validate_upload = validate_upload
        future = _ahn_report_executor.submit(_run_ahn_job, job)
        future.add_done_callback(lambda _future: slots.release())
    except BaseException:
        slots.release()
        if job is not None:
            with _ahn_report_jobs_lock:
                _ahn_report_jobs.pop(job.job_id, None)
        raise
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
        "parentJobId": job.parent_job_id,
        "editUrl": f"/api/v1/tem/report/jobs/{job.job_id}/edit" if job.status == "completed" else None,
        "previewUrl": f"/api/v1/tem/report/jobs/{job.job_id}/preview" if job.status == "completed" else None,
        "transfer": job.transfer_metadata,
        "status": job.status,
        "progressPct": job.progress_pct,
        "message": job.message,
        "summary": manifest.get("summary") or {},
        "manifest": manifest,
        "downloads": downloads,
        "error": job.error,
        "errorEventId": job.error_event_id,
        "errorFeedbackUrl": (
            f"/error-feedback/{job.error_event_id}" if job.error_event_id else None
        ),
    }


def build_ahn_page(resume_owner: str = "anonymous") -> str:
    page = """<!doctype html>
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
    .ahn-collect-progress .ahn-progress-track { position: relative; }
    .ahn-collect-progress .ahn-progress-bar {
      position: absolute;
      left: 0;
      width: 38%;
      animation: ahn-collect-progress 1.05s ease-in-out infinite;
      transition: none;
    }
    .ahn-upload-progress .ahn-progress-bar { background: #16a34a; }
    .ahn-progress.is-error .ahn-progress-bar { background: #dc2626; }
    @keyframes ahn-collect-progress {
      0% { transform: translateX(-110%); }
      100% { transform: translateX(280%); }
    }
    @media (prefers-reduced-motion: reduce) {
      .ahn-collect-progress .ahn-progress-bar { animation-duration: 2.4s; }
    }
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
    .ahn-transfer {
      display: grid;
      gap: 12px;
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
    }
    .ahn-transfer-head,
    .ahn-transfer-actions {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
    }
    .ahn-transfer-head strong { color: var(--ink); }
    .ahn-request-picker {
      display: grid;
      grid-template-columns: auto minmax(240px, 1fr);
      gap: 10px;
    }
    .ahn-request-picker select,
    .ahn-transfer-grid input {
      width: 100%;
      min-width: 0;
      min-height: 40px;
      border: 1px solid #bfd0e4;
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
      padding: 8px 10px;
      font: inherit;
    }
    .ahn-request-detail {
      display: flex;
      flex-wrap: wrap;
      gap: 7px 14px;
      min-height: 22px;
      color: #475569;
      font-size: 13px;
    }
    .ahn-request-detail.is-empty { color: var(--muted); }
    .ahn-request-detail b { margin-right: 4px; color: #1e3a5f; }
    .ahn-transfer-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(150px, 1fr));
      gap: 10px;
    }
    .ahn-transfer-grid label { min-width: 0; color: #475569; font-size: 13px; }
    .ahn-transfer-grid span { display: block; margin-bottom: 5px; font-weight: 700; }
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
    .ahn-busy.show { display: none; }
    .ahn-recovery { border: 1px solid #bfcee0; border-radius: 8px; padding: 12px 14px; margin: 12px 0; background: #eff6ff; color: #244565; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
    .ahn-recovery span { flex: 1 1 260px; min-width: 0; overflow-wrap: anywhere; }
    .ahn-recovery button { white-space: nowrap; flex-shrink: 0; }
    .ahn-transfer-stats { margin: 12px 0; padding: 12px 14px; border: 1px solid #bfcee0; border-radius: 8px; background: #f8fbff; color: #244565; font-size: 13px; overflow-wrap: anywhere; }
    .ahn-transfer-stats summary { cursor: pointer; margin-top: 6px; }
    #ahn-transfer-detail { white-space: pre-line; line-height: 1.6; }
    @media (max-width: 900px) {
      .ahn-topbar { align-items: flex-start; padding: 18px 16px; }
      .ahn-brand { display: block; }
      .ahn-brand h1 { font-size: 25px; }
      .ahn-brand span { display: block; margin-top: 4px; white-space: normal; }
      .ahn-main { padding: 14px 12px 24px; }
      .ahn-topbar { flex-wrap: wrap; }
      .ahn-brand { flex: 1 1 100%; }
      .ahn-actions { width: 100%; justify-content: flex-end; }
      button, .ahn-download { min-height: 40px; padding: 8px 11px; font-size: 14px; }
      .ahn-summary { grid-template-columns: repeat(2, minmax(120px, 1fr)); }
      .ahn-request-picker { grid-template-columns: 1fr; }
      .ahn-transfer-grid { grid-template-columns: repeat(2, minmax(120px, 1fr)); }
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
        <a class="ahn-download" id="ahn-admin-link" href="/operations" hidden>운영 관리</a>
        <a class="ahn-download" href="/voc?project=TEM" target="_blank" rel="noopener">VOC · 의견 보내기</a>
        <button type="button" id="ahn-example">예제 불러오기</button>
        <button type="submit" form="ahn-form" class="primary" id="ahn-run" disabled>보고서 생성</button>
        <button type="button" id="ahn-clear">초기화</button>
        <a class="ahn-download" id="ahn-account-link" href="/account" target="_blank" rel="noopener" hidden>회원정보 관리</a>
        <button type="button" id="ahn-logout" hidden>로그아웃</button>
      </div>
    </header>
    <main class="ahn-main" data-tem-resume-owner="__TEM_OWNER__">
      <div class="ahn-recovery" id="ahn-recovery" role="status">
        <span id="ahn-recovery-message">업로드 중 화면이 꺼지면 복귀 후 이어받습니다. 업로드 완료 후 보고서 생성은 서버에서 계속됩니다. 서버 재시작 또는 마지막 전송 후 2시간이 지나면 새 업로드가 필요합니다.</span>
        <button type="button" id="ahn-recovery-resume" hidden>업로드 이어받기</button>
      </div>
      <div class="ahn-transfer-stats" id="ahn-transfer-stats" hidden>
        <strong id="ahn-transfer-speed"></strong>
        <details><summary>업로드 성능 측정</summary><div id="ahn-transfer-detail"></div></details>
      </div>
      <section class="ahn-panel">
        <form id="ahn-form">
          <input class="ahn-hidden-input" type="file" id="ahn-bundle-files" name="files" multiple>
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
        <div class="ahn-progress-stage ahn-collect-progress" id="ahn-collect-progress">
          <div class="ahn-progress-row">
            <span id="ahn-collect-progress-label">선택한 파일 목록을 준비하는 중입니다.</span>
            <span>처리 중</span>
          </div>
          <div class="ahn-progress-track">
            <div class="ahn-progress-bar"></div>
          </div>
        </div>
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
        <div class="ahn-downloads" hidden style="display:none">
          <a href="#" class="ahn-download" id="ahn-download-pptx" aria-disabled="true">PPTX 다운로드</a>
          <a href="#" class="ahn-download" id="ahn-download-package" aria-disabled="true">보고서 ZIP 다운로드</a>
          <a href="#" class="ahn-download" id="ahn-download-json" aria-disabled="true">분석 JSON 다운로드</a>
        </div>
      </section>
      <section class="ahn-transfer" id="ahn-report-transfer" hidden style="display:none">
        <div class="ahn-transfer-head">
          <strong>보고서 전송 정보</strong>
          <span>보고서를 확인한 뒤 공유 저장소와 LIMS 전송 대기열에 등록합니다.</span>
        </div>
        <div class="ahn-request-picker">
          <button type="button" id="ahn-request-load">의뢰 조회</button>
          <select id="ahn-request-select" aria-label="TEM 의뢰 선택">
            <option value="">의뢰 조회 후 항목을 선택하세요</option>
          </select>
        </div>
        <div class="ahn-request-detail is-empty" id="ahn-request-detail">
          TEM 의뢰를 조회하면 상세 정보가 표시됩니다.
        </div>
        <div class="ahn-transfer-grid">
          <label><span>의뢰번호</span><input type="text" data-ahn-transfer-field="requestNumber" placeholder="조회 후 선택하거나 직접 입력" maxlength="100"></label>
          <label><span>실험코드</span><input type="text" data-ahn-transfer-field="limsExperimentCode" placeholder="LIMS 실험코드 (전송 시 필수)" maxlength="50"></label>
          <label><span>실험장비</span><input type="text" data-ahn-transfer-field="equipmentCode" value="TEM-EDGE-01"></label>
          <label><span>실험자</span><input type="text" data-ahn-transfer-field="operatorId" value="SSO-PENDING"></label>
        </div>
        <div class="ahn-transfer-actions">
          <span>의뢰번호, 실험코드, 실험장비, 실험자가 모두 있어야 전송할 수 있습니다.</span>
          <button type="button" class="primary" id="ahn-report-send" disabled>보고서 전송</button>
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
    var adminLink = document.getElementById("ahn-admin-link");
    var status = document.getElementById("ahn-status");
    var busy = document.getElementById("ahn-busy");
    var drop = document.getElementById("ahn-drop");
    var fileList = document.getElementById("ahn-file-list");
    var bundleMeta = document.getElementById("ahn-bundle-meta");
    var progress = document.getElementById("ahn-progress");
    var collectProgress = document.getElementById("ahn-collect-progress");
    var collectProgressLabel = document.getElementById("ahn-collect-progress-label");
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
    var requestLoad = document.getElementById("ahn-request-load");
    var requestSelect = document.getElementById("ahn-request-select");
    var requestDetail = document.getElementById("ahn-request-detail");
    var reportSendButton = document.getElementById("ahn-report-send");
    var logoutButton = document.getElementById("ahn-logout");
    var accountLink = document.getElementById("ahn-account-link");
    var reportTransferControls = Array.prototype.slice.call(
      document.querySelectorAll("[data-ahn-transfer-field]")
    );
    var bundleItems = [];
    var progressTimer = null;
    var clientProfile = window.RIST_CLIENT_PROFILE || {};
    var TEM_FILE_LIST_LIMIT = clientProfile.fileListLimit || 200;
    var TEM_MAX_FILES = 5000;
    var TEM_UPLOAD_CHUNK_RETRIES = 4;
    var TEM_MAX_FILE_BYTES = 250 * 1024 * 1024;
    var TEM_MAX_TOTAL_BYTES = 1200 * 1024 * 1024;
    var collectProgressVisible = false;
    var uploadProgressVisible = false;
    var reportProgressVisible = false;
    var uploadProgressError = false;
    var reportProgressError = false;
    var operationBusy = false;
    var collectingFiles = false;
    var requestItems = [];
    var lastReportJob = null;
    var REQUEST_EXPERIMENT_TYPE = "TEM";

    /* TEM_RESUME_RUNTIME */
    /* TEM_TRANSFER_RUNTIME */

    function configureAuthenticatedActions() {
      fetch("/api/v1/auth/me", {
        credentials: "same-origin",
        headers: {"Accept": "application/json"}
      }).then(function(response) {
        if (!response.ok) throw new Error("auth lookup failed");
        return response.json();
      }).then(function(payload) {
        var roles = payload && Array.isArray(payload.roles) ? payload.roles : [];
        if (adminLink) adminLink.hidden = roles.indexOf("ADMIN") === -1;
        if (accountLink) accountLink.hidden = false;
        if (logoutButton) logoutButton.hidden = false;
      }).catch(function() {
        if (adminLink) adminLink.hidden = true;
        if (accountLink) accountLink.hidden = true;
        if (logoutButton) logoutButton.hidden = true;
      });
    }

    function logoutFromProject() {
      if (!logoutButton || logoutButton.disabled) return;
      logoutButton.disabled = true;
      fetch("/api/v1/auth/logout", {
        method: "POST",
        credentials: "same-origin",
        headers: {"X-Requested-With": "RIST-Project"}
      }).then(function(response) {
        if (!response.ok) throw new Error("logout failed");
        window.location.href = "/login";
      }).catch(function() {
        logoutButton.disabled = false;
        window.alert("로그아웃하지 못했습니다. 다시 시도하세요.");
      });
    }

    if (logoutButton) logoutButton.addEventListener("click", logoutFromProject);
    configureAuthenticatedActions();

    function syncActionState() {
      runButton.disabled = operationBusy || collectingFiles || temRecovery.restoring || !bundleItems.length;
      exampleButton.disabled = operationBusy || collectingFiles || temRecovery.restoring;
      addFilesButton.disabled = operationBusy || collectingFiles || temRecovery.restoring;
      addFolderButton.disabled = operationBusy || collectingFiles || temRecovery.restoring;
      clearButton.disabled = operationBusy || collectingFiles || temRecovery.restoring;
      requestLoad.disabled = operationBusy || collectingFiles;
      drop.setAttribute("aria-busy", collectingFiles ? "true" : "false");
      form.setAttribute("aria-busy", operationBusy || collectingFiles ? "true" : "false");
      updateReportSendAvailability();
      syncRecoveryControls();
    }

    function setCollectingFiles(value, message) {
      collectingFiles = Boolean(value);
      if (collectingFiles) {
        var label = message || "첨부 파일 목록을 읽는 중입니다.";
        bundleMeta.textContent = label;
        collectProgressLabel.textContent = label;
        collectProgressVisible = true;
        updateProgressVisibility();
        syncActionState();
        return;
      }
      renderFileList();
      collectProgressVisible = false;
      updateProgressVisibility();
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
    function reportTransferValue(field) {
      var control = reportTransferControls.find(function(item) {
        return item.dataset.ahnTransferField === field;
      });
      return control ? String(control.value || "").trim() : "";
    }
    function setReportTransferValue(field, value) {
      reportTransferControls.forEach(function(control) {
        if (control.dataset.ahnTransferField === field) control.value = value || "";
      });
    }
    function reportTransferFormState() {
      return {
        requestNumber: reportTransferValue("requestNumber"),
        limsExperimentCode: reportTransferValue("limsExperimentCode"),
        equipmentCode: reportTransferValue("equipmentCode"),
        operatorId: reportTransferValue("operatorId")
      };
    }
    function selectedRequestItem() {
      if (requestSelect.value === "") return null;
      var index = Number(requestSelect.value);
      return Number.isInteger(index) && index >= 0 ? requestItems[index] || null : null;
    }
    function handleReportTransferInput() {
      var selected = selectedRequestItem();
      if (selected) {
        var code = String(selected.experimentCode || selected.testMethodCode || "").trim();
        var numberChanged = reportTransferValue("requestNumber") !== String(selected.requestNumber || "").trim();
        var codeChanged = reportTransferValue("limsExperimentCode") !== code;
        if (numberChanged || codeChanged) {
          requestSelect.value = "";
          if (numberChanged && !codeChanged) setReportTransferValue("limsExperimentCode", "");
        }
      }
      renderRequestDetail(selectedRequestItem());
      updateReportSendAvailability();
    }
    function requestOptionLabel(item) {
      return [
        item.requestNumber || "(의뢰번호 없음)",
        item.requestDate || "",
        item.requestStateName || "",
        item.experimentCode || item.testMethodCode || "(실험코드 없음)",
        item.experimentName || item.testMethodName || "",
        item.sampleName || "",
        item.customerRequestName || ""
      ].filter(Boolean).join(" · ");
    }
    function renderRequestDetail(item) {
      requestDetail.replaceChildren();
      if (!item) {
        requestDetail.classList.add("is-empty");
        requestDetail.textContent = reportTransferValue("requestNumber")
          ? "직접 입력한 의뢰번호입니다. 전송 전 의뢰번호와 LIMS 실험코드를 확인하세요."
          : "의뢰를 조회하여 선택하거나 아래에 의뢰번호를 직접 입력하세요.";
        return;
      }
      requestDetail.classList.remove("is-empty");
      [
        ["의뢰번호", item.requestNumber], ["의뢰일", item.requestDate],
        ["상태", item.requestStateName], ["의뢰명", item.customerRequestName],
        ["시료", item.sampleName], ["실험코드", item.experimentCode || item.testMethodCode],
        ["시험명", item.experimentName || item.testMethodName],
        ["담당자", item.testChargerName], ["고객", item.customerName]
      ].forEach(function(row) {
        if (!row[1]) return;
        var entry = document.createElement("span");
        var label = document.createElement("b");
        label.textContent = row[0];
        entry.appendChild(label);
        entry.appendChild(document.createTextNode(String(row[1])));
        requestDetail.appendChild(entry);
      });
    }
    function renderRequestOptions(items) {
      requestSelect.replaceChildren();
      var emptyOption = document.createElement("option");
      emptyOption.value = "";
      emptyOption.textContent = items.length ? "의뢰를 선택하세요" : "조회된 의뢰가 없습니다";
      requestSelect.appendChild(emptyOption);
      items.forEach(function(item, index) {
        var option = document.createElement("option");
        option.value = String(index);
        option.textContent = requestOptionLabel(item);
        requestSelect.appendChild(option);
      });
      renderRequestDetail(null);
      updateReportSendAvailability();
    }
    function updateReportSendAvailability() {
      var transfer = reportTransferFormState();
      var ready = Boolean(
        lastReportJob && lastReportJob.jobId && lastReportJob.status === "completed"
        && transfer.requestNumber && transfer.limsExperimentCode
        && transfer.equipmentCode && transfer.operatorId
      );
      reportSendButton.disabled = !ready || operationBusy || collectingFiles;
      reportSendButton.title = ready
        ? "완성된 TEM 보고서를 LIMS 전송 대기열에 등록합니다."
        : "보고서 완료 및 전송 정보 입력 후 전송할 수 있습니다.";
    }
    function progressMessage(percent) {
      if (percent < 25) return "raw 파일을 서버로 전송하는 중입니다.";
      if (percent < 48) return "TEM/STEM/EDS/코팅층 폴더를 분류하는 중입니다.";
      if (percent < 72) return "코팅층 OCR과 분석 JSON을 만드는 중입니다.";
      return "PowerPoint 보고서를 생성하는 중입니다. 완료되면 보고서 미리보기로 이동합니다.";
    }
    function updateProgressVisibility() {
      progress.classList.toggle(
        "is-visible",
        collectProgressVisible || uploadProgressVisible || reportProgressVisible
      );
      progress.classList.toggle("is-error", uploadProgressError || reportProgressError);
      collectProgress.classList.toggle("is-visible", collectProgressVisible);
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
    function failProgress(message, feedbackUrl) {
      stopProgressTimer();
      setProgress(100, message || "보고서 생성에 실패했습니다.", true, true);
      if (feedbackUrl) {
        var feedbackLink = document.createElement("a");
        feedbackLink.href = feedbackUrl;
        feedbackLink.target = "_blank";
        feedbackLink.rel = "noopener";
        feedbackLink.textContent = "오류 코멘트 남기기";
        feedbackLink.style.cssText = "margin-left:8px;color:inherit;font-weight:700;text-decoration:underline";
        progressLabel.appendChild(document.createTextNode(" "));
        progressLabel.appendChild(feedbackLink);
      }
      setTimeout(function() {
        setUploadProgress(0, "raw 파일 업로드 대기", false, false);
        setProgress(0, "보고서 생성 대기", false, false);
      }, feedbackUrl ? 10000 : 1800);
    }
    function filesOf(input) {
      return Array.prototype.slice.call(input.files || []);
    }
    function bundleItem(file, path) {
      return {file: file, path: path || file.webkitRelativePath || file.name};
    }
    function classifyFile(file) {
      var name = file.name.toLowerCase();
      if (name.charAt(0) === "." || name.indexOf("~$") === 0) return "skip";
      if (/\\.(tif|tiff|png|jpe?g|bmp|webp)$/.test(name)) return "image";
      if (/\\.docx$/.test(name)) return "docx";
      if (/\\.(xlsx|xls|xlsm|xlsb|csv|tsv)$/.test(name)) return "table";
      if (/\\.zip$/.test(name)) return "zip";
      return "content";
    }
    function classifySection(path) {
      if (/\\.docx$/i.test(String(path || ""))) return "EDS";
      var lowered = String(path || "").toLowerCase().split("/");
      if (lowered.indexOf("tem") >= 0) return "TEM";
      if (lowered.indexOf("stem") >= 0) return "STEM";
      if (lowered.indexOf("report") >= 0 || lowered.indexOf("reports") >= 0) return "EDS";
      if (lowered.indexOf("scale") >= 0) return "코팅층";
      return "기타";
    }
    function addBundleItems(items) {
      if (items.length > TEM_MAX_FILES) {
        throw new Error("파일이 너무 많습니다. " + TEM_MAX_FILES + "개 이하로 나누어 선택하세요.");
      }
      var seen = new Set(bundleItems.map(function(item) {
        return item.path + "|" + item.file.size + "|" + item.file.lastModified;
      }));
      var additions = [];
      items.forEach(function(item) {
        var key = item.path + "|" + item.file.size + "|" + item.file.lastModified;
        if (!seen.has(key)) {
          seen.add(key);
          additions.push(item);
        }
      });
      if (bundleItems.length + additions.length > TEM_MAX_FILES) {
        throw new Error("선택 가능한 파일 수는 " + TEM_MAX_FILES + "개입니다. 묶음을 나누어 업로드하세요.");
      }
      bundleItems = bundleItems.concat(additions);
    }
    function renderFileList() {
      var counts = {TEM: 0, STEM: 0, EDS: 0, "코팅층": 0, "기타": 0};
      var fragment = document.createDocumentFragment();
      fileList.replaceChildren();
      bundleItems.forEach(function(item, index) {
        var section = classifySection(item.path);
        counts[section] = (counts[section] || 0) + 1;
        if (index >= TEM_FILE_LIST_LIMIT) return;
        var chip = document.createElement("span");
        chip.className = "ahn-chip";
        chip.textContent = section + " · " + item.path;
        fragment.appendChild(chip);
      });
      if (bundleItems.length > TEM_FILE_LIST_LIMIT) {
        var more = document.createElement("span");
        more.className = "ahn-chip";
        more.textContent = "외 " + (bundleItems.length - TEM_FILE_LIST_LIMIT) + "개 · 목록만 생략하며 모든 선택 파일은 업로드됩니다.";
        fragment.appendChild(more);
      }
      fileList.appendChild(fragment);
      var transferPolicy = TemTransfer.policy(navigator);
      bundleMeta.textContent = bundleItems.length
        ? "TEM " + counts.TEM + " · STEM " + counts.STEM + " · EDS " + counts.EDS + " · 코팅층 " + counts["코팅층"] + " · 기타 " + counts["기타"] + " · " + transferPolicy.label + ": " + (transferPolicy.initial / 1048576) + " MiB부터 자동 조절 (최대 " + (transferPolicy.max / 1048576) + " MiB)"
        : "선택된 파일 없음";
      syncActionState();
    }
    function fileInputItems(input) {
      if (input.files && input.files.length > TEM_MAX_FILES) {
        throw new Error("파일이 너무 많습니다. " + TEM_MAX_FILES + "개 이하로 나누어 선택하세요.");
      }
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
            if (entries.length > TEM_MAX_FILES) {
              reject(new Error("폴더 항목이 너무 많습니다. 폴더를 나누어 업로드하세요."));
              return;
            }
            readBatch();
          }, reject);
        }
        readBatch();
      });
    }
    async function entryToBundleItems(entry, prefix, collected, depth, walkState) {
      prefix = prefix || "";
      collected = collected || [];
      depth = depth || 0;
      walkState = walkState || {visited: 0};
      walkState.visited += 1;
      if (depth > 32 || walkState.visited > TEM_MAX_FILES * 2 || (entry.isFile && collected.length >= TEM_MAX_FILES)) {
        throw new Error("폴더 깊이 또는 파일 수 한도를 초과했습니다. 폴더를 나누어 업로드하세요.");
      }
      if (entry.isFile) {
        var file = await new Promise(function(resolve, reject) {
          entry.file(function(file) {
            resolve(file);
          }, reject);
        });
        collected.push(bundleItem(file, prefix + file.name));
        if (collected.length % 50 === 0) await sleep(0);
      }
      if (entry.isDirectory) {
        var entries = await readAllDirectoryEntries(entry.createReader());
        for (var index = 0; index < entries.length; index += 1) {
          await entryToBundleItems(entries[index], prefix + entry.name + "/", collected, depth + 1, walkState);
        }
      }
      return collected;
    }
    function captureDroppedItems(dataTransfer) {
      // Drag data is readable only during the synchronous drop event. Keep
      // handles, not file contents, before yielding to paint or directory I/O.
      if (!dataTransfer) return [];
      if ((dataTransfer.items || []).length > TEM_MAX_FILES || (dataTransfer.files || []).length > TEM_MAX_FILES) {
        throw new Error("파일이 너무 많습니다. 묶음을 나누어 선택하세요.");
      }
      var items = Array.prototype.slice.call(dataTransfer.items || []);
      var captured = [];
      items.forEach(function(item) {
        if (item.kind !== "file") return;
        var entry = null;
        try {
          var getEntry = item.getAsEntry || item.webkitGetAsEntry;
          if (getEntry) entry = getEntry.call(item);
        } catch (_) { /* Fall back per file, not for the whole drop. */ }
        var file = !entry && item.getAsFile ? item.getAsFile() : null;
        if (entry || file) captured.push({entry: entry, file: file});
      });
      if (!captured.length) {
        captured = Array.prototype.slice.call(dataTransfer.files || []).map(function(file) {
          return {file: file};
        });
      }
      return captured;
    }
    async function droppedBundleItems(captured) {
      var collected = [];
      var walkState = {visited: 0};
      for (var index = 0; index < captured.length; index += 1) {
        var item = captured[index];
        if (item.entry) {
          await entryToBundleItems(item.entry, "", collected, 0, walkState);
        } else if (item.file) {
          collected.push(bundleItem(item.file));
        }
      }
      return collected;
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
    function errorFromResponse(source, text, fallback) {
      var payload = {};
      try { payload = JSON.parse(text || "{}"); } catch (_error) {}
      var getHeader = source && source.headers && source.headers.get
        ? function(name) { return source.headers.get(name); }
        : source && source.getResponseHeader
          ? function(name) { return source.getResponseHeader(name); }
          : function() { return null; };
      var error = new Error(payload.message || payload.detail || parseErrorMessage(text, fallback));
      error.status = source && source.status;
      error.code = payload.code;
      error.isTransientError = payload.retryable === true;
      error.errorEventId = payload.errorEventId || getHeader("X-Error-Event-Id");
      error.errorFeedbackUrl = payload.errorFeedbackUrl
        || getHeader("X-Error-Comment-Url")
        || (error.errorEventId ? "/error-feedback/" + encodeURIComponent(error.errorEventId) : "");
      return error;
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
    async function requestJsonPost(url, body) {
      var response;
      try {
        var options = {method: "POST"};
        if (body) { options.headers = {"Content-Type": "application/json"}; options.body = JSON.stringify(body); }
        response = await temFetch(url, options);
      } catch (error) {
        var wrapped = new Error(networkErrorMessage(error, url));
        wrapped.cause = error;
        wrapped.isNetworkError = true;
        throw wrapped;
      }
      var text = await response.text();
      if (!response.ok) {
        var error = errorFromResponse(response, text, "요청 처리에 실패했습니다.");
        error.isTransientError = error.isTransientError || response.status === 408 || response.status === 429 || response.status >= 500;
        throw error;
      }
      return JSON.parse(text);
    }
    async function requestJson(url, options) {
      var response;
      try {
        response = await fetch(url, options || {});
      } catch (error) {
        var wrapped = new Error(networkErrorMessage(error, url));
        wrapped.cause = error;
        wrapped.isNetworkError = true;
        throw wrapped;
      }
      var text = await response.text();
      if (!response.ok) {
        throw errorFromResponse(response, text, "요청 처리에 실패했습니다.");
      }
      return text ? JSON.parse(text) : {};
    }
    async function loadRequestItems() {
      requestLoad.disabled = true;
      requestLoad.textContent = "조회 중...";
      try {
        var payload = await requestJson(
          "/api/v1/requests?page=1&pageSize=200&experimentType="
            + encodeURIComponent(REQUEST_EXPERIMENT_TYPE),
          {
            headers: {"X-Request-Id": "tem-request-list-" + Date.now()}
          }
        );
        requestItems = Array.isArray(payload.items) ? payload.items : [];
        renderRequestOptions(requestItems);
        setStatus(
          requestItems.length
            ? "TEM 의뢰 목록을 불러왔습니다."
            : "조회된 TEM 의뢰가 없습니다.",
          false
        );
      } catch (error) {
        requestItems = [];
        renderRequestOptions([]);
        setStatus(error.message || "TEM 의뢰 조회에 실패했습니다.", true);
      } finally {
        requestLoad.textContent = "의뢰 조회";
        syncActionState();
      }
    }
    function applySelectedRequest() {
      var item = selectedRequestItem();
      if (!item) {
        renderRequestDetail(null);
        updateReportSendAvailability();
        return;
      }
      setReportTransferValue("requestNumber", item.requestNumber || "");
      setReportTransferValue(
        "limsExperimentCode",
        item.experimentCode || item.testMethodCode || ""
      );
      var equipmentCode = item.equipmentCode || item.deviceCode || item.instrumentCode;
      if (equipmentCode) setReportTransferValue("equipmentCode", equipmentCode);
      renderRequestDetail(item);
      updateReportSendAvailability();
    }
    async function sendReportJob() {
      var transfer = reportTransferFormState();
      if (!lastReportJob || !lastReportJob.jobId || lastReportJob.status !== "completed") {
        setStatus("먼저 TEM 보고서를 생성하고 확인하세요.", true);
        return;
      }
      if (!transfer.requestNumber || !transfer.limsExperimentCode
          || !transfer.equipmentCode || !transfer.operatorId) {
        setStatus("의뢰번호, 실험코드, 실험장비, 실험자를 모두 입력하세요.", true);
        return;
      }
      reportSendButton.disabled = true;
      reportSendButton.textContent = "전송 등록 중...";
      try {
        var result = await requestJson(
          "/api/v1/tem/report/jobs/" + encodeURIComponent(lastReportJob.jobId) + "/send",
          {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
              requestNumber: transfer.requestNumber,
              experimentCode: transfer.limsExperimentCode,
              equipmentCode: transfer.equipmentCode,
              operatorId: transfer.operatorId
            })
          }
        );
        setStatus(
          "TEM 보고서를 공유 저장소에 게시하고 전송 대기열에 등록했습니다."
            + (result.transferId ? " 전송 ID: " + result.transferId : ""),
          false
        );
      } catch (error) {
        setStatus(error.message || "TEM 보고서 전송 등록에 실패했습니다.", true);
      } finally {
        reportSendButton.textContent = "보고서 전송";
        updateReportSendAvailability();
      }
    }
    async function requestJsonPostWithRetry(url, attempts, retryMessage, body) {
      var lastError = null;
      for (var attempt = 1; attempt <= attempts; attempt += 1) {
        await waitTemVisible();
        try {
          return await requestJsonPost(url, body);
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
        var transfer = options.transfer;
        var started = transfer ? transfer.now() : 0;
        var startEpoch = transfer ? transfer.epoch() : 0;
        var uploadedAt = null, measured = false;
        function measure(payload, successful) {
          if (!transfer || measured) return;
          measured = true;
          var ended = transfer.now();
          var sample = {elapsedMs: Math.max(0, ended - started),
            responseWaitMs: uploadedAt === null ? null : Math.max(0, ended - uploadedAt),
            serverMs: payload && payload.chunkProcessingMs, epoch: startEpoch, retried: Boolean(options.retryCount)};
          if (successful) transfer.ack(options.blob.size, sample);
          else transfer.observe(sample);
        }
        xhr.open(
          "POST",
          "/api/v1/tem/upload-sessions/" + encodeURIComponent(options.uploadId) + "/chunks",
          true
        );
        xhr.timeout = 120000;
        xhr.upload.onprogress = function(event) {
          var loaded = event.lengthComputable ? Math.min(options.blob.size, event.loaded) : 0;
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
        xhr.upload.onload = function() { if (transfer) uploadedAt = transfer.now(); };
        xhr.onload = function() {
          var text = xhr.responseText || "";
          if (xhr.status >= 200 && xhr.status < 300) {
            try {
              var payload = JSON.parse(text);
              measure(payload, true);
              resolve(payload);
            } catch (_error) {
              measure(null, false);
              reject(new Error("서버 응답을 해석하지 못했습니다."));
            }
            return;
          }
          var error = errorFromResponse(xhr, text, "업로드 조각 전송에 실패했습니다.");
          measure(null, false);
          var failure = {};
          try { failure = JSON.parse(text); } catch (_parseError) {}
          error.isTransientError = failure.retryable === true || xhr.status === 408 || xhr.status === 429 || xhr.status >= 500;
          reject(error);
        };
        xhr.onerror = function(error) {
          measure(null, false);
          var wrapped = new Error(networkErrorMessage(error, "/api/v1/tem/upload-sessions"));
          wrapped.cause = error;
          wrapped.isNetworkError = true;
          reject(wrapped);
        };
        xhr.ontimeout = function(error) {
          measure(null, false);
          var wrapped = new Error("업로드 조각 전송 시간이 초과되었습니다. 같은 조각을 다시 전송합니다.");
          wrapped.cause = error;
          wrapped.isNetworkError = true;
          reject(wrapped);
        };
        xhr.onabort = function(error) {
          measure(null, false);
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
    async function chunkCrc32Main(blob) {
      var table = getCrc32Table();
      var crc = 0xFFFFFFFF;
      // At most 256KB is read into JS at a time, even for an 8MB upload chunk.
      // Yield between blocks so slow 32-bit CPUs can paint/respond to input.
      var blockBytes = 256 * 1024;
      for (var offset = 0; offset < blob.size; offset += blockBytes) {
        var block = blob.slice(offset, Math.min(blob.size, offset + blockBytes));
        var buffer = typeof block.arrayBuffer === "function" ? await block.arrayBuffer() : await new Promise(function(resolve, reject) {
          var reader = new FileReader();
          reader.onload = function() { resolve(reader.result); };
          reader.onerror = function() { reject(new Error("업로드 파일을 읽지 못했습니다.")); };
          reader.readAsArrayBuffer(block);
        });
        var bytes = new Uint8Array(buffer);
        for (var index = 0; index < bytes.length; index += 1) {
          crc = table[(crc ^ bytes[index]) & 0xFF] ^ (crc >>> 8);
        }
        bytes = null;
        buffer = null;
        await sleep(0);
      }
      return ((crc ^ 0xFFFFFFFF) >>> 0).toString(16).padStart(8, "0");
    }
    async function chunkCrc32(blob) {
      return temTransfer ? temTransfer.checksum(blob, chunkCrc32Main) : chunkCrc32Main(blob);
    }
    async function uploadChunkWithRetry(options) {
      var lastError = null;
      for (var attempt = 1; attempt <= TEM_UPLOAD_CHUNK_RETRIES; attempt += 1) {
        await waitTemVisible();
        try {
          options.retryCount = attempt - 1;
          return await requestUploadChunk(options);
        } catch (error) {
          lastError = error;
          if (!(error.isNetworkError || error.isTransientError) || attempt >= TEM_UPLOAD_CHUNK_RETRIES) break;
          if (options.transfer) options.transfer.retry();
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
      temTransfer = TemTransfer.create({render: renderTemTransfer});
      var transfer = temTransfer, transferFinished = false;
      try {
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
        var session, restoredFiles = [];
        if (temRecovery.record && temRecovery.record.uploadId) {
          session = await getTemResumeState(temRecovery.record.uploadId);
          if (session.job) { transferFinished = true; rememberTemJob(session.job); return session.job; }
          restoredFiles = await checkTemResumeSelection(selection.supported, session);
        } else {
          session = await requestJsonPostWithRetry(
            "/api/v1/tem/upload-sessions", 3, "업로드 세션 생성을 다시 시도하는 중입니다.",
            {files: temSelectionManifest(selection.supported)}
          );
          rememberTemUpload(session.uploadId);
        }
        var resumeOffsets = Object.create(null);
        restoredFiles.forEach(function(item) { resumeOffsets[item.path] = item.uploadedBytes; });
        var uploadedBytes = restoredFiles.reduce(function(sum, item) { return sum + item.uploadedBytes; }, 0);
        transfer.begin(totalBytes, uploadedBytes, session.maxChunkBytes);
        recoveryNotice("전송 중 화면이 꺼지면 복귀 후 이어받습니다. 지원되는 HTTPS 환경에서는 자동 화면 꺼짐 방지를 요청합니다.");
        if (selection.supported.length > (session.maxFileCount || TEM_MAX_FILES)) {
          throw new Error("서버 파일 수 한도를 초과했습니다. 묶음을 나누어 업로드하세요.");
        }
        await sendPreparedTemFiles(selection.supported, resumeOffsets, session, totalBytes, transfer);
        transferFinished = true;
        transfer.dispose('전송 완료');
        setUploadProgress(100, "raw 파일 업로드 완료. 무결성 및 암호화 여부를 검사하는 중입니다.", true, false);
        return await requestJsonPostWithRetry(
          "/api/v1/tem/upload-sessions/" + encodeURIComponent(session.uploadId) + "/complete?defer_validation=true",
          4,
          "보고서 작업 접수 응답을 다시 확인하는 중입니다."
        );
      } finally {
        transfer.dispose(transferFinished ? '전송 완료' : '전송 중단');
        if (temTransfer === transfer) temTransfer = null;
      }
    }
    async function requestReport(url) {
      var options = {method: "GET"};
      var response;
      try {
        response = await temFetch(url, options);
      } catch (error) {
        var wrapped = new Error(networkErrorMessage(error, url));
        wrapped.cause = error;
        wrapped.isNetworkError = true;
        throw wrapped;
      }
      var text = await response.text();
      if (!response.ok) {
        var error = errorFromResponse(response, text, "보고서 생성 요청에 실패했습니다.");
        error.isTransientError = error.isTransientError || response.status === 408 || response.status === 429 || response.status >= 500;
        throw error;
      }
      return JSON.parse(text);
    }
    function sleep(ms) {
      return new Promise(function(resolve) { setTimeout(resolve, ms); });
    }
    async function waitForReportJob(payload) {
      if (!payload || !payload.jobId) return payload;
      rememberTemJob(payload);
      stopProgressTimer();
      var current = payload;
      var shownPct = 0;
      var transientFailures = 0;
      while (current && current.status !== "completed") {
        if (current.status === "failed") {
          var error = current.error || {};
          var reportError = new Error(error.message || current.message || "TEM 보고서 생성에 실패했습니다.");
          reportError.errorEventId = current.errorEventId;
          reportError.errorFeedbackUrl = current.errorFeedbackUrl
            || (current.errorEventId ? "/error-feedback/" + encodeURIComponent(current.errorEventId) : "");
          throw reportError;
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
        await waitTemVisible();
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
    function openBundlePicker(input) {
      if (operationBusy || collectingFiles) return;
      // A picker is not collection. Older Windows browsers do not reliably
      // emit cancel/focus, so never lock the page while the dialog is open.
      input.value = "";
      try {
        input.click();
      } catch (error) {
        setStatus(error.message || String(error), true);
      }
    }
    function waitForCollectionPaint() {
      return new Promise(function(resolve) {
        var schedulePaint = window.requestAnimationFrame || function(callback) {
          return setTimeout(callback, 16);
        };
        schedulePaint(function() {
          setTimeout(resolve, 0);
        });
      });
    }
    async function collectSelectedFiles(input, message) {
      if (operationBusy || collectingFiles) {
        input.value = "";
        return;
      }
      if (!(input.files || []).length) return;
      setCollectingFiles(true, message);
      try {
        await waitForCollectionPaint();
        addBundleItems(fileInputItems(input));
      } catch (error) {
        setStatus(error.message || String(error), true);
      } finally {
        input.value = "";
        setCollectingFiles(false);
      }
    }
    bundleInput.addEventListener("change", function() {
      collectSelectedFiles(bundleInput, "첨부할 파일 목록을 읽는 중입니다.");
    });
    folderInput.addEventListener("change", function() {
      collectSelectedFiles(folderInput, "폴더 안의 raw 파일 목록을 읽는 중입니다.");
    });
    addFilesButton.addEventListener("click", function() {
      openBundlePicker(bundleInput);
    });
    addFolderButton.addEventListener("click", function() {
      openBundlePicker(folderInput);
    });
    drop.addEventListener("dragover", function(event) {
      event.preventDefault();
      if (operationBusy || collectingFiles) return;
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
      drop.classList.add("dragover");
    });
    drop.addEventListener("dragleave", function() {
      drop.classList.remove("dragover");
    });
    drop.addEventListener("drop", async function(event) {
      event.preventDefault();
      drop.classList.remove("dragover");
      if (operationBusy || collectingFiles) {
        setStatus("현재 작업이 완료된 뒤 파일을 추가하세요.", true);
        return;
      }
      setCollectingFiles(true, "드롭한 폴더의 raw 파일 목록을 읽는 중입니다.");
      try {
        var captured = captureDroppedItems(event.dataTransfer);
        await waitForCollectionPaint();
        var items = await droppedBundleItems(captured);
        if (!items.length) throw new Error("첨부할 파일을 찾지 못했습니다. 파일/폴더 추가 버튼 또는 ZIP 파일을 사용하세요.");
        addBundleItems(items);
        setStatus("TEM raw bundle 파일이 추가되었습니다.", false);
      } catch (error) {
        setStatus(error.message || String(error), true);
      } finally {
        setCollectingFiles(false);
      }
    });
    clearButton.addEventListener("click", function() {
      document.getElementById('ahn-transfer-stats').hidden = true;
      persistTemWork(null);
      temRecovery.retryOnReturn = false;
      recoveryNotice("새 작업을 시작합니다. 이전 작업의 서버 파일은 즉시 삭제하지 않으며 기존 보존 시간에 따라 정리됩니다.");
      form.reset();
      bundleItems = [];
      lastReportJob = null;
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
      updateReportSendAvailability();
    });
    exampleButton.addEventListener("click", async function() {
      if (temRecovery.record && !window.confirm("이 화면의 이어받기 연결을 지우고 예제를 시작할까요? 서버의 기존 작업은 취소하지 않습니다.")) return;
      persistTemWork(null);
      lastReportJob = null;
      updateReportSendAvailability();
      setBusy(true);
      startProgress("TEM 예제 보고서를 생성하는 중입니다.");
      try {
        await finishTemReport(await requestReport("/api/v1/tem/example", null));
      } catch (error) {
        temRecoveryError(error);
        setStatus(error.message || String(error), true);
        failProgress(
          error.message || "예제 보고서 생성에 실패했습니다.",
          error.errorFeedbackUrl || ""
        );
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
      await runTemUpload();
    });
    requestLoad.addEventListener("click", loadRequestItems);
    requestSelect.addEventListener("change", applySelectedRequest);
    reportTransferControls.forEach(function(control) {
      control.addEventListener("input", handleReportTransferInput);
      control.addEventListener("change", handleReportTransferInput);
    });
    reportSendButton.addEventListener("click", sendReportJob);
    renderRequestOptions([]);
    renderFileList();
    setStatus("TEM raw 폴더를 선택하면 보고서를 생성할 수 있습니다.", false);
    loadTemWork(document.querySelector("[data-tem-resume-owner]").getAttribute("data-tem-resume-owner"));
    syncRecoveryControls();
    restoreTemWork();
  })();
  </script>
</body>
</html>"""
    page = page.replace("/* TEM_RESUME_RUNTIME */", Path(__file__).with_name("tem_resume.js").read_text(encoding="utf-8"))
    page = page.replace("/* TEM_TRANSFER_RUNTIME */", Path(__file__).with_name("tem_transfer.js").read_text(encoding="utf-8"))
    page = page.replace("__TEM_OWNER__", escape(resume_owner, quote=True))
    return with_browser_support_notice(page)


@router.get("/tem", response_class=HTMLResponse, include_in_schema=False)
def tem_page(request: Request) -> HTMLResponse:
    owner = getattr(getattr(request.state, "auth_context", None), "user_id", None) or "anonymous"
    return HTMLResponse(build_ahn_page(str(owner)))


@router.post("/api/v1/tem/upload-sessions", response_class=JSONResponse, tags=["tem"])
def create_tem_upload_session(request: Request, manifest: UploadManifest | None = Body(default=None)) -> JSONResponse:
    _cleanup_old_jobs()
    expected = validate_manifest(manifest) if manifest else None
    session = _create_upload_session()
    session.owner_user_id = getattr(getattr(request.state, "auth_context", None), "user_id", None)
    session.expected_files = expected
    logger.info("TEM 청크 업로드 세션 생성 (upload_id=%s)", session.upload_id)
    return JSONResponse(_upload_session_payload(session))


@router.get("/api/v1/tem/upload-worker.js", include_in_schema=False)
def tem_upload_worker() -> FileResponse:
    return FileResponse(Path(__file__).with_name("tem_upload_worker.js"), media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})


@router.get("/api/v1/tem/upload-sessions/{upload_id}", response_class=JSONResponse, tags=["tem"])
def get_tem_upload_session(request: Request, upload_id: str) -> JSONResponse:
    _cleanup_old_jobs()
    existing_job = _job_for_completed_upload(upload_id)
    if existing_job is not None:
        from .tem_report_editor import authorized_job
        return JSONResponse({"job": _job_payload(authorized_job(request, existing_job.job_id, ready=False))})
    try:
        session = _get_upload_session(upload_id)
    except ApiException:
        # Completion can move the upload into the job registry between reads.
        existing_job = _job_for_completed_upload(upload_id)
        if existing_job is not None:
            from .tem_report_editor import authorized_job
            return JSONResponse({"job": _job_payload(authorized_job(request, existing_job.job_id, ready=False))})
        raise
    check_upload_owner(request, session)
    if not session.lock.acquire(blocking=False):
        raise ApiException(409, "TEM_UPLOAD_BUSY", "업로드 조각을 저장 중입니다. 잠시 후 다시 확인하세요.", retryable=True)
    try:
        if session.finalized:
            from .tem_report_editor import authorized_job
            job = _job_for_completed_upload(upload_id)
            if job is not None:
                return JSONResponse({"job": _job_payload(authorized_job(request, job.job_id, ready=False))})
        return JSONResponse(resume_payload(session))
    finally:
        session.lock.release()


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
    check_upload_owner(request, session)
    request.state.error_project = "TEM"
    request.state.error_source_paths = [session.input_root]
    started = time.perf_counter()
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
            # Excludes multipart receive/parsing and proxy buffering; not wire time.
            "chunkProcessingMs": round((time.perf_counter() - started) * 1000, 3),
        }
    )
    return JSONResponse(payload)


@router.post("/api/v1/tem/upload-sessions/{upload_id}/complete", response_class=JSONResponse, tags=["tem"])
def complete_tem_upload_session(request: Request, upload_id: str, defer_validation: bool = Query(False)) -> JSONResponse:
    _cleanup_old_jobs()
    existing_job = _job_for_completed_upload(upload_id)
    if existing_job is not None:
        from .tem_report_editor import authorized_job
        authorized_job(request, existing_job.job_id, ready=False)
        set_usage_context(
            request,
            project="TEM",
            job_id=existing_job.job_id,
            experiment_code="TEM",
        )
        return JSONResponse(_job_payload(existing_job))
    try:
        session = _get_upload_session(upload_id)
    except ApiException:
        # Another complete request may have moved the session between lookups.
        existing_job = _job_for_completed_upload(upload_id)
        if existing_job is not None:
            from .tem_report_editor import authorized_job
            authorized_job(request, existing_job.job_id, ready=False)
            return JSONResponse(_job_payload(existing_job))
        raise
    check_upload_owner(request, session)
    with session.lock:
        existing_job = _job_for_completed_upload(upload_id)
        if existing_job is not None:
            return JSONResponse(_job_payload(existing_job))
        return _complete_tem_upload_session(request, session, defer_validation=defer_validation)


def _complete_tem_upload_session(request: Request, session: AhnUploadSession, *, defer_validation: bool) -> JSONResponse:
    upload_id = session.upload_id
    request.state.error_project = "TEM"
    request.state.error_source_paths = [session.input_root]
    completed_files = [state for state in session.files.values() if state.completed]
    incomplete_files = [state.relative_path for state in session.files.values() if not state.completed]
    if session.expected_files is not None:
        incomplete_files.extend(key for key in session.expected_files if key not in session.files)
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
    if not defer_validation:
        with _ahn_processing_slots:
            _validate_ahn_upload_files(session.input_root)
    job = _submit_ahn_job(
        session.input_root,
        session.work_dir,
        app_error_archive(request.app),
        getattr(request.app.state, "settings", None),
        getattr(request.app.state, "database", None),
        app_usage_archive(request.app),
        request_usage_client_context(request),
        validate_upload=defer_validation,
        owner_user_id=getattr(getattr(request.state, "auth_context", None), "user_id", None),
    )
    session.finalized = True
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
    defer_validation: bool = Query(False),
) -> JSONResponse:
    set_usage_context(request, project="TEM", experiment_code="TEM")
    _cleanup_old_jobs()
    work_dir = Path(tempfile.mkdtemp(prefix="rist-ahn-web-"))
    upload_root = work_dir / "input"
    request.state.error_project = "TEM"
    request.state.error_source_paths = [upload_root]
    try:
        await _save_ahn_uploads(files, upload_root)
        if not defer_validation:
            def validate():
                with _ahn_processing_slots:
                    _validate_ahn_upload_files(upload_root)
            await run_in_threadpool(validate)
        input_root = _find_ahn_input_root(upload_root)
        job = _submit_ahn_job(
            input_root,
            work_dir,
            app_error_archive(request.app),
            getattr(request.app.state, "settings", None),
            getattr(request.app.state, "database", None),
            app_usage_archive(request.app),
            request_usage_client_context(request),
            validate_upload=defer_validation,
            owner_user_id=getattr(getattr(request.state, "auth_context", None), "user_id", None),
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
            getattr(request.app.state, "settings", None),
            getattr(request.app.state, "database", None),
            app_usage_archive(request.app),
            request_usage_client_context(request),
            owner_user_id=getattr(getattr(request.state, "auth_context", None), "user_id", None),
        )
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    logger.info("TEM 예제 웹 보고서 작업 시작 (job_id=%s)", job.job_id)
    set_usage_context(request, job_id=job.job_id)
    return JSONResponse(_job_payload(job))


@router.get("/api/v1/tem/report/jobs/{job_id}", response_class=JSONResponse, tags=["tem"])
def get_tem_report_job(request: Request, job_id: str) -> JSONResponse:
    from .tem_report_editor import authorized_job
    job = authorized_job(request, job_id, ready=False)
    return JSONResponse(_job_payload(job))


@router.get("/api/v1/tem/report/jobs/{job_id}/download/{kind}", tags=["tem"])
def download_tem_report(request: Request, job_id: str, kind: str) -> FileResponse:
    from .tem_report_editor import authorized_job
    job = authorized_job(request, job_id, ready=False)
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


@router.post("/api/v1/tem/report/jobs/{job_id}/send", tags=["tem"])
def send_tem_report_job(
    request: Request,
    job_id: str,
    payload: PreviewReportSendRequest,
) -> dict[str, Any]:
    from .tem_report_editor import authorized_job
    job = authorized_job(request, job_id)
    if job.review_error or not (job.output_dir / "tem-report.pdf").is_file():
        raise ApiException(409, "TEM_REVIEW_REQUIRED", "보고서 미리보기를 생성·확인한 뒤 전송하세요.")
    effective_payload = authenticated_transfer_payload(request, payload, "TEM")
    set_usage_context(
        request,
        project="TEM",
        job_id=job_id,
        request_number=effective_payload.request_number,
        experiment_code=effective_payload.experiment_code,
        equipment_code=effective_payload.equipment_code,
        operator_id=effective_payload.operator_id,
    )
    try:
        return send_preview_report_package(
            settings=getattr(request.app.state, "settings", None),
            database=getattr(request.app.state, "database", None),
            job=job,
            payload=effective_payload,
        )
    except FileNotFoundError as exc:
        raise ApiException(410, "TEM_REPORT_PACKAGE_EXPIRED", str(exc)) from exc
    except ValueError as exc:
        raise ApiException(409, "TEM_REPORT_NOT_READY", str(exc)) from exc
    except ReportQueueError as exc:
        raise ApiException(
            503 if exc.retryable else 500,
            exc.code,
            str(exc),
            retryable=exc.retryable,
        ) from exc


from .tem_report_editor import install_tem_editor
install_tem_editor(router)


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

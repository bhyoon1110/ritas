"""Reusable chunked upload session helpers for browser workspaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shutil
import tempfile
import time
from threading import Lock
from uuid import uuid4

from fastapi import UploadFile

from .errors import ApiException


DEFAULT_SESSION_TTL_SECONDS = 2 * 60 * 60
DEFAULT_READ_CHUNK_BYTES = 1024 * 1024


@dataclass
class UploadFileState:
    relative_path: str
    stored_path: str
    temp_path: str
    total_size: int
    uploaded_bytes: int = 0
    completed: bool = False


@dataclass
class UploadSession:
    upload_id: str
    work_dir: Path
    input_root: Path
    created_at: float
    updated_at: float
    files: dict[str, UploadFileState] = field(default_factory=dict)


def safe_relative_path(value: str | None, fallback: str) -> Path:
    raw = str(value or fallback).replace("\\", "/").strip("/")
    parts = [part for part in raw.split("/") if part and part not in {".", ".."}]
    if not parts:
        parts = [fallback]
    clean_parts = []
    for part in parts:
        clean = "".join(ch for ch in part if ch not in '\0\r\n')
        clean = clean.strip() or fallback
        clean_parts.append(clean)
    return Path(*clean_parts)


def unique_path(path: Path) -> Path:
    candidate = path
    index = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        index += 1
    return candidate


class ChunkUploadStore:
    def __init__(
        self,
        *,
        code_prefix: str,
        temp_prefix: str,
        allowed_extensions: set[str],
        max_file_bytes: int,
        max_total_bytes: int,
        allow_unknown_extensions: bool = False,
        session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
        read_chunk_bytes: int = DEFAULT_READ_CHUNK_BYTES,
    ) -> None:
        self.code_prefix = code_prefix
        self.temp_prefix = temp_prefix
        self.allowed_extensions = {item.lower() for item in allowed_extensions}
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes
        self.allow_unknown_extensions = allow_unknown_extensions
        self.session_ttl_seconds = session_ttl_seconds
        self.read_chunk_bytes = read_chunk_bytes
        self._sessions: dict[str, UploadSession] = {}
        self._completed_refs: dict[str, str] = {}
        self._lock = Lock()

    def cleanup(self, *, keep_completed_refs: set[str] | None = None) -> None:
        now = time.time()
        with self._lock:
            expired = [
                upload_id
                for upload_id, session in self._sessions.items()
                if now - session.updated_at > self.session_ttl_seconds
            ]
            sessions = [self._sessions.pop(upload_id, None) for upload_id in expired]
            if keep_completed_refs is not None:
                for upload_id, ref in list(self._completed_refs.items()):
                    if ref not in keep_completed_refs:
                        self._completed_refs.pop(upload_id, None)
        for session in sessions:
            if session is not None:
                shutil.rmtree(session.work_dir, ignore_errors=True)

    def create(self) -> UploadSession:
        work_dir = Path(tempfile.mkdtemp(prefix=self.temp_prefix))
        input_root = work_dir / "input"
        input_root.mkdir(parents=True, exist_ok=True)
        upload_id = uuid4().hex
        now = time.time()
        session = UploadSession(
            upload_id=upload_id,
            work_dir=work_dir,
            input_root=input_root,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._sessions[upload_id] = session
        return session

    def get(self, upload_id: str) -> UploadSession:
        with self._lock:
            session = self._sessions.get(upload_id)
            if session is not None:
                session.updated_at = time.time()
        if session is None:
            raise ApiException(
                404,
                f"{self.code_prefix}_UPLOAD_SESSION_NOT_FOUND",
                "업로드 세션을 찾을 수 없습니다. 파일을 다시 선택해 업로드하세요.",
            )
        return session

    def pop(self, upload_id: str) -> UploadSession | None:
        with self._lock:
            return self._sessions.pop(upload_id, None)

    def remember_completed_ref(self, upload_id: str, ref: str) -> None:
        with self._lock:
            self._completed_refs[upload_id] = ref

    def completed_ref(self, upload_id: str) -> str | None:
        with self._lock:
            return self._completed_refs.get(upload_id)

    def expected_total(self, session: UploadSession) -> int:
        return sum(int(file_state.total_size or 0) for file_state in session.files.values())

    def payload(self, session: UploadSession) -> dict:
        completed_files = [state for state in session.files.values() if state.completed]
        uploaded_bytes = sum(
            int(state.total_size if state.completed else state.uploaded_bytes)
            for state in session.files.values()
        )
        return {
            "uploadId": session.upload_id,
            "fileCount": len(session.files),
            "completedFileCount": len(completed_files),
            "uploadedBytes": uploaded_bytes,
            "totalBytes": self.expected_total(session),
        }

    def completed_files(self, session: UploadSession) -> list[UploadFileState]:
        return [state for state in session.files.values() if state.completed]

    def incomplete_files(self, session: UploadSession) -> list[str]:
        return [state.relative_path for state in session.files.values() if not state.completed]

    async def write_chunk(
        self,
        session: UploadSession,
        *,
        relative_path: str,
        offset: int,
        total_size: int,
        chunk_index: int,
        chunk_count: int,
        upload: UploadFile,
    ) -> UploadFileState:
        relative = safe_relative_path(relative_path, f"upload-{chunk_index}")
        suffix = relative.suffix.lower()
        if not suffix and relative.name.startswith("."):
            raise ApiException(
                400,
                f"{self.code_prefix}_INVALID_FILE_TYPE",
                f"숨김 파일은 업로드하지 않습니다: {relative.as_posix()}",
            )
        if not self.allow_unknown_extensions and suffix not in self.allowed_extensions:
            allowed = ", ".join(sorted(self.allowed_extensions))
            raise ApiException(
                400,
                f"{self.code_prefix}_INVALID_FILE_TYPE",
                f"지원하지 않는 파일입니다: {relative.as_posix()}. 허용 형식: {allowed}",
            )
        if total_size <= 0:
            raise ApiException(
                400,
                f"{self.code_prefix}_EMPTY_FILE",
                f"빈 파일은 업로드하지 않습니다: {relative.name}",
            )
        if total_size > self.max_file_bytes:
            raise ApiException(
                413,
                f"{self.code_prefix}_FILE_TOO_LARGE",
                f"{relative.name} 파일이 너무 큽니다.",
            )
        if offset < 0 or offset > total_size:
            raise ApiException(
                400,
                f"{self.code_prefix}_INVALID_UPLOAD_OFFSET",
                "업로드 offset 값이 올바르지 않습니다.",
            )
        if chunk_index < 0 or chunk_count <= 0 or chunk_index >= chunk_count:
            raise ApiException(
                400,
                f"{self.code_prefix}_INVALID_UPLOAD_CHUNK",
                "업로드 chunk 값이 올바르지 않습니다.",
            )

        file_key = relative.as_posix()
        with self._lock:
            file_state = session.files.get(file_key)
            if file_state is None:
                expected_total = self.expected_total(session) + total_size
                if expected_total > self.max_total_bytes:
                    raise ApiException(
                        413,
                        f"{self.code_prefix}_UPLOAD_TOO_LARGE",
                        "한 번에 업로드하는 파일의 총 크기가 너무 큽니다.",
                    )
                destination = unique_path(session.input_root / relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                temp_path = destination.with_name(destination.name + ".part")
                file_state = UploadFileState(
                    relative_path=file_key,
                    stored_path=destination.relative_to(session.input_root).as_posix(),
                    temp_path=temp_path.relative_to(session.input_root).as_posix(),
                    total_size=total_size,
                )
                session.files[file_key] = file_state
            elif file_state.total_size != total_size:
                raise ApiException(
                    400,
                    f"{self.code_prefix}_UPLOAD_SIZE_CHANGED",
                    "업로드 중 파일 크기가 변경되었습니다.",
                )
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
                f"{self.code_prefix}_UPLOAD_OFFSET_MISMATCH",
                "이전 업로드 조각이 아직 서버에 없습니다. 잠시 후 다시 시도하세요.",
                details={"expectedOffset": current_size, "receivedOffset": offset},
            )

        written = 0
        try:
            temp_path.parent.mkdir(parents=True, exist_ok=True)
            with temp_path.open("r+b" if temp_path.exists() else "wb") as output:
                output.seek(offset)
                while True:
                    chunk = await upload.read(self.read_chunk_bytes)
                    if not chunk:
                        break
                    written += len(chunk)
                    if offset + written > total_size:
                        raise ApiException(
                            400,
                            f"{self.code_prefix}_UPLOAD_CHUNK_TOO_LARGE",
                            "업로드 조각 크기가 파일 크기를 초과했습니다.",
                        )
                    output.write(chunk)
                output.truncate(offset + written)
        finally:
            await upload.close()

        file_state.uploaded_bytes = min(total_size, offset + written)
        if file_state.uploaded_bytes == total_size:
            if destination.exists():
                destination.unlink()
            temp_path.replace(destination)
            file_state.completed = True
        with self._lock:
            session.updated_at = time.time()
        return file_state


def read_completed_upload_files(session: UploadSession) -> list[tuple[str, bytes]]:
    uploaded: list[tuple[str, bytes]] = []
    for state in session.files.values():
        if not state.completed:
            continue
        path = session.input_root / state.stored_path
        uploaded.append((Path(state.relative_path).name, path.read_bytes()))
    return uploaded

"""TEM-only manifest and owner checks for browser upload recovery.

Sessions intentionally share the existing in-process lifetime; server restart
is reported as expired, never silently merged into a new/different upload.
"""
from fastapi import Request
from pydantic import BaseModel, Field

from .errors import ApiException


class UploadSourceFile(BaseModel):
    path: str = Field(min_length=1, max_length=1500)
    size: int = Field(gt=0, le=250 * 1024 * 1024)
    lastModified: int = Field(default=0, ge=0)


class UploadManifest(BaseModel):
    files: list[UploadSourceFile] = Field(min_length=1, max_length=5000)


def validate_manifest(manifest):
    from . import ahn_web as ahn
    if len(manifest.files) > ahn.MAX_AHN_UPLOAD_FILES or sum(f.size for f in manifest.files) > ahn.MAX_AHN_UPLOAD_TOTAL_BYTES:
        raise ApiException(413, "TEM_UPLOAD_TOO_LARGE", "TEM 업로드 파일 수 또는 총 용량 한도를 초과했습니다.")
    result = {}
    for source in manifest.files:
        key = ahn._safe_relative_path(source.path, "upload").as_posix()
        if key in result:
            raise ApiException(400, "TEM_UPLOAD_PATH_COLLISION", "서버에서 동일한 경로가 되는 파일이 있습니다. 이름을 구분한 뒤 다시 선택하세요.")
        result[key] = source.model_dump()
    return result


def check_upload_owner(request: Request, session):
    settings = getattr(request.app.state, "settings", None)
    if settings and getattr(settings, "auth_enabled", False):
        from .auth import require_context
        context = require_context(request)
        if not context.is_admin and ("TEM" not in context.projects or session.owner_user_id != context.user_id):
            raise ApiException(403, "TEM_UPLOAD_FORBIDDEN", "업로드 작성자 또는 관리자만 이 작업을 이어받을 수 있습니다.")


def resume_payload(session):
    from . import ahn_web as ahn
    payload = ahn._upload_session_payload(session)
    rows = []
    for key, source in (session.expected_files or {}).items():
        state = session.files.get(key)
        rows.append(dict(source, uploadedBytes=state.uploaded_bytes if state else 0,
                         chunks=list(state.chunks) if state else []))
    payload.update(files=rows, resumable=session.expected_files is not None,
                   expiresAt=session.updated_at + ahn.AHN_UPLOAD_SESSION_TTL_SECONDS)
    return payload

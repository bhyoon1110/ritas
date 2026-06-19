from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from .errors import ApiException


SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9._-]+")


def safe_component(value: str) -> str:
    cleaned = SAFE_COMPONENT.sub("_", value.strip()).strip("._")
    return cleaned or "unknown"


def validate_relative_path(value: str) -> str:
    if not value or "\x00" in value or "\\" in value:
        raise ApiException(400, "INVALID_RELATIVE_PATH", "상대 경로가 올바르지 않습니다.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ApiException(400, "INVALID_RELATIVE_PATH", "상대 경로가 올바르지 않습니다.")
    if path.parts and ":" in path.parts[0]:
        raise ApiException(400, "INVALID_RELATIVE_PATH", "드라이브 경로는 허용되지 않습니다.")
    return path.as_posix()


def resolve_under(root: Path, relative_path: str) -> Path:
    target = (root / relative_path).resolve()
    root_resolved = root.resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise ApiException(400, "INVALID_RELATIVE_PATH", "허용된 저장 경로를 벗어났습니다.")
    return target


def stream_to_temp(
    source: BinaryIO,
    temp_path: Path,
    max_bytes: int,
) -> tuple[int, str]:
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    try:
        with temp_path.open("wb") as destination:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise ApiException(
                        413,
                        "FILE_TOO_LARGE",
                        "파일이 서버의 최대 허용 크기를 초과했습니다.",
                    )
                digest.update(chunk)
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return size, digest.hexdigest()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temp_path.replace(path)

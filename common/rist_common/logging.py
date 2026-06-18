"""RIST 공통 로깅 모듈.

각 프로젝트(edge_api_server, lim, sune, ahn 등)는 다음과 같이 사용한다.

    from rist_common.logging import get_logger

    logger = get_logger(__name__)
    logger.info("작업 시작")

`get_logger`는 최초 호출 시 환경 변수를 읽어 핸들러를 한 번만 구성하므로,
프로젝트마다 별도의 설정 코드를 작성할 필요가 없다. 콘솔 출력은 항상 켜져
있고, 환경 변수로 파일 출력과 로그 레벨, 포맷(JSON/텍스트)을 제어한다.

환경 변수
---------
- RIST_LOG_LEVEL: 로그 레벨 (기본 INFO). DEBUG/INFO/WARNING/ERROR/CRITICAL
- RIST_LOG_FORMAT: ``text``(기본) 또는 ``json``
- RIST_LOG_FILE: 로그 파일 경로. 지정하면 회전(rotating) 파일 핸들러 추가
- RIST_LOG_DIR: 디렉터리만 지정. ``<RIST_LOG_DIR>/rist.log`` 로 기록
- RIST_LOG_MAX_BYTES: 회전 파일 한 개의 최대 크기 (기본 10MB)
- RIST_LOG_BACKUP_COUNT: 보관할 회전 파일 개수 (기본 5)
"""

from __future__ import annotations

import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

__all__ = ["get_logger", "configure_logging"]

ROOT_LOGGER_NAME = "rist"
DEFAULT_LEVEL = "INFO"
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5
_TEXT_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"

_configured = False


class _JsonFormatter(logging.Formatter):
    """로그 레코드를 한 줄 JSON으로 직렬화한다."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, _DATE_FORMAT),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # extra=로 전달한 사용자 필드를 포함한다.
        standard = logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
        for key, value in record.__dict__.items():
            if key not in standard and key not in payload:
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


def _resolve_level(level: str | int | None) -> int:
    if level is None:
        level = os.getenv("RIST_LOG_LEVEL", DEFAULT_LEVEL)
    if isinstance(level, int):
        return level
    resolved = logging.getLevelName(str(level).strip().upper())
    if isinstance(resolved, int):
        return resolved
    return logging.INFO


def _build_formatter(fmt: str | None) -> logging.Formatter:
    selected = (fmt or os.getenv("RIST_LOG_FORMAT", "text")).strip().lower()
    if selected == "json":
        return _JsonFormatter()
    return logging.Formatter(_TEXT_FORMAT, datefmt=_DATE_FORMAT)


def _resolve_log_file(log_file: str | os.PathLike[str] | None) -> Path | None:
    if log_file is not None:
        return Path(log_file).expanduser()
    env_file = os.getenv("RIST_LOG_FILE")
    if env_file:
        return Path(env_file).expanduser()
    env_dir = os.getenv("RIST_LOG_DIR")
    if env_dir:
        return Path(env_dir).expanduser() / "rist.log"
    return None


def configure_logging(
    *,
    level: str | int | None = None,
    fmt: str | None = None,
    log_file: str | os.PathLike[str] | None = None,
    force: bool = False,
) -> logging.Logger:
    """공통 ``rist`` 로거를 구성하고 반환한다.

    여러 번 호출해도 핸들러가 중복 추가되지 않는다(force=True 이면 재구성).
    명시 인자가 없으면 환경 변수를 사용한다.
    """
    global _configured
    root = logging.getLogger(ROOT_LOGGER_NAME)

    if _configured and not force:
        return root

    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    resolved_level = _resolve_level(level)
    formatter = _build_formatter(fmt)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    file_path = _resolve_log_file(log_file)
    if file_path is not None:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        max_bytes = _int_env("RIST_LOG_MAX_BYTES", DEFAULT_MAX_BYTES)
        backup_count = _int_env("RIST_LOG_BACKUP_COUNT", DEFAULT_BACKUP_COUNT)
        file_handler = RotatingFileHandler(
            file_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    root.setLevel(resolved_level)
    # 다른 라이브러리의 root 로거로 전파되어 메시지가 중복되지 않도록 한다.
    root.propagate = False
    _configured = True
    return root


def _int_env(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def get_logger(name: str | None = None) -> logging.Logger:
    """프로젝트에서 사용할 로거를 반환한다.

    최초 호출 시 공통 핸들러를 한 번 구성한다. ``name`` 은 보통 ``__name__`` 을
    넘기며, ``rist`` 네임스페이스 하위 로거로 묶여 동일한 핸들러/레벨을 공유한다.
    """
    if not _configured:
        configure_logging()
    if not name or name == ROOT_LOGGER_NAME:
        return logging.getLogger(ROOT_LOGGER_NAME)
    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")

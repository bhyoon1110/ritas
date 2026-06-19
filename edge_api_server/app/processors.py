"""장비별 분석 processor 실행 훅."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from rist_common import get_logger

from .config import Settings
from .storage import atomic_write_json

logger = get_logger(__name__)

_SKIP_JSON = {"llm-request.json", "llm-response.json", "report.json"}
_TAIL_CHARS = 8000


def run_processor_if_needed(
    settings: Settings,
    job: dict[str, Any],
    job_root: Path,
) -> bool:
    """분석 JSON이 없고 command가 설정된 경우 processor를 실행한다."""
    processed_dir = job_root / "processed"
    if _has_analysis_json(processed_dir):
        return False

    experiment_code = str(job["experiment_code"])
    key = _processor_key(experiment_code)
    command_template = os.getenv(f"RIST_PROCESSOR_COMMAND_{key}", "").strip()
    if not command_template:
        return False

    input_dir = job_root / "input"
    report_dir = job_root / "report"
    logs_dir = job_root / "logs"
    processed_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    command = shlex.split(
        _render_command_template(
            command_template,
            {
                "job_root": str(job_root),
                "input_dir": str(input_dir),
                "processed_dir": str(processed_dir),
                "report_dir": str(report_dir),
                "experiment_code": experiment_code,
                "job_id": str(job["job_id"]),
            },
        )
    )
    if not command:
        return False

    logger.info(
        "분석 processor 실행 (job_id=%s, experiment=%s)",
        job["job_id"],
        experiment_code,
    )
    log_path = logs_dir / f"processor-{key.lower()}.json"
    try:
        completed = subprocess.run(
            command,
            cwd=job_root,
            timeout=settings.processor_timeout_seconds,
            check=False,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        atomic_write_json(
            log_path,
            {
                "command": command,
                "timeoutSeconds": settings.processor_timeout_seconds,
                "stdoutTail": _tail(exc.stdout),
                "stderrTail": _tail(exc.stderr),
                "timedOut": True,
            },
        )
        raise RuntimeError(
            f"{experiment_code} processor가 제한 시간 내에 종료되지 않았습니다."
        ) from exc

    atomic_write_json(
        log_path,
        {
            "command": command,
            "returnCode": completed.returncode,
            "stdoutTail": _tail(completed.stdout),
            "stderrTail": _tail(completed.stderr),
            "timedOut": False,
        },
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{experiment_code} processor 실행 실패(returnCode={completed.returncode})"
        )
    return True


def _processor_key(experiment_code: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", experiment_code.upper()).strip("_")


def _render_command_template(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered


def _has_analysis_json(processed_dir: Path) -> bool:
    return processed_dir.exists() and any(
        path.name not in _SKIP_JSON for path in processed_dir.rglob("*.json")
    )


def _tail(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value[-_TAIL_CHARS:]

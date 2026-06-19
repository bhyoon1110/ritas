from __future__ import annotations

import json
import sys
from pathlib import Path

from app.config import Settings
from app.processors import _render_command_template, run_processor_if_needed


def _settings(tmp_path: Path) -> Settings:
    return Settings(storage_root=tmp_path, processor_timeout_seconds=10)


def _job() -> dict:
    return {
        "job_id": "job-123",
        "experiment_code": "FT-IR",
        "root_relative_path": "jobs/job-123",
    }


def test_processor_hook_runs_configured_command(monkeypatch, tmp_path: Path) -> None:
    job_root = tmp_path / "jobs" / "job-123"
    job_root.mkdir(parents=True)
    command = (
        f"{sys.executable} -c "
        "\"import json, pathlib; "
        "p=pathlib.Path('{processed_dir}')/'analysis-result.json'; "
        "p.write_text(json.dumps({'sample':'S1'}), encoding='utf-8')\""
    )
    monkeypatch.setenv("RIST_PROCESSOR_COMMAND_FT_IR", command)

    assert run_processor_if_needed(_settings(tmp_path), _job(), job_root) is True
    payload = json.loads(
        (job_root / "processed" / "analysis-result.json").read_text(
            encoding="utf-8"
        )
    )

    assert payload == {"sample": "S1"}
    assert (job_root / "logs" / "processor-ft_ir.json").exists()


def test_processor_hook_skips_when_analysis_exists(
    monkeypatch,
    tmp_path: Path,
) -> None:
    job_root = tmp_path / "jobs" / "job-123"
    analysis_path = job_root / "processed" / "analysis-result.json"
    analysis_path.parent.mkdir(parents=True)
    analysis_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv(
        "RIST_PROCESSOR_COMMAND_FT_IR",
        f"{sys.executable} -c \"raise SystemExit(1)\"",
    )

    assert run_processor_if_needed(_settings(tmp_path), _job(), job_root) is False


def test_processor_template_keeps_placeholder_value_in_one_argument() -> None:
    command = _render_command_template(
        "processor --job {job_id} --experiment {experiment_code}",
        {
            "job_id": "job 1 --flag",
            "experiment_code": "FT-IR",
        },
    )

    assert command == [
        "processor",
        "--job",
        "job 1 --flag",
        "--experiment",
        "FT-IR",
    ]

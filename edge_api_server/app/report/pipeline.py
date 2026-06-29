"""보고서 생성 오케스트레이션.

순서: processor 실행 -> 분석 JSON 로드 -> 규칙 기반 문서 작성 ->
(선택) LLM 슬롯 주석 -> 내부 JSON / 사용자용 보고서 렌더링 / 전달 ZIP 생성.
LLM 단계는 실패해도 규칙 기반 문안으로
보고서를 완성한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rist_common import get_logger

from ..config import Settings
from ..llm_client import LlmError, LocalLlmClient
from ..processors import run_processor_if_needed
from ..storage import atomic_write_json
from . import annotator
from .builders import AnalysisItem, get_builder
from .model import ReportDocument, ReportFigure
from .package import build_report_package
from .renderers import render_report_formats

logger = get_logger(__name__)

_SKIP_JSON = {"llm-request.json", "llm-response.json", "report.json"}
_FIGURE_SUFFIXES = {".png", ".jpg", ".jpeg"}


def load_analysis_results(processed_dir: Path) -> list[AnalysisItem]:
    if not processed_dir.exists():
        raise FileNotFoundError("processed 폴더를 찾을 수 없습니다.")
    candidates = sorted(
        path
        for path in processed_dir.rglob("*.json")
        if path.name not in _SKIP_JSON
    )
    if not candidates:
        raise FileNotFoundError(
            "구조화된 분석 결과 JSON이 없습니다. "
            "장비별 processor가 processed 폴더에 JSON을 생성해야 합니다."
        )
    results: list[AnalysisItem] = []
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FileNotFoundError(
                f"분석 결과 JSON을 읽을 수 없습니다: {path.name}"
            ) from exc
        results.append(
            {
                "relativePath": path.relative_to(processed_dir).as_posix(),
                "data": payload,
            }
        )
    return results


def _requested_report_options(job: dict[str, Any]) -> tuple[list[str], bool]:
    try:
        options = json.loads(job.get("report_options_json") or "{}")
    except json.JSONDecodeError:
        options = {}
    formats = options.get("reportFormats")
    if not isinstance(formats, list) or not formats:
        formats = [options.get("reportFormat") or "PPTX"]
    return [str(item).upper() for item in formats], bool(options.get("includeRawFiles"))


def _collect_figures(processed_dir: Path) -> list[ReportFigure]:
    figures: list[ReportFigure] = []
    if not processed_dir.exists():
        return figures
    for index, path in enumerate(
        sorted(
            item
            for item in processed_dir.rglob("*")
            if item.is_file() and item.suffix.lower() in _FIGURE_SUFFIXES
        ),
        start=1,
    ):
        figures.append(
            ReportFigure(
                figure_id=f"figure-{index}",
                title=path.stem.replace("_", " "),
                path=str(path),
                caption_slot="caption",
            )
        )
    return figures


def _write_email_body(document: ReportDocument, report_dir: Path) -> None:
    subject = document.auxiliary_texts.get("email_subject", "").strip()
    body = document.auxiliary_texts.get("email_body", "").strip()
    if not subject and not body:
        return
    lines = []
    if subject:
        lines.extend([f"# {subject}", ""])
    if body:
        lines.append(body)
    (report_dir / "email_body.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def generate_report(
    settings: Settings,
    job: dict[str, Any],
    *,
    llm_client: LocalLlmClient | None,
    generated_at: str,
) -> ReportDocument:
    job_root = settings.storage_root / job["root_relative_path"]
    processed_dir = job_root / "processed"
    report_dir = job_root / "report"
    logs_dir = job_root / "logs"

    run_processor_if_needed(settings, job, job_root)
    analysis = load_analysis_results(processed_dir)

    job_with_time = dict(job)
    job_with_time["_generated_at"] = generated_at

    builder = get_builder(job["experiment_code"])
    document = builder.build(job_with_time, analysis)
    document.figures = _collect_figures(processed_dir)

    spec = builder.llm_slots(job_with_time, analysis)
    if spec is not None:
        document.ensure_auxiliary_texts(spec.fallback)
    if spec is not None and llm_client is not None:
        try:
            slots = annotator.annotate(
                settings,
                llm_client,
                spec,
                processed_dir=processed_dir,
                logs_dir=logs_dir,
            )
            document.apply_llm_slots(slots)
            document.llm_used = True
        except LlmError as exc:
            document.llm_error = f"{exc.code}: {exc.message}"
            logger.warning(
                "LLM 슬롯 주석 실패 (job_id=%s, code=%s) — 규칙 기반 문안 사용",
                job["job_id"],
                exc.code,
            )
    elif spec is not None and llm_client is None:
        document.llm_error = "LLM 클라이언트가 비활성화되어 규칙 기반 문안을 사용했습니다."

    report_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(report_dir / "report.json", document.to_dict())
    (report_dir / "report.md").write_text(document.to_markdown(), encoding="utf-8")
    _write_email_body(document, report_dir)
    report_formats, include_raw_files = _requested_report_options(job)
    rendered_paths = render_report_formats(
        document,
        report_dir,
        report_formats,
        pdf_font_path=settings.pdf_font_path,
    )
    package_path = build_report_package(
        report_dir,
        job_root / "input",
        include_raw_files=include_raw_files,
    )
    logger.info(
        "보고서 생성 완료 (job_id=%s, llm_used=%s, rendered=%s, package=%s)",
        job["job_id"],
        document.llm_used,
        ", ".join(path.name for path in rendered_paths),
        package_path.name,
    )
    return document

from __future__ import annotations

import base64
import html
import json
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .report.builders import get_builder
from .llm_client import LlmError, LocalLlmClient
from .report import annotator
from .report.model import ReportFigure
from .report.package import build_report_package
from .report.renderers import render_report_formats
from .storage import atomic_write_json


@dataclass(frozen=True)
class RawSeries:
    label: str
    axis: list[float]
    values: list[float]


def parse_analysis_payload(analysis_json: str, figure_json: str | None = None) -> dict[str, Any]:
    payload = json.loads(analysis_json)
    if not isinstance(payload, dict):
        raise ValueError("analysis_json은 JSON 객체여야 합니다.")
    if figure_json:
        figure = json.loads(figure_json)
        if isinstance(figure, dict):
            payload["figure"] = figure
    return payload


def decode_figure_image(data_url: str) -> bytes:
    if not data_url.startswith("data:image/"):
        raise ValueError("figure_image는 data:image URL이어야 합니다.")
    try:
        _prefix, encoded = data_url.split(",", 1)
        return base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("figure_image를 디코딩할 수 없습니다.") from exc


def build_preview_report_package(
    *,
    experiment_code: str,
    analysis_payload: dict[str, Any],
    raw_series: list[RawSeries],
    figure_image: bytes,
    request_number: str = "WEB-PREVIEW",
    equipment_code: str = "WEB-PREVIEW",
    operator_id: str = "web-preview",
    settings: Any | None = None,
) -> tuple[Path, Path]:
    tmp_root = Path(tempfile.mkdtemp(prefix="rist-preview-report-"))
    job_root = tmp_root / "job"
    report_dir = job_root / "report"
    processed_dir = job_root / "processed"
    report_dir.mkdir(parents=True)
    processed_dir.mkdir(parents=True)

    analysis_path = processed_dir / "analysis-result.json"
    atomic_write_json(analysis_path, analysis_payload)
    image_path = processed_dir / "current_graph.png"
    image_path.write_bytes(figure_image)
    (report_dir / "current_graph.png").write_bytes(figure_image)

    job = {
        "job_id": "web-preview-report",
        "request_number": request_number,
        "experiment_code": experiment_code,
        "equipment_code": equipment_code,
        "operator_id": operator_id,
        "root_relative_path": "web-preview-report",
        "_generated_at": "",
    }
    builder = get_builder(experiment_code)
    document = builder.build(
        job,
        [{"relativePath": analysis_path.name, "data": analysis_payload}],
    )
    spec = builder.llm_slots(job, [{"relativePath": analysis_path.name, "data": analysis_payload}])
    if spec is not None:
        document.ensure_auxiliary_texts(spec.fallback)
    if spec is not None and settings is not None:
        llm_client = LocalLlmClient(
            settings.llm_base_url,
            settings.llm_model,
            settings.llm_timeout_seconds,
            settings.llm_temperature,
            settings.llm_max_tokens,
            settings.llm_validate_model,
        )
        try:
            slots = annotator.annotate(
                settings,
                llm_client,
                spec,
                processed_dir=processed_dir,
                logs_dir=job_root / "logs",
            )
            document.apply_llm_slots(slots)
            document.llm_used = True
        except LlmError as exc:
            document.llm_error = f"{exc.code}: {exc.message}"
        finally:
            llm_client.close()
    document.figures = [
        ReportFigure(
            figure_id="current-graph",
            title="현재 그래프 화면",
            path=str(image_path),
        )
    ]

    atomic_write_json(report_dir / "report.json", document.to_dict())
    (report_dir / "report.md").write_text(document.to_markdown(), encoding="utf-8")
    _write_email_body(document, report_dir)
    write_raw_data_xlsx(report_dir / "raw_data.xlsx", raw_series)
    render_report_formats(document, report_dir, ["PPTX", "HTML"])
    package = build_report_package(report_dir, job_root / "input", include_raw_files=False)
    return tmp_root, package


def cleanup_preview_report(tmp_root: Path) -> None:
    shutil.rmtree(tmp_root, ignore_errors=True)


def _write_email_body(document: Any, report_dir: Path) -> None:
    subject = document.auxiliary_texts.get("email_subject", "").strip()
    body = document.auxiliary_texts.get("email_body", "").strip()
    if not subject and not body:
        return
    lines: list[str] = []
    if subject:
        lines.extend([f"# {subject}", ""])
    if body:
        lines.append(body)
    (report_dir / "email_body.md").write_text(
        "\n".join(lines).rstrip() + "\n",
        encoding="utf-8",
    )


def write_raw_data_xlsx(path: Path, series: list[RawSeries]) -> None:
    axis_values = sorted(
        {
            round(float(value), 8)
            for item in series
            for value in item.axis
        }
    )
    lookups = []
    for item in series:
        lookups.append(
            {
                round(float(x), 8): y
                for x, y in zip(item.axis, item.values)
            }
        )
    rows: list[list[Any]] = [["Axis", *[item.label for item in series]]]
    for axis in axis_values:
        rows.append([axis, *[lookup.get(axis, "") for lookup in lookups]])
    _write_xlsx(path, rows)


def _write_xlsx(path: Path, rows: list[list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _xlsx_content_types())
        archive.writestr("_rels/.rels", _xlsx_root_rels())
        archive.writestr("xl/workbook.xml", _xlsx_workbook())
        archive.writestr("xl/_rels/workbook.xml.rels", _xlsx_workbook_rels())
        archive.writestr("xl/worksheets/sheet1.xml", _xlsx_sheet(rows))


def _xlsx_content_types() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""


def _xlsx_root_rels() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""


def _xlsx_workbook() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Raw Data" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""


def _xlsx_workbook_rels() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""


def _xlsx_sheet(rows: list[list[Any]]) -> str:
    body = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for col_index, value in enumerate(row, start=1):
            if value == "":
                continue
            cells.append(_xlsx_cell(row_index, col_index, value))
        body.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(body)}</sheetData>'
        "</worksheet>"
    )


def _xlsx_cell(row_index: int, col_index: int, value: Any) -> str:
    ref = f"{_xlsx_col(col_index)}{row_index}"
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"><v>{float(value):.12g}</v></c>'
    text = html.escape(str(value), quote=False)
    return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'


def _xlsx_col(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters

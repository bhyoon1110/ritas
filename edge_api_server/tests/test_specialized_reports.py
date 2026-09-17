from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import zipfile

import pytest
from PIL import Image

from app import xrd_web
from app.config import Settings
from app.errors import ApiException
from app.experiment_routing import (
    mapped_analysis_type_for_job,
    parse_analysis_type_map,
    resolve_analysis_type,
)
from app.specialized_reports import generate_specialized_report


def _job(
    root: str,
    *,
    experiment_code: str,
    equipment_code: str,
    report_format: str,
) -> dict:
    return {
        "job_id": "job-1",
        "root_relative_path": root,
        "request_number": "REQ-1",
        "experiment_code": experiment_code,
        "equipment_code": equipment_code,
        "operator_id": "operator-1",
        "report_options_json": json.dumps(
            {"reportFormats": [report_format], "includeRawFiles": False}
        ),
    }


def _tiny_tiff() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (32, 24), "white").save(stream, format="TIFF")
    return stream.getvalue()


def test_analysis_type_resolves_lims_code_from_config_or_equipment() -> None:
    configured = parse_analysis_type_map("A23141=XRD,B54123:TEM")

    assert resolve_analysis_type(
        experiment_code="A23141",
        equipment_code="AX-01",
        configured_map=configured,
    ) == "XRD"
    assert resolve_analysis_type(
        experiment_code="B54123",
        equipment_code="TEM-EDGE-01",
    ) == "TEM"
    assert mapped_analysis_type_for_job(
        {
            "experiment_code": "A23141",
            "equipment_code": "AX-01",
        },
        Settings(storage_root=Path("."), analysis_type_map=configured),
    ) == "XRD"
    assert mapped_analysis_type_for_job(
        {"experiment_code": "XRD", "equipment_code": "XRD-01"},
        Settings(storage_root=Path("."), analysis_type_map=configured),
    ) is None


def test_xrd_raw_bundle_generates_portable_html_package(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(storage_root=tmp_path)
    job = _job(
        "jobs/xrd-1",
        experiment_code="A23141",
        equipment_code="XRD-01",
        report_format="HTML",
    )
    input_dir = tmp_path / job["root_relative_path"] / "input"
    (input_dir / "raw").mkdir(parents=True)
    (input_dir / "raw" / "sample.txt").write_text(
        "10 1\n20 3\n30 2\n",
        encoding="utf-8",
    )
    xrd_web._write_synthetic_icdd_pdf_dir(input_dir / "ICDD Card")
    monkeypatch.setattr(xrd_web, "_xrd_comment_provider", lambda *_args, **_kwargs: None)

    result = generate_specialized_report(settings, job)

    assert result is not None
    assert result.analysis_type == "XRD"
    assert result.primary_path.name == "report.html"
    html = result.primary_path.read_text(encoding="utf-8")
    assert "sample Report" in html
    assert "상 동정 (Phase Identification) 결과" in html
    with zipfile.ZipFile(result.package_path) as archive:
        assert {"report.html", "report.md"} <= set(archive.namelist())


def test_tem_raw_bundle_generates_template_pptx_package(tmp_path: Path) -> None:
    pytest.importorskip("pptx")
    settings = Settings(storage_root=tmp_path)
    job = _job(
        "jobs/tem-1",
        experiment_code="B54123",
        equipment_code="TEM-01",
        report_format="PPTX",
    )
    image_path = (
        tmp_path
        / job["root_relative_path"]
        / "input"
        / "Bundle"
        / "stem"
        / "001_100kX.tif"
    )
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(_tiny_tiff())

    result = generate_specialized_report(settings, job)

    assert result is not None
    assert result.analysis_type == "TEM"
    assert result.primary_path.name == "report.pptx"
    assert result.primary_path.read_bytes().startswith(b"PK")
    with zipfile.ZipFile(result.package_path) as archive:
        assert {"report.pptx", "report.md"} <= set(archive.namelist())


def test_specialized_report_rejects_wrong_requested_format(tmp_path: Path) -> None:
    settings = Settings(storage_root=tmp_path)
    job = _job(
        "jobs/xrd-wrong-format",
        experiment_code="XRD",
        equipment_code="XRD-01",
        report_format="PPTX",
    )

    with pytest.raises(ApiException) as captured:
        generate_specialized_report(settings, job)

    assert captured.value.code == "XRD_REPORT_FORMAT_REQUIRED"


def test_existing_processor_json_keeps_generic_pipeline(tmp_path: Path) -> None:
    settings = Settings(storage_root=tmp_path)
    job = _job(
        "jobs/xrd-custom",
        experiment_code="XRD",
        equipment_code="XRD-01",
        report_format="PPTX",
    )
    processed = tmp_path / job["root_relative_path"] / "processed"
    processed.mkdir(parents=True)
    (processed / "analysis-result.json").write_text("{}", encoding="utf-8")

    assert generate_specialized_report(settings, job) is None

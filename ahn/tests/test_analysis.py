from pathlib import Path

from ahn.analysis import (
    _candidate_values_from_text,
    _select_supported_ocr_values,
    collect_project,
    extract_magnification,
)


DATA_ROOT = Path(__file__).resolve().parents[1] / "data" / "TESTData"


def test_extract_magnification_from_ahn_file_names() -> None:
    assert extract_magnification("HR Camera_1_8000X.tif") == "x8000"
    assert extract_magnification("HR Camera_10_600kX.tif") == "x600k"
    assert extract_magnification("BF_0817_120kX_6.tif") == "x120k"


def test_coating_ocr_candidate_parser_keeps_multiple_labels() -> None:
    assert _candidate_values_from_text("2.21 nm\n1.81 nm\n10 nm") == [2.21, 1.81]
    assert _candidate_values_from_text("2. IS rm") == [2.18]
    assert _select_supported_ocr_values(
        [[14.39, 19.08, 14.54], [14.59, 14.54], [14.59, 19.08, 14.54]]
    ) == [14.59, 19.08, 14.54]


def test_collect_project_reads_testdata_bundle() -> None:
    project = collect_project(DATA_ROOT)

    assert project.folders["tem"] == "TEM"
    assert project.folders["stem"] == "STEM"
    assert project.folders["report"] == "report"
    assert project.folders["scale"] == "Scale"
    assert [sample.sample_name for sample in project.tem_samples] == ["AlGn5", "RGB"]
    assert project.summary["temImageCount"] == 28
    assert project.summary["stemSampleCount"] == 5
    assert project.summary["stemImageCount"] == 45
    assert project.summary["stemBfImageCount"] == 45
    assert project.summary["edsReportCount"] == 5
    assert project.summary["spreadsheetCount"] == 5
    assert project.summary["coatingSampleCount"] == 1
    assert project.summary["coatingImageCount"] == 14
    assert project.coating_samples[0].sample_name == "Scale"
    assert all(
        item.note or item.thickness_nm is not None
        for item in project.coating_samples[0].measurements
    )
    assert all(
        not item.thickness_values_nm or item.thickness_nm is not None
        for item in project.coating_samples[0].measurements
    )


def test_collect_project_reads_reports_folder_alias(tmp_path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "Project 1_0647 Point2.docx").write_bytes(b"placeholder")
    (reports / "0647 point raw.xlsx").write_bytes(b"placeholder")

    project = collect_project(tmp_path)

    assert project.folders["report"] == "reports"
    assert project.summary["edsReportCount"] == 1
    assert project.summary["spreadsheetCount"] == 1
    assert project.eds_reports[0].title == "0647 Point2"
    assert project.eds_reports[0].analysis_type == "POINT"


def test_collect_project_keeps_spreadsheets_outside_report_folder(tmp_path) -> None:
    stem = tmp_path / "stem"
    stem.mkdir()
    (stem / "001_100kX.tif").write_bytes(b"placeholder")
    (tmp_path / "raw data.xlsx").write_bytes(b"placeholder")
    (tmp_path / "raw" / "line scan.csv").parent.mkdir()
    (tmp_path / "raw" / "line scan.csv").write_text("x,y\n1,2\n", encoding="utf-8")

    project = collect_project(tmp_path)

    assert project.summary["spreadsheetCount"] == 2
    assert [item.path for item in project.spreadsheets] == [
        "raw data.xlsx",
        "raw/line scan.csv",
    ]

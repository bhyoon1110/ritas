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

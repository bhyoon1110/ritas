from pathlib import Path

from ahn.analysis import (
    COATING_LABEL_DETECTION_MAX_DIMENSION,
    CoatingOcrResult,
    _candidate_values_from_text,
    _select_supported_ocr_values,
    collect_coating_samples,
    collect_project,
    extract_magnification,
    _ocr_label_boxes,
)
from PIL import Image
from ahn.processor import build_outputs


DATA_ROOT = Path(__file__).resolve().parents[1] / "data" / "TESTData"


def test_extract_magnification_from_ahn_file_names() -> None:
    assert extract_magnification("HR Camera_1_8000X.tif") == "x8000"
    assert extract_magnification("HR Camera_10_600kX.tif") == "x600k"
    assert extract_magnification("BF_0817_120kX_6.tif") == "x120k"


def test_coating_ocr_candidate_parser_keeps_multiple_labels() -> None:
    assert _candidate_values_from_text("2.21 nm\n1.81 nm\n10 nm") == [2.21, 1.81]
    assert _candidate_values_from_text("55.56 nm\n50 nm") == [55.56]
    assert _candidate_values_from_text("2. IS rm") == [2.18]
    assert _candidate_values_from_text("O.72 rm") == [0.72]
    assert _candidate_values_from_text("236.12 nm\n200 nm") == [236.12]
    assert _candidate_values_from_text("15-74 rn") == [15.74]
    assert _candidate_values_from_text("15-74") == []
    assert _select_supported_ocr_values(
        [[14.39, 19.08, 14.54], [14.59, 14.54], [14.59, 19.08, 14.54]]
    ) == [14.59, 19.08, 14.54]


def test_coating_label_detection_normalizes_high_resolution_image(monkeypatch) -> None:
    image = Image.new("L", (4000, 3000), color=80)
    seen_sizes = []

    def fake_label_box(detection_image, _box, _pytesseract):
        seen_sizes.append(detection_image.size)
        return [3.87], ["3.87 nm"]

    monkeypatch.setattr("ahn.analysis._ocr_label_box", fake_label_box)

    import cv2
    import numpy as np

    original_components = cv2.connectedComponentsWithStats

    def fake_components(_closed, _connectivity):
        stats = np.array(
            [
                [0, 0, 0, 0, 0],
                [700, 800, 650, 145, 78000],
            ],
            dtype=np.int32,
        )
        return 2, np.zeros((1, 1), dtype=np.int32), stats, np.zeros((2, 2))

    monkeypatch.setattr(cv2, "connectedComponentsWithStats", fake_components)
    try:
        values, text = _ocr_label_boxes(image, object())
    finally:
        monkeypatch.setattr(cv2, "connectedComponentsWithStats", original_components)

    assert values == [3.87]
    assert text == "3.87 nm"
    assert seen_sizes
    assert max(seen_sizes[0]) == COATING_LABEL_DETECTION_MAX_DIMENSION


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
    (tmp_path / "macro raw.xlsm").write_bytes(b"placeholder")
    (tmp_path / "binary raw.xlsb").write_bytes(b"placeholder")
    (tmp_path / "raw" / "line scan.csv").parent.mkdir()
    (tmp_path / "raw" / "line scan.csv").write_text("x,y\n1,2\n", encoding="utf-8")

    project = collect_project(tmp_path)

    assert project.summary["spreadsheetCount"] == 4
    assert [item.path for item in project.spreadsheets] == [
        "binary raw.xlsb",
        "macro raw.xlsm",
        "raw data.xlsx",
        "raw/line scan.csv",
    ]


def test_collect_coating_samples_preserves_order_with_parallel_ocr(tmp_path, monkeypatch) -> None:
    sample = tmp_path / "scale" / "Scale-A"
    sample.mkdir(parents=True)
    for file_name in ["sample-1.tif", "sample-2.tif", "sample-3.tif"]:
        (sample / file_name).write_bytes(b"placeholder")
    values = {
        "sample-1.tif": 1.0,
        "sample-2.tif": 2.0,
        "sample-3.tif": 3.0,
    }

    monkeypatch.setenv("RIST_TEM_OCR_WORKERS", "2")
    monkeypatch.setattr(
        "ahn.analysis._ocr_thickness_nm",
        lambda path: CoatingOcrResult(values_nm=[values[path.name]]),
    )

    samples = collect_coating_samples(tmp_path)

    assert [item.file_name for item in samples[0].measurements] == [
        "sample-1.tif",
        "sample-2.tif",
        "sample-3.tif",
    ]
    assert [item.thickness_nm for item in samples[0].measurements] == [1.0, 2.0, 3.0]


def test_build_outputs_emits_progress_events(tmp_path) -> None:
    stem = tmp_path / "stem"
    stem.mkdir()
    (stem / "001_100kX.tif").write_bytes(b"placeholder")
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "point raw.xlsm").write_bytes(b"placeholder")
    events = []

    manifest = build_outputs(
        input_dir=tmp_path,
        output_dir=tmp_path / "out",
        copy_raw_spreadsheets=True,
        progress_callback=lambda stage, pct, message: events.append((stage, pct, message)),
    )

    assert manifest["summary"]["stemImageCount"] == 1
    assert manifest["summary"]["spreadsheetCount"] == 1
    assert manifest["copiedSpreadsheets"] == ["raw/reports/point raw.xlsm"]
    assert (tmp_path / "out" / "raw" / "reports" / "point raw.xlsm").is_file()
    assert [event[0] for event in events] == ["collect", "json"]
    assert events[0][1] == 35
    assert events[1][1] == 58

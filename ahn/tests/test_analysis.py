from pathlib import Path

from ahn.analysis import (
    COATING_LABEL_DETECTION_MAX_DIMENSION,
    CoatingOcrResult,
    OcrCandidate,
    _candidate_values_from_text,
    _extract_coating_label_crop,
    _is_microscope_scale_box,
    _join_coating_label_tokens,
    _merge_rapid_ocr_candidates,
    _reconcile_coating_ocr_ensemble,
    _reconcile_coating_ocr_values,
    collect_coating_samples,
    collect_project,
    extract_magnification,
    _ocr_label_box,
    _ocr_label_boxes,
)
from PIL import Image, ImageDraw
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
    assert _candidate_values_from_text(
        "10 nm",
        exclude_microscope_scale=False,
    ) == [10.0]
    assert _candidate_values_from_text(
        "12.21 nm",
        exclude_microscope_scale=False,
    ) == [12.21]


def test_coating_label_crop_isolates_white_rectangle_from_leader_line() -> None:
    image = Image.new("L", (500, 300), color=70)
    draw = ImageDraw.Draw(image)
    draw.rectangle((180, 120, 350, 165), fill=255)
    draw.line((120, 220, 190, 150), fill=255, width=9)
    draw.line((120, 220, 190, 150), fill=0, width=3)
    draw.rectangle((210, 132, 225, 153), fill=0)

    crop, refined_box = _extract_coating_label_crop(image, (174, 116, 182, 54))

    assert 178 <= refined_box[0] <= 182
    assert 118 <= refined_box[1] <= 122
    assert refined_box[2] >= 168
    assert refined_box[3] >= 43
    assert crop.getpixel((0, 0)) == 255
    assert crop.width > refined_box[2]
    # The black center of the leader touches the left label boundary but is
    # removed before OCR; the interior text glyph remains.
    margin = (crop.width - refined_box[2]) // 2
    assert crop.crop((margin, margin, margin + 8, crop.height - margin)).getextrema()[0] == 255
    assert crop.getextrema()[0] == 0


def test_coating_label_crop_does_not_trim_wide_leading_digit_region() -> None:
    image = Image.new("L", (500, 300), color=70)
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 120, 350, 165), fill=255)
    # Mimic a tall italic leading digit that lowers the white fill ratio for
    # more than 15% of the label width.
    draw.rectangle((106, 121, 151, 157), fill=0)

    _crop, refined_box = _extract_coating_label_crop(image, (96, 116, 260, 54))

    assert refined_box[0] <= 102
    assert refined_box[2] >= 245


def test_coating_label_tokens_reassemble_split_decimal() -> None:
    assert _join_coating_label_tokens(("2.", "68", "nm")) == "2.68nm"
    assert _join_coating_label_tokens(("2.8", ".82", "nm")) == "2.82nm"
    assert _join_coating_label_tokens(("18.52", "nm")) == "18.52nm"


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


def test_coating_ocr_prefers_full_image_value_when_label_loses_leading_digit() -> None:
    assert _reconcile_coating_ocr_values([5.74], [15.74]) == [15.74]
    assert _reconcile_coating_ocr_values([2.21], [12.21]) == [12.21]


def test_coating_ocr_keeps_label_value_when_full_image_has_no_measurement() -> None:
    assert _reconcile_coating_ocr_values([3.87], []) == [3.87]


def test_coating_ocr_discards_extra_full_image_artifact() -> None:
    assert _reconcile_coating_ocr_values([7.66], [7.66, 17.661]) == [7.66]


def test_coating_ocr_ensemble_restores_leading_digit() -> None:
    candidates = [
        OcrCandidate(value_nm=12.21, text="12.21 nm", confidence=0.99),
    ]

    assert _reconcile_coating_ocr_ensemble(candidates, [2.21], []) == [12.21]
    assert _reconcile_coating_ocr_ensemble(candidates, [22.21], []) == [12.21]


def test_coating_ocr_ensemble_does_not_add_tesseract_crop_noise() -> None:
    candidates = [
        OcrCandidate(value_nm=7.66, text="7.66 nm", confidence=0.98),
    ]

    assert _reconcile_coating_ocr_ensemble(candidates, [7.0, 17.66], []) == [7.66]


def test_coating_ocr_ensemble_uses_tesseract_when_neural_detector_misses() -> None:
    assert _reconcile_coating_ocr_ensemble([], [14.72], []) == [14.72]


def test_coating_label_crop_removes_measurement_line_prefix() -> None:
    full = [
        OcrCandidate(
            value_nm=45.80,
            text="45.80 nm",
            confidence=0.98,
            box=(800, 700, 250, 80),
            source="rapid-original",
        ),
    ]
    label = [
        OcrCandidate(
            value_nm=5.80,
            text="5.80",
            confidence=1.0,
            box=(860, 730, 180, 45),
            source="rapid-label",
        ),
    ]

    assert [candidate.value_nm for candidate in _merge_rapid_ocr_candidates(full, label)] == [5.80]


def test_coating_label_crop_does_not_remove_real_leading_digit() -> None:
    full = [
        OcrCandidate(
            value_nm=12.21,
            text="12.21 nm",
            confidence=0.99,
            box=(571, 781, 132, 39),
            source="rapid-original",
        ),
    ]
    label = [
        OcrCandidate(
            value_nm=2.21,
            text="2.21",
            confidence=1.0,
            box=(575, 789, 122, 26),
            source="rapid-label",
        ),
    ]

    assert [candidate.value_nm for candidate in _merge_rapid_ocr_candidates(full, label)] == [12.21]


def test_coating_label_crop_adds_second_measurement_missed_by_full_image() -> None:
    full = [
        OcrCandidate(value_nm=3.20, text="3.20 nm", confidence=0.97, box=(569, 394, 255, 113)),
    ]
    labels = [
        OcrCandidate(value_nm=3.20, text="3.20", confidence=1.0, box=(621, 442, 186, 43)),
        OcrCandidate(value_nm=5.80, text="5.80", confidence=1.0, box=(859, 749, 186, 43)),
    ]

    assert [candidate.value_nm for candidate in _merge_rapid_ocr_candidates(full, labels)] == [3.20, 5.80]


def test_coating_label_crop_preserves_full_decimal_when_crop_loses_decimal() -> None:
    full = [
        OcrCandidate(value_nm=2.68, text="2.68 nm", confidence=0.99, box=(842, 1164, 258, 82)),
    ]
    labels = [
        OcrCandidate(value_nm=68.0, text="68", confidence=1.0, box=(862, 1182, 225, 52)),
    ]

    assert [candidate.value_nm for candidate in _merge_rapid_ocr_candidates(full, labels)] == [2.68]


def test_coating_label_crop_discards_unmatched_integer_fragment() -> None:
    full = [
        OcrCandidate(value_nm=7.18, text="7.18 nm", confidence=0.99, box=(576, 325, 495, 140)),
    ]
    labels = [
        OcrCandidate(value_nm=7.0, text="7", confidence=1.0, box=(592, 347, 460, 107)),
        OcrCandidate(value_nm=1.0, text="1", confidence=1.0, box=(1195, 218, 82, 44)),
    ]

    assert [candidate.value_nm for candidate in _merge_rapid_ocr_candidates(full, labels)] == [7.18]


def test_coating_label_crop_keeps_unmatched_decimal_measurement() -> None:
    labels = [
        OcrCandidate(value_nm=5.80, text="5.80", confidence=1.0, box=(859, 749, 186, 43)),
    ]

    assert [candidate.value_nm for candidate in _merge_rapid_ocr_candidates([], labels)] == [5.80]


def test_coating_ocr_identifies_lower_left_scale_bar() -> None:
    assert _is_microscope_scale_box((50, 900, 160, 60), (1000, 1000))
    assert not _is_microscope_scale_box((500, 500, 160, 60), (1000, 1000))


def test_coating_label_ocr_uses_threshold_variant_only_as_fallback(monkeypatch) -> None:
    image = Image.new("L", (600, 600), color=80)
    responses = iter(["7.66rm", "17.661"])
    monkeypatch.setattr("ahn.analysis._run_tesseract", lambda *_args, **_kwargs: next(responses))

    values, _texts = _ocr_label_box(image, (240, 300, 120, 35), object())

    assert values == [7.66]


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

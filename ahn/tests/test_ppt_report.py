from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

pptx = pytest.importorskip("pptx")
Presentation = pptx.Presentation

from ahn.ppt_report import build_pptx


def _write_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (120, 90), (220, 224, 230)).save(path)


def _base_project(root: Path) -> dict:
    return {
        "experiment": "AHN-TEM",
        "input_root": str(root),
        "generated_at": "2026-07-08T00:00:00+00:00",
        "folders": {},
        "tem_samples": [],
        "stem_samples": [],
        "eds_reports": [],
        "spreadsheets": [],
        "coating_samples": [],
        "summary": {},
    }


def _project_with_sections(root: Path, sections: set[str]) -> dict:
    image_path = root / "image.png"
    _write_image(image_path)
    report_path = root / "report" / "report.docx"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(b"placeholder")

    image_record = {
        "path": image_path.name,
        "file_name": image_path.name,
        "sample_name": "S1",
        "magnification": "x100k",
        "sequence": 1,
        "kind": "",
    }
    project = _base_project(root)
    if "tem" in sections:
        project["tem_samples"] = [{"sample_name": "TEM-A", "images": [image_record]}]
    if "stem" in sections:
        project["stem_samples"] = [{"sample_name": "STEM-A", "images": [image_record], "bf_images": []}]
    if "eds" in sections:
        project["eds_reports"] = [
            {
                "path": "report/report.docx",
                "file_name": "report.docx",
                "title": "EDS-A",
                "sample_name": "EDS-A",
                "analysis_type": "MAP",
            }
        ]
    if "coating" in sections:
        project["coating_samples"] = [
            {
                "sample_name": "Scale-A",
                "measurements": [
                    {
                        "index": 1,
                        "path": image_path.name,
                        "file_name": image_path.name,
                        "magnification": "x100k",
                        "thickness_nm": 2.5,
                        "thickness_values_nm": [2.0, 3.0],
                        "ocr_text": "",
                        "note": "OCR 라벨 2개 추출",
                        "ocr_review_required": False,
                        "ocr_warnings": [],
                    }
                ],
            }
        ]
    return project


@pytest.mark.parametrize(
    ("sections", "expected_titles"),
    [
        ({"tem", "stem", "eds", "coating"}, ["■ TEM 이미지 분석결과", "■ STEM 이미지 분석결과", "■ STEM EDS 분석결과", "■ TEM 코팅층"]),
        ({"tem", "eds", "coating"}, ["■ TEM 이미지 분석결과", "■ STEM EDS 분석결과", "■ TEM 코팅층"]),
        ({"stem", "coating"}, ["■ STEM 이미지 분석결과", "■ TEM 코팅층"]),
        ({"eds"}, ["■ STEM EDS 분석결과"]),
    ],
)
def test_build_pptx_includes_only_available_sections(tmp_path, monkeypatch, sections, expected_titles) -> None:
    image_path = tmp_path / "image.png"

    def fake_extract_docx(_docx_path: Path, _extract_dir: Path):
        return SimpleNamespace(media_paths=[image_path], tables=[])

    monkeypatch.setattr("ahn.ppt_report.extract_docx", fake_extract_docx)
    project = _project_with_sections(tmp_path, set(sections))
    output = tmp_path / "report.pptx"

    build_pptx(project, output)

    prs = Presentation(output)
    slide_texts = [" ".join(shape.text for shape in slide.shapes if getattr(shape, "has_text_frame", False)) for slide in prs.slides]
    all_text = "\n".join(slide_texts)
    assert len(prs.slides) == len(expected_titles)
    for title in expected_titles:
        assert title in all_text
    absent_titles = {
        "tem": "■ TEM 이미지 분석결과",
        "stem": "■ STEM 이미지 분석결과",
        "eds": "■ STEM EDS 분석결과",
        "coating": "■ TEM 코팅층",
    }
    for section, title in absent_titles.items():
        if section not in sections:
            assert title not in all_text

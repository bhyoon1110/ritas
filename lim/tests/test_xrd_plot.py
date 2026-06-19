from __future__ import annotations

from lim.xrd_plot import pdf_peak_warning


def test_pdf_peak_warning_explains_missing_pdf_files() -> None:
    warning = pdf_peak_warning("cards", pdf_count=0, parsed_count=0)

    assert warning is not None
    assert "PDF 파일" in warning


def test_pdf_peak_warning_explains_parse_failure() -> None:
    warning = pdf_peak_warning("cards", pdf_count=2, parsed_count=0)

    assert warning is not None
    assert "추출하지 못했습니다" in warning


def test_pdf_peak_warning_is_empty_when_peaks_exist() -> None:
    assert pdf_peak_warning("cards", pdf_count=2, parsed_count=1) is None

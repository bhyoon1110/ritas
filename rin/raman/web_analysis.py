"""In-memory Raman analysis used by the Edge web preview API."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ftir.assignment_libraries import AssignmentLibrary, flatten_assignment_libraries

from .peaks import detect_peaks_with_fwhm, peak_params_for_sensitivity
from .plotting import build_multi_raman_fig
from .preprocess import RamanRawError, load_raman_raw, preprocess_raman


SHIFT_MIN = 0.0
SHIFT_MAX = 4000.0
GRID_SIZE = 2200
SMOOTH_WINDOW = 11
SMOOTH_POLY = 3


class RamanAnalysisError(ValueError):
    """A user-correctable Raman parsing or analysis error."""

    def __init__(self, code: str, message: str, filename: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.filename = filename


def _sample_label(filename: str, used: set[str]) -> str:
    base = Path(filename).stem.strip() or "sample"
    label = base
    suffix = 2
    while label.casefold() in used:
        label = f"{base} ({suffix})"
        suffix += 1
    used.add(label.casefold())
    return label


def analyze_raman_files(
    files: list[tuple[str, bytes]],
    *,
    sensitivity: int = 25,
    smooth: bool = True,
    baseline: bool = True,
    assignment_libraries: list[AssignmentLibrary] | None = None,
) -> dict:
    if not files:
        raise RamanAnalysisError("RAMAN_FILES_REQUIRED", "Raman raw 파일이 필요합니다.")

    sensitivity = max(0, min(100, int(sensitivity)))
    params = peak_params_for_sensitivity(sensitivity)
    grid = np.linspace(SHIFT_MIN, SHIFT_MAX, GRID_SIZE)
    func_groups = (
        flatten_assignment_libraries(assignment_libraries)
        if assignment_libraries is not None
        else []
    )
    used_labels: set[str] = set()
    samples = []
    summaries = []

    for filename, content in files:
        try:
            raw = load_raman_raw(
                filename,
                content,
                shift_min=SHIFT_MIN,
                shift_max=SHIFT_MAX,
            )
        except RamanRawError as exc:
            raise RamanAnalysisError(
                "INVALID_RAMAN_RAW",
                f"Raman raw 파일을 읽을 수 없습니다: {filename} ({exc})",
                filename,
            ) from exc

        try:
            processed = preprocess_raman(
                raw["shift"].to_numpy(),
                raw["intensity"].to_numpy(),
                grid,
                smooth=smooth,
                baseline=baseline,
                smooth_window=SMOOTH_WINDOW,
                smooth_poly=SMOOTH_POLY,
            )
            peak_shift, peak_val, peak_fwhm, peak_idx = detect_peaks_with_fwhm(
                processed,
                grid,
                height=float(params["height"]),
                prominence=float(params["prominence"]),
                distance=int(params["distance"]),
            )
        except Exception as exc:
            raise RamanAnalysisError(
                "RAMAN_ANALYSIS_FAILED",
                f"Raman 전처리 또는 피크 분석에 실패했습니다: {filename}",
                filename,
            ) from exc

        label = _sample_label(filename, used_labels)
        samples.append(
            {
                "label": label,
                "grid": grid,
                "processed": processed,
                "peak_idx": peak_idx,
                "peak_shift": peak_shift,
                "peak_val": peak_val,
                "peak_fwhm": peak_fwhm,
            }
        )
        summaries.append(
            {
                "fileName": filename,
                "label": label,
                "pointCount": int(len(raw)),
                "peakCount": int(len(peak_idx)),
            }
        )

    figure = build_multi_raman_fig(
        samples,
        shift_min=SHIFT_MIN,
        shift_max=SHIFT_MAX,
        func_groups=func_groups,
        initial_sensitivity=sensitivity,
    )
    return {
        "figure": json.loads(figure.to_json()),
        "samples": summaries,
        "settings": {
            "sensitivity": sensitivity,
            "height": float(params["height"]),
            "prominence": float(params["prominence"]),
            "distance": int(params["distance"]),
            "smooth": smooth,
            "baseline": baseline,
            "shiftMin": SHIFT_MIN,
            "shiftMax": SHIFT_MAX,
            "assignmentLibraries": [
                {
                    "id": library.library_id,
                    "name": library.name,
                    "assignmentCount": len(library.assignments),
                }
                for library in (assignment_libraries or [])
            ],
        },
    }

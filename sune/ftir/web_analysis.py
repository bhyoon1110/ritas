"""In-memory FT-IR analysis used by the Edge web preview API."""

from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import re

import numpy as np

from .assignment_libraries import (
    AssignmentLibrary,
    flatten_assignment_libraries,
)
from .findings import load_func_groups
from .peaks import detect_peaks_with_fwhm, peak_params_for_sensitivity
from .plotting import build_multi_peak_fig
from .preprocess import load_dpt, preprocess


WN_MIN = 400.0
WN_MAX = 4000.0
SMOOTH_WINDOW = 11
SMOOTH_POLY = 3
FUNC_GROUPS_PATH = Path(__file__).resolve().parent / "resources" / "func_groups.csv"


class DptAnalysisError(ValueError):
    """A user-correctable DPT parsing or analysis error."""

    def __init__(self, code: str, message: str, filename: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.filename = filename


def _decode_text(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp949", "latin1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _looks_like_numeric_row(line: str) -> bool:
    tokens = re.split(r"[\s,\t]+", line.strip())
    if len(tokens) < 2:
        return False
    try:
        float(tokens[0])
        float(tokens[1])
    except ValueError:
        return False
    return True


def _parse_dpt_metadata(content: bytes) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for line in _decode_text(content).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _looks_like_numeric_row(stripped):
            break
        item = stripped.lstrip("#;").strip()
        if not item:
            continue
        if ":" in item:
            key, value = item.split(":", 1)
        elif "=" in item:
            key, value = item.split("=", 1)
        else:
            metadata.setdefault("Notes", item)
            continue
        key = key.strip()
        value = value.strip()
        if key:
            metadata[key] = value or "(미기재)"
    return metadata


def _sample_label(filename: str, used: set[str]) -> str:
    base = Path(filename).stem.strip() or "sample"
    label = base
    suffix = 2
    while label.casefold() in used:
        label = f"{base} ({suffix})"
        suffix += 1
    used.add(label.casefold())
    return label


def analyze_dpt_files(
    files: list[tuple[str, bytes]],
    *,
    sensitivity: int = 25,
    smooth: bool = True,
    assignment_libraries: list[AssignmentLibrary] | None = None,
) -> dict:
    """Analyze uploaded DPT bytes and return a Plotly-compatible payload."""
    if not files:
        raise DptAnalysisError("DPT_FILES_REQUIRED", "DPT 파일이 필요합니다.")

    sensitivity = max(0, min(100, int(sensitivity)))
    params = peak_params_for_sensitivity(sensitivity)
    grid_size = max(1750, int((WN_MAX - WN_MIN) / 2.0))
    grid = np.linspace(WN_MIN, WN_MAX, grid_size)
    func_groups = (
        load_func_groups(FUNC_GROUPS_PATH)
        if assignment_libraries is None
        else flatten_assignment_libraries(assignment_libraries)
    )
    used_labels: set[str] = set()
    samples = []
    summaries = []

    for filename, content in files:
        metadata = _parse_dpt_metadata(content)
        try:
            raw = load_dpt(BytesIO(content), WN_MIN, WN_MAX)
        except Exception as exc:
            raise DptAnalysisError(
                "INVALID_DPT",
                f"DPT 파일을 읽을 수 없습니다: {filename}",
                filename,
            ) from exc
        if len(raw) < 10:
            raise DptAnalysisError(
                "INSUFFICIENT_DPT_DATA",
                f"유효한 스펙트럼 데이터가 부족합니다: {filename}",
                filename,
            )

        try:
            sample_vec, _ = preprocess(
                raw["wn"].to_numpy(),
                raw["y"].to_numpy(),
                grid,
                smooth,
                SMOOTH_WINDOW,
                SMOOTH_POLY,
                return_mask=True,
            )
            peak_idx, peak_wn, peak_val, peak_fwhm = detect_peaks_with_fwhm(
                sample_vec,
                grid,
                params["height"],
                params["prominence"],
                params["distance"],
            )
        except Exception as exc:
            raise DptAnalysisError(
                "DPT_ANALYSIS_FAILED",
                f"전처리 또는 피크 분석에 실패했습니다: {filename}",
                filename,
            ) from exc

        label = _sample_label(filename, used_labels)
        samples.append(
            {
                "label": label,
                "grid": grid,
                "sample_vec": sample_vec,
                "peak_idx": peak_idx,
                "peak_wn": peak_wn,
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
                "metadata": metadata,
            }
        )

    figure = build_multi_peak_fig(
        samples,
        func_groups,
        WN_MIN,
        WN_MAX,
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
            "wavenumberMin": WN_MIN,
            "wavenumberMax": WN_MAX,
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

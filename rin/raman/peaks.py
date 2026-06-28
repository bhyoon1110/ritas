"""Raman peak detection helpers."""

from __future__ import annotations

import numpy as np
from scipy.signal import find_peaks, peak_widths


PEAK_SENSITIVITY_PRESETS = {
    "very_low": {"height": 0.35, "prominence": 0.25, "distance": 70},
    "low": {"height": 0.12, "prominence": 0.08, "distance": 30},
    "medium": {"height": 0.06, "prominence": 0.035, "distance": 18},
    "high": {"height": 0.025, "prominence": 0.012, "distance": 8},
}
PEAK_SENSITIVITY_VALUES = {"low": 25, "medium": 50, "high": 100}
PEAK_SENSITIVITY_ANCHORS = (
    (0, "very_low"),
    (25, "low"),
    (50, "medium"),
    (100, "high"),
)


def peak_params_for_sensitivity(value: int | float) -> dict[str, float | int]:
    sensitivity = max(0, min(100, int(round(float(value)))))
    for anchor_value, anchor_name in PEAK_SENSITIVITY_ANCHORS:
        if sensitivity == anchor_value:
            return dict(PEAK_SENSITIVITY_PRESETS[anchor_name])
    left = PEAK_SENSITIVITY_ANCHORS[0]
    right = PEAK_SENSITIVITY_ANCHORS[-1]
    for start, end in zip(PEAK_SENSITIVITY_ANCHORS, PEAK_SENSITIVITY_ANCHORS[1:]):
        if start[0] <= sensitivity <= end[0]:
            left, right = start, end
            break
    ratio = (sensitivity - left[0]) / (right[0] - left[0])
    start = PEAK_SENSITIVITY_PRESETS[left[1]]
    end = PEAK_SENSITIVITY_PRESETS[right[1]]

    def interpolate(name: str) -> float:
        return start[name] + (end[name] - start[name]) * ratio

    return {
        "height": interpolate("height"),
        "prominence": interpolate("prominence"),
        "distance": max(1, int(round(interpolate("distance")))),
    }


def detect_peaks_with_fwhm(
    values: np.ndarray,
    grid: np.ndarray,
    *,
    height: float,
    prominence: float,
    distance: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    indexes, _ = find_peaks(
        values,
        height=height,
        prominence=prominence,
        distance=distance,
    )
    if len(indexes) == 0:
        return indexes, np.array([]), np.array([]), np.array([])
    widths_pts, _, _, _ = peak_widths(values, indexes, rel_height=0.5)
    grid_step = (grid[-1] - grid[0]) / (len(grid) - 1)
    return grid[indexes], values[indexes], widths_pts * grid_step, indexes


def build_interactive_peak_candidates(
    values: np.ndarray,
    grid: np.ndarray,
    selected_indexes: np.ndarray,
    *,
    initial_sensitivity: int | float,
) -> list[dict]:
    first_seen: dict[int, int] = {}
    preset_indexes: dict[int, set[int]] = {}
    for sensitivity in range(101):
        params = peak_params_for_sensitivity(sensitivity)
        indexes, _ = find_peaks(
            values,
            height=float(params["height"]),
            prominence=float(params["prominence"]),
            distance=int(params["distance"]),
        )
        for index in indexes:
            first_seen.setdefault(int(index), sensitivity)
        if sensitivity in PEAK_SENSITIVITY_VALUES.values():
            preset_indexes[sensitivity] = {int(index) for index in indexes}

    indexes = np.array(sorted(first_seen), dtype=int)
    widths = np.array([])
    if len(indexes):
        widths_pts, _, _, _ = peak_widths(values, indexes, rel_height=0.5)
        grid_step = (grid[-1] - grid[0]) / (len(grid) - 1)
        widths = widths_pts * grid_step
    selected = {int(index) for index in selected_indexes}

    candidates = []
    for index, fwhm in zip(indexes, widths):
        levels = [
            name
            for name, sensitivity in PEAK_SENSITIVITY_VALUES.items()
            if int(index) in preset_indexes.get(sensitivity, set())
        ]
        candidates.append(
            {
                "index": int(index),
                "shift": float(grid[index]),
                "value": float(values[index]),
                "fwhm": float(fwhm),
                "levels": levels,
                "sensitivity_min": int(first_seen[int(index)]),
                "initial": int(index) in selected,
            }
        )
    return candidates


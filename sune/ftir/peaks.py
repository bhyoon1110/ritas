# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: CLI와 그래프 전반에서 공용하는 피크(peak) 검출 헬퍼.
# 실행 방법: 모듈 — 직접 실행하지 않고 import해서 사용 (ftir_analyze.py / ftir.cli 를 통해 동작)
# ─────────────────────────────────────────────────────────────────────────────
"""Peak detection helpers used across CLI and plotting."""

import numpy as np
from scipy.signal import find_peaks, peak_widths

PEAK_SENSITIVITY_PRESETS = {
    "high": {"height": 0.03, "prominence": 0.015, "distance": 10},
    "medium": {"height": 0.05, "prominence": 0.03, "distance": 15},
    "low": {"height": 0.08, "prominence": 0.06, "distance": 25},
    "very_low": {"height": 0.40, "prominence": 0.30, "distance": 70},
}

PEAK_SENSITIVITY_VALUES = {"low": 25, "medium": 50, "high": 100}
PEAK_SENSITIVITY_ANCHORS = (
    (0, "very_low"),
    (25, "low"),
    (50, "medium"),
    (100, "high"),
)


def peak_params_for_sensitivity(value):
    """0~100 민감도를 very-low/low/medium/high 사이에서 선형 보간한다."""
    sensitivity = max(0, min(100, int(round(float(value)))))
    for anchor_value, anchor_name in PEAK_SENSITIVITY_ANCHORS:
        if sensitivity == anchor_value:
            return dict(PEAK_SENSITIVITY_PRESETS[anchor_name])
    start_value, start_name = PEAK_SENSITIVITY_ANCHORS[0]
    end_value, end_name = PEAK_SENSITIVITY_ANCHORS[-1]
    for left, right in zip(
        PEAK_SENSITIVITY_ANCHORS,
        PEAK_SENSITIVITY_ANCHORS[1:],
    ):
        if left[0] <= sensitivity <= right[0]:
            start_value, start_name = left
            end_value, end_name = right
            break
    start = PEAK_SENSITIVITY_PRESETS[start_name]
    end = PEAK_SENSITIVITY_PRESETS[end_name]
    ratio = (sensitivity - start_value) / (end_value - start_value)

    def interpolate(name):
        return start[name] + (end[name] - start[name]) * ratio

    return {
        "height": interpolate("height"),
        "prominence": interpolate("prominence"),
        "distance": max(1, int(round(interpolate("distance")))),
    }


def detect_peaks_with_fwhm(vec, grid, height=0.05, prominence=0.03, distance=15):
    """벡터에서 피크 인덱스/파수/강도/FWHM를 한 번에 반환."""
    idx, _ = find_peaks(vec, height=height, prominence=prominence, distance=distance)
    if len(idx) == 0:
        return idx, np.array([]), np.array([]), np.array([])
    peak_wn = grid[idx]
    peak_val = vec[idx]
    widths_pts, _, _, _ = peak_widths(vec, idx, rel_height=0.5)
    wn_step = (grid[-1] - grid[0]) / (len(grid) - 1)
    peak_fwhm = widths_pts * wn_step
    return idx, peak_wn, peak_val, peak_fwhm


def build_interactive_peak_candidates(
    vec,
    grid,
    selected_idx,
    selected_wn,
    selected_val,
    selected_fwhm,
    initial_sensitivity="medium",
):
    """현재 검출 결과와 0~100 민감도 범위의 피크 후보를 병합한다."""
    candidates = {}

    def add_candidates(
        indexes,
        wns,
        values,
        fwhms,
        level=None,
        minimum_sensitivity=None,
        initial=False,
    ):
        for idx, wn, value, fwhm in zip(indexes, wns, values, fwhms):
            key = int(idx)
            candidate = candidates.setdefault(
                key,
                {
                    "index": key,
                    "wn": float(wn),
                    "value": float(value),
                    "fwhm": float(fwhm),
                    "levels": set(),
                    "sensitivity_min": 100,
                    "initial": False,
                },
            )
            if level:
                candidate["levels"].add(level)
            if minimum_sensitivity is not None:
                candidate["sensitivity_min"] = min(
                    candidate["sensitivity_min"],
                    int(minimum_sensitivity),
                )
            if initial:
                candidate["initial"] = True
                candidate["wn"] = float(wn)
                candidate["value"] = float(value)
                candidate["fwhm"] = float(fwhm)

    first_seen = {}
    preset_indexes = {}
    for sensitivity in range(101):
        params = peak_params_for_sensitivity(sensitivity)
        indexes, _ = find_peaks(
            vec,
            height=params["height"],
            prominence=params["prominence"],
            distance=params["distance"],
        )
        for idx in indexes:
            first_seen.setdefault(int(idx), sensitivity)
        if sensitivity in set(PEAK_SENSITIVITY_VALUES.values()):
            preset_indexes[sensitivity] = {int(idx) for idx in indexes}

    candidate_indexes = np.array(sorted(first_seen), dtype=int)
    if len(candidate_indexes):
        widths_pts, _, _, _ = peak_widths(vec, candidate_indexes, rel_height=0.5)
        wn_step = (grid[-1] - grid[0]) / (len(grid) - 1)
        fwhms = widths_pts * wn_step
        for idx, fwhm in zip(candidate_indexes, fwhms):
            levels = [
                level
                for level, sensitivity in PEAK_SENSITIVITY_VALUES.items()
                if int(idx) in preset_indexes.get(sensitivity, set())
            ]
            add_candidates(
                [idx],
                [grid[idx]],
                [vec[idx]],
                [fwhm],
                minimum_sensitivity=first_seen[int(idx)],
            )
            candidates[int(idx)]["levels"].update(levels)

    initial_value = PEAK_SENSITIVITY_VALUES.get(initial_sensitivity)
    if initial_value is None:
        try:
            initial_value = max(0, min(100, int(round(float(initial_sensitivity)))))
        except (TypeError, ValueError):
            initial_value = 50
    add_candidates(
        selected_idx,
        selected_wn,
        selected_val,
        selected_fwhm,
        minimum_sensitivity=initial_value,
        initial=True,
    )

    result = []
    for candidate in sorted(candidates.values(), key=lambda item: item["index"]):
        candidate["levels"] = sorted(candidate["levels"])
        result.append(candidate)
    return result

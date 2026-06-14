# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: CLI와 그래프 전반에서 공용하는 피크(peak) 검출 헬퍼.
# 실행 방법: 모듈 — 직접 실행하지 않고 import해서 사용 (ftir_analyze.py / ftir.cli 를 통해 동작)
# ─────────────────────────────────────────────────────────────────────────────
"""Peak detection helpers used across CLI and plotting."""

import numpy as np
from scipy.signal import find_peaks, peak_widths


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

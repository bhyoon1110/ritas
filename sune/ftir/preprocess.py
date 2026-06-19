# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: FTIR 스펙트럼 로딩 및 전처리(베이스라인·정규화 등) 유틸.
# 실행 방법: 모듈 — 직접 실행하지 않고 import해서 사용 (ftir_analyze.py / ftir.cli 를 통해 동작)
# ─────────────────────────────────────────────────────────────────────────────
"""FTIR spectrum loading and preprocessing utilities."""

import numpy as np
import pandas as pd
from scipy.interpolate import interp1d
from scipy.signal import savgol_filter, find_peaks


def load_dpt(path, wn_min, wn_max):
    """Bruker DPT (wavenumber, intensity) 파일 로드"""
    df = pd.read_csv(path, header=None, names=["wn", "y"])
    df = df.apply(pd.to_numeric, errors="coerce").dropna()
    df = df[(df["wn"] >= wn_min) & (df["wn"] <= wn_max)]
    return df.sort_values("wn")

def load_csv(path, wn_min, wn_max):
    """라이브러리 CSV (wavenumber, absorbance) 로드"""
    df = pd.read_csv(path, comment="#")
    df.columns = [c.strip() for c in df.columns]
    wn_col = next(
        (c for c in df.columns if "wave" in c.lower() or "wn" in c.lower()),
        None,
    )
    ab_col = next(
        (c for c in df.columns if "abs" in c.lower() or "int" in c.lower()),
        None,
    )
    if wn_col is None or ab_col is None:
        columns = ", ".join(map(str, df.columns))
        raise ValueError(
            f"CSV에 wavenumber/absorbance 컬럼이 없습니다: {path} ({columns})"
        )
    df = df[[wn_col, ab_col]].rename(columns={wn_col: "wn", ab_col: "y"})
    df = df.apply(pd.to_numeric, errors="coerce").dropna()
    df = df[(df["wn"] >= wn_min) & (df["wn"] <= wn_max)]
    return df.sort_values("wn")

def baseline_als(y, lam=1e5, p=0.01, n_iter=10):
    """Asymmetric Least Squares 베이스라인 보정"""
    from scipy.sparse import diags
    from scipy.sparse.linalg import spsolve

    L = len(y)
    ones = np.ones(L)
    D = diags([ones, -2 * ones, ones], [0, 1, 2], shape=(L - 2, L), format="csc")
    H = lam * D.T.dot(D)
    w = np.ones(L)
    for _ in range(n_iter):
        W = diags(w, 0, shape=(L, L), format="csc")
        Z = spsolve(W + H, w * y)
        w = p * (y > Z) + (1 - p) * (y <= Z)
    return y - Z

def preprocess(wn, y, grid, smooth=True, smooth_win=11, smooth_poly=3, return_mask=False):
    """보간 → 스무딩 → 베이스라인 보정 → Min-Max 정규화

    return_mask=True이면 (vec, valid_mask)를 반환.
    valid_mask: grid 포인트가 원본 측정 범위(wn.min~wn.max) 안에 있으면 True.
    측정 범위 밖은 보간으로 0이 채워지므로, 매칭 시 이 구간을 제외하기 위함.
    """
    wn_lo, wn_hi = float(np.min(wn)), float(np.max(wn))
    f = interp1d(wn, y, kind="linear", bounds_error=False, fill_value=0)
    y_grid = f(grid)

    if smooth:
        y_grid = savgol_filter(y_grid, window_length=smooth_win, polyorder=smooth_poly)

    y_grid = baseline_als(y_grid)

    mn, mx = y_grid.min(), y_grid.max()
    if mx - mn > 1e-10:
        y_grid = (y_grid - mn) / (mx - mn)

    if return_mask:
        valid_mask = (grid >= wn_lo) & (grid <= wn_hi)
        return y_grid, valid_mask
    return y_grid


# ──────────────────────────────────────────────────────────────
# 다중 지표 점수 — v2 신규
# ──────────────────────────────────────────────────────────────

def first_derivative(vec, win=11, poly=3, normalize=True):
    """Savitzky-Golay 1차 미분. 베이스라인·정규화 차이에 강건.
    normalize=False이면 L2 정규화를 생략 (마스크 적용 후 정규화하려는 경우)."""
    if len(vec) < win:
        win = len(vec) if len(vec) % 2 == 1 else len(vec) - 1
    d = savgol_filter(vec, window_length=win, polyorder=poly, deriv=1)
    if not normalize:
        return d
    n = np.linalg.norm(d)
    return d / n if n > 1e-12 else d

def detect_peaks_simple(vec, grid, height=0.05, prominence=0.03, distance=15):
    """벡터에서 피크 파수 배열만 반환."""
    idx, _ = find_peaks(vec, height=height, prominence=prominence, distance=distance)
    return grid[idx]

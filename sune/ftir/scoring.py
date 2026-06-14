# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: 라이브러리 로딩 및 복합 점수 계산(I/O 부수효 없는 테스트 가능 로직).
# 실행 방법: 모듈 — 직접 실행하지 않고 import해서 사용 (ftir_analyze.py / ftir.cli 를 통해 동작)
# ─────────────────────────────────────────────────────────────────────────────
"""Library loading and composite scoring (testable, no I/O side effects)."""

import os
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .library_matcher import (
    assign_confidence_tier,
    fingerprint_weight_vector,
    masked_deriv_cosine,
    peak_match_score,
    weighted_cosine,
)
from .preprocess import (
    detect_peaks_simple,
    first_derivative,
    load_csv,
    preprocess,
)


CAT_LABEL = {
    "01_battery":             "Battery",
    "02_steel_coating":       "Steel Coating",
    "03_engineering_plastic": "Engineering Plastic",
    "04_elastomers_seals":    "Elastomers & Seals",
    "05_ceramic_inorganic":   "Ceramic / Inorganic",
}


@dataclass
class LibraryBundle:
    """전처리된 라이브러리 스펙트럼 묶음."""
    vecs: list = field(default_factory=list)
    derivs: list = field(default_factory=list)
    peaks_list: list = field(default_factory=list)
    masks: list = field(default_factory=list)
    ranges: list = field(default_factory=list)
    meta: pd.DataFrame = field(default_factory=pd.DataFrame)


def load_library(manifest_path, library_dir, grid, wn_min, wn_max,
                 smooth, smooth_win, smooth_poly,
                 peak_height, peak_prominence, peak_distance,
                 category_filter=None):
    """매니페스트를 읽어 (LibraryBundle, original_manifest, applied_filter) 반환."""
    manifest = pd.read_csv(manifest_path)
    manifest["category"] = manifest["file"].apply(lambda x: x.split("/")[0])

    applied = None
    if category_filter:
        wanted = [c.strip() for c in category_filter.split(",")]
        manifest = manifest[manifest["category"].isin(wanted)].reset_index(drop=True)
        applied = wanted

    bundle = LibraryBundle()
    valid_idx = []
    for i, row in manifest.iterrows():
        fpath = os.path.join(library_dir, row["file"])
        try:
            df_lib = load_csv(fpath, wn_min, wn_max)
            if len(df_lib) < 10:
                continue
            vec, mask = preprocess(df_lib["wn"].values, df_lib["y"].values, grid,
                                   smooth, smooth_win, smooth_poly, return_mask=True)
            bundle.vecs.append(vec)
            bundle.derivs.append(first_derivative(vec, smooth_win, smooth_poly, normalize=False))
            bundle.peaks_list.append(detect_peaks_simple(vec, grid, peak_height,
                                                         peak_prominence, peak_distance))
            bundle.masks.append(mask)
            bundle.ranges.append((float(df_lib["wn"].min()), float(df_lib["wn"].max())))
            valid_idx.append(i)
        except Exception:
            pass

    bundle.meta = manifest.loc[valid_idx].reset_index(drop=True)
    return bundle, manifest, applied


def score_library(bundle, sample_vec, sample_deriv, sample_peaks, sample_mask,
                  sample_range, grid, fp_wsqrt, weights, overlap_only,
                  min_overlap_frac, peak_tol=8.0):
    """라이브러리 전체에 대해 종합 점수를 계산. valid_meta(DataFrame) 반환."""
    n_lib = len(bundle.vecs)
    cos_scores   = np.zeros(n_lib)
    der_scores   = np.zeros(n_lib)
    peak_scores  = np.zeros(n_lib)
    overlap_frac = np.zeros(n_lib)
    excluded     = np.zeros(n_lib, dtype=bool)

    s_lo, s_hi = sample_range
    wn_min, wn_max = float(grid[0]), float(grid[-1])
    grid_span = wn_max - wn_min

    for j in range(n_lib):
        if overlap_only:
            mask = sample_mask & bundle.masks[j]
            ov_lo = max(s_lo, bundle.ranges[j][0])
            ov_hi = min(s_hi, bundle.ranges[j][1])
            ov_span = max(0.0, ov_hi - ov_lo)
            overlap_frac[j] = ov_span / grid_span if grid_span > 0 else 0.0
            if mask.sum() < 10 or overlap_frac[j] < min_overlap_frac:
                excluded[j] = True
                continue
            wn_range = (ov_lo, ov_hi)
        else:
            mask = None
            overlap_frac[j] = 1.0
            wn_range = None

        cos_scores[j]  = weighted_cosine(sample_vec, bundle.vecs[j], fp_wsqrt, mask=mask)
        der_scores[j]  = masked_deriv_cosine(
            sample_deriv, bundle.derivs[j],
            mask if mask is not None else np.ones_like(sample_deriv, dtype=bool)
        )
        peak_scores[j] = peak_match_score(sample_peaks, bundle.peaks_list[j],
                                           tol=peak_tol, wn_range=wn_range)

    w_cos, w_der, w_peak = weights
    composite = w_cos * cos_scores + w_der * der_scores + w_peak * peak_scores

    if overlap_only:
        denom = max(1e-6, 1.0 - min_overlap_frac)
        penalty = 0.7 + 0.3 * np.clip((overlap_frac - min_overlap_frac) / denom, 0, 1)
        composite = composite * penalty

    composite[excluded] = 0.0

    valid_meta = bundle.meta.copy()
    valid_meta["cosine_similarity"] = cos_scores
    valid_meta["cosine_pct"]    = (cos_scores  * 100).round(2)
    valid_meta["deriv_pct"]     = (der_scores  * 100).round(2)
    valid_meta["peak_pct"]      = (peak_scores * 100).round(2)
    valid_meta["overlap_pct"]   = (overlap_frac * 100).round(1)
    valid_meta["composite_pct"] = (composite   * 100).round(2)

    return valid_meta, int(excluded.sum())


def rank_best_per_material(valid_meta, top_n, tier_id_score, tier_id_margin, tier_nomatch):
    """material 단위 최고 점수만 남기고 tier 부여."""
    best = (
        valid_meta.sort_values("composite_pct", ascending=False)
        .drop_duplicates(subset="material")
        .head(top_n)
        .reset_index(drop=True)
    )
    best["category_label"] = best["category"].map(CAT_LABEL).fillna(best["category"])

    tier, reason, margin = assign_confidence_tier(
        best, tier_id_score, tier_id_margin, tier_nomatch
    )

    def row_tier(score):
        if score < tier_nomatch:
            return "미동정 (No reliable match)"
        if score >= tier_id_score:
            return "동정 (Identified)"
        return "후보 복수 (Ambiguous)"

    best["tier"] = best["composite_pct"].apply(row_tier)
    if len(best):
        best.loc[0, "tier"] = tier

    return best, tier, reason, margin


def build_fingerprint_weights(grid, wn_min, fingerprint_weight_mult):
    """지문영역(400~1500cm⁻¹) sqrt 가중 벡터."""
    fp_lo = max(wn_min, 400)
    return fingerprint_weight_vector(grid, fp_lo, 1500, fingerprint_weight_mult)

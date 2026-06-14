# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: FTIR 스펙트럼과 라이브러리 항목 간 유사도 점수 계산 헬퍼.
# 실행 방법: 모듈 — 직접 실행하지 않고 import해서 사용 (ftir_analyze.py / ftir.cli 를 통해 동작)
# ─────────────────────────────────────────────────────────────────────────────
"""Library matching score helpers for FTIR spectra."""

import numpy as np


def masked_deriv_cosine(da, db, mask):
    """겹치는 구간(mask)에서만 미분 코사인. da, db는 비정규화 미분."""
    a = da[mask]
    b = db[mask]
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return float(np.dot(a, b) / (na * nb))

def fingerprint_weight_vector(grid, fp_lo=650, fp_hi=1500, weight=2.0):
    """지문영역(fp_lo~fp_hi)에 가중 배수를 주는 벡터 생성."""
    w = np.ones_like(grid)
    mask = (grid >= fp_lo) & (grid <= fp_hi)
    w[mask] = weight
    return np.sqrt(w)  # 코사인에 적용하기 위해 sqrt (양쪽 곱하면 weight)

def weighted_cosine(a, b, wsqrt, mask=None):
    """지문영역 가중 코사인 유사도. mask가 주어지면 그 구간에서만 계산."""
    if mask is not None:
        a = a[mask]; b = b[mask]; wsqrt = wsqrt[mask]
    aw = a * wsqrt
    bw = b * wsqrt
    na, nb = np.linalg.norm(aw), np.linalg.norm(bw)
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return float(np.dot(aw, bw) / (na * nb))

def peak_match_score(sample_peaks, lib_peaks, tol=8.0, wn_range=None):
    """
    시료 피크와 라이브러리 피크의 공유 비율 (F1).
    wn_range=(lo, hi)가 주어지면 그 구간 안의 피크만 사용 (겹치는 구간 비교).
    """
    sp = np.asarray(sample_peaks, dtype=float)
    lp = np.asarray(lib_peaks, dtype=float)
    if wn_range is not None:
        lo, hi = wn_range
        sp = sp[(sp >= lo) & (sp <= hi)]
        lp = lp[(lp >= lo) & (lp <= hi)]
    if len(sp) == 0 or len(lp) == 0:
        return 0.0

    matched_sample = sum(np.any(np.abs(lp - p) <= tol) for p in sp)
    recall = matched_sample / len(sp)
    matched_lib = sum(np.any(np.abs(sp - q) <= tol) for q in lp)
    precision = matched_lib / len(lp)
    if precision + recall < 1e-12:
        return 0.0
    return 2 * precision * recall / (precision + recall)


# ──────────────────────────────────────────────────────────────
# 신뢰도 등급 판정 — v2 신규
# ──────────────────────────────────────────────────────────────

def assign_confidence_tier(ranked_df, id_score, id_margin, nomatch_score):
    """
    종합 점수(composite_pct) 기준 정렬된 DataFrame에서 신뢰도 등급 판정.
    반환: (tier, reason, margin)
    """
    top1 = ranked_df.iloc[0]["composite_pct"]
    top2 = ranked_df.iloc[1]["composite_pct"] if len(ranked_df) > 1 else 0.0
    margin = top1 - top2

    if top1 < nomatch_score:
        return ("미동정 (No reliable match)",
                f"최고 점수 {top1:.1f}% < 임계 {nomatch_score:.0f}% — 라이브러리에 신뢰할 만한 후보 없음",
                margin)
    if top1 >= id_score and margin >= id_margin:
        return ("동정 (Identified)",
                f"최고 점수 {top1:.1f}% ≥ {id_score:.0f}% 이고 1·2위 차 {margin:.1f}%p ≥ {id_margin:.0f}%p",
                margin)
    return ("후보 복수 (Ambiguous)",
            f"최고 점수 {top1:.1f}%, 1·2위 차 {margin:.1f}%p — 단정 어려움, 분석자 확인 필요",
            margin)


# ──────────────────────────────────────────────────────────────
# 종합 판정 (라이브러리 + 룰 통합)
# ──────────────────────────────────────────────────────────────

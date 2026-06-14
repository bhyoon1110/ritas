# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: 분석 결과를 verdict.txt / verdict.json 파일로 저장하는 리포트 작성기.
# 실행 방법: 모듈 — 직접 실행하지 않고 import해서 사용 (ftir_analyze.py / ftir.cli 를 통해 동작)
# ─────────────────────────────────────────────────────────────────────────────
"""verdict.txt / verdict.json writers."""

import json
import os


def write_verdict_txt(path, sample_label, tier, reason, lib_size, category_filter,
                      best_per_material, cv_text, findings_text, is_identified):
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"시료: {sample_label}\n")
        f.write(f"신뢰도 판정: {tier}\n")
        f.write(f"근거: {reason}\n")
        f.write(f"비교 라이브러리: {lib_size}개")
        if category_filter:
            f.write(f" (카테고리 한정: {category_filter})")
        f.write("\n\n상위 후보:\n")
        for rank, row in best_per_material.head(3).iterrows():
            f.write(f"  #{rank+1} {row['material']} — 종합 {row['composite_pct']:.1f}% "
                    f"(코사인 {row['cosine_pct']:.1f} / 미분 {row['deriv_pct']:.1f} / 피크 {row['peak_pct']:.1f})\n")
        f.write("\n" + "=" * 60 + "\n")
        f.write("종합 판정 (라이브러리 + 룰 통합)\n")
        f.write("=" * 60 + "\n")
        f.write(cv_text + "\n")
        if not is_identified and findings_text:
            f.write("\n" + "=" * 60 + "\n")
            f.write("정성 소견 (확정 동정 아님 — 분석자 참고용)\n")
            f.write("=" * 60 + "\n")
            f.write(findings_text + "\n")


def build_verdict_json(sample_label, tier, reason, is_identified, library_size,
                       category_filter, rules_filter, wn_range, best_per_material,
                       findings, cv, rule_results, min_rule_score_pct=30.0):
    top = None
    if len(best_per_material):
        r0 = best_per_material.iloc[0]
        top = {
            "material": r0["material"],
            "category": r0["category_label"],
            "composite_pct": float(r0["composite_pct"]),
            "cosine_pct": float(r0["cosine_pct"]),
            "deriv_pct": float(r0["deriv_pct"]),
            "peak_pct": float(r0["peak_pct"]),
            "overlap_pct": float(r0["overlap_pct"]),
        }
    return {
        "sample": sample_label,
        "tier": tier,
        "reason": reason,
        "is_identified": bool(cv.get("is_identified", is_identified)),
        "is_library_identified": is_identified,
        "library_size": library_size,
        "category_filter": category_filter,
        "rules_filter": rules_filter,
        "wn_range": list(wn_range),
        "top_candidate": top,
        "findings": findings,
        "combined_verdict": {
            "verdict":     cv["verdict"],
            "confidence":  cv["confidence"],
            "identification_confidence": cv.get("identification_confidence"),
            "library_support": cv.get("library_support"),
            "rule_support": cv.get("rule_support"),
            "is_identified": cv.get("is_identified"),
            "product_profile": cv.get("product_profile"),
            "rule_evidence_summary": cv.get("rule_evidence_summary"),
            "explanation": cv["explanation"],
            "library_says":cv["library_says"],
            "rule_says":   cv["rule_says"],
            "action":      cv["action"],
            "mixture_candidates": cv.get("mixture_candidates"),
        },
        "rule_matches": [
            {k: v for k, v in r.items() if k != "description"}
            for r in rule_results
            if r["score_pct"] >= min_rule_score_pct
        ],
    }


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

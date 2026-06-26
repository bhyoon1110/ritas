# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: FTIR 분석 전체 파이프라인을 엮는 얼린(thin) CLI 오케스트레이션.
#            전처리·피크·막대·비교 HTML을 plot_style 로 생성하고 판정을 출력한다.
# 실행 방법: python -m ftir.cli <DPT파일> [옵션]
#            또는 python ftir_analyze.py <DPT파일> [옵션]  (권장 진입점)
#            주요 옵션: --origin (Origin 스타일), --top N 등
# ─────────────────────────────────────────────────────────────────────────────
"""Command-line orchestration for FTIR analysis (thin)."""

import argparse
import os
import sys

import numpy as np
import pandas as pd

from .findings import (
    assign_group,
    build_findings,
    detect_patterns,
    findings_to_text,
    load_func_groups,
)
from .peaks import detect_peaks_with_fwhm
from rist_common.plotting import write_responsive_html
from .plotting import (
    build_bar_fig,
    build_comparison_fig,
    build_multi_peak_fig,
    build_peak_fig,
    build_preprocess_fig,
    ftir_abs_trans_toggle_js,
)
from .preprocess import (
    detect_peaks_simple,
    first_derivative,
    load_dpt,
    preprocess,
)
from .reporting import build_verdict_json, write_json, write_verdict_txt
from .rule_engine import RuleEngine
from .scoring import (
    build_fingerprint_weights,
    load_library,
    rank_best_per_material,
    score_library,
)
from .verdict import combined_verdict, enrich_combined_verdict, format_combined_verdict


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="DPT 파일을 RIST FTIR 라이브러리와 비교하여 성분을 동정합니다."
    )
    parser.add_argument("dpt_file", nargs="*", help="분석할 DPT 파일 경로 (공백 포함 경로도 가능)")
    parser.add_argument("--output", "-o", default=None,
        help="결과 파일 저장 디렉토리 (기본값: DPT 파일과 같은 디렉토리)")
    parser.add_argument("--label", default=None,
        help="그래프에 표시할 시료 이름 (기본값: 파일명)")
    parser.add_argument("--origin", action="store_true",
        help="Origin(OriginLab) 논문 스타일로 그래프를 그린다 (기본: 원래 디자인)")
    parser.add_argument("--crosshair", dest="crosshair", action="store_true", default=True,
        help="마우스 위치에 십자선 + x/y 좌표를 표시 (기본 켜짐)")
    parser.add_argument("--no-crosshair", dest="crosshair", action="store_false",
        help="마우스 좌표 표시 끄기")

    # ── 라이브러리 ─────────────────────────────────────────────────
    parser.add_argument("--library-dir", default="data/RIST_FTIR_Library",
        help="라이브러리 디렉토리 경로 (기본값: data/RIST_FTIR_Library)")
    parser.add_argument("--category", default=None,
        help="비교를 특정 라이브러리 카테고리로 한정 (쉼표로 복수 지정 가능)")

    # ── 룰 선택 (라이브러리 선택과 대칭) ──────────────────────────
    parser.add_argument("--rules-dir", default=None,
        help="룰 디렉토리 경로 (기본값: 프로젝트 루트의 rules/)")
    parser.add_argument("--rule", action="append", default=None,
        help="적용할 룰을 파일 stem 또는 화합물명/별칭으로 선택. "
             "여러 번 지정하거나 쉼표로 구분 가능. 미지정 시 모든 룰 적용.")
    parser.add_argument("--rule-category", default=None,
        help="룰 YAML의 category 필드 기반 필터 (쉼표로 복수 지정 가능)")
    parser.add_argument("--list-rules", action="store_true",
        help="사용 가능한 룰 목록을 출력하고 종료")
    parser.add_argument("--no-rules", action="store_true",
        help="룰 엔진 비활성화 (라이브러리 매칭만 수행)")

    # ── 분석 파라미터 ──────────────────────────────────────────────
    parser.add_argument("--wn-min", type=float, default=400, help="분석 파수 하한 cm⁻¹ (기본 400)")
    parser.add_argument("--wn-max", type=float, default=4000, help="분석 파수 상한 cm⁻¹ (기본 4000)")
    parser.add_argument("--overlap-only", dest="overlap_only", action="store_true", default=True,
        help="시료·라이브러리 둘 다 측정된 구간에서만 비교 (기본 활성화)")
    parser.add_argument("--no-overlap-only", dest="overlap_only", action="store_false",
        help="전체 분석 구간에서 비교 (측정 안 된 구간은 0으로 처리)")
    parser.add_argument("--min-overlap-frac", type=float, default=0.5,
        help="겹치는 구간이 분석 구간의 이 비율 미만이면 비교 제외 (기본 0.5)")
    parser.add_argument("--top", type=int, default=10, help="상위 매칭 결과 개수 (기본 10)")
    parser.add_argument("--plot-top", type=int, default=5, help="비교 그래프 표시 개수 (기본 5)")
    parser.add_argument("--no-smooth", action="store_true", help="Savitzky-Golay 스무딩 비활성화")
    parser.add_argument("--peak-sensitivity", choices=["low", "medium", "high"], default="medium",
        help="피크 검출 민감도. low=잔피크 억제, medium=기존 기본값, high=작은 피크까지 검출")
    parser.add_argument("--peak-height", type=float, default=None,
        help="피크 최소 높이(정규화 강도, 0~1). 지정 시 --peak-sensitivity 값보다 우선")
    parser.add_argument("--peak-prominence", type=float, default=None,
        help="피크 최소 prominence(정규화 강도, 0~1). 값을 키우면 잔잔한 피크가 줄어듦")
    parser.add_argument("--peak-distance", type=int, default=None,
        help="피크 간 최소 거리(그리드 포인트). 값을 키우면 가까운 잔피크가 줄어듦")

    parser.add_argument("--w-cosine", type=float, default=0.40, help="원본 코사인 가중치 (기본 0.40)")
    parser.add_argument("--w-deriv",  type=float, default=0.30, help="1차 미분 코사인 가중치 (기본 0.30)")
    parser.add_argument("--w-peak",   type=float, default=0.30, help="피크 매칭 가중치 (기본 0.30)")
    parser.add_argument("--fingerprint-weight", type=float, default=2.0,
        help="지문영역(1500-650) 가중 배수. 1.0이면 가중 없음 (기본 2.0)")

    parser.add_argument("--tier-id-score",  type=float, default=75.0,
        help="'동정' 최소 종합 점수 %% (기본 75)")
    parser.add_argument("--tier-id-margin", type=float, default=7.0,
        help="'동정'을 위한 1·2위 최소 점수 차 %% (기본 7)")
    parser.add_argument("--tier-nomatch", type=float, default=65.0,
        help="이 점수 미만이면 '미동정' %% (기본 65)")
    return parser.parse_args()


def _expand_rule_names(rule_args):
    """--rule 인자(여러 번 + 쉼표 혼용)를 평탄화."""
    if not rule_args:
        return None
    out = []
    for v in rule_args:
        out.extend([s.strip() for s in v.split(",") if s.strip()])
    return out or None


def _resolve_rules_dir(arg_value):
    if arg_value:
        return arg_value
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(project_root, "rules")


def _resolve_peak_params(args):
    """CLI 옵션에서 피크 검출 파라미터를 결정한다.

    기존 기본값은 medium으로 유지한다. 잔피크가 많으면 low 또는
    --peak-prominence/--peak-distance 증가를 사용한다.
    """
    presets = {
        "high": {"height": 0.03, "prominence": 0.015, "distance": 10},
        "medium": {"height": 0.05, "prominence": 0.03, "distance": 15},
        "low": {"height": 0.08, "prominence": 0.06, "distance": 25},
    }
    params = dict(presets[args.peak_sensitivity])
    if args.peak_height is not None:
        params["height"] = args.peak_height
    if args.peak_prominence is not None:
        params["prominence"] = args.peak_prominence
    if args.peak_distance is not None:
        params["distance"] = args.peak_distance

    if params["height"] < 0:
        raise ValueError("--peak-height 는 0 이상이어야 합니다.")
    if params["prominence"] < 0:
        raise ValueError("--peak-prominence 는 0 이상이어야 합니다.")
    if params["distance"] < 1:
        raise ValueError("--peak-distance 는 1 이상이어야 합니다.")
    return params


def _print_rule_listing(rules_dir):
    rules = RuleEngine.list_available_rules(rules_dir)
    print(f"\n사용 가능한 룰 ({rules_dir}/):")
    if not rules:
        print("  (룰 파일 없음)")
        return
    for r in rules:
        aliases = f" / 별칭: {', '.join(r['aliases'])}" if r["aliases"] else ""
        cat = f"  [{r['category']}]" if r["category"] else ""
        print(f"  • {r['stem']:<25} {r['compound']}{cat}{aliases}")
    print(f"\n사용 예시:")
    print(f"  --rule phenolic_foam              # stem으로 선택")
    print(f"  --rule 'Phenolic Foam'            # 화합물명으로 선택")
    print(f"  --rule phenolic_foam --rule pet    # 여러 개")
    print(f"  --rule-category engineering_plastic  # 카테고리로 필터")


# ──────────────────────────────────────────────────────────────
# 메인 파이프라인
# ──────────────────────────────────────────────────────────────

def _run_single(dpt_path, args, rules_dir, rule_names, rule_categories):
    if not os.path.isfile(dpt_path):
        print(f"[오류] DPT 파일을 찾을 수 없습니다: {dpt_path}", file=sys.stderr)
        sys.exit(1)

    stem = os.path.splitext(os.path.basename(dpt_path))[0]
    output_dir = (os.path.join(args.output, stem) if args.output
                  else os.path.join(os.path.dirname(os.path.abspath(dpt_path)), stem))
    os.makedirs(output_dir, exist_ok=True)
    sample_label = args.label if args.label else stem

    WN_MIN, WN_MAX = args.wn_min, args.wn_max
    N_GRID = max(1750, int((WN_MAX - WN_MIN) / 2.0))
    SMOOTH = not args.no_smooth
    SMOOTH_WIN, SMOOTH_POLY = 11, 3
    TOP_N, PLOT_TOP_N = args.top, args.plot_top
    LIBRARY_DIR = args.library_dir
    MANIFEST = os.path.join(LIBRARY_DIR, "manifest.csv")
    FUNC_GROUPS_FILE = "data/func_groups.csv"

    try:
        peak_params = _resolve_peak_params(args)
    except ValueError as exc:
        print(f"[오류] {exc}", file=sys.stderr)
        sys.exit(1)
    PEAK_HEIGHT = peak_params["height"]
    PEAK_PROMINENCE = peak_params["prominence"]
    PEAK_DISTANCE = peak_params["distance"]
    PEAK_TOL = 8.0

    w_sum = args.w_cosine + args.w_deriv + args.w_peak
    if w_sum <= 0:
        print("[오류] 가중치 합이 0입니다.", file=sys.stderr)
        sys.exit(1)
    W = (args.w_cosine / w_sum, args.w_deriv / w_sum, args.w_peak / w_sum)

    GRID = np.linspace(WN_MIN, WN_MAX, N_GRID)
    FP_WSQRT = build_fingerprint_weights(GRID, WN_MIN, args.fingerprint_weight)

    # ── 1. 시료 로드 및 전처리 ───────────────────────────────────────
    print(f"[1/5] DPT 파일 로드: {dpt_path}")
    print(f"       피크 검출 설정: sensitivity={args.peak_sensitivity}, "
          f"height={PEAK_HEIGHT:g}, prominence={PEAK_PROMINENCE:g}, "
          f"distance={PEAK_DISTANCE}")
    raw = load_dpt(dpt_path, WN_MIN, WN_MAX)
    if len(raw) < 10:
        print("[오류] DPT 파일에 유효한 데이터가 충분하지 않습니다.", file=sys.stderr)
        sys.exit(1)
    sample_vec, sample_mask = preprocess(raw["wn"].values, raw["y"].values, GRID,
                                          SMOOTH, SMOOTH_WIN, SMOOTH_POLY, return_mask=True)
    sample_deriv = first_derivative(sample_vec, SMOOTH_WIN, SMOOTH_POLY, normalize=False)
    sample_peaks = detect_peaks_simple(sample_vec, GRID, PEAK_HEIGHT, PEAK_PROMINENCE, PEAK_DISTANCE)
    s_lo, s_hi = float(raw["wn"].min()), float(raw["wn"].max())
    print(f"       로드 완료: {len(raw)} 포인트 → 그리드 {N_GRID} 포인트, "
          f"측정범위 {s_lo:.0f}~{s_hi:.0f}cm⁻¹, 시료 피크 {len(sample_peaks)}개")

    fig_pre = build_preprocess_fig(raw, sample_vec, GRID, sample_label, WN_MIN, WN_MAX)
    pre_html = os.path.join(output_dir, f"{stem}_preprocess.html")
    write_responsive_html(fig_pre, pre_html, div_id="pre-plot", origin=args.origin,
                          crosshair=args.crosshair, title_edit=True,
                          legend_text_edit=True,
                          trace_highlight=True,
                          image_filename=f"{stem}_preprocess",
                          image_format_selector=True,
                          post_body_html=ftir_abs_trans_toggle_js(
                              "pre-plot",
                              yaxis_titles={
                                  "yaxis": {
                                      "absorbance": "Absorbance",
                                      "transmittance": "Transmittance (%)",
                                  },
                                  "yaxis2": {
                                      "absorbance": "Normalized Absorbance",
                                      "transmittance": "Transmittance (%)",
                                  },
                              },
                          ))
    print(f"       전처리 그래프 저장: {pre_html}")

    # ── 2. 피크 검출 및 작용기 분석 ─────────────────────────────────
    print(f"[2/5] 피크 검출 및 작용기 분석")
    func_groups = load_func_groups(FUNC_GROUPS_FILE)
    peak_idx, peak_wn, peak_val, peak_fwhm = detect_peaks_with_fwhm(
        sample_vec, GRID, PEAK_HEIGHT, PEAK_PROMINENCE, PEAK_DISTANCE
    )

    from .findings import assign_group
    rows = []
    for wn, val, fwhm in sorted(zip(peak_wn, peak_val, peak_fwhm), key=lambda x: -x[1]):
        name, _, note = assign_group(wn, func_groups)
        rows.append({"Wavenumber (cm⁻¹)": f"{wn:.1f}", "Intensity": f"{val:.3f}",
                     "FWHM (cm⁻¹)": f"{fwhm:.1f}", "Assigned Group": name, "Note": note})
    df_peaks = pd.DataFrame(rows)
    peaks_csv = os.path.join(output_dir, f"{stem}_peaks.csv")
    df_peaks.to_csv(peaks_csv, index=False, encoding="utf-8-sig")
    print(f"       검출된 피크: {len(peak_idx)}개 → {peaks_csv}")

    patterns = detect_patterns(peak_wn, peak_val)
    print("\n" + "═" * 65)
    print("  복수 피크 패턴 분석 (Functional Group Pattern Matching)")
    print("═" * 65)
    if patterns:
        for conf, pname, evidence in patterns:
            bar = "█" * int(conf * 20)
            print(f"  {conf*100:5.1f}% {bar:<20}  {pname}")
            print(f"         └ {evidence}")
    else:
        print("  패턴 없음 — 파라미터 조정 필요")
    print("═" * 65 + "\n")

    if patterns:
        df_patterns = pd.DataFrame(
            [{"Confidence (%)": f"{c*100:.1f}", "Pattern": p, "Evidence": e}
             for c, p, e in patterns]
        )
        patterns_csv = os.path.join(output_dir, f"{stem}_patterns.csv")
        df_patterns.to_csv(patterns_csv, index=False, encoding="utf-8-sig")
        print(f"       패턴 분석 저장: {patterns_csv}")

    fig_peak = build_peak_fig(
        sample_vec, GRID, peak_idx, peak_wn, peak_val, peak_fwhm,
        func_groups, sample_label, WN_MIN, WN_MAX,
    )
    peak_html = os.path.join(output_dir, f"{stem}_peaks.html")
    write_responsive_html(
        fig_peak, peak_html, div_id="peak-plot", origin=args.origin,
        crosshair=args.crosshair,
        responsive_legend=False,
        title_edit=True,
        legend_text_edit=True,
        peak_editor=True,
        image_filename=f"{stem}_peaks",
        image_format_selector=True,
        post_body_html=(
            ftir_abs_trans_toggle_js(
                "peak-plot",
                yaxis_titles={
                    "yaxis": {
                        "absorbance": "Normalized Absorbance",
                        "transmittance": "Transmittance (%)",
                    },
                },
            )
        ),
        config={"scrollZoom": True},
    )
    print(f"       피크 그래프 저장: {peak_html}")

    # ── 2b. 룰 기반 동정 ─────────────────────────────────────────────
    rule_results = []
    rules_filter_info = None
    if args.no_rules:
        print("[2b] 룰 엔진 비활성화 (--no-rules)")
    elif os.path.isdir(rules_dir):
        filter_msg = ""
        if rule_names: filter_msg += f", 룰 선택={rule_names}"
        if rule_categories: filter_msg += f", 룰 카테고리={rule_categories}"
        print(f"[2b] 룰 기반 동정: {rules_dir}{filter_msg}")
        engine = RuleEngine(rules_dir, rule_names=rule_names,
                            rule_categories=rule_categories)
        if len(engine.rules) == 0:
            print(f"       [경고] 선택된 룰이 0개입니다. 필터를 확인하세요. "
                  f"--list-rules 로 사용 가능한 룰을 확인할 수 있습니다.")
        rule_results = engine.evaluate(peak_wn, peak_val)
        rules_filter_info = {
            "rule_names": rule_names, "rule_categories": rule_categories,
            "loaded": [r.get("compound") for r in engine.rules],
            "skipped": [s["compound"] for s in engine.skipped],
        }
        rule_output = RuleEngine.format_results(rule_results, min_score_pct=30.0)
        print(rule_output)

        rule_json_path = os.path.join(output_dir, f"{stem}_rule_matches.json")
        write_json(rule_json_path, rule_results)
        print(f"       룰 결과 저장: {rule_json_path}")
    else:
        print(f"[2b] 룰 디렉토리 없음 ({rules_dir}) — 스킵")

    # ── 3. 라이브러리 로드 ──────────────────────────────────────────
    print(f"[3/5] 라이브러리 로드: {LIBRARY_DIR}")
    if not os.path.isfile(MANIFEST):
        print(f"[오류] manifest 파일을 찾을 수 없습니다: {MANIFEST}", file=sys.stderr)
        sys.exit(1)

    bundle, full_manifest, applied_cats = load_library(
        MANIFEST, LIBRARY_DIR, GRID, WN_MIN, WN_MAX,
        SMOOTH, SMOOTH_WIN, SMOOTH_POLY,
        PEAK_HEIGHT, PEAK_PROMINENCE, PEAK_DISTANCE,
        category_filter=args.category,
    )
    if applied_cats:
        print(f"       카테고리 필터 적용: {applied_cats} → {len(bundle.meta)}개 선택")
    if len(bundle.vecs) == 0:
        print(f"[오류] 로드된 라이브러리 스펙트럼이 0개입니다.", file=sys.stderr)
        sys.exit(1)
    print(f"       라이브러리 로드: {len(bundle.vecs)}개 스펙트럼")

    # ── 4. 점수 계산 ────────────────────────────────────────────────
    mode_str = "겹치는 구간만" if args.overlap_only else "전체 구간"
    print(f"[4/5] 다중 지표 점수 계산 ({mode_str}, 코사인 {W[0]:.2f} + 미분 {W[1]:.2f} + 피크 {W[2]:.2f}, "
          f"지문가중 x{args.fingerprint_weight})")

    valid_meta, n_excluded = score_library(
        bundle, sample_vec, sample_deriv, sample_peaks, sample_mask,
        (s_lo, s_hi), GRID, FP_WSQRT, W,
        args.overlap_only, args.min_overlap_frac, PEAK_TOL,
    )
    if n_excluded:
        print(f"       겹침 부족으로 제외된 항목: {n_excluded}개 "
              f"(측정범위 겹침 < {args.min_overlap_frac*100:.0f}%)")

    best_per_material, tier, reason, margin = rank_best_per_material(
        valid_meta, TOP_N, args.tier_id_score, args.tier_id_margin, args.tier_nomatch
    )

    print(f"\n{'Rank':<5} {'종합%':>7} {'코사인':>7} {'미분':>6} {'피크':>6} {'겹침':>6}  "
          f"{'Material':<32} {'Category':<22}")
    print("-" * 108)
    for rank, row in best_per_material.iterrows():
        print(f"{rank+1:<5} {row['composite_pct']:>6.1f}% {row['cosine_pct']:>6.1f}% "
              f"{row['deriv_pct']:>5.1f}% {row['peak_pct']:>5.1f}% {row['overlap_pct']:>5.0f}%  "
              f"{row['material']:<32} {row['category_label']:<22}")

    print("\n" + "━" * 65)
    print(f"  ▶ 신뢰도 판정: {tier}")
    print(f"     {reason}")
    print("━" * 65 + "\n")

    # ── 소견 + 종합 판정 ───────────────────────────────────────────
    top_lib_peaks = []
    if len(best_per_material) > 0:
        top_file = best_per_material.iloc[0]["file"]
        match_pos = valid_meta.index[valid_meta["file"] == top_file].tolist()
        if match_pos:
            top_lib_peaks = bundle.peaks_list[match_pos[0]]

    findings = build_findings(peak_wn, peak_val, patterns, best_per_material,
                              top_lib_peaks, tier)
    findings_text = findings_to_text(findings, sample_label)

    is_identified = tier.startswith("동정")
    if not is_identified and findings_text:
        print("┌─ 정성 소견 (확정 동정 아님) " + "─" * 35)
        for ln in findings_text.split("\n"):
            print(f"│ {ln}")
        print("└" + "─" * 64 + "\n")

    lib_top_material = best_per_material.iloc[0]["material"] if len(best_per_material) else "N/A"
    lib_top_score    = float(best_per_material.iloc[0]["composite_pct"]) if len(best_per_material) else 0.0
    cv = combined_verdict(tier, lib_top_material, lib_top_score, rule_results)
    cv = enrich_combined_verdict(cv, tier, rule_results, findings)
    cv_text = format_combined_verdict(cv)
    print(cv_text)

    # ── 5. 결과 저장 ─────────────────────────────────────────────────
    print(f"[5/5] 결과 저장 → {output_dir}")

    display_cols = ["material", "category_label", "source",
                    "composite_pct", "cosine_pct", "deriv_pct", "peak_pct",
                    "overlap_pct", "tier", "file"]
    csv_out = os.path.join(output_dir, f"{stem}_matches.csv")
    best_per_material[display_cols].to_csv(csv_out, index=False, encoding="utf-8-sig")
    print(f"       매칭 결과 저장: {csv_out}")

    summary_txt = os.path.join(output_dir, f"{stem}_verdict.txt")
    write_verdict_txt(summary_txt, sample_label, tier, reason, len(bundle.vecs),
                      args.category, best_per_material, cv_text, findings_text, is_identified)
    print(f"       신뢰도 요약 저장: {summary_txt}")

    verdict_json = build_verdict_json(
        sample_label, tier, reason, is_identified, len(bundle.vecs),
        args.category, rules_filter_info, [WN_MIN, WN_MAX],
        best_per_material, findings, cv, rule_results,
    )
    json_out = os.path.join(output_dir, f"{stem}_verdict.json")
    write_json(json_out, verdict_json)
    print(f"       구조화 JSON 저장: {json_out}")

    fig_bar = build_bar_fig(best_per_material, TOP_N, sample_label)
    bar_html = os.path.join(output_dir, f"{stem}_bar.html")
    write_responsive_html(fig_bar, bar_html, div_id="bar-plot", origin=args.origin,
                          responsive_legend=False, title_edit=True,
                          image_filename=f"{stem}_bar",
                          image_format_selector=True)
    print(f"       바 차트 저장: {bar_html}")

    fig_cmp = build_comparison_fig(
        sample_vec, GRID, best_per_material, PLOT_TOP_N, sample_label,
        LIBRARY_DIR, WN_MIN, WN_MAX, PEAK_HEIGHT, PEAK_PROMINENCE, PEAK_DISTANCE,
        SMOOTH, SMOOTH_WIN, SMOOTH_POLY,
    )
    html_out = os.path.join(output_dir, f"{stem}_comparison.html")
    write_responsive_html(
        fig_cmp, html_out, div_id="cmp-plot", origin=args.origin,
        crosshair=args.crosshair,
        title_edit=True,
        legend_text_edit=True,
        trace_highlight=True,
        image_filename=f"{stem}_comparison",
        image_format_selector=True,
        post_body_html=ftir_abs_trans_toggle_js(
            "cmp-plot",
            yaxis_titles={
                "yaxis": {
                    "absorbance": "Normalized Absorbance (offset)",
                    "transmittance": "Transmittance (%) (offset)",
                },
            },
        ),
        config={"scrollZoom": True},
    )
    print(f"       비교 그래프 저장: {html_out}")

    print("\n분석 완료!")
    print(f"출력 파일 목록:")
    for fname in [
        f"{stem}_preprocess.html", f"{stem}_peaks.csv", f"{stem}_peaks.html",
        f"{stem}_patterns.csv", f"{stem}_matches.csv", f"{stem}_verdict.txt",
        f"{stem}_verdict.json", f"{stem}_bar.html", f"{stem}_comparison.html",
        f"{stem}_rule_matches.json",
    ]:
        fpath = os.path.join(output_dir, fname)
        if os.path.isfile(fpath):
            print(f"  ✓ {fpath}")


def _run_multi_peak_overlay(dpt_paths, args):
    WN_MIN, WN_MAX = args.wn_min, args.wn_max
    N_GRID = max(1750, int((WN_MAX - WN_MIN) / 2.0))
    SMOOTH = not args.no_smooth
    SMOOTH_WIN, SMOOTH_POLY = 11, 3
    FUNC_GROUPS_FILE = "data/func_groups.csv"

    try:
        peak_params = _resolve_peak_params(args)
    except ValueError as exc:
        print(f"[오류] {exc}", file=sys.stderr)
        sys.exit(1)
    PEAK_HEIGHT = peak_params["height"]
    PEAK_PROMINENCE = peak_params["prominence"]
    PEAK_DISTANCE = peak_params["distance"]

    GRID = np.linspace(WN_MIN, WN_MAX, N_GRID)
    func_groups = load_func_groups(FUNC_GROUPS_FILE)
    output_dir = args.output or os.path.join(os.getcwd(), "outputs", "multi_samples")
    os.makedirs(output_dir, exist_ok=True)

    print(f"[1/2] DPT 파일 {len(dpt_paths)}개 로드 및 피크 검출")
    print(f"      피크 검출 설정: sensitivity={args.peak_sensitivity}, "
          f"height={PEAK_HEIGHT:g}, prominence={PEAK_PROMINENCE:g}, "
          f"distance={PEAK_DISTANCE}")

    samples = []
    rows = []
    for path in dpt_paths:
        if not os.path.isfile(path):
            print(f"[오류] DPT 파일을 찾을 수 없습니다: {path}", file=sys.stderr)
            sys.exit(1)
        stem = os.path.splitext(os.path.basename(path))[0]
        raw = load_dpt(path, WN_MIN, WN_MAX)
        if len(raw) < 10:
            print(f"[오류] DPT 파일에 유효한 데이터가 충분하지 않습니다: {path}",
                  file=sys.stderr)
            sys.exit(1)
        sample_vec, _ = preprocess(
            raw["wn"].values, raw["y"].values, GRID,
            SMOOTH, SMOOTH_WIN, SMOOTH_POLY, return_mask=True,
        )
        peak_idx, peak_wn, peak_val, peak_fwhm = detect_peaks_with_fwhm(
            sample_vec, GRID, PEAK_HEIGHT, PEAK_PROMINENCE, PEAK_DISTANCE
        )
        label = stem
        samples.append({
            "path": path,
            "label": label,
            "grid": GRID,
            "sample_vec": sample_vec,
            "peak_idx": peak_idx,
            "peak_wn": peak_wn,
            "peak_val": peak_val,
            "peak_fwhm": peak_fwhm,
        })
        for wn, val, fwhm in sorted(zip(peak_wn, peak_val, peak_fwhm), key=lambda x: -x[1]):
            name, _, note = assign_group(wn, func_groups)
            rows.append({
                "Sample": label,
                "Wavenumber (cm⁻¹)": f"{wn:.1f}",
                "Intensity": f"{val:.3f}",
                "FWHM (cm⁻¹)": f"{fwhm:.1f}",
                "Assigned Group": name,
                "Note": note,
            })
        print(f"      {label}: {len(raw)} 포인트, 피크 {len(peak_idx)}개")

    peaks_csv = os.path.join(output_dir, "multi_samples_peaks.csv")
    pd.DataFrame(rows).to_csv(peaks_csv, index=False, encoding="utf-8-sig")

    print("[2/2] 여러 샘플 피크 그래프 생성")
    fig_peak = build_multi_peak_fig(samples, func_groups, WN_MIN, WN_MAX)
    peak_html = os.path.join(output_dir, "multi_samples_peaks.html")
    write_responsive_html(
        fig_peak, peak_html, div_id="peak-plot", origin=args.origin,
        crosshair=args.crosshair,
        responsive_legend=False,
        title_edit=True,
        legend_text_edit=True,
        peak_editor=True,
        image_filename="multi_samples_peaks",
        image_format_selector=True,
        post_body_html=ftir_abs_trans_toggle_js(
            "peak-plot",
            yaxis_titles={
                "yaxis": {
                    "absorbance": "Normalized Absorbance",
                    "transmittance": "Transmittance (%)",
                },
            },
        ),
        config={"scrollZoom": True},
    )
    print(f"      피크 CSV 저장: {peaks_csv}")
    print(f"      피크 그래프 저장: {peak_html}")


def main():
    args = parse_args()
    rules_dir = _resolve_rules_dir(args.rules_dir)

    if args.list_rules:
        _print_rule_listing(rules_dir)
        return

    if not args.dpt_file:
        print("[오류] DPT 파일을 지정하세요. (--list-rules 로 룰 목록 확인 가능)",
              file=sys.stderr)
        sys.exit(2)

    rule_names = _expand_rule_names(args.rule)
    rule_categories = _expand_rule_names([args.rule_category]) if args.rule_category else None

    if len(args.dpt_file) > 1 and all(os.path.isfile(path) for path in args.dpt_file):
        _run_multi_peak_overlay(args.dpt_file, args)
        return

    dpt_path = " ".join(args.dpt_file)
    _run_single(dpt_path, args, rules_dir, rule_names, rule_categories)


if __name__ == "__main__":
    main()

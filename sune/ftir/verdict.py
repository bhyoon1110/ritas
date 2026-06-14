# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: 라이브러리 매칭과 룰 기반 식별 결과를 통합해 최종 판정(verdict)을 내리는 로직.
# 실행 방법: 모듈 — 직접 실행하지 않고 import해서 사용 (ftir_analyze.py / ftir.cli 를 통해 동작)
# ─────────────────────────────────────────────────────────────────────────────
"""Integrated verdict logic for library and rule-based FTIR identification."""


def _rule_label(r: dict) -> str:
    """compound_display(서브타입 포함) 가 있으면 우선 사용, 없으면 compound."""
    return r.get("compound_display") or r.get("compound", "?")


def combined_verdict(lib_tier: str, lib_top_material: str, lib_top_score: float,
                     rule_results: list[dict]) -> dict:
    """
    라이브러리 기반 신뢰도 등급과 룰 기반 결과를 교차 검증하여 최종 종합 판정.

    반환 dict:
      verdict       : 최종 판정 문자열
      confidence    : "HIGH" / "MEDIUM" / "LOW"
      explanation   : 판정 근거 (한국어)
      library_says  : 라이브러리 판정 요약
      rule_says     : 룰 판정 요약 (해당 없으면 None)
      action        : 권장 후속 조치
    """
    # 룰 결과 분류
    strong_rules = [r for r in rule_results if r.get("verdict") == "Rule Match ✓"]
    partial_rules = [r for r in rule_results
                     if r.get("verdict") in ("부분 일치 (금지 피크 존재)", "약한 일치 (참고용)")]
    no_rules_registered = len(rule_results) == 0

    is_lib_id        = lib_tier.startswith("동정")
    is_lib_ambiguous = "Ambiguous" in lib_tier
    is_lib_nomatch   = "미동정" in lib_tier

    lib_summary = f"라이브러리: {lib_top_material} ({lib_top_score:.1f}%) — {lib_tier}"

    # ── 복수 강매칭 룰: 혼합물 가능성 ───────────────────────────────
    if len(strong_rules) >= 2:
        names = [_rule_label(r) for r in strong_rules]
        scores = [r["score_pct"] for r in strong_rules]
        rule_summary = "룰: " + " / ".join(f"{n} ({s:.1f}%)" for n, s in zip(names, scores))
        return dict(
            verdict=f"복수 룰 동시 매칭 (혼합물 가능성)  [{' + '.join(names)}]",
            confidence="MEDIUM",
            explanation=(
                f"{len(strong_rules)}개 룰이 동시에 강하게 매칭됨 — 단일 성분이 아닌 혼합물 또는 "
                f"복합 처방 제품일 가능성. 라이브러리: {lib_top_material}({lib_top_score:.1f}%)."
            ),
            library_says=lib_summary, rule_says=rule_summary,
            action="혼합물 가능성 검토 — 각 룰의 필수 피크가 모두 실제로 존재하는지 육안 확인 권장",
            mixture_candidates=[{"compound": n, "score_pct": s} for n, s in zip(names, scores)],
        )

    # ── 케이스별 판정 ──────────────────────────────────────────────
    if no_rules_registered:
        # 룰이 하나도 없는 경우
        if is_lib_id:
            return dict(verdict="동정 (라이브러리)", confidence="MEDIUM",
                        explanation=f"라이브러리에서 {lib_top_material}으로 동정됨. "
                                    f"해당 물질의 룰이 미등록되어 룰 검증 불가.",
                        library_says=lib_summary, rule_says=None,
                        action="rules/ 폴더에 해당 물질 YAML 룰 등록 권장")
        else:
            return dict(verdict="미동정 (룰 없음)", confidence="LOW",
                        explanation="라이브러리 및 룰 모두 신뢰할 만한 결과 없음.",
                        library_says=lib_summary, rule_says=None,
                        action="라이브러리 보강 또는 룰 등록 필요")

    if is_lib_id and strong_rules:
        # 라이브러리와 룰 모두 동정
        rule_compound = _rule_label(strong_rules[0])
        rule_score    = strong_rules[0]["score_pct"]
        rule_summary  = f"룰: {rule_compound} ({rule_score:.1f}%) — Rule Match ✓"
        # 물질명 교차 확인 (포함 관계)
        name_match = (rule_compound.lower() in lib_top_material.lower() or
                      lib_top_material.lower() in rule_compound.lower() or
                      any(a.lower() in lib_top_material.lower()
                          for a in strong_rules[0].get("aliases", [])))
        if name_match:
            return dict(verdict=f"강한 동정 ✓✓  [{rule_compound}]",
                        confidence="HIGH",
                        explanation=f"룰({rule_score:.1f}%, 필수 피크 모두 일치)과 "
                                    f"라이브러리({lib_top_score:.1f}%)가 모두 동일 물질로 수렴. 신뢰도 높음.",
                        library_says=lib_summary, rule_says=rule_summary,
                        action="동정 완료")
        else:
            # 룰(피크 어사인)을 우선 — 라이브러리는 참고
            return dict(verdict=f"룰 기반 동정 ✓  [{rule_compound}]  (라이브러리는 {lib_top_material})",
                        confidence="HIGH",
                        explanation=f"룰에서 {rule_compound}({rule_score:.1f}%)의 필수 피크가 모두 검출됨. "
                                    f"라이브러리 1순위({lib_top_material} {lib_top_score:.1f}%)와는 상이하나, "
                                    f"피크 어사인 기반 판정을 우선 채택. 라이브러리는 유사 스펙트럼 또는 "
                                    f"누락된 후보일 가능성.",
                        library_says=lib_summary, rule_says=rule_summary,
                        action=f"라이브러리에 {rule_compound} 스펙트럼 보강 또는 라이브러리 후보 재검토 권장")

    if is_lib_id and partial_rules:
        rule_compound = _rule_label(partial_rules[0])
        rule_score    = partial_rules[0]["score_pct"]
        rule_summary  = f"룰: {rule_compound} ({rule_score:.1f}%) — {partial_rules[0]['verdict']}"
        return dict(verdict=f"라이브러리 동정 / 룰 부분 일치  [{lib_top_material}]",
                    confidence="MEDIUM",
                    explanation=f"라이브러리: {lib_top_material}({lib_top_score:.1f}%) 동정. "
                                f"룰({rule_compound})은 부분 일치 — 금지 피크 또는 일부 필수 피크 미검출. "
                                f"첨가제·혼합물 가능성 고려.",
                    library_says=lib_summary, rule_says=rule_summary,
                    action="금지 피크 원인 확인 권장")

    if is_lib_id and not strong_rules and not partial_rules:
        rule_summary = f"룰: {_rule_label(rule_results[0])} ({rule_results[0]['score_pct']:.1f}%) — 불일치"
        return dict(verdict=f"라이브러리 동정 / 룰 불일치  [{lib_top_material}]",
                    confidence="MEDIUM",
                    explanation=f"라이브러리: {lib_top_material}({lib_top_score:.1f}%) 동정. "
                                f"하지만 등록된 룰과 불일치 — 오탐 가능성 검토 필요.",
                    library_says=lib_summary, rule_says=rule_summary,
                    action="라이브러리 스펙트럼 품질 및 룰 재검토")

    if is_lib_ambiguous and strong_rules:
        rule_compound = _rule_label(strong_rules[0])
        rule_score    = strong_rules[0]["score_pct"]
        rule_summary  = f"룰: {rule_compound} ({rule_score:.1f}%) — Rule Match ✓"
        return dict(verdict=f"룰 기반 동정 ✓  [{rule_compound}]",
                    confidence="HIGH",
                    explanation=f"룰에서 {rule_compound}({rule_score:.1f}%)의 필수 피크가 모두 검출됨. "
                                f"라이브러리는 애매({lib_top_material} {lib_top_score:.1f}%)하나 "
                                f"피크 어사인을 신뢰. 동정 완료.",
                    library_says=lib_summary, rule_says=rule_summary,
                    action="동정 완료")

    if is_lib_ambiguous and partial_rules:
        rule_compound = _rule_label(partial_rules[0])
        rule_score    = partial_rules[0]["score_pct"]
        rule_summary  = f"룰: {rule_compound} ({rule_score:.1f}%) — {partial_rules[0]['verdict']}"
        return dict(verdict=f"복수 후보 (확인 필요)  [{lib_top_material} / {rule_compound}]",
                    confidence="LOW",
                    explanation=f"라이브러리({lib_top_score:.1f}%)도 애매, "
                                f"룰도 부분 일치({rule_score:.1f}%)에 그침. "
                                f"단일 성분으로 설명 어려운 스펙트럼.",
                    library_says=lib_summary, rule_says=rule_summary,
                    action="혼합물 가능성 고려, 추가 분석 필요")

    if is_lib_nomatch and strong_rules:
        rule_compound = _rule_label(strong_rules[0])
        rule_score    = strong_rules[0]["score_pct"]
        rule_summary  = f"룰: {rule_compound} ({rule_score:.1f}%) — Rule Match ✓"
        return dict(verdict=f"룰 기반 동정 ✓  [{rule_compound}]",
                    confidence="HIGH",
                    explanation=f"룰에서 {rule_compound}({rule_score:.1f}%)의 필수 피크가 모두 검출됨 — 동정. "
                                f"라이브러리는 미동정이나 피크 어사인 기반 판정을 우선 채택. "
                                f"라이브러리에 해당 물질 스펙트럼 보강 권장.",
                    library_says=lib_summary, rule_says=rule_summary,
                    action=f"라이브러리에 {rule_compound} 스펙트럼 추가 후 재분석")

    if is_lib_nomatch and partial_rules:
        rule_compound = _rule_label(partial_rules[0])
        rule_score    = partial_rules[0]["score_pct"]
        rule_summary  = f"룰: {rule_compound} ({rule_score:.1f}%) — {partial_rules[0]['verdict']}"
        return dict(verdict=f"약한 룰 후보  [{rule_compound}]",
                    confidence="LOW",
                    explanation=f"라이브러리 미동정, 룰에서 {rule_compound} 부분 일치({rule_score:.1f}%). "
                                f"금지 피크 또는 필수 피크 미검출로 확정 불가.",
                    library_says=lib_summary, rule_says=rule_summary,
                    action="금지 피크 원인 파악 및 룰 재검토")

    # 기본: 모두 미동정
    rule_summary = (f"룰: {_rule_label(rule_results[0])} ({rule_results[0]['score_pct']:.1f}%) — 불일치"
                    if rule_results else None)
    return dict(verdict="미동정",
                confidence="LOW",
                explanation="라이브러리 및 룰 모두 신뢰할 만한 매칭 없음.",
                library_says=lib_summary, rule_says=rule_summary,
                action="라이브러리/룰 보강 또는 상보적 분석 기법 활용")

def format_combined_verdict(cv: dict) -> str:
    """combined_verdict() 결과를 콘솔 출력용 문자열로 변환."""
    CONF_ICON = {"HIGH": "●●●", "MEDIUM": "●●○", "LOW": "●○○"}
    icon = CONF_ICON.get(cv["confidence"], "?")
    lines = [
        "╔" + "═" * 63 + "╗",
        f"║  종합 판정 (라이브러리 + 룰 통합)  신뢰도 {icon}",
        "╠" + "═" * 63 + "╣",
        f"║  {cv['verdict']}",
        "╠" + "═" * 63 + "╣",
        f"║  근거: {cv['explanation']}",
        f"║  {cv['library_says']}",
    ]
    if cv.get("rule_says"):
        lines.append(f"║  {cv['rule_says']}")
    if cv.get("mixture_candidates"):
        lines.append("╠" + "═" * 63 + "╣")
        lines.append("║  ▶ 혼합물 후보:")
        for m in cv["mixture_candidates"]:
            lines.append(f"║     - {m['compound']}  ({m['score_pct']:.1f}%)")
    lines += [
        "╠" + "═" * 63 + "╣",
        f"║  ▶ 권장 후속 조치: {cv['action']}",
        "╚" + "═" * 63 + "╝",
    ]
    return "\n".join(lines)

def summarize_rule_evidence(rule_result: dict | None) -> list[dict]:
    if not rule_result:
        return []
    evidence = []
    for key, role in [
        ("matched_required", "required"),
        ("matched_supporting", "supporting"),
        ("matched_context_markers", "context_marker"),
        ("triggered_warnings", "warning"),
        ("triggered_forbidden", "hard_forbidden"),
    ]:
        for peak in rule_result.get(key, []):
            evidence.append({
                "role": role,
                "label": peak.get("label"),
                "center": peak.get("center"),
                "intensity": peak.get("intensity"),
                "interpretation": peak.get("interpretation", ""),
            })
    return evidence

def build_product_profile(rule_results: list[dict], findings: dict | None = None) -> dict:
    best_rule = rule_results[0] if rule_results else None
    additives = (findings or {}).get("additives_and_process_markers", [])
    context_markers = (best_rule or {}).get("matched_context_markers", [])
    marker_names = [m.get("name") for m in additives if m.get("name")]
    context_interpretations = [m.get("interpretation") for m in context_markers if m.get("interpretation")]

    profile = {
        "base_material": best_rule.get("compound") if best_rule else None,
        "commercial_product_likelihood": "HIGH" if additives or context_markers else "MEDIUM",
        "formulation_markers": marker_names,
        "rule_context_interpretations": context_interpretations,
    }
    if best_rule and best_rule.get("compound") == "Phenolic Foam" and additives:
        profile["summary"] = "상업용 첨가제/공정 흔적이 동반된 Phenolic Foam"
    elif best_rule:
        profile["summary"] = best_rule.get("compound")
    else:
        profile["summary"] = None
    return profile

def enrich_combined_verdict(cv: dict, lib_tier: str, rule_results: list[dict],
                            findings: dict | None = None) -> dict:
    """종합 판정 JSON에 분리 신뢰도 필드를 보강."""
    out = dict(cv)
    strong_rules = [r for r in rule_results if r.get("verdict") == "Rule Match ✓"]
    partial_rules = [r for r in rule_results
                     if r.get("verdict") in ("부분 일치 (금지 피크 존재)", "약한 일치 (참고용)")]

    if lib_tier.startswith("동정"):
        library_support = "HIGH"
    elif "Ambiguous" in lib_tier:
        library_support = "MEDIUM"
    elif "미동정" in lib_tier:
        library_support = "NONE"
    else:
        library_support = "LOW"

    if strong_rules:
        # 피크 어사인 우선: 필수 피크 100% 매칭(Rule Match ✓)이면 HIGH.
        # 보조 피크 누락으로 score_pct가 낮아져도 강등하지 않는다.
        best_req_frac = float(strong_rules[0].get("required_fraction", 0.0))
        rule_support = "HIGH" if best_req_frac >= 1.0 else "MEDIUM"
    elif partial_rules:
        rule_support = "LOW"
    elif rule_results:
        rule_support = "NONE"
    else:
        rule_support = "UNAVAILABLE"

    if "identification_confidence" not in out:
        # 피크 어사인(룰)을 라이브러리보다 우선:
        #  - rule_support=HIGH  → HIGH (라이브러리 등급 무관)
        #  - rule_support=MEDIUM 또는 library_support=HIGH → MEDIUM
        #  - 그 외 → LOW
        if rule_support == "HIGH":
            identification_confidence = "HIGH"
        elif rule_support == "MEDIUM" or library_support == "HIGH":
            identification_confidence = "MEDIUM"
        else:
            identification_confidence = "LOW"
        out["identification_confidence"] = identification_confidence

    out.setdefault("library_support", library_support)
    out.setdefault("rule_support", rule_support)
    out["rule_evidence_summary"] = summarize_rule_evidence(strong_rules[0] if strong_rules else (rule_results[0] if rule_results else None))
    out["product_profile"] = build_product_profile(rule_results, findings)
    if findings and findings.get("additives_and_process_markers") and strong_rules:
        additive_names = ", ".join(m["name"] for m in findings["additives_and_process_markers"][:3])
        out["explanation"] = f"{out['explanation']} 첨가제/공정 흔적: {additive_names}."
    out["is_identified"] = (
        out["identification_confidence"] in ("HIGH", "MEDIUM") and
        not str(out.get("verdict", "")).startswith(("미동정", "상충", "복수 후보"))
    )
    return out


# ──────────────────────────────────────────────────────────────
# 작용기 관련 함수
# ──────────────────────────────────────────────────────────────

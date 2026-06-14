# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: YAML 룰 기반 FTIR 식별 엔진. 규칙 매칭으로 첨가제·물질을 판정한다.
# 실행 방법: 모듈 — 직접 실행하지 않고 import해서 사용 (ftir_analyze.py / ftir.cli 를 통해 동작)
# ─────────────────────────────────────────────────────────────────────────────
"""Rule-based FTIR identification engine."""

import glob
import os
import sys

import numpy as np

from .schema import load_yaml_safe, validate_rule


class RuleEngine:
    """
    rules/*.yaml 파일에 정의된 룰을 읽어 시료 스펙트럼과 비교.
    피크 범위 매칭 방식으로 작동하므로 라이브러리 스펙트럼이 없어도 동작.

    rule_names / rule_categories 로 적용할 룰을 라이브러리처럼 선택할 수 있다.
      - rule_names: 파일 stem(예: 'phenolic_foam'), 화합물명(예: 'Phenolic Foam'),
        또는 별칭 중 어느 하나와 부분 일치하면 포함
      - rule_categories: YAML의 category 필드와 부분 일치하면 포함
    """

    def __init__(self, rules_dir: str = "rules",
                 rule_names: list[str] | None = None,
                 rule_categories: list[str] | None = None,
                 quiet: bool = False):
        self.rules_dir = rules_dir
        self.rule_names = [s.strip().lower() for s in rule_names] if rule_names else None
        self.rule_categories = [s.strip().lower() for s in rule_categories] if rule_categories else None
        self.quiet = quiet
        self.rules: list[dict] = []
        self.skipped: list[dict] = []  # 필터로 제외된 룰
        self._load_all()

    # ── 룰 로드 ──────────────────────────────────────────────────────
    def _matches_filter(self, rule: dict, stem: str) -> bool:
        if self.rule_names is None and self.rule_categories is None:
            return True
        if self.rule_names is not None:
            haystack = [stem.lower(), str(rule.get("compound", "")).lower()]
            haystack += [str(a).lower() for a in rule.get("aliases", [])]
            if any(any(name in h or h in name for h in haystack) for name in self.rule_names):
                return True
        if self.rule_categories is not None:
            cat = str(rule.get("category", "")).lower()
            if any(c in cat for c in self.rule_categories):
                return True
        return False

    def _load_all(self):
        pattern = os.path.join(self.rules_dir, "*.yaml")
        for path in sorted(glob.glob(pattern)):
            try:
                rule = load_yaml_safe(path)
                validate_rule(rule, path)
                stem = os.path.splitext(os.path.basename(path))[0]
                rule["_source_file"] = os.path.basename(path)
                rule["_file_stem"] = stem
                if not self._matches_filter(rule, stem):
                    self.skipped.append({"file": rule["_source_file"],
                                          "compound": rule.get("compound"),
                                          "category": rule.get("category", "")})
                    continue
                self.rules.append(rule)
            except Exception as e:
                print(f"  [룰 로드 경고] {path}: {e}", file=sys.stderr)
        if not self.quiet:
            filt = ""
            if self.rule_names or self.rule_categories:
                filt = f" / 필터 적용 (제외 {len(self.skipped)}개)"
            print(f"       룰 로드: {len(self.rules)}개 ({self.rules_dir}/){filt}")

    # ── 사용 가능한 룰 목록 ─────────────────────────────────────────
    @staticmethod
    def list_available_rules(rules_dir: str) -> list[dict]:
        """디렉토리의 모든 YAML 룰을 메타데이터만 추려 반환 (필터 미적용)."""
        out = []
        pattern = os.path.join(rules_dir, "*.yaml")
        for path in sorted(glob.glob(pattern)):
            try:
                rule = load_yaml_safe(path)
                stem = os.path.splitext(os.path.basename(path))[0]
                out.append({
                    "file": os.path.basename(path),
                    "stem": stem,
                    "compound": rule.get("compound", "?"),
                    "category": rule.get("category", ""),
                    "aliases": rule.get("aliases", []),
                    "description": (rule.get("description") or "").strip().splitlines()[0]
                        if rule.get("description") else "",
                })
            except Exception as e:
                out.append({"file": os.path.basename(path), "stem": "",
                            "compound": f"[로드 실패: {e}]", "category": "",
                            "aliases": [], "description": ""})
        return out

    # ── 핵심 평가 함수 ──────────────────────────────────────────────
    @staticmethod
    def _peak_in_range(peak_wns, lo, hi, tol=0.0):
        """sample_peaks 중 [lo-tol, hi+tol] 범위 안에 있는 피크가 있으면 True."""
        return any((lo - tol) <= p <= (hi + tol) for p in peak_wns)

    @staticmethod
    def _strongest_in_range(peak_wns, peak_vals, lo, hi):
        """범위 내 가장 강한 피크 강도 반환 (없으면 0.0)."""
        hits = [v for w, v in zip(peak_wns, peak_vals) if lo <= w <= hi]
        return max(hits) if hits else 0.0

    def evaluate(
        self,
        peak_wns: np.ndarray,
        peak_vals: np.ndarray,
    ) -> list[dict]:
        """
        모든 룰에 대해 평가. 반환 리스트는 score 내림차순 정렬.
        각 항목:
          compound, score (0~1), required_fraction, matched_required,
          missed_required, matched_supporting, triggered_forbidden,
          detail (각 피크별 True/False)
        """
        results = []
        for rule in self.rules:
            res = self._eval_one(rule, peak_wns, peak_vals)
            results.append(res)
        results.sort(key=lambda x: -x["score"])
        return results

    def _eval_one(self, rule: dict, peak_wns, peak_vals) -> dict:
        """
        피크 룰 평가. v2 형식(flat list)과 v1 형식(required/supporting/forbidden 섹션)
        양쪽을 자동 판별하여 처리.
        """
        scoring_cfg = rule.get("scoring", {})
        default_tol  = float(scoring_cfg.get("default_tolerance",
                             scoring_cfg.get("peak_detection", {}).get("tolerance_cm", 15)))
        min_h        = float(scoring_cfg.get("min_height",
                             scoring_cfg.get("peak_detection", {}).get("min_height", 0.05)))
        min_req_frac = float(scoring_cfg.get("min_required_fraction", 0.60))
        match_thr    = float(scoring_cfg.get("match_threshold", 0.60))
        warn_penalty = float(scoring_cfg.get("warning_penalty_per_peak", 0.08))
        fb_penalty   = float(scoring_cfg.get("forbidden_penalty_per_peak", 0.15))

        # intensity 문자열 → 가중치 매핑 (YAML의 intensity_weights 또는 기본값)
        iw_cfg = scoring_cfg.get("intensity_weights", {})
        INTENSITY_WEIGHT = {
            "strong": float(iw_cfg.get("strong", 3.0)),
            "medium": float(iw_cfg.get("medium", 1.5)),
            "weak":   float(iw_cfg.get("weak",   0.8)),
        }

        # ── peaks 형식 판별 및 정규화 ────────────────────────────────
        peaks_raw = rule.get("peaks", {})

        if isinstance(peaks_raw, list):
            # v2: 플랫 리스트 (wavenumber_min/max + required + forbidden 필드)
            required_peaks  = []
            supporting_peaks = []
            warning_peaks = []
            context_peaks = []
            forbidden_peaks  = []
            for p in peaks_raw:
                lo = float(p.get("wavenumber_min", p.get("range", [0, 0])[0]))
                hi = float(p.get("wavenumber_max", p.get("range", [0, 0])[-1]))
                # wavenumber_min == wavenumber_max 인 경우 tolerance 적용
                tol_p = float(p.get("tolerance", default_tol))
                label = p.get("vibration_mode", p.get("label", ""))
                intens_str = str(p.get("intensity", "medium")).lower()
                w = float(p.get("weight", INTENSITY_WEIGHT.get(intens_str, 1.5)))
                center = (lo + hi) / 2.0
                role = str(p.get("role", "")).lower()
                entry = {"id": p.get("id", ""), "role": role or None,
                         "label": label, "center": center,
                         "range": [lo - tol_p, hi + tol_p],  # tolerance 이미 반영
                         "raw_range": [lo, hi], "weight": w,
                         "assignment": p.get("assignment", ""),
                         "diagnosticity": p.get("diagnosticity", ""),
                         "marker_type": p.get("marker_type", ""),
                         "interpretation": p.get("interpretation", ""),
                         "warning_type": p.get("warning_type", ""),
                         "allowed_context": p.get("allowed_context", []),
                         "min_intensity": float(p.get("min_intensity", min_h)),
                         "penalty": float(p.get("penalty", warn_penalty if role == "warning" else fb_penalty))}
                if role in ("context_marker", "additive", "process_marker", "modifier"):
                    context_peaks.append(entry)
                elif role == "warning":
                    warning_peaks.append(entry)
                elif role in ("hard_forbidden", "forbidden") or p.get("forbidden", False):
                    forbidden_peaks.append(entry)
                elif role == "required" or p.get("required", False):
                    required_peaks.append(entry)
                else:
                    supporting_peaks.append(entry)
        else:
            # v1: 섹션 분리형 (range + label + weight)
            def _v1_norm(plist):
                out = []
                for p in plist:
                    lo, hi = p["range"]
                    tol_p = float(p.get("tolerance", default_tol))
                    out.append({
                        "label": p.get("label", p.get("vibration_mode", "")),
                        "center": p.get("center", (lo + hi) / 2.0),
                        "range": [lo - tol_p, hi + tol_p],
                        "raw_range": [lo, hi],
                        "weight": float(p.get("weight", INTENSITY_WEIGHT.get(
                            str(p.get("intensity","medium")).lower(), 1.5))),
                        "assignment": p.get("assignment", ""),
                    })
                return out
            required_peaks   = _v1_norm(peaks_raw.get("required", []))
            supporting_peaks = _v1_norm(peaks_raw.get("supporting", []))
            warning_peaks    = _v1_norm(peaks_raw.get("warning", []))
            context_peaks    = _v1_norm(peaks_raw.get("context", []))
            forbidden_peaks  = _v1_norm(peaks_raw.get("forbidden", []))

        # ── required 평가 ────────────────────────────────────────────
        req_hits, req_misses = [], []
        req_weighted_num = 0.0
        req_weighted_den = sum(p["weight"] for p in required_peaks)

        for p in required_peaks:
            lo, hi = p["range"]
            intensity = self._strongest_in_range(peak_wns, peak_vals, lo, hi)
            found = intensity >= min_h
            display = {"label": p["label"], "center": p["center"],
                       "range": p["raw_range"]}
            if found:
                req_weighted_num += p["weight"]
                req_hits.append({**display, "intensity": round(float(intensity), 3)})
            else:
                req_misses.append(display)

        req_fraction = len(req_hits) / len(required_peaks) if required_peaks else 1.0
        req_weighted_fraction = (req_weighted_num / req_weighted_den) if req_weighted_den > 1e-9 else 1.0

        # ── supporting 평가 ──────────────────────────────────────────
        sup_hits = []
        sup_weighted_num = 0.0
        sup_weighted_den = sum(p["weight"] for p in supporting_peaks)

        for p in supporting_peaks:
            lo, hi = p["range"]
            intensity = self._strongest_in_range(peak_wns, peak_vals, lo, hi)
            if intensity >= min_h:
                sup_weighted_num += p["weight"]
                sup_hits.append({"label": p["label"], "center": p["center"],
                                 "range": p["raw_range"],
                                 "intensity": round(float(intensity), 3)})

        # ── warning / forbidden 평가 ─────────────────────────────────
        context_hits = []
        for p in context_peaks:
            lo, hi = p["range"]
            intensity = self._strongest_in_range(peak_wns, peak_vals, lo, hi)
            if intensity >= min_h:
                context_hits.append({"id": p.get("id", ""),
                                     "label": p["label"], "center": p["center"],
                                     "range": p["raw_range"],
                                     "intensity": round(float(intensity), 3),
                                     "marker_type": p.get("marker_type", ""),
                                     "interpretation": p.get("interpretation", ""),
                                     "assignment": p["assignment"]})

        warnings_triggered = []
        for p in warning_peaks:
            lo, hi = p["range"]
            intensity = self._strongest_in_range(peak_wns, peak_vals, lo, hi)
            threshold = p.get("min_intensity", min_h)
            if intensity >= threshold:
                warnings_triggered.append({"id": p.get("id", ""),
                                           "label": p["label"], "center": p["center"],
                                           "range": p["raw_range"],
                                           "intensity": round(float(intensity), 3),
                                           "min_intensity": threshold,
                                           "penalty": p.get("penalty", warn_penalty),
                                           "warning_type": p.get("warning_type", "soft"),
                                           "allowed_context": p.get("allowed_context", []),
                                           "assignment": p["assignment"]})

        fb_triggered = []
        for p in forbidden_peaks:
            lo, hi = p["range"]
            intensity = self._strongest_in_range(peak_wns, peak_vals, lo, hi)
            # min_intensity 필드: 이 강도 이상일 때만 패널티 (미지정 시 global min_h)
            threshold = p.get("min_intensity", min_h)
            if intensity >= threshold:
                fb_triggered.append({"id": p.get("id", ""),
                                     "label": p["label"], "center": p["center"],
                                     "range": p["raw_range"],
                                     "intensity": round(float(intensity), 3),
                                     "min_intensity": threshold,
                                     "assignment": p["assignment"]})

        # ── 종합 점수 계산 ───────────────────────────────────────────
        total_den = req_weighted_den * 2.0 + sup_weighted_den
        total_num = req_weighted_num * 2.0 + sup_weighted_num
        raw_score = (total_num / total_den) if total_den > 1e-9 else 0.0

        if req_fraction < min_req_frac:
            raw_score *= (req_fraction / min_req_frac) ** 2

        warning_penalty_total = sum(float(p.get("penalty", warn_penalty)) for p in warnings_triggered)
        forbidden_penalty_total = sum(float(p.get("penalty", fb_penalty)) for p in fb_triggered)
        penalty = min(0.6, warning_penalty_total + forbidden_penalty_total)
        final_score = max(0.0, raw_score - penalty)

        # ── 판정 ──────────────────────────────────────────────────────
        if req_fraction < min_req_frac:
            verdict = "불일치 (required 피크 부족)"
        elif final_score >= match_thr and not fb_triggered:
            verdict = "Rule Match ✓"
        elif final_score >= match_thr and fb_triggered:
            verdict = "부분 일치 (금지 피크 존재)"
        elif final_score >= match_thr * 0.7:
            verdict = "약한 일치 (참고용)"
        else:
            verdict = "불일치"

        # ── 서브타입 평가 (base가 Rule Match일 때만 의미 있음) ────────
        subtype_matches, subtype_all, subtype_summary, subtype_label = \
            self._eval_subtypes(rule, peak_wns, peak_vals, scoring_cfg,
                                base_matched=(verdict == "Rule Match ✓"))

        # 서브타입 매칭 결과를 최종 표시 명칭에 반영
        compound_base = rule.get("compound", "?")
        display_compound = compound_base
        if verdict == "Rule Match ✓" and subtype_label:
            display_compound = f"{compound_base} — {subtype_label}"

        return {
            "compound":              compound_base,
            "compound_display":      display_compound,
            "family":                rule.get("family", compound_base),
            "aliases":               rule.get("aliases", []),
            "category":              rule.get("category", ""),
            "source_file":           rule.get("_source_file", ""),
            "file_stem":             rule.get("_file_stem", ""),
            "description":           rule.get("description", ""),
            "score":                 round(float(final_score), 4),
            "score_pct":             round(float(final_score) * 100, 1),
            "required_fraction":     round(float(req_fraction), 3),
            "required_weighted_fraction": round(float(req_weighted_fraction), 3),
            "diagnostic_score":      round(float(req_weighted_fraction), 3),
            "support_score":         round(float((sup_weighted_num / sup_weighted_den) if sup_weighted_den > 1e-9 else 0.0), 3),
            "warning_penalty":       round(float(warning_penalty_total), 3),
            "forbidden_penalty":     round(float(forbidden_penalty_total), 3),
            "match_threshold":       match_thr,
            "verdict":               verdict,
            "matched_required":      req_hits,
            "missed_required":       req_misses,
            "matched_supporting":    sup_hits,
            "matched_context_markers": context_hits,
            "triggered_warnings":    warnings_triggered,
            "triggered_forbidden":   fb_triggered,
            "subtype_matches":       subtype_matches,
            "subtype_all":           subtype_all,
            "subtype_summary":       subtype_summary,
            "subtype_label":         subtype_label,
        }

    # ──────────────────────────────────────────────────────────────
    # 서브타입 평가 (Phenolic Foam의 Urea-modified / Melamine-modified 등)
    # ──────────────────────────────────────────────────────────────
    def _eval_subtypes(self, rule: dict, peak_wns, peak_vals,
                       parent_scoring: dict, base_matched: bool):
        """
        rule['subtypes'] 가 있으면 각 서브타입의 peaks를 평가한다.
        반환: (matched_list, all_list, summary_text, label_text)
          - matched_list: 매칭된 서브타입만 (score 내림차순)
          - all_list: 모든 서브타입의 평가 결과
          - summary_text: 사람 읽기용 요약 (verdict 출력용)
          - label_text: compound 뒤에 붙일 짧은 라벨
        base가 매칭 안 됐으면 평가는 하되 label은 비워둔다.
        """
        subtypes_raw = rule.get("subtypes", [])
        if not subtypes_raw:
            return [], [], "", ""

        default_tol = float(parent_scoring.get("default_tolerance", 15))
        min_h = float(parent_scoring.get("min_height", 0.05))

        all_results = []
        for st in subtypes_raw:
            st_peaks = st.get("peaks", [])
            req_entries, sup_entries = [], []
            for p in st_peaks:
                lo = float(p.get("wavenumber_min", p.get("range", [0, 0])[0]))
                hi = float(p.get("wavenumber_max", p.get("range", [0, 0])[-1]))
                tol_p = float(p.get("tolerance", default_tol))
                entry = {
                    "id": p.get("id", ""),
                    "label": p.get("vibration_mode", p.get("label", "")),
                    "center": (lo + hi) / 2.0,
                    "range": [lo - tol_p, hi + tol_p],
                    "raw_range": [lo, hi],
                    "assignment": p.get("assignment", ""),
                }
                role = str(p.get("role", "required")).lower()
                if role == "supporting":
                    sup_entries.append(entry)
                else:
                    req_entries.append(entry)

            req_hits, req_misses = [], []
            for p in req_entries:
                lo, hi = p["range"]
                intens = self._strongest_in_range(peak_wns, peak_vals, lo, hi)
                if intens >= min_h:
                    req_hits.append({"label": p["label"], "center": p["center"],
                                     "range": p["raw_range"],
                                     "intensity": round(float(intens), 3)})
                else:
                    req_misses.append({"label": p["label"], "center": p["center"],
                                       "range": p["raw_range"]})
            sup_hits = []
            for p in sup_entries:
                lo, hi = p["range"]
                intens = self._strongest_in_range(peak_wns, peak_vals, lo, hi)
                if intens >= min_h:
                    sup_hits.append({"label": p["label"], "center": p["center"],
                                     "range": p["raw_range"],
                                     "intensity": round(float(intens), 3)})

            req_frac = (len(req_hits) / len(req_entries)) if req_entries else 0.0
            sup_frac = (len(sup_hits) / len(sup_entries)) if sup_entries else 0.0
            min_st_frac = float(st.get("min_required_fraction", 1.0))

            # 점수: required 0.8 + supporting 0.2 가중
            if req_entries:
                score = 0.8 * req_frac + 0.2 * sup_frac if sup_entries else req_frac
            else:
                score = sup_frac
            matched = bool(req_entries) and req_frac >= min_st_frac

            all_results.append({
                "name":     st.get("name", "?"),
                "aliases":  st.get("aliases", []),
                "description": st.get("description", ""),
                "matched":  matched,
                "score":    round(float(score), 3),
                "score_pct": round(float(score) * 100, 1),
                "required_fraction": round(float(req_frac), 3),
                "matched_required":   req_hits,
                "missed_required":    req_misses,
                "matched_supporting": sup_hits,
            })

        all_results.sort(key=lambda x: -x["score"])
        matched_list = [r for r in all_results if r["matched"]]

        # 라벨/요약 생성
        family = rule.get("family", rule.get("compound", "")) + " 계열"
        if not base_matched:
            return matched_list, all_results, "", ""
        if len(matched_list) == 0:
            label = "변종 미확정"
            summary = f"{family} 매칭, 정의된 서브타입 중 일치 없음 → 변종 미확정"
        elif len(matched_list) == 1:
            label = matched_list[0]["name"]
            summary = f"서브타입: {label} ({matched_list[0]['score_pct']:.0f}%)"
        else:
            names = " + ".join(m["name"] for m in matched_list)
            label = f"복합 변종 ({names})"
            summary = f"복수 서브타입 동시 매칭: {names}"
        return matched_list, all_results, summary, label

    # ── 출력 포맷터 ──────────────────────────────────────────────────
    @staticmethod
    def format_results(results: list[dict], min_score_pct: float = 30.0) -> str:
        lines = []
        lines.append("═" * 65)
        lines.append("  룰 기반 동정 결과 (Rule-Based Identification)")
        lines.append("═" * 65)
        shown = [r for r in results if r["score_pct"] >= min_score_pct]
        if not shown:
            lines.append("  ✗ 임계값 이상 매칭된 룰 없음")
        for r in shown:
            display = r.get("compound_display") or r["compound"]
            lines.append(f"\n  ▶ {display}  [{r['source_file']}]")
            bar = "█" * int(r["score_pct"] / 5)
            lines.append(f"    점수: {r['score_pct']:5.1f}%  {bar}")
            lines.append(f"    판정: {r['verdict']}")
            lines.append(f"    필수 피크 검출: {len(r['matched_required'])}/{len(r['matched_required'])+len(r['missed_required'])} "
                         f"({r['required_fraction']*100:.0f}%)")
            if r.get("subtype_summary"):
                lines.append(f"    ◆ {r['subtype_summary']}")
                for st in r.get("subtype_matches", []):
                    pos = ", ".join(f"{p['center']:.0f}" for p in st["matched_required"]) or "-"
                    lines.append(f"       · {st['name']}  ({st['score_pct']:.0f}%)  검출: {pos} cm⁻¹")

            if r["matched_required"]:
                lines.append("    ✓ 검출된 필수 피크:")
                for p in r["matched_required"]:
                    lines.append(f"       - {p['center']:.0f} cm⁻¹  {p['label']}  (강도 {p['intensity']:.3f})")

            if r["missed_required"]:
                lines.append("    ✗ 미검출 필수 피크:")
                for p in r["missed_required"]:
                    lines.append(f"       - {p['center']:.0f} cm⁻¹  {p['label']}")

            if r["matched_supporting"]:
                labels = ", ".join(f"{p['center']:.0f}" for p in r["matched_supporting"])
                lines.append(f"    + 지지 피크 검출: {labels} cm⁻¹")

            if r.get("matched_context_markers"):
                labels = ", ".join(f"{p['center']:.0f}" for p in r["matched_context_markers"])
                lines.append(f"    + 첨가제/공정 마커: {labels} cm⁻¹")

            if r.get("triggered_warnings"):
                lines.append("    ! 주의 피크 감지 (맥락 반영):")
                for p in r["triggered_warnings"]:
                    lines.append(f"       - {p['center']:.0f} cm⁻¹  {p['label']}  → {p['assignment']}")

            if r["triggered_forbidden"]:
                lines.append("    ⚠ 금지 피크 감지 (신뢰도 저하):")
                for p in r["triggered_forbidden"]:
                    lines.append(f"       - {p['center']:.0f} cm⁻¹  {p['label']}  → {p['assignment']}")

        lines.append("\n" + "═" * 65)
        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# 시각화 함수
# ──────────────────────────────────────────────────────────────

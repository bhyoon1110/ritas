# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: 룰 엔진(RuleEngine)과 종합 판정(verdict)·첨가제 감지 로직의 단위 테스트.
# 실행 방법: python -m pytest tests/test_rule_engine.py
#            또는 python -m unittest tests.test_rule_engine
#            또는 python tests/test_rule_engine.py   (sune 디렉토리에서 실행)
# ─────────────────────────────────────────────────────────────────────────────
import unittest
from pathlib import Path
import sys

import numpy as np

SUNE_DIR = Path(__file__).resolve().parents[1]
if str(SUNE_DIR) not in sys.path:
    sys.path.insert(0, str(SUNE_DIR))

from ftir.findings import detect_additives_and_process_markers
from ftir.rule_engine import RuleEngine
from ftir.verdict import combined_verdict, enrich_combined_verdict


RULES_DIR = SUNE_DIR / "rules"


class RuleEngineTests(unittest.TestCase):
    def test_phenolic_foam_rule_matches_commercial_pf_markers(self):
        engine = RuleEngine(str(RULES_DIR))
        peak_wn = np.array([3395.7, 2861.4, 1714.7, 1600.7, 1474.6, 1220.5, 1140.4, 1006.3, 814.2, 752.2])
        peak_val = np.array([0.332, 0.355, 0.417, 0.477, 1.0, 0.854, 0.666, 0.609, 0.376, 0.325])

        result = engine.evaluate(peak_wn, peak_val)[0]

        self.assertEqual(result["compound"], "Phenolic Foam")
        self.assertEqual(result["verdict"], "Rule Match ✓")
        self.assertGreaterEqual(result["score_pct"], 85.0)
        self.assertEqual(result["required_fraction"], 1.0)
        self.assertFalse(result["triggered_forbidden"])

    def test_context_markers_do_not_change_pf_core_score_but_are_reported(self):
        engine = RuleEngine(str(RULES_DIR))
        peak_wn = np.array([3395.7, 2915.4, 1600.7, 1474.6, 1276.5, 1220.5, 1166.4, 1130.4, 814.2, 754.2])
        peak_val = np.array([0.324, 0.354, 0.466, 1.0, 0.343, 0.805, 0.589, 0.625, 0.403, 0.326])

        result = engine.evaluate(peak_wn, peak_val)[0]

        interpretations = {m["interpretation"] for m in result["matched_context_markers"]}
        self.assertIn("phosphorus_flame_retardant", interpretations)
        self.assertIn("fluorinated_surfactant_or_release_agent", interpretations)
        self.assertEqual(result["required_fraction"], 1.0)

    def test_additives_are_reported_separately_from_base_material(self):
        peak_wn = np.array([1276.5, 1166.4, 1130.4, 1716.7, 1650.7, 3395.7])
        peak_val = np.array([0.343, 0.589, 0.625, 0.405, 0.441, 0.324])

        markers = detect_additives_and_process_markers(peak_wn, peak_val)
        names = {m["name"] for m in markers}

        self.assertIn("인계 난연제 (phosphorus-based flame retardant)", names)
        self.assertIn("불소계 정포제/이형제 흔적", names)
        self.assertIn("설폰산계 산 촉매 잔류", names)

    def test_rule_support_is_split_from_library_support(self):
        rule_result = {"compound": "Phenolic Foam", "score_pct": 93.1,
                       "verdict": "Rule Match ✓", "required_fraction": 1.0}
        verdict = combined_verdict("미동정 (No reliable match)", "m-Xylene", 61.7, [rule_result])
        findings = {"additives_and_process_markers": [
            {"name": "인계 난연제 (phosphorus-based flame retardant)"}
        ]}
        enriched = enrich_combined_verdict(verdict, "미동정 (No reliable match)", [rule_result], findings)

        self.assertEqual(enriched["library_support"], "NONE")
        self.assertEqual(enriched["rule_support"], "HIGH")
        self.assertEqual(enriched["identification_confidence"], "HIGH")
        self.assertTrue(enriched["is_identified"])
        self.assertEqual(enriched["product_profile"]["base_material"], "Phenolic Foam")

    # ── 룰 선택 (rule selection) ─────────────────────────────────────
    def test_rule_selection_keeps_phenolic_by_stem(self):
        engine = RuleEngine(str(RULES_DIR), rule_names=["phenolic_foam"])
        self.assertEqual(len(engine.rules), 1)
        self.assertEqual(engine.rules[0]["compound"], "Phenolic Foam")
        self.assertEqual(len(engine.skipped), 0)

    def test_rule_selection_keeps_phenolic_by_alias(self):
        engine = RuleEngine(str(RULES_DIR), rule_names=["페놀폼"])
        self.assertEqual(len(engine.rules), 1)
        self.assertEqual(engine.rules[0]["compound"], "Phenolic Foam")

    def test_rule_selection_filters_nonexistent(self):
        engine = RuleEngine(str(RULES_DIR),
                            rule_names=["nonexistent_rule"], quiet=True)
        self.assertEqual(len(engine.rules), 0)
        self.assertEqual(len(engine.skipped), 1)

    def test_rule_selection_by_category(self):
        engine = RuleEngine(str(RULES_DIR),
                            rule_categories=["engineering"])
        self.assertEqual(len(engine.rules), 1)
        self.assertEqual(engine.rules[0]["compound"], "Phenolic Foam")

    def test_list_available_rules(self):
        rules = RuleEngine.list_available_rules(str(RULES_DIR))
        self.assertGreaterEqual(len(rules), 1)
        compounds = {r["compound"] for r in rules}
        self.assertIn("Phenolic Foam", compounds)
        pf = next(r for r in rules if r["compound"] == "Phenolic Foam")
        self.assertEqual(pf["stem"], "phenolic_foam")
        self.assertIn("페놀폼", pf["aliases"])

    # ── 혼합물(복수 룰 매칭) 처리 ────────────────────────────────────
    def test_combined_verdict_handles_multi_strong_rules(self):
        rule_a = {"compound": "Phenolic Foam", "score_pct": 92.0, "verdict": "Rule Match ✓"}
        rule_b = {"compound": "Polyurethane Foam", "score_pct": 88.0, "verdict": "Rule Match ✓"}
        verdict = combined_verdict("미동정 (No reliable match)", "m-Xylene", 55.0, [rule_a, rule_b])

        self.assertIn("혼합물", verdict["verdict"])
        self.assertEqual(verdict["confidence"], "MEDIUM")
        self.assertEqual(len(verdict["mixture_candidates"]), 2)
        names = {c["compound"] for c in verdict["mixture_candidates"]}
        self.assertEqual(names, {"Phenolic Foam", "Polyurethane Foam"})

    # ── 서브타입(변종) 판정 ─────────────────────────────────────────
    def _pf_base_peaks(self):
        # PF 공통 골격: OH, CH2, 방향족, CH2 가위, phenolic C-O, para-OOP
        return (
            [3395.0, 2885.0, 1605.0, 1474.0, 1218.0, 818.0],
            [0.50,   0.35,   0.55,   0.80,   0.85,   0.40],
        )

    def test_subtype_unconfirmed_when_only_one_marker(self):
        # amide I(1648)만 있고 amide II(1545)는 없는 경우 → 우레아 미확정
        engine = RuleEngine(str(RULES_DIR))
        wn, val = self._pf_base_peaks()
        wn += [1648.0]; val += [0.45]
        result = engine.evaluate(np.array(wn), np.array(val))[0]

        self.assertEqual(result["verdict"], "Rule Match ✓")
        self.assertEqual(result["subtype_label"], "변종 미확정")
        self.assertEqual(len(result["subtype_matches"]), 0)
        # Urea 서브타입 점수: required 1/2 매칭, supporting 없음 → 50%
        urea = next(s for s in result["subtype_all"] if s["name"].startswith("Urea"))
        self.assertFalse(urea["matched"])
        self.assertAlmostEqual(urea["score_pct"], 50.0, places=1)

    def test_subtype_match_when_paired_markers_present(self):
        # amide I(1648) + amide II(1545) 둘 다 → 우레아 매칭
        engine = RuleEngine(str(RULES_DIR))
        wn, val = self._pf_base_peaks()
        wn += [1648.0, 1545.0]; val += [0.45, 0.40]
        result = engine.evaluate(np.array(wn), np.array(val))[0]

        self.assertEqual(result["verdict"], "Rule Match ✓")
        self.assertEqual(len(result["subtype_matches"]), 1)
        self.assertTrue(result["subtype_matches"][0]["name"].startswith("Urea"))
        self.assertIn("Urea", result["subtype_label"])
        self.assertIn("Urea", result["compound_display"])
        self.assertIn("—", result["compound_display"])

    def test_subtype_composite_label_when_two_subtypes_match(self):
        # 우레아 짝 + MCA 짝 둘 다 → 복합 변종
        engine = RuleEngine(str(RULES_DIR))
        wn, val = self._pf_base_peaks()
        wn += [1648.0, 1545.0, 1780.0, 770.0]
        val += [0.45,  0.40,   0.50,   0.35]
        result = engine.evaluate(np.array(wn), np.array(val))[0]

        self.assertGreaterEqual(len(result["subtype_matches"]), 2)
        self.assertIn("복합 변종", result["subtype_label"])
        self.assertIn("복합 변종", result["compound_display"])

    # ── 피크 어사인 우선 정책 (룰 > 라이브러리) ───────────────────
    def test_rule_overrides_library_when_names_differ(self):
        # 라이브러리는 다른 물질을 동정했지만 룰이 강매칭 → 룰 우선
        rule_result = {"compound": "Phenolic Foam", "score_pct": 88.0,
                       "verdict": "Rule Match ✓", "required_fraction": 1.0,
                       "aliases": []}
        verdict = combined_verdict("동정 (확정 매칭)", "Polyurethane Foam", 91.0, [rule_result])
        self.assertIn("룰 기반 동정", verdict["verdict"])
        self.assertIn("Phenolic Foam", verdict["verdict"])
        self.assertEqual(verdict["confidence"], "HIGH")

    def test_rule_high_when_required_full_even_with_low_score(self):
        # supporting 누락으로 score_pct가 낮아도 required_fraction=1.0이면 rule_support=HIGH
        rule_result = {"compound": "Phenolic Foam", "score_pct": 62.0,
                       "verdict": "Rule Match ✓", "required_fraction": 1.0}
        verdict = combined_verdict("미동정 (No reliable match)", "?", 30.0, [rule_result])
        enriched = enrich_combined_verdict(verdict, "미동정 (No reliable match)", [rule_result], None)
        self.assertEqual(enriched["rule_support"], "HIGH")
        self.assertEqual(enriched["identification_confidence"], "HIGH")
        self.assertTrue(enriched["is_identified"])

    def test_rule_medium_when_required_partial(self):
        # required_fraction < 1.0 이면 verdict가 Rule Match라 해도 HIGH로 승격 안됨
        rule_result = {"compound": "Phenolic Foam", "score_pct": 85.0,
                       "verdict": "Rule Match ✓", "required_fraction": 0.83}
        verdict = combined_verdict("미동정 (No reliable match)", "?", 30.0, [rule_result])
        enriched = enrich_combined_verdict(verdict, "미동정 (No reliable match)", [rule_result], None)
        self.assertEqual(enriched["rule_support"], "MEDIUM")


if __name__ == "__main__":
    unittest.main()

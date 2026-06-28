# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: 작용기·진단 구간(diagnostic region) 기반의 정성적 소견(findings) 도출 유틸.
# 실행 방법: 모듈 — 직접 실행하지 않고 import해서 사용 (ftir_analyze.py / ftir.cli 를 통해 동작)
# ─────────────────────────────────────────────────────────────────────────────
"""Qualitative functional-group and diagnostic-region findings."""

from pathlib import Path

import numpy as np
import pandas as pd


# ──────────────────────────────────────────────────────────────
# detect_patterns / detect_additives 임계값 (모두 0~1 정규화 강도 기준)
# 필요 시 호출자가 모듈 속성을 덮어쓰는 식으로 튜닝 가능.
# ──────────────────────────────────────────────────────────────
THRESHOLDS = {
    # PF 코어 판별
    "pf_core_ch2_min":      0.20,   # 1475 cm⁻¹
    "pf_core_co_min":       0.20,   # 1220 cm⁻¹
    "pf_core_arom_min":     0.10,   # 1600 cm⁻¹
    "pf_core_para_min":     0.08,   # 820 cm⁻¹
    "pf_marker_min":        0.08,   # 보조 마커 카운트 기준
    # 첨가제
    "additive_p_min":       0.12,   # 1276 P=O
    "additive_p_high":      0.25,
    "additive_cf_min":      0.15,   # 1166 C-F
    "additive_sulfonic_min":0.15,   # 1140 S=O
    "additive_sulfonic_hi": 0.35,
    "additive_carbonyl_min":0.20,   # 1715 C=O
    "additive_amide_min":   0.15,   # 1650
    "additive_ohnh_min":    0.15,   # 3400
    # 기타 작용기
    "ester_co_min":         0.10,
    "ester_coc_min":        0.05,
    "amide_min":            0.08,
    "alcohol_oh_min":       0.10,
    "alcohol_co_min":       0.05,
    "aromatic_band_min":    0.05,
    "cf_band_min":          0.10,
    "silicone_siosi_min":   0.15,
    "silicone_ch3si_min":   0.05,
    "nitrile_min":          0.08,
    "aldehyde_co_min":      0.08,
    "aldehyde_ch_min":      0.03,
}


DEFAULT_FUNC_GROUPS_PATH = Path(__file__).resolve().parent / "resources" / "func_groups.csv"


def load_func_groups(func_groups_file="data/func_groups.csv"):
    path = Path(func_groups_file)
    if not path.is_file() and path.as_posix() == "data/func_groups.csv":
        path = DEFAULT_FUNC_GROUPS_PATH
    _fg = pd.read_csv(path, comment="/")
    return [
        (
            int(r["center_wn"]),
            int(r["tolerance"]),
            str(r["name"]),
            str(r["color"]),
            "" if pd.isna(r["note"]) else str(r["note"]),
        )
        for _, r in _fg.iterrows()
    ]

def assign_group_candidates(wn, func_groups):
    """Return the best matching assignment from each selected library."""
    selected = {}
    for raw in func_groups:
        center, tol, name, color, note = raw[:5]
        library_id = raw[5] if len(raw) > 5 else "default"
        library_name = raw[6] if len(raw) > 6 else ""
        delta = abs(wn - center)
        if delta > tol:
            continue
        candidate = {
            "name": name,
            "color": color,
            "note": note,
            "library_id": library_id,
            "library_name": library_name,
            "center_wn": float(center),
            "tolerance": float(tol),
            "delta": float(delta),
        }
        current = selected.get(library_id)
        if current is None or (candidate["tolerance"], candidate["delta"]) < (
            current["tolerance"],
            current["delta"],
        ):
            selected[library_id] = candidate
    return list(selected.values())


def assign_group(wn, func_groups):
    candidates = assign_group_candidates(wn, func_groups)
    if not candidates:
        return "unknown", "#9ca3af", ""
    candidate = candidates[0]
    return candidate["name"], candidate["color"], candidate["note"]

def get_peak_near(wns, vals, center, tol=40):
    matches = [(v, w) for w, v in zip(wns, vals) if abs(w - center) <= tol]
    return max(matches)[0] if matches else 0.0

def get_peak_hit(wns, vals, center, tol=40):
    matches = [(float(v), float(w)) for w, v in zip(wns, vals) if abs(w - center) <= tol]
    if not matches:
        return None
    intensity, wn = max(matches, key=lambda x: x[0])
    return {"wn": round(wn, 1), "intensity": round(intensity, 3)}

def detect_additives_and_process_markers(peak_wn, peak_val):
    """주성분이 아닌 첨가제/공정 흔적 후보를 구조화한다."""
    T = THRESHOLDS
    markers = []

    phosphate = get_peak_hit(peak_wn, peak_val, 1276, 18)
    if phosphate and phosphate["intensity"] >= T["additive_p_min"]:
        markers.append({
            "type": "additive",
            "name": "인계 난연제 (phosphorus-based flame retardant)",
            "confidence": "HIGH" if phosphate["intensity"] >= T["additive_p_high"] else "MEDIUM",
            "evidence": [{**phosphate, "assignment": "P=O stretch"}],
            "interpretation": "건축용 PF폼의 난연 등급 보강을 위한 인산 에스터/인계 난연제 가능성",
        })

    fluorinated = get_peak_hit(peak_wn, peak_val, 1166, 18)
    if fluorinated and fluorinated["intensity"] >= T["additive_cf_min"]:
        markers.append({
            "type": "additive_or_surface_residue",
            "name": "불소계 정포제/이형제 흔적",
            "confidence": "MEDIUM",
            "evidence": [{**fluorinated, "assignment": "C-F stretch"}],
            "interpretation": "불소계 계면활성제(정포제) 또는 PTFE/PVDF계 이형제 표면 잔류 가능성",
        })

    sulfonic = get_peak_hit(peak_wn, peak_val, 1140, 18)
    if sulfonic and sulfonic["intensity"] >= T["additive_sulfonic_min"]:
        markers.append({
            "type": "process_marker",
            "name": "설폰산계 산 촉매 잔류",
            "confidence": "HIGH" if sulfonic["intensity"] >= T["additive_sulfonic_hi"] else "MEDIUM",
            "evidence": [{**sulfonic, "assignment": "S=O stretch"}],
            "interpretation": "p-TSA/BSA 등 산 촉매 경화형 상업용 PF폼 제조 흔적",
        })

    carbonyl = get_peak_hit(peak_wn, peak_val, 1715, 30)
    amide = get_peak_hit(peak_wn, peak_val, 1650, 25)
    oh_nh = get_peak_hit(peak_wn, peak_val, 3400, 200)
    if carbonyl and carbonyl["intensity"] >= T["additive_carbonyl_min"]:
        evidence = [{**carbonyl, "assignment": "C=O stretch"}]
        if amide and amide["intensity"] >= T["additive_amide_min"]:
            evidence.append({**amide, "assignment": "amide I / H2O bend overlap"})
        if oh_nh and oh_nh["intensity"] >= T["additive_ohnh_min"]:
            evidence.append({**oh_nh, "assignment": "broad O-H/N-H stretch"})
        markers.append({
            "type": "modification_or_aging",
            "name": "우레아/멜라민 변성 또는 열산화 카보닐",
            "confidence": "MEDIUM",
            "evidence": evidence,
            "interpretation": "취성 개선/원가 절감을 위한 변성 PF 또는 경화 중 표면 산화 가능성",
        })

    return markers

def detect_patterns(peak_wn, peak_val):
    results = []

    pf_markers = {
        "ch2_bridge": get_peak_near(peak_wn, peak_val, 1475, 20),
        "phenolic_co": get_peak_near(peak_wn, peak_val, 1220, 25),
        "aromatic_cc": get_peak_near(peak_wn, peak_val, 1600, 20),
        "para_oop": get_peak_near(peak_wn, peak_val, 820, 20),
        "ortho_oop": get_peak_near(peak_wn, peak_val, 755, 15),
        "sulfonic": get_peak_near(peak_wn, peak_val, 1140, 20),
    }
    pf_marker_count = sum(1 for v in pf_markers.values() if v > 0.08)
    has_pf_core = (
        pf_markers["ch2_bridge"] > 0.20 and
        pf_markers["phenolic_co"] > 0.20 and
        pf_markers["aromatic_cc"] > 0.10 and
        pf_markers["para_oop"] > 0.08
    )

    if has_pf_core:
        conf = min(1.0, 0.45 + 0.10 * pf_marker_count + pf_markers["sulfonic"] * 0.15)
        evidence = (
            f"CH2 bridge 1475 ({pf_markers['ch2_bridge']:.2f}) + "
            f"phenolic C-O 1220 ({pf_markers['phenolic_co']:.2f}) + "
            f"aromatic C=C 1600 ({pf_markers['aromatic_cc']:.2f}) + "
            f"para/ortho C-H oop ({pf_markers['para_oop']:.2f}/{pf_markers['ortho_oop']:.2f})"
        )
        if pf_markers["sulfonic"] > 0.08:
            evidence += f" + sulfonic acid catalyst 1140 ({pf_markers['sulfonic']:.2f})"
        results.append((conf, "페놀릭/PF 수지 (Phenolic Foam/Resin)", evidence))

    co = get_peak_near(peak_wn, peak_val, 1735, 30)
    coc = get_peak_near(peak_wn, peak_val, 1240, 35)
    if co > 0.1 and coc > 0.05 and not (has_pf_core and co < 0.50):
        conf = min(1.0, (co + coc) / 1.5)
        results.append((conf, "에스터 (Ester)", f"C=O ~1735 ({co:.2f}) + C-O-C ~1240 ({coc:.2f})"))
    elif has_pf_core and co > 0.1:
        conf = min(0.65, co + 0.15)
        results.append((conf, "PF 변성/산화 카보닐", f"C=O ~1715 ({co:.2f}) — 우레아 변성 또는 열산화 가능"))

    co_acid = get_peak_near(peak_wn, peak_val, 1720, 25)
    oh_broad = any(abs(w - 2800) <= 400 for w in peak_wn)
    if co_acid > 0.1 and oh_broad:
        conf = min(1.0, co_acid * 1.2)
        results.append((conf, "카르복실산 (Carboxylic Acid)", f"C=O ~1720 ({co_acid:.2f}) + broad O-H 2500-3300"))

    amide1 = get_peak_near(peak_wn, peak_val, 1655, 25)
    amide2 = get_peak_near(peak_wn, peak_val, 1550, 40)
    nh = get_peak_near(peak_wn, peak_val, 3300, 80)
    if amide1 > 0.08:
        conf = min(1.0, amide1 + amide2 * 0.5 + nh * 0.3)
        results.append((conf, "아미드 (Amide)", f"아미드 I ~1655 ({amide1:.2f}) + 아미드 II ~1550 ({amide2:.2f})"))

    oh = get_peak_near(peak_wn, peak_val, 3400, 200)
    co_alc = max(get_peak_near(peak_wn, peak_val, 1050, 35), get_peak_near(peak_wn, peak_val, 1100, 35))
    if oh > 0.1 and co_alc > 0.05:
        conf = min(1.0, (oh + co_alc) / 1.5)
        results.append((conf, "알코올 (Alcohol)", f"O-H ~3400 ({oh:.2f}) + C-O ~1050-1100 ({co_alc:.2f})"))

    ar_peaks = [get_peak_near(peak_wn, peak_val, wn, 20) for wn in [1600, 1580, 1500]]
    ar_count = sum(1 for v in ar_peaks if v > 0.05)
    oop = max(
        get_peak_near(peak_wn, peak_val, 760, 30),
        get_peak_near(peak_wn, peak_val, 700, 30),
        get_peak_near(peak_wn, peak_val, 840, 30),
    )
    if ar_count >= 2 or (ar_count >= 1 and oop > 0.05):
        conf = min(1.0, sum(ar_peaks) * 0.5 + oop * 0.3)
        results.append((conf, "방향족 고리 (Aromatic)", f"C=C ring {ar_count}개 피크 + C-H oop ({oop:.2f})"))

    cf = get_peak_near(peak_wn, peak_val, 1170, 30)
    cf_aux = max(get_peak_near(peak_wn, peak_val, 840, 12), get_peak_near(peak_wn, peak_val, 880, 12))
    if cf > 0.1 and cf_aux > 0.1 and not has_pf_core:
        conf = min(0.85, cf * 0.8 + cf_aux * 0.5)
        results.append((conf, "플루오로폴리머 (PVDF/PTFE)", f"C-F ~1170 ({cf:.2f}) + 보조 C-F band ({cf_aux:.2f})"))
    elif cf > 0.1 and not has_pf_core:
        conf = min(0.35, cf * 0.5)
        results.append((conf, "C-F 가능성 (단일 피크)", f"C-F 후보 ~1170 ({cf:.2f}) — 단일 피크라 확정 불가"))

    siosi = get_peak_near(peak_wn, peak_val, 1100, 40)
    ch3si = get_peak_near(peak_wn, peak_val, 1260, 30)
    phosphate = get_peak_near(peak_wn, peak_val, 1276, 18)
    if siosi > 0.15 and ch3si > 0.05 and not (has_pf_core and phosphate > 0.12):
        conf = min(1.0, (siosi + ch3si) / 1.5)
        results.append((conf, "실리콘 (Silicone/Silicate)", f"Si-O-Si ~1100 ({siosi:.2f}) + Si-CH3 ~1260 ({ch3si:.2f})"))

    cn = get_peak_near(peak_wn, peak_val, 2230, 25)
    if cn > 0.08:
        conf = min(1.0, cn * 2.0)
        results.append((conf, "니트릴 (Nitrile)", f"C≡N ~2230 ({cn:.2f}) 날카로운 피크"))

    co_ald = get_peak_near(peak_wn, peak_val, 1695, 18)
    ch_ald = get_peak_near(peak_wn, peak_val, 2720, 30)
    if co_ald > 0.08 and ch_ald > 0.03:
        conf = min(1.0, co_ald + ch_ald)
        results.append((conf, "알데히드 (Aldehyde)", f"C=O ~1695 ({co_ald:.2f}) + Fermi C-H ~2720 ({ch_ald:.2f})"))

    return sorted(results, key=lambda x: -x[0])


# ──────────────────────────────────────────────────────────────
# 소견 자동 생성 — v3 신규 (미동정/애매 시 정성 해석)
# ──────────────────────────────────────────────────────────────

# 진단(diagnostic) 작용기 영역: (이름, 하한, 상한, 화학적 의미)
# 강한 피크가 이 영역에 있으면 후보 물질에 해당 작용기가 있어야 함.

DIAGNOSTIC_REGIONS = [
    ("O-H / N-H stretch",   3200, 3600, "수산기/아민/아마이드 (단, 수분 영향 주의)"),
    ("C-H stretch",         2840, 3000, "지방족/방향족 C-H"),
    ("C≡N / C≡C",           2200, 2270, "니트릴/알카인"),
    ("C=O stretch",         1680, 1760, "카보닐 (에스터/케톤/카르복실산/아마이드)"),
    ("aromatic C=C",        1480, 1620, "방향족 고리 또는 C=C"),
    ("C-O / S=O / C-F",     1000, 1300, "에터/에스터 C-O, 설폰 S=O, C-F"),
    ("aromatic C-H oop",     650,  900, "방향족 면외 굽힘 (치환 패턴)"),
    ("inorganic / metal-O",  400,  650, "무기염/금속산화물 격자 진동"),
]

# 환경 아티팩트 영역: 시료 성분이 아니라 측정 환경(공기/표면) 영향 가능

ENV_ARTIFACT_REGIONS = [
    ("CO₂ (대기)",        2300, 2400, "대기 중 이산화탄소 — 보통 시료 성분 아님"),
    ("H₂O O-H (수분)",    3550, 3700, "수증기/표면 수분 — 넓은 흡수면 주의"),
    ("H₂O 굽힘 (수분)",   1620, 1660, "수분 H-O-H 굽힘 — 단 1715 부근 C=O와 구별 필요"),
]

def classify_peaks_by_region(peak_wn, peak_val, regions):
    """피크를 영역별로 분류. 반환: [(region_name, meaning, [(wn,intensity),...]), ...]"""
    out = []
    for name, lo, hi, meaning in regions:
        hits = [(float(w), float(v)) for w, v in zip(peak_wn, peak_val) if lo <= w <= hi]
        if hits:
            hits.sort(key=lambda x: -x[1])
            out.append((name, meaning, hits))
    return out

def tag_environmental_peaks(peak_wn, peak_val):
    """환경 아티팩트 가능 피크 태깅. 반환: [{region, meaning, peaks}]"""
    tagged = []
    for name, lo, hi, meaning in ENV_ARTIFACT_REGIONS:
        hits = [{"wn": round(float(w), 1), "intensity": round(float(v), 3)}
                for w, v in zip(peak_wn, peak_val) if lo <= w <= hi]
        if hits:
            tagged.append({"region": name, "range_cm": [lo, hi],
                           "meaning": meaning, "peaks": hits})
    return tagged

def check_diagnostic_mismatch(peak_wn, peak_val, candidate_lib_peaks, strong_thresh=0.30, tol=20.0):
    """
    시료의 강한 diagnostic 피크가 후보(라이브러리)에 없으면 경고.
    점수는 건드리지 않고 경고 플래그만 생성 (사용자 선택: 경고만 기록).
    반환: [{region, sample_peak, meaning, note}]
    """
    warnings = []
    lib = np.asarray(candidate_lib_peaks, dtype=float)
    for name, lo, hi, meaning in DIAGNOSTIC_REGIONS:
        # 이 영역에서 시료의 가장 강한 피크
        region_hits = [(float(w), float(v)) for w, v in zip(peak_wn, peak_val) if lo <= w <= hi]
        if not region_hits:
            continue
        region_hits.sort(key=lambda x: -x[1])
        w_strong, v_strong = region_hits[0]
        if v_strong < strong_thresh:
            continue  # 약한 피크는 경고 대상 아님
        # 후보에 이 피크 근처(±tol) 피크가 있는가?
        has_match = len(lib) > 0 and np.any(np.abs(lib - w_strong) <= tol)
        if not has_match:
            warnings.append({
                "region": name,
                "sample_peak_cm": round(w_strong, 1),
                "intensity": round(v_strong, 3),
                "meaning": meaning,
                "note": f"시료에 강한 {name} 피크({w_strong:.0f}cm⁻¹, 강도 {v_strong:.2f})가 "
                        f"있으나 최상위 후보에는 대응 피크 없음 — 단일 성분으로 설명 어려움",
            })
    return warnings

def build_findings(peak_wn, peak_val, patterns, best_per_material,
                   top_lib_peaks, tier):
    """
    미동정/애매 시료의 정성 소견을 구조화. verdict 텍스트와 JSON 양쪽에 사용.
    - functional_groups: detect_patterns 결과 (물질명 아닌 작용기)
    - region_summary: diagnostic 영역별 관찰 피크
    - environmental_flags: CO₂/H₂O 등 환경 아티팩트 태깅
    - reference_candidates: 참고용 상위 후보 (확정 아님)
    - mismatch_warnings: 강한 diagnostic 피크 불일치 경고
    """
    findings = {}

    # 1) 작용기 정성 해석 (물질명 없이)
    findings["functional_groups"] = [
        {"group": name, "confidence_pct": round(conf * 100, 1), "evidence": evidence}
        for conf, name, evidence in patterns
    ]

    # 2) diagnostic 영역별 관찰 피크 요약
    region_cls = classify_peaks_by_region(peak_wn, peak_val, DIAGNOSTIC_REGIONS)
    findings["region_summary"] = [
        {"region": name, "meaning": meaning,
         "peaks": [{"wn": round(w, 1), "intensity": round(v, 3)} for w, v in hits[:4]]}
        for name, meaning, hits in region_cls
    ]

    # 3) 환경 아티팩트 태깅
    findings["environmental_flags"] = tag_environmental_peaks(peak_wn, peak_val)

    # 3b) 주성분과 분리된 첨가제/공정 흔적
    findings["additives_and_process_markers"] = detect_additives_and_process_markers(
        peak_wn, peak_val
    )

    # 4) 참고용 상위 후보 (확정 아님 명시)
    findings["reference_candidates"] = [
        {"rank": int(rank + 1), "material": row["material"],
         "category": row["category_label"],
         "composite_pct": float(row["composite_pct"]),
         "cosine_pct": float(row["cosine_pct"]),
         "deriv_pct": float(row["deriv_pct"]),
         "peak_pct": float(row["peak_pct"]),
         "note": "참고용 — 부분 유사도일 뿐 확정 동정 아님"}
        for rank, row in best_per_material.head(3).iterrows()
    ]

    # 5) diagnostic mismatch 경고 (최상위 후보 기준, 점수 영향 없음)
    findings["mismatch_warnings"] = check_diagnostic_mismatch(
        peak_wn, peak_val, top_lib_peaks
    )

    findings["tier"] = tier
    return findings

def findings_to_text(findings, sample_label):
    """소견 JSON을 사람이 읽는 한국어 verdict 문단으로 변환."""
    lines = []
    fg = findings.get("functional_groups", [])
    if fg:
        groups = ", ".join(f"{g['group']}({g['confidence_pct']:.0f}%)" for g in fg[:5])
        lines.append(f"■ 관찰된 작용기 패턴: {groups}")

    rs = findings.get("region_summary", [])
    if rs:
        lines.append("■ 주요 흡수대 (영역별):")
        for r in rs:
            peaks = ", ".join(f"{p['wn']:.0f}" for p in r["peaks"])
            lines.append(f"   - {r['region']} [{r['meaning']}]: {peaks} cm⁻¹")

    env = findings.get("environmental_flags", [])
    if env:
        lines.append("■ 환경 아티팩트 가능 피크 (해석 주의):")
        for e in env:
            peaks = ", ".join(f"{p['wn']:.0f}" for p in e["peaks"])
            lines.append(f"   - {e['region']}: {peaks} cm⁻¹ → {e['meaning']}")

    additives = findings.get("additives_and_process_markers", [])
    if additives:
        lines.append("■ 첨가제/공정 흔적:")
        for marker in additives:
            peaks = ", ".join(f"{p['wn']:.0f}" for p in marker["evidence"])
            lines.append(f"   - {marker['name']} [{marker['confidence']}]: {peaks} cm⁻¹ → {marker['interpretation']}")

    mw = findings.get("mismatch_warnings", [])
    if mw:
        lines.append("■ ⚠ 단일 성분 불일치 경고:")
        for w in mw:
            lines.append(f"   - {w['note']}")

    rc = findings.get("reference_candidates", [])
    if rc:
        lines.append("■ 참고용 상위 후보 (확정 아님):")
        for c in rc:
            lines.append(f"   - #{c['rank']} {c['material']} — 종합 {c['composite_pct']:.1f}% "
                         f"(코사인 {c['cosine_pct']:.0f}/미분 {c['deriv_pct']:.0f}/피크 {c['peak_pct']:.0f})")

    # 종합 소견 문장
    has_carbonyl = any("C=O" in r["region"] for r in rs)
    has_aromatic = any("aromatic C=C" in r["region"] for r in rs)
    has_co = any("C-O" in r["region"] for r in rs)
    has_oh = any("O-H" in r["region"] for r in rs)
    qual = []
    if has_aromatic: qual.append("방향족 고리")
    if has_carbonyl: qual.append("카보닐(C=O) 계열")
    if has_co: qual.append("C-O/S=O 계열")
    if has_oh: qual.append("O-H/N-H 계열")
    if qual:
        lines.append("")
        lines.append(f"▶ 종합 소견: {' + '.join(qual)} 흡수대가 관찰되어, "
                     f"단일 성분보다는 혼합물 또는 라이브러리 미수록 물질일 가능성이 있음. "
                     f"확정 동정을 위해서는 추가 분석(상보적 기법) 또는 라이브러리 보강 권장.")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# 룰 기반 동정 엔진 (Rule-Based Identification Engine)
# ──────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: data/rist_library/ 의 스펙트럼 중 data/RIST_FTIR_Library/ 에 빠진
#            항목을 목적별 서브디렉토리로 복사하고 manifest.csv 를 갱신한다.
# 실행 방법: python scripts/library/add_missing_to_library.py
#            (인자 없음 — 입력/출력 경로는 파일 상단 상수에 내장)
# ─────────────────────────────────────────────────────────────────────────────
"""
data/rist_library/ 에서 data/RIST_FTIR_Library/ 에 누락된 스펙트럼을 추가하고
manifest.csv 를 갱신한다.
"""

import shutil
import csv
from pathlib import Path
import numpy as np

# scripts/library/ 에서 sune 루트로 올라간다 (parents[2] = sune)
BASE = Path(__file__).resolve().parents[2]
SRC_ROOT = BASE / "data" / "rist_library"
LIB_ROOT = BASE / "data" / "RIST_FTIR_Library"
MANIFEST = LIB_ROOT / "manifest.csv"

# ─── 목적 서브디렉토리 매핑 규칙 ─────────────────────────────────────────────
# (검사 순서 중요: 더 구체적인 조건을 먼저)
DEST_RULES = [
    # battery
    ("battery/nist_", "01_battery/01_electrolyte_solvents"),
    # steel_coating  –  corrosion inhibitors
    ("steel_coating/nist_Benzimidazole", "02_steel_coating/01_corrosion_inhibitors"),
    ("steel_coating/nist_Benzotriazole", "02_steel_coating/01_corrosion_inhibitors"),
    # steel_coating  –  lubricants
    ("steel_coating/nist_DMSO",          "02_steel_coating/02_lubricants"),
    ("steel_coating/nist_Oleic",         "02_steel_coating/02_lubricants"),
    ("steel_coating/nist_Stearic",       "02_steel_coating/02_lubricants"),
    ("steel_coating/nist_Tributyl",      "02_steel_coating/02_lubricants"),
    ("steel_coating/nist_Triethyl",      "02_steel_coating/02_lubricants"),
    # engineering_plastic – bioplastics (PHBV, TPA, TAGs)
    ("engineering_plastic/zenodo_17195859_FT-IR_PHBV",            "03_engineering_plastic/03_bioplastics"),
    ("engineering_plastic/zenodo_17195859_FT-IR_commercial_TPA",  "03_engineering_plastic/03_bioplastics"),
    ("engineering_plastic/zenodo_17195859_FT-IR_TAGs",            "03_engineering_plastic/03_bioplastics"),
    # engineering_plastic – PET reprocessed → engineering
    ("engineering_plastic/zenodo_17195859_FT-IR_REX-PET",         "03_engineering_plastic/02_engineering"),
    # engineering_plastic – existing Nylon / PMMA → engineering
    ("engineering_plastic/existing_Nylon",  "03_engineering_plastic/02_engineering"),
    ("engineering_plastic/existing_PMMA",   "03_engineering_plastic/02_engineering"),
]

# ─── 소재명 & 출처 매핑 ──────────────────────────────────────────────────────
def infer_material(stem: str) -> str:
    """파일 스템에서 소재명을 추출한다."""
    s = stem
    mapping = {
        "nist_Acetonitrile__AN__":              "Acetonitrile (AN)",
        "nist_Diethyl_Carbonate__DEC__":        "Diethyl Carbonate (DEC)",
        "nist_Dimethyl_Carbonate__DMC__":       "Dimethyl Carbonate (DMC)",
        "nist_Ethylene_Carbonate__EC__":        "Ethylene Carbonate (EC)",
        "nist_gamma_Butyrolactone__GBL__":      "gamma-Butyrolactone (GBL)",
        "nist_NMP__N_Methyl_2_pyrrolidone__":   "NMP (N-Methyl-2-pyrrolidone)",
        "nist_Propylene_Carbonate__PC__":       "Propylene Carbonate (PC)",
        "nist_Benzimidazole__corrosion_inhibitor__": "Benzimidazole",
        "nist_Benzotriazole__corrosion_inhibitor__": "Benzotriazole",
        "nist_DMSO":                            "DMSO",
        "nist_Oleic_acid__lubricant__":         "Oleic Acid",
        "nist_Stearic_acid__lubricant__":       "Stearic Acid",
        "nist_Tributyl_phosphate":              "Tributyl Phosphate",
        "nist_Triethyl_phosphate":              "Triethyl Phosphate",
        "existing_Nylon-6.":                    "Nylon 6",   # 뒤에 숫자 없으므로 고정
        "existing_Nylon-66":                    "Nylon 66",
        "existing_PMMA-":                       "PMMA",
        "zenodo_17195859_FT-IR_commercial_TPA": "Terephthalic Acid (TPA)",
        "zenodo_17195859_FT-IR_PHBV":           "PHBV",
        "zenodo_17195859_FT-IR_REX-PET_sample": "Polyethylene Terephthalate (PET)",
        "zenodo_17195859_FT-IR_TAGs":           "Triglycerides (TAGs)",
    }
    for prefix, name in mapping.items():
        if s.startswith(prefix) or prefix in s:
            return name
    # fallback: 파일명 그대로
    return s.replace("_", " ")


def infer_source(stem: str) -> str:
    if stem.startswith("nist_"):
        return "NIST WebBook"
    if stem.startswith("zenodo_"):
        return "Zenodo"
    if stem.startswith("existing_"):
        return "RIST existing"
    return "unknown"


# ─── 이미 등록된 파일 basename 수집 ──────────────────────────────────────────
existing_basenames = set()
with open(MANIFEST, newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        existing_basenames.add(Path(row["file"]).name)

print(f"기존 라이브러리 파일 수: {len(existing_basenames)}")

# ─── 새 파일 탐색 및 추가 ─────────────────────────────────────────────────────
new_rows = []
copied = []

for src_csv in sorted(SRC_ROOT.rglob("*.csv")):
    basename = src_csv.name
    if basename in existing_basenames:
        continue  # 이미 있음

    # 상대 경로로 dest 서브디렉토리 결정
    rel = str(src_csv.relative_to(SRC_ROOT))  # e.g. "battery/nist_Aceto..."
    dest_sub = None
    for pattern, sub in DEST_RULES:
        if pattern in rel:
            dest_sub = sub
            break

    if dest_sub is None:
        print(f"  [SKIP] 목적 경로 매핑 없음: {rel}")
        continue

    dest_dir = LIB_ROOT / dest_sub
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file = dest_dir / basename

    # 복사
    shutil.copy2(src_csv, dest_file)
    copied.append(dest_file)

    # manifest 행 계산
    try:
        import pandas as pd
        df = pd.read_csv(src_csv)
        # 컬럼 찾기
        wn_col = next(c for c in df.columns if "wave" in c.lower() or c.lower() == "wn")
        ab_col = next(c for c in df.columns if "abs" in c.lower() or "int" in c.lower())
        wn = df[wn_col].astype(float)
        wn_min = round(float(wn.min()), 1)
        wn_max = round(float(wn.max()), 1)
        n_points = len(df)
    except Exception as e:
        print(f"  [WARN] 메타데이터 계산 실패 {basename}: {e}")
        wn_min, wn_max, n_points = "", "", ""

    stem = src_csv.stem
    material = infer_material(stem)
    source = infer_source(stem)
    rel_path = f"{dest_sub}/{basename}"

    new_rows.append({
        "file": rel_path,
        "material": material,
        "source": source,
        "intensity_type": "absorbance",
        "n_points": n_points,
        "wn_min": wn_min,
        "wn_max": wn_max,
    })
    print(f"  [ADD] {rel_path}  ({material})")

# ─── manifest.csv 에 새 행 추가 ───────────────────────────────────────────────
if new_rows:
    with open(MANIFEST, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["file","material","source","intensity_type","n_points","wn_min","wn_max"])
        writer.writerows(new_rows)
    print(f"\n완료: {len(new_rows)}개 스펙트럼 추가됨 → manifest.csv 갱신됨")
else:
    print("\n새로 추가할 파일이 없습니다.")

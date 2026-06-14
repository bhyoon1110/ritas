# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: NIST WebBook(JCAMP)에서 지정한 화합물(CAS 기준) FTIR 스펙트럼을
#            다운로드해 data/RIST_FTIR_Library/ 에 추가하고 manifest.csv 를 갱신한다.
#            (네트워크 연결 필요)
# 실행 방법: python scripts/library/download_nist_ftir.py
#            (인자 없음 — 대상 목록 TARGETS 와 경로는 파일 상단에 내장)
# ─────────────────────────────────────────────────────────────────────────────
"""
NIST WebBook에서 FTIR 스펙트럼을 다운로드하여 RIST 라이브러리에 추가한다.
URL 패턴: https://webbook.nist.gov/cgi/cbook.cgi?JCAMP=C{CAS_nodash}&Index={n}&Type=IR
"""

import re
import csv
import time
import numpy as np
import urllib.request
from pathlib import Path

# scripts/library/ 에서 sune 루트로 올라간다 (parents[2] = sune)
BASE      = Path(__file__).resolve().parents[2]
LIB_ROOT  = BASE / "data" / "RIST_FTIR_Library"
MANIFEST  = LIB_ROOT / "manifest.csv"
NIST_BASE = "https://webbook.nist.gov/cgi/cbook.cgi?JCAMP={cas}&Index={idx}&Type=IR"

# ─── 다운로드 대상 ─────────────────────────────────────────────────────────────
# (material_name, CAS_with_dashes, dest_subdir, file_prefix)
TARGETS = [
    # ── Battery electrolyte solvents ────────────────────────────────────────
    ("THF (Tetrahydrofuran)",         "109-99-9",    "01_battery/01_electrolyte_solvents",   "nist_THF"),
    ("1,2-Dimethoxyethane (DME)",     "110-71-4",    "01_battery/01_electrolyte_solvents",   "nist_DME"),
    ("1,3-Dioxolane (DOL)",           "646-06-0",    "01_battery/01_electrolyte_solvents",   "nist_DOL"),
    ("Sulfolane (TMS)",               "126-33-0",    "01_battery/01_electrolyte_solvents",   "nist_Sulfolane"),
    ("Trimethyl Phosphate (TMP)",     "512-56-1",    "01_battery/01_electrolyte_solvents",   "nist_TMP"),
    ("Diethyl Ether",                 "60-29-7",     "01_battery/01_electrolyte_solvents",   "nist_DiethylEther"),
    ("1,4-Dioxane",                   "123-91-1",    "01_battery/01_electrolyte_solvents",   "nist_Dioxane"),
    ("Acetone",                       "67-64-1",     "01_battery/01_electrolyte_solvents",   "nist_Acetone"),
    ("Water",                         "7732-18-5",   "01_battery/01_electrolyte_solvents",   "nist_Water"),

    # ── Steel coating – corrosion inhibitors ────────────────────────────────
    ("2-Mercaptobenzothiazole (MBT)", "149-30-4",    "02_steel_coating/01_corrosion_inhibitors", "nist_MBT"),
    ("Imidazole",                     "288-32-4",    "02_steel_coating/01_corrosion_inhibitors", "nist_Imidazole"),
    ("Sodium Benzoate",               "532-32-1",    "02_steel_coating/01_corrosion_inhibitors", "nist_SodiumBenzoate"),
    ("Phosphoric Acid",               "7664-38-2",   "02_steel_coating/01_corrosion_inhibitors", "nist_PhosphoricAcid"),

    # ── Steel coating – lubricants ──────────────────────────────────────────
    ("Glycerol",                      "56-81-5",     "02_steel_coating/02_lubricants",    "nist_Glycerol"),
    ("Lauric Acid",                   "143-07-7",    "02_steel_coating/02_lubricants",    "nist_LauricAcid"),
    ("Palmitic Acid",                 "57-10-3",     "02_steel_coating/02_lubricants",    "nist_PalmiticAcid"),
    ("Linoleic Acid",                 "60-33-3",     "02_steel_coating/02_lubricants",    "nist_LinoleicAcid"),
    ("n-Hexadecane",                  "544-76-3",    "02_steel_coating/02_lubricants",    "nist_Hexadecane"),
    ("Squalene",                      "111-02-4",    "02_steel_coating/02_lubricants",    "nist_Squalene"),

    # ── Steel coating – coatings / solvents ────────────────────────────────
    ("Toluene",                       "108-88-3",    "02_steel_coating/03_coatings_resins", "nist_Toluene"),
    ("m-Xylene",                      "108-38-3",    "02_steel_coating/03_coatings_resins", "nist_mXylene"),
    ("Methanol",                      "67-56-1",     "02_steel_coating/03_coatings_resins", "nist_Methanol"),
    ("Ethanol",                       "64-17-5",     "02_steel_coating/03_coatings_resins", "nist_Ethanol"),
    ("Isopropanol (IPA)",             "67-63-0",     "02_steel_coating/03_coatings_resins", "nist_IPA"),
    ("Methyl Ethyl Ketone (MEK)",     "78-93-3",     "02_steel_coating/03_coatings_resins", "nist_MEK"),
    ("n-Hexane",                      "110-54-3",    "02_steel_coating/03_coatings_resins", "nist_Hexane"),
    ("Cyclohexane",                   "110-82-7",    "02_steel_coating/03_coatings_resins", "nist_Cyclohexane"),
    ("Ethyl Acetate",                 "141-78-6",    "02_steel_coating/03_coatings_resins", "nist_EthylAcetate"),
    ("Dichloromethane (DCM)",         "75-09-2",     "02_steel_coating/03_coatings_resins", "nist_DCM"),
    ("n-Heptane",                     "142-82-5",    "02_steel_coating/03_coatings_resins", "nist_Heptane"),
    ("Chloroform",                    "67-66-3",     "02_steel_coating/03_coatings_resins", "nist_Chloroform"),

    # ── Engineering plastic monomers / additives ────────────────────────────
    ("Styrene",                       "100-42-5",    "03_engineering_plastic/01_commodity",  "nist_Styrene"),
    ("Methyl Methacrylate (MMA)",     "80-62-6",     "03_engineering_plastic/01_commodity",  "nist_MMA"),
    ("Caprolactam",                   "105-60-2",    "03_engineering_plastic/02_engineering","nist_Caprolactam"),
    ("Bisphenol A (BPA)",             "80-05-7",     "03_engineering_plastic/02_engineering","nist_BPA"),
    ("Adipic Acid",                   "124-04-9",    "03_engineering_plastic/02_engineering","nist_AdipicAcid"),
    ("Lactic Acid",                   "50-21-5",     "03_engineering_plastic/03_bioplastics","nist_LacticAcid"),
    ("Glycolic Acid",                 "79-14-1",     "03_engineering_plastic/03_bioplastics","nist_GlycolicAcid"),
]

# ─── JDX 파서 ─────────────────────────────────────────────────────────────────
def cas_to_nist(cas: str) -> str:
    """109-99-9  →  C109999"""
    return "C" + cas.replace("-", "")


def parse_jdx(text: str) -> tuple[np.ndarray, np.ndarray, str]:
    """
    JCAMP-DX 텍스트를 파싱하여 (wavenumber, absorbance, yunits) 반환.
    """
    meta = {}
    for key in ["XUNITS", "YUNITS", "XFACTOR", "YFACTOR", "DELTAX",
                "FIRSTX", "LASTX", "NPOINTS"]:
        m = re.search(rf"##{key}\s*=\s*(.+)", text)
        if m:
            meta[key] = m.group(1).strip()

    yunits = meta.get("YUNITS", "").upper()
    xfactor = float(meta.get("XFACTOR", "1.0"))
    yfactor = float(meta.get("YFACTOR", "1.0"))
    deltax  = float(meta.get("DELTAX",  "1.0"))

    # XYPOINTS 형식 (직접 X,Y 쌍)
    if "##XYPOINTS=(XY..XY)" in text:
        block_m = re.search(r"##XYPOINTS=\(XY\.\.XY\)(.*?)##END", text, re.DOTALL)
        if block_m:
            rows = block_m.group(1).strip().split("\n")
            wn_vals, y_vals = [], []
            for row in rows:
                parts = row.replace(",", " ").split()
                if len(parts) >= 2:
                    wn_vals.append(float(parts[0]) * xfactor)
                    y_vals.append(float(parts[1]) * yfactor)
            return np.array(wn_vals), np.array(y_vals), yunits

    # X++(Y..Y) 형식 (NIST 일반)
    block_m = re.search(r"##XYDATA=\(X\+\+\(Y\.\.Y\)\)(.*?)##END", text, re.DOTALL)
    if not block_m:
        raise ValueError("알 수 없는 JCAMP 형식")

    block = block_m.group(1).strip()
    wn_vals, y_vals = [], []
    for line in block.split("\n"):
        line = line.strip()
        if not line or line.startswith("##"):
            break
        nums = line.split()
        if not nums:
            continue
        x_start = float(nums[0]) * xfactor
        for i, val in enumerate(nums[1:]):
            try:
                wn_vals.append(x_start + i * deltax)
                y_vals.append(float(val) * yfactor)
            except ValueError:
                pass

    return np.array(wn_vals), np.array(y_vals), yunits


def transmittance_to_absorbance(t_pct: np.ndarray) -> np.ndarray:
    """%Transmittance → Absorbance (A = -log10(T/100))"""
    t = np.clip(t_pct, 0.0001, 100.0)
    return -np.log10(t / 100.0)


def download_jdx(cas_nist: str, index: int, timeout: int = 15) -> str | None:
    """NIST에서 JDX 텍스트를 받아 반환. 실패 시 None."""
    url = NIST_BASE.format(cas=cas_nist, idx=index)
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (research; FTIR library builder)"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            # NIST는 HTML을 반환하면 JDX가 아님
            if raw[:2] == b"<!":
                return None
            return raw.decode("utf-8", errors="replace")
    except Exception:
        return None


# ─── 이미 등록된 파일 basename 수집 ──────────────────────────────────────────
existing_basenames: set[str] = set()
with open(MANIFEST, newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        existing_basenames.add(Path(row["file"]).name)

print(f"기존 항목: {len(existing_basenames)}개")

# ─── 다운로드 루프 ─────────────────────────────────────────────────────────────
new_rows: list[dict] = []
MAX_INDEX = 6   # 최대 시도 index

for material, cas, dest_sub, prefix in TARGETS:
    cas_nist = cas_to_nist(cas)
    dest_dir = LIB_ROOT / dest_sub
    dest_dir.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    for idx in range(MAX_INDEX):
        basename = f"{prefix}_n{idx}.csv"
        if basename in existing_basenames:
            # 이미 있으면 다음 index 시도 (있는 만큼 건너뜀)
            downloaded += 1
            continue

        time.sleep(0.4)   # rate-limit
        jdx_text = download_jdx(cas_nist, idx)
        if jdx_text is None:
            break   # 더 이상 없음

        try:
            wn, y, yunits = parse_jdx(jdx_text)
        except Exception as e:
            print(f"  [ERR] {prefix}_n{idx}: {e}")
            break

        if len(wn) < 50:
            print(f"  [SKIP] {prefix}_n{idx}: 데이터 포인트 부족 ({len(wn)})")
            continue

        # transmittance → absorbance 변환
        if "TRANSMIT" in yunits:
            y = transmittance_to_absorbance(y)
            intensity_type = "absorbance (converted from %T)"
        else:
            intensity_type = "absorbance"

        # 범위 내 데이터만 (400~4000 cm-1)
        mask = (wn >= 400) & (wn <= 4000)
        wn, y = wn[mask], y[mask]
        if len(wn) < 50:
            print(f"  [SKIP] {prefix}_n{idx}: 400-4000 범위 포인트 부족")
            continue

        # CSV 저장
        dest_file = dest_dir / basename
        header_line = "wavenumber,absorbance\n"
        with open(dest_file, "w", encoding="utf-8") as f:
            f.write(header_line)
            for w, a in zip(wn, y):
                f.write(f"{w:.2f},{a:.8f}\n")

        existing_basenames.add(basename)
        downloaded += 1

        new_rows.append({
            "file": f"{dest_sub}/{basename}",
            "material": material,
            "source": "NIST WebBook",
            "intensity_type": intensity_type,
            "n_points": len(wn),
            "wn_min": round(float(wn.min()), 1),
            "wn_max": round(float(wn.max()), 1),
        })
        print(f"  [OK] {dest_sub}/{basename}  ({material}, {len(wn)} pts, {wn.min():.0f}-{wn.max():.0f} cm⁻¹)")

    if downloaded == 0:
        print(f"  [NONE] {material} ({cas}) – NIST에 스펙트럼 없음")

# ─── manifest 추가 ─────────────────────────────────────────────────────────────
if new_rows:
    with open(MANIFEST, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["file","material","source","intensity_type","n_points","wn_min","wn_max"]
        )
        writer.writerows(new_rows)
    print(f"\n완료: {len(new_rows)}개 스펙트럼 추가 → manifest.csv 갱신됨")
    print(f"총 라이브러리: {262 + len(new_rows)}개 (예상)")
else:
    print("\n새 스펙트럼 없음")

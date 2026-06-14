#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: 흩어진 raw FTIR 데이터를 일관된 형식(CSV)으로 표준화하여
#            data/RIST_FTIR_Library/ 한 폴더에 모으고 manifest 를 생성한다.
# 실행 방법: python scripts/library/organize_library.py
#            (인자 없음 — 입력/출력 경로는 파일 상단 BASE 상수에 내장)
# ─────────────────────────────────────────────────────────────────────────────
"""
RIST FTIR 라이브러리 전체 정리 스크립트
모든 raw 데이터를 하나의 폴더에 표준화하여 저장
출력: data/RIST_FTIR_Library/
"""

import re, io, shutil
import numpy as np
import pandas as pd
from pathlib import Path

BASE   = Path('/Users/byeonghoonyoon/PROJECT/RIST/data')
OUTPUT = BASE / 'RIST_FTIR_Library'

# ──────────────────────────────────────────────────────────────────
# 디렉토리 구조
# ──────────────────────────────────────────────────────────────────
DIRS = {
    'bat_electrolyte':    OUTPUT / '01_battery'            / '01_electrolyte_solvents',
    'bat_binder':         OUTPUT / '01_battery'            / '02_binders_polymers',
    'ste_inhibitor':      OUTPUT / '02_steel_coating'      / '01_corrosion_inhibitors',
    'ste_lubricant':      OUTPUT / '02_steel_coating'      / '02_lubricants',
    'ste_resin':          OUTPUT / '02_steel_coating'      / '03_coatings_resins',
    'eng_commodity':      OUTPUT / '03_engineering_plastic'/ '01_commodity',
    'eng_engineering':    OUTPUT / '03_engineering_plastic'/ '02_engineering',
    'eng_bioplastic':     OUTPUT / '03_engineering_plastic'/ '03_bioplastics',
    'elastomer':          OUTPUT / '04_elastomers_seals',
    'ceramic':            OUTPUT / '05_ceramic_inorganic',
}
for d in DIRS.values():
    d.mkdir(parents=True, exist_ok=True)

manifest_rows = []


# ──────────────────────────────────────────────────────────────────
# 유틸: CSV 저장 & 매니페스트 추가
# ──────────────────────────────────────────────────────────────────
def save(df, dest_dir, filename, material, source, intensity_type='absorbance'):
    path = dest_dir / filename
    df.to_csv(path, index=False)
    wn = df['wavenumber'].dropna()
    manifest_rows.append({
        'file':           str(path.relative_to(OUTPUT)),
        'material':       material,
        'source':         source,
        'intensity_type': intensity_type,
        'n_points':       len(df),
        'wn_min':         round(wn.min(), 1) if len(wn) else '',
        'wn_max':         round(wn.max(), 1) if len(wn) else '',
    })


# ──────────────────────────────────────────────────────────────────
# 유틸: %T → 흡광도 변환
# ──────────────────────────────────────────────────────────────────
def transmittance_to_absorbance(arr, pct=True):
    """pct=True → 0~100 범위 %T, pct=False → 0~1 범위 T"""
    arr = np.array(arr, dtype=float)
    if pct:
        arr = arr / 100.0
    arr = np.clip(arr, 1e-6, None)
    return -np.log10(arr)


# ──────────────────────────────────────────────────────────────────
# 공통 후처리: 650–4000 cm⁻¹ 범위 필터 + 정렬
# ──────────────────────────────────────────────────────────────────
def trim(df, wn_col='wavenumber', val_col='absorbance'):
    df = df[[wn_col, val_col]].copy()
    df.columns = ['wavenumber', 'absorbance']
    df = df.apply(pd.to_numeric, errors='coerce').dropna()
    df = df[(df['wavenumber'] >= 650) & (df['wavenumber'] <= 4000)]
    return df.sort_values('wavenumber').reset_index(drop=True)


# ════════════════════════════════════════════════════════════════════
# 소스 1 : NIST WebBook
#   data/rist_library/battery/nist_*.csv
#   data/rist_library/steel_coating/nist_*.csv
# ════════════════════════════════════════════════════════════════════
NIST_SUBCATEGORY = {
    # 배터리 전해질 용매
    'Ethylene_Carbonate':     ('bat_electrolyte', 'Ethylene Carbonate (EC)'),
    'Dimethyl_Carbonate':     ('bat_electrolyte', 'Dimethyl Carbonate (DMC)'),
    'Diethyl_Carbonate':      ('bat_electrolyte', 'Diethyl Carbonate (DEC)'),
    'Propylene_Carbonate':    ('bat_electrolyte', 'Propylene Carbonate (PC)'),
    'NMP':                    ('bat_electrolyte', 'NMP (N-Methyl-2-pyrrolidone)'),
    'gamma_Butyrolactone':    ('bat_electrolyte', 'gamma-Butyrolactone (GBL)'),
    'Acetonitrile':           ('bat_electrolyte', 'Acetonitrile (AN)'),
    'N_N_Dimethylacetamide':  ('bat_electrolyte', 'N,N-Dimethylacetamide (DMAc)'),
    'Vinylene_Carbonate':     ('bat_electrolyte', 'Vinylene Carbonate (VC)'),
    # 철강 윤활제
    'Oleic_acid':             ('ste_lubricant', 'Oleic Acid'),
    'Stearic_acid':           ('ste_lubricant', 'Stearic Acid'),
    'Triethyl_phosphate':     ('ste_lubricant', 'Triethyl Phosphate'),
    'Tributyl_phosphate':     ('ste_lubricant', 'Tributyl Phosphate'),
    'DMSO':                   ('ste_lubricant', 'DMSO'),
    # 방청제
    'Benzimidazole':          ('ste_inhibitor', 'Benzimidazole'),
    'Benzotriazole':          ('ste_inhibitor', 'Benzotriazole'),
    '2_Mercaptobenzothiazole':('ste_inhibitor', '2-Mercaptobenzothiazole'),
    'Imidazole':              ('ste_inhibitor', 'Imidazole'),
}

def process_nist():
    count = 0
    for cat_key in ('battery', 'steel_coating'):
        src_dir = BASE / 'rist_library' / cat_key
        for f in sorted(src_dir.glob('nist_*.csv')):
            stem = f.stem  # e.g. nist_Ethylene_Carbonate__EC__idx0
            # 화합물 키 추출
            inner = re.sub(r'^nist_', '', stem)
            inner = re.sub(r'_idx\d+$', '', inner)
            inner = re.sub(r'__.*?__', '', inner)  # 괄호 안 내용 제거

            # NIST_SUBCATEGORY 매핑
            matched_key = None
            for key in NIST_SUBCATEGORY:
                if key.lower() in inner.lower():
                    matched_key = key
                    break

            if matched_key is None:
                # 매핑 못 찾으면 원본 stem으로 이름 사용
                subcat = 'ste_lubricant' if cat_key == 'steel_coating' else 'bat_electrolyte'
                mat_name = inner.replace('_', ' ')
            else:
                subcat, mat_name = NIST_SUBCATEGORY[matched_key]

            df = pd.read_csv(f)
            df = trim(df)
            if len(df) < 10:
                continue

            # 인덱스 번호 (idx0/1/2 → 측정 조건 번호)
            idx_m = re.search(r'idx(\d+)', stem)
            idx   = idx_m.group(1) if idx_m else '0'
            safe_mat = re.sub(r'[^\w]', '_', mat_name)
            fname = f"nist_{safe_mat}_m{idx}.csv"
            save(df, DIRS[subcat], fname, mat_name, 'NIST WebBook')
            count += 1

    print(f"  [NIST] {count}개")


# ════════════════════════════════════════════════════════════════════
# 소스 2 : Open Specy RDS (openspecy_*.csv in rist_library)
# ════════════════════════════════════════════════════════════════════
OPENSPECY_SUBCAT = {
    # 배터리 바인더/폴리머
    'polyvinylidene': 'bat_binder', 'pvdf': 'bat_binder',
    'polyimide':      'bat_binder',
    'pyrrolidone':    'bat_binder',
    'polyvinylpyrrolidone': 'bat_binder',
    'styrene.butadiene': 'bat_binder',
    # 철강 코팅/수지
    'polyurethane':   'ste_resin', 'urethane': 'ste_resin',
    'epoxy': 'ste_resin', 'epoxide': 'ste_resin',
    'acrylic': 'ste_resin', 'silicone': 'ste_resin',
    'alkyd': 'ste_resin', 'phenoxy': 'ste_resin',
    # 엔지니어링 플라스틱
    'peek': 'eng_engineering', 'polyetheretherketone': 'eng_engineering',
    'pom': 'eng_engineering', 'polyoxymethylene': 'eng_engineering',
    'polycarbonate': 'eng_engineering',
    'polyamide': 'eng_engineering', 'copolyamide': 'eng_engineering',
    'nylon': 'eng_engineering', 'pmma': 'eng_engineering',
}

def process_openspecy():
    count = 0
    for cat_key in ('battery', 'steel_coating', 'engineering_plastic'):
        src_dir = BASE / 'rist_library' / cat_key
        for f in sorted(src_dir.glob('openspecy_*.csv')):
            stem = f.stem  # e.g. openspecy_polyurethane_500
            inner = re.sub(r'^openspecy_', '', stem)
            inner_low = inner.lower()

            # 서브카테고리 추론
            subcat = None
            for kw, sc in OPENSPECY_SUBCAT.items():
                if kw in inner_low:
                    subcat = sc
                    break
            if subcat is None:
                # 기본값: 원래 카테고리
                if cat_key == 'battery':       subcat = 'bat_binder'
                elif cat_key == 'steel_coating': subcat = 'ste_resin'
                else:                            subcat = 'eng_engineering'

            df = pd.read_csv(f)
            # Open Specy 컬럼: wavenumber, absorbance (이미 변환됨)
            if 'absorbance' not in df.columns and 'intensity' in df.columns:
                df = df.rename(columns={'intensity': 'absorbance'})
            df = trim(df)
            if len(df) < 10:
                continue

            mat_name = inner.replace('_', ' ').strip()
            # 끝의 숫자 제거해서 물질명 추출
            mat_name_clean = re.sub(r'\s+\d+$', '', mat_name).strip()
            safe_inner = re.sub(r'[^\w]', '_', inner)
            fname = f"openspecy_{safe_inner}.csv"
            safe_mat2 = re.sub(r'[^\w]', '_', mat_name_clean)
            save(df, DIRS[subcat], fname, mat_name_clean, 'Open Specy',
                 intensity_type='intensity_norm')
            count += 1

    print(f"  [Open Specy] {count}개")


# ════════════════════════════════════════════════════════════════════
# 소스 3 : figshare 열가소성 폴리머 (figshare_absorbance/)
# ════════════════════════════════════════════════════════════════════
FIGSHARE_SUBCAT = {
    # 범용 열가소성 (commodity)
    'PE': 'eng_commodity', 'PP': 'eng_commodity', 'PS': 'eng_commodity',
    'PVC': 'eng_commodity', 'PET': 'eng_commodity', 'PVAc': 'eng_commodity',
    'PVOH': 'eng_commodity', 'EVA': 'eng_commodity', 'EVOH': 'eng_commodity',
    # 엔지니어링 플라스틱
    'ABS': 'eng_engineering', 'SAN': 'eng_engineering',
    'PC': 'eng_engineering', 'PMMA': 'eng_engineering',
    'Nylon': 'eng_engineering', 'PAN': 'eng_engineering',
    'PBT': 'eng_engineering', 'PK': 'eng_engineering',
    'PU': 'ste_resin',
    # 바이오플라스틱
    'PLA': 'eng_bioplastic', 'PHB': 'eng_bioplastic',
    # 배터리 바인더
    'PVDF': 'bat_binder',
}
FIGSHARE_MATNAME = {
    'PE': 'Polyethylene', 'PP': 'Polypropylene', 'PS': 'Polystyrene',
    'PVC': 'Polyvinyl Chloride', 'PET': 'Polyethylene Terephthalate',
    'PVAc': 'Polyvinyl Acetate', 'PVOH': 'Polyvinyl Alcohol',
    'EVA': 'Ethylene Vinyl Acetate', 'EVOH': 'Ethylene Vinyl Alcohol',
    'ABS': 'ABS (Acrylonitrile Butadiene Styrene)',
    'SAN': 'Styrene Acrylonitrile',
    'PC': 'Polycarbonate', 'PMMA': 'Polymethyl Methacrylate',
    'Nylon': 'Nylon', 'PAN': 'Polyacrylonitrile',
    'PBT': 'Polybutylene Terephthalate', 'PK': 'Polyketone',
    'PU': 'Polyurethane', 'PLA': 'Polylactic Acid',
    'PHB': 'Polyhydroxybutyrate', 'PVDF': 'PVDF (Battery Binder)',
}

def process_figshare():
    count = 0
    src_dir = BASE / 'figshare_absorbance'
    for f in sorted(src_dir.glob('*.csv')):
        abbr = re.sub(r'[-_]\d+.*$', '', f.stem)
        if abbr not in FIGSHARE_SUBCAT:
            continue
        subcat   = FIGSHARE_SUBCAT[abbr]
        mat_name = FIGSHARE_MATNAME.get(abbr, abbr)

        df = pd.read_csv(f)
        df = trim(df)
        if len(df) < 10:
            continue

        fname = f"figshare_{f.name}"
        save(df, DIRS[subcat], fname, mat_name, 'figshare (thermoplastic dataset)')
        count += 1

    print(f"  [figshare] {count}개")


# ════════════════════════════════════════════════════════════════════
# 소스 4 : Open Specy O-Ring 고무 (openspecy/o_ring_spectra/)
# ════════════════════════════════════════════════════════════════════
ORING_MATNAME = {
    '1_2_polybutadiene':         'Polybutadiene (1,2-PB)',
    'acrylonitrile_butadiene':   'Nitrile Rubber (NBR)',
    'acrylonitrile_butadiene_styrene': 'ABS Rubber',
    'ethylene_propylene':        'EPDM Rubber',
    'fibre_polyvinylidene_fluoride': 'PVDF Fiber',
    'fibre_thermoplastic_elastomere': 'Thermoplastic Elastomer',
    'nitrile_rubber':            'Nitrile Rubber (NBR)',
    'polu_butadiene_acrylonitrile': 'Nitrile Rubber (NBR)',
    'polychloroprene':           'Polychloroprene (Neoprene)',
    'polyisoprene_chlorinated':  'Chlorinated Polyisoprene',
    'polytetrafluoroethylene':   'PTFE (Teflon)',
    'polyurethane':              'Polyurethane Rubber',
    'polyurethane_acrylic_resin':'Polyurethane Acrylic',
    'polyvinylidene_fluoride':   'PVDF',
    'sealing_ring_EPDM':         'EPDM O-Ring',
    'sealing_ring_Gardena':      'Gardena Sealing Ring',
    'silicone_PDMS':             'Silicone (PDMS)',
    'silicone_rubber':           'Silicone Rubber',
    'silicone_seal_reactor':     'Silicone Seal',
    'styrene_butadiene':         'Styrene-Butadiene Rubber (SBR)',
    'styrene_isoprene':          'Styrene-Isoprene Rubber',
    'Teflon_PTFE':               'PTFE (Teflon)',
    'windscreen_wiper_rubber':   'EPDM (Wiper Rubber)',
}

def process_oring():
    count = 0
    src_dir = BASE / 'openspecy' / 'o_ring_spectra'
    for f in sorted(src_dir.glob('*.csv')):
        stem = f.stem  # e.g. acrylonitrile_butadiene_277
        base_stem = re.sub(r'_\d+$', '', stem)  # 끝 ID 제거

        # 물질명 매핑
        mat_name = None
        for key, name in ORING_MATNAME.items():
            if key.lower() in base_stem.lower():
                mat_name = name
                break
        if mat_name is None:
            mat_name = base_stem.replace('_', ' ').title()

        df = pd.read_csv(f)
        # Open Specy 컬럼 처리
        if 'intensity' in df.columns:
            df = df.rename(columns={'intensity': 'absorbance'})
        df = trim(df)
        if len(df) < 10:
            continue

        fname = f"openspecy_oring_{f.name}"
        save(df, DIRS['elastomer'], fname, mat_name,
             'Open Specy (O-Ring collection)', intensity_type='intensity_norm')
        count += 1

    print(f"  [O-Ring] {count}개")


# ════════════════════════════════════════════════════════════════════
# 소스 5 : Zenodo (engineering_plastic의 zenodo_* 파일)
# ════════════════════════════════════════════════════════════════════
ZENODO_MATNAME = {
    'TPA':  ('Terephthalic Acid (TPA)', 'eng_commodity'),
    'PET':  ('Polyethylene Terephthalate (PET)', 'eng_commodity'),
    'PHBV': ('Polyhydroxybutyrate-valerate (PHBV)', 'eng_bioplastic'),
    'TAGs': ('Triglycerides (TAGs)', 'eng_bioplastic'),
}

def process_zenodo():
    count = 0
    src_dir = BASE / 'rist_library' / 'engineering_plastic'
    for f in sorted(src_dir.glob('zenodo_*.csv')):
        stem = f.stem  # e.g. zenodo_17195859_FT-IR_commercial_TPA
        mat_name = 'Unknown'
        subcat   = 'eng_commodity'
        for key, (name, sc) in ZENODO_MATNAME.items():
            if key in stem:
                mat_name = name
                subcat   = sc
                break

        df = pd.read_csv(f)
        df = trim(df)
        if len(df) < 10:
            continue

        fname = f"zenodo_{f.name[8:]}"  # zenodo_ 접두어 제거 후 재추가
        save(df, DIRS[subcat], fname, mat_name, 'Zenodo (FT-IR spectra)')
        count += 1

    print(f"  [Zenodo] {count}개")


# ════════════════════════════════════════════════════════════════════
# 소스 6 : HAp 세라믹 (FTIR & PL raw data/)
# ════════════════════════════════════════════════════════════════════
def process_hap():
    count = 0
    hap_base = BASE / 'FTIR & PL raw data' / 'FTIR'

    for meas_dir in sorted(hap_base.iterdir()):
        if not meas_dir.is_dir():
            continue
        meas_label = '1st' if 'First' in meas_dir.name else '2nd'

        for f in sorted(meas_dir.glob('*.csv')) + sorted(meas_dir.glob('*.CSV')):
            stem = f.stem  # e.g. HAp250, HAp250-15, HAp400-35

            # 소성 온도 & 첨가물 비율 파악
            m = re.match(r'HAp(\d+)(?:-(\d+))?', stem, re.I)
            if not m:
                continue
            temp   = m.group(1)
            ratio  = m.group(2) if m.group(2) else '0'
            mat_name = f"HAp_{temp}C" + (f"_{ratio}pct" if ratio != '0' else '')

            # 포맷 감지: 헤더 있음(absorbance) vs 없음(%T)
            try:
                raw = f.read_text(errors='ignore')
                lines = raw.strip().split('\n')

                # 헤더가 있는 포맷: "Wavelength [nm], Absorbance" (trailing comma 허용)
                if 'Wavelength' in lines[0] or 'wavelength' in lines[0] \
                   or 'Wavelength' in lines[1] or 'wavelength' in lines[1]:
                    df = pd.read_csv(f, skiprows=2, header=0,
                                     names=['wavenumber', 'absorbance', '_extra'],
                                     usecols=[0, 1])
                    df.columns = ['wavenumber', 'absorbance']
                    df = df.apply(pd.to_numeric, errors='coerce').dropna()

                # 헤더 없는 포맷: "wavenumber_nm  %T" (과학적 표기법)
                else:
                    df = pd.read_csv(io.StringIO(raw), header=None,
                                     names=['wavenumber', 'pct_T'],
                                     sep=r'[,\s]+', engine='python')
                    df = df.apply(pd.to_numeric, errors='coerce').dropna()
                    df['absorbance'] = transmittance_to_absorbance(df['pct_T'], pct=True)
                    df = df[['wavenumber', 'absorbance']]

                df = trim(df)
                if len(df) < 10:
                    continue

                fname = f"hap_{mat_name}_{meas_label}.csv"
                save(df, DIRS['ceramic'], fname,
                     f"Hydroxyapatite {temp}°C" + (f" +{ratio}%" if ratio != '0' else ''),
                     f'User measurement ({meas_label} meas.)')
                count += 1

            except Exception as e:
                print(f"  HAp 처리 오류 {f.name}: {e}")

    print(f"  [HAp] {count}개")


# ════════════════════════════════════════════════════════════════════
# 메인
# ════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    print("=" * 60)
    print("RIST FTIR 라이브러리 전체 정리")
    print(f"출력: {OUTPUT}")
    print("=" * 60)

    print("\n[1] NIST WebBook 소분자")
    process_nist()

    print("[2] Open Specy 고분자/수지")
    process_openspecy()

    print("[3] figshare 열가소성 폴리머 (59종)")
    process_figshare()

    print("[4] Open Specy O-Ring 고무 (37종)")
    process_oring()

    print("[5] Zenodo FT-IR 데이터")
    process_zenodo()

    print("[6] HAp 세라믹 (직접 측정)")
    process_hap()

    # ── 매니페스트 저장 ──────────────────────────────────────────
    df_m = pd.DataFrame(manifest_rows)
    df_m.to_csv(OUTPUT / 'manifest.csv', index=False)

    # ── 요약 ────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"총 수집: {len(df_m)}개 스펙트럼")
    print()

    # 카테고리별 집계
    df_m['category'] = df_m['file'].str.split('/').str[0]
    for cat in df_m['category'].unique():
        sub = df_m[df_m['category'] == cat]
        print(f"  {cat}: {len(sub)}개 | 소재 {sub['material'].nunique()}종")

    print(f"\n매니페스트: {OUTPUT / 'manifest.csv'}")
    print("=" * 60)

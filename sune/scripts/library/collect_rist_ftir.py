#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: Open Specy RDS / NIST WebBook / figshare 등 외부 소스에서 이차전지·
#            철강코팅·엔지니어링 플라스틱 FTIR 스펙트럼을 내려받아 data/rist_library/
#            아래에 수집한다. (네트워크 연결 필요)
# 실행 방법: python scripts/library/collect_rist_ftir.py
#            (인자 없음 — 저장 경로는 파일 상단 BASE/DIRS 상수에 내장)
# ─────────────────────────────────────────────────────────────────────────────
"""
RIST FTIR 라이브러리 종합 수집 스크립트
수집 대상: 이차전지, 철강/코팅, 엔지니어링 플라스틱 소재
소스: Open Specy RDS, NIST WebBook, figshare
"""

import os, re, time, json
import numpy as np
import pandas as pd
import requests
from pathlib import Path

BASE = Path('/Users/byeonghoonyoon/PROJECT/RIST/data')

DIRS = {
    'battery':              BASE / 'rist_library/battery',
    'steel_coating':        BASE / 'rist_library/steel_coating',
    'engineering_plastic':  BASE / 'rist_library/engineering_plastic',
}
for d in DIRS.values():
    d.mkdir(parents=True, exist_ok=True)

HEADERS = {'User-Agent': 'Mozilla/5.0 (Academic Research)'}

all_results = []

# ════════════════════════════════════════════════════════════════════
# Phase 1 : Open Specy RDS 추출
# ════════════════════════════════════════════════════════════════════
def extract_openspecy():
    import pyreadr
    print("  RDS 로딩 중…")
    meta = pyreadr.read_r(str(BASE / 'openspecy/ftir_metadata.rds'))[None].reset_index(drop=True)
    lib  = pyreadr.read_r(str(BASE / 'openspecy/ftir_library.rds'))[None]
    meta['lib_id'] = meta.index + 1
    meta['identity_str'] = meta['spectrum_identity'].astype(str)

    categories = {
        'battery': [
            'PVDF', 'polyvinylidene', 'CMC', 'carboxymethyl',
            'SBR', 'styrene.butadiene', 'NMP', 'pyrrolidone', 'polyimide',
        ],
        'steel_coating': [
            'epoxy', 'polyurethane', 'urethane', 'silicone',
            'acrylic', 'resin', 'alkyd',
        ],
        'engineering_plastic': [
            'PEEK', 'polyetheretherketone', 'POM', 'polyoxymethylene',
            'PMMA', 'polycarbonate', 'copolyamide', 'polyamide', 'nylon',
        ],
    }

    results = []
    for _, row in meta.iterrows():
        identity = row['identity_str'].lower()
        lib_id   = row['lib_id']

        cat = None
        for category, keywords in categories.items():
            if any(k.lower() in identity for k in keywords):
                cat = category
                break
        if cat is None:
            continue

        spec = lib[lib['sample_name'] == lib_id]
        if len(spec) == 0:
            continue

        safe  = re.sub(r'[^\w\s-]', '', row['identity_str']).strip().replace(' ', '_')[:50]
        fname = f"openspecy_{safe}_{lib_id}.csv"
        out   = DIRS[cat] / fname

        if not out.exists():
            df_out = spec[['wavenumber', 'intensity']].copy()
            df_out.columns = ['wavenumber', 'absorbance']
            df_out.to_csv(out, index=False)

        results.append({
            'source': 'openspecy', 'category': cat,
            'name': row['identity_str'], 'file': fname,
        })

    print(f"  → {len(results)}개 추출")
    return results


# ════════════════════════════════════════════════════════════════════
# JCAMP-DX 파서
# ════════════════════════════════════════════════════════════════════
def parse_jcamp(text):
    def _get(pat, txt, default=None):
        m = re.search(pat, txt, re.I)
        return m.group(1).strip() if m else default

    npoints = int(_get(r'##NPOINTS\s*=\s*(\d+)', text, '0'))
    if npoints == 0:
        return None

    xfactor = float(_get(r'##XFACTOR\s*=\s*([\d.eE+\-]+)', text, '1.0'))
    yfactor = float(_get(r'##YFACTOR\s*=\s*([\d.eE+\-]+)', text, '1.0'))
    firstx  = float(_get(r'##FIRSTX\s*=\s*([\d.eE+\-]+)', text, '0'))
    lastx   = float(_get(r'##LASTX\s*=\s*([\d.eE+\-]+)', text, '0'))
    deltax  = float(_get(r'##DELTAX\s*=\s*([\d.eE+\-]+)', text,
                         str((lastx - firstx) / max(npoints - 1, 1))))
    yunits  = (_get(r'##YUNITS\s*=\s*(.+)', text, '') or '').upper()

    # X++(Y..Y) 데이터 추출
    data_block = re.search(r'##XYDATA=\(X\+\+\(Y\.\.Y\)\)(.*?)(?:##END|##)',
                           text, re.DOTALL | re.I)
    if not data_block:
        return None

    y_values = []
    for line in data_block.group(1).splitlines():
        line = line.strip()
        if not line:
            continue   # 빈 줄 무시
        if line.startswith('##'):
            break      # 다음 레코드 시작 → 종료
        tokens = re.split(r'[\s,]+', line)
        # 첫 토큰은 X 값 (건너뜀), 나머지가 Y
        for tok in tokens[1:]:
            try:
                y_values.append(float(tok) * yfactor)
            except ValueError:
                pass

    if len(y_values) < 5:
        return None

    y_values = y_values[:npoints]
    wavenumbers = firstx + deltax * np.arange(len(y_values))
    wavenumbers *= xfactor

    # %T → 흡광도 변환
    arr = np.array(y_values)
    if 'TRANSMIT' in yunits or '%T' in yunits:
        arr = np.where(arr > 0, -np.log10(np.clip(arr, 1e-4, None)), 0.0)

    # 650–4000 cm⁻¹ 범위만 유지
    mask = (wavenumbers >= 650) & (wavenumbers <= 4000)
    return pd.DataFrame({'wavenumber': wavenumbers[mask], 'absorbance': arr[mask]})


# ════════════════════════════════════════════════════════════════════
# Phase 2 : NIST WebBook 소분자 (전해질, 용매)
# ════════════════════════════════════════════════════════════════════
NIST_COMPOUNDS = {
    # 이차전지 전해질 용매
    'Ethylene Carbonate (EC)':          ('C96491',  'battery'),
    'Dimethyl Carbonate (DMC)':         ('C616386', 'battery'),
    'Diethyl Carbonate (DEC)':          ('C105588', 'battery'),
    'Propylene Carbonate (PC)':         ('C108327', 'battery'),
    'NMP (N-Methyl-2-pyrrolidone)':     ('C872504', 'battery'),
    'Acetonitrile (AN)':                ('C75058',  'battery'),
    'gamma-Butyrolactone (GBL)':        ('C96480',  'battery'),   # 전해질 첨가제
    'Vinylene Carbonate (VC)':          ('C872365', 'battery'),   # 전해질 첨가제
    'Fluoroethylene Carbonate (FEC)':   ('C114435334', 'battery'),# SEI 형성제
    # 이차전지 음극 바인더 용매
    'N,N-Dimethylacetamide (DMAc)':     ('C127194', 'battery'),
    # 철강/코팅 관련 소분자
    'DMSO':                             ('C67685',  'steel_coating'),
    'Oleic acid (lubricant)':           ('C112801', 'steel_coating'),
    'Triethyl phosphate':               ('C78400',  'steel_coating'),
    'Tributyl phosphate':               ('C126738', 'steel_coating'),
    'Stearic acid (lubricant)':         ('C57114',  'steel_coating'),
    # 방청/부식억제제 (CAS → C+CAS없이 - 수정)
    'Benzimidazole (corrosion inhibitor)': ('C51172', 'steel_coating'),  # CAS 51-17-2
    'Benzotriazole (corrosion inhibitor)': ('C95147', 'steel_coating'),  # CAS 95-14-7
    '2-Mercaptobenzothiazole':          ('C149304', 'steel_coating'),  # CAS 149-30-4
    'Imidazole':                        ('C288321', 'steel_coating'),  # CAS 288-32-4
    # 세라믹/무기 첨가제 (소결/철강)
    'Titanium isopropoxide':            ('C546688', 'steel_coating'),
}

def fetch_nist():
    results = []
    for name, (nist_id, category) in NIST_COMPOUNDS.items():
        for idx in range(3):
            url = (f'https://webbook.nist.gov/cgi/cbook.cgi'
                   f'?JCAMP={nist_id}&Index={idx}&Type=IR')
            try:
                r = requests.get(url, headers=HEADERS, timeout=20)
                time.sleep(0.4)
            except Exception as e:
                print(f"  [NIST] {name} idx={idx} 오류: {e}")
                break

            if r.status_code != 200 or 'not found' in r.text.lower()[:60]:
                break

            df = parse_jcamp(r.text)
            if df is None or len(df) < 10:
                break

            safe  = re.sub(r'[^\w]', '_', name)[:40]
            fname = f"nist_{safe}_idx{idx}.csv"
            out   = DIRS[category] / fname
            if not out.exists():
                df.to_csv(out, index=False)

            results.append({
                'source': f'nist', 'category': category,
                'name': name, 'file': fname,
            })
            print(f"    {name} (idx={idx}): {len(df)} pts")

    print(f"  → {len(results)}개 다운로드")
    return results


# ════════════════════════════════════════════════════════════════════
# Phase 3 : figshare 검색 및 다운로드
# ════════════════════════════════════════════════════════════════════
FIGSHARE_QUERIES = [
    # (검색어, 카테고리)
    ('PVDF polyvinylidene fluoride FTIR infrared spectra binder',  'battery'),
    ('CMC carboxymethyl cellulose FTIR battery binder spectra',    'battery'),
    ('SBR styrene butadiene rubber FTIR infrared spectra',         'battery'),
    ('lithium battery separator PE PP FTIR infrared dataset',      'battery'),
    ('LiFePO4 cathode FTIR infrared spectra dataset CSV',          'battery'),
    ('epoxy resin FTIR infrared spectra dataset CSV',              'steel_coating'),
    ('polyurethane FTIR infrared absorbance dataset CSV',          'steel_coating'),
    ('silane APTES surface treatment FTIR infrared dataset',       'steel_coating'),
    ('anti corrosion coating FTIR infrared spectra CSV',           'steel_coating'),
    ('PEEK polyimide PI FTIR infrared spectra dataset',            'engineering_plastic'),
    ('polyamide nylon PA6 PA66 FTIR infrared spectra CSV dataset', 'engineering_plastic'),
    ('PMMA polymethyl methacrylate FTIR infrared dataset CSV',     'engineering_plastic'),
    ('POM polyoxymethylene acetal FTIR infrared spectra',          'engineering_plastic'),
    ('polycarbonate PC FTIR infrared spectra CSV dataset',         'engineering_plastic'),
]

def search_figshare():
    # 이미 있는 figshare 파일 집합 (중복 방지)
    existing = set()
    for d in DIRS.values():
        existing.update(f.name for f in d.glob('figshare_*.csv'))

    results = []
    seen_articles = set()

    for query, category in FIGSHARE_QUERIES:
        try:
            r = requests.post(
                'https://api.figshare.com/v2/articles/search',
                json={'search_for': query, 'item_type': 3, 'page_size': 8},
                timeout=30,
            )
            articles = r.json() if r.status_code == 200 else []
        except Exception as e:
            print(f"  [figshare] 검색 오류 ({query[:40]}): {e}")
            continue

        for article in articles[:4]:
            art_id = article['id']
            if art_id in seen_articles:
                continue
            seen_articles.add(art_id)

            try:
                fr = requests.get(
                    f'https://api.figshare.com/v2/articles/{art_id}/files',
                    timeout=20)
                files = fr.json() if fr.status_code == 200 else []
            except Exception:
                continue

            for f in files:
                fname_orig = f['name']
                if not fname_orig.lower().endswith('.csv'):
                    continue
                if any(kw in fname_orig.lower()
                       for kw in ['readme', 'index', 'manifest', 'metadata']):
                    continue

                fname = f"figshare_{art_id}_{fname_orig}"
                if fname in existing:
                    continue

                try:
                    dr = requests.get(f['download_url'], timeout=60)
                    time.sleep(0.3)
                except Exception:
                    continue

                if dr.status_code != 200 or len(dr.content) < 100:
                    continue

                # 간단한 CSV 유효성 검사 (2열 이상, 숫자 데이터)
                try:
                    import io
                    df_test = pd.read_csv(io.StringIO(dr.text), nrows=5)
                    if df_test.shape[1] < 2:
                        continue
                    # 모든 컬럼이 문자열이면 스킵
                    numeric_cols = df_test.select_dtypes(include='number').shape[1]
                    if numeric_cols == 0:
                        continue
                except Exception:
                    continue

                out = DIRS[category] / fname
                out.write_bytes(dr.content)
                existing.add(fname)
                results.append({
                    'source': f'figshare_{art_id}', 'category': category,
                    'name': fname_orig, 'file': fname,
                })
                print(f"    [{category}] figshare {art_id}: {fname_orig}")

    print(f"  → {len(results)}개 다운로드")
    return results


# ════════════════════════════════════════════════════════════════════
# Phase 4 : 기존 figshare_absorbance 중 엔지니어링 플라스틱 분류
# ════════════════════════════════════════════════════════════════════
# figshare 기존 파일 약어 → 카테고리/풀이름 매핑
FIGSHARE_MAP = {
    'ABS':   ('engineering_plastic', 'Acrylonitrile Butadiene Styrene'),
    'EVA':   ('engineering_plastic', 'Ethylene Vinyl Acetate'),
    'EVOH':  ('engineering_plastic', 'Ethylene Vinyl Alcohol (EVOH)'),
    'Nylon': ('engineering_plastic', 'Nylon (Polyamide)'),
    'PAN':   ('engineering_plastic', 'Polyacrylonitrile'),
    'PBT':   ('engineering_plastic', 'Polybutylene Terephthalate'),
    'PC':    ('engineering_plastic', 'Polycarbonate'),
    'PE':    ('engineering_plastic', 'Polyethylene'),
    'PET':   ('engineering_plastic', 'Polyethylene Terephthalate'),
    'PHB':   ('engineering_plastic', 'Polyhydroxybutyrate (Bioplastic)'),
    'PK':    ('engineering_plastic', 'Polyketone'),
    'PLA':   ('engineering_plastic', 'Polylactic Acid (Bioplastic)'),
    'PMMA':  ('engineering_plastic', 'Polymethyl Methacrylate (Acrylic)'),
    'PP':    ('engineering_plastic', 'Polypropylene'),
    'PS':    ('engineering_plastic', 'Polystyrene'),
    'PVC':   ('engineering_plastic', 'Polyvinyl Chloride'),
    'PVOH':  ('engineering_plastic', 'Polyvinyl Alcohol'),
    'SAN':   ('engineering_plastic', 'Styrene Acrylonitrile'),
    # 코팅/접착제
    'PU':    ('steel_coating',       'Polyurethane Coating'),
    'PVAc':  ('steel_coating',       'Polyvinyl Acetate (Adhesive/Coating)'),
    # 배터리
    'PVDF':  ('battery',             'Polyvinylidene Fluoride (Battery Binder)'),
}

def reclassify_existing_figshare():
    """기존 figshare_absorbance 폴더의 모든 파일을 카테고리별로 복사"""
    src_dir = BASE / 'figshare_absorbance'
    if not src_dir.exists():
        return []

    import shutil, re
    results = []
    for csv_file in src_dir.glob('*.csv'):
        # 파일명에서 소재 약어 추출 (예: 'PMMA-3' → 'PMMA')
        stem = csv_file.stem
        abbr = re.sub(r'[-_]\d+.*$', '', stem)  # 숫자 뒤 제거
        if abbr not in FIGSHARE_MAP:
            continue
        cat, mat_name = FIGSHARE_MAP[abbr]
        dest = DIRS[cat] / f"figshare_{csv_file.name}"
        if not dest.exists():
            shutil.copy2(csv_file, dest)
        results.append({
            'source': 'figshare_existing', 'category': cat,
            'name': mat_name, 'file': dest.name,
        })

    print(f"  → {len(results)}개 재분류")
    return results


# ════════════════════════════════════════════════════════════════════
# Phase 5 : Zenodo 공개 FTIR 데이터셋 직접 다운로드
# ════════════════════════════════════════════════════════════════════
ZENODO_RECORDS = [
    # (record_id, category, 설명)
    ('17195859', 'engineering_plastic', 'FT-IR spectra PET/PHBV/TPA'),
]

def download_zenodo():
    import io
    results = []
    for rec_id, category, desc in ZENODO_RECORDS:
        try:
            r = requests.get(f'https://zenodo.org/api/records/{rec_id}', timeout=20)
            if r.status_code != 200:
                continue
            data = r.json()
            files = data.get('files', [])
        except Exception as e:
            print(f"  [Zenodo] {rec_id} 오류: {e}")
            continue

        for f in files:
            key = f['key']
            if not key.lower().endswith('.csv'):
                continue
            if any(kw in key.lower() for kw in ['readme', 'readme', 'manifest']):
                continue

            try:
                dr = requests.get(f['links']['self'], timeout=30)
                time.sleep(0.3)
            except Exception:
                continue

            if dr.status_code != 200:
                continue

            # 세미콜론 구분자 처리 (Zenodo FTIR 파일 특성)
            try:
                text = dr.text
                sep = ';' if ';' in text.split('\n')[0] else ','
                df_raw = pd.read_csv(io.StringIO(text), sep=sep, header=0,
                                     names=['wavenumber', 'absorbance'],
                                     skiprows=1)
                # 숫자 변환
                df_raw = df_raw.apply(pd.to_numeric, errors='coerce').dropna()
                if len(df_raw) < 10 or df_raw['wavenumber'].max() < 500:
                    continue
                # 650-4000 cm⁻¹ 범위 필터
                df_raw = df_raw[(df_raw['wavenumber'] >= 650) & (df_raw['wavenumber'] <= 4000)]
            except Exception:
                continue

            safe_key = re.sub(r'[^\w.-]', '_', key)
            fname = f"zenodo_{rec_id}_{safe_key}"
            out = DIRS[category] / fname
            if not out.exists():
                df_raw.to_csv(out, index=False)

            results.append({
                'source': f'zenodo_{rec_id}', 'category': category,
                'name': key, 'file': fname,
            })
            print(f"    {key}: {len(df_raw)} pts → {fname}")

    print(f"  → {len(results)}개 다운로드")
    return results


# ════════════════════════════════════════════════════════════════════
# 메인
# ════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    print("=" * 60)
    print("RIST FTIR 라이브러리 종합 수집")
    print("=" * 60)

    print("\n[Phase 1] Open Specy RDS 추출")
    all_results.extend(extract_openspecy())

    print("\n[Phase 2] NIST WebBook 소분자 (전해질/용매/방청제)")
    all_results.extend(fetch_nist())

    print("\n[Phase 3] figshare 검색 및 다운로드")
    all_results.extend(search_figshare())

    print("\n[Phase 4] 기존 figshare 데이터 전체 재분류 (59개)")
    all_results.extend(reclassify_existing_figshare())

    print("\n[Phase 5] Zenodo 공개 FTIR 데이터셋")
    all_results.extend(download_zenodo())

    # 결과 매니페스트 저장
    df = pd.DataFrame(all_results)
    manifest_path = BASE / 'rist_library_manifest.csv'
    df.to_csv(manifest_path, index=False)

    print("\n" + "=" * 60)
    print(f"총 수집: {len(df)}개 스펙트럼")
    print(df['category'].value_counts().to_string())
    print(f"\n매니페스트: {manifest_path}")
    print("=" * 60)

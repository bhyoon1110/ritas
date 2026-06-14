# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: 랩 PC HTTP 파일 서버에서 O-ring OPUS(Entry*.0) 파일을 받아
#            brukeropusreader 로 흡광도 스펙트럼(CSV)을 추출한다. (네트워크 필요)
# 실행 방법: python scripts/library/download_opus_from_lab_pc.py --host <랩PC_IP> [--port 8080]
#            [--local-dir <이미_받은_폴더>]
#            예) python scripts/library/download_opus_from_lab_pc.py --host 192.168.0.10
#            (선행 작업: 랩 PC에서 lab_pc_file_server_py27.py 를 먼저 실행)
# ─────────────────────────────────────────────────────────────────────────────
"""
랩 PC 파일 서버에서 O-ring Entry*.0 파일을 다운로드하고
brukeropusreader로 흡광도 스펙트럼을 추출합니다.

사용 방법:
1. 랩 PC에서 lab_pc_file_server_py27.py 실행 (DATA_DIR = C:\My Documents\O-ring\Spectra)
2. 이 스크립트 실행: python download_opus_from_lab_pc.py --host 192.168.x.x
"""

import argparse
import os
import sys
import urllib.request
import json
import numpy as np
import pandas as pd
from pathlib import Path

try:
    from brukeropusreader import read_file as opus_read
except ImportError:
    print("brukeropusreader 설치 필요: pip install brukeropusreader")
    sys.exit(1)

DOWNLOAD_DIR = Path("data/opus_entry_files")
OUTPUT_DIR = Path("opus_complete_results")

def list_files(host, port=8080):
    url = f"http://{host}:{port}/list"
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read())

def download_file(host, filename, dest_path, port=8080):
    url = f"http://{host}:{port}/download/{urllib.parse.quote(filename)}"
    urllib.request.urlretrieve(url, dest_path)

def extract_spectrum(opus_path):
    """OPUS 파일에서 파수(x)와 흡광도(y) 추출"""
    data = opus_read(str(opus_path))
    
    # 흡광도(AB) 또는 단일빔(ScSm) 데이터 찾기
    for key in ['AB', 'IgSm', 'ScSm', 'Tr']:
        if key in data and len(data[key]) > 0:
            y = np.array(data[key])
            # X축(파수) 생성
            if f'{key} Data Parameter' in data:
                params = data[f'{key} Data Parameter']
                fxv = params.get('FXV', 4000)
                lxv = params.get('LXV', 400)
                npt = params.get('NPT', len(y))
                x = np.linspace(fxv, lxv, int(npt))
            else:
                x = np.arange(len(y))
            return x, y, key
    return None, None, None

def main():
    import urllib.parse

    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='localhost', help='랩 PC IP 주소')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--local-dir', default=None,
                        help='이미 다운로드된 파일 폴더 (서버 없이 로컬 처리)')
    args = parser.parse_args()

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1단계: 파일 목록 수집
    if args.local_dir:
        local_path = Path(args.local_dir)
        files = sorted(local_path.glob("Entry*.0"))
        print(f"로컬 폴더에서 {len(files)}개 파일 발견")
        opus_files = files
    else:
        print(f"랩 PC {args.host}:{args.port} 에서 파일 목록 조회 중...")
        try:
            file_list = list_files(args.host, args.port)
        except Exception as e:
            print(f"연결 실패: {e}")
            print("\n랩 PC에서 다음 명령으로 서버를 먼저 실행하세요:")
            print(r"  python lab_pc_file_server_py27.py")
            sys.exit(1)

        entry_files = [f for f in file_list if f['name'].startswith('Entry') and f['name'].endswith('.0')]
        entry_files.sort(key=lambda x: x['name'])
        print(f"Entry 파일 {len(entry_files)}개 발견")

        # 2단계: 다운로드
        opus_files = []
        for i, f in enumerate(entry_files):
            dest = DOWNLOAD_DIR / f['name']
            if not dest.exists():
                print(f"  다운로드 중 ({i+1}/{len(entry_files)}): {f['name']}")
                download_file(args.host, f['name'], dest, args.port)
            opus_files.append(dest)

    # 3단계: 스펙트럼 추출
    print(f"\n스펙트럼 추출 중 ({len(opus_files)}개)...")
    results = []
    failed = []

    for opus_path in opus_files:
        name = opus_path.stem  # Entry001 등
        try:
            x, y, data_type = extract_spectrum(opus_path)
            if x is None:
                failed.append((name, "데이터 블록 없음"))
                continue

            # 유효성 검사
            y_valid = y[np.isfinite(y)]
            if len(y_valid) == 0 or y_valid.min() < -0.1 or y_valid.max() > 5:
                failed.append((name, f"범위 이상: min={y_valid.min():.3f} max={y_valid.max():.3f}"))

            # CSV 저장
            df = pd.DataFrame({'wavenumber': x, 'absorbance': y})
            csv_path = OUTPUT_DIR / f"{name}.csv"
            df.to_csv(csv_path, index=False)

            results.append({
                'name': name,
                'data_type': data_type,
                'n_points': len(y),
                'min': float(y_valid.min()) if len(y_valid) > 0 else None,
                'max': float(y_valid.max()) if len(y_valid) > 0 else None,
            })
            print(f"  ✓ {name}: {len(y)}pts, {data_type}, min={y_valid.min():.3f} max={y_valid.max():.3f}")

        except Exception as e:
            failed.append((name, str(e)))
            print(f"  ✗ {name}: {e}")

    # 4단계: 요약
    print(f"\n=== 완료 ===")
    print(f"성공: {len(results)}개 / 전체: {len(opus_files)}개")
    if failed:
        print(f"실패: {len(failed)}개")
        for name, reason in failed[:10]:
            print(f"  - {name}: {reason}")

    summary_df = pd.DataFrame(results)
    summary_df.to_csv(OUTPUT_DIR / "extraction_summary.csv", index=False)
    print(f"요약 저장: {OUTPUT_DIR}/extraction_summary.csv")
    print(f"스펙트럼 CSV: {OUTPUT_DIR}/Entry*.csv")

if __name__ == '__main__':
    main()

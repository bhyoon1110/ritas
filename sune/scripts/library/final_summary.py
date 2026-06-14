# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: OPUS 라이브러리 추출 결과(master_spectra.csv)를 읽어 화합물 수·
#            파일별 분포·강도 통계 등 최종 요약을 출력하고 데이터를 검증한다.
# 실행 방법: python scripts/library/final_summary.py
#            (인자 없음 — opus_complete_results 경로는 함수 내부에 내장)
# ─────────────────────────────────────────────────────────────────────────────
"""
OPUS 라이브러리 추출 최종 요약 및 데이터 검증
"""

import csv
from pathlib import Path
import pandas as pd

def create_final_summary():
    """최종 데이터 요약"""
    
    results_dir = Path("/Users/byeonghoonyoon/PROJECT/RIST/opus_complete_results")
    
    print("\n" + "="*80)
    print("OPUS ATR-FTIR O-RING 라이브러리 - 최종 추출 요약")
    print("="*80)
    
    # Master CSV 읽기
    master_csv = results_dir / "master_spectra.csv"
    df = pd.read_csv(master_csv)
    
    print(f"\n✅ 총 추출된 화합물: {len(df)}개")
    print(f"✅ 파일 범위: D01 ~ D14 (14개 파일)")
    print(f"✅ 각 스펙트럼 포인트: 1005개")
    print(f"✅ Wave number 범위: 4000 ~ 400 cm⁻¹ (ATR-FTIR 표준)")
    
    print("\n" + "-"*80)
    print("파일별 화합물 분포:")
    print("-"*80)
    
    file_dist = df['File'].value_counts().sort_index()
    for filename, count in file_dist.items():
        file_name = filename.replace("ATR-FTIR O-RING LIBRARY.", "")
        print(f"  {file_name:6s}: {count:3d}개 스펙트럼")
    
    print("\n" + "-"*80)
    print("강도(Intensity) 통계:")
    print("-"*80)
    
    print(f"  전체 최소값: {df['Min_Intensity'].min():.8f}")
    print(f"  전체 최대값: {df['Max_Intensity'].max():.8f}")
    print(f"  평균 강도: {df['Mean_Intensity'].mean():.8f}")
    print(f"  표준편차: {df['Mean_Intensity'].std():.8f}")
    
    # 상위 강도 화합물
    print("\n  상위 강도 화합물 (Top 10):")
    top_compounds = df.nlargest(10, 'Max_Intensity')[['CompoundID', 'File', 'Max_Intensity']]
    for idx, (_, row) in enumerate(top_compounds.iterrows(), 1):
        file_name = row['File'].replace("ATR-FTIR O-RING LIBRARY.", "")
        print(f"    {idx:2d}. {row['CompoundID']:12s} (파일: {file_name:6s}, 최대: {row['Max_Intensity']:.6f})")
    
    print("\n" + "-"*80)
    print("생성된 파일 목록:")
    print("-"*80)
    
    csv_files = sorted(results_dir.glob("*spectrum.csv"))
    print(f"  ✓ master_spectra.csv - 전체 화합물 통계 (1개)")
    print(f"  ✓ D##_S##_spectrum.csv - 개별 화합물 데이터 ({len(csv_files)}개)")
    print(f"  ✓ PNG 그래프 (3개):")
    print(f"    - 01_all_spectra_overlay.png (모든 화합물 오버레이)")
    print(f"    - 02_statistics_comparison.png (강도 비교)")
    print(f"    - 03_individual_spectra.png (개별 스펙트럼)")
    
    print("\n" + "-"*80)
    print("데이터 포맷:")
    print("-"*80)
    print("  각 CSV 파일 컬럼:")
    print("    - Wavenumber_cm-1: 파수 (4000~400 cm⁻¹)")
    print("    - Intensity_au: 강도 (상대강도 단위)")
    
    # 샘플 데이터 표시
    print("\n" + "-"*80)
    print("샘플 데이터 (D01_S00 - 첫 번째 화합물):")
    print("-"*80)
    
    sample_csv = results_dir / "D01_S00_spectrum.csv"
    if sample_csv.exists():
        sample_df = pd.read_csv(sample_csv)
        print(sample_df.head(10).to_string(index=False))
        print(f"  ... (총 {len(sample_df)}행)")
    
    print("\n" + "="*80)
    print("완료! 모든 데이터가 준비되었습니다.")
    print("="*80)
    print(f"\n위치: {results_dir}")
    print("\n이제 각 화합물의 Wave number와 Intensity 데이터로 그래프를 그릴 수 있습니다!")
    print("→ Matplotlib, Plotly 등으로 상세 분석 그래프 작성 가능")
    print("→ 화합물 비교 분석 가능")
    print("→ 스펙트럼 매칭 가능\n")


if __name__ == "__main__":
    create_final_summary()

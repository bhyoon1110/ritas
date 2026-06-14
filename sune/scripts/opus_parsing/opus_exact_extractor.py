# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: OPUS 파일에서 오프셋 0x32부터 시작하는 정확한 스펙트럼 데이터를
#            추출하고 그래프(PNG)·CSV로 저장한다. (디버그용 matplotlib)
# 실행 방법: python scripts/opus_parsing/opus_exact_extractor.py
#            (인자 없음 — filepath/output_dir 경로는 하단 __main__ 블록에 내장)
# ─────────────────────────────────────────────────────────────────────────────
"""
OPUS 라이브러리 - 정확한 스펙트럼 추출
오프셋 0x32부터 시작하는 데이터 추출
"""

import struct
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
import csv

def extract_opus_spectra(filepath):
    """OPUS 파일에서 정확하게 스펙트럼 추출"""
    
    with open(filepath, 'rb') as f:
        data = f.read()
    
    print(f"파일: {Path(filepath).name}")
    print(f"크기: {len(data)} bytes\n")
    
    # 오프셋 0x32부터 float 데이터 추출
    offset = 0x32
    spectra = []
    
    # 데이터가 약 1005개 값씩 반복된다는 것을 알고 있음
    # 각 블록: 1005개 intensity + 18개 메타데이터
    spectrum_size = 1005
    
    print("=== 스펙트럼 추출 ===")
    print(f"시작 오프셋: 0x{offset:04x}")
    print(f"예상 스펙트럼 크기: {spectrum_size}개 값\n")
    
    i = offset
    spectrum_count = 0
    
    while i + spectrum_size * 4 < len(data):
        spectrum_values = []
        valid_count = 0
        
        # 1005개의 float 값 읽기
        for j in range(spectrum_size):
            pos = i + j * 4
            if pos + 4 > len(data):
                break
            
            try:
                value = struct.unpack('<f', data[pos:pos+4])[0]
                spectrum_values.append(value)
                
                # 합리적인 범위 확인 (0-1 범위)
                if 0 <= value <= 1:
                    valid_count += 1
            except:
                spectrum_values.append(0.0)
        
        if len(spectrum_values) == spectrum_size:
            valid_ratio = valid_count / spectrum_size
            
            spectra.append({
                'position': i,
                'values': spectrum_values,
                'valid_ratio': valid_ratio,
                'min': min(spectrum_values),
                'max': max(spectrum_values),
                'mean': np.mean(spectrum_values)
            })
            
            spectrum_count += 1
            print(f"스펙트럼 #{spectrum_count}")
            print(f"  오프셋: 0x{i:06x}")
            print(f"  유효값 비율: {valid_ratio*100:.1f}%")
            print(f"  범위: {min(spectrum_values):.6f} ~ {max(spectrum_values):.6f}")
            print(f"  평균: {np.mean(spectrum_values):.6f}\n")
            
            # 다음 블록으로 이동 (1005 float + 18 float 메타데이터)
            i += (spectrum_size + 18) * 4
        else:
            break
    
    return spectra


def generate_wavenumber(num_points=1005):
    """Wave number 범위 생성 (ATR-FTIR: 4000-400 cm-1)"""
    start_wn = 4000
    end_wn = 400
    wavenumber = np.linspace(start_wn, end_wn, num_points)
    return wavenumber


def plot_spectra(spectra, output_dir=None):
    """스펙트럼 시각화"""
    if not spectra:
        print("추출된 스펙트럼이 없습니다.")
        return
    
    if output_dir is None:
        output_dir = "/Users/byeonghoonyoon/PROJECT/RIST"
    
    wavenumber = generate_wavenumber(len(spectra[0]['values']))
    
    # 1. 모든 스펙트럼을 하나의 그래프에
    plt.figure(figsize=(14, 8))
    
    colors = plt.cm.tab20(np.linspace(0, 1, len(spectra)))
    
    for idx, spectrum in enumerate(spectra):
        plt.plot(wavenumber, spectrum['values'], 
                linewidth=1.5, alpha=0.7, 
                color=colors[idx], label=f"Compound #{idx+1}")
    
    plt.xlabel('Wavenumber (cm⁻¹)', fontsize=12)
    plt.ylabel('Intensity (a.u.)', fontsize=12)
    plt.title(f'OPUS Library - {len(spectra)} Compounds', fontsize=14, fontweight='bold')
    plt.gca().invert_xaxis()  # Wave number는 역순
    plt.grid(True, alpha=0.3)
    plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left', fontsize=9, ncol=2)
    plt.tight_layout()
    
    filepath = f"{output_dir}/opus_spectra_overlay.png"
    plt.savefig(filepath, dpi=150, bbox_inches='tight')
    print(f"✓ 오버레이 그래프: {filepath}")
    plt.close()
    
    # 2. 각 스펙트럼 개별 표시 (처음 6개)
    num_to_plot = min(6, len(spectra))
    fig, axes = plt.subplots(num_to_plot, 1, figsize=(12, 3*num_to_plot))
    if num_to_plot == 1:
        axes = [axes]
    
    for idx in range(num_to_plot):
        spectrum = spectra[idx]
        ax = axes[idx]
        
        ax.plot(wavenumber, spectrum['values'], linewidth=1.5, color='darkblue')
        ax.fill_between(wavenumber, spectrum['values'], alpha=0.3)
        ax.set_ylabel('Intensity', fontsize=10)
        ax.set_title(f'Compound #{idx+1} (Valid: {spectrum["valid_ratio"]*100:.1f}%)', fontsize=11)
        ax.grid(True, alpha=0.3)
        ax.invert_xaxis()
    
    axes[-1].set_xlabel('Wavenumber (cm⁻¹)', fontsize=12)
    plt.tight_layout()
    
    filepath = f"{output_dir}/opus_spectra_individual.png"
    plt.savefig(filepath, dpi=150)
    print(f"✓ 개별 그래프: {filepath}")
    plt.close()
    
    # 3. 통계 정보
    plt.figure(figsize=(12, 5))
    
    # 평균값 비교
    plt.subplot(1, 2, 1)
    mean_values = [s['mean'] for s in spectra]
    plt.bar(range(len(spectra)), mean_values, color=colors)
    plt.xlabel('Compound #')
    plt.ylabel('Average Intensity')
    plt.title('Average Intensity per Compound')
    plt.grid(True, alpha=0.3, axis='y')
    
    # 최대값 비교
    plt.subplot(1, 2, 2)
    max_values = [s['max'] for s in spectra]
    plt.bar(range(len(spectra)), max_values, color=colors)
    plt.xlabel('Compound #')
    plt.ylabel('Maximum Intensity')
    plt.title('Maximum Intensity per Compound')
    plt.grid(True, alpha=0.3, axis='y')
    
    plt.tight_layout()
    filepath = f"{output_dir}/opus_statistics.png"
    plt.savefig(filepath, dpi=150)
    print(f"✓ 통계 그래프: {filepath}")
    plt.close()


def save_csv(spectra, output_dir=None):
    """CSV 파일로 저장"""
    if not spectra:
        return
    
    if output_dir is None:
        output_dir = "/Users/byeonghoonyoon/PROJECT/RIST"
    
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    wavenumber = generate_wavenumber(len(spectra[0]['values']))
    
    for idx, spectrum in enumerate(spectra):
        filename = f"spectrum_{idx:02d}.csv"
        filepath = Path(output_dir) / filename
        
        with open(filepath, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Wavenumber_cm-1', 'Intensity_au'])
            
            for wn, intensity in zip(wavenumber, spectrum['values']):
                writer.writerow([f'{wn:.4f}', f'{intensity:.8f}'])
    
    print(f"✓ CSV 파일 저장: {output_dir}/spectrum_*.csv ({len(spectra)}개)")


if __name__ == "__main__":
    filepath = "/Users/byeonghoonyoon/PROJECT/RIST/data/Library/ATR-FTIR LIBRARY O-RING - 1816158 - SL/Library/ATR-FTIR O-RING LIBRARY.D01"
    
    print("="*70)
    print("OPUS 라이브러리 - 정확한 스펙트럼 추출")
    print("="*70 + "\n")
    
    # 스펙트럼 추출
    spectra = extract_opus_spectra(filepath)
    
    if spectra:
        print("="*70)
        print(f"✓ {len(spectra)}개 스펙트럼 추출 성공!")
        print("="*70 + "\n")
        
        # 시각화
        print("그래프 생성 중...")
        output_dir = "/Users/byeonghoonyoon/PROJECT/RIST/opus_results"
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        
        plot_spectra(spectra, output_dir)
        
        # CSV 저장
        print("\nCSV 저장 중...")
        save_csv(spectra, output_dir)
        
        print("\n" + "="*70)
        print("완료!")
        print("="*70)
        print(f"결과 위치: {output_dir}")
    else:
        print("❌ 스펙트럼 추출 실패")

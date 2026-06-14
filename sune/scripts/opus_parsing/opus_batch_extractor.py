# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: OPUS 라이브러리의 모든 D 파일에서 스펙트럼을 일괄 추출해 CSV로
#            저장하고, S01 파일 분석으로 화합물 정보를 매핑한다. (그래프 PNG 포함)
# 실행 방법: python scripts/opus_parsing/opus_batch_extractor.py
#            (인자 없음 — library_dir/output_dir 경로는 main() 내부에 내장)
# ─────────────────────────────────────────────────────────────────────────────
"""
OPUS 라이브러리 - 전체 스펙트럼 데이터 추출기
모든 D파일에서 스펙트럼 추출 및 CSV 저장
S01 파일 분석으로 화합물 정보 추출
"""

import struct
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
import csv
from collections import defaultdict

class OpusLibraryBatchExtractor:
    """전체 OPUS 라이브러리 처리"""
    
    def __init__(self, library_path):
        self.library_path = Path(library_path)
        self.d_files = sorted(self.library_path.glob('*.D*'))
        self.spectra_data = defaultdict(list)
    
    def extract_all_spectra(self):
        """모든 D 파일에서 스펙트럼 추출"""
        print(f"발견된 D 파일: {len(self.d_files)}개\n")
        
        for file_index, d_file in enumerate(self.d_files, 1):
            filename = d_file.name
            spectra = self.extract_from_file(d_file)
            
            for spectrum_index, spectrum in enumerate(spectra):
                compound_id = f"D{file_index:02d}_S{spectrum_index:02d}"
                
                self.spectra_data[compound_id] = {
                    'file': filename,
                    'file_index': file_index,
                    'spectrum_index': spectrum_index,
                    'values': spectrum['values'],
                    'stats': {
                        'min': min(spectrum['values']),
                        'max': max(spectrum['values']),
                        'mean': np.mean(spectrum['values']),
                        'count': len(spectrum['values'])
                    }
                }
            
            print(f"[{file_index:2d}/{len(self.d_files)}] {filename:40s} → {len(spectra):2d} 스펙트럼")
        
        return self.spectra_data
    
    def extract_from_file(self, filepath):
        """단일 파일에서 스펙트럼 추출"""
        with open(filepath, 'rb') as f:
            data = f.read()
        
        spectra = []
        offset = 0x32
        spectrum_size = 1005
        
        while offset + spectrum_size * 4 < len(data):
            spectrum_values = []
            
            for j in range(spectrum_size):
                pos = offset + j * 4
                if pos + 4 > len(data):
                    break
                
                try:
                    value = struct.unpack('<f', data[pos:pos+4])[0]
                    spectrum_values.append(value)
                except:
                    spectrum_values.append(0.0)
            
            if len(spectrum_values) == spectrum_size:
                spectra.append({
                    'position': offset,
                    'values': spectrum_values
                })
                offset += (spectrum_size + 18) * 4
            else:
                break
        
        return spectra
    
    def generate_wavenumber(self, num_points=1005):
        """Wave number 범위 생성"""
        start_wn = 4000
        end_wn = 400
        return np.linspace(start_wn, end_wn, num_points)
    
    def save_all_csv(self, output_dir):
        """모든 스펙트럼을 CSV로 저장"""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        wavenumber = self.generate_wavenumber()
        
        # 마스터 CSV 생성
        master_csv = output_dir / "master_spectra.csv"
        with open(master_csv, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['CompoundID', 'File', 'FileIndex', 'SpectrumIndex', 
                           'Min_Intensity', 'Max_Intensity', 'Mean_Intensity', 'Point_Count'])
            
            for compound_id in sorted(self.spectra_data.keys()):
                data = self.spectra_data[compound_id]
                stats = data['stats']
                writer.writerow([
                    compound_id,
                    data['file'],
                    data['file_index'],
                    data['spectrum_index'],
                    f"{stats['min']:.8f}",
                    f"{stats['max']:.8f}",
                    f"{stats['mean']:.8f}",
                    stats['count']
                ])
        
        print(f"\n✓ 마스터 CSV: {master_csv}")
        
        # 개별 스펙트럼 CSV
        spectrum_count = 0
        for compound_id in sorted(self.spectra_data.keys()):
            spectrum_values = self.spectra_data[compound_id]['values']
            
            filename = f"{compound_id}_spectrum.csv"
            filepath = output_dir / filename
            
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['Wavenumber_cm-1', 'Intensity_au'])
                
                for wn, intensity in zip(wavenumber, spectrum_values):
                    writer.writerow([f'{wn:.4f}', f'{intensity:.8f}'])
            
            spectrum_count += 1
        
        print(f"✓ 개별 스펙트럼 CSV: {spectrum_count}개 ({output_dir})")
        
        return master_csv
    
    def create_summary_plots(self, output_dir):
        """요약 그래프 생성"""
        output_dir = Path(output_dir)
        
        # 1. 모든 스펙트럼 오버레이
        wavenumber = self.generate_wavenumber()
        
        fig, ax = plt.subplots(figsize=(14, 8))
        
        compounds = sorted(self.spectra_data.keys())
        colors = plt.cm.tab20c(np.linspace(0, 1, len(compounds)))
        
        for idx, compound_id in enumerate(compounds):
            spectrum_values = self.spectra_data[compound_id]['values']
            ax.plot(wavenumber, spectrum_values, linewidth=1, alpha=0.7, 
                   color=colors[idx], label=compound_id)
        
        ax.set_xlabel('Wavenumber (cm⁻¹)', fontsize=12)
        ax.set_ylabel('Intensity (a.u.)', fontsize=12)
        ax.set_title(f'OPUS O-RING Library - All Spectra ({len(compounds)} compounds)', 
                    fontsize=14, fontweight='bold')
        ax.invert_xaxis()
        ax.grid(True, alpha=0.3)
        ax.legend(bbox_to_anchor=(1.05, 1), loc='upper left', fontsize=8, ncol=2)
        
        filepath = output_dir / "01_all_spectra_overlay.png"
        plt.savefig(filepath, dpi=150, bbox_inches='tight')
        plt.close()
        print(f"✓ 오버레이 그래프: {filepath}")
        
        # 2. 통계 비교
        fig, axes = plt.subplots(1, 3, figsize=(15, 5))
        
        compound_labels = list(compounds)
        min_values = [self.spectra_data[c]['stats']['min'] for c in compound_labels]
        max_values = [self.spectra_data[c]['stats']['max'] for c in compound_labels]
        mean_values = [self.spectra_data[c]['stats']['mean'] for c in compound_labels]
        
        axes[0].bar(range(len(compounds)), min_values, color=colors)
        axes[0].set_xlabel('Compound ID')
        axes[0].set_ylabel('Minimum Intensity')
        axes[0].set_title('Min Intensity per Compound')
        axes[0].grid(True, alpha=0.3, axis='y')
        axes[0].set_xticks(range(len(compounds)))
        axes[0].set_xticklabels(compound_labels, rotation=45, fontsize=8)
        
        axes[1].bar(range(len(compounds)), max_values, color=colors)
        axes[1].set_xlabel('Compound ID')
        axes[1].set_ylabel('Maximum Intensity')
        axes[1].set_title('Max Intensity per Compound')
        axes[1].grid(True, alpha=0.3, axis='y')
        axes[1].set_xticks(range(len(compounds)))
        axes[1].set_xticklabels(compound_labels, rotation=45, fontsize=8)
        
        axes[2].bar(range(len(compounds)), mean_values, color=colors)
        axes[2].set_xlabel('Compound ID')
        axes[2].set_ylabel('Average Intensity')
        axes[2].set_title('Average Intensity per Compound')
        axes[2].grid(True, alpha=0.3, axis='y')
        axes[2].set_xticks(range(len(compounds)))
        axes[2].set_xticklabels(compound_labels, rotation=45, fontsize=8)
        
        plt.tight_layout()
        filepath = output_dir / "02_statistics_comparison.png"
        plt.savefig(filepath, dpi=150, bbox_inches='tight')
        plt.close()
        print(f"✓ 통계 그래프: {filepath}")
        
        # 3. 개별 스펙트럼 (처음 12개)
        num_to_show = min(12, len(compounds))
        fig, axes = plt.subplots(4, 3, figsize=(15, 12))
        axes = axes.flatten()
        
        for idx, compound_id in enumerate(compounds[:num_to_show]):
            spectrum_values = self.spectra_data[compound_id]['values']
            stats = self.spectra_data[compound_id]['stats']
            
            ax = axes[idx]
            ax.plot(wavenumber, spectrum_values, linewidth=1.5, color='darkblue')
            ax.fill_between(wavenumber, spectrum_values, alpha=0.3)
            ax.set_title(f'{compound_id}\n(Mean: {stats["mean"]:.6f})', fontsize=10)
            ax.set_ylabel('Intensity', fontsize=9)
            ax.grid(True, alpha=0.3)
            ax.invert_xaxis()
        
        # 불필요한 서브플롯 숨기기
        for idx in range(num_to_show, len(axes)):
            axes[idx].set_visible(False)
        
        axes[-3].set_xlabel('Wavenumber (cm⁻¹)', fontsize=10)
        
        plt.tight_layout()
        filepath = output_dir / "03_individual_spectra.png"
        plt.savefig(filepath, dpi=150, bbox_inches='tight')
        plt.close()
        print(f"✓ 개별 스펙트럼: {filepath}")


def main():
    library_dir = "/Users/byeonghoonyoon/PROJECT/RIST/data/Library/ATR-FTIR LIBRARY O-RING - 1816158 - SL/Library"
    output_dir = "/Users/byeonghoonyoon/PROJECT/RIST/opus_complete_results"
    
    print("="*70)
    print("OPUS 라이브러리 - 전체 스펙트럼 데이터 추출")
    print("="*70 + "\n")
    
    # 모든 스펙트럼 추출
    print("1. 스펙트럼 추출 중...\n")
    extractor = OpusLibraryBatchExtractor(library_dir)
    spectra = extractor.extract_all_spectra()
    
    print(f"\n총 {len(spectra)}개 스펙트럼 추출 완료!\n")
    
    # CSV 저장
    print("="*70)
    print("2. CSV 파일 저장 중...\n")
    extractor.save_all_csv(output_dir)
    
    # 그래프 생성
    print("\n" + "="*70)
    print("3. 그래프 생성 중...\n")
    extractor.create_summary_plots(output_dir)
    
    print("\n" + "="*70)
    print("완료!")
    print("="*70)
    print(f"\n결과 위치: {output_dir}")
    print(f"총 스펙트럼: {len(spectra)}개")


if __name__ == "__main__":
    main()

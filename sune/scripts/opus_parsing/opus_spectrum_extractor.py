# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: OPUS 라이브러리에서 파수(Wave number)·흡광도(Intensity) 데이터를
#            추출하고 시각화(PNG)·CSV 저장한다. (OpusLibraryParser 클래스)
# 실행 방법: python scripts/opus_parsing/opus_spectrum_extractor.py
#            (인자 없음 — library_dir/output 경로는 main() 내부에 내장)
# ─────────────────────────────────────────────────────────────────────────────
"""
OPUS 라이브러리 스펙트럼 데이터 추출기
Wave number와 Intensity 데이터 추출 및 시각화
"""

import struct
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
import csv

class OpusLibraryParser:
    """OPUS 라이브러리 파일 파서"""
    
    def __init__(self, filepath):
        self.filepath = filepath
        self.data = None
        self.spectra = []
        self.load_file()
    
    def load_file(self):
        """파일 로드"""
        with open(self.filepath, 'rb') as f:
            self.data = f.read()
        print(f"파일 로드됨: {Path(self.filepath).name} ({len(self.data)} bytes)")
    
    def extract_float_blocks(self):
        """Float32 데이터 블록 추출 - 4바이트 정렬된 데이터 추출"""
        blocks = []
        
        # 0x32 오프셋부터 시작하는 알려진 데이터 블록
        # 이전 분석에서 패턴: 1005개 값 + 18개 값 반복
        
        # 먼저 모든 float 값 추출
        all_floats = []
        for i in range(0, len(self.data) - 3, 4):
            try:
                value = struct.unpack('<f', self.data[i:i+4])[0]
                # NaN/Inf 제외
                if not (np.isnan(value) or np.isinf(value)):
                    all_floats.append((i, value))
            except:
                pass
        
        print(f"총 float 값: {len(all_floats)}개")
        
        # 연속된 블록 찾기 (4바이트 정렬)
        if len(all_floats) > 0:
            current_block = [all_floats[0][1]]
            current_pos = all_floats[0][0]
            
            for j in range(1, len(all_floats)):
                pos, val = all_floats[j]
                prev_pos, prev_val = all_floats[j-1]
                
                # 4바이트 간격 확인
                if pos == prev_pos + 4:
                    current_block.append(val)
                else:
                    if len(current_block) > 50:
                        blocks.append({
                            'position': current_pos,
                            'values': current_block.copy(),
                            'count': len(current_block)
                        })
                    current_block = [val]
                    current_pos = pos
            
            # 마지막 블록
            if len(current_block) > 50:
                blocks.append({
                    'position': current_pos,
                    'values': current_block.copy(),
                    'count': len(current_block)
                })
        
        return blocks
    
    def extract_spectra(self):
        """스펙트럼 데이터 추출"""
        blocks = self.extract_float_blocks()
        
        print(f"\n발견한 데이터 블록: {len(blocks)}개")
        
        # 크기별로 그룹화 (대부분의 스펙트럼은 같은 포인트 수를 가짐)
        block_by_size = {}
        for block in blocks:
            size = block['count']
            if size not in block_by_size:
                block_by_size[size] = []
            block_by_size[size].append(block)
        
        print("\n블록 크기별 분포:")
        for size in sorted(block_by_size.keys(), reverse=True)[:10]:
            count = len(block_by_size[size])
            print(f"  {size:4d} 포인트: {count:3d}개 블록")
            if count > 0:
                sample = block_by_size[size][0]['values']
                print(f"       최소: {min(sample):.6f}, 최대: {max(sample):.6f}, 평균: {np.mean(sample):.6f}")
        
        # 가장 많은 블록 크기를 스펙트럼으로 선택
        main_size = max(block_by_size.keys(), key=lambda x: len(block_by_size[x]))
        print(f"\n선택된 스펙트럼 크기: {main_size} 포인트")
        
        self.spectra = block_by_size[main_size]
        return self.spectra
    
    def estimate_wavenumber(self, num_points=None):
        """Wave number 생성 (FTIR은 보통 4000-400 cm-1 범위)"""
        if num_points is None:
            if self.spectra:
                num_points = self.spectra[0]['count']
            else:
                return None
        
        # 일반적인 ATR-FTIR 범위: 4000 ~ 600 cm-1
        start_wn = 4000
        end_wn = 400
        wavenumber = np.linspace(start_wn, end_wn, num_points)
        return wavenumber
    
    def save_spectra_csv(self, output_dir=None):
        """스펙트럼을 CSV로 저장"""
        if not self.spectra:
            print("추출된 스펙트럼이 없습니다.")
            return
        
        if output_dir is None:
            output_dir = "/Users/byeonghoonyoon/PROJECT/RIST/spectra_data"
        
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        
        wavenumber = self.estimate_wavenumber()
        
        for i, spectrum in enumerate(self.spectra[:20]):  # 처음 20개만 저장
            filename = f"spectrum_{i:02d}.csv"
            filepath = Path(output_dir) / filename
            
            intensity = spectrum['values']
            
            with open(filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['Wavenumber (cm-1)', 'Intensity (a.u.)'])
                for wn, intens in zip(wavenumber, intensity):
                    writer.writerow([f'{wn:.2f}', f'{intens:.6f}'])
            
            print(f"저장됨: {filepath}")
        
        return output_dir
    
    def plot_spectra(self, num_to_plot=5, output_path=None):
        """스펙트럼 그리기"""
        if not self.spectra:
            print("추출된 스펙트럼이 없습니다.")
            return
        
        wavenumber = self.estimate_wavenumber()
        num_to_plot = min(num_to_plot, len(self.spectra))
        
        fig, axes = plt.subplots(num_to_plot, 1, figsize=(12, 3*num_to_plot))
        if num_to_plot == 1:
            axes = [axes]
        
        for idx in range(num_to_plot):
            spectrum = self.spectra[idx]
            intensity = spectrum['values']
            
            ax = axes[idx]
            ax.plot(wavenumber, intensity, linewidth=1.5, color='blue')
            ax.set_xlabel('Wavenumber (cm⁻¹)')
            ax.set_ylabel('Intensity (a.u.)')
            ax.set_title(f'Spectrum #{idx+1}')
            ax.grid(True, alpha=0.3)
            ax.invert_xaxis()  # Wave number는 역순으로 표시
        
        plt.tight_layout()
        
        if output_path is None:
            output_path = "/Users/byeonghoonyoon/PROJECT/RIST/opus_spectra.png"
        
        plt.savefig(output_path, dpi=150)
        print(f"\n그래프 저장됨: {output_path}")
        plt.close()
    
    def plot_all_spectra_overlay(self, output_path=None):
        """모든 스펙트럼을 하나의 그래프에 표시"""
        if not self.spectra:
            print("추출된 스펙트럼이 없습니다.")
            return
        
        wavenumber = self.estimate_wavenumber()
        
        plt.figure(figsize=(14, 8))
        
        # 색상 맵 생성
        colors = plt.cm.viridis(np.linspace(0, 1, len(self.spectra)))
        
        for idx, spectrum in enumerate(self.spectra):
            intensity = spectrum['values']
            plt.plot(wavenumber, intensity, linewidth=0.8, alpha=0.7, 
                    color=colors[idx], label=f'Compound #{idx+1}')
        
        plt.xlabel('Wavenumber (cm⁻¹)', fontsize=12)
        plt.ylabel('Intensity (a.u.)', fontsize=12)
        plt.title('OPUS Library - All Spectra Overlay', fontsize=14)
        plt.grid(True, alpha=0.3)
        plt.gca().invert_xaxis()
        plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left', fontsize=8, ncol=2)
        plt.tight_layout()
        
        if output_path is None:
            output_path = "/Users/byeonghoonyoon/PROJECT/RIST/opus_all_spectra.png"
        
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"오버레이 그래프 저장됨: {output_path}")
        plt.close()


def main():
    library_dir = "/Users/byeonghoonyoon/PROJECT/RIST/data/Library/ATR-FTIR LIBRARY O-RING - 1816158 - SL/Library"
    
    # D01 파일 (스펙트럼 데이터)
    d01_file = f"{library_dir}/ATR-FTIR O-RING LIBRARY.D01"
    
    print("="*70)
    print("OPUS 라이브러리 스펙트럼 데이터 추출")
    print("="*70)
    
    parser = OpusLibraryParser(d01_file)
    
    # 스펙트럼 추출
    parser.extract_spectra()
    
    # 데이터 저장
    print("\n" + "="*70)
    print("CSV로 저장 중...")
    print("="*70)
    csv_dir = parser.save_spectra_csv()
    
    # 그래프 생성
    print("\n" + "="*70)
    print("그래프 생성 중...")
    print("="*70)
    parser.plot_spectra(num_to_plot=5)
    parser.plot_all_spectra_overlay()
    
    print("\n" + "="*70)
    print("완료!")
    print("="*70)
    
    # 통계
    if parser.spectra:
        total_compounds = len(parser.spectra)
        points_per_spectrum = parser.spectra[0]['count']
        print(f"\n발견된 화합물 수: {total_compounds}")
        print(f"각 스펙트럼 포인트: {points_per_spectrum}")


if __name__ == "__main__":
    main()
